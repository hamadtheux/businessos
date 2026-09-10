from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import CheckConstraint

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import (  # noqa: E402
    MarketingAIError,
    MarketingNotFoundError,
    MarketingValidationError,
)
from app.agents.provider import AIAgentProviderMetadata  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.marketing import CreativeAsset, MarketingContent  # noqa: E402
from app.schemas.ai_agent import MAX_AGENT_TASK_LENGTH  # noqa: E402
from app.schemas.marketing import (  # noqa: E402
    CreativeAssetResponse,
    MAX_VIDEO_STRATEGY_BYTES,
    VideoCreativeCreateRequest,
    VideoCreativeStrategy,
)
from app.services.creative_video import (  # noqa: E402
    UnavailableVideoGenerationProvider,
    VideoGenerationRequest,
    VideoGenerationSubmission,
    VideoProviderError,
)
from app.services.marketing import (  # noqa: E402
    _CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN,
    _CREATIVE_STRATEGY_TASK_BUDGET,
    _build_video_strategy_task,
    create_video_creative_strategy,
    start_video_generation,
)


BUSINESS_ID = uuid4()
USER_ID = uuid4()
NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def _strategy(**updates: object) -> VideoCreativeStrategy:
    values: dict[str, object] = {
        "hook": "See how the service supports a calmer working day.",
        "script": "A concise, grounded service story followed by the approved CTA.",
        "storyboard_summary": "A product-led sequence from problem to supported service outcome.",
        "scenes": [
            {
                "scene_number": 1,
                "duration_seconds": 6,
                "purpose": "Establish the customer need.",
                "visual": "A business owner begins a focused work session.",
                "motion": "Controlled push in.",
                "voiceover": "Make room for the work that matters.",
                "on_screen_copy": "Work with clarity",
            },
            {
                "scene_number": 2,
                "duration_seconds": 9,
                "purpose": "Show the supported service story and end card.",
                "visual": "The service is shown through a truthful customer workflow.",
                "motion": "Match cut into a composed end frame.",
                "voiceover": "Explore the service.",
                "on_screen_copy": "Explore",
            },
        ],
        "shot_plan": ["Medium establishing shot", "Product-led end frame"],
        "continuity_direction": "Keep subject, wardrobe, light, and color consistent.",
        "reference_asset_strategy": "Use only approved business and brand references.",
        "audio_direction": "Quiet contemporary bed below clear voiceover.",
        "caption_plan": "Burn in concise accessible captions matching the exact script.",
        "end_card": "Business name, grounded message, and Explore CTA.",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "recommended_channel": "instagram",
        "offer": None,
        "claim_source": "none",
        "cta": "Explore",
        "evidence_source_ids": [],
        "recommendations": [],
        "proposed_actions": [],
    }
    values.update(updates)
    return VideoCreativeStrategy.model_validate(values)


def _asset(**updates: object) -> CreativeAsset:
    strategy = _strategy()
    values: dict[str, object] = {
        "id": uuid4(),
        "business_id": BUSINESS_ID,
        "campaign_id": None,
        "content_id": None,
        "asset_type": "video_vertical",
        "media_type": "video",
        "source_type": "ai_brief",
        "instructions": "Create a grounded service video.",
        "visual_direction": strategy.storyboard_summary,
        "generation_status": "strategy_ready",
        "storage_reference": None,
        "width": 1080,
        "height": 1920,
        "aspect_ratio": "9:16",
        "alt_text": None,
        "duration_seconds": 15,
        "provider_key": None,
        "provider_job_reference": None,
        "creative_metadata": {
            "schema_version": 1,
            "pipeline": "provider_neutral_video_v1",
            "phase": "planning_complete",
            "video_strategy": strategy.canonical_payload(),
        },
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return CreativeAsset(**values)


class _Session:
    def __init__(self, asset: CreativeAsset) -> None:
        self.asset = asset
        self.added: list[object] = []
        self.statements: list[object] = []
        self.flush_calls = 0

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.asset

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Rows([])

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_calls += 1


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _StrategySession(_Session):
    def __init__(
        self,
        scalar_values: list[object] | None = None,
        capability_rows: list[list[object]] | None = None,
    ) -> None:
        super().__init__(_asset())
        self.scalar_values = list(scalar_values or [])
        self.capability_rows = list(capability_rows or [[], []])

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, statement):
        self.statements.append(statement)
        rows = self.capability_rows.pop(0) if self.capability_rows else []
        return _Rows(rows)


