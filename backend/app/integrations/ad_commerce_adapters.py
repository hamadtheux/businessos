from __future__ import annotations

import json
from decimal import Decimal
from typing import Mapping, Sequence
from urllib.parse import quote

from app.exceptions.integration import IntegrationProviderUnavailableError
from app.integrations.ad_commerce_contracts import (
    CommerceDestinationAccount,
    NormalizedProductDestinationInput,
    ProductDestination,
    ProductDestinationStatus,
    ProductGroup,
    ProductIssue,
    ProductWriteResult,
)
from app.integrations.credentials import CredentialMaterial
from app.integrations.oauth_adapters import OAuthHttpClient, _ProviderHttpError


_MERCHANT_ACCOUNTS = "https://merchantapi.googleapis.com/accounts/v1/accounts"
_MERCHANT_DATASOURCES = "https://merchantapi.googleapis.com/datasources/v1"
_MERCHANT_PRODUCTS = "https://merchantapi.googleapis.com/products/v1"
_META_GRAPH = "https://graph.facebook.com"
_MAX_PAGES = 100
_MAX_RESULTS = 10_000


class AdCommerceProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool, uncertain: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        super().__init__(code)


class GoogleMerchantAdapter:
    """Current Merchant API v1 adapter; ProductInput acknowledgement is not approval."""

    provider = "google"

    def __init__(self, *, http: OAuthHttpClient | None = None) -> None:
        self._http = http or OAuthHttpClient()

    async def list_accounts(
        self, credentials: CredentialMaterial,
    ) -> Sequence[CommerceDestinationAccount]:
        values = await self._paginate(
            "GET", _MERCHANT_ACCOUNTS, credentials, collection="accounts",
            params={"pageSize": "500"},
        )
        return tuple(
            CommerceDestinationAccount(
                provider="google",
                external_reference=_resource_id(item, "name", "accounts/"),
                display_name=_display_name(item, "accountName", "name"),
            )
            for item in values
        )

    async def list_destinations(
        self, credentials: CredentialMaterial, *, account_reference: str,
    ) -> Sequence[ProductDestination]:
        account = _numeric_reference(account_reference)
        values = await self._paginate(
            "GET",
            f"{_MERCHANT_DATASOURCES}/accounts/{account}/dataSources",
            credentials,
            collection="dataSources",
            params={"pageSize": "1000"},
        )
        return tuple(
            ProductDestination(
                external_reference=_required_string(item, "name"),
                display_name=_display_name(item, "displayName", "name"),
                managed=_is_managed_data_source(item),
                destination_type="api_data_source",
                parent_reference=account,
            )
            for item in values
            if isinstance(item.get("primaryProductDataSource"), Mapping)
            and isinstance(item["primaryProductDataSource"].get("contentLanguage"), str)
        )

    async def create_managed_destination(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        display_name: str,
        content_language: str,
        feed_label: str,
    ) -> ProductDestination:
        account = _numeric_reference(account_reference)
        body = {
            "displayName": display_name[:160],
            "primaryProductDataSource": {
                "channel": "ONLINE_PRODUCTS",
                "contentLanguage": content_language.lower(),
                "feedLabel": feed_label.upper(),
            },
        }
        value = await self._request(
            "POST", f"{_MERCHANT_DATASOURCES}/accounts/{account}/dataSources",
            credentials, json_body=body,
        )
        return ProductDestination(
            external_reference=_required_string(value, "name"),
            display_name=_display_name(value, "displayName", "name"),
            managed=True,
            destination_type="api_data_source",
            parent_reference=account,
        )

    async def upsert_product(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str,
        product: NormalizedProductDestinationInput,
        idempotency_key: str,
    ) -> ProductWriteResult:
        account = _numeric_reference(account_reference)
        data_source = _google_data_source(destination_reference, account)
        body = google_product_input(product)
        value = await self._request(
            "POST",
            f"{_MERCHANT_PRODUCTS}/accounts/{account}/productInputs:insert",
            credentials,
            params={"dataSource": data_source},
            json_body=body,
            idempotency_key=idempotency_key,
            mutation=True,
        )
        reference = _optional_string(value, "name")
        return ProductWriteResult(
            offer_id=product.offer_id,
            external_product_reference=reference,
            state="submitted",
            acknowledged=True,
        )

    async def archive_product(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str,
        offer_id: str,
        external_product_reference: str | None,
        owned: bool,
        idempotency_key: str,
    ) -> ProductWriteResult:
        if not owned or not external_product_reference:
            return ProductWriteResult(
                offer_id=offer_id,
                external_product_reference=external_product_reference,
                state="attention_required",
                acknowledged=False,
                issues=(ProductIssue(
                    code="external_product_not_managed",
                    message="The product was not removed because this destination does not own it.",
                    severity="warning",
                    resolution="owner_input_required",
                ),),
            )
        account = _numeric_reference(account_reference)
        prefix = f"accounts/{account}/productInputs/"
        if not external_product_reference.startswith(prefix):
            raise AdCommerceProviderError("provider_reference_invalid", retryable=False)
        await self._request(
            "DELETE",
            f"{_MERCHANT_PRODUCTS}/{quote(external_product_reference, safe='/~')}",
            credentials,
            params={"dataSource": _google_data_source(destination_reference, account)},
            idempotency_key=idempotency_key,
            mutation=True,
        )
        return ProductWriteResult(
            offer_id=offer_id,
            external_product_reference=external_product_reference,
            state="archived",
            acknowledged=True,
        )

    async def reconcile_products(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str | None = None,
    ) -> Sequence[ProductDestinationStatus]:
        account = _numeric_reference(account_reference)
        values = await self._paginate(
            "GET", f"{_MERCHANT_PRODUCTS}/accounts/{account}/products",
            credentials, collection="products", params={"pageSize": "1000"},
        )
        statuses: list[ProductDestinationStatus] = []
        for item in values:
            data_sources = item.get("dataSource") or item.get("dataSources")
            if destination_reference:
                if isinstance(data_sources, str) and data_sources != destination_reference:
                    continue
                if isinstance(data_sources, list) and destination_reference not in data_sources:
                    continue
            issues = _google_issues(item)
            statuses.append(ProductDestinationStatus(
                offer_id=_product_offer_id(item),
                external_product_reference=_optional_string(item, "name"),
                state=_google_product_state(item, issues),
                issues=issues,
            ))
        return tuple(statuses)

    async def list_account_issues(
        self, credentials: CredentialMaterial, *, account_reference: str,
    ) -> tuple[ProductIssue, ...]:
        account = _numeric_reference(account_reference)
        values = await self._paginate(
            "GET", f"{_MERCHANT_ACCOUNTS}/{account}/issues",
            credentials, collection="accountIssues", params={"pageSize": "100"},
        )
        result: list[ProductIssue] = []
        for item in values[:50]:
            code = str(item.get("name") or item.get("code") or "account_issue").rsplit("/", 1)[-1][:100]
            message = str(item.get("detail") or item.get("title") or code)[:500]
            severity = "error" if str(item.get("severity", "")).casefold() in {"error", "critical"} else "warning"
            result.append(ProductIssue(
                code=code, message=message, severity=severity,
                resolution=_issue_resolution(code, message),
            ))
        return tuple(result)

    async def upsert_product_group(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        group: ProductGroup,
        idempotency_key: str,
    ) -> ProductGroup:
        # Merchant owns products, while Google Ads owns listing-group filters.
        # The provider-neutral group remains authoritative until campaign mutation.
        _numeric_reference(account_reference)
        return group

    async def _paginate(self, method, url, credentials, *, collection, params):
        result: list[Mapping[str, object]] = []
        token: str | None = None
        for _ in range(_MAX_PAGES):
            query = dict(params)
            if token:
                query["pageToken"] = token
            value = await self._request(method, url, credentials, params=query)
            page = value.get(collection, [])
            if not isinstance(page, list):
                raise AdCommerceProviderError("provider_response_invalid", retryable=False)
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_RESULTS:
                raise AdCommerceProviderError("provider_response_too_large", retryable=False)
            token = _optional_string(value, "nextPageToken")
            if not token:
                return result
        raise AdCommerceProviderError("provider_pagination_limit", retryable=False)

    async def _request(
        self, method, url, credentials, *, params=None, json_body=None,
        idempotency_key=None, mutation=False,
    ):
        headers = {"Authorization": f"Bearer {_access_token(credentials)}"}
        if idempotency_key:
            headers["X-AIBOS-Idempotency-Key"] = idempotency_key
        try:
            return await self._http.request_json(
                method, url, headers=headers, params=params, json_body=json_body,
            )
        except _ProviderHttpError as exc:
            raise _provider_error(exc.status_code, mutation=mutation) from None
        except IntegrationProviderUnavailableError:
            raise AdCommerceProviderError(
                "provider_temporary_failure", retryable=not mutation, uncertain=mutation,
            ) from None


