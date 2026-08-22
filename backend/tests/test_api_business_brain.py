import os
import unittest
from datetime import UTC, datetime, timedelta
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
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.business_membership import BusinessMembership
from app.models.user import User
from app.schemas.business_brain import MAX_KNOWLEDGE_CONTENT_LENGTH

BUSINESS_A_ID = UUID("30000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("40000000-0000-0000-0000-000000000002")


class BusinessBrainApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _user()
        self.business_a = _business(BUSINESS_A_ID, "Tenant A", "brain-tenant-a")
        self.business_b = _business(BUSINESS_B_ID, "Tenant B", "brain-tenant-b")
        self.membership_a = _membership(self.business_a, self.user)
        self.membership_b = _membership(self.business_b, self.user)
        self.accessible = {
            BUSINESS_A_ID: (self.business_a, self.membership_a),
        }
        self.session = _KnowledgeSession()
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

    def test_openapi_exposes_only_typed_knowledge_crud(self) -> None:
        schema = app.openapi()
        base = "/api/v1/businesses/{business_id}/brain/knowledge"
        self.assertEqual(set(schema["paths"][base]), {"get", "post"})
        self.assertEqual(
            set(schema["paths"][f"{base}/{{entry_id}}"]),
            {"get", "patch", "delete"},
        )
        create_schema = schema["components"]["schemas"]["BusinessKnowledgeEntryCreate"]
        self.assertEqual(
            set(create_schema["properties"]),
            {"category", "title", "content", "status"},
        )

    async def test_authentication_is_required_and_private(self) -> None:
        del app.dependency_overrides[get_business_access]
        del app.dependency_overrides[get_current_user]

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self._assert_private(response)
        self.assertEqual(self.session.execute_calls, 0)

    async def test_business_membership_is_required_with_safe_404(self) -> None:
        del app.dependency_overrides[get_business_access]
        self.session.access_row = None

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Business not found."})
        self._assert_private(response)
        self.assertEqual(self.session.scalars_calls, 0)

    async def test_empty_list_is_tenant_scoped_and_not_cached(self) -> None:
        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.session.requested_business_id, BUSINESS_A_ID)
        self.assertEqual(self.session.commit_calls, 0)
        self._assert_private(response)

    async def test_create_returns_201_and_forces_manual_source(self) -> None:
        response = await self.client.post(
            self._base_url(),
            json={
                "category": "policy",
                "title": "  Returns  ",
                "content": "  Returns are accepted within 30 days.  ",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["business_id"], str(BUSINESS_A_ID))
        self.assertEqual(payload["title"], "Returns")
        self.assertEqual(payload["content"], "Returns are accepted within 30 days.")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["source_type"], "manual")
        self.assertIsNone(payload["source_reference"])
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_create_rejects_source_spoofing_and_unknown_fields(self) -> None:
        for field, value in (
            ("source_type", "system"),
            ("source_reference", "internal:1"),
            ("business_id", str(BUSINESS_B_ID)),
            ("unknown", "value"),
        ):
            with self.subTest(field=field):
                response = await self.client.post(
                    self._base_url(),
                    json={
                        "category": "general",
                        "title": "Question",
                        "content": "Answer",
                        field: value,
                    },
                )
                self.assertEqual(response.status_code, 422)
                self._assert_private(response)
        self.assertEqual(self.session.entries, [])

    async def test_list_returns_only_the_authorized_tenant(self) -> None:
        own = _entry(BUSINESS_A_ID, "Own")
        other = _entry(BUSINESS_B_ID, "Private")
        self.session.entries.extend([other, own])

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["title"] for row in response.json()], ["Own"])
        self.assertNotIn("Private", response.text)

    async def test_category_and_status_filters_are_independent(self) -> None:
        general = _entry(BUSINESS_A_ID, "General", category="general")
        faq = _entry(BUSINESS_A_ID, "FAQ", category="faq")
        archived_faq = _entry(
            BUSINESS_A_ID,
            "Old FAQ",
            category="faq",
            status="archived",
        )
        self.session.entries.extend([archived_faq, faq, general])

        category_response = await self.client.get(f"{self._base_url()}?category=faq")
        archived_response = await self.client.get(
            f"{self._base_url()}?category=faq&status=archived"
        )

        self.assertEqual(
            [row["title"] for row in category_response.json()],
            ["FAQ"],
        )
        self.assertEqual(
            [row["title"] for row in archived_response.json()],
            ["Old FAQ"],
        )

    async def test_default_list_excludes_archived_and_orders_deterministically(
        self,
    ) -> None:
        later = _entry(BUSINESS_A_ID, "Later")
        archived = _entry(BUSINESS_A_ID, "Archived", status="archived")
        earlier = _entry(BUSINESS_A_ID, "Earlier")
        later.created_at = earlier.created_at + timedelta(days=1)
        self.session.entries.extend([later, archived, earlier])

        response = await self.client.get(self._base_url())

        self.assertEqual(
            [row["title"] for row in response.json()],
            ["Earlier", "Later"],
        )

    async def test_invalid_filters_are_rejected(self) -> None:
        for query in ("category=inventory", "status=deleted"):
            with self.subTest(query=query):
                response = await self.client.get(f"{self._base_url()}?{query}")
                self.assertEqual(response.status_code, 422)
                self._assert_private(response)

    async def test_get_one_uses_business_and_entry_identifiers(self) -> None:
        entry = _entry(BUSINESS_A_ID, "Support hours")
        self.session.entries.append(entry)

        response = await self.client.get(f"{self._base_url()}/{entry.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], str(entry.id))
        self.assertEqual(self.session.requested_business_id, BUSINESS_A_ID)
        self.assertEqual(self.session.requested_entry_id, entry.id)
        self._assert_private(response)

    async def test_wrong_tenant_item_is_safe_404(self) -> None:
        entry = _entry(BUSINESS_B_ID, "Private policy")
        self.session.entries.append(entry)

        response = await self.client.get(f"{self._base_url()}/{entry.id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Knowledge entry not found."})
        self.assertNotIn("Private policy", response.text)
        self._assert_private(response)

    async def test_patch_changes_only_supplied_fields_and_refreshes_updated_at(
        self,
    ) -> None:
        entry = _entry(
            BUSINESS_A_ID,
            "Original",
            content="Original content",
            category="general",
        )
        old_updated_at = entry.updated_at
        self.session.entries.append(entry)

        response = await self.client.patch(
            f"{self._base_url()}/{entry.id}",
            json={"title": "  Updated  ", "status": "draft"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["title"], "Updated")
        self.assertEqual(payload["content"], "Original content")
        self.assertEqual(payload["category"], "general")
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["source_type"], "manual")
        self.assertEqual(self.session.refresh_calls, 1)
        self.assertNotEqual(entry.updated_at, old_updated_at)
        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_patch_rejects_blank_null_and_excessive_content(self) -> None:
        entry = _entry(BUSINESS_A_ID, "Original")
        self.session.entries.append(entry)
        cases = (
            {"title": "   "},
            {"content": "   "},
            {"title": None},
            {"content": "x" * (MAX_KNOWLEDGE_CONTENT_LENGTH + 1)},
        )
        for payload in cases:
            with self.subTest(field=next(iter(payload))):
                response = await self.client.patch(
                    f"{self._base_url()}/{entry.id}",
                    json=payload,
                )
                self.assertEqual(response.status_code, 422)
                self._assert_private(response)
        self.assertEqual(entry.title, "Original")
        self.assertEqual(self.session.commit_calls, 0)

    async def test_patch_cannot_change_source_or_business(self) -> None:
        entry = _entry(BUSINESS_A_ID, "Original")
        self.session.entries.append(entry)
        for field in ("source_type", "source_reference", "business_id"):
            with self.subTest(field=field):
                response = await self.client.patch(
                    f"{self._base_url()}/{entry.id}",
                    json={field: "forbidden"},
                )
                self.assertEqual(response.status_code, 422)
        self.assertEqual(entry.source_type, "manual")
        self.assertIsNone(entry.source_reference)
        self.assertEqual(entry.business_id, BUSINESS_A_ID)

    async def test_delete_archives_physically_retained_entry_and_is_idempotent(
        self,
    ) -> None:
        entry = _entry(BUSINESS_A_ID, "Retain me")
        self.session.entries.append(entry)
        url = f"{self._base_url()}/{entry.id}"

        first = await self.client.delete(url)
        flushes_after_first = self.session.flush_calls
        second = await self.client.delete(url)

        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)
        self.assertEqual(first.content, b"")
        self.assertEqual(entry.status, "archived")
        self.assertIn(entry, self.session.entries)
        self.assertEqual(self.session.flush_calls, flushes_after_first)
        self.assertEqual(self.session.commit_calls, 2)
        self._assert_private(first)
        self._assert_private(second)

    async def test_archived_status_filter_returns_archived_entry(self) -> None:
        entry = _entry(BUSINESS_A_ID, "Archived", status="archived")
        self.session.entries.append(entry)

        response = await self.client.get(f"{self._base_url()}?status=archived")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["title"] for row in response.json()], ["Archived"])

    async def test_cross_tenant_patch_and_delete_are_impossible(self) -> None:
        entry = _entry(BUSINESS_B_ID, "Private")
        self.session.entries.append(entry)
        url = f"{self._base_url(BUSINESS_B_ID)}/{entry.id}"

        patch_response = await self.client.patch(url, json={"title": "Stolen"})
        delete_response = await self.client.delete(url)

        self.assertEqual(patch_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(entry.title, "Private")
        self.assertEqual(entry.status, "active")
        self._assert_private(patch_response)
        self._assert_private(delete_response)

    async def test_persistence_failure_is_safe_and_rolls_back(self) -> None:
        self.session.flush_error = SQLAlchemyError("private database detail")

        response = await self.client.post(
            self._base_url(),
            json={"category": "general", "title": "Title", "content": "Content"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Business Brain knowledge is temporarily unavailable."},
        )
        self.assertNotIn("private database detail", response.text)
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)
        self._assert_private(response)

    def _base_url(self, business_id: UUID = BUSINESS_A_ID) -> str:
        return f"/api/v1/businesses/{business_id}/brain/knowledge"

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


class _KnowledgeSession:
    def __init__(self) -> None:
        self.entries: list[BusinessKnowledgeEntry] = []
        self.pending: list[BusinessKnowledgeEntry] = []
        self.transaction_additions: list[BusinessKnowledgeEntry] = []
        self.access_row = None
        self.flush_error: SQLAlchemyError | None = None
        self.execute_calls = 0
        self.scalars_calls = 0
        self.flush_calls = 0
        self.refresh_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.requested_business_id: UUID | None = None
        self.requested_entry_id: UUID | None = None

    async def execute(self, statement) -> _AccessResult:
        self.execute_calls += 1
        return _AccessResult(self.access_row)

    async def scalar(self, statement):
        params = statement.compile().params
        business_id = _parameter(params, "business_id")
        entry_id = _parameter(params, "id")
        self.requested_business_id = business_id
        self.requested_entry_id = entry_id
        matches = [entry for entry in self.entries if entry.business_id == business_id]
        if entry_id is not None:
            matches = [entry for entry in matches if entry.id == entry_id]
        return matches[0] if matches else None

    async def scalars(self, statement) -> _ScalarResult:
        self.scalars_calls += 1
        sql = str(statement)
        params = statement.compile().params
        business_id = _parameter(params, "business_id")
        category = _parameter(params, "category")
        entry_status = _parameter(params, "status")
        self.requested_business_id = business_id
        entries = [entry for entry in self.entries if entry.business_id == business_id]
        if category is not None:
            entries = [entry for entry in entries if entry.category == category]
        if entry_status is not None:
            if "business_knowledge_entries.status !=" in sql:
                entries = [entry for entry in entries if entry.status != entry_status]
            else:
                entries = [entry for entry in entries if entry.status == entry_status]
        return _ScalarResult(
            sorted(entries, key=lambda entry: (entry.created_at, entry.id))
        )

    def add(self, entry: BusinessKnowledgeEntry) -> None:
        self.pending.append(entry)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error
        now = datetime.now(UTC)
        for index, entry in enumerate(self.pending):
            if entry.id is None:
                entry.id = uuid4()
            if entry.created_at is None:
                entry.created_at = now + timedelta(microseconds=index)
            if entry.updated_at is None:
                entry.updated_at = entry.created_at
            self.entries.append(entry)
            self.transaction_additions.append(entry)
        self.pending.clear()

    async def refresh(
        self,
        entry: BusinessKnowledgeEntry,
        *,
        attribute_names=None,
    ) -> None:
        self.refresh_calls += 1
        if attribute_names == ["updated_at"]:
            entry.updated_at = datetime.now(UTC)

    async def commit(self) -> None:
        self.commit_calls += 1
        self.transaction_additions.clear()

    async def rollback(self) -> None:
        self.rollback_calls += 1
        for entry in self.transaction_additions:
            if entry in self.entries:
                self.entries.remove(entry)
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
        email="brain-owner@example.com",
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


def _entry(
    business_id: UUID,
    title: str,
    *,
    content: str = "Answer",
    category: str = "general",
    status: str = "active",
) -> BusinessKnowledgeEntry:
    sequence = _entry.counter
    _entry.counter += 1
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence)
    return BusinessKnowledgeEntry(
        id=uuid4(),
        business_id=business_id,
        category=category,
        title=title,
        content=content,
        status=status,
        source_type="manual",
        source_reference=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


_entry.counter = 0
