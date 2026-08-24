import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  ApiClient,
  ApiError,
  ApiNetworkError,
} from "../services/api-client.ts";
import {
  catalogImportPreviewFromError,
  commitCatalogImportAndReload,
  createCatalogApi,
  humanizeCatalogError,
  type CatalogApi,
} from "../services/catalog.ts";
import type {
  CatalogImportPreviewResponse,
  CatalogItem,
  UserLoginResponse,
  UserPublic,
} from "../services/api-types.ts";
import {
  CATALOG_IMPORT_MAX_BYTES,
  canImportCatalogPreview,
  catalogCreateFromDraft,
  catalogFileValidationMessage,
  catalogUpdateFromDraft,
  createCatalogItemDraft,
  createPasteCatalogFile,
  formatCatalogPrice,
  isCurrentCatalogResponse,
  pasteListLines,
} from "../features/catalog/catalog-model.ts";
import {
  catalogDraftForSessionStorage,
  createInitialCatalogDraft,
} from "../features/onboarding/catalog-import.ts";
import {
  CatalogPersistenceAfterBusinessCreationError,
  persistOnboardingCatalog,
  saveOnboardingWorkspace,
  type OnboardingForm,
} from "../features/onboarding/onboarding-save.ts";
import { businessFromSummary } from "../services/business-model.ts";
import { workspaceRepository } from "../services/workspace-repository.ts";
import type { WorkspaceData } from "../types/workspace.ts";

const businessA = "20000000-0000-4000-8000-000000000001";
const businessB = "20000000-0000-4000-8000-000000000002";

const user: UserPublic = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "catalog-owner@example.test",
  first_name: "Catalog",
  last_name: "Owner",
  status: "active",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

const item: CatalogItem = {
  id: "30000000-0000-4000-8000-000000000001",
  business_id: businessA,
  item_type: "product",
  name: "Server Product",
  description: "Server description",
  sku: "SERVER-001",
  price: "19.99",
  status: "active",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const preview: CatalogImportPreviewResponse = {
  file: { filename: "catalog.csv", file_type: "csv", size_bytes: 40 },
  detected_columns: {
    name: "Product Name",
    item_type: "Type",
    sku: "Code",
    price: "Unit Price",
  },
  total_rows: 2,
  valid_rows: 2,
  invalid_rows: 0,
  preview_rows: [
    {
      row: 2,
      normalized: {
        name: "One",
        item_type: "product",
        description: null,
        sku: "ONE",
        price: "10.00",
        status: "active",
      },
      item: {
        item_type: "product",
        name: "One",
        description: null,
        sku: "ONE",
        price: "10.00",
        status: "active",
      },
      errors: [],
    },
  ],
  errors: [],
  preview_limit: 100,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function loginResponse(): UserLoginResponse {
  return {
    access_token: "memory-only-catalog-token",
    token_type: "bearer",
    expires_in: 900,
    user,
  };
}

async function authenticatedCatalogApi(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });
  return createCatalogApi(client);
}

test("catalog loads the active real business UUID and server filters", async () => {
  const requests: string[] = [];
  const api = await authenticatedCatalogApi(async (input) => {
    const url = String(input);
    if (url.endsWith("/login")) return jsonResponse(loginResponse());
    requests.push(url);
    return jsonResponse([item]);
  });

  const loaded = await api.listCatalogItems(businessA, {
    itemType: "product",
    status: "archived",
  });

  const url = new URL(requests[0]);
  assert.equal(url.pathname, `/api/v1/businesses/${businessA}/catalog`);
  assert.equal(url.searchParams.get("item_type"), "product");
  assert.equal(url.searchParams.get("status"), "archived");
  assert.deepEqual(loaded, [item]);
});

test("empty catalog responses remain empty with no fabricated records", async () => {
  const api = await authenticatedCatalogApi(async (input) =>
    String(input).endsWith("/login")
      ? jsonResponse(loginResponse())
      : jsonResponse([]),
  );

  assert.deepEqual(await api.listCatalogItems(businessA), []);
});

