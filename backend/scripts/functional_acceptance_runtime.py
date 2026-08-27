"""Durable local functional acceptance for the golden QA commerce tenant.

This script intentionally creates clearly labeled records through the running
HTTP API.  It only accepts loopback URLs and never dispatches an external
connector action.  A configured development AI provider is exercised because
the product contract requires real, persisted AI output when a key exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx


QA_BUSINESS_ID = UUID("a11b0a50-2026-4824-9000-000000000001")
QA_BUSINESS_NAME = "[QA-ACCEPTANCE] Golden Commerce"
QA_LABEL = "[QA-ACCEPTANCE]"
QA_EMAIL = "golden-commerce-owner@example.com"
ADMIN_EMAIL = "qa-platform-admin@example.com"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("AIBOS_QA_BASE_URL", "http://127.0.0.1:5174"),
    )
    parser.add_argument(
        "--admin-api-url",
        default=os.getenv("AIBOS_QA_ADMIN_API_URL", "http://127.0.0.1:8003"),
    )
    return parser.parse_args()


def _loopback(value: str, label: str) -> str:
    normalized = value.rstrip("/")
    if urlparse(normalized).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"{label} must be a loopback URL")
    return normalized


def _expect(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        body = response.text.replace("\n", " ")[:1_000]
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {body}"
        )
    return response


def _register_or_login(
    client: httpx.Client,
    api_url: str,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
) -> tuple[str, bool]:
    registration = client.post(
        f"{api_url}/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    _expect(registration, 201, 409)
    login = _expect(
        client.post(
            f"{api_url}/auth/login",
            json={"email": email, "password": password},
        ),
        200,
    ).json()
    return login["access_token"], registration.status_code == 201


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    raise RuntimeError("Expected an API list response")


def _one(values: list[dict[str, Any]], predicate, label: str) -> dict[str, Any]:
    result = next((item for item in values if predicate(item)), None)
    if result is None:
        raise RuntimeError(f"Missing expected {label}")
    return result


async def _provider_contract_acceptance() -> dict[str, str]:
    """Exercise real adapter request/parsing code with local-only transports."""
    from app.integrations.commerce_adapters import (
        BigCommerceAdapter,
        CustomApiCommerceAdapter,
        MagentoCommerceAdapter,
        ShopifyCommerceAdapter,
        WooCommerceAdapter,
    )
    from app.integrations.commerce_contracts import CommerceSyncRequest
    from app.integrations.commerce_http import SafeCommerceHttpClient
    from app.integrations.credentials import CredentialMaterial

    async def exercise(adapter_type, credentials, request, payload) -> str:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=payload,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            adapter = adapter_type(SafeCommerceHttpClient(client, validate_dns=False))
            page = await adapter.synchronize(
                CredentialMaterial(credentials), request,
                idempotency_key="qa-provider-contract",
            )
            if page.store is None:
                raise RuntimeError("Provider contract did not normalize store identity")
            return page.store.name
        finally:
            await client.aclose()

    store_request = lambda account, url=None: CommerceSyncRequest(
        external_account_id=account, mode="initial", domain="store",
        store_url=url, page_size=1,
    )
    results = {
        "shopify": await exercise(
            ShopifyCommerceAdapter, {"access_token": "test-token"},
            store_request("qa-shopify", "https://shop.example.com"),
            {"data": {"shop": {"id": "qa-shopify", "name": "QA Shopify", "currencyCode": "USD"}}},
        ),
        "woocommerce": await exercise(
            WooCommerceAdapter, {"consumer_key": "test-key", "consumer_secret": "test-secret"},
            store_request("qa-woo", "https://shop.example.com"),
            {"environment": {"site_title": "QA WooCommerce", "currency": "USD", "timezone": "UTC"}},
        ),
        "bigcommerce": await exercise(
            BigCommerceAdapter, {"access_token": "test-token", "store_hash": "qa123"},
            store_request("qa123"),
            {"id": "qa123", "name": "QA BigCommerce", "currency": "USD"},
        ),
        "magento": await exercise(
            MagentoCommerceAdapter, {"access_token": "test-token"},
            store_request("qa-magento", "https://shop.example.com"),
            [{"id": "qa-magento", "website_name": "QA Adobe Commerce", "base_currency_code": "USD"}],
        ),
    }

    async def custom_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, json={
            "data": [{"id": "qa-custom-product", "name": "QA Custom product", "price": "9.99", "inventory_quantity": 2}],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(custom_handler))
    try:
        custom = CustomApiCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False))
        page = await custom.synchronize(
            CredentialMaterial({
                "api_token": "test-token",
                "configuration": json.dumps({"endpoints": {"products": "api/products"}}),
            }),
            CommerceSyncRequest(
                external_account_id="qa-custom", mode="initial", domain="products",
                store_url="https://api.example.com", page_size=10,
            ),
            idempotency_key="qa-custom-contract",
        )
        if len(page.products) != 1 or page.products[0].availability != "in_stock":
            raise RuntimeError("Custom API contract normalization failed")
        results["custom_api"] = page.products[0].name
    finally:
        await client.aclose()
    return results


async def _phase_two_provider_contract_acceptance() -> dict[str, Any]:
    """Exercise Phase 2 request mapping and provider-truth parsing locally."""
    from app.core.config import settings
    from app.integrations.ad_commerce_adapters import GoogleMerchantAdapter, MetaCatalogAdapter
    from app.integrations.ad_commerce_contracts import NormalizedProductDestinationInput
    from app.integrations.credentials import CredentialMaterial
    from app.integrations import action_adapters as _action_adapters  # noqa: F401
    from app.integrations.provider_action_adapters import ProviderConnectorActionAdapter
    from app.schemas.ai_action_payload import CampaignAudience, CampaignCreative, CreateGoogleAdsCampaignPayload
    from pydantic import SecretStr

    class ScriptedProvider:
        def __init__(self, responses: list[dict[str, object]]) -> None:
            self.responses = list(responses)
            self.calls: list[dict[str, object]] = []

        async def request_json(self, method: str, url: str, **kwargs):
            self.calls.append({"method": method, "url": url, **kwargs})
            if not self.responses:
                raise RuntimeError("unexpected_provider_request")
            return self.responses.pop(0)

    credentials = CredentialMaterial(values={"access_token": "qa-provider-token"})
    product = NormalizedProductDestinationInput(
        offer_id="QA-PHASE2-1", title="QA Phase 2 product",
        description="Authoritative QA product used only by local contract transports.",
        link="https://shop.example/qa-phase-two",
        image_link="https://cdn.example/qa-phase-two.jpg",
        availability="in_stock", price=Decimal("12.34"), currency="USD",
        content_language="en", feed_label="US", brand="QA",
    )
    google_http = ScriptedProvider([
        {"name": "accounts/111/productInputs/en~US~QA-PHASE2-1"},
        {"products": [{
            "name": "accounts/111/products/en~US~QA-PHASE2-1",
            "offerId": "QA-PHASE2-1",
            "dataSource": "accounts/111/dataSources/9",
            "productStatus": {"destinationStatuses": [{"approvedCountries": ["US"]}]},
        }]},
    ])
    google = GoogleMerchantAdapter(http=google_http)
    submitted = await google.upsert_product(
        credentials, account_reference="111",
        destination_reference="accounts/111/dataSources/9", product=product,
        idempotency_key="qa:phase2:google:product",
    )
    processed = await google.reconcile_products(
        credentials, account_reference="111",
        destination_reference="accounts/111/dataSources/9",
    )
    if submitted.state != "submitted" or not processed or processed[0].state != "eligible":
        raise RuntimeError("Google Merchant submitted/processed truth boundary failed")

    meta_http = ScriptedProvider([{"data": []}, {"id": "meta-product-1"}])
    meta = await MetaCatalogAdapter(http=meta_http).upsert_product(
        credentials, account_reference="10", destination_reference="20",
        product=product, idempotency_key="qa:phase2:meta:product",
    )
    if meta.external_product_reference != "meta-product-1" or meta.state != "submitted":
        raise RuntimeError("Meta catalog contract mapping failed")

    ads_http = ScriptedProvider([{"mutateOperationResponses": [
        {"campaignBudgetResult": {"resourceName": "customers/222/campaignBudgets/10"}},
        {"campaignResult": {"resourceName": "customers/222/campaigns/20"}},
        {"assetGroupResult": {"resourceName": "customers/222/assetGroups/30"}},
        {"assetGroupListingGroupFilterResult": {"resourceName": "customers/222/assetGroupListingGroupFilters/40"}},
    ]}])
    ads = ProviderConnectorActionAdapter(
        connector_type="google_ads",
        configuration=settings.model_copy(update={
            "google_ads_developer_token": SecretStr("qa-developer-token"),
            "google_ads_api_version": "v25",
        }),
        http=ads_http,
    )
    campaign = await ads.execute(
        credentials=credentials, action_type="create_google_ads_campaign",
        payload=CreateGoogleAdsCampaignPayload(
            campaign_name="QA retail PMax", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["qa-campaign:1"], destination_url="https://shop.example",
            ),
            network="performance_max", merchant_account_ref="111",
            product_offer_ids=["QA-PHASE2-1"],
        ),
        selected_resources=({
            "resource_type": "google_ads_customer", "external_reference": "222",
        },),
        delivery_target=None, idempotency_key="qa:phase2:google:campaign",
    )
    return {
        "google_merchant_write_state": submitted.state,
        "google_merchant_processed_state": processed[0].state,
        "meta_catalog_write_state": meta.state,
        "google_pmax_campaign_reference": campaign.external_reference_id,
        "external_network_used": False,
    }


async def _durable_commerce_sync_acceptance() -> dict[str, Any]:
    """Persist initial and incremental sync through the existing job/run domain."""
    from sqlalchemy import func, select

    from app.db.session import AsyncSessionFactory
    from app.integrations.commerce_contracts import CommerceSyncPage
    from app.integrations.commerce_registry import CommerceConnectorRegistry
    from app.integrations.credentials import CredentialMaterial, InMemoryIntegrationCredentialStore
    from app.models.background_job import BackgroundJob
    from app.models.catalog_item import CatalogItem
    from app.models.commerce import (
        CatalogMedia,
        CatalogVariant,
        CommerceConnection,
        CommerceEvent,
        CommerceSyncRun,
        CommerceWebhookReceipt,
        ExternalCustomerMapping,
        ExternalOrderMapping,
        ExternalProductMapping,
    )
    from app.models.customer import Customer
    from app.models.order import Order, OrderFulfillment, OrderRefund
    from app.schemas.commerce import (
        NormalizedCollection,
        NormalizedCustomer,
        NormalizedFulfillment,
        NormalizedMedia,
        NormalizedOrder,
        NormalizedOrderLine,
        NormalizedProduct,
        NormalizedRefund,
        NormalizedRefundLine,
        NormalizedStore,
        NormalizedVariant,
        NormalizedWebhookEvent,
    )
    from app.services.background_jobs import record_job_success
    from app.services.business_brain_assembly import iterate_business_brain_sources
    from app.services.commerce import ingest_provider_webhook, process_sync_run_page, request_sync

    created_at = datetime(2026, 8, 1, 12, tzinfo=UTC)

    lifecycle = {"product_state": "active"}
    acceptance_key = uuid4().hex[:12]
    webhook_event_id = f"qa-webhook-{acceptance_key}"
    webhook_body = json.dumps({"event_id": webhook_event_id}, separators=(",", ":")).encode()

    class AcceptanceConnector:
        provider = "custom_api"
        capabilities = frozenset({
            "store_read", "catalog_read", "variants", "inventory_read",
            "customers_read", "orders_read", "refunds_read",
            "fulfillments_read", "incremental_sync", "webhooks",
        })

        async def synchronize(self, _credentials, request, *, idempotency_key):
            if not idempotency_key.startswith("commerce-sync:"):
                raise RuntimeError("Sync page did not receive a durable idempotency key")
            if request.domain == "store":
                return CommerceSyncPage(domain="store", store=NormalizedStore(
                    external_account_id="qa-contract-store", name="QA Contract Store",
                    public_url="https://qa-commerce.example.com", currency="USD", timezone="UTC",
                ))
            if request.domain == "products":
                incremental = request.mode in {"incremental", "manual_retry"}
                page_two = request.cursor.get("page") == 2
                if page_two:
                    return CommerceSyncPage(
                        domain="products",
                        products=(NormalizedProduct(
                            external_object_id="qa-product-2", name="QA Archive-safe Product",
                            sku="QA-SYNC-002", price=Decimal("12.00"), currency="USD",
                            availability="unknown", inventory_quantity=None,
                        ),),
                        complete_snapshot=True,
                    )
                archived = lifecycle["product_state"] == "archived"
                product = NormalizedProduct(
                    external_object_id="qa-product-1", name="QA Durable Product",
                    sku="QA-SYNC-001",
                    price=Decimal("21.00") if incremental else Decimal("20.00"),
                    compare_at_price=Decimal("25.00"), currency="USD",
                    inventory_quantity=7 if incremental else 5,
                    availability="in_stock", brand="QA Brand", vendor="QA Vendor",
                    published=not archived, status="archived" if archived else "active",
                    collections=[NormalizedCollection(
                        external_object_id="qa-collection-1", title="QA Collection", handle="qa",
                    )],
                    media=[NormalizedMedia(
                        external_object_id="qa-media-1",
                        source_url="https://cdn.example.com/qa-product.jpg",
                        alt_text="QA durable product", position=0,
                    )],
                    variants=[NormalizedVariant(
                        external_object_id="qa-variant-1", title="Default",
                        sku="QA-SYNC-001", price=Decimal("21.00") if incremental else Decimal("20.00"),
                        compare_at_price=Decimal("25.00"), inventory_quantity=7 if incremental else 5,
                        available=True, published=True, barcode="0123456789012",
                    )],
                    provider_updated_at=datetime(
                        2026, 8,
                        4 if lifecycle["product_state"] == "restored" else 3 if archived else 2 if incremental else 1,
                        12, tzinfo=UTC,
                    ),
                )
                return CommerceSyncPage(
                    domain="products", products=(product,),
                    next_cursor={} if incremental else {"page": 2},
                    has_more=not incremental,
                    complete_snapshot=False,
                )
            if request.domain == "customers":
                return CommerceSyncPage(domain="customers", customers=(NormalizedCustomer(
                    external_object_id="qa-customer-1",
                    display_name="QA Updated Buyer" if request.mode == "incremental" else "QA Buyer",
                    email="qa-commerce-buyer@example.com", phone="+1 555 867 5309",
                    provider_updated_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
                ),))
            if request.domain == "orders":
                incremental_order = request.mode in {"incremental", "manual_retry"}
                customer = NormalizedCustomer(
                    external_object_id="qa-customer-1", display_name="QA Updated Buyer",
                    email="qa-commerce-buyer@example.com", phone="+1 555 867 5309",
                )
                return CommerceSyncPage(domain="orders", orders=(NormalizedOrder(
                    external_object_id="qa-order-1", order_number="QA-SYNC-1001",
                    external_customer_id="qa-customer-1", customer=customer,
                    currency="USD", subtotal=Decimal("20.00"), total=Decimal("20.00"),
                    payment_status="partially_refunded", fulfillment_status="fulfilled",
                    status="completed", created_at=created_at,
                    updated_at=datetime(2026, 8, 2, 12, tzinfo=UTC),
                    lines=[NormalizedOrderLine(
                        external_object_id="qa-line-1", external_product_id="qa-product-1",
                        external_variant_id="qa-variant-1", sku="QA-SYNC-001",
                        title="QA Durable Product", quantity=1, unit_price=Decimal("20.00"),
                    )],
                    refunds=[NormalizedRefund(
                        external_object_id="qa-refund-1", amount=Decimal("5.00" if incremental_order else "3.00"),
                        currency="USD", occurred_at=datetime(2026, 8, 2, 10, tzinfo=UTC),
                        lines=[NormalizedRefundLine(
                            external_order_line_id="qa-line-1", quantity=1,
                            amount=Decimal("5.00" if incremental_order else "3.00"),
                        )],
                    )],
                    fulfillments=[NormalizedFulfillment(
                        external_object_id="qa-fulfillment-1", status="fulfilled",
                        occurred_at=datetime(2026, 8, 2, 9, tzinfo=UTC),
                        tracking_company="QA Carrier",
                        tracking_number="QA-TRACK-1" if incremental_order else "QA-TRACK-0",
                        tracking_url=(
                            "https://tracking.example.com/QA-TRACK-1"
                            if incremental_order else "https://tracking.example.com/QA-TRACK-0"
                        ),
                        external_order_line_ids=["qa-line-1"],
                    )],
                ),))
            raise RuntimeError("Unexpected sync domain")

        def verify_and_parse_webhook(self, _credentials, request):
            if request.body != webhook_body:
                raise RuntimeError("Acceptance webhook body was modified")
            return NormalizedWebhookEvent(
                external_event_id=webhook_event_id, topic="products.updated",
                external_object_id="qa-product-1", reconciliation_domain="products",
            )

    credentials = InMemoryIntegrationCredentialStore()
    material = CredentialMaterial({"api_token": "qa-token", "configuration": "{}"})
    reference = await credentials.store(
        business_id=QA_BUSINESS_ID, connector_type="commerce_custom_api",
        purpose="connection", material=material,
    )
    connector = AcceptanceConnector()
    registry = CommerceConnectorRegistry({"custom_api": connector})

    async with AsyncSessionFactory() as session:
        connection = await session.scalar(select(CommerceConnection).where(
            CommerceConnection.business_id == QA_BUSINESS_ID,
            CommerceConnection.provider == "custom_api",
            CommerceConnection.external_account_id == "qa-contract-store",
        ))
        if connection is None:
            connection = CommerceConnection(
                business_id=QA_BUSINESS_ID, provider="custom_api",
                display_name=f"{QA_LABEL} Durable contract store",
                external_account_id="qa-contract-store",
                store_url="https://qa-commerce.example.com",
                status="connected", health="not_checked",
                capabilities=sorted(connector.capabilities), sync_cursor={}, safe_metadata={},
            )
            session.add(connection)
            await session.flush()
        connection.credential_reference = reference
        connection.status = "connected"
        connection.health = "not_checked"
        connection.failure_code = None
        connection.capabilities = sorted(connector.capabilities)
        initial, _ = await request_sync(
            session, business_id=QA_BUSINESS_ID, connection_id=connection.id,
            mode="initial", idempotency_key=f"qa-commerce-initial:{acceptance_key}",
        )
        await session.commit()
        connection_id = connection.id

    async def finish_run(run_id: UUID) -> CommerceSyncRun:
        for _ in range(12):
            async with AsyncSessionFactory() as session:
                run = await session.scalar(select(CommerceSyncRun).where(
                    CommerceSyncRun.id == run_id,
                    CommerceSyncRun.business_id == QA_BUSINESS_ID,
                ))
                if run is None:
                    raise RuntimeError("Acceptance sync run disappeared")
                if run.status in {"completed", "completed_with_issues"}:
                    return run
                job = await session.scalar(select(BackgroundJob).where(
                    BackgroundJob.business_id == QA_BUSINESS_ID,
                    BackgroundJob.commerce_sync_run_id == run_id,
                    BackgroundJob.status == "queued",
                ).order_by(BackgroundJob.created_at, BackgroundJob.id).with_for_update())
                if job is None:
                    raise RuntimeError("Durable commerce page job was not queued")
                now = datetime.now(UTC)
                job.status = "processing"
                job.attempt_count += 1
                job.claimed_at = now
                job.lease_expires_at = now + timedelta(seconds=60)
                job.worker_id = "qa-commerce-acceptance-worker"
                await process_sync_run_page(
                    session, business_id=QA_BUSINESS_ID, sync_run_id=run_id,
                    execution_id=job.id, credentials=credentials, connectors=registry,
                )
                await record_job_success(
                    session, job_id=job.id, worker_id="qa-commerce-acceptance-worker",
                )
                await session.commit()
        raise RuntimeError("Durable commerce sync exceeded its bounded page budget")

    initial = await finish_run(initial.id)
    async with AsyncSessionFactory() as session:
        initial_refund = await session.scalar(select(OrderRefund).where(
            OrderRefund.business_id == QA_BUSINESS_ID,
            OrderRefund.provider == "custom_api",
            OrderRefund.external_account_id == "qa-contract-store",
            OrderRefund.external_object_id == "qa-refund-1",
        ))
        initial_fulfillment = await session.scalar(select(OrderFulfillment).where(
            OrderFulfillment.business_id == QA_BUSINESS_ID,
            OrderFulfillment.provider == "custom_api",
            OrderFulfillment.external_account_id == "qa-contract-store",
            OrderFulfillment.external_object_id == "qa-fulfillment-1",
        ))
        if initial_refund is None or initial_refund.amount != Decimal("3.00"):
            raise RuntimeError("Initial refund fact did not persist")
        if initial_fulfillment is None or initial_fulfillment.tracking_number != "QA-TRACK-0":
            raise RuntimeError("Initial fulfillment fact did not persist")
    async with AsyncSessionFactory() as session:
        incremental, _ = await request_sync(
            session, business_id=QA_BUSINESS_ID, connection_id=connection_id,
            mode="incremental", idempotency_key=f"qa-commerce-incremental:{acceptance_key}",
        )
        await session.commit()
    incremental = await finish_run(incremental.id)

    lifecycle["product_state"] = "archived"
    async with AsyncSessionFactory() as session:
        archived, _ = await request_sync(
            session, business_id=QA_BUSINESS_ID, connection_id=connection_id,
            mode="incremental", idempotency_key=f"qa-commerce-archive:{acceptance_key}",
        )
        await session.commit()
    archived = await finish_run(archived.id)
    async with AsyncSessionFactory() as session:
        archived_published = await session.scalar(
            select(CatalogItem.published)
            .join(ExternalProductMapping, ExternalProductMapping.catalog_item_id == CatalogItem.id)
            .where(
                ExternalProductMapping.business_id == QA_BUSINESS_ID,
                ExternalProductMapping.external_account_id == "qa-contract-store",
                ExternalProductMapping.external_object_id == "qa-product-1",
            )
        )
        if archived_published is not False:
            raise RuntimeError("Upstream product archive did not unpublish the local mapped product")

    lifecycle["product_state"] = "restored"
    async with AsyncSessionFactory() as session:
        restored, _ = await request_sync(
            session, business_id=QA_BUSINESS_ID, connection_id=connection_id,
            mode="incremental", idempotency_key=f"qa-commerce-restore:{acceptance_key}",
        )
        await session.commit()
    restored = await finish_run(restored.id)

    async with AsyncSessionFactory() as session:
        first_receipt, first_duplicate = await ingest_provider_webhook(
            session, provider="custom_api", connection_id=connection_id,
            headers={"x-commerce-signature": "acceptance"}, body=webhook_body,
            credentials=credentials, connectors=registry,
        )
        second_receipt, second_duplicate = await ingest_provider_webhook(
            session, provider="custom_api", connection_id=connection_id,
            headers={"x-commerce-signature": "acceptance"}, body=webhook_body,
            credentials=credentials, connectors=registry,
        )
        if first_duplicate or not second_duplicate or first_receipt.id != second_receipt.id:
            raise RuntimeError("Webhook delivery deduplication did not return the original receipt")
        await session.commit()

    async with AsyncSessionFactory() as session:
        connection = await session.scalar(select(CommerceConnection).where(
            CommerceConnection.id == connection_id,
            CommerceConnection.business_id == QA_BUSINESS_ID,
        ))
        product_mapping = await session.scalar(select(ExternalProductMapping).where(
            ExternalProductMapping.business_id == QA_BUSINESS_ID,
            ExternalProductMapping.external_account_id == "qa-contract-store",
            ExternalProductMapping.external_object_id == "qa-product-1",
        ))
        product = await session.scalar(select(CatalogItem).where(
            CatalogItem.id == product_mapping.catalog_item_id,
            CatalogItem.business_id == QA_BUSINESS_ID,
        ))
        customer_mapping = await session.scalar(select(ExternalCustomerMapping).where(
            ExternalCustomerMapping.business_id == QA_BUSINESS_ID,
            ExternalCustomerMapping.external_account_id == "qa-contract-store",
            ExternalCustomerMapping.external_object_id == "qa-customer-1",
        ))
        customer = await session.scalar(select(Customer).where(
            Customer.id == customer_mapping.customer_id,
            Customer.business_id == QA_BUSINESS_ID,
        ))
        order_mapping = await session.scalar(select(ExternalOrderMapping).where(
            ExternalOrderMapping.business_id == QA_BUSINESS_ID,
            ExternalOrderMapping.external_account_id == "qa-contract-store",
            ExternalOrderMapping.external_object_id == "qa-order-1",
        ))
        order = await session.scalar(select(Order).where(
            Order.id == order_mapping.order_id, Order.business_id == QA_BUSINESS_ID,
        ))
        refund_record = await session.scalar(select(OrderRefund).where(
            OrderRefund.business_id == QA_BUSINESS_ID,
            OrderRefund.provider == "custom_api",
            OrderRefund.external_account_id == "qa-contract-store",
            OrderRefund.external_object_id == "qa-refund-1",
        ))
        fulfillment_record = await session.scalar(select(OrderFulfillment).where(
            OrderFulfillment.business_id == QA_BUSINESS_ID,
            OrderFulfillment.provider == "custom_api",
            OrderFulfillment.external_account_id == "qa-contract-store",
            OrderFulfillment.external_object_id == "qa-fulfillment-1",
        ))
        counts = {
            "products": int(await session.scalar(select(func.count()).select_from(ExternalProductMapping).where(
                ExternalProductMapping.business_id == QA_BUSINESS_ID,
                ExternalProductMapping.external_account_id == "qa-contract-store",
                ExternalProductMapping.external_object_id == "qa-product-1",
            )) or 0),
            "variants": int(await session.scalar(select(func.count()).select_from(CatalogVariant).where(
                CatalogVariant.business_id == QA_BUSINESS_ID,
                CatalogVariant.external_account_id == "qa-contract-store",
                CatalogVariant.external_object_id == "qa-variant-1",
            )) or 0),
            "media": int(await session.scalar(select(func.count()).select_from(CatalogMedia).where(
                CatalogMedia.business_id == QA_BUSINESS_ID,
                CatalogMedia.external_account_id == "qa-contract-store",
                CatalogMedia.external_object_id == "qa-media-1",
                CatalogMedia.active.is_(True),
            )) or 0),
            "customers": int(await session.scalar(select(func.count()).select_from(ExternalCustomerMapping).where(
                ExternalCustomerMapping.business_id == QA_BUSINESS_ID,
                ExternalCustomerMapping.external_account_id == "qa-contract-store",
                ExternalCustomerMapping.external_object_id == "qa-customer-1",
            )) or 0),
            "orders": int(await session.scalar(select(func.count()).select_from(ExternalOrderMapping).where(
                ExternalOrderMapping.business_id == QA_BUSINESS_ID,
                ExternalOrderMapping.external_account_id == "qa-contract-store",
                ExternalOrderMapping.external_object_id == "qa-order-1",
            )) or 0),
            "refunds": int(await session.scalar(select(func.count()).select_from(OrderRefund).where(
                OrderRefund.business_id == QA_BUSINESS_ID,
                OrderRefund.provider == "custom_api",
                OrderRefund.external_account_id == "qa-contract-store",
            )) or 0),
            "fulfillments": int(await session.scalar(select(func.count()).select_from(OrderFulfillment).where(
                OrderFulfillment.business_id == QA_BUSINESS_ID,
                OrderFulfillment.provider == "custom_api",
                OrderFulfillment.external_account_id == "qa-contract-store",
            )) or 0),
            "events": int(await session.scalar(select(func.count()).select_from(CommerceEvent).where(
                CommerceEvent.business_id == QA_BUSINESS_ID,
                CommerceEvent.source == "custom_api",
                CommerceEvent.external_event_id.like("order:qa-order-1:%"),
            )) or 0),
            "webhook_receipts": int(await session.scalar(select(func.count()).select_from(CommerceWebhookReceipt).where(
                CommerceWebhookReceipt.business_id == QA_BUSINESS_ID,
                CommerceWebhookReceipt.connection_id == connection_id,
                CommerceWebhookReceipt.external_event_id == webhook_event_id,
            )) or 0),
        }
        if any(counts[key] != 1 for key in ("products", "variants", "media", "customers", "orders", "refunds", "fulfillments")):
            raise RuntimeError(f"Commerce idempotency acceptance failed: {counts}")
        if counts["events"] != 4:
            raise RuntimeError(f"Commerce event idempotency acceptance failed: {counts}")
        if counts["webhook_receipts"] != 1:
            raise RuntimeError(f"Commerce webhook idempotency acceptance failed: {counts}")
        if product.price != Decimal("21.00") or product.inventory_quantity != 7 or product.availability != "in_stock":
            raise RuntimeError("Incremental catalog truth did not persist")
        if not product.published:
            raise RuntimeError("Restored upstream product did not republish locally")
        if customer.display_name != "QA Updated Buyer" or order.refunded_amount != Decimal("5.00"):
            raise RuntimeError("Incremental customer/order truth did not persist")
        if refund_record is None or refund_record.amount != Decimal("5.00"):
            raise RuntimeError("Incremental refund change did not update the existing record")
        if fulfillment_record is None or fulfillment_record.tracking_number != "QA-TRACK-1":
            raise RuntimeError("Incremental fulfillment change did not update the existing record")
        sources = [source async for source in iterate_business_brain_sources(
            session, QA_BUSINESS_ID, allowed_source_types={"catalog_item"},
        )]
        brain = next((source for source in sources if source.source_id == f"catalog:{product.id}"), None)
        if brain is None or "Availability: in_stock" not in brain.content or "Source: custom_api" not in brain.content:
            raise RuntimeError("Business Brain did not consume curated synchronized commerce facts")
        if connection.health != "healthy" or connection.last_success_at is None:
            raise RuntimeError("Connection health did not become server-authoritative")
        evidence = {
            "initial_run": str(initial.id),
            "initial_pages": initial.pages_processed,
            "incremental_run": str(incremental.id),
            "incremental_pages": incremental.pages_processed,
            "archive_run": str(archived.id),
            "archive_unpublished": archived_published is False,
            "restore_run": str(restored.id),
            "restore_published": product.published,
            "connection_health": connection.health,
            "counts_after_incremental": counts,
            "product_price_after_incremental": str(product.price),
            "inventory_after_incremental": product.inventory_quantity,
            "customer_identity": str(customer.id),
            "order_id": str(order.id),
            "refund_amount_after_incremental": str(refund_record.amount),
            "tracking_after_incremental": fulfillment_record.tracking_number,
            "business_brain_source": brain.source_id,
        }
        await session.commit()

    async with AsyncSessionFactory() as session:
        persisted = await session.scalar(select(CommerceSyncRun.id).where(
            CommerceSyncRun.id == restored.id,
            CommerceSyncRun.business_id == QA_BUSINESS_ID,
            CommerceSyncRun.status.in_(("completed", "completed_with_issues")),
        ))
        if persisted is None:
            raise RuntimeError("Commerce sync did not persist across session reload")
    evidence["reload_persistence"] = True
    return evidence


async def _durable_phase_two_acceptance() -> dict[str, Any]:
    """Persist and reload truthful local feed preflight state on PostgreSQL."""
    from sqlalchemy import func, select

    from app.db.session import AsyncSessionFactory
    from app.models.commerce import CommerceFeedDestination, CommerceFeedProductStatus
    from app.services.commerce import evaluate_feed_quality

    async with AsyncSessionFactory() as session:
        destination = await session.scalar(select(CommerceFeedDestination).where(
            CommerceFeedDestination.business_id == QA_BUSINESS_ID,
            CommerceFeedDestination.provider == "google_merchant_center",
            CommerceFeedDestination.external_account_id == "qa-phase2-merchant",
        ))
        if destination is None:
            destination = CommerceFeedDestination(
                business_id=QA_BUSINESS_ID,
                provider="google_merchant_center",
                external_account_id="qa-phase2-merchant",
                external_resource_id="accounts/qa-phase2-merchant/dataSources/local-preflight",
                managed=True,
                content_language="en",
                feed_label="US",
                display_name="[QA-ACCEPTANCE] Phase 2 Merchant preflight",
                status="configuration_required",
            )
            session.add(destination)
            await session.flush()
        await evaluate_feed_quality(
            session, business_id=QA_BUSINESS_ID, destination_id=destination.id,
        )
        destination_id = destination.id
        await session.commit()

    async with AsyncSessionFactory() as session:
        destination = await session.scalar(select(CommerceFeedDestination).where(
            CommerceFeedDestination.id == destination_id,
            CommerceFeedDestination.business_id == QA_BUSINESS_ID,
        ))
        if destination is None:
            raise RuntimeError("Phase 2 feed destination did not persist")
        product_count = int(await session.scalar(select(func.count()).select_from(
            CommerceFeedProductStatus,
        ).where(
            CommerceFeedProductStatus.business_id == QA_BUSINESS_ID,
            CommerceFeedProductStatus.destination_id == destination_id,
        )) or 0)
        synchronized_locally = int(await session.scalar(select(func.count()).select_from(
            CommerceFeedProductStatus,
        ).where(
            CommerceFeedProductStatus.business_id == QA_BUSINESS_ID,
            CommerceFeedProductStatus.destination_id == destination_id,
            CommerceFeedProductStatus.last_synchronized_at.is_not(None),
        )) or 0)
        if product_count < 1 or synchronized_locally:
            raise RuntimeError("Phase 2 local preflight/provider truth boundary failed")
        return {
            "destination_id": str(destination_id),
            "product_preflight_rows": product_count,
            "provider_synchronized_rows": synchronized_locally,
            "persisted_across_session_reload": True,
        }


async def _commerce_phase_one_acceptance() -> dict[str, Any]:
    return {
        "provider_contract_test_transports": await _provider_contract_acceptance(),
        "durable_internal_sync": await _durable_commerce_sync_acceptance(),
        "phase_two_provider_contract_test_transports": await _phase_two_provider_contract_acceptance(),
        "phase_two_durable_feed_truth": await _durable_phase_two_acceptance(),
        "live_provider_acceptance": "CONFIGURATION REQUIRED",
    }


def main() -> None:
    args = _arguments()
    frontend = _loopback(args.base_url, "base-url")
    admin_origin = _loopback(args.admin_api_url, "admin-api-url")
    api_url = f"{frontend}/api/v1"
    admin_api_url = f"{admin_origin}/api/v1"
    qa_password = os.getenv("AIBOS_QA_PASSWORD", "")
    admin_password = os.getenv("AIBOS_QA_ADMIN_PASSWORD", "")
    if not qa_password or not admin_password:
        raise RuntimeError("AIBOS_QA_PASSWORD and AIBOS_QA_ADMIN_PASSWORD are required")

    today = date.today()
    period_start = today - timedelta(days=30)
    schedule_at = datetime.now(UTC) + timedelta(days=3)
    evidence: dict[str, Any] = {
        "qa_business": {
            "id": str(QA_BUSINESS_ID),
            "name": QA_BUSINESS_NAME,
            "owner_email": QA_EMAIL,
        },
        "external_writes": "disabled",
    }

    with httpx.Client(timeout=150, follow_redirects=False) as client:
        token, registered = _register_or_login(
            client,
            api_url,
            email=QA_EMAIL,
            password=qa_password,
            first_name="Golden",
            last_name="QA Owner",
        )
        headers = _headers(token)
        businesses = _expect(client.get(f"{api_url}/businesses", headers=headers), 200).json()
        business = next(
            (item for item in businesses if item["id"] == str(QA_BUSINESS_ID)),
            None,
        )
        business_created = False
        if business is None:
            onboarding = _expect(
                client.post(
                    f"{api_url}/businesses",
                    headers=headers,
                    json={
                        "business_id": str(QA_BUSINESS_ID),
                        "name": QA_BUSINESS_NAME,
                        "business_type": "e-commerce",
                        "timezone": "Asia/Karachi",
                        "currency": "PKR",
                        "locale": "en",
                        "website_url": "https://qa-golden-commerce.invalid",
                        "location": "Karachi, Pakistan",
                        "description": (
                            f"{QA_LABEL} Local reusable commerce tenant for persisted "
                            "functional acceptance only."
                        ),
                        "brand_voice": "Clear, practical, warm, and evidence-based.",
                        "avoid_keywords": ["guaranteed", "miracle", "risk-free"],
                        "branding": {
                            "primary_color": "#176B3F",
                            "secondary_color": "#E6F5E9",
                            "accent_color": "#E96825",
                        },
                    },
                ),
                201,
            ).json()
            business = onboarding["business"]
            business_created = True
        evidence["onboarding"] = {
            "user_registered": registered,
            "business_created": business_created,
            "business_id": business["id"],
        }

        with httpx.Client(timeout=60) as admin_client:
            admin_token, _ = _register_or_login(
                admin_client,
                admin_api_url,
                email=ADMIN_EMAIL,
                password=admin_password,
                first_name="QA",
                last_name="Platform Admin",
            )
            billing = _expect(
                admin_client.put(
                    f"{admin_api_url}/platform/billing/businesses/"
                    f"{QA_BUSINESS_ID}/subscription",
                    headers=_headers(admin_token),
                    json={
                        "plan_code": "pro",
                        "billing_interval": "month",
                        "trial_days": 0,
                        "reason": "Local golden tenant functional production acceptance",
                    },
                ),
                200,
            ).json()
        if billing["plan_code"] != "pro" or billing["subscription_status"] != "active":
            raise RuntimeError("Server-authoritative Pro assignment did not persist")
        evidence["billing"] = {
            "plan": billing["plan_code"],
            "status": billing["subscription_status"],
            "provider_configured": billing["provider_configured"],
        }

        refreshed = _expect(client.post(f"{api_url}/auth/refresh"), 200).json()
        token = refreshed["access_token"]
        headers = _headers(token)
        base = f"{api_url}/businesses/{QA_BUSINESS_ID}"

        branding = _expect(
            client.put(
                f"{base}/branding",
                headers=headers,
                json={
                    "primary_color": "#176B3F",
                    "secondary_color": "#E6F5E9",
                    "accent_color": "#E96825",
                },
            ),
            200,
        ).json()
        evidence["branding"] = {
            "primary_color": branding["primary_color"],
            "persisted": True,
        }

        catalog = _expect(client.get(f"{base}/catalog", headers=headers), 200).json()
        if not catalog:
            csv_data = (
                "type,name,description,sku,price,status\n"
                "product,[QA-ACCEPTANCE] Focus Tea,Calming green tea blend for focused work,QA-TEA-001,1850.00,active\n"
                "product,[QA-ACCEPTANCE] Desk Reset Kit,Notebook timer and planning cards for a weekly reset,QA-KIT-002,4200.00,active\n"
                "product,[QA-ACCEPTANCE] Momentum Planner,Undated ninety-day commerce planning workbook,QA-PLAN-003,2750.00,active\n"
            ).encode()
            files = {"file": ("qa-golden-catalog.csv", csv_data, "text/csv")}
            preview = _expect(
                client.post(f"{base}/catalog/import/preview", headers=headers, files=files),
                200,
            ).json()
            if preview["valid_rows"] != 3 or preview["invalid_rows"] != 0:
                raise RuntimeError(f"Catalog import preview was not valid: {preview}")
            files = {"file": ("qa-golden-catalog.csv", csv_data, "text/csv")}
            _expect(client.post(f"{base}/catalog/import", headers=headers, files=files), 201)
            catalog = _expect(client.get(f"{base}/catalog", headers=headers), 200).json()
        if len(catalog) < 3 or not all(QA_LABEL in item["name"] for item in catalog[:3]):
            raise RuntimeError("The QA catalog was not persisted")
        evidence["products"] = [
            {"id": item["id"], "name": item["name"], "sku": item["sku"], "price": item["price"]}
            for item in catalog
            if QA_LABEL in item["name"]
        ]

        commerce_connections = _expect(
            client.get(f"{base}/commerce/connections", headers=headers), 200
        ).json()
        commerce_name = f"{QA_LABEL} Website source"
        commerce_connection = next(
            (item for item in commerce_connections if item["display_name"] == commerce_name),
            None,
        )
        if commerce_connection is None:
            commerce_connection = _expect(
                client.post(
                    f"{base}/commerce/connections",
                    headers=headers,
                    json={
                        "provider": "website",
                        "display_name": commerce_name,
                        "external_account_id": "qa-golden-website",
                        "store_url": "https://qa-golden-commerce.invalid",
                    },
                ),
                201,
            ).json()
        if commerce_connection["status"] != "configuration_required":
            raise RuntimeError("Unconfigured commerce source falsely reported connected")

        event_id = "qa-checkout-abandoned-001"
        event_response = _expect(
            client.post(
                f"{base}/commerce/events",
                headers=headers,
                json={
                    "event_type": "checkout_abandoned",
                    "source": "qa_acceptance",
                    "external_event_id": event_id,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "anonymous_session_id": "qa-acceptance-session-001",
                    "catalog_item_id": catalog[0]["id"],
                    "safe_metadata": {"flow": "functional_acceptance"},
                },
            ),
            201,
        ).json()
        duplicate_event = _expect(
            client.post(
                f"{base}/commerce/events",
                headers=headers,
                json={
                    "event_type": "checkout_abandoned",
                    "source": "qa_acceptance",
                    "external_event_id": event_id,
                    "occurred_at": event_response["occurred_at"],
                    "anonymous_session_id": "qa-acceptance-session-001",
                    "catalog_item_id": catalog[0]["id"],
                    "safe_metadata": {"flow": "functional_acceptance"},
                },
            ),
            201,
        ).json()
        if event_response["id"] != duplicate_event["id"] or not duplicate_event["duplicate"]:
            raise RuntimeError("Commerce event idempotency did not hold")

        segment_name = f"{QA_LABEL} Checkout abandoners"
        segments = _expect(
            client.get(f"{base}/commerce/audience-segments", headers=headers), 200
        ).json()
        segment = next((item for item in segments if item["name"] == segment_name), None)
        if segment is None:
            segment = _expect(
                client.post(
                    f"{base}/commerce/audience-segments/compile",
                    headers=headers,
                    json={"definition": "Customers who abandoned a checkout", "name": segment_name},
                ),
                201,
            ).json()
        if segment["source_classification"] != "first_party_observed":
            raise RuntimeError("Audience provenance was not preserved")

        destinations = _expect(
            client.get(f"{base}/commerce/feed-destinations", headers=headers), 200
        ).json()
        destination_name = f"{QA_LABEL} Google Merchant preflight"
        destination = next(
            (item for item in destinations if item["display_name"] == destination_name), None
        )
        if destination is None:
            destination = _expect(
                client.post(
                    f"{base}/commerce/feed-destinations",
                    headers=headers,
                    json={
                        "provider": "google_merchant_center",
                        "display_name": destination_name,
                        "external_account_id": "qa-merchant-account",
                    },
                ),
                201,
            ).json()
        destination = _expect(
            client.post(
                f"{base}/commerce/feed-destinations/{destination['id']}/evaluate",
                headers=headers,
            ),
            200,
        ).json()
        feed_products = _expect(
            client.get(
                f"{base}/commerce/feed-destinations/{destination['id']}/products",
                headers=headers,
            ),
            200,
        ).json()
        if len(feed_products) < 3 or destination["status"] != "configuration_required":
            raise RuntimeError("Feed preflight or provider truthfulness failed")
        evidence["commerce_foundation"] = {
            "connection_status": commerce_connection["status"],
            "event_idempotent": True,
            "audience_segment_id": segment["id"],
            "feed_preflight_products": len(feed_products),
            "feed_destination_status": destination["status"],
        }

        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        required_knowledge = (
            (
                "faq",
                f"{QA_LABEL} Delivery and returns",
                "Karachi orders are prepared within one business day. Local delivery is normally two to three business days. Unused items may be returned within fourteen days after review by support.",
            ),
            (
                "brand",
                f"{QA_LABEL} Customer promise",
                "Recommend only catalog products that fit the stated need. Never promise guaranteed productivity or business results. Quote prices in PKR from the current catalog.",
            ),
        )
        existing_titles = {item["title"] for item in knowledge}
        for category, title, content in required_knowledge:
            if title not in existing_titles:
                _expect(
                    client.post(
                        f"{base}/brain/knowledge",
                        headers=headers,
                        json={
                            "category": category,
                            "title": title,
                            "content": content,
                            "status": "active",
                        },
                    ),
                    201,
                )
        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        manifest_before = _expect(client.get(f"{base}/brain/manifest", headers=headers), 200).json()
        if manifest_before["source_count"] < 7:
            raise RuntimeError(f"Business Brain is unexpectedly sparse: {manifest_before}")

        customer_page = _expect(
            client.get(f"{base}/customers?page_size=100", headers=headers), 200
        ).json()
        customer = next(
            (item for item in _items(customer_page) if item.get("email") == "sana.qa@example.com"),
            None,
        )
        if customer is None:
            customer = _expect(
                client.post(
                    f"{base}/customers",
                    headers=headers,
                    json={
                        "display_name": f"{QA_LABEL} Sana Buyer",
                        "first_name": "Sana",
                        "last_name": "Buyer",
                        "email": "sana.qa@example.com",
                        "phone": "+923001112233",
                        "source": "qa_acceptance",
                        "tags": ["qa-acceptance", "repeat-buyer"],
                        "company": "QA Studio",
                        "notes": "Golden tenant customer; safe local acceptance record.",
                    },
                ),
                201,
            ).json()

        leads_page = _expect(
            client.get(f"{base}/crm/leads?page_size=100", headers=headers), 200
        ).json()
        lead = next(
            (item for item in _items(leads_page) if item.get("email") == "sana.qa@example.com"),
            None,
        )
        if lead is None:
            lead = _expect(
                client.post(
                    f"{base}/crm/leads",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "display_name": f"{QA_LABEL} Sana Focus Bundle",
                        "company": "QA Studio",
                        "email": customer["email"],
                        "phone": customer["phone"],
                        "stage": "new",
                        "source": "qa_acceptance",
                        "priority": "high",
                        "qualification_state": "unqualified",
                        "estimated_value": "6950.00",
                        "currency": "PKR",
                        "expected_close_date": (today + timedelta(days=7)).isoformat(),
                        "notes": "Customer asked about a Focus Tea and Desk Reset Kit bundle.",
                    },
                ),
                201,
            ).json()
        if lead["qualification_state"] != "qualified":
            lead = _expect(
                client.post(
                    f"{base}/crm/leads/{lead['id']}/qualification",
                    headers=headers,
                    json={"qualification_state": "qualified"},
                ),
                200,
            ).json()
        if lead["stage"] == "new":
            lead = _expect(
                client.post(
                    f"{base}/crm/leads/{lead['id']}/stage",
                    headers=headers,
                    json={"stage": "qualified"},
                ),
                200,
            ).json()
        if lead["customer_id"] != customer["id"]:
            raise RuntimeError("CRM lead lost its customer relationship")
        evidence["customer_crm"] = {
            "customer_id": customer["id"],
            "lead_id": lead["id"],
            "stage": lead["stage"],
            "qualification_state": lead["qualification_state"],
            "relationship_persisted": True,
        }

        orders_page = _expect(
            client.get(f"{base}/orders?page_size=100", headers=headers), 200
        ).json()
        order = next(
            (item for item in _items(orders_page) if item["customer_id"] == customer["id"]),
            None,
        )
        if order is None:
            first, second = evidence["products"][:2]
            order = _expect(
                client.post(
                    f"{base}/orders",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "source": "qa_acceptance",
                        "currency": "PKR",
                        "notes": f"{QA_LABEL} Persisted commerce acceptance order.",
                        "lines": [
                            {
                                "catalog_item_id": first["id"],
                                "description": first["name"],
                                "quantity": 1,
                                "unit_price": first["price"],
                            },
                            {
                                "catalog_item_id": second["id"],
                                "description": second["name"],
                                "quantity": 1,
                                "unit_price": second["price"],
                            },
                        ],
                    },
                ),
                201,
            ).json()
        if order["status"] == "draft":
            order = _expect(
                client.post(
                    f"{base}/orders/{order['id']}/status",
                    headers=headers,
                    json={"status": "confirmed"},
                ),
                200,
            ).json()
        if order["status"] == "confirmed":
            order = _expect(
                client.post(
                    f"{base}/orders/{order['id']}/status",
                    headers=headers,
                    json={"status": "processing"},
                ),
                200,
            ).json()
        evidence["product_order"] = {
            "order_id": order["id"],
            "order_number": order["order_number"],
            "status": order["status"],
            "total": order["total"],
            "customer_id": order["customer_id"],
        }

        workflows = _expect(
            client.get(f"{base}/automations/workflows?page_size=100", headers=headers), 200
        ).json()
        copilot_name = f"{QA_LABEL} Abandoned checkout Copilot"
        copilot_workflow = next(
            (item for item in _items(workflows) if item["name"] == copilot_name), None
        )
        if copilot_workflow is None:
            copilot = _expect(
                client.post(
                    f"{base}/automations/copilot/compile",
                    headers=headers,
                    json={
                        "name": copilot_name,
                        "timezone": "Asia/Karachi",
                        "prompt": "When checkout is abandoned wait 2 hours and send WhatsApp, then stop after purchase.",
                    },
                ),
                201,
            ).json()
            copilot_workflow = copilot["workflow"]
            if not copilot["executable_actions_withheld"] or "whatsapp_business" not in copilot["required_integrations"]:
                raise RuntimeError("Automation Copilot did not fail closed around external delivery")
        copilot_simulation = _expect(
            client.post(
                f"{base}/automations/workflows/{copilot_workflow['id']}/simulate",
                headers=headers,
                json={"payload": {"event": {"type": "checkout_abandoned", "entity_type": "commerce_event"}}},
            ),
            200,
        ).json()
        evidence["automation_copilot"] = {
            "workflow_id": copilot_workflow["id"],
            "dry_run_completed": copilot_simulation["completed"],
            "external_dispatches": 0,
        }
        workflow_name = f"{QA_LABEL} New lead evidence workflow"
        workflow = next(
            (item for item in _items(workflows) if item["name"] == workflow_name),
            None,
        )
        if workflow is None:
            workflow = _expect(
                client.post(
                    f"{base}/automations/workflows",
                    headers=headers,
                    json={
                        "name": workflow_name,
                        "description": "Create an evidence-linked opportunity and an internal notification for a newly created QA lead.",
                        "trigger_type": "lead_created",
                        "timezone": "Asia/Karachi",
                    },
                ),
                201,
            ).json()
            trigger = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "trigger",
                        "name": "Lead created",
                        "configuration": {"kind": "trigger", "trigger_type": "lead_created"},
                        "position_x": 0,
                        "position_y": 0,
                        "order_index": 0,
                    },
                ),
                201,
            ).json()
            opportunity_node = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "internal_operation",
                        "name": "Create evidence-linked opportunity",
                        "configuration": {
                            "kind": "internal_operation",
                            "operation": "create_opportunity",
                            "parameters": {
                                "title": f"{QA_LABEL} Follow up qualified bundle interest",
                                "description": "A real QA customer and linked CRM lead expressed interest in two persisted catalog products. Review the qualified lead and prepare a value-based follow-up; no message has been sent.",
                                "category": "sales_opportunity",
                                "source": "automation",
                                "priority": "high",
                                "estimated_value": "6950.00",
                                "currency": "PKR",
                                "customer_id": customer["id"],
                                "lead_id": lead["id"],
                            },
                        },
                        "position_x": 260,
                        "position_y": 0,
                        "order_index": 1,
                    },
                ),
                201,
            ).json()
            notification_node = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "internal_operation",
                        "name": "Notify the owner",
                        "configuration": {
                            "kind": "internal_operation",
                            "operation": "create_notification",
                            "parameters": {
                                "category": "automation",
                                "title": f"{QA_LABEL} New lead workflow completed",
                                "message": "A real lead-created trigger produced an internal sales opportunity and this notification.",
                                "priority": "high",
                                "related_entity_type": "crm_lead",
                                "related_entity_id": lead["id"],
                            },
                        },
                        "position_x": 520,
                        "position_y": 0,
                        "order_index": 2,
                    },
                ),
                201,
            ).json()
            end = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "end",
                        "name": "Complete",
                        "configuration": {"kind": "end", "outcome": "success"},
                        "position_x": 780,
                        "position_y": 0,
                        "order_index": 3,
                    },
                ),
                201,
            ).json()
            for source, target in (
                (trigger["node_key"], opportunity_node["node_key"]),
                (opportunity_node["node_key"], notification_node["node_key"]),
                (notification_node["node_key"], end["node_key"]),
            ):
                _expect(
                    client.post(
                        f"{base}/automations/workflows/{workflow['id']}/edges",
                        headers=headers,
                        json={"source_node_key": source, "target_node_key": target},
                    ),
                    201,
                )
            validation = _expect(
                client.get(
                    f"{base}/automations/workflows/{workflow['id']}/validation",
                    headers=headers,
                ),
                200,
            ).json()
            if not validation["valid"]:
                raise RuntimeError(f"Automation graph is invalid: {validation}")
            workflow = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/status",
                    headers=headers,
                    json={"status": "active"},
                ),
                200,
            ).json()

        automation_lead_email = "automation.qa@example.com"
        leads_page = _expect(
            client.get(f"{base}/crm/leads?page_size=100", headers=headers), 200
        ).json()
        automation_lead = next(
            (item for item in _items(leads_page) if item.get("email") == automation_lead_email),
            None,
        )
        if automation_lead is None:
            automation_lead = _expect(
                client.post(
                    f"{base}/crm/leads",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "display_name": f"{QA_LABEL} Automation Trigger Lead",
                        "email": automation_lead_email,
                        "stage": "new",
                        "source": "qa_automation",
                        "priority": "medium",
                        "qualification_state": "unqualified",
                        "estimated_value": "4200.00",
                        "currency": "PKR",
                        "notes": "Created after workflow activation to produce a real lead_created event.",
                    },
                ),
                201,
            ).json()
        events_page = _expect(
            client.get(f"{base}/automations/events?page_size=100", headers=headers), 200
        ).json()
        event = _one(
            _items(events_page),
            lambda item: item["event_type"] == "lead_created"
            and item["entity_id"] == automation_lead["id"],
            "lead_created automation event",
        )
        if event["status"] == "pending":
            processed = _expect(
                client.post(
                    f"{base}/automations/events/{event['id']}/process",
                    headers=headers,
                ),
                200,
            ).json()
            run_ids = processed["created_run_ids"]
        else:
            runs_page = _expect(
                client.get(
                    f"{base}/automations/runs?workflow_id={workflow['id']}&page_size=100",
                    headers=headers,
                ),
                200,
            ).json()
            run_ids = [item["id"] for item in _items(runs_page)]
        if not run_ids:
            raise RuntimeError("Processing the lead event created no workflow run")
        run_id = run_ids[0]
        run = _expect(
            client.post(f"{base}/automations/runs/{run_id}/advance", headers=headers),
            200,
        ).json()
        if run["status"] != "succeeded":
            raise RuntimeError(f"Internal automation did not succeed: {run}")
        node_runs = _expect(
            client.get(
                f"{base}/automations/runs/{run_id}/nodes?page_size=100",
                headers=headers,
            ),
            200,
        ).json()
        evidence["automation"] = {
            "workflow_id": workflow["id"],
            "event_id": event["id"],
            "run_id": run_id,
            "status": run["status"],
            "steps": [
                {"name": item["node_name"], "status": item["status"], "summary": item["result_summary"]}
                for item in _items(node_runs)
            ],
        }

        baseline_agent = _expect(
            client.post(
                f"{base}/agents/execute",
                headers=headers,
                json={
                    "role": "business_manager",
                    "task": "Summarize the current QA business using cited catalog and policy facts only. Analysis only; do not propose or execute actions.",
                },
            ),
            200,
        ).json()
        context_entry_title = f"{QA_LABEL} Current operating priority"
        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        context_entry = next(
            (item for item in knowledge if item["title"] == context_entry_title),
            None,
        )
        context_content = (
            "Current QA priority: follow up qualified bundle interest, complete the "
            "internal content review, and do not dispatch external provider actions. "
            f"Acceptance refresh: {datetime.now(UTC).isoformat()}."
        )
        if context_entry is None:
            context_entry = _expect(
                client.post(
                    f"{base}/brain/knowledge",
                    headers=headers,
                    json={
                        "category": "operations",
                        "title": context_entry_title,
                        "content": context_content,
                        "status": "active",
                    },
                ),
                201,
            ).json()
        else:
            context_entry = _expect(
                client.patch(
                    f"{base}/brain/knowledge/{context_entry['id']}",
                    headers=headers,
                    json={"content": context_content},
                ),
                200,
            ).json()
        manifest_after = _expect(client.get(f"{base}/brain/manifest", headers=headers), 200).json()
        if manifest_after["revision"] == manifest_before["revision"]:
            raise RuntimeError("Business Brain revision did not change after a real source update")

        agent_tasks = {
            "cmo": "Propose an internal weekly content focus for the persisted QA catalog. Analysis only; do not publish or propose actions.",
            "sales": "Explain a safe follow-up approach for the Focus Tea and Desk Reset Kit interest. Analysis only; do not send messages or propose actions.",
            "support": "Draft a factual support answer about delivery and returns using Business Brain. Analysis only; do not contact anyone or propose actions.",
            "operations": "Identify one internal operational priority from the current business context. Analysis only; do not dispatch or propose actions.",
            "analytics": "Summarize what can and cannot be concluded from the current QA business sources. Analysis only; do not propose actions.",
        }
        agent_results = {
            "business_manager": {
                "status": baseline_agent["output"]["status"],
                "summary": baseline_agent["output"]["summary"],
                "context_revision": baseline_agent["context_revision"],
                "source_count": baseline_agent["business_brain_source_count"],
            }
        }
        for role, task in agent_tasks.items():
            result = _expect(
                client.post(
                    f"{base}/agents/execute",
                    headers=headers,
                    json={"role": role, "task": task},
                ),
                200,
            ).json()
            agent_results[role] = {
                "status": result["output"]["status"],
                "summary": result["output"]["summary"],
                "context_revision": result["context_revision"],
                "source_count": result["business_brain_source_count"],
            }
        if agent_results["analytics"]["context_revision"] == baseline_agent["context_revision"]:
            raise RuntimeError("Later AI execution did not receive the revised Business Brain")
        config = _expect(
            client.patch(
                f"{base}/agents/analytics",
                headers=headers,
                json={
                    "custom_instructions": (
                        "For this QA tenant, separate observed counts from hypotheses and cite "
                        "the available Business Brain records."
                    )
                },
            ),
            200,
        ).json()
        persisted_config = _expect(
            client.get(f"{base}/agents/analytics", headers=headers), 200
        ).json()
        if persisted_config["custom_instructions"] != config["custom_instructions"]:
            raise RuntimeError("Agent configuration did not persist")
        evidence["business_brain"] = {
            "before": manifest_before,
            "after": manifest_after,
            "knowledge_records": [
                {"id": item["id"], "title": item["title"], "category": item["category"]}
                for item in _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
                if QA_LABEL in item["title"]
            ],
            "ai_context_revision_changed": True,
        }
        evidence["ai_agents"] = agent_results

        commands = [
            "What should I focus on today?",
            "Create a marketing plan for my products.",
            "Find sales opportunities.",
            "Summarize my customers and CRM pipeline.",
            "Suggest content for this week.",
        ]
        command_history = _expect(
            client.get(f"{base}/commands?page_size=100", headers=headers), 200
        ).json()
        command_results = []
        for command in commands:
            value = next(
                (
                    item
                    for item in _items(command_history)
                    if item["command"] == command
                    and item["status"] in {"completed", "needs_approval"}
                    and item["summary"]
                ),
                None,
            )
            last_response: httpx.Response | None = None
            for _ in range(3):
                if value is not None:
                    value = _expect(
                        client.get(
                            f"{base}/commands/{value['id']}", headers=headers
                        ),
                        200,
                    ).json()
                    break
                last_response = client.post(
                    f"{base}/commands",
                    headers=headers,
                    json={"command": command, "trigger_source": "command_center"},
                )
                if last_response.status_code == 201:
                    candidate = last_response.json()
                    if (
                        candidate["status"] in {"completed", "needs_approval"}
                        and candidate["summary"]
                    ):
                        value = candidate
                        break
                elif last_response.status_code != 503:
                    _expect(last_response, 201)
            if value is None:
                detail = last_response.text[:1_000] if last_response is not None else "no response"
                raise RuntimeError(
                    f"Command did not produce useful persisted output after bounded retries: "
                    f"{command!r}: {detail}"
                )
            command_results.append({
                "id": value["id"],
                "command": command,
                "status": value["status"],
                "primary_role": value["route"]["primary_role"],
                "summary": value["summary"],
                "executions": len(value["executions"]),
            })
        evidence["commands"] = command_results

        audience_name = f"{QA_LABEL} Karachi focus buyers"
        audience_page = _expect(
            client.get(f"{base}/marketing/audiences?page_size=100", headers=headers), 200
        ).json()
        audience = next(
            (item for item in _items(audience_page) if item["name"] == audience_name),
            None,
        )
        if audience is None:
            audience = _expect(
                client.post(
                    f"{base}/marketing/audiences",
                    headers=headers,
                    json={
                        "name": audience_name,
                        "countries": ["PK"],
                        "regions": ["Karachi"],
                        "min_age": 21,
                        "max_age": 55,
                        "languages": ["en"],
                        "customer_lifecycle": ["qualified_lead", "existing_customer"],
                        "crm_stages": ["qualified"],
                        "interests": ["focus", "planning", "work routines"],
                        "existing_customer_segment": "QA acceptance contacts",
                        "segment_description": "Internal hypothesis grounded in the persisted customer, CRM lead, and order records.",
                    },
                ),
                201,
            ).json()
        plan_title = f"{QA_LABEL} Thirty-day product plan"
        plan_page = _expect(
            client.get(f"{base}/marketing/plans?page_size=100", headers=headers), 200
        ).json()
        plan = next(
            (item for item in _items(plan_page) if item["title"] == plan_title), None
        )
        if plan is None:
            plan = _expect(
                client.post(
                    f"{base}/marketing/plans/generate",
                    headers=headers,
                    json={
                        "goal": "Create an internal thirty-day plan to grow qualified interest in the three persisted QA products without external ad execution.",
                        "title": plan_title,
                        "target_audience": "Existing QA customers and qualified planning-product leads in Karachi.",
                        "channels": ["instagram", "email", "website"],
                        "budget_guidance": "0.00",
                        "period_start": today.isoformat(),
                        "period_end": (today + timedelta(days=30)).isoformat(),
                    },
                ),
                201,
            ).json()
        campaign_name = f"{QA_LABEL} Focus bundle campaign"
        campaign_page = _expect(
            client.get(f"{base}/marketing/campaigns?page_size=100", headers=headers), 200
        ).json()
        campaign = next(
            (item for item in _items(campaign_page) if item["name"] == campaign_name),
            None,
        )
        if campaign is None:
            campaign = _expect(
                client.post(
                    f"{base}/marketing/campaigns/generate",
                    headers=headers,
                    json={
                        "goal": "Prepare an internal product-bundle campaign proposal using the persisted catalog, customer, CRM, and order aggregates.",
                        "name": campaign_name,
                        "audience_definition": "Karachi contacts with observed interest in focus and planning products; unobserved details remain hypotheses.",
                        "channels": ["instagram", "email"],
                        "planned_budget": "0.00",
                        "budget_mode": "lifetime",
                        "start_date": today.isoformat(),
                        "end_date": (today + timedelta(days=21)).isoformat(),
                        "catalog_item_ids": [catalog[0]["id"], catalog[1]["id"]],
                    },
                ),
                201,
            ).json()
        else:
            campaign = _expect(
                client.get(
                    f"{base}/marketing/campaigns/{campaign['id']}", headers=headers
                ),
                200,
            ).json()
        hypothesis = _expect(
            client.get(
                f"{base}/marketing/campaigns/{campaign['id']}/audience-intelligence",
                headers=headers,
            ),
            200,
        ).json()
        content_title = f"{QA_LABEL} Focus bundle review draft"
        edited_title = f"{QA_LABEL} Focus bundle — reviewed copy"
        content_page = _expect(
            client.get(f"{base}/marketing/content?page_size=100", headers=headers), 200
        ).json()
        edited = next(
            (item for item in _items(content_page) if item["title"] == edited_title),
            None,
        )
        if edited is None:
            content = next(
                (item for item in _items(content_page) if item["title"] == content_title),
                None,
            )
            last_content_response: httpx.Response | None = None
            for _ in range(3):
                if content is not None:
                    break
                last_content_response = client.post(
                    f"{base}/marketing/content/generate",
                    headers=headers,
                    json={
                        "prompt": "Write a review-ready Instagram post for the Focus Tea and Desk Reset Kit. Use current catalog facts and avoid guaranteed outcomes.",
                        "campaign_id": campaign["id"],
                        "channel": "instagram",
                        "content_type": "social_post",
                        "title": content_title,
                        "language": "en",
                    },
                )
                if last_content_response.status_code == 201:
                    content = last_content_response.json()
                elif last_content_response.status_code != 503:
                    _expect(last_content_response, 201)
            if content is None:
                raise RuntimeError(
                    "AI content did not produce a persisted draft after bounded retries: "
                    + (last_content_response.text[:1_000] if last_content_response else "no response")
                )
            edited = _expect(
                client.post(
                    f"{base}/marketing/content/{content['id']}/versions",
                    headers=headers,
                    json={
                        "title": edited_title,
                        "body": content["body"] + "\n\nQA review note: current catalog prices apply; no outcome is guaranteed.",
                        "cta": "Review the current catalog before ordering.",
                    },
                ),
                201,
            ).json()
        for status_value in ("review", "approved"):
            if edited["status"] == status_value or edited["status"] in {"approved", "scheduled"}:
                continue
            edited = _expect(
                client.post(
                    f"{base}/marketing/content/{edited['id']}/status",
                    headers=headers,
                    json={"status": status_value},
                ),
                200,
            ).json()
        schedules = _expect(
            client.get(f"{base}/marketing/calendar", headers=headers), 200
        ).json()
        schedule = next(
            (item for item in schedules if item["content_id"] == edited["id"]), None
        )
        if schedule is None:
            schedule = _expect(
                client.post(
                    f"{base}/marketing/calendar",
                    headers=headers,
                    json={
                        "content_id": edited["id"],
                        "scheduled_for": schedule_at.isoformat().replace("+00:00", "Z"),
                    },
                ),
                201,
            ).json()
        approved_page = _expect(
            client.get(f"{base}/approvals?status=approved&limit=100", headers=headers),
            200,
        ).json()
        approval = next(
            (
                item
                for item in _items(approved_page)
                if edited_title in (item.get("action") or {}).get("description", "")
            ),
            None,
        )
        if approval is None:
            proposal = _expect(
                client.post(
                    f"{base}/marketing/content/{edited['id']}/prepare-publish",
                    headers=headers,
                    json={"channel": "instagram"},
                ),
                201,
            ).json()
            if proposal["approval_status"] != "pending" or proposal["connector_state"] == "ready_after_approval":
                raise RuntimeError("Governed content proposal did not remain connector-safe")
            approval_before = _expect(
                client.get(f"{base}/approvals/{proposal['approval_id']}", headers=headers), 200
            ).json()
            approval = _expect(
                client.post(
                    f"{base}/approvals/{proposal['approval_id']}/approve",
                    headers=headers,
                    json={"decision_note": "QA content and safe payload reviewed; keep external dispatch unavailable."},
                ),
                200,
            ).json()
        else:
            approval_before = {"status": "pending"}
        if approval["status"] != "approved" or approval["action"]["status"] not in {"ready", "queued"}:
            raise RuntimeError("Approval did not ready or durably queue the AIAction")

        trend = _expect(
            client.post(
                f"{base}/marketing/trends",
                headers=headers,
                json={
                    "title": f"{QA_LABEL} Bundle interest in persisted CRM and order data",
                    "category": "customer_demand",
                    "description": "The golden tenant contains a qualified bundle lead and a processing two-product order. This is a local QA signal only, not a market-wide claim.",
                    "source": "manual",
                    "source_reference": f"crm-lead:{lead['id']};order:{order['id']}",
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "relevance_score": "0.900",
                    "confidence": "0.850",
                },
            ),
            201,
        ).json()
        trend = _expect(
            client.post(
                f"{base}/marketing/trends/{trend['id']}/status",
                headers=headers,
                json={"status": "reviewed"},
            ),
            200,
        ).json()
        trend_opportunity = _expect(
            client.post(
                f"{base}/marketing/trends/{trend['id']}/opportunity",
                headers=headers,
                json={"priority": "high"},
            ),
            201,
        ).json()
        if (
            trend_opportunity["source_entity_id"] != trend["id"]
            or not trend_opportunity["reason"]
            or not trend_opportunity["provenance"]
        ):
            raise RuntimeError("Trend opportunity lost its evidence linkage")
        evidence["marketing"] = {
            "saved_audience_id": audience["id"],
            "plan": {"id": plan["id"], "status": plan["status"], "title": plan["title"]},
            "campaign": {
                "id": campaign["id"],
                "status": campaign["status"],
                "name": campaign["name"],
                "source_evidence": campaign["source_evidence"],
            },
            "audience_hypothesis": {
                "id": hypothesis["id"],
                "classification": hypothesis["classification"],
                "evidence": hypothesis["evidence"],
            },
            "content": {"id": edited["id"], "status": edited["status"], "title": edited["title"]},
            "calendar": {"id": schedule["id"], "status": schedule["status"], "scheduled_for": schedule["scheduled_for"]},
            "approval": {
                "id": approval["id"],
                "before": approval_before["status"],
                "after": approval["status"],
                "action_status": approval["action"]["status"],
                "payload_summary": approval["action"]["payload_summary"],
            },
            "opportunity": {
                "id": trend_opportunity["id"],
                "source": trend_opportunity["source"],
                "source_entity_type": trend_opportunity["source_entity_type"],
                "source_entity_id": trend_opportunity["source_entity_id"],
                "reason": trend_opportunity["reason"],
                "recommendation": trend_opportunity["recommendation"],
                "provenance": trend_opportunity["provenance"],
            },
        }

        chatbot = _expect(
            client.put(
                f"{base}/chatbot",
                headers=headers,
                json={
                    "enabled": True,
                    "display_name": "Golden Commerce Assistant",
                    "welcome_message": "Ask about the QA catalog, delivery, returns, or a current order.",
                    "placeholder_text": "Ask about a product",
                    "tone": "friendly",
                    "theme": "light",
                    "position": "bottom_right",
                    "launcher_style": "bubble",
                    "allowed_capabilities": [
                        "answer_business_questions",
                        "search_products_services",
                        "recommend_products_services",
                        "capture_lead",
                        "lookup_order_status",
                        "request_human_handoff",
                    ],
                    "allowed_domains": [],
                    "privacy_policy_url": None,
                    "consent_text": "I agree that this local QA business may follow up.",
                    "require_lead_consent": True,
                    "default_locale": "en",
                    "border_radius": 18,
                },
            ),
            200,
        ).json()
        hosted = _expect(
            client.post(f"{base}/chatbot/deployments/hosted", headers=headers), 201
        ).json()
        if ":5174/hosted.html" not in (hosted["hosted_url"] or ""):
            raise RuntimeError(f"Hosted assistant URL points at the wrong local app: {hosted}")
        public_config = _expect(
            client.get(
                f"{api_url}/public/hosted-widgets/{chatbot['widget_public_id']}/config"
            ),
            200,
        ).json()
        public_session = _expect(
            client.post(
                f"{api_url}/public/hosted-widgets/{chatbot['widget_public_id']}/sessions"
            ),
            201,
        ).json()
        public_headers = {"Authorization": f"Bearer {public_session['session_token']}"}
        public_answer = _expect(
            client.post(
                f"{api_url}/public/widgets/{chatbot['widget_public_id']}/sessions/messages",
                headers=public_headers,
                json={"message": "What is the price of the Focus Tea and what is your return policy?"},
            ),
            200,
        ).json()
        if not any(item["name"].endswith("Focus Tea") for item in public_answer["products"]):
            raise RuntimeError("Hosted chatbot did not return the real catalog product")
        conversations = _expect(
            client.get(f"{base}/conversations?page_size=100", headers=headers), 200
        ).json()
        conversation = _one(
            _items(conversations),
            lambda item: item["channel"] == "website" and "Focus Tea" in (item["latest_message"] or ""),
            "hosted chatbot conversation",
        )
        conversation_detail = _expect(
            client.get(f"{base}/conversations/{conversation['id']}", headers=headers), 200
        ).json()
        if len(conversation_detail["messages"]) < 2:
            raise RuntimeError("Hosted conversation message history did not persist")
        order_lookup = _expect(
            client.post(
                f"{api_url}/public/widgets/{chatbot['widget_public_id']}/sessions/order-status",
                headers=public_headers,
                json={
                    "order_reference": order["order_number"],
                    "email": customer["email"],
                    "phone": None,
                },
            ),
            200,
        ).json()
        evidence["chatbot"] = {
            "config_status": chatbot["lifecycle_status"],
            "hosted_url": hosted["hosted_url"],
            "public_business_name": public_config["business_name"],
            "answer": public_answer["message"],
            "catalog_results": public_answer["products"],
            "conversation_id": conversation["id"],
            "message_count": len(conversation_detail["messages"]),
            "order_lookup": order_lookup,
        }

        report = _expect(
            client.post(
                f"{base}/reports/generate",
                headers=headers,
                json={
                    "report_type": "daily_operations",
                    "period_start": period_start.isoformat(),
                    "period_end": today.isoformat(),
                },
            ),
            201,
        ).json()
        analytics = _expect(
            client.get(
                f"{base}/analytics/core?period_start={period_start}&period_end={today}",
                headers=headers,
            ),
            200,
        ).json()
        marketing_analytics = _expect(
            client.get(
                f"{base}/marketing/analytics?period_start={period_start}&period_end={today}",
                headers=headers,
            ),
            200,
        ).json()
        if analytics["customers"] < 1 or analytics["orders"] < 1 or analytics["ai_executions"] < 6:
            raise RuntimeError(f"Core analytics did not reflect QA activity: {analytics}")
        report_metrics = report["metrics"]
        if report_metrics.get("customers", 0) < 1 or report_metrics.get("orders", 0) < 1:
            raise RuntimeError(f"Daily report did not reflect QA records: {report}")
        evidence["dashboard_analytics_report"] = {
            "core": analytics,
            "marketing": marketing_analytics,
            "daily_report": {
                "id": report["id"],
                "summary": report["summary"],
                "metrics": report_metrics,
            },
        }

        notifications = _expect(
            client.get(f"{base}/notifications?page_size=100", headers=headers), 200
        ).json()
        notification_items = _items(notifications)
        if not notification_items:
            raise RuntimeError("No persisted notifications were produced")
        chosen_notification = notification_items[0]
        _expect(
            client.post(
                f"{base}/notifications/{chosen_notification['id']}/read",
                headers=headers,
            ),
            200,
        )
        notifications_after = _expect(
            client.get(f"{base}/notifications?page_size=100", headers=headers), 200
        ).json()
        persisted_read = _one(
            _items(notifications_after),
            lambda item: item["id"] == chosen_notification["id"],
            "read notification",
        )
        if not persisted_read["read"]:
            raise RuntimeError("Notification read state did not persist")
        audits = _expect(
            client.get(f"{base}/audit?page_size=100", headers=headers), 200
        ).json()
        audit_items = _items(audits)
        required_audit_prefixes = (
            "customer.",
            "crm_lead.",
            "approval.",
            "automation.",
            "chatbot.",
            "marketing.",
        )
        missing_audits = [
            prefix
            for prefix in required_audit_prefixes
            if not any(item["event_type"].startswith(prefix) for item in audit_items)
        ]
        if missing_audits:
            raise RuntimeError(f"Missing required audit groups: {missing_audits}")
        evidence["notifications"] = {
            "count": len(notification_items),
            "categories": sorted({item["category"] for item in notification_items}),
            "read_state_persisted": True,
        }
        evidence["audit"] = {
            "count": len(audit_items),
            "event_types": sorted({item["event_type"] for item in audit_items}),
        }

        integrations = _expect(
            client.get(f"{base}/integrations/connections", headers=headers), 200
        ).json()
        processing = _expect(
            client.get(f"{base}/processing/health", headers=headers), 200
        ).json()
        evidence["external_configuration"] = {
            "integration_connections": len(integrations),
            "billing_provider_configured": billing["provider_configured"],
            "external_connector_writes": "disabled",
            "processing": processing,
        }

    evidence["commerce_phase_one_acceptance"] = asyncio.run(
        _commerce_phase_one_acceptance()
    )

    with httpx.Client(timeout=60) as relogin_client:
        relogin = _expect(
            relogin_client.post(
                f"{api_url}/auth/login",
                json={"email": QA_EMAIL, "password": qa_password},
            ),
            200,
        ).json()
        reload_headers = _headers(relogin["access_token"])
        persisted_businesses = _expect(
            relogin_client.get(f"{api_url}/businesses", headers=reload_headers), 200
        ).json()
        if not any(item["id"] == str(QA_BUSINESS_ID) for item in persisted_businesses):
            raise RuntimeError("QA business did not persist across a new login")
        persisted_catalog = _expect(
            relogin_client.get(
                f"{api_url}/businesses/{QA_BUSINESS_ID}/catalog",
                headers=reload_headers,
            ),
            200,
        ).json()
        persisted_order = _expect(
            relogin_client.get(
                f"{api_url}/businesses/{QA_BUSINESS_ID}/orders/{evidence['product_order']['order_id']}",
                headers=reload_headers,
            ),
            200,
        ).json()
        evidence["relogin_persistence"] = {
            "business": True,
            "catalog_count": len(persisted_catalog),
            "order_status": persisted_order["status"],
        }

    print(json.dumps(evidence, indent=2, sort_keys=True))
    print("RESULT: golden QA functional acceptance data and workflows persisted")


if __name__ == "__main__":
    main()
