from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.schemas.marketing import CreativeStrategyProposal  # noqa: E402
from app.agents.provider import AIAgentProviderMetadata  # noqa: E402
from app.exceptions.ai_agent import AIAgentProviderError  # noqa: E402
from app.exceptions.marketing import MarketingAIError  # noqa: E402
from app.services.creative_direction import (  # noqa: E402
    _contains_any,
    _genericness_risk,
    CreativeConceptProposal,
    CreativeDirectorSynthesis,
    build_creative_director_task,
    build_creative_direction,
    build_visual_art_direction,
    creative_direction_meets_quality_floor,
)
from app.services.creative_research import (  # noqa: E402
    PublicCreativeResearchContext,
    build_research_request,
    degraded_research_bundle,
)
from app.services.marketing import _creative_direction_with_fallback  # noqa: E402


def _strategy() -> CreativeStrategyProposal:
    return CreativeStrategyProposal(
        marketing_goal="Promote a verified seasonal discount.",
        target_audience="Qualified business owners.",
        audience_insight="Lead with operational value.",
        campaign_angle="A premium product-first offer.",
        hook="A better way to run the business.",
        headline="Run your business with an AI team",
        supporting_message="Automate marketing, sales, and customer operations.",
        offer="50% OFF",
        cta="Claim the offer",
        visual_concept="A premium AI operations environment with one central system.",
        composition_direction="Hero on the right with copy on the left.",
        subject_focus="The supported AI Business OS represented as an operational hub.",
        mood="Confident, modern, and premium.",
        lighting="Cinematic controlled light.",
        negative_space="Quiet left-side copy corridor.",
        brand_treatment="Use restrained blue palette cues.",
        recommended_channel="instagram",
        pr_guardrails=("Use only supported capabilities.",),
        prohibited_claims=("No invented outcomes.",),
    )


def _context(channel: str = "instagram") -> PublicCreativeResearchContext:
    return PublicCreativeResearchContext(
        industry="technology",
        channel=channel,
        campaign_objective="promotional offer",
        creative_format="social square",
        style_family="premium modern",
    )


