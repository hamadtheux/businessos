from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.schemas.ai_context import (
    AIContextBundle,
    BusinessBrainContextSource,
    BusinessMemoryContextSource,
)
from app.services.creative_authority import (
    AuthoritativeCreativeContext,
    assemble_authoritative_creative_context,
    build_authoritative_creative_context,
)
from app.services.ai_context_policy import cmo_context_policy
from app.services.creative_visual_review import build_visual_review_task
from app.services.creative_world_class import (
    assess_world_class_creative,
    world_class_director_contract,
    world_class_raw_visual_contract,
    world_class_regeneration_instruction,
)


def _strong_9d_brain_concept(**overrides: str):
    values = {
        "business_context": (
            "9D Brain AI Business Operating System coordinates AI agents across "
            "sales, marketing, support, operations, automation workflows, and "
            "customer conversations."
        ),
        "campaign_goal": (
            "Demonstrate how an AI team reduces repetitive business work while "
            "keeping the business owner in control."
        ),
        "audience": (
            "Small business owners managing leads, campaigns, customer support, "
            "and daily operations."
        ),
        "subject_focus": (
            "9D Brain AI agents coordinating a customer inquiry through a real "
            "business workflow."
        ),
        "campaign_angle": (
            "One customer inquiry becomes coordinated action across the business."
        ),
        "marketing_idea": (
            "Demonstration: one customer inquiry enters the system and AI agents "
            "coordinate sales response, campaign follow-up, support handoff, and "
            "owner oversight."
        ),
        "customer_care_reason": (
            "Owners care because repetitive handoffs consume time; coordinated "
            "automation reduces manual work so that they can focus on decisions."
        ),
        "hero_subject": (
            "A credible customer interaction moving through one connected workflow "
            "with AI agents visibly coordinating sales, marketing, support, and "
            "business operations."
        ),
        "hero_relevance": (
            "The workflow is shown in action and demonstrates the supported system "
            "causing a real operational outcome."
        ),
        "product_story": (
            "A customer inquiry enters the system, AI agents coordinate the workflow "
            "and handoffs, and the owner receives a clear outcome without manually "
            "moving work between teams."
        ),
        "visual_metaphor": (
            "A before and after transformation from fragmented customer work to one "
            "coordinated operational workflow."
        ),
        "scroll_stopping_hook": (
            "A visual reveal showing one inquiry transform into coordinated sales, "
            "campaign, support, and owner outcomes."
        ),
    }
    values.update(overrides)
    return assess_world_class_creative(**values)


def test_world_class_engine_rejects_generic_premium_desk_creative() -> None:
    result = assess_world_class_creative(
        business_context=(
            "9D Brain is an AI Business Operating System for modern businesses. "
            "It coordinates AI agents for marketing, sales, support, operations, "
            "customer conversations, campaigns, automation, and reporting."
        ),
        campaign_goal=(
            "Show business owners that 9D Brain acts like an AI team that handles "
            "real business work and reduces manual workload."
        ),
        audience="Small and medium business owners who want AI automation.",
        subject_focus="9D Brain AI Business Operating System and its AI agents.",
        campaign_angle="Make space for what matters.",
        marketing_idea=(
            "A calm premium productivity scene communicating that business owners "
            "can focus on important work."
        ),
        customer_care_reason=(
            "Business owners want more focus and less clutter in their working day."
        ),
        hero_subject=(
            "A premium minimalist desk with stacked books, glasses, plant, pen, "
            "ceramic objects and clean negative space."
        ),
        hero_relevance=(
            "The clean desk symbolizes productivity and having more time."
        ),
        product_story=(
            "The workspace implies that using 9D Brain helps owners simplify their day."
        ),
        visual_metaphor="A clean premium workspace with space to breathe.",
        scroll_stopping_hook=(
            "Large clean headline beside a sophisticated lifestyle desk scene."
        ),
    )

    assert result.approved is False
    assert result.stock_lifestyle_risk >= 90
    assert result.replaceable_brand_risk >= 90
    assert result.product_service_mechanism < 50
    assert result.visual_proof < 50
    assert result.commercial_readiness < 50

    assert {
        "generic_lifestyle_stock_scene",
        "no_product_service_mechanism",
        "replaceable_brand_idea",
        "weak_visual_proof",
    }.issubset(result.hard_failures)


