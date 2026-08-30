from __future__ import annotations

import os
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.integrations.ad_commerce_adapters import (  # noqa: E402
    AdCommerceProviderError,
    GoogleMerchantAdapter,
    MetaCatalogAdapter,
    google_product_input,
    meta_product_fields,
)
from app.integrations.ad_commerce_contracts import (  # noqa: E402
    NormalizedProductDestinationInput,
    ProductGroup,
)
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.integrations.oauth_adapters import _ProviderHttpError  # noqa: E402
from app.integrations.oauth_adapters import ConfiguredOAuthConnector  # noqa: E402
from app.integrations import action_adapters as _action_adapters  # noqa: E402,F401
from app.integrations.action_adapters import ConnectorRequestNotSentError  # noqa: E402
from app.integrations.provider_action_adapters import (  # noqa: E402
    ProviderConnectorActionAdapter,
    _google_retail_pmax_operations,
)
from app.schemas.ai_action_payload import (  # noqa: E402
    CampaignAudience,
    CampaignCreative,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
)
from app.services.marketing_actions import prepare_campaign_action  # noqa: E402
from app.services.ad_commerce import (  # noqa: E402
    _normalize_product,
    _platform_managed_destination_name,
)
from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.schemas.marketing import AudienceCreate, CampaignCreate  # noqa: E402
from app.services.marketing import create_audience, create_campaign  # noqa: E402
from pydantic import SecretStr  # noqa: E402


TOKEN = CredentialMaterial(values={"access_token": "provider-token"})


class _ScriptedHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def request_json(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"Unexpected provider request: {method} {url}")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _product() -> NormalizedProductDestinationInput:
    return NormalizedProductDestinationInput(
        offer_id="EGGS-12",
        title="Premium Farm Eggs",
        description="Twelve fresh eggs.",
        link="https://shop.example/products/eggs",
        image_link="https://cdn.example/eggs.jpg",
        additional_image_links=("https://cdn.example/eggs-side.jpg",),
        availability="in_stock",
        price=Decimal("12.34"),
        sale_price=Decimal("10.00"),
        currency="USD",
        feed_label="US",
        brand="Acme Farm",
    )


