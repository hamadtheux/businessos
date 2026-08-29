from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.exceptions.commerce import CommerceConfigurationRequiredError, CommerceProviderError
from app.integrations.commerce_contracts import (
    CommerceSyncPage,
    CommerceSyncRequest,
    CommerceWebhookRequest,
)
from app.integrations.commerce_http import (
    SafeCommerceHttpClient,
    parse_bounded_json_bytes,
    provider_url,
    validate_public_https_url,
)
from app.integrations.credentials import CredentialMaterial
from app.schemas.commerce import (
    NormalizedAddress,
    NormalizedCollection,
    NormalizedCustomer,
    NormalizedDiscount,
    NormalizedFulfillment,
    NormalizedMedia,
    NormalizedOrder,
    NormalizedOrderLine,
    NormalizedProduct,
    NormalizedRefund,
    NormalizedRefundLine,
    NormalizedShipping,
    NormalizedStore,
    NormalizedTax,
    NormalizedVariant,
    NormalizedWebhookEvent,
)


_DOMAIN_SEQUENCE = ("store", "products", "customers", "orders")
_HTML = re.compile(r"<[^>]+>")


def _required(credentials: CredentialMaterial, key: str) -> str:
    value = credentials.values.get(key, "").strip()
    if not value:
        raise CommerceConfigurationRequiredError()
    return value


