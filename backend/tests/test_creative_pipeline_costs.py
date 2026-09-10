from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from creative_pipeline_fixtures import brand_inputs, brand_plan, brand_strategy, brand_synthesis
from test_creative_world_class import _saas_authority, _saas_brain_bundle
from test_marketing_service import (
    BUSINESS_ID, USER_ID, _ScalarSession, _FailingCommitSession,
    _business_record, _creative_asset, _DurableCheckpointStorage,
    _png_bytes, _composed_candidate, _visual_review, _visual_result,
)
from app.agents.provider import AIAgentProviderMetadata
from app.exceptions.marketing import MarketingAIError, MarketingNotFoundError, MarketingPersistenceError
from app.schemas.marketing import CreativePlan
from app.services import marketing
from app.services.creative_direction import (
    CreativeConceptProposal,
    CreativeDirectorSynthesis,
    CreativeDirectionCheckpoint,
    build_creative_direction,
    creative_direction_meets_hard_eligibility,
    creative_direction_meets_quality_floor,
)
from app.services.creative_provider import CreativeGenerationResult


@pytest.fixture
def runtime(monkeypatch):
    request = AsyncMock(return_value=object())
    execute = AsyncMock()
    authority = AsyncMock(return_value=None)
    monkeypatch.setattr(marketing, "_build_cmo_execution_request", request)
    monkeypatch.setattr(marketing, "execute_ai_agent_typed_with_metadata", execute)
    monkeypatch.setattr(marketing, "assemble_authoritative_creative_context", authority)
    monkeypatch.setattr(marketing.CreativeCompositor, "compose_candidates", lambda *args: (_composed_candidate("minimal_hero", color=(100,120,140)),))
    monkeypatch.setattr(marketing, "assess_creative_quality", lambda *args, **kwargs: SimpleNamespace(approved_for_delivery=True, approved_for_semantic_review=True, failure_kind=None, overall_score=90))
    return SimpleNamespace(execute=execute, request=request, authority=authority)


@pytest.fixture
def real_runtime(monkeypatch):
    """Same provider doubles, with the real deterministic compositor enabled."""
    request = AsyncMock(return_value=object())
    execute = AsyncMock()
    authority = AsyncMock(return_value=None)
    monkeypatch.setattr(marketing, "_build_cmo_execution_request", request)
    monkeypatch.setattr(marketing, "execute_ai_agent_typed_with_metadata", execute)
    monkeypatch.setattr(marketing, "assemble_authoritative_creative_context", authority)
    monkeypatch.setattr(
        marketing,
        "assess_creative_quality",
        lambda *args, **kwargs: SimpleNamespace(
            approved_for_delivery=True,
            approved_for_semantic_review=True,
            failure_kind=None,
            overall_score=90,
        ),
    )
    return SimpleNamespace(execute=execute, request=request, authority=authority)


def execution(*, strong=True, alternative=False):
    return SimpleNamespace(output=brand_synthesis(strong=strong, alternative=alternative), provider_metadata=AIAgentProviderMetadata())


def simple_creative_plan() -> CreativePlan:
    strategy = brand_strategy()
    return CreativePlan(
        headline=strategy.headline,
        supporting_copy=strategy.supporting_message,
        caption="A grounded editorial moment for owners.",
        cta=strategy.cta,
        concept_name="The opening-time balancing act",
        creative_idea="Make a familiar opening-time tension visible through one human action.",
        audience_reason_to_care="Shopkeepers recognize the pressure of opening while handling daily work.",
        visual_story=(
            "A shopkeeper balances receipts while lifting the opening shutter, so the "
            "uneven arms make the morning pressure visible."
        ),
        hero_subject="A real shopkeeper at the storefront with receipts and the opening shutter",
        hero_action="Balances receipts while lifting the opening shutter",
        visible_consequence="The uneven arms make the opening-time pressure visible.",
        art_direction="Contemporary editorial photography in soft directional morning light.",
        image_style="Grounded editorial photography",
        composition_intent="Hero at the storefront with a clear right-side copy corridor.",
        negative_space_intent="Keep the right third quiet for deterministic copy and logo.",
        image_prompt="A real shopkeeper balancing receipts while lifting a storefront shutter.",
        visual_exclusions=("glowing AI brain", "fake dashboard", "floating app icons"),
        brand_treatment=strategy.brand_treatment,
        recommended_channel="instagram",
    )


def asset_for_job():
    asset = _creative_asset(status="queued", visual_direction=brand_strategy().model_dump_json())
    asset.creative_metadata = {"image_generation_epoch": 1, "image_generation_started_attempt": 0, "generation_requested_by_user_id": str(USER_ID)}
    return asset


def providers(reviews=None):
    image = SimpleNamespace(provider_name="fake_image", generate_draft=AsyncMock(return_value=CreativeGenerationResult(content=_png_bytes(),width=1024,height=1024)))
    review = SimpleNamespace(provider_name="fake_vision", review=AsyncMock(side_effect=reviews or [_visual_result(_visual_review())]))
    return image, review


def test_full_director_checkpoint_reproduces_the_16kb_metadata_overflow():
    strategy, context, research = brand_inputs()
    plan = build_creative_direction(
        strategy=strategy,
        context=context,
        research=research,
        story_mode="brand_offer",
        synthesis=_production_sized_director_synthesis(),
    )
    base_metadata = {
        "image_generation_epoch": 1,
        "image_generation_started_attempt": 0,
        "generation_requested_by_user_id": str(USER_ID),
    }
    old_metadata = {
        **base_metadata,
        "director_state": {
            "epoch": 1,
            "mode": "brand_offer",
            "calls": 1,
            "pending": False,
            "plans": {"1": plan.model_dump(mode="json")},
        },
    }
    old_size = marketing._creative_metadata_byte_size(old_metadata)
    assert old_size > 16_384

    checkpoint = CreativeDirectionCheckpoint(
        generation_epoch=1,
        image_attempt=1,
        selected_concept=CreativeConceptProposal.model_validate(
            plan.selected_concept.model_dump(exclude={"scorecard"})
        ),
        research_fingerprint=plan.research_fingerprint,
        used_live_research=plan.used_live_research,
        used_ai_synthesis=plan.used_ai_synthesis,
    )
    compact_metadata = {
        **base_metadata,
        "director_state": {
            "checkpoint_version": 1,
            "epoch": 1,
            "mode": "brand_offer",
            "calls": 1,
            "pending": False,
            "plans": {"1": checkpoint.model_dump(mode="json")},
        },
    }
    compact_size = marketing._creative_metadata_byte_size(compact_metadata)
    assert compact_size <= marketing._CREATIVE_METADATA_APP_MAX_BYTES
    assert "scorecard" not in json.dumps(compact_metadata)


