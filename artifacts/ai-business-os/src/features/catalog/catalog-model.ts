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
  compareAtPrice: string;
  currency: string;
  cost: string;
  productUrl: string;
  inventoryQuantity: string;
  availability: CatalogItem["availability"];
  brand: string;
  vendor: string;
  gtin: string;
  mpn: string;
  condition: CatalogItem["condition"];
  googleProductCategory: string;
  tags: string;
  published: boolean;
  status: CatalogItemStatus;
};

export function createCatalogItemDraft(item?: CatalogItem): CatalogItemDraft {
  return {
    itemType: item?.item_type ?? "product",
    name: item?.name ?? "",
    description: item?.description ?? "",
    sku: item?.sku ?? "",
    price: item?.price ?? "",
    compareAtPrice: item?.compare_at_price ?? "",
    currency: item?.currency ?? "",
    cost: item?.cost ?? "",
    productUrl: item?.product_url ?? "",
    inventoryQuantity: item?.inventory_quantity?.toString() ?? "",
    availability: item?.availability ?? "unknown",
    brand: item?.brand ?? "",
    vendor: item?.vendor ?? "",
    gtin: item?.gtin ?? "",
    mpn: item?.mpn ?? "",
    condition: item?.condition ?? "new",
    googleProductCategory: item?.google_product_category ?? "",
    tags: item?.tags?.join(", ") ?? "",
    published: item?.published ?? true,
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

function optionalCurrency(value: string) {
  const normalized = value.trim().toUpperCase();
  return normalized || null;
}

function optionalInteger(value: string) {
  const normalized = value.trim();
  return normalized ? Number(normalized) : null;
}

function normalizedTags(value: string) {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

export function validateCatalogItemDraft(
  draft: CatalogItemDraft,
): string | null {
  if (!draft.name.trim()) {
    return "Add a name to continue.";
  }

  const priceFields = [draft.price, draft.compareAtPrice, draft.cost];

  if (priceFields.some((value) => value.trim() && !/^\d{1,12}(?:\.\d{1,2})?$/.test(value.trim()))) {
    return "Use a positive price with no more than two decimal places.";
  }

  const currency = draft.currency.trim();
  if (currency && !/^[A-Za-z]{3}$/.test(currency)) {
    return "Use a three-letter currency code such as USD or PKR.";
  }

  const inventory = draft.inventoryQuantity.trim();
  if (inventory && (!/^\d+$/.test(inventory) || Number(inventory) > 2_147_483_647)) {
    return "Inventory must be a whole number from 0 through 2,147,483,647.";
  }

  const productUrl = draft.productUrl.trim();
  if (productUrl) {
    try {
      const parsed = new URL(productUrl);
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
        return "Use a public HTTP or HTTPS product URL without embedded credentials.";
      }
    } catch {
      return "Use a valid HTTP or HTTPS product URL.";
    }
  }

  const tags = normalizedTags(draft.tags);
  if (tags.length > 100 || tags.some((item) => item.length > 80)) {
    return "Use at most 100 tags, with no tag longer than 80 characters.";
  }

  return null;
}

export function catalogCreateFromDraft(
  draft: CatalogItemDraft,
): CatalogItemCreate {
  const value: CatalogItemCreate = {
    item_type: draft.itemType,
    name: draft.name.trim(),
    description: optionalText(draft.description),
    sku: optionalSku(draft.sku),
    price: optionalPrice(draft.price),
    status: draft.status,
  };
  const optionalCommerce = {
    compare_at_price: optionalPrice(draft.compareAtPrice),
    currency: optionalCurrency(draft.currency),
    cost: optionalPrice(draft.cost),
    product_url: optionalText(draft.productUrl),
    inventory_quantity: optionalInteger(draft.inventoryQuantity),
    brand: optionalText(draft.brand),
    vendor: optionalText(draft.vendor),
    gtin: optionalText(draft.gtin),
    mpn: optionalText(draft.mpn),
    google_product_category: optionalText(draft.googleProductCategory),
  };
  for (const [key, fieldValue] of Object.entries(optionalCommerce)) {
    if (fieldValue !== null) {
      Object.assign(value, { [key]: fieldValue });
    }
  }
  const tags = normalizedTags(draft.tags);
  if (tags.length) value.tags = tags;
  if (draft.availability !== "unknown") value.availability = draft.availability;
  if (draft.condition !== "new") value.condition = draft.condition;
  if (!draft.published) value.published = false;
  return value;
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
  const compareAtPrice = optionalPrice(draft.compareAtPrice);
  const currency = optionalCurrency(draft.currency);
  const cost = optionalPrice(draft.cost);
  const productUrl = optionalText(draft.productUrl);
  const inventoryQuantity = optionalInteger(draft.inventoryQuantity);
  const brand = optionalText(draft.brand);
  const vendor = optionalText(draft.vendor);
  const gtin = optionalText(draft.gtin);
  const mpn = optionalText(draft.mpn);
  const googleProductCategory = optionalText(draft.googleProductCategory);
  const tags = normalizedTags(draft.tags);

  if (draft.itemType !== item.item_type) {
    update.item_type = draft.itemType;
  }

  if (name !== item.name) {
    update.name = name;
  }

  if (description !== item.description) {
    update.description = description;
  }

  if (sku !== item.sku) {
    update.sku = sku;
  }

  if (price !== item.price) {
    update.price = price;
  }

  if (compareAtPrice !== (item.compare_at_price ?? null)) update.compare_at_price = compareAtPrice;
  if (currency !== (item.currency ?? null)) update.currency = currency;
  if (cost !== (item.cost ?? null)) update.cost = cost;
  if (productUrl !== (item.product_url ?? null)) update.product_url = productUrl;
  if (inventoryQuantity !== (item.inventory_quantity ?? null)) update.inventory_quantity = inventoryQuantity;
  if (draft.availability !== (item.availability ?? "unknown")) update.availability = draft.availability;
  if (brand !== (item.brand ?? null)) update.brand = brand;
  if (vendor !== (item.vendor ?? null)) update.vendor = vendor;
  if (gtin !== (item.gtin ?? null)) update.gtin = gtin;
  if (mpn !== (item.mpn ?? null)) update.mpn = mpn;
  if (draft.condition !== (item.condition ?? "new")) update.condition = draft.condition;
  if (googleProductCategory !== (item.google_product_category ?? null)) update.google_product_category = googleProductCategory;
  if (JSON.stringify(tags) !== JSON.stringify(item.tags ?? [])) update.tags = tags;
  if (draft.published !== (item.published ?? true)) update.published = draft.published;

  if (draft.status !== item.status) {
    update.status = draft.status;
  }

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
  if (price === null) {
    return "No price";
  }

  const numericPrice = Number(price);

  if (!Number.isFinite(numericPrice)) {
    return `${currency} ${price}`;
  }

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

/**
 * Detects the optional structured paste header:
 *
 * Name | Description | Price
 *
 * Header matching is intentionally case-insensitive.
 */
function isPasteHeader(line: string) {
  const fields = line
    .split("|")
    .map((field) => field.trim().toLowerCase());

  if (fields.length !== 3) {
    return false;
  }

  const [name, description, price] = fields;

  return (
    (name === "name" || name === "product name" || name === "item name") &&
    description === "description" &&
    price === "price"
  );
}

/**
 * CatalogItem.price is represented as decimal text.
 *
 * PostgreSQL NUMERIC(14, 2) allows at most twelve integer digits and
 * two fractional digits, so pasted values are validated before we generate
 * the intermediate CSV.
 */
function normalizePastedPrice(
  value: string,
  lineNumber: number,
): string | null {
  const normalized = value.trim();

  if (!normalized) {
    return null;
  }

  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(normalized)) {
    throw new Error(
      `Line ${lineNumber}: price must be a number with up to 12 digits before the decimal and up to 2 decimal places.`,
    );
  }

  return normalized;
}

/**
 * Supports two safe paste formats.
 *
 * Simple:
 * Premium Farm Eggs
 *
 * Structured:
 * Premium Farm Eggs | 12-pack fresh premium eggs | 8.99
 *
 * We deliberately do not try to guess arbitrary column structures.
 * CSV/XLSX upload remains the correct path for complex datasets.
 */
function parsePastedCatalogItem(
  line: string,
  itemType: CatalogItemType,
  lineNumber: number,
): CatalogItemCreate {
  if (!line.includes("|")) {
    const name = line.trim();

    if (!name) {
      throw new Error(`Line ${lineNumber}: product name is required.`);
    }

    return {
      item_type: itemType,
      name,
    };
  }

  const fields = line
    .split("|")
    .map((field) => field.trim());

  if (fields.length !== 3) {
    throw new Error(
      `Line ${lineNumber}: use the format "Name | Description | Price".`,
    );
  }

  const [name, description, price] = fields;

  if (!name) {
    throw new Error(`Line ${lineNumber}: product name is required.`);
  }

  return {
    item_type: itemType,
    name,
    description: description || null,
    price: normalizePastedPrice(price, lineNumber),
  };
}

export function createPasteCatalogFile(
  value: string,
  itemType: CatalogItemType,
) {
  const pastedLines = pasteListLines(value);

  if (!pastedLines.length) {
    throw new Error("Paste at least one item name.");
  }

  const hasHeader = isPasteHeader(pastedLines[0]);

  const lines = hasHeader
    ? pastedLines.slice(1)
    : pastedLines;

  if (!lines.length) {
    throw new Error("Paste at least one item below the header.");
  }

  if (lines.length > CATALOG_IMPORT_MAX_ROWS) {
    throw new Error("Paste no more than 2,000 items at a time.");
  }

  const startingLineNumber = hasHeader ? 2 : 1;

  const items = lines.map((line, index) =>
    parsePastedCatalogItem(
      line,
      itemType,
      startingLineNumber + index,
    ),
  );

  return createCatalogItemsFile(
    items,
    "pasted-catalog.csv",
  );
}

export function createCatalogItemsFile(
  items: CatalogItemCreate[],
  filename = "catalog-items.csv",
) {
  if (!items.length) {
    throw new Error("Add at least one catalog item.");
  }

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

  return new File(
    [[header, ...rows].join("\n"), "\n"],
    filename,
    {
      type: "text/csv",
    },
  );
}

export function canImportCatalogPreview(
  preview: CatalogImportPreviewResponse | null,
) {
  return Boolean(
    preview &&
      preview.valid_rows > 0 &&
      preview.invalid_rows === 0,
  );
}