test("business switching requests each tenant and rejects stale response identity", async () => {
  const paths: string[] = [];
  const api = await authenticatedCatalogApi(async (input) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    paths.push(new URL(String(input)).pathname);
    return jsonResponse([]);
  });

  await api.listCatalogItems(businessA);
  await api.listCatalogItems(businessB);

  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/catalog`,
    `/api/v1/businesses/${businessB}/catalog`,
  ]);
  assert.equal(isCurrentCatalogResponse(businessA, 1, businessB, 2), false);
  assert.equal(isCurrentCatalogResponse(businessB, 2, businessB, 2), true);
});

test("business-switch cancellation stays intentional and never becomes network state", async () => {
  const controller = new AbortController();
  let markRequestStarted!: () => void;
  const requestStarted = new Promise<void>((resolve) => {
    markRequestStarted = resolve;
  });
  const abortError = new DOMException(
    "Business A request was cancelled.",
    "AbortError",
  );
  const api = await authenticatedCatalogApi(async (input, init) => {
    if (String(input).endsWith("/login")) {
      return jsonResponse(loginResponse());
    }
    return new Promise<Response>((_resolve, reject) => {
      markRequestStarted();
      init?.signal?.addEventListener("abort", () => reject(abortError), {
        once: true,
      });
    });
  });

  const businessARequest = api.listCatalogItems(
    businessA,
    {},
    controller.signal,
  );
  await requestStarted;
  controller.abort();

  let falseNetworkState = false;
  await assert.rejects(businessARequest, (error: unknown) => {
    falseNetworkState = error instanceof ApiNetworkError;
    assert.equal(error, abortError);
    assert.equal((error as Error).name, "AbortError");
    return true;
  });
  assert.equal(falseNetworkState, false);
});

test("manual product and service creation use server IDs and exact decimal text", async () => {
  const requests: RequestInit[] = [];
  let createCount = 0;
  const api = await authenticatedCatalogApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    requests.push(init ?? {});
    createCount += 1;
    const body = JSON.parse(String(init?.body)) as {
      item_type: "product" | "service";
      price: string | null;
    };
    return jsonResponse(
      {
        ...item,
        id: `server-generated-${createCount}`,
        item_type: body.item_type,
        price: body.price,
      },
      201,
    );
  });

  const product = await api.createCatalogItem(businessA, {
    item_type: "product",
    name: "Product",
    price: "19.99",
  });
  const service = await api.createCatalogItem(businessA, {
    item_type: "service",
    name: "Service",
    price: null,
  });

  assert.equal(product.id, "server-generated-1");
  assert.equal(product.price, "19.99");
  assert.equal(service.id, "server-generated-2");
  assert.equal(service.item_type, "service");
  assert.equal(service.price, null);
  for (const request of requests) {
    const body = JSON.parse(String(request.body)) as Record<string, unknown>;
    for (const forbidden of [
      "business_id",
      "currency",
      "inventory",
      "created_at",
      "updated_at",
    ]) {
      assert.equal(Object.hasOwn(body, forbidden), false);
    }
  }
});

test("blank price creates null and never silently becomes zero", () => {
  assert.deepEqual(
    catalogCreateFromDraft({
      ...createCatalogItemDraft(),
      name: "Priceless service",
      itemType: "service",
      price: "   ",
    }),
    {
      item_type: "service",
      name: "Priceless service",
      description: null,
      sku: null,
      price: null,
      status: "active",
    },
  );
});

test("money display uses the active business currency", () => {
  assert.match(formatCatalogPrice("19.99", "PKR", "en-US"), /PKR/);
  assert.match(formatCatalogPrice("19.99", "AED", "en-US"), /AED/);
  assert.equal(formatCatalogPrice(null, "USD", "en-US"), "No price");
});

test("duplicate SKU errors use safe business-scoped UX", () => {
  const message = humanizeCatalogError(
    new ApiError(409, { detail: "uq_catalog_items_business_sku" }),
  );
  assert.equal(message, "This SKU is already used in this business.");
  assert.equal(message.includes("uq_catalog"), false);
});

test("PATCH builder sends only intended changes and explicit null clears fields", () => {
  const draft = createCatalogItemDraft(item);
  const changes = catalogUpdateFromDraft(item, {
    ...draft,
    name: "Updated",
    description: "",
    price: "",
  });

  assert.deepEqual(changes, {
    name: "Updated",
    description: null,
    price: null,
  });
  assert.equal(Object.hasOwn(changes, "sku"), false);
  assert.equal(Object.hasOwn(changes, "item_type"), false);
});

test("catalog PATCH and archive use tenant item paths and correct methods", async () => {
  const requests: Array<{ path: string; init?: RequestInit }> = [];
  const api = await authenticatedCatalogApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    requests.push({ path: new URL(String(input)).pathname, init });
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return jsonResponse({ ...item, description: null });
  });

  await api.updateCatalogItem(businessA, item.id, { description: null });
  await api.archiveCatalogItem(businessA, item.id);

  const expectedPath = `/api/v1/businesses/${businessA}/catalog/${item.id}`;
  assert.deepEqual(
    requests.map((request) => [request.path, request.init?.method]),
    [
      [expectedPath, "PATCH"],
      [expectedPath, "DELETE"],
    ],
  );
  assert.deepEqual(JSON.parse(String(requests[0].init?.body)), {
    description: null,
  });
});

test("CSV and XLSX pass client validation while XLS is rejected", () => {
  assert.equal(
    catalogFileValidationMessage(new File(["name\nOne\n"], "catalog.csv")),
    null,
  );
  assert.equal(
    catalogFileValidationMessage(new File(["xlsx"], "catalog.xlsx")),
    null,
  );
  assert.match(
    catalogFileValidationMessage(new File(["xls"], "catalog.xls")) ?? "",
    /not supported/,
  );
});

test("files over 10 MB are rejected before API use", () => {
  const oversized = new File(
    [new Uint8Array(CATALOG_IMPORT_MAX_BYTES + 1)],
    "catalog.csv",
  );
  assert.match(catalogFileValidationMessage(oversized) ?? "", /10 MB/);
});

test("preview and import use authenticated multipart with the same selected file", async () => {
  const uploaded: File[] = [];
  const paths: string[] = [];
  const api = await authenticatedCatalogApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    paths.push(new URL(String(input)).pathname);
    assert.ok(init?.body instanceof FormData);
    const file = (init?.body as FormData).get("file");
    assert.ok(file instanceof File);
    uploaded.push(file);
    return paths.at(-1)?.endsWith("/preview")
      ? jsonResponse(preview)
      : jsonResponse({ created_count: 2, total_rows: 2 }, 201);
  });
  const file = new File(["name\nOne\nTwo\n"], "catalog.csv", {
    type: "text/csv",
  });

  const checked = await api.previewCatalogImport(businessA, file);
  const result = await api.importCatalogFile(businessA, file);

  assert.equal(checked.detected_columns.name, "Product Name");
  assert.equal(result.created_count, 2);
  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/catalog/import/preview`,
    `/api/v1/businesses/${businessA}/catalog/import`,
  ]);
  assert.equal(uploaded[0].name, file.name);
  assert.equal(uploaded[1].name, file.name);
  assert.equal(await uploaded[0].text(), await uploaded[1].text());
});

