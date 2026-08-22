from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.exceptions.business_memory import (
    BusinessMemoryCursorError,
    BusinessMemoryNotFoundError,
    BusinessMemoryPersistenceError,
    BusinessMemorySupersessionError,
)
from app.models.business_memory import BusinessMemory
from app.schemas.business_memory import (
    BusinessMemoryCreate,
    BusinessMemoryUpdate,
)
from app.services import business_memory as service


def make_session() -> MagicMock:
    session = MagicMock()

    session.add = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()

    return session


def make_memory(
    *,
    business_id: UUID | None = None,
    memory_id: UUID | None = None,
    memory_type: str = "semantic",
    content: str = "Customer prefers email communication.",
    status: str = "active",
    importance: int = 3,
    confidence: Decimal = Decimal("1.000"),
    source_type: str = "manual",
    source_reference: str | None = None,
    superseded_by_memory_id: UUID | None = None,
    created_at: datetime | None = None,
) -> BusinessMemory:
    created = created_at or datetime.now(timezone.utc)

    return BusinessMemory(
        id=memory_id or uuid4(),
        business_id=business_id or uuid4(),
        memory_type=memory_type,
        content=content,
        status=status,
        importance=importance,
        confidence=confidence,
        source_type=source_type,
        source_reference=source_reference,
        occurred_at=None,
        last_reinforced_at=None,
        content_hash=service.build_memory_content_hash(
            memory_type,  # type: ignore[arg-type]
            content,
        ),
        superseded_by_memory_id=superseded_by_memory_id,
        created_at=created,
        updated_at=created,
    )


def test_memory_hash_is_deterministic_and_sha256() -> None:
    first = service.build_memory_content_hash(
        "semantic",
        "Customer prefers email.",
    )
    second = service.build_memory_content_hash(
        "semantic",
        "Customer prefers email.",
    )

    assert first == second
    assert len(first) == 64
    assert first == first.lower()

    int(first, 16)


def test_memory_hash_normalizes_outer_whitespace_and_line_endings() -> None:
    first = service.build_memory_content_hash(
        "semantic",
        " \r\nCustomer prefers email.\r\n ",
    )
    second = service.build_memory_content_hash(
        "semantic",
        "\nCustomer prefers email.\n",
    )

    assert first == second


def test_memory_hash_keeps_memory_type_as_part_of_identity() -> None:
    content = "Customer prefers email."

    semantic_hash = service.build_memory_content_hash(
        "semantic",
        content,
    )
    customer_hash = service.build_memory_content_hash(
        "customer",
        content,
    )

    assert semantic_hash != customer_hash


def test_memory_cursor_round_trip() -> None:
    created_at = datetime.now(timezone.utc)
    memory_id = uuid4()

    cursor = service.encode_memory_cursor(
        created_at,
        memory_id,
    )

    decoded_created_at, decoded_memory_id = (
        service.decode_memory_cursor(cursor)
    )

    assert decoded_created_at == created_at
    assert decoded_memory_id == memory_id


def test_memory_cursor_rejects_naive_datetime_on_encode() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        service.encode_memory_cursor(
            datetime.now(),
            uuid4(),
        )


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!",
        "not-valid-base64***",
        "a" * 513,
    ],
)
def test_memory_cursor_rejects_invalid_payloads(
    cursor: str,
) -> None:
    with pytest.raises(BusinessMemoryCursorError):
        service.decode_memory_cursor(cursor)


