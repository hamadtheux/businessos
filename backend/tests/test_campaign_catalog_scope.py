import os
import unittest
from types import SimpleNamespace
from uuid import uuid4

from pydantic import ValidationError

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.schemas.marketing import CampaignGenerateRequest  # noqa: E402
from app.services.marketing import _resolve_campaign_catalog_products  # noqa: E402


class _ScalarResult:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _Session:
    def __init__(self, values):
        self.values = list(values)
        self.statements = []

    async def scalars(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.values)


def _product(business_id, *, published=True, status="active"):
    return SimpleNamespace(
        id=uuid4(),
        business_id=business_id,
        item_type="product",
        status=status,
        published=published,
    )


class CampaignCatalogScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_selected_scope_requires_ids(self):
        with self.assertRaises(ValidationError):
            CampaignGenerateRequest(
                goal="Sell products",
                catalog_scope="selected",
            )

    def test_all_and_recommended_reject_explicit_ids(self):
        item_id = uuid4()

        for scope in ("all", "recommended"):
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                CampaignGenerateRequest(
                    goal="Sell products",
                    catalog_scope=scope,
                    catalog_item_ids=[item_id],
                )

    async def test_legacy_explicit_ids_normalize_to_selected(self):
        business_id = uuid4()
        product = _product(business_id)
        data = CampaignGenerateRequest(
            goal="Sell this product",
            catalog_item_ids=[product.id],
        )

        scope, products = await _resolve_campaign_catalog_products(
            _Session([product]),
            business_id=business_id,
            data=data,
        )

        self.assertEqual(scope, "selected")
        self.assertEqual([item.id for item in products], [product.id])

    async def test_all_scope_uses_tenant_catalog_database_truth(self):
        business_id = uuid4()
        first = _product(business_id)
        second = _product(business_id)

        scope, products = await _resolve_campaign_catalog_products(
            _Session([first, second]),
            business_id=business_id,
            data=CampaignGenerateRequest(
                goal="Promote the catalog",
                catalog_scope="all",
            ),
        )

        self.assertEqual(scope, "all")
        self.assertEqual(
            {item.id for item in products},
            {first.id, second.id},
        )

    async def test_empty_all_scope_fails_closed(self):
        with self.assertRaisesRegex(
            MarketingValidationError,
            "catalog_selection_empty",
        ):
            await _resolve_campaign_catalog_products(
                _Session([]),
                business_id=uuid4(),
                data=CampaignGenerateRequest(
                    goal="Promote products",
                    catalog_scope="all",
                ),
            )

    async def test_cross_tenant_row_fails_closed(self):
        business_id = uuid4()
        other = _product(uuid4())

        with self.assertRaisesRegex(
            MarketingValidationError,
            "catalog_selection_invalid",
        ):
            await _resolve_campaign_catalog_products(
                _Session([other]),
                business_id=business_id,
                data=CampaignGenerateRequest(
                    goal="Promote products",
                    catalog_scope="all",
                ),
            )

    async def test_recommended_scope_requires_an_eligible_catalog(self):
        with self.assertRaisesRegex(
            MarketingValidationError,
            "catalog_selection_empty",
        ):
            await _resolve_campaign_catalog_products(
                _Session([]),
                business_id=uuid4(),
                data=CampaignGenerateRequest(
                    goal="Promote best products",
                    catalog_scope="recommended",
                ),
            )


if __name__ == "__main__":
    unittest.main()
