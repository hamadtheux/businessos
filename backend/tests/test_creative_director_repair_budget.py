"""Production-length repair requests must cross the real runtime/provider boundary."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from creative_pipeline_fixtures import brand_inputs, brand_strategy
from test_creative_pipeline_costs import asset_for_job, execution, providers, runtime
from test_marketing_service import BUSINESS_ID, _ScalarSession, _business_record, _DurableCheckpointStorage
from test_ai_agent_runtime import _context_bundle
from app.agents import runtime as agent_runtime
from app.agents.provider import AIAgentTypedProviderResult
from app.schemas.marketing import CreativeStrategyProposal
from app.services import marketing
from app.services.creative_direction import (
    CreativeDirectorTaskBudgetError, build_creative_director_task,
)


def long_strategy() -> CreativeStrategyProposal:
    values = brand_strategy().model_dump()
    values.update(
        marketing_goal=(
            "Build awareness of the pressures of opening a neighborhood shop, "
            "with a shopkeeper balancing receipts on one palm while lifting the "
            "opening shutter with the other hand in morning light."
        ),
        target_audience=(
            "Neighborhood shopkeepers handling receipts and opening shutters "
            "at the shop entrance before the morning begins."
        ),
        audience_insight=(
            "Shopkeepers juggle receipts while trying to open their shutters each morning. "
            "One hand holds receipts while the other lifts the shutter, leaving the shopkeeper "
            "balancing two physical tasks at the entrance. The campaign should make this familiar "
            "opening-time pressure visible through the position of the arms and the weight of the "
            "paper. Receipts, shutters, the palm and the threshold belong to the same scene; "
            "morning light establishes the occasion without promising commercial results."
        ),
        campaign_angle=(
            "Receipts compete with opening shutters for a shopkeeper's hands; "
            "the rising shutter pulls opposite the tilting paper."
        ),
        headline="Opening time asks a shopkeeper to balance more than the rising shutter",
        supporting_message=(
            "See the shopkeeper balance receipts on one palm while lifting the opening shutter with the other. "
            + "The arms, paper and morning light establish the shop-opening occasion. " * 7
        ).strip(),
        visual_concept=(
            "A shopkeeper, receipts and opening shutters in morning light; "
            "both hands and the shop entrance share the frame."
        ),
        subject_focus=(
            "A shopkeeper at opening time, with receipts balanced on one palm "
            "and the shutter held overhead."
        ),
        brand_treatment=(
            "Restrained blue palette and controlled brand identity; reserve "
            "space for the approved logo and headline."
        ),
        pr_guardrails=[
            "Use only the stated shop-opening occasion.", "No customer testimonials.",
            "No performance guarantees.", "No made-up discounts.",
            "No invented catalog items.", "No claims of labor savings.",
            "No unsupported brand capabilities.", "Human approval is required before publishing.",
        ],
    )
    return CreativeStrategyProposal.model_validate(values)


@pytest.mark.parametrize("boundary", [False, True])
def test_valid_initial_task_has_identical_repair_budget_and_facts(boundary):
    strategy = long_strategy()
    if boundary:
        values = strategy.model_dump()
        values["brand_treatment"] += " Keep all receipts inside the shop frame."
        strategy = CreativeStrategyProposal.model_validate(values)
    _, context, research = brand_inputs()
    initial = build_creative_director_task(
        strategy=strategy, context=context, research=research,
        story_mode="brand_offer", repair=False,
    )
    repaired = build_creative_director_task(
        strategy=strategy, context=context, research=research,
        story_mode="brand_offer", repair=True,
    )
    assert len(initial) == (4000 if boundary else 3959)
    assert repaired == initial
    for name in (
        "marketing_goal", "target_audience", "audience_insight", "campaign_angle",
        "headline", "visual_concept", "subject_focus", "brand_treatment",
    ):
        assert getattr(strategy, name) in repaired
    assert "do not invent a product" in repaired
    assert "Never include URLs" in repaired
    # supporting_message and PR guardrails are schema-valid long inputs, but
    # the Director task never embedded these fields. Do not change that boundary.
    assert len(strategy.supporting_message) == 594
    assert len(strategy.audience_insight) == 486
    assert len(strategy.pr_guardrails) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("strong_repair", [False, True])
async def test_long_campaign_soft_or_strong_repair_dispatches_through_actual_runtime(
    runtime, monkeypatch, caplog, strong_repair,
):
    strategy = long_strategy()
    _, context, research = brand_inputs()
    initial = build_creative_director_task(
        strategy=strategy, context=context, research=research,
        story_mode="brand_offer", repair=False,
    )
    assert len(initial) == 3959
    asset = asset_for_job()
    asset.visual_direction = strategy.model_dump_json()
    image, review = providers()
    executions = [execution(strong=False), execution(strong=strong_repair)]
    provider_requests = []

    async def generate_typed(request, output_type):
        provider_requests.append(request)
        image.generate_draft.assert_not_awaited()
        if len(provider_requests) == 2:
            assert "director_repair_dispatched" in caplog.messages
            assert "This is the only text repair" in request.task
            assert request.task.count("This is the only text repair") == 1
            assert "concept_quality_failed" in request.task
        value = executions[len(provider_requests) - 1]
        return AIAgentTypedProviderResult(output=value.output, metadata=value.provider_metadata)

    provider = SimpleNamespace(
        provider_name="fake_director", generate=AsyncMock(),
        generate_typed_with_metadata=AsyncMock(side_effect=generate_typed),
    )
    # Exercise the real 4,000-character request schema, context assembly/rendering
    # and provider dispatch. Only tenant data retrieval and the provider are fake.
    monkeypatch.setattr(marketing, "_build_cmo_execution_request", REAL_BUILD_REQUEST)
    dispatch = AsyncMock(wraps=agent_runtime.execute_ai_agent_typed_with_metadata)
    monkeypatch.setattr(marketing, "execute_ai_agent_typed_with_metadata", dispatch)

    async def assemble(session, business_id, request):
        return _context_bundle(business_id=business_id, purpose="marketing", task=request.task)
    monkeypatch.setattr(agent_runtime, "assemble_ai_context", assemble)
    caplog.set_level(logging.INFO, logger="aibos.marketing")
    result = await marketing.run_queued_creative_asset_generation(
        _ScalarSession([asset, _business_record(), None, asset]),
        business_id=BUSINESS_ID, creative_asset_id=asset.id,
        provider=image, storage=_DurableCheckpointStorage(),
        director_provider=provider, visual_review_provider=review,
        require_semantic_review=True,
    )
    assert dispatch.await_count == 2
    assert provider.generate_typed_with_metadata.await_count == 2
    assert image.generate_draft.await_count == 1
    assert result.generation_status == "ready"
    context_text = dispatch.await_args.kwargs["server_context"]
    assert len(context_text) <= 8000
    repair = json.loads(context_text)
    assert repair["deficiencies"]
    assert set(repair["rejected_proposal"]) == {"hero", "story"}
    assert len(repair["rejected_proposal"]["hero"]) <= 500
    assert len(repair["rejected_proposal"]["story"]) <= 400
    assert len(provider_requests[1].task) > 4000  # Runtime context has a separate budget.
    for event in (
        "director_repair_started", "director_repair_task_built",
        "director_repair_dispatched",
        "director_repair_succeeded" if strong_repair else "director_repair_failed_quality",
    ):
        assert event in caplog.messages
    assert "director_repair_task_invalid" not in caplog.messages
    assert strategy.audience_insight not in caplog.text
    if not strong_repair:
        assert "creative_soft_quality_below_target_proceeding_to_render" in caplog.messages


REAL_BUILD_REQUEST = marketing._build_cmo_execution_request


@pytest.mark.asyncio
async def test_oversized_repair_logs_numeric_diagnostics_without_dispatch(runtime, caplog):
    strategy = long_strategy()
    values = strategy.model_dump()
    values["brand_treatment"] += " Keep all receipts inside the shop frame.!"
    strategy = CreativeStrategyProposal.model_validate(values)
    _, context, research = brand_inputs()
    with pytest.raises(CreativeDirectorTaskBudgetError) as invalid:
        build_creative_director_task(
            strategy=strategy, context=context, research=research,
            story_mode="brand_offer", repair=True,
        )
    assert invalid.value.mandatory_length == 4001
    asset = asset_for_job()
    asset.creative_metadata["director_state"] = {
        "epoch": 1, "mode": "brand_offer", "calls": 1, "pending": False,
        "plans": {}, "rejected_scene": {"hero": "Rejected hero", "story": "Rejected story"},
    }
    caplog.set_level(logging.INFO, logger="aibos.marketing")
    with pytest.raises(marketing.MarketingAIError):
        await marketing._creative_direction_with_fallback(
            _ScalarSession([]), business_id=BUSINESS_ID, strategy=strategy,
            context=context, research=research, provider=object(),
            max_output_tokens=4000, story_mode="brand_offer", value=asset,
        )
    runtime.execute.assert_not_awaited()
    records = [record for record in caplog.records if record.message == "director_repair_task_invalid"]
    assert len(records) == 1
    record = records[0]
    assert record.task_length == record.mandatory_length == 4001
    assert record.max_task_length == 4000
    assert record.director_call_number == 2
    assert "director_repair_dispatched" not in caplog.messages
    assert strategy.audience_insight not in caplog.text


def test_repair_context_bounds_survive_unicode_and_reject_extra_payload():
    rejected = {"hero": "🟦" * 500, "story": "\n" * 400}
    context = marketing._creative_director_repair_context(rejected)
    assert len(context) < 8000
    assert json.loads(context)["rejected_proposal"] == rejected
    with pytest.raises(ValueError):
        marketing._creative_director_repair_context({**rejected, "raw_critic_prose": "not allowed"})
    with pytest.raises(ValueError):
        marketing._creative_director_repair_context({"hero": "x" * 501, "story": "story"})


@pytest.mark.parametrize("field, maximum", [
    ("marketing_goal", 300), ("target_audience", 500), ("audience_insight", 500),
    ("campaign_angle", 500), ("headline", 180), ("supporting_message", 600),
    ("visual_concept", 900), ("subject_focus", 500), ("brand_treatment", 700),
])
def test_individual_schema_maxima_preserve_identical_initial_and_repair_tasks(field, maximum):
    # Exercise each schema maximum independently: all maxima combined cannot
    # fit the unchanged global task limit. The realistic joint fixture above
    # separately reproduces the production overflow at 3,959 characters.
    values = brand_strategy().model_dump()
    original = values[field]
    values[field] = original + " " + "campaign detail " * maximum
    values[field] = values[field][:maximum - 1] + "."
    strategy = CreativeStrategyProposal.model_validate(values)
    assert len(getattr(strategy, field)) == maximum
    _, context, research = brand_inputs()
    initial = build_creative_director_task(
        strategy=strategy, context=context, research=research,
        story_mode="brand_offer", repair=False,
    )
    repaired = build_creative_director_task(
        strategy=strategy, context=context, research=research,
        story_mode="brand_offer", repair=True,
    )
    assert repaired == initial
    assert len(repaired) <= 4000
    if field != "supporting_message":
        assert getattr(strategy, field) in repaired