def test_world_class_engine_accepts_specific_cause_mechanism_outcome_story() -> None:
    result = _strong_9d_brain_concept()

    assert result.approved is True
    assert result.business_specificity >= 70
    assert result.product_service_mechanism >= 70
    assert result.customer_causality >= 62
    assert result.marketing_idea_strength >= 68
    assert result.visual_proof >= 68
    assert result.differentiation >= 62
    assert result.commercial_readiness >= 68

    assert result.stock_lifestyle_risk < 55
    assert result.decorative_abstraction_risk < 55
    assert result.replaceable_brand_risk < 55
    assert result.hard_failures == ()


def test_offering_does_not_trigger_ring_decorative_false_positive() -> None:
    result = _strong_9d_brain_concept(
        product_story=(
            "The offering coordinates a customer workflow, demonstrates the service "
            "in action, and creates an operational outcome."
        ),
        hero_subject=(
            "The offering in use during a real customer interaction and workflow."
        ),
    )

    assert "decorative_abstraction_as_story" not in result.hard_failures
    assert result.decorative_abstraction_risk < 55


def test_replaceable_generic_productivity_metaphor_is_not_rescued_by_brand_name() -> None:
    result = _strong_9d_brain_concept(
        campaign_angle="Work smarter with 9D Brain.",
        marketing_idea=(
            "A premium productivity scene showing a calm stylish office and more focus."
        ),
        hero_subject=(
            "A stylish office desk with coffee cup, plant, notebook, and laptop."
        ),
        hero_relevance=(
            "The office represents peace of mind and productivity for 9D Brain users."
        ),
        product_story=(
            "The environment suggests that the 9D Brain offering helps people "
            "work smarter."
        ),
        visual_metaphor="A clean workspace representing more focus.",
        scroll_stopping_hook="A premium minimal desk with strong negative space.",
    )

    assert result.approved is False
    assert result.replaceable_brand_risk >= 55
    assert (
        "generic_lifestyle_stock_scene" in result.hard_failures
        or "generic_productivity_metaphor" in result.hard_failures
        or "replaceable_brand_idea" in result.hard_failures
    )


def test_visual_review_task_uses_real_newlines_not_literal_escape_text() -> None:
    task = build_visual_review_task(
        SimpleNamespace(
            campaign_objective="Demonstrate the supported product clearly.",
            channel="instagram",
            concept_name="Product proof",
            concept_expectations=(
                "Show the supported offering causing a visible customer outcome."
            ),
            expected_headline="See the difference",
            expected_offer=None,
            expected_cta="Learn more",
            brand_expectations="Use the controlled tenant identity.",
            quality_threshold=82,
            review_mode="offering_proof",
        )
    )

    assert "\n\nCAMPAIGN EXPECTATIONS:\n" in task
    assert "\nSCORING STANDARD:\n" in task
    assert "\nAPPROVAL TEST:\n" in task

    # The provider must receive formatted policy text, not visible backslash+n
    # escape sequences.
    assert "\\n" not in task


