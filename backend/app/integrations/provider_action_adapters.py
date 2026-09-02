from __future__ import annotations

import base64
from email.message import EmailMessage
import json
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
    "facebook": frozenset({"publish_social_post", "send_customer_message"}),
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
            if (
                self.connector_type == "facebook"
                and action_type == "send_customer_message"
            ):
                return await self._send_meta_customer_message(
                    payload, delivery_target, selected_resources, token
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
        body = {"raw": raw}
        if isinstance(payload, SendEmailPayload) and payload.thread_ref:
            body["threadId"] = payload.thread_ref
        response = await self._http.request_json(
            "POST", _GMAIL_SEND, headers=headers, json_body=body
        )
        reference = _required_reference(response, "id")
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id=reference,
            safe_metadata={"provider": "gmail", "delivery_status": "submitted"},
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
            safe_metadata={"provider": "whatsapp_business", "delivery_status": "submitted"},
        )

    async def _send_meta_customer_message(self, payload, target, resources, token):
        if not isinstance(payload, SendCustomerMessagePayload):
            raise ConnectorRequestNotSentError("payload_invalid")
        if not target or not payload.channel_resource_ref:
            raise ConnectorRequestNotSentError("delivery_target_required")
        if self.connector_type != "facebook":
            raise ConnectorRequestNotSentError(
                "customer_messaging_not_configured"
            )
        resource_type = "facebook_page"
        resource = _resource(
            resources,
            resource_type,
            expected_reference=payload.channel_resource_ref,
        )
        if resource is None:
            raise ConnectorRequestNotSentError("channel_resource_not_selected")
        token_response = await self._http.request_json(
            "GET",
            f"{self._meta_root()}/{resource}",
            params={"fields": "access_token", "access_token": token},
        )
        provider_token = token_response.get("access_token")
        if not isinstance(provider_token, str) or not provider_token:
            raise ConnectorRequestNotSentError(
                "page_access_token_unavailable"
            )
        response = await self._http.request_json(
            "POST",
            f"{self._meta_root()}/{resource}/messages",
            headers={"Authorization": f"Bearer {provider_token}"},
            json_body={
                "recipient": {"id": target},
                "messaging_type": "RESPONSE",
                "message": {"text": payload.message},
            },
        )
        reference = _required_reference(response, "message_id")
        return ConnectorActionResult(
            succeeded=True,
            external_reference_id=reference,
            safe_metadata={
                "provider": self.connector_type,
                "delivery_status": "submitted",
            },
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
            commerce_values = (
                payload.catalog_ref,
                payload.product_set_ref,
                payload.page_ref,
                payload.conversion_dataset_ref,
            )
            commerce_campaign = any(commerce_values)
            if commerce_campaign and not all(
                (*commerce_values, payload.creative.destination_url)
            ):
                # Validate the whole catalog-sales chain before creating even
                # the paused provider campaign. A local validation failure must
                # never leave an orphan provider object behind.
                raise ConnectorRequestNotSentError("meta_catalog_assets_required")
            if commerce_campaign and payload.budget_period != "daily":
                raise ConnectorRequestNotSentError("daily_budget_required")
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
            campaign_reference = _required_reference(response, "id")
            if commerce_campaign:
                try:
                    ad_set = await self._http.request_json(
                        "POST", f"{self._meta_root()}/{account}/adsets", headers=headers,
                        data={
                            "name": f"{payload.campaign_name} products"[:200],
                            "campaign_id": campaign_reference,
                            "daily_budget": str(int(payload.budget * 100)),
                            "billing_event": "IMPRESSIONS",
                            "optimization_goal": "OFFSITE_CONVERSIONS",
                            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                            "destination_type": "WEBSITE",
                            "targeting": json.dumps({"geo_locations": {"countries": payload.audience.countries}}, separators=(",", ":")),
                            "promoted_object": json.dumps({
                                "product_set_id": payload.product_set_ref,
                                "pixel_id": payload.conversion_dataset_ref,
                                "custom_event_type": "PURCHASE",
                            }, separators=(",", ":")),
                            "status": "PAUSED",
                        },
                    )
                    ad_set_reference = _required_reference(ad_set, "id")
                    link_data = {
                        "link": payload.creative.destination_url,
                        "message": payload.primary_text or payload.campaign_name,
                        "name": payload.headline or payload.campaign_name,
                        "description": payload.description or "",
                        "call_to_action": {"type": payload.call_to_action, "value": {"link": payload.creative.destination_url}},
                    }
                    creative = await self._http.request_json(
                        "POST", f"{self._meta_root()}/{account}/adcreatives", headers=headers,
                        data={
                            "name": f"{payload.campaign_name} creative"[:200],
                            "product_set_id": payload.product_set_ref,
                            "object_story_spec": json.dumps({
                                "page_id": payload.page_ref,
                                "template_data": link_data,
                            }, separators=(",", ":")),
                        },
                    )
                    creative_reference = _required_reference(creative, "id")
                    ad = await self._http.request_json(
                        "POST", f"{self._meta_root()}/{account}/ads", headers=headers,
                        data={
                            "name": f"{payload.campaign_name} ad"[:200],
                            "adset_id": ad_set_reference,
                            "creative": json.dumps({"creative_id": creative_reference}),
                            "status": "PAUSED",
                        },
                    )
                    ad_reference = _required_reference(ad, "id")
                except (
                    _ProviderHttpError,
                    IntegrationProviderUnavailableError,
                    ConnectorRequestNotSentError,
                    RuntimeError,
                ):
                    # The campaign exists, so any later failure has an unknown
                    # external outcome. The dispatcher must not blind-retry.
                    raise RuntimeError("provider_outcome_unknown") from None
                return ConnectorActionResult(
                    succeeded=True, external_reference_id=campaign_reference,
                    safe_metadata={
                        "provider": "meta_ads", "status": "provider_pending",
                        "ad_set_reference": ad_set_reference,
                        "creative_reference": creative_reference,
                        "ad_reference": ad_reference,
                    },
                )
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=campaign_reference,
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
            if payload.network == "performance_max" and payload.merchant_account_ref:
                operations = _google_retail_pmax_operations(customer, payload, micros)
            else:
                operations = [
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
                ]
            response = await self._http.request_json(
                "POST",
                f"{root}/googleAds:mutate",
                headers=ads_headers,
                json_body={
                    "mutateOperations": operations,
                    "partialFailure": False,
                    "responseContentType": "RESOURCE_NAME_ONLY",
                },
            )
            reference = _google_campaign_reference(response)
            metadata: dict[str, str | int | bool] = {
                "provider": "google_ads", "status": "paused",
            }
            if payload.network == "performance_max" and payload.merchant_account_ref:
                metadata.update(_google_child_references(response))
            return ConnectorActionResult(
                succeeded=True,
                external_reference_id=reference,
                safe_metadata=metadata,
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
        or configuration.external_connector_write_mode != "enabled"
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


def _google_retail_pmax_operations(customer: str, payload, micros: str) -> list[dict[str, object]]:
    merchant = payload.merchant_account_ref
    if merchant is None or not merchant.isdigit() or not payload.creative.destination_url:
        raise ConnectorRequestNotSentError("merchant_and_landing_page_required")
    budget = f"customers/{customer}/campaignBudgets/-1"
    campaign = f"customers/{customer}/campaigns/-2"
    asset_group = f"customers/{customer}/assetGroups/-3"
    operations: list[dict[str, object]] = [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget,
            "name": f"{payload.campaign_name} budget",
            "amountMicros": micros,
            "deliveryMethod": "STANDARD",
            "explicitlyShared": False,
        }}},
        {"campaignOperation": {"create": {
            "resourceName": campaign,
            "name": payload.campaign_name,
            "status": "PAUSED",
            "advertisingChannelType": "PERFORMANCE_MAX",
            "campaignBudget": budget,
            "maximizeConversionValue": {},
            "shoppingSetting": {
                "merchantId": merchant,
                "campaignPriority": 0,
                "enableLocal": False,
            },
        }}},
        {"assetGroupOperation": {"create": {
            "resourceName": asset_group,
            "name": f"{payload.campaign_name} products",
            "campaign": campaign,
            "finalUrls": [payload.creative.destination_url],
            "status": "PAUSED",
        }}},
    ]
    if payload.product_offer_ids:
        root = f"customers/{customer}/assetGroupListingGroupFilters/-4"
        operations.append({"assetGroupListingGroupFilterOperation": {"create": {
            "resourceName": root,
            "assetGroup": asset_group,
            "type": "SUBDIVISION",
            "listingSource": "SHOPPING",
        }}})
        next_temp = -5
        for offer_id in payload.product_offer_ids:
            operations.append({"assetGroupListingGroupFilterOperation": {"create": {
                "resourceName": f"customers/{customer}/assetGroupListingGroupFilters/{next_temp}",
                "assetGroup": asset_group,
                "parentListingGroupFilter": root,
                "type": "UNIT_INCLUDED",
                "listingSource": "SHOPPING",
                "caseValue": {"productItemId": {"value": offer_id}},
            }}})
            next_temp -= 1
        # The mandatory other-case unit closes the subdivision tree and is excluded.
        operations.append({"assetGroupListingGroupFilterOperation": {"create": {
            "resourceName": f"customers/{customer}/assetGroupListingGroupFilters/{next_temp}",
            "assetGroup": asset_group,
            "parentListingGroupFilter": root,
            "type": "UNIT_EXCLUDED",
            "listingSource": "SHOPPING",
            "caseValue": {"productItemId": {}},
        }}})
    else:
        operations.append({"assetGroupListingGroupFilterOperation": {"create": {
            "resourceName": f"customers/{customer}/assetGroupListingGroupFilters/-4",
            "assetGroup": asset_group,
            "type": "UNIT_INCLUDED",
            "listingSource": "SHOPPING",
        }}})
    return operations


def _resource(
    resources,
    resource_type: str,
    *,
    expected_reference: str | None = None,
) -> str | None:
    for item in resources:
        if item.get("resource_type") == resource_type:
            value = item.get("external_reference")
            if (
                isinstance(value, str)
                and value
                and (expected_reference is None or value == expected_reference)
            ):
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


def _google_child_references(response: Mapping[str, object]) -> dict[str, str]:
    values = response.get("mutateOperationResponses")
    if not isinstance(values, list):
        return {}
    result: dict[str, str] = {}
    keys = {
        "campaignBudgetResult": "budget_reference",
        "assetGroupResult": "asset_group_reference",
        "assetGroupListingGroupFilterResult": "listing_group_reference",
    }
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for provider_key, normalized_key in keys.items():
            candidate = value.get(provider_key)
            reference = candidate.get("resourceName") if isinstance(candidate, Mapping) else None
            if isinstance(reference, str) and normalized_key not in result:
                result[normalized_key] = _required_reference({"value": reference}, "value")
    return result


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
