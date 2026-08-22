import os
import unittest
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = (
    "business-onboarding-test-secret-with-at-least-thirty-two-bytes"
)

from app.exceptions.business import (  # noqa: E402
    BusinessOnboardingConflictError,
    BusinessOnboardingPersistenceError,
)
from app.models.business import Business  # noqa: E402
from app.models.business_branding import BusinessBranding  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.schemas.business import (  # noqa: E402
    BusinessBrandingInput,
    BusinessOnboardingInput,
)
from app.services.business import (  # noqa: E402
    CreatedBusinessContext,
    create_business_from_onboarding,
)
from app.utils.slug import add_uuid_slug_suffix, create_slug_base  # noqa: E402


class BusinessOnboardingSchemaTests(unittest.TestCase):
    def test_valid_input_is_accepted_with_safe_defaults(self) -> None:
        onboarding = self._onboarding()

        self.assertEqual(onboarding.timezone, "UTC")
        self.assertEqual(onboarding.currency, "USD")
        self.assertEqual(onboarding.locale, "en")
        self.assertIsNone(onboarding.branding)

    def test_unknown_top_level_fields_are_rejected(self) -> None:
        for field_name in ("slug", "status", "owner_id", "logo_url"):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    self._onboarding(**{field_name: "untrusted"})

    def test_name_is_trimmed_without_collapsing_internal_whitespace(self) -> None:
        onboarding = self._onboarding(name="  Green   Valley Farm  ")

        self.assertEqual(onboarding.name, "Green   Valley Farm")

    def test_blank_and_oversized_names_are_rejected(self) -> None:
        for name in ("   ", "x" * 161):
            with self.subTest(name_length=len(name)):
                with self.assertRaises(ValidationError):
                    self._onboarding(name=name)

    def test_business_type_is_trimmed_lowercase_and_extensible(self) -> None:
        onboarding = self._onboarding(business_type="  FUTURE_INDUSTRY  ")

        self.assertEqual(onboarding.business_type, "future_industry")

    def test_blank_and_oversized_business_types_are_rejected(self) -> None:
        for business_type in ("   ", "x" * 81):
            with self.subTest(type_length=len(business_type)):
                with self.assertRaises(ValidationError):
                    self._onboarding(business_type=business_type)

    def test_valid_iana_timezones_are_accepted(self) -> None:
        for timezone in (
            "UTC",
            "Asia/Karachi",
            "Europe/London",
            "America/New_York",
        ):
            with self.subTest(timezone=timezone):
                onboarding = self._onboarding(timezone=f"  {timezone}  ")
                self.assertEqual(onboarding.timezone, timezone)

    def test_invalid_timezones_are_rejected(self) -> None:
        for timezone in ("", "Not/A_Real_Zone", "../UTC"):
            with self.subTest(timezone=timezone):
                with self.assertRaises(ValidationError):
                    self._onboarding(timezone=timezone)

    def test_currency_is_trimmed_and_normalized_to_uppercase(self) -> None:
        onboarding = self._onboarding(currency="  pkr  ")

        self.assertEqual(onboarding.currency, "PKR")

    def test_invalid_currency_is_rejected(self) -> None:
        for currency in ("US", "US12", "U1D", "ÜSD"):
            with self.subTest(currency=currency):
                with self.assertRaises(ValidationError):
                    self._onboarding(currency=currency)

    def test_locale_is_trimmed_validated_and_normalized(self) -> None:
        expected_values = {
            " EN ": "en",
            "en-us": "en-US",
            "EN-gb": "en-GB",
            "ur-pk": "ur-PK",
            "zh-hant-tw": "zh-Hant-TW",
        }
        for supplied, expected in expected_values.items():
            with self.subTest(locale=supplied):
                self.assertEqual(self._onboarding(locale=supplied).locale, expected)

    def test_invalid_locale_is_rejected(self) -> None:
        for locale in ("e", "en_US", "english-US", "en-123456789"):
            with self.subTest(locale=locale):
                with self.assertRaises(ValidationError):
                    self._onboarding(locale=locale)

    def test_valid_hex_colors_are_normalized_to_uppercase(self) -> None:
        branding = BusinessBrandingInput(
            primary_color="#176B45",
            secondary_color="#ffffff",
            accent_color="#ABCDEF",
        )

        self.assertEqual(branding.primary_color, "#176B45")
        self.assertEqual(branding.secondary_color, "#FFFFFF")
        self.assertEqual(branding.accent_color, "#ABCDEF")

    def test_invalid_hex_colors_are_rejected(self) -> None:
        for color in ("#FFF", "FFFFFF", "red", "#GGGGGG"):
            with self.subTest(color=color):
                with self.assertRaises(ValidationError):
                    BusinessBrandingInput(primary_color=color)

    def test_logo_url_and_data_url_cannot_be_supplied(self) -> None:
        for logo_value in (
            "https://untrusted.example/logo.png",
            "data:image/png;base64,untrusted",
        ):
            with self.subTest(logo_value=logo_value):
                with self.assertRaises(ValidationError):
                    self._onboarding(branding={"logo_url": logo_value})

    def _onboarding(self, **overrides: object) -> BusinessOnboardingInput:
        values: dict[str, object] = {
            "business_id": uuid4(),
            "name": "Green Valley Farm",
            "business_type": "farm",
        }
        values.update(overrides)
        return BusinessOnboardingInput(**values)