def test_actual_coffee_product_is_not_treated_as_generic_coffee_prop() -> None:
    result = assess_world_class_creative(
        business_context=(
            "A specialty coffee roaster selling fresh single-origin coffee."
        ),
        campaign_goal=(
            "Demonstrate freshness and the sensory result of freshly roasted coffee."
        ),
        audience="Coffee customers who care about roast freshness and flavor.",
        subject_focus=(
            "Freshly roasted coffee served in a coffee cup and coffee mug."
        ),
        campaign_angle=(
            "Show the direct transformation from fresh roast to aromatic cup."
        ),
        marketing_idea=(
            "A product demonstration reveals freshly roasted beans becoming the "
            "finished coffee experience."
        ),
        customer_care_reason=(
            "Customers care because roast freshness creates a more aromatic and "
            "flavorful cup."
        ),
        hero_subject=(
            "A coffee cup and coffee mug containing the finished fresh-roast product."
        ),
        hero_relevance=(
            "The actual coffee product is shown in use as the result of the roast."
        ),
        product_story=(
            "Freshly roasted beans transform into the finished cup, demonstrating "
            "the product in use and the resulting coffee experience."
        ),
        visual_metaphor=(
            "A before and after transformation from roasted beans to finished coffee."
        ),
        scroll_stopping_hook=(
            "A visual reveal connects the fresh roast directly to the finished cup."
        ),
    )

    assert "generic_lifestyle_stock_scene" not in result.hard_failures

    # Coffee imagery is legitimate here because the authoritative subject focus
    # says coffee is the product, rather than a random productivity prop.
    assert result.stock_lifestyle_risk < 55


def test_actual_jewelry_ring_is_not_treated_as_decorative_ring_geometry() -> None:
    result = assess_world_class_creative(
        business_context=(
            "A fine jewelry business creating custom engagement jewelry."
        ),
        campaign_goal=(
            "Demonstrate the craftsmanship and meaning of a custom engagement ring."
        ),
        audience="Customers choosing a distinctive engagement ring.",
        subject_focus=(
            "A handcrafted engagement ring as the actual jewelry product."
        ),
        campaign_angle=(
            "Show how personal design decisions become a one-of-one finished ring."
        ),
        marketing_idea=(
            "A craftsmanship demonstration connects the customer's design choice "
            "to the finished jewelry outcome."
        ),
        customer_care_reason=(
            "Customers care because a custom ring turns a personal choice into a "
            "lasting one-of-one object."
        ),
        hero_subject=(
            "The finished engagement ring shown as the real product in use."
        ),
        hero_relevance=(
            "The ring is the supported product and visibly proves the craftsmanship."
        ),
        product_story=(
            "A customer design choice moves through the custom-making process and "
            "results in the finished engagement ring."
        ),
        visual_metaphor=(
            "The ring itself is the proof of the customer's design becoming real."
        ),
        scroll_stopping_hook=(
            "A transformation reveal moves from design choice to finished ring."
        ),
    )

    assert "decorative_abstraction_as_story" not in result.hard_failures
    assert result.decorative_abstraction_risk < 55


def test_server_owned_regeneration_instruction_targets_generic_stock_failure() -> None:
    instruction = world_class_regeneration_instruction(
        (
            "generic_lifestyle_stock_scene",
            "replaceable_brand_idea",
        )
    )

    normalized = instruction.casefold()

    assert "business-specific" in normalized
    assert "product or service" in normalized
    assert "office props" in normalized

    # Correction is server-owned and never echoes arbitrary critic prose.
    assert "chain-of-thought" not in normalized
    assert "http://" not in normalized
    assert "https://" not in normalized


