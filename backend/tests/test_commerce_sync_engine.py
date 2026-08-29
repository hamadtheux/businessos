from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import hmac
import io
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import ValidationError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.commerce import CommerceConfigurationRequiredError, CommerceProviderError, CommerceValidationError  # noqa: E402
from app.integrations.commerce_adapters import (  # noqa: E402
    BigCommerceAdapter,
    CustomApiCommerceAdapter,
    MagentoCommerceAdapter,
    ShopifyCommerceAdapter,
    WooCommerceAdapter,
)
from app.integrations.commerce_contracts import CommerceSyncRequest, CommerceWebhookRequest  # noqa: E402
from app.integrations.commerce_http import MAX_JSON_NODES, MAX_PROVIDER_RESPONSE_BYTES, SafeCommerceHttpClient, resolve_public_host, validate_public_https_url  # noqa: E402
from app.integrations.commerce_registry import commerce_connectors  # noqa: E402
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.schemas.commerce import CommerceImportMapping, NormalizedOrder, NormalizedProduct  # noqa: E402
from app.services.commerce_import import preview_import  # noqa: E402


class ProviderNormalizerTests(unittest.TestCase):
    def test_shopify_normalizes_variants_media_and_unknown_inventory_truthfully(self) -> None:
        product = ShopifyCommerceAdapter.normalize_product({
            "id": "gid://shopify/Product/1", "title": "Trail Shoe", "status": "ACTIVE",
            "descriptionHtml": "<p>Waterproof</p>", "vendor": "North", "tags": ["shoe"],
            "variants": {"nodes": [{
                "id": "gid://shopify/ProductVariant/2", "title": "Blue / 42", "sku": "ts-42",
                "price": "99.50", "compareAtPrice": "120.00", "availableForSale": True,
                "inventoryQuantity": None, "barcode": "123", "selectedOptions": [{"name": "Size", "value": "42"}],
            }]},
            "media": {"nodes": [{"id": "m1", "mediaContentType": "IMAGE", "alt": "Blue shoe", "preview": {"image": {"url": "https://cdn.example.com/shoe.jpg"}}}]},
        })
        self.assertEqual(product.variants[0].sku, "ts-42")
        self.assertIsNone(product.inventory_quantity)
        self.assertEqual(product.availability, "in_stock")
        self.assertEqual(product.media[0].alt_text, "Blue shoe")

    def test_woocommerce_normalizes_variations_and_order_financials(self) -> None:
        product = WooCommerceAdapter.normalize_product({
            "id": 8, "name": "Tee", "status": "publish", "stock_status": "outofstock",
            "images": [], "categories": [], "tags": [],
        }, variations=[{
            "id": 9, "sku": "tee-m", "price": "20", "regular_price": "25",
            "stock_quantity": 0, "stock_status": "outofstock", "attributes": [{"name": "Size", "option": "M"}],
        }])
        self.assertEqual(product.availability, "out_of_stock")
        self.assertEqual(product.variants[0].option_values, {"Size": "M"})
        order = WooCommerceAdapter.normalize_order({
            "id": 20, "number": "20", "customer_id": 7, "currency": "USD", "status": "completed",
            "date_created_gmt": "2026-08-01T12:00:00Z", "date_paid": "2026-08-01T12:01:00Z",
            "billing": {"first_name": "A", "last_name": "Buyer", "email": "a@example.com"},
            "line_items": [{"id": 1, "name": "Tee", "quantity": 2, "subtotal": "40", "total": "35", "total_tax": "2"}],
            "discount_total": "5", "total_tax": "2", "shipping_total": "3", "total": "40",
        })
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.lines[0].discount_amount, 5)

    def test_bigcommerce_and_magento_normalizers_do_not_leak_provider_shapes(self) -> None:
        big = BigCommerceAdapter.normalize_product({
            "id": 1, "name": "Bag", "price": 30, "inventory_level": None,
            "is_visible": True, "variants": [], "images": [],
        })
        magento = MagentoCommerceAdapter.normalize_product({
            "id": 2, "sku": "BAG", "name": "Bag", "price": 30, "status": 1,
            "custom_attributes": [{"attribute_code": "description", "value": "A bag"}],
            "media_gallery_entries": [], "extension_attributes": {},
        }, "https://shop.example.com")
        self.assertIsInstance(big, NormalizedProduct)
        self.assertIsInstance(magento, NormalizedProduct)
        self.assertEqual(big.availability, "unknown")
        self.assertEqual(magento.description, "A bag")

    def test_provider_order_facts_use_authoritative_payment_refund_and_tracking_fields(self) -> None:
        big = BigCommerceAdapter.normalize_order({
            "id": 1, "currency_code": "USD", "subtotal_ex_tax": "20", "total_inc_tax": "20",
            "payment_status": "paid", "status": "Awaiting Shipment",
            "date_created": "2026-08-01T12:00:00Z",
        }, lines=[{"id": 7, "name": "Tee", "quantity": 1, "base_price": "20"}], refunds=[{
            "id": 8, "amount": "5", "created": "2026-08-02T12:00:00Z",
            "items": [{"item_id": 7, "quantity": 1, "amount": "5"}],
        }])
        self.assertEqual(big.payment_status, "partially_refunded")
        self.assertEqual(big.refunds[0].lines[0].external_order_line_id, "7")

        magento = MagentoCommerceAdapter.normalize_order({
            "entity_id": 2, "increment_id": "2", "order_currency_code": "USD",
            "subtotal": "20", "grand_total": "20", "total_paid": "0", "status": "processing",
            "created_at": "2026-08-01T12:00:00Z", "items": [],
        }, refund_records=[], shipment_records=[])
        self.assertEqual(magento.payment_status, "pending")

        shopify = ShopifyCommerceAdapter.normalize_order({
            "id": "o1", "name": "#1", "currencyCode": "USD",
            "subtotalPriceSet": {"shopMoney": {"amount": "20"}},
            "totalPriceSet": {"shopMoney": {"amount": "20"}},
            "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED",
            "createdAt": "2026-08-01T12:00:00Z", "lineItems": {"nodes": []},
            "fulfillments": [{
                "id": "f1", "status": "SUCCESS", "createdAt": "2026-08-02T12:00:00Z",
                "trackingInfo": [
                    {"company": "A", "number": "TRACK-A", "url": "https://tracking.example/a"},
                    {"company": "B", "number": "TRACK-B", "url": "https://tracking.example/b"},
                ],
                "fulfillmentLineItems": {"nodes": []},
            }],
        })
        self.assertEqual([item.tracking_number for item in shopify.fulfillments], ["TRACK-A", "TRACK-B"])
        self.assertEqual(len({item.external_object_id for item in shopify.fulfillments}), 2)

    def test_normalized_order_rejects_invented_statuses(self) -> None:
        with self.assertRaises(ValidationError):
            NormalizedOrder(
                external_object_id="1", order_number="1", currency="USD",
                subtotal=1, total=1, payment_status="probably_paid",
                created_at=datetime.now(UTC),
            )


class ProviderTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_shopify_graphql_pagination_and_rate_metadata(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-shopify-access-token"], "token")
            return httpx.Response(200, headers={
                "content-type": "application/json", "x-shopify-shop-api-call-limit": "2/40",
            }, json={"data": {"products": {
                "nodes": [{
                    "id": "p1", "title": "One", "status": "ACTIVE",
                    "variants": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                    "media": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                    "collections": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                }],
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-2"},
            }}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        adapter = ShopifyCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False))
        page = await adapter.synchronize(
            CredentialMaterial({"access_token": "token"}),
            CommerceSyncRequest(external_account_id="shop", mode="initial", domain="products", store_url="https://shop.example.com"),
            idempotency_key="sync-0001",
        )
        await client.aclose()
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_cursor, {"after": "cursor-2"})
        self.assertEqual(page.provider_metadata["api_call_limit"], "2/40")

    async def test_shopify_completes_nested_variant_pages_and_fails_closed_if_incomplete(self) -> None:
        requests: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            request_body = json.loads(request.content)
            requests.append(request_body)
            if len(requests) == 1:
                payload = {"data": {"products": {
                    "nodes": [{
                        "id": "p1", "title": "One", "status": "ACTIVE",
                        "variants": {"nodes": [{"id": "v1", "title": "One", "price": "1", "availableForSale": True}], "pageInfo": {"hasNextPage": True, "endCursor": "variant-cursor"}},
                        "media": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                        "collections": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                    }], "pageInfo": {"hasNextPage": False},
                }}}
            else:
                self.assertEqual(request_body["variables"]["after"], "variant-cursor")
                payload = {"data": {"node": {"variants": {
                    "nodes": [{"id": "v2", "title": "Two", "price": "2", "availableForSale": True}],
                    "pageInfo": {"hasNextPage": False},
                }}}}
            return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        page = await ShopifyCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"access_token": "token"}),
            CommerceSyncRequest(external_account_id="shop", mode="initial", domain="products", store_url="https://shop.example.com"),
            idempotency_key="shopify-nested",
        )
        await client.aclose()
        self.assertEqual([item.external_object_id for item in page.products[0].variants], ["v1", "v2"])
        self.assertEqual(len(requests), 2)

        async def incomplete_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"products": {
                "nodes": [{
                    "id": "p1", "title": "One", "status": "ACTIVE",
                    "variants": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                    "media": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": None}},
                    "collections": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                }], "pageInfo": {"hasNextPage": False},
            }}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(incomplete_handler))
        with self.assertRaises(CommerceProviderError) as caught:
            await ShopifyCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
                CredentialMaterial({"access_token": "token"}),
                CommerceSyncRequest(external_account_id="shop", mode="initial", domain="products", store_url="https://shop.example.com"),
                idempotency_key="shopify-incomplete",
            )
        await client.aclose()
        self.assertEqual(caught.exception.code, "provider_payload_incomplete")

    async def test_woocommerce_variations_are_paginated_to_contract_boundary(self) -> None:
        variation_pages: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/products"):
                return httpx.Response(200, headers={"content-type": "application/json", "x-wp-totalpages": "1"}, json=[{
                    "id": 10, "name": "Variable", "status": "publish", "type": "variable",
                    "variations": [101, 102], "images": [], "categories": [], "tags": [],
                }])
            page = int(request.url.params["page"])
            variation_pages.append(page)
            return httpx.Response(200, headers={"content-type": "application/json", "x-wp-totalpages": "2"}, json=[{
                "id": 100 + page, "name": f"Variant {page}", "price": str(page),
                "stock_quantity": page, "stock_status": "instock", "attributes": [],
            }])

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        page = await WooCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"consumer_key": "key", "consumer_secret": "secret"}),
            CommerceSyncRequest(external_account_id="woo", mode="initial", domain="products", store_url="https://shop.example.com"),
            idempotency_key="woo-variations",
        )
        await client.aclose()
        self.assertEqual(variation_pages, [1, 2])
        self.assertEqual(len(page.products[0].variants), 2)

    async def test_transport_classifies_authentication_and_rate_limit(self) -> None:
        for status, code, retryable in ((401, "authentication_failed", False), (403, "authorization_required", False), (404, "provider_not_found", False), (422, "provider_validation_error", False), (429, "rate_limited", True), (503, "temporary_provider_failure", True)):
            async def handler(_request: httpx.Request, value=status) -> httpx.Response:
                return httpx.Response(value, headers={"content-type": "application/json"}, json={})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            transport = SafeCommerceHttpClient(client, validate_dns=False)
            with self.subTest(status=status), self.assertRaises(CommerceProviderError) as caught:
                await transport.request_json("GET", "https://api.example.com/items")
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.retryable, retryable)
            await client.aclose()

        async def retry_after_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"content-type": "application/json", "retry-after": "37"}, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(retry_after_handler))
        with self.assertRaises(CommerceProviderError) as caught:
            await SafeCommerceHttpClient(client, validate_dns=False).request_json("GET", "https://api.example.com/items")
        await client.aclose()
        self.assertEqual(caught.exception.retry_after_seconds, 37)

    async def test_woocommerce_bigcommerce_and_magento_paginate_incrementally(self) -> None:
        observed: list[httpx.Request] = []

        async def woo_handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(200, headers={
                "content-type": "application/json", "x-wp-totalpages": "2",
            }, json=[{"id": 1, "name": "One", "status": "publish", "images": [], "categories": [], "tags": []}])

        client = httpx.AsyncClient(transport=httpx.MockTransport(woo_handler))
        page = await WooCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"consumer_key": "key", "consumer_secret": "secret"}),
            CommerceSyncRequest(external_account_id="woo", mode="incremental", domain="products", store_url="https://shop.example.com", updated_since=datetime(2026, 8, 1, tzinfo=UTC)),
            idempotency_key="woo-sync-1",
        )
        self.assertEqual(page.next_cursor, {"page": 2})
        self.assertIn("modified_after=", str(observed[-1].url))
        await client.aclose()

        async def big_handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(200, headers={"content-type": "application/json"}, json={
                "data": [{"id": 2, "name": "Two", "price": 2, "variants": [], "images": []}],
                "meta": {"pagination": {"total_pages": 2}},
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(big_handler))
        page = await BigCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"access_token": "token", "store_hash": "abc123"}),
            CommerceSyncRequest(external_account_id="abc123", mode="incremental", domain="products", updated_since=datetime(2026, 8, 1, tzinfo=UTC)),
            idempotency_key="big-sync-1",
        )
        self.assertEqual(page.next_cursor, {"page": 2})
        self.assertIn("date_modified%3Amin=", str(observed[-1].url))
        await client.aclose()

        async def magento_handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(200, headers={"content-type": "application/json"}, json={
                "items": [{"id": 3, "sku": "THREE", "name": "Three", "price": 3, "status": 1, "custom_attributes": [], "media_gallery_entries": [], "extension_attributes": {}}],
                "total_count": 101,
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(magento_handler))
        page = await MagentoCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"access_token": "token"}),
            CommerceSyncRequest(external_account_id="magento", mode="incremental", domain="products", store_url="https://shop.example.com", updated_since=datetime(2026, 8, 1, tzinfo=UTC)),
            idempotency_key="magento-sync-1",
        )
        self.assertEqual(page.next_cursor, {"page": 2})
        self.assertIn("conditionType%5D=gteq", str(observed[-1].url))
        await client.aclose()

    async def test_transport_rejects_redirects_oversize_depth_and_timeouts(self) -> None:
        cases = (
            (lambda request: httpx.Response(302, headers={"location": "https://other.example.com"}, request=request), "unsafe_redirect"),
            (lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=b"{}", request=request), "invalid_content_type"),
            (lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b'"' + b"x" * MAX_PROVIDER_RESPONSE_BYTES + b'"', request=request), "response_too_large"),
            (lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=(b"[" * 65) + b"0" + (b"]" * 65), request=request), "invalid_response"),
            (lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps([0] * MAX_JSON_NODES).encode(), request=request), "invalid_response"),
        )
        for handler, code in cases:
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with self.subTest(code=code), self.assertRaises(CommerceProviderError) as caught:
                await SafeCommerceHttpClient(client, validate_dns=False).request_json("GET", "https://api.example.com/items")
            self.assertEqual(caught.exception.code, code)
            await client.aclose()

        async def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow provider", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
        with self.assertRaises(CommerceProviderError) as caught:
            await SafeCommerceHttpClient(client, validate_dns=False).request_json("GET", "https://api.example.com/items")
        self.assertEqual(caught.exception.code, "provider_unavailable")
        self.assertTrue(caught.exception.retryable)
        await client.aclose()

    async def test_magento_orders_fetch_authoritative_refunds_and_shipments(self) -> None:
        observed_paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed_paths.append(request.url.path)
            if request.url.path.endswith("/orders"):
                payload = {"items": [{
                    "entity_id": 42, "increment_id": "000042", "status": "processing",
                    "order_currency_code": "USD", "subtotal": "20", "grand_total": "20",
                    "total_refunded": "5", "created_at": "2026-08-01T10:00:00Z",
                    "updated_at": "2026-08-02T10:00:00Z",
                    "items": [{"item_id": 7, "name": "Tee", "sku": "TEE", "qty_ordered": "2", "price": "10"}],
                }], "total_count": 1}
            elif request.url.path.endswith("/creditmemos"):
                self.assertIn("conditionType%5D=in", str(request.url))
                payload = {"items": [{
                    "entity_id": 90, "order_id": 42, "grand_total": "5",
                    "order_currency_code": "USD", "created_at": "2026-08-02T10:00:00Z",
                    "items": [{"order_item_id": 7, "qty": "1", "row_total_incl_tax": "5"}],
                }], "total_count": 1}
            elif request.url.path.endswith("/shipments"):
                payload = {"items": [{
                    "entity_id": 91, "order_id": 42, "created_at": "2026-08-02T11:00:00Z",
                    "items": [{"order_item_id": 7, "qty": "1"}],
                    "tracks": [{"entity_id": 92, "track_number": "TRACK-42", "title": "Carrier"}],
                }], "total_count": 1}
            else:
                self.fail(f"Unexpected Magento path {request.url.path}")
            return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        page = await MagentoCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"access_token": "token"}),
            CommerceSyncRequest(
                external_account_id="magento", mode="incremental", domain="orders",
                store_url="https://shop.example.com", page_size=100,
            ),
            idempotency_key="magento-order-sync",
        )
        await client.aclose()
        self.assertEqual(observed_paths, [
            "/rest/V1/orders", "/rest/V1/creditmemos", "/rest/V1/shipments",
        ])
        self.assertEqual(page.orders[0].refunds[0].external_object_id, "90")
        self.assertEqual(page.orders[0].refunds[0].lines[0].external_order_line_id, "7")
        self.assertEqual(page.orders[0].fulfillments[0].tracking_number, "TRACK-42")
        self.assertEqual(page.orders[0].fulfillment_status, "partial")

    async def test_magento_configurable_children_become_variants(self) -> None:
        observed_paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            observed_paths.append(request.url.path)
            if "/configurable-products/" in request.url.path:
                payload = [{
                    "id": 12, "sku": "SHIRT-BLUE", "name": "Blue shirt", "price": "25", "status": 1,
                    "custom_attributes": [{"attribute_code": "color", "value": "4"}],
                    "extension_attributes": {"stock_item": {"qty": 3, "is_in_stock": True}},
                }]
            else:
                payload = {"items": [{
                    "id": 11, "sku": "SHIRT", "name": "Shirt", "type_id": "configurable",
                    "price": "20", "status": 1, "custom_attributes": [], "media_gallery_entries": [],
                    "extension_attributes": {"configurable_product_options": [{
                        "attribute_code": "color", "label": "Color",
                        "values": [{"value_index": 4, "label": "Blue"}],
                    }]},
                }], "total_count": 1}
            return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        page = await MagentoCommerceAdapter(SafeCommerceHttpClient(client, validate_dns=False)).synchronize(
            CredentialMaterial({"access_token": "token"}),
            CommerceSyncRequest(external_account_id="magento", mode="initial", domain="products", store_url="https://shop.example.com"),
            idempotency_key="magento-configurable",
        )
        await client.aclose()
        self.assertEqual(observed_paths, [
            "/rest/V1/products", "/rest/V1/configurable-products/SHIRT/children",
        ])
        self.assertEqual(page.products[0].variants[0].external_object_id, "12")
        self.assertEqual(page.products[0].variants[0].option_values, {"Color": "Blue"})
        self.assertEqual(page.products[0].inventory_quantity, 3)

    async def test_dns_resolution_fails_closed_for_local_names(self) -> None:
        with self.assertRaises(CommerceValidationError):
            await resolve_public_host("localhost")

    async def test_dns_resolution_fails_closed_when_public_name_resolves_private(self) -> None:
        loop = MagicMock()
        loop.getaddrinfo = AsyncMock(return_value=[
            (2, 1, 6, "", ("10.20.30.40", 443)),
        ])
        with (
            patch("app.integrations.commerce_http.asyncio.get_running_loop", return_value=loop),
            self.assertRaises(CommerceValidationError),
        ):
            await resolve_public_host("provider.example.com")


