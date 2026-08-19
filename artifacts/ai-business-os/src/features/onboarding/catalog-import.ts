export const expectedCatalogColumns = [
  "Name",
  "SKU",
  "Price",
  "Stock / Availability",
  "Category",
  "Description",
] as const;

export type CatalogImportMethod =
  "manual" | "upload" | "store" | "paste" | "skip";

export type CatalogStoreProvider =
  "Shopify" | "WooCommerce" | "Custom Store / API";

export type CatalogDraftProduct = {
  id: string;
  name: string;
  sku: string;
  price: string;
  availability: string;
  category: string;
  description: string;
};

export type CatalogPreviewRow = CatalogDraftProduct & {
  errors: string[];
};

export type CatalogDraft = {
  method: CatalogImportMethod | null;
  confirmed: boolean;
  sourceName: string;
  storeProvider: CatalogStoreProvider | null;
  pastedText: string;
  products: CatalogDraftProduct[];
};

export function createBlankCatalogProduct(
  id = `product-${Date.now()}`,
): CatalogDraftProduct {
  return {
    id,
    name: "",
    sku: "",
    price: "",
    availability: "In stock",
    category: "",
    description: "",
  };
}

export function createInitialCatalogDraft(): CatalogDraft {
  return {
    method: null,
    confirmed: false,
    sourceName: "",
    storeProvider: null,
    pastedText: "",
    products: [createBlankCatalogProduct("product-1")],
  };
}

function parseCsvLine(line: string) {
  const values: string[] = [];
  let current = "";
  let quoted = false;

  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (character === '"') {
      if (quoted && line[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      values.push(current.trim());
      current = "";
    } else {
      current += character;
    }
  }
  values.push(current.trim());
  return values;
}

function normalizedHeader(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

const headerAliases: Record<keyof Omit<CatalogDraftProduct, "id">, string[]> = {
  name: ["name", "product", "productname", "service", "title"],
  sku: ["sku", "productsku", "code"],
  price: ["price", "unitprice", "cost"],
  availability: [
    "stock",
    "availability",
    "stockavailability",
    "inventory",
    "quantity",
  ],
  category: ["category", "type", "collection"],
  description: ["description", "details", "summary"],
};

function findColumn(headers: string[], field: keyof typeof headerAliases) {
  return headers.findIndex((header) =>
    headerAliases[field].includes(normalizedHeader(header)),
  );
}

function productFromValues(
  values: string[],
  indexes: Record<keyof Omit<CatalogDraftProduct, "id">, number>,
  rowIndex: number,
): CatalogPreviewRow {
  const read = (field: keyof typeof indexes) => {
    const index = indexes[field];
    return index >= 0 ? (values[index] ?? "").trim() : "";
  };
  const name = read("name");
  const rawPrice = read("price");
  const normalizedPrice = rawPrice.replace(/[$£€₨,\s]/g, "");
  const errors: string[] = [];
  if (!name) errors.push("Missing name");
  if (
    rawPrice &&
    (!Number.isFinite(Number(normalizedPrice)) || Number(normalizedPrice) < 0)
  ) {
    errors.push("Invalid price");
  }

  return {
    id: `imported-product-${Date.now()}-${rowIndex}`,
    name,
    sku: read("sku"),
    price: rawPrice ? normalizedPrice : "0",
    availability: read("availability") || "In stock",
    category: read("category"),
    description: read("description"),
    errors,
  };
}

export function parseCatalogRows(input: string): CatalogPreviewRow[] {
  const lines = input
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return [];

  const rows = lines.map((line) =>
    line.includes("\t")
      ? line.split("\t").map((value) => value.trim())
      : parseCsvLine(line),
  );
  const possibleHeaders = rows[0];
  const hasHeaders = possibleHeaders.some((header) =>
    Object.values(headerAliases).flat().includes(normalizedHeader(header)),
  );
  const indexes = hasHeaders
    ? {
        name: findColumn(possibleHeaders, "name"),
        sku: findColumn(possibleHeaders, "sku"),
        price: findColumn(possibleHeaders, "price"),
        availability: findColumn(possibleHeaders, "availability"),
        category: findColumn(possibleHeaders, "category"),
        description: findColumn(possibleHeaders, "description"),
      }
    : {
        name: 0,
        sku: 1,
        price: 2,
        availability: 3,
        category: 4,
        description: 5,
      };

  return (hasHeaders ? rows.slice(1) : rows).map((values, index) =>
    productFromValues(values, indexes, index),
  );
}

export function createWorkbookPrototypeRows(): CatalogPreviewRow[] {
  return parseCatalogRows(
    [
      expectedCatalogColumns.join("\t"),
      "Harvest Box\tBOX-001\t28\tIn stock\tSeasonal\tWeekly selection of fresh products",
      "Raw Honey\tHNY-012\t12\t18 available\tPantry\tLocal raw honey in a glass jar",
      "Farm Tour\tTOUR-01\t45\tBooking required\tServices\tGuided weekend farm visit",
    ].join("\n"),
  );
}

export function validCatalogProducts(rows: CatalogPreviewRow[]) {
  return rows
    .filter((row) => row.errors.length === 0)
    .map(({ errors: _errors, ...product }) => product);
}