class GoogleMerchantContractTests(unittest.IsolatedAsyncioTestCase):
    def test_managed_destination_rebrands_legacy_platform_prefix_once(self) -> None:
        self.assertEqual(
            _platform_managed_destination_name("AI Business OS · Store"),
            "9D Brain · Store",
        )
        self.assertEqual(
            _platform_managed_destination_name("9D Frame · Store"),
            "9D Brain · Store",
        )
        self.assertEqual(
            _platform_managed_destination_name("9DFrame · Store"),
            "9D Brain · Store",
        )
        self.assertEqual(
            _platform_managed_destination_name("9D Brain · Store"),
            "9D Brain · Store",
        )

    def test_product_input_uses_current_v1_shape_without_invented_identifiers(self) -> None:
        value = google_product_input(_product())
        self.assertEqual(value["offerId"], "EGGS-12")
        self.assertEqual(value["contentLanguage"], "en")
        self.assertEqual(value["feedLabel"], "US")
        attributes = value["productAttributes"]
        self.assertEqual(attributes["price"], {"amountMicros": "12340000", "currencyCode": "USD"})
        self.assertEqual(attributes["salePrice"], {"amountMicros": "10000000", "currencyCode": "USD"})
        self.assertNotIn("gtin", attributes)
        self.assertNotIn("mpn", attributes)
        self.assertNotIn("shipping", attributes)

    async def test_account_discovery_is_paginated_and_never_silently_selects(self) -> None:
        http = _ScriptedHttp([
            {"accounts": [{"name": "accounts/111", "accountName": "Primary"}], "nextPageToken": "next"},
            {"accounts": [{"name": "accounts/222", "accountName": "Secondary"}]},
        ])
        accounts = await GoogleMerchantAdapter(http=http).list_accounts(TOKEN)
        self.assertEqual([item.external_reference for item in accounts], ["111", "222"])
        self.assertEqual(http.calls[1]["params"]["pageToken"], "next")

    async def test_data_source_create_and_product_insert_are_real_v1_requests(self) -> None:
        http = _ScriptedHttp([
            {
                "name": "accounts/111/dataSources/9",
                "displayName": "9D Brain · Store",
                "primaryProductDataSource": {"contentLanguage": "en"},
            },
            {"name": "accounts/111/productInputs/en~US~EGGS-12"},
        ])
        adapter = GoogleMerchantAdapter(http=http)
        destination = await adapter.create_managed_destination(
            TOKEN, account_reference="111", display_name="9D Brain · Store",
            content_language="en", feed_label="US",
        )
        result = await adapter.upsert_product(
            TOKEN, account_reference="111", destination_reference=destination.external_reference,
            product=_product(), idempotency_key="sync:eggs",
        )
        self.assertTrue(destination.managed)
        self.assertEqual(result.state, "submitted")
        self.assertNotEqual(result.state, "eligible")
        insert = http.calls[1]
        self.assertEqual(
            insert["url"],
            "https://merchantapi.googleapis.com/products/v1/accounts/111/productInputs:insert",
        )
        self.assertEqual(insert["params"]["dataSource"], "accounts/111/dataSources/9")
        self.assertEqual(insert["json_body"]["productAttributes"]["title"], "Premium Farm Eggs")

    async def test_processed_product_reconciliation_uses_country_status_and_source(self) -> None:
        http = _ScriptedHttp([{
            "products": [
                {
                    "name": "accounts/111/products/en~US~EGGS-12",
                    "offerId": "EGGS-12",
                    "dataSource": "accounts/111/dataSources/9",
                    "productStatus": {
                        "destinationStatuses": [{"approvedCountries": ["US"], "pendingCountries": [], "disapprovedCountries": []}],
                        "itemLevelIssues": [],
                    },
                },
                {
                    "name": "accounts/111/products/en~US~OTHER",
                    "offerId": "OTHER",
                    "dataSource": "accounts/111/dataSources/99",
                    "productStatus": {"destinationStatuses": [{"approvedCountries": ["US"]}]},
                },
            ]
        }])
        statuses = await GoogleMerchantAdapter(http=http).reconcile_products(
            TOKEN, account_reference="111", destination_reference="accounts/111/dataSources/9",
        )
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0].offer_id, "EGGS-12")
        self.assertEqual(statuses[0].state, "eligible")

    async def test_disapproval_and_issue_resolution_are_truthful_and_bounded(self) -> None:
        http = _ScriptedHttp([{"products": [{
            "name": "accounts/111/products/en~US~EGGS-12", "offerId": "EGGS-12",
            "dataSource": "accounts/111/dataSources/9",
            "productStatus": {
                "destinationStatuses": [{"disapprovedCountries": ["US"]}],
                "itemLevelIssues": [{
                    "code": "landing_page_mismatch", "severity": "DISAPPROVED",
                    "description": "Landing page price differs", "attribute": "link",
                }],
            },
        }]}])
        status = (await GoogleMerchantAdapter(http=http).reconcile_products(
            TOKEN, account_reference="111", destination_reference="accounts/111/dataSources/9",
        ))[0]
        self.assertEqual(status.state, "ineligible")
        self.assertEqual(status.issues[0].resolution, "store_source_update_required")

    async def test_unowned_archive_is_refused_without_provider_call(self) -> None:
        http = _ScriptedHttp([])
        result = await GoogleMerchantAdapter(http=http).archive_product(
            TOKEN, account_reference="111", destination_reference="accounts/111/dataSources/9",
            offer_id="EGGS-12", external_product_reference="accounts/111/productInputs/en~US~EGGS-12",
            owned=False, idempotency_key="archive",
        )
        self.assertFalse(result.acknowledged)
        self.assertEqual(result.state, "attention_required")
        self.assertEqual(http.calls, [])

    async def test_rate_limit_after_mutation_is_uncertain_not_blindly_retryable(self) -> None:
        adapter = GoogleMerchantAdapter(http=_ScriptedHttp([_ProviderHttpError(429)]))
        with self.assertRaises(AdCommerceProviderError) as raised:
            await adapter.upsert_product(
                TOKEN, account_reference="111", destination_reference="accounts/111/dataSources/9",
                product=_product(), idempotency_key="sync:eggs",
            )
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertTrue(raised.exception.uncertain)
        self.assertFalse(raised.exception.retryable)