def _brand_offer_concept(**overrides: str):
    values = {
        "business_context": (
            "9D Brain is a technology brand for small business owners managing "
            "marketing, sales, support, and daily operations."
        ),
        "campaign_goal": "Build awareness through a distinctive launch-day campaign.",
        "audience": "Small business owners facing a crowded launch day.",
        "subject_focus": "A grounded launch-day audience tension and reveal.",
        "campaign_angle": "Turn launch-day noise into one confident decision moment.",
        "marketing_idea": (
            "A campaign-specific before-and-after contrast turns launch-day tension "
            "into one unmistakable branded reveal."
        ),
        "customer_care_reason": (
            "Owners care because launch-day noise creates hesitation; the reveal "
            "turns that tension into a clear next decision."
        ),
        "hero_subject": (
            "A small-business launch moment split between crowded signals and one "
            "confident brand-owned decision."
        ),
        "hero_relevance": (
            "The visual contrast and reveal are specific to the grounded launch "
            "campaign and its small-business audience."
        ),
        "product_story": (
            "The compatibility field describes audience tension, visual transition, "
            "reveal, and consequence without asserting an offering."
        ),
        "visual_metaphor": (
            "A launch-day transition from crowded choices to a single confident path."
        ),
        "scroll_stopping_hook": (
            "An unexpected visual reveal resolves the campaign tension in one glance."
        ),
        "story_mode": "brand_offer",
    }
    values.update(overrides)
    return assess_world_class_creative(**values)


def test_story_mode_entry_points_reject_invalid_values() -> None:
    invalid = "unsupported_mode"
    kwargs = {
        "business_context": "Grounded business context",
        "campaign_goal": "Grounded campaign goal",
        "audience": "Grounded audience",
        "subject_focus": "Grounded subject",
        "campaign_angle": "Grounded angle",
        "marketing_idea": "Grounded idea",
        "customer_care_reason": "Grounded reason",
        "hero_subject": "Grounded hero",
        "hero_relevance": "Grounded relevance",
        "product_story": "Grounded story",
        "visual_metaphor": "Grounded metaphor",
        "scroll_stopping_hook": "Grounded hook",
        "story_mode": invalid,
    }
    import pytest

    with pytest.raises(ValueError, match="story mode"):
        assess_world_class_creative(**kwargs)  # type: ignore[arg-type]
    for selector in (world_class_director_contract, world_class_raw_visual_contract):
        with pytest.raises(ValueError, match="story mode"):
            selector(invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="story mode"):
        world_class_regeneration_instruction((), story_mode=invalid)  # type: ignore[arg-type]


def test_offering_proof_keeps_the_existing_product_mechanism_contract() -> None:
    implicit = _strong_9d_brain_concept()
    explicit = _strong_9d_brain_concept(story_mode="offering_proof")
    assert explicit == implicit
    assert "actual supported product" in world_class_raw_visual_contract(
        "offering_proof"
    ).casefold()
    assert "supported product or service" in world_class_regeneration_instruction(
        ("weak_visual_proof",),
        story_mode="offering_proof",
    ).casefold()


def test_brand_offer_accepts_a_grounded_campaign_mechanism_without_product_proof() -> None:
    from creative_pipeline_fixtures import brand_inputs, brand_synthesis
    strategy, context, _ = brand_inputs()
    concept = brand_synthesis().candidates[0]
    result = assess_world_class_creative(
        business_context=f"{context.industry} {strategy.audience_insight}",
        campaign_goal=strategy.marketing_goal, audience=strategy.target_audience,
        subject_focus=strategy.subject_focus, campaign_angle=strategy.campaign_angle,
        **{name: getattr(concept, name) for name in (
            "marketing_idea", "customer_care_reason", "hero_subject", "hero_relevance",
            "product_story", "visual_metaphor", "scroll_stopping_hook",
        )}, story_mode="brand_offer",
    )
    assert "no_product_service_mechanism" not in result.hard_failures
    assert result.approved is True


def test_brand_offer_quality_vocabulary_cannot_self_validate() -> None:
    result = _brand_offer_concept()
    assert not result.approved
    assert "weak_visual_proof" in result.hard_failures
    assert "no_business_specific_mechanism" in result.hard_failures


def test_brand_offer_cannot_launder_generic_props_through_generated_subject_focus() -> None:
    result = _brand_offer_concept(
        subject_focus="Premium desk, coffee mug, plant, notebook, and laptop.",
        marketing_idea="A premium desk scene creates a calm productivity mood.",
        customer_care_reason="Owners want more focus and peace of mind.",
        hero_subject="A desk with coffee mug, plant, notebook, and laptop.",
        hero_relevance="The desk suggests productivity.",
        product_story="The quiet workspace suggests working smarter.",
        visual_metaphor="Make space for what matters.",
        scroll_stopping_hook="A polished stock workspace with negative space.",
    )
    assert result.approved is False
    assert "generic_lifestyle_stock_scene" in result.hard_failures
    assert "replaceable_brand_idea" in result.hard_failures


def test_brand_offer_rejects_decorative_or_weak_replaceable_mechanisms() -> None:
    cases = (
        {
            "marketing_idea": "Abstract gradients and circles imply momentum.",
            "hero_subject": "Floating blue rings, waves, circles, and glowing orbs.",
            "hero_relevance": "The decorative geometry feels modern.",
            "product_story": "Abstract shapes create a premium mood.",
            "visual_metaphor": "A generic blue gradient wave.",
            "scroll_stopping_hook": "A glowing orb on a dark background.",
        },
        {
            "marketing_idea": "A clean premium campaign for modern companies.",
            "hero_subject": "An anonymous polished lifestyle scene.",
            "hero_relevance": "It looks professional.",
            "product_story": "The scene communicates quality.",
            "visual_metaphor": "Quiet confidence.",
            "scroll_stopping_hook": "Large empty luxury space.",
        },
    )
    for values in cases:
        result = _brand_offer_concept(**values)
        assert result.approved is False
        assert set(result.hard_failures).intersection(
            {
                "decorative_abstraction_as_story",
                "replaceable_brand_idea",
                "weak_marketing_mechanism",
                "weak_visual_proof",
            }
        )


def test_brand_offer_contracts_forbid_invention_without_requiring_an_offering() -> None:
    director = world_class_director_contract("brand_offer").casefold()
    raw = world_class_raw_visual_contract("brand_offer").casefold()
    retry = world_class_regeneration_instruction(
        ("weak_visual_proof",),
        story_mode="brand_offer",
    ).casefold()
    for contract in (director, raw, retry):
        for forbidden in ("product", "service", "package", "app", "interface", "ui", "feature", "workflow"):
            assert forbidden in contract
        assert "do not invent" in contract
    assert "actual supported product" not in raw
    assert "supported product" not in retry
    assert "product moment" not in retry
    assert "service moment" not in retry



def _saas_authority(
    *,
    business_id: UUID,
    content: str = (
        "Acme AI coordinates AI-assisted marketing, sales, support, operations, "
        "and reporting for small businesses."
    ),
) -> AuthoritativeCreativeContext:
    return build_authoritative_creative_context(
        _saas_brain_bundle(business_id=business_id, content=content),
        business_id=business_id,
    )


def _saas_brain_bundle(*, business_id: UUID, content: str):
    source = BusinessBrainContextSource(
        business_id=business_id,
        source_type="knowledge_entry",
        source_id="knowledge:saas-capability",
        title="Public platform capability",
        content=content,
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
        content_hash=sha256(content.encode()).hexdigest(),
    )
    return AIContextBundle(
        business_id=business_id,
        purpose="marketing",
        task="Assemble creative authority.",
        sources=[source],
        source_count=1,
        business_brain_source_count=1,
        memory_source_count=0,
        revision="a" * 64,
    )


def _saas_authority_from_contents(
    *,
    business_id: UUID,
    contents: tuple[str, ...],
) -> AuthoritativeCreativeContext:
    sources = [
        BusinessBrainContextSource(
            business_id=business_id,
            source_type="knowledge_entry",
            source_id=f"knowledge:saas-capability-{index}",
            title=f"Capability segment {index}",
            content=content,
            updated_at=datetime(2026, 8, 24, tzinfo=UTC),
            content_hash=sha256(content.encode()).hexdigest(),
        )
        for index, content in enumerate(contents)
    ]
    bundle = AIContextBundle(
        business_id=business_id,
        purpose="marketing",
        task="Assemble creative authority.",
        sources=sources,
        source_count=len(sources),
        business_brain_source_count=len(sources),
        memory_source_count=0,
        revision="e" * 64,
    )
    return build_authoritative_creative_context(bundle, business_id=business_id)


