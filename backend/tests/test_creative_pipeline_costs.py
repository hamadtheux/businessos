from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from creative_pipeline_fixtures import brand_inputs, brand_plan, brand_strategy, brand_synthesis
from test_marketing_service import (
    BUSINESS_ID, USER_ID, _ScalarSession, _FailingCommitSession,
    _business_record, _creative_asset, _DurableCheckpointStorage,
    _png_bytes, _composed_candidate, _visual_review, _visual_result,
)
from app.agents.provider import AIAgentProviderMetadata
from app.exceptions.marketing import MarketingAIError, MarketingNotFoundError, MarketingPersistenceError
from app.services import marketing
from app.services.creative_direction import build_creative_direction, creative_direction_meets_quality_floor
from app.services.creative_provider import CreativeGenerationResult


@pytest.fixture
def runtime(monkeypatch):
    request = AsyncMock(return_value=object())
    execute = AsyncMock()
    monkeypatch.setattr(marketing, "_build_cmo_execution_request", request)
    monkeypatch.setattr(marketing, "execute_ai_agent_typed_with_metadata", execute)
    monkeypatch.setattr(marketing.CreativeCompositor, "compose_candidates", lambda *args: (_composed_candidate("minimal_hero", color=(100,120,140)),))
    monkeypatch.setattr(marketing, "assess_creative_quality", lambda *args, **kwargs: SimpleNamespace(approved_for_delivery=True, approved_for_semantic_review=True, failure_kind=None, overall_score=90))
    return SimpleNamespace(execute=execute, request=request)


def execution(*, strong=True, alternative=False):
    return SimpleNamespace(output=brand_synthesis(strong=strong, alternative=alternative), provider_metadata=AIAgentProviderMetadata())


def asset_for_job():
    asset = _creative_asset(status="queued", visual_direction=brand_strategy().model_dump_json())
    asset.creative_metadata = {"image_generation_epoch": 1, "image_generation_started_attempt": 0, "generation_requested_by_user_id": str(USER_ID)}
    return asset


def providers(reviews=None):
    image = SimpleNamespace(provider_name="fake_image", generate_draft=AsyncMock(return_value=CreativeGenerationResult(content=_png_bytes(),width=1024,height=1024)))
    review = SimpleNamespace(provider_name="fake_vision", review=AsyncMock(side_effect=reviews or [_visual_result(_visual_review())]))
    return image, review


async def run(asset, image, review, *, storage=None, session=None):
    return await marketing.run_queued_creative_asset_generation(
        session or _ScalarSession([asset,_business_record(),None,asset]), business_id=BUSINESS_ID,
        creative_asset_id=asset.id, provider=image, storage=storage or _DurableCheckpointStorage(),
        director_provider=SimpleNamespace(provider_name="fake_director"), visual_review_provider=review,
        require_semantic_review=True,
    )


def concept_failure():
    return _visual_result(_visual_review(approved=False, repair_class="raw_visual",
        meaningless_focal_story=True, replaceable_brand_creative=True,
        commercially_weak=True, generic_template_output=True,
        repair_instructions="PRIVATE_CRITIC_PROSE must never reach repair request"))


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
    assert image.generate_draft.await_count == 1
    task = runtime.request.await_args.args[2]
    assert "SERVER REPAIR" in task and len(task) <= 4000


@pytest.mark.asyncio
async def test_two_weak_directions_fail_before_image_and_survive_worker_reentry(runtime):
    runtime.execute.side_effect = [execution(strong=False), execution(strong=False)]
    asset = asset_for_job()
    image, review = providers()
    for _ in range(3):
        result = await run(asset,image,review)
        assert result.generation_status == "failed"
        assert result.creative_metadata["image_generation_failure_stage"] == "direction_quality"
    assert runtime.execute.await_count == 2
    image.generate_draft.assert_not_awaited()
    review.review.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["unchanged", "weak", "different"])
async def test_concept_failure_requires_materially_new_validated_direction(runtime, repair):
    runtime.execute.side_effect = [execution(), execution(strong=repair != "weak", alternative=repair == "different")]
    asset = asset_for_job()
    image, review = providers([concept_failure(), _visual_result(_visual_review())])
    result = await run(asset,image,review)
    assert runtime.execute.await_count == 2
    assert "PRIVATE_CRITIC_PROSE" not in runtime.request.await_args.args[2]
    assert image.generate_draft.await_count == (2 if repair == "different" else 1)
    assert result.generation_status == ("ready" if repair == "different" else "failed")
    if repair == "different":
        first, second = image.generate_draft.await_args_list
        assert "Opening time balancing act" in first.args[0].instructions
        assert "The paper sunrise" in second.args[0].instructions


@pytest.mark.asyncio
async def test_semantic_failure_after_text_repair_has_no_third_director_or_second_image(runtime):
    runtime.execute.side_effect = [execution(strong=False), execution()]
    asset = asset_for_job()
    image, review = providers([concept_failure()])
    result = await run(asset,image,review)
    assert result.generation_status == "failed"
    assert runtime.execute.await_count == 2
    assert image.generate_draft.await_count == 1


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
