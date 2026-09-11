import json
import os
import unittest
from decimal import Decimal

from pydantic import SecretStr

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.config import settings  # noqa: E402
from app.integrations.action_adapters import (  # noqa: E402
    ConnectorRequestNotSentError,
)
from app.integrations.credentials import CredentialMaterial  # noqa: E402
from app.integrations.provider_action_adapters import (  # noqa: E402
    ProviderConnectorActionAdapter,
)
from app.schemas.ai_action_payload import (  # noqa: E402
    CampaignAudience,
    CampaignCreative,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
)


TOKEN = CredentialMaterial(
    values={"access_token": "provider-token"}
)


class _MetaMediaPayload(CreateMetaCampaignPayload):
    campaign_media_urls: tuple[str, ...]


class _GoogleMediaPayload(CreateGoogleAdsCampaignPayload):
    campaign_media_urls: tuple[str, ...]


class _ScriptedHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request_json(self, method, url, **kwargs):
        self.calls.append(
            {"method": method, "url": url, **kwargs}
        )
        if not self.responses:
            raise AssertionError(
                f"Unexpected provider request: {method} {url}"
            )
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _media_urls():
    return (
        "https://objects.example.test/square.jpg?sig=1",
        "https://objects.example.test/portrait.jpg?sig=2",
        "https://objects.example.test/vertical.jpg?sig=3",
    )


class MetaCampaignMediaProviderTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_uploaded_image_creates_real_paused_meta_child_chain(
        self,
    ):
        http = _ScriptedHttp(
            [
                {"id": "100"},
                {"id": "200"},
                {"id": "300"},
                {"id": "400"},
            ]
        )
        configuration = settings.model_copy(
            update={"meta_graph_api_version": "v26.0"}
        )
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads",
            configuration=configuration,
            http=http,
        )

        payload = _MetaMediaPayload(
            campaign_name="9D Brain launch",
            objective="traffic",
            budget=Decimal("20"),
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(
                countries=["US"]
            ),
            creative=CampaignCreative(
                creative_refs=["creative_asset:asset-1"],
                destination_url="https://example.com/",
            ),
            primary_text="Grow your business.",
            headline="Meet 9D Brain",
            description="Your AI Business OS.",
            call_to_action="LEARN_MORE",
            campaign_media_urls=_media_urls(),
        )

        result = await adapter.execute(
            credentials=TOKEN,
            action_type="create_meta_campaign",
            payload=payload,
            selected_resources=(
                {
                    "resource_type": "ad_account",
                    "external_reference": "act_123",
                },
                {
                    "resource_type": "facebook_page",
                    "external_reference": "456",
                },
            ),
            delivery_target=None,
            idempotency_key="meta-media-1",
        )

        self.assertEqual(len(http.calls), 4)
        self.assertTrue(
            http.calls[0]["url"].endswith(
                "/act_123/campaigns"
            )
        )
        self.assertTrue(
            http.calls[1]["url"].endswith(
                "/act_123/adsets"
            )
        )
        self.assertTrue(
            http.calls[2]["url"].endswith(
                "/act_123/adcreatives"
            )
        )
        self.assertTrue(
            http.calls[3]["url"].endswith(
                "/act_123/ads"
            )
        )

        adset = http.calls[1]["data"]
        self.assertEqual(
            adset["optimization_goal"],
            "LINK_CLICKS",
        )
        self.assertEqual(adset["status"], "PAUSED")

        story = json.loads(
            http.calls[2]["data"]["object_story_spec"]
        )
        self.assertEqual(story["page_id"], "456")
        self.assertEqual(
            story["link_data"]["picture"],
            _media_urls()[0],
        )
        self.assertEqual(
            story["link_data"]["link"],
            "https://example.com/",
        )

        self.assertEqual(
            result.external_reference_id,
            "100",
        )
        self.assertEqual(
            result.safe_metadata["media_source"],
            "uploaded",
        )
        self.assertEqual(
            result.safe_metadata["ad_set_reference"],
            "200",
        )
        self.assertEqual(
            result.safe_metadata["creative_reference"],
            "300",
        )
        self.assertEqual(
            result.safe_metadata["ad_reference"],
            "400",
        )

    async def test_awareness_media_does_not_invent_destination(
        self,
    ):
        http = _ScriptedHttp(
            [
                {"id": "100"},
                {"id": "200"},
                {"id": "300"},
                {"id": "400"},
            ]
        )
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads",
            configuration=settings.model_copy(
                update={
                    "meta_graph_api_version": "v26.0"
                }
            ),
            http=http,
        )

        payload = _MetaMediaPayload(
            campaign_name="Awareness",
            objective="awareness",
            budget=Decimal("10"),
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(
                countries=["PK"]
            ),
            creative=CampaignCreative(
                creative_refs=["creative_asset:asset-2"],
            ),
            campaign_media_urls=_media_urls(),
        )

        await adapter.execute(
            credentials=TOKEN,
            action_type="create_meta_campaign",
            payload=payload,
            selected_resources=(
                {
                    "resource_type": "ad_account",
                    "external_reference": "act_123",
                },
                {
                    "resource_type": "facebook_page",
                    "external_reference": "456",
                },
            ),
            delivery_target=None,
            idempotency_key="meta-awareness-1",
        )

        story = json.loads(
            http.calls[2]["data"]["object_story_spec"]
        )
        self.assertNotIn("link_data", story)
        self.assertEqual(
            story["photo_data"]["url"],
            _media_urls()[0],
        )

    async def test_unsupported_media_objective_fails_before_provider_write(
        self,
    ):
        http = _ScriptedHttp([])
        adapter = ProviderConnectorActionAdapter(
            connector_type="meta_ads",
            configuration=settings.model_copy(
                update={
                    "meta_graph_api_version": "v26.0"
                }
            ),
            http=http,
        )

        payload = _MetaMediaPayload(
            campaign_name="Lead campaign",
            objective="leads",
            budget=Decimal("20"),
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(
                countries=["US"]
            ),
            creative=CampaignCreative(
                creative_refs=["creative_asset:asset-3"],
            ),
            campaign_media_urls=_media_urls(),
        )

        with self.assertRaisesRegex(
            ConnectorRequestNotSentError,
            "meta_campaign_objective_setup_required",
        ):
            await adapter.execute(
                credentials=TOKEN,
                action_type="create_meta_campaign",
                payload=payload,
                selected_resources=(
                    {
                        "resource_type": "ad_account",
                        "external_reference": "act_123",
                    },
                    {
                        "resource_type": "facebook_page",
                        "external_reference": "456",
                    },
                ),
                delivery_target=None,
                idempotency_key="meta-lead-1",
            )

        self.assertEqual(http.calls, [])

    async def test_google_media_fails_before_mutation_until_asset_contract_ready(
        self,
    ):
        http = _ScriptedHttp([])
        adapter = ProviderConnectorActionAdapter(
            connector_type="google_ads",
            configuration=settings.model_copy(
                update={
                    "google_ads_developer_token": SecretStr(
                        "developer-token"
                    ),
                    "google_ads_api_version": "v25",
                }
            ),
            http=http,
        )

        payload = _GoogleMediaPayload(
            campaign_name="Google image campaign",
            objective="traffic",
            budget=Decimal("20"),
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(
                countries=["US"]
            ),
            creative=CampaignCreative(
                creative_refs=["creative_asset:asset-4"],
                destination_url="https://example.com/",
            ),
            network="performance_max",
            campaign_media_urls=(
                "https://objects.example.test/square.jpg?sig=1",
                "https://objects.example.test/landscape.jpg?sig=2",
            ),
        )

        with self.assertRaisesRegex(
            ConnectorRequestNotSentError,
            "google_campaign_media_asset_upload_required",
        ):
            await adapter.execute(
                credentials=TOKEN,
                action_type="create_google_ads_campaign",
                payload=payload,
                selected_resources=(
                    {
                        "resource_type": "google_ads_customer",
                        "external_reference": "1234567890",
                    },
                ),
                delivery_target=None,
                idempotency_key="google-media-1",
            )

        self.assertEqual(http.calls, [])


if __name__ == "__main__":
    unittest.main()
