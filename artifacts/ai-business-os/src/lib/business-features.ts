import type { Business } from "../types/business.ts";
import {
  businessIndustrySupportsScheduling,
  type OnboardingIndustry,
} from "./business-industries.ts";

export type BusinessFeature =
  | "ai_command_center"
  | "ai_agents"
  | "website_chatbot"
  | "automations"
  | "marketing_cmo"
  | "campaigns"
  | "competitor_intelligence"
  | "trend_intelligence"
  | "scheduling"
  | "integrations"
  | "advanced_analytics"
  | "reports";
export type BusinessFeatureSubject = Pick<Business, "industry">;
export type BusinessFeatureItem = { feature?: BusinessFeature };
export type IntegrationConnectorRecommendation =
  | "whatsapp_business"
  | "gmail"
  | "google_calendar"
  | "google_ads"
  | "meta_ads"
  | "facebook"
  | "instagram"
  | "microsoft_outlook";

const INTEGRATION_RECOMMENDATIONS: Readonly<
  Partial<Record<OnboardingIndustry, readonly IntegrationConnectorRecommendation[]>>
> = {
  "Farm/Agriculture": ["whatsapp_business", "facebook", "gmail", "google_calendar"],
  "Real Estate": ["whatsapp_business", "gmail", "facebook", "instagram", "google_calendar"],
  "E-commerce": ["meta_ads", "google_ads", "instagram", "facebook", "gmail", "whatsapp_business"],
  Hospital: [
    "google_calendar",
    "whatsapp_business",
    "gmail",
    "microsoft_outlook",
  ],
  Clinic: [
    "google_calendar",
    "whatsapp_business",
    "gmail",
    "microsoft_outlook",
  ],
  "Medical Practice": [
    "google_calendar",
    "whatsapp_business",
    "gmail",
    "microsoft_outlook",
  ],
  Dental: [
    "google_calendar",
    "whatsapp_business",
    "gmail",
    "microsoft_outlook",
  ],
  "Professional Services": [
    "gmail",
    "google_calendar",
    "whatsapp_business",
    "microsoft_outlook",
  ],
  Other: ["gmail", "google_calendar", "whatsapp_business"],
};

export function recommendedIntegrationConnectors(
  industry: string | null | undefined,
): readonly IntegrationConnectorRecommendation[] {
  if (!industry || !(industry in INTEGRATION_RECOMMENDATIONS)) return [];
  return INTEGRATION_RECOMMENDATIONS[industry as OnboardingIndustry] ?? [];
}

type BusinessFeaturePolicy = {
  isIndustryEnabled: (
    industry: string | null | undefined,
  ) => boolean;
  protectedRoutes: readonly string[];
  surfacedContentPattern: RegExp;
};

/**
 * Product visibility policy only. This does not disable or remove backend
 * capabilities; it controls which business workspaces surface their UI.
 */
export const BUSINESS_FEATURE_POLICY: Readonly<
  Partial<Record<BusinessFeature, BusinessFeaturePolicy>>
> = {
  scheduling: {
    isIndustryEnabled: businessIndustrySupportsScheduling,
    protectedRoutes: ["/scheduling"],
    surfacedContentPattern:
      /\b(?:appointments?|appointment types?|available appointment slots?|available slots?|doctors?|service providers?|provider availability|scheduling|(?:today'?s|tomorrow'?s) schedule)\b/i,
  },
};

export function isBusinessFeatureEnabledForIndustry(
  industry: string | null | undefined,
  feature: BusinessFeature,
): boolean {
  const policy = BUSINESS_FEATURE_POLICY[feature];
  if (!policy) return true;
  return policy.isIndustryEnabled(industry);
}

export function isBusinessFeatureEnabled(
  business: BusinessFeatureSubject | null | undefined,
  feature: BusinessFeature,
  entitlements?: Record<string, boolean | number> | null,
): boolean {
  const industryEnabled = isBusinessFeatureEnabledForIndustry(business?.industry, feature);
  if (!industryEnabled) return false;
  return entitlements === undefined ? true : entitlements?.[feature] === true;
}

export function filterBusinessFeatureItems<T extends BusinessFeatureItem>(
  business: BusinessFeatureSubject | null | undefined,
  items: readonly T[],
  entitlements?: Record<string, boolean | number> | null,
): T[] {
  return items.filter(
    (item) =>
      !item.feature || isBusinessFeatureEnabled(business, item.feature, entitlements),
  );
}

export function businessFeatureForPath(
  pathname: string,
): BusinessFeature | undefined {
  const normalizedPath = pathname.split(/[?#]/, 1)[0] || "/";
  return (Object.entries(BUSINESS_FEATURE_POLICY) as Array<
    [BusinessFeature, BusinessFeaturePolicy]
  >).find(([, policy]) =>
    policy.protectedRoutes.some(
      (route) =>
        normalizedPath === route || normalizedPath.startsWith(`${route}/`),
    ),
  )?.[0];
}

export function businessFeatureRouteRedirect(
  business: BusinessFeatureSubject | null | undefined,
  pathname: string,
): string | null {
  const feature = businessFeatureForPath(pathname);
  return feature && !isBusinessFeatureEnabled(business, feature)
    ? "/dashboard"
    : null;
}

export function isBusinessFeatureContentVisible(
  business: BusinessFeatureSubject | null | undefined,
  content: string,
): boolean {
  return (Object.entries(BUSINESS_FEATURE_POLICY) as Array<
    [BusinessFeature, BusinessFeaturePolicy]
  >).every(
    ([feature, policy]) =>
      !policy.surfacedContentPattern.test(content) ||
      isBusinessFeatureEnabled(business, feature),
  );
}
