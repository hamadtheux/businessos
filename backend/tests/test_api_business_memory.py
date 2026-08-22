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
from app.models.business_memory import BusinessMemory
from app.models.user import User
from app.schemas.business_memory import MAX_MEMORY_CONTENT_LENGTH
from app.services.business_memory import build_memory_content_hash

BUSINESS_A_ID = UUID("50000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("60000000-0000-0000-0000-000000000002")


class BusinessMemoryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.user = _user()
        self.business_a = _business(
            BUSINESS_A_ID,
            "Memory Tenant A",
            "memory-tenant-a",
        )
        self.business_b = _business(
            BUSINESS_B_ID,
            "Memory Tenant B",
            "memory-tenant-b",
        )
        self.membership_a = _membership(self.business_a, self.user)
        self.membership_b = _membership(self.business_b, self.user)

        self.accessible = {
            BUSINESS_A_ID: (self.business_a, self.membership_a),
        }

        self.session = _MemorySession()
        self.original_dependency_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_user() -> User:
            return self.user

        async def override_access(
            business_id: UUID,
        ) -> BusinessAccessContext:
            context = self.accessible.get(business_id)
            if context is None:
                raise HTTPException(
                    status_code=404,
                    detail="Business not found.",
                )

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
        app.dependency_overrides.update(
            self.original_dependency_overrides
        )

    def test_openapi_exposes_only_public_memory_crud(self) -> None:
        schema = app.openapi()

        base = "/api/v1/businesses/{business_id}/memory"

        self.assertEqual(
            set(schema["paths"][base]),
            {"get", "post"},
        )
        self.assertEqual(
            set(schema["paths"][f"{base}/{{memory_id}}"]),
            {"get", "patch", "delete"},
        )

        create_schema = schema["components"]["schemas"][
            "BusinessMemoryCreate"
        ]

        self.assertEqual(
            set(create_schema["properties"]),
            {
                "memory_type",
                "content",
                "importance",
                "occurred_at",
            },
        )

        forbidden = {
            "business_id",
            "status",
            "confidence",
            "source_type",
            "source_reference",
            "content_hash",
            "last_reinforced_at",
            "superseded_by_memory_id",
            "created_at",
            "updated_at",
        }

        self.assertTrue(
            forbidden.isdisjoint(create_schema["properties"])
        )

    async def test_authentication_is_required_and_private(self) -> None:
        del app.dependency_overrides[get_business_access]
        del app.dependency_overrides[get_current_user]

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers["WWW-Authenticate"],
            "Bearer",
        )
        self._assert_private(response)
        self.assertEqual(self.session.execute_calls, 0)

    async def test_business_membership_is_required_with_safe_404(
        self,
    ) -> None:
        del app.dependency_overrides[get_business_access]
        self.session.access_row = None

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "Business not found."},
        )
        self._assert_private(response)
        self.assertEqual(self.session.scalars_calls, 0)

    async def test_empty_list_is_tenant_scoped_bounded_and_private(
        self,
    ) -> None:
        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [],
                "next_cursor": None,
            },
        )
        self.assertEqual(
            self.session.requested_business_id,
            BUSINESS_A_ID,
        )
        self.assertEqual(self.session.commit_calls, 0)
        self._assert_private(response)

    async def test_create_returns_201_and_forces_trusted_manual_metadata(
        self,
    ) -> None:
        response = await self.client.post(
            self._base_url(),
            json={
                "memory_type": "semantic",
                "content": "  Customers prefer morning delivery.  ",
                "importance": 4,
            },
        )

        self.assertEqual(response.status_code, 201)

        payload = response.json()

        self.assertEqual(
            payload["business_id"],
            str(BUSINESS_A_ID),
        )
        self.assertEqual(
            payload["memory_type"],
            "semantic",
        )
        self.assertEqual(
            payload["content"],
            "Customers prefer morning delivery.",
        )
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["importance"], 4)
        self.assertEqual(
            Decimal(str(payload["confidence"])),
            Decimal("1.000"),
        )
        self.assertEqual(payload["source_type"], "manual")
        self.assertIsNone(payload["occurred_at"])
        self.assertIsNone(payload["last_reinforced_at"])
        self.assertIsNone(payload["superseded_by_memory_id"])

        self.assertNotIn("content_hash", payload)
        self.assertNotIn("source_reference", payload)

        self.assertEqual(self.session.commit_calls, 1)
        self._assert_private(response)

    async def test_create_rejects_all_trusted_field_spoofing(
        self,
    ) -> None:
        cases = (
            ("business_id", str(BUSINESS_B_ID)),
            ("status", "active"),
            ("confidence", "0.100"),
            ("source_type", "system"),
            ("source_reference", "conversation:private"),
            ("content_hash", "a" * 64),
            ("last_reinforced_at", datetime.now(UTC).isoformat()),
            ("superseded_by_memory_id", str(uuid4())),
            ("unknown", "value"),
        )

        for field, value in cases:
            with self.subTest(field=field):
                response = await self.client.post(
                    self._base_url(),
                    json={
                        "memory_type": "semantic",
                        "content": "Allowed content",
                        field: value,
                    },
                )

                self.assertEqual(response.status_code, 422)
                self._assert_private(response)

        self.assertEqual(self.session.memories, [])
        self.assertEqual(self.session.commit_calls, 0)

    async def test_create_validates_content_and_importance(self) -> None:
        cases = (
            {
                "memory_type": "semantic",
                "content": "   ",
            },
            {
                "memory_type": "semantic",
                "content": "x" * (MAX_MEMORY_CONTENT_LENGTH + 1),
            },
            {
                "memory_type": "semantic",
                "content": "Content",
                "importance": 0,
            },
            {
                "memory_type": "semantic",
                "content": "Content",
                "importance": 6,
            },
            {
                "memory_type": "unknown",
                "content": "Content",
            },
        )

        for payload in cases:
            with self.subTest(payload=payload):
                response = await self.client.post(
                    self._base_url(),
                    json=payload,
                )

                self.assertEqual(response.status_code, 422)
                self._assert_private(response)

        self.assertEqual(self.session.memories, [])

    async def test_default_list_returns_active_authorized_tenant_only(
        self,
    ) -> None:
        active_a = _memory(
            BUSINESS_A_ID,
            "Active A",
        )
        archived_a = _memory(
            BUSINESS_A_ID,
            "Archived A",
            status="archived",
        )
        active_b = _memory(
            BUSINESS_B_ID,
            "Private B",
        )

        self.session.memories.extend(
            [
                active_b,
                archived_a,
                active_a,
            ]
        )

        response = await self.client.get(self._base_url())

        self.assertEqual(response.status_code, 200)

        payload = response.json()

        self.assertEqual(
            [item["content"] for item in payload["items"]],
            ["Active A"],
        )
        self.assertNotIn("Private B", response.text)
        self.assertNotIn("Archived A", response.text)

    async def test_memory_type_and_status_filters_are_server_backed(
        self,
    ) -> None:
        semantic = _memory(
            BUSINESS_A_ID,
            "Semantic",
            memory_type="semantic",
        )
        episodic = _memory(
            BUSINESS_A_ID,
            "Episode",
            memory_type="episodic",
        )
        archived_semantic = _memory(
            BUSINESS_A_ID,
            "Old semantic",
            memory_type="semantic",
            status="archived",
        )

        self.session.memories.extend(
            [
                archived_semantic,
                episodic,
                semantic,
            ]
        )

        semantic_response = await self.client.get(
            f"{self._base_url()}?memory_type=semantic"
        )

        archived_response = await self.client.get(
            f"{self._base_url()}"
            "?memory_type=semantic&status=archived"
        )

        self.assertEqual(
            [
                item["content"]
                for item in semantic_response.json()["items"]
            ],
            ["Semantic"],
        )

        self.assertEqual(
            [
                item["content"]
                for item in archived_response.json()["items"]
            ],
            ["Old semantic"],
        )

    async def test_invalid_filters_and_limit_are_rejected(self) -> None:
        queries = (
            "memory_type=unknown",
            "status=deleted",
            "limit=0",
            "limit=201",
        )

        for query in queries:
            with self.subTest(query=query):
                response = await self.client.get(
                    f"{self._base_url()}?{query}"
                )

                self.assertEqual(response.status_code, 422)
                self._assert_private(response)

    async def test_pagination_is_deterministic_without_duplicates(
        self,
    ) -> None:
        first = _memory(BUSINESS_A_ID, "Oldest")
        second = _memory(BUSINESS_A_ID, "Middle")
        third = _memory(BUSINESS_A_ID, "Newest")

        self.session.memories.extend(
            [
                first,
                second,
                third,
            ]
        )

        page_one = await self.client.get(
            f"{self._base_url()}?limit=2"
        )

        self.assertEqual(page_one.status_code, 200)

        page_one_payload = page_one.json()

        self.assertEqual(
            [
                item["content"]
                for item in page_one_payload["items"]
            ],
            ["Newest", "Middle"],
        )

        self.assertIsNotNone(page_one_payload["next_cursor"])

        page_two = await self.client.get(
            self._base_url(),
            params={
                "limit": 2,
                "cursor": page_one_payload["next_cursor"],
            },
        )

        self.assertEqual(page_two.status_code, 200)

        page_two_payload = page_two.json()

        self.assertEqual(
            [
                item["content"]
                for item in page_two_payload["items"]
            ],
            ["Oldest"],
        )

        self.assertIsNone(page_two_payload["next_cursor"])

        page_one_ids = {
            item["id"]
            for item in page_one_payload["items"]
        }
        page_two_ids = {
            item["id"]
            for item in page_two_payload["items"]
        }

        self.assertTrue(page_one_ids.isdisjoint(page_two_ids))

    async def test_malformed_cursor_is_safe_422(self) -> None:
        response = await self.client.get(
            self._base_url(),
            params={"cursor": "definitely-not-a-valid-cursor"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {"detail": "Invalid memory pagination cursor."},
        )
        self._assert_private(response)

    async def test_get_one_uses_business_and_memory_identifiers(
        self,
    ) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Tenant-scoped memory",
        )

        self.session.memories.append(memory)

        response = await self.client.get(
            f"{self._base_url()}/{memory.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["id"],
            str(memory.id),
        )
        self.assertEqual(
            self.session.requested_business_id,
            BUSINESS_A_ID,
        )
        self.assertEqual(
            self.session.requested_memory_id,
            memory.id,
        )
        self._assert_private(response)

    async def test_wrong_tenant_memory_is_safe_404(self) -> None:
        memory = _memory(
            BUSINESS_B_ID,
            "Private tenant memory",
        )

        self.session.memories.append(memory)

        response = await self.client.get(
            f"{self._base_url()}/{memory.id}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "Business memory not found."},
        )
        self.assertNotIn(
            "Private tenant memory",
            response.text,
        )
        self._assert_private(response)

    async def test_patch_changes_only_supplied_fields_and_rehashes(
        self,
    ) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Original memory",
            memory_type="semantic",
            importance=3,
        )

        old_hash = memory.content_hash
        old_updated_at = memory.updated_at

        self.session.memories.append(memory)

        response = await self.client.patch(
            f"{self._base_url()}/{memory.id}",
            json={
                "content": "  Updated memory  ",
                "importance": 5,
            },
        )

        self.assertEqual(response.status_code, 200)

        payload = response.json()

        self.assertEqual(
            payload["content"],
            "Updated memory",
        )
        self.assertEqual(payload["importance"], 5)
        self.assertEqual(
            payload["memory_type"],
            "semantic",
        )
        self.assertEqual(payload["status"], "active")
        self.assertEqual(payload["source_type"], "manual")

        self.assertNotEqual(memory.content_hash, old_hash)
        self.assertNotEqual(memory.updated_at, old_updated_at)

        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.session.refresh_calls, 1)

        self._assert_private(response)

    async def test_memory_type_change_recalculates_hash(self) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Same content",
            memory_type="semantic",
        )

        old_hash = memory.content_hash

        self.session.memories.append(memory)

        response = await self.client.patch(
            f"{self._base_url()}/{memory.id}",
            json={"memory_type": "procedural"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["memory_type"],
            "procedural",
        )
        self.assertNotEqual(memory.content_hash, old_hash)

    async def test_patch_rejects_null_required_and_trusted_fields(
        self,
    ) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Original",
        )

        self.session.memories.append(memory)

        cases = (
            {"memory_type": None},
            {"content": None},
            {"importance": None},
            {"status": None},
            {"content": "   "},
            {"content_hash": "a" * 64},
            {"source_type": "system"},
            {"source_reference": "private"},
            {"confidence": "0.100"},
            {"business_id": str(BUSINESS_B_ID)},
            {"superseded_by_memory_id": str(uuid4())},
            {"last_reinforced_at": datetime.now(UTC).isoformat()},
        )

        for payload in cases:
            with self.subTest(payload=payload):
                response = await self.client.patch(
                    f"{self._base_url()}/{memory.id}",
                    json=payload,
                )

                self.assertEqual(response.status_code, 422)
                self._assert_private(response)

        self.assertEqual(memory.content, "Original")
        self.assertEqual(memory.source_type, "manual")

    async def test_public_patch_cannot_directly_claim_superseded(
        self,
    ) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Current",
        )

        self.session.memories.append(memory)

        response = await self.client.patch(
            f"{self._base_url()}/{memory.id}",
            json={"status": "superseded"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "detail": (
                    "Requested memory lifecycle transition "
                    "is invalid."
                )
            },
        )
        self.assertEqual(memory.status, "active")
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.rollback_calls, 1)
        self._assert_private(response)

    async def test_occurred_at_can_be_explicitly_cleared(self) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Event",
        )
        memory.occurred_at = datetime(
            2026,
            1,
            10,
            12,
            0,
            tzinfo=UTC,
        )

        self.session.memories.append(memory)

        response = await self.client.patch(
            f"{self._base_url()}/{memory.id}",
            json={"occurred_at": None},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["occurred_at"])
        self.assertIsNone(memory.occurred_at)

    async def test_delete_archives_and_is_idempotent(self) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Retain this history",
        )

        self.session.memories.append(memory)

        url = f"{self._base_url()}/{memory.id}"

        first = await self.client.delete(url)
        flushes_after_first = self.session.flush_calls

        second = await self.client.delete(url)

        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)

        self.assertEqual(first.content, b"")
        self.assertEqual(second.content, b"")

        self.assertEqual(memory.status, "archived")
        self.assertIn(memory, self.session.memories)

        self.assertEqual(
            self.session.flush_calls,
            flushes_after_first,
        )

        self.assertEqual(self.session.commit_calls, 2)

        self._assert_private(first)
        self._assert_private(second)

    async def test_archived_filter_returns_archived_memory(self) -> None:
        memory = _memory(
            BUSINESS_A_ID,
            "Historical",
            status="archived",
        )

        self.session.memories.append(memory)

        response = await self.client.get(
            f"{self._base_url()}?status=archived"
        )

        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            [
                item["content"]
                for item in response.json()["items"]
            ],
            ["Historical"],
        )

    async def test_cross_tenant_create_get_patch_delete_are_denied(
        self,
    ) -> None:
        private_memory = _memory(
            BUSINESS_B_ID,
            "Business B private memory",
        )

        self.session.memories.append(private_memory)

        create_response = await self.client.post(
            self._base_url(BUSINESS_B_ID),
            json={
                "memory_type": "semantic",
                "content": "Attempted write",
            },
        )

        get_response = await self.client.get(
            f"{self._base_url(BUSINESS_B_ID)}/{private_memory.id}"
        )

        patch_response = await self.client.patch(
            f"{self._base_url(BUSINESS_B_ID)}/{private_memory.id}",
            json={"content": "Stolen"},
        )

        delete_response = await self.client.delete(
            f"{self._base_url(BUSINESS_B_ID)}/{private_memory.id}"
        )

        for response in (
            create_response,
            get_response,
            patch_response,
            delete_response,
        ):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(
                response.json(),
                {"detail": "Business not found."},
            )
            self._assert_private(response)

        self.assertEqual(
            private_memory.content,
            "Business B private memory",
        )
        self.assertEqual(private_memory.status, "active")

    async def test_persistence_failure_is_safe_and_rolls_back(
        self,
    ) -> None:
        self.session.flush_error = SQLAlchemyError(
            "private database implementation detail"
        )

        response = await self.client.post(
            self._base_url(),
            json={
                "memory_type": "semantic",
                "content": "Safe public content",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "detail": (
                    "Business memory is temporarily unavailable."
                )
            },
        )

        self.assertNotIn(
            "private database implementation detail",
            response.text,
        )

        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.memories, [])

        self._assert_private(response)

    def _base_url(
        self,
        business_id: UUID = BUSINESS_A_ID,
    ) -> str:
        return f"/api/v1/businesses/{business_id}/memory"

    def _assert_private(
        self,
        response: httpx.Response,
    ) -> None:
        self.assertEqual(
            response.headers["Cache-Control"],
            "no-store",
        )
        self.assertEqual(
            response.headers["Pragma"],
            "no-cache",
        )


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


