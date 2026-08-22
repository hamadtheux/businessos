import os
import unittest

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import configure_mappers

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models import Business, BusinessKnowledgeEntry
from app.schemas.business_brain import (
    MAX_KNOWLEDGE_CONTENT_LENGTH,
    BusinessKnowledgeEntryCreate,
    BusinessKnowledgeEntryResponse,
    BusinessKnowledgeEntryUpdate,
)

EXPECTED_CATEGORIES = {
    "general",
    "faq",
    "policy",
    "procedure",
    "brand",
    "sales",
    "support",
    "operations",
    "marketing",
}


class BusinessKnowledgeEntryModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_mappers()
        cls.table = BusinessKnowledgeEntry.__table__

    def test_model_metadata_has_only_the_focused_knowledge_fields(self) -> None:
        self.assertEqual(self.table.name, "business_knowledge_entries")
        self.assertEqual(
            set(self.table.columns.keys()),
            {
                "id",
                "business_id",
                "category",
                "title",
                "content",
                "status",
                "source_type",
                "source_reference",
                "created_at",
                "updated_at",
            },
        )
        self.assertTrue(self.table.c.id.primary_key)

    def test_business_id_is_required_and_cascades(self) -> None:
        column = self.table.c.business_id
        self.assertFalse(column.nullable)
        foreign_key = next(iter(column.foreign_keys))
        self.assertEqual(foreign_key.target_fullname, "businesses.id")
        self.assertEqual(foreign_key.ondelete, "CASCADE")

    def test_database_category_constraint_is_exact(self) -> None:
        sql = str(self._check("ck_business_knowledge_entries_valid_category").sqltext)
        for category in EXPECTED_CATEGORIES:
            self.assertIn(f"'{category}'", sql)
        self.assertNotIn("document", sql)

    def test_database_rejects_invalid_category_by_check_constraint(self) -> None:
        constraint = self._check("ck_business_knowledge_entries_valid_category")
        self.assertNotIn("'invalid'", str(constraint.sqltext))

    def test_database_status_constraint_has_the_full_lifecycle(self) -> None:
        sql = str(self._check("ck_business_knowledge_entries_valid_status").sqltext)
        for entry_status in ("active", "draft", "archived"):
            self.assertIn(f"'{entry_status}'", sql)

    def test_database_rejects_invalid_status_by_check_constraint(self) -> None:
        constraint = self._check("ck_business_knowledge_entries_valid_status")
        self.assertNotIn("deleted", str(constraint.sqltext))

    def test_database_source_types_do_not_claim_future_ingestion(self) -> None:
        sql = str(
            self._check("ck_business_knowledge_entries_valid_source_type").sqltext
        )
        self.assertIn("'manual'", sql)
        self.assertIn("'system'", sql)
        for unsupported in ("document", "website", "integration"):
            self.assertNotIn(unsupported, sql)

    def test_title_and_content_are_required_and_bounded(self) -> None:
        title = self.table.c.title
        content = self.table.c.content
        self.assertFalse(title.nullable)
        self.assertIsInstance(title.type, String)
        self.assertEqual(title.type.length, 250)
        self.assertFalse(content.nullable)
        self.assertIsInstance(content.type, Text)
        self.assertIn(
            "BETWEEN 1 AND 50000",
            str(self._check("ck_business_knowledge_entries_valid_content").sqltext),
        )

    def test_source_reference_is_nullable_bounded_text(self) -> None:
        column = self.table.c.source_reference
        self.assertTrue(column.nullable)
        self.assertIsInstance(column.type, String)
        self.assertEqual(column.type.length, 1024)

    def test_timestamps_are_required_and_timezone_aware(self) -> None:
        for field in ("created_at", "updated_at"):
            with self.subTest(field=field):
                column = self.table.c[field]
                self.assertFalse(column.nullable)
                self.assertIsInstance(column.type, DateTime)
                self.assertTrue(column.type.timezone)
                self.assertIsNotNone(column.server_default)

    def test_indexes_are_exactly_the_two_tenant_first_compounds(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in self.table.indexes
        }
        self.assertEqual(
            indexes,
            {
                "ix_business_knowledge_entries_business_status": (
                    "business_id",
                    "status",
                ),
                "ix_business_knowledge_entries_business_category": (
                    "business_id",
                    "category",
                ),
            },
        )

    def test_business_relationship_uses_database_aware_orphan_cascade(self) -> None:
        relationship = Business.knowledge_entries.property
        self.assertTrue(relationship.passive_deletes)
        self.assertIn("delete-orphan", relationship.cascade)
        self.assertEqual(relationship.back_populates, "business")

    def test_vector_embedding_and_chunk_columns_are_absent(self) -> None:
        forbidden = {
            "embedding",
            "embedding_model",
            "vector",
            "tokens",
            "chunk",
            "chunk_data",
        }
        self.assertTrue(forbidden.isdisjoint(self.table.columns.keys()))

    def test_catalog_and_product_fields_are_not_duplicated(self) -> None:
        forbidden = {
            "product_id",
            "catalog_item_id",
            "sku",
            "price",
            "item_type",
            "inventory",
        }
        self.assertTrue(forbidden.isdisjoint(self.table.columns.keys()))

    def _check(self, name: str) -> CheckConstraint:
        return next(
            constraint
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint) and constraint.name == name
        )


