from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image

from app.schemas.marketing import VideoCreativeStrategy, VideoScene
from app.services.creative_compositor import (
    CreativeCompositionInput,
    CreativeCompositor,
)
from app.services.creative_engine import (
    build_creative_master_plan,
    build_image_execution_plan,
    build_video_execution_plan,
    composition_plan_is_valid,
    detect_generic_visual_shorthand,
    route_creative_failure,
    validate_composition_plan,
)
from app.services.creative_provider import CreativeGenerationRequest
from app.services.creative_video import VideoGenerationRequest


def _direction(*, bad: bool = False) -> SimpleNamespace:
    if bad:
        concept = SimpleNamespace(
            concept_name="AI Hub",
            marketing_idea="Business functions converge into one coordinated intelligence.",
            customer_care_reason="Owners want more productivity.",
            hero_subject="A central glowing AI hub with floating department icons.",
            hero_relevance="The hub represents the business.",
            product_story="Neon network lines connect generic futuristic tech panels.",
            scroll_stopping_hook="A blue futuristic AI scene.",
            visual_metaphor="A glowing central orb.",
            layout_intent="Rigid image-left/copy-right template.",
            focal_area="central glowing hub",
            text_zone="quiet right panel",
            offer_treatment="small offer badge",
            cta_treatment="filled CTA",
            depth="layered digital depth",
            image_style="generic dark SaaS visual",
            camera_direction="front-facing wide view",
            lighting="blue neon lighting",
            mood="futuristic",
            visual_density="medium",
            background_complexity="floating nodes and data panels",
            brand_expression="brand palette and logo",
        )
    else:
        concept = SimpleNamespace(
            concept_name="One Operating Rhythm",
            marketing_idea="A small-business owner moves from four competing work streams to one controlled operating rhythm.",
            customer_care_reason="Owners care because customer messages, orders, follow-ups, and reporting compete for the same attention during a busy day.",
            hero_subject="A small-business owner at a real counter with an order slip, customer message, follow-up note, and daily report in hand.",
            hero_relevance="The visible work streams are the owner's real operating pressure, not decorative software symbolism.",
            product_story="The owner gathers the distinct work streams into one deliberate sequence, turning scattered pressure into a clear next action.",
            scroll_stopping_hook="A decisive mid-action moment where scattered work becomes one clean line of motion.",
            visual_metaphor="A physical handoff from a crowded counter to one ordered operating rhythm.",
            layout_intent="Asymmetric magazine composition with the owner crossing the visual field and a quiet upper-left copy corridor.",
            focal_area="owner in the right-middle field",
            text_zone="quiet upper-left copy corridor",
            offer_treatment="restrained editorial annotation",
            cta_treatment="small dark premium CTA",
            depth="foreground work slips, crisp hero plane, soft counter background",
            image_style="observational commercial photography with tactile paper and natural human gesture",
            camera_direction="three-quarter medium frame with a slight lateral perspective",
            lighting="directional morning window light with controlled shadow",
            mood="focused and relieved",
            visual_density="restrained",
            background_complexity="real counter detail kept low in the copy corridor",
            brand_expression="quiet brand color appears in one practical object and the deterministic identity layer",
        )
    return SimpleNamespace(selected_concept=concept)


def _strategy() -> SimpleNamespace:
    return SimpleNamespace(
        headline="Run your business with one clear rhythm.",
    )


def _raw_visual() -> bytes:
    output = BytesIO()
    Image.new("RGB", (800, 600), (128, 128, 128)).save(output, format="PNG")
    return output.getvalue()


def test_good_fixture_shares_one_concept_between_image_and_video() -> None:
    master = build_creative_master_plan(
        strategy=_strategy(),
        direction=_direction(),
        aspect_ratio="1:1",
    )

    image = build_image_execution_plan(master)
    video = build_video_execution_plan(master, duration_seconds=15)

    assert image.composition_family == video.composition_family == master.composition.family
    assert master.campaign_mechanism in image.still_prompt
    assert video.opening_hook == master.visual_hook
    assert video.scene_plan[-1].text_safe_strategy == master.composition.negative_space_strategy
    assert composition_plan_is_valid(master.composition)


