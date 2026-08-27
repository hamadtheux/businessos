from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.commerce import (
    CommerceConflictError,
    CommerceNotFoundError,
    CommercePersistenceError,
    CommerceValidationError,
    CommerceConfigurationRequiredError,
    CommerceProviderError,
)
from app.exceptions.integration import IntegrationCredentialUnavailableError
from app.integrations.commerce_contracts import CommerceSyncPage, CommerceSyncRequest as ConnectorSyncRequest, CommerceWebhookRequest
from app.integrations.commerce_registry import CommerceConnectorRegistry, commerce_connectors
from app.integrations.credentials import CredentialMaterial, IntegrationCredentialStore, credential_store
from app.exceptions.operations import OperationsConflictError, OperationsPersistenceError, OperationsValidationError
from app.models.catalog_item import CatalogItem
from app.models.commerce import (
    AudienceSegment,
    AudienceSegmentMember,
    CatalogMedia,
    CatalogSource,
    CatalogVariant,
    CommerceConnection,
    CommerceEvent,
    CommerceFeedDestination,
    CommerceFeedProductStatus,
    CommerceSyncRun,
    CommerceSyncIssue,
    CommerceWebhookReceipt,
    ExternalCustomerMapping,
    ExternalOrderMapping,
    ExternalProductMapping,
)
from app.models.customer import Customer
from app.models.integration import IntegrationConnection
from app.models.order import Order, OrderAddress, OrderFulfillment, OrderLineItem, OrderRefund, OrderRefundLine
from app.schemas.commerce import (
    AudienceRule,
    AudienceRuleCondition,
    AudienceSegmentCompileRequest,
    AudienceSegmentCreate,
    CommerceConnectionCreate,
    CommerceConnectionConfigure,
    CommerceEventCreate,
    FeedDestinationCreate,
    NormalizedProduct,
    NormalizedCustomer,
    NormalizedOrder,
)
from app.services.background_jobs import enqueue_job
from app.services.automation_events import record_automation_event
from app.services.customer_identity import resolve_customer_identity
from app.services.operations import record_audit
from app.domain.audience_safety import contains_sensitive_targeting


_PROVIDER_CAPABILITIES = ["catalog_read", "incremental_sync", "variants", "inventory_read"]
_PROVIDER_CREDENTIAL_FIELDS = {
    "shopify": ({"access_token"}, {"access_token", "webhook_secret", "api_version"}),
    "woocommerce": ({"consumer_key", "consumer_secret"}, {"consumer_key", "consumer_secret", "webhook_secret"}),
    "bigcommerce": ({"access_token", "store_hash"}, {"access_token", "store_hash", "webhook_secret"}),
    "magento": (
        {"access_token"},
        {"access_token", "webhook_public_key", "webhook_secret"},
    ),
    "custom_api": ({"api_token", "configuration"}, {"api_token", "configuration", "webhook_secret"}),
}


async def list_connections(session: AsyncSession, *, business_id: UUID) -> list[CommerceConnection]:
    try:
        return list((await session.scalars(
            select(CommerceConnection).where(CommerceConnection.business_id == business_id)
            .order_by(CommerceConnection.updated_at.desc(), CommerceConnection.id.desc())
        )).all())
    except SQLAlchemyError:
        raise CommercePersistenceError("connection_read_failed") from None


async def get_connection(
    session: AsyncSession, *, business_id: UUID, connection_id: UUID, for_update: bool = False,
) -> CommerceConnection:
    statement = select(CommerceConnection).where(
        CommerceConnection.id == connection_id,
        CommerceConnection.business_id == business_id,
    )
    if for_update:
        statement = statement.with_for_update()
    try:
        value = await session.scalar(statement)
    except SQLAlchemyError:
        raise CommercePersistenceError("connection_read_failed") from None
    if value is None:
        raise CommerceNotFoundError("connection_not_found")
    return value


async def create_connection(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID,
    data: CommerceConnectionCreate,
) -> CommerceConnection:
    if data.integration_connection_id is not None:
        integration = await session.scalar(select(IntegrationConnection).where(
            IntegrationConnection.id == data.integration_connection_id,
            IntegrationConnection.business_id == business_id,
        ))
        if integration is None:
            raise CommerceValidationError("integration_connection_invalid")
    value = CommerceConnection(
        business_id=business_id,
        connected_by_user_id=actor_user_id,
        provider=data.provider,
        display_name=data.display_name,
        external_account_id=data.external_account_id,
        store_url=str(data.store_url) if data.store_url else None,
        integration_connection_id=data.integration_connection_id,
        status="configuration_required",
        health="not_checked",
        capabilities=_PROVIDER_CAPABILITIES,
        safe_metadata={},
        sync_cursor={},
    )
    session.add(value)
    try:
        await session.flush()
    except IntegrityError:
        raise CommerceConflictError("connection_already_exists") from None
    except SQLAlchemyError:
        raise CommercePersistenceError("connection_create_failed") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.connection_created", entity_type="commerce_connection",
        entity_id=value.id,
        summary=f"Added {value.display_name}; provider synchronization remains {value.status.replace('_', ' ')}.",
    )
    return value


async def configure_connection(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    actor_user_id: UUID,
    data: CommerceConnectionConfigure,
    credentials: IntegrationCredentialStore = credential_store,
    connectors: CommerceConnectorRegistry = commerce_connectors,
) -> CommerceConnection:
    """Authenticate before claiming a connection is connected."""
    connection = await get_connection(
        session, business_id=business_id, connection_id=connection_id, for_update=True,
    )
    connector = connectors.connector(connection.provider)
    if connector is None or connection.provider in {"csv", "manual", "xml_feed", "google_product_feed"}:
        raise CommerceValidationError("provider_configuration_not_supported")
    material_values = {
        key: value.get_secret_value()
        for key, value in data.credentials.items()
    }
    required_fields, allowed_fields = _PROVIDER_CREDENTIAL_FIELDS[connection.provider]
    if set(material_values) - allowed_fields or not required_fields.issubset(material_values):
        raise CommerceConfigurationRequiredError("provider_credentials_invalid")
    if connection.store_url:
        material_values.setdefault("store_url", connection.store_url)
    try:
        material = CredentialMaterial(values=material_values)
    except ValueError:
        raise CommerceConfigurationRequiredError("credential_material_invalid") from None
    # Prove the submitted material against the provider before storing or
    # rotating it. A failed authentication can therefore never replace a
    # previously working credential reference.
    try:
        page = await connector.synchronize(
            material,
            ConnectorSyncRequest(
                external_account_id=connection.external_account_id or str(connection.id),
                mode="initial",
                domain="store",
                store_url=connection.store_url,
                page_size=1,
            ),
            idempotency_key=f"commerce-auth:{connection.id}",
        )
    except (CommerceConfigurationRequiredError, CommerceProviderError):
        raise
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        raise CommerceProviderError("invalid_response", retryable=False) from None
    if page.store is None:
        raise CommerceProviderError("invalid_response")
    old_reference = connection.credential_reference
    try:
        if old_reference:
            await credentials.rotate(
                old_reference,
                business_id=business_id,
                connector_type=f"commerce_{connection.provider}",
                purpose="connection",
                material=material,
            )
            reference = old_reference
        else:
            reference = await credentials.store(
                business_id=business_id,
                connector_type=f"commerce_{connection.provider}",
                purpose="connection",
                material=material,
            )
    except IntegrationCredentialUnavailableError:
        raise CommerceConfigurationRequiredError("secure_credential_store_unavailable") from None
    connection.credential_reference = reference
    connection.external_account_id = page.store.external_account_id
    connection.store_name = page.store.name
    connection.status = "connected"
    connection.health = "not_checked"
    connection.failure_code = None
    connection.consecutive_failures = 0
    connection.capabilities = sorted(connector.capabilities)
    connection.safe_metadata = {
        "currency": page.store.currency,
        "timezone": page.store.timezone,
        "public_url": str(page.store.public_url) if page.store.public_url else None,
    }
    try:
        await session.flush()
    except SQLAlchemyError:
        if old_reference is None:
            try:
                await credentials.revoke(
                    reference,
                    business_id=business_id,
                    connector_type=f"commerce_{connection.provider}",
                    purpose="connection",
                )
            except IntegrationCredentialUnavailableError:
                pass
        raise CommercePersistenceError("connection_configuration_failed") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.connection_authenticated", entity_type="commerce_connection",
        entity_id=connection.id,
        summary=f"Authenticated {connection.display_name}; no provider secrets were persisted in application data.",
    )
    return connection


async def create_sync_run(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    mode: str,
    idempotency_key: str,
) -> tuple[CommerceSyncRun, bool]:
    connection = await get_connection(session, business_id=business_id, connection_id=connection_id, for_update=True)
    existing = await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.business_id == business_id,
        CommerceSyncRun.connection_id == connection_id,
        CommerceSyncRun.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        return existing, False
    ready = bool(
        connection.credential_reference
        and connection.status not in {"configuration_required", "connection_required", "authentication_expired", "disabled"}
    )
    status = "queued" if ready else "configuration_required"
    value = CommerceSyncRun(
        business_id=business_id, connection_id=connection_id, mode=mode,
        idempotency_key=idempotency_key, status=status,
        failure_code=None if status == "queued" else connection.status,
    )
    session.add(value)
    try:
        await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(CommerceSyncRun).where(
            CommerceSyncRun.business_id == business_id,
            CommerceSyncRun.connection_id == connection_id,
            CommerceSyncRun.idempotency_key == idempotency_key,
        ))
        if existing is None:
            raise CommercePersistenceError("sync_run_create_failed") from None
        return existing, False
    except SQLAlchemyError:
        raise CommercePersistenceError("sync_run_create_failed") from None
    return value, True


