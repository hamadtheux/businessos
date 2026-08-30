import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  createOnboardingBusinessInput,
  createOnboardingBusinessId,
  humanizeOnboardingSaveError,
  saveOnboardingWorkspace,
  type OnboardingForm,
} from "../features/onboarding/onboarding-save.ts";
import { createInitialCatalogDraft } from "../features/onboarding/catalog-import.ts";
import {
  resetSettingsBranding,
  saveSettingsBranding,
} from "../features/settings/settings-branding-save.ts";
import {
  applyBrandingToBusinessList,
  brandingFromResponse,
  brandingUpdateFromIdentity,
  businessFromSummary,
  createBusinessOnboardingPayload,
  isCurrentBrandingResponse,
  resolveActiveBusinessId,
} from "../services/business-model.ts";
import { businessDraftRepository } from "../services/business-draft-repository.ts";
import {
  BusinessLogoUploadAfterCreationError,
  persistBusinessOnboarding,
} from "../services/business-onboarding-persistence.ts";
import {
  readBrandLogo,
  revokeBrandLogo,
  validateBrandLogo,
} from "../services/brand-logo.ts";
import { ApiError, ApiNetworkError } from "../services/api-client.ts";
import type { BusinessSummary } from "../services/api-types.ts";
import { BRAND_LOGO_MAX_BYTES, type Business } from "../types/business.ts";
import { deriveBrandTheme } from "./brand-theme.ts";
import {
  isApplicationBootstrapping,
  nextProtectedRoute,
} from "../services/app-routing.ts";
import {
  demoWorkspaceDataEnabled,
  workspaceRepository,
} from "../services/workspace-repository.ts";

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

const summary: BusinessSummary = {
  id: "20000000-0000-4000-8000-000000000001",
  name: "Server Business",
  slug: "server-business",
  business_type: "real estate",
  status: "active",
  timezone: "UTC",
  currency: "USD",
  locale: "en",
  membership_role: "owner",
  created_at: "2026-01-01T00:00:00Z",
};

test("logo preview validation remains bounded to safe raster formats", () => {
  assert.equal(
    validateBrandLogo({
      name: "brand.png",
      size: 400_000,
      type: "image/png",
    }),
    null,
  );
  assert.match(
    validateBrandLogo({
      name: "brand.svg",
      size: 4_000,
      type: "image/svg+xml",
    }) ?? "",
    /PNG, JPG, JPEG, or WebP/,
  );
  assert.match(
    validateBrandLogo({
      name: "huge.webp",
      size: BRAND_LOGO_MAX_BYTES + 1,
      type: "image/webp",
    }) ?? "",
    /smaller than 5 MB/,
  );
});

test("logo selection creates an ephemeral object URL preview", () => {
  const file = new File([new Uint8Array([1, 2, 3])], "logo.png", {
    type: "image/png",
  });

  const logo = readBrandLogo(file);

  assert.equal(logo.file, file);
  assert.match(logo.previewUrl, /^blob:/);
  assert.equal("dataUrl" in logo, false);
  revokeBrandLogo(logo);
});

test("saved colors and logos are removed from local workspace drafts", () => {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
  });
  businessDraftRepository.save(summary.id, {
    name: "Ignored local name",
    industry: "Real Estate",
    brandIdentity: {
      primaryColor: "#172554",
      secondaryColor: "#475B91",
      logoUrl: "https://untrusted.example.test/logo.png",
      logo: {
        previewUrl: "blob:preview-only",
        file: {} as File,
        name: "preview.png",
        mimeType: "image/png",
        size: 24,
      },
    },
  });

  const business = businessFromSummary(
    summary,
    businessDraftRepository.get(summary.id),
  );

  assert.equal(business.name, "Server Business");
  assert.equal(business.membershipRole, "owner");
  assert.equal(business.brandIdentity?.primaryColor, undefined);
  assert.equal(business.brandIdentity?.secondaryColor, undefined);
  assert.equal(business.brandIdentity?.logoUrl, undefined);
  assert.equal(business.brandIdentity?.logo, undefined);
  const stored = localStorage.getItem(
    `ai-business-os:business-ui-draft:${summary.id}:v1`,
  );
  assert.equal(stored?.includes("primaryColor"), false);
  assert.equal(stored?.includes("secondaryColor"), false);
  assert.equal(stored?.includes("logoUrl"), false);
  assert.equal(stored?.includes("previewUrl"), false);
  assert.equal(stored?.includes("data:image"), false);
});

