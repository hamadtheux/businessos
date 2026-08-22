import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from app.exceptions.ai_context import AIContextAssemblyError  # noqa: E402
from app.exceptions.business_brain import BusinessBrainAssemblyError  # noqa: E402
from app.models.business_memory import BusinessMemory  # noqa: E402
from app.schemas.ai_context import (  # noqa: E402
    AIContextBundle,
    AIContextRequest,
    BusinessBrainContextSource,
)
from app.services.ai_context import assemble_ai_context  # noqa: E402
from app.services.business_brain_assembly import BusinessBrainSource  # noqa: E402
from app.services.business_memory import build_memory_content_hash  # noqa: E402


BUSINESS_A_ID = UUID("61000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("62000000-0000-0000-0000-000000000002")

MEMORY_A_ID = UUID("63000000-0000-0000-0000-000000000001")
MEMORY_B_ID = UUID("63000000-0000-0000-0000-000000000002")
MEMORY_C_ID = UUID("63000000-0000-0000-0000-000000000003")

BASE_TIME = datetime(
    2026,
    8,
    21,
    10,
    0,
    tzinfo=UTC,
)


class AIContextSchemaTests(unittest.TestCase):
    def test_request_trims_task_and_keeps_typed_filters(self) -> None:
        request = AIContextRequest(
            purpose="sales",
            task="  Follow up with qualified lead.  ",
            brain_source_types=[
                "business_profile",
                "catalog_item",
            ],
            memory_types=[
                "customer",
                "decision",
            ],
            min_memory_importance=3,
            min_memory_confidence=Decimal("0.750"),
        )

        self.assertEqual(
            request.task,
            "Follow up with qualified lead.",
        )
        self.assertEqual(
            request.brain_source_types,
            [
                "business_profile",
                "catalog_item",
            ],
        )
        self.assertEqual(
            request.memory_types,
            [
                "customer",
                "decision",
            ],
        )
        self.assertEqual(
            request.min_memory_importance,
            3,
        )
        self.assertEqual(
            request.min_memory_confidence,
            Decimal("0.750"),
        )

    def test_duplicate_brain_source_types_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AIContextRequest(
                task="Prepare context.",
                brain_source_types=[
                    "catalog_item",
                    "catalog_item",
                ],
            )

    def test_duplicate_memory_types_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AIContextRequest(
                task="Prepare context.",
                memory_types=[
                    "customer",
                    "customer",
                ],
            )

    def test_at_least_one_context_source_must_be_enabled(self) -> None:
        with self.assertRaises(ValidationError):
            AIContextRequest(
                task="Prepare context.",
                include_business_brain=False,
                include_memory=False,
            )

    def test_brain_filter_cannot_be_used_when_brain_is_disabled(self) -> None:
        with self.assertRaises(ValidationError):
            AIContextRequest(
                task="Prepare memory context.",
                include_business_brain=False,
                include_memory=True,
                brain_source_types=[
                    "catalog_item",
                ],
            )

    def test_memory_filter_cannot_be_used_when_memory_is_disabled(self) -> None:
        with self.assertRaises(ValidationError):
            AIContextRequest(
                task="Prepare Business Brain context.",
                include_business_brain=True,
                include_memory=False,
                memory_types=[
                    "customer",
                ],
            )

    def test_bundle_rejects_incorrect_source_counts(self) -> None:
        source = BusinessBrainContextSource(
            business_id=BUSINESS_A_ID,
            source_type="business_profile",
            source_id="business:profile",
            title="Business profile",
            content="Name: Tenant A",
            updated_at=BASE_TIME,
            content_hash=sha256(
                b"Name: Tenant A"
            ).hexdigest(),
        )

        with self.assertRaises(ValidationError):
            AIContextBundle(
                business_id=BUSINESS_A_ID,
                purpose="general",
                task="Prepare context.",
                sources=[source],
                source_count=2,
                business_brain_source_count=1,
                memory_source_count=0,
                revision="a" * 64,
            )


class AIContextAssemblyTests(
    unittest.IsolatedAsyncioTestCase,
):
    async def test_business_brain_filter_and_limit_are_enforced(
        self,
    ) -> None:
        sources = [
            _brain_source(
                "business_profile",
                "business:profile",
                "Business profile",
                "Name: Tenant A",
            ),
            _brain_source(
                "branding",
                "business:branding",
                "Branding",
                "Primary color: #112233",
            ),
            _brain_source(
                "catalog_item",
                "catalog:1",
                "Milk",
                "Name: Milk",
            ),
            _brain_source(
                "knowledge_entry",
                "knowledge:1",
                "Returns",
                "Content: Returns accepted.",
            ),
        ]

        session = _MemorySession()

        request = AIContextRequest(
            purpose="sales",
            task="Prepare product context.",
            include_business_brain=True,
            include_memory=False,
            brain_source_types=[
                "catalog_item",
                "knowledge_entry",
            ],
            brain_source_limit=1,
        )

        with patch(
            "app.services.ai_context.iterate_business_brain_sources",
            new=_brain_iterator(sources),
        ):
            bundle = await assemble_ai_context(
                session,
                BUSINESS_A_ID,
                request,
            )

        self.assertEqual(
            bundle.source_count,
            1,
        )
        self.assertEqual(
            bundle.business_brain_source_count,
            1,
        )
        self.assertEqual(
            bundle.memory_source_count,
            0,
        )

        self.assertEqual(
            bundle.sources[0].origin,
            "business_brain",
        )
        self.assertEqual(
            bundle.sources[0].source_type,
            "catalog_item",
        )
        self.assertEqual(
            bundle.sources[0].title,
            "Milk",
        )

        self.assertEqual(
            session.scalars_calls,
            0,
        )

    async def test_memory_query_is_tenant_scoped_active_and_thresholded(
        self,
    ) -> None:
        memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="customer",
            content="Customer prefers WhatsApp.",
            importance=5,
            confidence=Decimal("0.900"),
        )

        session = _MemorySession(
            memories=[memory],
        )

        request = AIContextRequest(
            purpose="sales",
            task="Prepare customer context.",
            include_business_brain=False,
            include_memory=True,
            memory_types=[
                "customer",
                "decision",
            ],
            memory_limit=10,
            min_memory_importance=4,
            min_memory_confidence=Decimal("0.800"),
        )

        bundle = await assemble_ai_context(
            session,
            BUSINESS_A_ID,
            request,
        )

        self.assertEqual(
            bundle.source_count,
            1,
        )
        self.assertEqual(
            bundle.business_brain_source_count,
            0,
        )
        self.assertEqual(
            bundle.memory_source_count,
            1,
        )

        source = bundle.sources[0]

        self.assertEqual(
            source.origin,
            "business_memory",
        )
        self.assertEqual(
            source.business_id,
            BUSINESS_A_ID,
        )
        self.assertEqual(
            source.memory_id,
            MEMORY_A_ID,
        )
        self.assertEqual(
            source.memory_type,
            "customer",
        )
        self.assertEqual(
            source.importance,
            5,
        )
        self.assertEqual(
            source.confidence,
            Decimal("0.900"),
        )

        self.assertEqual(
            session.scalars_calls,
            1,
        )

        statement = session.statements[0]
        sql = str(statement)
        params = statement.compile().params
        values = list(params.values())

        self.assertIn(
            "business_memories.business_id",
            sql,
        )
        self.assertIn(
            "business_memories.status",
            sql,
        )
        self.assertIn(
            "business_memories.importance",
            sql,
        )
        self.assertIn(
            "business_memories.confidence",
            sql,
        )
        self.assertIn(
            "business_memories.memory_type",
            sql,
        )

        self.assertIn(
            BUSINESS_A_ID,
            values,
        )
        self.assertIn(
            "active",
            values,
        )
        self.assertIn(
            4,
            values,
        )
        self.assertIn(
            Decimal("0.800"),
            values,
        )

        self.assertTrue(
            any(
                value == ["customer", "decision"]
                or value == (
                    "customer",
                    "decision",
                )
                for value in values
            )
        )

        self.assertIn(
            "ORDER BY business_memories.importance DESC",
            sql,
        )
        self.assertIn(
            "business_memories.confidence DESC",
            sql,
        )
        self.assertIn(
            "business_memories.updated_at DESC",
            sql,
        )
        self.assertIn(
            "business_memories.id DESC",
            sql,
        )

    async def test_brain_sources_are_followed_by_memory_sources(
        self,
    ) -> None:
        brain = [
            _brain_source(
                "business_profile",
                "business:profile",
                "Business profile",
                "Name: Tenant A",
            ),
            _brain_source(
                "catalog_item",
                "catalog:1",
                "Milk",
                "Name: Milk",
            ),
        ]

        memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="customer",
            content="Customer prefers WhatsApp.",
            importance=4,
            confidence=Decimal("0.900"),
        )

        session = _MemorySession(
            memories=[memory],
        )

        request = AIContextRequest(
            purpose="business_manager",
            task="Prepare combined business context.",
        )

        with patch(
            "app.services.ai_context.iterate_business_brain_sources",
            new=_brain_iterator(brain),
        ):
            bundle = await assemble_ai_context(
                session,
                BUSINESS_A_ID,
                request,
            )

        self.assertEqual(
            bundle.source_count,
            3,
        )
        self.assertEqual(
            bundle.business_brain_source_count,
            2,
        )
        self.assertEqual(
            bundle.memory_source_count,
            1,
        )

        self.assertEqual(
            [
                source.origin
                for source in bundle.sources
            ],
            [
                "business_brain",
                "business_brain",
                "business_memory",
            ],
        )

        self.assertEqual(
            bundle.business_id,
            BUSINESS_A_ID,
        )
        self.assertEqual(
            bundle.purpose,
            "business_manager",
        )
        self.assertEqual(
            bundle.task,
            "Prepare combined business context.",
        )

        self.assertRegex(
            bundle.revision,
            r"^[0-9a-f]{64}$",
        )

    async def test_mismatched_business_brain_tenant_is_rejected(
        self,
    ) -> None:
        brain = [
            _brain_source(
                "business_profile",
                "business:profile",
                "Other tenant",
                "Name: Tenant B",
                business_id=BUSINESS_B_ID,
            )
        ]

        request = AIContextRequest(
            task="Prepare context.",
            include_memory=False,
        )

        with patch(
            "app.services.ai_context.iterate_business_brain_sources",
            new=_brain_iterator(brain),
        ):
            with self.assertRaises(
                AIContextAssemblyError,
            ):
                await assemble_ai_context(
                    _MemorySession(),
                    BUSINESS_A_ID,
                    request,
                )

    async def test_mismatched_memory_tenant_is_rejected(
        self,
    ) -> None:
        leaked_memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_B_ID,
            memory_type="customer",
            content="Tenant B private memory.",
        )

        session = _MemorySession(
            memories=[leaked_memory],
        )

        request = AIContextRequest(
            task="Prepare memory.",
            include_business_brain=False,
        )

        with self.assertRaises(
            AIContextAssemblyError,
        ):
            await assemble_ai_context(
                session,
                BUSINESS_A_ID,
                request,
            )

    async def test_non_active_memory_returned_by_database_is_rejected(
        self,
    ) -> None:
        archived = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="semantic",
            content="Archived memory.",
            status="archived",
        )

        session = _MemorySession(
            memories=[archived],
        )

        request = AIContextRequest(
            task="Prepare memory.",
            include_business_brain=False,
        )

        with self.assertRaises(
            AIContextAssemblyError,
        ):
            await assemble_ai_context(
                session,
                BUSINESS_A_ID,
                request,
            )

    async def test_business_brain_failure_becomes_safe_context_error(
        self,
    ) -> None:
        request = AIContextRequest(
            task="Prepare context.",
            include_memory=False,
        )

        with patch(
            "app.services.ai_context.iterate_business_brain_sources",
            new=_broken_brain_iterator(
                BusinessBrainAssemblyError(
                    "private business brain database detail"
                )
            ),
        ):
            with self.assertRaises(
                AIContextAssemblyError,
            ) as raised:
                await assemble_ai_context(
                    _MemorySession(),
                    BUSINESS_A_ID,
                    request,
                )

        self.assertNotIn(
            "private business brain database detail",
            str(raised.exception),
        )

    async def test_memory_database_failure_becomes_safe_context_error(
        self,
    ) -> None:
        session = _MemorySession(
            scalar_error=SQLAlchemyError(
                "private PostgreSQL detail"
            ),
        )

        request = AIContextRequest(
            task="Prepare memory.",
            include_business_brain=False,
        )

        with self.assertRaises(
            AIContextAssemblyError,
        ) as raised:
            await assemble_ai_context(
                session,
                BUSINESS_A_ID,
                request,
            )

        self.assertNotIn(
            "private PostgreSQL detail",
            str(raised.exception),
        )

    async def test_revision_is_deterministic_for_same_selected_sources(
        self,
    ) -> None:
        brain = [
            _brain_source(
                "business_profile",
                "business:profile",
                "Business profile",
                "Name: Tenant A",
            )
        ]

        memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="customer",
            content="Customer prefers email.",
        )

        async def assemble(
            *,
            purpose: str,
            task: str,
        ) -> AIContextBundle:
            with patch(
                "app.services.ai_context.iterate_business_brain_sources",
                new=_brain_iterator(brain),
            ):
                return await assemble_ai_context(
                    _MemorySession(
                        memories=[memory],
                    ),
                    BUSINESS_A_ID,
                    AIContextRequest(
                        purpose=purpose,
                        task=task,
                    ),
                )

        first = await assemble(
            purpose="sales",
            task="Prepare sales context.",
        )

        second = await assemble(
            purpose="support",
            task="Prepare support context.",
        )

        self.assertEqual(
            first.revision,
            second.revision,
        )

        self.assertNotEqual(
            first.task,
            second.task,
        )

        self.assertNotEqual(
            first.purpose,
            second.purpose,
        )

    async def test_revision_changes_when_selected_memory_hash_changes(
        self,
    ) -> None:
        first_memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="semantic",
            content="Original learned fact.",
        )

        changed_memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="semantic",
            content="Changed learned fact.",
        )

        request = AIContextRequest(
            task="Prepare memory.",
            include_business_brain=False,
        )

        first = await assemble_ai_context(
            _MemorySession(
                memories=[first_memory],
            ),
            BUSINESS_A_ID,
            request,
        )

        changed = await assemble_ai_context(
            _MemorySession(
                memories=[changed_memory],
            ),
            BUSINESS_A_ID,
            request,
        )

        self.assertNotEqual(
            first.revision,
            changed.revision,
        )

    async def test_revision_for_empty_selected_collection_is_stable_sha256(
        self,
    ) -> None:
        request = AIContextRequest(
            task="Prepare filtered context.",
            include_business_brain=False,
            memory_types=[],
        )

        bundle = await assemble_ai_context(
            _MemorySession(
                memories=[],
            ),
            BUSINESS_A_ID,
            request,
        )

        self.assertEqual(
            bundle.source_count,
            0,
        )
        self.assertEqual(
            bundle.revision,
            sha256().hexdigest(),
        )

    async def test_memory_source_limit_is_bounded_in_database_query(
        self,
    ) -> None:
        memories = [
            _memory(
                memory_id=MEMORY_A_ID,
                business_id=BUSINESS_A_ID,
                memory_type="customer",
                content="Memory A.",
            ),
            _memory(
                memory_id=MEMORY_B_ID,
                business_id=BUSINESS_A_ID,
                memory_type="customer",
                content="Memory B.",
            ),
        ]

        session = _MemorySession(
            memories=memories,
        )

        request = AIContextRequest(
            task="Prepare two memories.",
            include_business_brain=False,
            memory_limit=2,
        )

        bundle = await assemble_ai_context(
            session,
            BUSINESS_A_ID,
            request,
        )

        self.assertEqual(
            bundle.memory_source_count,
            2,
        )

        statement = session.statements[0]
        compiled = statement.compile()

        self.assertTrue(
            any(
                value == 2
                for value in compiled.params.values()
            )
        )

    async def test_memory_runtime_source_excludes_internal_source_reference(
        self,
    ) -> None:
        memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="ai_learning",
            content="Customers prefer evening contact.",
            source_type="system",
            source_reference="agent:sales:private-run-id",
        )

        bundle = await assemble_ai_context(
            _MemorySession(
                memories=[memory],
            ),
            BUSINESS_A_ID,
            AIContextRequest(
                task="Prepare memory.",
                include_business_brain=False,
            ),
        )

        source = bundle.sources[0]
        serialized = source.model_dump()

        self.assertNotIn(
            "source_reference",
            serialized,
        )
        self.assertNotIn(
            "source_type",
            serialized,
        )
        self.assertNotIn(
            "status",
            serialized,
        )

    async def test_assembly_performs_no_writes(self) -> None:
        memory = _memory(
            memory_id=MEMORY_A_ID,
            business_id=BUSINESS_A_ID,
            memory_type="semantic",
            content="Read-only context.",
        )

        session = _MemorySession(
            memories=[memory],
        )

        request = AIContextRequest(
            task="Prepare context.",
            include_business_brain=False,
        )

        await assemble_ai_context(
            session,
            BUSINESS_A_ID,
            request,
        )

        self.assertEqual(
            session.add_calls,
            0,
        )
        self.assertEqual(
            session.flush_calls,
            0,
        )
        self.assertEqual(
            session.commit_calls,
            0,
        )
        self.assertEqual(
            session.rollback_calls,
            0,
        )