class MetaCatalogAdapter:
    provider = "meta"

    def __init__(self, *, api_version: str = "v26.0", http: OAuthHttpClient | None = None) -> None:
        self._root = f"{_META_GRAPH}/{api_version}"
        self._http = http or OAuthHttpClient()

    async def list_accounts(self, credentials: CredentialMaterial) -> Sequence[CommerceDestinationAccount]:
        token = _access_token(credentials)
        businesses = await self._paginate(
            f"{self._root}/me/businesses", token, collection="data",
            params={"fields": "id,name", "limit": "100"},
        )
        return tuple(
            CommerceDestinationAccount(
                provider="meta", external_reference=_required_string(item, "id"),
                display_name=_display_name(item, "name", "id"),
            ) for item in businesses
        )

    async def list_destinations(
        self, credentials: CredentialMaterial, *, account_reference: str,
    ) -> Sequence[ProductDestination]:
        business = _numeric_reference(account_reference)
        values = await self._paginate(
            f"{self._root}/{business}/owned_product_catalogs",
            _access_token(credentials), collection="data",
            params={"fields": "id,name,vertical", "limit": "100"},
        )
        return tuple(ProductDestination(
            external_reference=_required_string(item, "id"),
            display_name=_display_name(item, "name", "id"),
            managed=False,
            destination_type="catalog",
            parent_reference=business,
        ) for item in values)

    async def upsert_product(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str,
        product: NormalizedProductDestinationInput,
        idempotency_key: str,
    ) -> ProductWriteResult:
        _numeric_reference(account_reference)
        catalog = _numeric_reference(destination_reference)
        data = meta_product_fields(product)
        existing = await self._paginate(
            f"{self._root}/{catalog}/products", _access_token(credentials),
            collection="data",
            params={"fields": "id,retailer_id", "limit": "100"},
        )
        match = next((item for item in existing if item.get("retailer_id") == product.offer_id), None)
        if match is None:
            value = await self._request(
                "POST", f"{self._root}/{catalog}/products", credentials,
                data=data, mutation=True,
            )
            reference = _optional_string(value, "id")
        else:
            reference = _required_string(match, "id")
            value = await self._request(
                "POST", f"{self._root}/{_numeric_reference(reference)}", credentials,
                data=data, mutation=True,
            )
            if value.get("success") is not True and _optional_string(value, "id") is None:
                raise AdCommerceProviderError("external_state_uncertain", retryable=False, uncertain=True)
        return ProductWriteResult(
            offer_id=product.offer_id,
            external_product_reference=reference,
            state="submitted",
            acknowledged=bool(reference),
        )

    async def archive_product(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str,
        offer_id: str,
        external_product_reference: str | None,
        owned: bool,
        idempotency_key: str,
    ) -> ProductWriteResult:
        _numeric_reference(account_reference)
        _numeric_reference(destination_reference)
        if not owned or not external_product_reference:
            return ProductWriteResult(
                offer_id=offer_id, external_product_reference=external_product_reference,
                state="attention_required", acknowledged=False,
                issues=(ProductIssue(
                    code="external_product_not_managed",
                    message="The external catalog item is not managed by AI Business OS.",
                    severity="warning", resolution="owner_input_required",
                ),),
            )
        reference = _numeric_reference(external_product_reference)
        value = await self._request(
            "DELETE", f"{self._root}/{reference}", credentials,
            data={"request_id": idempotency_key[:255]}, mutation=True,
        )
        if value.get("success") is not True:
            raise AdCommerceProviderError("external_state_uncertain", retryable=False, uncertain=True)
        return ProductWriteResult(
            offer_id=offer_id, external_product_reference=reference,
            state="archived", acknowledged=True,
        )

    async def reconcile_products(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        destination_reference: str | None = None,
    ) -> Sequence[ProductDestinationStatus]:
        _numeric_reference(account_reference)
        if destination_reference is None:
            return ()
        catalog = _numeric_reference(destination_reference)
        values = await self._paginate(
            f"{self._root}/{catalog}/products", _access_token(credentials),
            collection="data",
            params={
                "fields": "id,retailer_id,availability,review_status,issues",
                "limit": "100",
            },
        )
        result: list[ProductDestinationStatus] = []
        for item in values:
            issues = _meta_issues(item)
            review = str(item.get("review_status", "")).casefold()
            state = "ineligible" if any(issue.severity == "error" for issue in issues) else (
                "limited" if issues else "eligible" if review in {"approved", "active"} else "processing"
            )
            result.append(ProductDestinationStatus(
                offer_id=_optional_string(item, "retailer_id") or _required_string(item, "id"),
                external_product_reference=_required_string(item, "id"),
                state=state,
                issues=issues,
            ))
        return tuple(result)

    async def upsert_product_group(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        group: ProductGroup,
        idempotency_key: str,
    ) -> ProductGroup:
        _numeric_reference(account_reference)
        catalog = group.rule.get("catalog_reference")
        if not isinstance(catalog, str):
            raise AdCommerceProviderError("catalog_required", retryable=False)
        catalog = _numeric_reference(catalog)
        # Retailer ID filtering is deterministic and never embeds customer data.
        filter_value = {"retailer_id": {"is_any": list(group.offer_ids)}}
        sets = await self._paginate(
            f"{self._root}/{catalog}/product_sets", _access_token(credentials),
            collection="data", params={"fields": "id,name", "limit": "100"},
        )
        existing = next((item for item in sets if item.get("name") == group.name[:100]), None)
        data = {"name": group.name[:100], "filter": json.dumps(filter_value, separators=(",", ":"))}
        if existing is None:
            value = await self._request(
                "POST", f"{self._root}/{catalog}/product_sets", credentials,
                data=data, mutation=True,
            )
            reference = _required_string(value, "id")
        else:
            reference = _required_string(existing, "id")
            value = await self._request(
                "POST", f"{self._root}/{_numeric_reference(reference)}", credentials,
                data=data, mutation=True,
            )
            if value.get("success") is not True:
                raise AdCommerceProviderError("external_state_uncertain", retryable=False, uncertain=True)
        return ProductGroup(
            external_key=group.external_key, name=group.name, rule=group.rule,
            offer_ids=group.offer_ids, external_reference=reference,
        )

    async def _paginate(self, url, token, *, collection, params):
        result: list[Mapping[str, object]] = []
        next_url: str | None = url
        query = {**params, "access_token": token}
        for _ in range(_MAX_PAGES):
            value = await self._request("GET", next_url, None, params=query)
            page = value.get(collection, [])
            if not isinstance(page, list):
                raise AdCommerceProviderError("provider_response_invalid", retryable=False)
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_RESULTS:
                raise AdCommerceProviderError("provider_response_too_large", retryable=False)
            paging = value.get("paging")
            candidate = paging.get("next") if isinstance(paging, Mapping) else None
            if not isinstance(candidate, str) or not candidate.startswith(f"{_META_GRAPH}/"):
                return result
            next_url, query = candidate, {}
        raise AdCommerceProviderError("provider_pagination_limit", retryable=False)

    async def _request(self, method, url, credentials, *, params=None, data=None, mutation=False):
        if credentials is not None:
            data = {**(data or {}), "access_token": _access_token(credentials)}
        try:
            return await self._http.request_json(method, url, params=params, data=data)
        except _ProviderHttpError as exc:
            raise _provider_error(exc.status_code, mutation=mutation) from None
        except IntegrationProviderUnavailableError:
            raise AdCommerceProviderError(
                "provider_temporary_failure", retryable=not mutation, uncertain=mutation,
            ) from None