async def enqueue_sync_run(
    session: AsyncSession,
    *,
    run: CommerceSyncRun,
) -> None:
    job_type = "commerce_initial_sync" if run.mode in {"initial", "full"} else "commerce_incremental_sync"
    await enqueue_job(
        session,
        business_id=run.business_id,
        job_type=job_type,
        idempotency_key=f"commerce-sync:{run.id}:start",
        commerce_sync_run_id=run.id,
    )


async def request_sync(
    session: AsyncSession, *, business_id: UUID, connection_id: UUID,
    mode: str, idempotency_key: str,
) -> tuple[CommerceSyncRun, bool]:
    run, created = await create_sync_run(
        session, business_id=business_id, connection_id=connection_id,
        mode=mode, idempotency_key=idempotency_key,
    )
    if created and run.status == "queued":
        run.next_cursor = {"domain": "store", "cursor": {}, "completed_domains": []}
        await enqueue_sync_run(session, run=run)
    return run, created


async def list_sync_runs(
    session: AsyncSession, *, business_id: UUID, connection_id: UUID, limit: int,
) -> list[CommerceSyncRun]:
    await get_connection(session, business_id=business_id, connection_id=connection_id)
    return list((await session.scalars(select(CommerceSyncRun).where(
        CommerceSyncRun.business_id == business_id,
        CommerceSyncRun.connection_id == connection_id,
    ).order_by(CommerceSyncRun.created_at.desc(), CommerceSyncRun.id.desc()).limit(limit))).all())


async def list_sync_issues(
    session: AsyncSession, *, business_id: UUID, sync_run_id: UUID,
) -> list[CommerceSyncIssue]:
    if not await session.scalar(select(CommerceSyncRun.id).where(
        CommerceSyncRun.id == sync_run_id, CommerceSyncRun.business_id == business_id,
    )):
        raise CommerceNotFoundError("sync_run_not_found")
    return list((await session.scalars(select(CommerceSyncIssue).where(
        CommerceSyncIssue.business_id == business_id,
        CommerceSyncIssue.sync_run_id == sync_run_id,
    ).order_by(CommerceSyncIssue.severity.desc(), CommerceSyncIssue.created_at, CommerceSyncIssue.id))).all())