class BusinessSlugTests(unittest.TestCase):
    def test_slug_is_url_safe_and_collapses_separators(self) -> None:
        self.assertEqual(
            create_slug_base("  Café & Green---Valley!  "),
            "cafe-green-valley",
        )

    def test_slug_uses_safe_fallback_when_name_has_no_ascii_content(self) -> None:
        self.assertEqual(create_slug_base("農場"), "business")

    def test_uuid_suffix_is_deterministic_and_within_database_limit(self) -> None:
        business_id = uuid4()
        base = create_slug_base("x" * 200)

        first = add_uuid_slug_suffix(base, business_id)
        second = add_uuid_slug_suffix(base, business_id)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 120)
        self.assertTrue(first.endswith(business_id.hex[:12]))


class BusinessOnboardingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_business_uses_supplied_id_and_creates_owner(self) -> None:
        business_id = uuid4()
        user_id = uuid4()
        session = _FakeAsyncSession()

        result = await create_business_from_onboarding(
            session,
            user_id,
            self._onboarding(business_id=business_id),
        )

        self.assertTrue(result.created)
        self.assertEqual(result.business.id, business_id)
        self.assertEqual(result.business.slug, "green-valley-farm")
        self.assertEqual(result.business.status, "active")
        self.assertEqual(result.membership.business_id, business_id)
        self.assertEqual(result.membership.user_id, user_id)
        self.assertEqual(result.membership.role, "owner")
        self.assertEqual(result.membership.status, "active")
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(
            {type(item) for item in session.added_history},
            {Business, BusinessMembership},
        )

    async def test_slug_is_generated_server_side_from_normalized_name(self) -> None:
        result = await create_business_from_onboarding(
            _FakeAsyncSession(),
            uuid4(),
            self._onboarding(name="  My New Restaurant!  "),
        )

        self.assertEqual(result.business.slug, "my-new-restaurant")

    async def test_branding_colors_are_persisted_without_logo_url(self) -> None:
        session = _FakeAsyncSession()
        onboarding = self._onboarding(
            branding={
                "primary_color": "#176b45",
                "secondary_color": "#ffffff",
                "accent_color": "#ABCDEF",
            }
        )

        result = await create_business_from_onboarding(
            session,
            uuid4(),
            onboarding,
        )

        self.assertIsNotNone(result.branding)
        assert result.branding is not None
        self.assertEqual(result.branding.primary_color, "#176B45")
        self.assertEqual(result.branding.secondary_color, "#FFFFFF")
        self.assertEqual(result.branding.accent_color, "#ABCDEF")
        self.assertIsNone(result.branding.logo_url)
        self.assertEqual(
            sum(isinstance(item, BusinessBranding) for item in session.added_history),
            1,
        )

    async def test_omitted_or_empty_branding_creates_no_row(self) -> None:
        for branding in (None, {}):
            with self.subTest(branding=branding):
                session = _FakeAsyncSession()
                result = await create_business_from_onboarding(
                    session,
                    uuid4(),
                    self._onboarding(branding=branding),
                )

                self.assertIsNone(result.branding)
                self.assertFalse(
                    any(
                        isinstance(item, BusinessBranding)
                        for item in session.added_history
                    )
                )

    async def test_exact_retry_returns_existing_context_without_duplicates(
        self,
    ) -> None:
        session = _FakeAsyncSession()
        user_id = uuid4()
        onboarding = self._onboarding(
            branding={"primary_color": "#176B45"}
        )

        created = await create_business_from_onboarding(
            session,
            user_id,
            onboarding,
        )
        retried = await create_business_from_onboarding(
            session,
            user_id,
            onboarding,
        )

        self.assertTrue(created.created)
        self.assertFalse(retried.created)
        self.assertIs(retried.business, created.business)
        self.assertIs(retried.membership, created.membership)
        self.assertIs(retried.branding, created.branding)
        self.assertEqual(len(session.businesses), 1)
        self.assertEqual(len(session.memberships), 1)
        self.assertEqual(len(session.brandings), 1)
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 0)

    async def test_retry_with_different_business_data_raises_conflict(self) -> None:
        session = _FakeAsyncSession()
        user_id = uuid4()
        onboarding = self._onboarding()
        await create_business_from_onboarding(session, user_id, onboarding)

        with self.assertRaises(BusinessOnboardingConflictError):
            await create_business_from_onboarding(
                session,
                user_id,
                self._onboarding(
                    business_id=onboarding.business_id,
                    name="Different Name",
                ),
            )

        self.assertEqual(len(session.businesses), 1)
        self.assertEqual(len(session.memberships), 1)

    async def test_retry_with_different_branding_raises_conflict(self) -> None:
        session = _FakeAsyncSession()
        user_id = uuid4()
        onboarding = self._onboarding(
            branding={"primary_color": "#176B45"}
        )
        await create_business_from_onboarding(session, user_id, onboarding)

        with self.assertRaises(BusinessOnboardingConflictError):
            await create_business_from_onboarding(
                session,
                user_id,
                self._onboarding(
                    business_id=onboarding.business_id,
                    branding={"primary_color": "#FFFFFF"},
                ),
            )

        self.assertEqual(len(session.brandings), 1)

    async def test_other_users_business_id_fails_closed_without_data_leak(
        self,
    ) -> None:
        business_id = uuid4()
        private_owner_id = uuid4()
        business = _make_business(
            business_id=business_id,
            name="Private Tenant Name",
            slug="private-tenant-name",
        )
        membership = _make_membership(business, private_owner_id)
        session = _FakeAsyncSession(
            businesses=[business],
            memberships=[membership],
        )

        with self.assertRaises(BusinessOnboardingConflictError) as raised:
            await create_business_from_onboarding(
                session,
                uuid4(),
                self._onboarding(business_id=business_id),
            )

        error_text = str(raised.exception)
        self.assertEqual(
            error_text,
            "Business onboarding conflicts with an existing resource",
        )
        self.assertNotIn(business.name, error_text)
        self.assertNotIn(str(private_owner_id), error_text)
        self.assertEqual(session.added_history, [])

    async def test_non_owner_membership_does_not_authorize_retry(self) -> None:
        business = _make_business(
            business_id=uuid4(),
            name="Existing Business",
            slug="existing-business",
        )
        user_id = uuid4()
        membership = _make_membership(business, user_id, role="member")
        session = _FakeAsyncSession(
            businesses=[business],
            memberships=[membership],
        )

        with self.assertRaises(BusinessOnboardingConflictError):
            await create_business_from_onboarding(
                session,
                user_id,
                self._onboarding(business_id=business.id),
            )

    async def test_taken_base_slug_uses_deterministic_uuid_fallback(self) -> None:
        existing = _make_business(
            business_id=uuid4(),
            name="Green Valley Farm",
            slug="green-valley-farm",
        )
        session = _FakeAsyncSession(businesses=[existing])
        onboarding = self._onboarding()

        result = await create_business_from_onboarding(
            session,
            uuid4(),
            onboarding,
        )

        expected = add_uuid_slug_suffix(
            "green-valley-farm",
            onboarding.business_id,
        )
        self.assertEqual(result.business.slug, expected)

    async def test_concurrent_base_slug_collision_retries_once_with_suffix(
        self,
    ) -> None:
        competing = _make_business(
            business_id=uuid4(),
            name="Competing Business",
            slug="green-valley-farm",
        )
        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("ix_businesses_slug")],
            rollback_actions=[
                lambda current: current.store_business(competing),
            ],
        )
        onboarding = self._onboarding()

        result = await create_business_from_onboarding(
            session,
            uuid4(),
            onboarding,
        )

        self.assertTrue(result.created)
        self.assertEqual(
            result.business.slug,
            add_uuid_slug_suffix("green-valley-farm", onboarding.business_id),
        )
        self.assertEqual(session.flush_calls, 2)
        self.assertEqual(session.savepoint_rollbacks, 1)
        self.assertFalse(session.transaction_broken)

    async def test_concurrent_same_owner_retry_returns_existing_resource(
        self,
    ) -> None:
        user_id = uuid4()
        onboarding = self._onboarding(
            branding={"accent_color": "#ABCDEF"}
        )
        concurrent_business = _make_business(
            business_id=onboarding.business_id,
            name=onboarding.name,
            slug="green-valley-farm",
            business_type=onboarding.business_type,
            timezone=onboarding.timezone,
            currency=onboarding.currency,
            locale=onboarding.locale,
        )
        concurrent_membership = _make_membership(
            concurrent_business,
            user_id,
        )
        concurrent_branding = _make_branding(
            concurrent_business,
            accent_color="#ABCDEF",
        )

        def inject_concurrent_context(session: _FakeAsyncSession) -> None:
            session.store_business(concurrent_business)
            session.memberships.append(concurrent_membership)
            session.brandings[concurrent_business.id] = concurrent_branding

        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("pk_businesses")],
            rollback_actions=[inject_concurrent_context],
        )

        result = await create_business_from_onboarding(
            session,
            user_id,
            onboarding,
        )

        self.assertFalse(result.created)
        self.assertIs(result.business, concurrent_business)
        self.assertIs(result.membership, concurrent_membership)
        self.assertIs(result.branding, concurrent_branding)
        self.assertEqual(len(session.businesses), 1)
        self.assertEqual(len(session.memberships), 1)
        self.assertEqual(len(session.brandings), 1)
        self.assertEqual(session.commit_calls, 0)
        self.assertTrue(session.savepoint_rollbacks)
        self.assertFalse(session.transaction_broken)

    async def test_known_collision_without_matching_owner_is_generic_conflict(
        self,
    ) -> None:
        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("pk_businesses")]
        )

        with self.assertRaises(BusinessOnboardingConflictError):
            await create_business_from_onboarding(
                session,
                uuid4(),
                self._onboarding(),
            )

        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)

    async def test_unrelated_integrity_error_maps_to_persistence_error(
        self,
    ) -> None:
        session = _FakeAsyncSession(
            flush_errors=[_integrity_error("ck_businesses_valid_status")]
        )

        with self.assertRaises(BusinessOnboardingPersistenceError) as raised:
            await create_business_from_onboarding(
                session,
                uuid4(),
                self._onboarding(),
            )

        self.assertNotIsInstance(raised.exception, IntegrityError)
        self.assertEqual(
            str(raised.exception),
            "Unable to persist business onboarding",
        )
        self.assertTrue(session.savepoint_rollbacks)
        self.assertFalse(session.transaction_broken)
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.businesses, {})
        self.assertEqual(session.memberships, [])
        self.assertEqual(session.brandings, {})

    async def test_database_read_failure_maps_to_persistence_error(self) -> None:
        session = _FakeAsyncSession(
            scalar_error=SQLAlchemyError("private database failure")
        )

        with self.assertRaises(BusinessOnboardingPersistenceError) as raised:
            await create_business_from_onboarding(
                session,
                uuid4(),
                self._onboarding(),
            )

        self.assertNotIn("private database failure", str(raised.exception))
        self.assertEqual(session.commit_calls, 0)

    async def test_created_context_is_immutable(self) -> None:
        result = await create_business_from_onboarding(
            _FakeAsyncSession(),
            uuid4(),
            self._onboarding(),
        )

        with self.assertRaises(FrozenInstanceError):
            result.created = False  # type: ignore[misc]

    def _onboarding(self, **overrides: object) -> BusinessOnboardingInput:
        values: dict[str, object] = {
            "business_id": uuid4(),
            "name": "Green Valley Farm",
            "business_type": "farm",
            "timezone": "UTC",
            "currency": "USD",
            "locale": "en",
        }
        values.update(overrides)
        return BusinessOnboardingInput(**values)


