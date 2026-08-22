import base64
import binascii
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Final
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.business_memory import (
    BusinessMemoryCursorError,
    BusinessMemoryNotFoundError,
    BusinessMemoryPersistenceError,
    BusinessMemorySupersessionError,
)
from app.models.business_memory import BusinessMemory
from app.schemas.business_memory import (
    BusinessMemoryCreate,
    BusinessMemoryStatus,
    BusinessMemoryType,
    BusinessMemoryUpdate,
    MAX_MEMORY_CONTENT_LENGTH,
    MAX_MEMORY_SOURCE_REFERENCE_LENGTH,
)

DEFAULT_MEMORY_PAGE_SIZE: Final = 50
MAX_MEMORY_PAGE_SIZE: Final = 200
MAX_DUPLICATE_CANDIDATES: Final = 100

_PERSISTENCE_MESSAGE: Final = "Unable to persist business memory"
_CURSOR_MESSAGE: Final = "Invalid memory pagination cursor"
_SUPERSESSION_MESSAGE: Final = "Invalid memory supersession"

_ALLOWED_MEMORY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "episodic",
        "semantic",
        "procedural",
        "decision",
        "customer",
        "ai_learning",
    }
)

_CONFIDENCE_QUANTUM: Final = Decimal("0.001")
_MIN_CONFIDENCE: Final = Decimal("0.000")
_MAX_CONFIDENCE: Final = Decimal("1.000")


async def list_business_memories(
    session: AsyncSession,
    business_id: UUID,
    *,
    memory_type: BusinessMemoryType | None = None,
    status: BusinessMemoryStatus | None = None,
    limit: int = DEFAULT_MEMORY_PAGE_SIZE,
    cursor: str | None = None,
) -> tuple[list[BusinessMemory], str | None]:
    """
    Return one bounded tenant-scoped page using descending keyset pagination.

    Default retrieval includes only active memories.
    """
    _validate_page_limit(limit)

    cursor_position = decode_memory_cursor(cursor) if cursor is not None else None

    statement = select(BusinessMemory).where(
        BusinessMemory.business_id == business_id
    )

    if memory_type is not None:
        statement = statement.where(BusinessMemory.memory_type == memory_type)

    if status is None:
        statement = statement.where(BusinessMemory.status == "active")
    else:
        statement = statement.where(BusinessMemory.status == status)

    if cursor_position is not None:
        cursor_created_at, cursor_id = cursor_position
        statement = statement.where(
            or_(
                BusinessMemory.created_at < cursor_created_at,
                and_(
                    BusinessMemory.created_at == cursor_created_at,
                    BusinessMemory.id < cursor_id,
                ),
            )
        )

    statement = statement.order_by(
        BusinessMemory.created_at.desc(),
        BusinessMemory.id.desc(),
    ).limit(limit + 1)

    try:
        result = await session.scalars(statement)
        records = list(result.all())
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    if not all(isinstance(record, BusinessMemory) for record in records):
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE)

    has_more = len(records) > limit
    items = records[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_memory_cursor(last.created_at, last.id)

    return items, next_cursor


async def get_business_memory(
    session: AsyncSession,
    business_id: UUID,
    memory_id: UUID,
) -> BusinessMemory:
    """Load one memory using both tenant ID and memory ID."""
    try:
        memory = await session.scalar(
            select(BusinessMemory).where(
                BusinessMemory.business_id == business_id,
                BusinessMemory.id == memory_id,
            )
        )
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    if memory is None:
        raise BusinessMemoryNotFoundError("Business memory not found")

    if not isinstance(memory, BusinessMemory):
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE)

    return memory


async def create_manual_memory(
    session: AsyncSession,
    business_id: UUID,
    memory_create: BusinessMemoryCreate,
) -> BusinessMemory:
    """
    Create one manually-authored memory.

    Trusted provenance and confidence values are server controlled.
    """
    content = memory_create.content
    content_hash = build_memory_content_hash(
        memory_create.memory_type,
        content,
    )

    memory = BusinessMemory(
        business_id=business_id,
        memory_type=memory_create.memory_type,
        content=content,
        status="active",
        importance=memory_create.importance,
        confidence=Decimal("1.000"),
        source_type="manual",
        source_reference=None,
        occurred_at=memory_create.occurred_at,
        last_reinforced_at=None,
        content_hash=content_hash,
        superseded_by_memory_id=None,
    )

    session.add(memory)

    try:
        await session.flush()
        await session.refresh(
            memory,
            attribute_names=["created_at", "updated_at"],
        )
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    return memory