test("legacy saved-color drafts are sanitized when first read", () => {
  const legacyBusinessId = "20000000-0000-4000-8000-000000000099";
  localStorage.setItem(
    `ai-business-os:business-ui-draft:${legacyBusinessId}:v1`,
    JSON.stringify({
      website: "",
      brandIdentity: {
        primaryColor: "#111111",
        secondaryColor: "#222222",
        accentColor: "#333333",
      },
    }),
  );

  const migrated = businessDraftRepository.get(legacyBusinessId);
  const stored = localStorage.getItem(
    `ai-business-os:business-ui-draft:${legacyBusinessId}:v1`,
  );

  assert.equal(migrated?.brandIdentity, undefined);
  assert.equal(stored?.includes("primaryColor"), false);
  assert.equal(stored?.includes("secondaryColor"), false);
  assert.equal(stored?.includes("accentColor"), false);
});

test("backend branding is the saved source and drives derived theme tokens", () => {
  const identity = brandingFromResponse(
    {
      primary_color: "#5B3FBB",
      secondary_color: "#475B91",
      accent_color: "#D15B83",
      logo_url: null,
    },
    undefined,
  );

  assert.equal(identity?.primaryColor, "#5B3FBB");
  assert.equal(deriveBrandTheme(identity).brandPrimary, "#5B3FBB");
  assert.deepEqual(brandingUpdateFromIdentity(identity ?? null), {
    primary_color: "#5B3FBB",
    secondary_color: "#475B91",
    accent_color: "#D15B83",
  });
});

test("onboarding colors restore from the branding GET representation", () => {
  const onboardingInput = {
    name: "Restored Studio",
    industry: "Other",
    brandIdentity: {
      primaryColor: "#5B3FBB",
      secondaryColor: "#475B91",
      accentColor: "#D15B83",
    },
  };
  const payload = createBusinessOnboardingPayload(summary.id, onboardingInput);
  const reloaded = applyBrandingToBusinessList(
    [businessFromSummary(summary)],
    summary.id,
    {
      primary_color: payload.branding?.primary_color ?? null,
      secondary_color: payload.branding?.secondary_color ?? null,
      accent_color: payload.branding?.accent_color ?? null,
      logo_url: null,
    },
  );

  assert.deepEqual(reloaded[0].brandIdentity, onboardingInput.brandIdentity);
});

test("business branding applies only to its tenant and stale switches are ignored", () => {
  const tenantA = businessFromSummary(summary);
  const tenantB = businessFromSummary({
    ...summary,
    id: "20000000-0000-4000-8000-000000000002",
    name: "Tenant B",
  });
  const branded = applyBrandingToBusinessList([tenantA, tenantB], tenantB.id, {
    primary_color: "#123456",
    secondary_color: null,
    accent_color: null,
    logo_url: "/api/v1/media/businesses/tenant-b/logo.png",
  });

  assert.equal(branded[0].brandIdentity, undefined);
  assert.equal(branded[1].brandIdentity?.primaryColor, "#123456");
  assert.equal(
    branded[1].brandIdentity?.logoUrl,
    "/api/v1/media/businesses/tenant-b/logo.png",
  );
  assert.equal(isCurrentBrandingResponse(tenantB.id, 2, tenantB.id, 2), true);
  assert.equal(isCurrentBrandingResponse(tenantA.id, 1, tenantB.id, 2), false);
});