class _ConfiguredProvider:
    provider_name = "test-video-provider"
    configured = True

    def __init__(self) -> None:
        self.requests: list[VideoGenerationRequest] = []

    async def submit(self, request: VideoGenerationRequest) -> VideoGenerationSubmission:
        self.requests.append(request)
        return VideoGenerationSubmission(
            provider_name=self.provider_name,
            provider_job_reference="job-123",
        )


class _RetryProvider(_ConfiguredProvider):
    async def submit(self, request: VideoGenerationRequest) -> VideoGenerationSubmission:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise VideoProviderError
        return VideoGenerationSubmission(
            provider_name=self.provider_name,
            provider_job_reference="job-retried",
        )


class CreativeVideoSchemaTests(TestCase):
    def test_maximum_context_video_task_is_deterministic_and_bounded(self) -> None:
        owner_intent = "OWNER-INTENT-" + ("o" * (2000 - len("OWNER-INTENT-")))
        campaign_name = "CAMPAIGN-" + ("n" * (180 - len("CAMPAIGN-")))
        campaign_objective = "OBJECTIVE-" + ("g" * (1000 - len("OBJECTIVE-")))
        content_title = "TITLE-" + ("t" * (180 - len("TITLE-")))
        body_start = "BODY-BEGIN-"
        body_end = "-BODY-END"
        content_body = (
            body_start
            + ("b" * (20_000 - len(body_start) - len(body_end)))
            + body_end
        )
        authorized_offer = "OFFER-" + ("f" * (160 - len("OFFER-")))
        validated_cta = "Explore the authorized collection"
        data = VideoCreativeCreateRequest(
            duration_seconds=30,
            aspect_ratio="16:9",
            instructions=owner_intent,
            style="STYLE-" + ("s" * (160 - len("STYLE-"))),
            audio_preference="AUDIO-" + ("a" * (160 - len("AUDIO-"))),
            motion_preference="MOTION-" + ("m" * (160 - len("MOTION-"))),
        )
        campaign = SimpleNamespace(
            name=campaign_name,
            objective=campaign_objective,
        )
        content = SimpleNamespace(
            channel="linkedin",
            title=content_title,
            body=content_body,
        )

        task = _build_video_strategy_task(
            data=data,
            campaign=campaign,
            content=content,
            trusted_content_cta=validated_cta,
            authorized_offer=authorized_offer,
            claim_source="owner_provided_campaign_input",
        )
        repeated_task = _build_video_strategy_task(
            data=data,
            campaign=campaign,
            content=content,
            trusted_content_cta=validated_cta,
            authorized_offer=authorized_offer,
            claim_source="owner_provided_campaign_input",
        )

        self.assertEqual(task, repeated_task)
        self.assertLessEqual(len(task), _CREATIVE_STRATEGY_TASK_BUDGET)
        self.assertEqual(_CREATIVE_STRATEGY_TASK_BUDGET, 3744)
        self.assertEqual(_CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN, 256)
        self.assertLessEqual(
            len(task) + _CREATIVE_STRATEGY_RUNTIME_RULE_MARGIN,
            MAX_AGENT_TASK_LENGTH,
        )
        self.assertIn(f"Authorized offer: {authorized_offer}", task)
        self.assertIn(f"Validated content CTA: {validated_cta}", task)
        self.assertIn("Duration: 30 seconds exactly.", task)
        self.assertIn("Aspect ratio: 16:9 exactly.", task)
        self.assertIn("Channel: linkedin", task)
        self.assertIn("Server claim source: owner_provided_campaign_input.", task)
        self.assertIn("Owner intent: OWNER-INTENT-", task)
        self.assertIn(f"Content body: {body_start}", task)
        self.assertNotIn(body_end, task)
        self.assertIn("Return exactly one VideoCreativeStrategy", task)
        self.assertIn("Never invent prices", task)
        self.assertIn("Do not browse, publish, submit a provider job", task)

    def test_public_creative_response_is_an_explicit_safe_allowlist(self) -> None:
        storyboard_summary = "A bounded, safe storyboard summary for the studio."
        asset = _asset(
            provider_key="private-provider",
            provider_job_reference="private-job-123",
            visual_direction=storyboard_summary,
            creative_metadata={
                "schema_version": 1,
                "phase": "provider_submission_accepted",
                "submission_idempotency_key": "private-idempotency-key",
                "future_reconciliation_state": {"attempt": 7},
                "raw_provider_payload": {"secret": "must-not-leak"},
            },
        )

        payload = CreativeAssetResponse.model_validate(asset).model_dump(mode="json")

        self.assertEqual(
            set(payload),
            {
                "id",
                "business_id",
                "created_at",
                "updated_at",
                "campaign_id",
                "content_id",
                "asset_type",
                "media_type",
                "source_type",
                "instructions",
                "visual_direction",
                "generation_status",
                "storage_reference",
                "width",
                "height",
                "aspect_ratio",
                "alt_text",
                "duration_seconds",
            },
        )
        self.assertEqual(payload["visual_direction"], storyboard_summary)
        serialized = str(payload)
        self.assertNotIn("creative_metadata", payload)
        self.assertNotIn("provider_key", payload)
        self.assertNotIn("provider_job_reference", payload)
        self.assertNotIn("submission_idempotency_key", serialized)
        self.assertNotIn("future_reconciliation_state", serialized)
        self.assertNotIn("raw_provider_payload", serialized)
        self.assertNotIn("private-job-123", serialized)

    def test_scene_durations_and_numbering_are_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            _strategy(scenes=[{
                "scene_number": 2,
                "duration_seconds": 15,
                "purpose": "Invalid first scene.",
                "visual": "A scene.",
                "motion": "Static.",
            }])

    def test_provider_handoff_is_bounded_and_normalized(self) -> None:
        submission = VideoGenerationSubmission(
            provider_name="  provider-one  ",
            provider_job_reference="  job-one  ",
        )
        self.assertEqual(submission.provider_name, "provider-one")
        self.assertEqual(submission.provider_job_reference, "job-one")
        with self.assertRaises(ValueError):
            VideoGenerationSubmission(
                provider_name="   ",
                provider_job_reference="job-one",
            )
        with self.assertRaises(ValueError):
            VideoGenerationSubmission(
                provider_name="provider-one",
                provider_job_reference="   ",
            )
        with self.assertRaises(ValueError):
            VideoGenerationRequest(
                business_id=BUSINESS_ID,
                creative_asset_id=uuid4(),
                strategy_json="x" * (MAX_VIDEO_STRATEGY_BYTES + 1),
                duration_seconds=15,
                aspect_ratio="9:16",
                idempotency_key=str(uuid4()),
            )

    def test_maximum_bound_is_enforced_during_strategy_construction(self) -> None:
        scenes = [
            {
                "scene_number": index + 1,
                "duration_seconds": 4 if index < 6 else 3,
                "purpose": "p" * 160,
                "visual": "v" * 400,
                "motion": "m" * 240,
                "voiceover": "o" * 300,
                "on_screen_copy": "c" * 120,
            }
            for index in range(8)
        ]
        with self.assertRaisesRegex(
            ValidationError,
            f"video strategy exceeds the {MAX_VIDEO_STRATEGY_BYTES}-byte limit",
        ):
            _strategy(
                hook="h" * 240,
                script="s" * 1200,
                storyboard_summary="b" * 600,
                scenes=scenes,
                shot_plan=["q" * 300 for _index in range(8)],
                continuity_direction="d" * 400,
                reference_asset_strategy="r" * 400,
                audio_direction="a" * 300,
                caption_plan="t" * 300,
                end_card="e" * 300,
                duration_seconds=30,
            )

    def test_provider_accepts_canonical_strategy_above_legacy_character_limit(self) -> None:
        scenes = [
            {
                "scene_number": index + 1,
                "duration_seconds": 4 if index < 6 else 3,
                "purpose": "Grounded customer workflow",
                "visual": "Supported product in a real customer workflow. " * 6,
                "motion": "Controlled camera motion. " * 6,
                "voiceover": "A truthful supported product story. " * 5,
                "on_screen_copy": "Explore the product",
            }
            for index in range(8)
        ]
        strategy = _strategy(
            scenes=scenes,
            shot_plan=["Grounded commercial shot plan. " * 5 for _index in range(8)],
            duration_seconds=30,
        )
        strategy_json = strategy.canonical_json()
        self.assertGreater(len(strategy_json), 5000)
        request = VideoGenerationRequest(
            business_id=BUSINESS_ID,
            creative_asset_id=uuid4(),
            strategy_json=strategy_json,
            duration_seconds=30,
            aspect_ratio="9:16",
            idempotency_key=str(uuid4()),
        )
        payload = request.external_payload()
        self.assertEqual(
            set(payload),
            {"strategy", "duration_seconds", "aspect_ratio", "idempotency_key"},
        )
        self.assertNotIn("business_id", payload)
        self.assertNotIn("creative_asset_id", payload)

    def test_migration_is_linear_and_upgrade_is_additive(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "alembic/versions/9d7a2c4e6f81_add_creative_media_pipeline.py"
        ).read_text()
        upgrade = source.split("def upgrade() -> None:", 1)[1].split(
            "def downgrade() -> None:",
            1,
        )[0]

        self.assertIn('down_revision: str | Sequence[str] | None = "8c4f2a91d7e6"', source)
        for column in (
            "media_type",
            "duration_seconds",
            "provider_key",
            "provider_job_reference",
            "creative_metadata",
        ):
            self.assertIn(f'"{column}"', upgrade)
        self.assertEqual(upgrade.count("op.add_column"), 5)
        self.assertNotIn("drop_column", upgrade)
        self.assertNotIn("drop_table", upgrade)
        self.assertIn("consistent_media_asset_type", upgrade)
        self.assertIn("char_length(btrim(provider_key)) BETWEEN 1 AND 64", upgrade)
        self.assertIn(
            "char_length(btrim(provider_job_reference)) BETWEEN 1 AND 255",
            upgrade,
        )
        self.assertIn("uq_marketing_creative_assets_provider_job", upgrade)
        self.assertIn('postgresql_where=sa.text("provider_job_reference IS NOT NULL")', upgrade)
        downgrade = source.split("def downgrade() -> None:", 1)[1]
        self.assertIn("This downgrade intentionally discards", downgrade)
        self.assertIn(
            "feature-specific video strategy and provider metadata when the new columns",
            downgrade,
        )
        self.assertNotIn(
            "Provider state and strategy remain inspectable",
            downgrade,
        )
        self.assertLess(
            downgrade.index('op.drop_index('),
            downgrade.index('op.drop_column(table, "provider_job_reference")'),
        )

    def test_database_model_enforces_media_state_and_provider_identity(self) -> None:
        checks = {
            constraint.name.removeprefix("ck_marketing_creative_assets_"): str(
                constraint.sqltext
            )
            for constraint in CreativeAsset.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        media_pairing = checks["consistent_media_asset_type"]
        self.assertIn("media_type = 'image'", media_pairing)
        self.assertIn("'social_square'", media_pairing)
        self.assertIn("media_type = 'video'", media_pairing)
        self.assertIn("'video_vertical'", media_pairing)
        self.assertIn(
            "char_length(btrim(provider_key)) BETWEEN 1 AND 64",
            checks["valid_provider_key"],
        )
        self.assertIn(
            "provider_job_reference IS NULL OR provider_key IS NOT NULL",
            checks["consistent_provider_job_identity"],
        )
        self.assertIn(
            "NOT (media_type = 'video' AND generation_status IN",
            checks["consistent_video_async_state"],
        )
        provider_job_index = next(
            index
            for index in CreativeAsset.__table__.indexes
            if index.name == "uq_marketing_creative_assets_provider_job"
        )
        self.assertTrue(provider_job_index.unique)
        self.assertEqual(
            str(provider_job_index.dialect_options["postgresql"]["where"]),
            "provider_job_reference IS NOT NULL",
        )


class CreativeVideoServiceTests(IsolatedAsyncioTestCase):
    async def test_video_request_validation_error_maps_safely(self) -> None:
        try:
            VideoCreativeStrategy.model_validate({})
        except ValidationError as error:
            validation_error = error
        else:  # pragma: no cover - schema must reject an empty strategy
            self.fail("Expected schema validation to fail")

        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(side_effect=validation_error),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(),
            ) as runtime,
            self.assertRaises(MarketingAIError),
        ):
            await create_video_creative_strategy(
                _StrategySession(),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=VideoCreativeCreateRequest(instructions="Create a grounded video."),
                provider=SimpleNamespace(provider_name="test-video-strategy"),
            )
        runtime.assert_not_awaited()

    async def test_runtime_validation_error_is_not_broadly_swallowed(self) -> None:
        try:
            VideoCreativeStrategy.model_validate({})
        except ValidationError as error:
            validation_error = error
        else:  # pragma: no cover - schema must reject an empty strategy
            self.fail("Expected schema validation to fail")

        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(side_effect=validation_error),
            ),
            self.assertRaises(ValidationError),
        ):
            await create_video_creative_strategy(
                _StrategySession(),
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=VideoCreativeCreateRequest(instructions="Create a grounded video."),
                provider=SimpleNamespace(provider_name="test-video-strategy"),
            )

    async def test_video_revalidates_existing_malformed_cta(self) -> None:
        content_id = uuid4()
        content = MarketingContent(
            id=content_id,
            business_id=BUSINESS_ID,
            campaign_id=None,
            channel="instagram",
            content_type="social_post",
            title="Supported collection",
            body="A grounded product story that says shop now.",
            cta="buy Now",
            language="en",
            status="approved",
            ai_generated=True,
            version=1,
            parent_content_id=None,
            root_content_id=content_id,
            created_by_user_id=USER_ID,
            source_evidence=[],
            created_at=NOW,
            updated_at=NOW,
        )
        execution = SimpleNamespace(
            output=_strategy(cta="buy Now"),
            provider_metadata=AIAgentProviderMetadata(provider_request_id="req-video"),
        )
        session = _StrategySession(
            scalar_values=[content],
            capability_rows=[[], []],
        )
        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(return_value=execution),
            ),
        ):
            asset = await create_video_creative_strategy(
                session,
                business_id=BUSINESS_ID,
                actor_user_id=USER_ID,
                data=VideoCreativeCreateRequest(
                    content_id=content.id,
                    instructions="Make a Buy Now video.",
                ),
                provider=SimpleNamespace(provider_name="test-video-strategy"),
            )

        strategy = VideoCreativeStrategy.model_validate(
            asset.creative_metadata["video_strategy"]
        )
        self.assertEqual(strategy.cta, "Learn More")
        self.assertEqual(asset.visual_direction, strategy.storyboard_summary)

    async def test_unconfigured_provider_records_truthful_durable_state(self) -> None:
        asset = _asset()
        session = _Session(asset)

        result = await start_video_generation(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=UnavailableVideoGenerationProvider(),
        )

        self.assertEqual(result.generation_status, "provider_required")
        self.assertIsNone(result.storage_reference)
        self.assertEqual(result.creative_metadata["phase"], "provider_required")
        self.assertEqual(session.flush_calls, 1)
        self.assertTrue(any(isinstance(item, AuditLog) for item in session.added))
        statement = str(session.statements[0])
        self.assertIn("marketing_creative_assets.business_id", statement)
        self.assertIn("marketing_creative_assets.id", statement)
        self.assertIn("FOR UPDATE", statement)

    async def test_configured_provider_records_queue_reference_without_fake_media(self) -> None:
        asset = _asset()
        session = _Session(asset)
        provider = _ConfiguredProvider()

        result = await start_video_generation(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )

        self.assertEqual(result.generation_status, "queued")
        self.assertEqual(result.provider_key, "test-video-provider")
        self.assertEqual(result.provider_job_reference, "job-123")
        self.assertIsNone(result.storage_reference)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].business_id, BUSINESS_ID)
        request = provider.requests[0]
        self.assertIsNotNone(request.execution_plan_json)
        payload = request.external_payload()
        identity = payload["creative_identity"]
        execution_plan = payload["execution_plan"]
        self.assertEqual(identity["territory_key"], request.creative_territory_key)
        self.assertEqual(identity["concept_name"], request.creative_concept_name)
        self.assertEqual(identity["campaign_mechanism"], request.campaign_mechanism)
        self.assertEqual(
            identity["composition_family"],
            execution_plan["composition_family"],
        )
        self.assertEqual(len(execution_plan["scene_plan"]), 5)
        self.assertEqual(
            result.creative_metadata["submission_idempotency_key"],
            request.idempotency_key,
        )
        self.assertNotIn("business_id", payload)

        repeated = await start_video_generation(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )
        self.assertIs(repeated, result)
        self.assertEqual(len(provider.requests), 1)

    async def test_cross_tenant_asset_never_submits_or_reveals_state(self) -> None:
        asset = _asset(business_id=uuid4())
        provider = _ConfiguredProvider()
        with self.assertRaises(MarketingNotFoundError):
            await start_video_generation(
                _Session(asset),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=provider,
            )
        self.assertEqual(provider.requests, [])

    async def test_failed_submission_retry_reuses_stable_idempotency_key(self) -> None:
        asset = _asset()
        session = _Session(asset)
        provider = _RetryProvider()

        first = await start_video_generation(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )
        self.assertEqual(first.generation_status, "failed")
        second = await start_video_generation(
            session,
            business_id=BUSINESS_ID,
            creative_asset_id=asset.id,
            actor_user_id=USER_ID,
            provider=provider,
        )

        self.assertEqual(second.generation_status, "queued")
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(
            provider.requests[0].idempotency_key,
            provider.requests[1].idempotency_key,
        )

    async def test_asset_and_strategy_format_must_match(self) -> None:
        asset = _asset(duration_seconds=8)
        with self.assertRaises(MarketingValidationError):
            await start_video_generation(
                _Session(asset),
                business_id=BUSINESS_ID,
                creative_asset_id=asset.id,
                actor_user_id=USER_ID,
                provider=UnavailableVideoGenerationProvider(),
            )
