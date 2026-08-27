"""Real PostgreSQL acceptance for Phase 5 customer/product intelligence.

Covers:
- provider/manual authoritative timestamps
- tenant isolation and composite PostgreSQL ownership
- same-currency repeat/high-value analysis
- repeat-purchase cadence
- high-value customer cohort promotion
- customer value decline
- product-affinity support/confidence/lift
- sellability rejection for unavailable targets
- Opportunity persistence
- durable initial AI-analysis jobs
- zero external-action execution attempts

All fixture identifiers are unique and deleted in ``finally``.
No AI provider or external connector is invoked.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings
from app.db.session import AsyncSessionFactory, engine
from app.domain.background_jobs import initial_opportunity_analysis_job_key
from app.models.action_execution_attempt import ActionExecutionAttempt
from app.models.background_job import BackgroundJob
from app.models.business import Business
from app.models.catalog_item import CatalogItem
from app.models.customer import Customer
from app.models.order import Order, OrderLineItem
from app.models.opportunity import Opportunity
from app.services.marketing_automation import (
    _ComparisonWindow,
    _create_opportunity_if_missing,
    _detect_customer_value_declines,
    _detect_high_value_customer_at_risk,
    _detect_product_affinity_cross_sell,
    _detect_repeat_purchase_due,
)


ALLOWED_SMOKE_ENVIRONMENTS = {
    "development",
    "dev",
    "local",
    "test",
    "testing",
}


def _customer(
    *,
    business_id: UUID,
    label: str,
) -> Customer:
    return Customer(
        id=uuid4(),
        business_id=business_id,
        display_name=f"Phase 5 Smoke {label}",
        status="active",
        source="manual",
        active=True,
    )


def _product(
    *,
    business_id: UUID,
    suffix: str,
    label: str,
    availability: str = "in_stock",
    inventory_quantity: int | None = 100,
    published: bool = True,
    status: str = "active",
) -> CatalogItem:
    return CatalogItem(
        id=uuid4(),
        business_id=business_id,
        item_type="product",
        name=f"Phase 5 {label}",
        sku=f"p5-{label.casefold().replace(' ', '-')}-{suffix}",
        price=Decimal("100.00"),
        currency="USD",
        inventory_quantity=inventory_quantity,
        availability=availability,
        published=published,
        source="manual",
        sync_state="manual",
        status=status,
    )


def _order(
    *,
    business_id: UUID,
    customer_id: UUID,
    suffix: str,
    sequence: int,
    occurred_at: datetime | None,
    amount: Decimal,
    currency: str = "USD",
    source: str = "shopify",
) -> Order:
    values: dict[str, object] = {
        "id": uuid4(),
        "business_id": business_id,
        "customer_id": customer_id,
        "order_number": f"p5-{suffix}-{sequence:04d}",
        "status": "completed",
        "source": source,
        "currency": currency,
        "subtotal": amount,
        "adjustment_amount": Decimal("0.00"),
        "discount_amount": Decimal("0.00"),
        "tax_amount": Decimal("0.00"),
        "shipping_amount": Decimal("0.00"),
        "refunded_amount": Decimal("0.00"),
        "total": amount,
        "payment_status": "paid",
        "fulfillment_status": "fulfilled",
    }

    if source == "manual":
        if occurred_at is None:
            raise ValueError("manual_order_requires_created_at")
        values["created_at"] = occurred_at
        values["provider_created_at"] = None
    else:
        values["provider_created_at"] = occurred_at

    return Order(**values)


def _line(
    *,
    business_id: UUID,
    order_id: UUID,
    catalog_item_id: UUID,
    suffix: str,
    sequence: int,
    description: str,
    amount: Decimal = Decimal("100.00"),
) -> OrderLineItem:
    return OrderLineItem(
        id=uuid4(),
        business_id=business_id,
        order_id=order_id,
        catalog_item_id=catalog_item_id,
        description=description,
        quantity=1,
        unit_price=amount,
        external_object_id=f"p5-line-{suffix}-{sequence}-{uuid4().hex[:8]}",
        discount_amount=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
    )


async def _persist_signal(
    *,
    session,
    business_id: UUID,
    signal,
) -> bool:
    return await _create_opportunity_if_missing(
        session,
        business_id=business_id,
        dedupe_key=signal.dedupe_key,
        title=signal.title,
        description=signal.description,
        category=signal.category,
        source=signal.source,
        source_entity_type=signal.source_entity_type,
        source_entity_id=signal.source_entity_id,
        customer_id=signal.customer_id,
        reason=signal.reason,
        confidence=signal.confidence,
        recommendation=signal.recommendation,
        provenance=signal.provenance,
        estimated_value=signal.estimated_value,
        currency=signal.currency,
        priority=signal.priority,
        suggested_action="analyze_business_opportunity",
        enqueue_initial_analysis=True,
    )


def _assert_pii_free_provenance(*signals) -> None:
    forbidden_markers = (
        "display_name",
        "email",
        "phone",
        "address",
        "notes",
        "@",
    )

    for signal in signals:
        evidence_text = repr(signal.provenance).casefold()
        leaked = [
            marker
            for marker in forbidden_markers
            if marker in evidence_text
        ]
        if leaked:
            raise RuntimeError(
                f"PII marker leaked into {signal.category} provenance: {leaked}"
            )


async def main() -> None:
    environment = str(settings.environment).casefold()
    if environment not in ALLOWED_SMOKE_ENVIRONMENTS:
        raise RuntimeError(
            f"refusing Phase 5 PostgreSQL smoke in environment={environment!r}"
        )

    suffix = uuid4().hex[:8]
    business_a_id = uuid4()
    business_b_id = uuid4()

    now = datetime.now(UTC).replace(microsecond=0)

    window = _ComparisonWindow(
        baseline_start=now - timedelta(days=14),
        baseline_end=now - timedelta(days=7),
        recent_start=now - timedelta(days=7),
        recent_end=now,
        window_key=f"postgres-p5-{suffix}",
    )

    business_ids = (business_a_id, business_b_id)

    try:
        # ==================================================================
        # 1. Real tenant + customer + catalog fixtures
        # ==================================================================

        high_customer = _customer(
            business_id=business_a_id,
            label="High Value Customer",
        )
        repeat_customer = _customer(
            business_id=business_a_id,
            label="Repeat Customer",
        )
        decline_customer = _customer(
            business_id=business_a_id,
            label="Decline Customer",
        )
        affinity_customer = _customer(
            business_id=business_a_id,
            label="Affinity Customer",
        )

        cohort_customers = [
            _customer(
                business_id=business_a_id,
                label=f"Cohort {index}",
            )
            for index in range(12)
        ]

        affinity_other_customers = [
            _customer(
                business_id=business_a_id,
                label=f"Affinity Other {index}",
            )
            for index in range(20)
        ]

        foreign_customer = _customer(
            business_id=business_b_id,
            label="Foreign Tenant Customer",
        )

        source_product = _product(
            business_id=business_a_id,
            suffix=suffix,
            label="Source",
        )
        target_product = _product(
            business_id=business_a_id,
            suffix=suffix,
            label="Target",
            availability="in_stock",
            inventory_quantity=100,
        )
        unavailable_product = _product(
            business_id=business_a_id,
            suffix=suffix,
            label="Unavailable",
            availability="out_of_stock",
            inventory_quantity=0,
        )
        filler_product = _product(
            business_id=business_a_id,
            suffix=suffix,
            label="Filler",
        )

        foreign_product = _product(
            business_id=business_b_id,
            suffix=suffix,
            label="Foreign",
        )

        async with AsyncSessionFactory() as session:
            session.add_all([
                Business(
                    id=business_a_id,
                    name="Phase 5 PostgreSQL Smoke A",
                    slug=f"phase5-intelligence-smoke-a-{suffix}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                ),
                Business(
                    id=business_b_id,
                    name="Phase 5 PostgreSQL Smoke B",
                    slug=f"phase5-intelligence-smoke-b-{suffix}",
                    business_type="e-commerce",
                    status="active",
                    timezone="UTC",
                    currency="USD",
                    locale="en",
                ),
            ])
            await session.flush()

            session.add_all([
                high_customer,
                repeat_customer,
                decline_customer,
                affinity_customer,
                *cohort_customers,
                *affinity_other_customers,
                foreign_customer,
                source_product,
                target_product,
                unavailable_product,
                filler_product,
                foreign_product,
            ])
            await session.flush()

            orders: list[Order] = []
            lines: list[OrderLineItem] = []
            order_sequence = 1
            line_sequence = 1

            def add_order(
                *,
                customer_id: UUID,
                occurred_at: datetime | None,
                amount: Decimal,
                currency: str = "USD",
                source: str = "shopify",
                products: tuple[CatalogItem, ...] = (),
            ) -> Order:
                nonlocal order_sequence, line_sequence

                value = _order(
                    business_id=business_a_id,
                    customer_id=customer_id,
                    suffix=suffix,
                    sequence=order_sequence,
                    occurred_at=occurred_at,
                    amount=amount,
                    currency=currency,
                    source=source,
                )
                order_sequence += 1
                orders.append(value)

                for product in products:
                    lines.append(_line(
                        business_id=business_a_id,
                        order_id=value.id,
                        catalog_item_id=product.id,
                        suffix=suffix,
                        sequence=line_sequence,
                        description=product.name,
                        amount=amount,
                    ))
                    line_sequence += 1

                return value

            # --------------------------------------------------------------
            # Repeat cadence + high-value customer.
            # Four USD purchases, 14-day cadence, latest 76 days ago.
            # --------------------------------------------------------------
            for days_ago in (118, 104, 90, 76):
                add_order(
                    customer_id=high_customer.id,
                    occurred_at=now - timedelta(days=days_ago),
                    amount=Decimal("1000.00"),
                    currency="USD",
                )

            # Huge EUR purchase proves currency isolation.
            add_order(
                customer_id=high_customer.id,
                occurred_at=now - timedelta(days=30),
                amount=Decimal("50000.00"),
                currency="EUR",
            )

            # Provider order with missing provider timestamp must fail closed.
            add_order(
                customer_id=high_customer.id,
                occurred_at=None,
                amount=Decimal("99999.00"),
                currency="USD",
                source="shopify",
            )

            # Separate lower-value repeat customer so repeat_purchase_due can
            # remain a distinct accepted signal after high-value promotion.
            for days_ago in (80, 70, 60, 50):
                add_order(
                    customer_id=repeat_customer.id,
                    occurred_at=now - timedelta(days=days_ago),
                    amount=Decimal("40.00"),
                    currency="USD",
                )

            # --------------------------------------------------------------
            # Same-currency cohort for high-value percentile.
            # --------------------------------------------------------------
            for index, customer in enumerate(cohort_customers):
                add_order(
                    customer_id=customer.id,
                    occurred_at=now - timedelta(days=20 + index),
                    amount=Decimal(str(50 + index * 5)),
                    currency="USD",
                )

            # Foreign tenant has an enormous order; it must never affect A.
            foreign_order = _order(
                business_id=business_b_id,
                customer_id=foreign_customer.id,
                suffix=f"b{suffix}",
                sequence=1,
                occurred_at=now - timedelta(days=10),
                amount=Decimal("999999.00"),
                currency="USD",
                source="shopify",
            )

            # --------------------------------------------------------------
            # Customer value decline:
            # preceding 28 days: 4 x 250 = 1000
            # recent 28 days:    1 x 200 = 200
            # Manual source exercises created_at authority.
            # --------------------------------------------------------------
            for days_ago in (50, 45, 40, 35):
                add_order(
                    customer_id=decline_customer.id,
                    occurred_at=now - timedelta(days=days_ago),
                    amount=Decimal("250.00"),
                    source="manual",
                )

            add_order(
                customer_id=decline_customer.id,
                occurred_at=now - timedelta(days=2),
                amount=Decimal("200.00"),
                source="manual",
            )

            # --------------------------------------------------------------
            # Product affinity.
            #
            # 20 eligible catalog-bearing orders:
            # A appears in 10
            # B appears in 5
            # A+B appears in 4
            #
            # P(B|A) = 4/10 = .40
            # P(B)   = 5/20 = .25
            # lift   = 1.60
            #
            # Out-of-stock C mirrors strong co-purchase support but must
            # never become a target recommendation.
            # --------------------------------------------------------------
            affinity_orders: list[Order] = []

            for index in range(20):
                customer = (
                    affinity_customer
                    if index == 9
                    else affinity_other_customers[index]
                )

                if index < 4:
                    products = (
                        source_product,
                        target_product,
                        unavailable_product,
                    )
                elif index < 10:
                    products = (source_product,)
                elif index == 10:
                    products = (target_product,)
                elif index == 11:
                    products = (unavailable_product,)
                else:
                    products = (filler_product,)

                # Make designated A-only candidate the newest source-only
                # customer so it is deterministically selected first.
                days_ago = 1 if index == 9 else 30 + index

                order = add_order(
                    customer_id=customer.id,
                    occurred_at=now - timedelta(days=days_ago),
                    amount=Decimal("100.00"),
                    products=products,
                )
                affinity_orders.append(order)

            session.add_all(orders)
            session.add(foreign_order)
            await session.flush()

            foreign_line = _line(
                business_id=business_b_id,
                order_id=foreign_order.id,
                catalog_item_id=foreign_product.id,
                suffix=f"b{suffix}",
                sequence=1,
                description=foreign_product.name,
                amount=Decimal("999999.00"),
            )

            session.add_all([*lines, foreign_line])
            await session.commit()

            cross_tenant_order_id = affinity_orders[0].id

        print("FIXTURES: real PostgreSQL commerce rows committed")

        # ==================================================================
        # 2. PostgreSQL independently rejects cross-tenant catalog ownership
        # ==================================================================

        async with AsyncSessionFactory() as session:
            session.add(OrderLineItem(
                id=uuid4(),
                business_id=business_a_id,
                order_id=cross_tenant_order_id,
                catalog_item_id=foreign_product.id,
                description="Cross-tenant line must fail",
                quantity=1,
                unit_price=Decimal("1.00"),
                external_object_id=f"cross-tenant-{uuid4()}",
                discount_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
            ))

            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
            else:
                raise RuntimeError(
                    "PostgreSQL accepted cross-tenant catalog ownership"
                )

        print("TENANT FK: cross-tenant order/catalog line rejected")

        # ==================================================================
        # 3. Run all four real Phase 5 detectors
        # ==================================================================

        async with AsyncSessionFactory() as session:
            repeat_candidates = await _detect_repeat_purchase_due(
                session,
                business_id=business_a_id,
                window=window,
                limit=100,
            )

            high_value_signals = await _detect_high_value_customer_at_risk(
                session,
                business_id=business_a_id,
                window=window,
                cadence_candidates=repeat_candidates,
            )

            decline_signals = await _detect_customer_value_declines(
                session,
                business_id=business_a_id,
                window=window,
            )

            affinity_signals = await _detect_product_affinity_cross_sell(
                session,
                business_id=business_a_id,
                window=window,
            )

        repeat_signal = next(
            (
                signal
                for signal in repeat_candidates
                if signal.customer_id == repeat_customer.id
            ),
            None,
        )
        if repeat_signal is None:
            raise RuntimeError(
                "repeat_purchase_due did not detect the repeat fixture"
            )

        repeat_evidence = repeat_signal.provenance[0]
        if (
            repeat_signal.category != "repeat_purchase_due"
            or repeat_signal.customer_id != repeat_customer.id
            or repeat_signal.currency != "USD"
            or int(repeat_evidence["purchase_count"]) != 4
            or str(repeat_evidence["median_purchase_interval_days"])
            != "10.000"
            or str(repeat_evidence["purchase_overdue_ratio"]) != "5.000"
            or repeat_evidence["order_timestamp_policy"]
            != "manual_created_at_else_provider_created_at_required"
        ):
            raise RuntimeError(
                "repeat-purchase cadence/linkage evidence differed from fixture truth"
            )

        high_signal = next(
            (
                signal
                for signal in high_value_signals
                if signal.customer_id == high_customer.id
                and signal.currency == "USD"
            ),
            None,
        )
        if high_signal is None:
            raise RuntimeError(
                "high_value_customer_at_risk did not detect the high-value fixture"
            )

        high_evidence = high_signal.provenance[0]
        high_observed_value = str(
            high_evidence.get("observed_retained_revenue")
        )
        if (
            high_signal.category != "high_value_customer_at_risk"
            or high_signal.customer_id != high_customer.id
            or high_signal.currency != "USD"
            or high_observed_value not in {"4000", "4000.0", "4000.00"}
            or int(high_evidence["purchase_count"]) != 4
            or int(high_evidence["cohort_size"]) < 10
            or high_evidence["customer_value_scope"]
            != "observed_trailing_retained_revenue_not_predicted_clv"
        ):
            raise RuntimeError(
                "high-value same-currency observed evidence differed from fixture truth"
            )

        if any(
            signal.customer_id == foreign_customer.id
            for signal in (
                repeat_candidates
                + high_value_signals
                + decline_signals
                + affinity_signals
            )
        ):
            raise RuntimeError(
                "tenant B customer leaked into tenant A intelligence"
            )

        decline_signal = next(
            (
                signal
                for signal in decline_signals
                if signal.customer_id == decline_customer.id
            ),
            None,
        )
        if decline_signal is None:
            raise RuntimeError(
                "customer_value_decline did not detect the manual-order fixture"
            )

        decline_evidence = decline_signal.provenance[0]
        if (
            decline_signal.category != "customer_value_decline"
            or decline_signal.customer_id != decline_customer.id
            or decline_signal.currency != "USD"
            or int(decline_evidence["baseline_order_count"]) != 4
            or int(decline_evidence["recent_order_count"]) != 1
            or str(decline_evidence["baseline_net_revenue"])
            not in {"1000", "1000.0", "1000.00"}
            or str(decline_evidence["recent_net_revenue"])
            not in {"200", "200.0", "200.00"}
            or str(decline_evidence["decline_ratio"]) != "0.800"
            or str(decline_evidence["purchase_count_decline_ratio"])
            != "0.750"
            or decline_evidence["customer_value_scope"]
            != "observed_comparable_retained_revenue_not_predicted_clv"
        ):
            raise RuntimeError(
                "manual timestamp customer-value evidence differed from fixture truth"
            )

        decline_claims = " ".join((
            decline_signal.description,
            decline_signal.reason,
        )).casefold()
        if "is predicted" in decline_claims or "will churn" in decline_claims:
            raise RuntimeError(
                "customer-value decline made an unsupported predictive claim"
            )

        affinity_signal = next(
            (
                signal
                for signal in affinity_signals
                if (
                    signal.customer_id == affinity_customer.id
                    and signal.source_entity_id == target_product.id
                )
            ),
            None,
        )
        if affinity_signal is None:
            raise RuntimeError(
                "product_affinity_cross_sell did not detect the A->B fixture; "
                "observed="
                + repr([
                    {
                        "customer_id": str(signal.customer_id),
                        "target_id": str(signal.source_entity_id),
                        "evidence": signal.provenance[0]
                        if signal.provenance
                        else None,
                    }
                    for signal in affinity_signals
                ])
            )

        affinity_evidence = affinity_signal.provenance[0]
        if (
            affinity_signal.category != "product_affinity_cross_sell"
            or affinity_signal.customer_id != affinity_customer.id
            or affinity_signal.source_entity_type != "catalog_item"
            or affinity_signal.source_entity_id != target_product.id
            or int(affinity_evidence["eligible_order_count"]) != 20
            or int(affinity_evidence["source_order_count"]) != 10
            or int(affinity_evidence["target_order_count"]) != 5
            or int(affinity_evidence["co_purchase_order_count"]) != 4
            or str(affinity_evidence["directional_confidence"]) != "0.400"
            or str(affinity_evidence["affinity_lift"]) != "1.600"
            or affinity_evidence["product_identity_scope"]
            != "tenant_catalog_item_id_only;external_variant_identity_excluded"
            or "no causal claim"
            not in str(affinity_evidence["affinity_disclaimer"]).casefold()
        ):
            raise RuntimeError(
                "product affinity support/confidence/lift or identity evidence "
                "differed from fixture truth"
            )

        if any(
            signal.source_entity_id == unavailable_product.id
            for signal in affinity_signals
        ):
            raise RuntimeError(
                "out-of-stock product became a cross-sell target"
            )

        if any(
            signal.source_entity_id == foreign_product.id
            for signal in affinity_signals
        ):
            raise RuntimeError(
                "tenant B catalog item leaked into tenant A affinity"
            )

        _assert_pii_free_provenance(
            repeat_signal,
            high_signal,
            decline_signal,
            affinity_signal,
        )

        print("DETECTOR: repeat_purchase_due PASSED")
        print("DETECTOR: high_value_customer_at_risk PASSED")
        print("  - same-currency USD cohort respected")
        print("  - missing provider timestamp ignored")
        print("DETECTOR: customer_value_decline PASSED")
        print("  - manual created_at authority respected")
        print("DETECTOR: product_affinity_cross_sell PASSED")
        print("  - exact support 4/10/5/20, confidence 0.400, lift 1.600")
        print("  - unavailable target rejected")

        # ==================================================================
        # 4. Persist canonical Opportunities + durable analysis jobs
        # ==================================================================

        signals_to_persist = (
            repeat_signal,
            high_signal,
            decline_signal,
            affinity_signal,
        )

        async with AsyncSessionFactory() as session:
            created = []

            for signal in signals_to_persist:
                created.append(await _persist_signal(
                    session=session,
                    business_id=business_a_id,
                    signal=signal,
                ))

            duplicate_results = []
            for signal in signals_to_persist:
                duplicate_results.append(await _persist_signal(
                    session=session,
                    business_id=business_a_id,
                    signal=signal,
                ))

            await session.commit()

            if created != [True, True, True, True]:
                raise RuntimeError(
                    f"canonical Opportunity persistence failed: {created}"
                )
            if duplicate_results != [False, False, False, False]:
                raise RuntimeError(
                    "duplicate Opportunity persistence was not suppressed: "
                    f"{duplicate_results}"
                )

        async with AsyncSessionFactory() as session:
            opportunities = list((await session.scalars(
                select(Opportunity).where(
                    Opportunity.business_id == business_a_id,
                    Opportunity.category.in_((
                        "repeat_purchase_due",
                        "high_value_customer_at_risk",
                        "customer_value_decline",
                        "product_affinity_cross_sell",
                    )),
                )
            )).all())

            fixture_dedupe_keys = {
                signal.dedupe_key
                for signal in signals_to_persist
            }

            opportunities = [
                opportunity
                for opportunity in opportunities
                if opportunity.dedupe_key in fixture_dedupe_keys
            ]

            if len(opportunities) != 4:
                raise RuntimeError(
                    f"expected 4 persisted Opportunities, got {len(opportunities)}"
                )

            signals_by_key = {
                signal.dedupe_key: signal
                for signal in signals_to_persist
            }
            for opportunity in opportunities:
                expected = signals_by_key[opportunity.dedupe_key]
                if (
                    opportunity.business_id != business_a_id
                    or opportunity.customer_id != expected.customer_id
                    or opportunity.source_entity_type
                    != expected.source_entity_type
                    or opportunity.source_entity_id
                    != expected.source_entity_id
                    or opportunity.provenance != expected.provenance
                    or opportunity.category != expected.category
                    or opportunity.status != "open"
                    or opportunity.suggested_action
                    != "analyze_business_opportunity"
                ):
                    raise RuntimeError(
                        "persisted Opportunity fields differed from detector signal"
                    )

            opportunity_ids = {
                opportunity.id
                for opportunity in opportunities
            }

            jobs = list((await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.business_id == business_a_id,
                    BackgroundJob.opportunity_id.in_(opportunity_ids),
                    BackgroundJob.job_type
                    == "analyze_business_opportunity",
                )
            )).all())

            if len(jobs) != 4:
                raise RuntimeError(
                    f"expected 4 durable analysis jobs, got {len(jobs)}"
                )

            if any(
                job.business_id != business_a_id
                or job.status != "queued"
                or job.opportunity_id not in opportunity_ids
                or job.attempt_count != 0
                or job.idempotency_key
                != initial_opportunity_analysis_job_key(job.opportunity_id)
                for job in jobs
            ):
                raise RuntimeError(
                    "analysis job typed references/lifecycle were invalid"
                )

            attempts = int(await session.scalar(
                select(func.count())
                .select_from(ActionExecutionAttempt)
                .where(
                    ActionExecutionAttempt.business_id == business_a_id
                )
            ) or 0)

            dispatch_jobs = int(await session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.business_id == business_a_id,
                    BackgroundJob.job_type
                    == "dispatch_action_execution",
                )
            ) or 0)

            if attempts != 0 or dispatch_jobs != 0:
                raise RuntimeError(
                    "Phase 5 intelligence crossed the external execution boundary"
                )

        print("PERSISTENCE: 4 canonical Opportunities PASSED")
        print("JOBS: 4 durable AI-analysis jobs PASSED")
        print("EXECUTION BOUNDARY: zero attempts / zero dispatch jobs PASSED")

        print()
        print(
            "PHASE 5 CUSTOMER/PRODUCT INTELLIGENCE "
            "POSTGRESQL SMOKE TEST PASSED"
        )
        print("  - tenant isolation: real PostgreSQL + detector scope")
        print("  - repeat cadence: accepted")
        print("  - high-value observed cohort: accepted")
        print("  - customer value decline: accepted")
        print("  - product affinity cross-sell: accepted")
        print("  - sellability rejection: accepted")
        print("  - Opportunity persistence: accepted")
        print("  - durable analysis enqueue: accepted")
        print("  - external execution: zero")

    finally:
        # Explicit cleanup keeps this safe even if a smoke assertion fails
        # after fixtures have been committed.
        try:
            async with AsyncSessionFactory() as session:
                await session.execute(delete(BackgroundJob).where(
                    BackgroundJob.business_id.in_(business_ids)
                ))
                await session.execute(delete(Opportunity).where(
                    Opportunity.business_id.in_(business_ids)
                ))
                await session.execute(delete(OrderLineItem).where(
                    OrderLineItem.business_id.in_(business_ids)
                ))
                await session.execute(delete(Order).where(
                    Order.business_id.in_(business_ids)
                ))
                await session.execute(delete(CatalogItem).where(
                    CatalogItem.business_id.in_(business_ids)
                ))
                await session.execute(delete(Customer).where(
                    Customer.business_id.in_(business_ids)
                ))
                await session.execute(delete(Business).where(
                    Business.id.in_(business_ids)
                ))
                await session.commit()

                remaining_counts = {
                    "businesses": int(await session.scalar(
                        select(func.count())
                        .select_from(Business)
                        .where(Business.id.in_(business_ids))
                    ) or 0),
                    "customers": int(await session.scalar(
                        select(func.count())
                        .select_from(Customer)
                        .where(Customer.business_id.in_(business_ids))
                    ) or 0),
                    "catalog_items": int(await session.scalar(
                        select(func.count())
                        .select_from(CatalogItem)
                        .where(CatalogItem.business_id.in_(business_ids))
                    ) or 0),
                    "orders": int(await session.scalar(
                        select(func.count())
                        .select_from(Order)
                        .where(Order.business_id.in_(business_ids))
                    ) or 0),
                    "order_line_items": int(await session.scalar(
                        select(func.count())
                        .select_from(OrderLineItem)
                        .where(OrderLineItem.business_id.in_(business_ids))
                    ) or 0),
                    "opportunities": int(await session.scalar(
                        select(func.count())
                        .select_from(Opportunity)
                        .where(Opportunity.business_id.in_(business_ids))
                    ) or 0),
                    "background_jobs": int(await session.scalar(
                        select(func.count())
                        .select_from(BackgroundJob)
                        .where(BackgroundJob.business_id.in_(business_ids))
                    ) or 0),
                }
                if any(remaining_counts.values()):
                    raise RuntimeError(
                        "Phase 5 smoke cleanup left fixture rows: "
                        f"{remaining_counts}"
                    )

            print("CLEANUP: Phase 5 PostgreSQL fixture counts verified zero")
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
