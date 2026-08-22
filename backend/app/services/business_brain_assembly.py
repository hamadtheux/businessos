from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Final
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.models.business import Business
from app.models.business_branding import BusinessBranding
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.catalog_item import CatalogItem
from app.schemas.business_brain import BusinessBrainSourceType

DEFAULT_SOURCE_BATCH_SIZE: Final = 250
MAX_SOURCE_BATCH_SIZE: Final = 1_000
BUSINESS_BRAIN_SOURCE_TYPES: Final[tuple[BusinessBrainSourceType, ...]] = (
    "business_profile",
    "branding",
    "catalog_item",
    "knowledge_entry",
)
_ASSEMBLY_MESSAGE: Final = "Unable to assemble Business Brain sources"


@dataclass(frozen=True, slots=True)
class BusinessBrainSource:
    """Immutable canonical runtime record derived from one authoritative source."""

    business_id: UUID
    source_type: BusinessBrainSourceType
    source_id: str
    title: str
    content: str
    updated_at: datetime
    content_hash: str


@dataclass(frozen=True, slots=True)
class BusinessBrainManifest:
    """Deterministic logical revision of the currently active source collection."""

    business_id: UUID
    source_count: int
    source_counts_by_type: Mapping[BusinessBrainSourceType, int]
    revision: str


def build_business_profile_source(business: Business) -> BusinessBrainSource:
    """Create the single canonical profile source for one loaded business."""
    content = _serialize_fields(
        (
            ("Name", business.name),
            ("Business type", business.business_type),
            ("Timezone", business.timezone),
            ("Currency", business.currency),
            ("Locale", business.locale),
        )
    )
    return _build_source(
        business_id=business.id,
        source_type="business_profile",
        source_id="business:profile",
        title="Business profile",
        content=content,
        updated_at=business.updated_at,
    )


def build_branding_source(
    business_id: UUID,
    branding: BusinessBranding | None,
) -> BusinessBrainSource | None:
    """Create a compact branding source only when public branding is meaningful."""
    if branding is None:
        return None
    _require_matching_business(business_id, branding.business_id)
    public_fields = (
        ("Primary color", branding.primary_color),
        ("Secondary color", branding.secondary_color),
        ("Accent color", branding.accent_color),
        ("Logo URL", branding.logo_url),
    )
    if not any(_has_meaningful_value(value) for _, value in public_fields):
        return None
    return _build_source(
        business_id=business_id,
        source_type="branding",
        source_id="business:branding",
        title="Branding",
        content=_serialize_fields(public_fields),
        updated_at=branding.updated_at,
    )


def build_catalog_source(
    business_id: UUID,
    business_currency: str,
    item: CatalogItem,
) -> BusinessBrainSource:
    """Create one canonical source for an already-loaded active catalog item."""
    _require_matching_business(business_id, item.business_id)
    price = None
    if item.price is not None:
        # CatalogItem.price is authoritative NUMERIC(14, 2). Decimal formatting
        # preserves those two database scale digits without binary floating point.
        price = f"{item.price:.2f} {business_currency}"
    content = _serialize_fields(
        (
            ("Type", item.item_type.capitalize()),
            ("Name", item.name),
            ("SKU", item.sku),
            ("Price", price),
            ("Description", item.description),
        )
    )
    return _build_source(
        business_id=business_id,
        source_type="catalog_item",
        source_id=f"catalog:{item.id}",
        title=item.name,
        content=content,
        updated_at=item.updated_at,
    )


def build_knowledge_source(
    business_id: UUID,
    entry: BusinessKnowledgeEntry,
) -> BusinessBrainSource:
    """Create one canonical source without altering authored knowledge content."""
    _require_matching_business(business_id, entry.business_id)
    content = _serialize_fields(
        (
            ("Category", entry.category.capitalize()),
            ("Title", entry.title),
            ("Source type", entry.source_type.capitalize()),
            ("Content", entry.content),
        )
    )
    return _build_source(
        business_id=business_id,
        source_type="knowledge_entry",
        source_id=f"knowledge:{entry.id}",
        title=entry.title,
        content=content,
        updated_at=entry.updated_at,
    )


async def iterate_business_brain_sources(
    session: AsyncSession,
    business_id: UUID,
    *,
    batch_size: int = DEFAULT_SOURCE_BATCH_SIZE,
) -> AsyncIterator[BusinessBrainSource]:
    """Stream deterministic authoritative sources in bounded tenant-scoped pages."""
    _validate_batch_size(batch_size)
    business = await _load_business(session, business_id)
    yield build_business_profile_source(business)

    branding = await _load_branding(session, business_id)
    branding_source = build_branding_source(business_id, branding)
    if branding_source is not None:
        yield branding_source

    async for item in _iterate_active_catalog_items(
        session,
        business_id,
        batch_size=batch_size,
    ):
        yield build_catalog_source(business_id, business.currency, item)

    async for entry in _iterate_active_knowledge_entries(
        session,
        business_id,
        batch_size=batch_size,
    ):
        yield build_knowledge_source(business_id, entry)