test("branding update payload never includes local or server logo data", () => {
  const payload = brandingUpdateFromIdentity({
    primaryColor: "#123456",
    logoUrl: "https://storage.example.test/read-only.png",
    logo: {
      previewUrl: "blob:preview-only",
      file: {} as File,
      name: "preview.png",
      mimeType: "image/png",
      size: 24,
    },
  });

  assert.deepEqual(Object.keys(payload).sort(), [
    "accent_color",
    "primary_color",
    "secondary_color",
  ]);
  assert.equal(JSON.stringify(payload).includes("logo"), false);
  assert.deepEqual(brandingUpdateFromIdentity(null), {
    primary_color: null,
    secondary_color: null,
    accent_color: null,
  });
});

test("settings save and reset use the branding updater and retain a failed draft", async () => {
  const draft = {
    primaryColor: "#123456",
    secondaryColor: "#234567",
    accentColor: "#345678",
  };
  const original = structuredClone(draft);
  const updates: Array<{ id: string; identity: unknown }> = [];
  const business = businessFromSummary(summary);
  const update = async (id: string, identity: unknown) => {
    updates.push({ id, identity });
    return {
      ...business,
      brandIdentity:
        identity && typeof identity === "object"
          ? (identity as Business["brandIdentity"])
          : undefined,
    };
  };
  const upload = async () => business;
  const remove = async () => business;

  await saveSettingsBranding(
    summary.id,
    draft,
    true,
    update,
    upload,
    remove,
    undefined,
  );
  await resetSettingsBranding(summary.id, update);

  assert.equal(updates[0].id, summary.id);
  assert.deepEqual(updates[0].identity, {
    ...draft,
    logo: undefined,
    logoUrl: undefined,
  });
  assert.equal(updates[1].identity, null);

  await assert.rejects(
    saveSettingsBranding(
      summary.id,
      draft,
      true,
      async () => {
        throw new Error("save failed");
      },
      upload,
      remove,
      undefined,
    ),
  );
  assert.deepEqual(draft, original);
});

test("resetting workspace colors preserves the tenant business logo", async () => {
  const logoUrl = "/api/v1/media/businesses/current/logo.png";
  const business = {
    ...businessFromSummary(summary),
    brandIdentity: { logoUrl },
  };
  const updates: Array<unknown> = [];

  const reset = await resetSettingsBranding(
    summary.id,
    async (_businessId, identity) => {
      updates.push(identity);
      return business;
    },
  );

  assert.deepEqual(updates, [null]);
  assert.equal(reset.brandIdentity?.logoUrl, logoUrl);
});

test("failed logo replacement preserves its retry file and previous server logo", async () => {
  const previousLogoUrl = "/api/v1/media/businesses/current/old-logo.png";
  const file = new File([new Uint8Array([1, 2, 3])], "new-logo.png", {
    type: "image/png",
  });
  const draft = {
    primaryColor: "#123456",
    secondaryColor: "",
    accentColor: "",
    logo: {
      file,
      previewUrl: "blob:new-logo",
      name: file.name,
      mimeType: "image/png" as const,
      size: file.size,
    },
    logoUrl: previousLogoUrl,
  };
  const serverBusiness = {
    ...businessFromSummary(summary),
    brandIdentity: {
      primaryColor: "#123456",
      logoUrl: previousLogoUrl,
    },
  };

  await assert.rejects(
    saveSettingsBranding(
      summary.id,
      draft,
      true,
      async () => serverBusiness,
      async () => {
        throw new Error("upload failed");
      },
      async () => serverBusiness,
      previousLogoUrl,
    ),
  );

  assert.equal(draft.logo.file, file);
  assert.equal(draft.logo.previewUrl, "blob:new-logo");
  assert.equal(serverBusiness.brandIdentity.logoUrl, previousLogoUrl);
});

