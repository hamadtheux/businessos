import type {
  BrandIdentity,
  Business,
  BusinessInput,
} from "../../types/business.ts";
import {
  createCatalogItemsFile,
  validateCatalogItemDraft,
} from "../catalog/catalog-model.ts";
import {
  humanizeCatalogError,
  type CatalogApi,
} from "../../services/catalog.ts";
import { ApiError, humanizeApiError } from "../../services/api-client.ts";
import { manualCatalogItems, type CatalogDraft } from "./catalog-import.ts";

export const onboardingSetupSteps = [
  "Creating business workspace",
  "Saving business information",
  "Validating products/services",
  "Saving products/services",
  "Configuring brand voice",
  "Preparing connected channels",
  "Finalizing workspace",
] as const;

export type OnboardingForm = {
  name: string;
  industry: BusinessInput["industry"];
  website: string;
  location: string;
  timezone: string;
  currency: string;
  locale: string;
  description: string;
  tone: string;
  avoidKeywords: string;
  channels: NonNullable<BusinessInput["connectedChannels"]>;
  brandIdentity?: BrandIdentity;
};

export type OnboardingSaveAdapters = {
  createBusiness: (
    input: BusinessInput,
    businessId: string,
  ) => Promise<Business>;
  saveCatalog: (
    business: Business,
    catalog: CatalogDraft,
  ) => void | Promise<void>;
  onProgress?: (completedSteps: number) => void | Promise<void>;
};

export class CatalogPersistenceAfterBusinessCreationError extends Error {
  readonly businessSaved = true;
  readonly stage = "catalog";

  constructor(message: string) {
    super(message);
    this.name = "CatalogPersistenceAfterBusinessCreationError";
  }
}

export function createOnboardingBusinessId() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10).join(""),
  ].join("-");
}

export function createOnboardingBusinessInput(
  form: OnboardingForm,
  _catalog: CatalogDraft,
  onboardingComplete = true,
): BusinessInput {
  return {
    name: form.name.trim(),
    industry: form.industry,
    website: form.website.trim(),
    location: form.location.trim(),
    timezone: form.timezone,
    currency: form.currency,
    locale: form.locale,
    description: form.description.trim(),
    tone: form.tone.trim(),
    avoidKeywords: form.avoidKeywords.trim(),
    connectedChannels: form.channels,
    onboardingComplete,
    theme: form.industry === "Real Estate" ? "navy" : "green",
    brandIdentity: form.brandIdentity,
  };
}

export async function persistOnboardingCatalog(
  api: CatalogApi,
  businessId: string,
  catalog: CatalogDraft,
  selectedFile: File | null,
) {
  if (catalog.method === "skip") {
    return api.listCatalogItems(businessId);
  }
  if (catalog.method === "store") {
    throw new Error(
      "Store connections are coming soon. Choose another catalog option or skip for now.",
    );
  }

  let file = selectedFile;
  if (catalog.method === "manual") {
    const drafts = catalog.products.filter((product) => product.name.trim());
    const invalid = drafts.find((product) => validateCatalogItemDraft(product));
    if (invalid) {
      throw new Error(
        validateCatalogItemDraft(invalid) ?? "Check the catalog details.",
      );
    }
    file = createCatalogItemsFile(
      manualCatalogItems(catalog),
      "onboarding-catalog.csv",
    );
  }
  if (!file) {
    throw new Error(
      catalog.method === "paste"
        ? "Prepare the pasted list again before retrying."
        : "Choose the catalog file again before retrying.",
    );
  }

  const preview = await api.previewCatalogImport(businessId, file);
  if (preview.invalid_rows > 0 || preview.valid_rows === 0) {
    const firstError = preview.errors[0];
    const detail = firstError
      ? `Row ${firstError.row}: ${firstError.message}`
      : "No valid catalog rows were found.";
    throw new Error(
      `${detail} Fix the file and retry. Nothing has been imported yet.`,
    );
  }
  await api.importCatalogFile(businessId, file);
  return api.listCatalogItems(businessId);
}

export async function saveOnboardingWorkspace(
  form: OnboardingForm,
  catalog: CatalogDraft,
  businessId: string,
  adapters: OnboardingSaveAdapters,
) {
  const progress = async (completedSteps: number) => {
    await adapters.onProgress?.(completedSteps);
  };

  await progress(0);
  const business = await adapters.createBusiness(
    createOnboardingBusinessInput(form, catalog, true),
    businessId,
  );
  await progress(1);
  await progress(2);
  try {
    await adapters.saveCatalog(business, catalog);
  } catch (error) {
    const message =
      error instanceof Error && !(error instanceof ApiError)
        ? error.message
        : humanizeCatalogError(
            error,
            "The business was saved, but its catalog could not be saved. Retry the catalog or skip it for now.",
          );
    throw new CatalogPersistenceAfterBusinessCreationError(message);
  }
  await progress(4);
  await progress(5);
  await progress(6);
  await progress(7);

  return business;
}

export function humanizeOnboardingSaveError(error: unknown) {
  if (
    error instanceof Error &&
    "businessSaved" in error &&
    error.businessSaved === true
  ) {
    return error.message;
  }
  if (error instanceof ApiError && error.status === 409) {
    return "This business setup conflicts with an existing workspace. Review the details or contact support.";
  }
  return humanizeApiError(
    error,
    "We couldn't save this workspace. Your information is safe—please try again.",
  );
}
