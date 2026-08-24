from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault(
    "AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test"
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.action_execution_attempt import (  # noqa: E402
    ActionExecutionAttemptConflictError,
    ActionExecutionAttemptValidationError,
)
from app.exceptions.automation_intelligence import (  # noqa: E402
    AutomationIntelligenceProviderError,
)
from app.integrations.automation_contracts import (  # noqa: E402
    CompetitorCandidateResult,
    CompetitorEvidenceResult,
)
from app.models.ai_action import AIAction  # noqa: E402
from app.models.automation_intelligence import (  # noqa: E402
    AdvertisingSpendPolicy,
    AudienceHypothesis,
    ChatbotDeployment,
    CompetitorCandidateEvidence,
    MarketingActionProposal,
)
from app.models.background_job import BackgroundJob  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.marketing import Campaign  # noqa: E402
from app.schemas.ai_action_payload import (  # noqa: E402
    ChangeAdBudgetPayload,
    CreateMetaCampaignPayload,
    LaunchMetaCampaignPayload,
)
from app.schemas.marketing import ScheduledContentProposal  # noqa: E402
from app.services.action_execution_attempt import (  # noqa: E402
    prepare_action_execution_attempt,
)
from app.services.action_policy import (  # noqa: E402
    canonical_action_payload_hash,
    evaluate_action_policy,
)
from app.services.action_registry import ACTION_REGISTRY  # noqa: E402
from app.services.advertising_spend_policy import (  # noqa: E402
    require_advertising_spend_authorized,
)
from app.services.automation_intelligence import (  # noqa: E402
    _bounded_metadata,
    _canonical_url,
    _persist_candidate_result,
    discovery_window_key,
    manual_discovery_window_key,
    schedule_competitor_discovery,
)
from app.services.background_jobs import (  # noqa: E402
    _synchronize_linked_run,
    claim_jobs,
    record_job_failure,
)
from app.services.business_brain_assembly import (  # noqa: E402
    build_appointment_type_source,
    iterate_business_brain_sources,
)
from app.services.marketing import _execute_cmo  # noqa: E402
from app.services.marketing_actions import _connector_state  # noqa: E402


BUSINESS_ID = UUID("91000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("92000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class TenantConstraintTests(unittest.TestCase):
    def test_cross_tenant_references_are_composite(self) -> None:
        expected = {
            AIAction.__table__: {("execution_id", "business_id")},
            MarketingActionProposal.__table__: {
                ("execution_id", "business_id"),
                ("approval_id", "business_id"),
            },
            CompetitorCandidateEvidence.__table__: {
                ("candidate_id", "business_id"),
                ("discovery_run_id", "business_id"),
            },
            BackgroundJob.__table__: {
                ("competitor_discovery_run_id", "business_id"),
                ("marketing_automation_run_id", "business_id"),
            },
        }
        for table, required in expected.items():
            actual = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, ForeignKeyConstraint)
            }
            self.assertTrue(required.issubset(actual), table.name)

    def test_campaign_is_the_only_audience_link_owner(self) -> None:
        self.assertNotIn("campaign_id", AudienceHypothesis.__table__.c)
        self.assertIn("audience_hypothesis_id", Campaign.__table__.c)

    def test_chatbot_identity_is_site_specific_and_hosted_is_fixed(self) -> None:
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in ChatbotDeployment.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(
            ("business_id", "target_type", "deployment_target_key"), unique_columns
        )

    def test_c7_downgrade_transforms_new_job_values_before_old_checks(self) -> None:
        source = Path(
            "alembic/versions/c7d4e9a21f06_add_automation_first_intelligence.py"
        ).read_text()
        downgrade = source[source.index("def downgrade()") :]
        self.assertLess(
            downgrade.index("UPDATE background_jobs"),
            downgrade.index("job_type IN ('process_automation_event'"),
        )
        self.assertLess(
            downgrade.index("DELETE FROM background_jobs"),
            downgrade.rindex("job_type IN ('process_automation_event'"),
        )


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_refresh_window_is_not_the_weekly_schedule_window(self) -> None:
        self.assertNotEqual(
            manual_discovery_window_key(NOW), discovery_window_key(NOW)
        )

    async def test_manual_refresh_returns_recent_run_without_weekly_dedupe(self) -> None:
        recent = SimpleNamespace(
            id=uuid4(), business_id=BUSINESS_ID, trigger_type="manual_refresh"
        )
        returned, created = await schedule_competitor_discovery(
            _ScalarSession([BUSINESS_ID, recent]),
            business_id=BUSINESS_ID,
            trigger_type="manual_refresh",
            now=NOW,
        )
        self.assertIs(returned, recent)
        self.assertFalse(created)

    def test_canonical_url_requires_absolute_http_with_host(self) -> None:
        self.assertEqual(_canonical_url("HTTPS://Example.Test/path"), "https://example.test/path")
        for invalid in ("example.test/path", "javascript:alert(1)", "https:///missing"):
            with self.assertRaises(AutomationIntelligenceProviderError):
                _canonical_url(invalid)

    def test_metadata_depth_key_count_and_size_are_bounded(self) -> None:
        self.assertEqual(_bounded_metadata({"safe": ["value", 1, True]}), {"safe": ["value", 1, True]})
        with self.assertRaises(AutomationIntelligenceProviderError):
            _bounded_metadata({"a": {"b": {"c": {"d": {"e": "too deep"}}}}})
        with self.assertRaises(AutomationIntelligenceProviderError):
            _bounded_metadata({str(index): index for index in range(101)})
        with self.assertRaises(AutomationIntelligenceProviderError):
            _bounded_metadata({"oversized": "x" * 501})

    async def test_provider_cannot_silently_truncate_evidence(self) -> None:
        evidence = tuple(
            CompetitorEvidenceResult(
                source_type="provider_result",
                source_reference=f"provider-record-{index}",
                title="Evidence",
                excerpt="Bounded public evidence",
                observed_at=NOW,
            )
            for index in range(21)
        )
        result = CompetitorCandidateResult(
            name="Candidate",
            website_domain="example.test",
            canonical_url="https://example.test",
            discovery_reason="Supported overlap",
            confidence=Decimal("0.7"),
            industry_relationship=None,
            geographic_relationship=None,
            evidence=evidence,
        )
        with self.assertRaises(AutomationIntelligenceProviderError):
            await _persist_candidate_result(
                _ScalarSession([]),
                business_id=BUSINESS_ID,
                run=SimpleNamespace(id=uuid4(), provider_key="provider_test"),
                result=result,
                now=NOW,
            )

    async def test_evidence_source_type_and_timezone_are_validated(self) -> None:
        for source_type, observed_at in (
            ("unsupported", NOW),
            ("provider_result", NOW.replace(tzinfo=None)),
        ):
            result = CompetitorCandidateResult(
                name="Candidate",
                website_domain="example.test",
                canonical_url="https://example.test",
                discovery_reason="Supported overlap",
                confidence=Decimal("0.7"),
                industry_relationship=None,
                geographic_relationship=None,
                evidence=(CompetitorEvidenceResult(
                    source_type=source_type,  # type: ignore[arg-type]
                    source_reference="provider-record",
                    title="Evidence",
                    excerpt="Bounded public evidence",
                    observed_at=observed_at,
                ),),
            )
            with self.assertRaises(AutomationIntelligenceProviderError):
                await _persist_candidate_result(
                    _ScalarSession([None]),
                    business_id=BUSINESS_ID,
                    run=SimpleNamespace(id=uuid4(), provider_key="provider_test"),
                    result=result,
                    now=NOW,
                )


class SpendAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def _policy(self, **changes) -> AdvertisingSpendPolicy:
        values = {
            "id": uuid4(),
            "business_id": BUSINESS_ID,
            "currency": "USD",
            "max_single_campaign_budget": Decimal("100.00"),
            "max_single_budget_change": Decimal("50.00"),
            "daily_advertising_limit": Decimal("100.00"),
            "monthly_ai_managed_limit": Decimal("3100.00"),
            "active": True,
            "set_by_user_id": None,
        }
        values.update(changes)
        return AdvertisingSpendPolicy(**values)

    def _create_payload(self, budget: str) -> CreateMetaCampaignPayload:
        return CreateMetaCampaignPayload(
            campaign_name="Bounded campaign",
            objective="leads",
            budget=budget,
            currency="USD",
            budget_period="daily",
            audience={"countries": ["US"]},
            creative={"creative_refs": ["marketing-campaign:source"]},
        )

    async def test_below_and_equal_caps_pass_but_above_or_missing_fail(self) -> None:
        definition = ACTION_REGISTRY.require("create_meta_campaign")
        for budget in ("99.99", "100.00"):
            await require_advertising_spend_authorized(
                _ScalarSession([self._policy()]),
                business_id=BUSINESS_ID,
                definition=definition,
                payload=self._create_payload(budget),
            )
        for values in ([], [self._policy()]):
            payload = self._create_payload("100.01")
            with self.assertRaises(ActionExecutionAttemptValidationError):
                await require_advertising_spend_authorized(
                    _ScalarSession(values),
                    business_id=BUSINESS_ID,
                    definition=definition,
                    payload=payload,
                )

    async def test_wrong_currency_and_cross_tenant_campaign_fail_closed(self) -> None:
        create = ACTION_REGISTRY.require("create_meta_campaign")
        with self.assertRaises(ActionExecutionAttemptValidationError):
            await require_advertising_spend_authorized(
                _ScalarSession([self._policy(currency="EUR")]),
                business_id=BUSINESS_ID,
                definition=create,
                payload=self._create_payload("50.00"),
            )
        launch = ACTION_REGISTRY.require("launch_meta_campaign")
        campaign_id = uuid4()
        foreign = SimpleNamespace(
            id=campaign_id,
            business_id=OTHER_BUSINESS_ID,
            planned_budget=Decimal("25"),
            currency="USD",
            budget_mode="daily",
        )
        with self.assertRaises(ActionExecutionAttemptValidationError):
            await require_advertising_spend_authorized(
                _ScalarSession([self._policy(), foreign]),
                business_id=BUSINESS_ID,
                definition=launch,
                payload=LaunchMetaCampaignPayload(
                    campaign_ref=f"marketing-campaign:{campaign_id}"
                ),
            )

    async def test_budget_change_cap_uses_tenant_campaign_baseline(self) -> None:
        campaign_id = uuid4()
        campaign = SimpleNamespace(
            id=campaign_id,
            business_id=BUSINESS_ID,
            planned_budget=Decimal("100"),
            currency="USD",
            budget_mode="daily",
        )
        definition = ACTION_REGISTRY.require("change_ad_budget")
        for budget in ("150.00", "150.01"):
            operation = require_advertising_spend_authorized(
                _ScalarSession([
                    self._policy(
                        max_single_campaign_budget=Decimal("200"),
                        daily_advertising_limit=None,
                        monthly_ai_managed_limit=None,
                    ),
                    campaign,
                ]),
                business_id=BUSINESS_ID,
                definition=definition,
                payload=ChangeAdBudgetPayload(
                    campaign_ref=f"marketing-campaign:{campaign_id}",
                    budget=budget,
                    currency="USD",
                    budget_period="daily",
                ),
            )
            if budget == "150.00":
                await operation
            else:
                with self.assertRaises(ActionExecutionAttemptValidationError):
                    await operation

    async def test_mutated_payload_no_longer_matches_policy_authorization(self) -> None:
        action = _ready_crm_action()
        action.action_payload = {"customer_ref": "lead-1", "stage": "won"}
        with self.assertRaises(ActionExecutionAttemptConflictError):
            await prepare_action_execution_attempt(
                _ActionSession(action),
                business_id=BUSINESS_ID,
                action_id=action.id,
            )


class RuntimeBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_filter_is_applied_before_optional_tables_are_loaded(self) -> None:
        business = Business(
            id=BUSINESS_ID,
            name="Clinic",
            slug="clinic",
            business_type="clinic",
            status="active",
            timezone="UTC",
            currency="USD",
            locale="en",
            created_at=NOW,
            updated_at=NOW,
        )
        session = _BrainProfileSession(business)
        sources = [
            source
            async for source in iterate_business_brain_sources(
                session,
                BUSINESS_ID,
                allowed_source_types={"business_profile"},
            )
        ]
        self.assertEqual([source.source_type for source in sources], ["business_profile"])
        self.assertEqual(session.scalars_calls, 0)

    def test_public_appointment_source_excludes_booking_policy_and_people(self) -> None:
        appointment_type = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            name="Consultation",
            description="A public service description.",
            duration_minutes=30,
            updated_at=NOW,
            minimum_notice_minutes=120,
            cancellation_cutoff_minutes=60,
        )
        source = build_appointment_type_source(BUSINESS_ID, appointment_type)
        self.assertIn("Consultation", source.content)
        self.assertNotIn("notice", source.content.casefold())
        self.assertNotIn("cancellation", source.content.casefold())
        self.assertNotIn("patient", source.content.casefold())

    async def test_healthcare_cmo_receives_no_memory_knowledge_or_catalog(self) -> None:
        provider = SimpleNamespace()
        result = SimpleNamespace(output=SimpleNamespace(summary="safe"))
        call = AsyncMock(return_value=result)
        with patch("app.services.marketing.execute_ai_agent", new=call):
            returned = await _execute_cmo(
                _IndustrySession("clinic"), BUSINESS_ID, "Prepare content", provider
            )
        self.assertIs(returned, result)
        request = call.await_args.args[2]
        self.assertFalse(request.include_memory)
        self.assertEqual(
            request.brain_source_types,
            ["business_profile", "branding", "appointment_type"],
        )

    def test_content_output_is_named_and_rejects_workflow_state(self) -> None:
        proposal = ScheduledContentProposal.model_validate({
            "body": "Review-ready copy",
            "title": "A named proposal",
            "cta": None,
            "creative_brief": "Use brand colors.",
            "recommended_channel": "website",
            "generation_reasoning": "Grounded in public service context.",
            "evidence_source_ids": [],
        })
        self.assertEqual(proposal.title, "A named proposal")
        with self.assertRaises(ValidationError):
            ScheduledContentProposal.model_validate({
                **proposal.model_dump(), "status": "published"
            })

    async def test_unexpected_connector_registry_errors_propagate(self) -> None:
        with patch(
            "app.services.marketing_actions.require_connector",
            side_effect=RuntimeError("unexpected"),
        ), self.assertRaises(RuntimeError):
            await _connector_state(
                _ScalarSession([]),
                business_id=BUSINESS_ID,
                connector_type="meta_ads",
                action_type="create_meta_campaign",
            )

    async def test_linked_run_tracks_terminal_failure_and_manual_retry(self) -> None:
        run = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            status="running",
            started_at=NOW,
            completed_at=None,
            failure_code=None,
        )
        job = SimpleNamespace(
            business_id=BUSINESS_ID,
            competitor_discovery_run_id=run.id,
            marketing_automation_run_id=None,
        )
        session = _ScalarSession([run, run])
        await _synchronize_linked_run(
            session,
            job=job,
            status="failed",
            instant=NOW,
            failure_code="retry_exhausted",
        )
        self.assertEqual((run.status, run.failure_code), ("failed", "retry_exhausted"))
        await _synchronize_linked_run(
            session,
            job=job,
            status="queued",
            instant=NOW,
            failure_code=None,
        )
        self.assertEqual(run.status, "queued")
        self.assertIsNone(run.completed_at)

    async def test_retryable_job_failure_keeps_linked_run_nonterminal(self) -> None:
        run = SimpleNamespace(status="running")
        job = SimpleNamespace(
            id=uuid4(),
            business_id=BUSINESS_ID,
            job_type="discover_competitors",
            status="processing",
            worker_id="worker-1",
            attempt_count=1,
            max_attempts=4,
            competitor_discovery_run_id=uuid4(),
            marketing_automation_run_id=None,
            available_at=NOW,
            claimed_at=NOW,
            lease_expires_at=NOW,
            completed_at=None,
            failure_code=None,
        )
        session = _ScalarSession([job, run])
        await record_job_failure(
            session,
            job_id=job.id,
            worker_id="worker-1",
            failure_code="provider_unavailable",
            retryable=True,
        )
        self.assertEqual(job.status, "queued")
        self.assertEqual(run.status, "running")
        self.assertEqual(len(session.values), 1)

    async def test_claim_query_uses_per_tenant_fair_rank(self) -> None:
        class CaptureSession:
            statement = None

            async def scalars(self, statement):
                self.statement = statement
                return SimpleNamespace(all=lambda: [])

            async def flush(self):
                return None

        session = CaptureSession()

        await claim_jobs(
            session,
            worker_id="worker-1",
            batch_size=10,
            lease_seconds=60,
            now=NOW,
        )
        sql = str(session.statement.compile())
        self.assertIn("row_number() OVER (PARTITION BY background_jobs.business_id", sql)


