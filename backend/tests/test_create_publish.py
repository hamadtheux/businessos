from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import UploadFile
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import (  # noqa: E402
    MarketingAIError,
    MarketingNotFoundError,
    MarketingPersistenceError,
    MarketingValidationError,
)
from app.models.marketing import MarketingContent  # noqa: E402
from app.schemas.ai_agent import MAX_AGENT_TASK_LENGTH  # noqa: E402
from app.schemas.marketing import (  # noqa: E402
    ContentPackageGenerateRequest,
    ContentPackageManualRequest,
    ContentCreate,
    ContentVersionCreate,
    PlatformContentVariant,
)
from app.services.marketing import (  # noqa: E402
    _CTACapabilities,
    _build_create_publish_task,
    _normalize_creative_display_cta,
    _package_media,
    create_content_version,
    create_manual_content_package,
    generate_content_package,
    prepare_uploaded_creative_asset,
)
from app.services.marketing_media import (  # noqa: E402
    PreparedMarketingMedia,
    read_marketing_media,
)


BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


class _Rows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class _Session:
    def __init__(self, scalar_values: list[object] | None = None) -> None:
        self.scalar_values = list(scalar_values or [])
        self.added: list[object] = []
        self.flush_calls = 0
        self.refresh_calls = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _statement: object) -> _Rows:
        return _Rows([])

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1
        for value in self.added:
            if hasattr(value, "created_at") and getattr(value, "created_at", None) is None:
                value.created_at = NOW
            if hasattr(value, "updated_at") and getattr(value, "updated_at", None) is None:
                value.updated_at = NOW



    async def refresh(self, _value: object) -> None:
        self.refresh_calls += 1

class _FailingFlushSession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.info: dict[str, object] = {}
        self.rollback_calls = 0

    async def flush(self) -> None:
        raise SQLAlchemyError("database unavailable")

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), (28, 58, 102)).save(output, format="PNG")
    return output.getvalue()




def _typed_package_execution(execution: SimpleNamespace) -> SimpleNamespace:
    from app.schemas.marketing import ContentPackageProposal

    return SimpleNamespace(
        context_revision=execution.context_revision,
        business_brain_source_count=execution.business_brain_source_count,
        memory_source_count=execution.memory_source_count,
        output=ContentPackageProposal.model_validate_json(
            execution.output.summary
        ),
    )


class CreatePublishFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_maximum_create_publish_input_is_bounded_before_agent_runtime(self) -> None:
        data = ContentPackageGenerateRequest(
            goal="OWNER-GOAL-START " + ("g" * 2383),
            platforms=[
                "instagram",
                "facebook",
                "linkedin",
                "tiktok",
                "youtube",
            ],
            audience="a" * 400,
            tone="t" * 120,
            objective="o" * 120,
            visual_preference="v" * 400,
            language="en",
        )

        task = _build_create_publish_task(data, None)

        self.assertIn("OWNER-GOAL-START", task)
        self.assertIn(
            "Return each requested platform exactly once.",
            task,
        )
        self.assertIn(
            "Nothing may be approved, scheduled, sent, or published.",
            task,
        )

        # Keep explicit headroom for the mandatory server-owned CMO privacy
        # instruction appended by _build_cmo_execution_request().
        self.assertLessEqual(
            len(task),
            MAX_AGENT_TASK_LENGTH - 256,
        )
        self.assertLessEqual(
            len(task) + 256,
            MAX_AGENT_TASK_LENGTH,
        )


    async def test_one_runtime_call_creates_native_platform_variants(self) -> None:
        execution = SimpleNamespace(
            context_revision="b" * 64,
            business_brain_source_count=4,
            memory_source_count=1,
            output=SimpleNamespace(
                summary=json.dumps(
                    {
                        "canonical_message": "Northstar shared a product update.",
                        "headline": "A Northstar update",
                        "creative_concept": "A clear, product-led announcement.",
                        "visual_direction": "Use the saved brand system and clean negative space.",
                        "variants": [
                            {
                                "platform": "instagram",
                                "title": "A Northstar update",
                                "caption": "A concise update for our Instagram community.",
                                "cta": "Run Smarter",
                                "hashtags": ["#Northstar"],
                                "alt_text": "A branded Northstar announcement.",
                            },
                            {
                                "platform": "linkedin",
                                "title": "An update from Northstar",
                                "caption": "A professional update for the Northstar community.",
                                "cta": "Learn More",
                                "hashtags": ["#Northstar"],
                            },
                        ],
                    }
                ),
                recommendations=[],
                proposed_actions=[],
            ),
        )
        session = _Session()
        with patch(
            "app.services.marketing._execute_content_package",
            new=AsyncMock(return_value=_typed_package_execution(execution)),
        ) as runtime:
            package = await generate_content_package(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentPackageGenerateRequest(
                    goal="Share a product update",
                    platforms=["instagram", "linkedin"],
                ),
                provider=SimpleNamespace(),
            )

        runtime.assert_awaited_once()
        self.assertEqual(session.refresh_calls, 2)
        self.assertEqual(len(package.contents), 2)
        self.assertEqual(package.contents[0].platform_fields["platform"], "instagram")
        self.assertEqual(package.contents[1].platform_fields["platform"], "linkedin")
        self.assertNotEqual(package.contents[0].body, package.contents[1].body)
        self.assertTrue(all(item.ai_generated for item in package.contents))
        self.assertTrue(all(item.status == "draft" for item in package.contents))
        self.assertEqual(package.contents[0].cta, "Run Smarter")
        persisted = [item for item in session.added if isinstance(item, MarketingContent)]
        self.assertEqual(persisted[0].cta, "Run Smarter")

    def test_display_cta_preserves_safe_copy_and_enforces_capabilities(self) -> None:
        self.assertEqual(_normalize_creative_display_cta("Run Smarter"), "Run Smarter")
        self.assertEqual(_normalize_creative_display_cta("Shop Now"), "Learn More")
        self.assertEqual(
            _normalize_creative_display_cta(
                "Shop Now",
                capabilities=_CTACapabilities(can_shop=True),
            ),
            "Shop Now",
        )

    async def test_package_rejects_missing_unexpected_or_duplicate_platforms(self) -> None:
        invalid_variants = (
            [
                {
                    "platform": "instagram",
                    "caption": "Instagram post",
                    "cta": "Learn More",
                },
            ],
            [
                {
                    "platform": "instagram",
                    "caption": "Instagram post",
                    "cta": "Learn More",
                },
                {
                    "platform": "tiktok",
                    "caption": "Unexpected post",
                    "cta": "Learn More",
                },
            ],
            [
                {
                    "platform": "instagram",
                    "caption": "Instagram post",
                    "cta": "Learn More",
                },
                {
                    "platform": "instagram",
                    "caption": "Duplicate post",
                    "cta": "Learn More",
                },
            ],
        )
        for variants in invalid_variants:
            with self.subTest(platforms=[item["platform"] for item in variants]):
                execution = SimpleNamespace(
                    context_revision="b" * 64,
                    business_brain_source_count=1,
                    memory_source_count=0,
                    output=SimpleNamespace(
                        summary=json.dumps(
                            {
                                "canonical_message": "Product update",
                                "headline": "Product update",
                                "creative_concept": "Clear announcement",
                                "visual_direction": "Use the saved brand system.",
                                "variants": variants,
                            }
                        ),
                        recommendations=[],
                        proposed_actions=[],
                    ),
                )
                session = _Session()
                with patch(
                    "app.services.marketing._execute_content_package",
                    new=AsyncMock(return_value=_typed_package_execution(execution)),
                ) as runtime:
                    with self.assertRaises(MarketingAIError):
                        await generate_content_package(
                            session,
                            business_id=BUSINESS_ID,
                            actor_user_id=USER_ID,
                            data=ContentPackageGenerateRequest(
                                goal="Share a product update",
                                platforms=["instagram", "linkedin"],
                            ),
                            provider=SimpleNamespace(),
                        )

                runtime.assert_awaited_once()
                self.assertEqual(session.added, [])

    async def test_manual_mode_preserves_user_copy_without_ai(self) -> None:
        authored = "We will close at 4 PM on Friday.\nThank you for planning ahead."
        session = _Session()
        with patch("app.services.marketing._execute_content_package", new=AsyncMock()) as runtime:
            package = await create_manual_content_package(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=ContentPackageManualRequest(
                    post_text=authored,
                    platforms=["facebook", "youtube"],
                ),
            )

        runtime.assert_not_awaited()
        self.assertEqual(session.refresh_calls, 2)
        self.assertEqual([item.body for item in package.contents], [authored, authored])
        self.assertTrue(all(not item.ai_generated for item in package.contents))

    def test_platform_fields_and_native_terms_remain_bounded(self) -> None:
        with self.assertRaisesRegex(ValidationError, "storage limit"):
            ContentCreate(
                channel="instagram",
                content_type="social_post",
                title="Bounded",
                body="Bounded body",
                platform_fields={"caption": "x" * 8192},
            )
        with self.assertRaisesRegex(ValidationError, "bounded and unique"):
            PlatformContentVariant(
                platform="instagram",
                caption="A native caption",
                hashtags=["#Launch", "#launch"],
            )

    async def test_saved_version_retains_unique_package_identity(self) -> None:
        content_id = uuid4()
        root_id = uuid4()
        package_id = uuid4()
        parent = MarketingContent(
            id=content_id,
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Original",
            body="Original body",
            cta=None,
            platform_fields={"platform": "instagram", "caption": "Original body"},
            language="en",
            status="draft",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=root_id,
            created_by_user_id=USER_ID,
            proposal_key=f"create-publish:{package_id}:instagram",
            created_at=NOW,
            updated_at=NOW,
        )
        session = _Session([parent, 1])

        saved = await create_content_version(
            session,
            business_id=BUSINESS_ID,
            content_id=content_id,
            actor_user_id=USER_ID,
            data=ContentVersionCreate(
                title="Edited",
                body="Edited body",
                cta=None,
            ),
        )

        self.assertEqual(saved.version, 2)
        self.assertEqual(
            saved.proposal_key,
            f"create-publish:{package_id}:instagram:v2",
        )
        self.assertEqual(saved.platform_fields["caption"], "Original body")

    async def test_upload_reader_sanitizes_images_and_rejects_fake_video(self) -> None:
        image = await read_marketing_media(
            UploadFile(
                filename="../Launch.PNG",
                file=BytesIO(_png()),
                headers={"content-type": "image/png"},
            ),
            duration_seconds=None,
        )
        self.assertEqual(image.media_type, "image")
        self.assertEqual(image.content_type, "image/png")
        self.assertEqual(image.original_name, "Launch.PNG")
        self.assertEqual((image.width, image.height), (64, 64))

        with self.assertRaisesRegex(MarketingValidationError, "unsupported"):
            await read_marketing_media(
                UploadFile(
                    filename="launch.mp4",
                    file=BytesIO(b"not-a-video"),
                    headers={"content-type": "video/mp4"},
                ),
                    duration_seconds=10,
                )

    async def test_media_selection_is_tenant_scoped_and_single_use(self) -> None:
        media_id = uuid4()
        with patch(
            "app.services.marketing.get_creative_asset",
            new=AsyncMock(side_effect=MarketingNotFoundError),
        ) as lookup:
            with self.assertRaises(MarketingNotFoundError):
                await _package_media(
                    _Session(),
                    business_id=BUSINESS_ID,
                    media_asset_id=media_id,
                )
        self.assertEqual(lookup.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(lookup.await_args.kwargs["creative_asset_id"], media_id)

        attached = SimpleNamespace(
            source_type="import",
            generation_status="ready",
            content_id=uuid4(),
            storage_reference="https://media.example.test/source.png",
        )
        with patch(
            "app.services.marketing.get_creative_asset",
            new=AsyncMock(return_value=attached),
        ):
            with self.assertRaisesRegex(
                MarketingValidationError,
                "uploaded_media_unavailable",
            ):
                await _package_media(
                    _Session(),
                    business_id=BUSINESS_ID,
                    media_asset_id=media_id,
                )

    async def test_failed_upload_persistence_deletes_private_object(self) -> None:
        session = _FailingFlushSession()
        storage = SimpleNamespace(
            put=AsyncMock(),
            delete=AsyncMock(),
            public_url=lambda key: f"https://media.example.test/{key}",
        )
        media = PreparedMarketingMedia(
            content=_png(),
            content_type="image/png",
            extension="png",
            media_type="image",
            width=64,
            height=64,
            duration_seconds=None,
            original_name="launch.png",
        )

        with self.assertRaises(MarketingPersistenceError):
            await prepare_uploaded_creative_asset(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                media=media,
                storage=storage,
            )

        self.assertEqual(storage.put.await_count, 5)

        stored_keys = [
            call.args[0]
            for call in storage.put.await_args_list
        ]
        self.assertEqual(len(set(stored_keys)), 5)

        self.assertEqual(storage.delete.await_count, 5)
        deleted_keys = [
            call.args[0]
            for call in storage.delete.await_args_list
        ]
        self.assertEqual(deleted_keys, list(reversed(stored_keys)))
        self.assertEqual(session.rollback_calls, 1)
        self.assertEqual(
            session.info.get("pending_creative_storage_compensations"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
