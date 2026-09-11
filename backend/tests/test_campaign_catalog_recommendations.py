import os
import unittest
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.schemas.marketing import CampaignGenerateRequest  # noqa: E402
from app.services.marketing import (  # noqa: E402
    _MAX_RECOMMENDED_CAMPAIGN_PRODUCTS,
    _rank_campaign_catalog_recommendations,
    _resolve_campaign_catalog_products,
)


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _Scalars:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)


class _Session:
    def __init__(
        self,
        products,
        *,
        order_rows=None,
        performance_rows=None,
    ):
        self.products = list(products)
        self.results = [
            list(order_rows or []),
            list(performance_rows or []),
        ]
        self.execute_calls = 0

    async def scalars(self, _statement):
        return _Scalars(self.products)

    async def execute(self, _statement):
        self.execute_calls += 1
        return _Rows(self.results.pop(0))


def _product(
    business_id,
    name,
    *,
    availability="in_stock",
    price="100.00",
    product_url="https://example.test/product",
    sku="SKU",
    description="Product description",
    brand="Brand",
    vendor=None,
    gtin=None,
    mpn=None,
    category="Apparel",
):
    return SimpleNamespace(
        id=uuid4(),
        business_id=business_id,
        item_type="product",
        name=name,
        status="active",
        published=True,
        availability=availability,
        price=Decimal(price) if price is not None else None,
        product_url=product_url,
        sku=sku,
        description=description,
        brand=brand,
        vendor=vendor,
        gtin=gtin,
        mpn=mpn,
        google_product_category=category,
    )


class CampaignCatalogRecommendationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_first_party_sales_rank_before_provider_only_history(self):
        business_id = uuid4()
        seller = _product(business_id, "Seller")
        advertised = _product(business_id, "Advertised")
        fallback = _product(business_id, "Fallback")

        session = _Session(
            [fallback, advertised, seller],
            order_rows=[
                SimpleNamespace(
                    catalog_item_id=seller.id,
                    units=8,
                    revenue=Decimal("800.00"),
                ),
            ],
            performance_rows=[
                SimpleNamespace(
                    catalog_item_id=advertised.id,
                    spend=Decimal("100.00"),
                    conversions=Decimal("10.0000"),
                    conversion_value=Decimal("1500.00"),
                ),
            ],
        )

        ranked = await _rank_campaign_catalog_recommendations(
            session,
            business_id=business_id,
            products=[fallback, advertised, seller],
        )

        self.assertEqual(
            [item.id for item in ranked],
            [seller.id, advertised.id, fallback.id],
        )

    async def test_provider_value_then_conversions_then_roas_rank_provider_rows(self):
        business_id = uuid4()
        high_value = _product(business_id, "High value")
        lower_value = _product(business_id, "Lower value")

        session = _Session(
            [high_value, lower_value],
            performance_rows=[
                SimpleNamespace(
                    catalog_item_id=lower_value.id,
                    spend=Decimal("10.00"),
                    conversions=Decimal("4.0000"),
                    conversion_value=Decimal("80.00"),
                ),
                SimpleNamespace(
                    catalog_item_id=high_value.id,
                    spend=Decimal("100.00"),
                    conversions=Decimal("3.0000"),
                    conversion_value=Decimal("500.00"),
                ),
            ],
        )

        ranked = await _rank_campaign_catalog_recommendations(
            session,
            business_id=business_id,
            products=[lower_value, high_value],
        )

        self.assertEqual(ranked[0].id, high_value.id)

    async def test_catalog_quality_is_truthful_fallback_without_history(self):
        business_id = uuid4()
        complete = _product(
            business_id,
            "Complete",
            availability="in_stock",
        )
        incomplete = _product(
            business_id,
            "Incomplete",
            availability="unknown",
            price=None,
            product_url=None,
            sku=None,
            description=None,
            brand=None,
            category=None,
        )

        session = _Session([incomplete, complete])

        ranked = await _rank_campaign_catalog_recommendations(
            session,
            business_id=business_id,
            products=[incomplete, complete],
        )

        self.assertEqual(ranked[0].id, complete.id)

    async def test_recommendations_are_bounded(self):
        business_id = uuid4()
        products = [
            _product(business_id, f"Product {index:02d}")
            for index in range(
                _MAX_RECOMMENDED_CAMPAIGN_PRODUCTS + 8
            )
        ]

        ranked = await _rank_campaign_catalog_recommendations(
            _Session(products),
            business_id=business_id,
            products=products,
        )

        self.assertEqual(
            len(ranked),
            _MAX_RECOMMENDED_CAMPAIGN_PRODUCTS,
        )

    async def test_resolver_now_returns_real_recommended_subset(self):
        business_id = uuid4()
        first = _product(business_id, "First")
        second = _product(business_id, "Second")

        scope, products = await _resolve_campaign_catalog_products(
            _Session(
                [second, first],
                order_rows=[
                    SimpleNamespace(
                        catalog_item_id=first.id,
                        units=3,
                        revenue=Decimal("300.00"),
                    ),
                ],
            ),
            business_id=business_id,
            data=CampaignGenerateRequest(
                goal="Promote the strongest products",
                catalog_scope="recommended",
            ),
        )

        self.assertEqual(scope, "recommended")
        self.assertEqual(products[0].id, first.id)

    async def test_unrelated_tenant_metrics_never_affect_rank(self):
        business_id = uuid4()
        first = _product(business_id, "A")
        second = _product(business_id, "B")

        unrelated_id = uuid4()

        ranked = await _rank_campaign_catalog_recommendations(
            _Session(
                [first, second],
                order_rows=[
                    SimpleNamespace(
                        catalog_item_id=unrelated_id,
                        units=9999,
                        revenue=Decimal("999999.00"),
                    ),
                ],
                performance_rows=[
                    SimpleNamespace(
                        catalog_item_id=unrelated_id,
                        spend=Decimal("1.00"),
                        conversions=Decimal("999.0000"),
                        conversion_value=Decimal("999999.00"),
                    ),
                ],
            ),
            business_id=business_id,
            products=[first, second],
        )

        self.assertEqual(
            {item.id for item in ranked},
            {first.id, second.id},
        )


if __name__ == "__main__":
    unittest.main()
