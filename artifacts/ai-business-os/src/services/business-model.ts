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

const INDUSTRY_LABELS: Record<string, string> = {
  agriculture: "Farm/Agriculture",
  farm: "Farm/Agriculture",
  "farm/agriculture": "Farm/Agriculture",
  "real estate": "Real Estate",
  ecommerce: "E-commerce",
  "e-commerce": "E-commerce",
  dental: "Dental",
  other: "Other",
};

export function businessFromSummary(
  summary: BusinessSummary,
  draft?: BusinessLocalDraft,
): Business {
  const industry =
    INDUSTRY_LABELS[summary.business_type.toLowerCase()] ??
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
    website: draft?.website ?? "",
    location: draft?.location ?? "",
    description: draft?.description ?? "",
    tone: draft?.tone ?? "",
    avoidKeywords: draft?.avoidKeywords ?? "",
    connectedChannels: draft?.connectedChannels ?? [],
    products: [],
    onboardingComplete: true,
    theme: draft?.theme ?? (industry === "Real Estate" ? "navy" : "green"),
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
      (input.industry === "Real Estate" ? "navy" : "green"),
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
    business_type: input.industry.trim().toLowerCase(),
    timezone: input.timezone ?? "UTC",
    currency: normalizeCurrency(input.currency),
    locale: input.locale ?? "en",
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
