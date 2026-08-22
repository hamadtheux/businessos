import {
  catalogCreateFromDraft,
  createCatalogItemDraft,
  type CatalogItemDraft,
} from "../catalog/catalog-model.ts";
import type { CatalogItemType } from "../../services/api-types.ts";

export const expectedCatalogColumns = [
  "Product Name",
  "Type",
  "Details",
  "SKU",
  "Unit Price",
  "Status",
] as const;

export type CatalogImportMethod =
  "manual" | "upload" | "store" | "paste" | "skip";

export type CatalogStoreProvider =
  "Shopify" | "WooCommerce" | "Custom Store / API";

export type CatalogDraftProduct = CatalogItemDraft & { id: string };

export type CatalogDraft = {
  method: CatalogImportMethod | null;
  confirmed: boolean;
  sourceName: string;
  storeProvider: CatalogStoreProvider | null;
  pastedText: string;
  defaultItemType: CatalogItemType;
  products: CatalogDraftProduct[];
};

export function createBlankCatalogProduct(
  id = `product-${Date.now()}`,
): CatalogDraftProduct {
  return { id, ...createCatalogItemDraft() };
}

export function createInitialCatalogDraft(): CatalogDraft {
  return {
    method: null,
    confirmed: false,
    sourceName: "",
    storeProvider: null,
    pastedText: "",
    defaultItemType: "product",
    products: [createBlankCatalogProduct("product-1")],
  };
}

export function manualCatalogItems(catalog: CatalogDraft) {
  return catalog.products
    .filter((product) => product.name.trim())
    .map(catalogCreateFromDraft);
}

export function catalogDraftForSessionStorage(
  catalog: CatalogDraft,
): CatalogDraft {
  const ephemeralMethod =
    catalog.method === "upload" || catalog.method === "paste";
  return {
    ...catalog,
    pastedText: "",
    sourceName: ephemeralMethod ? "" : catalog.sourceName,
    confirmed: ephemeralMethod ? false : catalog.confirmed,
    products: ephemeralMethod ? [] : catalog.products,
  };
}

export function restoreCatalogDraft(
  value: Partial<CatalogDraft> | undefined,
): CatalogDraft {
  const initial = createInitialCatalogDraft();
  if (!value) return initial;
  const products = (value.products ?? initial.products).map((product) => ({
    ...createBlankCatalogProduct(product.id),
    ...product,
    itemType: product.itemType ?? "product",
    status: product.status ?? "active",
  }));
  return catalogDraftForSessionStorage({
    ...initial,
    ...value,
    defaultItemType: value.defaultItemType ?? "product",
    products,
  });
}
