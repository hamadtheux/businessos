import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.api.dependencies.auth import get_current_user  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.business import (  # noqa: E402
    BusinessOnboardingConflictError,
    BusinessOnboardingPersistenceError,
)
from app.main import app  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_branding import BusinessBranding  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.business import BusinessOnboardingInput  # noqa: E402
from app.services.business import CreatedBusinessContext  # noqa: E402
from app.services.billing import BillingEntitlementError  # noqa: E402
from app.utils.slug import create_slug_base  # noqa: E402


class BusinessOnboardingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _make_user()
        self.session = _FakeAsyncSession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_current_user() -> User:
            return self.user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_current_user
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_post_route_has_authenticated_typed_openapi_contract(self) -> None:
        operation = app.openapi()["paths"]["/api/v1/businesses"]["post"]
        request_schema = operation["requestBody"]["content"][
            "application/json"
        ]["schema"]
        created_schema = operation["responses"]["201"]["content"][
            "application/json"
        ]["schema"]
        retry_schema = operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"]

        self.assertEqual(
            request_schema["$ref"],
            "#/components/schemas/BusinessOnboardingInput",
        )
        self.assertEqual(
            created_schema["$ref"],
            "#/components/schemas/BusinessOnboardingResponse",
        )
        self.assertEqual(
            retry_schema["$ref"],
            "#/components/schemas/BusinessOnboardingResponse",
        )
        self.assertTrue(operation["security"])

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_current_user]

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
        ) as service_mock:
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        service_mock.assert_not_awaited()
        self.assertEqual(self.session.commit_calls, 0)

    async def test_authenticated_database_user_becomes_owner(self) -> None:
        del app.dependency_overrides[get_current_user]
        self.session.user = self.user
        access_token = create_access_token(self.user.id)

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ) as service_mock:
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(),
                headers={"Authorization": f"Bearer {access_token}"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.session.scalar_calls, 1)
        supplied_session, supplied_user_id, _ = service_mock.await_args.args
        self.assertIs(supplied_session, self.session)
        self.assertEqual(supplied_user_id, self.user.id)
        self.assertEqual(self.session.memberships[0].user_id, self.user.id)

    async def test_valid_creation_returns_safe_201_after_commit(self) -> None:
        payload = self._valid_payload(
            name="  Green Valley Farm  ",
            business_type="  FARM  ",
            timezone="Asia/Karachi",
            currency="pkr",
            locale="en-pk",
        )

        with (
            patch(
                "app.api.v1.businesses.create_business_from_onboarding",
                new_callable=AsyncMock,
                side_effect=self._create_or_retry,
            ),
            patch("app.core.security.create_access_token") as token_mock,
            patch(
                "app.api.v1.businesses._build_onboarding_response",
                wraps=_asserting_response_builder(self.session),
            ),
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=payload,
            )

        self.assertEqual(response.status_code, 201)
        response_data = response.json()
        self.assertTrue(response_data["created"])
        self.assertEqual(
            response_data["business"],
            {
                "id": payload["business_id"],
                "name": "Green Valley Farm",
                "slug": "green-valley-farm",
                "business_type": "farm",
                "status": "active",
                "timezone": "Asia/Karachi",
                "currency": "PKR",
                "locale": "en-PK",
                "website_url": None,
                "location": None,
                "description": None,
                "brand_voice": None,
                "avoid_keywords": [],
                "membership_role": "owner",
                "created_at": response_data["business"]["created_at"],
            },
        )
        self.assertIsNone(response_data["branding"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(self.session.commit_calls, 1)
        self.assertTrue(self.session.transaction_committed)
        self.assertEqual(self.session.rollback_calls, 0)
        token_mock.assert_not_called()
        self.assertNotIn("access_token", response_data)
        self.assertNotIn("membership_id", response.text)

    async def test_server_controlled_fields_are_rejected(self) -> None:
        untrusted_fields: tuple[tuple[str, object], ...] = (
            ("user_id", str(uuid4())),
            ("owner_id", str(uuid4())),
            ("role", "owner"),
            ("status", "active"),
            ("slug", "caller-controlled"),
            ("logo_url", "https://untrusted.example/logo.png"),
        )
        for field_name, value in untrusted_fields:
            with self.subTest(field_name=field_name):
                with patch(
                    "app.api.v1.businesses.create_business_from_onboarding",
                    new_callable=AsyncMock,
                ) as service_mock:
                    response = await self.client.post(
                        "/api/v1/businesses",
                        json=self._valid_payload(**{field_name: value}),
                    )

                self.assertEqual(response.status_code, 422)
                service_mock.assert_not_awaited()

    async def test_logo_url_is_rejected_inside_branding(self) -> None:
        for logo_url in (
            "https://untrusted.example/logo.png",
            "data:image/png;base64,untrusted",
        ):
            with self.subTest(logo_url=logo_url):
                response = await self.client.post(
                    "/api/v1/businesses",
                    json=self._valid_payload(
                        branding={"logo_url": logo_url}
                    ),
                )

                self.assertEqual(response.status_code, 422)

    async def test_branding_colors_and_read_only_logo_field_are_returned_safely(
        self,
    ) -> None:
        payload = self._valid_payload(
            branding={
                "primary_color": "#176b45",
                "secondary_color": "#ffffff",
                "accent_color": "#ABCDEF",
            }
        )

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=payload,
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["branding"],
            {
                "primary_color": "#176B45",
                "secondary_color": "#FFFFFF",
                "accent_color": "#ABCDEF",
                "logo_url": None,
            },
        )
        self.assertNotIn("business_id", response.json()["branding"])
        self.assertEqual(len(self.session.brandings), 1)
        self.assertIsNone(self.session.brandings[0].logo_url)

    async def test_exact_retry_returns_200_without_duplicates(self) -> None:
        payload = self._valid_payload(
            branding={"primary_color": "#176B45"}
        )

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ):
            created_response = await self.client.post(
                "/api/v1/businesses",
                json=payload,
            )
            retry_response = await self.client.post(
                "/api/v1/businesses",
                json=payload,
            )

        self.assertEqual(created_response.status_code, 201)
        self.assertTrue(created_response.json()["created"])
        self.assertEqual(retry_response.status_code, 200)
        self.assertFalse(retry_response.json()["created"])
        self.assertEqual(
            retry_response.json()["business"]["id"],
            payload["business_id"],
        )
        self.assertEqual(len(self.session.businesses), 1)
        self.assertEqual(len(self.session.memberships), 1)
        self.assertEqual(len(self.session.brandings), 1)
        self.assertEqual(self.session.commit_calls, 2)

    async def test_created_business_appears_in_business_list(self) -> None:
        payload = self._valid_payload()

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ):
            create_response = await self.client.post(
                "/api/v1/businesses",
                json=payload,
            )

        list_response = await self.client.get("/api/v1/businesses")

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()), 1)
        self.assertEqual(list_response.json()[0]["id"], payload["business_id"])
        self.assertEqual(list_response.json()[0]["membership_role"], "owner")

    async def test_changed_retry_and_cross_tenant_collision_are_safe_409s(
        self,
    ) -> None:
        private_details = (
            "different onboarding payload",
            "Private Tenant Name owned by private-user@example.com",
        )
        responses: list[httpx.Response] = []

        for private_detail in private_details:
            with patch(
                "app.api.v1.businesses.create_business_from_onboarding",
                new_callable=AsyncMock,
                side_effect=BusinessOnboardingConflictError(private_detail),
            ):
                responses.append(
                    await self.client.post(
                        "/api/v1/businesses",
                        json=self._valid_payload(),
                    )
                )

        self.assertEqual(responses[0].status_code, 409)
        self.assertEqual(responses[1].status_code, 409)
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(
            responses[0].json(),
            {
                "detail": (
                    "Business onboarding conflicts with an existing resource."
                )
            },
        )
        for response, private_detail in zip(responses, private_details):
            self.assertNotIn(private_detail, response.text)
        self.assertEqual(self.session.rollback_calls, 2)
        self.assertEqual(self.session.commit_calls, 0)

    async def test_persistence_failure_returns_503_and_rolls_back_all_rows(
        self,
    ) -> None:
        async def fail_after_staging(
            session: _FakeAsyncSession,
            user_id: UUID,
            onboarding: BusinessOnboardingInput,
        ) -> CreatedBusinessContext:
            context = _make_context(onboarding, user_id, created=True)
            session.stage_context(context)
            raise BusinessOnboardingPersistenceError(
                "private database constraint detail"
            )

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=fail_after_staging,
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(
                    branding={"accent_color": "#ABCDEF"}
                ),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business onboarding is temporarily unavailable."},
        )
        self.assertNotIn("constraint", response.text)
        self._assert_no_persisted_or_staged_rows()
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)

    async def test_business_limit_returns_structured_upgrade_guidance(
        self,
    ) -> None:
        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=BillingEntitlementError(
                "usage_limit_reached", "max_businesses"
            ),
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "usage_limit_reached",
                    "entitlement_key": "max_businesses",
                    "current": None,
                    "limit": None,
                    "upgrade_required": True,
                }
            },
        )
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)

    async def test_commit_failure_returns_503_and_rolls_back_all_rows(
        self,
    ) -> None:
        self.session.commit_error = SQLAlchemyError("private commit failure")

        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(
                    branding={"primary_color": "#176B45"}
                ),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business onboarding is temporarily unavailable."},
        )
        self.assertNotIn("private commit failure", response.text)
        self._assert_no_persisted_or_staged_rows()
        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertFalse(self.session.transaction_committed)

    async def test_no_mock_domain_data_is_created(self) -> None:
        with patch(
            "app.api.v1.businesses.create_business_from_onboarding",
            new_callable=AsyncMock,
            side_effect=self._create_or_retry,
        ):
            response = await self.client.post(
                "/api/v1/businesses",
                json=self._valid_payload(),
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.session.businesses), 1)
        self.assertEqual(len(self.session.memberships), 1)
        self.assertEqual(len(self.session.brandings), 0)
        self.assertEqual(
            {type(item) for item in self.session.committed_objects},
            {Business, BusinessMembership},
        )

    def _valid_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "business_id": str(uuid4()),
            "name": "Green Valley Farm",
            "business_type": "farm",
            "timezone": "UTC",
            "currency": "USD",
            "locale": "en",
        }
        payload.update(overrides)
        return payload

    async def _create_or_retry(
        self,
        session: "_FakeAsyncSession",
        user_id: UUID,
        onboarding: BusinessOnboardingInput,
    ) -> CreatedBusinessContext:
        existing_business = next(
            (
                business
                for business in session.businesses
                if business.id == onboarding.business_id
            ),
            None,
        )
        if existing_business is not None:
            membership = next(
                item
                for item in session.memberships
                if item.business_id == existing_business.id
                and item.user_id == user_id
                and item.role == "owner"
            )
            branding = next(
                (
                    item
                    for item in session.brandings
                    if item.business_id == existing_business.id
                ),
                None,
            )
            return CreatedBusinessContext(
                business=existing_business,
                membership=membership,
                branding=branding,
                created=False,
            )

        context = _make_context(onboarding, user_id, created=True)
        session.stage_context(context)
        return context

    def _assert_no_persisted_or_staged_rows(self) -> None:
        self.assertEqual(self.session.businesses, [])
        self.assertEqual(self.session.memberships, [])
        self.assertEqual(self.session.brandings, [])
        self.assertEqual(self.session.staged_objects, [])