class _ScalarSession:
    def __init__(self, values: list[object]) -> None:
        self.values = list(values)
        self.added: list[object] = []

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


class _ActionSession(_ScalarSession):
    def __init__(self, action: AIAction) -> None:
        super().__init__([action])


class _BrainProfileSession:
    def __init__(self, business: Business) -> None:
        self.business = business
        self.scalars_calls = 0

    async def scalar(self, _statement):
        return self.business

    async def scalars(self, _statement):
        self.scalars_calls += 1
        raise AssertionError("disallowed source table was queried")


class _IndustrySession(AsyncSession):
    def __init__(self, industry: str) -> None:
        super().__init__()
        self.industry = industry

    async def scalar(self, _statement, **_kwargs):
        return self.industry


def _ready_crm_action() -> AIAction:
    action = AIAction(
        id=uuid4(),
        business_id=BUSINESS_ID,
        execution_id=uuid4(),
        proposal_index=0,
        action_type="update_crm",
        description="Update a governed CRM record.",
        risk_level="low",
        proposed_requires_approval=False,
        status="ready",
        action_payload={"customer_ref": "lead-1", "stage": "qualified"},
        policy_decision="allow",
        policy_reason_code="policy_allow",
        policy_evaluated_at=NOW,
        execution_started_at=None,
        execution_completed_at=None,
        result_summary=None,
        failure_code=None,
        external_reference_id=None,
    )
    evaluation = evaluate_action_policy(action, business_id=BUSINESS_ID)
    assert evaluation.validated_payload is not None
    action.authorized_payload_hash = canonical_action_payload_hash(
        evaluation.validated_payload
    )
    return action