@pytest.mark.asyncio
async def test_compact_checkpoint_is_persisted_without_scorecards(runtime):
    runtime.execute.return_value = execution()
    asset = asset_for_job()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    state = asset.creative_metadata["director_state"]
    checkpoint = state["plans"]["1"]
    assert state["checkpoint_version"] == 1
    assert checkpoint["checkpoint_version"] == 1
    assert "scorecard" not in checkpoint
    assert marketing._creative_metadata_byte_size(asset.creative_metadata) <= marketing._CREATIVE_METADATA_APP_MAX_BYTES


@pytest.mark.asyncio
async def test_good_v2_pipeline_uses_prompt_plan_and_one_image_call(real_runtime, monkeypatch):
    real_runtime.execute.return_value = execution()
    asset = asset_for_job()
    image, review = providers()
    captured_inputs = []
    original_compose_candidates = marketing.CreativeCompositor.compose_candidates

    def capture_and_compose(compositor, composition_input):
        captured_inputs.append(composition_input)
        return original_compose_candidates(compositor, composition_input)

    monkeypatch.setattr(
        marketing.CreativeCompositor,
        "compose_candidates",
        capture_and_compose,
    )
    storage = _DurableCheckpointStorage()

    result = await run(asset, image, review, storage=storage)

    assert result.generation_status == "ready"
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1
    assert captured_inputs
    composition_input = captured_inputs[0]
    assert composition_input.composition_plan is not None
    plan = composition_input.composition_plan
    request = image.generate_draft.await_args.args[0]
    assert f"COMPOSITION FAMILY: {plan.family}" in request.instructions
    assert (
        f"x={plan.focal_subject_region.x:.2f}, "
        f"y={plan.focal_subject_region.y:.2f}"
    ) in request.instructions
    assert "HEADLINE SAFE ZONE: normalized region" in request.instructions
    assert "CROP SAFETY:" in request.instructions
    assert "ART DIRECTION:" in request.instructions
    assert "floating app icons" in request.instructions.casefold()
    assert len(request.instructions) <= 5_000
    assert any(
        candidate.selected_layout == plan.family
        for candidate in original_compose_candidates(
            marketing.CreativeCompositor(),
            composition_input,
        )
    )
    assert any("/raw/" in key for key in storage.objects)


@pytest.mark.asyncio
async def test_simple_user_creative_plan_reaches_v2_ready_with_one_planner_and_image(
    real_runtime,
):
    real_runtime.execute.return_value = SimpleNamespace(
        output=simple_creative_plan(),
        provider_metadata=AIAgentProviderMetadata(),
    )
    asset = asset_for_job()
    image, review = providers()

    result = await run(asset, image, review, storage=_DurableCheckpointStorage())

    assert result.generation_status == "ready"
    assert real_runtime.execute.await_count == 1
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1


@pytest.mark.asyncio
async def test_blue_ai_hub_end_to_end_review_never_reaches_ready(real_runtime):
    real_runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    real_runtime.execute.return_value = _saas_pipeline_execution(strong=True)
    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, _review = providers()
    critic = _visual_result(
        _visual_review(
            approved=False,
            repair_class="raw_visual",
            generic_template_output=True,
            ai_cliche_visual=True,
            ai_cliche_risk=92,
            repair_instructions="Regenerate the generic AI hub visual.",
        )
    )
    review = SimpleNamespace(
        provider_name="fake_vision",
        review=AsyncMock(return_value=critic),
    )

    result = await marketing.run_queued_creative_asset_generation(
        _PipelineSession([asset, _business_record(), None, asset]),
        business_id=BUSINESS_ID,
        creative_asset_id=asset.id,
        provider=image,
        storage=_DurableCheckpointStorage(),
        director_provider=SimpleNamespace(provider_name="fake_director"),
        visual_review_provider=review,
        max_image_attempts=1,
        require_semantic_review=True,
    )

    assert result.generation_status == "failed"
    assert image.generate_draft.await_count == 1
    reviewed_request = review.review.await_args.args[0]
    assert reviewed_request.final_png