async def process_sync_run_page(
    session: AsyncSession,
    *,
    business_id: UUID,
    sync_run_id: UUID,
    execution_id: UUID | None = None,
    credentials: IntegrationCredentialStore = credential_store,
    connectors: CommerceConnectorRegistry = commerce_connectors,
) -> bool:
    """Fetch and commit exactly one bounded provider page.

    Returns True when another page was durably queued.
    """
    run = await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.id == sync_run_id,
        CommerceSyncRun.business_id == business_id,
    ))
    if run is None:
        raise CommerceNotFoundError("sync_run_not_found")
    if run.status in {"completed", "completed_with_issues"}:
        return False
    if execution_id is not None and run.provider_metadata.get("last_execution_id") == str(execution_id):
        return False
    connection = await get_connection(
        session, business_id=business_id, connection_id=run.connection_id,
    )
    connector = connectors.connector(connection.provider)
    if connector is None or not connection.credential_reference:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="configuration_required", retryable=False,
        )
        raise CommerceConfigurationRequiredError()
    try:
        material = await credentials.retrieve(
            connection.credential_reference,
            business_id=business_id,
            connector_type=f"commerce_{connection.provider}",
            purpose="connection",
        )
    except IntegrationCredentialUnavailableError:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="configuration_required", retryable=False,
        )
        raise CommerceConfigurationRequiredError() from None
    state = dict(run.next_cursor or {})
    expected_pages_processed = run.pages_processed
    expected_state_fingerprint = sha256(
        json.dumps(
            state, sort_keys=True, default=str, separators=(",", ":"),
        ).encode()
    ).hexdigest()
    domain = str(state.get("domain") or "store")
    if domain == "inventory":
        domain = "products"
    if domain not in {"store", "products", "customers", "orders"}:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="invalid_cursor", retryable=False,
        )
        raise CommerceProviderError("invalid_cursor")
    raw_cursor = state.get("cursor") or {}
    cursor = dict(raw_cursor) if isinstance(raw_cursor, dict) else {}
    watermark = state.get("watermark")
    updated_since = _parse_cursor_datetime(watermark) if run.mode in {"incremental", "manual_retry"} else None
    if updated_since is None and run.mode in {"incremental", "manual_retry"}:
        updated_since = connection.last_success_at
    try:
        page = await connector.synchronize(
            material,
            ConnectorSyncRequest(
                external_account_id=connection.external_account_id or str(connection.id),
                mode=run.mode,
                domain=domain,
                cursor=cursor,
                store_url=connection.store_url,
                updated_since=updated_since,
                external_object_id=_optional_cursor_text(state.get("external_object_id")),
                page_size=100,
            ),
            idempotency_key=f"commerce-sync:{run.id}:{domain}:{run.pages_processed + 1}",
        )
    except CommerceProviderError as error:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code=error.code, retryable=error.retryable,
        )
        raise
    except CommerceConfigurationRequiredError:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="configuration_required", retryable=False,
        )
        raise
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="invalid_response", retryable=False,
        )
        raise CommerceProviderError("invalid_response", retryable=False) from None
    if page.domain != domain:
        await mark_sync_failure(
            session, business_id=business_id, sync_run_id=sync_run_id,
            code="invalid_response", retryable=False,
        )
        raise CommerceProviderError("invalid_response", retryable=False)
    run = await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.id == sync_run_id,
        CommerceSyncRun.business_id == business_id,
    ).with_for_update())
    if run is None:
        raise CommerceNotFoundError("sync_run_not_found")
    locked_state_fingerprint = sha256(
        json.dumps(
            dict(run.next_cursor or {}),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if (
        run.status in {
            "completed", "completed_with_issues", "failed",
            "configuration_required",
        }
        or run.pages_processed != expected_pages_processed
        or locked_state_fingerprint != expected_state_fingerprint
    ):
        # Another worker committed this page while the provider request was in
        # flight. Its durable cursor is authoritative, so this stale result is
        # discarded instead of double-applying counters/events or scheduling
        # a second successor page.
        return False
    connection = await get_connection(
        session, business_id=business_id, connection_id=run.connection_id, for_update=True,
    )
    now = datetime.now(UTC)
    run.status = "running"
    run.started_at = run.started_at or now
    run.completed_at = None
    run.failure_code = None
    connection.status = "syncing"
    connection.last_sync_started_at = connection.last_sync_started_at or run.started_at
    await _apply_normalized_page(
        session, run=run, connection=connection, page=page, synchronized_at=now,
    )
    run.pages_processed += 1
    run.provider_metadata = _bounded_metadata({
        **run.provider_metadata,
        **page.provider_metadata,
        "last_execution_id": str(execution_id) if execution_id else None,
    })
    completed = list(state.get("completed_domains") or [])
    if page.has_more:
        next_state = {
            "domain": domain,
            "cursor": dict(page.next_cursor),
            "watermark": watermark or (connection.last_success_at.isoformat() if connection.last_success_at else None),
            "completed_domains": completed,
        }
    else:
        if domain not in completed:
            completed.append(domain)
        if domain == "products" and run.mode in {"initial", "full"} and page.complete_snapshot:
            await _archive_missing_products(
                session, business_id=business_id, connection=connection, run=run,
            )
        next_domain = _next_domain(domain)
        if next_domain is None:
            await _complete_sync_run(session, run=run, connection=connection, instant=now)
            return False
        next_state = {
            "domain": next_domain,
            "cursor": {},
            "watermark": watermark or (connection.last_success_at.isoformat() if connection.last_success_at else None),
            "completed_domains": completed,
        }
    run.next_cursor = next_state
    connection.sync_cursor = next_state
    digest = sha256(json.dumps(next_state, sort_keys=True, default=str).encode()).hexdigest()[:20]
    job_type = "commerce_initial_sync" if run.mode in {"initial", "full"} else "commerce_incremental_sync"
    await enqueue_job(
        session,
        business_id=business_id,
        job_type=job_type,
        idempotency_key=f"commerce-sync:{run.id}:{digest}",
        commerce_sync_run_id=run.id,
    )
    await session.flush()
    return True


async def _apply_normalized_page(
    session: AsyncSession, *, run: CommerceSyncRun, connection: CommerceConnection,
    page: CommerceSyncPage, synchronized_at: datetime,
) -> None:
    if page.store is not None:
        connection.external_account_id = page.store.external_account_id
        connection.store_name = page.store.name
        connection.safe_metadata = _bounded_metadata({
            **connection.safe_metadata,
            "currency": page.store.currency,
            "timezone": page.store.timezone,
            "public_url": str(page.store.public_url) if page.store.public_url else None,
        })
    source = await _get_or_create_source(session, connection=connection)
    for product in page.products:
        created, changed, variant_count = await _upsert_product(
            session, business_id=run.business_id, connection=connection,
            source=source, product=product, synchronized_at=synchronized_at,
        )
        run.products_created += int(created)
        run.products_updated += int(changed and not created)
        run.variants_processed += variant_count
    for customer in page.customers:
        try:
            created, changed, _value = await _upsert_customer(
                session, business_id=run.business_id, connection=connection,
                customer=customer, synchronized_at=synchronized_at,
            )
        except CommerceValidationError as error:
            _add_sync_issue(
                session, run=run, code=str(error) or "customer_identity_invalid",
                message="A provider customer could not be resolved from trusted identity fields.",
                external_object_id=customer.external_object_id,
            )
            continue
        run.customers_created += int(created)
        run.customers_updated += int(changed and not created)
    for order in page.orders:
        try:
            created, changed, refund_count, fulfillment_count = await _upsert_order(
                session, business_id=run.business_id, connection=connection,
                order=order, synchronized_at=synchronized_at,
            )
        except CommerceValidationError as error:
            _add_sync_issue(
                session, run=run, code=str(error) or "order_invalid",
                message="A provider order could not be applied safely.",
                external_object_id=order.external_object_id,
            )
            continue
        run.orders_created += int(created)
        run.orders_updated += int(changed and not created)
        run.refunds_processed += refund_count
        run.fulfillments_processed += fulfillment_count
    source.last_synchronized_at = synchronized_at
    await session.flush()


async def _upsert_customer(
    session: AsyncSession, *, business_id: UUID, connection: CommerceConnection,
    customer: NormalizedCustomer, synchronized_at: datetime,
) -> tuple[bool, bool, Customer]:
    account = connection.external_account_id or str(connection.id)
    mapping = await session.scalar(select(ExternalCustomerMapping).where(
        ExternalCustomerMapping.business_id == business_id,
        ExternalCustomerMapping.provider == connection.provider,
        ExternalCustomerMapping.external_account_id == account,
        ExternalCustomerMapping.external_object_id == customer.external_object_id,
    ))
    created = mapping is None
    if mapping is None:
        if not (customer.email or customer.phone):
            raise CommerceValidationError("customer_verified_identity_required")
        try:
            resolution = await resolve_customer_identity(
                session, business_id=business_id, display_name=customer.display_name,
                email=customer.email, phone=customer.phone, source=connection.provider,
                create_if_missing=True, tags=customer.tags, company=customer.company,
            )
        except (OperationsConflictError, OperationsValidationError):
            raise CommerceValidationError("customer_identity_invalid") from None
        except OperationsPersistenceError:
            raise CommercePersistenceError("customer_identity_failed") from None
        if resolution.customer is None:
            raise CommerceValidationError("customer_identity_invalid")
        value = resolution.customer
        mapping = ExternalCustomerMapping(
            business_id=business_id, connection_id=connection.id,
            customer_id=value.id, provider=connection.provider,
            external_account_id=account, external_object_id=customer.external_object_id,
            last_synchronized_at=synchronized_at,
        )
        session.add(mapping)
    else:
        value = await session.scalar(select(Customer).where(
            Customer.id == mapping.customer_id, Customer.business_id == business_id,
        ))
        if value is None:
            raise CommercePersistenceError("mapped_customer_missing")
        if customer.email or customer.phone:
            try:
                resolution = await resolve_customer_identity(
                    session,
                    business_id=business_id,
                    display_name=customer.display_name,
                    email=customer.email,
                    phone=customer.phone,
                    source=connection.provider,
                    create_if_missing=False,
                )
            except (OperationsConflictError, OperationsValidationError):
                raise CommerceValidationError("customer_identity_invalid") from None
            except OperationsPersistenceError:
                raise CommercePersistenceError("customer_identity_failed") from None
            if resolution.customer is not None and resolution.customer.id != value.id:
                raise CommerceValidationError("customer_identity_conflict")
    before = (value.display_name, value.first_name, value.last_name, value.email, value.phone, value.company)
    if customer.display_name:
        value.display_name = customer.display_name
    if customer.first_name:
        value.first_name = customer.first_name
    if customer.last_name:
        value.last_name = customer.last_name
    if customer.email:
        value.email = customer.email.casefold()
    if customer.phone:
        value.phone = customer.phone
    if customer.company:
        value.company = customer.company
    value.source = connection.provider
    value.status = "active"
    value.active = True
    mapping.provider_updated_at = customer.provider_updated_at
    mapping.last_synchronized_at = synchronized_at
    changed = before != (value.display_name, value.first_name, value.last_name, value.email, value.phone, value.company)
    return created, changed, value


async def _upsert_order(
    session: AsyncSession, *, business_id: UUID, connection: CommerceConnection,
    order: NormalizedOrder, synchronized_at: datetime,
) -> tuple[bool, bool, int, int]:
    account = connection.external_account_id or str(connection.id)
    mapping = await session.scalar(select(ExternalOrderMapping).where(
        ExternalOrderMapping.business_id == business_id,
        ExternalOrderMapping.provider == connection.provider,
        ExternalOrderMapping.external_account_id == account,
        ExternalOrderMapping.external_object_id == order.external_object_id,
    ))
    customer_value: Customer | None = None
    if order.external_customer_id:
        customer_mapping = await session.scalar(select(ExternalCustomerMapping).where(
            ExternalCustomerMapping.business_id == business_id,
            ExternalCustomerMapping.provider == connection.provider,
            ExternalCustomerMapping.external_account_id == account,
            ExternalCustomerMapping.external_object_id == order.external_customer_id,
        ))
        if customer_mapping is not None:
            customer_value = await session.scalar(select(Customer).where(
                Customer.id == customer_mapping.customer_id,
                Customer.business_id == business_id,
            ))
    if customer_value is None and order.customer is not None:
        _created, _changed, customer_value = await _upsert_customer(
            session, business_id=business_id, connection=connection,
            customer=order.customer, synchronized_at=synchronized_at,
        )
    created = mapping is None
    if mapping is None:
        order_number = order.order_number
        if await session.scalar(select(Order.id).where(
            Order.business_id == business_id, Order.order_number == order_number,
        )):
            order_number = f"{connection.provider[:8]}-{sha256(order.external_object_id.encode()).hexdigest()[:12]}"[:40]
        value = Order(
            business_id=business_id, customer_id=customer_value.id if customer_value else None,
            order_number=order_number, status=order.status, source=connection.provider,
            currency=order.currency, subtotal=order.subtotal, adjustment_amount=0, total=order.total,
        )
        session.add(value)
        await session.flush()
        mapping = ExternalOrderMapping(
            business_id=business_id, connection_id=connection.id, order_id=value.id,
            provider=connection.provider, external_account_id=account,
            external_object_id=order.external_object_id, last_synchronized_at=synchronized_at,
        )
        session.add(mapping)
    else:
        value = await session.scalar(select(Order).where(
            Order.id == mapping.order_id, Order.business_id == business_id,
        ))
        if value is None:
            raise CommercePersistenceError("mapped_order_missing")
    before = (
        value.status, value.total, value.payment_status, value.fulfillment_status,
        value.customer_id,
    )
    value.customer_id = customer_value.id if customer_value else value.customer_id
    value.status = order.status
    value.currency = order.currency
    value.subtotal = order.subtotal
    value.adjustment_amount = 0
    value.discount_amount = order.discount_amount
    value.tax_amount = order.tax_amount
    value.shipping_amount = order.shipping_amount
    value.refunded_amount = sum((refund.amount for refund in order.refunds), Decimal("0"))
    value.total = order.total
    value.payment_status = order.payment_status
    value.fulfillment_status = order.fulfillment_status
    value.provider_created_at = order.created_at
    value.provider_updated_at = order.updated_at
    mapping.provider_updated_at = order.updated_at
    mapping.last_synchronized_at = synchronized_at
    await _sync_order_lines(
        session, business_id=business_id, connection=connection, order_value=value,
        lines=order.lines,
    )
    await _sync_order_address(session, business_id=business_id, order_value=value, address_type="billing", address=order.billing_address)
    await _sync_order_address(session, business_id=business_id, order_value=value, address_type="shipping", address=order.shipping_address)
    refund_count = await _sync_refunds(
        session, business_id=business_id, connection=connection,
        order_value=value, order=order,
    )
    fulfillment_count = await _sync_fulfillments(
        session, business_id=business_id, connection=connection,
        order_value=value, order=order,
    )
    await _record_order_events(session, business_id=business_id, connection=connection, order_value=value, order=order)
    changed = before != (value.status, value.total, value.payment_status, value.fulfillment_status, value.customer_id)
    return created, changed, refund_count, fulfillment_count


async def _sync_order_lines(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, order_value: Order, lines) -> None:
    account = connection.external_account_id or str(connection.id)
    seen: set[str] = set()
    for line in lines:
        seen.add(line.external_object_id)
        value = await session.scalar(select(OrderLineItem).where(
            OrderLineItem.business_id == business_id,
            OrderLineItem.order_id == order_value.id,
            OrderLineItem.external_object_id == line.external_object_id,
        ))
        catalog_item_id = None
        if line.external_product_id:
            product_mapping = await session.scalar(select(ExternalProductMapping).where(
                ExternalProductMapping.business_id == business_id,
                ExternalProductMapping.provider == connection.provider,
                ExternalProductMapping.external_account_id == account,
                ExternalProductMapping.external_object_id == line.external_product_id,
            ))
            catalog_item_id = product_mapping.catalog_item_id if product_mapping else None
        if value is None:
            value = OrderLineItem(
                business_id=business_id, order_id=order_value.id,
                external_object_id=line.external_object_id,
                description=line.title, quantity=line.quantity, unit_price=line.unit_price,
            )
            session.add(value)
        value.catalog_item_id = catalog_item_id
        value.external_variant_id = line.external_variant_id
        value.sku = line.sku
        value.description = line.title
        value.quantity = line.quantity
        value.unit_price = line.unit_price
        value.discount_amount = line.discount_amount
        value.tax_amount = line.tax_amount
    existing = list((await session.scalars(select(OrderLineItem).where(
        OrderLineItem.business_id == business_id,
        OrderLineItem.order_id == order_value.id,
        OrderLineItem.external_object_id.is_not(None),
    ))).all())
    for value in existing:
        if value.external_object_id not in seen:
            await session.delete(value)


async def _sync_order_address(session: AsyncSession, *, business_id: UUID, order_value: Order, address_type: str, address) -> None:
    if address is None:
        return
    value = await session.scalar(select(OrderAddress).where(
        OrderAddress.business_id == business_id,
        OrderAddress.order_id == order_value.id,
        OrderAddress.address_type == address_type,
    ))
    if value is None:
        value = OrderAddress(business_id=business_id, order_id=order_value.id, address_type=address_type)
        session.add(value)
    for field, item in address.model_dump().items():
        setattr(value, field, item)


async def _sync_refunds(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, order_value: Order, order: NormalizedOrder) -> int:
    account = connection.external_account_id or str(connection.id)
    for refund in order.refunds:
        value = await session.scalar(select(OrderRefund).where(
            OrderRefund.business_id == business_id,
            OrderRefund.order_id == order_value.id,
            OrderRefund.external_object_id == refund.external_object_id,
        ))
        if value is None:
            value = OrderRefund(
                business_id=business_id, order_id=order_value.id,
                external_object_id=refund.external_object_id,
                provider=connection.provider, external_account_id=account,
                amount=refund.amount, currency=refund.currency,
                occurred_at=refund.occurred_at, reason=refund.reason,
            )
            session.add(value)
            await session.flush()
        value.amount = refund.amount
        value.currency = refund.currency
        value.occurred_at = refund.occurred_at
        value.reason = refund.reason
        value.provider = connection.provider
        value.external_account_id = account
        existing_lines = list((await session.scalars(select(OrderRefundLine).where(
            OrderRefundLine.business_id == business_id,
            OrderRefundLine.refund_id == value.id,
        ))).all())
        for index, line in enumerate(refund.lines):
            if index < len(existing_lines):
                line_value = existing_lines[index]
            else:
                line_value = OrderRefundLine(business_id=business_id, refund_id=value.id)
                session.add(line_value)
            line_value.external_order_line_id = line.external_order_line_id
            line_value.quantity = line.quantity
            line_value.amount = line.amount
        for line_value in existing_lines[len(refund.lines):]:
            await session.delete(line_value)
    return len(order.refunds)


async def _sync_fulfillments(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, order_value: Order, order: NormalizedOrder) -> int:
    account = connection.external_account_id or str(connection.id)
    for fulfillment in order.fulfillments:
        value = await session.scalar(select(OrderFulfillment).where(
            OrderFulfillment.business_id == business_id,
            OrderFulfillment.order_id == order_value.id,
            OrderFulfillment.external_object_id == fulfillment.external_object_id,
        ))
        if value is None:
            value = OrderFulfillment(
                business_id=business_id, order_id=order_value.id,
                external_object_id=fulfillment.external_object_id,
                provider=connection.provider, external_account_id=account,
            )
            session.add(value)
        value.status = fulfillment.status
        value.provider = connection.provider
        value.external_account_id = account
        value.occurred_at = fulfillment.occurred_at
        value.tracking_company = fulfillment.tracking_company
        value.tracking_number = fulfillment.tracking_number
        value.tracking_url = str(fulfillment.tracking_url) if fulfillment.tracking_url else None
        value.external_order_line_ids = list(dict.fromkeys(fulfillment.external_order_line_ids))
    return len(order.fulfillments)


async def _record_order_events(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, order_value: Order, order: NormalizedOrder) -> None:
    event_types = [("order_created", order.created_at)]
    if order.payment_status in {"paid", "partially_refunded", "refunded"}:
        event_types.append(("order_paid", order.updated_at or order.created_at))
    if order.fulfillment_status == "fulfilled":
        event_types.append(("order_fulfilled", order.updated_at or order.created_at))
    if order.refunds:
        event_types.append(("order_refunded", max(item.occurred_at for item in order.refunds)))
    for event_type, occurred_at in event_types:
        external_event_id = f"order:{order.external_object_id}:{event_type}"
        event_id = uuid4()
        inserted_id = await session.scalar(
            pg_insert(CommerceEvent)
            .values(
                id=event_id,
                business_id=business_id,
                customer_id=order_value.customer_id,
                order_id=order_value.id,
                event_type=event_type,
                source=connection.provider,
                external_event_id=external_event_id,
                occurred_at=occurred_at,
                safe_metadata={"provider_order_id": order.external_object_id},
            )
            .on_conflict_do_nothing(
                constraint="uq_commerce_events_source_external",
            )
            .returning(CommerceEvent.id)
        )
        if inserted_id is None:
            continue
        record_automation_event(
            session, business_id=business_id, event_type=event_type,
            entity_type="commerce_event", entity_id=inserted_id,
            payload={"event": {"type": event_type, "source": connection.provider}, "order_id": str(order_value.id)},
        )


async def _archive_missing_products(session: AsyncSession, *, business_id: UUID, connection: CommerceConnection, run: CommerceSyncRun) -> None:
    if run.started_at is None:
        return
    source = await _get_or_create_source(session, connection=connection)
    mappings = list((await session.scalars(select(ExternalProductMapping).where(
        ExternalProductMapping.business_id == business_id,
        ExternalProductMapping.catalog_source_id == source.id,
        ExternalProductMapping.last_synchronized_at < run.started_at,
        ExternalProductMapping.sync_state != "archived",
    ))).all())
    for mapping in mappings:
        item = await session.scalar(select(CatalogItem).where(
            CatalogItem.id == mapping.catalog_item_id,
            CatalogItem.business_id == business_id,
        ))
        if item is not None and item.sync_state != "local_override":
            item.status = "archived"
            item.published = False
            item.sync_state = "in_sync"
            mapping.sync_state = "archived"
            run.products_archived += 1


async def _complete_sync_run(session: AsyncSession, *, run: CommerceSyncRun, connection: CommerceConnection, instant: datetime) -> None:
    run.status = "completed_with_issues" if run.failures or run.warnings else "completed"
    run.completed_at = instant
    run.next_cursor = {}
    connection.status = "connected"
    connection.health = "healthy" if not (run.failures or run.warnings) else "degraded"
    connection.failure_code = None
    connection.consecutive_failures = 0
    connection.last_sync_completed_at = instant
    connection.last_success_at = instant
    connection.sync_cursor = {"updated_since": instant.isoformat()}
    receipts = list((await session.scalars(select(CommerceWebhookReceipt).where(
        CommerceWebhookReceipt.business_id == run.business_id,
        CommerceWebhookReceipt.sync_run_id == run.id,
        CommerceWebhookReceipt.status == "queued",
    ))).all())
    for receipt in receipts:
        receipt.status = "reconciled"
        receipt.reconciled_at = instant
        receipt.failure_code = None
    await session.flush()


async def mark_sync_failure(
    session: AsyncSession, *, business_id: UUID, sync_run_id: UUID,
    code: str, retryable: bool,
) -> None:
    run = await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.id == sync_run_id, CommerceSyncRun.business_id == business_id,
    ).with_for_update())
    if run is None:
        return
    connection = await get_connection(
        session, business_id=business_id, connection_id=run.connection_id, for_update=True,
    )
    now = datetime.now(UTC)
    run.started_at = run.started_at or now
    run.failure_code = code[:64]
    connection.consecutive_failures += 1
    connection.failure_code = code[:64]
    connection.last_sync_completed_at = now
    if code in {"authentication_failed", "authorization_required", "configuration_required"}:
        connection.status = "authentication_expired" if code != "configuration_required" else "configuration_required"
        connection.health = "reauth_required" if code != "configuration_required" else "not_checked"
    elif code == "rate_limited":
        connection.status = "rate_limited"
        connection.health = "rate_limited"
    else:
        connection.status = "attention_required" if retryable else "failed"
        connection.health = "degraded" if retryable else "failed"
    if not retryable:
        run.status = "configuration_required" if code == "configuration_required" else "failed"
        run.completed_at = now
        receipts = list((await session.scalars(select(CommerceWebhookReceipt).where(
            CommerceWebhookReceipt.business_id == business_id,
            CommerceWebhookReceipt.sync_run_id == run.id,
            CommerceWebhookReceipt.status == "queued",
        ))).all())
        for receipt in receipts:
            receipt.status = "failed"
            receipt.failure_code = code[:64]
    _add_sync_issue(
        session, run=run, code=code,
        message=_safe_provider_issue_message(code), severity="error",
    )
    await session.flush()


