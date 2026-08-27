from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.automation_intelligence import (  # noqa: E402
    AutomationIntelligencePersistenceError,
)
from app.exceptions.background_jobs import BackgroundJobPersistenceError  # noqa: E402
from app.services.marketing_automation import (  # noqa: E402
    _GrowthSignal,
    _business_growth_comparison_window,
    _create_opportunity_if_missing,
    _detect_advertising_inefficiency,
    _detect_inventory_risks,
    _detect_product_demand_declines,
    _detect_refund_anomalies,
    _detect_revenue_declines,
    analyze_bounded_campaign_opportunities,
)


BUSINESS_ID = UUID("f1000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


class BusinessGrowthAutomationTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_growth_dispatch_is_isolated_from_campaign_path(self) -> None:
        growth_run = _run("business_growth")
        campaign_run = _run("campaign_opportunities")
        growth_dispatch = AsyncMock(return_value=growth_run)
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=growth_run),
        ), patch(
            "app.services.marketing_automation._analyze_bounded_business_growth",
            new=growth_dispatch,
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ):
            returned = await analyze_bounded_campaign_opportunities(
                _Session(), business_id=BUSINESS_ID, run_id=growth_run.id, now=NOW
            )
        self.assertIs(returned, growth_run)
        growth_dispatch.assert_awaited_once()

        growth_dispatch.reset_mock()
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=campaign_run),
        ), patch(
            "app.services.marketing_automation._analyze_bounded_business_growth",
            new=growth_dispatch,
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ):
            returned = await analyze_bounded_campaign_opportunities(
                _Session(execute_values=[[], []]),
                business_id=BUSINESS_ID,
                run_id=campaign_run.id,
                now=NOW,
            )
        self.assertIs(returned, campaign_run)
        self.assertEqual(campaign_run.status, "completed")
        growth_dispatch.assert_not_awaited()

    async def test_non_commerce_business_finishes_without_commerce_queries(self) -> None:
        run = _run("business_growth")
        session = _Session(scalar_values=["clinic"])
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ):
            returned = await analyze_bounded_campaign_opportunities(
                session, business_id=BUSINESS_ID, run_id=run.id, now=NOW
            )
        self.assertIs(returned, run)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.proposal_count, 0)
        self.assertEqual(session.execute_statements, [])

    async def test_revenue_decline_requires_material_same_currency_evidence(self) -> None:
        rows = [
            SimpleNamespace(
                currency="USD",
                recent_order_count=3,
                baseline_order_count=10,
                recent_net_revenue=Decimal("400.00"),
                baseline_net_revenue=Decimal("1000.00"),
            ),
            SimpleNamespace(
                currency="EUR",
                recent_order_count=0,
                baseline_order_count=4,
                recent_net_revenue=Decimal("0.00"),
                baseline_net_revenue=Decimal("1000.00"),
            ),
        ]
        session = _Session(execute_values=[rows])
        signals = await _detect_revenue_declines(
            session, business_id=BUSINESS_ID, window=_window()
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].currency, "USD")
        self.assertIn(":USD:", signals[0].dedupe_key)
        evidence = signals[0].provenance[0]
        self.assertEqual(evidence["baseline_order_count"], 10)
        self.assertEqual(evidence["recent_net_revenue"], "400.00")

        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)
        self.assertIn("orders.provider_created_at", sql)
        self.assertIn("orders.created_at", sql)
        self.assertIn("CASE WHEN", sql)
        self.assertIn("manual", compiled.params.values())

    async def test_revenue_decline_ignores_tiny_or_insufficient_baseline(self) -> None:
        rows = [SimpleNamespace(
            currency="USD",
            recent_order_count=0,
            baseline_order_count=1,
            recent_net_revenue=Decimal("0.00"),
            baseline_net_revenue=Decimal("25.00"),
        )]
        signals = await _detect_revenue_declines(
            _Session(execute_values=[rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )
        self.assertEqual(signals, [])

    async def test_product_decline_is_material_capped_and_tenant_scoped(self) -> None:
        item_id = uuid4()
        rows = [
            SimpleNamespace(
                catalog_item_id=item_id,
                catalog_item_name="Supported product",
                currency="USD",
                recent_order_count=2,
                baseline_order_count=5,
                recent_units=3,
                baseline_units=12,
                recent_recorded_revenue=Decimal("150.00"),
                baseline_recorded_revenue=Decimal("800.00"),
            ),
            SimpleNamespace(
                catalog_item_id=uuid4(),
                catalog_item_name="One-unit noise",
                currency="USD",
                recent_order_count=0,
                baseline_order_count=1,
                recent_units=0,
                baseline_units=1,
                recent_recorded_revenue=Decimal("0.00"),
                baseline_recorded_revenue=Decimal("20.00"),
            ),
        ]
        session = _Session(execute_values=[rows])
        signals = await _detect_product_demand_declines(
            session, business_id=BUSINESS_ID, window=_window()
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].source_entity_id, item_id)
        self.assertEqual(signals[0].source_entity_type, "catalog_item")
        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertGreaterEqual(
            sum(value == BUSINESS_ID for value in compiled.params.values()), 3
        )

    async def test_ad_detector_uses_only_provider_attribution_and_ratios(self) -> None:
        item_id = uuid4()
        campaign_id = uuid4()
        rows = [SimpleNamespace(
            provider="google",
            campaign_id=campaign_id,
            catalog_item_id=item_id,
            catalog_item_name="Product",
            currency="USD",
            recent_spend=Decimal("250.00"),
            baseline_spend=Decimal("200.00"),
            recent_clicks=100,
            baseline_clicks=100,
            recent_conversions=Decimal("2.0000"),
            baseline_conversions=Decimal("10.0000"),
            recent_conversion_value=Decimal("100.00"),
            baseline_conversion_value=Decimal("800.00"),
            recent_slice_count=7,
            baseline_slice_count=7,
        )]
        session = _Session(execute_values=[rows])
        signals = await _detect_advertising_inefficiency(
            session, business_id=BUSINESS_ID, window=_window()
        )
        self.assertEqual(len(signals), 1)
        self.assertIn("provider-attributed", signals[0].description)
        self.assertIn("no causal claim", str(signals[0].provenance[0]))
        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertIn("provider_attributed", compiled.params.values())
        self.assertNotIn("ai_business_os_derived", compiled.params.values())
        self.assertNotIn("unknown", compiled.params.values())

    async def test_ad_detector_ignores_tiny_or_stable_performance(self) -> None:
        rows = [SimpleNamespace(
            provider="meta",
            campaign_id=uuid4(),
            catalog_item_id=uuid4(),
            catalog_item_name="Product",
            currency="USD",
            recent_spend=Decimal("20.00"),
            baseline_spend=Decimal("20.00"),
            recent_clicks=10,
            baseline_clicks=10,
            recent_conversions=Decimal("1.0000"),
            baseline_conversions=Decimal("1.0000"),
            recent_conversion_value=Decimal("50.00"),
            baseline_conversion_value=Decimal("50.00"),
            recent_slice_count=1,
            baseline_slice_count=1,
        )]
        signals = await _detect_advertising_inefficiency(
            _Session(execute_values=[rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )
        self.assertEqual(signals, [])

    async def test_refund_anomaly_requires_rate_history_and_same_currency(self) -> None:
        revenue_rows = [SimpleNamespace(
            currency="USD",
            recent_order_count=10,
            baseline_order_count=10,
            recent_paid_order_revenue=Decimal("1000.00"),
            baseline_paid_order_revenue=Decimal("1000.00"),
        )]
        refund_rows = [SimpleNamespace(
            currency="USD",
            recent_refund_count=3,
            baseline_refund_count=1,
            recent_refund_amount=Decimal("200.00"),
            baseline_refund_amount=Decimal("25.00"),
        )]
        session = _Session(execute_values=[revenue_rows, refund_rows])
        signals = await _detect_refund_anomalies(
            session, business_id=BUSINESS_ID, window=_window()
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].currency, "USD")
        self.assertEqual(
            signals[0].provenance[0]["refund_timestamp"],
            "order_refunds.occurred_at",
        )
        refund_sql = str(session.execute_statements[1].compile(
            dialect=postgresql.dialect()
        ))
        self.assertIn("order_refunds.currency = orders.currency", refund_sql)

    async def test_refund_anomaly_ignores_single_tiny_refund(self) -> None:
        revenue_rows = [SimpleNamespace(
            currency="USD",
            recent_order_count=10,
            baseline_order_count=10,
            recent_paid_order_revenue=Decimal("1000.00"),
            baseline_paid_order_revenue=Decimal("1000.00"),
        )]
        refund_rows = [SimpleNamespace(
            currency="USD",
            recent_refund_count=1,
            baseline_refund_count=0,
            recent_refund_amount=Decimal("5.00"),
            baseline_refund_amount=Decimal("0.00"),
        )]
        signals = await _detect_refund_anomalies(
            _Session(execute_values=[revenue_rows, refund_rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )
        self.assertEqual(signals, [])

    async def test_inventory_risk_uses_known_inventory_and_sales_velocity(self) -> None:
        item_id = uuid4()
        rows = [
            SimpleNamespace(
                catalog_item_id=item_id,
                catalog_item_name="Fast seller",
                inventory_quantity=3,
                units_sold=14,
                order_count=7,
            ),
            SimpleNamespace(
                catalog_item_id=uuid4(),
                catalog_item_name="Unknown stock",
                inventory_quantity=None,
                units_sold=20,
                order_count=10,
            ),
            SimpleNamespace(
                catalog_item_id=uuid4(),
                catalog_item_name="No velocity",
                inventory_quantity=1,
                units_sold=0,
                order_count=0,
            ),
        ]
        session = _Session(execute_values=[rows])
        signals = await _detect_inventory_risks(
            session, business_id=BUSINESS_ID, window=_window()
        )
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].source_entity_id, item_id)
        evidence = signals[0].provenance[0]
        self.assertEqual(evidence["known_inventory_quantity"], 3)
        self.assertEqual(evidence["estimated_days_of_cover"], "3")
        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertIn("catalog_items.inventory_quantity IS NOT NULL", str(compiled))

    async def test_proposal_count_counts_only_atomic_new_inserts(self) -> None:
        run = _run("business_growth")
        first = _signal("one")
        second = _signal("two")
        session = _Session(scalar_values=["e-commerce"])
        create = AsyncMock(side_effect=[True, False])
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ), patch(
            "app.services.marketing_automation._detect_revenue_declines",
            new=AsyncMock(return_value=[first, second]),
        ), patch(
            "app.services.marketing_automation._detect_product_demand_declines",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.marketing_automation._detect_advertising_inefficiency",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.marketing_automation._detect_refund_anomalies",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.marketing_automation._detect_inventory_risks",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.marketing_automation._create_opportunity_if_missing",
            new=create,
        ):
            returned = await analyze_bounded_campaign_opportunities(
                session, business_id=BUSINESS_ID, run_id=run.id, now=NOW
            )
        self.assertEqual(returned.proposal_count, 1)
        self.assertEqual(create.await_count, 2)
        self.assertTrue(all(
            call.kwargs["suggested_action"] == "analyze_business_opportunity"
            for call in create.await_args_list
        ))
        self.assertTrue(all(
            call.kwargs["enqueue_initial_analysis"] is True
            for call in create.await_args_list
        ))

    async def test_business_growth_is_bounded_to_twenty_new_jobs(self) -> None:
        run = _run("business_growth")
        detector_signals = [
            _signal(f"{detector}-{index}")
            for detector in range(5)
            for index in range(8)
        ]
        create = AsyncMock(return_value=True)
        detector_patches = (
            patch(
                "app.services.marketing_automation._detect_revenue_declines",
                new=AsyncMock(return_value=detector_signals[0:8]),
            ),
            patch(
                "app.services.marketing_automation._detect_product_demand_declines",
                new=AsyncMock(return_value=detector_signals[8:16]),
            ),
            patch(
                "app.services.marketing_automation._detect_advertising_inefficiency",
                new=AsyncMock(return_value=detector_signals[16:24]),
            ),
            patch(
                "app.services.marketing_automation._detect_refund_anomalies",
                new=AsyncMock(return_value=detector_signals[24:32]),
            ),
            patch(
                "app.services.marketing_automation._detect_inventory_risks",
                new=AsyncMock(return_value=detector_signals[32:40]),
            ),
        )
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ), patch(
            "app.services.marketing_automation._create_opportunity_if_missing",
            new=create,
        ), detector_patches[0], detector_patches[1], detector_patches[2], detector_patches[3], detector_patches[4]:
            returned = await analyze_bounded_campaign_opportunities(
                _Session(scalar_values=["e-commerce"]),
                business_id=BUSINESS_ID,
                run_id=run.id,
                now=NOW,
            )
        self.assertEqual(returned.proposal_count, 20)
        self.assertEqual(create.await_count, 20)
        self.assertTrue(all(
            call.kwargs["enqueue_initial_analysis"] is True
            for call in create.await_args_list
        ))

    async def test_detector_database_failure_is_sanitized(self) -> None:
        run = _run("business_growth")
        session = _Session(scalar_values=["e-commerce"])
        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.marketing_automation.require_feature", new=AsyncMock()
        ), patch(
            "app.services.marketing_automation._detect_revenue_declines",
            new=AsyncMock(side_effect=SQLAlchemyError("sensitive SQL")),
        ):
            with self.assertRaisesRegex(
                AutomationIntelligencePersistenceError,
                "business_growth_context_failed",
            ) as raised:
                await analyze_bounded_campaign_opportunities(
                    session, business_id=BUSINESS_ID, run_id=run.id, now=NOW
                )
        self.assertNotIn("sensitive SQL", str(raised.exception))

    async def test_opportunity_helper_supports_null_entity_and_generic_suggestion(self) -> None:
        session = _Session(scalar_values=[uuid4()])
        created = await _create_opportunity_if_missing(
            session,
            business_id=BUSINESS_ID,
            dedupe_key="business-growth:revenue-decline:USD:2026-08-24",
            title="Revenue decline",
            description="Observed decline.",
            category="revenue_decline",
            source="commerce",
            source_entity_type=None,
            source_entity_id=None,
            reason="Deterministic evidence.",
            confidence=Decimal("0.800"),
            recommendation="Analyze it.",
            provenance=[],
            currency="USD",
            priority="high",
            suggested_action="analyze_business_opportunity",
        )
        self.assertTrue(created)
        compiled = session.scalar_statements[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertIsNone(compiled.params["source_entity_id"])
        self.assertEqual(compiled.params["currency"], "USD")
        self.assertEqual(compiled.params["priority"], "high")
        self.assertEqual(
            compiled.params["suggested_action"], "analyze_business_opportunity"
        )

    async def test_new_opportunity_enqueues_exactly_one_typed_analysis_job(self) -> None:
        opportunity_id = uuid4()
        session = _Session(scalar_values=[opportunity_id])
        enqueue = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        with patch(
            "app.services.marketing_automation.enqueue_job", new=enqueue
        ):
            created = await _create_opportunity_if_missing(
                session,
                **_opportunity_values("new"),
                enqueue_initial_analysis=True,
            )
        self.assertTrue(created)
        enqueue.assert_awaited_once_with(
            session,
            business_id=BUSINESS_ID,
            job_type="analyze_business_opportunity",
            idempotency_key=f"opportunity-analysis:{opportunity_id}:initial",
            opportunity_id=opportunity_id,
        )
        self.assertNotIn("provider", enqueue.await_args.kwargs)
        self.assertNotIn("api_key", enqueue.await_args.kwargs)

    async def test_duplicate_opportunity_does_not_enqueue_analysis(self) -> None:
        enqueue = AsyncMock()
        with patch(
            "app.services.marketing_automation.enqueue_job", new=enqueue
        ):
            created = await _create_opportunity_if_missing(
                _Session(),
                **_opportunity_values("duplicate"),
                enqueue_initial_analysis=True,
            )
        self.assertFalse(created)
        enqueue.assert_not_awaited()

    async def test_concurrent_atomic_insert_results_enqueue_only_for_winner(self) -> None:
        opportunity_id = uuid4()
        enqueue = AsyncMock(return_value=SimpleNamespace(id=uuid4()))
        with patch(
            "app.services.marketing_automation.enqueue_job", new=enqueue
        ):
            created = await asyncio.gather(
                _create_opportunity_if_missing(
                    _Session(scalar_values=[opportunity_id]),
                    **_opportunity_values("concurrent"),
                    enqueue_initial_analysis=True,
                ),
                _create_opportunity_if_missing(
                    _Session(),
                    **_opportunity_values("concurrent"),
                    enqueue_initial_analysis=True,
                ),
            )
        self.assertEqual(created, [True, False])
        enqueue.assert_awaited_once()

    async def test_campaign_opportunity_insert_never_enqueues_growth_analysis(self) -> None:
        enqueue = AsyncMock()
        with patch(
            "app.services.marketing_automation.enqueue_job", new=enqueue
        ):
            created = await _create_opportunity_if_missing(
                _Session(scalar_values=[uuid4()]),
                **_opportunity_values("campaign"),
            )
        self.assertTrue(created)
        enqueue.assert_not_awaited()

    async def test_analysis_enqueue_failure_is_sanitized_and_aborts_handoff(self) -> None:
        with patch(
            "app.services.marketing_automation.enqueue_job",
            new=AsyncMock(side_effect=BackgroundJobPersistenceError("private")),
        ), self.assertRaisesRegex(
            AutomationIntelligencePersistenceError,
            "opportunity_analysis_enqueue_failed",
        ):
            await _create_opportunity_if_missing(
                _Session(scalar_values=[uuid4()]),
                **_opportunity_values("enqueue-failure"),
                enqueue_initial_analysis=True,
            )


def _run(run_type: str):
    return SimpleNamespace(
        id=uuid4(),
        business_id=BUSINESS_ID,
        run_type=run_type,
        status="queued",
        started_at=None,
        completed_at=None,
        failure_code=None,
        proposal_count=0,
        window_start=date(2026, 8, 24),
        window_end=date(2026, 8, 24),
    )


def _window():
    return _business_growth_comparison_window(_run("business_growth"))


def _signal(suffix: str) -> _GrowthSignal:
    return _GrowthSignal(
        dedupe_key=f"business-growth:test:{suffix}:2026-08-24",
        title="Signal",
        description="Observed deterministic signal.",
        category="revenue_decline",
        source="commerce",
        source_entity_type=None,
        source_entity_id=None,
        reason="Evidence.",
        confidence=Decimal("0.800"),
        recommendation="Analyze it.",
        provenance=[],
    )


def _opportunity_values(suffix: str) -> dict[str, object]:
    return {
        "business_id": BUSINESS_ID,
        "dedupe_key": f"business-growth:test:{suffix}:2026-08-24",
        "title": "Signal",
        "description": "Observed deterministic signal.",
        "category": "revenue_decline",
        "source": "commerce",
        "source_entity_type": None,
        "source_entity_id": None,
        "reason": "Evidence.",
        "confidence": Decimal("0.800"),
        "recommendation": "Analyze it.",
        "provenance": [],
        "suggested_action": "analyze_business_opportunity",
    }


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


class _Session:
    def __init__(
        self,
        *,
        scalar_values: list[object] | None = None,
        execute_values: list[list[object]] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.execute_values = list(execute_values or [])
        self.scalar_statements: list[object] = []
        self.execute_statements: list[object] = []
        self.flush_count = 0

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def execute(self, statement):
        self.execute_statements.append(statement)
        values = self.execute_values.pop(0) if self.execute_values else []
        return _Result(values)

    async def scalars(self, statement):
        return await self.execute(statement)

    async def flush(self) -> None:
        self.flush_count += 1