@pytest.mark.asyncio
async def test_director_result_persistence_rolls_back_and_logs_only_safe_integrity_diagnostics(
    runtime,
    caplog,
):
    runtime.execute.return_value = execution()
    strategy, context, research = brand_inputs()
    asset = asset_for_job()
    session = _FailingFlushOnCall([asset], fail_on_flush=2)

    with pytest.raises(MarketingPersistenceError):
        await marketing._creative_direction_with_fallback(
            session,
            business_id=BUSINESS_ID,
            strategy=strategy,
            research=research,
            context=context,
            provider=SimpleNamespace(provider_name="fake_director"),
            max_output_tokens=4_000,
            story_mode="brand_offer",
            value=asset,
            persist_progress=True,
        )

    assert session.rollback_calls == 1
    records = [
        record for record in caplog.records
        if record.getMessage().startswith("creative_direction_state_persist_failed")
    ]
    assert len(records) == 1
    assert records[0].exception_type == "IntegrityError"
    assert records[0].metadata_bytes <= marketing._CREATIVE_METADATA_APP_MAX_BYTES
    assert "private database detail" not in caplog.text
    assert "creative metadata check contained" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_revalidates_compact_checkpoint_with_current_authority(
    runtime,
    monkeypatch,
):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.return_value = _saas_pipeline_execution(strong=True)
    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()
    storage = _DurableCheckpointStorage()

    monkeypatch.setattr(
        marketing.CreativeCompositor,
        "compose_candidates",
        lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        await run(asset, image, review, storage=storage)

    assert "scorecard" not in json.dumps(asset.creative_metadata)
    monkeypatch.setattr(
        marketing.CreativeCompositor,
        "compose_candidates",
        lambda *args: (_composed_candidate("minimal_hero", color=(100, 120, 140)),),
    )
    recovered = await run(asset, image, review, storage=storage)

    assert recovered.generation_status == "ready"
    assert runtime.authority.await_count == 2
    assert runtime.execute.await_count == 1
    assert image.generate_draft.await_count == 1


@pytest.mark.asyncio
async def test_persisted_scorecards_cannot_authorize_a_currently_weak_concept(runtime):
    strategy, context, research = brand_inputs()
    weak_plan = build_creative_direction(
        strategy=strategy,
        context=context,
        research=research,
        story_mode="brand_offer",
        synthesis=brand_synthesis(strong=False),
    )
    legacy = weak_plan.model_dump(mode="json")
    for candidate in legacy["candidates"]:
        candidate["scorecard"] = {
            key: 0 for key in candidate["scorecard"]
        }
    selected_name = weak_plan.selected_concept.concept_name
    for candidate in legacy["candidates"]:
        if candidate["concept_name"] == selected_name:
            candidate["scorecard"] = {
                key: 100 for key in candidate["scorecard"]
            }
    legacy["selected_concept"] = next(
        candidate for candidate in legacy["candidates"]
        if candidate["concept_name"] == selected_name
    )
    asset = asset_for_job()
    asset.creative_metadata["director_state"] = {
        "epoch": 1,
        "mode": "brand_offer",
        "calls": 1,
        "pending": False,
        "plans": {"1": legacy},
    }
    with pytest.raises(MarketingAIError):
        await marketing._creative_direction_with_fallback(
            _ScalarSession([asset]),
            business_id=BUSINESS_ID,
            strategy=strategy,
            research=research,
            context=context,
            provider=runtime,
            max_output_tokens=4_000,
            story_mode="brand_offer",
            value=asset,
        )
    runtime.execute.assert_not_awaited()


async def run(asset, image, review, *, storage=None, session=None):
    return await marketing.run_queued_creative_asset_generation(
        session or _PipelineSession([asset,_business_record(),None,asset]), business_id=BUSINESS_ID,
        creative_asset_id=asset.id, provider=image, storage=storage or _DurableCheckpointStorage(),
        director_provider=SimpleNamespace(provider_name="fake_director"), visual_review_provider=review,
        require_semantic_review=True,
    )


class _PipelineSession(_ScalarSession, AsyncSession):
    """In-memory session that exercises the production AsyncSession boundary."""


class _FailingFlushOnCall(_ScalarSession):
    def __init__(self, values, *, fail_on_flush: int):
        super().__init__(values)
        self.fail_on_flush = fail_on_flush

    async def flush(self):
        self.flush_calls += 1
        if self.flush_calls == self.fail_on_flush:
            raise IntegrityError(
                "creative metadata check contained a private value",
                {},
                ValueError("private database detail"),
            )


def _production_sized_director_synthesis() -> CreativeDirectorSynthesis:
    """Three valid proposals with fields close to the production maxima."""
    _strategy, _context, _research = brand_inputs()
    maxima = {
        "concept_name": 100,
        "marketing_idea": 300,
        "customer_care_reason": 300,
        "strategic_reason": 300,
        "hero_subject": 500,
        "hero_relevance": 300,
        "product_story": 400,
        "scroll_stopping_hook": 300,
        "visual_metaphor": 300,
        "layout_intent": 500,
        "focal_area": 160,
        "text_zone": 220,
        "offer_treatment": 160,
        "cta_treatment": 160,
        "depth": 180,
        "image_style": 220,
        "camera_direction": 220,
        "lighting": 240,
        "mood": 240,
        "visual_density": 120,
        "background_complexity": 180,
        "brand_expression": 300,
        "originality_notes": 300,
    }
    unique_tokens = (
        ("amber", "ledger", "canopy"),
        ("cobalt", "shutter", "ribbon"),
        ("violet", "threshold", "lantern"),
    )
    proposals = []
    for index, (first, second, third) in enumerate(unique_tokens):
        values = {
            field: (
                f"{first} {second} {third} grounded detail "
                + "material evidence " * 50
            )[:maximum]
            for field, maximum in maxima.items()
        }
        values.update(
            concept_name=f"{first} {second} {third} concept {index}",
            marketing_idea=(
                f"{first} {second} {third} distinct mechanism evidence "
                + "material " * 60
            )[:300],
            visual_metaphor=(
                f"{first} {second} {third} distinct visual metaphor "
                + "shape tension " * 60
            )[:300],
            hero_subject=(
                f"{first} {second} {third} shopkeeper scene "
                + "grounded physical subject " * 60
            )[:500],
            product_story=(
                f"{first} {second} {third} visible consequence grounded scene "
                + "material detail " * 60
            )[:400],
            inspiration_principles=tuple(
                f"{first} principle {item} grounded"[:180]
                for item in range(6)
            ),
            avoid_patterns=tuple(
                f"avoid {first} {item} generic"[:180]
                for item in range(6)
            ),
        )
        proposals.append(CreativeConceptProposal.model_validate(values))
    return CreativeDirectorSynthesis(candidates=tuple(proposals))


def concept_failure():
    return _visual_result(_visual_review(approved=False, repair_class="raw_visual",
        meaningless_focal_story=True, replaceable_brand_creative=True,
        commercially_weak=True, generic_template_output=True,
        repair_instructions="PRIVATE_CRITIC_PROSE must never reach repair request"))


_PRODUCTION_HEADLINE = "Run Your Business With One Clear Rhythm"
_PRODUCTION_SUPPORTING = (
    "Bring marketing, sales, support, and operations into one connected way of "
    "working."
)
_PRODUCTION_CTA = "Run Smarter"
_PRODUCTION_OWNER_INTENT = f"""Headline:
{_PRODUCTION_HEADLINE}
Supporting copy:
{_PRODUCTION_SUPPORTING}
CTA:
{_PRODUCTION_CTA}

Owner visual direction:
Show a real business owner in a believable working environment managing customer
messages, an order, follow-up work, marketing work, and reporting. Capture the
moment scattered responsibilities become one controlled rhythm. Human premium
editorial commercial photography.

Explicitly avoid:
- glowing AI brain
- glowing AI hub
- neon network
- floating department icons
- holograms
- robots
- fake dashboards
- generic futuristic blue technology
"""


def _production_strategy():
    return _saas_pipeline_strategy().model_copy(update={
        "marketing_goal": (
            "Show small business owners a calmer way to handle customer messages, "
            "orders, follow-up, marketing, and reporting."
        ),
        "target_audience": (
            "Small business owners handling customer messages, orders, follow-up, "
            "marketing, and reporting."
        ),
        "audience_insight": (
            "Several real responsibilities compete for the owner's attention in "
            "the same working day."
        ),
        "campaign_angle": (
            "Scattered customer messages, orders, follow-up, marketing, and "
            "reporting become one controlled working rhythm around the owner."
        ),
        "headline": _PRODUCTION_HEADLINE,
        "supporting_message": _PRODUCTION_SUPPORTING,
        "cta": _PRODUCTION_CTA,
        "visual_concept": (
            "A real business owner physically organizing five recognizable work "
            "responsibilities in a believable working environment."
        ),
        "subject_focus": (
            "One real business owner with customer messages, an order, follow-up, "
            "marketing work, and reporting."
        ),
        "mood": "Human premium editorial commercial photography.",
        "lighting": "Believable soft directional daylight.",
        "negative_space": "Protected quiet copy column with no objects.",
    })


def _production_synthesis(kind: str) -> CreativeDirectorSynthesis:
    synthesis = _saas_pipeline_synthesis(strong=False)
    candidates = list(synthesis.candidates)
    if kind == "initial_weak":
        values = {
            "concept_name": "Competing work at noon",
            "marketing_idea": (
                "A real business owner holds mixed daily work while competing "
                "responsibilities press into the same moment."
            ),
            "customer_care_reason": (
                "Small business owners care because customer work and business duties "
                "compete for attention during one working day."
            ),
            "hero_subject": (
                "A real small-business owner holding mixed customer and business "
                "papers in a believable working environment."
            ),
            "hero_relevance": (
                "The owner and ordinary papers make daily divided attention visible."
            ),
            "product_story": (
                "The owner holds mixed customer papers while an order and unfinished "
                "marketing work sit nearby; the visible consequence is divided attention."
            ),
            "visual_metaphor": "Several ordinary papers competing for one owner's hands.",
            "scroll_stopping_hook": (
                "One owner caught while several real duties demand attention."
            ),
        }
    elif kind == "repair_soft":
        values = {
            "concept_name": "The controlled working rhythm",
            "marketing_idea": (
                "A human before-to-after sequence shows a real owner turning customer "
                "messages, an order, follow-up, marketing, and reporting into one "
                "calm working rhythm."
            ),
            "customer_care_reason": (
                "Owners care because the shift from scattered responsibilities to "
                "visible order makes the working day feel manageable."
            ),
            "hero_subject": (
                "A real small-business owner arranging five physical work cards for "
                "customer messages, an order, follow-up, marketing, and reporting."
            ),
            "hero_relevance": (
                "The owner's hands and recognizable work cards belong to the exact "
                "daily responsibilities named by the campaign."
            ),
            "product_story": (
                "The owner arranges five physical work cards into one calm sequence; "
                "customer messages, an order, follow-up, marketing, and reporting sit "
                "in a visibly controlled order in the same working environment."
            ),
            "visual_metaphor": (
                "A visible editorial shift from scattered work to one controlled "
                "human rhythm."
            ),
            "scroll_stopping_hook": (
                "The decisive instant five scattered responsibilities align under "
                "one owner's hand."
            ),
        }
    elif kind == "strong":
        values = {
            "concept_name": "One owner, five responsibilities, one rhythm",
            "marketing_idea": (
                "A before-and-after human demonstration: one owner gathers customer "
                "messages and orders, then sorts follow-up, marketing, and reporting "
                "into one controlled rhythm."
            ),
            "customer_care_reason": (
                "Owners care because customer messages, orders, follow-up, marketing, "
                "and reporting compete for attention; the physical sequence makes a "
                "calmer working day visible."
            ),
            "hero_subject": (
                "A real small-business owner gathering customer messages and orders "
                "while sorting follow-up, marketing, and reporting cards."
            ),
            "hero_relevance": (
                "Every physical cue corresponds to one responsibility named by the "
                "campaign and converges under the owner's control."
            ),
            "product_story": (
                "A real small-business owner gathers customer messages and orders, "
                "sorts follow-up and marketing into related groups, and places a "
                "reporting card into one controlled sequence; scattered work becomes "
                "visibly ordered."
            ),
            "visual_metaphor": (
                "Five scattered responsibilities become one physical rhythm under "
                "the owner's hands."
            ),
            "scroll_stopping_hook": (
                "A decisive overhead editorial moment as five work streams align into "
                "one sequence."
            ),
        }
    else:
        raise ValueError("unknown production synthesis kind")
    candidates[0] = candidates[0].model_copy(update=values)
    return synthesis.model_copy(update={"candidates": tuple(candidates)})


def _production_execution(kind: str):
    return SimpleNamespace(
        output=_production_synthesis(kind),
        provider_metadata=AIAgentProviderMetadata(),
    )


@pytest.mark.asyncio
async def test_weak_initial_strong_text_repair_buys_only_after_second_director(runtime):
    runtime.execute.side_effect = [execution(strong=False), execution()]
    asset = asset_for_job()
    image, review = providers()
    async def image_after_repair(request):
        assert runtime.execute.await_count == 2
        assert asset.creative_metadata["director_state"]["calls"] == 2
        return CreativeGenerationResult(content=_png_bytes(), width=1024, height=1024)
    image.generate_draft.side_effect = image_after_repair
    result = await run(asset,image,review)
    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    runtime.authority.assert_awaited_once()
    assert image.generate_draft.await_count == 1
    task = runtime.request.await_args.args[2]
    assert "SERVER REPAIR" not in task and len(task) <= 4000
    repair_context = json.loads(runtime.execute.await_args.kwargs["server_context"])
    assert repair_context["reason"] == "concept_quality_failed"
    assert "This is the only text repair" in repair_context["instruction"]
    assert set(repair_context["rejected_proposal"]) == {"hero", "story"}


@pytest.mark.asyncio
async def test_production_campaign_strong_initial_uses_one_director_and_one_image(
    runtime,
):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.return_value = _production_execution("strong")
    asset = asset_for_job()
    asset.visual_direction = _production_strategy().model_dump_json()
    asset.instructions = _PRODUCTION_OWNER_INTENT
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 1
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1


@pytest.mark.asyncio
async def test_production_campaign_weak_then_strong_uses_two_directors_one_image(
    runtime,
):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.side_effect = [
        _production_execution("initial_weak"),
        _production_execution("strong"),
    ]
    asset = asset_for_job()
    asset.visual_direction = _production_strategy().model_dump_json()
    asset.instructions = _PRODUCTION_OWNER_INTENT
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 1


@pytest.mark.asyncio
async def test_production_soft_weak_hard_eligible_repair_reaches_v2_ready(
    real_runtime,
    monkeypatch,
    caplog,
):
    caplog.set_level("INFO", logger="aibos.marketing")
    authority = _saas_authority(business_id=BUSINESS_ID)
    real_runtime.authority.return_value = authority
    real_runtime.execute.side_effect = [
        _production_execution("initial_weak"),
        _production_execution("repair_soft"),
    ]
    asset = asset_for_job()
    asset.visual_direction = _production_strategy().model_dump_json()
    asset.instructions = _PRODUCTION_OWNER_INTENT
    image, review = providers()
    storage = _DurableCheckpointStorage()
    captured_inputs = []
    original_compose = marketing.CreativeCompositor.compose_candidates

    def capture_and_compose(compositor, composition_input):
        captured_inputs.append(composition_input)
        return original_compose(compositor, composition_input)

    monkeypatch.setattr(
        marketing.CreativeCompositor,
        "compose_candidates",
        capture_and_compose,
    )

    context = marketing.derive_public_research_context(
        business_type=_business_record().business_type,
        channel="instagram",
        asset_type=asset.asset_type,
        strategy_text=(
            f"{asset.instructions} {_production_strategy().marketing_goal} "
            f"{_production_strategy().campaign_angle}"
        ),
        visual_text=(
            f"{_production_strategy().visual_concept} "
            f"{_production_strategy().mood} "
            f"{_production_strategy().brand_treatment}"
        ),
    )
    research = marketing.degraded_research_bundle(
        marketing.build_research_request(context, max_results=12),
        provider="internal_patterns",
    )
    repaired = build_creative_direction(
        strategy=_production_strategy(),
        research=research,
        context=context,
        story_mode="brand_offer",
        authoritative_context=authority,
        synthesis=_production_synthesis("repair_soft"),
    )
    assert creative_direction_meets_hard_eligibility(repaired)
    assert not creative_direction_meets_quality_floor(repaired)

    result = await run(asset, image, review, storage=storage)

    assert result.generation_status == "ready"
    assert real_runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1
    assert any("/raw/" in key for key in storage.objects)
    assert captured_inputs
    composition_input = captured_inputs[0]
    assert composition_input.composition_plan is not None
    assert composition_input.headline == _PRODUCTION_HEADLINE
    assert composition_input.supporting_copy == _PRODUCTION_SUPPORTING
    assert composition_input.cta == _PRODUCTION_CTA
    provider_request = image.generate_draft.await_args.args[0]
    assert "CREATIVE ENGINE V2" in provider_request.instructions
    assert _PRODUCTION_HEADLINE not in provider_request.instructions
    critic_request = review.review.await_args.args[0]
    assert critic_request.expected_headline == _PRODUCTION_HEADLINE
    assert critic_request.expected_cta == _PRODUCTION_CTA
    assert "creative_research_degraded_continuing" in caplog.text
    assert "creative_soft_quality_below_target_proceeding_to_render" in caplog.text
    first_task = real_runtime.request.await_args_list[0].args[2]
    assert "OWNER CREATIVE INTENT" in first_task
    repair_context = json.loads(
        real_runtime.execute.await_args_list[1].kwargs["server_context"]
    )
    assert repair_context["deficiencies"]
    assert "one visible action" in repair_context["instruction"]


@pytest.mark.asyncio
async def test_two_hard_invalid_director_results_use_zero_call_grounded_rescue(
    runtime,
):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.side_effect = [
        _saas_pipeline_execution(strong=False),
        _saas_pipeline_execution(strong=False),
    ]
    asset = asset_for_job()
    asset.visual_direction = _production_strategy().model_dump_json()
    asset.instructions = _PRODUCTION_OWNER_INTENT
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1
    checkpoint = asset.creative_metadata["director_state"]["plans"]["1"]
    assert checkpoint["selected_concept"]["concept_name"] == "Grounded human sequence"


@pytest.mark.asyncio
async def test_two_soft_weak_hard_eligible_directions_reach_one_image(runtime):
    runtime.execute.side_effect = [execution(strong=False), execution(strong=False)]
    asset = asset_for_job()
    image, review = providers()
    result = await run(asset,image,review)
    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 1
    assert review.review.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["unchanged", "weak", "different"])