def _add_sync_issue(
    session: AsyncSession, *, run: CommerceSyncRun, code: str, message: str,
    external_object_id: str | None = None, severity: str = "warning",
) -> None:
    if severity == "error":
        run.failures += 1
    else:
        run.warnings += 1
    session.add(CommerceSyncIssue(
        business_id=run.business_id, sync_run_id=run.id,
        external_object_id=external_object_id,
        severity=severity, code=re.sub(r"[^a-z0-9_]", "_", code.casefold())[:64] or "sync_issue",
        message=message[:1000], safe_details={},
    ))


def _safe_provider_issue_message(code: str) -> str:
    return {
        "authentication_failed": "Provider authentication expired or was rejected.",
        "configuration_required": "Provider configuration is required.",
        "rate_limited": "The provider rate limit was reached; synchronization will retry safely.",
        "provider_unavailable": "The provider is temporarily unavailable.",
        "temporary_provider_failure": "The provider is temporarily unavailable.",
        "authorization_required": "The provider credential lacks permission for this commerce resource.",
        "provider_validation_error": "The provider rejected the synchronized request.",
        "provider_not_found": "The requested provider resource no longer exists.",
        "provider_payload_incomplete": "Provider child data exceeded the safe normalized contract and was not applied.",
        "invalid_cursor": "The provider cursor became invalid and requires safe reconciliation.",
    }.get(code, "Provider synchronization failed without exposing sensitive provider details.")


def _next_domain(domain: str) -> str | None:
    domains = ("store", "products", "customers", "orders")
    index = domains.index(domain)
    return domains[index + 1] if index + 1 < len(domains) else None


def _parse_cursor_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _optional_cursor_text(value: object) -> str | None:
    return str(value)[:255] if value is not None else None


def _bounded_metadata(value: dict[str, object]) -> dict[str, object]:
    safe = {str(key)[:80]: item for key, item in list(value.items())[:100] if item is None or isinstance(item, (str, int, bool))}
    if len(json.dumps(safe, default=str, separators=(",", ":"))) > 16_000:
        return {}
    return safe


async def ingest_provider_webhook(
    session: AsyncSession,
    *,
    provider: str,
    connection_id: UUID,
    headers: dict[str, str],
    body: bytes,
    credentials: IntegrationCredentialStore = credential_store,
    connectors: CommerceConnectorRegistry = commerce_connectors,
) -> tuple[CommerceWebhookReceipt, bool]:
    connection = await session.scalar(select(CommerceConnection).where(
        CommerceConnection.id == connection_id,
        CommerceConnection.provider == provider,
        CommerceConnection.status != "disabled",
    ))
    if connection is None:
        raise CommerceNotFoundError("connection_not_found")
    connector = connectors.connector(provider)
    if connector is None or not connection.credential_reference:
        raise CommerceConfigurationRequiredError()
    try:
        material = await credentials.retrieve(
            connection.credential_reference,
            business_id=connection.business_id,
            connector_type=f"commerce_{provider}",
            purpose="connection",
        )
    except IntegrationCredentialUnavailableError:
        raise CommerceConfigurationRequiredError() from None
    event = connector.verify_and_parse_webhook(
        material,
        CommerceWebhookRequest(
            headers=headers, body=body,
            connection_external_account_id=connection.external_account_id,
        ),
    )
    existing = await session.scalar(select(CommerceWebhookReceipt).where(
        CommerceWebhookReceipt.connection_id == connection.id,
        CommerceWebhookReceipt.external_event_id == event.external_event_id,
    ))
    if existing is not None:
        return existing, True
    receipt = CommerceWebhookReceipt(
        business_id=connection.business_id, connection_id=connection.id,
        external_event_id=event.external_event_id, topic=event.topic,
        reconciliation_domain=event.reconciliation_domain,
        external_object_id=event.external_object_id,
        status="received", received_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(receipt)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(CommerceWebhookReceipt).where(
            CommerceWebhookReceipt.connection_id == connection.id,
            CommerceWebhookReceipt.external_event_id == event.external_event_id,
        ))
        if existing is None:
            raise CommercePersistenceError("webhook_ingest_failed") from None
        return existing, True
    await enqueue_job(
        session, business_id=connection.business_id,
        job_type="commerce_webhook_reconcile",
        idempotency_key=f"commerce-webhook:{receipt.id}",
        commerce_webhook_receipt_id=receipt.id,
    )
    receipt.status = "queued"
    return receipt, False


async def reconcile_webhook(
    session: AsyncSession, *, business_id: UUID, receipt_id: UUID,
) -> CommerceWebhookReceipt:
    receipt = await session.scalar(select(CommerceWebhookReceipt).where(
        CommerceWebhookReceipt.id == receipt_id,
        CommerceWebhookReceipt.business_id == business_id,
    ).with_for_update())
    if receipt is None:
        raise CommerceNotFoundError("webhook_receipt_not_found")
    if receipt.status == "reconciled":
        return receipt
    connection = await get_connection(
        session, business_id=business_id, connection_id=receipt.connection_id,
    )
    run, _created = await create_sync_run(
        session, business_id=business_id, connection_id=connection.id,
        mode="incremental", idempotency_key=f"webhook:{receipt.external_event_id}",
    )
    if run.status == "configuration_required":
        receipt.status = "failed"
        receipt.failure_code = "configuration_required"
        raise CommerceConfigurationRequiredError()
    domain = "products" if receipt.reconciliation_domain == "inventory" else receipt.reconciliation_domain
    run.next_cursor = {
        "domain": domain, "cursor": {},
        "watermark": connection.last_success_at.isoformat() if connection.last_success_at else None,
        "external_object_id": receipt.external_object_id,
        "completed_domains": [],
    }
    receipt.sync_run_id = run.id
    receipt.status = "queued"
    receipt.failure_code = None
    await enqueue_sync_run(session, run=run)
    await session.flush()
    return receipt


async def apply_catalog_sync(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection_id: UUID,
    sync_run_id: UUID,
    products: list[NormalizedProduct],
    complete_snapshot: bool,
    next_cursor: dict[str, object] | None = None,
    provider_metadata: dict[str, object] | None = None,
) -> CommerceSyncRun:
    connection = await get_connection(session, business_id=business_id, connection_id=connection_id, for_update=True)
    run = await session.scalar(select(CommerceSyncRun).where(
        CommerceSyncRun.id == sync_run_id,
        CommerceSyncRun.business_id == business_id,
        CommerceSyncRun.connection_id == connection_id,
    ).with_for_update())
    if run is None:
        raise CommerceNotFoundError("sync_run_not_found")
    if run.status in {"completed", "completed_with_issues"}:
        return run
    if not connection.external_account_id:
        raise CommerceValidationError("external_account_required")
    now = datetime.now(UTC)
    run.status = "running"
    run.started_at = run.started_at or now
    connection.status = "syncing"
    connection.last_sync_started_at = run.started_at
    source = await _get_or_create_source(session, connection=connection)
    seen: set[str] = set()
    for product in products:
        seen.add(product.external_object_id)
        created, changed, variant_count = await _upsert_product(
            session, business_id=business_id, connection=connection,
            source=source, product=product, synchronized_at=now,
        )
        run.products_created += int(created)
        run.products_updated += int(changed and not created)
        run.variants_processed += variant_count
    if complete_snapshot:
        mappings = list((await session.scalars(select(ExternalProductMapping).where(
            ExternalProductMapping.business_id == business_id,
            ExternalProductMapping.catalog_source_id == source.id,
            ExternalProductMapping.sync_state != "archived",
        ))).all())
        for mapping in mappings:
            if mapping.external_object_id in seen:
                continue
            item = await session.scalar(select(CatalogItem).where(
                CatalogItem.id == mapping.catalog_item_id,
                CatalogItem.business_id == business_id,
            ))
            if item is not None and item.sync_state != "local_override":
                item.status = "archived"
                item.sync_state = "in_sync"
                mapping.sync_state = "archived"
                run.products_archived += 1
    run.status = "completed_with_issues" if run.failures or run.warnings else "completed"
    run.completed_at = now
    run.next_cursor = dict(next_cursor or {})
    run.provider_metadata = dict(provider_metadata or {})
    connection.status = "connected"
    connection.health = "healthy" if not (run.failures or run.warnings) else "degraded"
    connection.failure_code = None
    connection.last_sync_completed_at = now
    connection.last_success_at = now
    connection.sync_cursor = dict(next_cursor or {})
    source.last_synchronized_at = now
    try:
        await session.flush()
    except SQLAlchemyError:
        raise CommercePersistenceError("catalog_sync_failed") from None
    return run


async def _get_or_create_source(session: AsyncSession, *, connection: CommerceConnection) -> CatalogSource:
    account = connection.external_account_id or str(connection.id)
    source = await session.scalar(select(CatalogSource).where(
        CatalogSource.business_id == connection.business_id,
        CatalogSource.provider == connection.provider,
        CatalogSource.external_account_id == account,
    ))
    if source is None:
        source = CatalogSource(
            business_id=connection.business_id, commerce_connection_id=connection.id,
            provider=connection.provider, external_account_id=account,
            display_name=connection.display_name, authoritative=True, status="active",
        )
        session.add(source)
        await session.flush()
    return source


async def _upsert_product(
    session: AsyncSession,
    *,
    business_id: UUID,
    connection: CommerceConnection,
    source: CatalogSource,
    product: NormalizedProduct,
    synchronized_at: datetime,
) -> tuple[bool, bool, int]:
    account = connection.external_account_id or str(connection.id)
    mapping = await session.scalar(select(ExternalProductMapping).where(
        ExternalProductMapping.business_id == business_id,
        ExternalProductMapping.provider == connection.provider,
        ExternalProductMapping.external_account_id == account,
        ExternalProductMapping.external_object_id == product.external_object_id,
    ))
    snapshot = product.model_dump(mode="json")
    fingerprint = sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    created = mapping is None
    changed = (
        created
        or mapping.content_fingerprint != fingerprint
        or mapping.sync_state == "archived"
    )
    if mapping is None:
        sku = product.sku
        if sku and await session.scalar(select(CatalogItem.id).where(
            CatalogItem.business_id == business_id, CatalogItem.sku == sku,
        )):
            sku = None
        item = CatalogItem(business_id=business_id, item_type="product", name=product.name, sku=sku)
        session.add(item)
        await session.flush()
        mapping = ExternalProductMapping(
            business_id=business_id, catalog_source_id=source.id, catalog_item_id=item.id,
            provider=connection.provider, external_account_id=account,
            external_object_id=product.external_object_id, content_fingerprint=fingerprint,
            last_synchronized_at=synchronized_at,
        )
        session.add(mapping)
    else:
        item = await session.scalar(select(CatalogItem).where(
            CatalogItem.id == mapping.catalog_item_id, CatalogItem.business_id == business_id,
        ))
        if item is None:
            raise CommercePersistenceError("mapped_catalog_item_missing")
    if changed and item.sync_state != "local_override":
        values = product.model_dump(exclude={
            "external_object_id", "image_urls", "media", "collections", "variants",
            "provider_updated_at", "safe_metadata",
        })
        values["product_url"] = str(product.product_url) if product.product_url else None
        values.update({
            "item_type": "product", "source": connection.provider,
            "sync_state": "in_sync", "last_synchronized_at": synchronized_at,
            "provider_metadata": {
                **dict(product.safe_metadata),
                "collections": [
                    {
                        "external_object_id": collection.external_object_id,
                        "title": collection.title,
                        "handle": collection.handle,
                    }
                    for collection in product.collections[:100]
                ],
            },
        })
        if values.get("sku") and values["sku"] != item.sku:
            conflict = await session.scalar(select(CatalogItem.id).where(
                CatalogItem.business_id == business_id,
                CatalogItem.sku == values["sku"],
                CatalogItem.id != item.id,
            ))
            if conflict:
                values["sku"] = item.sku
        for key, value in values.items():
            setattr(item, key, value)
    mapping.content_fingerprint = fingerprint
    mapping.provider_updated_at = product.provider_updated_at
    mapping.last_synchronized_at = synchronized_at
    mapping.authoritative_snapshot = snapshot
    mapping.safe_metadata = dict(product.safe_metadata)
    if item.sync_state != "local_override":
        mapping.sync_state = "in_sync"
    variant_count = await _synchronize_variants(
        session, business_id=business_id, item=item, provider=connection.provider,
        external_account_id=account, product=product, synchronized_at=synchronized_at,
    )
    await _synchronize_media(
        session, business_id=business_id, item=item, product=product,
        provider=connection.provider, external_account_id=account,
    )
    return created, changed, variant_count


