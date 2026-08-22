from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4


# Allow this script to run directly with:
# uv run python scripts/smoke_ai_context_postgres.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.models.business import Business
from app.models.business_branding import BusinessBranding
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.catalog_item import CatalogItem
from app.schemas.ai_context import AIContextRequest
from app.schemas.business_memory import BusinessMemoryCreate
from app.services.ai_context import assemble_ai_context
from app.services.business_memory import (
    create_manual_memory,
    create_system_memory,
)


ALLOWED_SMOKE_ENVIRONMENTS = {
    "development",
    "dev",
    "local",
    "test",
    "testing",
}


def _assert_safe_environment() -> None:
    environment = settings.environment.strip().lower()

    if environment not in ALLOWED_SMOKE_ENVIRONMENTS:
        raise RuntimeError(
            "Refusing to run AI Context PostgreSQL smoke test outside "
            "development/test environment. "
            f"Current environment: {settings.environment!r}"
        )


def _business(
    *,
    name: str,
    slug: str,
) -> Business:
    return Business(
        name=name,
        slug=slug,
        business_type="farm",
        status="active",
        timezone="Asia/Karachi",
        currency="PKR",
        locale="en-PK",
    )


async def run_smoke_test() -> None:
    _assert_safe_environment()

    suffix = uuid4().hex[:12]

    business_a = _business(
        name=f"AI Context Farm A {suffix}",
        slug=f"ai-context-farm-a-{suffix}",
    )

    business_b = _business(
        name=f"AI Context Farm B {suffix}",
        slug=f"ai-context-farm-b-{suffix}",
    )

    async with AsyncSessionFactory() as session:
        try:
            # ---------------------------------------------------------
            # 1. Create two real tenants
            # ---------------------------------------------------------
            session.add_all(
                [
                    business_a,
                    business_b,
                ]
            )

            await session.flush()

            assert business_a.id is not None
            assert business_b.id is not None
            assert business_a.id != business_b.id

            # ---------------------------------------------------------
            # 2. Tenant A branding
            # ---------------------------------------------------------
            branding_a = BusinessBranding(
                business_id=business_a.id,
                primary_color="#0F5132",
                secondary_color="#E7F4EA",
                accent_color="#F4A261",
                logo_url="/api/v1/media/context-smoke-logo",
                logo_storage_key="internal/context-smoke-logo",
            )

            session.add(branding_a)

            # ---------------------------------------------------------
            # 3. Tenant A catalog
            # ---------------------------------------------------------
            active_product = CatalogItem(
                business_id=business_a.id,
                item_type="product",
                name="Premium Fresh Milk",
                description="Fresh farm milk delivered daily.",
                sku="MILK-CONTEXT-001",
                price=Decimal("500.00"),
                status="active",
            )

            active_service = CatalogItem(
                business_id=business_a.id,
                item_type="service",
                name="Farm Consultation",
                description="One-hour agricultural consultation.",
                sku="CONSULT-CONTEXT-001",
                price=Decimal("2500.00"),
                status="active",
            )

            archived_product = CatalogItem(
                business_id=business_a.id,
                item_type="product",
                name="Archived Product Should Never Appear",
                description="Historical product.",
                sku="ARCHIVED-CONTEXT-001",
                price=Decimal("100.00"),
                status="archived",
            )

            # Tenant B catalog must never leak.
            tenant_b_product = CatalogItem(
                business_id=business_b.id,
                item_type="product",
                name="TENANT B PRIVATE PRODUCT",
                description="This must never appear in Tenant A context.",
                sku="TENANT-B-PRIVATE",
                price=Decimal("999.00"),
                status="active",
            )

            session.add_all(
                [
                    active_product,
                    active_service,
                    archived_product,
                    tenant_b_product,
                ]
            )

            # ---------------------------------------------------------
            # 4. Tenant A curated Business Brain knowledge
            # ---------------------------------------------------------
            active_policy = BusinessKnowledgeEntry(
                business_id=business_a.id,
                category="policy",
                title="Delivery policy",
                content=(
                    "Same-day delivery is available within the local service area."
                ),
                status="active",
                source_type="manual",
                source_reference=None,
            )

            active_sales_knowledge = BusinessKnowledgeEntry(
                business_id=business_a.id,
                category="sales",
                title="Sales guidance",
                content=(
                    "Recommend subscription delivery for recurring milk customers."
                ),
                status="active",
                source_type="manual",
                source_reference=None,
            )

            archived_knowledge = BusinessKnowledgeEntry(
                business_id=business_a.id,
                category="general",
                title="Archived Knowledge Should Never Appear",
                content="Historical information.",
                status="archived",
                source_type="manual",
                source_reference=None,
            )

            tenant_b_knowledge = BusinessKnowledgeEntry(
                business_id=business_b.id,
                category="general",
                title="TENANT B PRIVATE KNOWLEDGE",
                content=(
                    "This private Tenant B knowledge must never appear "
                    "inside Tenant A context."
                ),
                status="active",
                source_type="manual",
                source_reference=None,
            )

            session.add_all(
                [
                    active_policy,
                    active_sales_knowledge,
                    archived_knowledge,
                    tenant_b_knowledge,
                ]
            )

            await session.flush()

            # ---------------------------------------------------------
            # 5. Tenant A persistent memories
            # ---------------------------------------------------------
            customer_memory = await create_manual_memory(
                session,
                business_a.id,
                BusinessMemoryCreate(
                    memory_type="customer",
                    content=(
                        "Recurring customers prefer WhatsApp reminders "
                        "before delivery."
                    ),
                    importance=5,
                    occurred_at=datetime.now(UTC),
                ),
            )

            decision_memory = await create_system_memory(
                session,
                business_a.id,
                memory_type="decision",
                content=(
                    "Prioritize subscription offers for customers "
                    "ordering milk weekly."
                ),
                confidence=Decimal("0.920"),
                source_reference="agent:sales:ai-context-smoke",
                importance=4,
                occurred_at=datetime.now(UTC),
            )

            low_importance_memory = await create_system_memory(
                session,
                business_a.id,
                memory_type="ai_learning",
                content=(
                    "Low-priority observation should be excluded "
                    "by importance threshold."
                ),
                confidence=Decimal("0.950"),
                source_reference="agent:analytics:low-priority",
                importance=1,
            )

            low_confidence_memory = await create_system_memory(
                session,
                business_a.id,
                memory_type="semantic",
                content=(
                    "Low-confidence fact should be excluded "
                    "by confidence threshold."
                ),
                confidence=Decimal("0.300"),
                source_reference="agent:analytics:low-confidence",
                importance=5,
            )

            # Tenant B memory must never leak.
            tenant_b_memory = await create_system_memory(
                session,
                business_b.id,
                memory_type="customer",
                content="TENANT B PRIVATE MEMORY",
                confidence=Decimal("1.000"),
                source_reference="agent:private:tenant-b",
                importance=5,
            )

            assert customer_memory.business_id == business_a.id
            assert decision_memory.business_id == business_a.id
            assert tenant_b_memory.business_id == business_b.id

            # ---------------------------------------------------------
            # 6. Assemble complete Tenant A context
            # ---------------------------------------------------------
            request = AIContextRequest(
                purpose="sales",
                task=(
                    "Prepare trusted context for following up with "
                    "a recurring milk customer."
                ),
                include_business_brain=True,
                include_memory=True,
                memory_types=[
                    "customer",
                    "decision",
                    "semantic",
                    "ai_learning",
                ],
                min_memory_importance=3,
                min_memory_confidence=Decimal("0.800"),
                brain_source_limit=100,
                memory_limit=50,
            )

            first_bundle = await assemble_ai_context(
                session,
                business_a.id,
                request,
            )

            # ---------------------------------------------------------
            # 7. Validate bundle identity + counts
            # ---------------------------------------------------------
            assert first_bundle.business_id == business_a.id
            assert first_bundle.purpose == "sales"
            assert first_bundle.task == request.task

            assert first_bundle.source_count == len(
                first_bundle.sources
            )

            assert (
                first_bundle.business_brain_source_count
                + first_bundle.memory_source_count
                == first_bundle.source_count
            )

            assert first_bundle.business_brain_source_count > 0
            assert first_bundle.memory_source_count == 2

            assert len(first_bundle.revision) == 64

            # ---------------------------------------------------------
            # 8. Verify authoritative Business Brain content
            # ---------------------------------------------------------
            brain_sources = [
                source
                for source in first_bundle.sources
                if source.origin == "business_brain"
            ]

            brain_text = "\n".join(
                source.content
                for source in brain_sources
            )

            brain_titles = {
                source.title
                for source in brain_sources
            }

            assert "Business profile" in brain_titles
            assert "Branding" in brain_titles
            assert "Premium Fresh Milk" in brain_titles
            assert "Farm Consultation" in brain_titles
            assert "Delivery policy" in brain_titles
            assert "Sales guidance" in brain_titles

            assert (
                "Archived Product Should Never Appear"
                not in brain_text
            )

            assert (
                "Archived Knowledge Should Never Appear"
                not in brain_text
            )

            assert "TENANT B PRIVATE PRODUCT" not in brain_text
            assert "TENANT B PRIVATE KNOWLEDGE" not in brain_text

            # Internal branding storage key must never enter context.
            assert "internal/context-smoke-logo" not in brain_text

            # ---------------------------------------------------------
            # 9. Verify selected Persistent Memory content
            # ---------------------------------------------------------
            memory_sources = [
                source
                for source in first_bundle.sources
                if source.origin == "business_memory"
            ]

            memory_ids = {
                source.memory_id
                for source in memory_sources
            }

            memory_text = "\n".join(
                source.content
                for source in memory_sources
            )

            assert customer_memory.id in memory_ids
            assert decision_memory.id in memory_ids

            assert low_importance_memory.id not in memory_ids
            assert low_confidence_memory.id not in memory_ids
            assert tenant_b_memory.id not in memory_ids

            assert "TENANT B PRIVATE MEMORY" not in memory_text

            # ---------------------------------------------------------
            # 10. Verify no internal memory provenance leakage
            # ---------------------------------------------------------
            serialized_memory_sources = [
                source.model_dump()
                for source in memory_sources
            ]

            for serialized in serialized_memory_sources:
                assert "source_reference" not in serialized
                assert "source_type" not in serialized
                assert "status" not in serialized

            # ---------------------------------------------------------
            # 11. Verify exact tenant identity on every runtime source
            # ---------------------------------------------------------
            assert all(
                source.business_id == business_a.id
                for source in first_bundle.sources
            )

            # ---------------------------------------------------------
            # 12. Deterministic revision for identical context
            # ---------------------------------------------------------
            second_bundle = await assemble_ai_context(
                session,
                business_a.id,
                request,
            )

            assert (
                second_bundle.revision
                == first_bundle.revision
            )

            assert (
                [
                    (
                        source.origin,
                        getattr(
                            source,
                            "source_id",
                            getattr(
                                source,
                                "memory_id",
                                None,
                            ),
                        ),
                    )
                    for source in second_bundle.sources
                ]
                == [
                    (
                        source.origin,
                        getattr(
                            source,
                            "source_id",
                            getattr(
                                source,
                                "memory_id",
                                None,
                            ),
                        ),
                    )
                    for source in first_bundle.sources
                ]
            )

            # ---------------------------------------------------------
            # 13. Verify Business-Brain-only context
            # ---------------------------------------------------------
            brain_only = await assemble_ai_context(
                session,
                business_a.id,
                AIContextRequest(
                    purpose="marketing",
                    task="Prepare authoritative marketing context.",
                    include_business_brain=True,
                    include_memory=False,
                    brain_source_types=[
                        "business_profile",
                        "branding",
                        "catalog_item",
                    ],
                ),
            )

            assert brain_only.memory_source_count == 0

            assert all(
                source.origin == "business_brain"
                for source in brain_only.sources
            )

            assert all(
                source.source_type
                in {
                    "business_profile",
                    "branding",
                    "catalog_item",
                }
                for source in brain_only.sources
            )

            # ---------------------------------------------------------
            # 14. Verify Memory-only context
            # ---------------------------------------------------------
            memory_only = await assemble_ai_context(
                session,
                business_a.id,
                AIContextRequest(
                    purpose="support",
                    task="Prepare customer memories.",
                    include_business_brain=False,
                    include_memory=True,
                    memory_types=[
                        "customer",
                    ],
                    min_memory_importance=1,
                    min_memory_confidence=Decimal("0.000"),
                ),
            )

            assert memory_only.business_brain_source_count == 0
            assert memory_only.memory_source_count == 1

            assert all(
                source.origin == "business_memory"
                for source in memory_only.sources
            )

            assert memory_only.sources[0].memory_id == customer_memory.id

            # ---------------------------------------------------------
            # 15. Verify Tenant B independently assembles only Tenant B data
            # ---------------------------------------------------------
            tenant_b_bundle = await assemble_ai_context(
                session,
                business_b.id,
                AIContextRequest(
                    purpose="general",
                    task="Prepare Tenant B context.",
                ),
            )

            assert tenant_b_bundle.business_id == business_b.id

            assert all(
                source.business_id == business_b.id
                for source in tenant_b_bundle.sources
            )

            tenant_b_text = "\n".join(
                source.content
                for source in tenant_b_bundle.sources
            )

            assert "TENANT B PRIVATE PRODUCT" in tenant_b_text
            assert "TENANT B PRIVATE KNOWLEDGE" in tenant_b_text
            assert "TENANT B PRIVATE MEMORY" in tenant_b_text

            assert "Premium Fresh Milk" not in tenant_b_text
            assert (
                "Recurring customers prefer WhatsApp reminders"
                not in tenant_b_text
            )

            print()
            print(
                "AI Context PostgreSQL smoke test PASSED"
            )
            print()
            print("Verified:")
            print(
                "  ✓ Business Profile → AI Context"
            )
            print(
                "  ✓ Branding → AI Context"
            )
            print(
                "  ✓ active Catalog → AI Context"
            )
            print(
                "  ✓ active Knowledge → AI Context"
            )
            print(
                "  ✓ Persistent Memory → AI Context"
            )
            print(
                "  ✓ memory importance threshold"
            )
            print(
                "  ✓ memory confidence threshold"
            )
            print(
                "  ✓ archived Business Brain sources excluded"
            )
            print(
                "  ✓ internal provenance/storage metadata excluded"
            )
            print(
                "  ✓ Tenant A / Tenant B isolation"
            )
            print(
                "  ✓ Business-Brain-only assembly"
            )
            print(
                "  ✓ Memory-only assembly"
            )
            print(
                "  ✓ deterministic context revision"
            )
            print(
                "  ✓ automatic rollback / no permanent smoke data"
            )

        finally:
            # The entire smoke test is intentionally transactional.
            # No smoke businesses, catalog, knowledge, branding, or
            # memories remain after the script completes.
            await session.rollback()


async def main() -> None:
    await run_smoke_test()


if __name__ == "__main__":
    asyncio.run(main())