async def test_concept_failure_requires_materially_new_validated_direction(runtime, repair):
    runtime.execute.side_effect = [execution(), execution(strong=repair != "weak", alternative=repair == "different")]
    asset = asset_for_job()
    image, review = providers([concept_failure(), _visual_result(_visual_review())])
    result = await run(asset,image,review)
    assert runtime.execute.await_count == 2
    assert "PRIVATE_CRITIC_PROSE" not in runtime.request.await_args.args[2]
    assert "PRIVATE_CRITIC_PROSE" not in runtime.execute.await_args.kwargs["server_context"]
    assert image.generate_draft.await_count == 2
    assert result.generation_status == "ready"
    first, second = image.generate_draft.await_args_list
    if repair == "different":
        assert "Opening time balancing act" in first.args[0].instructions
        assert "The paper sunrise" in second.args[0].instructions
    else:
        assert first.args[0].instructions != second.args[0].instructions


@pytest.mark.asyncio
async def test_semantic_failure_after_text_repair_has_no_third_director_and_stays_bounded(runtime):
    runtime.execute.side_effect = [execution(strong=False), execution()]
    asset = asset_for_job()
    image, review = providers([concept_failure()])
    result = await run(asset,image,review)
    assert result.generation_status == "failed"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 2


@pytest.mark.asyncio
async def test_true_raw_execution_defect_reuses_valid_concept_with_two_images_max(runtime):
    runtime.execute.return_value = execution()
    asset = asset_for_job()
    image, review = providers([
        _visual_result(_visual_review(approved=False, repair_class="raw_visual", accidental_generated_text=True)),
        _visual_result(_visual_review()),
    ])
    result = await run(asset,image,review)
    assert result.generation_status == "ready"
    assert image.generate_draft.await_count == 2
    assert runtime.execute.await_count == 1


