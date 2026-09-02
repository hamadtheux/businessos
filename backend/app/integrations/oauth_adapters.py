from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

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
    ExternalMailMessage,
    ExternalMailMessageContent,
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
_GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1"
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
        if self._provider == "meta":
            endpoint = (
                "https://www.facebook.com/"
                f"{self._configuration.meta_graph_api_version}/dialog/oauth"
            )
        values = {
            "client_id": self._client_id,
            "redirect_uri": request.redirect_uri,
            "response_type": "code",
            "scope": " ".join(request.scopes),
            "state": request.state,
        }
        if self._provider != "meta":
            values.update(
                {
                    "code_challenge": request.code_challenge,
                    "code_challenge_method": "S256",
                }
            )
        if self._provider == "google":
            values.update(
                {
                    "access_type": "offline",
                    "prompt": "consent",
                }
            )
        elif self._provider == "meta":
            configuration_id = self._configuration.meta_login_configuration_id
            if not configuration_id:
                raise IntegrationProviderUnavailableError(
                    "provider_unavailable"
                )
            values.update(
                {
                    "scope": ",".join(request.scopes),
                    "config_id": configuration_id,
                    "auth_type": "rerequest",
                    "override_default_response_type": "true",
                }
            )
        return f"{endpoint}?{urlencode(values)}"

    async def exchange_authorization_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> AuthorizationExchange:
        if self._provider == "meta":
            response = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/oauth/access_token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            return await self._meta_system_user_exchange(response)

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
        material = _token_material(response)
        scopes = _scopes(response.get("scope")) or CONNECTOR_REGISTRY[
            self.connector_type
        ].requested_oauth_scopes(
            self._configuration.external_connector_write_mode
        )
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
            # Facebook Login for Business returns a Business Integration
            # System User token. It is not a short-lived user token and must
            # not be sent through the fb_exchange_token flow. An expiring
            # system-user credential requires a fresh business authorization.
            return CredentialRefreshResult(
                status="reauth_required",
                failure_code="authorization_expired",
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
            message_events = _normalize_whatsapp_message_events(payload)
            if message_events:
                return message_events

        if self.connector_type in {"facebook", "instagram"}:
            messaging_events = _normalize_meta_messaging_events(
                payload,
                connector_type=self.connector_type,
            )
            if messaging_events:
                return messaging_events

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

    async def subscribe_resources(
        self,
        credentials: CredentialMaterial,
        resources: Sequence[ExternalResource],
    ) -> None:
        """Subscribe authorized Facebook Pages to the app's supported events."""
        if self.connector_type != "facebook":
            return
        system_token = _access_token(credentials)
        pages = [item for item in resources if item.resource_type == "facebook_page"]
        if not pages:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        for page in pages:
            token_response = await self._http.request_json(
                "GET",
                f"{self._meta_root()}/{page.external_reference}",
                params={"fields": "access_token", "access_token": system_token},
            )
            page_token = _required_string(token_response, "access_token")
            subscribed = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/{page.external_reference}/subscribed_apps",
                data={
                    "subscribed_fields": (
                        "messages,messaging_postbacks,message_deliveries,message_reads"
                    ),
                    "access_token": page_token,
                },
            )
            if subscribed.get("success") is not True:
                raise IntegrationProviderUnavailableError("provider_response_invalid")

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

    async def list_mail_messages(
        self,
        credentials: CredentialMaterial,
        *,
        limit: int,
    ) -> Sequence[ExternalMailMessage]:
        if self.connector_type != "gmail":
            raise IntegrationProviderUnavailableError("provider_unavailable")
        if not 1 <= limit <= 20:
            raise IntegrationProviderUnavailableError("provider_response_invalid")

        headers = _bearer(_access_token(credentials))
        value = await self._http.request_json(
            "GET",
            f"{_GMAIL_ROOT}/users/me/messages",
            headers=headers,
            params={"maxResults": str(limit)},
        )

        raw_messages = value.get("messages", [])
        if not isinstance(raw_messages, list):
            raise IntegrationProviderUnavailableError("provider_response_invalid")

        result: list[ExternalMailMessage] = []
        for item in raw_messages[:limit]:
            if not isinstance(item, Mapping):
                raise IntegrationProviderUnavailableError("provider_response_invalid")

            message_reference = _required_string(item, "id")
            listed_thread_reference = _optional_string(item, "threadId") or ""

            detail = await self._http.request_json(
                "GET",
                f"{_GMAIL_ROOT}/users/me/messages/{quote(message_reference, safe='')}",
                headers=headers,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject"],
                },
            )

            returned_reference = _required_string(detail, "id")
            if returned_reference != message_reference:
                raise IntegrationProviderUnavailableError("provider_response_invalid")

            thread_reference = (
                _optional_string(detail, "threadId")
                or listed_thread_reference
                or message_reference
            )

            result.append(
                ExternalMailMessage(
                    external_message_reference=message_reference,
                    external_thread_reference=thread_reference[:255],
                    sender=_gmail_header(detail, "From")[:320],
                    subject=_gmail_header(detail, "Subject")[:500],
                    snippet=(_optional_string(detail, "snippet") or "")[:1_000],
                )
            )

        return result

    async def read_mail_message(
        self,
        credentials: CredentialMaterial,
        *,
        message_reference: str,
    ) -> ExternalMailMessageContent:
        if self.connector_type != "gmail":
            raise IntegrationProviderUnavailableError("provider_unavailable")
        if not 1 <= len(message_reference) <= 255:
            raise IntegrationProviderUnavailableError("provider_response_invalid")

        detail = await self._http.request_json(
            "GET",
            f"{_GMAIL_ROOT}/users/me/messages/{quote(message_reference, safe='')}",
            headers=_bearer(_access_token(credentials)),
            params={"format": "full"},
        )

        returned_reference = _required_string(detail, "id")
        if returned_reference != message_reference:
            raise IntegrationProviderUnavailableError("provider_response_invalid")

        thread_reference = (
            _optional_string(detail, "threadId")
            or message_reference
        )

        return ExternalMailMessageContent(
            external_message_reference=message_reference,
            external_thread_reference=thread_reference[:255],
            sender=_gmail_header(detail, "From")[:320],
            subject=_gmail_header(detail, "Subject")[:500],
            snippet=(_optional_string(detail, "snippet") or "")[:1_000],
            body_text=_gmail_plain_text(detail)[:20_000],
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

    async def _meta_system_user_exchange(
        self,
        response: Mapping[str, object],
    ) -> AuthorizationExchange:
        token_payload = response.get("data")
        if not isinstance(token_payload, Mapping):
            token_payload = response
        token = _required_string(token_payload, "access_token")
        debug_response = await self._http.request_json(
            "GET",
            f"{self._meta_root()}/debug_token",
            headers=_bearer(f"{self._client_id}|{self._client_secret}"),
            params={
                "input_token": token,
            },
        )
        debug = debug_response.get("data")
        if not isinstance(debug, Mapping):
            raise IntegrationProviderUnavailableError(
                "provider_response_invalid"
            )
        app_id = debug.get("app_id")
        token_kind = debug.get("type")
        if (
            debug.get("is_valid") is not True
            or str(app_id) != self._client_id
            or token_kind not in {
                "SYSTEM_USER",
                "BUSINESS_INTEGRATION_SYSTEM_USER",
            }
        ):
            raise IntegrationProviderUnavailableError(
                "provider_response_invalid"
            )

        scopes = _string_sequence(debug.get("scopes"), limit=30)
        if not scopes:
            raise IntegrationProviderUnavailableError(
                "provider_response_invalid"
            )

        values = {
            "access_token": token,
            "token_type": (
                _optional_string(token_payload, "token_type") or "bearer"
            ),
            "meta_token_type": str(token_kind),
            "scope": " ".join(scopes),
        }
        system_user_id = debug.get("user_id")
        if isinstance(system_user_id, (str, int)) and str(system_user_id):
            values["system_user_id"] = str(system_user_id)[:255]

        expires_at = _positive_unix_timestamp(debug.get("expires_at"))
        if expires_at is None:
            expires_in = token_payload.get("expires_in")
            if isinstance(expires_in, (int, float)) and expires_in > 0:
                expires_at = datetime.now(UTC) + timedelta(
                    seconds=min(int(expires_in), 31_536_000)
                )
        if expires_at is not None:
            values["expires_at"] = expires_at.isoformat()

        data_access_expires_at = _positive_unix_timestamp(
            debug.get("data_access_expires_at")
        )
        if data_access_expires_at is not None:
            values["data_access_expires_at"] = (
                data_access_expires_at.isoformat()
            )

        return AuthorizationExchange(
            credentials=CredentialMaterial(values=values),
            granted_scopes=scopes,
        )

    async def _list_meta_resources(self, token: str) -> Sequence[ExternalResource]:
        root = self._meta_root()
        resources: list[ExternalResource] = []
        if self.connector_type == "facebook":
            try:
                pages = await self._meta_expanded_accounts(
                    token=token,
                    fields="accounts{id,name,business{id,name},tasks}",
                )
            except _ProviderHttpError as exc:
                if exc.status_code not in {400, 403}:
                    raise
                pages = await self._meta_expanded_accounts(
                    token=token,
                    fields="accounts{id,name,tasks}",
                )
        elif self.connector_type == "instagram":
            pages = await self._meta_paged_items(
                f"{root}/me/accounts",
                token=token,
                params={
                    "fields": (
                        "id,name,business{id,name},tasks,"
                        "instagram_business_account{id,username}"
                    ),
                    "limit": "100",
                },
            )
        else:
            pages = []

        if self.connector_type in {"facebook", "instagram"}:
            businesses: dict[str, ExternalResource] = {}
            for item in pages:
                page_id = _required_string(item, "id")
                business = item.get("business")
                metadata: dict[str, str] = {}
                parent_reference: str | None = None
                if isinstance(business, Mapping):
                    business_id = _optional_string(business, "id")
                    if business_id and len(business_id) <= 255:
                        business_name = (
                            _optional_string(business, "name") or business_id
                        )[:160]
                        parent_reference = business_id
                        metadata["meta_business_id"] = business_id
                        if self.connector_type == "facebook":
                            businesses.setdefault(
                                business_id,
                                ExternalResource(
                                    "meta_business",
                                    business_id,
                                    business_name,
                                ),
                            )
                tasks = _string_sequence(item.get("tasks"), limit=20)
                if tasks:
                    metadata["capabilities"] = ",".join(tasks)[:255]
                resources.append(
                    ExternalResource(
                        "facebook_page",
                        page_id,
                        (_optional_string(item, "name") or page_id)[:160],
                        parent_reference=parent_reference,
                        metadata=metadata or None,
                    )
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
            return [*businesses.values(), *resources][:_MAX_PROVIDER_ITEMS]
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

    async def _meta_expanded_accounts(
        self, *, token: str, fields: str,
    ) -> list[Mapping[str, object]]:
        value = await self._http.request_json(
            "GET",
            f"{self._meta_root()}/me",
            params={"fields": fields, "access_token": token},
        )
        accounts = value.get("accounts")
        if not isinstance(accounts, Mapping):
            raise IntegrationProviderUnavailableError("provider_unavailable")
        return await self._meta_expanded_account_items(
            accounts,
            token=token,
        )

    async def _meta_expanded_account_items(
        self,
        value: Mapping[str, object],
        *,
        token: str,
    ) -> list[Mapping[str, object]]:
        result: list[Mapping[str, object]] = []
        for page_index in range(_MAX_PROVIDER_PAGES):
            page = value.get("data")
            if not isinstance(page, list):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            if any(not isinstance(item, Mapping) for item in page):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            result.extend(item for item in page if isinstance(item, Mapping))
            if len(result) > _MAX_PROVIDER_ITEMS:
                raise IntegrationProviderUnavailableError("provider_response_too_large")
            paging = value.get("paging")
            if paging is not None and not isinstance(paging, Mapping):
                raise IntegrationProviderUnavailableError("provider_unavailable")
            candidate = paging.get("next") if isinstance(paging, Mapping) else None
            if not isinstance(candidate, str):
                return result
            if page_index == _MAX_PROVIDER_PAGES - 1:
                break
            next_url, query = self._meta_paging_request(candidate, token=token)
            value = await self._http.request_json(
                "GET",
                next_url,
                params=query,
            )
        raise IntegrationProviderUnavailableError("provider_pagination_limit")

    def _meta_paging_request(
        self, candidate: str, *, token: str,
    ) -> tuple[str, Mapping[str, str]]:
        if len(candidate) > 8_192:
            raise IntegrationProviderUnavailableError("provider_unavailable")
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "graph.facebook.com"
                or parsed.fragment
                or not parsed.path.startswith(
                    f"/{self._configuration.meta_graph_api_version}/"
                )
            ):
                raise IntegrationProviderUnavailableError(
                    "provider_unavailable"
                )
            query = {
                key: value
                for key, value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                    max_num_fields=50,
                )
                if key != "access_token"
            }
        except ValueError:
            raise IntegrationProviderUnavailableError(
                "provider_unavailable"
            ) from None
        query["access_token"] = token
        next_url = urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "", "")
        )
        return next_url, query

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
        if definition.oauth_provider == "meta" and (
            connector_type != "facebook"
            or not configuration.meta_login_configuration_id
        ):
            # This production configuration is intentionally scoped to Pages,
            # Messenger, and leads. Ads, Instagram, Catalog, and WhatsApp use
            # separate future Login for Business configurations.
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


