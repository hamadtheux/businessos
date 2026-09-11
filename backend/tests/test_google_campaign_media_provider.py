import os
import unittest

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault(
    "AIBOS_AUTH_SECRET_KEY",
    "x" * 32,
)

from pydantic import SecretStr  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.action_boundary import (  # noqa: E402
    _MaterializedGoogleAdsCampaignPayload,
)
from app.integrations.credentials import (  # noqa: E402
    CredentialMaterial,
)
from app.integrations.provider_action_adapters import (  # noqa: E402
    ProviderConnectorActionAdapter,
    _google_standard_pmax_operations,
)
from app.schemas.ai_action_payload import (  # noqa: E402
    CampaignAudience,
    CampaignCreative,
)


TOKEN = CredentialMaterial(
    values={"access_token": "provider-token"}
)


class _Http:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def request_json(
        self,
        method,
        url,
        **kwargs,
    ):
        self.calls.append(
            {
                "method": method,
                "url": url,
                **kwargs,
            }
        )
        return self.response


def _payload():
    return _MaterializedGoogleAdsCampaignPayload(
        campaign_name="Launch",
        objective="sales",
        budget="20.00",
        currency="USD",
        budget_period="daily",
        audience=CampaignAudience(
            countries=["US"]
        ),
        creative=CampaignCreative(
            creative_refs=[
                "creative_asset:11111111-1111-1111-1111-111111111111"
            ],
            destination_url=(
                "https://example.com/shop"
            ),
        ),
        network="performance_max",
        business_name="Example Store",
        business_logo_ref=(
            "business_logo:"
            + ("a" * 64)
        ),
        headlines=[
            "Example Store",
            "Shop the collection",
            "Built for everyday use",
        ],
        long_headlines=[
            "Discover the approved Example Store collection",
        ],
        descriptions=[
            "Explore the approved collection and find the right product.",
            "Shop trusted products using the approved campaign experience.",
        ],
        campaign_media_bytes=(
            b"square-image-bytes",
            b"landscape-image-bytes",
        ),
        campaign_logo_bytes=(
            b"square-logo-bytes"
        ),
    )


class GoogleUploadedMediaPMaxTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_standard_pmax_contains_required_asset_contract(
        self,
    ):
        operations = (
            _google_standard_pmax_operations(
                "222",
                _payload(),
                "20000000",
            )
        )

        campaign = next(
            operation[
                "campaignOperation"
            ]["create"]
            for operation in operations
            if "campaignOperation"
            in operation
        )

        self.assertEqual(
            campaign["status"],
            "PAUSED",
        )
        self.assertEqual(
            campaign[
                "advertisingChannelType"
            ],
            "PERFORMANCE_MAX",
        )
        self.assertTrue(
            campaign[
                "brandGuidelinesEnabled"
            ]
        )
        self.assertEqual(
            campaign[
                "containsEuPoliticalAdvertising"
            ],
            "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        )

        campaign_assets = [
            operation[
                "campaignAssetOperation"
            ]["create"]
            for operation in operations
            if "campaignAssetOperation"
            in operation
        ]
        self.assertEqual(
            {
                item["fieldType"]
                for item in campaign_assets
            },
            {
                "BUSINESS_NAME",
                "LOGO",
            },
        )

        asset_group_assets = [
            operation[
                "assetGroupAssetOperation"
            ]["create"]
            for operation in operations
            if "assetGroupAssetOperation"
            in operation
        ]

        field_types = [
            item["fieldType"]
            for item in asset_group_assets
        ]

        self.assertEqual(
            field_types.count("HEADLINE"),
            3,
        )
        self.assertEqual(
            field_types.count("LONG_HEADLINE"),
            1,
        )
        self.assertEqual(
            field_types.count("DESCRIPTION"),
            2,
        )
        self.assertEqual(
            field_types.count(
                "MARKETING_IMAGE"
            ),
            1,
        )
        self.assertEqual(
            field_types.count(
                "SQUARE_MARKETING_IMAGE"
            ),
            1,
        )
        self.assertNotIn(
            "BUSINESS_NAME",
            field_types,
        )
        self.assertNotIn(
            "LOGO",
            field_types,
        )

        asset_group = next(
            operation[
                "assetGroupOperation"
            ]["create"]
            for operation in operations
            if "assetGroupOperation"
            in operation
        )
        self.assertEqual(
            asset_group["finalUrls"],
            ["https://example.com/shop"],
        )

    async def test_provider_sends_one_non_partial_atomic_mutate(
        self,
    ):
        response = {
            "mutateOperationResponses": [
                {
                    "campaignBudgetResult": {
                        "resourceName": (
                            "customers/222/"
                            "campaignBudgets/10"
                        )
                    }
                },
                {
                    "campaignResult": {
                        "resourceName": (
                            "customers/222/"
                            "campaigns/20"
                        )
                    }
                },
                {
                    "assetGroupResult": {
                        "resourceName": (
                            "customers/222/"
                            "assetGroups/30"
                        )
                    }
                },
            ]
        }

        http = _Http(response)
        configuration = settings.model_copy(
            update={
                "google_ads_developer_token": (
                    SecretStr(
                        "developer-token"
                    )
                ),
                "google_ads_api_version": "v25",
            }
        )

        adapter = (
            ProviderConnectorActionAdapter(
                connector_type="google_ads",
                configuration=configuration,
                http=http,
            )
        )

        result = await adapter.execute(
            credentials=TOKEN,
            action_type=(
                "create_google_ads_campaign"
            ),
            payload=_payload(),
            selected_resources=(
                {
                    "resource_type": (
                        "google_ads_customer"
                    ),
                    "external_reference": "222",
                },
            ),
            delivery_target=None,
            idempotency_key=(
                "campaign:uploaded:1"
            ),
        )

        self.assertEqual(
            len(http.calls),
            1,
        )

        request = http.calls[0]
        self.assertTrue(
            request["url"].endswith(
                "/googleAds:mutate"
            )
        )
        self.assertFalse(
            request["json_body"][
                "partialFailure"
            ]
        )
        self.assertEqual(
            result.external_reference_id,
            "customers/222/campaigns/20",
        )
        self.assertEqual(
            result.safe_metadata[
                "asset_group_reference"
            ],
            "customers/222/assetGroups/30",
        )


if __name__ == "__main__":
    unittest.main()
