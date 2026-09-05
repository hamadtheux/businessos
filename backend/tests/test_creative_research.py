from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.services.creative_research import (  # noqa: E402
    CreativeInspirationInsight,
    CreativeResearchCache,
    CreativeResearchEngine,
    CreativeResearchResult,
    PublicCreativeResearchContext,
    UnavailableCreativeResearchProvider,
    build_research_request,
    derive_public_research_context,
    normalize_references,
    sanitize_reference_url,
)
from app.services.creative_research_openai import (  # noqa: E402
    OpenAICreativeResearchProvider,
)


def _insight(
    url: str,
    *,
    title: str = "Premium launch campaign",
    score: int = 90,
) -> CreativeInspirationInsight:
    return CreativeInspirationInsight(
        source_domain="provider-supplied.invalid",
        source_url=url,
        title=title,
        relevance_score=score,
        layout_pattern="asymmetrical editorial composition",
        hierarchy_pattern="hero first, concise headline second",
        visual_style="premium commercial editorial",
        focal_point_strategy="one offset product focal point",
        negative_space_strategy="quiet left-side copy corridor",
        typography_character="confident compact sans serif",
        color_relationship="restrained neutrals with one accent",
        cta_treatment="compact high-contrast CTA",
        offer_treatment="small supporting offer badge",
        imagery_style="cinematic product photography",
        composition_density="medium and controlled",
        platform_fit="strong square social read",
        reusable_design_principles=(
            "single offset hero",
            "restrained palette",
            "protected copy zone",
        ),
    )


def _context() -> PublicCreativeResearchContext:
    return PublicCreativeResearchContext(
        industry="technology",
        channel="instagram",
        campaign_objective="promotional offer",
        creative_format="social square",
        style_family="premium modern",
    )