class _ScalarResult:
    def __init__(
        self,
        values: list[BusinessMemory],
    ) -> None:
        self.values = list(values)

    def all(self) -> list[BusinessMemory]:
        return self.values


class _MemorySession:
    def __init__(
        self,
        *,
        memories: list[BusinessMemory] | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.memories = memories or []
        self.scalar_error = scalar_error

        self.scalars_calls = 0
        self.statements: list[object] = []

        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    async def scalars(
        self,
        statement: object,
    ) -> _ScalarResult:
        self.scalars_calls += 1
        self.statements.append(statement)

        if self.scalar_error is not None:
            raise self.scalar_error

        return _ScalarResult(
            self.memories,
        )

    def add(
        self,
        instance: object,
    ) -> None:
        self.add_calls += 1

    async def flush(self) -> None:
        self.flush_calls += 1

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _brain_source(
    source_type: str,
    source_id: str,
    title: str,
    content: str,
    *,
    business_id: UUID = BUSINESS_A_ID,
) -> BusinessBrainSource:
    return BusinessBrainSource(
        business_id=business_id,
        source_type=source_type,  # type: ignore[arg-type]
        source_id=source_id,
        title=title,
        content=content,
        updated_at=BASE_TIME,
        content_hash=sha256(
            content.encode("utf-8")
        ).hexdigest(),
    )


def _brain_iterator(
    sources: list[BusinessBrainSource],
):
    async def iterate(
        session: object,
        business_id: UUID,
        *,
        batch_size: int,
    ):
        _ = session
        _ = business_id
        _ = batch_size

        for source in sources:
            yield source

    return iterate


def _broken_brain_iterator(
    error: Exception,
):
    async def iterate(
        session: object,
        business_id: UUID,
        *,
        batch_size: int,
    ):
        _ = session
        _ = business_id
        _ = batch_size

        if False:
            yield None

        raise error

    return iterate


def _memory(
    *,
    memory_id: UUID,
    business_id: UUID,
    memory_type: str,
    content: str,
    status: str = "active",
    importance: int = 3,
    confidence: Decimal = Decimal("1.000"),
    source_type: str = "manual",
    source_reference: str | None = None,
) -> BusinessMemory:
    return BusinessMemory(
        id=memory_id,
        business_id=business_id,
        memory_type=memory_type,
        content=content,
        status=status,
        importance=importance,
        confidence=confidence,
        source_type=source_type,
        source_reference=source_reference,
        occurred_at=None,
        last_reinforced_at=None,
        content_hash=build_memory_content_hash(
            memory_type,  # type: ignore[arg-type]
            content,
        ),
        superseded_by_memory_id=None,
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )