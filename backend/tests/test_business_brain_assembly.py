import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.business_brain import BusinessBrainAssemblyError
from app.models.business import Business
from app.models.business_branding import BusinessBranding
from app.models.business_knowledge_entry import BusinessKnowledgeEntry
from app.models.catalog_item import CatalogItem
from app.services.business_brain_assembly import (
    BUSINESS_BRAIN_SOURCE_TYPES,
    MAX_SOURCE_BATCH_SIZE,
    build_branding_source,
    build_business_brain_manifest,
    build_business_profile_source,
    build_catalog_source,
    build_knowledge_source,
    iterate_business_brain_sources,
)

BUSINESS_A_ID = UUID("51000000-0000-0000-0000-000000000001")
BUSINESS_B_ID = UUID("52000000-0000-0000-0000-000000000002")
BASE_TIME = datetime(2026, 8, 20, tzinfo=UTC)


class BusinessBrainSourceBuilderTests(unittest.TestCase):
    def test_stable_namespaced_ids_and_explicit_business_id(self) -> None:
        business = _business(BUSINESS_A_ID)
        branding = _branding(BUSINESS_A_ID)
        catalog = _catalog(BUSINESS_A_ID, 1, name="Milk")
        knowledge = _knowledge(BUSINESS_A_ID, 2, title="Returns")

        sources = (
            build_business_profile_source(business),
            build_branding_source(BUSINESS_A_ID, branding),
            build_catalog_source(BUSINESS_A_ID, "PKR", catalog),
            build_knowledge_source(BUSINESS_A_ID, knowledge),
        )

        self.assertEqual(
            [source.source_id for source in sources if source is not None],
            [
                "business:profile",
                "business:branding",
                f"catalog:{catalog.id}",
                f"knowledge:{knowledge.id}",
            ],
        )
        self.assertTrue(
            all(
                source.business_id == BUSINESS_A_ID
                for source in sources
                if source is not None
            )
        )

    def test_runtime_source_is_immutable(self) -> None:
        source = build_business_profile_source(_business(BUSINESS_A_ID))
        with self.assertRaises(FrozenInstanceError):
            source.title = "Changed"  # type: ignore[misc]

    def test_content_hash_is_deterministic_lowercase_sha256(self) -> None:
        first = build_business_profile_source(_business(BUSINESS_A_ID))
        second = build_business_profile_source(_business(BUSINESS_A_ID))
        changed = build_business_profile_source(
            _business(BUSINESS_A_ID, name="Changed Business")
        )

        self.assertEqual(first.content_hash, second.content_hash)
        self.assertNotEqual(first.content_hash, changed.content_hash)
        self.assertRegex(first.content_hash, r"^[0-9a-f]{64}$")

    def test_individual_hashes_do_not_depend_on_collection_order(self) -> None:
        first_item = _catalog(BUSINESS_A_ID, 1, name="First")
        second_item = _catalog(BUSINESS_A_ID, 2, name="Second")
        forward = [
            build_catalog_source(BUSINESS_A_ID, "USD", item)
            for item in (first_item, second_item)
        ]
        reverse = [
            build_catalog_source(BUSINESS_A_ID, "USD", item)
            for item in (second_item, first_item)
        ]
        self.assertEqual(
            {source.source_id: source.content_hash for source in forward},
            {source.source_id: source.content_hash for source in reverse},
        )

    def test_business_profile_uses_only_stable_authoritative_fields(self) -> None:
        business = _business(BUSINESS_A_ID)
        source = build_business_profile_source(business)

        self.assertEqual(source.updated_at, business.updated_at)
        for label in ("Name:", "Business type:", "Timezone:", "Currency:", "Locale:"):
            self.assertIn(label, source.content)
        for forbidden in ("status", "created_at", "membership", "user", "secret"):
            self.assertNotIn(forbidden, source.content.lower())

    def test_meaningful_branding_is_compact_and_excludes_storage_key(self) -> None:
        branding = _branding(BUSINESS_A_ID)
        source = build_branding_source(BUSINESS_A_ID, branding)

        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.source_id, "business:branding")
        self.assertIn("Primary color: #112233", source.content)
        self.assertIn("Logo URL: /api/v1/media/public-logo", source.content)
        self.assertNotIn("private/storage/key", source.content)
        self.assertNotIn("logo_storage_key", source.content)

    def test_absent_or_empty_branding_does_not_fabricate_source(self) -> None:
        empty = _branding(
            BUSINESS_A_ID,
            primary_color=None,
            secondary_color=None,
            accent_color=None,
            logo_url=None,
        )
        empty.logo_storage_key = "internal-only/key"
        self.assertIsNone(build_branding_source(BUSINESS_A_ID, None))
        self.assertIsNone(build_branding_source(BUSINESS_A_ID, empty))

    def test_catalog_representation_is_decimal_safe_and_compact(self) -> None:
        product = _catalog(
            BUSINESS_A_ID,
            1,
            name="Fresh Milk",
            item_type="product",
            price=Decimal("500.00"),
            sku="MILK-001",
            description="Fresh daily.",
        )
        service = _catalog(
            BUSINESS_A_ID,
            2,
            name="Consultation",
            item_type="service",
            price=Decimal("19.90"),
        )

        product_source = build_catalog_source(BUSINESS_A_ID, "PKR", product)
        service_source = build_catalog_source(BUSINESS_A_ID, "USD", service)

        self.assertIn("Type: Product", product_source.content)
        self.assertIn("Price: 500.00 PKR", product_source.content)
        self.assertIn("Price: 19.90 USD", service_source.content)
        self.assertNotIn("inventory", product_source.content.lower())
        self.assertNotIn("stock", product_source.content.lower())

    def test_null_catalog_fields_have_stable_omission(self) -> None:
        item = _catalog(
            BUSINESS_A_ID,
            1,
            sku=None,
            price=None,
            description=None,
        )
        first = build_catalog_source(BUSINESS_A_ID, "USD", item)
        second = build_catalog_source(BUSINESS_A_ID, "USD", item)
        self.assertNotIn("SKU:", first.content)
        self.assertNotIn("Price:", first.content)
        self.assertNotIn("Description:", first.content)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_knowledge_preserves_authored_content_and_safe_metadata(self) -> None:
        authored = "First line.\n  Indented second line.\nFinal line."
        entry = _knowledge(
            BUSINESS_A_ID,
            1,
            category="policy",
            title="Returns",
            content=authored,
        )
        entry.source_reference = "internal:reference"
        source = build_knowledge_source(BUSINESS_A_ID, entry)

        self.assertIn("Category: Policy", source.content)
        self.assertIn("Title: Returns", source.content)
        self.assertTrue(source.content.endswith(authored))
        for forbidden in (
            "internal:reference",
            "source_reference",
            "embedding",
            "vector",
            "token",
        ):
            self.assertNotIn(forbidden, source.content)

    def test_builders_reject_mismatched_tenant_objects(self) -> None:
        with self.assertRaises(BusinessBrainAssemblyError):
            build_catalog_source(
                BUSINESS_A_ID,
                "USD",
                _catalog(BUSINESS_B_ID, 1),
            )
        with self.assertRaises(BusinessBrainAssemblyError):
            build_knowledge_source(
                BUSINESS_A_ID,
                _knowledge(BUSINESS_B_ID, 1),
            )
        with self.assertRaises(BusinessBrainAssemblyError):
            build_branding_source(BUSINESS_A_ID, _branding(BUSINESS_B_ID))


class BusinessBrainAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_catalog_and_knowledge_only_in_deterministic_order(
        self,
    ) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            catalog=[
                _catalog(BUSINESS_A_ID, 4, name="Archived", status="archived"),
                _catalog(BUSINESS_A_ID, 2, name="Service", item_type="service"),
                _catalog(BUSINESS_A_ID, 3, name="Draft", status="draft"),
                _catalog(BUSINESS_A_ID, 1, name="Product"),
            ],
            knowledge=[
                _knowledge(BUSINESS_A_ID, 4, title="Archived", status="archived"),
                _knowledge(BUSINESS_A_ID, 2, title="Active two"),
                _knowledge(BUSINESS_A_ID, 3, title="Draft", status="draft"),
                _knowledge(BUSINESS_A_ID, 1, title="Active one"),
            ],
        )

        sources = [
            source
            async for source in iterate_business_brain_sources(
                session,
                BUSINESS_A_ID,
                batch_size=2,
            )
        ]

        self.assertEqual(
            [source.source_type for source in sources],
            [
                "business_profile",
                "catalog_item",
                "catalog_item",
                "knowledge_entry",
                "knowledge_entry",
            ],
        )
        self.assertEqual(
            [source.title for source in sources],
            ["Business profile", "Product", "Service", "Active one", "Active two"],
        )
        self.assertNotIn("Draft", {source.title for source in sources})
        self.assertNotIn("Archived", {source.title for source in sources})

    async def test_branding_source_appears_after_profile_when_meaningful(self) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            branding=_branding(BUSINESS_A_ID),
        )
        sources = [
            source
            async for source in iterate_business_brain_sources(
                session,
                BUSINESS_A_ID,
            )
        ]
        self.assertEqual(
            [source.source_type for source in sources],
            ["business_profile", "branding"],
        )

    async def test_all_database_queries_are_tenant_scoped(self) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            catalog=[
                _catalog(BUSINESS_A_ID, 1, name="Tenant A catalog"),
                _catalog(BUSINESS_B_ID, 2, name="Tenant B catalog"),
            ],
            knowledge=[
                _knowledge(BUSINESS_A_ID, 1, title="Tenant A knowledge"),
                _knowledge(BUSINESS_B_ID, 2, title="Tenant B knowledge"),
            ],
        )
        sources = [
            source
            async for source in iterate_business_brain_sources(
                session,
                BUSINESS_A_ID,
            )
        ]
        titles = {source.title for source in sources}

        self.assertIn("Tenant A catalog", titles)
        self.assertIn("Tenant A knowledge", titles)
        self.assertNotIn("Tenant B catalog", titles)
        self.assertNotIn("Tenant B knowledge", titles)
        self.assertTrue(
            all(business_id == BUSINESS_A_ID for business_id in session.tenant_ids)
        )
        for statement in session.statements:
            sql = str(statement)
            self.assertTrue("business_id" in sql or "WHERE businesses.id =" in sql)

    async def test_keyset_batching_crosses_boundaries_without_fixed_small_limit(
        self,
    ) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            catalog=[
                _catalog(BUSINESS_A_ID, sequence, name=f"Item {sequence}")
                for sequence in range(1, 9)
            ],
            knowledge=[
                _knowledge(BUSINESS_A_ID, sequence, title=f"Knowledge {sequence}")
                for sequence in range(1, 7)
            ],
        )

        sources = [
            source
            async for source in iterate_business_brain_sources(
                session,
                BUSINESS_A_ID,
                batch_size=3,
            )
        ]

        self.assertEqual(len(sources), 1 + 8 + 6)
        self.assertGreaterEqual(session.catalog_page_calls, 3)
        self.assertGreaterEqual(session.knowledge_page_calls, 3)
        later_pages = [
            str(statement)
            for statement in session.statements
            if "created_at >" in str(statement)
        ]
        self.assertTrue(later_pages)
        self.assertTrue(all("OFFSET" not in sql.upper() for sql in later_pages))

    async def test_manifest_counts_only_the_explicit_tenant(self) -> None:
        manifest = await build_business_brain_manifest(
            _AssemblySession(
                business=_business(BUSINESS_A_ID),
                catalog=[
                    _catalog(BUSINESS_A_ID, 1),
                    _catalog(BUSINESS_B_ID, 2),
                ],
                knowledge=[
                    _knowledge(BUSINESS_A_ID, 1),
                    _knowledge(BUSINESS_B_ID, 2),
                ],
            ),
            BUSINESS_A_ID,
        )

        self.assertEqual(manifest.source_count, 3)
        self.assertEqual(manifest.source_counts_by_type["catalog_item"], 1)
        self.assertEqual(manifest.source_counts_by_type["knowledge_entry"], 1)

    async def test_manifest_counts_and_revision_are_deterministic(self) -> None:
        def session() -> _AssemblySession:
            return _AssemblySession(
                business=_business(BUSINESS_A_ID),
                branding=_branding(BUSINESS_A_ID),
                catalog=[
                    _catalog(BUSINESS_A_ID, 1),
                    _catalog(BUSINESS_A_ID, 2, item_type="service"),
                    _catalog(BUSINESS_A_ID, 3, status="draft"),
                ],
                knowledge=[
                    _knowledge(BUSINESS_A_ID, 1),
                    _knowledge(BUSINESS_A_ID, 2, status="archived"),
                ],
            )

        first = await build_business_brain_manifest(session(), BUSINESS_A_ID)
        second = await build_business_brain_manifest(session(), BUSINESS_A_ID)

        self.assertEqual(first.source_count, 5)
        self.assertEqual(
            dict(first.source_counts_by_type),
            {
                "business_profile": 1,
                "branding": 1,
                "appointment_type": 0,
                "catalog_item": 2,
                "knowledge_entry": 1,
            },
        )
        self.assertEqual(first.revision, second.revision)
        self.assertRegex(first.revision, r"^[0-9a-f]{64}$")
        self.assertEqual(
            tuple(first.source_counts_by_type), BUSINESS_BRAIN_SOURCE_TYPES
        )
        with self.assertRaises(TypeError):
            first.source_counts_by_type["catalog_item"] = 99  # type: ignore[index]

    async def test_manifest_revision_changes_for_catalog_or_knowledge_change(
        self,
    ) -> None:
        catalog = _catalog(BUSINESS_A_ID, 1, description="Original")
        knowledge = _knowledge(BUSINESS_A_ID, 1, content="Original")

        async def revision() -> str:
            manifest = await build_business_brain_manifest(
                _AssemblySession(
                    business=_business(BUSINESS_A_ID),
                    catalog=[catalog],
                    knowledge=[knowledge],
                ),
                BUSINESS_A_ID,
            )
            return manifest.revision

        original = await revision()
        catalog.description = "Changed"
        catalog_changed = await revision()
        knowledge.content = "Changed"
        knowledge_changed = await revision()

        self.assertNotEqual(original, catalog_changed)
        self.assertNotEqual(catalog_changed, knowledge_changed)

    async def test_manifest_revision_changes_for_addition_and_archive(self) -> None:
        catalog = [_catalog(BUSINESS_A_ID, 1)]

        async def manifest():
            return await build_business_brain_manifest(
                _AssemblySession(
                    business=_business(BUSINESS_A_ID),
                    catalog=catalog,
                ),
                BUSINESS_A_ID,
            )

        original = await manifest()
        added_item = _catalog(BUSINESS_A_ID, 2)
        catalog.append(added_item)
        added = await manifest()
        added_item.status = "archived"
        archived = await manifest()

        self.assertNotEqual(original.revision, added.revision)
        self.assertNotEqual(added.revision, archived.revision)
        self.assertEqual(added.source_count, original.source_count + 1)
        self.assertEqual(archived.source_count, original.source_count)
        self.assertEqual(archived.revision, original.revision)

    async def test_assembly_performs_no_writes(self) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            catalog=[_catalog(BUSINESS_A_ID, 1)],
            knowledge=[_knowledge(BUSINESS_A_ID, 1)],
        )
        await build_business_brain_manifest(session, BUSINESS_A_ID)
        self.assertEqual(session.write_calls, 0)

    async def test_sqlalchemy_failure_becomes_safe_domain_error(self) -> None:
        session = _AssemblySession(
            business=_business(BUSINESS_A_ID),
            scalar_error=SQLAlchemyError("private database detail"),
        )
        with self.assertRaises(BusinessBrainAssemblyError) as raised:
            await build_business_brain_manifest(session, BUSINESS_A_ID)
        self.assertNotIn("private database detail", str(raised.exception))

    async def test_batch_size_is_deliberately_bounded(self) -> None:
        for batch_size in (0, -1, True, MAX_SOURCE_BATCH_SIZE + 1):
            with self.subTest(batch_size=batch_size), self.assertRaises(ValueError):
                _ = [
                    source
                    async for source in iterate_business_brain_sources(
                        _AssemblySession(business=_business(BUSINESS_A_ID)),
                        BUSINESS_A_ID,
                        batch_size=batch_size,
                    )
                ]