def google_product_input(product: NormalizedProductDestinationInput) -> dict[str, object]:
    attributes: dict[str, object] = {
        "title": product.title,
        "description": product.description,
        "link": product.link,
        "imageLink": product.image_link,
        "availability": product.availability,
        "condition": product.condition,
        "price": _google_price(product.price, product.currency),
    }
    optional = {
        "additionalImageLinks": list(product.additional_image_links) or None,
        "salePrice": _google_price(product.sale_price, product.currency) if product.sale_price is not None else None,
        "brand": product.brand,
        "gtin": product.gtin,
        "mpn": product.mpn,
        "googleProductCategory": product.google_product_category,
        "productType": product.product_type,
        "itemGroupId": product.item_group_id,
    }
    attributes.update({key: value for key, value in optional.items() if value is not None})
    labels = [value for key, value in product.custom_labels.items() if key != "feed_label"]
    for index, value in enumerate(labels[:5]):
        attributes[f"customLabel{index}"] = value
    return {
        "offerId": product.offer_id,
        "contentLanguage": product.content_language,
        "feedLabel": product.feed_label or product.currency,
        "productAttributes": attributes,
    }


def meta_product_fields(product: NormalizedProductDestinationInput) -> dict[str, str]:
    values = {
        "retailer_id": product.offer_id,
        "name": product.title,
        "description": product.description,
        "availability": product.availability,
        "condition": product.condition,
        "price": str(int(product.price * 100)),
        "currency": product.currency,
        "url": product.link,
        "image_url": product.image_link,
    }
    optional = {
        "sale_price": str(int(product.sale_price * 100)) if product.sale_price is not None else None,
        "brand": product.brand,
        "gtin": product.gtin,
        "manufacturer_part_number": product.mpn,
        "retailer_product_group_id": product.item_group_id,
    }
    values.update({key: value for key, value in optional.items() if value is not None})
    return values