async def _synchronize_variants(
    session: AsyncSession, *, business_id: UUID, item: CatalogItem, provider: str,
    external_account_id: str, product: NormalizedProduct, synchronized_at: datetime,
) -> int:
    seen: set[str] = set()
    for variant in product.variants:
        seen.add(variant.external_object_id)
        value = await session.scalar(select(CatalogVariant).where(
            CatalogVariant.business_id == business_id,
            CatalogVariant.provider == provider,
            CatalogVariant.external_account_id == external_account_id,
            CatalogVariant.external_object_id == variant.external_object_id,
        ))
        if value is None:
            value = CatalogVariant(
                business_id=business_id, catalog_item_id=item.id, provider=provider,
                external_account_id=external_account_id,
                external_object_id=variant.external_object_id,
                title=variant.title, last_synchronized_at=synchronized_at,
            )
            session.add(value)
        for key, field_value in variant.model_dump(exclude={"external_object_id"}).items():
            setattr(value, key, field_value)
        value.last_synchronized_at = synchronized_at
    existing = list((await session.scalars(select(CatalogVariant).where(
        CatalogVariant.business_id == business_id,
        CatalogVariant.catalog_item_id == item.id,
        CatalogVariant.provider == provider,
        CatalogVariant.external_account_id == external_account_id,
    ))).all())
    for value in existing:
        if value.external_object_id not in seen:
            value.published = False
            value.available = False
    return len(product.variants)


async def _synchronize_media(
    session: AsyncSession, *, business_id: UUID, item: CatalogItem, product: NormalizedProduct,
    provider: str, external_account_id: str,
) -> None:
    normalized = list(product.media)
    known_urls = {str(media.source_url) for media in normalized}
    normalized.extend(
        {"source_url": url, "position": position, "media_type": "image", "external_object_id": None, "alt_text": None}
        for position, url in enumerate(product.image_urls)
        if str(url) not in known_urls
    )
    seen: set[tuple[str, str]] = set()
    for position, media in enumerate(normalized):
        source_url = str(media.source_url if hasattr(media, "source_url") else media["source_url"])
        external_id = media.external_object_id if hasattr(media, "external_object_id") else media["external_object_id"]
        identity = ("external", external_id) if external_id else ("url", source_url)
        if identity in seen:
            continue
        seen.add(identity)
        statement = select(CatalogMedia).where(
            CatalogMedia.business_id == business_id,
            CatalogMedia.catalog_item_id == item.id,
        )
        if external_id:
            statement = statement.where(
                CatalogMedia.provider == provider,
                CatalogMedia.external_account_id == external_account_id,
                CatalogMedia.external_object_id == external_id,
            )
        else:
            statement = statement.where(CatalogMedia.source_url == source_url)
        value = await session.scalar(statement)
        if value is None and external_id:
            value = await session.scalar(select(CatalogMedia).where(
                CatalogMedia.business_id == business_id,
                CatalogMedia.catalog_item_id == item.id,
                CatalogMedia.source_url == source_url,
            ))
        if value is None:
            value = CatalogMedia(
                business_id=business_id, catalog_item_id=item.id,
                media_type=media.media_type if hasattr(media, "media_type") else media["media_type"],
                provider=provider, external_account_id=external_account_id,
                external_object_id=external_id,
                source_url=source_url, position=media.position if hasattr(media, "position") else position,
                authoritative=True,
            )
            session.add(value)
        value.provider = provider
        value.external_account_id = external_account_id
        value.external_object_id = external_id
        value.source_url = source_url
        value.alt_text = media.alt_text if hasattr(media, "alt_text") else media["alt_text"]
        value.position = media.position if hasattr(media, "position") else position
        value.active = True
    existing = list((await session.scalars(select(CatalogMedia).where(
        CatalogMedia.business_id == business_id,
        CatalogMedia.catalog_item_id == item.id,
        CatalogMedia.provider == provider,
        CatalogMedia.external_account_id == external_account_id,
    ))).all())
    for value in existing:
        identity = (
            ("external", value.external_object_id)
            if value.external_object_id
            else ("url", value.source_url)
        )
        if identity not in seen:
            value.active = False


async def ingest_event(
    session: AsyncSession,
    *,
    business_id: UUID,
    actor_user_id: UUID | None,
    data: CommerceEventCreate,
) -> tuple[CommerceEvent, bool]:
    existing = await session.scalar(select(CommerceEvent).where(
        CommerceEvent.business_id == business_id,
        CommerceEvent.source == data.source,
        CommerceEvent.external_event_id == data.external_event_id,
    ))
    if existing is not None:
        return existing, True
    customer_id = data.customer_id
    if customer_id is not None:
        if not await session.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.business_id == business_id)):
            raise CommerceValidationError("customer_not_found")
    elif data.customer_email or data.customer_phone:
        try:
            identity = await resolve_customer_identity(
                session, business_id=business_id,
                display_name=data.customer_display_name, email=data.customer_email,
                phone=data.customer_phone, source=data.source, create_if_missing=True,
                actor_user_id=actor_user_id,
            )
        except (OperationsConflictError, OperationsValidationError):
            raise CommerceValidationError("customer_identity_invalid") from None
        except OperationsPersistenceError:
            raise CommercePersistenceError("customer_identity_failed") from None
        customer_id = identity.customer.id if identity.customer else None
    await _validate_event_reference(session, CatalogItem, business_id, data.catalog_item_id, "catalog_item_not_found")
    await _validate_event_reference(session, Order, business_id, data.order_id, "order_not_found")
    anonymous_hash = (
        sha256(f"{business_id}:{data.anonymous_session_id}".encode()).hexdigest()
        if data.anonymous_session_id else None
    )
    value = CommerceEvent(
        business_id=business_id, customer_id=customer_id,
        catalog_item_id=data.catalog_item_id, order_id=data.order_id,
        event_type=data.event_type, source=data.source,
        external_event_id=data.external_event_id,
        anonymous_session_hash=anonymous_hash, occurred_at=data.occurred_at,
        safe_metadata=data.safe_metadata,
    )
    try:
        # Keep the unique-key collision inside a savepoint. Without it, a
        # concurrent duplicate leaves the outer async transaction failed and
        # prevents the authoritative event from being read back.
        async with session.begin_nested():
            session.add(value)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(CommerceEvent).where(
            CommerceEvent.business_id == business_id,
            CommerceEvent.source == data.source,
            CommerceEvent.external_event_id == data.external_event_id,
        ))
        if existing is None:
            raise CommercePersistenceError("event_ingest_failed") from None
        return existing, True
    except SQLAlchemyError:
        raise CommercePersistenceError("event_ingest_failed") from None
    record_automation_event(
        session, business_id=business_id, event_type=data.event_type,
        entity_type="commerce_event", entity_id=value.id,
        payload={
            "event": {"type": data.event_type, "source": data.source},
            "customer": {"id": str(customer_id)} if customer_id else {},
            "catalog_item_id": str(data.catalog_item_id) if data.catalog_item_id else None,
            "order_id": str(data.order_id) if data.order_id else None,
        },
    )
    return value, False


async def _validate_event_reference(session, model, business_id: UUID, value_id: UUID | None, code: str) -> None:
    if value_id is None:
        return
    if not await session.scalar(select(model.id).where(model.id == value_id, model.business_id == business_id)):
        raise CommerceValidationError(code)


async def list_events(
    session: AsyncSession, *, business_id: UUID, event_type: str | None, limit: int,
) -> list[CommerceEvent]:
    statement = select(CommerceEvent).where(CommerceEvent.business_id == business_id)
    if event_type:
        statement = statement.where(CommerceEvent.event_type == event_type)
    try:
        return list((await session.scalars(statement.order_by(
            CommerceEvent.occurred_at.desc(), CommerceEvent.id.desc()
        ).limit(limit))).all())
    except SQLAlchemyError:
        raise CommercePersistenceError("event_read_failed") from None


