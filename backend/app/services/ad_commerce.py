from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions.commerce import (
    CommerceConflictError,
    CommerceConfigurationRequiredError,
    CommerceNotFoundError,
    CommercePersistenceError,
    CommerceProviderError,
    CommerceValidationError,
)
from app.integrations.ad_commerce_adapters import (
    AdCommerceProviderError,
    GoogleMerchantAdapter,
    MetaCatalogAdapter,
)
from app.integrations.ad_commerce_contracts import (
    NormalizedProductDestinationInput,
    ProductGroup as NormalizedProductGroup,
    ProductIssue,
)
from app.integrations.credentials import IntegrationCredentialStore, credential_store
from app.models.catalog_item import CatalogItem
from app.models.commerce import (
    CatalogMedia,
    CommerceFeedDestination,
    CommerceFeedProductStatus,
    ProductGroup,
    ProductGroupDestination,
    ProductGroupItem,
)
from app.models.integration import IntegrationConnection
from app.schemas.commerce import ProductGroupCreate
from app.services.operations import record_audit


def provider_adapters():
    return {
        "google_merchant_center": GoogleMerchantAdapter(),
        "meta_product_catalog": MetaCatalogAdapter(api_version=settings.meta_graph_api_version),
    }


async def synchronize_destination(
    session: AsyncSession,
    *,
    business_id: UUID,
    destination_id: UUID,
    actor_user_id: UUID,
    idempotency_key: str,
    reconcile_only: bool = False,
    adapters: Mapping[str, object] | None = None,
    credentials: IntegrationCredentialStore = credential_store,
) -> CommerceFeedDestination:
    destination = await _destination(
        session, business_id=business_id, destination_id=destination_id, for_update=True,
    )
    connection, material = await _authorized_material(
        session, business_id=business_id, destination=destination, credentials=credentials,
    )
    adapter = (adapters or provider_adapters()).get(destination.provider)
    if adapter is None:
        raise CommerceConfigurationRequiredError("configuration_required")
    if not destination.external_account_id:
        raise CommerceConfigurationRequiredError("asset_selection_required")

    if (
        destination.provider == "google_merchant_center"
        and destination.managed
        and not destination.external_resource_id
    ):
        available = await adapter.list_destinations(
            material, account_reference=destination.external_account_id,
        )
        managed = next((item for item in available if item.managed and item.display_name == destination.display_name), None)
        if managed is None:
            managed = await adapter.create_managed_destination(
                material, account_reference=destination.external_account_id,
                display_name=(destination.display_name if destination.display_name.casefold().startswith("ai business os") else f"AI Business OS · {destination.display_name}")[:160],
                content_language=destination.content_language,
                feed_label=destination.feed_label or "US",
            )
        destination.external_resource_id = managed.external_reference
        resources = list(connection.selected_resources)
        resources.append({
            "resource_type": "google_merchant_data_source",
            "external_reference": managed.external_reference,
            "display_name": managed.display_name,
        })
        connection.selected_resources = resources[:20]
    if not destination.external_resource_id:
        raise CommerceConfigurationRequiredError("asset_selection_required")

    destination.status = "syncing"
    destination.failure_code = None
    now = datetime.now(UTC)
    existing = {
        value.catalog_item_id: value
        for value in (await session.scalars(select(CommerceFeedProductStatus).where(
            CommerceFeedProductStatus.business_id == business_id,
            CommerceFeedProductStatus.destination_id == destination_id,
        ))).all()
    }
    products = list((await session.scalars(select(CatalogItem).where(
        CatalogItem.business_id == business_id,
        CatalogItem.item_type == "product",
    ).order_by(CatalogItem.id))).all())
    media_rows = list((await session.scalars(select(CatalogMedia).where(
        CatalogMedia.business_id == business_id,
        CatalogMedia.catalog_item_id.in_([item.id for item in products]) if products else False,
        CatalogMedia.media_type == "image",
        CatalogMedia.active.is_(True),
        CatalogMedia.authoritative.is_(True),
    ).order_by(CatalogMedia.catalog_item_id, CatalogMedia.position, CatalogMedia.id))).all())
    media: dict[UUID, list[str]] = {}
    for item in media_rows:
        if _public_https(item.source_url):
            media.setdefault(item.catalog_item_id, []).append(item.source_url)

    try:
        if not reconcile_only:
            for product in products:
                status = existing.get(product.id)
                if status is None:
                    status = CommerceFeedProductStatus(
                        business_id=business_id,
                        destination_id=destination_id,
                        catalog_item_id=product.id,
                    )
                    session.add(status)
                    existing[product.id] = status
                offer_id = product.sku or str(product.id)
                if product.status == "archived" or not product.published:
                    result = await adapter.archive_product(
                        material,
                        account_reference=destination.external_account_id,
                        destination_reference=destination.external_resource_id,
                        offer_id=offer_id,
                        external_product_reference=status.external_product_id,
                        owned=status.owned_by_aibos,
                        idempotency_key=f"{idempotency_key}:{product.id}:archive",
                    )
                    _apply_write(status, result, now)
                    continue
                normalized, issues = _normalize_product(product, media.get(product.id, []), destination)
                if normalized is None:
                    status.external_product_id = status.external_product_id or offer_id
                    status.status = "attention_required"
                    status.missing_attributes = [issue.attribute or issue.code for issue in issues]
                    status.warnings = [issue.message for issue in issues]
                    status.provider_issues = [_issue_dict(issue) for issue in issues]
                    status.provider_error_code = "product_ineligible"
                    continue
                result = await adapter.upsert_product(
                    material,
                    account_reference=destination.external_account_id,
                    destination_reference=destination.external_resource_id,
                    product=normalized,
                    idempotency_key=f"{idempotency_key}:{product.id}:upsert",
                )
                _apply_write(status, result, now)

        reconciled = await adapter.reconcile_products(
            material,
            account_reference=destination.external_account_id,
            destination_reference=destination.external_resource_id,
        )
        if destination.provider == "google_merchant_center" and hasattr(adapter, "list_account_issues"):
            account_issues = await adapter.list_account_issues(
                material, account_reference=destination.external_account_id,
            )
            metadata = dict(destination.safe_metadata)
            metadata["account_issues"] = [_issue_dict(issue) for issue in account_issues[:50]]
            destination.safe_metadata = metadata
        by_offer = {value.offer_id: value for value in reconciled}
        for product in products:
            local = existing.get(product.id)
            if local is None:
                continue
            provider_status = by_offer.get(product.sku or str(product.id))
            if provider_status is not None:
                local.external_product_id = provider_status.external_product_reference or local.external_product_id
                local.status = provider_status.state
                local.provider_issues = [_issue_dict(issue) for issue in provider_status.issues]
                local.warnings = [issue.message for issue in provider_status.issues]
                local.provider_error_code = next((issue.code for issue in provider_status.issues if issue.severity == "error"), None)
                local.last_synchronized_at = now
        _update_destination_counts(destination, list(existing.values()), now)
        connection.last_successful_sync_at = now
        await session.flush()
    except AdCommerceProviderError as exc:
        destination.status = "attention_required"
        destination.failure_code = exc.code[:64]
        await session.flush()
        raise CommerceProviderError(exc.code, retryable=exc.retryable) from None
    except SQLAlchemyError:
        raise CommercePersistenceError("feed_sync_failed") from None

    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.feed_destination_synchronized",
        entity_type="commerce_feed_destination", entity_id=destination.id,
        summary=(
            f"Synchronized {destination.display_name}: {destination.submitted_count} submitted, "
            f"{destination.eligible_count} eligible, {destination.limited_count} limited, "
            f"{destination.rejected_count} ineligible."
        ),
    )
    return destination