def test_memory_cursor_rejects_wrong_json_shape() -> None:
    raw = json.dumps(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ).encode("utf-8")

    cursor = (
        base64.urlsafe_b64encode(raw)
        .decode("ascii")
        .rstrip("=")
    )

    with pytest.raises(BusinessMemoryCursorError):
        service.decode_memory_cursor(cursor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        201,
        True,
    ],
)
async def test_list_rejects_invalid_page_limits(
    limit: int,
) -> None:
    session = make_session()

    with pytest.raises(ValueError, match="limit must be between"):
        await service.list_business_memories(
            session,
            uuid4(),
            limit=limit,
        )

    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_is_tenant_scoped_active_by_default_and_paginated() -> None:
    session = make_session()
    business_id = uuid4()

    newest = make_memory(
        business_id=business_id,
        created_at=datetime(
            2026,
            8,
            21,
            12,
            3,
            tzinfo=timezone.utc,
        ),
    )
    middle = make_memory(
        business_id=business_id,
        created_at=datetime(
            2026,
            8,
            21,
            12,
            2,
            tzinfo=timezone.utc,
        ),
    )
    oldest = make_memory(
        business_id=business_id,
        created_at=datetime(
            2026,
            8,
            21,
            12,
            1,
            tzinfo=timezone.utc,
        ),
    )

    scalar_result = MagicMock()
    scalar_result.all.return_value = [
        newest,
        middle,
        oldest,
    ]

    session.scalars.return_value = scalar_result

    items, next_cursor = await service.list_business_memories(
        session,
        business_id,
        limit=2,
    )

    assert items == [newest, middle]
    assert next_cursor is not None

    cursor_created_at, cursor_id = (
        service.decode_memory_cursor(next_cursor)
    )

    assert cursor_created_at == middle.created_at
    assert cursor_id == middle.id

    statement = session.scalars.await_args.args[0]
    statement_text = str(statement)

    assert "business_memories.business_id" in statement_text
    assert "business_memories.status" in statement_text

    compiled = statement.compile()

    assert business_id in compiled.params.values()
    assert "active" in compiled.params.values()


@pytest.mark.asyncio
async def test_list_supports_memory_type_and_status_filters() -> None:
    session = make_session()
    business_id = uuid4()

    scalar_result = MagicMock()
    scalar_result.all.return_value = []

    session.scalars.return_value = scalar_result

    items, cursor = await service.list_business_memories(
        session,
        business_id,
        memory_type="decision",
        status="archived",
        limit=25,
    )

    assert items == []
    assert cursor is None

    statement = session.scalars.await_args.args[0]
    compiled = statement.compile()

    assert business_id in compiled.params.values()
    assert "decision" in compiled.params.values()
    assert "archived" in compiled.params.values()


@pytest.mark.asyncio
async def test_list_translates_database_failure() -> None:
    session = make_session()

    session.scalars.side_effect = SQLAlchemyError(
        "database unavailable"
    )

    with pytest.raises(BusinessMemoryPersistenceError):
        await service.list_business_memories(
            session,
            uuid4(),
        )


@pytest.mark.asyncio
async def test_get_memory_is_tenant_scoped() -> None:
    session = make_session()

    business_id = uuid4()
    memory_id = uuid4()

    memory = make_memory(
        business_id=business_id,
        memory_id=memory_id,
    )

    session.scalar.return_value = memory

    result = await service.get_business_memory(
        session,
        business_id,
        memory_id,
    )

    assert result is memory

    statement = session.scalar.await_args.args[0]
    statement_text = str(statement)

    assert "business_memories.business_id" in statement_text
    assert "business_memories.id" in statement_text

    compiled = statement.compile()

    assert business_id in compiled.params.values()
    assert memory_id in compiled.params.values()


@pytest.mark.asyncio
async def test_get_memory_returns_not_found_without_cross_tenant_fallback() -> None:
    session = make_session()

    session.scalar.return_value = None

    with pytest.raises(
        BusinessMemoryNotFoundError,
        match="Business memory not found",
    ):
        await service.get_business_memory(
            session,
            uuid4(),
            uuid4(),
        )