test("removing a saved logo invokes the real delete adapter", async () => {
  const previousLogoUrl = "/api/v1/media/businesses/current/logo.png";
  const serverBusiness = {
    ...businessFromSummary(summary),
    brandIdentity: { primaryColor: "#123456", logoUrl: previousLogoUrl },
  };
  const deletedIds: string[] = [];

  await saveSettingsBranding(
    summary.id,
    {
      primaryColor: "#123456",
      secondaryColor: "",
      accentColor: "",
    },
    true,
    async () => serverBusiness,
    async () => serverBusiness,
    async (businessId) => {
      deletedIds.push(businessId);
      return {
        ...serverBusiness,
        brandIdentity: { primaryColor: "#123456" },
      };
    },
    previousLogoUrl,
  );

  assert.deepEqual(deletedIds, [summary.id]);
});

test("successful logo upload replaces preview authority with backend URL", async () => {
  const file = new File([new Uint8Array([1, 2, 3])], "new-logo.png", {
    type: "image/png",
  });
  const uploadedBusiness = {
    ...businessFromSummary(summary),
    brandIdentity: {
      primaryColor: "#123456",
      logoUrl: "/api/v1/media/businesses/current/new-logo.png",
    },
  };

  const saved = await saveSettingsBranding(
    summary.id,
    {
      primaryColor: "#123456",
      secondaryColor: "",
      accentColor: "",
      logo: {
        file,
        previewUrl: "blob:new-logo",
        name: file.name,
        mimeType: "image/png",
        size: file.size,
      },
    },
    true,
    async () => businessFromSummary(summary),
    async (businessId, uploadedFile) => {
      assert.equal(businessId, summary.id);
      assert.equal(uploadedFile, file);
      return uploadedBusiness;
    },
    async () => uploadedBusiness,
    undefined,
  );

  assert.equal(saved, uploadedBusiness);
  assert.equal(saved.brandIdentity?.logo, undefined);
  assert.equal(
    saved.brandIdentity?.logoUrl,
    "/api/v1/media/businesses/current/new-logo.png",
  );
});

test("business list mapping never fabricates inaccessible businesses", () => {
  const loaded = [summary].map((item) => businessFromSummary(item));

  assert.deepEqual(
    loaded.map((business) => business.id),
    [summary.id],
  );
  assert.deepEqual(
    [].map((item) => businessFromSummary(item)),
    [],
  );
});

test("active business preference is accepted only from the backend list", () => {
  const business = businessFromSummary(summary);

  assert.equal(resolveActiveBusinessId(summary.id, [business]), summary.id);
  assert.equal(
    resolveActiveBusinessId("30000000-0000-4000-8000-000000000001", [business]),
    summary.id,
  );
  assert.equal(resolveActiveBusinessId(summary.id, []), "");
});

test("route decisions wait for bootstrap and send empty accounts to onboarding", () => {
  assert.equal(isApplicationBootstrapping("bootstrapping", false), true);
  assert.equal(isApplicationBootstrapping("authenticated", true), true);
  assert.equal(isApplicationBootstrapping("recoverable_error", false), false);
  assert.equal(
    nextProtectedRoute({
      status: "authenticated",
      businessesLoading: true,
      businessesError: "",
      businessCount: 0,
      location: "/login",
    }),
    null,
  );
  assert.equal(
    nextProtectedRoute({
      status: "authenticated",
      businessesLoading: false,
      businessesError: "",
      businessCount: 0,
      location: "/login",
    }),
    "/onboarding",
  );
  assert.equal(
    nextProtectedRoute({
      status: "authenticated",
      businessesLoading: false,
      businessesError: "",
      businessCount: 1,
      location: "/login",
    }),
    "/dashboard",
  );
  assert.equal(
    nextProtectedRoute({
      status: "recoverable_error",
      businessesLoading: false,
      businessesError: "",
      businessCount: 0,
      location: "/dashboard",
    }),
    null,
  );
});