class MetaCatalogContractTests(unittest.IsolatedAsyncioTestCase):
    def test_product_fields_preserve_authoritative_minor_units(self) -> None:
        value = meta_product_fields(_product())
        self.assertEqual(value["retailer_id"], "EGGS-12")
        self.assertEqual(value["price"], "1234")
        self.assertEqual(value["sale_price"], "1000")
        self.assertEqual(value["currency"], "USD")
        self.assertNotIn("gtin", value)

    async def test_discovery_follows_provider_pagination(self) -> None:
        http = _ScriptedHttp([
            {"data": [{"id": "1", "name": "One"}], "paging": {"next": "https://graph.facebook.com/v26.0/me/businesses?after=x"}},
            {"data": [{"id": "2", "name": "Two"}]},
        ])
        accounts = await MetaCatalogAdapter(http=http).list_accounts(TOKEN)
        self.assertEqual([item.external_reference for item in accounts], ["1", "2"])
        self.assertEqual(http.calls[1]["url"], "https://graph.facebook.com/v26.0/me/businesses?after=x")

    async def test_repeated_product_sync_updates_existing_retailer_id(self) -> None:
        http = _ScriptedHttp([
            {"data": [{"id": "900", "retailer_id": "EGGS-12"}]},
            {"success": True},
        ])
        result = await MetaCatalogAdapter(http=http).upsert_product(
            TOKEN, account_reference="10", destination_reference="20",
            product=_product(), idempotency_key="sync:eggs",
        )
        self.assertEqual(result.external_product_reference, "900")
        self.assertEqual(http.calls[1]["url"], "https://graph.facebook.com/v26.0/900")
        self.assertEqual(http.calls[1]["data"]["retailer_id"], "EGGS-12")

    async def test_product_set_updates_by_deterministic_internal_name(self) -> None:
        http = _ScriptedHttp([
            {"data": [{"id": "500", "name": "Best sellers"}]},
            {"success": True},
        ])
        result = await MetaCatalogAdapter(http=http).upsert_product_group(
            TOKEN, account_reference="10",
            group=ProductGroup(
                external_key="best-sellers", name="Best sellers",
                rule={"catalog_reference": "20"}, offer_ids=("EGGS-12", "MILK-1"),
            ),
            idempotency_key="group:best-sellers",
        )
        self.assertEqual(result.external_reference, "500")
        self.assertIn('"retailer_id":{"is_any":["EGGS-12","MILK-1"]}', http.calls[1]["data"]["filter"])


