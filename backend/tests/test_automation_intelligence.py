from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.automation_intelligence import (  # noqa: E402
    AutomationIntelligenceNotFoundError,
    AutomationIntelligencePersistenceError,
    AutomationIntelligenceProviderError,
)
from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.integrations.automation_contracts import (  # noqa: E402
    CompetitorCandidateResult,
    CompetitorEvidenceResult,
)
from app.integrations.automation_registry import (  # noqa: E402
    advertising_provider,
    default_competitor_research_provider,
    social_publishing_provider,
    website_deployment_provider,
)
from app.models.automation_intelligence import (  # noqa: E402
    CompetitorCandidate,
    CompetitorCandidateEvidence,
)
from app.models.marketing import Competitor  # noqa: E402
from app.services.automation_intelligence import (  # noqa: E402
    _get_candidate,
    _persist_candidate_result,
    _provider_reference,
    content_window,
    discovery_window_key,
    due_intelligence_businesses_statement,
    enqueue_due_intelligence_automation,
    run_competitor_discovery,
    schedule_marketing_automation,
)
from app.services.marketing_actions import _connector_state, prepare_content_publish_action  # noqa: E402
from app.services.marketing_automation import (  # noqa: E402
    _create_opportunity_if_missing,
    _industry_guardrail,
)


NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class AutomationIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    def test_refresh_windows_are_stable_and_bounded(self) -> None:
        self.assertEqual(discovery_window_key(NOW), discovery_window_key(NOW.replace(hour=23)))
        start, end, key = content_window(NOW)
        self.assertEqual((end - start).days, 6)
        self.assertIn(key, {discovery_window_key(NOW), "2026-w35"})

    def test_scheduler_batch_skips_tenants_with_all_current_windows(self) -> None:
        statement = due_intelligence_businesses_statement(now=NOW, limit=100)
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertGreaterEqual(compiled.count("NOT (EXISTS"), 4)
        self.assertIn("competitor-discovery:scheduled:2026-w35", compiled)
        self.assertIn("marketing-automation:content_plan:2026-w35", compiled)
        self.assertIn(
            "marketing-automation:campaign_opportunities:2026-08-24", compiled
        )
        self.assertIn(
            "marketing-automation:business_growth:2026-08-24", compiled
        )
        self.assertIn("lower(btrim(businesses.business_type))", compiled)
        self.assertIn("'e-commerce'", compiled)

    async def test_scheduler_adds_growth_once_only_for_commerce_businesses(self) -> None:
        commerce = SimpleNamespace(id=uuid4(), business_type="ecommerce")
        clinic = SimpleNamespace(id=uuid4(), business_type="clinic")
        schedule_discovery = AsyncMock(
            return_value=(SimpleNamespace(), True)
        )
        schedule_marketing = AsyncMock(
            return_value=(SimpleNamespace(), True)
        )
        with patch(
            "app.services.automation_intelligence.schedule_competitor_discovery",
            new=schedule_discovery,
        ), patch(
            "app.services.automation_intelligence.schedule_marketing_automation",
            new=schedule_marketing,
        ):
            created = await enqueue_due_intelligence_automation(
                _SchedulerSession([commerce, clinic]),  # type: ignore[arg-type]
                now=NOW,
            )
        self.assertEqual(created, 7)
        growth_calls = [
            call for call in schedule_marketing.await_args_list
            if call.kwargs["run_type"] == "business_growth"
        ]
        self.assertEqual(len(growth_calls), 1)
        self.assertEqual(growth_calls[0].kwargs["business_id"], commerce.id)
        self.assertFalse(any(
            call.kwargs["business_id"] == clinic.id
            and call.kwargs["run_type"] == "business_growth"
            for call in schedule_marketing.await_args_list
        ))

    async def test_business_growth_schedule_is_idempotent_per_window(self) -> None:
        business_id = uuid4()
        run_id = uuid4()
        run = SimpleNamespace(
            id=run_id,
            business_id=business_id,
            run_type="business_growth",
            idempotency_key="marketing-automation:business_growth:2026-08-24",
        )
        enqueue = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        with patch(
            "app.services.automation_intelligence.enqueue_job", new=enqueue
        ):
            first, first_created = await schedule_marketing_automation(
                _Session([run_id, run]),
                business_id=business_id,
                run_type="business_growth",
                now=NOW,
            )
            repeated, repeated_created = await schedule_marketing_automation(
                _Session([None, run]),
                business_id=business_id,
                run_type="business_growth",
                now=NOW,
            )
        self.assertIs(first, run)
        self.assertIs(repeated, run)
        self.assertTrue(first_created)
        self.assertFalse(repeated_created)
        enqueue.assert_awaited_once_with(
            ANY,
            business_id=business_id,
            job_type="analyze_campaign_opportunities",
            idempotency_key=f"marketing-automation-run:{run_id}",
            marketing_automation_run_id=run_id,
        )

    def test_production_provider_registry_fails_closed(self) -> None:
        self.assertIsNone(default_competitor_research_provider())
        self.assertIsNone(website_deployment_provider("shopify"))
        self.assertIsNone(advertising_provider("meta_ads"))
        self.assertIsNone(social_publishing_provider("instagram"))

    async def test_provider_candidate_stays_suggested_and_retains_provenance(self) -> None:
        business_id = uuid4()
        run = SimpleNamespace(id=uuid4(), provider_key="research_test")
        result = CompetitorCandidateResult(
            name="Supported public company",
            website_domain="https://example.test/about",
            canonical_url="https://example.test/",
            discovery_reason="Its public positioning overlaps the business service category.",
            confidence=Decimal("0.770"),
            industry_relationship="Same service category",
            geographic_relationship=None,
            evidence=(CompetitorEvidenceResult(
                source_type="public_url",
                source_reference="https://example.test/about",
                title="Public company page",
                excerpt="Public positioning evidence supplied by the test provider.",
                observed_at=NOW,
            ),),
        )
        session = _Session([None, 0])
        created, evidence_added = await _persist_candidate_result(
            session, business_id=business_id, run=run, result=result, now=NOW,
        )
        self.assertTrue(created)
        self.assertEqual(evidence_added, 1)
        candidate = next(item for item in session.added if isinstance(item, CompetitorCandidate))
        evidence = next(item for item in session.added if isinstance(item, CompetitorCandidateEvidence))
        self.assertEqual(candidate.business_id, business_id)
        self.assertEqual(candidate.status, "suggested")
        self.assertIsNone(candidate.competitor_id)
        self.assertEqual(evidence.business_id, business_id)
        self.assertEqual(evidence.discovery_run_id, run.id)
        self.assertEqual(evidence.source_reference, "https://example.test/about")
        self.assertFalse(any(isinstance(item, Competitor) for item in session.added))

    async def test_candidate_without_evidence_is_rejected_no_fake_fallback(self) -> None:
        result = CompetitorCandidateResult(
            name="Unsupported guess", website_domain=None, canonical_url=None,
            discovery_reason="Guess", confidence=Decimal("0.5"),
            industry_relationship=None, geographic_relationship=None, evidence=(),
        )
        with self.assertRaises(AutomationIntelligenceProviderError):
            await _persist_candidate_result(
                _Session([]), business_id=uuid4(),
                run=SimpleNamespace(id=uuid4(), provider_key="test"),
                result=result, now=NOW,
            )

    def test_provider_references_reject_clickable_unsafe_schemes(self) -> None:
        self.assertEqual(
            _provider_reference("https://example.test/report"),
            "https://example.test/report",
        )
        self.assertEqual(_provider_reference("report-opaque-id"), "report-opaque-id")
        with self.assertRaises(AutomationIntelligenceProviderError):
            _provider_reference("javascript:alert(1)")

    async def test_missing_provider_records_unavailable_without_candidates(self) -> None:
        business_id = uuid4()
        run = SimpleNamespace(
            id=uuid4(), business_id=business_id, status="queued", started_at=None,
            completed_at=None, provider_key=None, failure_code=None, candidate_count=0,
            brain_revision="0" * 64,
        )
        session = _Session([])
        with patch(
            "app.services.automation_intelligence._get_discovery_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.automation_intelligence.require_feature", new=AsyncMock(),
        ):
            returned = await run_competitor_discovery(
                session, business_id=business_id, run_id=run.id, provider=None, now=NOW,
            )
        self.assertIs(returned, run)
        self.assertEqual(run.status, "provider_unavailable")
        self.assertEqual(run.candidate_count, 0)
        self.assertFalse(session.added)

    async def test_cross_business_candidate_is_hidden(self) -> None:
        candidate = SimpleNamespace(id=uuid4(), business_id=uuid4())
        with self.assertRaises(AutomationIntelligenceNotFoundError):
            await _get_candidate(
                _Session([candidate]), business_id=uuid4(), candidate_id=candidate.id,
            )

    async def test_unapproved_content_cannot_create_publish_action(self) -> None:
        content = SimpleNamespace(id=uuid4(), status="review")
        with patch(
            "app.services.marketing_actions.get_content", new=AsyncMock(return_value=content),
        ), self.assertRaises(MarketingValidationError):
            await prepare_content_publish_action(
                _Session([]), business_id=uuid4(), content_id=content.id,
                requested_by_user_id=uuid4(), channel="instagram",
            )

    async def test_external_execution_requires_connection_and_real_write_provider(self) -> None:
        business_id = uuid4()
        missing = await _connector_state(
            _Session([None]), business_id=business_id,
            connector_type="meta_ads", action_type="create_meta_campaign",
        )
        self.assertEqual(missing["connector_state"], "connection_required")
        connected = SimpleNamespace(status="connected", authentication_state="authorized")
        disabled = await _connector_state(
            _Session([connected]), business_id=business_id,
            connector_type="meta_ads", action_type="create_meta_campaign",
        )
        self.assertEqual(disabled["connector_state"], "provider_disabled")
        self.assertIn("No execution attempt", str(disabled["connector_message"]))
        unsupported = await _connector_state(
            _Session([]), business_id=business_id,
            connector_type="linkedin", action_type="publish_social_post",
        )
        self.assertEqual(unsupported["connector_state"], "provider_disabled")
        self.assertIn(
            "No authenticated connector definition",
            str(unsupported["connector_message"]),
        )

    async def test_opportunity_insert_is_atomic_and_business_scoped(
        self,
    ) -> None:
        business_id = uuid4()
        inserted_id = uuid4()
        source_entity_id = uuid4()
        session = _Session([inserted_id])

        created = await _create_opportunity_if_missing(
            session,
            business_id=business_id,
            dedupe_key="business-growth:revenue-decline:2026-08-24",
            title="Revenue decline detected",
            description="Observed paid-order revenue declined versus the prior comparable period.",
            category="revenue_opportunity",
            source="commerce",
            source_entity_type="commerce_growth_signal",
            source_entity_id=source_entity_id,
            reason="Deterministic first-party commerce comparison detected a material decline.",
            confidence=Decimal("0.900"),
            recommendation="Review the decline and identify an evidence-backed recovery action.",
            provenance=[{
                "classification": "first_party_observed",
                "source_type": "orders",
                "source_id": None,
            }],
        )

        self.assertTrue(created)
        self.assertEqual(len(session.scalar_statements), 1)

        compiled = session.scalar_statements[0].compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)

        self.assertIn(
            "ON CONFLICT (business_id, dedupe_key) DO NOTHING",
            sql,
        )
        self.assertEqual(
            compiled.params["business_id"],
            business_id,
        )
        self.assertEqual(
            compiled.params["dedupe_key"],
            "business-growth:revenue-decline:2026-08-24",
        )
        self.assertEqual(session.added, [])

    async def test_opportunity_insert_returns_false_for_duplicate(
        self,
    ) -> None:
        session = _Session([])

        created = await _create_opportunity_if_missing(
            session,
            business_id=uuid4(),
            dedupe_key="business-growth:duplicate:test",
            title="Duplicate signal",
            description="This signal has already been persisted.",
            category="revenue_opportunity",
            source="commerce",
            source_entity_type="commerce_growth_signal",
            source_entity_id=uuid4(),
            reason="Deterministic duplicate test.",
            confidence=Decimal("0.900"),
            recommendation="No duplicate opportunity should be created.",
            provenance=[],
        )

        self.assertFalse(created)
        self.assertEqual(len(session.scalar_statements), 1)
        self.assertEqual(session.added, [])

    async def test_opportunity_insert_sanitizes_database_failure(
        self,
    ) -> None:
        session = _Session(
            [],
            scalar_error=SQLAlchemyError(
                "sensitive database details"
            ),
        )

        with self.assertRaisesRegex(
            AutomationIntelligencePersistenceError,
            "opportunity_persist_failed",
        ):
            await _create_opportunity_if_missing(
                session,
                business_id=uuid4(),
                dedupe_key="business-growth:persistence:test",
                title="Persistence test",
                description="Persistence failure must be sanitized.",
                category="revenue_opportunity",
                source="commerce",
                source_entity_type="commerce_growth_signal",
                source_entity_id=uuid4(),
                reason="Test database failure handling.",
                confidence=Decimal("0.900"),
                recommendation="Do not leak database details.",
                provenance=[],
            )

        self.assertEqual(len(session.scalar_statements), 1)
        self.assertEqual(session.added, [])

    def test_industry_guardrails_do_not_cross_domain_boundaries(self) -> None:
        healthcare = _industry_guardrail("clinic")
        commerce = _industry_guardrail("e-commerce")
        real_estate = _industry_guardrail("real estate")
        self.assertIn("PHI", healthcare)
        self.assertIn("trusted context", commerce)
        self.assertIn("Do not treat catalog items as properties", real_estate)


class _Session:
    def __init__(
        self,
        scalar_values: list[object],
        *,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values)
        self.scalar_error = scalar_error
        self.scalar_statements: list[object] = []
        self.added: list[object] = []

    async def scalar(self, statement):
        self.scalar_statements.append(statement)

        if self.scalar_error is not None:
            raise self.scalar_error

        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()


class _ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return list(self.values)


class _SchedulerSession:
    def __init__(self, businesses: list[object]) -> None:
        self.businesses = businesses

    async def scalars(self, _statement) -> _ScalarResult:
        return _ScalarResult(self.businesses)