async def create_system_memory(
    session: AsyncSession,
    business_id: UUID,
    *,
    memory_type: BusinessMemoryType,
    content: str,
    confidence: Decimal,
    source_reference: str | None = None,
    importance: int = 3,
    occurred_at: datetime | None = None,
) -> BusinessMemory:
    """
    Internal trusted write path for future AI/agent/integration systems.

    This function is intentionally not exposed directly through the public API.
    """
    normalized_content = _validate_and_normalize_internal_content(content)
    normalized_confidence = _validate_and_normalize_confidence(confidence)
    normalized_source_reference = _normalize_source_reference(source_reference)

    if memory_type not in _ALLOWED_MEMORY_TYPES:
        raise ValueError("Unsupported memory type")

    if isinstance(importance, bool) or not 1 <= importance <= 5:
        raise ValueError("importance must be between 1 and 5")

    _validate_aware_datetime(occurred_at, field_name="occurred_at")

    memory = BusinessMemory(
        business_id=business_id,
        memory_type=memory_type,
        content=normalized_content,
        status="active",
        importance=importance,
        confidence=normalized_confidence,
        source_type="system",
        source_reference=normalized_source_reference,
        occurred_at=occurred_at,
        last_reinforced_at=None,
        content_hash=build_memory_content_hash(
            memory_type,
            normalized_content,
        ),
        superseded_by_memory_id=None,
    )

    session.add(memory)

    try:
        await session.flush()
        await session.refresh(
            memory,
            attribute_names=["created_at", "updated_at"],
        )
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    return memory


async def update_business_memory(
    session: AsyncSession,
    business_id: UUID,
    memory_id: UUID,
    memory_update: BusinessMemoryUpdate,
) -> BusinessMemory:
    """
    Apply only explicitly supplied public-editable fields.

    Supersession itself must use supersede_business_memory().
    """
    memory = await get_business_memory(session, business_id, memory_id)
    changes = memory_update.model_dump(exclude_unset=True)

    requested_status = changes.get("status")

    if requested_status == "superseded" and memory.status != "superseded":
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if memory.status == "superseded":
        if requested_status is not None and requested_status != "superseded":
            raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if not changes:
        return memory

    hash_relevant_change = False

    for field_name, value in changes.items():
        if field_name in {"memory_type", "content"}:
            hash_relevant_change = True
        setattr(memory, field_name, value)

    if hash_relevant_change:
        memory.content_hash = build_memory_content_hash(
            memory.memory_type,
            memory.content,
        )

    try:
        await session.flush()
        await session.refresh(memory, attribute_names=["updated_at"])
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    return memory


async def archive_business_memory(
    session: AsyncSession,
    business_id: UUID,
    memory_id: UUID,
) -> BusinessMemory:
    """
    Soft-archive an active memory.

    Archived and superseded memories remain historical records and DELETE is
    idempotent for both lifecycle states.
    """
    memory = await get_business_memory(session, business_id, memory_id)

    if memory.status in {"archived", "superseded"}:
        return memory

    memory.status = "archived"

    try:
        await session.flush()
        await session.refresh(memory, attribute_names=["updated_at"])
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    return memory


async def supersede_business_memory(
    session: AsyncSession,
    business_id: UUID,
    old_memory_id: UUID,
    replacement_memory_id: UUID,
) -> BusinessMemory:
    """
    Link an older memory to a newer active memory within the same tenant.

    Both records are loaded through business-scoped queries.
    """
    if old_memory_id == replacement_memory_id:
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    old_memory = await get_business_memory(
        session,
        business_id,
        old_memory_id,
    )
    replacement = await get_business_memory(
        session,
        business_id,
        replacement_memory_id,
    )

    if old_memory.status == "superseded":
        if old_memory.superseded_by_memory_id == replacement.id:
            return old_memory
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if old_memory.status == "archived":
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if replacement.status != "active":
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if replacement.superseded_by_memory_id is not None:
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    if replacement.superseded_by_memory_id == old_memory.id:
        raise BusinessMemorySupersessionError(_SUPERSESSION_MESSAGE)

    old_memory.status = "superseded"
    old_memory.superseded_by_memory_id = replacement.id

    try:
        await session.flush()
        await session.refresh(old_memory, attribute_names=["updated_at"])
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    return old_memory


async def find_active_memory_candidates_by_hash(
    session: AsyncSession,
    business_id: UUID,
    *,
    memory_type: BusinessMemoryType,
    content: str,
    limit: int = 20,
) -> list[BusinessMemory]:
    """
    Internal duplicate/reinforcement hook.

    Matching hashes are candidates only; this function does not automatically
    merge, reinforce, or deduplicate memories.
    """
    if isinstance(limit, bool) or not 1 <= limit <= MAX_DUPLICATE_CANDIDATES:
        raise ValueError(
            f"limit must be between 1 and {MAX_DUPLICATE_CANDIDATES}"
        )

    content_hash = build_memory_content_hash(memory_type, content)

    statement = (
        select(BusinessMemory)
        .where(
            BusinessMemory.business_id == business_id,
            BusinessMemory.status == "active",
            BusinessMemory.content_hash == content_hash,
        )
        .order_by(
            BusinessMemory.created_at.desc(),
            BusinessMemory.id.desc(),
        )
        .limit(limit)
    )

    try:
        result = await session.scalars(statement)
        memories = list(result.all())
    except SQLAlchemyError:
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE) from None

    if not all(isinstance(memory, BusinessMemory) for memory in memories):
        raise BusinessMemoryPersistenceError(_PERSISTENCE_MESSAGE)

    return memories