class CreativeDirectionTests(TestCase):
    def _research(self):
        context = _context()
        return degraded_research_bundle(
            build_research_request(context, max_results=12),
            provider="internal_patterns",
        )

    def test_generic_markers_use_token_and_phrase_boundaries(self) -> None:
        self.assertFalse(_contains_any("supported offering", ("ring",)))
        self.assertLess(_genericness_risk("supported business offering"), 24)
        for phrase in (
            "blue rings",
            "concentric rings",
            "abstract gradient",
            "decorative circles",
        ):
            with self.subTest(phrase=phrase):
                self.assertGreater(_genericness_risk(phrase), 24)

    def test_strategy_rejects_duplicate_headline_and_offer(self) -> None:
        values = _strategy().model_dump()
        values.update({"headline": "50% OFF", "offer": "50% off"})
        with self.assertRaisesRegex(ValidationError, "offer must be separate"):
            CreativeStrategyProposal.model_validate(values)

    def test_weak_intent_yields_three_scored_original_concepts_and_one_winner(self) -> None:
        context = _context()
        research = self._research()
        plan = build_creative_direction(
            strategy=_strategy(),
            research=research,
            context=context,
        )

        self.assertEqual(len(plan.candidates), 3)
        scores = [value.scorecard.overall_score for value in plan.candidates]
        self.assertEqual(plan.selected_concept.scorecard.overall_score, max(scores))
        for candidate in plan.candidates:
            self.assertNotEqual(candidate.hero_subject, "50% OFF")
            self.assertTrue(
                any(
                    marker in candidate.offer_treatment.casefold()
                    for marker in ("offer", "badge", "chip", "annotation")
                )
            )
            self.assertNotIn("copy this", candidate.originality_notes.casefold())
        self.assertGreater(len(set(scores)), 1)

    def test_typed_director_requires_exactly_three_materially_different_proposals(self) -> None:
        fallback = build_creative_direction(
            strategy=_strategy(),
            research=self._research(),
            context=_context(),
        )
        proposals = tuple(
            CreativeConceptProposal.model_validate(
                candidate.model_dump(exclude={"scorecard"})
            )
            for candidate in fallback.candidates
        )

        synthesis = CreativeDirectorSynthesis(candidates=proposals)
        directed = build_creative_direction(
            strategy=_strategy(),
            research=self._research(),
            context=_context(),
            synthesis=synthesis,
        )

        self.assertTrue(directed.used_ai_synthesis)
        self.assertEqual(len(directed.candidates), 3)
        self.assertGreater(
            len({candidate.scorecard.overall_score for candidate in directed.candidates}),
            1,
        )
        with self.assertRaises(ValidationError):
            CreativeDirectorSynthesis(candidates=proposals[:2])
        with self.assertRaises(ValidationError):
            CreativeDirectorSynthesis(
                candidates=(proposals[0], proposals[0], proposals[0])
            )
        unsafe = proposals[0].model_dump()
        unsafe["layout_intent"] = "Copy this exact source layout"
        with self.assertRaisesRegex(ValidationError, "original abstract direction"):
            CreativeConceptProposal.model_validate(unsafe)

    def test_director_task_contains_only_abstract_research_not_urls_or_source_copy(self) -> None:
        task = build_creative_director_task(
            strategy=_strategy(),
            research=self._research(),
            context=_context(),
        )
        self.assertNotIn("http", task.casefold())
        self.assertNotIn("source_url", task)
        self.assertNotIn("copy this", task.casefold())
        self.assertNotIn("make this exact", task.casefold())
        self.assertIn("exactly three", task.casefold())
        self.assertIn("abstract", task.casefold())

    def test_director_task_budget_preserves_authoritative_values_and_final_contract(self) -> None:
        strategy = _strategy()

        # Deliberately overwhelm every expendable research section. The builder
        # must shrink this material rather than truncating trusted campaign truth
        # or the final server-owned quality/safety contract.
        research = SimpleNamespace(
            dominant_patterns=(
                "RESEARCH-FIRST " + ("R" * 3000) + " RESEARCH-END-SENTINEL",
            ),
            emerging_patterns=(
                "EMERGING-FIRST " + ("E" * 3000) + " EMERGING-END-SENTINEL",
            ),
            recommended_visual_directions=(
                "DIRECTION-FIRST " + ("D" * 3000) + " DIRECTION-END-SENTINEL",
            ),
            avoid_patterns=(
                "AVOID-FIRST " + ("A" * 3000) + " AVOID-END-SENTINEL",
            ),
            originality_constraints=(
                "ORIGINALITY-FIRST "
                + ("O" * 3000)
                + " ORIGINALITY-END-SENTINEL",
            ),
        )

        task = build_creative_director_task(
            strategy=strategy,
            research=research,  # type: ignore[arg-type]
            context=_context(),
        )

        self.assertLessEqual(len(task), 4000)

        # Authoritative campaign truth must survive exactly.
        authoritative_values = (
            strategy.marketing_goal,
            strategy.target_audience,
            strategy.audience_insight,
            strategy.campaign_angle,
            strategy.headline,
            strategy.offer,
            strategy.cta,
            strategy.visual_concept,
            strategy.subject_focus,
            strategy.brand_treatment,
        )

        for value in authoritative_values:
            if value:
                with self.subTest(authoritative_value=value):
                    self.assertIn(value, task)

        # The final contract must remain intact even under extreme dynamic input.
        required_contract = (
            "OUTPUT RULES:",
            "Return exactly three candidates",
            "marketing_idea must state the advertising mechanism",
            "product_story must show cause -> mechanism -> outcome",
            "Reject generic stock/lifestyle scenes",
            "Reject swap-logo concepts",
            "Never copy, clone, replicate",
            "Never include URLs",
            "raw image will contain no typography",
        )

        for marker in required_contract:
            with self.subTest(contract_marker=marker):
                self.assertIn(marker, task)

        # Research is allowed to exist, but it is the expendable layer.
        self.assertIn("ABSTRACT RESEARCH SIGNALS ONLY:", task)
        self.assertIn("RESEARCH-FIRST", task)

        # The deliberately enormous research tail must be removed rather than
        # pushing mandatory output/safety rules beyond the global budget.
        self.assertNotIn("RESEARCH-END-SENTINEL", task)
        self.assertNotIn("R" * 1000, task)

        # The mandatory final contract must occur after the bounded research
        # block, proving it was not chopped away by an end-of-string slice.
        self.assertGreater(
            task.index("OUTPUT RULES:"),
            task.index("ABSTRACT RESEARCH SIGNALS ONLY:"),
        )

    def test_channel_changes_platform_specific_direction(self) -> None:
        strategy = _strategy()
        instagram_context = _context("instagram")
        linkedin_context = _context("linkedin")
        instagram = build_creative_direction(
            strategy=strategy,
            research=degraded_research_bundle(
                build_research_request(instagram_context, max_results=12),
                provider="internal",
            ),
            context=instagram_context,
        )
        linkedin = build_creative_direction(
            strategy=strategy,
            research=degraded_research_bundle(
                build_research_request(linkedin_context, max_results=12),
                provider="internal",
            ),
            context=linkedin_context,
        )

        self.assertNotEqual(
            instagram.selected_concept.layout_intent,
            linkedin.selected_concept.layout_intent,
        )
        self.assertIn("editorial professional", linkedin.selected_concept.layout_intent)

    def test_raw_art_direction_excludes_exact_deterministic_copy(self) -> None:
        strategy = _strategy()
        context = _context()
        plan = build_creative_direction(
            strategy=strategy,
            research=degraded_research_bundle(
                build_research_request(context, max_results=12),
                provider="internal",
            ),
            context=context,
        )
        prompt = build_visual_art_direction(
            strategy=strategy,
            direction=plan,
            context=context,
            aspect_ratio="1:1",
            primary_color="#123456",
            secondary_color="#F4F0E8",
            accent_color="#D27D2D",
        )

        self.assertNotIn(strategy.headline, prompt)
        self.assertNotIn(strategy.supporting_message, prompt)
        self.assertNotIn(strategy.cta or "missing", prompt)
        self.assertNotIn(strategy.offer or "missing", prompt)
        self.assertNotIn("http", prompt)
        self.assertIn("Selected original concept", prompt)
        self.assertIn("Reserved overlay zone", prompt)
        self.assertIn("DO NOT GENERATE words, letters, numbers", prompt)
        self.assertIn("do not imitate or reproduce", prompt)

    def test_ai_business_os_offer_concepts_keep_product_story_without_image_text(self) -> None:
        strategy = _strategy()
        context = _context()
        plan = build_creative_direction(
            strategy=strategy,
            research=degraded_research_bundle(
                build_research_request(context, max_results=12),
                provider="internal",
            ),
            context=context,
        )
        prompt = build_visual_art_direction(
            strategy=strategy,
            direction=plan,
            context=context,
            aspect_ratio="1:1",
            primary_color="#114CAC",
            secondary_color=None,
            accent_color=None,
        )

        self.assertEqual(strategy.offer, "50% OFF")
        self.assertTrue(
            all(candidate.scorecard.product_relevance >= 60 for candidate in plan.candidates)
        )
        self.assertIn("AI Business OS", plan.selected_concept.hero_subject)
        self.assertIn("supported offering", plan.selected_concept.product_story.casefold())
        self.assertIn("Product or service story:", prompt)
        self.assertIn("Immediate visual hook:", prompt)
        for exact_copy in (
            strategy.headline,
            strategy.supporting_message,
            strategy.offer,
            strategy.cta,
        ):
            self.assertNotIn(exact_copy or "[missing]", prompt)

    def test_generic_blue_circle_concepts_fail_server_owned_direction_floor(self) -> None:
        context = _context()
        research = self._research()
        fallback = build_creative_direction(
            strategy=_strategy(),
            research=research,
            context=context,
        )
        generic_values = (
            {
                "concept_name": "Blue Circle Future",
                "marketing_idea": "Decorative blue circles signal modern technology.",
                "customer_care_reason": "The audience sees a familiar software aesthetic.",
                "hero_subject": "Concentric blue circles on a dark gradient.",
                "hero_relevance": "Abstract circles imply technology.",
                "product_story": "Decorative rings create a generic innovation mood.",
                "scroll_stopping_hook": "Bright concentric rings.",
                "visual_metaphor": "Concentric blue rings.",
            },
            {
                "concept_name": "Gradient Network",
                "marketing_idea": "A blue gradient and geometric shapes signal connected technology.",
                "customer_care_reason": "The audience recognizes a generic digital theme.",
                "hero_subject": "Abstract blue network shapes.",
                "hero_relevance": "Geometry implies software.",
                "product_story": "Floating abstract nodes create a technology mood.",
                "scroll_stopping_hook": "Glowing geometric nodes.",
                "visual_metaphor": "Abstract connected shapes.",
            },
            {
                "concept_name": "Digital Wave",
                "marketing_idea": "A glowing blue wave creates a futuristic software mood.",
                "customer_care_reason": "The audience recognizes premium technology styling.",
                "hero_subject": "Decorative blue wave and floating orbs.",
                "hero_relevance": "The wave implies digital motion.",
                "product_story": "A gradient wave and orbs suggest generic innovation.",
                "scroll_stopping_hook": "Glowing blue wave.",
                "visual_metaphor": "Futuristic blue wave.",
            },
        )
        proposals = tuple(
            CreativeConceptProposal.model_validate(
                {
                    **candidate.model_dump(exclude={"scorecard"}),
                    **generic,
                }
            )
            for candidate, generic in zip(
                fallback.candidates,
                generic_values,
                strict=True,
            )
        )

        direction = build_creative_direction(
            strategy=_strategy(),
            research=research,
            context=context,
            synthesis=CreativeDirectorSynthesis(candidates=proposals),
        )

        self.assertFalse(creative_direction_meets_quality_floor(direction))
        self.assertTrue(
            all(candidate.scorecard.overall_score <= 58 for candidate in direction.candidates)
        )
        self.assertGreaterEqual(
            direction.selected_concept.scorecard.genericness_risk,
            65,
        )


