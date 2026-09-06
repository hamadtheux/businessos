from __future__ import annotations

from types import SimpleNamespace

from app.services.creative_visual_review import build_visual_review_task
from app.services.creative_world_class import (
    assess_world_class_creative,
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