class _ScalarResult:
    def __init__(self, values) -> None:
        self.values = list(values)

    def all(self):
        return self.values


class _AssemblySession:
    def __init__(
        self,
        *,
        business: Business,
        branding: BusinessBranding | None = None,
        catalog: list[CatalogItem] | None = None,
        knowledge: list[BusinessKnowledgeEntry] | None = None,
        scalar_error: SQLAlchemyError | None = None,
    ) -> None:
        self.business = business
        self.branding = branding
        self.catalog = catalog or []
        self.knowledge = knowledge or []
        self.scalar_error = scalar_error
        self.catalog_offset = 0
        self.knowledge_offset = 0
        self.catalog_page_calls = 0
        self.knowledge_page_calls = 0
        self.tenant_ids: list[UUID] = []
        self.statements = []
        self.write_calls = 0

    async def scalar(self, statement):
        if self.scalar_error is not None:
            raise self.scalar_error
        self.statements.append(statement)
        params = statement.compile().params
        business_id = _parameter(params, "id") or _parameter(params, "business_id")
        if "FROM business_branding" in str(statement):
            business_id = _parameter(params, "business_id")
            self.tenant_ids.append(business_id)
            if self.branding is not None and self.branding.business_id == business_id:
                return self.branding
            return None
        self.tenant_ids.append(business_id)
        return self.business if self.business.id == business_id else None

    async def scalars(self, statement) -> _ScalarResult:
        self.statements.append(statement)
        params = statement.compile().params
        business_id = _parameter(params, "business_id")
        self.tenant_ids.append(business_id)
        limit = next(
            value
            for name, value in params.items()
            if name.startswith("param_") and isinstance(value, int)
        )
        if "FROM catalog_items" in str(statement):
            self.catalog_page_calls += 1
            active = sorted(
                (
                    item
                    for item in self.catalog
                    if item.business_id == business_id and item.status == "active"
                ),
                key=lambda item: (item.created_at, item.id),
            )
            page = active[self.catalog_offset : self.catalog_offset + limit]
            self.catalog_offset += len(page)
            return _ScalarResult(page)
        if "FROM appointment_types" in str(statement):
            return _ScalarResult([])
        self.knowledge_page_calls += 1
        active = sorted(
            (
                entry
                for entry in self.knowledge
                if entry.business_id == business_id and entry.status == "active"
            ),
            key=lambda entry: (entry.created_at, entry.id),
        )
        page = active[self.knowledge_offset : self.knowledge_offset + limit]
        self.knowledge_offset += len(page)
        return _ScalarResult(page)

    def add(self, _record) -> None:
        self.write_calls += 1

    async def flush(self) -> None:
        self.write_calls += 1

    async def commit(self) -> None:
        self.write_calls += 1


