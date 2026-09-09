"""Executable brand campaign scenes; no selected catalog offering is assumed."""
from app.schemas.marketing import CreativeStrategyProposal
from app.services.creative_direction import CreativeConceptProposal, CreativeDirectorSynthesis, build_creative_direction
from app.services.creative_research import PublicCreativeResearchContext, build_research_request, degraded_research_bundle


def brand_strategy():
    return CreativeStrategyProposal(
        marketing_goal="Build awareness of the pressures of opening a neighborhood shop.",
        target_audience="Neighborhood shopkeepers handling receipts and opening shutters.",
        audience_insight="Shopkeepers juggle receipts while trying to open their shutters each morning.",
        campaign_angle="Receipts compete with opening shutters for a shopkeeper's hands.",
        hook="Opening time", headline="Made for the moment", supporting_message="See the daily balancing act.",
        cta="Explore now", visual_concept="A shopkeeper, receipts and opening shutters at morning light.",
        composition_direction="Hero subject left with clean open space right.",
        subject_focus="A shopkeeper at opening time.", mood="Contemporary editorial photography.",
        lighting="Soft directional morning light.", negative_space="Clear right corridor for typography.",
        brand_treatment="Restrained blue palette and controlled brand identity.",
        recommended_channel="instagram", pr_guardrails=("Only grounded context.",),
        prohibited_claims=("No invented outcomes.",),
    )


def brand_inputs():
    context = PublicCreativeResearchContext(industry="retail", channel="instagram", campaign_objective="brand awareness", creative_format="social square", style_family="premium modern")
    research = degraded_research_bundle(build_research_request(context,max_results=12), provider="internal_patterns")
    return brand_strategy(), context, research


def brand_synthesis(*, strong=True, alternative=False):
    strategy, context, research = brand_inputs()
    base = build_creative_direction(strategy=strategy, context=context, research=research, story_mode="brand_offer")
    candidates = [CreativeConceptProposal.model_validate(c.model_dump(exclude={"scorecard"})) for c in base.candidates]
    if strong:
        values = dict(
            concept_name="Opening time balancing act",
            marketing_idea="An unexpected contrast: a shopkeeper balances receipts on one palm while lifting the shutter with the other; opening time becomes a demonstration of divided attention.",
            customer_care_reason="Shopkeepers care because receipts compete with opening shutters for their hands; the physical balancing act makes that morning pressure visible without claiming a brand solution.",
            hero_subject="A shopkeeper lifting opening shutters with one hand while balancing receipts on the other palm, framed at street level.",
            hero_relevance="The receipts and opening shutters belong to the shopkeeper's morning; the contrast shows the audience's physical attention split in one glance.",
            product_story="Receipts pile on a shopkeeper's outstretched palm while opening shutters pull the other arm upward; the unbalanced arms create a visible consequence of divided attention, not a promised customer outcome.",
            visual_metaphor="The shopkeeper's two arms become an uneven scale between receipts and opening shutters, an unexpected visual tension.",
            scroll_stopping_hook="An unexpected reveal: the tilting receipts above an open palm visibly contrast with the heavy shutter rising on the other side.",
        )
        if alternative:
            values.update(
                concept_name="The paper sunrise",
                marketing_idea="An unexpected visual reveal: receipts cover opening shutters, cutting morning light into narrow strips; the retail doorway becomes a demonstration of the shopkeeper's opening-time pressure.",
                hero_subject="Receipts covering opening shutters at the shop entrance; morning light falls through tiny gaps onto the empty threshold.",
                product_story="Receipts cover opening shutters and block morning light at the threshold; a shopkeeper peels one sheet from the doorway, letting a narrow beam cross the dark floor as a visible consequence.",
                visual_metaphor="A paper eclipse of morning sunlight at the shop entrance makes opening-time pressure visible through an unexpected contrast.",
            )
        candidates[0] = candidates[0].model_copy(update=values)
    return CreativeDirectorSynthesis(candidates=tuple(candidates))


def brand_plan(*, alternative=False):
    strategy, context, research = brand_inputs()
    return build_creative_direction(strategy=strategy, context=context,research=research,story_mode="brand_offer",synthesis=brand_synthesis(alternative=alternative))