class WebhookSecurityTests(unittest.TestCase):
    def test_shopify_webhook_signature_and_event_identity(self) -> None:
        body = json.dumps({"id": 77, "updated_at": "2026-08-01T12:00:00Z"}, separators=(",", ":")).encode()
        signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
        event = ShopifyCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({
                "webhook_secret": "secret",
                "store_url": "https://shop.example.test",
            }),
            CommerceWebhookRequest(headers={
                "x-shopify-hmac-sha256": signature,
                "x-shopify-shop-domain": "shop.example.test",
                "x-shopify-webhook-id": "delivery-1", "x-shopify-topic": "orders/updated",
            }, body=body),
        )
        self.assertEqual(event.external_event_id, "delivery-1")
        self.assertEqual(event.reconciliation_domain, "orders")
        with self.assertRaises(CommerceProviderError):
            ShopifyCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"webhook_secret": "secret"}),
                CommerceWebhookRequest(headers={"x-shopify-hmac-sha256": "bad"}, body=body),
            )
        with self.assertRaises(CommerceProviderError):
            ShopifyCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({
                    "webhook_secret": "secret",
                    "store_url": "https://shop.example.test",
                }),
                CommerceWebhookRequest(headers={
                    "x-shopify-hmac-sha256": signature,
                    "x-shopify-shop-domain": "other.example.test",
                    "x-shopify-webhook-id": "delivery-1",
                    "x-shopify-topic": "orders/updated",
                }, body=body),
            )

    def test_other_provider_webhooks_verify_and_normalize(self) -> None:
        body = json.dumps({"id": 88, "date_modified_gmt": "2026-08-01T12:00:00Z"}, separators=(",", ":")).encode()
        signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
        woo = WooCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({
                "webhook_secret": "secret",
                "store_url": "https://woo.example.test",
            }),
            CommerceWebhookRequest(headers={
                "x-wc-webhook-signature": signature,
                "x-wc-webhook-delivery-id": "woo-1",
                "x-wc-webhook-topic": "order.updated",
                "x-wc-webhook-source": "https://woo.example.test/",
            }, body=body),
        )
        self.assertEqual(woo.reconciliation_domain, "orders")
        with self.assertRaises(CommerceProviderError):
            WooCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({
                    "webhook_secret": "secret",
                    "store_url": "https://woo.example.test",
                }),
                CommerceWebhookRequest(headers={
                    "x-wc-webhook-signature": signature,
                    "x-wc-webhook-delivery-id": "woo-1",
                    "x-wc-webhook-topic": "order.updated",
                    "x-wc-webhook-source": "https://other.example.test/",
                }, body=body),
            )

        event_id, timestamp = "big-1", str(int(time.time()))
        big_body = json.dumps({"producer": "stores/abc123", "scope": "store/order/updated", "data": {"id": 99}, "created_at": int(timestamp)}, separators=(",", ":")).encode()
        signed = f"{event_id}.{timestamp}.".encode() + big_body
        big_signature = base64.b64encode(hmac.new(b"secret", signed, hashlib.sha256).digest()).decode()
        big = BigCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({"webhook_secret": "whsec_secret", "store_hash": "abc123"}),
            CommerceWebhookRequest(headers={
                "webhook-signature": f"v1,{big_signature}",
                "webhook-id": event_id, "webhook-timestamp": timestamp,
            }, body=big_body),
        )
        self.assertEqual(big.external_object_id, "99")
        wrong_store_body = json.dumps({"producer": "stores/other", "scope": "store/order/updated", "data": {"id": 99}, "created_at": int(timestamp)}, separators=(",", ":")).encode()
        wrong_store_signed = f"{event_id}.{timestamp}.".encode() + wrong_store_body
        wrong_store_signature = base64.b64encode(hmac.new(b"secret", wrong_store_signed, hashlib.sha256).digest()).decode()
        with self.assertRaises(CommerceProviderError):
            BigCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"webhook_secret": "whsec_secret", "store_hash": "abc123"}),
                CommerceWebhookRequest(headers={
                    "webhook-signature": f"v1,{wrong_store_signature}",
                    "webhook-id": event_id, "webhook-timestamp": timestamp,
                }, body=wrong_store_body),
            )
        stale_timestamp = str(int(timestamp) - 301)
        stale_signed = f"{event_id}.{stale_timestamp}.".encode() + big_body
        stale_signature = base64.b64encode(hmac.new(b"secret", stale_signed, hashlib.sha256).digest()).decode()
        with self.assertRaises(CommerceProviderError):
            BigCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"webhook_secret": "whsec_secret", "store_hash": "abc123"}),
                CommerceWebhookRequest(headers={
                    "webhook-signature": f"v1,{stale_signature}",
                    "webhook-id": event_id, "webhook-timestamp": stale_timestamp,
                }, body=big_body),
            )

        magento_body = json.dumps({"event": "order.updated", "event_id": "magento-1", "entity_id": 101}, separators=(",", ":")).encode()
        magento_signature = hmac.new(b"secret", magento_body, hashlib.sha256).hexdigest()
        magento = MagentoCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({"webhook_secret": "secret"}),
            CommerceWebhookRequest(headers={"x-magento-signature": magento_signature}, body=magento_body),
        )
        self.assertEqual(magento.reconciliation_domain, "orders")

        custom_body = json.dumps({"event_id": "custom-1", "topic": "inventory.updated", "object_id": "v1"}, separators=(",", ":")).encode()
        custom_signature = hmac.new(b"secret", custom_body, hashlib.sha256).hexdigest()
        custom = CustomApiCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({"webhook_secret": "secret"}),
            CommerceWebhookRequest(headers={"x-commerce-signature": custom_signature}, body=custom_body),
        )
        self.assertEqual(custom.reconciliation_domain, "inventory")

    def test_adobe_webhook_uses_public_key_signature_and_request_identity(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        body = json.dumps({"event": "order.updated", "entity_id": 103}, separators=(",", ":")).encode()
        signature = base64.b64encode(private_key.sign(
            base64.b64encode(body), padding.PKCS1v15(), hashes.SHA256(),
        )).decode()
        event = MagentoCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({"webhook_public_key": public_key}),
            CommerceWebhookRequest(headers={
                "x-adobe-commerce-webhook-signature": signature,
                "x-adobe-commerce-request-id": "adobe-request-1",
                "x-adobe-commerce-event": "order.updated",
            }, body=body),
        )
        self.assertEqual(event.external_event_id, "adobe-request-1")
        self.assertEqual(event.reconciliation_domain, "orders")
        with self.assertRaises(CommerceProviderError):
            MagentoCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"webhook_public_key": public_key}),
                CommerceWebhookRequest(headers={
                    "x-adobe-commerce-webhook-signature": signature,
                    "x-adobe-commerce-request-id": "adobe-request-2",
                    "x-adobe-commerce-event": "order.updated",
                }, body=body + b" "),
            )
        wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        for material, supplied in (
            ({"webhook_public_key": public_key}, "not-base64!"),
            ({"webhook_public_key": wrong_key}, signature),
            ({"webhook_public_key": "not-a-public-key"}, signature),
        ):
            with self.subTest(material=next(iter(material))), self.assertRaises(CommerceProviderError):
                MagentoCommerceAdapter().verify_and_parse_webhook(
                    CredentialMaterial(material),
                    CommerceWebhookRequest(headers={
                        "x-adobe-commerce-webhook-signature": supplied,
                        "x-adobe-commerce-request-id": "adobe-request-invalid",
                        "x-adobe-commerce-event": "order.updated",
                    }, body=body),
                )
        with self.assertRaises(CommerceConfigurationRequiredError):
            MagentoCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"access_token": "not-a-webhook-key"}),
                CommerceWebhookRequest(headers={
                    "x-adobe-commerce-webhook-signature": signature,
                    "x-adobe-commerce-request-id": "adobe-request-unconfigured",
                    "x-adobe-commerce-event": "order.updated",
                }, body=body),
            )

    def test_legacy_magento_hmac_is_explicit_and_rejects_bad_secret(self) -> None:
        body = json.dumps({"event": "order.updated", "event_id": "legacy-1", "entity_id": 101}, separators=(",", ":")).encode()
        signature = hmac.new(b"legacy-secret", body, hashlib.sha256).hexdigest()
        event = MagentoCommerceAdapter().verify_and_parse_webhook(
            CredentialMaterial({"webhook_secret": "legacy-secret"}),
            CommerceWebhookRequest(headers={"x-magento-signature": signature}, body=body),
        )
        self.assertEqual(event.external_event_id, "legacy-1")
        with self.assertRaises(CommerceProviderError):
            MagentoCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"webhook_secret": "wrong-secret"}),
                CommerceWebhookRequest(headers={"x-magento-signature": signature}, body=body),
            )
        with self.assertRaises(CommerceConfigurationRequiredError):
            MagentoCommerceAdapter().verify_and_parse_webhook(
                CredentialMaterial({"access_token": "not-a-webhook-secret"}),
                CommerceWebhookRequest(headers={"x-magento-signature": signature}, body=body),
            )