test("preview row errors remain human-oriented and disable atomic import", () => {
  const invalid = {
    ...preview,
    valid_rows: 1,
    invalid_rows: 1,
    errors: [{ row: 7, field: "price", message: "price must be at least 0" }],
  };
  assert.equal(invalid.errors[0].row, 7);
  assert.equal(canImportCatalogPreview(invalid), false);
  assert.equal(canImportCatalogPreview(preview), true);
});

test("atomic 422 import preview is recovered without creating local records", () => {
  const invalid = {
    ...preview,
    valid_rows: 1,
    invalid_rows: 1,
    errors: [{ row: 3, field: "price", message: "price must be at least 0" }],
  };
  const recovered = catalogImportPreviewFromError(new ApiError(422, invalid));
  const localItems: CatalogItem[] = [];

  assert.deepEqual(recovered, invalid);
  assert.deepEqual(localItems, []);
  assert.match(humanizeCatalogError(new ApiError(422, invalid)), /Nothing/);
});

test("successful import reloads the authoritative server catalog", async () => {
  const events: string[] = [];
  const api = {
    importCatalogFile: async (_businessId: string, _file: File) => {
      events.push("import");
      return { created_count: 2, total_rows: 2 };
    },
    listCatalogItems: async () => {
      events.push("list");
      return [item];
    },
  } as CatalogApi;
  const file = new File(["name\nOne\n"], "catalog.csv");

  const committed = await commitCatalogImportAndReload(api, businessA, file);

  assert.deepEqual(events, ["import", "list"]);
  assert.equal(committed.items[0].id, item.id);
});

test("network failures preserve the caller's selected File for retry", async () => {
  const file = new File(["name\nRetry\n"], "retry.csv");
  const selected = file;
  const api = {
    importCatalogFile: async () => {
      throw new ApiNetworkError();
    },
  } as CatalogApi;

  await assert.rejects(api.importCatalogFile(businessA, selected));
  assert.equal(selected, file);
  assert.equal(await selected.text(), "name\nRetry\n");
});

