import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.business import (
    BusinessAccessContext,
    get_business_access,
)
from app.db.session import get_db_session
from app.main import app
from app.models.business import Business
from app.models.business_membership import BusinessMembership
from app.models.catalog_item import CatalogItem
from app.models.user import User

BUSINESS_A_ID = UUID("10000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("20000000-0000-0000-0000-000000000002")


class CatalogApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _user()
        self.business_a = _business(BUSINESS_A_ID, "Tenant A", "tenant-a")
        self.business_b = _business(BUSINESS_B_ID, "Tenant B", "tenant-b")
        self.membership_a = _membership(self.business_a, self.user)
        self.membership_b = _membership(self.business_b, self.user)
        self.accessible = {
            BUSINESS_A_ID: (self.business_a, self.membership_a),
        }
        self.session = _CatalogSession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_user() -> User:
            return self.user

        async def override_access(business_id: UUID) -> BusinessAccessContext:
            context = self.accessible.get(business_id)
            if context is None:
                raise HTTPException(status_code=404, detail="Business not found.")
            business, membership = context
            return BusinessAccessContext(
                user=self.user,
                business=business,
                membership=membership,
            )

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[get_business_access] = override_access
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_dependency_overrides)

    def test_openapi_exposes_typed_crud_and_import_contract(self) -> None:
        schema = app.openapi()
        base = "/api/v1/businesses/{business_id}/catalog"
        self.assertEqual(set(schema["paths"][base]), {"get", "post"})
        self.assertEqual(
            set(schema["paths"][f"{base}/{{item_id}}"]),
            {"get", "patch", "delete"},
        )
        self.assertIn("post", schema["paths"][f"{base}/import/preview"])
        self.assertIn("post", schema["paths"][f"{base}/import"])
        self.assertIn(
            "multipart/form-data",
            schema["paths"][f"{base}/import"]["post"]["requestBody"]["content"],
        )

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_business_access]
        del app.dependency_overrides[get_current_user]

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(self.session.execute_calls, 0)

    async def test_business_membership_is_required_with_safe_404(self) -> None:
        del app.dependency_overrides[get_business_access]
        self.session.access_row = None

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        self.assertEqual(self.session.scalars_calls, 0)

    async def test_empty_list_is_tenant_scoped_and_not_cached(self) -> None:
        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self._assert_private(response)
        self.assertEqual(self.session.requested_business_id, BUSINESS_A_ID)
        self.assertEqual(self.session.commit_calls, 0)

    async def test_create_product_normalizes_sku_and_serializes_decimal_safely(
        self,
    ) -> None:
        response = await self.client.post(
            self._base_url(),
            json={
                "item_type": "product",
                "name": "  Widget  ",
                "sku": " abc-1 ",
                "price": "19.95",
            },
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["business_id"], str(BUSINESS_A_ID))
        self.assertEqual(data["name"], "Widget")
        self.assertEqual(data["sku"], "ABC-1")
        self.assertEqual(data["price"], "19.95")
        self.assertEqual(data["status"], "active")
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_create_service_returns_201(self) -> None:
        response = await self.client.post(
            self._base_url(),
            json={"item_type": "service", "name": "Consultation", "price": None},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["item_type"], "service")
        self.assertIsNone(response.json()["price"])

    async def test_duplicate_sku_conflicts_only_inside_tenant(self) -> None:
        self.session.items.append(_catalog_item(BUSINESS_A_ID, "Existing", sku="SAME"))
        conflict = await self.client.post(
            self._base_url(),
            json={"item_type": "product", "name": "Duplicate", "sku": "same"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.json(),
            {"detail": "SKU already exists in this business."},
        )
        self.assertNotIn("uq_catalog", conflict.text)
        self.assertEqual(self.session.rollback_calls, 1)

        self.accessible[BUSINESS_B_ID] = (self.business_b, self.membership_b)
        allowed = await self.client.post(
            self._base_url(BUSINESS_B_ID),
            json={"item_type": "product", "name": "Allowed", "sku": "same"},
        )
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(allowed.json()["business_id"], str(BUSINESS_B_ID))

    async def test_get_one_uses_business_and_item_identifiers(self) -> None:
        item = _catalog_item(BUSINESS_A_ID, "Widget")
        self.session.items.append(item)
        response = await self.client.get(f"{self._base_url()}/{item.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(item.id))
        self.assertEqual(self.session.requested_business_id, BUSINESS_A_ID)
        self.assertEqual(self.session.requested_item_id, item.id)

    async def test_wrong_tenant_item_is_safe_404(self) -> None:
        item = _catalog_item(BUSINESS_B_ID, "Private")
        self.session.items.append(item)
        response = await self.client.get(f"{self._base_url()}/{item.id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Catalog item not found."})
        self.assertNotIn("Private", response.text)

    async def test_patch_changes_only_explicit_fields_and_can_clear_nullable_fields(
        self,
    ) -> None:
        item = _catalog_item(
            BUSINESS_A_ID,
            "Original",
            description="Description",
            sku="OLD",
            price=Decimal("10.00"),
        )
        self.session.items.append(item)

        first = await self.client.patch(
            f"{self._base_url()}/{item.id}",
            json={"name": "Updated", "status": "draft"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["description"], "Description")
        self.assertEqual(first.json()["sku"], "OLD")
        self.assertEqual(first.json()["price"], "10.00")

        cleared = await self.client.patch(
            f"{self._base_url()}/{item.id}",
            json={"description": None, "sku": None, "price": None},
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.json()["description"])
        self.assertIsNone(cleared.json()["sku"])
        self.assertIsNone(cleared.json()["price"])
        self.assertEqual(self.session.refresh_calls, 2)

    async def test_patch_validates_money_and_sku_conflicts(self) -> None:
        first = _catalog_item(BUSINESS_A_ID, "First", sku="FIRST")
        second = _catalog_item(BUSINESS_A_ID, "Second", sku="SECOND")
        self.session.items.extend([first, second])

        invalid = await self.client.patch(
            f"{self._base_url()}/{first.id}",
            json={"price": "-1.00"},
        )
        self.assertEqual(invalid.status_code, 422)

        conflict = await self.client.patch(
            f"{self._base_url()}/{first.id}",
            json={"sku": " second "},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(first.sku, "FIRST")

    async def test_delete_archives_and_is_idempotent(self) -> None:
        item = _catalog_item(BUSINESS_A_ID, "Widget")
        self.session.items.append(item)
        url = f"{self._base_url()}/{item.id}"

        first = await self.client.delete(url)
        second = await self.client.delete(url)

        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        self.assertEqual(first.content, b"")
        self.assertEqual(item.status, "archived")
        self.assertIn(item, self.session.items)
        self._assert_private(first)

    async def test_archived_items_are_excluded_by_default_but_filterable(self) -> None:
        active = _catalog_item(BUSINESS_A_ID, "Active")
        archived = _catalog_item(BUSINESS_A_ID, "Archived", status="archived")
        service = _catalog_item(BUSINESS_A_ID, "Service", item_type="service")
        self.session.items.extend([archived, service, active])

        default = await self.client.get(self._base_url())
        archived_only = await self.client.get(f"{self._base_url()}?status=archived")
        services = await self.client.get(f"{self._base_url()}?item_type=service")

        self.assertEqual([row["name"] for row in default.json()], ["Active", "Service"])
        self.assertEqual([row["name"] for row in archived_only.json()], ["Archived"])
        self.assertEqual([row["name"] for row in services.json()], ["Service"])

    async def test_cross_tenant_update_and_delete_are_impossible(self) -> None:
        item = _catalog_item(BUSINESS_B_ID, "Private")
        self.session.items.append(item)
        url = f"{self._base_url(BUSINESS_B_ID)}/{item.id}"

        patch_response = await self.client.patch(url, json={"name": "Stolen"})
        delete_response = await self.client.delete(url)

        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(item.name, "Private")
        self.assertEqual(item.status, "active")

    async def test_csv_preview_writes_zero_rows(self) -> None:
        response = await self.client.post(
            f"{self._base_url()}/import/preview",
            files={"file": ("catalog.csv", b"title,code\nWidget,abc\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["valid_rows"], 1)
        self.assertEqual(
            response.json()["detected_columns"], {"name": "title", "sku": "code"}
        )
        self.assertEqual(self.session.items, [])
        self.assertEqual(self.session.commit_calls, 0)
        self._assert_private(response)

    async def test_atomic_import_creates_all_valid_rows_in_one_commit(self) -> None:
        content = b"name,type,price\nWidget,product,10.00\nAdvice,service,25.00\n"
        response = await self.client.post(
            f"{self._base_url()}/import",
            files={"file": ("catalog.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), {"created_count": 2, "total_rows": 2})
        self.assertEqual(len(self.session.items), 2)
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_one_invalid_import_row_creates_zero_items_and_returns_typed_422(
        self,
    ) -> None:
        content = b"name,price\nValid,10.00\nInvalid,-1.00\n"
        response = await self.client.post(
            f"{self._base_url()}/import",
            files={"file": ("catalog.csv", content, "text/csv")},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["total_rows"], 2)
        self.assertEqual(response.json()["invalid_rows"], 1)
        self.assertEqual(response.json()["errors"][0]["row"], 3)
        self.assertEqual(self.session.items, [])
        self.assertEqual(self.session.commit_calls, 0)

    async def test_import_database_failure_rolls_back_every_pending_row(self) -> None:
        self.session.flush_error = SQLAlchemyError("internal database failure")
        response = await self.client.post(
            f"{self._base_url()}/import",
            files={"file": ("catalog.csv", b"name\nOne\nTwo\n", "text/csv")},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business catalog is temporarily unavailable."},
        )
        self.assertEqual(self.session.items, [])
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertNotIn("internal database", response.text)

    async def test_upload_type_size_and_invalid_content_fail_safely(self) -> None:
        cases = (
            ("catalog.xls", b"name\nWidget\n", 422),
            ("catalog.xlsx", b"not an xlsx", 422),
            ("catalog.csv", b"x" * (10 * 1024 * 1024 + 1), 413),
        )
        for filename, content, expected_status in cases:
            with self.subTest(filename=filename):
                response = await self.client.post(
                    f"{self._base_url()}/import/preview",
                    files={"file": (filename, content, "application/octet-stream")},
                )
                self.assertEqual(response.status_code, expected_status)
                self._assert_private(response)

    def _base_url(self, business_id: UUID = BUSINESS_A_ID) -> str:
        return f"/api/v1/businesses/{business_id}/catalog"

    def _assert_private(self, response: httpx.Response) -> None:
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")


class _ScalarResult:
    def __init__(self, values) -> None:
        self.values = list(values)

    def all(self):
        return self.values


class _AccessResult:
    def __init__(self, row) -> None:
        self.row = row

    def one_or_none(self):
        return self.row


class _CatalogSession:
    def __init__(self) -> None:
        self.items: list[CatalogItem] = []
        self.pending: list[CatalogItem] = []
        self.transaction_additions: list[CatalogItem] = []
        self.access_row = None
        self.flush_error: SQLAlchemyError | None = None
        self.execute_calls = 0
        self.scalars_calls = 0
        self.flush_calls = 0
        self.refresh_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.requested_business_id: UUID | None = None
        self.requested_item_id: UUID | None = None

    async def execute(self, statement) -> _AccessResult:
        self.execute_calls += 1
        return _AccessResult(self.access_row)

    async def scalar(self, statement):
        params = statement.compile().params
        business_id = _parameter(params, "business_id")
        self.requested_business_id = business_id
        sku = _parameter(params, "sku")
        item_id = _parameter(params, "id")
        if item_id is not None:
            self.requested_item_id = item_id

        matches = [item for item in self.items if item.business_id == business_id]
        if sku is not None:
            matches = [item for item in matches if item.sku == sku]
            if item_id is not None:
                matches = [item for item in matches if item.id != item_id]
            return matches[0].id if matches else None
        if item_id is not None:
            matches = [item for item in matches if item.id == item_id]
        return matches[0] if matches else None

    async def scalars(self, statement) -> _ScalarResult:
        self.scalars_calls += 1
        sql = str(statement)
        params = statement.compile().params
        business_id = _parameter(params, "business_id")
        self.requested_business_id = business_id
        items = [item for item in self.items if item.business_id == business_id]

        if sql.lstrip().startswith("SELECT catalog_items.sku"):
            candidates = next(
                (
                    set(value)
                    for value in params.values()
                    if isinstance(value, (list, tuple, set, frozenset))
                ),
                set(),
            )
            return _ScalarResult(
                item.sku
                for item in items
                if item.sku is not None and item.sku in candidates
            )

        item_type = _parameter(params, "item_type")
        item_status = _parameter(params, "status")
        if item_type is not None:
            items = [item for item in items if item.item_type == item_type]
        if item_status is not None:
            if "catalog_items.status !=" in sql:
                items = [item for item in items if item.status != item_status]
            else:
                items = [item for item in items if item.status == item_status]
        return _ScalarResult(sorted(items, key=lambda item: (item.created_at, item.id)))

    def add(self, item: CatalogItem) -> None:
        self.pending.append(item)

    def add_all(self, items: list[CatalogItem]) -> None:
        self.pending.extend(items)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error
        now = datetime.now(UTC)
        for index, item in enumerate(self.pending):
            if item.id is None:
                item.id = uuid4()
            if item.created_at is None:
                item.created_at = now + timedelta(microseconds=index)
            if item.updated_at is None:
                item.updated_at = item.created_at
            self.items.append(item)
            self.transaction_additions.append(item)
        self.pending.clear()

    async def refresh(self, item: CatalogItem, *, attribute_names=None) -> None:
        self.refresh_calls += 1
        if attribute_names == ["updated_at"]:
            item.updated_at = datetime.now(UTC)

    async def commit(self) -> None:
        self.commit_calls += 1
        self.transaction_additions.clear()

    async def rollback(self) -> None:
        self.rollback_calls += 1
        for item in self.transaction_additions:
            if item in self.items:
                self.items.remove(item)
        self.transaction_additions.clear()
        self.pending.clear()


def _parameter(params: dict[str, object], prefix: str):
    return next(
        (value for name, value in params.items() if name.startswith(f"{prefix}_")),
        None,
    )


def _user() -> User:
    return User(
        id=uuid4(),
        email="owner@example.com",
        password_hash="hash",
        first_name="Owner",
        status="active",
        is_email_verified=True,
    )


def _business(business_id: UUID, name: str, slug: str) -> Business:
    return Business(
        id=business_id,
        name=name,
        slug=slug,
        business_type="retail",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
    )


def _membership(business: Business, user: User) -> BusinessMembership:
    return BusinessMembership(
        id=uuid4(),
        business_id=business.id,
        user_id=user.id,
        role="owner",
        status="active",
    )


def _catalog_item(
    business_id: UUID,
    name: str,
    *,
    item_type: str = "product",
    description: str | None = None,
    sku: str | None = None,
    price: Decimal | None = None,
    status: str = "active",
) -> CatalogItem:
    sequence = _catalog_item.counter
    _catalog_item.counter += 1
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence)
    return CatalogItem(
        id=uuid4(),
        business_id=business_id,
        item_type=item_type,
        name=name,
        description=description,
        sku=sku,
        price=price,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )


_catalog_item.counter = 0