class _ConstraintViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("database integrity constraint violated")
        self.constraint_name = constraint_name
        self.sqlstate = "23505"


def _integrity_error(constraint_name: str) -> IntegrityError:
    return IntegrityError(
        "statement omitted",
        {},
        _ConstraintViolation(constraint_name),
    )


class _FakeNestedTransaction:
    def __init__(self, session: "_FakeAsyncSession") -> None:
        self.session = session

    async def __aenter__(self) -> "_FakeNestedTransaction":
        self.session.savepoint_starts += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self.session.savepoint_releases += 1
        else:
            self.session.savepoint_rollbacks += 1
            self.session.pending.clear()
            self.session.transaction_broken = False
            if self.session.rollback_actions:
                action = self.session.rollback_actions.pop(0)
                action(self.session)
        return False


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        businesses: list[Business] | None = None,
        memberships: list[BusinessMembership] | None = None,
        brandings: list[BusinessBranding] | None = None,
        flush_errors: list[IntegrityError] | None = None,
        rollback_actions: list[Callable[["_FakeAsyncSession"], None]] | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.businesses = {
            business.id: business for business in businesses or []
        }
        self.memberships = list(memberships or [])
        self.brandings = {
            branding.business_id: branding for branding in brandings or []
        }
        self.flush_errors = list(flush_errors or [])
        self.rollback_actions = list(rollback_actions or [])
        self.scalar_error = scalar_error
        self.pending: list[object] = []
        self.added_history: list[object] = []
        self.scalar_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.savepoint_starts = 0
        self.savepoint_releases = 0
        self.savepoint_rollbacks = 0
        self.transaction_broken = False
        self.executed_sql: list[str] = []

    async def scalar(self, statement: object) -> object | None:
        self.scalar_calls += 1
        if self.scalar_error is not None:
            raise self.scalar_error

        self.executed_sql.append(str(statement))
        description = statement.column_descriptions[0]
        entity = description["entity"]
        expression_name = description["name"]
        parameters = statement.compile().params

        if entity is Business and expression_name == "Business":
            business_id = _parameter_value(parameters, "id")
            return self.businesses.get(business_id)
        if entity is Business and expression_name == "id":
            slug = _parameter_value(parameters, "slug")
            return next(
                (
                    business.id
                    for business in self.businesses.values()
                    if business.slug == slug
                ),
                None,
            )
        if entity is BusinessMembership:
            business_id = _parameter_value(parameters, "business_id")
            user_id = _parameter_value(parameters, "user_id")
            role = _parameter_value(parameters, "role")
            return next(
                (
                    membership
                    for membership in self.memberships
                    if membership.business_id == business_id
                    and membership.user_id == user_id
                    and membership.role == role
                ),
                None,
            )
        if entity is BusinessBranding:
            business_id = _parameter_value(parameters, "business_id")
            return self.brandings.get(business_id)
        raise AssertionError(f"Unexpected scalar statement: {statement}")

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction(self)

    def add(self, instance: object) -> None:
        self.pending.append(instance)
        self.added_history.append(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_errors:
            self.transaction_broken = True
            raise self.flush_errors.pop(0)

        now = datetime.now(UTC)
        for item in self.pending:
            if isinstance(item, Business):
                item.status = item.status or "active"
                item.created_at = now
                item.updated_at = now
                self.store_business(item)
            elif isinstance(item, BusinessMembership):
                item.id = item.id or uuid4()
                item.created_at = now
                item.updated_at = now
                self.memberships.append(item)
            elif isinstance(item, BusinessBranding):
                item.created_at = now
                item.updated_at = now
                self.brandings[item.business_id] = item
            else:
                raise AssertionError(f"Unexpected pending object: {item!r}")
        self.pending.clear()

    async def commit(self) -> None:
        self.commit_calls += 1

    def store_business(self, business: Business) -> None:
        self.businesses[business.id] = business


def _parameter_value(parameters: dict[str, object], prefix: str) -> object:
    return next(
        value
        for name, value in parameters.items()
        if name == prefix or name.startswith(f"{prefix}_")
    )


def _make_business(
    *,
    business_id: UUID,
    name: str,
    slug: str,
    business_type: str = "farm",
    timezone: str = "UTC",
    currency: str = "USD",
    locale: str = "en",
) -> Business:
    business = Business(
        id=business_id,
        name=name,
        slug=slug,
        business_type=business_type,
        status="active",
        timezone=timezone,
        currency=currency,
        locale=locale,
    )
    business.created_at = datetime.now(UTC)
    business.updated_at = business.created_at
    return business


def _make_membership(
    business: Business,
    user_id: UUID,
    *,
    role: str = "owner",
) -> BusinessMembership:
    membership = BusinessMembership(
        id=uuid4(),
        business_id=business.id,
        user_id=user_id,
        role=role,
        status="active",
    )
    membership.created_at = datetime.now(UTC)
    membership.updated_at = membership.created_at
    return membership


def _make_branding(
    business: Business,
    *,
    primary_color: str | None = None,
    secondary_color: str | None = None,
    accent_color: str | None = None,
) -> BusinessBranding:
    branding = BusinessBranding(
        business_id=business.id,
        primary_color=primary_color,
        secondary_color=secondary_color,
        accent_color=accent_color,
    )
    branding.created_at = datetime.now(UTC)
    branding.updated_at = branding.created_at
    return branding
