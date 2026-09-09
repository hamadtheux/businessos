from __future__ import annotations

from types import SimpleNamespace

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