class _MemorySession:
    def __init__(self) -> None:
        self.memories: list[BusinessMemory] = []
        self.pending: list[BusinessMemory] = []
        self.transaction_additions: list[BusinessMemory] = []

        self.access_row = None
        self.flush_error: SQLAlchemyError | None = None

        self.execute_calls = 0
        self.scalars_calls = 0
        self.flush_calls = 0
        self.refresh_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

        self.requested_business_id: UUID | None = None
        self.requested_memory_id: UUID | None = None

    async def execute(self, statement) -> _AccessResult:
        self.execute_calls += 1
        return _AccessResult(self.access_row)

    async def scalar(self, statement):
        params = statement.compile().params

        business_id = _parameter(
            params,
            "business_id",
        )
        memory_id = _parameter(
            params,
            "id",
        )

        self.requested_business_id = business_id
        self.requested_memory_id = memory_id

        matches = [
            memory
            for memory in self.memories
            if memory.business_id == business_id
        ]

        if memory_id is not None:
            matches = [
                memory
                for memory in matches
                if memory.id == memory_id
            ]

        return matches[0] if matches else None

    async def scalars(self, statement) -> _ScalarResult:
        self.scalars_calls += 1

        params = statement.compile().params

        business_id = _parameter(
            params,
            "business_id",
        )
        memory_type = _parameter(
            params,
            "memory_type",
        )
        memory_status = _parameter(
            params,
            "status",
        )
        cursor_created_at = _parameter(
            params,
            "created_at",
        )
        cursor_id = _parameter(
            params,
            "id",
        )

        self.requested_business_id = business_id

        memories = [
            memory
            for memory in self.memories
            if memory.business_id == business_id
        ]

        if memory_type is not None:
            memories = [
                memory
                for memory in memories
                if memory.memory_type == memory_type
            ]

        if memory_status is not None:
            memories = [
                memory
                for memory in memories
                if memory.status == memory_status
            ]

        if cursor_created_at is not None:
            memories = [
                memory
                for memory in memories
                if (
                    memory.created_at < cursor_created_at
                    or (
                        memory.created_at == cursor_created_at
                        and cursor_id is not None
                        and memory.id.int < cursor_id.int
                    )
                )
            ]

        memories.sort(
            key=lambda memory: (
                memory.created_at,
                memory.id.int,
            ),
            reverse=True,
        )

        return _ScalarResult(memories)

    def add(self, memory: BusinessMemory) -> None:
        self.pending.append(memory)

    async def flush(self) -> None:
        self.flush_calls += 1

        if self.flush_error is not None:
            raise self.flush_error

        now = datetime.now(UTC)

        for index, memory in enumerate(self.pending):
            if memory.id is None:
                memory.id = uuid4()

            if memory.created_at is None:
                memory.created_at = (
                    now + timedelta(microseconds=index)
                )

            if memory.updated_at is None:
                memory.updated_at = memory.created_at

            self.memories.append(memory)
            self.transaction_additions.append(memory)

        self.pending.clear()

    async def refresh(
        self,
        memory: BusinessMemory,
        *,
        attribute_names=None,
    ) -> None:
        self.refresh_calls += 1

        if attribute_names == ["updated_at"]:
            memory.updated_at = (
                datetime.now(UTC) + timedelta(seconds=1)
            )

    async def commit(self) -> None:
        self.commit_calls += 1
        self.transaction_additions.clear()

    async def rollback(self) -> None:
        self.rollback_calls += 1

        for memory in self.transaction_additions:
            if memory in self.memories:
                self.memories.remove(memory)

        self.transaction_additions.clear()
        self.pending.clear()


