import type { Business, BusinessInput } from "@workspace/api-client-react";
import type { WorkspaceData } from "@/types/workspace";
import { slug } from "../../lib/product-utils.ts";
import type { CatalogDraft, CatalogDraftProduct } from "./catalog-import";

export const onboardingSetupSteps = [
  "Creating business workspace",
  "Saving business information",
  "Adding products/services",
  "Configuring brand voice",
  "Preparing connected channels",
  "Creating AI team",
  "Finalizing workspace",
] as const;

export type OnboardingForm = {
  name: string;
  industry: BusinessInput["industry"];
  website: string;
  location: string;
  timezone: string;
  currency: string;
  description: string;
  tone: string;
  avoidKeywords: string;
  channels: NonNullable<BusinessInput["connectedChannels"]>;
};

export type OnboardingSaveAdapters = {
  createBusiness: (
    input: BusinessInput,
    businessId: string,
  ) => Promise<Business>;
  saveWorkspace: (
    business: Business,
    products: CatalogDraftProduct[],
    catalog: CatalogDraft,
  ) => void | Promise<void>;
  onProgress?: (completedSteps: number) => void | Promise<void>;
};

export function createOnboardingBusinessId(name: string, draftId: string) {
  return `${slug(name) || "business"}-${draftId}`;
}

export function catalogProductsForSave(catalog: CatalogDraft) {
  return catalog.method === "skip" || catalog.method === "store"
    ? []
    : catalog.products.filter((product) => product.name.trim());
}

export function createOnboardingBusinessInput(
  form: OnboardingForm,
  catalog: CatalogDraft,
  onboardingComplete = true,
): BusinessInput {
  const products = catalogProductsForSave(catalog);
  return {
    name: form.name.trim(),
    industry: form.industry,
    website: form.website.trim(),
    location: form.location.trim(),
    timezone: form.timezone,
    currency: form.currency,
    description: form.description.trim(),
    tone: form.tone.trim(),
    avoidKeywords: form.avoidKeywords.trim(),
    connectedChannels: form.channels,
    products: products.map((product) => ({
      id: product.id,
      name: product.name.trim(),
      price: Number(product.price) || 0,
      availability: product.availability,
    })),
    onboardingComplete,
    theme: form.industry === "Real Estate" ? "navy" : "green",
  };
}

export function addCatalogToWorkspace(
  current: WorkspaceData,
  catalog: CatalogDraft,
  products: CatalogDraftProduct[],
): WorkspaceData {
  return {
    ...current,
    catalog: {
      method: catalog.method ?? "manual",
      sourceName:
        catalog.sourceName ||
        (catalog.method === "manual" ? "Manual onboarding" : ""),
      storeProvider: catalog.storeProvider,
      confirmedAt: new Date().toISOString(),
      items: products.map((product) => ({
        id: product.id,
        name: product.name.trim(),
        sku: product.sku,
        price: Number(product.price) || 0,
        availability: product.availability,
        category: product.category,
        description: product.description,
      })),
    },
  };
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
  const products = catalogProductsForSave(catalog);

  await progress(0);
  const pendingBusiness = await adapters.createBusiness(
    createOnboardingBusinessInput(form, catalog, false),
    businessId,
  );
  await progress(1);
  await progress(2);
  await adapters.saveWorkspace(pendingBusiness, products, catalog);
  await progress(3);
  await progress(4);
  await progress(5);
  await progress(6);
  const business = await adapters.createBusiness(
    createOnboardingBusinessInput(form, catalog, true),
    businessId,
  );
  await progress(7);

  return business;
}

export function humanizeOnboardingSaveError(error: unknown) {
  if (error instanceof Error && error.message.trim()) return error.message;
  return "We couldn't save this workspace in your browser. Please try again.";
}