test("paste list ignores blanks, quotes CSV safely, and keeps formulas inert", async () => {
  const file = createPasteCatalogFile(
    ' Apples \n\n"Premium" Bananas\n=NOT_EXECUTED()',
    "product",
  );
  const csv = await file.text();

  assert.deepEqual(
    pasteListLines(' Apples \n\n"Premium" Bananas\n=NOT_EXECUTED()'),
    ["Apples", '"Premium" Bananas', "=NOT_EXECUTED()"],
  );
  assert.match(csv, /"""Premium"" Bananas"/);
  assert.match(csv, /"=NOT_EXECUTED\(\)"/);
  assert.equal(csv.includes("undefined"), false);
  assert.equal(csv.split("\n").filter(Boolean).length, 4);
});

test("paste product and service modes flow through real preview/import", async () => {
  for (const itemType of ["product", "service"] as const) {
    const events: string[] = [];
    const api = {
      previewCatalogImport: async (_businessId: string, file: File) => {
        events.push(`preview:${await file.text()}`);
        return preview;
      },
      importCatalogFile: async (_businessId: string, file: File) => {
        events.push(`import:${await file.text()}`);
        return { created_count: 1, total_rows: 1 };
      },
      listCatalogItems: async () => {
        events.push("list");
        return [item];
      },
    } as CatalogApi;
    const catalog = {
      ...createInitialCatalogDraft(),
      method: "paste" as const,
      confirmed: true,
      defaultItemType: itemType,
    };
    const file = createPasteCatalogFile("One", itemType);

    await persistOnboardingCatalog(api, businessA, catalog, file);

    assert.equal(events[0].startsWith("preview:"), true);
    assert.equal(events[1].startsWith("import:"), true);
    assert.match(events[0], new RegExp(`,"${itemType}"`));
    assert.deepEqual(events.slice(2), ["list"]);
  }
});

test("paste list enforces the backend-aligned 2,000 row limit", () => {
  const twoThousand = Array.from(
    { length: 2_000 },
    (_, index) => `Item ${index}`,
  );
  assert.doesNotThrow(() =>
    createPasteCatalogFile(twoThousand.join("\n"), "product"),
  );
  assert.throws(
    () =>
      createPasteCatalogFile(
        [...twoThousand, "One too many"].join("\n"),
        "product",
      ),
    /2,000/,
  );
});

test("onboarding creates business before preview/import and reloads real items", async () => {
  const events: string[] = [];
  const business = businessFromSummary({
    id: businessA,
    name: "Catalog Business",
    slug: "catalog-business",
    business_type: "other",
    status: "active",
    timezone: "UTC",
    currency: "USD",
    locale: "en",
    membership_role: "owner",
    created_at: "2026-01-01T00:00:00Z",
  });
  const file = new File(["name\nOne\n"], "catalog.csv");
  const catalog = {
    ...createInitialCatalogDraft(),
    method: "upload" as const,
    confirmed: true,
  };
  const api = {
    previewCatalogImport: async () => {
      events.push("preview");
      return preview;
    },
    importCatalogFile: async () => {
      events.push("import");
      return { created_count: 2, total_rows: 2 };
    },
    listCatalogItems: async () => {
      events.push("list");
      return [item];
    },
  } as CatalogApi;

  await saveOnboardingWorkspace(onboardingForm(), catalog, businessA, {
    createBusiness: async (_input, id) => {
      assert.equal(id, businessA);
      events.push("business");
      return business;
    },
    saveCatalog: async (created, currentCatalog) => {
      await persistOnboardingCatalog(api, created.id, currentCatalog, file);
    },
  });

  assert.deepEqual(events, ["business", "preview", "import", "list"]);
});

test("onboarding persists catalog only for industries whose workspace exposes catalog", async () => {
  const business = businessFromSummary({
    id: businessA,
    name: "Industry Onboarding",
    slug: "industry-onboarding",
    business_type: "other",
    status: "active",
    timezone: "UTC",
    currency: "USD",
    locale: "en",
    membership_role: "owner",
    created_at: "2026-01-01T00:00:00Z",
  });

  const catalog = {
    ...createInitialCatalogDraft(),
    method: "skip" as const,
  };

  const hiddenIndustries: OnboardingForm["industry"][] = [
    "Real Estate",
    "Hospital",
    "Clinic",
    "Medical Practice",
    "Dental",
    "Professional Services",
  ];

  for (const industry of hiddenIndustries) {
    let saveCatalogCalls = 0;

    await saveOnboardingWorkspace(
      {
        ...onboardingForm(),
        industry,
      },
      catalog,
      businessA,
      {
        createBusiness: async () => business,
        saveCatalog: async () => {
          saveCatalogCalls += 1;
        },
      },
    );

    assert.equal(
      saveCatalogCalls,
      0,
      `${industry} must not persist a commerce catalog during onboarding`,
    );
  }

  const catalogIndustries: OnboardingForm["industry"][] = [
    "Farm/Agriculture",
    "E-commerce",
  ];

  for (const industry of catalogIndustries) {
    let saveCatalogCalls = 0;

    await saveOnboardingWorkspace(
      {
        ...onboardingForm(),
        industry,
      },
      catalog,
      businessA,
      {
        createBusiness: async () => business,
        saveCatalog: async () => {
          saveCatalogCalls += 1;
        },
      },
    );

    assert.equal(
      saveCatalogCalls,
      1,
      `${industry} must retain catalog onboarding persistence`,
    );
  }
});

test("manual onboarding drafts use the atomic import service", async () => {
  let uploadedCsv = "";
  const catalog = {
    ...createInitialCatalogDraft(),
    method: "manual" as const,
    products: [
      {
        ...createInitialCatalogDraft().products[0],
        name: "Consultation",
        itemType: "service" as const,
        price: "75.00",
      },
    ],
  };
  const api = {
    previewCatalogImport: async (_businessId: string, file: File) => {
      uploadedCsv = await file.text();
      return { ...preview, total_rows: 1, valid_rows: 1 };
    },
    importCatalogFile: async () => ({ created_count: 1, total_rows: 1 }),
    listCatalogItems: async () => [item],
  } as CatalogApi;

  await persistOnboardingCatalog(api, businessA, catalog, null);

  assert.match(uploadedCsv, /"Consultation","service"/);
  assert.match(uploadedCsv, /"75\.00"/);
});

test("CSV and XLSX onboarding files both use the real backend unchanged", async () => {
  for (const file of [
    new File(["name\nOne\n"], "catalog.csv"),
    new File([new Uint8Array([80, 75, 3, 4])], "catalog.xlsx"),
  ]) {
    const seen: File[] = [];
    const api = {
      previewCatalogImport: async (_businessId: string, selected: File) => {
        seen.push(selected);
        return preview;
      },
      importCatalogFile: async (_businessId: string, selected: File) => {
        seen.push(selected);
        return { created_count: 2, total_rows: 2 };
      },
      listCatalogItems: async () => [item],
    } as CatalogApi;
    const catalog = {
      ...createInitialCatalogDraft(),
      method: "upload" as const,
      confirmed: true,
    };

    await persistOnboardingCatalog(api, businessA, catalog, file);

    assert.equal(seen[0], file);
    assert.equal(seen[1], file);
  }
});

test("catalog failure records business-saved state and retry keeps one UUID", async () => {
  const attemptedIds: string[] = [];
  let failCatalog = true;
  const business = businessFromSummary({
    id: businessA,
    name: "Retry Catalog",
    slug: "retry-catalog",
    business_type: "other",
    status: "active",
    timezone: "UTC",
    currency: "USD",
    locale: "en",
    membership_role: "owner",
    created_at: "2026-01-01T00:00:00Z",
  });
  const catalog = {
    ...createInitialCatalogDraft(),
    method: "skip" as const,
  };
  const adapters = {
    createBusiness: async (_input: unknown, id: string) => {
      attemptedIds.push(id);
      return business;
    },
    saveCatalog: async () => {
      if (failCatalog) {
        failCatalog = false;
        throw new ApiNetworkError();
      }
    },
  };

  await assert.rejects(
    saveOnboardingWorkspace(
      onboardingForm(),
      catalog,
      businessA,
      adapters as never,
    ),
    (error: unknown) =>
      error instanceof CatalogPersistenceAfterBusinessCreationError &&
      error.businessSaved &&
      error.stage === "catalog",
  );
  await saveOnboardingWorkspace(
    onboardingForm(),
    catalog,
    businessA,
    adapters as never,
  );

  assert.deepEqual(attemptedIds, [businessA, businessA]);
  assert.equal(new Set(attemptedIds).size, 1);
});

test("skip catalog makes no preview or import request", async () => {
  const events: string[] = [];
  const api = {
    previewCatalogImport: async () => {
      events.push("preview");
      return preview;
    },
    importCatalogFile: async () => {
      events.push("import");
      return { created_count: 0, total_rows: 0 };
    },
    listCatalogItems: async () => {
      events.push("list");
      return [];
    },
  } as CatalogApi;

  await persistOnboardingCatalog(
    api,
    businessA,
    { ...createInitialCatalogDraft(), method: "skip" },
    null,
  );

  assert.deepEqual(events, ["list"]);
});

test("onboarding session draft removes paste content and file authority", () => {
  const stored = catalogDraftForSessionStorage({
    ...createInitialCatalogDraft(),
    method: "paste",
    confirmed: true,
    pastedText: "Sensitive pasted item",
    sourceName: "Pasted catalog list",
    products: [
      {
        ...createInitialCatalogDraft().products[0],
        name: "Leftover manual draft",
      },
    ],
  });

  assert.equal(stored.pastedText, "");
  assert.equal(stored.sourceName, "");
  assert.equal(stored.confirmed, false);
  assert.deepEqual(stored.products, []);
  assert.equal("file" in stored, false);
});

test("workspace persistence strips catalog records from localStorage", () => {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true,
  });
  Object.defineProperty(globalThis, "window", {
    value: { dispatchEvent: () => true },
    configurable: true,
  });
  const businessId = "40000000-0000-4000-8000-000000000077";
  const current = workspaceRepository.get(businessId, "Other");
  const withCatalog: WorkspaceData = {
    ...current,
    catalog: {
      method: "manual",
      sourceName: "should-not-persist.csv",
      storeProvider: null,
      confirmedAt: new Date().toISOString(),
      items: [
        {
          id: "local-fake-id",
          name: "Must not persist",
          sku: "LOCAL",
          price: 1,
          availability: "fake",
          category: "fake",
          description: "fake",
        },
      ],
    },
  };

  workspaceRepository.set(businessId, withCatalog);
  const stored = [
    ...Array.from({ length: storage.length }, (_, index) => storage.key(index)),
  ]
    .filter((key): key is string => Boolean(key))
    .map((key) => storage.getItem(key) ?? "")
    .join("\n");

  assert.equal(stored.includes("Must not persist"), false);
  assert.equal(stored.includes("local-fake-id"), false);
  assert.equal(
    workspaceRepository.get(businessId, "Other").catalog.items.length,
    0,
  );
});

test("catalog cutover source has no fake fallback, scattered fetch, or durable file storage", async () => {
  const [screen, service, onboarding, repository, app] = await Promise.all([
    readFile(
      new URL(
        "../features/catalog/industry-workspace-page.tsx",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(new URL("../services/catalog.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../features/onboarding/onboarding-page.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../services/workspace-repository.ts", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../App.tsx", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(screen, /activeBusiness\?\.products|Date\.now\(\).*item/);
  assert.doesNotMatch(screen, /\bfetch\s*\(/);
  assert.doesNotMatch(service, /localStorage|sessionStorage/);
  assert.match(repository, /withoutCatalogAuthority/);
  assert.match(onboarding, /catalogDraftForSessionStorage/);
  assert.doesNotMatch(
    onboarding,
    /(?:local|session)Storage\.setItem\([^)]*(?:File|base64|pastedText)/,
  );
  assert.match(app, /function CatalogWorkspaceRoute/);
  assert.match(
    app,
    /isWorkspaceModuleVisible\(activeBusiness\?\.industry, "catalog"\)/,
  );
  assert.match(app, /profile\.catalogRoute === expectedRoute/);
  assert.match(app, /path="\/products"/);
  assert.match(app, /expectedRoute="\/products"/);
  assert.match(app, /path="\/properties"/);
  assert.match(app, /expectedRoute="\/properties"/);
  assert.match(app, /return enabled \? <IndustryWorkspacePage \/> : null/);
  assert.doesNotMatch(
    app,
    /path="\/products" component=\{workspaceModule\(IndustryWorkspacePage\)\}/,
  );
});

function onboardingForm(): OnboardingForm {
  return {
    name: "Catalog Business",
    industry: "Other",
    website: "",
    location: "",
    timezone: "UTC",
    currency: "USD",
    locale: "en",
    description: "",
    tone: "Clear",
    avoidKeywords: "",
    channels: [],
  };
}

class MemoryStorage implements Storage {
  private values = new Map<string, string>();

  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}
