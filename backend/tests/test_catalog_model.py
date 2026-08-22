import os
import unittest
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import CheckConstraint, Numeric, UniqueConstraint
from sqlalchemy.orm import configure_mappers

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.models import Business, CatalogItem
from app.schemas.catalog import (
    CatalogItemCreate,
    CatalogItemResponse,
    CatalogItemUpdate,
)


class CatalogItemModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_mappers()
        cls.table = CatalogItem.__table__

    def test_catalog_model_metadata_is_complete(self) -> None:
        self.assertEqual(self.table.name, "catalog_items")
        self.assertEqual(
            set(self.table.columns.keys()),
            {
                "id",
                "business_id",
                "item_type",
                "name",
                "description",
                "sku",
                "price",
                "status",
                "created_at",
                "updated_at",
            },
        )
        self.assertTrue(self.table.c.id.primary_key)

    def test_business_id_is_required_cascading_and_not_redundantly_indexed(
        self,
    ) -> None:
        column = self.table.c.business_id
        self.assertFalse(column.nullable)
        foreign_key = next(iter(column.foreign_keys))
        self.assertEqual(foreign_key.target_fullname, "businesses.id")
        self.assertEqual(foreign_key.ondelete, "CASCADE")
        self.assertNotIn(
            "ix_catalog_items_business_id",
            {index.name for index in self.table.indexes},
        )

    def test_product_and_service_are_the_only_model_types(self) -> None:
        constraint = self._check("ck_catalog_items_valid_item_type")
        self.assertIn("'product'", str(constraint.sqltext))
        self.assertIn("'service'", str(constraint.sqltext))
        self.assertNotIn("inventory", str(constraint.sqltext))

    def test_status_constraint_supports_catalog_lifecycle(self) -> None:
        constraint = self._check("ck_catalog_items_valid_status")
        sql = str(constraint.sqltext)
        for value in ("active", "draft", "archived"):
            self.assertIn(f"'{value}'", sql)

    def test_price_is_nullable_decimal_numeric_14_2(self) -> None:
        column = self.table.c.price
        self.assertTrue(column.nullable)
        self.assertIsInstance(column.type, Numeric)
        self.assertEqual(column.type.precision, 14)
        self.assertEqual(column.type.scale, 2)
        self.assertTrue(column.type.asdecimal)

    def test_negative_database_price_is_prevented(self) -> None:
        constraint = self._check("ck_catalog_items_valid_price")
        self.assertIn("price >= 0", str(constraint.sqltext))
        self.assertIn("price <= 999999999999.99", str(constraint.sqltext))

    def test_sku_unique_constraint_is_tenant_scoped_and_nullable(self) -> None:
        unique = next(
            constraint
            for constraint in self.table.constraints
            if isinstance(constraint, UniqueConstraint)
            and constraint.name == "uq_catalog_items_business_sku"
        )
        self.assertEqual(
            [column.name for column in unique.columns], ["business_id", "sku"]
        )
        self.assertTrue(self.table.c.sku.nullable)

    def test_same_sku_namespace_is_independent_across_businesses(self) -> None:
        unique = next(
            constraint
            for constraint in self.table.constraints
            if constraint.name == "uq_catalog_items_business_sku"
        )
        self.assertEqual(
            tuple(column.name for column in unique.columns), ("business_id", "sku")
        )

    def test_production_indexes_are_bounded_and_tenant_first(self) -> None:
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in self.table.indexes
        }
        self.assertEqual(
            indexes["ix_catalog_items_business_status"], ("business_id", "status")
        )
        self.assertEqual(
            indexes["ix_catalog_items_business_item_type"], ("business_id", "item_type")
        )
        self.assertEqual(len(indexes), 2)

    def test_business_deletion_relationship_uses_safe_cascade(self) -> None:
        relationship = Business.catalog_items.property
        self.assertTrue(relationship.passive_deletes)
        self.assertIn("delete-orphan", relationship.cascade)
        self.assertEqual(relationship.back_populates, "business")

    def test_inventory_and_binary_columns_are_absent(self) -> None:
        forbidden = {
            "stock_quantity",
            "warehouse",
            "reserved_stock",
            "inventory_movements",
            "currency",
            "file",
            "file_data",
            "binary_data",
        }
        self.assertTrue(forbidden.isdisjoint(self.table.columns.keys()))

    def _check(self, name: str) -> CheckConstraint:
        return next(
            constraint
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint) and constraint.name == name
        )