def _parameter(
    params: dict[str, object],
    prefix: str,
):
    return next(
        (
            value
            for name, value in params.items()
            if name.startswith(f"{prefix}_")
        ),
        None,
    )


def _user() -> User:
    return User(
        id=uuid4(),
        email="memory-owner@example.com",
        password_hash="hash",
        first_name="Owner",
        status="active",
        is_email_verified=True,
    )


def _business(
    business_id: UUID,
    name: str,
    slug: str,
) -> Business:
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


def _membership(
    business: Business,
    user: User,
) -> BusinessMembership:
    return BusinessMembership(
        id=uuid4(),
        business_id=business.id,
        user_id=user.id,
        role="owner",
        status="active",
    )


def _memory(
    business_id: UUID,
    content: str,
    *,
    memory_type: str = "semantic",
    status: str = "active",
    importance: int = 3,
) -> BusinessMemory:
    sequence = _memory.counter
    _memory.counter += 1

    timestamp = (
        datetime(2026, 1, 1, tzinfo=UTC)
        + timedelta(seconds=sequence)
    )

    return BusinessMemory(
        id=uuid4(),
        business_id=business_id,
        memory_type=memory_type,
        content=content,
        status=status,
        importance=importance,
        confidence=Decimal("1.000"),
        source_type="manual",
        source_reference=None,
        occurred_at=None,
        last_reinforced_at=None,
        content_hash=build_memory_content_hash(
            memory_type,
            content,
        ),
        superseded_by_memory_id=None,
        created_at=timestamp,
        updated_at=timestamp,
    )


_memory.counter = 0