@pytest.mark.asyncio
async def test_create_manual_memory_controls_trusted_metadata() -> None:
    session = make_session()

    business_id = uuid4()
    occurred_at = datetime.now(timezone.utc)

    payload = BusinessMemoryCreate(
        memory_type="customer",
        content="  Customer prefers WhatsApp.  ",
        importance=4,
        occurred_at=occurred_at,
    )

    memory = await service.create_manual_memory(
        session,
        business_id,
        payload,
    )

    assert memory.business_id == business_id
    assert memory.memory_type == "customer"
    assert memory.content == "Customer prefers WhatsApp."
    assert memory.status == "active"
    assert memory.importance == 4

    assert memory.confidence == Decimal("1.000")
    assert memory.source_type == "manual"
    assert memory.source_reference is None

    assert memory.occurred_at == occurred_at
    assert memory.last_reinforced_at is None
    assert memory.superseded_by_memory_id is None

    assert memory.content_hash == (
        service.build_memory_content_hash(
            "customer",
            "Customer prefers WhatsApp.",
        )
    )

    session.add.assert_called_once_with(memory)
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_manual_memory_translates_database_failure() -> None:
    session = make_session()

    session.flush.side_effect = SQLAlchemyError(
        "write failed"
    )

    payload = BusinessMemoryCreate(
        memory_type="semantic",
        content="Business ships internationally.",
    )

    with pytest.raises(BusinessMemoryPersistenceError):
        await service.create_manual_memory(
            session,
            uuid4(),
            payload,
        )


@pytest.mark.asyncio
async def test_create_system_memory_sets_trusted_system_provenance() -> None:
    session = make_session()

    business_id = uuid4()
    occurred_at = datetime.now(timezone.utc)

    memory = await service.create_system_memory(
        session,
        business_id,
        memory_type="ai_learning",
        content="  Customers respond better in the evening.  ",
        confidence=Decimal("0.875"),
        source_reference="  agent:sales:analysis-123  ",
        importance=5,
        occurred_at=occurred_at,
    )

    assert memory.business_id == business_id
    assert memory.memory_type == "ai_learning"

    assert (
        memory.content
        == "Customers respond better in the evening."
    )

    assert memory.status == "active"
    assert memory.importance == 5
    assert memory.confidence == Decimal("0.875")

    assert memory.source_type == "system"
    assert memory.source_reference == "agent:sales:analysis-123"

    assert memory.occurred_at == occurred_at
    assert memory.last_reinforced_at is None
    assert memory.superseded_by_memory_id is None

    session.add.assert_called_once_with(memory)
    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("confidence", "message"),
    [
        (
            Decimal("-0.001"),
            "confidence must be between",
        ),
        (
            Decimal("1.001"),
            "confidence must be between",
        ),
        (
            Decimal("0.1234"),
            "confidence cannot exceed three decimal places",
        ),
        (
            Decimal("NaN"),
            "confidence must be finite",
        ),
    ],
)
async def test_create_system_memory_rejects_invalid_confidence(
    confidence: Decimal,
    message: str,
) -> None:
    session = make_session()

    with pytest.raises(ValueError, match=message):
        await service.create_system_memory(
            session,
            uuid4(),
            memory_type="semantic",
            content="Valid memory.",
            confidence=confidence,
        )

    session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "importance",
    [
        0,
        6,
        True,
    ],
)
async def test_create_system_memory_rejects_invalid_importance(
    importance: int,
) -> None:
    session = make_session()

    with pytest.raises(
        ValueError,
        match="importance must be between 1 and 5",
    ):
        await service.create_system_memory(
            session,
            uuid4(),
            memory_type="semantic",
            content="Valid memory.",
            confidence=Decimal("0.900"),
            importance=importance,
        )

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_system_memory_rejects_blank_content() -> None:
    session = make_session()

    with pytest.raises(
        ValueError,
        match="content cannot be blank",
    ):
        await service.create_system_memory(
            session,
            uuid4(),
            memory_type="semantic",
            content="   ",
            confidence=Decimal("0.900"),
        )

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_system_memory_rejects_naive_occurred_at() -> None:
    session = make_session()

    with pytest.raises(
        ValueError,
        match="occurred_at must be timezone-aware",
    ):
        await service.create_system_memory(
            session,
            uuid4(),
            memory_type="semantic",
            content="Valid memory.",
            confidence=Decimal("0.900"),
            occurred_at=datetime.now(),
        )

    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_content_recalculates_hash() -> None:
    session = make_session()

    business_id = uuid4()
    memory = make_memory(
        business_id=business_id,
        content="Old information.",
    )

    original_hash = memory.content_hash

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        result = await service.update_business_memory(
            session,
            business_id,
            memory.id,
            BusinessMemoryUpdate(
                content="New information.",
            ),
        )

    assert result is memory
    assert memory.content == "New information."
    assert memory.content_hash != original_hash

    assert memory.content_hash == (
        service.build_memory_content_hash(
            "semantic",
            "New information.",
        )
    )

    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_memory_type_recalculates_hash() -> None:
    session = make_session()

    business_id = uuid4()

    memory = make_memory(
        business_id=business_id,
        memory_type="semantic",
        content="Customer prefers email.",
    )

    original_hash = memory.content_hash

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        await service.update_business_memory(
            session,
            business_id,
            memory.id,
            BusinessMemoryUpdate(
                memory_type="customer",
            ),
        )

    assert memory.memory_type == "customer"
    assert memory.content_hash != original_hash

    assert memory.content_hash == (
        service.build_memory_content_hash(
            "customer",
            memory.content,
        )
    )