def _string_sequence(value: object, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > limit:
        return ()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return ()
        normalized = item.strip()
        if not normalized or len(normalized) > 255:
            return ()
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _positive_unix_timestamp(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (ValueError, OverflowError, OSError):
        raise IntegrationProviderUnavailableError(
            "provider_response_invalid"
        ) from None


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


def _normalize_meta_messaging_events(
    payload: Mapping[str, object],
    *,
    connector_type: str,
) -> tuple[NormalizedIntegrationEvent, ...]:
    """Normalize bounded Messenger/Instagram messaging webhook evidence."""
    expected_object = "page" if connector_type == "facebook" else "instagram"
    if payload.get("object") != expected_object:
        return ()
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    result: list[NormalizedIntegrationEvent] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        resource_reference = _bounded_provider_reference(entry.get("id"))
        if resource_reference is None:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        messaging = entry.get("messaging")
        if not isinstance(messaging, list) or len(messaging) > 100:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        for item in messaging:
            if not isinstance(item, Mapping):
                raise IntegrationProviderUnavailableError("provider_response_invalid")
            occurred_at = _meta_millis_time(item.get("timestamp", entry.get("time")))
            sender = item.get("sender")
            recipient = item.get("recipient")
            sender_reference = (
                _bounded_provider_reference(sender.get("id"))
                if isinstance(sender, Mapping)
                else None
            )
            recipient_reference = (
                _bounded_provider_reference(recipient.get("id"))
                if isinstance(recipient, Mapping)
                else None
            )
            message = item.get("message")
            postback = item.get("postback")
            delivery = item.get("delivery")
            read = item.get("read")

            if isinstance(message, Mapping):
                message_reference = _bounded_provider_reference(message.get("mid"))
                if message.get("is_echo") is True:
                    if message_reference:
                        result.append(_meta_delivery_event(
                            resource_reference=resource_reference,
                            message_reference=message_reference,
                            delivery_status="sent",
                            occurred_at=occurred_at,
                        ))
                    continue
                content = message.get("text")
                if (
                    not sender_reference
                    or not recipient_reference
                    or not message_reference
                    or recipient_reference != resource_reference
                    or not isinstance(content, str)
                    or not content.strip()
                    or len(content.strip()) > 10_000
                ):
                    # Attachments are not claimed as supported until durable
                    # attachment storage exists. Refuse malformed text evidence.
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                result.append(NormalizedIntegrationEvent(
                    external_event_id=message_reference,
                    event_type="message_received",
                    occurred_at=occurred_at,
                    safe_payload={
                        "external_conversation_reference": sender_reference,
                        "external_message_reference": message_reference,
                        "sender_external_reference": sender_reference,
                        "external_resource_reference": resource_reference,
                        "content": content.strip(),
                    },
                ))
            elif isinstance(postback, Mapping):
                if (
                    not sender_reference
                    or not recipient_reference
                    or recipient_reference != resource_reference
                ):
                    raise IntegrationProviderUnavailableError(
                        "provider_response_invalid"
                    )
                raw_content = postback.get("title") or postback.get("payload")
                if not isinstance(raw_content, str) or not raw_content.strip():
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                identity = json.dumps(item, sort_keys=True, separators=(",", ":"))
                reference = _bounded_provider_reference(postback.get("mid")) or (
                    "meta_postback_" + hashlib.sha256(identity.encode()).hexdigest()
                )
                result.append(NormalizedIntegrationEvent(
                    external_event_id=reference,
                    event_type="message_received",
                    occurred_at=occurred_at,
                    safe_payload={
                        "external_conversation_reference": sender_reference,
                        "external_message_reference": reference,
                        "sender_external_reference": sender_reference,
                        "external_resource_reference": resource_reference,
                        "content": raw_content.strip()[:10_000],
                    },
                ))
            elif isinstance(delivery, Mapping):
                mids = delivery.get("mids")
                if not isinstance(mids, list) or not mids or len(mids) > 100:
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                for mid in mids:
                    reference = _bounded_provider_reference(mid)
                    if not reference:
                        raise IntegrationProviderUnavailableError("provider_response_invalid")
                    result.append(_meta_delivery_event(
                        resource_reference=resource_reference,
                        message_reference=reference,
                        delivery_status="delivered",
                        occurred_at=occurred_at,
                    ))
            elif isinstance(read, Mapping):
                watermark = read.get("watermark")
                if (
                    not sender_reference
                    or not isinstance(watermark, (int, float))
                    or watermark <= 0
                ):
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                identity = (
                    f"{resource_reference}\n"
                    f"{sender_reference}\n"
                    f"read\n{int(watermark)}"
                )
                result.append(NormalizedIntegrationEvent(
                    external_event_id="meta_read_" + hashlib.sha256(identity.encode()).hexdigest(),
                    event_type="message_status_updated",
                    occurred_at=_meta_millis_time(watermark),
                    safe_payload={
                        "delivery_status": "read",
                        "delivery_watermark": int(watermark),
                        "external_conversation_reference": sender_reference,
                        "external_resource_reference": resource_reference,
                    },
                ))
            else:
                raise IntegrationProviderUnavailableError("provider_response_invalid")
            if len(result) > 100:
                raise IntegrationProviderUnavailableError("provider_response_invalid")
    return tuple(result)


def _normalize_whatsapp_message_events(
    payload: Mapping[str, object],
) -> tuple[NormalizedIntegrationEvent, ...]:
    if payload.get("object") != "whatsapp_business_account":
        return ()
    entries = payload.get("entry")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise IntegrationProviderUnavailableError("provider_response_invalid")
    result: list[NormalizedIntegrationEvent] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        changes = entry.get("changes")
        if not isinstance(changes, list) or len(changes) > 100:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        for change in changes:
            value = change.get("value") if isinstance(change, Mapping) else None
            if not isinstance(value, Mapping) or "messages" not in value:
                continue
            metadata = value.get("metadata")
            phone_reference = (
                _bounded_provider_reference(metadata.get("phone_number_id"))
                if isinstance(metadata, Mapping)
                else None
            )
            messages = value.get("messages")
            contacts = value.get("contacts")
            if not phone_reference or not isinstance(messages, list) or len(messages) > 100:
                raise IntegrationProviderUnavailableError("provider_response_invalid")
            names: dict[str, str] = {}
            if isinstance(contacts, list):
                for contact in contacts[:100]:
                    profile = contact.get("profile") if isinstance(contact, Mapping) else None
                    wa_id = contact.get("wa_id") if isinstance(contact, Mapping) else None
                    name = profile.get("name") if isinstance(profile, Mapping) else None
                    if isinstance(wa_id, str) and isinstance(name, str) and name.strip():
                        names[wa_id] = name.strip()[:160]
            for message in messages:
                if not isinstance(message, Mapping) or message.get("type") != "text":
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                reference = _bounded_provider_reference(message.get("id"))
                sender = _bounded_provider_reference(message.get("from"))
                text_value = message.get("text")
                content = text_value.get("body") if isinstance(text_value, Mapping) else None
                if not reference or not sender or not isinstance(content, str) or not content.strip():
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
                safe_payload: dict[str, object] = {
                    "external_conversation_reference": sender,
                    "external_message_reference": reference,
                    "sender_external_reference": sender,
                    "sender_phone": sender,
                    "external_resource_reference": phone_reference,
                    "content": content.strip()[:10_000],
                }
                if sender in names:
                    safe_payload["sender_display_name"] = names[sender]
                result.append(NormalizedIntegrationEvent(
                    external_event_id=reference,
                    event_type="message_received",
                    occurred_at=_whatsapp_status_time(message.get("timestamp")),
                    safe_payload=safe_payload,
                ))
                if len(result) > 100:
                    raise IntegrationProviderUnavailableError("provider_response_invalid")
    return tuple(result)


def _bounded_provider_reference(value: object) -> str | None:
    normalized = str(value).strip() if isinstance(value, (str, int)) else ""
    return normalized if 1 <= len(normalized) <= 255 else None


def _meta_millis_time(value: object) -> datetime:
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
        except (ValueError, OverflowError, OSError):
            pass
    raise IntegrationProviderUnavailableError("provider_response_invalid")


def _meta_delivery_event(
    *,
    resource_reference: str,
    message_reference: str,
    delivery_status: str,
    occurred_at: datetime,
) -> NormalizedIntegrationEvent:
    identity = f"{resource_reference}\n{message_reference}\n{delivery_status}\n{occurred_at.isoformat()}"
    return NormalizedIntegrationEvent(
        external_event_id="meta_status_" + hashlib.sha256(identity.encode()).hexdigest(),
        event_type="message_status_updated",
        occurred_at=occurred_at,
        safe_payload={
            "external_message_reference": message_reference,
            "delivery_status": delivery_status,
            "external_resource_reference": resource_reference,
        },
    )


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


def _gmail_plain_text(
    message: Mapping[str, object],
) -> str:
    payload = message.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return _gmail_plain_text_part(payload, depth=0)


def _gmail_plain_text_part(
    part: Mapping[str, object],
    *,
    depth: int,
) -> str:
    if depth > 12:
        raise IntegrationProviderUnavailableError("provider_response_invalid")

    mime_type = part.get("mimeType")
    if mime_type == "text/plain":
        body = part.get("body")
        if not isinstance(body, Mapping):
            return ""
        data = body.get("data")
        if not isinstance(data, str) or not data:
            return ""
        if len(data) > 100_000:
            raise IntegrationProviderUnavailableError("provider_response_invalid")
        try:
            padded = data + "=" * (-len(data) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            return decoded.decode("utf-8", errors="replace")
        except (ValueError, UnicodeEncodeError):
            raise IntegrationProviderUnavailableError(
                "provider_response_invalid"
            ) from None

    parts = part.get("parts")
    if not isinstance(parts, list):
        return ""

    values: list[str] = []
    for child in parts[:100]:
        if not isinstance(child, Mapping):
            continue
        value = _gmail_plain_text_part(child, depth=depth + 1)
        if value:
            values.append(value)
        if sum(len(item) for item in values) >= 20_000:
            break

    return "\n\n".join(values)[:20_000]


def _gmail_header(
    message: Mapping[str, object],
    name: str,
) -> str:
    payload = message.get("payload")
    if not isinstance(payload, Mapping):
        return ""

    headers = payload.get("headers")
    if not isinstance(headers, list):
        return ""

    target = name.casefold()
    for item in headers[:100]:
        if not isinstance(item, Mapping):
            continue
        header_name = item.get("name")
        header_value = item.get("value")
        if (
            isinstance(header_name, str)
            and header_name.casefold() == target
            and isinstance(header_value, str)
        ):
            return header_value

    return ""


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