async def build_business_brain_manifest(
    session: AsyncSession,
    business_id: UUID,
    *,
    batch_size: int = DEFAULT_SOURCE_BATCH_SIZE,
) -> BusinessBrainManifest:
    """Fold the ordered source stream into a deterministic read-only manifest."""
    counts: dict[BusinessBrainSourceType, int] = {
        source_type: 0 for source_type in BUSINESS_BRAIN_SOURCE_TYPES
    }
    source_count = 0
    revision_hasher = sha256()
    async for source in iterate_business_brain_sources(
        session,
        business_id,
        batch_size=batch_size,
    ):
        source_count += 1
        counts[source.source_type] += 1
        # Source IDs cannot contain NUL and hashes have a fixed ASCII length, so
        # this framing is unambiguous without constructing a giant manifest blob.
        revision_hasher.update(source.source_id.encode("utf-8"))
        revision_hasher.update(b"\x00")
        revision_hasher.update(source.content_hash.encode("ascii"))
        revision_hasher.update(b"\n")

    return BusinessBrainManifest(
        business_id=business_id,
        source_count=source_count,
        source_counts_by_type=MappingProxyType(counts),
        revision=revision_hasher.hexdigest(),
    )


async def _load_business(session: AsyncSession, business_id: UUID) -> Business:
    try:
        business = await session.scalar(
            select(Business).where(Business.id == business_id)
        )
    except SQLAlchemyError:
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE) from None
    if not isinstance(business, Business) or business.id != business_id:
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE)
    return business


async def _load_branding(
    session: AsyncSession,
    business_id: UUID,
) -> BusinessBranding | None:
    try:
        branding = await session.scalar(
            select(BusinessBranding).where(BusinessBranding.business_id == business_id)
        )
    except SQLAlchemyError:
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE) from None
    if branding is not None and (
        not isinstance(branding, BusinessBranding)
        or branding.business_id != business_id
    ):
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE)
    return branding


async def _iterate_active_catalog_items(
    session: AsyncSession,
    business_id: UUID,
    *,
    batch_size: int,
) -> AsyncIterator[CatalogItem]:
    cursor: tuple[datetime, UUID] | None = None
    while True:
        statement = select(CatalogItem).where(
            CatalogItem.business_id == business_id,
            CatalogItem.status == "active",
        )
        if cursor is not None:
            statement = statement.where(
                _after_cursor(
                    CatalogItem.created_at,
                    CatalogItem.id,
                    cursor,
                )
            )
        statement = statement.order_by(
            CatalogItem.created_at.asc(),
            CatalogItem.id.asc(),
        ).limit(batch_size)
        items = await _load_page(session, statement, CatalogItem)
        if not items:
            return
        for item in items:
            _require_matching_business(business_id, item.business_id)
            yield item
        if len(items) < batch_size:
            return
        last = items[-1]
        cursor = (last.created_at, last.id)


async def _iterate_active_knowledge_entries(
    session: AsyncSession,
    business_id: UUID,
    *,
    batch_size: int,
) -> AsyncIterator[BusinessKnowledgeEntry]:
    cursor: tuple[datetime, UUID] | None = None
    while True:
        statement = select(BusinessKnowledgeEntry).where(
            BusinessKnowledgeEntry.business_id == business_id,
            BusinessKnowledgeEntry.status == "active",
        )
        if cursor is not None:
            statement = statement.where(
                _after_cursor(
                    BusinessKnowledgeEntry.created_at,
                    BusinessKnowledgeEntry.id,
                    cursor,
                )
            )
        statement = statement.order_by(
            BusinessKnowledgeEntry.created_at.asc(),
            BusinessKnowledgeEntry.id.asc(),
        ).limit(batch_size)
        entries = await _load_page(session, statement, BusinessKnowledgeEntry)
        if not entries:
            return
        for entry in entries:
            _require_matching_business(business_id, entry.business_id)
            yield entry
        if len(entries) < batch_size:
            return
        last = entries[-1]
        cursor = (last.created_at, last.id)


async def _load_page[RecordT: (CatalogItem, BusinessKnowledgeEntry)](
    session: AsyncSession,
    statement: Select[tuple[RecordT]],
    expected_type: type[RecordT],
) -> list[RecordT]:
    try:
        result = await session.scalars(statement)
        records = list(result.all())
    except SQLAlchemyError:
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE) from None
    if not all(isinstance(record, expected_type) for record in records):
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE)
    return records


def _after_cursor(
    created_at_column: InstrumentedAttribute[datetime],
    id_column: InstrumentedAttribute[UUID],
    cursor: tuple[datetime, UUID],
) -> ColumnElement[bool]:
    created_at, record_id = cursor
    return or_(
        created_at_column > created_at,
        and_(created_at_column == created_at, id_column > record_id),
    )


def _build_source(
    *,
    business_id: UUID,
    source_type: BusinessBrainSourceType,
    source_id: str,
    title: str,
    content: str,
    updated_at: datetime,
) -> BusinessBrainSource:
    return BusinessBrainSource(
        business_id=business_id,
        source_type=source_type,
        source_id=source_id,
        title=title,
        content=content,
        updated_at=updated_at,
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
    )


def _serialize_fields(fields: Sequence[tuple[str, object | None]]) -> str:
    """Apply one compact stable label format while preserving source values."""
    return "\n".join(
        f"{label}: {value}" for label, value in fields if _has_meaningful_value(value)
    )


def _has_meaningful_value(value: object | None) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _require_matching_business(expected: UUID, actual: UUID) -> None:
    if actual != expected:
        raise BusinessBrainAssemblyError(_ASSEMBLY_MESSAGE)


def _validate_batch_size(batch_size: int) -> None:
    if isinstance(batch_size, bool) or not 1 <= batch_size <= MAX_SOURCE_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_SOURCE_BATCH_SIZE}")