@pytest.mark.asyncio
async def test_raw_checkpoint_recovery_reuses_direction_and_buys_no_duplicate_image(runtime, monkeypatch):
    runtime.execute.return_value = execution()
    asset = asset_for_job()
    image, review = providers()
    storage = _DurableCheckpointStorage()
    def crash(*args):
        raise KeyboardInterrupt("simulate process loss after paid checkpoint")
    monkeypatch.setattr(marketing.CreativeCompositor,"compose_candidates", crash)
    with pytest.raises(KeyboardInterrupt):
        await run(asset,image,review,storage=storage)
    monkeypatch.setattr(marketing.CreativeCompositor,"compose_candidates", lambda *args: (_composed_candidate("minimal_hero",color=(100,120,140)),))
    result = await run(asset,image,review,storage=storage)
    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 1
    assert image.generate_draft.await_count == 1


@pytest.mark.asyncio
async def test_unknown_director_outcome_cannot_be_reissued(runtime):
    runtime.execute.side_effect = RuntimeError("simulated worker interruption")
    asset = asset_for_job()
    image, review = providers()
    with pytest.raises(RuntimeError):
        await run(asset,image,review)
    result = await run(asset,image,review)
    assert result.generation_status == "failed"
    assert runtime.execute.await_count == 1
    image.generate_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_reservation_commit_failure_calls_no_provider(runtime):
    asset = asset_for_job()
    image, review = providers()
    session = _FailingCommitSession([asset,_business_record(),None], fail_on_commit=2)
    with pytest.raises(MarketingPersistenceError):
        await run(asset,image,review,session=session)
    runtime.execute.assert_not_awaited()
    image.generate_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_director_budget_rejects_cross_tenant_asset_before_call(runtime):
    strategy, context, research = brand_inputs()
    asset = asset_for_job()
    with pytest.raises(MarketingNotFoundError):
        await marketing._creative_direction_with_fallback(
            _ScalarSession([]),business_id=uuid4(),strategy=strategy,context=context,research=research,
            provider=object(),max_output_tokens=4000,value=asset,
        )
    runtime.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_and_list_polling_cannot_start_director_or_images(runtime):
    asset = asset_for_job()
    image, review = providers()
    for _ in range(3):
        assert await marketing.get_creative_asset(_ScalarSession([asset]),business_id=BUSINESS_ID,creative_asset_id=asset.id) is asset
        assert await marketing.list_creative_assets(_ScalarSession([],rows=[[asset]]),business_id=BUSINESS_ID,campaign_id=None,content_id=None) == [asset]
    runtime.execute.assert_not_awaited()
    image.generate_draft.assert_not_awaited()
    review.review.assert_not_awaited()
    assert asset.generation_status == "queued"


