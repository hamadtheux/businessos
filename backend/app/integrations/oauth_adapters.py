from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence
from urllib.parse import quote, urlencode

import httpx

from app.core.config import Settings
from app.exceptions.integration import IntegrationProviderUnavailableError
from app.integrations.contracts import (
    AuthorizationExchange,
    AuthorizationRequest,
    ConnectionHealthResult,
    CredentialRefreshResult,
    ExternalCalendarEvent,
    ExternalIdentity,
    ExternalResource,
    NormalizedAdPerformance,
    NormalizedCampaignStatus,
    NormalizedIntegrationEvent,
)
from app.integrations.credentials import CredentialMaterial
from app.integrations.providers import OAUTH_PROVIDER_ENDPOINTS
from app.integrations.registry import CONNECTOR_REGISTRY


_MAX_PROVIDER_RESPONSE_BYTES = 1_048_576
_GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_CALENDAR = "https://www.googleapis.com/calendar/v3"
_GOOGLE_ADS_ROOT = "https://googleads.googleapis.com"
_META_ROOT = "https://graph.facebook.com"
_MAX_PROVIDER_PAGES = 100
_MAX_PROVIDER_ITEMS = 100_000


class _ProviderHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class OAuthHttpClient:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout = timeout_seconds

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers=dict(headers or {}),
                    params=dict(params or {}),
                    data=dict(data or {}) if data is not None else None,
                    json=dict(json_body or {}) if json_body is not None else None,
                )
            if len(response.content) > _MAX_PROVIDER_RESPONSE_BYTES:
                raise _ProviderHttpError(502)
            if not 200 <= response.status_code < 300:
                raise _ProviderHttpError(response.status_code)
            value = response.json() if response.content else {}
            if not isinstance(value, dict):
                raise _ProviderHttpError(502)
            return value
        except _ProviderHttpError:
            raise
        except Exception:
            raise IntegrationProviderUnavailableError(
                "provider_unavailable"
            ) from None


