import type {
  BusinessBrandingResponse,
  BusinessBrandingUpdate,
  BusinessOnboardingInput,
  BusinessSummary,
} from "./api-types.ts";
import type {
  BrandIdentity,
  Business,
  BusinessInput,
  BusinessLocalDraft,
} from "../types/business.ts";
import {
  businessIndustryBackendCode,
  businessIndustryDefaultTheme,
  businessIndustryLabelFromBackendCode,
  type OnboardingIndustry,
} from "../lib/business-industries.ts";

export function businessFromSummary(
  summary: BusinessSummary,
  draft?: BusinessLocalDraft,
): Business {
  const industry =
    businessIndustryLabelFromBackendCode(summary.business_type) ??
    summary.business_type;
  return {
    id: summary.id,
    name: summary.name,
    slug: summary.slug,
    businessType: summary.business_type,
    status: summary.status,
    timezone: summary.timezone,
    currency: summary.currency,
    locale: summary.locale,
    membershipRole: summary.membership_role,
    createdAt: summary.created_at,
    industry,
    website: summary.website_url ?? draft?.website ?? "",
    location: summary.location ?? draft?.location ?? "",
    description: summary.description ?? draft?.description ?? "",
    tone: summary.brand_voice ?? draft?.tone ?? "",
    avoidKeywords:
      summary.avoid_keywords?.join(", ") ?? draft?.avoidKeywords ?? "",
    connectedChannels: draft?.connectedChannels ?? [],
    products: [],
    onboardingComplete: true,
    theme: draft?.theme ?? businessIndustryDefaultTheme(industry),
    brandIdentity: undefined,
  };
}

export function businessDraftFromInput(
  input: BusinessInput,
  current?: BusinessLocalDraft,
): BusinessLocalDraft {
  return {
    website: input.website ?? current?.website ?? "",
    location: input.location ?? current?.location ?? "",
    description: input.description ?? current?.description ?? "",
    tone: input.tone ?? current?.tone ?? "",
    avoidKeywords: input.avoidKeywords ?? current?.avoidKeywords ?? "",
    connectedChannels:
      input.connectedChannels ?? current?.connectedChannels ?? [],
    theme:
      input.theme ??
      current?.theme ??
      businessIndustryDefaultTheme(input.industry),
  };
}

export function brandingFromResponse(
  branding: BusinessBrandingResponse | null,
  localIdentity?: Pick<BrandIdentity, "logo">,
): BrandIdentity | undefined {
  const identity: BrandIdentity = {
    ...(localIdentity?.logo ? { logo: localIdentity.logo } : {}),
    ...(branding?.logo_url ? { logoUrl: branding.logo_url } : {}),
    ...(branding?.primary_color
      ? { primaryColor: branding.primary_color }
      : {}),
    ...(branding?.secondary_color
      ? { secondaryColor: branding.secondary_color }
      : {}),
    ...(branding?.accent_color ? { accentColor: branding.accent_color } : {}),
  };
  return Object.values(identity).some((value) => value !== undefined)
    ? identity
    : undefined;
}

export function brandingUpdateFromIdentity(
  identity: BrandIdentity | null,
): BusinessBrandingUpdate {
  return {
    primary_color: identity?.primaryColor ?? null,
    secondary_color: identity?.secondaryColor ?? null,
    accent_color: identity?.accentColor ?? null,
  };
}

export function applyBrandingToBusinessList(
  businesses: Business[],
  businessId: string,
  branding: BusinessBrandingResponse | null,
  localIdentity?: Pick<BrandIdentity, "logo">,
): Business[] {
  return businesses.map((business) =>
    business.id === businessId
      ? {
          ...business,
          brandIdentity: brandingFromResponse(branding, localIdentity),
        }
      : business,
  );
}

export function isCurrentBrandingResponse(
  requestedBusinessId: string,
  requestedVersion: number,
  activeBusinessId: string,
  currentVersion: number,
): boolean {
  return (
    requestedBusinessId === activeBusinessId &&
    requestedVersion === currentVersion
  );
}

export function createBusinessOnboardingPayload(
  businessId: string,
  input: BusinessInput,
): BusinessOnboardingInput {
  const identity = input.brandIdentity ?? undefined;
  const branding = identity
    ? {
        primary_color: identity.primaryColor,
        secondary_color: identity.secondaryColor,
        accent_color: identity.accentColor,
      }
    : undefined;
  return {
    business_id: businessId,
    name: input.name.trim(),
    business_type: businessIndustryBackendCode(
      input.industry as OnboardingIndustry,
    ),
    timezone: input.timezone ?? "UTC",
    currency: normalizeCurrency(input.currency),
    locale: input.locale ?? "en",
    website_url: optionalText(input.website),
    location: optionalText(input.location),
    description: optionalText(input.description),
    brand_voice: optionalText(input.tone),
    avoid_keywords: parseAvoidKeywords(input.avoidKeywords),
    branding,
  };
}

export function resolveActiveBusinessId(
  persistedId: string | null,
  businesses: Business[],
): string {
  if (
    persistedId &&
    businesses.some((business) => business.id === persistedId)
  ) {
    return persistedId;
  }
  return businesses[0]?.id ?? "";
}

function normalizeCurrency(value?: string): string {
  const match = /^[A-Za-z]{3}/.exec(value?.trim() ?? "");
  return (match?.[0] ?? "USD").toUpperCase();
}

export function createBusinessProfilePayload(input: BusinessInput) {
  return {
    name: input.name.trim(),
    timezone: input.timezone ?? "UTC",
    currency: normalizeCurrency(input.currency),
    locale: input.locale ?? "en",
    website_url: optionalText(input.website),
    location: optionalText(input.location),
    description: optionalText(input.description),
    brand_voice: optionalText(input.tone),
    avoid_keywords: parseAvoidKeywords(input.avoidKeywords),
  };
}

function optionalText(value?: string): string | null {
  const normalized = value?.trim() ?? "";
  return normalized || null;
}

function parseAvoidKeywords(value?: string): string[] {
  const seen = new Set<string>();
  return (value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter((item) => {
      const key = item.toLocaleLowerCase();
      if (!item || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}