def build_memory_content_hash(
    memory_type: BusinessMemoryType,
    content: str,
) -> str:
    """
    Build the deterministic semantic fingerprint for one memory.

    The memory type is part of the identity so identical text used for two
    different memory semantics does not accidentally share one fingerprint.
    """
    normalized_content = _normalize_content_for_hash(content)

    hasher = sha256()
    hasher.update(memory_type.encode("utf-8"))
    hasher.update(b"\x00")
    hasher.update(normalized_content.encode("utf-8"))
    return hasher.hexdigest()


def encode_memory_cursor(created_at: datetime, memory_id: UUID) -> str:
    """Encode a pagination position as compact URL-safe JSON."""
    _validate_aware_datetime(created_at, field_name="created_at")

    payload = {
        "created_at": created_at.isoformat(),
        "id": str(memory_id),
    }
    raw = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_memory_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Strictly decode and validate one opaque memory pagination cursor."""
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise BusinessMemoryCursorError(_CURSOR_MESSAGE)

    try:
        encoded = cursor.encode("ascii")
        padding = b"=" * (-len(encoded) % 4)
        raw = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )

        if len(raw) > 512:
            raise BusinessMemoryCursorError(_CURSOR_MESSAGE)

        payload = json.loads(raw.decode("utf-8"))

        if not isinstance(payload, dict):
            raise BusinessMemoryCursorError(_CURSOR_MESSAGE)

        if set(payload) != {"created_at", "id"}:
            raise BusinessMemoryCursorError(_CURSOR_MESSAGE)

        created_at_raw = payload["created_at"]
        memory_id_raw = payload["id"]

        if not isinstance(created_at_raw, str) or not isinstance(
            memory_id_raw,
            str,
        ):
            raise BusinessMemoryCursorError(_CURSOR_MESSAGE)

        created_at = datetime.fromisoformat(created_at_raw)
        _validate_aware_datetime(created_at, field_name="created_at")

        memory_id = UUID(memory_id_raw)

    except BusinessMemoryCursorError:
        raise
    except (
        UnicodeEncodeError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BusinessMemoryCursorError(_CURSOR_MESSAGE) from None

    return created_at, memory_id


def _normalize_content_for_hash(content: str) -> str:
    """
    Normalize only transport-level line endings and outer whitespace.

    Internal authored whitespace is preserved.
    """
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def _validate_and_normalize_internal_content(content: str) -> str:
    if not isinstance(content, str):
        raise ValueError("content must be a string")

    normalized = content.strip()

    if not normalized:
        raise ValueError("content cannot be blank")

    if len(normalized) > MAX_MEMORY_CONTENT_LENGTH:
        raise ValueError(
            f"content cannot exceed {MAX_MEMORY_CONTENT_LENGTH} characters"
        )

    return normalized


def _validate_and_normalize_confidence(confidence: Decimal) -> Decimal:
    if not isinstance(confidence, Decimal):
        raise ValueError("confidence must be Decimal")

    if not confidence.is_finite():
        raise ValueError("confidence must be finite")

    if not _MIN_CONFIDENCE <= confidence <= _MAX_CONFIDENCE:
        raise ValueError("confidence must be between 0.000 and 1.000")

    try:
        quantized = confidence.quantize(_CONFIDENCE_QUANTUM)
    except InvalidOperation:
        raise ValueError("confidence is invalid") from None

    if quantized != confidence:
        raise ValueError("confidence cannot exceed three decimal places")

    return quantized


def _normalize_source_reference(source_reference: str | None) -> str | None:
    if source_reference is None:
        return None

    if not isinstance(source_reference, str):
        raise ValueError("source_reference must be a string")

    normalized = source_reference.strip()

    if not normalized:
        return None

    if len(normalized) > MAX_MEMORY_SOURCE_REFERENCE_LENGTH:
        raise ValueError(
            "source_reference exceeds the supported maximum length"
        )

    return normalized


def _validate_aware_datetime(
    value: datetime | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        return

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_page_limit(limit: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_MEMORY_PAGE_SIZE:
        raise ValueError(
            f"limit must be between 1 and {MAX_MEMORY_PAGE_SIZE}"
        )