def _saas_brand_offer_assessment(
    *,
    authoritative_context: AuthoritativeCreativeContext | None = None,
    **overrides: str,
):
    values = {
        "business_context": (
            "Technology business serving small business owners."
        ),
        "campaign_goal": (
            "Show one AI team helping the owner stay across marketing, sales, "
            "support, operations, and reporting."
        ),
        "audience": (
            "Small business owners managing marketing, sales, support, "
            "operations, and reporting."
        ),
        "subject_focus": "An AI team for business.",
        "campaign_angle": (
            "Marketing, sales, support, operations, and reporting move as one "
            "coordinated team around the owner."
        ),
        "marketing_idea": (
            "Five business work streams for marketing, sales, support, operations, "
            "and reporting approach one owner from separate directions while AI "
            "agents coordinate and route them into one coherent rhythm."
        ),
        "customer_care_reason": (
            "Owners care because marketing, sales, support, operations, and "
            "reporting compete for attention during the same working day."
        ),
        "hero_subject": (
            "One small-business owner at the center while five distinct work "
            "streams for marketing, sales, support, operations, and reporting "
            "converge from separate directions."
        ),
        "hero_relevance": (
            "The five grounded business functions visibly converge around the "
            "owner rather than appearing as unrelated decoration."
        ),
        "product_story": (
            "Marketing and sales move in from one side while support and operations "
            "pass from the other; AI agents coordinate and route the five work "
            "streams so they converge into one aligned rhythm around the owner."
        ),
        "visual_metaphor": (
            "Five separate business work streams converge into one coordinated "
            "operating rhythm around the owner."
        ),
        "scroll_stopping_hook": (
            "Five visibly separate business streams converge around one owner in "
            "a single decisive visual moment."
        ),
        "story_mode": "brand_offer",
    }
    values.update(overrides)
    return assess_world_class_creative(
        **values,
        authoritative_evidence_segments=(
            authoritative_context.evidence_segments
            if authoritative_context is not None
            else ()
        ),
    )


def test_brand_offer_accepts_grounded_saas_operational_relationship() -> None:
    result = _saas_brand_offer_assessment(
        authoritative_context=_saas_authority(business_id=uuid4()),
    )

    assert result.approved is True
    assert result.product_service_mechanism >= 70
    assert result.customer_causality >= 62
    assert result.visual_proof >= 68
    assert result.hard_failures == ()


def test_generated_strategy_cannot_authorize_the_same_saas_capability() -> None:
    business_id = uuid4()
    authorized = _saas_brand_offer_assessment(
        authoritative_context=_saas_authority(business_id=business_id),
    )
    unauthorized = _saas_brand_offer_assessment()

    assert authorized.approved is True
    assert unauthorized.approved is False
    assert "no_business_specific_mechanism" in unauthorized.hard_failures


def test_cross_tenant_brain_source_cannot_build_creative_authority() -> None:
    owner_id = uuid4()
    other_id = uuid4()
    content = "Acme AI coordinates marketing, sales, support, operations, and reporting."
    source = BusinessBrainContextSource(
        business_id=other_id,
        source_type="knowledge_entry",
        source_id="knowledge:other-tenant",
        title="Other tenant capability",
        content=content,
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
        content_hash=sha256(content.encode()).hexdigest(),
    )
    bundle = AIContextBundle(
        business_id=owner_id,
        purpose="marketing",
        task="Assemble creative authority.",
        sources=[source],
        source_count=1,
        business_brain_source_count=1,
        memory_source_count=0,
        revision="b" * 64,
    )

    with pytest.raises(ValueError, match="cross-tenant"):
        build_authoritative_creative_context(bundle, business_id=owner_id)


