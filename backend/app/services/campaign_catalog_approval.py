from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal
from uuid import UUID


CatalogScope = Literal["none", "selected", "all", "recommended"]

MAX_PROVIDER_CATALOG_PRODUCTS = 1000


class CatalogApprovalSnapshotError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApprovedCatalogProduct:
    catalog_item_id: UUID
    offer_id: str


@dataclass(frozen=True, slots=True)
class ApprovedCatalogSnapshot:
    scope: CatalogScope
    products: tuple[ApprovedCatalogProduct, ...]


def approved_campaign_catalog_snapshot(
    *,
    normalized_proposal: object,
    durable_product_ids: Iterable[UUID],
    max_products: int = MAX_PROVIDER_CATALOG_PRODUCTS,
) -> ApprovedCatalogSnapshot:
    """
    Resolve the exact catalog scope the owner reviewed.

    CampaignProductSelection is the durable identity authority.
    normalized_proposal.selected_products is the proposal-time provider-offer
    snapshot. Both representations must agree before provider preparation.
    """
    durable_ids = tuple(durable_product_ids)
    durable_set = set(durable_ids)

    if len(durable_set) != len(durable_ids):
        raise CatalogApprovalSnapshotError(
            "campaign_catalog_selection_duplicate"
        )

    if not durable_ids:
        return ApprovedCatalogSnapshot(
            scope="none",
            products=(),
        )

    if (
        not isinstance(normalized_proposal, dict)
        or normalized_proposal.get("catalog_scope")
        not in {"selected", "all", "recommended"}
        or not isinstance(
            normalized_proposal.get("selected_products"),
            list,
        )
    ):
        raise CatalogApprovalSnapshotError(
            "campaign_catalog_snapshot_required"
        )

    scope = normalized_proposal["catalog_scope"]
    raw_products = normalized_proposal["selected_products"]

    products: list[ApprovedCatalogProduct] = []
    seen_ids: set[UUID] = set()
    seen_offer_ids: set[str] = set()

    for raw_product in raw_products:
        if not isinstance(raw_product, dict):
            raise CatalogApprovalSnapshotError(
                "campaign_catalog_snapshot_invalid"
            )

        raw_id = raw_product.get("catalog_item_id")
        offer_id = raw_product.get("offer_id")

        if not isinstance(raw_id, str):
            raise CatalogApprovalSnapshotError(
                "campaign_catalog_snapshot_invalid"
            )

        try:
            catalog_item_id = UUID(raw_id)
        except ValueError:
            raise CatalogApprovalSnapshotError(
                "campaign_catalog_snapshot_invalid"
            ) from None

        if (
            not isinstance(offer_id, str)
            or not offer_id.strip()
            or len(offer_id) > 255
        ):
            raise CatalogApprovalSnapshotError(
                "campaign_catalog_offer_reference_invalid"
            )

        offer_id = offer_id.strip()

        if (
            catalog_item_id in seen_ids
            or offer_id in seen_offer_ids
        ):
            raise CatalogApprovalSnapshotError(
                "campaign_catalog_snapshot_duplicate"
            )

        seen_ids.add(catalog_item_id)
        seen_offer_ids.add(offer_id)

        products.append(
            ApprovedCatalogProduct(
                catalog_item_id=catalog_item_id,
                offer_id=offer_id,
            )
        )

    if (
        len(products) != len(durable_ids)
        or seen_ids != durable_set
    ):
        raise CatalogApprovalSnapshotError(
            "campaign_catalog_snapshot_mismatch"
        )

    if len(products) > max_products:
        raise CatalogApprovalSnapshotError(
            "campaign_catalog_provider_limit_exceeded"
        )

    return ApprovedCatalogSnapshot(
        scope=scope,
        products=tuple(products),
    )
