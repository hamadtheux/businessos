import os
import re
import unittest

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, String
from sqlalchemy.orm import configure_mappers


TEST_DATABASE_URL = "postgresql+asyncpg://database.invalid/test"
TEST_AUTH_SECRET = "x" * 32
os.environ["AIBOS_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AIBOS_AUTH_SECRET_KEY"] = TEST_AUTH_SECRET

from app.db.base import Base  # noqa: E402
from app.models import (  # noqa: E402
    Business,
    BusinessBranding,
    BusinessMembership,
)


EXPECTED_BRANDING_COLUMNS = {
    "business_id",
    "logo_url",
    "logo_storage_key",
    "primary_color",
    "secondary_color",
    "accent_color",
    "created_at",
    "updated_at",
}
COLOR_COLUMN_NAMES = (
    "primary_color",
    "secondary_color",
    "accent_color",
)
HEX_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


class BusinessBrandingModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_mappers()
        cls.table = Base.metadata.tables["business_branding"]

    def test_business_branding_table_is_registered_with_expected_columns(
        self,
    ) -> None:
        self.assertIs(BusinessBranding.__table__, self.table)
        self.assertEqual(set(self.table.columns.keys()), EXPECTED_BRANDING_COLUMNS)

    def test_business_id_is_sole_primary_key_without_second_id(self) -> None:
        self.assertEqual(
            [column.name for column in self.table.primary_key.columns],
            ["business_id"],
        )
        self.assertNotIn("id", self.table.columns)

    def test_business_id_foreign_key_cascades_to_businesses(self) -> None:
        foreign_keys = list(self.table.c.business_id.foreign_keys)

        self.assertEqual(len(foreign_keys), 1)
        self.assertEqual(foreign_keys[0].target_fullname, "businesses.id")
        self.assertEqual(foreign_keys[0].ondelete, "CASCADE")
        self.assertEqual(
            foreign_keys[0].constraint.name,
            "fk_business_branding_business_id_businesses",
        )

    def test_optional_branding_source_fields_are_nullable_and_bounded(
        self,
    ) -> None:
        self.assertTrue(self.table.c.logo_url.nullable)
        self.assertIsInstance(self.table.c.logo_url.type, String)
        self.assertEqual(self.table.c.logo_url.type.length, 2048)
        self.assertTrue(self.table.c.logo_storage_key.nullable)
        self.assertIsInstance(self.table.c.logo_storage_key.type, String)
        self.assertEqual(self.table.c.logo_storage_key.type.length, 1024)

        for column_name in COLOR_COLUMN_NAMES:
            with self.subTest(column_name=column_name):
                column = self.table.c[column_name]
                self.assertTrue(column.nullable)
                self.assertIsInstance(column.type, String)
                self.assertEqual(column.type.length, 7)

    def test_timestamps_are_timezone_aware_and_use_server_defaults(self) -> None:
        for column_name in ("created_at", "updated_at"):
            with self.subTest(column_name=column_name):
                column = self.table.c[column_name]
                self.assertIsInstance(column.type, DateTime)
                self.assertTrue(column.type.timezone)
                self.assertFalse(column.nullable)
                self.assertIsNotNone(column.server_default)

    def test_each_color_has_named_postgresql_hex_constraint(self) -> None:
        constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint)
        }

        for column_name in COLOR_COLUMN_NAMES:
            with self.subTest(column_name=column_name):
                constraint_name = f"ck_business_branding_valid_{column_name}"
                self.assertIn(constraint_name, constraints)
                constraint_sql = constraints[constraint_name]
                self.assertIn(f"{column_name} IS NULL", constraint_sql)
                self.assertIn(
                    f"{column_name} ~ '^#[0-9A-Fa-f]{{6}}$'",
                    constraint_sql,
                )

    def test_valid_six_digit_hex_values_match_constraint_pattern(self) -> None:
        for value in ("#176B45", "#ffffff", "#ABCDEF"):
            with self.subTest(value=value):
                self.assertIsNotNone(HEX_PATTERN.fullmatch(value))

    def test_invalid_color_formats_do_not_match_constraint_pattern(self) -> None:
        for value in ("176B45", "#FFF", "red", "#GGGGGG"):
            with self.subTest(value=value):
                self.assertIsNone(HEX_PATTERN.fullmatch(value))

    def test_relationships_are_lazy_one_to_one_with_safe_cascade(self) -> None:
        business_branding_relationship = Business.branding.property
        branding_business_relationship = BusinessBranding.business.property

        self.assertFalse(business_branding_relationship.uselist)
        self.assertFalse(branding_business_relationship.uselist)
        self.assertEqual(business_branding_relationship.lazy, "select")
        self.assertEqual(branding_business_relationship.lazy, "select")
        self.assertTrue(business_branding_relationship.passive_deletes)
        self.assertIn("delete-orphan", business_branding_relationship.cascade)

    def test_existing_business_and_membership_tables_remain_intact(self) -> None:
        self.assertEqual(
            set(Business.__table__.columns.keys()),
            {
                "id",
                "name",
                "slug",
                "business_type",
                "status",
                "timezone",
                "currency",
                "locale",
                "website_url",
                "location",
                "description",
                "brand_voice",
                "avoid_keywords",
                "created_at",
                "updated_at",
            },
        )
        self.assertEqual(
            set(BusinessMembership.__table__.columns.keys()),
            {
                "id",
                "business_id",
                "user_id",
                "role",
                "status",
                "created_at",
                "updated_at",
            },
        )

    def test_no_derived_theme_or_image_storage_columns_exist(self) -> None:
        forbidden_columns = {
            "brand_primary_hover",
            "brand_on_primary",
            "sidebar_background",
            "focus_ring",
            "soft_color",
            "contrast_ratio",
            "theme_tokens",
            "image",
            "image_bytes",
            "logo_blob",
            "logo_base64",
            "logo_data_url",
        }

        self.assertTrue(forbidden_columns.isdisjoint(self.table.columns.keys()))
        self.assertFalse(
            any(isinstance(column.type, LargeBinary) for column in self.table.columns)
        )