def test_actual_brand_gate_accepts_scene_but_rejects_boilerplate():
    strategy,context,research=brand_inputs()
    for strong in (True,False):
        plan=build_creative_direction(strategy=strategy,context=context,research=research,story_mode="brand_offer",synthesis=brand_synthesis(strong=strong))
        assert creative_direction_meets_quality_floor(plan) is strong


@pytest.mark.parametrize("invention", [
    "A new app", "An invented application", "A new user interface", "A fictional UI",
    "A product in use", "A service in action", "A package", "A software feature",
    "A workflow", "An integration hub", "A fulfillment process", "A fulfillment path",
    "The brand doubles sales", "Customers saved hours", "Revenue rises 200%", "Proven results",
])
def test_invention_cannot_rescue_or_poison_an_otherwise_strong_scene(invention):
    strategy,context,research=brand_inputs()
    synthesis=brand_synthesis()
    candidates=list(synthesis.candidates)
    candidates[0]=candidates[0].model_copy(update={"hero_subject": f"{candidates[0].hero_subject} {invention}."})
    poisoned=synthesis.model_copy(update={"candidates":tuple(candidates)})
    plan=build_creative_direction(strategy=strategy,context=context,research=research,story_mode="brand_offer",synthesis=poisoned)
    assert not creative_direction_meets_quality_floor(plan)


@pytest.mark.asyncio
async def test_deployed_job_with_raw_attempt_but_no_direction_fails_without_new_spend(runtime):
    asset=asset_for_job()
    asset.creative_metadata["image_generation_started_attempt"]=1
    image,review=providers()
    result=await run(asset,image,review)
    assert result.generation_status == "failed"
    runtime.execute.assert_not_awaited()
    image.generate_draft.assert_not_awaited()


def test_director_dependency_disables_hidden_sdk_retries_only_for_creative_jobs(monkeypatch):
    from app.api.dependencies import creative
    from app.core.config import Settings
    from unittest.mock import Mock
    config=Settings(_env_file=None, database_url="postgresql+asyncpg://database.invalid/test", auth_secret_key="x"*32, openai_max_retries=5)
    build=Mock(return_value=object())
    monkeypatch.setattr(creative,"settings",config)
    monkeypatch.setattr(creative,"create_openai_provider",build)
    creative.get_creative_director_provider.cache_clear()
    try:
        creative.get_creative_director_provider()
    finally:
        creative.get_creative_director_provider.cache_clear()
    assert build.call_args.args[0].openai_max_retries == 0
    assert config.openai_max_retries == 5


def test_grounded_prop_with_quality_labels_but_no_visible_consequence_fails_gate():
    strategy,context,research=brand_inputs()
    synthesis=brand_synthesis()
    proposals=list(synthesis.candidates)
    proposals[0]=proposals[0].model_copy(update={
        "hero_subject":"A shopkeeper holding receipts in a premium atmospheric portrait.",
        "product_story":"A shopkeeper holds receipts for a campaign-specific contrast reveal consequence transition transformation; a premium atmosphere suggests quality.",
    })
    plan=build_creative_direction(strategy=strategy,context=context,research=research,story_mode="brand_offer",synthesis=synthesis.model_copy(update={"candidates":tuple(proposals)}))
    assert not creative_direction_meets_quality_floor(plan)


