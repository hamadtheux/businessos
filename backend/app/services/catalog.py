from collections.abc import Collection
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.catalog import (
    CatalogItemNotFoundError,
    CatalogPersistenceError,
    CatalogSkuConflictError,
)
from app.models.catalog_item import CatalogItem
from app.schemas.catalog import (
    CatalogItemCreate,
    CatalogItemStatus,
    CatalogItemType,
    CatalogItemUpdate,
)

_SKU_UNIQUE_CONSTRAINT: Final = "uq_catalog_items_business_sku"
_PERSISTENCE_MESSAGE: Final = "Unable to persist the business catalog"


async def list_catalog_items(
    session: AsyncSession,
    business_id: UUID,
    *,
    item_type: CatalogItemType | None = None,
    item_status: CatalogItemStatus | None = None,
) -> list[CatalogItem]:
    """List catalog items for one explicitly authorized business."""
    statement = select(CatalogItem).where(CatalogItem.business_id == business_id)
    if item_type is not None:
        statement = statement.where(CatalogItem.item_type == item_type)
    if item_status is None:
        statement = statement.where(CatalogItem.status != "archived")
    else:
        statement = statement.where(CatalogItem.status == item_status)
    statement = statement.order_by(CatalogItem.created_at.asc(), CatalogItem.id.asc())

    try:
        result = await session.scalars(statement)
        items = result.all()
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None

    if not all(isinstance(item, CatalogItem) for item in items):
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE)
    return list(items)


async def get_catalog_item(
    session: AsyncSession,
    business_id: UUID,
    item_id: UUID,
) -> CatalogItem:
    """Load one item using both the tenant and item identifiers."""
    try:
        item = await session.scalar(
            select(CatalogItem).where(
                CatalogItem.business_id == business_id,
                CatalogItem.id == item_id,
            )
        )
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None

    if item is None:
        raise CatalogItemNotFoundError("Catalog item not found")
    if not isinstance(item, CatalogItem):
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE)
    return item


async def create_catalog_item(
    session: AsyncSession,
    business_id: UUID,
    item_create: CatalogItemCreate,
) -> CatalogItem:
    """Create and flush one tenant-scoped item without committing."""
    if item_create.sku is not None:
        await _ensure_sku_available(session, business_id, item_create.sku)

    item = CatalogItem(
        business_id=business_id,
        **item_create.model_dump(),
    )
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as error:
        _raise_integrity_error(error, sku=item_create.sku)
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None
    return item


async def update_catalog_item(
    session: AsyncSession,
    business_id: UUID,
    item_id: UUID,
    item_update: CatalogItemUpdate,
) -> CatalogItem:
    """Apply only explicitly supplied fields and flush without committing."""
    item = await get_catalog_item(session, business_id, item_id)
    changes = item_update.model_dump(exclude_unset=True)
    if "sku" in changes and changes["sku"] is not None and changes["sku"] != item.sku:
        await _ensure_sku_available(
            session,
            business_id,
            changes["sku"],
            exclude_item_id=item.id,
        )

    for field_name, value in changes.items():
        setattr(item, field_name, value)

    try:
        await session.flush()
        # PostgreSQL evaluates the SQL expression used by TimestampMixin.onupdate.
        # Reload it while the async session is active so response serialization
        # never attempts an implicit async database read.
        await session.refresh(item, attribute_names=["updated_at"])
    except IntegrityError as error:
        _raise_integrity_error(error, sku=changes.get("sku"))
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None
    return item


async def archive_catalog_item(
    session: AsyncSession,
    business_id: UUID,
    item_id: UUID,
) -> CatalogItem:
    """Idempotently archive one tenant-scoped item without deleting it."""
    item = await get_catalog_item(session, business_id, item_id)
    if item.status == "archived":
        return item
    item.status = "archived"
    try:
        await session.flush()
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None
    return item


async def find_existing_catalog_skus(
    session: AsyncSession,
    business_id: UUID,
    skus: Collection[str],
) -> set[str]:
    """Return only SKU conflicts inside the supplied tenant namespace."""
    if not skus:
        return set()
    try:
        result = await session.scalars(
            select(CatalogItem.sku).where(
                CatalogItem.business_id == business_id,
                CatalogItem.sku.in_(tuple(sorted(skus))),
            )
        )
        existing = result.all()
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None

    if not all(isinstance(sku, str) for sku in existing):
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE)
    return set(existing)


async def create_catalog_items(
    session: AsyncSession,
    business_id: UUID,
    item_creates: Collection[CatalogItemCreate],
) -> list[CatalogItem]:
    """Prepare and flush an atomic catalog batch without committing."""
    items = [
        CatalogItem(business_id=business_id, **item_create.model_dump())
        for item_create in item_creates
    ]
    session.add_all(items)
    try:
        await session.flush()
    except IntegrityError as error:
        _raise_integrity_error(error)
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None
    return items


async def _ensure_sku_available(
    session: AsyncSession,
    business_id: UUID,
    sku: str,
    *,
    exclude_item_id: UUID | None = None,
) -> None:
    statement = select(CatalogItem.id).where(
        CatalogItem.business_id == business_id,
        CatalogItem.sku == sku,
    )
    if exclude_item_id is not None:
        statement = statement.where(CatalogItem.id != exclude_item_id)
    try:
        existing_id = await session.scalar(statement)
    except SQLAlchemyError:
        raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None
    if existing_id is not None:
        raise CatalogSkuConflictError(
            "SKU already exists in this business",
            sku=sku,
        )


def _raise_integrity_error(error: IntegrityError, *, sku: str | None = None) -> None:
    if _constraint_name(error) == _SKU_UNIQUE_CONSTRAINT:
        raise CatalogSkuConflictError(
            "SKU already exists in this business",
            sku=sku,
        ) from None
    raise CatalogPersistenceError(_PERSISTENCE_MESSAGE) from None


def _constraint_name(error: IntegrityError) -> str | None:
    current = getattr(error, "orig", None)
    visited: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in visited:
        visited.add(id(current))
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str):
            return name
        diagnostic = getattr(current, "diag", None)
        name = getattr(diagnostic, "constraint_name", None)
        if isinstance(name, str):
            return name
        current = current.__cause__ or current.__context__
    return None
