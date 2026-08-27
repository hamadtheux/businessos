from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import io
import re
from typing import BinaryIO, Iterator, Mapping
from uuid import UUID
from defusedxml import ElementTree as SafeElementTree
from defusedxml.common import DefusedXmlException
import xml.etree.ElementTree as ElementTree

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.commerce import CommercePersistenceError, CommerceValidationError
from app.models.commerce import CommerceConnection, CommerceSyncRun
from app.schemas.commerce import (
    CommerceImportFailure,
    CommerceImportMapping,
    CommerceImportPreviewResponse,
    CommerceImportResultResponse,
    NormalizedProduct,
)
from app.services.commerce import _get_or_create_source, _upsert_product, get_connection


MAX_IMPORT_BYTES = 50 * 1024 * 1024
MAX_IMPORT_ITEMS = 250_000
MAX_FAILURES_RETURNED = 500
_ALIASES = {
    "external_object_id": ("id", "product_id", "external_id", "g:id"),
    "name": ("name", "title", "product_name", "g:title"),
    "description": ("description", "body", "g:description"),
    "sku": ("sku", "item_sku"),
    "product_url": ("url", "link", "product_url", "g:link"),
    "image_url": ("image", "image_url", "image_link", "g:image_link"),
    "price": ("price", "sale_price", "g:price"),
    "compare_at_price": ("compare_at_price", "regular_price"),
    "currency": ("currency", "currency_code"),
    "inventory_quantity": ("inventory", "inventory_quantity", "stock", "quantity"),
    "availability": ("availability", "stock_status", "g:availability"),
    "brand": ("brand", "g:brand"),
    "vendor": ("vendor", "manufacturer"),
    "gtin": ("gtin", "barcode", "g:gtin"),
    "mpn": ("mpn", "g:mpn"),
    "condition": ("condition", "g:condition"),
    "category": ("category", "product_type", "google_product_category", "g:google_product_category"),
    "tags": ("tags", "labels"),
    "published": ("published", "active", "visible"),
    "updated_at": ("updated_at", "modified_at", "date_modified"),
}


def preview_import(
    stream: BinaryIO,
    *,
    filename: str,
    file_type: str,
    mapping: CommerceImportMapping,
    limit: int = 25,
) -> CommerceImportPreviewResponse:
    products: list[NormalizedProduct] = []
    failures: list[CommerceImportFailure] = []
    detected: set[str] = set()
    truncated = False
    for item_number, raw in _iter_records(stream, filename=filename, file_type=file_type):
        detected.update(raw)
        try:
            products.append(_normalize_record(raw, item_number=item_number, mapping=mapping))
        except (ValidationError, ValueError, CommerceValidationError) as error:
            failures.append(_failure(item_number, raw, error))
        if item_number >= limit:
            truncated = True
            break
    return CommerceImportPreviewResponse(
        file_type=file_type, detected_fields=sorted(detected)[:100],
        products=products, failures=failures, truncated=truncated,
    )