@pytest.mark.asyncio
async def test_recovery_after_concept_repair_preserves_both_image_directions(runtime, monkeypatch):
    runtime.execute.side_effect=[execution(),execution(alternative=True)]
    asset=asset_for_job()
    image,review=providers([concept_failure(),concept_failure(),_visual_result(_visual_review())])
    storage=_DurableCheckpointStorage()
    count=0
    def compose(*args):
        nonlocal count
        count+=1
        if count==2:
            raise KeyboardInterrupt("second paid checkpoint saved")
        return (_composed_candidate("minimal_hero",color=(100,120,140)),)
    monkeypatch.setattr(marketing.CreativeCompositor,"compose_candidates",compose)
    with pytest.raises(KeyboardInterrupt):
        await run(asset,image,review,storage=storage)
    result=await run(asset,image,review,storage=storage)
    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 2
    names=[call.args[0].concept_name for call in review.review.await_args_list]
    assert names == ["Opening time balancing act","Opening time balancing act","The paper sunrise"]


@pytest.mark.asyncio
async def test_exhausted_required_review_budget_cannot_buy_an_unreviewable_image(runtime):
    runtime.execute.return_value=execution()
    asset=asset_for_job()
    image,review=providers([concept_failure()])
    result=await marketing.run_queued_creative_asset_generation(
        _ScalarSession([asset,_business_record(),None,asset]),business_id=BUSINESS_ID,
        creative_asset_id=asset.id,provider=image,storage=_DurableCheckpointStorage(),
        director_provider=SimpleNamespace(provider_name="fake_director"),
        visual_review_provider=review,max_visual_review_calls=1,require_semantic_review=True,
    )
    assert result.generation_status == "failed"
    assert image.generate_draft.await_count == 1
    assert runtime.execute.await_count == 1


def test_new_explicit_epoch_resets_only_its_attempt_accounting():
    asset=asset_for_job()
    asset.creative_metadata.update(image_generation_started_attempt=2,director_state={"epoch":1,"calls":2},revision_of="retained-history")
    assert marketing._next_image_generation_epoch(asset) == 2
    assert asset.creative_metadata["image_generation_started_attempt"] == 0
    assert "director_state" not in asset.creative_metadata
    assert asset.creative_metadata["revision_of"] == "retained-history"


def test_grounded_scene_does_not_need_quality_vocabulary_to_pass():
    strategy,context,research=brand_inputs()
    synthesis=brand_synthesis()
    proposals=list(synthesis.candidates)
    proposals[0]=proposals[0].model_copy(update={
        "marketing_idea":"A shopkeeper balances receipts on one palm while lifting the shutter with the other; opening time splits attention between two physical tasks.",
        "customer_care_reason":"Receipts compete with opening shutters for the shopkeeper's hands every morning.",
        "hero_relevance":"The shopkeeper's arms must simultaneously support receipts and opening shutters.",
        "product_story":"Receipts pile on a shopkeeper's outstretched palm while opening shutters pull the other arm upward; the arms tilt in opposite directions.",
        "visual_metaphor":"The shopkeeper's two arms form an uneven scale between receipts and opening shutters.",
        "scroll_stopping_hook":"Tilting receipts above an open palm sit opposite a heavy shutter rising on the other side.",
    })
    plan=build_creative_direction(strategy=strategy,context=context,research=research,story_mode="brand_offer",synthesis=synthesis.model_copy(update={"candidates":tuple(proposals)}))
    assert creative_direction_meets_quality_floor(plan)



def _saas_pipeline_strategy():
    return brand_strategy().model_copy(
        update={
            "marketing_goal": (
                "Show small business owners how one AI team can help them stay "
                "across marketing, sales, support, operations, and reporting."
            ),
            "target_audience": (
                "Small business owners managing marketing, sales, support, "
                "operations, and reporting."
            ),
            "audience_insight": (
                "Marketing, sales, support, operations, and reporting compete "
                "for the owner's attention throughout the same working day."
            ),
            "campaign_angle": (
                "Marketing, sales, support, operations, and reporting move as "
                "one coordinated team around the owner."
            ),
            "headline": "Your AI Team for Business",
            "supporting_message": (
                "Bring marketing, sales, support, operations, and reporting "
                "together with AI agents working with you. Run Smarter."
            ),
            "cta": "Learn More",
            "visual_concept": (
                "Five grounded business work streams converge around one owner."
            ),
            "subject_focus": (
                "One small-business owner and five grounded business functions."
            ),
            "prohibited_claims": (
                "No invented outcomes.",
                "No invented interfaces.",
                "No unsupported product capabilities.",
            ),
        }
    )