async def create_segment(
    session: AsyncSession, *, business_id: UUID, actor_user_id: UUID,
    data: AudienceSegmentCreate, natural_language_definition: str | None = None,
) -> AudienceSegment:
    _validate_no_sensitive_targeting(
        data.name,
        data.description or "",
        natural_language_definition or "",
        json.dumps(data.rule.model_dump(mode="json"), sort_keys=True),
    )
    product_ids = {
        condition.product_id
        for condition in (*data.rule.all, *data.rule.exclude)
        if condition.product_id is not None
    }
    if product_ids:
        owned_ids = set((await session.scalars(select(CatalogItem.id).where(
            CatalogItem.business_id == business_id,
            CatalogItem.id.in_(product_ids),
        ))).all())
        if owned_ids != product_ids:
            raise CommerceValidationError("segment_product_invalid")
    value = AudienceSegment(
        business_id=business_id, created_by_user_id=actor_user_id,
        name=data.name, description=data.description,
        natural_language_definition=natural_language_definition,
        rule=data.rule.model_dump(mode="json"),
        source_classification=data.source_classification,
        status="draft",
    )
    session.add(value)
    try:
        await session.flush()
    except SQLAlchemyError:
        raise CommercePersistenceError("segment_create_failed") from None
    await refresh_segment(session, business_id=business_id, segment_id=value.id)
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="audience.segment_created", entity_type="audience_segment",
        entity_id=value.id, summary=f"Created recalculable audience segment {value.name}.",
    )
    return value


async def compile_segment(
    session: AsyncSession, *, business_id: UUID, actor_user_id: UUID,
    data: AudienceSegmentCompileRequest,
) -> AudienceSegment:
    definition = data.definition.strip()
    _validate_no_sensitive_targeting(definition)
    normalized = definition.casefold()
    conditions: list[AudienceRuleCondition] = []
    purchase = re.search(r"bought\s+(.+?)\s+(\w+|\d+)\s+times?\s+in\s+the\s+last\s+(\d+)\s+days?", normalized)
    if purchase:
        count_text = purchase.group(2)
        count = {"once": 1, "twice": 2, "three": 3}.get(count_text)
        if count is None:
            count = int(count_text) if count_text.isdigit() else 0
        if count < 1:
            raise CommerceValidationError("segment_definition_unsupported")
        product = await session.scalar(select(CatalogItem).where(
            CatalogItem.business_id == business_id,
            CatalogItem.item_type == "product",
            CatalogItem.name.ilike(f"%{purchase.group(1).strip()}%"),
        ).order_by(CatalogItem.updated_at.desc()).limit(1))
        if product is None:
            raise CommerceValidationError("segment_product_not_found")
        conditions.append(AudienceRuleCondition(
            field="order.count", operator="gte", value=count,
            product_id=product.id,
            lookback_days=int(purchase.group(3)),
        ))
    inactive = re.search(r"(?:haven't|have not|hasn't|has not)\s+ordered\s+in\s+the\s+last\s+(\d+)\s+days?", normalized)
    if inactive:
        conditions.append(AudienceRuleCondition(
            field="order.last_at", operator="not_within_days", value=int(inactive.group(1)),
        ))
    abandoned = re.search(r"abandon(?:ed|ers?)\s+(?:a\s+)?(?:cart|checkout)", normalized)
    if abandoned:
        conditions.append(AudienceRuleCondition(
            field="event.count", operator="gte", value=1,
            event_type="checkout_abandoned", lookback_days=30,
        ))
    if not conditions:
        raise CommerceValidationError("segment_definition_unsupported")
    name = data.name or definition[:160]
    return await create_segment(
        session, business_id=business_id, actor_user_id=actor_user_id,
        natural_language_definition=definition,
        data=AudienceSegmentCreate(
            name=name, description="Deterministic first-party segment compiled from plain language.",
            rule=AudienceRule(all=conditions), source_classification="first_party_observed",
        ),
    )


async def list_segments(session: AsyncSession, *, business_id: UUID) -> list[AudienceSegment]:
    try:
        return list((await session.scalars(select(AudienceSegment).where(
            AudienceSegment.business_id == business_id,
            AudienceSegment.status != "archived",
        ).order_by(AudienceSegment.updated_at.desc(), AudienceSegment.id.desc()))).all())
    except SQLAlchemyError:
        raise CommercePersistenceError("segment_read_failed") from None


async def audience_export_preflight(
    session: AsyncSession, *, business_id: UUID, segment_id: UUID, provider: str,
) -> dict[str, object]:
    """Return export readiness without exposing or presuming consented identities."""
    segment = await session.scalar(select(AudienceSegment).where(
        AudienceSegment.id == segment_id,
        AudienceSegment.business_id == business_id,
    ))
    if segment is None:
        raise CommerceNotFoundError("segment_not_found")
    if contains_sensitive_targeting(
        segment.name,
        segment.description or "",
        segment.natural_language_definition or "",
        json.dumps(segment.rule, sort_keys=True),
    ):
        raise CommerceValidationError("sensitive_targeting_prohibited")
    matched = int(await session.scalar(select(func.count(AudienceSegmentMember.id)).where(
        AudienceSegmentMember.business_id == business_id,
        AudienceSegmentMember.segment_id == segment_id,
    )) or 0)
    connector_type = "google_ads" if provider == "google" else "meta_ads"
    connection = await session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.business_id == business_id,
        IntegrationConnection.connector_type == connector_type,
        IntegrationConnection.status == "connected",
        IntegrationConnection.authentication_state == "authorized",
    ).order_by(IntegrationConnection.updated_at.desc()))
    issues: list[str] = []
    if connection is None:
        issues.append("provider_connection_required")
    if not matched:
        issues.append("audience_has_no_matched_members")
    # Imported identity is not permission for advertising use. Until a
    # durable purpose/channel consent ledger exists, no identity may be
    # normalized, hashed, queued, logged, or exported.
    issues.append("consent_registry_required")
    return {
        "segment_id": segment.id,
        "provider": provider,
        "ready": False,
        "matched_member_count": matched,
        "consented_member_count": 0,
        "consent_registry_available": False,
        "provider_acknowledgement_required": True,
        "identity_handling": "normalize_and_sha256_in_memory",
        "issues": issues,
    }


async def refresh_segment(
    session: AsyncSession, *, business_id: UUID, segment_id: UUID,
) -> AudienceSegment:
    segment = await session.scalar(select(AudienceSegment).where(
        AudienceSegment.id == segment_id, AudienceSegment.business_id == business_id,
    ).with_for_update())
    if segment is None:
        raise CommerceNotFoundError("segment_not_found")
    try:
        rule = AudienceRule.model_validate(segment.rule)
    except ValidationError:
        raise CommerceValidationError("segment_rule_invalid") from None
    customer_ids = set((await session.scalars(select(Customer.id).where(
        Customer.business_id == business_id, Customer.status != "archived",
    ))).all())
    now = datetime.now(UTC)
    for condition in rule.all:
        customer_ids &= await _matching_customers(
            session, business_id=business_id, condition=condition, now=now,
        )
    for condition in rule.exclude:
        customer_ids -= await _matching_customers(
            session, business_id=business_id, condition=condition, now=now,
        )
    await session.execute(delete(AudienceSegmentMember).where(
        AudienceSegmentMember.business_id == business_id,
        AudienceSegmentMember.segment_id == segment_id,
    ))
    session.add_all([
        AudienceSegmentMember(
            business_id=business_id, segment_id=segment_id, customer_id=customer_id,
            matched_at=now, evidence_summary="Matched deterministic first-party segment rules.",
        )
        for customer_id in sorted(customer_ids, key=str)
    ])
    segment.matched_customer_count = len(customer_ids)
    segment.last_refreshed_at = now
    try:
        await session.flush()
    except SQLAlchemyError:
        raise CommercePersistenceError("segment_refresh_failed") from None
    return segment