def _google_price(amount: Decimal, currency: str) -> dict[str, object]:
    return {"amountMicros": str(int(amount * 1_000_000)), "currencyCode": currency}


def _access_token(credentials: CredentialMaterial) -> str:
    value = credentials.values.get("access_token")
    if not value:
        raise AdCommerceProviderError("authorization_required", retryable=False)
    return value


def _provider_error(status: int, *, mutation: bool) -> AdCommerceProviderError:
    if status in {401, 403}:
        return AdCommerceProviderError("reauthorization_required", retryable=False)
    if status == 429:
        return AdCommerceProviderError("rate_limited", retryable=not mutation, uncertain=mutation)
    if status >= 500:
        return AdCommerceProviderError("provider_temporary_failure", retryable=not mutation, uncertain=mutation)
    if status in {400, 404, 409, 422}:
        return AdCommerceProviderError("provider_validation_error", retryable=False)
    return AdCommerceProviderError("provider_failure", retryable=False)


def _numeric_reference(value: str) -> str:
    normalized = value.removeprefix("act_")
    if not normalized.isdigit() or len(normalized) > 32:
        raise AdCommerceProviderError("provider_reference_invalid", retryable=False)
    return value if value.startswith("act_") else normalized


def _google_data_source(value: str, account: str) -> str:
    if value.startswith(f"accounts/{account}/dataSources/") and value.rsplit("/", 1)[-1].isdigit():
        return value
    if value.isdigit():
        return f"accounts/{account}/dataSources/{value}"
    raise AdCommerceProviderError("data_source_invalid", retryable=False)


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result or len(result) > 2048:
        raise AdCommerceProviderError("provider_response_invalid", retryable=False)
    return result


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    result = value.get(key)
    return result if isinstance(result, str) and result and len(result) <= 2048 else None


