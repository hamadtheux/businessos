from __future__ import annotations

import base64
from email.message import EmailMessage
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from app.core.config import Settings
from app.exceptions.integration import IntegrationProviderUnavailableError
from app.integrations.action_adapters import (
    ConnectorActionResult,
    ConnectorRejectedError,
    ConnectorRequestNotSentError,
)
from app.integrations.credentials import CredentialMaterial
from app.integrations.oauth_adapters import (
    OAuthHttpClient,
    _ProviderHttpError,
)
from app.schemas.ai_action_payload import (
    ChangeAdBudgetPayload,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
    LaunchGoogleAdsCampaignPayload,
    LaunchMetaCampaignPayload,
    PauseAdCampaignPayload,
    PublishSocialPostPayload,
    SendCustomerMessagePayload,
    SendEmailPayload,
    SendWhatsAppMessagePayload,
)


_GOOGLE_ADS_ROOT = "https://googleads.googleapis.com"
_GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_ACTION_TYPES = MappingProxyType({
    "gmail": frozenset({"send_email", "send_customer_message"}),
    "whatsapp_business": frozenset({"send_whatsapp_message", "send_customer_message"}),
    "facebook": frozenset({"publish_social_post"}),
    "instagram": frozenset({"publish_social_post"}),
    "meta_ads": frozenset({
        "create_meta_campaign", "launch_meta_campaign",
        "change_ad_budget", "pause_ad_campaign",
    }),
    "google_ads": frozenset({
        "create_google_ads_campaign", "launch_google_ads_campaign",
        "change_ad_budget", "pause_ad_campaign",
    }),
})