class CreativeDirectorRuntimeTests(IsolatedAsyncioTestCase):
    def _inputs(self):
        context = _context()
        research = degraded_research_bundle(
            build_research_request(context, max_results=12),
            provider="internal_patterns",
        )
        fallback = build_creative_direction(
            strategy=_strategy(),
            research=research,
            context=context,
        )
        synthesis = CreativeDirectorSynthesis(
            candidates=tuple(
                CreativeConceptProposal.model_validate(
                    candidate.model_dump(exclude={"scorecard"})
                )
                for candidate in fallback.candidates
            )
        )
        return context, research, synthesis

    def _weak_direction(self, field: str):
        context, research, _synthesis = self._inputs()
        direction = build_creative_direction(
            strategy=_strategy(),
            research=research,
            context=context,
        )
        weak_score = direction.selected_concept.scorecard.model_copy(
            update={field: 54}
        )
        weak_selected = direction.selected_concept.model_copy(
            update={"scorecard": weak_score}
        )
        return context, research, direction.model_copy(
            update={"selected_concept": weak_selected}
        )

    async def test_weak_deterministic_fallback_cannot_bypass_any_core_floor(self) -> None:
        for field in (
            "business_specific_relevance",
            "product_relevance",
            "marketing_idea_strength",
            "visual_storytelling",
        ):
            with self.subTest(field=field):
                context, research, weak = self._weak_direction(field)
                with (
                    patch(
                        "app.services.marketing.build_creative_direction",
                        return_value=weak,
                    ),
                    self.assertRaises(MarketingAIError),
                ):
                    await _creative_direction_with_fallback(
                        object(),
                        business_id=uuid4(),
                        strategy=_strategy(),
                        research=research,
                        context=context,
                        provider=None,
                        max_output_tokens=4_000,
                    )

    async def test_provider_failure_fallback_is_quality_gated(self) -> None:
        context, research, weak = self._weak_direction("product_relevance")
        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(side_effect=AIAgentProviderError("safe failure")),
            ),
            patch(
                "app.services.marketing.build_creative_direction",
                return_value=weak,
            ),
            self.assertRaises(MarketingAIError),
        ):
            await _creative_direction_with_fallback(
                object(),
                business_id=uuid4(),
                strategy=_strategy(),
                research=research,
                context=context,
                provider=SimpleNamespace(provider_name="test_director"),
                max_output_tokens=4_000,
            )

    async def test_low_quality_ai_fallback_is_also_quality_gated(self) -> None:
        context, research, weak = self._weak_direction("visual_storytelling")
        execution = SimpleNamespace(
            output=SimpleNamespace(),
            provider_metadata=AIAgentProviderMetadata(),
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
            patch(
                "app.services.marketing.build_creative_direction",
                side_effect=[weak, weak],
            ),
            self.assertRaises(MarketingAIError),
        ):
            await _creative_direction_with_fallback(
                object(),
                business_id=uuid4(),
                strategy=_strategy(),
                research=research,
                context=context,
                provider=SimpleNamespace(provider_name="test_director"),
                max_output_tokens=4_000,
            )

    async def test_one_typed_director_call_drives_server_scored_selection(self) -> None:
        context, research, synthesis = self._inputs()
        provider = SimpleNamespace(provider_name="test_director")
        execution = SimpleNamespace(
            output=synthesis,
            provider_metadata=AIAgentProviderMetadata(
                provider_request_id="req_director",
                input_tokens=300,
                output_tokens=500,
            ),
        )
        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(return_value=execution),
            ) as execute,
        ):
            direction, metadata = await _creative_direction_with_fallback(
                object(),
                business_id=uuid4(),
                strategy=_strategy(),
                research=research,
                context=context,
                provider=provider,
                max_output_tokens=4_000,
            )

        execute.assert_awaited_once()
        self.assertIs(execute.await_args.args[4], CreativeDirectorSynthesis)
        self.assertEqual(execute.await_args.kwargs["max_output_tokens"], 4_000)
        self.assertTrue(direction.used_ai_synthesis)
        self.assertEqual(metadata.provider_request_id, "req_director")

    async def test_director_provider_failure_uses_pattern_fallback_without_leak(self) -> None:
        context, research, _synthesis = self._inputs()
        provider = SimpleNamespace(provider_name="test_director")
        with (
            patch(
                "app.services.marketing._build_cmo_execution_request",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.marketing.execute_ai_agent_typed_with_metadata",
                new=AsyncMock(
                    side_effect=AIAgentProviderError(
                        "sk-proj-secret provider payload"
                    )
                ),
            ) as execute,
            self.assertLogs("aibos.marketing", level="WARNING") as captured,
        ):
            direction, metadata = await _creative_direction_with_fallback(
                object(),
                business_id=uuid4(),
                strategy=_strategy(),
                research=research,
                context=context,
                provider=provider,
                max_output_tokens=4_000,
            )

        execute.assert_awaited_once()
        self.assertFalse(direction.used_ai_synthesis)
        self.assertIsNone(metadata.provider_request_id)
        self.assertNotIn("sk-proj-secret", " ".join(captured.output))