test("optional branding failure cannot become a global business-list failure", async () => {
  const source = await readFile(
    new URL("../business-context.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /Branding is optional presentation data/);
  assert.doesNotMatch(
    source,
    /We couldn't load this business's branding/,
  );
});

test("normal mode starts unsupported workspace modules without demo records", () => {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
  });
  const workspace = workspaceRepository.get(
    "40000000-0000-4000-8000-000000000001",
    "Other",
  );

  assert.equal(demoWorkspaceDataEnabled, false);
  assert.deepEqual(workspace.orders, []);
  assert.deepEqual(workspace.customers, []);
  assert.deepEqual(workspace.analytics.revenueSeries, []);
  assert.equal(workspace.analytics.revenue, 0);
});

test("onboarding payload sends only supported server fields and brand colors", () => {
  const payload = createBusinessOnboardingPayload(summary.id, {
    name: " Cutover Studio ",
    industry: "Other",
    website: "https://example.test",
    location: "Private draft location",
    timezone: "UTC",
    currency: "USD · $",
    locale: "en-US",
    products: [
      { id: "draft-product", name: "Draft", price: 4, availability: "Ready" },
    ],
    brandIdentity: {
      primaryColor: "#5B3FBB",
      secondaryColor: "#475B91",
      accentColor: "#D15B83",
      logo: {
        file: new File([new Uint8Array([1])], "preview.png", {
          type: "image/png",
        }),
        previewUrl: "blob:preview-only",
        name: "preview.png",
        mimeType: "image/png",
        size: 24,
      },
    },
  });

  assert.deepEqual(Object.keys(payload).sort(), [
    "avoid_keywords",
    "brand_voice",
    "branding",
    "business_id",
    "business_type",
    "currency",
    "description",
    "locale",
    "location",
    "name",
    "timezone",
    "website_url",
  ]);
  assert.equal(payload.website_url, "https://example.test");
  assert.equal(payload.location, "Private draft location");
  assert.deepEqual(payload.branding, {
    primary_color: "#5B3FBB",
    secondary_color: "#475B91",
    accent_color: "#D15B83",
  });
  assert.equal(JSON.stringify(payload).includes("data:image"), false);
  assert.equal(JSON.stringify(payload).includes("blob:"), false);
  for (const forbidden of ["owner", "user", "role", "status", "slug"]) {
    assert.equal(Object.hasOwn(payload, forbidden), false);
  }
});