async def _matching_customers(
    session: AsyncSession, *, business_id: UUID, condition: AudienceRuleCondition, now: datetime,
) -> set[UUID]:
    if condition.field.startswith("customer."):
        column = {"customer.status": Customer.status, "customer.source": Customer.source, "customer.tags": Customer.tags}[condition.field]
        statement = select(Customer.id).where(Customer.business_id == business_id)
        if condition.operator == "equals":
            statement = statement.where(column == condition.value)
        elif condition.operator == "not_equals":
            statement = statement.where(column != condition.value)
        elif condition.operator == "contains":
            statement = statement.where(column.contains([condition.value]))
        else:
            raise CommerceValidationError("segment_rule_invalid")
        return set((await session.scalars(statement)).all())
    if condition.field in {"order.count", "order.total"}:
        aggregate = func.count(Order.id) if condition.field == "order.count" else func.coalesce(func.sum(Order.total), 0)
        statement = select(Order.customer_id, aggregate.label("value")).where(
            Order.business_id == business_id, Order.status != "canceled",
        )
        if condition.lookback_days:
            statement = statement.where(Order.created_at >= now - timedelta(days=condition.lookback_days))
        if condition.product_id:
            product_orders = select(OrderLineItem.order_id).where(
                OrderLineItem.business_id == business_id,
                OrderLineItem.catalog_item_id == condition.product_id,
            ).distinct()
            statement = statement.where(Order.id.in_(product_orders))
        rows = (await session.execute(statement.group_by(Order.customer_id))).all()
        matched = {
            customer_id for customer_id, value in rows
            if _compare(value, condition.operator, condition.value)
        }
        if _compare(0, condition.operator, condition.value):
            matched |= await _active_customer_ids(session, business_id) - {
                customer_id for customer_id, _ in rows
            }
        return matched
    if condition.field == "order.last_at":
        statement = select(Order.customer_id, func.max(Order.created_at)).where(
            Order.business_id == business_id, Order.status != "canceled",
        )
        if condition.product_id:
            product_orders = select(OrderLineItem.order_id).where(
                OrderLineItem.business_id == business_id,
                OrderLineItem.catalog_item_id == condition.product_id,
            ).distinct()
            statement = statement.where(Order.id.in_(product_orders))
        rows = (await session.execute(statement.group_by(Order.customer_id))).all()
        days = int(condition.value)
        cutoff = now - timedelta(days=days)
        matched = {customer_id for customer_id, last_at in rows if (
            last_at >= cutoff if condition.operator == "within_days" else last_at < cutoff
        )}
        if condition.operator == "not_within_days":
            matched |= await _active_customer_ids(session, business_id) - {
                customer_id for customer_id, _ in rows
            }
        return matched
    if condition.field in {"event.count", "event.last_at"}:
        aggregate = (
            func.count(CommerceEvent.id)
            if condition.field == "event.count"
            else func.max(CommerceEvent.occurred_at)
        )
        statement = select(CommerceEvent.customer_id, aggregate).where(
            CommerceEvent.business_id == business_id, CommerceEvent.customer_id.is_not(None),
        )
        if condition.event_type:
            statement = statement.where(CommerceEvent.event_type == condition.event_type)
        if condition.product_id:
            statement = statement.where(CommerceEvent.catalog_item_id == condition.product_id)
        if condition.field == "event.count" and condition.lookback_days:
            statement = statement.where(CommerceEvent.occurred_at >= now - timedelta(days=condition.lookback_days))
        rows = (await session.execute(statement.group_by(CommerceEvent.customer_id))).all()
        if condition.field == "event.count":
            matched = {
                customer_id for customer_id, value in rows
                if customer_id and _compare(value, condition.operator, condition.value)
            }
            if _compare(0, condition.operator, condition.value):
                matched |= await _active_customer_ids(session, business_id) - {
                    customer_id for customer_id, _ in rows if customer_id
                }
            return matched
        cutoff = now - timedelta(days=int(condition.value))
        matched = {
            customer_id for customer_id, last_at in rows
            if customer_id and (
                last_at >= cutoff if condition.operator == "within_days" else last_at < cutoff
            )
        }
        if condition.operator == "not_within_days":
            matched |= await _active_customer_ids(session, business_id) - {
                customer_id for customer_id, _ in rows if customer_id
            }
        return matched
    raise CommerceValidationError("segment_rule_invalid")


async def _active_customer_ids(session: AsyncSession, business_id: UUID) -> set[UUID]:
    return set((await session.scalars(select(Customer.id).where(
        Customer.business_id == business_id,
        Customer.status != "archived",
    ))).all())


def _compare(actual, operator: str, expected) -> bool:
    if operator == "gte":
        return actual >= expected
    if operator == "lte":
        return actual <= expected
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    raise CommerceValidationError("segment_rule_invalid")


def _validate_no_sensitive_targeting(*values: str) -> None:
    if contains_sensitive_targeting(*values):
        raise CommerceValidationError("sensitive_targeting_prohibited")


async def create_feed_destination(
    session: AsyncSession, *, business_id: UUID, actor_user_id: UUID,
    data: FeedDestinationCreate,
) -> CommerceFeedDestination:
    value = CommerceFeedDestination(
        business_id=business_id, provider=data.provider,
        external_account_id=data.external_account_id,
        integration_connection_id=data.integration_connection_id,
        external_resource_id=data.external_resource_id,
        managed=data.managed,
        content_language=data.content_language,
        feed_label=data.feed_label,
        display_name=data.display_name, status="configuration_required",
    )
    session.add(value)
    try:
        await session.flush()
    except IntegrityError:
        raise CommerceConflictError("feed_destination_already_exists") from None
    except SQLAlchemyError:
        raise CommercePersistenceError("feed_destination_create_failed") from None
    record_audit(
        session, business_id=business_id, actor_user_id=actor_user_id,
        event_type="commerce.feed_destination_created", entity_type="commerce_feed_destination",
        entity_id=value.id,
        summary=f"Added {data.display_name}; provider configuration is required before synchronization.",
    )
    return value


async def list_feed_destinations(session: AsyncSession, *, business_id: UUID) -> list[CommerceFeedDestination]:
    try:
        return list((await session.scalars(select(CommerceFeedDestination).where(
            CommerceFeedDestination.business_id == business_id,
        ).order_by(CommerceFeedDestination.updated_at.desc()))).all())
    except SQLAlchemyError:
        raise CommercePersistenceError("feed_destination_read_failed") from None


async def evaluate_feed_quality(
    session: AsyncSession, *, business_id: UUID, destination_id: UUID,
) -> CommerceFeedDestination:
    """Persist deterministic preflight results without claiming provider approval."""
    destination = await session.scalar(select(CommerceFeedDestination).where(
        CommerceFeedDestination.id == destination_id,
        CommerceFeedDestination.business_id == business_id,
    ).with_for_update())
    if destination is None:
        raise CommerceNotFoundError("feed_destination_not_found")
    products = list((await session.scalars(select(CatalogItem).where(
        CatalogItem.business_id == business_id,
        CatalogItem.item_type == "product",
        CatalogItem.status == "active",
        CatalogItem.published.is_(True),
    ).order_by(CatalogItem.id))).all())
    product_ids = {product.id for product in products}
    media_ids = set((await session.scalars(select(CatalogMedia.catalog_item_id).where(
        CatalogMedia.business_id == business_id,
        CatalogMedia.catalog_item_id.in_(product_ids) if product_ids else False,
        CatalogMedia.media_type == "image",
    ))).all()) if product_ids else set()
    existing = {
        value.catalog_item_id: value
        for value in (await session.scalars(select(CommerceFeedProductStatus).where(
            CommerceFeedProductStatus.business_id == business_id,
            CommerceFeedProductStatus.destination_id == destination_id,
        ))).all()
    }
    eligible = warnings_count = rejected = 0
    for product in products:
        missing: list[str] = []
        warnings: list[str] = []
        if not product.description:
            missing.append("description")
        if product.price is None:
            missing.append("price")
        if not (product.currency):
            missing.append("currency")
        if not product.product_url:
            missing.append("product_url")
        if product.id not in media_ids:
            missing.append("image")
        if product.availability == "unknown":
            missing.append("availability")
        if not product.gtin and not (product.brand and product.mpn):
            warnings.append("Add a legitimate GTIN, or brand and MPN when the product has them; do not fabricate identifiers.")
        if destination.provider == "google_merchant_center" and not product.google_product_category:
            warnings.append("Google product category is not set; provider classification may still apply.")
        recommendations: list[dict[str, object]] = []
        for attribute in missing:
            resolution = (
                "owner_input_required"
                if attribute in {"price", "currency"}
                else "store_source_update_required"
            )
            recommendations.append({
                "code": f"missing_{attribute}",
                "message": f"Supply the authoritative {attribute.replace('_', ' ')}; AI will not invent it.",
                "severity": "error",
                "resolution": resolution,
                "attribute": attribute,
            })
        if not product.gtin and not (product.brand and product.mpn):
            recommendations.append({
                "code": "missing_product_identifier",
                "message": "Add a legitimate GTIN, or authoritative brand and MPN, only when available.",
                "severity": "warning",
                "resolution": "owner_input_required",
                "attribute": "gtin",
            })
        if product.description and len(product.description.strip()) < 80:
            recommendations.append({
                "code": "description_improvement",
                "message": "AI may improve wording using existing product facts, subject to owner review.",
                "severity": "warning",
                "resolution": "auto_fix_safe",
                "attribute": "description",
            })
        if destination.provider == "google_merchant_center" and not product.google_product_category:
            recommendations.append({
                "code": "google_product_category_review",
                "message": "Review the provider taxonomy; do not infer a factual category without evidence.",
                "severity": "warning",
                "resolution": "owner_input_required",
                "attribute": "google_product_category",
            })
        status = "rejected" if missing else "warning" if warnings else "eligible"
        eligible += int(status in {"eligible", "warning"})
        warnings_count += int(status == "warning")
        rejected += int(status == "rejected")
        value = existing.pop(product.id, None)
        if value is None:
            value = CommerceFeedProductStatus(
                business_id=business_id,
                destination_id=destination_id,
                catalog_item_id=product.id,
            )
            session.add(value)
        value.external_product_id = product.sku or str(product.id)
        value.status = status
        value.missing_attributes = missing
        value.warnings = warnings
        value.provider_error_code = None
        value.provider_issues = recommendations[:50]
        # This is a local preflight, not a provider synchronization.
        value.last_synchronized_at = None
    for value in existing.values():
        value.status = "removed"
        value.missing_attributes = []
        value.warnings = []
        value.provider_issues = []
    destination.eligible_count = eligible
    destination.warning_count = warnings_count
    destination.rejected_count = rejected
    try:
        await session.flush()
    except SQLAlchemyError:
        raise CommercePersistenceError("feed_quality_evaluation_failed") from None
    return destination


async def list_feed_product_statuses(
    session: AsyncSession, *, business_id: UUID, destination_id: UUID,
) -> list[CommerceFeedProductStatus]:
    if not await session.scalar(select(CommerceFeedDestination.id).where(
        CommerceFeedDestination.id == destination_id,
        CommerceFeedDestination.business_id == business_id,
    )):
        raise CommerceNotFoundError("feed_destination_not_found")
    try:
        return list((await session.scalars(select(CommerceFeedProductStatus).where(
            CommerceFeedProductStatus.business_id == business_id,
            CommerceFeedProductStatus.destination_id == destination_id,
            CommerceFeedProductStatus.status != "removed",
        ).order_by(CommerceFeedProductStatus.status, CommerceFeedProductStatus.catalog_item_id))).all())
    except SQLAlchemyError:
        raise CommercePersistenceError("feed_product_status_read_failed") from None