def test_same_master_identity_reaches_fake_image_and_video_requests() -> None:
    master = build_creative_master_plan(
        strategy=_strategy(),
        direction=_direction(),
        aspect_ratio="1:1",
    )
    image_plan = build_image_execution_plan(master)
    video_plan = build_video_execution_plan(master, duration_seconds=15)
    video_strategy = VideoCreativeStrategy(
        hook=master.visual_hook,
        script=master.single_minded_message,
        storyboard_summary="A grounded owner story moves from pressure to one clear next action.",
        scenes=[
            VideoScene(
                scene_number=1,
                duration_seconds=15,
                purpose="Show the grounded operating change.",
                visual=master.hero_subject,
                motion=master.hero_action,
            )
        ],
        shot_plan=["Three-quarter medium frame with controlled lateral motion."],
        continuity_direction=master.temporal_transition,
        reference_asset_strategy=master.brand_presence_strategy,
        audio_direction="Quiet, tactile working-room sound.",
        caption_plan="Use the approved deterministic end card.",
        end_card="Use the exact approved CTA.",
        duration_seconds=15,
        aspect_ratio="1:1",
        recommended_channel="instagram",
    )
    image_request = CreativeGenerationRequest(
        business_id=uuid4(),
        creative_asset_id=uuid4(),
        instructions=image_plan.still_prompt,
        width=640,
        height=640,
        aspect_ratio="1:1",
    )
    video_request = VideoGenerationRequest(
        business_id=uuid4(),
        creative_asset_id=uuid4(),
        strategy_json=video_strategy.canonical_json(),
        duration_seconds=15,
        aspect_ratio="1:1",
        idempotency_key=str(uuid4()),
        creative_territory_key=master.territory_key,
        creative_concept_name=master.concept_name,
        campaign_mechanism=master.campaign_mechanism,
        composition_family=master.composition.family,
        execution_plan_json=video_plan.model_dump_json(),
    )

    captured_image_request = image_request
    captured_video_payload = video_request.external_payload()
    image_identity = {
        "territory_key": f"TERRITORY KEY: {master.territory_key}",
        "concept_name": f"CONCEPT: {master.concept_name}",
        "campaign_mechanism": master.campaign_mechanism[:180],
        "composition_family": f"COMPOSITION FAMILY: {master.composition.family}",
    }
    for value in image_identity.values():
        assert value in captured_image_request.instructions

    assert captured_video_payload["creative_identity"] == {
        "territory_key": master.territory_key,
        "concept_name": master.concept_name,
        "campaign_mechanism": master.campaign_mechanism,
        "composition_family": master.composition.family,
    }
    assert captured_video_payload["execution_plan"]["composition_family"] == (
        master.composition.family
    )


def test_bad_blue_ai_hub_fixture_is_a_concept_failure() -> None:
    master = build_creative_master_plan(
        strategy=_strategy(),
        direction=_direction(bad=True),
        aspect_ratio="1:1",
    )
    risk = detect_generic_visual_shorthand(
        " ".join(
            (
                master.hero_subject,
                master.hero_action,
                master.visual_metaphor,
                master.art_direction,
            )
        )
    )

    assert risk.hard_failure
    assert {"glowing_ai_hub", "neon_network"}.issubset(risk.matched_patterns)
    decision = route_creative_failure(
        concept_failure=True,
        codes=risk.matched_patterns,
    )
    assert decision.failure_class == "concept"
    assert decision.action == "reselect_concept"
    assert decision.media_spend_allowed is False


def test_local_layout_repair_never_allows_media_spend() -> None:
    decision = route_creative_failure(
        composition_failure=True,
        branding_typography_failure=True,
        codes=("headline_wrap", "cta_bounds"),
    )
    assert decision.action == "local_recompose"
    assert decision.media_spend_allowed is False


def test_unsupported_claim_fails_closed_before_raw_media_repair() -> None:
    decision = route_creative_failure(
        semantic_grounding_failure=True,
        raw_media_failure=True,
        codes=("unsupported_claim",),
    )
    assert decision.failure_class == "semantic_grounding"
    assert decision.action == "fail_closed"
    assert decision.media_spend_allowed is False


def test_image_prompt_is_layout_aware_and_bans_provider_typography() -> None:
    master = build_creative_master_plan(
        strategy=_strategy(),
        direction=_direction(),
        aspect_ratio="9:16",
    )
    prompt = build_image_execution_plan(master).still_prompt.casefold()

    assert "composition family:" in prompt
    assert "headline safe zone" in prompt
    assert "no text" in prompt
    assert "no logos" in prompt
    assert "floating app icons" in prompt
    assert len(prompt) <= 5000


def _regions_overlap(first, second) -> bool:
    return (
        min(first.x + first.width, second.x + second.width) > max(first.x, second.x)
        and min(first.y + first.height, second.y + second.height) > max(first.y, second.y)
    )


def _subject_overlap_master(aspect_ratio: str):
    direction = _direction()
    direction.selected_concept.layout_intent = "Subject overlap campaign composition"
    return build_creative_master_plan(
        strategy=_strategy(),
        direction=direction,
        aspect_ratio=aspect_ratio,
    )


