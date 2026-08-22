from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.business_brain import (
    BusinessKnowledgeEntryNotFoundError,
    BusinessKnowledgePersistenceError,
)
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.schemas.business_brain import (
    BusinessKnowledgeCategory,
    BusinessKnowledgeEntryCreate,
    BusinessKnowledgeEntryUpdate,
    BusinessKnowledgeStatus,
)

_PERSISTENCE_MESSAGE = "Unable to persist Business Brain knowledge"


async def list_knowledge_entries(
    session: AsyncSession,
    business_id: UUID,
    *,
    category: BusinessKnowledgeCategory | None = None,
    entry_status: BusinessKnowledgeStatus | None = None,
) -> list[BusinessKnowledgeEntry]:
    """List entries for one explicitly authorized business."""
    statement = select(BusinessKnowledgeEntry).where(
        BusinessKnowledgeEntry.business_id == business_id
    )
    if category is not None:
        statement = statement.where(BusinessKnowledgeEntry.category == category)
    if entry_status is None:
        statement = statement.where(BusinessKnowledgeEntry.status != "archived")
    else:
        statement = statement.where(BusinessKnowledgeEntry.status == entry_status)
    statement = statement.order_by(
        BusinessKnowledgeEntry.created_at.asc(),
        BusinessKnowledgeEntry.id.asc(),
    )

    try:
        result = await session.scalars(statement)
        entries = result.all()
    except SQLAlchemyError:
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE) from None

    if not all(isinstance(entry, BusinessKnowledgeEntry) for entry in entries):
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE)
    return list(entries)


async def get_knowledge_entry(
    session: AsyncSession,
    business_id: UUID,
    entry_id: UUID,
) -> BusinessKnowledgeEntry:
    """Load one entry using both its tenant and entry identifiers."""
    try:
        entry = await session.scalar(
            select(BusinessKnowledgeEntry).where(
                BusinessKnowledgeEntry.business_id == business_id,
                BusinessKnowledgeEntry.id == entry_id,
            )
        )
    except SQLAlchemyError:
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE) from None

    if entry is None:
        raise BusinessKnowledgeEntryNotFoundError("Knowledge entry not found")
    if not isinstance(entry, BusinessKnowledgeEntry):
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE)
    return entry


async def create_knowledge_entry(
    session: AsyncSession,
    business_id: UUID,
    entry_create: BusinessKnowledgeEntryCreate,
) -> BusinessKnowledgeEntry:
    """Create and flush one manual entry without committing."""
    entry = BusinessKnowledgeEntry(
        business_id=business_id,
        **entry_create.model_dump(),
        source_type="manual",
        source_reference=None,
    )
    session.add(entry)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE) from None
    return entry


async def update_knowledge_entry(
    session: AsyncSession,
    business_id: UUID,
    entry_id: UUID,
    entry_update: BusinessKnowledgeEntryUpdate,
) -> BusinessKnowledgeEntry:
    """Apply explicitly supplied editable fields and flush without committing."""
    entry = await get_knowledge_entry(session, business_id, entry_id)
    for field_name, value in entry_update.model_dump(exclude_unset=True).items():
        setattr(entry, field_name, value)

    try:
        await session.flush()
        # TimestampMixin uses a SQL expression for onupdate. Reload it inside the
        # async session so response serialization never triggers implicit I/O.
        await session.refresh(entry, attribute_names=["updated_at"])
    except SQLAlchemyError:
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE) from None
    return entry


async def archive_knowledge_entry(
    session: AsyncSession,
    business_id: UUID,
    entry_id: UUID,
) -> BusinessKnowledgeEntry:
    """Idempotently archive one tenant-scoped entry without deleting it."""
    entry = await get_knowledge_entry(session, business_id, entry_id)
    if entry.status == "archived":
        return entry
    entry.status = "archived"
    try:
        await session.flush()
    except SQLAlchemyError:
        raise BusinessKnowledgePersistenceError(_PERSISTENCE_MESSAGE) from None
    return entry