async def create_product_group(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    data: ProductGroupCreate,
) -> ProductGroup:
    product_ids = set((await session.scalars(select(CatalogItem.id).where(
        CatalogItem.business_id == business_id,
        CatalogItem.id.in_(data.catalog_item_ids),
        CatalogItem.item_type == "product",
        CatalogItem.status != "archived",
    ))).all())
    if product_ids != set(data.catalog_item_ids):
        raise CommerceValidationError("catalog_selection_invalid")
    group = ProductGroup(
        business_id=business_id, created_by_user_id=actor_user_id,
        name=data.name, external_key=data.external_key,
        group_type=data.group_type, rule=data.rule, status="active",
    )
    session.add(group)
    try:
        await session.flush()
        for product_id in data.catalog_item_ids:
            session.add(ProductGroupItem(
                business_id=business_id, product_group_id=group.id,
                catalog_item_id=product_id,
            ))
        await session.flush()
    except IntegrityError:
        raise CommerceConflictError("product_group_already_exists") from None
    except SQLAlchemyError:
        raise CommercePersistenceError("product_group_create_failed") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.product_group_created", entity_type="commerce_product_group",
        entity_id=group.id, summary=f"Created product group {group.name} with {len(product_ids)} products.",
    )
    return group


async def list_product_groups(session: AsyncSession, *, business_id: UUID) -> list[dict[str, object]]:
    groups = list((await session.scalars(select(ProductGroup).where(
        ProductGroup.business_id == business_id,
        ProductGroup.status != "archived",
    ).order_by(ProductGroup.updated_at.desc(), ProductGroup.id))).all())
    ids = [item.id for item in groups]
    rows = list((await session.execute(select(
        ProductGroupItem.product_group_id, ProductGroupItem.catalog_item_id,
    ).where(
        ProductGroupItem.business_id == business_id,
        ProductGroupItem.product_group_id.in_(ids) if ids else False,
    ))).all())
    members: dict[UUID, list[UUID]] = {}
    for group_id, item_id in rows:
        members.setdefault(group_id, []).append(item_id)
    return [{
        **{column.name: getattr(group, column.name) for column in group.__table__.columns},
        "catalog_item_ids": members.get(group.id, []),
    } for group in groups]