def test_subject_overlap_protects_all_copy_regions_in_square_and_tall_plans() -> None:
    unsafe = _subject_overlap_master("1:1").composition
    unsafe = unsafe.model_copy(
        update={"focal_subject_region": unsafe.headline_safe_zone}
    )
    assert not composition_plan_is_valid(unsafe)
    assert "headline_focal_subject_overlap" in validate_composition_plan(unsafe)

    for aspect_ratio in ("1:1", "9:16"):
        master = _subject_overlap_master(aspect_ratio)
        plan = master.composition
        assert plan.family == "subject_overlap"
        assert plan.subject_overlap_allowed
        assert composition_plan_is_valid(plan)
        for name, region in (
            ("brand", plan.brand_zone),
            ("headline", plan.headline_safe_zone),
            ("supporting_copy", plan.supporting_copy_zone),
            ("cta", plan.cta_zone),
        ):
            assert not _regions_overlap(plan.focal_subject_region, region), name

        prompt = build_image_execution_plan(master).still_prompt
        assert "FOCAL SUBJECT:" in prompt
        assert "HEADLINE SAFE ZONE:" in prompt
        assert f"x={plan.focal_subject_region.x:.2f}" in prompt
        assert f"x={plan.headline_safe_zone.x:.2f}" in prompt

        width, height = (640, 640) if aspect_ratio == "1:1" else (360, 640)
        result = CreativeCompositor().compose(
            CreativeCompositionInput(
                raw_visual=_raw_visual(),
                target_width=width,
                target_height=height,
                asset_type="story_reel" if aspect_ratio == "9:16" else "social_square",
                headline="Made for the moment",
                supporting_copy="Grounded copy with a clear customer reason to care.",
                cta="Explore",
                business_name="Acme Studio",
                composition_plan=plan,
            )
        )
        assert result.selected_layout == "subject_overlap"
        assert result.quality.image_bounds == (0, 0, width, height)

        def pixel_box(region):
            return (
                round(region.x * width),
                round(region.y * height),
                round((region.x + region.width) * width),
                round((region.y + region.height) * height),
            )

        for name, region in (
            ("business_name", plan.brand_zone),
            ("headline", plan.headline_safe_zone),
            ("supporting_copy", plan.supporting_copy_zone),
            ("cta", plan.cta_zone),
        ):
            actual = result.quality.text_bounds[name]
            expected = pixel_box(region)
            assert actual[0] >= expected[0], name
            assert actual[1] >= expected[1], name
            assert actual[2] <= expected[2], name
            assert actual[3] <= expected[3], name


def test_v2_composition_families_render_with_bounded_real_typography() -> None:
    families = (
        "full_bleed_hero",
        "editorial_split",
        "subject_overlap",
        "centered_campaign_poster",
        "asymmetric_magazine",
        "premium_minimal",
        "brand_offer_spotlight",
        "typographic_led",
    )
    signatures = {}
    results = {}
    for family in families:
        result = CreativeCompositor().compose(
            CreativeCompositionInput(
                raw_visual=_raw_visual(),
                target_width=640,
                target_height=640,
                asset_type="social_square",
                headline="Made for the moment",
                supporting_copy="Grounded copy with a clear customer reason to care.",
                cta="Explore",
                business_name="Acme Studio",
                primary_color="#123456",
                secondary_color="#F4F0E8",
                accent_color="#D27D2D",
                composition_family=family,
            )
        )
        results[family] = result
        signatures[family] = (
            result.quality.image_bounds,
            result.quality.text_bounds["headline"],
            result.quality.text_bounds["supporting_copy"],
            result.quality.copy_safe_area_ratio,
        )
        assert result.selected_layout == family
        assert result.quality.valid_png
        assert (result.width, result.height) == (640, 640)
        for left, top, right, bottom in result.quality.text_bounds.values():
            margin = result.quality.safe_margin
            assert left >= margin
            assert top >= margin
            assert right <= result.width - margin
            assert bottom <= result.height - margin

    assert len(set(signatures.values())) == 8
    assert signatures["subject_overlap"] != signatures["full_bleed_hero"]
    assert signatures["subject_overlap"] != signatures["editorial_split"]
    assert results["full_bleed_hero"].quality.image_bounds == (0, 0, 640, 640)
    assert (
        results["editorial_split"].quality.image_bounds[0]
        > results["full_bleed_hero"].quality.image_bounds[0]
    )
    assert (
        results["premium_minimal"].quality.image_bounds[2]
        - results["premium_minimal"].quality.image_bounds[0]
        < results["asymmetric_magazine"].quality.image_bounds[2]
        - results["asymmetric_magazine"].quality.image_bounds[0]
    )
    typographic_headline = results["typographic_led"].quality.text_bounds["headline"]
    typographic_width = typographic_headline[2] - typographic_headline[0]
    assert typographic_width > (
        results["premium_minimal"].quality.text_bounds["headline"][2]
        - results["premium_minimal"].quality.text_bounds["headline"][0]
    )