async def import_products(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    stream: BinaryIO,
    filename: str,
    file_type: str,
    mapping: CommerceImportMapping,
    idempotency_key: str,
) -> CommerceImportResultResponse:
    connection = await get_connection(
        session, business_id=business_id, connection_id=connection_id, for_update=True,
    )
    expected_provider = "csv" if file_type == "csv" else file_type
    if connection.provider != expected_provider:
        raise CommerceValidationError("import_provider_mismatch")
    connection.external_account_id = connection.external_account_id or f"import:{connection.id}"
    existing = await _existing_import_run(
        session, business_id=business_id, connection=connection,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        return CommerceImportResultResponse(
            sync_run_id=existing.id, status=existing.status,
            products_created=existing.products_created,
            products_updated=existing.products_updated,
            products_failed=existing.failures, failures=[],
        )
    run = CommerceSyncRun(
        business_id=business_id, connection_id=connection.id,
        mode="full", idempotency_key=idempotency_key,
        status="running", started_at=datetime.now(UTC),
        provider_metadata={"filename_hash": sha256(filename.encode()).hexdigest()[:24], "file_type": file_type},
    )
    session.add(run)
    await session.flush()
    source = await _get_or_create_source(session, connection=connection)
    failures: list[CommerceImportFailure] = []
    processed = 0
    synchronized_at = run.started_at
    try:
        for item_number, raw in _iter_records(stream, filename=filename, file_type=file_type):
            if item_number > MAX_IMPORT_ITEMS:
                raise CommerceValidationError("import_item_limit_exceeded")
            try:
                product = _normalize_record(raw, item_number=item_number, mapping=mapping)
                created, changed, variant_count = await _upsert_product(
                    session, business_id=business_id, connection=connection,
                    source=source, product=product, synchronized_at=synchronized_at,
                )
                run.products_created += int(created)
                run.products_updated += int(changed and not created)
                run.variants_processed += variant_count
            except (ValidationError, ValueError, CommerceValidationError) as error:
                run.failures += 1
                if len(failures) < MAX_FAILURES_RETURNED:
                    failures.append(_failure(item_number, raw, error))
            processed += 1
            if processed % 250 == 0:
                await session.flush()
        if processed == 0:
            raise CommerceValidationError("import_empty")
        run.pages_processed = (processed + 249) // 250
        run.status = "completed_with_issues" if run.failures else "completed"
        run.completed_at = datetime.now(UTC)
        connection.status = "connected"
        connection.health = "healthy" if not run.failures else "degraded"
        connection.last_sync_started_at = run.started_at
        connection.last_sync_completed_at = run.completed_at
        connection.last_success_at = run.completed_at
        connection.failure_code = None
        source.last_synchronized_at = run.completed_at
        await session.flush()
    except CommerceValidationError:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.failure_code = "import_invalid"
        connection.status = "attention_required"
        connection.health = "degraded"
        connection.failure_code = "import_invalid"
        raise
    except (CommercePersistenceError, SQLAlchemyError, OSError):
        raise CommercePersistenceError("commerce_import_failed") from None
    return CommerceImportResultResponse(
        sync_run_id=run.id, status=run.status,
        products_created=run.products_created,
        products_updated=run.products_updated,
        products_failed=run.failures, failures=failures,
    )


async def _existing_import_run(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, idempotency_key: str) -> CommerceSyncRun | None:
    from sqlalchemy import select

    return await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.business_id == business_id,
        CommerceSyncRun.connection_id == connection.id,
        CommerceSyncRun.idempotency_key == idempotency_key,
    ))


def _iter_records(stream: BinaryIO, *, filename: str, file_type: str) -> Iterator[tuple[int, dict[str, str]]]:
    _validate_stream_size(stream)
    if file_type == "csv":
        yield from _iter_csv(stream)
    elif file_type in {"xml_feed", "google_product_feed"}:
        yield from _iter_xml(stream, google=file_type == "google_product_feed")
    else:
        raise CommerceValidationError("import_file_type_invalid")


def _validate_stream_size(stream: BinaryIO) -> None:
    try:
        current = stream.tell()
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(current)
    except (OSError, AttributeError):
        return
    if size <= 0 or size > MAX_IMPORT_BYTES:
        raise CommerceValidationError("import_file_size_invalid")


def _iter_csv(stream: BinaryIO) -> Iterator[tuple[int, dict[str, str]]]:
    stream.seek(0)
    wrapper = io.TextIOWrapper(stream, encoding="utf-8-sig", errors="strict", newline="")
    try:
        reader = csv.DictReader(wrapper)
        if not reader.fieldnames or len(reader.fieldnames) > 200:
            raise CommerceValidationError("import_header_invalid")
        for item_number, row in enumerate(reader, start=1):
            if row is None:
                continue
            yield item_number, {_normalize_key(key): str(value or "").strip() for key, value in row.items() if key}
    except (csv.Error, UnicodeError):
        raise CommerceValidationError("import_csv_invalid") from None
    finally:
        wrapper.detach()


def _iter_xml(stream: BinaryIO, *, google: bool) -> Iterator[tuple[int, dict[str, str]]]:
    stream.seek(0)
    prefix = stream.read(4096).upper()
    stream.seek(0)
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise CommerceValidationError("import_xml_unsafe")
    item_number = 0
    try:
        for event, element in SafeElementTree.iterparse(stream, events=("end",)):
            tag = _local_name(element.tag)
            if tag not in ({"item", "entry"} if google else {"item", "entry", "product"}):
                continue
            item_number += 1
            row: dict[str, str] = {}
            for child in list(element):
                key = _xml_key(child.tag, google=google)
                if child.text and child.text.strip():
                    row[key] = child.text.strip()
            yield item_number, row
            element.clear()
    except (ElementTree.ParseError, DefusedXmlException):
        raise CommerceValidationError("import_xml_invalid") from None


