from __future__ import annotations

import unittest
from uuid import uuid4

from app.schemas.marketing import CampaignPreflightResponse
from app.services.marketing_actions import (
    _campaign_internal_product_group_id,
)
from app.services.campaign_catalog_approval import (
    CatalogApprovalSnapshotError,
    approved_campaign_catalog_snapshot,
)


class CampaignCatalogApprovalSnapshotTests(
    unittest.TestCase
):
    def test_exact_snapshot_preserves_approved_offer_ids(
        self,
    ) -> None:
        first = uuid4()
        second = uuid4()

        snapshot = approved_campaign_catalog_snapshot(
            normalized_proposal={
                "catalog_scope": "all",
                "selected_products": [
                    {
                        "catalog_item_id": str(first),
                        "offer_id": "SKU-ORIGINAL-1",
                    },
                    {
                        "catalog_item_id": str(second),
                        "offer_id": "SKU-ORIGINAL-2",
                    },
                ],
            },
            durable_product_ids=[first, second],
        )

        self.assertEqual(snapshot.scope, "all")
        self.assertEqual(
            [
                item.offer_id
                for item in snapshot.products
            ],
            [
                "SKU-ORIGINAL-1",
                "SKU-ORIGINAL-2",
            ],
        )

    def test_snapshot_must_match_durable_product_ids(
        self,
    ) -> None:
        first = uuid4()
        second = uuid4()

        with self.assertRaisesRegex(
            CatalogApprovalSnapshotError,
            "campaign_catalog_snapshot_mismatch",
        ):
            approved_campaign_catalog_snapshot(
                normalized_proposal={
                    "catalog_scope": "selected",
                    "selected_products": [
                        {
                            "catalog_item_id": str(first),
                            "offer_id": "SKU-1",
                        }
                    ],
                },
                durable_product_ids=[
                    first,
                    second,
                ],
            )

    def test_duplicate_snapshot_product_fails_closed(
        self,
    ) -> None:
        first_product_id = uuid4()
        second_product_id = uuid4()

        with self.assertRaisesRegex(
            CatalogApprovalSnapshotError,
            "campaign_catalog_snapshot_duplicate",
        ):
            approved_campaign_catalog_snapshot(
                normalized_proposal={
                    "catalog_scope": "recommended",
                    "selected_products": [
                        {
                            "catalog_item_id": str(first_product_id),
                            "offer_id": "SKU-1",
                        },
                        {
                            "catalog_item_id": str(first_product_id),
                            "offer_id": "SKU-2",
                        },
                    ],
                },
                durable_product_ids=[
                    first_product_id,
                    second_product_id,
                ],
            )

    def test_missing_offer_reference_fails_closed(
        self,
    ) -> None:
        product_id = uuid4()

        with self.assertRaisesRegex(
            CatalogApprovalSnapshotError,
            "campaign_catalog_offer_reference_invalid",
        ):
            approved_campaign_catalog_snapshot(
                normalized_proposal={
                    "catalog_scope": "selected",
                    "selected_products": [
                        {
                            "catalog_item_id": str(product_id),
                            "offer_id": "",
                        }
                    ],
                },
                durable_product_ids=[product_id],
            )

    def test_provider_limit_never_broadens_scope(
        self,
    ) -> None:
        product_ids = [uuid4(), uuid4()]

        with self.assertRaisesRegex(
            CatalogApprovalSnapshotError,
            "campaign_catalog_provider_limit_exceeded",
        ):
            approved_campaign_catalog_snapshot(
                normalized_proposal={
                    "catalog_scope": "all",
                    "selected_products": [
                        {
                            "catalog_item_id": str(product_ids[0]),
                            "offer_id": "SKU-1",
                        },
                        {
                            "catalog_item_id": str(product_ids[1]),
                            "offer_id": "SKU-2",
                        },
                    ],
                },
                durable_product_ids=product_ids,
                max_products=1,
            )

    def test_campaign_product_group_id_is_read_from_persisted_proposal(
        self,
    ) -> None:
        group_id = uuid4()

        campaign = type(
            "CampaignStub",
            (),
            {
                "normalized_proposal": {
                    "product_group": {
                        "internal_product_group_id": str(
                            group_id
                        )
                    }
                }
            },
        )()

        self.assertEqual(
            _campaign_internal_product_group_id(
                campaign
            ),
            group_id,
        )

    def test_invalid_campaign_product_group_id_fails_closed(
        self,
    ) -> None:
        campaign = type(
            "CampaignStub",
            (),
            {
                "normalized_proposal": {
                    "product_group": {
                        "internal_product_group_id": "invalid"
                    }
                }
            },
        )()

        self.assertIsNone(
            _campaign_internal_product_group_id(
                campaign
            )
        )

    def test_preflight_schema_carries_exact_sync_target(
        self,
    ) -> None:
        group_id = uuid4()
        destination_id = uuid4()

        response = CampaignPreflightResponse(
            ready=False,
            provider="meta",
            selected_products=2,
            eligible_products=2,
            product_group_id=group_id,
            feed_destination_id=destination_id,
            issues=[
                {
                    "code": (
                        "campaign_product_set_sync_required"
                    ),
                    "message": "Sync exact product set.",
                    "blocking": True,
                }
            ],
        )

        self.assertEqual(
            response.product_group_id,
            group_id,
        )
        self.assertEqual(
            response.feed_destination_id,
            destination_id,
        )

    def test_non_catalog_campaign_returns_none_scope(
        self,
    ) -> None:
        snapshot = approved_campaign_catalog_snapshot(
            normalized_proposal={},
            durable_product_ids=[],
        )

        self.assertEqual(snapshot.scope, "none")
        self.assertEqual(snapshot.products, ())


if __name__ == "__main__":
    unittest.main()