class ConfiguredOAuthConnector:
    """Production OAuth/read adapter shared by one configured connector."""

    def __init__(
        self,
        *,
        connector_type: str,
        provider: str,
        client_id: str,
        client_secret: str,
        configuration: Settings,
        http: OAuthHttpClient | None = None,
    ) -> None:
        self.connector_type = connector_type
        self._provider = provider
        self._client_id = client_id
        self._client_secret = client_secret
        self._configuration = configuration
        self._http = http or OAuthHttpClient(
            timeout_seconds=configuration.connector_dispatch_timeout_seconds
        )

    async def build_authorization_url(
        self, request: AuthorizationRequest
    ) -> str:
        endpoint = OAUTH_PROVIDER_ENDPOINTS[self._provider].authorization_endpoint
        values = {
            "client_id": self._client_id,
            "redirect_uri": request.redirect_uri,
            "response_type": "code",
            "scope": " ".join(request.scopes),
            "state": request.state,
            "code_challenge": request.code_challenge,
            "code_challenge_method": "S256",
        }
        if self._provider == "google":
            values.update(
                {"access_type": "offline", "include_granted_scopes": "true", "prompt": "consent"}
            )
        return f"{endpoint}?{urlencode(values)}"

    async def exchange_authorization_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> AuthorizationExchange:
        endpoint = OAUTH_PROVIDER_ENDPOINTS[self._provider].token_endpoint
        response = await self._http.request_json(
            "POST",
            endpoint,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if self._provider == "meta":
            response = await self._exchange_long_lived_meta_token(response)
        material = _token_material(response)
        scopes = _scopes(response.get("scope")) or CONNECTOR_REGISTRY[
            self.connector_type
        ].oauth_scopes
        return AuthorizationExchange(
            credentials=material,
            granted_scopes=tuple(scopes),
        )

    async def refresh_credentials(
        self, credentials: CredentialMaterial
    ) -> CredentialRefreshResult:
        try:
            if self._provider == "google":
                refresh_token = credentials.values.get("refresh_token")
                if not refresh_token:
                    return CredentialRefreshResult(status="reauth_required")
                response = await self._http.request_json(
                    "POST",
                    OAUTH_PROVIDER_ENDPOINTS["google"].token_endpoint,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                values = dict(_token_material(response).values)
                values["refresh_token"] = refresh_token
                return CredentialRefreshResult(
                    status="refreshed",
                    credentials=CredentialMaterial(values=values),
                )
            response = await self._exchange_long_lived_meta_token(
                {"access_token": credentials.values.get("access_token", "")}
            )
            return CredentialRefreshResult(
                status="refreshed", credentials=_token_material(response)
            )
        except _ProviderHttpError as exc:
            if exc.status_code in {400, 401, 403}:
                return CredentialRefreshResult(
                    status="reauth_required", failure_code="authorization_expired"
                )
            return CredentialRefreshResult(
                status="temporary_failure", failure_code="provider_unavailable"
            )
        except IntegrationProviderUnavailableError:
            return CredentialRefreshResult(
                status="temporary_failure", failure_code="provider_unavailable"
            )

    async def revoke_credentials(self, credentials: CredentialMaterial) -> None:
        token = _access_token(credentials)
        try:
            if self._provider == "google":
                await self._http.request_json(
                    "POST",
                    OAUTH_PROVIDER_ENDPOINTS["google"].revocation_endpoint or "",
                    data={"token": token},
                )
            else:
                await self._http.request_json(
                    "DELETE",
                    f"{self._meta_root()}/me/permissions",
                    params={"access_token": token},
                )
        except _ProviderHttpError as exc:
            if exc.status_code not in {400, 401, 404}:
                raise IntegrationProviderUnavailableError(
                    "provider_unavailable"
                ) from None

    async def get_identity(
        self, credentials: CredentialMaterial
    ) -> ExternalIdentity:
        token = _access_token(credentials)
        if self._provider == "google":
            value = await self._http.request_json(
                "GET", _GOOGLE_USERINFO, headers=_bearer(token)
            )
            reference = _required_string(value, "sub")
            name = _optional_string(value, "name") or _optional_string(value, "email") or "Google account"
        else:
            value = await self._http.request_json(
                "GET",
                f"{self._meta_root()}/me",
                params={"fields": "id,name", "access_token": token},
            )
            reference = _required_string(value, "id")
            name = _optional_string(value, "name") or "Meta account"
        return ExternalIdentity(reference, name[:160])

    async def list_resources(
        self, credentials: CredentialMaterial
    ) -> Sequence[ExternalResource]:
        token = _access_token(credentials)
        if self.connector_type == "gmail":
            identity = await self.get_identity(credentials)
            return [ExternalResource("mailbox", identity.external_account_reference, identity.display_name)]
        if self.connector_type == "google_calendar":
            value = await self._http.request_json(
                "GET",
                f"{_GOOGLE_CALENDAR}/users/me/calendarList",
                headers=_bearer(token),
                params={"maxResults": "100"},
            )
            return _resources(value, "calendar", name_key="summary")
        if self.connector_type == "google_ads":
            developer_token = self._google_ads_developer_token()
            value = await self._http.request_json(
                "GET",
                f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers:listAccessibleCustomers",
                headers={**_bearer(token), "developer-token": developer_token},
            )
            names = value.get("resourceNames", [])
            if not isinstance(names, list):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            resources = [
                ExternalResource("google_ads_customer", item.rsplit("/", 1)[-1], item)
                for item in names[:100]
                if isinstance(item, str) and item
            ]
            for resource in list(resources):
                customer = resource.external_reference
                links = await self._google_search_rows(
                    f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers/{customer}/googleAds:search",
                    headers={**_bearer(token), "developer-token": developer_token},
                    query="SELECT product_link.product_link_id, product_link.type, product_link.merchant_center.merchant_center_id FROM product_link",
                )
                for row in links:
                    link = row.get("productLink")
                    merchant_link = link.get("merchantCenter") if isinstance(link, Mapping) else None
                    merchant_id = merchant_link.get("merchantCenterId") if isinstance(merchant_link, Mapping) else None
                    if isinstance(merchant_id, str) and merchant_id:
                        resources.append(ExternalResource(
                            "google_merchant_ads_link", f"{customer}:{merchant_id}",
                            f"Ads {customer} ↔ Merchant {merchant_id}",
                            parent_reference=customer,
                            metadata={"merchant_account": merchant_id},
                        ))
                conversions = await self._google_search_rows(
                    f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers/{customer}/googleAds:search",
                    headers={**_bearer(token), "developer-token": developer_token},
                    query="SELECT conversion_action.resource_name, conversion_action.name, conversion_action.category, conversion_action.status FROM conversion_action WHERE conversion_action.status = 'ENABLED' AND conversion_action.category = 'PURCHASE'",
                )
                for row in conversions:
                    conversion = row.get("conversionAction")
                    if isinstance(conversion, Mapping):
                        reference = _required_string(conversion, "resourceName")
                        resources.append(ExternalResource(
                            "google_conversion_action", reference,
                            (_optional_string(conversion, "name") or reference)[:160],
                            parent_reference=customer,
                        ))
            merchant = await self._google_get_paged_items(
                "https://merchantapi.googleapis.com/accounts/v1/accounts",
                headers=_bearer(token), params={"pageSize": "100"}, item_key="accounts",
            )
            for item in merchant:
                name = _required_string(item, "name")
                account = name.rsplit("/", 1)[-1]
                resources.append(ExternalResource(
                    "google_merchant_account", account,
                    (_optional_string(item, "accountName") or name)[:160],
                ))
                data_sources = await self._google_get_paged_items(
                    f"https://merchantapi.googleapis.com/datasources/v1/accounts/{account}/dataSources",
                    headers=_bearer(token), params={"pageSize": "100"}, item_key="dataSources",
                )
                for source in data_sources:
                    source_name = _required_string(source, "name")
                    resources.append(ExternalResource(
                        "google_merchant_data_source", source_name,
                        (_optional_string(source, "displayName") or source_name)[:160],
                        parent_reference=account,
                        metadata={"managed": str((_optional_string(source, "displayName") or "").casefold().startswith("ai business os")).lower()},
                    ))
            return resources[:100]
        return await self._list_meta_resources(token)

    async def health_check(
        self, credentials: CredentialMaterial
    ) -> ConnectionHealthResult:
        try:
            await self.get_identity(credentials)
            return ConnectionHealthResult(health="healthy")
        except _ProviderHttpError as exc:
            if exc.status_code in {400, 401, 403}:
                return ConnectionHealthResult(
                    health="reauth_required", failure_code="authorization_expired"
                )
            return ConnectionHealthResult(
                health="degraded", failure_code="provider_unavailable"
            )
        except IntegrationProviderUnavailableError:
            return ConnectionHealthResult(
                health="degraded", failure_code="provider_unavailable"
            )

    async def normalize_webhooks(
        self, payload: Mapping[str, object]
    ) -> Sequence[NormalizedIntegrationEvent]:
        """
        Normalize one verified provider webhook into one or more canonical events.

        Most connectors currently produce exactly one normalized event.

        WhatsApp Cloud API delivery callbacks may contain multiple independent
        entry[].changes[].value.statuses[] records in one signed HTTP request.
        Each evidenced status must become its own durable integration event so
        delivery reconciliation cannot silently drop provider evidence.
        """
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > self._configuration.integration_webhook_max_bytes:
            raise IntegrationProviderUnavailableError("provider_unavailable")

        if self.connector_type == "whatsapp_business":
            status_events = _normalize_whatsapp_status_events(payload)
            if status_events:
                return status_events

        event_id = _event_reference(payload, serialized)
        event_type = _event_type(self.connector_type, payload)
        occurred_at = _event_time(payload)
        safe_payload = {
            key: value
            for key, value in payload.items()
            if isinstance(key, str)
            and len(key) <= 64
            and isinstance(value, (str, int, bool, type(None)))
            and (not isinstance(value, str) or len(value) <= 2_000)
        }
        return (
            NormalizedIntegrationEvent(
                external_event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                safe_payload=safe_payload,
            ),
        )

    async def normalize_webhook(
        self, payload: Mapping[str, object]
    ) -> NormalizedIntegrationEvent:
        """
        Backward-compatible singular normalization entry point.

        Callers that need complete provider webhook semantics should use
        normalize_webhooks(). Refuse to silently discard additional events.
        """
        events = tuple(await self.normalize_webhooks(payload))
        if len(events) != 1:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        return events[0]

    async def list_calendar_events(
        self,
        credentials: CredentialMaterial,
        *,
        calendar_reference: str,
        starts_at: datetime,
        ends_at: datetime,
    ) -> Sequence[ExternalCalendarEvent]:
        if self.connector_type != "google_calendar":
            raise IntegrationProviderUnavailableError("provider_unavailable")
        value = await self._http.request_json(
            "GET",
            f"{_GOOGLE_CALENDAR}/calendars/{quote(calendar_reference, safe='')}/events",
            headers=_bearer(_access_token(credentials)),
            params={
                "timeMin": starts_at.isoformat(),
                "timeMax": ends_at.isoformat(),
                "singleEvents": "true",
                "maxResults": "100",
            },
        )
        result: list[ExternalCalendarEvent] = []
        for item in _items(value):
            start = _nested_datetime(item, "start")
            end = _nested_datetime(item, "end")
            updated = _parse_datetime(_required_string(item, "updated"))
            if start and end:
                result.append(
                    ExternalCalendarEvent(
                        external_event_id=_required_string(item, "id"),
                        external_calendar_reference=calendar_reference,
                        title=(_optional_string(item, "summary") or "Busy")[:200],
                        starts_at=start,
                        ends_at=end,
                        status="canceled" if item.get("status") == "cancelled" else "confirmed",
                        updated_at=updated,
                    )
                )
        return result

    async def read_campaign_performance(
        self,
        credentials: CredentialMaterial,
        *,
        account_reference: str,
        period_start: date,
        period_end: date,
    ) -> Sequence[NormalizedAdPerformance]:
        if self.connector_type == "google_ads":
            return await self._google_performance(
                credentials, account_reference, period_start, period_end
            )
        if self.connector_type == "meta_ads":
            return await self._meta_performance(
                credentials, account_reference, period_start, period_end
            )
        raise IntegrationProviderUnavailableError("provider_unavailable")

    async def read_campaign_status(
        self, credentials: CredentialMaterial, *, account_reference: str,
        campaign_reference: str,
    ) -> NormalizedCampaignStatus:
        if self.connector_type == "google_ads":
            campaign_id = campaign_reference.rsplit("/", 1)[-1]
            if not campaign_id.isdigit():
                raise IntegrationProviderUnavailableError("provider_unavailable")
            value = await self._http.request_json(
                "POST",
                f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers/{account_reference}/googleAds:search",
                headers={**_bearer(_access_token(credentials)), "developer-token": self._google_ads_developer_token()},
                json_body={"query": (
                    "SELECT campaign.id, campaign.status, campaign.primary_status, "
                    "campaign.primary_status_reasons FROM campaign WHERE campaign.id = " + campaign_id
                ), "pageSize": 1},
            )
            rows = _mapping_items(value, "results")
            if not rows or not isinstance(rows[0].get("campaign"), Mapping):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            campaign = rows[0]["campaign"]
            provider_status = str(campaign.get("primaryStatus") or campaign.get("status") or "UNKNOWN")
            reasons = campaign.get("primaryStatusReasons", [])
            issues = tuple(str(item)[:160] for item in reasons[:20]) if isinstance(reasons, list) else ()
            normalized = {
                "ELIGIBLE": "active", "LIMITED": "attention_required", "PAUSED": "paused",
                "REMOVED": "completed", "ENDED": "completed", "PENDING": "provider_pending",
                "MISCONFIGURED": "attention_required", "NOT_ELIGIBLE": "attention_required",
            }.get(provider_status.upper(), "unknown_external_state")
            return NormalizedCampaignStatus(campaign_reference, normalized, provider_status[:64], issues)
        if self.connector_type == "meta_ads":
            value = await self._http.request_json(
                "GET", f"{self._meta_root()}/{campaign_reference.rsplit('/', 1)[-1]}",
                params={"fields": "id,status,effective_status,issues_info", "access_token": _access_token(credentials)},
            )
            provider_status = str(value.get("effective_status") or value.get("status") or "UNKNOWN")
            raw_issues = value.get("issues_info", [])
            issues = tuple(str(item.get("error_summary") or item.get("error_message") or "provider issue")[:160] for item in raw_issues[:20] if isinstance(item, Mapping)) if isinstance(raw_issues, list) else ()
            normalized = {
                "ACTIVE": "active", "PAUSED": "paused", "CAMPAIGN_PAUSED": "paused",
                "PENDING_REVIEW": "provider_pending", "IN_PROCESS": "provider_pending",
                "COMPLETED": "completed", "ARCHIVED": "completed", "DELETED": "completed",
                "DISAPPROVED": "attention_required", "WITH_ISSUES": "attention_required",
                "ERROR": "failed",
            }.get(provider_status.upper(), "unknown_external_state")
            return NormalizedCampaignStatus(campaign_reference, normalized, provider_status[:64], issues)
        raise IntegrationProviderUnavailableError("provider_unavailable")

    async def _exchange_long_lived_meta_token(
        self, response: Mapping[str, object]
    ) -> Mapping[str, object]:
        token = _required_string(response, "access_token")
        return await self._http.request_json(
            "GET",
            OAUTH_PROVIDER_ENDPOINTS["meta"].token_endpoint,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "fb_exchange_token": token,
            },
        )

    async def _list_meta_resources(self, token: str) -> Sequence[ExternalResource]:
        root = self._meta_root()
        resources: list[ExternalResource] = []
        if self.connector_type in {"facebook", "instagram"}:
            pages = await self._meta_paged_items(
                f"{root}/me/accounts",
                token=token,
                params={
                    "fields": "id,name,instagram_business_account{id,username}",
                    "limit": "100",
                },
            )
            for item in pages:
                page_id = _required_string(item, "id")
                resources.append(
                    ExternalResource("facebook_page", page_id, (_optional_string(item, "name") or page_id)[:160])
                )
                instagram = item.get("instagram_business_account")
                if self.connector_type == "instagram" and isinstance(instagram, dict):
                    reference = _required_string(instagram, "id")
                    resources.append(
                        ExternalResource(
                            "instagram_account",
                            reference,
                            (_optional_string(instagram, "username") or reference)[:160],
                            parent_reference=page_id,
                        )
                    )
            return resources[:100]
        businesses = await self._meta_paged_items(
            f"{root}/me/businesses", token=token, params={"limit": "100"}
        )
        for business in businesses:
            reference = _required_string(business, "id")
            resources.append(
                ExternalResource("meta_business", reference, (_optional_string(business, "name") or reference)[:160])
            )
        if self.connector_type == "meta_ads":
            accounts = await self._meta_paged_items(
                f"{root}/me/adaccounts",
                token=token, params={"fields": "id,name,account_id", "limit": "100"},
            )
            resources.extend(
                ExternalResource(
                    "ad_account",
                    _required_string(item, "id"),
                    (_optional_string(item, "name") or _required_string(item, "id"))[:160],
                )
                for item in accounts
            )
            pages = await self._meta_paged_items(
                f"{root}/me/accounts", token=token,
                params={"fields": "id,name", "limit": "100"},
            )
            resources.extend(ExternalResource(
                "facebook_page", _required_string(item, "id"),
                (_optional_string(item, "name") or _required_string(item, "id"))[:160],
            ) for item in pages)
            for business in [item for item in resources if item.resource_type == "meta_business"]:
                catalogs = await self._meta_paged_items(
                    f"{root}/{business.external_reference}/owned_product_catalogs",
                    token=token, params={"fields": "id,name", "limit": "100"},
                )
                resources.extend(ExternalResource(
                    "meta_catalog", _required_string(item, "id"),
                    (_optional_string(item, "name") or _required_string(item, "id"))[:160],
                    parent_reference=business.external_reference,
                ) for item in catalogs)
                datasets = await self._meta_paged_items(
                    f"{root}/{business.external_reference}/owned_pixels",
                    token=token, params={"fields": "id,name", "limit": "100"},
                )
                resources.extend(ExternalResource(
                    "conversion_dataset", _required_string(item, "id"),
                    (_optional_string(item, "name") or _required_string(item, "id"))[:160],
                    parent_reference=business.external_reference,
                ) for item in datasets)
        elif self.connector_type == "whatsapp_business":
            for business in list(resources):
                accounts = await self._meta_paged_items(
                    f"{root}/{business.external_reference}/owned_whatsapp_business_accounts",
                    token=token, params={"fields": "id,name", "limit": "100"},
                )
                for item in accounts:
                    reference = _required_string(item, "id")
                    resources.append(
                        ExternalResource(
                            "whatsapp_business_account",
                            reference,
                            (_optional_string(item, "name") or reference)[:160],
                            parent_reference=business.external_reference,
                        )
                    )
                    phone_numbers = await self._meta_paged_items(
                        f"{root}/{reference}/phone_numbers",
                        token=token,
                        params={
                            "fields": "id,display_phone_number,verified_name",
                            "limit": "100",
                        },
                    )
                    for phone in phone_numbers:
                        phone_reference = _required_string(phone, "id")
                        resources.append(
                            ExternalResource(
                                "phone_number",
                                phone_reference,
                                (
                                    _optional_string(phone, "verified_name")
                                    or _optional_string(phone, "display_phone_number")
                                    or phone_reference
                                )[:160],
                                parent_reference=reference,
                            )
                        )
        return resources[:100]

    async def _google_performance(self, credentials, account, start, end):
        query = (
            "SELECT campaign.id, segments.date, metrics.cost_micros, "
            "metrics.impressions, metrics.clicks, metrics.conversions, "
            "metrics.conversions_value, segments.product_item_id FROM shopping_performance_view WHERE segments.date "
            f"BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        rows = await self._google_search_rows(
            f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers/{account}/googleAds:search",
            headers={
                **_bearer(_access_token(credentials)),
                "developer-token": self._google_ads_developer_token(),
            },
            query=query,
        )
        result: list[NormalizedAdPerformance] = []
        for row in rows:
            campaign = row.get("campaign")
            segments = row.get("segments")
            metrics = row.get("metrics")
            if not all(isinstance(item, dict) for item in (campaign, segments, metrics)):
                continue
            day = date.fromisoformat(_required_string(segments, "date"))
            result.append(
                NormalizedAdPerformance(
                    external_campaign_reference=str(campaign.get("id", "")),
                    period_start=day,
                    period_end=day,
                    spend=Decimal(str(metrics.get("costMicros", 0))) / Decimal(1_000_000),
                    impressions=int(metrics.get("impressions", 0)),
                    clicks=int(metrics.get("clicks", 0)),
                    conversions=int(Decimal(str(metrics.get("conversions", 0)))),
                    revenue=Decimal(str(metrics.get("conversionsValue", 0))),
                    external_product_reference=_optional_string(segments, "productItemId"),
                )
            )
        return result

    async def _meta_performance(self, credentials, account, start, end):
        items = await self._meta_paged_items(
            f"{self._meta_root()}/{account}/insights",
            token=_access_token(credentials),
            params={
                "fields": "campaign_id,spend,impressions,clicks,reach,actions,action_values,product_id",
                "level": "campaign",
                "time_increment": "1",
                "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
                "limit": "500",
                "breakdowns": "product_id",
            },
        )
        result: list[NormalizedAdPerformance] = []
        for item in items:
            day_start = date.fromisoformat(_required_string(item, "date_start"))
            day_end = date.fromisoformat(_required_string(item, "date_stop"))
            result.append(
                NormalizedAdPerformance(
                    external_campaign_reference=_required_string(item, "campaign_id"),
                    period_start=day_start,
                    period_end=day_end,
                    spend=_decimal(item.get("spend")),
                    impressions=int(item.get("impressions", 0)),
                    clicks=int(item.get("clicks", 0)),
                    conversions=_meta_action_total(item.get("actions"), "purchase"),
                    revenue=_meta_action_value(item.get("action_values"), "purchase"),
                    reach=int(item.get("reach", 0)),
                    leads=_meta_action_total(item.get("actions"), "lead"),
                    external_product_reference=_optional_string(item, "product_id"),
                )
            )
        return result

    async def _google_search_rows(
        self, url: str, *, headers: Mapping[str, str], query: str,
    ) -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        token: str | None = None
        for _ in range(_MAX_PROVIDER_PAGES):
            body = {"query": query, "pageSize": 10_000}
            if token:
                body["pageToken"] = token
            value = await self._http.request_json(
                "POST", url, headers=headers, json_body=body,
            )
            page = value.get("results", [])
            if not isinstance(page, list):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_PROVIDER_ITEMS:
                raise IntegrationProviderUnavailableError("provider_response_too_large")
            token = _optional_string(value, "nextPageToken")
            if not token:
                return result
        raise IntegrationProviderUnavailableError("provider_pagination_limit")

    async def _google_get_paged_items(
        self, url: str, *, headers: Mapping[str, str],
        params: Mapping[str, str], item_key: str,
    ) -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        token: str | None = None
        for _ in range(_MAX_PROVIDER_PAGES):
            query = dict(params)
            if token:
                query["pageToken"] = token
            value = await self._http.request_json(
                "GET", url, headers=headers, params=query,
            )
            page = value.get(item_key, [])
            if not isinstance(page, list):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_PROVIDER_ITEMS:
                raise IntegrationProviderUnavailableError("provider_response_too_large")
            token = _optional_string(value, "nextPageToken")
            if not token:
                return result
        raise IntegrationProviderUnavailableError("provider_pagination_limit")

    async def _meta_paged_items(
        self, url: str, *, token: str, params: Mapping[str, str],
    ) -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        next_url = url
        query: Mapping[str, str] = {**params, "access_token": token}
        for _ in range(_MAX_PROVIDER_PAGES):
            value = await self._http.request_json("GET", next_url, params=query)
            page = value.get("data", [])
            if not isinstance(page, list):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_PROVIDER_ITEMS:
                raise IntegrationProviderUnavailableError("provider_response_too_large")
            paging = value.get("paging")
            candidate = paging.get("next") if isinstance(paging, Mapping) else None
            if not isinstance(candidate, str):
                return result
            if not candidate.startswith(f"{_META_ROOT}/{self._configuration.meta_graph_api_version}/"):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            next_url = candidate
            query = {}
        raise IntegrationProviderUnavailableError("provider_pagination_limit")

    def _meta_root(self) -> str:
        return f"{_META_ROOT}/{self._configuration.meta_graph_api_version}"

    def _google_ads_developer_token(self) -> str:
        value = self._configuration.google_ads_developer_token
        if value is None or not value.get_secret_value().strip():
            raise IntegrationProviderUnavailableError("provider_unavailable")
        return value.get_secret_value()


def build_configured_oauth_adapters(
    configuration: Settings,
) -> dict[str, ConfiguredOAuthConnector]:
    if (
        configuration.integration_credential_backend == "disabled"
        or configuration.integration_oauth_callback_url is None
    ):
        return {}
    values: dict[str, ConfiguredOAuthConnector] = {}
    providers = {
        "google": (
            configuration.google_oauth_client_id,
            configuration.google_oauth_client_secret,
        ),
        "meta": (
            configuration.meta_oauth_client_id,
            configuration.meta_oauth_client_secret,
        ),
        "microsoft": (
            configuration.microsoft_oauth_client_id,
            configuration.microsoft_oauth_client_secret,
        ),
    }
    for connector_type, definition in CONNECTOR_REGISTRY.items():
        if definition.foundation_only:
            continue
        client_id, secret = providers[definition.oauth_provider]
        if not client_id or secret is None:
            continue
        if connector_type == "google_ads" and configuration.google_ads_developer_token is None:
            continue
        values[connector_type] = ConfiguredOAuthConnector(
            connector_type=connector_type,
            provider=definition.oauth_provider,
            client_id=client_id,
            client_secret=secret.get_secret_value(),
            configuration=configuration,
        )
    return values


def _token_material(response: Mapping[str, object]) -> CredentialMaterial:
    access_token = _required_string(response, "access_token")
    values = {"access_token": access_token}
    for key in ("refresh_token", "token_type", "scope"):
        value = response.get(key)
        if isinstance(value, str) and value:
            values[key] = value
    expires_in = response.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        values["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=min(int(expires_in), 31_536_000))
        ).isoformat()
    return CredentialMaterial(values=values)


def _access_token(credentials: CredentialMaterial) -> str:
    value = credentials.values.get("access_token")
    if not value:
        raise IntegrationProviderUnavailableError("provider_unavailable")
    return value


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > 4_096:
        raise IntegrationProviderUnavailableError("provider_unavailable")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) and item else None