class ProviderConnectorActionAdapter:
    def __init__(
        self,
        *,
        connector_type: str,
        configuration: Settings,
        http: OAuthHttpClient | None = None,
    ) -> None:
        self.connector_type = connector_type
        self.supported_action_types = _ACTION_TYPES[connector_type]
        self._configuration = configuration
        self._http = http or OAuthHttpClient(
            timeout_seconds=configuration.connector_dispatch_timeout_seconds
        )

    async def execute(
        self,
        *,
        credentials: CredentialMaterial,
        action_type: str,
        payload,
        selected_resources: tuple[Mapping[str, str], ...],
        delivery_target: str | None,
        idempotency_key: str,
    ) -> ConnectorActionResult:
        if action_type not in self.supported_action_types:
            raise ConnectorRequestNotSentError("unsupported_action")
        token = credentials.values.get("access_token")
        if not token:
            raise ConnectorRequestNotSentError("credential_invalid")
        headers = {
            "Authorization": f"Bearer {token}",
            "X-AIBOS-Idempotency-Key": idempotency_key,
        }
        try:
            if self.connector_type == "gmail":
                return await self._send_gmail(payload, delivery_target, headers)
            if self.connector_type == "whatsapp_business":
                return await self._send_whatsapp(
                    payload, delivery_target, selected_resources, headers
                )
            if self.connector_type in {"facebook", "instagram"}:
                return await self._publish_social(
                    payload, selected_resources, headers
                )
            if self.connector_type == "meta_ads":
                return await self._mutate_meta_ads(
                    action_type, payload, selected_resources, headers
                )
            return await self._mutate_google_ads(
                action_type, payload, selected_resources, headers
            )
        except _ProviderHttpError as exc:
            if exc.status_code < 500:
                raise ConnectorRejectedError("provider_rejected") from None
            # A provider 5xx can follow an accepted mutation. The dispatcher
            # must classify this as uncertain and never blind-retry it.
            raise RuntimeError("provider_outcome_unknown") from None
        except IntegrationProviderUnavailableError:
            raise RuntimeError("provider_outcome_unknown") from None

    async def _send_gmail(self, payload, target, headers):
        if not isinstance(payload, (SendEmailPayload, SendCustomerMessagePayload)):
            raise ConnectorRequestNotSentError("payload_invalid")
        if not target:
            raise ConnectorRequestNotSentError("delivery_target_required")
        message = EmailMessage()
        message["To"] = target
        if isinstance(payload, SendEmailPayload):
            message["Subject"] = payload.subject
            body = payload.body
        else:
            message["Subject"] = "Message from your business"
            body = payload.message
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).rstrip(b"=").decode("ascii")
        response = await self._http.request_json(
            "POST", _GMAIL_SEND, headers=headers, json_body={"raw": raw}
        )
        reference = _required_reference(response, "id")
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id=reference,
            safe_metadata={"provider": "gmail"},
        )

    async def _send_whatsapp(self, payload, target, resources, headers):
        if not isinstance(payload, (SendWhatsAppMessagePayload, SendCustomerMessagePayload)):
            raise ConnectorRequestNotSentError("payload_invalid")
        if not target:
            raise ConnectorRequestNotSentError("delivery_target_required")
        phone_number_id = _resource(resources, "phone_number")
        if phone_number_id is None:
            raise ConnectorRequestNotSentError("phone_number_selection_required")
        response = await self._http.request_json(
            "POST",
            f"{self._meta_root()}/{phone_number_id}/messages",
            headers=headers,
            json_body={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": target,
                "type": "text",
                "text": {"preview_url": False, "body": payload.message},
            },
        )
        messages = response.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise RuntimeError("provider_outcome_unknown")
        reference = _required_reference(messages[0], "id")
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id=reference,
            safe_metadata={"provider": "whatsapp_business"},
        )

    async def _publish_social(self, payload, resources, headers):
        if not isinstance(payload, PublishSocialPostPayload):
            raise ConnectorRequestNotSentError("payload_invalid")
        if self.connector_type == "facebook":
            page_id = _resource(resources, "facebook_page")
            if page_id is None:
                raise ConnectorRequestNotSentError("page_selection_required")
            response = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/{page_id}/feed",
                headers=headers,
                data={"message": payload.content},
            )
            reference = _required_reference(response, "id")
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=reference,
                safe_metadata={"provider": "facebook"},
            )
        instagram_id = _resource(resources, "instagram_account")
        media_url = next(
            (item for item in payload.media_refs if _safe_public_media_url(item)),
            None,
        )
        if instagram_id is None or media_url is None:
            raise ConnectorRequestNotSentError(
                "instagram_media_and_account_required"
            )
        container = await self._http.request_json(
            "POST",
            f"{self._meta_root()}/{instagram_id}/media",
            headers=headers,
            data={"image_url": media_url, "caption": payload.content},
        )
        container_id = _required_reference(container, "id")
        published = await self._http.request_json(
            "POST",
            f"{self._meta_root()}/{instagram_id}/media_publish",
            headers=headers,
            data={"creation_id": container_id},
        )
        reference = _required_reference(published, "id")
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id=reference,
            safe_metadata={"provider": "instagram"},
        )

    async def _mutate_meta_ads(self, action_type, payload, resources, headers):
        account = _resource(resources, "ad_account")
        if account is None:
            raise ConnectorRequestNotSentError("ad_account_selection_required")
        if isinstance(payload, CreateMetaCampaignPayload):
            objective = {
                "awareness": "OUTCOME_AWARENESS",
                "traffic": "OUTCOME_TRAFFIC",
                "engagement": "OUTCOME_ENGAGEMENT",
                "leads": "OUTCOME_LEADS",
                "sales": "OUTCOME_SALES",
                "app_promotion": "OUTCOME_APP_PROMOTION",
            }[payload.objective]
            response = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/{account}/campaigns",
                headers=headers,
                data={
                    "name": payload.campaign_name,
                    "objective": objective,
                    "status": "PAUSED",
                    "special_ad_categories": "[]",
                },
            )
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=_required_reference(response, "id"),
                safe_metadata={"provider": "meta_ads", "status": "paused"},
            )
        if isinstance(payload, (LaunchMetaCampaignPayload, PauseAdCampaignPayload)):
            status = "ACTIVE" if action_type == "launch_meta_campaign" else "PAUSED"
            response = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/{payload.campaign_ref}",
                headers=headers,
                data={"status": status},
            )
            if response.get("success") is not True:
                raise RuntimeError("provider_outcome_unknown")
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=payload.campaign_ref,
                safe_metadata={"provider": "meta_ads", "status": status.casefold()},
            )
        if isinstance(payload, ChangeAdBudgetPayload):
            field = "daily_budget" if payload.budget_period == "daily" else "lifetime_budget"
            response = await self._http.request_json(
                "POST",
                f"{self._meta_root()}/{payload.campaign_ref}",
                headers=headers,
                data={field: str(int(payload.budget * 100))},
            )
            if response.get("success") is not True:
                raise RuntimeError("provider_outcome_unknown")
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=payload.campaign_ref,
                safe_metadata={"provider": "meta_ads", "budget_period": payload.budget_period},
            )
        raise ConnectorRequestNotSentError("payload_invalid")

    async def _mutate_google_ads(self, action_type, payload, resources, headers):
        customer = _resource(resources, "google_ads_customer")
        developer = self._configuration.google_ads_developer_token
        if customer is None or developer is None:
            raise ConnectorRequestNotSentError("google_ads_configuration_required")
        ads_headers = {
            **headers,
            "developer-token": developer.get_secret_value(),
        }
        root = (
            f"{_GOOGLE_ADS_ROOT}/{self._configuration.google_ads_api_version}"
            f"/customers/{customer}"
        )
        if isinstance(payload, CreateGoogleAdsCampaignPayload):
            micros = str(int(payload.budget * 1_000_000))
            response = await self._http.request_json(
                "POST",
                f"{root}/googleAds:mutate",
                headers=ads_headers,
                json_body={
                    "mutateOperations": [
                        {"campaignBudgetOperation": {"create": {
                            "resourceName": f"customers/{customer}/campaignBudgets/-1",
                            "name": f"{payload.campaign_name} budget",
                            "amountMicros": micros,
                            "deliveryMethod": "STANDARD",
                            "explicitlyShared": False,
                        }}},
                        {"campaignOperation": {"create": {
                            "resourceName": f"customers/{customer}/campaigns/-2",
                            "name": payload.campaign_name,
                            "status": "PAUSED",
                            "advertisingChannelType": payload.network.upper(),
                            "campaignBudget": f"customers/{customer}/campaignBudgets/-1",
                            "maximizeConversions": {},
                        }}},
                    ],
                    "partialFailure": False,
                    "responseContentType": "RESOURCE_NAME_ONLY",
                },
            )
            reference = _google_campaign_reference(response)
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=reference,
                safe_metadata={"provider": "google_ads", "status": "paused"},
            )
        if isinstance(payload, (LaunchGoogleAdsCampaignPayload, PauseAdCampaignPayload)):
            status = "ENABLED" if action_type == "launch_google_ads_campaign" else "PAUSED"
            resource = _google_resource(customer, "campaigns", payload.campaign_ref)
            await self._http.request_json(
                "POST",
                f"{root}/campaigns:mutate",
                headers=ads_headers,
                json_body={"operations": [{
                    "update": {"resourceName": resource, "status": status},
                    "updateMask": "status",
                }]},
            )
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=resource,
                safe_metadata={"provider": "google_ads", "status": status.casefold()},
            )
        if isinstance(payload, ChangeAdBudgetPayload):
            campaign_resource = _google_resource(customer, "campaigns", payload.campaign_ref)
            query = (
                "SELECT campaign.campaign_budget FROM campaign WHERE "
                f"campaign.resource_name = '{campaign_resource}' LIMIT 1"
            )
            search = await self._http.request_json(
                "POST", f"{root}/googleAds:search", headers=ads_headers,
                json_body={"query": query, "pageSize": 1},
            )
            budget_resource = _google_budget_reference(search)
            await self._http.request_json(
                "POST",
                f"{root}/campaignBudgets:mutate",
                headers=ads_headers,
                json_body={"operations": [{
                    "update": {
                        "resourceName": budget_resource,
                        "amountMicros": str(int(payload.budget * 1_000_000)),
                    },
                    "updateMask": "amount_micros",
                }]},
            )
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=budget_resource,
                safe_metadata={"provider": "google_ads", "budget_period": payload.budget_period},
            )
        raise ConnectorRequestNotSentError("payload_invalid")

    def _meta_root(self) -> str:
        return f"https://graph.facebook.com/{self._configuration.meta_graph_api_version}"