class BusinessKnowledgeEntrySchemaTests(unittest.TestCase):
    def test_all_supported_categories_validate(self) -> None:
        for category in EXPECTED_CATEGORIES:
            with self.subTest(category=category):
                entry = BusinessKnowledgeEntryCreate(
                    category=category,
                    title="Question",
                    content="Answer",
                )
                self.assertEqual(entry.category, category)
                self.assertEqual(entry.status, "active")

    def test_invalid_category_and_status_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._create(category="inventory")
        with self.assertRaises(ValidationError):
            self._create(status="deleted")

    def test_title_and_content_are_trimmed(self) -> None:
        entry = self._create(title="  Returns  ", content="  Within 30 days.  ")
        self.assertEqual(entry.title, "Returns")
        self.assertEqual(entry.content, "Within 30 days.")

    def test_blank_or_missing_title_is_rejected(self) -> None:
        for title in (None, "", "   "):
            with self.subTest(title=title), self.assertRaises(ValidationError):
                payload = {"category": "general", "content": "Answer"}
                if title is not None:
                    payload["title"] = title
                BusinessKnowledgeEntryCreate.model_validate(payload)

    def test_blank_or_missing_content_is_rejected(self) -> None:
        for content in (None, "", "   "):
            with self.subTest(content=content), self.assertRaises(ValidationError):
                payload = {"category": "general", "title": "Question"}
                if content is not None:
                    payload["content"] = content
                BusinessKnowledgeEntryCreate.model_validate(payload)

    def test_title_and_content_limits_are_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            self._create(title="x" * 251)
        with self.assertRaises(ValidationError):
            self._create(content="x" * (MAX_KNOWLEDGE_CONTENT_LENGTH + 1))

    def test_client_cannot_supply_server_managed_fields(self) -> None:
        for extra in (
            "id",
            "business_id",
            "source_type",
            "source_reference",
            "created_at",
            "updated_at",
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                BusinessKnowledgeEntryCreate.model_validate(
                    {
                        "category": "general",
                        "title": "Question",
                        "content": "Answer",
                        extra: "forbidden",
                    }
                )

    def test_patch_distinguishes_omitted_fields(self) -> None:
        update = BusinessKnowledgeEntryUpdate(title="  Updated  ")
        self.assertEqual(update.model_dump(exclude_unset=True), {"title": "Updated"})

    def test_patch_rejects_null_and_blank_fields(self) -> None:
        for field in ("category", "title", "content", "status"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                BusinessKnowledgeEntryUpdate.model_validate({field: None})
        for field in ("title", "content"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                BusinessKnowledgeEntryUpdate.model_validate({field: "   "})

    def test_patch_forbids_source_and_tenant_changes(self) -> None:
        for field in ("business_id", "source_type", "source_reference"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                BusinessKnowledgeEntryUpdate.model_validate({field: "forbidden"})

    def test_response_includes_only_safe_source_metadata(self) -> None:
        fields = set(BusinessKnowledgeEntryResponse.model_fields)
        self.assertIn("source_reference", fields)
        self.assertNotIn("embedding", fields)
        self.assertNotIn("internal_metadata", fields)

    def _create(self, **overrides) -> BusinessKnowledgeEntryCreate:
        payload = {
            "category": "general",
            "title": "Question",
            "content": "Answer",
        }
        payload.update(overrides)
        return BusinessKnowledgeEntryCreate.model_validate(payload)