class AdvertisingProviderContractTests(unittest.IsolatedAsyncioTestCase):
    def test_sale_mapping_preserves_regular_and_current_prices(self) -> None:
        product = SimpleNamespace(
            id=uuid4(), sku="EGGS-12", name="Eggs", description="Fresh eggs",
            product_url="https://shop.example/eggs", availability="in_stock",
            price=Decimal("10.00"), compare_at_price=Decimal("12.34"), currency="USD",
            brand="Farm", gtin=None, mpn=None, condition="new",
            google_product_category=None, tags=[],
        )
        destination = SimpleNamespace(content_language="en", feed_label="US")
        normalized, issues = _normalize_product(
            product, ["https://cdn.example/eggs.jpg"], destination,
        )
        self.assertEqual(issues, ())
        self.assertEqual(normalized.price, Decimal("12.34"))
        self.assertEqual(normalized.sale_price, Decimal("10.00"))

    async def test_google_performance_sync_follows_page_tokens(self) -> None:
        row = {
            "campaign": {"id": "123"}, "segments": {"date": "2026-08-01", "productItemId": "EGGS-12"},
            "metrics": {"costMicros": "1000000", "impressions": "10", "clicks": "2", "conversions": "1", "conversionsValue": "12.34"},
        }
        http = _ScriptedHttp([
            {"results": [row], "nextPageToken": "page-2"},
            {"results": [{**row, "segments": {"date": "2026-08-02", "productItemId": "EGGS-12"}}]},
        ])
        configuration = settings.model_copy(update={
            "google_ads_developer_token": SecretStr("developer-token"),
            "google_ads_api_version": "v25",
        })
        adapter = ConfiguredOAuthConnector(
            connector_type="google_ads", provider="google", client_id="client",
            client_secret="secret", configuration=configuration, http=http,
        )
        from datetime import date
        values = await adapter.read_campaign_performance(
            TOKEN, account_reference="111", period_start=date(2026, 8, 1), period_end=date(2026, 8, 2),
        )
        self.assertEqual(len(values), 2)
        self.assertEqual(http.calls[1]["json_body"]["pageToken"], "page-2")
        self.assertEqual(values[0].external_product_reference, "EGGS-12")

    async def test_sensitive_targeting_fails_before_campaign_or_audience_persistence(self) -> None:
        session = AsyncMock()
        with self.assertRaises(MarketingValidationError):
            await create_campaign(
                session, business_id=uuid4(), actor_user_id=uuid4(),
                data=CampaignCreate(
                    name="Unsafe", objective="Grow", audience_definition="People inferred to have a health condition",
                    channels=["meta"], planned_budget=Decimal("10"),
                ),
            )
        with self.assertRaises(MarketingValidationError):
            await create_audience(
                session, business_id=uuid4(), actor_user_id=uuid4(),
                data=AudienceCreate(name="Unsafe", interests=["political ideology"]),
            )
        session.add.assert_not_called()

    async def test_manual_campaign_preparation_preserves_legacy_non_commerce_path(self) -> None:
        campaign = SimpleNamespace(
            id=uuid4(), channels=["google_ads"], campaign_type=None,
            name="Brand search", objective="Drive qualified traffic",
            planned_budget=Decimal("20"), currency="USD", budget_mode="daily",
            landing_destination="https://shop.example", geographic_targeting=["US"],
            audience_hypothesis_id=None,
        )
        session = AsyncMock()
        session.execute.return_value = SimpleNamespace(all=lambda: [])
        expected = {"proposal": "prepared"}
        with patch(
            "app.services.marketing_actions.get_campaign", new=AsyncMock(return_value=campaign),
        ), patch(
            "app.services.marketing_actions._existing_link", new=AsyncMock(return_value=None),
        ), patch(
            "app.services.marketing_actions._trusted_campaign_audience",
            new=AsyncMock(return_value=(["US"], None, None)),
        ), patch(
            "app.services.marketing_actions.preflight_campaign", new=AsyncMock(),
        ) as preflight, patch(
            "app.services.marketing_actions._materialize_governed_proposal",
            new=AsyncMock(return_value=expected),
        ) as materialize:
            result = await prepare_campaign_action(
                session, business_id=uuid4(), campaign_id=campaign.id,
                requested_by_user_id=uuid4(), channel="google_ads",
            )
        self.assertEqual(result, expected)
        preflight.assert_not_awaited()
        payload = materialize.await_args.kwargs["action"].action_payload
        self.assertEqual(payload.network, "search")
        self.assertIsNone(payload.merchant_account_ref)

    def test_google_retail_pmax_has_complete_listing_group_tree(self) -> None:
        payload = CreateGoogleAdsCampaignPayload(
            campaign_name="Egg growth", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["marketing-campaign:1"],
                destination_url="https://shop.example/products/eggs",
            ),
            network="performance_max", merchant_account_ref="111",
            product_offer_ids=["EGGS-12", "EGGS-24"],
        )
        operations = _google_retail_pmax_operations("222", payload, "20000000")
        campaign = operations[1]["campaignOperation"]["create"]
        self.assertEqual(campaign["advertisingChannelType"], "PERFORMANCE_MAX")
        self.assertEqual(campaign["shoppingSetting"]["merchantId"], "111")
        filters = [item["assetGroupListingGroupFilterOperation"]["create"] for item in operations[3:]]
        self.assertEqual(filters[0]["type"], "SUBDIVISION")
        self.assertEqual([item["type"] for item in filters[1:]], ["UNIT_INCLUDED", "UNIT_INCLUDED", "UNIT_EXCLUDED"])
        self.assertTrue(all(item.get("parentListingGroupFilter") == filters[0]["resourceName"] for item in filters[1:]))

    async def test_google_retail_execution_returns_durable_child_references(self) -> None:
        http = _ScriptedHttp([{"mutateOperationResponses": [
            {"campaignBudgetResult": {"resourceName": "customers/222/campaignBudgets/10"}},
            {"campaignResult": {"resourceName": "customers/222/campaigns/20"}},
            {"assetGroupResult": {"resourceName": "customers/222/assetGroups/30"}},
            {"assetGroupListingGroupFilterResult": {"resourceName": "customers/222/assetGroupListingGroupFilters/40"}},
        ]}])
        configuration = settings.model_copy(update={
            "google_ads_developer_token": SecretStr("developer-token"),
            "google_ads_api_version": "v25",
        })
        adapter = ProviderConnectorActionAdapter(
            connector_type="google_ads", configuration=configuration, http=http,
        )
        payload = CreateGoogleAdsCampaignPayload(
            campaign_name="Egg growth", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["marketing-campaign:1"],
                destination_url="https://shop.example/products/eggs",
            ),
            network="performance_max", merchant_account_ref="111",
            product_offer_ids=["EGGS-12"],
        )
        result = await adapter.execute(
            credentials=TOKEN, action_type="create_google_ads_campaign", payload=payload,
            selected_resources=({"resource_type": "google_ads_customer", "external_reference": "222"},),
            delivery_target=None, idempotency_key="campaign:google:1",
        )
        self.assertEqual(result.external_reference_id, "customers/222/campaigns/20")
        self.assertEqual(result.safe_metadata["budget_reference"], "customers/222/campaignBudgets/10")
        self.assertEqual(result.safe_metadata["asset_group_reference"], "customers/222/assetGroups/30")
        self.assertEqual(result.safe_metadata["listing_group_reference"], "customers/222/assetGroupListingGroupFilters/40")

    async def test_meta_catalog_campaign_creates_paused_full_child_chain(self) -> None:
        http = _ScriptedHttp([
            {"id": "100"}, {"id": "200"}, {"id": "300"}, {"id": "400"},
        ])
        configuration = settings.model_copy(update={"meta_graph_api_version": "v26.0"})
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads", configuration=configuration, http=http,
        )
        payload = CreateMetaCampaignPayload(
            campaign_name="Egg growth", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["marketing-campaign:1"],
                destination_url="https://shop.example/products/eggs",
            ),
            catalog_ref="20", product_set_ref="500", page_ref="600",
            conversion_dataset_ref="700", primary_text="Fresh eggs", headline="Shop eggs",
        )
        result = await adapter.execute(
            credentials=TOKEN, action_type="create_meta_campaign", payload=payload,
            selected_resources=({"resource_type": "ad_account", "external_reference": "act_10"},),
            delivery_target=None, idempotency_key="campaign:1",
        )
        self.assertEqual(result.external_reference_id, "100")
        self.assertEqual(result.safe_metadata["ad_reference"], "400")
        self.assertEqual([call["url"].rsplit("/", 1)[-1] for call in http.calls], ["campaigns", "adsets", "adcreatives", "ads"])
        self.assertTrue(all(call["data"]["status"] == "PAUSED" for call in (http.calls[0], http.calls[1], http.calls[3])))

    async def test_meta_catalog_assets_are_validated_before_any_provider_mutation(self) -> None:
        http = _ScriptedHttp([])
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads",
            configuration=settings.model_copy(update={"meta_graph_api_version": "v26.0"}),
            http=http,
        )
        payload = CreateMetaCampaignPayload(
            campaign_name="Incomplete", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(creative_refs=["campaign:1"]),
            catalog_ref="20",
        )
        with self.assertRaises(ConnectorRequestNotSentError):
            await adapter.execute(
                credentials=TOKEN, action_type="create_meta_campaign", payload=payload,
                selected_resources=({"resource_type": "ad_account", "external_reference": "act_10"},),
                delivery_target=None, idempotency_key="campaign:incomplete",
            )
        self.assertEqual(http.calls, [])

    async def test_meta_child_failure_after_campaign_create_is_unknown_not_retryable(self) -> None:
        http = _ScriptedHttp([{"id": "100"}, _ProviderHttpError(400)])
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads",
            configuration=settings.model_copy(update={"meta_graph_api_version": "v26.0"}),
            http=http,
        )
        payload = CreateMetaCampaignPayload(
            campaign_name="Partial", objective="sales", budget=Decimal("20"),
            currency="USD", budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["campaign:1"], destination_url="https://shop.example/eggs",
            ),
            catalog_ref="20", product_set_ref="500", page_ref="600",
            conversion_dataset_ref="700",
        )
        with self.assertRaisesRegex(RuntimeError, "provider_outcome_unknown"):
            await adapter.execute(
                credentials=TOKEN, action_type="create_meta_campaign", payload=payload,
                selected_resources=({"resource_type": "ad_account", "external_reference": "act_10"},),
                delivery_target=None, idempotency_key="campaign:partial",
            )
        self.assertEqual(len(http.calls), 2)


if __name__ == "__main__":
    unittest.main()