class _FakeResult:
    def __init__(self, rows: list[tuple[Business, str]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[Business, str]]:
        return self.rows


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.businesses: list[Business] = []
        self.memberships: list[BusinessMembership] = []
        self.brandings: list[BusinessBranding] = []
        self.staged_objects: list[object] = []
        self.committed_objects: list[object] = []
        self.commit_calls = 0
        self.rollback_calls = 0
        self.execute_calls = 0
        self.commit_error: SQLAlchemyError | None = None
        self.transaction_committed = False
        self.scalar_calls = 0
        self.user: User | None = None

    def stage_context(self, context: CreatedBusinessContext) -> None:
        self.staged_objects.extend((context.business, context.membership))
        if context.branding is not None:
            self.staged_objects.append(context.branding)

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

        for item in self.staged_objects:
            if isinstance(item, Business):
                self.businesses.append(item)
            elif isinstance(item, BusinessMembership):
                self.memberships.append(item)
            elif isinstance(item, BusinessBranding):
                self.brandings.append(item)
            else:
                raise AssertionError(f"Unexpected staged object: {item!r}")
            self.committed_objects.append(item)
        self.staged_objects.clear()
        self.transaction_committed = True

    async def rollback(self) -> None:
        self.rollback_calls += 1
        self.staged_objects.clear()
        self.transaction_committed = False

    async def scalar(self, statement: object) -> User | None:
        self.scalar_calls += 1
        return self.user

    async def execute(self, statement: object) -> _FakeResult:
        self.execute_calls += 1
        parameters = statement.compile().params
        requested_user_id = next(
            value
            for name, value in parameters.items()
            if name.startswith("user_id")
        )
        rows: list[tuple[Business, str]] = []
        for membership in self.memberships:
            if (
                membership.user_id != requested_user_id
                or membership.status != "active"
            ):
                continue
            business = next(
                (
                    candidate
                    for candidate in self.businesses
                    if candidate.id == membership.business_id
                    and candidate.status == "active"
                ),
                None,
            )
            if business is not None:
                rows.append((business, membership.role))
        rows.sort(key=lambda row: (row[0].created_at, row[0].id))
        return _FakeResult(rows)


def _make_context(
    onboarding: BusinessOnboardingInput,
    user_id: UUID,
    *,
    created: bool,
) -> CreatedBusinessContext:
    now = datetime.now(UTC)
    business = Business(
        id=onboarding.business_id,
        name=onboarding.name,
        slug=create_slug_base(onboarding.name),
        business_type=onboarding.business_type,
        status="active",
        timezone=onboarding.timezone,
        currency=onboarding.currency,
        locale=onboarding.locale,
    )
    business.created_at = now
    business.updated_at = now
    membership = BusinessMembership(
        id=uuid4(),
        business_id=business.id,
        user_id=user_id,
        role="owner",
        status="active",
    )
    membership.created_at = now
    membership.updated_at = now

    branding = None
    branding_input = onboarding.branding
    if branding_input is not None and any(
        color is not None
        for color in (
            branding_input.primary_color,
            branding_input.secondary_color,
            branding_input.accent_color,
        )
    ):
        branding = BusinessBranding(
            business_id=business.id,
            primary_color=branding_input.primary_color,
            secondary_color=branding_input.secondary_color,
            accent_color=branding_input.accent_color,
        )
        branding.created_at = now
        branding.updated_at = now

    return CreatedBusinessContext(
        business=business,
        membership=membership,
        branding=branding,
        created=created,
    )


def _make_user() -> User:
    now = datetime.now(UTC)
    user = User(
        id=uuid4(),
        email="owner@example.com",
        password_hash="stored-test-hash",
        first_name="Owner",
        last_name="User",
        status="active",
        is_email_verified=True,
    )
    user.created_at = now
    user.updated_at = now
    return user


def _asserting_response_builder(session: _FakeAsyncSession):
    from app.api.v1.businesses import _build_onboarding_response

    def build_after_commit(context: CreatedBusinessContext):
        if not session.transaction_committed:
            raise AssertionError("Response was built before transaction commit")
        return _build_onboarding_response(context)

    return build_after_commit
