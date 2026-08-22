import type {
  CatalogImportPreviewResponse,
  CatalogItem,
  CatalogItemCreate,
  CatalogItemStatus,
  CatalogItemType,
  CatalogItemUpdate,
} from "../../services/api-types.ts";

export const CATALOG_IMPORT_MAX_ROWS = 2_000;
export const CATALOG_IMPORT_MAX_BYTES = 10 * 1024 * 1024;

export type CatalogItemDraft = {
  itemType: CatalogItemType;
  name: string;
  description: string;
  sku: string;
  price: string;
  status: CatalogItemStatus;
};

export function createCatalogItemDraft(item?: CatalogItem): CatalogItemDraft {
  return {
    itemType: item?.item_type ?? "product",
    name: item?.name ?? "",
    description: item?.description ?? "",
    sku: item?.sku ?? "",
    price: item?.price ?? "",
    status: item?.status ?? "active",
  };
}

function optionalText(value: string) {
  const normalized = value.trim();
  return normalized || null;
}

function optionalSku(value: string) {
  const normalized = value.trim().toUpperCase();
  return normalized || null;
}

function optionalPrice(value: string) {
  const normalized = value.trim();
  return normalized || null;
}

export function validateCatalogItemDraft(
  draft: CatalogItemDraft,
): string | null {
  if (!draft.name.trim()) return "Add a name to continue.";
  const price = draft.price.trim();
  if (price && !/^\d+(?:\.\d{1,2})?$/.test(price)) {
    return "Use a positive price with no more than two decimal places.";
  }
  return null;
}

export function catalogCreateFromDraft(
  draft: CatalogItemDraft,
): CatalogItemCreate {
  return {
    item_type: draft.itemType,
    name: draft.name.trim(),
    description: optionalText(draft.description),
    sku: optionalSku(draft.sku),
    price: optionalPrice(draft.price),
    status: draft.status,
  };
}

export function catalogUpdateFromDraft(
  item: CatalogItem,
  draft: CatalogItemDraft,
): CatalogItemUpdate {
  const update: CatalogItemUpdate = {};
  const name = draft.name.trim();
  const description = optionalText(draft.description);
  const sku = optionalSku(draft.sku);
  const price = optionalPrice(draft.price);
  if (draft.itemType !== item.item_type) update.item_type = draft.itemType;
  if (name !== item.name) update.name = name;
  if (description !== item.description) update.description = description;
  if (sku !== item.sku) update.sku = sku;
  if (price !== item.price) update.price = price;
  if (draft.status !== item.status) update.status = draft.status;
  return update;
}

export function isCurrentCatalogResponse(
  requestedBusinessId: string,
  requestedVersion: number,
  activeBusinessId: string,
  currentVersion: number,
) {
  return (
    requestedBusinessId === activeBusinessId &&
    requestedVersion === currentVersion
  );
}

export function formatCatalogPrice(
  price: string | null,
  currency: string,
  locale: string,
) {
  if (price === null) return "No price";
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice)) return `${currency} ${price}`;
  try {
    return new Intl.NumberFormat(locale || "en", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(numericPrice);
  } catch {
    return `${currency} ${price}`;
  }
}

export function catalogFileValidationMessage(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (extension !== "csv" && extension !== "xlsx") {
    return "Choose a .csv or .xlsx file. Legacy .xls files are not supported.";
  }
  if (file.size > CATALOG_IMPORT_MAX_BYTES) {
    return "Choose a file no larger than 10 MB.";
  }
  return null;
}

export function pasteListLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function csvCell(value: string) {
  return `"${value.replaceAll('"', '""')}"`;
}

export function createPasteCatalogFile(
  value: string,
  itemType: CatalogItemType,
) {
  const lines = pasteListLines(value);
  if (!lines.length) throw new Error("Paste at least one item name.");
  if (lines.length > CATALOG_IMPORT_MAX_ROWS) {
    throw new Error("Paste no more than 2,000 items at a time.");
  }
  return createCatalogItemsFile(
    lines.map((name) => ({ item_type: itemType, name })),
    "pasted-catalog.csv",
  );
}

export function createCatalogItemsFile(
  items: CatalogItemCreate[],
  filename = "catalog-items.csv",
) {
  if (!items.length) throw new Error("Add at least one catalog item.");
  if (items.length > CATALOG_IMPORT_MAX_ROWS) {
    throw new Error("Add no more than 2,000 items at a time.");
  }
  const header = "name,item_type,description,sku,price,status";
  const rows = items.map((item) =>
    [
      item.name,
      item.item_type,
      item.description ?? "",
      item.sku ?? "",
      item.price ?? "",
      item.status ?? "active",
    ]
      .map((value) => csvCell(value))
      .join(","),
  );
  return new File([[header, ...rows].join("\n"), "\n"], filename, {
    type: "text/csv",
  });
}

export function canImportCatalogPreview(
  preview: CatalogImportPreviewResponse | null,
) {
  return Boolean(
    preview && preview.valid_rows > 0 && preview.invalid_rows === 0,
  );
}