@pytest.mark.asyncio
async def test_public_update_cannot_directly_supersede_memory() -> None:
    session = make_session()

    business_id = uuid4()
    memory = make_memory(
        business_id=business_id,
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        with pytest.raises(
            BusinessMemorySupersessionError,
        ):
            await service.update_business_memory(
                session,
                business_id,
                memory.id,
                BusinessMemoryUpdate(
                    status="superseded",
                ),
            )

    assert memory.status == "active"
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_superseded_memory_cannot_be_reactivated() -> None:
    session = make_session()

    business_id = uuid4()

    memory = make_memory(
        business_id=business_id,
        status="superseded",
        superseded_by_memory_id=uuid4(),
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        with pytest.raises(
            BusinessMemorySupersessionError,
        ):
            await service.update_business_memory(
                session,
                business_id,
                memory.id,
                BusinessMemoryUpdate(
                    status="active",
                ),
            )

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_active_memory_is_soft_delete() -> None:
    session = make_session()

    business_id = uuid4()
    memory = make_memory(
        business_id=business_id,
        status="active",
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        result = await service.archive_business_memory(
            session,
            business_id,
            memory.id,
        )

    assert result is memory
    assert memory.status == "archived"

    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        "archived",
        "superseded",
    ],
)
async def test_archive_is_idempotent_for_historical_states(
    status: str,
) -> None:
    session = make_session()

    business_id = uuid4()

    memory = make_memory(
        business_id=business_id,
        status=status,
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(return_value=memory),
    ):
        result = await service.archive_business_memory(
            session,
            business_id,
            memory.id,
        )

    assert result is memory
    assert memory.status == status

    session.flush.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_links_old_memory_to_active_replacement() -> None:
    session = make_session()

    business_id = uuid4()

    old_memory = make_memory(
        business_id=business_id,
        status="active",
    )

    replacement = make_memory(
        business_id=business_id,
        status="active",
    )

    get_memory = AsyncMock(
        side_effect=[
            old_memory,
            replacement,
        ]
    )

    with patch.object(
        service,
        "get_business_memory",
        new=get_memory,
    ):
        result = await service.supersede_business_memory(
            session,
            business_id,
            old_memory.id,
            replacement.id,
        )

    assert result is old_memory
    assert old_memory.status == "superseded"

    assert (
        old_memory.superseded_by_memory_id
        == replacement.id
    )

    assert get_memory.await_count == 2

    first_call = get_memory.await_args_list[0].args
    second_call = get_memory.await_args_list[1].args

    assert first_call == (
        session,
        business_id,
        old_memory.id,
    )

    assert second_call == (
        session,
        business_id,
        replacement.id,
    )

    session.flush.assert_awaited_once()
    session.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_supersede_rejects_self_reference() -> None:
    session = make_session()

    business_id = uuid4()
    memory_id = uuid4()

    with pytest.raises(
        BusinessMemorySupersessionError,
    ):
        await service.supersede_business_memory(
            session,
            business_id,
            memory_id,
            memory_id,
        )

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_rejects_archived_old_memory() -> None:
    session = make_session()

    business_id = uuid4()

    old_memory = make_memory(
        business_id=business_id,
        status="archived",
    )

    replacement = make_memory(
        business_id=business_id,
        status="active",
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(
            side_effect=[
                old_memory,
                replacement,
            ]
        ),
    ):
        with pytest.raises(
            BusinessMemorySupersessionError,
        ):
            await service.supersede_business_memory(
                session,
                business_id,
                old_memory.id,
                replacement.id,
            )

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_rejects_non_active_replacement() -> None:
    session = make_session()

    business_id = uuid4()

    old_memory = make_memory(
        business_id=business_id,
        status="active",
    )

    replacement = make_memory(
        business_id=business_id,
        status="archived",
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(
            side_effect=[
                old_memory,
                replacement,
            ]
        ),
    ):
        with pytest.raises(
            BusinessMemorySupersessionError,
        ):
            await service.supersede_business_memory(
                session,
                business_id,
                old_memory.id,
                replacement.id,
            )

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_supersede_same_replacement_is_idempotent() -> None:
    session = make_session()

    business_id = uuid4()
    replacement_id = uuid4()

    old_memory = make_memory(
        business_id=business_id,
        status="superseded",
        superseded_by_memory_id=replacement_id,
    )

    replacement = make_memory(
        business_id=business_id,
        memory_id=replacement_id,
        status="active",
    )

    with patch.object(
        service,
        "get_business_memory",
        new=AsyncMock(
            side_effect=[
                old_memory,
                replacement,
            ]
        ),
    ):
        result = await service.supersede_business_memory(
            session,
            business_id,
            old_memory.id,
            replacement.id,
        )

    assert result is old_memory
    assert (
        old_memory.superseded_by_memory_id
        == replacement.id
    )

    session.flush.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_duplicate_candidates_is_tenant_scoped_active_only() -> None:
    session = make_session()

    business_id = uuid4()
    content = "Customer prefers WhatsApp."

    matching_memory = make_memory(
        business_id=business_id,
        memory_type="customer",
        content=content,
    )

    scalar_result = MagicMock()
    scalar_result.all.return_value = [
        matching_memory,
    ]

    session.scalars.return_value = scalar_result

    results = (
        await service.find_active_memory_candidates_by_hash(
            session,
            business_id,
            memory_type="customer",
            content=content,
            limit=20,
        )
    )

    assert results == [matching_memory]

    statement = session.scalars.await_args.args[0]
    statement_text = str(statement)
    compiled = statement.compile()

    assert "business_memories.business_id" in statement_text
    assert "business_memories.status" in statement_text
    assert "business_memories.content_hash" in statement_text

    assert business_id in compiled.params.values()
    assert "active" in compiled.params.values()

    expected_hash = (
        service.build_memory_content_hash(
            "customer",
            content,
        )
    )

    assert expected_hash in compiled.params.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    [
        0,
        -1,
        101,
        True,
    ],
)
async def test_duplicate_candidate_lookup_rejects_invalid_limit(
    limit: int,
) -> None:
    session = make_session()

    with pytest.raises(
        ValueError,
        match="limit must be between",
    ):
        await service.find_active_memory_candidates_by_hash(
            session,
            uuid4(),
            memory_type="semantic",
            content="Valid memory.",
            limit=limit,
        )

    session.scalars.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_candidate_lookup_translates_database_failure() -> None:
    session = make_session()

    session.scalars.side_effect = SQLAlchemyError(
        "lookup failed"
    )

    with pytest.raises(BusinessMemoryPersistenceError):
        await service.find_active_memory_candidates_by_hash(
            session,
            uuid4(),
            memory_type="semantic",
            content="Valid memory.",
        )