class CustomApiAndImportSecurityTests(unittest.TestCase):
    def test_custom_api_rejects_absolute_or_traversal_endpoints(self) -> None:
        for endpoint in ("http://localhost/products", "https://other.example/products", "../admin"):
            material = CredentialMaterial({
                "api_token": "token", "configuration": json.dumps({"endpoints": {"products": endpoint}}),
            })
            with self.subTest(endpoint=endpoint), self.assertRaises(CommerceConfigurationRequiredError):
                CustomApiCommerceAdapter._configuration(material)

    def test_url_validation_rejects_private_targets_and_credentials(self) -> None:
        for url in (
            "http://example.com", "https://user:pass@example.com",
            "https://0.0.0.0", "https://10.0.0.1", "https://127.0.0.1",
            "https://169.254.169.254/latest", "https://172.16.0.1",
            "https://192.168.1.1", "https://[::1]", "https://[fc00::1]",
            "https://[fe80::1]",
        ):
            with self.subTest(url=url), self.assertRaises(CommerceValidationError):
                validate_public_https_url(url)

    def test_csv_and_google_xml_preview_streams_records_and_preserves_unknown_inventory(self) -> None:
        csv_preview = preview_import(
            io.BytesIO(b"id,name,price,currency,availability\np1,Shoe,19.99,USD,\n"),
            filename="products.csv", file_type="csv", mapping=CommerceImportMapping(),
        )
        self.assertEqual(csv_preview.products[0].availability, "unknown")
        xml_preview = preview_import(
            io.BytesIO(b'''<?xml version="1.0"?><rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><g:id>p2</g:id><g:title>Bag</g:title><g:price>25.00 USD</g:price><g:availability>in stock</g:availability></item></channel></rss>'''),
            filename="feed.xml", file_type="google_product_feed", mapping=CommerceImportMapping(),
        )
        self.assertEqual(xml_preview.products[0].external_object_id, "p2")
        self.assertEqual(xml_preview.products[0].availability, "in_stock")

    def test_xml_doctype_is_rejected(self) -> None:
        with self.assertRaises(CommerceValidationError):
            preview_import(
                io.BytesIO(b'<!DOCTYPE x [<!ENTITY y "boom">]><products/>'),
                filename="unsafe.xml", file_type="xml_feed", mapping=CommerceImportMapping(),
            )

    def test_import_preview_reports_partial_success_without_losing_valid_rows(self) -> None:
        preview = preview_import(
            io.BytesIO(b"id,name,price\np1,Good,10\np2,,bad\n"),
            filename="partial.csv", file_type="csv", mapping=CommerceImportMapping(),
        )
        self.assertEqual([item.external_object_id for item in preview.products], ["p1"])
        self.assertEqual(len(preview.failures), 1)


class RegistryTests(unittest.TestCase):
    def test_real_adapters_are_registered_without_claiming_connection_credentials(self) -> None:
        definitions = {item.provider: item for item in commerce_connectors.provider_definitions()}
        for provider in ("shopify", "woocommerce", "bigcommerce", "magento", "custom_api"):
            self.assertTrue(definitions[provider].configured)
            self.assertEqual(definitions[provider].implementation_status, "code_ready_credentials_required")
        self.assertEqual(definitions["manual"].implementation_status, "production_functional")
        self.assertEqual(definitions["csv"].implementation_status, "production_functional")
        self.assertEqual(definitions["xml_feed"].implementation_status, "production_functional")
        self.assertEqual(definitions["google_product_feed"].implementation_status, "production_functional")