def test_persistent_memory_alone_cannot_build_creative_authority() -> None:
    business_id = uuid4()
    memory = BusinessMemoryContextSource(
        business_id=business_id,
        memory_id=uuid4(),
        memory_type="semantic",
        content="The company uses predictive revenue forecasting.",
        importance=5,
        confidence="1.000",
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
        content_hash="c" * 64,
    )
    bundle = AIContextBundle(
        business_id=business_id,
        purpose="marketing",
        task="Assemble creative authority.",
        sources=[memory],
        source_count=1,
        business_brain_source_count=0,
        memory_source_count=1,
        revision="d" * 64,
    )

    with pytest.raises(ValueError, match="memory"):
        build_authoritative_creative_context(bundle, business_id=business_id)


def test_verified_capabilities_do_not_enable_fake_ui_or_unsupported_forecasting() -> None:
    authority = _saas_authority(business_id=uuid4())
    fake_ui = _saas_brand_offer_assessment(
        authoritative_context=authority,
        hero_subject="A fake dashboard interface showing marketing and sales.",
        hero_relevance="The dashboard represents the platform.",
        product_story="Render a fake dashboard interface that coordinates marketing and sales.",
    )
    unsupported = _saas_brand_offer_assessment(
        authoritative_context=authority,
        marketing_idea="Predictive revenue AI automatically forecasts next-quarter revenue.",
        hero_subject="Predictive revenue forecasting for the business owner.",
        hero_relevance="The forecasting capability is the visual hero.",
        product_story="Predictive revenue forecasting routes next-quarter revenue decisions.",
    )

    assert fake_ui.approved is False
    assert unsupported.approved is False


def test_verified_non_ui_feature_can_ground_a_story_but_fake_dashboard_stays_blocked() -> None:
    authority = _saas_authority(
        business_id=uuid4(),
        content=(
            "Acme AI provides an automated feature that coordinates marketing, "
            "sales and support workflows."
        ),
    )
    verified = _saas_brand_offer_assessment(
        authoritative_context=authority,
        marketing_idea=(
            "The verified feature coordinates marketing, sales and support "
            "around one owner."
        ),
        hero_subject=(
            "One owner watches the verified feature route marketing, sales and "
            "support through one workflow."
        ),
        hero_relevance="The feature is shown through a non-interface operational handoff.",
        product_story=(
            "The feature routes marketing to sales while support converges into "
            "one owner response through the workflow."
        ),
    )
    fake_dashboard = _saas_brand_offer_assessment(
        authoritative_context=authority,
        hero_subject="A fake dashboard interface showing the verified feature.",
        product_story=(
            "Render a fake dashboard interface that shows the feature routing "
            "marketing, sales and support."
        ),
    )

    assert verified.approved is True
    assert fake_dashboard.approved is False


def test_negative_brain_evidence_does_not_authorize_positive_forecasting() -> None:
    authority = _saas_authority(
        business_id=uuid4(),
        content="Acme does NOT provide predictive revenue forecasting.",
    )
    result = _saas_brand_offer_assessment(
        authoritative_context=authority,
        marketing_idea="Predictive revenue forecasting coordinates the business.",
        hero_subject="Predictive revenue forecasting for the business owner.",
        hero_relevance="The forecasting capability is the visual hero.",
        product_story="Predictive revenue forecasting routes revenue decisions.",
    )

    assert result.approved is False


def test_unrelated_brain_segments_cannot_be_combined_into_one_capability() -> None:
    authority = _saas_authority_from_contents(
        business_id=uuid4(),
        contents=(
            "Acme AI provides automation for marketing and sales.",
            "Acme AI provides support workflows.",
        ),
    )
    result = _saas_brand_offer_assessment(
        authoritative_context=authority,
        marketing_idea="An automation workflow coordinates marketing, sales and support.",
        hero_subject="An automation workflow joins marketing, sales and support for one owner.",
        product_story=(
            "The automation workflow routes marketing to sales and support into "
            "one coordinated response."
        ),
    )

    assert result.approved is False