def build_configured_action_adapters(
    configuration: Settings,
) -> dict[str, ProviderConnectorActionAdapter]:
    if (
        not configuration.external_connector_writes_enabled
        or configuration.integration_credential_backend == "disabled"
    ):
        return {}
    adapters: dict[str, ProviderConnectorActionAdapter] = {}
    if configuration.google_oauth_client_id and configuration.google_oauth_client_secret:
        adapters["gmail"] = ProviderConnectorActionAdapter(
            connector_type="gmail", configuration=configuration
        )
        if configuration.google_ads_developer_token is not None:
            adapters["google_ads"] = ProviderConnectorActionAdapter(
                connector_type="google_ads", configuration=configuration
            )
    if configuration.meta_oauth_client_id and configuration.meta_oauth_client_secret:
        for connector_type in (
            "whatsapp_business", "facebook", "instagram", "meta_ads"
        ):
            adapters[connector_type] = ProviderConnectorActionAdapter(
                connector_type=connector_type, configuration=configuration
            )
    return adapters


def _resource(resources, resource_type: str) -> str | None:
    for item in resources:
        if item.get("resource_type") == resource_type:
            value = item.get("external_reference")
            if isinstance(value, str) and value:
                return value
    return None


def _required_reference(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not item
        or len(item) > 255
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-" for character in item)
    ):
        raise RuntimeError("provider_outcome_unknown")
    return item


def _safe_public_media_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _google_resource(customer: str, collection: str, reference: str) -> str:
    prefix = f"customers/{customer}/{collection}/"
    if reference.startswith(prefix):
        return reference
    if not reference.isdigit():
        raise ConnectorRequestNotSentError("google_resource_invalid")
    return f"{prefix}{reference}"


def _google_campaign_reference(response: Mapping[str, object]) -> str:
    values = response.get("mutateOperationResponses")
    if not isinstance(values, list):
        raise RuntimeError("provider_outcome_unknown")
    for value in values:
        if not isinstance(value, dict):
            continue
        result = value.get("campaignResult")
        if isinstance(result, dict):
            reference = result.get("resourceName")
            if isinstance(reference, str):
                return _required_reference({"value": reference}, "value")
    raise RuntimeError("provider_outcome_unknown")


def _google_budget_reference(response: Mapping[str, object]) -> str:
    values = response.get("results")
    if not isinstance(values, list) or not values or not isinstance(values[0], dict):
        raise ConnectorRejectedError("campaign_not_found")
    campaign = values[0].get("campaign")
    if not isinstance(campaign, dict):
        raise ConnectorRejectedError("campaign_not_found")
    reference = campaign.get("campaignBudget")
    if not isinstance(reference, str):
        raise ConnectorRejectedError("campaign_budget_not_found")
    return _required_reference({"value": reference}, "value")
