from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.exceptions.business_memory import BusinessMemoryNotFoundError
from app.models.business import Business
from app.models.business_memory import BusinessMemory
from app.schemas.business_memory import BusinessMemoryCreate
from app.services.business_memory import (
    archive_business_memory,
    create_manual_memory,
    create_system_memory,
    find_active_memory_candidates_by_hash,
    get_business_memory,
    list_business_memories,
    supersede_business_memory,
)


ALLOWED_SMOKE_ENVIRONMENTS = {
    "development",
    "dev",
    "local",
    "test",
    "testing",
}


def _assert_safe_environment() -> None:
    environment = settings.environment.strip().lower()

    if environment not in ALLOWED_SMOKE_ENVIRONMENTS:
        raise RuntimeError(
            "Refusing to run PostgreSQL smoke test outside a development/test "
            f"environment. Current environment: {settings.environment!r}"
        )


def _make_business(name: str, slug: str) -> Business:
    return Business(
        name=name,
        slug=slug,
        business_type="services",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
    )


async def _memory_count(
    session,
    business_id: UUID,
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(BusinessMemory)
        .where(
            BusinessMemory.business_id == business_id,
        )
    )

    return int(count or 0)


async def _assert_importance_constraint(
    session,
    business_id: UUID,
) -> None:
    """
    Prove PostgreSQL itself rejects an invalid importance value.

    Raw SQL is intentional here so this validates the database constraint,
    independently of Pydantic and service-layer validation.
    """
    invalid_id = uuid4()

    with_error = False

    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO business_memories (
                        id,
                        business_id,
                        memory_type,
                        content,
                        status,
                        importance,
                        confidence,
                        source_type,
                        content_hash
                    )
                    VALUES (
                        :id,
                        :business_id,
                        :memory_type,
                        :content,
                        :status,
                        :importance,
                        :confidence,
                        :source_type,
                        :content_hash
                    )
                    """
                ),
                {
                    "id": invalid_id,
                    "business_id": business_id,
                    "memory_type": "semantic",
                    "content": "Invalid importance constraint test.",
                    "status": "active",
                    "importance": 6,
                    "confidence": Decimal("1.000"),
                    "source_type": "manual",
                    "content_hash": "a" * 64,
                },
            )

    except IntegrityError:
        with_error = True

    assert with_error, (
        "PostgreSQL accepted importance=6. "
        "The business_memories importance constraint is not working."
    )


async def _assert_self_supersession_constraint(
    session,
    business_id: UUID,
) -> None:
    """
    Prove PostgreSQL rejects a memory pointing to itself as its replacement.
    """
    invalid_id = uuid4()

    with_error = False

    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    """
                    INSERT INTO business_memories (
                        id,
                        business_id,
                        memory_type,
                        content,
                        status,
                        importance,
                        confidence,
                        source_type,
                        content_hash,
                        superseded_by_memory_id
                    )
                    VALUES (
                        :id,
                        :business_id,
                        :memory_type,
                        :content,
                        :status,
                        :importance,
                        :confidence,
                        :source_type,
                        :content_hash,
                        :superseded_by_memory_id
                    )
                    """
                ),
                {
                    "id": invalid_id,
                    "business_id": business_id,
                    "memory_type": "semantic",
                    "content": "Invalid self-supersession constraint test.",
                    "status": "superseded",
                    "importance": 3,
                    "confidence": Decimal("1.000"),
                    "source_type": "manual",
                    "content_hash": "b" * 64,
                    "superseded_by_memory_id": invalid_id,
                },
            )

    except IntegrityError:
        with_error = True

    assert with_error, (
        "PostgreSQL accepted a self-superseding memory. "
        "The self-supersession constraint is not working."
    )


async def run_smoke_test() -> None:
    _assert_safe_environment()

    suffix = uuid4().hex[:12]

    business_a = _make_business(
        f"Memory Smoke A {suffix}",
        f"memory-smoke-a-{suffix}",
    )

    business_b = _make_business(
        f"Memory Smoke B {suffix}",
        f"memory-smoke-b-{suffix}",
    )

    async with AsyncSessionFactory() as session:
        try:
            # -------------------------------------------------------------
            # 1. Real businesses
            # -------------------------------------------------------------
            session.add_all(
                [
                    business_a,
                    business_b,
                ]
            )

            await session.flush()

            assert business_a.id is not None
            assert business_b.id is not None
            assert business_a.id != business_b.id

            # -------------------------------------------------------------
            # 2. Manual memory
            # -------------------------------------------------------------
            manual_memory = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="customer",
                    content="  Customer prefers WhatsApp communication.  ",
                    importance=4,
                    occurred_at=datetime.now(UTC),
                ),
            )

            assert manual_memory.business_id == business_a.id
            assert manual_memory.memory_type == "customer"
            assert manual_memory.content == (
                "Customer prefers WhatsApp communication."
            )
            assert manual_memory.status == "active"
            assert manual_memory.importance == 4
            assert manual_memory.confidence == Decimal("1.000")
            assert manual_memory.source_type == "manual"
            assert manual_memory.source_reference is None
            assert len(manual_memory.content_hash) == 64

            # -------------------------------------------------------------
            # 3. Trusted system memory
            # -------------------------------------------------------------
            system_memory = await create_system_memory(
                session,
                business_a.id,
                memory_type="ai_learning",
                content=(
                    "Customers respond more frequently during evening hours."
                ),
                confidence=Decimal("0.875"),
                source_reference="agent:sales:smoke-test",
                importance=5,
                occurred_at=datetime.now(UTC),
            )

            assert system_memory.business_id == business_a.id
            assert system_memory.source_type == "system"
            assert system_memory.confidence == Decimal("0.875")
            assert system_memory.importance == 5
            assert (
                system_memory.source_reference
                == "agent:sales:smoke-test"
            )

            # -------------------------------------------------------------
            # 4. Tenant B has its own isolated memory
            # -------------------------------------------------------------
            tenant_b_memory = await create_manual_memory(
                session,
                business_b.id,
                BusinessMemoryCreate(
                    memory_type="customer",
                    content="Tenant B customer prefers email.",
                    importance=3,
                ),
            )

            assert tenant_b_memory.business_id == business_b.id

            # Cross-tenant lookup must behave as not-found.
            cross_tenant_blocked = False

            try:
                await get_business_memory(
                    session,
                    business_b.id,
                    manual_memory.id,
                )
            except BusinessMemoryNotFoundError:
                cross_tenant_blocked = True

            assert cross_tenant_blocked, (
                "Tenant isolation failure: Business B could access "
                "Business A memory."
            )

            # -------------------------------------------------------------
            # 5. Duplicate/reinforcement candidate lookup
            # -------------------------------------------------------------
            duplicate_manual = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="semantic",
                    content="Business provides same-day local delivery.",
                ),
            )

            duplicate_system = await create_system_memory(
                session,
                business_a.id,
                memory_type="semantic",
                content="Business provides same-day local delivery.",
                confidence=Decimal("0.800"),
                source_reference="agent:operations:smoke-test",
            )

            candidates = await find_active_memory_candidates_by_hash(
                session,
                business_a.id,
                memory_type="semantic",
                content="Business provides same-day local delivery.",
            )

            candidate_ids = {
                candidate.id
                for candidate in candidates
            }

            assert duplicate_manual.id in candidate_ids
            assert duplicate_system.id in candidate_ids

            tenant_b_candidates = (
                await find_active_memory_candidates_by_hash(
                    session,
                    business_b.id,
                    memory_type="semantic",
                    content="Business provides same-day local delivery.",
                )
            )

            assert tenant_b_candidates == []

            # -------------------------------------------------------------
            # 6. Real keyset pagination
            # -------------------------------------------------------------
            page_oldest = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="decision",
                    content="Decision page item oldest.",
                ),
            )

            page_middle = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="decision",
                    content="Decision page item middle.",
                ),
            )

            page_newest = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="decision",
                    content="Decision page item newest.",
                ),
            )

            page_oldest.created_at = datetime(
                2026,
                8,
                21,
                10,
                0,
                0,
                tzinfo=UTC,
            )

            page_middle.created_at = datetime(
                2026,
                8,
                21,
                11,
                0,
                0,
                tzinfo=UTC,
            )

            page_newest.created_at = datetime(
                2026,
                8,
                21,
                12,
                0,
                0,
                tzinfo=UTC,
            )

            await session.flush()

            first_page, next_cursor = await list_business_memories(
                session,
                business_a.id,
                memory_type="decision",
                limit=2,
            )

            assert [
                memory.id
                for memory in first_page
            ] == [
                page_newest.id,
                page_middle.id,
            ]

            assert next_cursor is not None

            second_page, final_cursor = await list_business_memories(
                session,
                business_a.id,
                memory_type="decision",
                limit=2,
                cursor=next_cursor,
            )

            assert [
                memory.id
                for memory in second_page
            ] == [
                page_oldest.id,
            ]

            assert final_cursor is None

            assert not (
                {
                    memory.id
                    for memory in first_page
                }
                & {
                    memory.id
                    for memory in second_page
                }
            )

            # -------------------------------------------------------------
            # 7. Supersession
            # -------------------------------------------------------------
            old_memory = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="procedural",
                    content="Old fulfillment procedure.",
                ),
            )

            replacement_memory = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="procedural",
                    content="Updated fulfillment procedure.",
                ),
            )

            superseded = await supersede_business_memory(
                session,
                business_a.id,
                old_memory.id,
                replacement_memory.id,
            )

            assert superseded.status == "superseded"
            assert (
                superseded.superseded_by_memory_id
                == replacement_memory.id
            )

            persisted_old = await get_business_memory(
                session,
                business_a.id,
                old_memory.id,
            )

            assert persisted_old.status == "superseded"
            assert (
                persisted_old.superseded_by_memory_id
                == replacement_memory.id
            )

            # -------------------------------------------------------------
            # 8. Archive + idempotence
            # -------------------------------------------------------------
            archived = await archive_business_memory(
                session,
                business_a.id,
                replacement_memory.id,
            )

            assert archived.status == "archived"

            archived_again = await archive_business_memory(
                session,
                business_a.id,
                replacement_memory.id,
            )

            assert archived_again.status == "archived"

            active_procedural, _ = await list_business_memories(
                session,
                business_a.id,
                memory_type="procedural",
            )

            active_procedural_ids = {
                memory.id
                for memory in active_procedural
            }

            assert old_memory.id not in active_procedural_ids
            assert replacement_memory.id not in active_procedural_ids

            # -------------------------------------------------------------
            # 9. Real PostgreSQL constraints
            # -------------------------------------------------------------
            await _assert_importance_constraint(
                session,
                business_a.id,
            )

            await _assert_self_supersession_constraint(
                session,
                business_a.id,
            )

            # -------------------------------------------------------------
            # 10. Real ON DELETE CASCADE + tenant isolation
            # -------------------------------------------------------------
            business_a_memory_count = await _memory_count(
                session,
                business_a.id,
            )

            business_b_memory_count = await _memory_count(
                session,
                business_b.id,
            )

            assert business_a_memory_count > 0
            assert business_b_memory_count == 1

            # Core SQL delete intentionally bypasses ORM relationship
            # cascades so PostgreSQL's FK ON DELETE CASCADE is tested.
            await session.execute(
                text(
                    """
                    DELETE FROM businesses
                    WHERE id = :business_id
                    """
                ),
                {
                    "business_id": business_a.id,
                },
            )

            await session.flush()

            assert (
                await _memory_count(
                    session,
                    business_a.id,
                )
                == 0
            )

            assert (
                await _memory_count(
                    session,
                    business_b.id,
                )
                == 1
            )

            print()
            print("Persistent Business Memory PostgreSQL smoke test PASSED")
            print()
            print("Verified:")
            print("  ✓ manual memory persistence")
            print("  ✓ trusted system memory persistence")
            print("  ✓ tenant isolation")
            print("  ✓ duplicate/hash candidate lookup")
            print("  ✓ keyset pagination")
            print("  ✓ supersession")
            print("  ✓ archive lifecycle")
            print("  ✓ PostgreSQL importance constraint")
            print("  ✓ PostgreSQL self-supersession constraint")
            print("  ✓ business-memory ON DELETE CASCADE")
            print("  ✓ smoke data rollback / no permanent test data")

        finally:
            # Never leave smoke-test businesses or memories behind.
            await session.rollback()


async def main() -> None:
    await run_smoke_test()


if __name__ == "__main__":
    asyncio.run(main())