class CatalogItemSchemaTests(unittest.TestCase):
    def test_valid_product_and_service(self) -> None:
        for item_type in ("product", "service"):
            with self.subTest(item_type=item_type):
                item = CatalogItemCreate(item_type=item_type, name="Consultation")
                self.assertEqual(item.item_type, item_type)
                self.assertEqual(item.status, "active")

    def test_invalid_item_type_and_status_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CatalogItemCreate(item_type="inventory", name="Widget")
        with self.assertRaises(ValidationError):
            CatalogItemCreate(item_type="product", name="Widget", status="deleted")

    def test_name_and_sku_are_normalized(self) -> None:
        item = CatalogItemCreate(
            item_type="product",
            name="  Widget  ",
            sku="  abc-1  ",
        )
        self.assertEqual(item.name, "Widget")
        self.assertEqual(item.sku, "ABC-1")
        self.assertIsNone(
            CatalogItemCreate(item_type="product", name="Widget", sku="  ").sku
        )

    def test_name_is_required_and_bounded(self) -> None:
        for name in ("", "   ", "x" * 201):
            with (
                self.subTest(name_length=len(name)),
                self.assertRaises(ValidationError),
            ):
                CatalogItemCreate(item_type="product", name=name)

    def test_description_is_trimmed_and_bounded(self) -> None:
        item = CatalogItemCreate(
            item_type="service",
            name="Advice",
            description="  Helpful  ",
        )
        self.assertEqual(item.description, "Helpful")
        with self.assertRaises(ValidationError):
            CatalogItemCreate(
                item_type="service",
                name="Advice",
                description="x" * 10_001,
            )

    def test_price_requires_nonnegative_decimal_with_two_places(self) -> None:
        item = CatalogItemCreate(
            item_type="product",
            name="Widget",
            price="19.95",
        )
        self.assertEqual(item.price, Decimal("19.95"))
        for price in ("-0.01", "1.001", "1000000000000.00"):
            with self.subTest(price=price), self.assertRaises(ValidationError):
                CatalogItemCreate(item_type="product", name="Widget", price=price)

    def test_client_cannot_supply_tenant_or_timestamp_fields(self) -> None:
        for extra in ("business_id", "created_at", "updated_at"):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                CatalogItemCreate.model_validate(
                    {"item_type": "product", "name": "Widget", extra: "forbidden"}
                )

    def test_patch_distinguishes_omitted_from_explicit_null(self) -> None:
        omitted = CatalogItemUpdate(name="Updated")
        explicit = CatalogItemUpdate(description=None, sku=None, price=None)
        self.assertEqual(omitted.model_dump(exclude_unset=True), {"name": "Updated"})
        self.assertEqual(
            explicit.model_dump(exclude_unset=True),
            {"description": None, "sku": None, "price": None},
        )

    def test_patch_rejects_null_for_nonnullable_fields(self) -> None:
        for field in ("item_type", "name", "status"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                CatalogItemUpdate.model_validate({field: None})

    def test_decimal_response_serializes_without_binary_float(self) -> None:
        payload = CatalogItemResponse.model_validate(
            CatalogItem(
                id="00000000-0000-0000-0000-000000000001",
                business_id="00000000-0000-0000-0000-000000000002",
                item_type="product",
                name="Widget",
                price=Decimal("19.95"),
                status="active",
                created_at="2026-08-20T00:00:00+00:00",
                updated_at="2026-08-20T00:00:00+00:00",
            )
        )
        self.assertEqual(payload.model_dump_json().count('"19.95"'), 1)