def _normalize_record(raw: Mapping[str, str], *, item_number: int, mapping: CommerceImportMapping) -> NormalizedProduct:
    values = {field: _mapped_value(raw, field, mapping) for field in _ALIASES}
    name = values["name"]
    if not name:
        raise CommerceValidationError("product_name_required")
    external_id = values["external_object_id"] or values["sku"] or values["product_url"]
    if not external_id:
        raise CommerceValidationError("external_product_identifier_required")
    price, currency_from_price = _parse_price(values["price"])
    currency = (values["currency"] or currency_from_price or "USD").upper()
    quantity = int(values["inventory_quantity"]) if values["inventory_quantity"] else None
    availability = _normalize_availability(values["availability"], quantity)
    image_urls = [values["image_url"]] if values["image_url"] else []
    tags = [item.strip()[:80] for item in re.split(r"[,|]", values["tags"] or "") if item.strip()][:100]
    published = str(values["published"] or "true").casefold() not in {"false", "0", "no", "inactive"}
    return NormalizedProduct(
        external_object_id=str(external_id)[:255], name=name[:200], description=(values["description"] or None),
        sku=values["sku"] or None, product_url=values["product_url"] or None,
        image_urls=image_urls, price=price,
        compare_at_price=_parse_price(values["compare_at_price"])[0] if values["compare_at_price"] else None,
        currency=currency, inventory_quantity=quantity, availability=availability,
        brand=values["brand"] or None, vendor=values["vendor"] or None,
        gtin=values["gtin"] or None, mpn=values["mpn"] or None,
        condition=_normalize_condition(values["condition"]), google_product_category=values["category"] or None,
        tags=tags, published=published, status="active" if published else "draft",
        provider_updated_at=_parse_datetime(values["updated_at"]),
        safe_metadata={"import_item_number": item_number},
    )


def _mapped_value(raw: Mapping[str, str], field: str, mapping: CommerceImportMapping) -> str:
    configured = mapping.fields.get(field)
    if configured:
        return str(raw.get(_normalize_key(configured), "")).strip()
    for alias in _ALIASES[field]:
        value = raw.get(_normalize_key(alias))
        if value:
            return str(value).strip()
    return ""


def _parse_price(value: str) -> tuple[Decimal | None, str | None]:
    if not value:
        return None, None
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([A-Za-z]{3})?", value.replace(",", ""))
    if not match:
        raise CommerceValidationError("product_price_invalid")
    amount = Decimal(match.group(1))
    if amount < 0:
        raise CommerceValidationError("product_price_invalid")
    return amount.quantize(Decimal("0.01")), match.group(2).upper() if match.group(2) else None


def _normalize_availability(value: str, quantity: int | None) -> str:
    normalized = value.casefold().replace(" ", "_")
    aliases = {"instock": "in_stock", "in_stock": "in_stock", "available": "in_stock", "outofstock": "out_of_stock", "out_of_stock": "out_of_stock", "preorder": "preorder", "backorder": "backorder"}
    if normalized in aliases:
        return aliases[normalized]
    if quantity is not None:
        return "in_stock" if quantity > 0 else "out_of_stock"
    return "unknown"


def _normalize_condition(value: str) -> str:
    normalized = value.casefold()
    return normalized if normalized in {"new", "refurbished", "used"} else "new"


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _failure(item_number: int, raw: Mapping[str, str], error: Exception) -> CommerceImportFailure:
    external_id = raw.get("id") or raw.get("product_id") or raw.get("sku")
    code = str(error).split("\n", 1)[0]
    if isinstance(error, ValidationError):
        code = "product_validation_failed"
    return CommerceImportFailure(
        item_number=item_number, external_object_id=str(external_id)[:255] if external_id else None,
        code=re.sub(r"[^a-z0-9_]", "_", code.casefold())[:64] or "product_invalid",
        message="This product row could not be normalized; review its required commerce fields.",
    )


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_:]", "_", value.strip().casefold())[:160]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _xml_key(tag: str, *, google: bool) -> str:
    local = _local_name(tag)
    return f"g:{local}" if google else local