def _scopes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item for item in value.split() if item)[:30]
    return ()


def _items(value: Mapping[str, object]) -> list[Mapping[str, object]]:
    items = value.get("items", value.get("data", []))
    if not isinstance(items, list):
        return []
    return [item for item in items[:100] if isinstance(item, dict)]


def _mapping_items(
    value: Mapping[str, object], key: str,
) -> list[Mapping[str, object]]:
    items = value.get(key, [])
    if not isinstance(items, list):
        raise IntegrationProviderUnavailableError("provider_unavailable")
    return [item for item in items[:100] if isinstance(item, dict)]


def _normalize_whatsapp_status_events(
    payload: Mapping[str, object],
) -> tuple[NormalizedIntegrationEvent, ...]:
    """
    Parse Meta WhatsApp Cloud API delivery evidence.

    Expected provider shape:
      entry[].changes[].value.statuses[]

    Only provider-evidenced delivery states are emitted. Recipient/customer
    identifiers, pricing information, errors, and raw provider payloads are not
    copied into the canonical safe payload.
    """
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return ()

    # Never silently discard provider evidence at an outer batch boundary.
    if len(entries) > 100:
        raise IntegrationProviderUnavailableError(
            "provider_response_invalid"
        )

    result: list[NormalizedIntegrationEvent] = []
    supported_statuses = frozenset({"sent", "delivered", "read", "failed"})

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue

        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        if len(changes) > 100:
            raise IntegrationProviderUnavailableError(
                "provider_response_invalid"
            )

        for change in changes:
            if not isinstance(change, Mapping):
                continue

            value = change.get("value")
            if not isinstance(value, Mapping):
                continue

            # Absence of statuses means this may be an inbound message
            # webhook and normal WhatsApp normalization may continue.
            #
            # Presence of statuses, however, is authoritative delivery
            # evidence. Malformed or unsupported evidence must fail closed;
            # it must never be reclassified as message_received.
            if "statuses" not in value:
                continue

            statuses = value.get("statuses")
            if not isinstance(statuses, list) or not statuses:
                raise IntegrationProviderUnavailableError(
                    "provider_response_invalid"
                )

            # Never silently truncate provider delivery evidence. A single
            # Meta value containing more than the supported batch size must
            # fail closed so the webhook can be retried/inspected rather than
            # losing status transitions.
            if len(statuses) > 100:
                raise IntegrationProviderUnavailableError(
                    "provider_response_invalid"
                )

            for status_item in statuses:
                if not isinstance(status_item, Mapping):
                    raise IntegrationProviderUnavailableError(
                        "provider_response_invalid"
                    )

                message_reference = status_item.get("id")
                provider_status = status_item.get("status")
                timestamp_value = status_item.get("timestamp")

                if (
                    not isinstance(message_reference, str)
                    or not message_reference
                    or len(message_reference) > 255
                ):
                    raise IntegrationProviderUnavailableError(
                        "provider_response_invalid"
                    )

                if (
                    not isinstance(provider_status, str)
                    or provider_status not in supported_statuses
                ):
                    raise IntegrationProviderUnavailableError(
                        "provider_response_invalid"
                    )

                occurred_at = _whatsapp_status_time(timestamp_value)

                # A WhatsApp message may legitimately progress through several
                # states with the same WAMID. Include evidenced status and time
                # in the provider event identity so each transition is durable,
                # deterministic, and independently deduplicated.
                identity = (
                    f"{message_reference}\n"
                    f"{provider_status}\n"
                    f"{occurred_at.isoformat()}"
                )
                external_event_id = (
                    "wa_status_"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()
                )

                if len(result) >= 100:
                    raise IntegrationProviderUnavailableError(
                        "provider_response_invalid"
                    )

                result.append(
                    NormalizedIntegrationEvent(
                        external_event_id=external_event_id,
                        event_type="message_status_updated",
                        occurred_at=occurred_at,
                        safe_payload={
                            "external_message_reference": message_reference,
                            "delivery_status": provider_status,
                        },
                    )
                )

    return tuple(result)