async def synchronize_product_group(
    session: AsyncSession,
    *,
    business_id: UUID,
    product_group_id: UUID,
    destination_id: UUID,
    actor_user_id: UUID,
    idempotency_key: str,
    adapters: Mapping[str, object] | None = None,
    credentials: IntegrationCredentialStore = credential_store,
) -> ProductGroupDestination:
    group = await session.scalar(select(ProductGroup).where(
        ProductGroup.id == product_group_id,
        ProductGroup.business_id == business_id,
        ProductGroup.status == "active",
    ))
    if group is None:
        raise CommerceNotFoundError("product_group_not_found")
    destination = await _destination(session, business_id=business_id, destination_id=destination_id)
    _, material = await _authorized_material(
        session, business_id=business_id, destination=destination, credentials=credentials,
    )
    members = list((await session.scalars(select(CatalogItem).join(
        ProductGroupItem,
        (ProductGroupItem.catalog_item_id == CatalogItem.id)
        & (ProductGroupItem.business_id == CatalogItem.business_id),
    ).where(
        ProductGroupItem.business_id == business_id,
        ProductGroupItem.product_group_id == group.id,
    ))).all())
    adapter = (adapters or provider_adapters()).get(destination.provider)
    if adapter is None or not destination.external_account_id:
        raise CommerceConfigurationRequiredError("configuration_required")
    rule = dict(group.rule)
    if destination.provider == "meta_product_catalog":
        if not destination.external_resource_id:
            raise CommerceConfigurationRequiredError("catalog_required")
        rule["catalog_reference"] = destination.external_resource_id
    normalized = NormalizedProductGroup(
        external_key=group.external_key, name=group.name, rule=rule,
        offer_ids=tuple(item.sku or str(item.id) for item in members),
    )
    binding = await session.scalar(select(ProductGroupDestination).where(
        ProductGroupDestination.business_id == business_id,
        ProductGroupDestination.product_group_id == group.id,
        ProductGroupDestination.destination_id == destination.id,
    ).with_for_update())
    if binding is None:
        binding = ProductGroupDestination(
            business_id=business_id, product_group_id=group.id,
            destination_id=destination.id,
        )
        session.add(binding)
    try:
        result = await adapter.upsert_product_group(
            material, account_reference=destination.external_account_id,
            group=normalized, idempotency_key=idempotency_key,
        )
        binding.external_reference = result.external_reference
        binding.status = "ready"
        binding.failure_code = None
        await session.flush()
    except AdCommerceProviderError as exc:
        binding.status = "attention_required"
        binding.failure_code = exc.code[:64]
        await session.flush()
        raise CommerceProviderError(exc.code, retryable=exc.retryable) from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.product_group_synchronized", entity_type="commerce_product_group",
        entity_id=group.id, summary=f"Mapped product group {group.name} to {destination.display_name}.",
    )
    return binding


async def _destination(session, *, business_id, destination_id, for_update=False):
    statement = select(CommerceFeedDestination).where(
        CommerceFeedDestination.id == destination_id,
        CommerceFeedDestination.business_id == business_id,
    )
    if for_update:
        statement = statement.with_for_update()
    destination = await session.scalar(statement)
    if destination is None:
        raise CommerceNotFoundError("feed_destination_not_found")
    return destination