def _decimal(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise CommerceProviderError("invalid_response") from None


def _integer(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise CommerceProviderError("invalid_response") from None
    return max(0, parsed)


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CommerceProviderError("invalid_response") from None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _text(value: object, limit: int, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized[:limit] if normalized else default


def _identifier(value: object) -> str:
    if value is None or isinstance(value, bool):
        raise CommerceProviderError("invalid_response")
    normalized = str(value).strip()
    if not normalized:
        raise CommerceProviderError("invalid_response")
    return normalized[:255]


def _strip_html(value: object) -> str | None:
    text = _text(value, 20_000)
    return _HTML.sub(" ", text).strip()[:10_000] if text else None


def _availability(quantity: int | None, available: bool | None = None, backorder: bool = False) -> str:
    if backorder:
        return "backorder"
    if quantity is None:
        return "in_stock" if available is True else "out_of_stock" if available is False else "unknown"
    return "in_stock" if quantity > 0 else "out_of_stock"


def _topic_domain(topic: str) -> str:
    normalized = topic.casefold()
    if "product" in normalized or "inventory" in normalized:
        return "inventory" if "inventory" in normalized else "products"
    if "customer" in normalized:
        return "customers"
    return "orders"


def _safe_hmac(secret: str, body: bytes, supplied: str, *, hexadecimal: bool = False) -> bool:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = digest.hex() if hexadecimal else base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, supplied.strip())


def _safe_adobe_signature(public_key_pem: str, body: bytes, supplied: str) -> bool:
    """Verify Adobe Commerce's RSA signature over the base64 payload."""
    try:
        signature = base64.b64decode(supplied.strip(), validate=True)
        public_key = serialization.load_pem_public_key(public_key_pem.encode())
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 2048:
            return False
        public_key.verify(
            signature,
            base64.b64encode(body),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, UnsupportedAlgorithm, TypeError, ValueError):
        return False
    return True


def _webhook_payload(body: bytes) -> Mapping[str, object]:
    payload = parse_bounded_json_bytes(body, max_bytes=2 * 1024 * 1024)
    if not isinstance(payload, Mapping):
        raise CommerceProviderError("invalid_response")
    return payload


class ShopifyCommerceAdapter:
    provider = "shopify"
    capabilities = frozenset({
        "store_read", "catalog_read", "variants", "inventory_read", "customers_read",
        "orders_read", "refunds_read", "fulfillments_read", "incremental_sync", "webhooks",
    })

    def __init__(self, transport: SafeCommerceHttpClient | None = None) -> None:
        self._http = transport or SafeCommerceHttpClient()

    async def synchronize(self, credentials: CredentialMaterial, request: CommerceSyncRequest, *, idempotency_key: str) -> CommerceSyncPage:
        _ = idempotency_key
        token = _required(credentials, "access_token")
        base = request.store_url or credentials.values.get("store_url")
        if not base:
            raise CommerceConfigurationRequiredError()
        version = credentials.values.get("api_version", "2026-07")
        if re.fullmatch(r"20\d{2}-(01|04|07|10)", version) is None:
            raise CommerceConfigurationRequiredError("invalid_api_version")
        endpoint = provider_url(base, f"admin/api/{version}/graphql.json")
        query, variables = self._query(request)
        auth_headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        payload, headers = await self._http.request_json(
            "POST", endpoint,
            headers=auth_headers,
            json_body={"query": query, "variables": variables},
            allowed_host=urlsplit(base).hostname,
        )
        data = self._graphql_data(payload)
        metadata = {"api_version": version}
        if headers.get("x-shopify-shop-api-call-limit"):
            metadata["api_call_limit"] = headers["x-shopify-shop-api-call-limit"][:40]
        if request.domain == "store":
            return CommerceSyncPage(domain="store", store=self.normalize_store(data.get("shop")), provider_metadata=metadata)
        connection = data.get(request.domain)
        if not isinstance(connection, Mapping):
            raise CommerceProviderError("invalid_response")
        nodes = connection.get("nodes") or []
        page_info = connection.get("pageInfo") or {}
        if not isinstance(nodes, list) or not isinstance(page_info, Mapping):
            raise CommerceProviderError("invalid_response")
        if request.domain == "products":
            await self._complete_product_connections(endpoint, auth_headers, nodes)
        elif request.domain == "orders":
            await self._complete_order_connections(endpoint, auth_headers, nodes)
        has_more = bool(page_info.get("hasNextPage"))
        cursor = {"after": page_info.get("endCursor")} if has_more and page_info.get("endCursor") else {}
        common = dict(domain=request.domain, next_cursor=cursor, has_more=has_more, complete_snapshot=not has_more and request.mode in {"initial", "full"}, provider_metadata=metadata)
        if request.domain == "products":
            return CommerceSyncPage(products=tuple(self.normalize_product(item) for item in nodes), **common)
        if request.domain == "customers":
            return CommerceSyncPage(customers=tuple(self.normalize_customer(item) for item in nodes), **common)
        if request.domain == "orders":
            return CommerceSyncPage(orders=tuple(self.normalize_order(item) for item in nodes), **common)
        raise CommerceProviderError("invalid_cursor")

    def _query(self, request: CommerceSyncRequest) -> tuple[str, dict[str, object]]:
        outer_limit = 25 if request.domain in {"products", "orders"} else 100
        count = max(1, min(request.page_size, outer_limit))
        variables: dict[str, object] = {"first": count, "after": request.cursor.get("after")}
        updated = f" updated_at:>={request.updated_since.isoformat()}" if request.updated_since else ""
        variables["query"] = updated.strip() or None
        if request.domain == "store":
            return "query { shop { id name url currencyCode ianaTimezone email updatedAt } }", {}
        if request.domain == "products":
            return """query($first:Int!,$after:String,$query:String){products(first:$first,after:$after,query:$query,sortKey:UPDATED_AT){nodes{id title descriptionHtml handle status vendor productType tags updatedAt onlineStoreUrl featuredMedia{preview{image{url altText}}} collections(first:100){nodes{id title handle}pageInfo{hasNextPage endCursor}} media(first:100){nodes{id mediaContentType alt preview{image{url altText}}}pageInfo{hasNextPage endCursor}} variants(first:250){nodes{id title sku barcode price compareAtPrice availableForSale inventoryQuantity selectedOptions{name value}}pageInfo{hasNextPage endCursor}}}pageInfo{hasNextPage endCursor}}}""", variables
        if request.domain == "customers":
            return """query($first:Int!,$after:String,$query:String){customers(first:$first,after:$after,query:$query,sortKey:UPDATED_AT){nodes{id displayName firstName lastName email phone tags updatedAt defaultAddress{firstName lastName company address1 address2 city province zip countryCodeV2 phone}}pageInfo{hasNextPage endCursor}}}""", variables
        if request.domain == "orders":
            return """query($first:Int!,$after:String,$query:String){orders(first:$first,after:$after,query:$query,sortKey:UPDATED_AT){nodes{id name createdAt updatedAt displayFinancialStatus displayFulfillmentStatus currencyCode subtotalPriceSet{shopMoney{amount}} totalDiscountsSet{shopMoney{amount}} totalTaxSet{shopMoney{amount}} totalShippingPriceSet{shopMoney{amount}} totalPriceSet{shopMoney{amount}} customer{id displayName firstName lastName email phone} billingAddress{firstName lastName company address1 address2 city province zip countryCodeV2 phone} shippingAddress{firstName lastName company address1 address2 city province zip countryCodeV2 phone} lineItems(first:250){nodes{id title quantity sku originalUnitPriceSet{shopMoney{amount}} discountedUnitPriceSet{shopMoney{amount}} variant{id} product{id}}pageInfo{hasNextPage endCursor}} refunds{id createdAt note totalRefundedSet{shopMoney{amount currencyCode}} refundLineItems(first:250){nodes{quantity subtotalSet{shopMoney{amount}} lineItem{id}}pageInfo{hasNextPage endCursor}}} fulfillments{id status createdAt trackingInfo{company number url} fulfillmentLineItems(first:250){nodes{lineItem{id}}pageInfo{hasNextPage endCursor}}}}pageInfo{hasNextPage endCursor}}}""", variables
        raise CommerceProviderError("invalid_cursor")

    @staticmethod
    def _graphql_data(payload: object) -> Mapping[str, object]:
        if not isinstance(payload, Mapping):
            raise CommerceProviderError("invalid_response")
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            codes = {
                str((item.get("extensions") or {}).get("code") or "").upper()
                for item in errors
                if isinstance(item, Mapping)
                and isinstance(item.get("extensions"), Mapping)
            }
            if codes & {"THROTTLED", "MAX_COST_EXCEEDED"}:
                raise CommerceProviderError("rate_limited", retryable=True)
            if codes & {"ACCESS_DENIED", "UNAUTHORIZED"}:
                raise CommerceProviderError("authentication_failed", retryable=False)
            raise CommerceProviderError("request_failed", retryable=False)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise CommerceProviderError("invalid_response")
        return data

    async def _complete_product_connections(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        nodes: list[object],
    ) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                raise CommerceProviderError("invalid_response")
            await self._complete_node_connection(
                endpoint, headers, node, owner_type="Product", field="variants",
                selection="id title sku barcode price compareAtPrice availableForSale inventoryQuantity selectedOptions{name value}",
                maximum_items=500,
            )
            await self._complete_node_connection(
                endpoint, headers, node, owner_type="Product", field="media",
                selection="id mediaContentType alt preview{image{url altText}}",
                maximum_items=100,
            )
            await self._complete_node_connection(
                endpoint, headers, node, owner_type="Product", field="collections",
                selection="id title handle", maximum_items=100,
            )

    async def _complete_order_connections(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        nodes: list[object],
    ) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                raise CommerceProviderError("invalid_response")
            await self._complete_node_connection(
                endpoint, headers, node, owner_type="Order", field="lineItems",
                selection="id title quantity sku originalUnitPriceSet{shopMoney{amount}} discountedUnitPriceSet{shopMoney{amount}} variant{id} product{id}",
                maximum_items=1000,
            )
            for refund in node.get("refunds") or []:
                if not isinstance(refund, dict):
                    raise CommerceProviderError("invalid_response")
                await self._complete_node_connection(
                    endpoint, headers, refund, owner_type="Refund", field="refundLineItems",
                    selection="quantity subtotalSet{shopMoney{amount}} lineItem{id}",
                    maximum_items=500,
                )
            for fulfillment in node.get("fulfillments") or []:
                if not isinstance(fulfillment, dict):
                    raise CommerceProviderError("invalid_response")
                await self._complete_node_connection(
                    endpoint, headers, fulfillment, owner_type="Fulfillment", field="fulfillmentLineItems",
                    selection="lineItem{id}", maximum_items=500,
                )

    async def _complete_node_connection(
        self,
        endpoint: str,
        headers: Mapping[str, str],
        owner: dict[str, object],
        *,
        owner_type: str,
        field: str,
        selection: str,
        maximum_items: int,
    ) -> None:
        connection = owner.get(field)
        if not isinstance(connection, Mapping):
            raise CommerceProviderError("invalid_response")
        current_nodes = connection.get("nodes")
        page_info = connection.get("pageInfo") or {}
        if not isinstance(current_nodes, list) or not isinstance(page_info, Mapping):
            raise CommerceProviderError("invalid_response")
        merged = list(current_nodes)
        while page_info.get("hasNextPage"):
            if len(merged) >= maximum_items or not page_info.get("endCursor"):
                raise CommerceProviderError("provider_payload_incomplete", retryable=False)
            count = min(250, maximum_items - len(merged))
            query = (
                "query($id:ID!,$first:Int!,$after:String){node(id:$id){"
                f"... on {owner_type}{{{field}(first:$first,after:$after)"
                f"{{nodes{{{selection}}}pageInfo{{hasNextPage endCursor}}}}}}}}}}"
            )
            payload, _ = await self._http.request_json(
                "POST", endpoint, headers=headers,
                json_body={"query": query, "variables": {
                    "id": _identifier(owner.get("id")), "first": count,
                    "after": page_info.get("endCursor"),
                }},
                allowed_host=urlsplit(endpoint).hostname,
            )
            data = self._graphql_data(payload)
            returned_owner = data.get("node")
            returned_connection = returned_owner.get(field) if isinstance(returned_owner, Mapping) else None
            if not isinstance(returned_connection, Mapping):
                raise CommerceProviderError("invalid_response")
            returned_nodes = returned_connection.get("nodes")
            page_info = returned_connection.get("pageInfo") or {}
            if not isinstance(returned_nodes, list) or not isinstance(page_info, Mapping):
                raise CommerceProviderError("invalid_response")
            merged.extend(returned_nodes)
            if len(merged) > maximum_items:
                raise CommerceProviderError("provider_payload_incomplete", retryable=False)
        owner[field] = {"nodes": merged, "pageInfo": dict(page_info)}

    @staticmethod
    def normalize_store(raw: object) -> NormalizedStore:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        return NormalizedStore(
            external_account_id=_identifier(raw.get("id")), name=str(raw.get("name") or "Shopify store"),
            public_url=raw.get("url"), currency=_text(raw.get("currencyCode"), 3),
            timezone=_text(raw.get("ianaTimezone"), 100), email=_text(raw.get("email"), 320),
            provider_updated_at=_datetime(raw.get("updatedAt")),
        )

    @staticmethod
    def normalize_product(raw: object) -> NormalizedProduct:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        variants_raw = ((raw.get("variants") or {}).get("nodes") or []) if isinstance(raw.get("variants"), Mapping) else []
        variants = [ShopifyCommerceAdapter._variant(item) for item in variants_raw]
        media_raw = ((raw.get("media") or {}).get("nodes") or []) if isinstance(raw.get("media"), Mapping) else []
        media = [value for index, item in enumerate(media_raw) if (value := ShopifyCommerceAdapter._media(item, index))]
        collections_raw = ((raw.get("collections") or {}).get("nodes") or []) if isinstance(raw.get("collections"), Mapping) else []
        collections = [
            NormalizedCollection(
                external_object_id=str(item.get("id")),
                title=str(item.get("title") or "Collection")[:255],
                handle=_text(item.get("handle"), 255),
            )
            for item in collections_raw
            if isinstance(item, Mapping) and item.get("id")
        ]
        featured = raw.get("featuredMedia")
        preview = featured.get("preview") if isinstance(featured, Mapping) else None
        image = preview.get("image") if isinstance(preview, Mapping) else None
        if not media and isinstance(image, Mapping) and image.get("url"):
            media.append(NormalizedMedia(source_url=image["url"], alt_text=_text(image.get("altText"), 1000)))
        quantity_values = [item.inventory_quantity for item in variants if item.inventory_quantity is not None]
        quantity = sum(quantity_values) if quantity_values else None
        first = variants[0] if variants else None
        status = str(raw.get("status") or "ACTIVE").upper()
        return NormalizedProduct(
            external_object_id=_identifier(raw.get("id")), name=str(raw.get("title") or "Untitled product")[:200],
            description=_strip_html(raw.get("descriptionHtml")), sku=first.sku if first else None,
            product_url=raw.get("onlineStoreUrl"), image_urls=[item.source_url for item in media if item.media_type == "image"], media=media,
            collections=collections,
            price=first.price if first else None, compare_at_price=first.compare_at_price if first else None,
            inventory_quantity=quantity, availability=_availability(quantity, any(item.available for item in variants) if variants else None),
            vendor=_text(raw.get("vendor"), 160), brand=_text(raw.get("vendor"), 160),
            google_product_category=_text(raw.get("productType"), 255),
            tags=[str(value)[:80] for value in (raw.get("tags") or [])][:100],
            published=status == "ACTIVE", status="archived" if status == "ARCHIVED" else "draft" if status == "DRAFT" else "active",
            provider_updated_at=_datetime(raw.get("updatedAt")), variants=variants,
            safe_metadata={"handle": _text(raw.get("handle"), 255), "provider_status": status},
        )

    @staticmethod
    def _variant(raw: object) -> NormalizedVariant:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        quantity = _integer(raw.get("inventoryQuantity"))
        return NormalizedVariant(
            external_object_id=_identifier(raw.get("id")), title=str(raw.get("title") or "Default")[:200],
            sku=_text(raw.get("sku"), 100), barcode=_text(raw.get("barcode"), 64),
            price=_decimal(raw.get("price")), compare_at_price=_decimal(raw["compareAtPrice"]) if raw.get("compareAtPrice") not in (None, "") else None,
            inventory_quantity=quantity, available=bool(raw.get("availableForSale")),
            option_values={str(item.get("name"))[:80]: str(item.get("value"))[:255] for item in (raw.get("selectedOptions") or []) if isinstance(item, Mapping)},
        )

    @staticmethod
    def _media(raw: object, position: int) -> NormalizedMedia | None:
        if not isinstance(raw, Mapping):
            return None
        preview = raw.get("preview")
        image = preview.get("image") if isinstance(preview, Mapping) else None
        if not isinstance(image, Mapping) or not image.get("url"):
            return None
        kind = str(raw.get("mediaContentType") or "IMAGE").upper()
        return NormalizedMedia(
            external_object_id=_text(raw.get("id"), 255), media_type="video" if "VIDEO" in kind else "image",
            source_url=image["url"], alt_text=_text(raw.get("alt") or image.get("altText"), 1000), position=position,
        )

    @staticmethod
    def normalize_customer(raw: object) -> NormalizedCustomer:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        return NormalizedCustomer(
            external_object_id=_identifier(raw.get("id")), display_name=_text(raw.get("displayName"), 160),
            first_name=_text(raw.get("firstName"), 80), last_name=_text(raw.get("lastName"), 80),
            email=_text(raw.get("email"), 320), phone=_text(raw.get("phone"), 32),
            tags=[str(value)[:40] for value in (raw.get("tags") or [])][:20],
            address=_address(raw.get("defaultAddress"), shopify=True), provider_updated_at=_datetime(raw.get("updatedAt")),
        )

    @staticmethod
    def normalize_order(raw: object) -> NormalizedOrder:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        money = lambda field: _decimal((((raw.get(field) or {}).get("shopMoney") or {}).get("amount")))
        currency = str(raw.get("currencyCode") or "USD").upper()
        lines_raw = ((raw.get("lineItems") or {}).get("nodes") or []) if isinstance(raw.get("lineItems"), Mapping) else []
        lines = []
        for line in lines_raw:
            if not isinstance(line, Mapping):
                continue
            amount = (((line.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount"))
            lines.append(NormalizedOrderLine(
                external_object_id=_identifier(line.get("id")), external_product_id=_mapping_id(line.get("product")),
                external_variant_id=_mapping_id(line.get("variant")), sku=_text(line.get("sku"), 100),
                title=str(line.get("title") or "Order item")[:300], quantity=max(1, int(line.get("quantity") or 1)), unit_price=_decimal(amount),
            ))
        customer_raw = raw.get("customer")
        customer = ShopifyCommerceAdapter.normalize_customer(customer_raw) if isinstance(customer_raw, Mapping) else None
        refunds = [ShopifyCommerceAdapter._refund(item, currency) for item in (raw.get("refunds") or []) if isinstance(item, Mapping)]
        fulfillments = [
            fulfillment
            for item in (raw.get("fulfillments") or [])
            if isinstance(item, Mapping)
            for fulfillment in ShopifyCommerceAdapter._fulfillments(item)
        ]
        financial = str(raw.get("displayFinancialStatus") or "unknown").casefold()
        fulfillment = str(raw.get("displayFulfillmentStatus") or "unknown").casefold()
        return NormalizedOrder(
            external_object_id=_identifier(raw.get("id")), order_number=_identifier(raw.get("name") or raw.get("id"))[-40:],
            external_customer_id=customer.external_object_id if customer else None, customer=customer, currency=currency,
            subtotal=money("subtotalPriceSet"), discount_amount=money("totalDiscountsSet"),
            tax_amount=money("totalTaxSet"), shipping_amount=money("totalShippingPriceSet"), total=money("totalPriceSet"),
            payment_status=_payment_status(financial), fulfillment_status=_fulfillment_status(fulfillment),
            status="completed" if fulfillment == "fulfilled" else "processing",
            created_at=_datetime(raw.get("createdAt")) or datetime.now(UTC), updated_at=_datetime(raw.get("updatedAt")),
            billing_address=_address(raw.get("billingAddress"), shopify=True), shipping_address=_address(raw.get("shippingAddress"), shopify=True),
            lines=lines, refunds=refunds, fulfillments=fulfillments,
        )

    @staticmethod
    def _refund(raw: Mapping[str, object], currency: str) -> NormalizedRefund:
        total = (((raw.get("totalRefundedSet") or {}).get("shopMoney") or {}) if isinstance(raw.get("totalRefundedSet"), Mapping) else {})
        nodes = ((raw.get("refundLineItems") or {}).get("nodes") or []) if isinstance(raw.get("refundLineItems"), Mapping) else []
        lines = [NormalizedRefundLine(
            external_order_line_id=_mapping_id(item.get("lineItem")), quantity=max(1, int(item.get("quantity") or 1)),
            amount=_decimal((((item.get("subtotalSet") or {}).get("shopMoney") or {}).get("amount"))),
        ) for item in nodes if isinstance(item, Mapping)]
        return NormalizedRefund(
            external_object_id=_identifier(raw.get("id")), amount=_decimal(total.get("amount")),
            currency=str(total.get("currencyCode") or currency).upper(), occurred_at=_datetime(raw.get("createdAt")) or datetime.now(UTC),
            reason=_text(raw.get("note"), 1000), lines=lines,
        )

    @staticmethod
    def _fulfillments(raw: Mapping[str, object]) -> list[NormalizedFulfillment]:
        tracking_items = [item for item in (raw.get("trackingInfo") or []) if isinstance(item, Mapping)] or [{}]
        nodes = ((raw.get("fulfillmentLineItems") or {}).get("nodes") or []) if isinstance(raw.get("fulfillmentLineItems"), Mapping) else []
        base_id = _identifier(raw.get("id"))
        line_ids = [
            value
            for item in nodes
            if isinstance(item, Mapping)
            if (value := _mapping_id(item.get("lineItem")))
        ]
        return [NormalizedFulfillment(
            external_object_id=base_id if len(tracking_items) == 1 else f"{base_id}:tracking:{index}",
            status=_fulfillment_record_status(raw.get("status")),
            occurred_at=_datetime(raw.get("createdAt")), tracking_company=_text(tracking.get("company"), 160),
            tracking_number=_text(tracking.get("number"), 255), tracking_url=tracking.get("url"),
            external_order_line_ids=line_ids,
        ) for index, tracking in enumerate(tracking_items, start=1)]

    def verify_and_parse_webhook(self, credentials: CredentialMaterial, request: CommerceWebhookRequest) -> NormalizedWebhookEvent:
        secret = _required(credentials, "webhook_secret")
        supplied = request.headers.get("x-shopify-hmac-sha256", "")
        if not supplied or not _safe_hmac(secret, request.body, supplied):
            raise CommerceProviderError("webhook_verification_failed")
        expected_host = _credential_store_host(credentials)
        supplied_host = request.headers.get("x-shopify-shop-domain", "").rstrip(".").casefold()
        if expected_host is None or supplied_host != expected_host:
            raise CommerceProviderError("webhook_verification_failed")
        topic = request.headers.get("x-shopify-topic", "").strip()
        event_id = request.headers.get("x-shopify-webhook-id", "").strip()
        payload = _webhook_payload(request.body)
        if not topic or not event_id:
            raise CommerceProviderError("invalid_response")
        return NormalizedWebhookEvent(
            external_event_id=event_id, topic=topic, external_object_id=_text(payload.get("admin_graphql_api_id") or payload.get("id"), 255),
            occurred_at=_datetime(payload.get("updated_at") or payload.get("created_at")), reconciliation_domain=_topic_domain(topic),
        )


class WooCommerceAdapter:
    provider = "woocommerce"
    capabilities = ShopifyCommerceAdapter.capabilities

    def __init__(self, transport: SafeCommerceHttpClient | None = None) -> None:
        self._http = transport or SafeCommerceHttpClient()

    async def synchronize(self, credentials: CredentialMaterial, request: CommerceSyncRequest, *, idempotency_key: str) -> CommerceSyncPage:
        _ = idempotency_key
        key, secret = _required(credentials, "consumer_key"), _required(credentials, "consumer_secret")
        base = request.store_url or credentials.values.get("store_url")
        if not base:
            raise CommerceConfigurationRequiredError()
        headers = {"Authorization": "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()}
        if request.domain == "store":
            raw, _headers = await self._http.request_json("GET", provider_url(base, "wp-json/wc/v3/system_status"), headers=headers, allowed_host=urlsplit(base).hostname)
            environment = raw.get("environment", {}) if isinstance(raw, Mapping) else {}
            return CommerceSyncPage(domain="store", store=NormalizedStore(
                external_account_id=request.external_account_id, name=_text(environment.get("site_title"), 160, default="WooCommerce store") or "WooCommerce store",
                public_url=base, currency=_text(environment.get("currency"), 3), timezone=_text(environment.get("timezone"), 100),
            ))
        endpoint = {"products": "products", "customers": "customers", "orders": "orders"}.get(request.domain)
        if endpoint is None:
            raise CommerceProviderError("invalid_cursor")
        page = int(request.cursor.get("page", 1))
        params: dict[str, object] = {"page": page, "per_page": min(request.page_size, 100), "orderby": "modified", "order": "asc"}
        if request.updated_since:
            params["modified_after"] = request.updated_since.isoformat()
        raw, response_headers = await self._http.request_json(
            "GET", provider_url(base, f"wp-json/wc/v3/{endpoint}"), headers=headers, params=params, allowed_host=urlsplit(base).hostname,
        )
        if not isinstance(raw, list):
            raise CommerceProviderError("invalid_response")
        total_pages = int(response_headers.get("x-wp-totalpages", page))
        has_more = page < total_pages
        common = dict(domain=request.domain, next_cursor={"page": page + 1} if has_more else {}, has_more=has_more, complete_snapshot=not has_more and request.mode in {"initial", "full"})
        if request.domain == "products":
            products = []
            for item in raw:
                variations: list[object] = []
                if isinstance(item, Mapping) and item.get("variations"):
                    variations = await self._paged_subresource(
                        base=base,
                        headers=headers,
                        path=f"products/{_identifier(item.get('id'))}/variations",
                        maximum_items=500,
                    )
                products.append(self.normalize_product(item, variations=variations))
            return CommerceSyncPage(products=tuple(products), **common)
        if request.domain == "customers":
            return CommerceSyncPage(customers=tuple(self.normalize_customer(item) for item in raw), **common)
        orders = []
        for item in raw:
            refunds_raw: object = []
            if isinstance(item, Mapping) and item.get("refunds"):
                refunds_raw = await self._paged_subresource(
                    base=base,
                    headers=headers,
                    path=f"orders/{_identifier(item.get('id'))}/refunds",
                    maximum_items=500,
                )
            orders.append(self.normalize_order(item, refunds=refunds_raw if isinstance(refunds_raw, list) else []))
        return CommerceSyncPage(orders=tuple(orders), **common)

    async def _paged_subresource(
        self,
        *,
        base: str,
        headers: Mapping[str, str],
        path: str,
        maximum_items: int,
    ) -> list[object]:
        records: list[object] = []
        maximum_pages = max(1, (maximum_items + 99) // 100)
        for page in range(1, maximum_pages + 1):
            payload, response_headers = await self._http.request_json(
                "GET",
                provider_url(base, f"wp-json/wc/v3/{path}"),
                headers=headers,
                params={"page": page, "per_page": 100},
                allowed_host=urlsplit(base).hostname,
            )
            if not isinstance(payload, list):
                raise CommerceProviderError("invalid_response")
            records.extend(payload)
            total_pages = int(response_headers.get("x-wp-totalpages", page))
            if total_pages > maximum_pages or len(records) > maximum_items:
                raise CommerceProviderError("response_too_large", retryable=False)
            if page >= total_pages:
                return records
        raise CommerceProviderError("response_too_large", retryable=False)

    @staticmethod
    def normalize_product(raw: object, *, variations: object = ()) -> NormalizedProduct:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        variants = [WooCommerceAdapter._variant(item) for item in variations if isinstance(item, Mapping)] if isinstance(variations, list) else []
        if not variants and raw.get("type") == "simple":
            variants = [WooCommerceAdapter._variant({**raw, "id": f"product:{raw.get('id')}", "attributes": []})]
        images = [item for item in (raw.get("images") or []) if isinstance(item, Mapping) and item.get("src")]
        categories = [
            NormalizedCollection(
                external_object_id=_identifier(item.get("id")),
                title=str(item.get("name") or item.get("slug") or item.get("id"))[:255],
                handle=_text(item.get("slug"), 255),
            )
            for item in (raw.get("categories") or [])
            if isinstance(item, Mapping) and item.get("id") is not None
        ]
        quantity = _integer(raw.get("stock_quantity"))
        return NormalizedProduct(
            external_object_id=_identifier(raw.get("id")), name=str(raw.get("name") or "Untitled product")[:200],
            description=_strip_html(raw.get("description") or raw.get("short_description")), sku=_text(raw.get("sku"), 100),
            product_url=raw.get("permalink"), image_urls=[item["src"] for item in images[:50]],
            media=[NormalizedMedia(external_object_id=_text(item.get("id"), 255), source_url=item["src"], alt_text=_text(item.get("alt"), 1000), position=index) for index, item in enumerate(images[:100])],
            collections=categories[:100],
            price=_decimal(raw["price"]) if raw.get("price") not in (None, "") else None,
            compare_at_price=_decimal(raw["regular_price"]) if raw.get("regular_price") not in (None, "") else None,
            inventory_quantity=quantity, availability=_availability(quantity, raw.get("stock_status") == "instock", raw.get("backorders") in {"yes", "notify"}),
            tags=[str(item.get("name"))[:80] for item in (raw.get("tags") or []) if isinstance(item, Mapping)][:100],
            google_product_category=_text(next((item.get("name") for item in (raw.get("categories") or []) if isinstance(item, Mapping)), None), 255),
            published=str(raw.get("status")) == "publish", status="archived" if str(raw.get("status")) == "trash" else "draft" if str(raw.get("status")) != "publish" else "active",
            provider_updated_at=_datetime(raw.get("date_modified_gmt") or raw.get("date_modified")), variants=variants,
            safe_metadata={"slug": _text(raw.get("slug"), 255), "provider_status": _text(raw.get("status"), 40)},
        )

    @staticmethod
    def _variant(raw: Mapping[str, object]) -> NormalizedVariant:
        quantity = _integer(raw.get("stock_quantity"))
        return NormalizedVariant(
            external_object_id=_identifier(raw.get("id")), title=_text(raw.get("name"), 200, default="Default") or "Default",
            sku=_text(raw.get("sku"), 100), price=_decimal(raw["price"]) if raw.get("price") not in (None, "") else None,
            compare_at_price=_decimal(raw["regular_price"]) if raw.get("regular_price") not in (None, "") else None,
            inventory_quantity=quantity, available=str(raw.get("stock_status")) == "instock",
            option_values={str(item.get("name"))[:80]: str(item.get("option"))[:255] for item in (raw.get("attributes") or []) if isinstance(item, Mapping)},
        )

    @staticmethod
    def normalize_customer(raw: object) -> NormalizedCustomer:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        return NormalizedCustomer(
            external_object_id=_identifier(raw.get("id")), display_name=_text(raw.get("username"), 160),
            first_name=_text(raw.get("first_name"), 80), last_name=_text(raw.get("last_name"), 80),
            email=_text(raw.get("email"), 320), phone=_text(((raw.get("billing") or {}).get("phone")) if isinstance(raw.get("billing"), Mapping) else None, 32),
            address=_address(raw.get("shipping") or raw.get("billing")), provider_updated_at=_datetime(raw.get("date_modified_gmt") or raw.get("date_modified")),
        )

    @staticmethod
    def normalize_order(raw: object, *, refunds: list[object] = ()) -> NormalizedOrder:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        billing = raw.get("billing") if isinstance(raw.get("billing"), Mapping) else {}
        customer = None
        customer_id = raw.get("customer_id")
        if customer_id not in (None, 0, "0") or billing.get("email") or billing.get("phone"):
            customer = NormalizedCustomer(
                external_object_id=str(customer_id or f"guest:{raw.get('id')}"),
                display_name=_text(" ".join(filter(None, (billing.get("first_name"), billing.get("last_name")))), 160),
                first_name=_text(billing.get("first_name"), 80), last_name=_text(billing.get("last_name"), 80),
                email=_text(billing.get("email"), 320), phone=_text(billing.get("phone"), 32), address=_address(billing),
            )
        lines = [NormalizedOrderLine(
            external_object_id=_identifier(item.get("id")), external_product_id=_text(item.get("product_id"), 255),
            external_variant_id=_text(item.get("variation_id"), 255), sku=_text(item.get("sku"), 100),
            title=str(item.get("name") or "Order item")[:300], quantity=max(1, int(item.get("quantity") or 1)),
            unit_price=(_decimal(item.get("subtotal")) / max(1, int(item.get("quantity") or 1))).quantize(Decimal("0.01")),
            discount_amount=max(Decimal("0"), _decimal(item.get("subtotal")) - _decimal(item.get("total"))), tax_amount=_decimal(item.get("total_tax")),
        ) for item in (raw.get("line_items") or []) if isinstance(item, Mapping)]
        currency = str(raw.get("currency") or "USD").upper()
        refund_values = [NormalizedRefund(
            external_object_id=_identifier(item.get("id")), amount=abs(_decimal(item.get("amount"))), currency=currency,
            occurred_at=_datetime(item.get("date_created_gmt") or item.get("date_created")) or datetime.now(UTC), reason=_text(item.get("reason"), 1000),
            lines=[NormalizedRefundLine(external_order_line_id=_text(line.get("id"), 255), quantity=max(1, int(abs(line.get("quantity") or 1))), amount=abs(_decimal(line.get("total")))) for line in (item.get("line_items") or []) if isinstance(line, Mapping)],
        ) for item in refunds if isinstance(item, Mapping)]
        status = str(raw.get("status") or "processing").casefold()
        payment = "refunded" if status == "refunded" else "paid" if raw.get("date_paid") else "pending"
        fulfillment = "fulfilled" if status == "completed" else "unfulfilled"
        return NormalizedOrder(
            external_object_id=_identifier(raw.get("id")), order_number=_identifier(raw.get("number") or raw.get("id"))[:40],
            external_customer_id=customer.external_object_id if customer else None, customer=customer, currency=currency,
            subtotal=sum((line.unit_price * line.quantity for line in lines), Decimal("0")),
            discount_amount=_decimal(raw.get("discount_total")), tax_amount=_decimal(raw.get("total_tax")), shipping_amount=_decimal(raw.get("shipping_total")), total=_decimal(raw.get("total")),
            payment_status=payment, fulfillment_status=fulfillment, status="completed" if status == "completed" else "canceled" if status in {"cancelled", "failed"} else "processing",
            created_at=_datetime(raw.get("date_created_gmt") or raw.get("date_created")) or datetime.now(UTC), updated_at=_datetime(raw.get("date_modified_gmt") or raw.get("date_modified")),
            billing_address=_address(billing), shipping_address=_address(raw.get("shipping")), lines=lines, refunds=refund_values,
        )

    def verify_and_parse_webhook(self, credentials: CredentialMaterial, request: CommerceWebhookRequest) -> NormalizedWebhookEvent:
        secret = _required(credentials, "webhook_secret")
        signature = request.headers.get("x-wc-webhook-signature", "")
        if not signature or not _safe_hmac(secret, request.body, signature):
            raise CommerceProviderError("webhook_verification_failed")
        expected_host = _credential_store_host(credentials)
        source = request.headers.get("x-wc-webhook-source", "")
        try:
            supplied_host = urlsplit(source).hostname.casefold()
        except (AttributeError, ValueError):
            supplied_host = None
        if expected_host is None or supplied_host != expected_host:
            raise CommerceProviderError("webhook_verification_failed")
        payload = _webhook_payload(request.body)
        topic = request.headers.get("x-wc-webhook-topic", "")
        event_id = request.headers.get("x-wc-webhook-delivery-id", "")
        if not topic or not event_id:
            raise CommerceProviderError("invalid_response")
        return NormalizedWebhookEvent(external_event_id=event_id, topic=topic, external_object_id=_text(payload.get("id"), 255), occurred_at=_datetime(payload.get("date_modified_gmt") or payload.get("date_created_gmt")), reconciliation_domain=_topic_domain(topic))


class BigCommerceAdapter:
    provider = "bigcommerce"
    capabilities = ShopifyCommerceAdapter.capabilities

    def __init__(self, transport: SafeCommerceHttpClient | None = None) -> None:
        self._http = transport or SafeCommerceHttpClient()

    async def synchronize(self, credentials: CredentialMaterial, request: CommerceSyncRequest, *, idempotency_key: str) -> CommerceSyncPage:
        _ = idempotency_key
        token, store_hash = _required(credentials, "access_token"), _required(credentials, "store_hash")
        if re.fullmatch(r"[A-Za-z0-9]{2,64}", store_hash) is None:
            raise CommerceConfigurationRequiredError("invalid_store_hash")
        base = f"https://api.bigcommerce.com/stores/{store_hash}/"
        headers = {"X-Auth-Token": token}
        if request.domain == "store":
            raw, _ = await self._http.request_json("GET", provider_url(base, "v2/store"), headers=headers, allowed_host="api.bigcommerce.com")
            if not isinstance(raw, Mapping):
                raise CommerceProviderError("invalid_response")
            return CommerceSyncPage(domain="store", store=NormalizedStore(
                external_account_id=store_hash, name=str(raw.get("name") or "BigCommerce store")[:160], public_url=raw.get("domain"),
                currency=_text(raw.get("currency"), 3), timezone=_text(raw.get("timezone", {}).get("name") if isinstance(raw.get("timezone"), Mapping) else raw.get("timezone"), 100),
            ))
        page = int(request.cursor.get("page", 1))
        if request.domain == "products":
            path, version, extra = "catalog/products", "v3", {"include": "variants,images"}
        elif request.domain == "customers":
            path, version, extra = "customers", "v3", {}
        elif request.domain == "orders":
            path, version, extra = "orders", "v2", {}
        else:
            raise CommerceProviderError("invalid_cursor")
        params: dict[str, object] = {"page": page, "limit": min(request.page_size, 100), **extra}
        if request.updated_since:
            params["date_modified:min"] = request.updated_since.isoformat()
        raw, _headers = await self._http.request_json("GET", provider_url(base, f"{version}/{path}"), headers=headers, params=params, allowed_host="api.bigcommerce.com")
        if version == "v3":
            if not isinstance(raw, Mapping) or not isinstance(raw.get("data"), list):
                raise CommerceProviderError("invalid_response")
            items = raw["data"]
            pagination = (raw.get("meta") or {}).get("pagination") or {}
            total_pages = int(pagination.get("total_pages") or page)
        else:
            if not isinstance(raw, list):
                raise CommerceProviderError("invalid_response")
            items = raw
            total_pages = page + 1 if len(items) == min(request.page_size, 100) else page
        has_more = page < total_pages
        common = dict(domain=request.domain, next_cursor={"page": page + 1} if has_more else {}, has_more=has_more, complete_snapshot=not has_more and request.mode in {"initial", "full"})
        if request.domain == "products":
            return CommerceSyncPage(products=tuple(self.normalize_product(item) for item in items), **common)
        if request.domain == "customers":
            return CommerceSyncPage(customers=tuple(self.normalize_customer(item) for item in items), **common)
        orders = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            order_id = item.get("id")
            line_items, _ = await self._http.request_json("GET", provider_url(base, f"v2/orders/{order_id}/products"), headers=headers, allowed_host="api.bigcommerce.com")
            shipments, _ = await self._http.request_json(
                "GET",
                provider_url(base, f"v2/orders/{order_id}/shipments"),
                headers=headers,
                allowed_host="api.bigcommerce.com",
            )
            refunds: object = []
            try:
                refund_payload, _ = await self._http.request_json("GET", provider_url(base, f"v3/orders/{order_id}/payment_actions/refunds"), headers=headers, allowed_host="api.bigcommerce.com")
                refunds = refund_payload.get("data", []) if isinstance(refund_payload, Mapping) else []
            except CommerceProviderError as exc:
                if exc.code not in {"request_failed"}:
                    raise
            orders.append(self.normalize_order(
                item,
                lines=line_items if isinstance(line_items, list) else [],
                refunds=refunds if isinstance(refunds, list) else [],
                shipments=shipments if isinstance(shipments, list) else [],
            ))
        return CommerceSyncPage(orders=tuple(orders), **common)

    @staticmethod
    def normalize_product(raw: object) -> NormalizedProduct:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        variants = [NormalizedVariant(
            external_object_id=_identifier(item.get("id")), title=_text(item.get("sku"), 200, default="Default") or "Default",
            sku=_text(item.get("sku"), 100), price=_decimal(item["price"]) if item.get("price") is not None else _decimal(raw.get("price")),
            compare_at_price=_decimal(item["retail_price"]) if item.get("retail_price") is not None else None,
            inventory_quantity=_integer(item.get("inventory_level")), available=bool((item.get("inventory_level") or 0) > 0),
            option_values={str(option.get("display_name"))[:80]: str(option.get("label"))[:255] for option in (item.get("option_values") or []) if isinstance(option, Mapping)},
        ) for item in (raw.get("variants") or []) if isinstance(item, Mapping)]
        images = [item for item in (raw.get("images") or []) if isinstance(item, Mapping) and item.get("url_standard")]
        quantity = _integer(raw.get("inventory_level"))
        return NormalizedProduct(
            external_object_id=_identifier(raw.get("id")), name=str(raw.get("name") or "Untitled product")[:200], description=_strip_html(raw.get("description")),
            sku=_text(raw.get("sku"), 100), product_url=raw.get("custom_url", {}).get("url") if isinstance(raw.get("custom_url"), Mapping) and str(raw.get("custom_url", {}).get("url", "")).startswith("http") else None,
            image_urls=[item["url_standard"] for item in images[:50]], media=[NormalizedMedia(external_object_id=_text(item.get("id"), 255), source_url=item["url_standard"], alt_text=_text(item.get("description"), 1000), position=int(item.get("sort_order") or index)) for index, item in enumerate(images[:100])],
            price=_decimal(raw.get("price")), compare_at_price=_decimal(raw["retail_price"]) if raw.get("retail_price") is not None else None,
            cost=_decimal(raw["cost_price"]) if raw.get("cost_price") is not None else None, inventory_quantity=quantity, availability=_availability(quantity),
            brand=_text(raw.get("brand_name"), 160), gtin=_text(raw.get("gtin"), 32), mpn=_text(raw.get("mpn"), 100),
            published=bool(raw.get("is_visible", True)), status="active" if raw.get("is_visible", True) else "draft", provider_updated_at=_datetime(raw.get("date_modified")), variants=variants,
            safe_metadata={"provider_status": "visible" if raw.get("is_visible", True) else "hidden"},
        )

    @staticmethod
    def normalize_customer(raw: object) -> NormalizedCustomer:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        addresses = raw.get("addresses") or []
        return NormalizedCustomer(
            external_object_id=_identifier(raw.get("id")), display_name=_text(" ".join(filter(None, (raw.get("first_name"), raw.get("last_name")))), 160),
            first_name=_text(raw.get("first_name"), 80), last_name=_text(raw.get("last_name"), 80), email=_text(raw.get("email"), 320), phone=_text(raw.get("phone"), 32),
            company=_text(raw.get("company"), 160), address=_address(addresses[0]) if addresses and isinstance(addresses[0], Mapping) else None,
            provider_updated_at=_datetime(raw.get("date_modified")),
        )

    @staticmethod
    def normalize_order(
        raw: object,
        *,
        lines: list[object],
        refunds: list[object],
        shipments: list[object] = (),
    ) -> NormalizedOrder:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        order_lines = [NormalizedOrderLine(
            external_object_id=_identifier(item.get("id")), external_product_id=_text(item.get("product_id"), 255), external_variant_id=_text(item.get("variant_id"), 255),
            sku=_text(item.get("sku"), 100), title=str(item.get("name") or "Order item")[:300], quantity=max(1, int(item.get("quantity") or 1)),
            unit_price=_decimal(item.get("base_price") or item.get("price_inc_tax")), discount_amount=_decimal(item.get("discount_amount")), tax_amount=_decimal(item.get("total_tax")),
        ) for item in lines if isinstance(item, Mapping)]
        currency = str(raw.get("currency_code") or "USD").upper()
        refund_values = [NormalizedRefund(
            external_object_id=_identifier(item.get("id")), amount=_decimal(item.get("amount")), currency=currency,
            occurred_at=_datetime(item.get("created")) or datetime.now(UTC), reason=_text(item.get("reason"), 1000),
            lines=[NormalizedRefundLine(
                external_order_line_id=_identifier(line.get("item_id") or line.get("order_product_id")),
                quantity=max(1, int(_decimal(line.get("quantity") or 1))),
                amount=_decimal(line.get("amount")),
            ) for line in (item.get("items") or item.get("line_items") or []) if isinstance(line, Mapping) and (line.get("item_id") is not None or line.get("order_product_id") is not None)],
        ) for item in refunds if isinstance(item, Mapping)]
        fulfillment_values = [NormalizedFulfillment(
            external_object_id=_identifier(item.get("id")),
            status="fulfilled",
            occurred_at=_datetime(item.get("date_created")),
            tracking_company=_text(item.get("shipping_provider"), 160),
            tracking_number=_text(item.get("tracking_number"), 255),
            external_order_line_ids=[
                str(line.get("order_product_id"))
                for line in (item.get("items") or [])
                if isinstance(line, Mapping) and line.get("order_product_id") is not None
            ],
        ) for item in shipments if isinstance(item, Mapping) and item.get("id") is not None]
        customer_id = raw.get("customer_id")
        billing = raw.get("billing_address") if isinstance(raw.get("billing_address"), Mapping) else {}
        customer = NormalizedCustomer(
            external_object_id=str(customer_id or f"guest:{raw.get('id')}"), display_name=_text(" ".join(filter(None, (billing.get("first_name"), billing.get("last_name")))), 160),
            first_name=_text(billing.get("first_name"), 80), last_name=_text(billing.get("last_name"), 80), email=_text(billing.get("email"), 320), phone=_text(billing.get("phone"), 32), address=_address(billing),
        ) if customer_id or billing.get("email") or billing.get("phone") else None
        status = str(raw.get("status") or "").casefold()
        refund_total = sum((item.amount for item in refund_values), Decimal("0"))
        explicit_payment = _payment_status(str(raw.get("payment_status") or "unknown").casefold())
        payment_status = (
            "refunded" if refund_total >= _decimal(raw.get("total_inc_tax")) and refund_total
            else "partially_refunded" if refund_total
            else explicit_payment
        )
        return NormalizedOrder(
            external_object_id=_identifier(raw.get("id")), order_number=_identifier(raw.get("id"))[:40], external_customer_id=customer.external_object_id if customer else None, customer=customer, currency=currency,
            subtotal=_decimal(raw.get("subtotal_ex_tax")), discount_amount=_decimal(raw.get("discount_amount")), tax_amount=_decimal(raw.get("total_tax")), shipping_amount=_decimal(raw.get("shipping_cost_inc_tax")), total=_decimal(raw.get("total_inc_tax")),
            payment_status=payment_status,
            fulfillment_status="fulfilled" if status in {"completed", "shipped"} else "partial" if "partial" in status else "unfulfilled",
            status="completed" if status in {"completed", "shipped"} else "canceled" if "cancel" in status else "processing",
            created_at=_datetime(raw.get("date_created")) or datetime.now(UTC), updated_at=_datetime(raw.get("date_modified")), billing_address=_address(billing),
            lines=order_lines, refunds=refund_values, fulfillments=fulfillment_values,
        )

    def verify_and_parse_webhook(self, credentials: CredentialMaterial, request: CommerceWebhookRequest) -> NormalizedWebhookEvent:
        secret = _required(credentials, "webhook_secret")
        signature = request.headers.get("webhook-signature") or request.headers.get("x-bc-webhook-signature", "")
        event_id = request.headers.get("webhook-id", "")
        timestamp = request.headers.get("webhook-timestamp", "")
        try:
            timestamp_value = int(timestamp)
        except (TypeError, ValueError):
            timestamp_value = 0
        if not event_id or abs(int(datetime.now(UTC).timestamp()) - timestamp_value) > 300:
            raise CommerceProviderError("webhook_verification_failed")
        signed = f"{event_id}.{timestamp}.".encode() + request.body
        candidates = [
            item.removeprefix("v1,")
            for item in signature.split(" ")
            if item.startswith("v1,") and len(item) > 3
        ]
        valid = any(_safe_hmac(secret.removeprefix("whsec_"), signed, candidate) for candidate in candidates)
        if not signature or not valid:
            raise CommerceProviderError("webhook_verification_failed")
        payload = _webhook_payload(request.body)
        expected_producer = f"stores/{_required(credentials, 'store_hash')}"
        if payload.get("producer") != expected_producer:
            raise CommerceProviderError("webhook_verification_failed")
        topic = str(payload.get("scope") or "")
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        return NormalizedWebhookEvent(external_event_id=event_id or str(payload.get("hash") or ""), topic=topic, external_object_id=_text(data.get("id") or data.get("order_id"), 255), occurred_at=datetime.fromtimestamp(int(payload.get("created_at")), UTC) if payload.get("created_at") else None, reconciliation_domain=_topic_domain(topic))


class MagentoCommerceAdapter:
    provider = "magento"
    capabilities = ShopifyCommerceAdapter.capabilities

    def __init__(self, transport: SafeCommerceHttpClient | None = None) -> None:
        self._http = transport or SafeCommerceHttpClient()

    async def synchronize(self, credentials: CredentialMaterial, request: CommerceSyncRequest, *, idempotency_key: str) -> CommerceSyncPage:
        _ = idempotency_key
        token = _required(credentials, "access_token")
        base = request.store_url or credentials.values.get("store_url")
        if not base:
            raise CommerceConfigurationRequiredError()
        headers = {"Authorization": f"Bearer {token}"}
        if request.domain == "store":
            raw, _ = await self._http.request_json("GET", provider_url(base, "rest/V1/store/storeConfigs"), headers=headers, allowed_host=urlsplit(base).hostname)
            item = raw[0] if isinstance(raw, list) and raw else None
            if not isinstance(item, Mapping):
                raise CommerceProviderError("invalid_response")
            return CommerceSyncPage(domain="store", store=NormalizedStore(
                external_account_id=str(item.get("id") or request.external_account_id), name=_text(item.get("website_name"), 160, default="Adobe Commerce store") or "Adobe Commerce store",
                public_url=item.get("base_url"), currency=_text(item.get("base_currency_code"), 3), timezone=_text(item.get("timezone"), 100),
            ))
        path = {"products": "products", "customers": "customers/search", "orders": "orders"}.get(request.domain)
        if path is None:
            raise CommerceProviderError("invalid_cursor")
        page = int(request.cursor.get("page", 1))
        params: dict[str, object] = {"searchCriteria[currentPage]": page, "searchCriteria[pageSize]": min(request.page_size, 100)}
        if request.updated_since:
            params.update({"searchCriteria[filterGroups][0][filters][0][field]": "updated_at", "searchCriteria[filterGroups][0][filters][0][value]": request.updated_since.isoformat(), "searchCriteria[filterGroups][0][filters][0][conditionType]": "gteq"})
        raw, _ = await self._http.request_json("GET", provider_url(base, f"rest/V1/{path}"), headers=headers, params=params, allowed_host=urlsplit(base).hostname)
        if not isinstance(raw, Mapping) or not isinstance(raw.get("items"), list):
            raise CommerceProviderError("invalid_response")
        items = raw["items"]
        total = int(raw.get("total_count") or len(items))
        has_more = page * min(request.page_size, 100) < total
        common = dict(domain=request.domain, next_cursor={"page": page + 1} if has_more else {}, has_more=has_more, complete_snapshot=not has_more and request.mode in {"initial", "full"})
        if request.domain == "products":
            products: list[NormalizedProduct] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                children: list[object] | None = None
                if str(item.get("type_id") or "").casefold() == "configurable":
                    sku = _identifier(item.get("sku"))
                    payload, _ = await self._http.request_json(
                        "GET",
                        provider_url(
                            base,
                            f"rest/V1/configurable-products/{quote(sku, safe='')}/children",
                        ),
                        headers=headers,
                        allowed_host=urlsplit(base).hostname,
                    )
                    if not isinstance(payload, list):
                        raise CommerceProviderError("invalid_response")
                    if len(payload) > 500:
                        raise CommerceProviderError("response_too_large", retryable=False)
                    children = payload
                products.append(self.normalize_product(item, base, children=children))
            return CommerceSyncPage(products=tuple(products), **common)
        if request.domain == "customers":
            return CommerceSyncPage(customers=tuple(self.normalize_customer(item) for item in items), **common)
        order_ids = [_identifier(item.get("entity_id")) for item in items if isinstance(item, Mapping)]
        credit_memos, shipments = await self._order_records(
            base=base, headers=headers, order_ids=order_ids,
        )
        refunds_by_order: dict[str, list[object]] = {}
        for item in credit_memos:
            if isinstance(item, Mapping) and item.get("order_id") is not None:
                refunds_by_order.setdefault(_identifier(item.get("order_id")), []).append(item)
        shipments_by_order: dict[str, list[object]] = {}
        for item in shipments:
            if isinstance(item, Mapping) and item.get("order_id") is not None:
                shipments_by_order.setdefault(_identifier(item.get("order_id")), []).append(item)
        return CommerceSyncPage(orders=tuple(
            self.normalize_order(
                item,
                refund_records=refunds_by_order.get(_identifier(item.get("entity_id")), []),
                shipment_records=shipments_by_order.get(_identifier(item.get("entity_id")), []),
            )
            for item in items if isinstance(item, Mapping)
        ), **common)

    async def _order_records(
        self,
        *,
        base: str,
        headers: Mapping[str, str],
        order_ids: list[str],
    ) -> tuple[list[object], list[object]]:
        if not order_ids:
            return [], []
        results: list[list[object]] = []
        for path in ("creditmemos", "shipments"):
            records: list[object] = []
            for page in range(1, 21):
                raw, _ = await self._http.request_json(
                    "GET",
                    provider_url(base, f"rest/V1/{path}"),
                    headers=headers,
                    params={
                        "searchCriteria[filterGroups][0][filters][0][field]": "order_id",
                        "searchCriteria[filterGroups][0][filters][0][value]": ",".join(order_ids),
                        "searchCriteria[filterGroups][0][filters][0][conditionType]": "in",
                        "searchCriteria[currentPage]": page,
                        "searchCriteria[pageSize]": 100,
                    },
                    allowed_host=urlsplit(base).hostname,
                )
                if not isinstance(raw, Mapping) or not isinstance(raw.get("items"), list):
                    raise CommerceProviderError("invalid_response")
                records.extend(raw["items"])
                total = int(raw.get("total_count") or len(records))
                if len(records) >= total:
                    break
            else:
                raise CommerceProviderError("response_too_large", retryable=False)
            results.append(records)
        return results[0], results[1]

    @staticmethod
    def normalize_product(
        raw: object,
        base_url: str,
        *,
        children: list[object] | None = None,
    ) -> NormalizedProduct:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        attrs = {str(item.get("attribute_code")): item.get("value") for item in (raw.get("custom_attributes") or []) if isinstance(item, Mapping)}
        media_entries = [item for item in (raw.get("media_gallery_entries") or []) if isinstance(item, Mapping) and item.get("file")]
        images = [provider_url(base_url, f"media/catalog/product/{str(item['file']).lstrip('/')}") for item in media_entries]
        extensions = raw.get("extension_attributes") if isinstance(raw.get("extension_attributes"), Mapping) else {}
        stock = (extensions.get("stock_item") or {}) if isinstance(extensions, Mapping) else {}
        quantity = _integer(stock.get("qty")) if isinstance(stock, Mapping) else None
        option_definitions = [
            item for item in (extensions.get("configurable_product_options") or [])
            if isinstance(item, Mapping)
        ] if isinstance(extensions, Mapping) else []
        variants = [
            MagentoCommerceAdapter._variant(
                item,
                option_definitions=option_definitions,
            )
            for item in (children or [])
            if isinstance(item, Mapping)
        ]
        if children is None:
            variants = [NormalizedVariant(
                external_object_id=_identifier(raw.get("id") or raw.get("sku")),
                title="Default",
                sku=_text(raw.get("sku"), 100),
                price=_decimal(raw.get("price")),
                inventory_quantity=quantity,
                available=bool(stock.get("is_in_stock")) if isinstance(stock, Mapping) else quantity is not None and quantity > 0,
            )]
        elif variants:
            child_quantities = [item.inventory_quantity for item in variants if item.inventory_quantity is not None]
            quantity = sum(child_quantities) if child_quantities else None
        provider_available = (
            any(item.available for item in variants)
            if children is not None
            else bool(stock.get("is_in_stock")) if isinstance(stock, Mapping) else None
        )
        status = int(raw.get("status") or 1)
        return NormalizedProduct(
            external_object_id=_identifier(raw.get("id") or raw.get("sku")), name=str(raw.get("name") or attrs.get("name") or raw.get("sku") or "Untitled product")[:200],
            description=_strip_html(attrs.get("description") or attrs.get("short_description")), sku=_text(raw.get("sku"), 100), image_urls=images[:50],
            media=[NormalizedMedia(external_object_id=_text(item.get("id"), 255), source_url=url, alt_text=_text(item.get("label"), 1000), position=int(item.get("position") or index)) for index, (item, url) in enumerate(zip(media_entries[:100], images[:100], strict=False))],
            price=_decimal(raw.get("price")), inventory_quantity=quantity, availability=_availability(quantity, provider_available),
            brand=_text(attrs.get("manufacturer"), 160), gtin=_text(attrs.get("gtin"), 32), mpn=_text(attrs.get("mpn"), 100),
            published=status == 1, status="active" if status == 1 else "draft", provider_updated_at=_datetime(raw.get("updated_at")), variants=variants,
            safe_metadata={"attribute_set_id": int(raw.get("attribute_set_id") or 0), "provider_status": status},
        )

    @staticmethod
    def _variant(
        raw: Mapping[str, object],
        *,
        option_definitions: list[Mapping[str, object]],
    ) -> NormalizedVariant:
        attrs = {
            str(item.get("attribute_code")): item.get("value")
            for item in (raw.get("custom_attributes") or [])
            if isinstance(item, Mapping) and item.get("attribute_code")
        }
        option_values: dict[str, str] = {}
        for option in option_definitions:
            code = _text(option.get("attribute_code"), 80)
            if not code or code not in attrs:
                continue
            value = attrs[code]
            labels = {
                str(item.get("value_index")): str(item.get("label"))[:255]
                for item in (option.get("values") or [])
                if isinstance(item, Mapping) and item.get("value_index") is not None and item.get("label")
            }
            option_values[str(option.get("label") or code)[:80]] = labels.get(str(value), str(value)[:255])
        extensions = raw.get("extension_attributes") if isinstance(raw.get("extension_attributes"), Mapping) else {}
        stock = (extensions.get("stock_item") or {}) if isinstance(extensions, Mapping) else {}
        quantity = _integer(stock.get("qty")) if isinstance(stock, Mapping) else None
        available = bool(stock.get("is_in_stock")) if isinstance(stock, Mapping) else quantity is not None and quantity > 0
        return NormalizedVariant(
            external_object_id=_identifier(raw.get("id") or raw.get("sku")),
            title=_text(raw.get("name"), 200, default=" / ".join(option_values.values()) or "Variant") or "Variant",
            sku=_text(raw.get("sku"), 100),
            barcode=_text(attrs.get("gtin") or attrs.get("barcode"), 64),
            price=_decimal(raw.get("price")),
            inventory_quantity=quantity,
            available=available and int(raw.get("status") or 1) == 1,
            option_values=option_values,
        )

    @staticmethod
    def normalize_customer(raw: object) -> NormalizedCustomer:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        addresses = raw.get("addresses") or []
        return NormalizedCustomer(
            external_object_id=_identifier(raw.get("id")), display_name=_text(" ".join(filter(None, (raw.get("firstname"), raw.get("lastname")))), 160),
            first_name=_text(raw.get("firstname"), 80), last_name=_text(raw.get("lastname"), 80), email=_text(raw.get("email"), 320),
            address=_address(addresses[0], magento=True) if addresses and isinstance(addresses[0], Mapping) else None, provider_updated_at=_datetime(raw.get("updated_at")),
        )

    @staticmethod
    def normalize_order(
        raw: object,
        *,
        refund_records: list[object] | None = None,
        shipment_records: list[object] | None = None,
    ) -> NormalizedOrder:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        lines = [NormalizedOrderLine(
            external_object_id=_identifier(item.get("item_id")), external_product_id=_text(item.get("product_id"), 255), sku=_text(item.get("sku"), 100),
            title=str(item.get("name") or "Order item")[:300], quantity=max(1, int(_decimal(item.get("qty_ordered")))), unit_price=_decimal(item.get("price")),
            discount_amount=_decimal(item.get("discount_amount")), tax_amount=_decimal(item.get("tax_amount")),
        ) for item in (raw.get("items") or []) if isinstance(item, Mapping) and not item.get("parent_item_id")]
        billing = raw.get("billing_address") if isinstance(raw.get("billing_address"), Mapping) else {}
        customer_id = raw.get("customer_id")
        customer = NormalizedCustomer(
            external_object_id=str(customer_id or f"guest:{raw.get('entity_id')}"), display_name=_text(raw.get("customer_name") or " ".join(filter(None, (raw.get("customer_firstname"), raw.get("customer_lastname")))), 160),
            first_name=_text(raw.get("customer_firstname"), 80), last_name=_text(raw.get("customer_lastname"), 80), email=_text(raw.get("customer_email"), 320), address=_address(billing, magento=True),
        ) if customer_id or raw.get("customer_email") else None
        status = str(raw.get("status") or "processing").casefold()
        currency = str(raw.get("order_currency_code") or "USD").upper()
        refunded = _decimal(raw.get("total_refunded"))
        refunds = [MagentoCommerceAdapter.normalize_refund(item, currency) for item in (refund_records or [])]
        if refund_records is None and refunded > 0:
            refunds = [NormalizedRefund(
                external_object_id=f"order-refund:{_identifier(raw.get('entity_id'))}",
                amount=refunded,
                currency=currency,
                occurred_at=_datetime(raw.get("updated_at")) or datetime.now(UTC),
            )]
        fulfillments = [
            fulfillment
            for item in (shipment_records or [])
            for fulfillment in MagentoCommerceAdapter.normalize_fulfillments(item)
        ]
        shipped_quantities: dict[str, int] = {}
        for shipment in shipment_records or []:
            if not isinstance(shipment, Mapping):
                continue
            for item in shipment.get("items") or []:
                if isinstance(item, Mapping) and item.get("order_item_id") is not None:
                    order_item_id = _identifier(item.get("order_item_id"))
                    shipped_quantities[order_item_id] = (
                        shipped_quantities.get(order_item_id, 0)
                        + max(0, int(_decimal(item.get("qty"))))
                    )
        ordered_quantities = {
            _identifier(item.get("item_id")): max(1, int(_decimal(item.get("qty_ordered"))))
            for item in (raw.get("items") or [])
            if isinstance(item, Mapping) and not item.get("parent_item_id")
        }
        if status == "complete" or (
            ordered_quantities
            and all(shipped_quantities.get(key, 0) >= quantity for key, quantity in ordered_quantities.items())
        ):
            fulfillment_status = "fulfilled"
        elif any(shipped_quantities.values()):
            fulfillment_status = "partial"
        else:
            fulfillment_status = "unfulfilled"
        grand_total = _decimal(raw.get("grand_total"))
        total_paid_raw = raw.get("total_paid")
        total_paid = _decimal(total_paid_raw) if total_paid_raw is not None else None
        payment_status = (
            "refunded" if refunded >= grand_total and refunded
            else "partially_refunded" if refunded
            else "paid" if total_paid is not None and total_paid >= grand_total and grand_total
            else "authorized" if total_paid is not None and total_paid > 0
            else "pending" if total_paid is not None
            else "unknown"
        )
        return NormalizedOrder(
            external_object_id=_identifier(raw.get("entity_id")), order_number=_identifier(raw.get("increment_id") or raw.get("entity_id"))[:40], external_customer_id=customer.external_object_id if customer else None, customer=customer,
            currency=currency, subtotal=_decimal(raw.get("subtotal")), discount_amount=abs(_decimal(raw.get("discount_amount"))), tax_amount=_decimal(raw.get("tax_amount")), shipping_amount=_decimal(raw.get("shipping_amount")), total=_decimal(raw.get("grand_total")),
            payment_status=payment_status,
            fulfillment_status=fulfillment_status, status="completed" if status == "complete" else "canceled" if status in {"canceled", "closed"} else "processing",
            created_at=_datetime(raw.get("created_at")) or datetime.now(UTC), updated_at=_datetime(raw.get("updated_at")), billing_address=_address(billing, magento=True), lines=lines, refunds=refunds, fulfillments=fulfillments,
        )

    @staticmethod
    def normalize_refund(raw: object, order_currency: str) -> NormalizedRefund:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        comments = raw.get("comments") or []
        reason = next((
            _text(item.get("comment"), 1000)
            for item in comments
            if isinstance(item, Mapping) and item.get("comment")
        ), None)
        lines = [NormalizedRefundLine(
            external_order_line_id=_text(item.get("order_item_id"), 255),
            quantity=max(1, int(_decimal(item.get("qty")))),
            amount=abs(_decimal(item.get("row_total_incl_tax") or item.get("row_total"))),
        ) for item in (raw.get("items") or []) if isinstance(item, Mapping)]
        return NormalizedRefund(
            external_object_id=_identifier(raw.get("entity_id") or raw.get("increment_id")),
            amount=abs(_decimal(raw.get("grand_total"))),
            currency=str(raw.get("order_currency_code") or order_currency).upper(),
            occurred_at=_datetime(raw.get("created_at") or raw.get("updated_at")) or datetime.now(UTC),
            reason=reason,
            lines=lines,
        )

    @staticmethod
    def normalize_fulfillments(raw: object) -> list[NormalizedFulfillment]:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        shipment_id = _identifier(raw.get("entity_id") or raw.get("increment_id"))
        occurred_at = _datetime(raw.get("created_at") or raw.get("updated_at"))
        line_ids = [
            _identifier(item.get("order_item_id"))
            for item in (raw.get("items") or [])
            if isinstance(item, Mapping) and item.get("order_item_id") is not None
        ]
        tracks = [item for item in (raw.get("tracks") or []) if isinstance(item, Mapping)]
        if not tracks:
            return [NormalizedFulfillment(
                external_object_id=shipment_id,
                status="fulfilled",
                occurred_at=occurred_at,
                external_order_line_ids=line_ids,
            )]
        multiple = len(tracks) > 1
        return [NormalizedFulfillment(
            external_object_id=(
                f"{shipment_id}:track:{_identifier(track.get('entity_id') or track.get('track_number'))}"
                if multiple else shipment_id
            ),
            status="fulfilled",
            occurred_at=occurred_at,
            tracking_company=_text(track.get("title") or track.get("carrier_code"), 160),
            tracking_number=_text(track.get("track_number"), 255),
            external_order_line_ids=line_ids,
        ) for track in tracks]

    def verify_and_parse_webhook(self, credentials: CredentialMaterial, request: CommerceWebhookRequest) -> NormalizedWebhookEvent:
        adobe_signature = request.headers.get("x-adobe-commerce-webhook-signature", "")
        legacy_signature = request.headers.get("x-magento-signature", "")
        if adobe_signature:
            public_key = _required(credentials, "webhook_public_key")
            verified = _safe_adobe_signature(public_key, request.body, adobe_signature)
        elif legacy_signature:
            secret = _required(credentials, "webhook_secret")
            verified = _safe_hmac(secret, request.body, legacy_signature, hexadecimal=True)
        else:
            verified = False
        if not verified:
            raise CommerceProviderError("webhook_verification_failed")
        payload = _webhook_payload(request.body)
        topic = request.headers.get("x-adobe-commerce-event", "") or str(payload.get("event") or "")
        event_id = (
            request.headers.get("x-adobe-commerce-request-id", "")
            or request.headers.get("x-adobe-commerce-webhook-id", "")
            or str(payload.get("event_id") or "")
        )
        if not topic or not event_id:
            raise CommerceProviderError("invalid_response")
        return NormalizedWebhookEvent(external_event_id=event_id, topic=topic, external_object_id=_text(payload.get("entity_id") or payload.get("id"), 255), occurred_at=_datetime(payload.get("updated_at") or payload.get("created_at")), reconciliation_domain=_topic_domain(topic))


class CustomApiCommerceAdapter:
    """Constrained JSON connector: declarative endpoints and field names only."""

    provider = "custom_api"
    capabilities = frozenset({"store_read", "catalog_read", "inventory_read", "customers_read", "orders_read", "incremental_sync", "webhooks"})

    def __init__(self, transport: SafeCommerceHttpClient | None = None) -> None:
        self._http = transport or SafeCommerceHttpClient()

    async def synchronize(self, credentials: CredentialMaterial, request: CommerceSyncRequest, *, idempotency_key: str) -> CommerceSyncPage:
        _ = idempotency_key
        token = _required(credentials, "api_token")
        config = self._configuration(credentials)
        base = request.store_url or credentials.values.get("store_url")
        if not base:
            raise CommerceConfigurationRequiredError()
        endpoint = config["endpoints"].get(request.domain)
        if not isinstance(endpoint, str):
            if request.domain == "store":
                return CommerceSyncPage(domain="store", store=NormalizedStore(external_account_id=request.external_account_id, name="Custom commerce store", public_url=base))
            return CommerceSyncPage(domain=request.domain, complete_snapshot=request.mode in {"initial", "full"})
        url = provider_url(base, endpoint)
        page = int(request.cursor.get("page", 1))
        headers = {str(config.get("auth_header", "Authorization")): f"{config.get('auth_scheme', 'Bearer')} {token}".strip()}
        raw, response_headers = await self._http.request_json("GET", url, headers=headers, params={str(config.get("page_parameter", "page")): page, str(config.get("page_size_parameter", "limit")): min(request.page_size, 100)}, allowed_host=urlsplit(base).hostname)
        data_key = config.get("data_key", "data")
        items = raw.get(data_key) if isinstance(raw, Mapping) else raw
        if not isinstance(items, list):
            raise CommerceProviderError("invalid_response")
        next_page = response_headers.get(str(config.get("next_page_header", "x-next-page")))
        has_more = bool(next_page) or len(items) == min(request.page_size, 100)
        common = dict(domain=request.domain, next_cursor={"page": int(next_page) if next_page and next_page.isdigit() else page + 1} if has_more else {}, has_more=has_more, complete_snapshot=not has_more and request.mode in {"initial", "full"})
        mapping = config.get("mapping", {}).get(request.domain, {})
        if request.domain == "products":
            return CommerceSyncPage(products=tuple(self._product(item, mapping) for item in items), **common)
        if request.domain == "customers":
            return CommerceSyncPage(customers=tuple(self._customer(item, mapping) for item in items), **common)
        if request.domain == "orders":
            return CommerceSyncPage(orders=tuple(self._order(item, mapping) for item in items), **common)
        raise CommerceProviderError("invalid_cursor")

    @staticmethod
    def _configuration(credentials: CredentialMaterial) -> dict[str, Any]:
        try:
            value = json.loads(_required(credentials, "configuration"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CommerceConfigurationRequiredError("invalid_custom_configuration") from None
        if not isinstance(value, dict) or not isinstance(value.get("endpoints"), dict) or len(json.dumps(value)) > 32_768:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        allowed = {"endpoints", "mapping", "data_key", "page_parameter", "page_size_parameter", "next_page_header", "auth_header", "auth_scheme"}
        if set(value) - allowed:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        if set(value["endpoints"]) - {"products", "customers", "orders"}:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        for endpoint in value["endpoints"].values():
            if (
                not isinstance(endpoint, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]{0,1023}", endpoint) is None
                or "//" in endpoint
            ):
                raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        mapping = value.get("mapping", {})
        if not isinstance(mapping, dict) or set(mapping) - {"products", "customers", "orders"}:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        for fields in mapping.values():
            if (
                not isinstance(fields, dict)
                or len(fields) > 40
                or any(
                    not isinstance(key, str)
                    or not isinstance(field, str)
                    or re.fullmatch(r"[A-Za-z0-9_]{1,64}", key) is None
                    or re.fullmatch(r"[A-Za-z0-9_]{1,64}", field) is None
                    for key, field in fields.items()
                )
            ):
                raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        if value.get("auth_header", "Authorization") not in {"Authorization", "X-API-Key"}:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        if value.get("auth_scheme", "Bearer") not in {"Bearer", "Token", ""}:
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        for name, default in (
            ("data_key", "data"),
            ("page_parameter", "page"),
            ("page_size_parameter", "limit"),
            ("next_page_header", "x-next-page"),
        ):
            candidate = value.get(name, default)
            if not isinstance(candidate, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate) is None:
                raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        return value

    @staticmethod
    def _field(raw: object, mapping: Mapping[str, object], name: str, default: object = None) -> object:
        if not isinstance(raw, Mapping):
            raise CommerceProviderError("invalid_response")
        key = mapping.get(name, name)
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key):
            raise CommerceConfigurationRequiredError("invalid_custom_configuration")
        return raw.get(key, default)

    def _product(self, raw: object, mapping: object) -> NormalizedProduct:
        fields = mapping if isinstance(mapping, Mapping) else {}
        quantity = _integer(self._field(raw, fields, "inventory_quantity"))
        return NormalizedProduct(
            external_object_id=_identifier(self._field(raw, fields, "id")), name=str(self._field(raw, fields, "name", "Untitled product"))[:200],
            description=_text(self._field(raw, fields, "description"), 10_000), sku=_text(self._field(raw, fields, "sku"), 100),
            product_url=self._field(raw, fields, "product_url"), price=_decimal(self._field(raw, fields, "price")) if self._field(raw, fields, "price") is not None else None,
            currency=_text(self._field(raw, fields, "currency"), 3), inventory_quantity=quantity, availability=_availability(quantity),
            published=bool(self._field(raw, fields, "published", True)), provider_updated_at=_datetime(self._field(raw, fields, "updated_at")),
        )

    def _customer(self, raw: object, mapping: object) -> NormalizedCustomer:
        fields = mapping if isinstance(mapping, Mapping) else {}
        return NormalizedCustomer(external_object_id=_identifier(self._field(raw, fields, "id")), display_name=_text(self._field(raw, fields, "name"), 160), email=_text(self._field(raw, fields, "email"), 320), phone=_text(self._field(raw, fields, "phone"), 32), provider_updated_at=_datetime(self._field(raw, fields, "updated_at")))

    def _order(self, raw: object, mapping: object) -> NormalizedOrder:
        fields = mapping if isinstance(mapping, Mapping) else {}
        total = _decimal(self._field(raw, fields, "total"))
        external_id = _identifier(self._field(raw, fields, "id"))
        return NormalizedOrder(external_object_id=external_id, order_number=_identifier(self._field(raw, fields, "number", external_id))[:40], currency=str(self._field(raw, fields, "currency", "USD")).upper(), subtotal=_decimal(self._field(raw, fields, "subtotal", total)), total=total, created_at=_datetime(self._field(raw, fields, "created_at")) or datetime.now(UTC), updated_at=_datetime(self._field(raw, fields, "updated_at")))

    def verify_and_parse_webhook(self, credentials: CredentialMaterial, request: CommerceWebhookRequest) -> NormalizedWebhookEvent:
        secret = _required(credentials, "webhook_secret")
        supplied = request.headers.get("x-commerce-signature", "")
        if not supplied or not _safe_hmac(secret, request.body, supplied, hexadecimal=True):
            raise CommerceProviderError("webhook_verification_failed")
        payload = _webhook_payload(request.body)
        return NormalizedWebhookEvent(external_event_id=str(payload.get("event_id") or ""), topic=str(payload.get("topic") or ""), external_object_id=_text(payload.get("object_id"), 255), occurred_at=_datetime(payload.get("occurred_at")), reconciliation_domain=_topic_domain(str(payload.get("topic") or "")))


def _credential_store_host(credentials: CredentialMaterial) -> str | None:
    value = credentials.values.get("store_url")
    if not isinstance(value, str):
        return None
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return None
    return host.rstrip(".").casefold() if host else None


def _address(raw: object, *, shopify: bool = False, magento: bool = False) -> NormalizedAddress | None:
    if not isinstance(raw, Mapping) or not raw:
        return None
    street = raw.get("street") if magento else None
    address1 = street[0] if isinstance(street, list) and street else raw.get("address1")
    address2 = street[1] if isinstance(street, list) and len(street) > 1 else raw.get("address2")
    return NormalizedAddress(
        first_name=_text(raw.get("firstName") if shopify else raw.get("firstname") if magento else raw.get("first_name"), 80),
        last_name=_text(raw.get("lastName") if shopify else raw.get("lastname") if magento else raw.get("last_name"), 80),
        company=_text(raw.get("company"), 160), address1=_text(address1, 255), address2=_text(address2, 255),
        city=_text(raw.get("city"), 120), region=_text(raw.get("province") if shopify else raw.get("region", {}).get("region") if magento and isinstance(raw.get("region"), Mapping) else raw.get("state"), 120),
        postal_code=_text(raw.get("zip") if shopify else raw.get("postcode") if magento else raw.get("postcode") or raw.get("postal_code"), 32),
        country_code=_text(raw.get("countryCodeV2") if shopify else raw.get("country_id") if magento else raw.get("country") or raw.get("country_code"), 2),
        phone=_text(raw.get("telephone") if magento else raw.get("phone"), 32),
    )


def _mapping_id(raw: object) -> str | None:
    return _text(raw.get("id"), 255) if isinstance(raw, Mapping) else None


def _payment_status(value: str) -> str:
    return {
        "paid": "paid", "authorized": "authorized", "pending": "pending", "partially_refunded": "partially_refunded",
        "refunded": "refunded", "voided": "voided", "expired": "failed",
    }.get(value.casefold(), "unknown")


def _fulfillment_status(value: str) -> str:
    return {"fulfilled": "fulfilled", "partial": "partial", "unfulfilled": "unfulfilled", "restocked": "canceled"}.get(value.casefold(), "unknown")


def _fulfillment_record_status(value: object) -> str:
    normalized = str(value or "pending").casefold()
    return normalized if normalized in {"pending", "open", "in_progress", "fulfilled", "canceled", "failed"} else "fulfilled" if normalized == "success" else "pending"


BUILTIN_COMMERCE_CONNECTORS = {
    "shopify": ShopifyCommerceAdapter(),
    "woocommerce": WooCommerceAdapter(),
    "bigcommerce": BigCommerceAdapter(),
    "magento": MagentoCommerceAdapter(),
    "custom_api": CustomApiCommerceAdapter(),
}
