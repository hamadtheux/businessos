from __future__ import annotations

import asyncio
import os
import unittest
from dataclasses import replace
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
    _rank_business_growth_signals,
    _create_opportunity_if_missing,
    _detect_advertising_inefficiency,
    _detect_customer_value_declines,
    _detect_high_value_customer_at_risk,
    _detect_inventory_risks,
    _detect_product_affinity_cross_sell,
    _detect_product_demand_declines,
    _detect_refund_anomalies,
    _detect_repeat_purchase_due,
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

    async def test_repeat_purchase_due_uses_customer_specific_cadence(self) -> None:
        customer_id = uuid4()
        order_ids = [uuid4() for _ in range(4)]

        candidate_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                purchase_count=4,
                last_purchase_at=datetime(2026, 7, 15, tzinfo=UTC),
                observed_retained_revenue=Decimal("480.00"),
            )
        ]
        history_rows = [
            SimpleNamespace(
                order_id=order_ids[0],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
                retained_revenue=Decimal("120.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[1],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 6, 15, tzinfo=UTC),
                retained_revenue=Decimal("120.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[2],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
                retained_revenue=Decimal("120.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[3],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                retained_revenue=Decimal("120.00"),
            ),
        ]

        session = _Session(
            execute_values=[candidate_rows, history_rows]
        )
        signals = await _detect_repeat_purchase_due(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.category, "repeat_purchase_due")
        self.assertEqual(signal.customer_id, customer_id)
        self.assertEqual(signal.source_entity_type, "customer")
        self.assertEqual(signal.source_entity_id, customer_id)
        self.assertEqual(signal.currency, "USD")
        self.assertIn(str(order_ids[-1]), signal.dedupe_key)
        self.assertNotIn("@", str(signal.provenance))
        self.assertNotIn("phone", str(signal.provenance).lower())
        self.assertNotIn("display_name", str(signal.provenance).lower())

        evidence = signal.provenance[0]
        self.assertEqual(evidence["purchase_count"], 4)
        self.assertEqual(evidence["purchase_interval_count"], 3)
        self.assertEqual(
            evidence["median_purchase_interval_days"],
            "14.000",
        )
        self.assertGreater(
            Decimal(evidence["purchase_overdue_ratio"]),
            Decimal("1.50"),
        )

        candidate_sql = str(
            session.execute_statements[0].compile(
                dialect=postgresql.dialect()
            )
        )
        self.assertIn("customers", candidate_sql)
        self.assertIn("customers.active IS true", candidate_sql)
        self.assertIn("orders.provider_created_at", candidate_sql)
        self.assertIn("orders.created_at", candidate_sql)
        self.assertIn("orders.customer_id IS NOT NULL", candidate_sql)


    async def test_repeat_purchase_due_fails_closed_when_not_overdue(self) -> None:
        customer_id = uuid4()
        order_ids = [uuid4() for _ in range(4)]

        candidate_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                purchase_count=4,
                last_purchase_at=datetime(2026, 8, 22, tzinfo=UTC),
                observed_retained_revenue=Decimal("400.00"),
            )
        ]
        history_rows = [
            SimpleNamespace(
                order_id=order_ids[0],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
                retained_revenue=Decimal("100.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[1],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 8, 8, tzinfo=UTC),
                retained_revenue=Decimal("100.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[2],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 8, 15, tzinfo=UTC),
                retained_revenue=Decimal("100.00"),
            ),
            SimpleNamespace(
                order_id=order_ids[3],
                customer_id=customer_id,
                currency="USD",
                occurred_at=datetime(2026, 8, 22, tzinfo=UTC),
                retained_revenue=Decimal("100.00"),
            ),
        ]

        signals = await _detect_repeat_purchase_due(
            _Session(execute_values=[candidate_rows, history_rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )
        self.assertEqual(signals, [])


    async def test_repeat_purchase_due_requires_three_real_purchases(self) -> None:
        customer_id = uuid4()

        candidate_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                purchase_count=2,
                last_purchase_at=datetime(2026, 6, 1, tzinfo=UTC),
                observed_retained_revenue=Decimal("200.00"),
            )
        ]

        signals = await _detect_repeat_purchase_due(
            _Session(execute_values=[candidate_rows, []]),
            business_id=BUSINESS_ID,
            window=_window(),
        )
        self.assertEqual(signals, [])


    async def test_high_value_customer_at_risk_uses_same_currency_cohort(self) -> None:
        customer_id = uuid4()
        source_order_id = uuid4()

        repeat_signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:repeat-purchase-due:"
                f"{customer_id}:USD:{source_order_id}"
            ),
            title="Repeat purchase cadence exceeded",
            description="Observed repeat-purchase cadence exceeded.",
            category="repeat_purchase_due",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason="Observed cadence evidence.",
            confidence=Decimal("0.850"),
            recommendation="Analyze it.",
            provenance=[{
                "classification": "first_party_observed",
                "detector": "repeat_purchase_due",
                "source_type": "orders",
                "source_id": str(source_order_id),
                "observed_at": "2026-07-15T00:00:00+00:00",
                "currency": "USD",
                "purchase_count": 5,
                "purchase_overdue_ratio": "2.200",
            }],
            priority="medium",
            currency="USD",
            rank_score=Decimal("0.800"),
        )

        cohort_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                value_window_purchase_count=5,
                observed_retained_revenue=Decimal("2200.00"),
                cohort_size=20,
                observed_value_percentile=Decimal("0.950"),
            )
        ]

        session = _Session(execute_values=[cohort_rows])

        signals = await _detect_high_value_customer_at_risk(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
            cadence_candidates=[repeat_signal],
        )

        self.assertEqual(len(signals), 1)
        signal = signals[0]

        self.assertEqual(
            signal.category,
            "high_value_customer_at_risk",
        )
        self.assertEqual(signal.customer_id, customer_id)
        self.assertEqual(signal.source_entity_type, "customer")
        self.assertEqual(signal.source_entity_id, customer_id)
        self.assertEqual(signal.currency, "USD")
        self.assertEqual(signal.priority, "high")
        self.assertIn(str(source_order_id), signal.dedupe_key)

        evidence = signal.provenance[0]
        self.assertEqual(evidence["cohort_size"], 20)
        self.assertEqual(
            evidence["observed_value_percentile"],
            "0.950",
        )
        self.assertEqual(evidence["value_lookback_days"], 365)
        self.assertIn(
            "not_predicted_clv",
            evidence["customer_value_scope"],
        )

        evidence_text = str(signal.provenance).lower()
        self.assertNotIn("@", evidence_text)
        self.assertNotIn("phone", evidence_text)
        self.assertNotIn("display_name", evidence_text)

        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)

        self.assertIn("percent_rank", sql.lower())
        self.assertIn("customers.active IS true", sql)
        self.assertIn("orders.provider_created_at", sql)
        self.assertIn("orders.created_at", sql)
        self.assertIn("orders.customer_id IS NOT NULL", sql)
        self.assertGreaterEqual(
            sum(
                value == BUSINESS_ID
                for value in compiled.params.values()
            ),
            2,
        )


    async def test_high_value_customer_at_risk_requires_meaningful_cohort(self) -> None:
        customer_id = uuid4()
        source_order_id = uuid4()

        repeat_signal = _GrowthSignal(
            dedupe_key=(
                "business-growth:repeat-purchase-due:"
                f"{customer_id}:USD:{source_order_id}"
            ),
            title="Repeat purchase cadence exceeded",
            description="Observed cadence.",
            category="repeat_purchase_due",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason="Observed cadence evidence.",
            confidence=Decimal("0.850"),
            recommendation="Analyze it.",
            provenance=[{
                "classification": "first_party_observed",
                "detector": "repeat_purchase_due",
                "source_type": "orders",
                "source_id": str(source_order_id),
                "observed_at": "2026-07-15T00:00:00+00:00",
                "currency": "USD",
                "purchase_count": 4,
                "purchase_overdue_ratio": "2.000",
            }],
            currency="USD",
        )

        rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                value_window_purchase_count=4,
                observed_retained_revenue=Decimal("9000.00"),
                cohort_size=5,
                observed_value_percentile=Decimal("1.000"),
            )
        ]

        signals = await _detect_high_value_customer_at_risk(
            _Session(execute_values=[rows]),
            business_id=BUSINESS_ID,
            window=_window(),
            cadence_candidates=[repeat_signal],
        )

        self.assertEqual(signals, [])


    async def test_high_value_customer_at_risk_requires_repeat_cadence_candidate(self) -> None:
        session = _Session()

        signals = await _detect_high_value_customer_at_risk(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
            cadence_candidates=[],
        )

        self.assertEqual(signals, [])
        self.assertEqual(session.execute_statements, [])


    async def test_high_value_promotion_suppresses_weaker_repeat_duplicate(self) -> None:
        run = _run("business_growth")
        customer_id = uuid4()

        repeat = _GrowthSignal(
            dedupe_key="business-growth:repeat-purchase-due:customer:USD:order",
            title="Repeat",
            description="Repeat cadence signal.",
            category="repeat_purchase_due",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason="Evidence.",
            confidence=Decimal("0.850"),
            recommendation="Analyze it.",
            provenance=[],
            currency="USD",
            rank_score=Decimal("0.800"),
        )

        promoted = _GrowthSignal(
            dedupe_key="business-growth:high-value-customer-at-risk:customer:USD:order",
            title="High value",
            description="High-value customer risk.",
            category="high_value_customer_at_risk",
            source="commerce",
            source_entity_type="customer",
            source_entity_id=customer_id,
            customer_id=customer_id,
            reason="Stronger evidence.",
            confidence=Decimal("0.900"),
            recommendation="Analyze it.",
            provenance=[],
            priority="high",
            currency="USD",
            rank_score=Decimal("0.900"),
        )

        create = AsyncMock(return_value=True)

        with patch(
            "app.services.marketing_automation.get_marketing_run",
            new=AsyncMock(return_value=run),
        ), patch(
            "app.services.marketing_automation.require_feature",
            new=AsyncMock(),
        ), patch(
            "app.services.marketing_automation._detect_revenue_declines",
            new=AsyncMock(return_value=[]),
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
            "app.services.marketing_automation._detect_repeat_purchase_due",
            new=AsyncMock(return_value=[repeat]),
        ) as repeat_detector, patch(
            "app.services.marketing_automation._detect_high_value_customer_at_risk",
            new=AsyncMock(return_value=[promoted]),
        ) as high_value_detector, patch(
            "app.services.marketing_automation._create_opportunity_if_missing",
            new=create,
        ):
            returned = await analyze_bounded_campaign_opportunities(
                _Session(scalar_values=["e-commerce"]),
                business_id=BUSINESS_ID,
                run_id=run.id,
                now=NOW,
            )

        self.assertEqual(returned.proposal_count, 1)
        self.assertEqual(create.await_count, 1)
        self.assertEqual(
            create.await_args.kwargs["category"],
            "high_value_customer_at_risk",
        )

        repeat_detector.assert_awaited_once()
        self.assertEqual(
            repeat_detector.await_args.kwargs["limit"],
            100,
        )

        high_value_detector.assert_awaited_once()
        self.assertEqual(
            high_value_detector.await_args.kwargs[
                "cadence_candidates"
            ],
            [repeat],
        )


    async def test_customer_value_decline_requires_revenue_and_purchase_decline(self) -> None:
        customer_id = uuid4()

        rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                recent_order_count=1,
                baseline_order_count=4,
                recent_net_revenue=Decimal("250.00"),
                baseline_net_revenue=Decimal("1000.00"),
            )
        ]

        session = _Session(execute_values=[rows])

        signals = await _detect_customer_value_declines(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(len(signals), 1)

        signal = signals[0]

        self.assertEqual(signal.category, "customer_value_decline")
        self.assertEqual(signal.customer_id, customer_id)
        self.assertEqual(signal.source_entity_type, "customer")
        self.assertEqual(signal.source_entity_id, customer_id)
        self.assertEqual(signal.currency, "USD")
        self.assertEqual(signal.priority, "high")
        self.assertIsNone(signal.estimated_value)

        evidence = signal.provenance[0]

        self.assertEqual(evidence["baseline_order_count"], 4)
        self.assertEqual(evidence["recent_order_count"], 1)
        self.assertEqual(evidence["baseline_net_revenue"], "1000.00")
        self.assertEqual(evidence["recent_net_revenue"], "250.00")
        self.assertEqual(evidence["comparison_window_days"], 28)
        self.assertEqual(
            evidence["purchase_count_decline_ratio"],
            "0.750",
        )
        self.assertIn(
            "not_predicted_clv",
            evidence["customer_value_scope"],
        )

        evidence_text = str(signal.provenance).lower()
        self.assertNotIn("@", evidence_text)
        self.assertNotIn("phone", evidence_text)
        self.assertNotIn("display_name", evidence_text)

        compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)

        self.assertIn("customers.active IS true", sql)
        self.assertIn("orders.customer_id IS NOT NULL", sql)
        self.assertIn("orders.provider_created_at", sql)
        self.assertIn("orders.created_at", sql)
        self.assertIn("orders.currency", sql)


    async def test_customer_value_decline_rejects_revenue_only_decline(self) -> None:
        customer_id = uuid4()

        rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                recent_order_count=4,
                baseline_order_count=4,
                recent_net_revenue=Decimal("300.00"),
                baseline_net_revenue=Decimal("1000.00"),
            )
        ]

        signals = await _detect_customer_value_declines(
            _Session(execute_values=[rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(signals, [])


    async def test_customer_value_decline_requires_meaningful_baseline(self) -> None:
        customer_id = uuid4()

        rows = [
            SimpleNamespace(
                customer_id=customer_id,
                currency="USD",
                recent_order_count=0,
                baseline_order_count=2,
                recent_net_revenue=Decimal("0.00"),
                baseline_net_revenue=Decimal("500.00"),
            )
        ]

        signals = await _detect_customer_value_declines(
            _Session(execute_values=[rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(signals, [])


    async def test_product_affinity_cross_sell_uses_support_confidence_and_lift(self) -> None:
        customer_id = uuid4()
        source_item_id = uuid4()
        target_item_id = uuid4()

        pair_rows = [
            SimpleNamespace(
                source_catalog_item_id=source_item_id,
                target_catalog_item_id=target_item_id,
                target_catalog_item_name="Complementary Product",
                target_availability="in_stock",
                co_purchase_order_count=4,
                source_order_count=10,
                target_order_count=8,
                eligible_order_count=40,
            )
        ]

        customer_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                source_purchase_count=3,
                last_source_purchase_at=datetime(
                    2026, 8, 20, 12, tzinfo=UTC
                ),
            )
        ]

        session = _Session(
            execute_values=[pair_rows, customer_rows]
        )

        signals = await _detect_product_affinity_cross_sell(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(len(signals), 1)

        signal = signals[0]

        self.assertEqual(
            signal.category,
            "product_affinity_cross_sell",
        )
        self.assertEqual(signal.customer_id, customer_id)
        self.assertEqual(
            signal.source_entity_type,
            "catalog_item",
        )
        self.assertEqual(
            signal.source_entity_id,
            target_item_id,
        )
        self.assertIsNone(signal.currency)
        self.assertIn(
            str(target_item_id),
            signal.dedupe_key,
        )

        evidence = signal.provenance[0]

        self.assertEqual(
            evidence["source_catalog_item_id"],
            str(source_item_id),
        )
        self.assertEqual(
            evidence["target_catalog_item_id"],
            str(target_item_id),
        )
        self.assertEqual(
            evidence["co_purchase_order_count"],
            4,
        )
        self.assertEqual(
            evidence["directional_confidence"],
            "0.400",
        )
        self.assertEqual(
            evidence["affinity_lift"],
            "2.000",
        )
        self.assertEqual(
            evidence["affinity_lookback_days"],
            180,
        )
        self.assertEqual(
            evidence["target_availability"],
            "in_stock",
        )
        self.assertIn(
            "external_variant_identity_excluded",
            evidence["product_identity_scope"],
        )
        self.assertIn(
            "no causal claim",
            evidence["affinity_disclaimer"],
        )

        evidence_text = str(signal.provenance).lower()
        self.assertNotIn("@", evidence_text)
        self.assertNotIn("phone", evidence_text)
        self.assertNotIn("display_name", evidence_text)

        pair_compiled = session.execute_statements[0].compile(
            dialect=postgresql.dialect()
        )
        pair_sql = str(pair_compiled)

        self.assertIn(
            "order_line_items.catalog_item_id",
            pair_sql,
        )
        self.assertIn(
            "catalog_items",
            pair_sql,
        )
        self.assertIn(
            "published IS true",
            pair_sql,
        )
        self.assertIn(
            "availability",
            pair_sql,
        )
        self.assertNotIn(
            "external_variant_id",
            pair_sql,
        )

        customer_compiled = session.execute_statements[1].compile(
            dialect=postgresql.dialect()
        )
        customer_sql = str(customer_compiled)

        self.assertIn(
            "EXISTS",
            customer_sql.upper(),
        )
        self.assertIn(
            "customers.active IS true",
            customer_sql,
        )
        self.assertIn(
            target_item_id,
            customer_compiled.params.values(),
        )


    async def test_product_affinity_cross_sell_rejects_weak_lift(self) -> None:
        pair_rows = [
            SimpleNamespace(
                source_catalog_item_id=uuid4(),
                target_catalog_item_id=uuid4(),
                target_catalog_item_name="Common Product",
                target_availability="in_stock",
                co_purchase_order_count=3,
                source_order_count=10,
                target_order_count=15,
                eligible_order_count=20,
            )
        ]

        session = _Session(execute_values=[pair_rows])

        signals = await _detect_product_affinity_cross_sell(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(signals, [])
        self.assertEqual(
            len(session.execute_statements),
            1,
        )


    async def test_product_affinity_cross_sell_cap_prefers_recent_customers(
        self,
    ) -> None:
        source_item_id = UUID(
            "f2000000-0000-4000-8000-000000000001"
        )
        target_item_id = UUID(
            "f2000000-0000-4000-8000-000000000002"
        )
        customer_ids = [
            UUID(f"f3000000-0000-4000-8000-{index:012d}")
            for index in range(1, 6)
        ]
        pair_rows = [
            SimpleNamespace(
                source_catalog_item_id=source_item_id,
                target_catalog_item_id=target_item_id,
                target_catalog_item_name="Complementary Product",
                target_availability="in_stock",
                co_purchase_order_count=4,
                source_order_count=10,
                target_order_count=5,
                eligible_order_count=20,
            )
        ]
        customer_rows = [
            SimpleNamespace(
                customer_id=customer_id,
                source_purchase_count=1,
                last_source_purchase_at=datetime(
                    2026,
                    8,
                    15 + index,
                    12,
                    tzinfo=UTC,
                ),
            )
            for index, customer_id in enumerate(customer_ids)
        ]

        signals = await _detect_product_affinity_cross_sell(
            _Session(execute_values=[pair_rows, customer_rows]),
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(len(signals), 4)
        self.assertEqual(
            [signal.customer_id for signal in signals],
            list(reversed(customer_ids[1:])),
        )
        self.assertNotIn(
            customer_ids[0],
            [signal.customer_id for signal in signals],
        )


    async def test_product_affinity_cross_sell_requires_meaningful_support(self) -> None:
        pair_rows = [
            SimpleNamespace(
                source_catalog_item_id=uuid4(),
                target_catalog_item_id=uuid4(),
                target_catalog_item_name="Sparse Product",
                target_availability="in_stock",
                co_purchase_order_count=1,
                source_order_count=5,
                target_order_count=3,
                eligible_order_count=20,
            )
        ]

        session = _Session(execute_values=[pair_rows])

        signals = await _detect_product_affinity_cross_sell(
            session,
            business_id=BUSINESS_ID,
            window=_window(),
        )

        self.assertEqual(signals, [])
        self.assertEqual(
            len(session.execute_statements),
            1,
        )


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

    async def test_opportunity_helper_persists_customer_and_estimated_value(self) -> None:
        opportunity_id = uuid4()
        customer_id = uuid4()
        session = _Session(scalar_values=[opportunity_id])

        created = await _create_opportunity_if_missing(
            session,
            business_id=BUSINESS_ID,
            dedupe_key="business-growth:customer-value:test:2026-08-24",
            title="Customer value opportunity",
            description="Observed customer purchase evidence.",
            category="customer_value_decline",
            source="commerce",
            source_entity_type=None,
            source_entity_id=None,
            reason="Deterministic customer evidence.",
            confidence=Decimal("0.850"),
            recommendation="Analyze the evidence.",
            provenance=[],
            customer_id=customer_id,
            estimated_value=Decimal("250.00"),
            currency="USD",
            suggested_action="analyze_business_opportunity",
        )

        self.assertTrue(created)

        compiled = session.scalar_statements[0].compile(
            dialect=postgresql.dialect()
        )
        self.assertEqual(compiled.params["customer_id"], customer_id)
        self.assertEqual(
            compiled.params["estimated_value"],
            Decimal("250.00"),
        )
        self.assertEqual(compiled.params["currency"], "USD")


    def test_global_growth_ranking_prioritizes_priority_before_score(self) -> None:
        medium = replace(
            _signal("medium"),
            priority="medium",
            rank_score=Decimal("0.990"),
            confidence=Decimal("0.990"),
        )
        high = replace(
            _signal("high"),
            priority="high",
            rank_score=Decimal("0.500"),
            confidence=Decimal("0.700"),
        )

        ranked = _rank_business_growth_signals(([medium, high],))

        self.assertEqual(
            [signal.dedupe_key for signal in ranked],
            [high.dedupe_key, medium.dedupe_key],
        )


    def test_global_growth_ranking_uses_score_then_confidence(self) -> None:
        lower_score = replace(
            _signal("lower-score"),
            priority="high",
            rank_score=Decimal("0.700"),
            confidence=Decimal("0.990"),
        )
        lower_confidence = replace(
            _signal("lower-confidence"),
            priority="high",
            rank_score=Decimal("0.800"),
            confidence=Decimal("0.750"),
        )
        higher_confidence = replace(
            _signal("higher-confidence"),
            priority="high",
            rank_score=Decimal("0.800"),
            confidence=Decimal("0.900"),
        )

        ranked = _rank_business_growth_signals(
            ([lower_score, lower_confidence, higher_confidence],)
        )

        self.assertEqual(
            [signal.dedupe_key for signal in ranked],
            [
                higher_confidence.dedupe_key,
                lower_confidence.dedupe_key,
                lower_score.dedupe_key,
            ],
        )


    def test_global_growth_ranking_does_not_compare_raw_currency_values(self) -> None:
        larger_money_lower_signal = replace(
            _signal("usd-large"),
            priority="medium",
            rank_score=Decimal("0.600"),
            confidence=Decimal("0.850"),
            estimated_value=Decimal("1000000.00"),
            currency="USD",
        )
        smaller_money_stronger_signal = replace(
            _signal("eur-small"),
            priority="medium",
            rank_score=Decimal("0.900"),
            confidence=Decimal("0.850"),
            estimated_value=Decimal("10.00"),
            currency="EUR",
        )

        ranked = _rank_business_growth_signals(
            ([larger_money_lower_signal, smaller_money_stronger_signal],)
        )

        self.assertEqual(
            ranked[0].dedupe_key,
            smaller_money_stronger_signal.dedupe_key,
        )


    def test_global_growth_ranking_is_bounded_to_twenty(self) -> None:
        detector_results = tuple(
            [
                replace(
                    _signal(f"{detector}-{index}"),
                    rank_score=Decimal("0.500")
                    + Decimal(detector * 4 + index) / Decimal("100"),
                    confidence=Decimal("0.800"),
                )
                for index in range(4)
            ]
            for detector in range(6)
        )

        ranked = _rank_business_growth_signals(detector_results)

        self.assertEqual(len(ranked), 20)
        scores = [signal.rank_score for signal in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))




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