async def _authorized_material(session, *, business_id, destination, credentials):
    expected = "google_ads" if destination.provider == "google_merchant_center" else "meta_ads"
    if destination.integration_connection_id is None:
        raise CommerceConfigurationRequiredError("connection_required")
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.id == destination.integration_connection_id,
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type == expected,
    ))
    if connection is None:
        raise CommerceConfigurationRequiredError("connection_required")
    if connection.status != "connected" or connection.authentication_state != "authorized" or not connection.credential_reference:
        code = "reauthorization_required" if connection.authentication_state in {"failed", "revoked"} else "authorization_required"
        raise CommerceConfigurationRequiredError(code)
    selected = {(item.get("resource_type"), item.get("external_reference")) for item in connection.selected_resources}
    account_type = "google_merchant_account" if expected == "google_ads" else "meta_business"
    resource_type = "google_merchant_data_source" if expected == "google_ads" else "meta_catalog"
    if (account_type, destination.external_account_id) not in selected:
        raise CommerceConfigurationRequiredError("account_selection_required")
    if destination.external_resource_id and (resource_type, destination.external_resource_id) not in selected:
        raise CommerceConfigurationRequiredError("asset_selection_required")
    if not destination.external_resource_id and not (
        destination.provider == "google_merchant_center" and destination.managed
    ):
        raise CommerceConfigurationRequiredError("asset_selection_required")
    material = await credentials.retrieve(
        connection.credential_reference, business_id=business_id,
        connector_type=connection.connector_type, purpose="oauth_credentials",
    )
    return connection, material


def _normalize_product(product, images, destination):
    missing = []
    required = {
        "description": product.description,
        "price": product.price,
        "currency": product.currency,
        "product_url": product.product_url,
        "image": images[0] if images else None,
    }
    for key, value in required.items():
        if value is None or value == "":
            missing.append(ProductIssue(
                code=f"missing_{key}", message=f"Authoritative {key.replace('_', ' ')} is required.",
                severity="error", resolution="owner_input_required" if key in {"price", "currency"} else "store_source_update_required",
                attribute=key,
            ))
    if product.availability == "unknown":
        missing.append(ProductIssue(
            code="missing_availability", message="Authoritative availability is required.",
            severity="error", resolution="store_source_update_required", attribute="availability",
        ))
    if missing:
        return None, tuple(missing)
    sale_price = None
    provider_price = product.price
    if product.compare_at_price is not None and product.price is not None and product.price < product.compare_at_price:
        sale_price = product.price
        # Provider catalogs treat `price` as the regular price and
        # `sale_price` as the current discounted price. CatalogItem keeps the
        # current price in `price` and the regular price in `compare_at_price`.
        provider_price = product.compare_at_price
    return NormalizedProductDestinationInput(
        offer_id=product.sku or str(product.id), title=product.name,
        description=product.description, link=product.product_url,
        image_link=images[0], additional_image_links=tuple(images[1:11]),
        availability=product.availability, price=provider_price,
        currency=product.currency, content_language=destination.content_language,
        feed_label=destination.feed_label or product.currency,
        sale_price=sale_price, brand=product.brand, gtin=product.gtin, mpn=product.mpn,
        condition=product.condition, google_product_category=product.google_product_category,
        product_type=product.tags[0] if product.tags else None,
    ), ()


def _apply_write(local, result, now):
    local.external_product_id = result.external_product_reference or local.external_product_id
    local.status = result.state
    local.owned_by_aibos = local.owned_by_aibos or result.acknowledged
    local.submitted_at = now if result.state == "submitted" else local.submitted_at
    local.provider_issues = [_issue_dict(issue) for issue in result.issues]
    local.warnings = [issue.message for issue in result.issues]
    local.provider_error_code = next((issue.code for issue in result.issues if issue.severity == "error"), None)


def _update_destination_counts(destination, statuses, now):
    active = [item for item in statuses if item.status not in {"removed", "archived"}]
    destination.synchronized_count = len(active)
    destination.submitted_count = sum(item.status in {"submitted", "processing", "eligible", "limited", "warning"} for item in active)
    destination.eligible_count = sum(item.status == "eligible" for item in active)
    destination.limited_count = sum(item.status in {"limited", "warning"} for item in active)
    destination.warning_count = sum(bool(item.warnings) for item in active)
    destination.rejected_count = sum(item.status in {"attention_required", "ineligible", "rejected", "error"} for item in active)
    destination.last_synchronized_at = now
    destination.status = "attention_required" if destination.rejected_count else "connected"
    destination.failure_code = "product_ineligible" if destination.rejected_count else None


def _issue_dict(issue: ProductIssue) -> dict[str, object]:
    return {
        "code": issue.code, "message": issue.message, "severity": issue.severity,
        "resolution": issue.resolution, "attribute": issue.attribute,
        "provider_reference": issue.provider_reference,
    }


def _public_https(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None