def _parameter(params: dict[str, object], prefix: str):
    return next(
        (value for name, value in params.items() if name.startswith(f"{prefix}_")),
        None,
    )


def _business(
    business_id: UUID,
    *,
    name: str = "Acme Farm",
) -> Business:
    return Business(
        id=business_id,
        name=name,
        slug="acme-farm",
        business_type="farm",
        status="active",
        timezone="Asia/Karachi",
        currency="PKR",
        locale="en-PK",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def _branding(
    business_id: UUID,
    *,
    primary_color: str | None = "#112233",
    secondary_color: str | None = "#445566",
    accent_color: str | None = None,
    logo_url: str | None = "/api/v1/media/public-logo",
) -> BusinessBranding:
    return BusinessBranding(
        business_id=business_id,
        primary_color=primary_color,
        secondary_color=secondary_color,
        accent_color=accent_color,
        logo_url=logo_url,
        logo_storage_key="private/storage/key",
        created_at=BASE_TIME,
        updated_at=BASE_TIME,
    )


def _catalog(
    business_id: UUID,
    sequence: int,
    *,
    name: str = "Catalog item",
    item_type: str = "product",
    description: str | None = None,
    sku: str | None = None,
    price: Decimal | None = None,
    status: str = "active",
) -> CatalogItem:
    timestamp = BASE_TIME + timedelta(seconds=sequence)
    return CatalogItem(
        id=UUID(int=sequence),
        business_id=business_id,
        item_type=item_type,
        name=name,
        description=description,
        sku=sku,
        price=price,
        status=status,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _knowledge(
    business_id: UUID,
    sequence: int,
    *,
    category: str = "general",
    title: str = "Knowledge entry",
    content: str = "Authoritative knowledge content.",
    status: str = "active",
) -> BusinessKnowledgeEntry:
    timestamp = BASE_TIME + timedelta(minutes=1, seconds=sequence)
    return BusinessKnowledgeEntry(
        id=UUID(int=1000 + sequence),
        business_id=business_id,
        category=category,
        title=title,
        content=content,
        status=status,
        source_type="manual",
        source_reference=None,
        created_at=timestamp,
        updated_at=timestamp,
    )