def _resource_id(item: Mapping[str, object], key: str, prefix: str) -> str:
    value = _required_string(item, key)
    if not value.startswith(prefix):
        raise AdCommerceProviderError("provider_response_invalid", retryable=False)
    return _numeric_reference(value.removeprefix(prefix))


def _display_name(item: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = _optional_string(item, key)
        if value:
            return value[:160]
    raise AdCommerceProviderError("provider_response_invalid", retryable=False)


def _is_managed_data_source(item: Mapping[str, object]) -> bool:
    name = (_optional_string(item, "displayName") or "").casefold()
    return name.startswith("ai business os")


def _product_offer_id(item: Mapping[str, object]) -> str:
    offer = _optional_string(item, "offerId")
    if offer:
        return offer
    name = _required_string(item, "name")
    return name.rsplit("~", 1)[-1]


def _google_issues(item: Mapping[str, object]) -> tuple[ProductIssue, ...]:
    status = item.get("productStatus")
    raw = status.get("itemLevelIssues", []) if isinstance(status, Mapping) else item.get("issues", [])
    if not isinstance(raw, list):
        return ()
    result: list[ProductIssue] = []
    for issue in raw[:50]:
        if not isinstance(issue, Mapping):
            continue
        code = str(issue.get("code") or issue.get("type") or "provider_issue")[:100]
        message = str(issue.get("description") or issue.get("detail") or code)[:500]
        severity = "error" if str(issue.get("severity", "")).casefold() in {"error", "disapproved"} else "warning"
        result.append(ProductIssue(
            code=code, message=message, severity=severity,
            resolution=_issue_resolution(code, message),
            provider_reference=_optional_string(issue, "documentation"),
            attribute=_optional_string(issue, "attribute"),
        ))
    return tuple(result)


def _meta_issues(item: Mapping[str, object]) -> tuple[ProductIssue, ...]:
    raw = item.get("issues", [])
    if not isinstance(raw, list):
        return ()
    result: list[ProductIssue] = []
    for issue in raw[:50]:
        if not isinstance(issue, Mapping):
            continue
        code = str(issue.get("type") or issue.get("code") or "provider_issue")[:100]
        message = str(issue.get("message") or issue.get("description") or code)[:500]
        result.append(ProductIssue(
            code=code, message=message,
            severity="error" if str(issue.get("severity", "")).casefold() == "error" else "warning",
            resolution=_issue_resolution(code, message),
        ))
    return tuple(result)


def _issue_resolution(code: str, message: str):
    value = f"{code} {message}".casefold()
    if "policy" in value:
        return "provider_policy_review_required"
    if "mismatch" in value and any(
        term in value for term in ("price", "availability", "landing", "link")
    ):
        return "store_source_update_required"
    if any(term in value for term in ("gtin", "mpn", "brand", "price", "availability")):
        return "owner_input_required"
    if any(term in value for term in ("image", "landing", "link", "source")):
        return "store_source_update_required"
    return "provider_policy_review_required"


def _google_product_state(item, issues):
    if item.get("archived") is True:
        return "archived"
    destinations = item.get("destinationStatuses")
    status = item.get("productStatus")
    if isinstance(status, Mapping):
        destinations = status.get("destinationStatuses")
    if isinstance(destinations, list):
        approved = 0
        pending = 0
        disapproved = 0
        for destination in destinations:
            if not isinstance(destination, Mapping):
                continue
            approved += _country_count(destination.get("approvedCountries"))
            pending += _country_count(destination.get("pendingCountries"))
            disapproved += _country_count(destination.get("disapprovedCountries"))
        if approved:
            return "limited" if issues or pending or disapproved else "eligible"
        if pending:
            return "processing"
        if disapproved:
            return "ineligible"
    if any(issue.severity == "error" for issue in issues):
        return "ineligible"
    return "limited" if issues else "processing"


def _country_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0
