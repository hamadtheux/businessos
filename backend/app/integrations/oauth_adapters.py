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
            return [
                ExternalResource("google_ads_customer", item.rsplit("/", 1)[-1], item)
                for item in names[:100]
                if isinstance(item, str) and item
            ]
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

    async def normalize_webhook(
        self, payload: Mapping[str, object]
    ) -> NormalizedIntegrationEvent:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > self._configuration.integration_webhook_max_bytes:
            raise IntegrationProviderUnavailableError("provider_unavailable")
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
        return NormalizedIntegrationEvent(
            external_event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            safe_payload=safe_payload,
        )

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
            pages = await self._http.request_json(
                "GET",
                f"{root}/me/accounts",
                params={
                    "fields": "id,name,instagram_business_account{id,username}",
                    "limit": "100",
                    "access_token": token,
                },
            )
            for item in _items(pages):
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
        businesses = await self._http.request_json(
            "GET", f"{root}/me/businesses", params={"limit": "100", "access_token": token}
        )
        for business in _items(businesses):
            reference = _required_string(business, "id")
            resources.append(
                ExternalResource("meta_business", reference, (_optional_string(business, "name") or reference)[:160])
            )
        if self.connector_type == "meta_ads":
            accounts = await self._http.request_json(
                "GET",
                f"{root}/me/adaccounts",
                params={"fields": "id,name,account_id", "limit": "100", "access_token": token},
            )
            resources.extend(
                ExternalResource(
                    "ad_account",
                    _required_string(item, "id"),
                    (_optional_string(item, "name") or _required_string(item, "id"))[:160],
                )
                for item in _items(accounts)
            )
        elif self.connector_type == "whatsapp_business":
            for business in list(resources):
                accounts = await self._http.request_json(
                    "GET",
                    f"{root}/{business.external_reference}/owned_whatsapp_business_accounts",
                    params={"fields": "id,name", "limit": "100", "access_token": token},
                )
                for item in _items(accounts):
                    reference = _required_string(item, "id")
                    resources.append(
                        ExternalResource(
                            "whatsapp_business_account",
                            reference,
                            (_optional_string(item, "name") or reference)[:160],
                            parent_reference=business.external_reference,
                        )
                    )
                    phone_numbers = await self._http.request_json(
                        "GET",
                        f"{root}/{reference}/phone_numbers",
                        params={
                            "fields": "id,display_phone_number,verified_name",
                            "limit": "100",
                            "access_token": token,
                        },
                    )
                    for phone in _items(phone_numbers):
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
            "metrics.conversions_value FROM campaign WHERE segments.date "
            f"BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        value = await self._http.request_json(
            "POST",
            f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}/customers/{account}/googleAds:search",
            headers={
                **_bearer(_access_token(credentials)),
                "developer-token": self._google_ads_developer_token(),
            },
            json_body={"query": query, "pageSize": 10_000},
        )
        result: list[NormalizedAdPerformance] = []
        rows = value.get("results", [])
        if not isinstance(rows, list):
            return result
        for row in rows[:10_000]:
            if not isinstance(row, dict):
                continue
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
                )
            )
        return result

    async def _meta_performance(self, credentials, account, start, end):
        value = await self._http.request_json(
            "GET",
            f"{self._meta_root()}/{account}/insights",
            params={
                "fields": "campaign_id,spend,impressions,clicks,reach,actions,action_values",
                "level": "campaign",
                "time_increment": "1",
                "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
                "limit": "500",
                "access_token": _access_token(credentials),
            },
        )
        result: list[NormalizedAdPerformance] = []
        for item in _items(value):
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
                )
            )
        return result

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
