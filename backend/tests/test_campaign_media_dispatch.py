import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.integrations.action_boundary import (  # noqa: E402
    _materialize_campaign_media_payload,
)
from app.services.business_branding import (  # noqa: E402
    business_logo_reference,
)
from app.schemas.ai_action_payload import (  # noqa: E402
    CampaignAudience,
    CampaignCreative,
    CreateGoogleAdsCampaignPayload,
    CreateMetaCampaignPayload,
)


class _Session:
    def __init__(self, values):
        self.values = list(values)

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None


class _Storage:
    def __init__(self, references, objects=None):
        self.references = references
        self.objects = objects or {}
        self.presented = []
        self.reads = []

    def object_key_from_reference(self, reference):
        return self.references[reference]

    async def get(self, object_key, *, max_bytes):
        self.reads.append((object_key, max_bytes))
        content = self.objects[object_key]
        if len(content) > max_bytes:
            raise ValueError("object too large")
        return content

    def presentation_url(self, object_key, *, expires_in_seconds):
        self.presented.append((object_key, expires_in_seconds))
        return (
            "https://objects.example.test/"
            + object_key
            + "?X-Amz-Signature="
            + ("a" * 128)
        )


def _logo_bytes(width=320, height=120):
    image = Image.new(
        "RGBA",
        (width, height),
        (20, 40, 80, 255),
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _asset(business_id, campaign_id, asset_id):
    base = (
        f"businesses/{business_id}/marketing/uploads/{asset_id}/variants"
    )
    refs = {
        name: f"https://media.example.test/{base}/{name}.jpg"
        for name in (
            "square_1_1",
            "landscape_1_91_1",
            "portrait_4_5",
            "vertical_9_16",
        )
    }
    return (
        SimpleNamespace(
            id=asset_id,
            business_id=business_id,
            campaign_id=campaign_id,
            source_type="import",
            generation_status="ready",
            storage_reference=(
                f"https://media.example.test/"
                f"businesses/{business_id}/marketing/uploads/"
                f"{asset_id}/source.png"
            ),
            media_type="image",
            creative_metadata={
                "variants": {
                    name: {"storage_reference": ref}
                    for name, ref in refs.items()
                }
            },
        ),
        {
            ref: f"{base}/{name}.jpg"
            for name, ref in refs.items()
        },
    )


class CampaignMediaDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_meta_uses_exact_campaign_bound_upload_variants(self):
        business_id = uuid4()
        campaign_id = uuid4()
        action_id = uuid4()
        asset_id = uuid4()

        asset, references = _asset(
            business_id,
            campaign_id,
            asset_id,
        )
        proposal = SimpleNamespace(entity_id=campaign_id)
        storage = _Storage(references)

        payload = CreateMetaCampaignPayload(
            campaign_name="Launch",
            objective="traffic",
            budget="20.00",
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=[f"creative_asset:{asset_id}"],
                destination_url="https://example.com/",
            ),
        )

        result = await _materialize_campaign_media_payload(
            _Session([proposal, asset]),
            business_id=business_id,
            action_id=action_id,
            action_type="create_meta_campaign",
            payload=payload,
            storage=storage,
        )

        self.assertEqual(
            result.creative.creative_refs,
            [f"creative_asset:{asset_id}"],
        )
        self.assertEqual(len(result.campaign_media_urls), 3)
        self.assertEqual(len(storage.presented), 3)
        self.assertTrue(
            all(url.startswith("https://") for url in result.campaign_media_urls)
        )

    async def test_google_materializes_private_image_and_logo_bytes(self):
        business_id = uuid4()
        campaign_id = uuid4()
        action_id = uuid4()
        asset_id = uuid4()

        asset, references = _asset(
            business_id,
            campaign_id,
            asset_id,
        )
        proposal = SimpleNamespace(
            entity_id=campaign_id
        )

        logo_key = (
            f"businesses/{business_id}/"
            "branding/logo/current.png"
        )
        branding = SimpleNamespace(
            business_id=business_id,
            logo_storage_key=logo_key,
        )
        logo_ref = business_logo_reference(
            branding,
            business_id=business_id,
        )

        objects = {
            object_key: (
                b"square-image"
                if "square_1_1" in object_key
                else b"landscape-image"
            )
            for object_key in references.values()
        }
        objects[logo_key] = _logo_bytes()

        storage = _Storage(
            references,
            objects=objects,
        )

        payload = CreateGoogleAdsCampaignPayload(
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
                    f"creative_asset:{asset_id}"
                ],
                destination_url=(
                    "https://example.com/"
                ),
            ),
            network="performance_max",
            business_name="Example",
            business_logo_ref=logo_ref,
            headlines=[
                "Example",
                "Launch campaign",
                "Grow with confidence",
            ],
            long_headlines=[
                "Launch the approved campaign for Example",
            ],
            descriptions=[
                "Approved campaign description one.",
                "Approved campaign description two.",
            ],
        )

        result = await _materialize_campaign_media_payload(
            _Session(
                [
                    proposal,
                    asset,
                    branding,
                ]
            ),
            business_id=business_id,
            action_id=action_id,
            action_type=(
                "create_google_ads_campaign"
            ),
            payload=payload,
            storage=storage,
        )

        self.assertEqual(
            result.creative.creative_refs,
            [f"creative_asset:{asset_id}"],
        )
        self.assertEqual(
            result.campaign_media_bytes,
            (
                b"square-image",
                b"landscape-image",
            ),
        )
        self.assertTrue(
            result.campaign_logo_bytes
        )
        self.assertEqual(
            storage.presented,
            [],
        )
        self.assertEqual(
            len(storage.reads),
            3,
        )


    async def test_cross_campaign_asset_fails_closed(self):
        business_id = uuid4()
        approved_campaign_id = uuid4()
        wrong_campaign_id = uuid4()
        action_id = uuid4()
        asset_id = uuid4()

        asset, references = _asset(
            business_id,
            wrong_campaign_id,
            asset_id,
        )
        proposal = SimpleNamespace(entity_id=approved_campaign_id)

        payload = CreateMetaCampaignPayload(
            campaign_name="Launch",
            objective="traffic",
            budget="20.00",
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=[f"creative_asset:{asset_id}"],
            ),
        )

        with self.assertRaises(Exception):
            await _materialize_campaign_media_payload(
                _Session([proposal, asset]),
                business_id=business_id,
                action_id=action_id,
                action_type="create_meta_campaign",
                payload=payload,
                storage=_Storage(references),
            )

    async def test_legacy_campaign_without_uploaded_media_is_unchanged(self):
        payload = CreateGoogleAdsCampaignPayload(
            campaign_name="Legacy search",
            objective="traffic",
            budget="20.00",
            currency="USD",
            budget_period="daily",
            audience=CampaignAudience(countries=["US"]),
            creative=CampaignCreative(
                creative_refs=["marketing-campaign:legacy"],
            ),
            network="search",
        )

        result = await _materialize_campaign_media_payload(
            _Session([]),
            business_id=uuid4(),
            action_id=uuid4(),
            action_type="create_google_ads_campaign",
            payload=payload,
            storage=None,
        )

        self.assertIs(result, payload)


if __name__ == "__main__":
    unittest.main()