test("logo rendering keeps transparent contain sizing for wide and square assets", async () => {
  const [tenantLogoSource, styles] = await Promise.all([
    readFile(
      new URL("../components/tenant-logo.tsx", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  assert.match(tenantLogoSource, /naturalWidth\s*\/\s*naturalHeight/);
  assert.match(
    styles,
    /business-brand-mark\.has-logo[^}]*background:\s*transparent/,
  );
  assert.match(
    styles,
    /business-brand-mark\.has-logo img[^}]*object-fit:\s*contain/,
  );
  assert.doesNotMatch(
    styles,
    /business-brand-mark\.has-logo img[^}]*object-fit:\s*cover/,
  );
});

test("failed onboarding retry reuses one stable UUID", async () => {
  const businessId = createOnboardingBusinessId();
  assert.match(
    businessId,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  const form: OnboardingForm = {
    name: "Retry Studio",
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
    brandIdentity: { primaryColor: "#5B3FBB" },
  };
  const catalog = { ...createInitialCatalogDraft(), method: "skip" as const };
  const attemptedIds: string[] = [];
  let fail = true;
  const business = businessFromSummary({ ...summary, id: businessId });
  const adapters = {
    createBusiness: async (_input: unknown, id: string) => {
      attemptedIds.push(id);
      if (fail) {
        fail = false;
        throw new ApiNetworkError();
      }
      return business;
    },
    saveCatalog: () => undefined,
  };

  await assert.rejects(
    saveOnboardingWorkspace(form, catalog, businessId, adapters as never),
  );
  const completed = await saveOnboardingWorkspace(
    form,
    catalog,
    businessId,
    adapters as never,
  );

  assert.deepEqual(attemptedIds, [businessId, businessId]);
  assert.equal(completed.id, businessId);
});

test("onboarding creates the business before logo upload and retries one identity", async () => {
  const businessId = createOnboardingBusinessId();
  const events: string[] = [];
  const createdIds: string[] = [];
  let uploadShouldFail = true;
  const file = new File([new Uint8Array([1, 2, 3])], "logo.png", {
    type: "image/png",
  });
  const input = {
    name: "Logo Studio",
    industry: "Other",
    brandIdentity: {
      primaryColor: "#123456",
      logo: {
        file,
        previewUrl: "blob:onboarding-logo",
        name: file.name,
        mimeType: "image/png" as const,
        size: file.size,
      },
    },
  };
  const api = {
    create: async (payload: { business_id: string }) => {
      events.push("create");
      createdIds.push(payload.business_id);
      return {
        business: summary,
        branding: null,
        created: createdIds.length === 1,
      };
    },
    uploadLogo: async () => {
      events.push("upload");
      if (uploadShouldFail) {
        uploadShouldFail = false;
        throw new ApiNetworkError();
      }
      return {
        primary_color: "#123456",
        secondary_color: null,
        accent_color: null,
        logo_url: "/api/v1/media/businesses/current/logo.png",
      };
    },
  };

  await assert.rejects(
    persistBusinessOnboarding(api, businessId, input),
    (error: unknown) =>
      error instanceof BusinessLogoUploadAfterCreationError &&
      error.businessSaved,
  );
  await persistBusinessOnboarding(api, businessId, input);

  assert.deepEqual(events, ["create", "upload", "create", "upload"]);
  assert.deepEqual(createdIds, [businessId, businessId]);
  assert.equal(new Set(createdIds).size, 1);
});

test("onboarding can skip logo without invoking the upload API", async () => {
  let uploadCalls = 0;
  await persistBusinessOnboarding(
    {
      create: async () => ({
        business: summary,
        branding: null,
        created: true,
      }),
      uploadLogo: async () => {
        uploadCalls += 1;
        throw new Error("must not run");
      },
    },
    summary.id,
    { name: "No Logo Studio", industry: "Other" },
  );

  assert.equal(uploadCalls, 0);
});

test("onboarding conflict uses safe UX without backend internals", () => {
  const message = humanizeOnboardingSaveError(
    new ApiError(409, { detail: "internal-record-reference" }),
  );

  assert.match(message, /conflicts with an existing workspace/);
  assert.equal(message.includes("internal-record-reference"), false);
});

test("onboarding distinguishes a saved business from a failed logo upload", () => {
  const partialFailure = Object.assign(
    new Error(
      "The business was saved, but its logo could not be uploaded. Retry the logo or skip it for now.",
    ),
    { businessSaved: true },
  );

  assert.equal(
    humanizeOnboardingSaveError(partialFailure),
    partialFailure.message,
  );
});

test("onboarding and workspace storage contain no permanent logo preview", async () => {
  const [onboardingSource, draftSource, logoSource] = await Promise.all([
    readFile(
      new URL("../features/onboarding/onboarding-page.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../services/business-draft-repository.ts", import.meta.url),
      "utf8",
    ),
    readFile(new URL("../services/brand-logo.ts", import.meta.url), "utf8"),
  ]);

  assert.match(onboardingSource, /onboardingBrandDraftForStorage/);
  assert.match(onboardingSource, /logo:\s*undefined/);
  assert.doesNotMatch(draftSource, /previewUrl|dataUrl/);
  assert.doesNotMatch(logoSource, /FileReader|readAsDataURL|base64/);
  assert.match(logoSource, /URL\.createObjectURL/);
  assert.match(logoSource, /URL\.revokeObjectURL/);
});

test("onboarding business input omits catalog from the business save", () => {
  const input = createOnboardingBusinessInput(
    {
      name: "Catalog Studio",
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
    },
    { ...createInitialCatalogDraft(), method: "skip" },
  );

  assert.equal(input.products, undefined);
  assert.equal(input.brandIdentity, undefined);
});