def _saas_pipeline_synthesis(*, strong: bool):
    synthesis = brand_synthesis(strong=False)
    candidates = list(synthesis.candidates)

    generic_variants = (
        {
            "concept_name": "Generic AI network",
            "marketing_idea": (
                "A futuristic digital network suggests connected business."
            ),
            "customer_care_reason": (
                "Business owners want modern connected technology."
            ),
            "hero_subject": (
                "Floating glowing nodes on a dark blue gradient."
            ),
            "hero_relevance": "The network suggests modern technology.",
            "product_story": (
                "Glowing nodes float through a digital network."
            ),
            "visual_metaphor": "A glowing digital network.",
            "scroll_stopping_hook": "Blue glowing nodes.",
        },
        {
            "concept_name": "Premium productivity atmosphere",
            "marketing_idea": (
                "A clean premium workspace suggests smarter work."
            ),
            "customer_care_reason": (
                "Business owners want more focus."
            ),
            "hero_subject": (
                "A premium desk with laptop, plant, notebook and coffee."
            ),
            "hero_relevance": "The workspace represents productivity.",
            "product_story": (
                "A calm workspace creates a modern productivity mood."
            ),
            "visual_metaphor": "A clean workspace.",
            "scroll_stopping_hook": "Premium negative space.",
        },
        {
            "concept_name": "Abstract AI wave",
            "marketing_idea": (
                "A futuristic wave communicates AI momentum."
            ),
            "customer_care_reason": (
                "Owners want their business to feel modern."
            ),
            "hero_subject": (
                "A glowing blue wave with floating circles."
            ),
            "hero_relevance": "The wave represents technology.",
            "product_story": (
                "Abstract waves and circles imply connected intelligence."
            ),
            "visual_metaphor": "A futuristic digital wave.",
            "scroll_stopping_hook": "A bright glowing wave.",
        },
    )

    for index, updates in enumerate(generic_variants):
        candidates[index] = candidates[index].model_copy(update=updates)

    if strong:
        candidates[0] = candidates[0].model_copy(
            update={
                "concept_name": "One AI team, five business streams",
                "marketing_idea": (
                    "Five grounded business work streams for marketing, sales, "
                    "support, operations, and reporting approach one owner from "
                    "separate directions while AI agents coordinate and route "
                    "them into one coherent rhythm."
                ),
                "customer_care_reason": (
                    "Owners care because marketing, sales, support, operations, "
                    "and reporting compete for attention during the same day."
                ),
                "hero_subject": (
                    "One small-business owner at center while five distinct work "
                    "streams for marketing, sales, support, operations, and "
                    "reporting converge from separate directions."
                ),
                "hero_relevance": (
                    "The grounded business functions visibly converge around the "
                    "owner instead of appearing as unrelated decoration."
                ),
                "product_story": (
                    "Marketing and sales move in from one side while support and "
                    "operations pass from the other; AI agents coordinate and "
                    "route the five work streams so they converge into one aligned "
                    "rhythm around the owner."
                ),
                "visual_metaphor": (
                    "Five separate business work streams converge into one "
                    "coordinated rhythm around the owner."
                ),
                "scroll_stopping_hook": (
                    "Five visibly separate business streams converge around one "
                    "owner in a single decisive moment."
                ),
            }
        )

    return synthesis.model_copy(update={"candidates": tuple(candidates)})


def _saas_pipeline_execution(*, strong: bool):
    return SimpleNamespace(
        output=_saas_pipeline_synthesis(strong=strong),
        provider_metadata=AIAgentProviderMetadata(),
    )


def _automation_pipeline_execution():
    synthesis = _saas_pipeline_synthesis(strong=True)
    candidates = list(synthesis.candidates)
    candidates[0] = candidates[0].model_copy(
        update={
            "concept_name": "One automation workflow, one owner",
            "marketing_idea": (
                "An automation workflow coordinates marketing, sales and support "
                "around one owner."
            ),
            "hero_subject": (
                "One small-business owner watches an automation workflow route "
                "marketing, sales and support."
            ),
            "hero_relevance": (
                "The workflow is shown through a non-interface operational "
                "handoff around the owner."
            ),
            "product_story": (
                "The automation workflow routes marketing to sales and support "
                "converges into one owner response; marketing, sales and support "
                "move as one coordinated operating rhythm."
            ),
            "visual_metaphor": (
                "An automation workflow turns separate marketing, sales and "
                "support paths into one coordinated operating rhythm."
            ),
            "scroll_stopping_hook": (
                "Separate marketing, sales and support paths converge around one "
                "owner in a single decisive visual moment."
            ),
        }
    )
    return SimpleNamespace(
        output=synthesis.model_copy(update={"candidates": tuple(candidates)}),
        provider_metadata=AIAgentProviderMetadata(),
    )


@pytest.mark.asyncio
async def test_saas_brand_weak_then_strong_repair_reaches_one_image(runtime):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.side_effect = [
        _saas_pipeline_execution(strong=False),
        _saas_pipeline_execution(strong=True),
    ]

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 2
    runtime.authority.assert_awaited_once()
    assert image.generate_draft.await_count == 1
    assert review.review.await_count >= 1
    assert "Acme AI coordinates" not in json.dumps(asset.creative_metadata)


@pytest.mark.asyncio
async def test_saas_brand_strong_initial_reaches_one_image(runtime):
    runtime.authority.return_value = _saas_authority(business_id=BUSINESS_ID)
    runtime.execute.return_value = _saas_pipeline_execution(strong=True)

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assert runtime.execute.await_count == 1
    runtime.authority.assert_awaited_once()
    assert image.generate_draft.await_count == 1


@pytest.mark.asyncio
async def test_same_saas_direction_without_authoritative_brain_buys_zero_images(runtime):
    runtime.execute.side_effect = [
        _saas_pipeline_execution(strong=True),
        _saas_pipeline_execution(strong=True),
    ]

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "failed"
    assert runtime.execute.await_count == 2
    image.generate_draft.assert_not_awaited()
    review.review.assert_not_awaited()


@pytest.mark.asyncio
async def test_saas_brand_two_weak_directions_buy_zero_images(runtime):
    runtime.execute.side_effect = [
        _saas_pipeline_execution(strong=False),
        _saas_pipeline_execution(strong=False),
    ]

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "failed"
    assert runtime.execute.await_count == 2
    image.generate_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_authority_survives_scoring_to_renderer_and_fake_image_once(
    runtime,
    monkeypatch,
):
    import app.services.creative_authority as authority_service

    monkeypatch.setattr(
        marketing,
        "assemble_authoritative_creative_context",
        authority_service.assemble_authoritative_creative_context,
    )
    assemble = AsyncMock(
        return_value=_saas_brain_bundle(
            business_id=BUSINESS_ID,
            content=(
                "Acme AI provides automation workflows that coordinate "
                "marketing, sales and support."
            ),
        )
    )
    monkeypatch.setattr(authority_service, "assemble_ai_context", assemble)
    runtime.execute.return_value = _automation_pipeline_execution()

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "ready"
    assemble.assert_awaited_once()
    assert image.generate_draft.await_count == 1
    instructions = image.generate_draft.await_args.args[0].instructions
    assert "automation workflow" in instructions.casefold()
    assert "Acme AI provides automation workflows that coordinate" not in instructions


@pytest.mark.asyncio
async def test_same_automation_direction_without_authority_fails_before_image(runtime):
    runtime.execute.return_value = _automation_pipeline_execution()

    asset = asset_for_job()
    asset.visual_direction = _saas_pipeline_strategy().model_dump_json()
    image, review = providers()

    result = await run(asset, image, review)

    assert result.generation_status == "failed"
    image.generate_draft.assert_not_awaited()
    review.review.assert_not_awaited()