class CreativeResearchPrivacyTests(TestCase):
    def test_dangerous_private_values_collapse_to_allowlisted_public_dimensions(self) -> None:
        private = (
            "Customer John Smith at john@example.com wants secret enterprise price "
            "73829. Internal launch codename BLACKORBIT. "
            "sk-proj-never-log-this wants an urgent 50% sale"
        )
        context = derive_public_research_context(
            business_type="AI software for BLACKORBIT account 73829",
            channel="instagram",
            asset_type="social_square",
            strategy_text=private,
            visual_text="premium private launch for John",
        )
        request = build_research_request(context, max_results=12)

        outward = " ".join((*request.queries, request.cache_key)).casefold()
        for secret in (
            "john",
            "smith",
            "john@example.com",
            "73829",
            "blackorbit",
            "sk-proj-never-log-this",
        ):
            self.assertNotIn(secret, outward)
        self.assertEqual(
            set(context.model_dump()),
            {
                "industry",
                "channel",
                "campaign_objective",
                "creative_format",
                "style_family",
            },
        )

    def test_private_business_and_crm_text_never_enters_queries_or_cache_key(self) -> None:
        private = (
            "Secret customer Fatima owes $71,492 and internal launch codename ORCHID. "
            "Create a 50% discount campaign."
        )
        context = derive_public_research_context(
            business_type="custom AI software for Client-8821",
            channel="instagram",
            asset_type="social_square",
            strategy_text=private,
            visual_text="Premium product-led launch for private CRM lead Fatima",
        )
        request = build_research_request(context, max_results=12)
        serialized = " ".join(request.queries).casefold()

        self.assertEqual(context.industry, "technology")
        self.assertEqual(context.campaign_objective, "promotional offer")
        for secret in ("fatima", "71,492", "orchid", "client-8821", "crm"):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, request.cache_key)

        other = derive_public_research_context(
            business_type="AI SaaS",
            channel="instagram",
            asset_type="social_square",
            strategy_text="A different private customer wants a sale",
            visual_text="Premium product scene",
        )
        self.assertEqual(
            request.cache_key,
            build_research_request(other, max_results=12).cache_key,
        )

    def test_reference_urls_are_https_bounded_and_tracking_free(self) -> None:
        normalized = sanitize_reference_url(
            "https://Dribbble.com/shots/123?utm_source=private&view=full#comments"
        )
        self.assertEqual(normalized, "https://dribbble.com/shots/123?view=full")
        for unsafe in (
            "http://example.com/work",
            "https://user:pass@example.com/work",
            "https://localhost/work",
            "https://127.0.0.1/work",
            "https://169.254.169.254/latest/meta-data",
            "https://10.2.3.4/work",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                sanitize_reference_url(unsafe)

    def test_references_are_deduplicated_and_domain_diverse(self) -> None:
        references = [
            _insight("https://dribbble.com/shots/1", title="Campaign one", score=99),
            _insight("https://dribbble.com/shots/1?utm_source=x", title="Campaign one", score=98),
            _insight("https://dribbble.com/shots/2", title="Campaign two", score=97),
            _insight("https://dribbble.com/shots/3", title="Campaign three", score=96),
            _insight("https://behance.net/gallery/1", title="Campaign four", score=95),
            _insight("https://awwwards.com/sites/one", title="Campaign five", score=94),
            _insight("https://example.org/campaign/six", title="Campaign six", score=93),
        ]
        selected = normalize_references(references, max_results=5)

        self.assertEqual(len(selected), 5)
        self.assertEqual(len({value.source_url for value in selected}), 5)
        self.assertGreaterEqual(len({value.source_domain for value in selected}), 4)
        self.assertLessEqual(
            sum(value.source_domain == "dribbble.com" for value in selected),
            2,
        )
        self.assertFalse(hasattr(selected[0], "source_image"))


class CreativeResearchEngineTests(IsolatedAsyncioTestCase):
    async def test_degraded_logs_do_not_reconstruct_private_classifier_input(self) -> None:
        context = derive_public_research_context(
            business_type="AI software for BLACKORBIT account 73829",
            channel="instagram",
            asset_type="social_square",
            strategy_text=(
                "John john@example.com 73829 BLACKORBIT "
                "sk-proj-never-log-this requests a sale"
            ),
            visual_text="premium private launch for John",
        )
        engine = CreativeResearchEngine(
            provider=UnavailableCreativeResearchProvider(),
            cache=CreativeResearchCache(ttl_seconds=60),
            timeout_seconds=1,
            max_results=12,
        )

        with self.assertLogs("aibos.creative_research", level="INFO") as captured:
            await engine.research(context)

        logs = " ".join(captured.output).casefold()
        for secret in (
            "john",
            "john@example.com",
            "73829",
            "blackorbit",
            "sk-proj-never-log-this",
        ):
            self.assertNotIn(secret, logs)

    async def test_unavailable_and_timeout_degrade_without_blocking_creation(self) -> None:
        unavailable = CreativeResearchEngine(
            provider=UnavailableCreativeResearchProvider(),
            cache=CreativeResearchCache(ttl_seconds=60),
            timeout_seconds=1,
            max_results=12,
        )
        bundle = await unavailable.research(_context())
        self.assertTrue(bundle.degraded)
        self.assertEqual(bundle.reference_count, 0)

        async def slow_search(_request):
            await asyncio.sleep(0.05)

        slow_provider = SimpleNamespace(
            provider_name="slow",
            search=AsyncMock(side_effect=slow_search),
        )
        slow = CreativeResearchEngine(
            provider=slow_provider,
            cache=CreativeResearchCache(ttl_seconds=60),
            timeout_seconds=1,
            max_results=12,
        )
        # The constructor's production bound is intentional; patching the
        # private timeout isolates wait_for behavior without weakening it.
        slow._timeout_seconds = 0.01
        timed_out = await slow.research(_context())
        self.assertTrue(timed_out.degraded)

    async def test_generic_successful_research_is_cached_without_tenant_identity(self) -> None:
        result = CreativeResearchResult(
            provider="test_search",
            references=(
                _insight("https://dribbble.com/shots/1"),
                _insight("https://behance.net/gallery/2", title="Second campaign"),
                _insight("https://awwwards.com/sites/3", title="Third campaign"),
                _insight("https://example.org/work/4", title="Fourth campaign"),
            ),
        )
        provider = SimpleNamespace(
            provider_name="test_search",
            search=AsyncMock(return_value=result),
        )
        engine = CreativeResearchEngine(
            provider=provider,
            cache=CreativeResearchCache(ttl_seconds=60),
            timeout_seconds=1,
            max_results=12,
        )

        first = await engine.research(_context())
        second = await engine.research(_context())

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        provider.search.assert_awaited_once()
        request = provider.search.await_args.args[0]
        self.assertFalse(hasattr(request, "business_id"))

    async def test_openai_adapter_uses_web_search_without_response_storage(self) -> None:
        reference = _insight("https://dribbble.com/shots/verified")
        unverified = _insight(
            "https://example.org/model-invented-source",
            title="Unverified model output",
        )
        parsed = {
            "references": [reference.model_dump(), unverified.model_dump()],
        }
        response = SimpleNamespace(
            status="completed",
            output=[
                SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        sources=[SimpleNamespace(url=reference.source_url)]
                    ),
                ),
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(
                            type="output_text",
                            parsed=parsed,
                            annotations=[],
                        )
                    ],
                ),
            ],
        )
        parse = AsyncMock(return_value=response)
        client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
        provider = OpenAICreativeResearchProvider(client=client, model="gpt-test")

        result = await provider.search(
            build_research_request(_context(), max_results=12)
        )

        self.assertEqual(len(result.references), 1)
        self.assertEqual(result.references[0].source_url, reference.source_url)
        kwargs = parse.await_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertEqual(kwargs["max_tool_calls"], 3)
        self.assertEqual(kwargs["tools"][0]["type"], "web_search")
        task = kwargs["input"][1]["content"].casefold()
        self.assertNotIn("business_id", task)
        self.assertNotIn("customer", task)