def _whatsapp_status_time(value: object) -> datetime:
    if isinstance(value, str) and value.isdigit():
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (ValueError, OverflowError, OSError):
            pass

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (ValueError, OverflowError, OSError):
            pass

    raise IntegrationProviderUnavailableError("provider_response_invalid")


def _resources(value: Mapping[str, object], resource_type: str, *, name_key: str):
    result = []
    for item in _items(value):
        reference = _required_string(item, "id")
        result.append(
            ExternalResource(
                resource_type,
                reference,
                (_optional_string(item, name_key) or reference)[:160],
            )
        )
    return result


def _event_reference(payload: Mapping[str, object], serialized: str) -> str:
    for key in ("external_event_id", "id", "event_id", "mid"):
        value = payload.get(key)
        if isinstance(value, str) and 1 <= len(value) <= 255:
            return value
    return f"evt_{hashlib.sha256(serialized.encode()).hexdigest()}"


def _event_type(connector_type: str, payload: Mapping[str, object]):
    supplied = payload.get("event_type")
    allowed = {
        "message_received", "message_status_updated", "email_received",
        "calendar_event_changed", "performance_data_available",
    }
    if isinstance(supplied, str) and supplied in allowed:
        return supplied
    return {
        "whatsapp_business": "message_received",
        "gmail": "email_received",
        "google_calendar": "calendar_event_changed",
        "google_ads": "performance_data_available",
        "meta_ads": "performance_data_available",
        "facebook": "message_received",
        "instagram": "message_received",
        "microsoft_outlook": "email_received",
    }[connector_type]


def _event_time(payload: Mapping[str, object]) -> datetime:
    value = payload.get("occurred_at", payload.get("timestamp", payload.get("time")))
    if isinstance(value, str):
        try:
            return _parse_datetime(value)
        except (ValueError, TypeError):
            pass
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (ValueError, OverflowError):
            pass
    return datetime.now(UTC)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _nested_datetime(item: Mapping[str, object], key: str) -> datetime | None:
    value = item.get(key)
    if not isinstance(value, dict):
        return None
    raw = value.get("dateTime")
    if not isinstance(raw, str):
        return None
    return _parse_datetime(raw)


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except InvalidOperation:
        return Decimal("0")


def _meta_action_total(value: object, action_type: str) -> int:
    if not isinstance(value, list):
        return 0
    return int(sum(
        _decimal(item.get("value"))
        for item in value
        if isinstance(item, dict) and item.get("action_type") == action_type
    ))


def _meta_action_value(value: object, action_type: str) -> Decimal:
    if not isinstance(value, list):
        return Decimal("0")
    return sum(
        (
            _decimal(item.get("value"))
            for item in value
            if isinstance(item, dict) and item.get("action_type") == action_type
        ),
        Decimal("0"),
    )