def test_one_positive_brain_segment_can_authorize_a_matching_operational_story() -> None:
    authority = _saas_authority(
        business_id=uuid4(),
        content="Acme AI provides automated marketing, sales and support workflows.",
    )
    result = _saas_brand_offer_assessment(authoritative_context=authority)

    assert result.approved is True


def test_creative_authority_uses_the_cmo_privacy_source_boundary() -> None:
    healthcare = cmo_context_policy("clinic")
    professional = cmo_context_policy("professional services")
    real_estate = cmo_context_policy("real estate")

    assert healthcare.include_memory is False
    assert healthcare.brain_source_types == (
        "business_profile", "branding", "appointment_type"
    )
    assert professional.include_memory is False
    assert professional.brain_source_types == healthcare.brain_source_types
    assert professional.privacy_instruction != healthcare.privacy_instruction
    assert real_estate.brain_source_types == (
        "business_profile", "branding", "knowledge_entry"
    )


@pytest.mark.asyncio
async def test_authority_assembly_uses_business_brain_without_memory(monkeypatch) -> None:
    import app.services.creative_authority as authority_service

    business_id = uuid4()
    bundle = _saas_brain_bundle(
        business_id=business_id,
        content=(
            "Acme AI coordinates AI-assisted marketing, sales, support, "
            "operations, and reporting for small businesses."
        ),
    )
    assemble = AsyncMock(return_value=bundle)
    monkeypatch.setattr(authority_service, "assemble_ai_context", assemble)

    result = await assemble_authoritative_creative_context(
        object(),
        business_id=business_id,
        business_type="technology",
    )

    request = assemble.await_args.args[2]
    assert request.include_business_brain is True
    assert request.include_memory is False
    assert result.source_count == 1
    assert "marketing" in result.evidence_tokens


def test_brand_offer_capability_word_list_without_relationship_still_fails() -> None:
    result = _saas_brand_offer_assessment(
        marketing_idea=(
            "AI agents, marketing, sales, support, operations and reporting for "
            "modern business."
        ),
        customer_care_reason=(
            "Owners care about marketing, sales, support, operations and reporting."
        ),
        hero_subject=(
            "Marketing, sales, support, operations and reporting arranged around "
            "a business owner."
        ),
        hero_relevance="The business functions appear around the owner.",
        product_story=(
            "Marketing, sales, support, operations and reporting appear together "
            "around the owner."
        ),
        visual_metaphor="A clean arrangement of business functions.",
        scroll_stopping_hook="A premium modern composition.",
    )

    assert result.approved is False
    assert "weak_visual_proof" in result.hard_failures


def test_brand_offer_rejects_generic_ai_network_even_with_grounded_terms() -> None:
    result = _saas_brand_offer_assessment(
        marketing_idea=(
            "Marketing, sales, support, operations and reporting connect and flow "
            "through a glowing digital network."
        ),
        customer_care_reason=(
            "Owners care because marketing, sales, support, operations and "
            "reporting all need attention."
        ),
        hero_subject=(
            "A glowing digital network of floating nodes for marketing, sales, "
            "support, operations and reporting."
        ),
        hero_relevance="The glowing network represents the business functions.",
        product_story=(
            "Marketing, sales, support, operations and reporting connect through "
            "floating nodes and flow across a glowing digital network."
        ),
        visual_metaphor="A futuristic network of glowing nodes.",
        scroll_stopping_hook="A glowing AI network with blue nodes.",
    )

    assert result.approved is False
    assert (
        "weak_visual_proof" in result.hard_failures
        or "no_business_specific_mechanism" in result.hard_failures
        or "decorative_abstraction_as_story" in result.hard_failures
    )
