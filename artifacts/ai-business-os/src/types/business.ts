import type { BusinessStatus } from "../services/api-types.ts";

export const BRAND_LOGO_MAX_MEGABYTES = 5;
export const BRAND_LOGO_MAX_BYTES = BRAND_LOGO_MAX_MEGABYTES * 1_000_000;

export type BrandLogo = {
  /** Ephemeral browser-only upload draft. Never persist this object. */
  file: File;
  previewUrl: string;
  name: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  size: number;
};

export type BrandIdentity = {
  logo?: BrandLogo;
  /** Trusted backend/object-storage presentation URL. */
  logoUrl?: string;
  primaryColor?: string;
  secondaryColor?: string;
  accentColor?: string;
};

export type BusinessProduct = {
  id: string;
  name: string;
  price: number;
  availability: string;
};

export type Business = {
  id: string;
  name: string;
  slug: string;
  businessType: string;
  status: BusinessStatus;
  timezone: string;
  currency: string;
  locale: string;
  membershipRole: string;
  createdAt: string;
  industry: string;
  website: string;
  location: string;
  description: string;
  tone: string;
  avoidKeywords: string;
  connectedChannels: string[];
  products: BusinessProduct[];
  onboardingComplete: boolean;
  theme: "green" | "navy";
  brandIdentity?: BrandIdentity;
};

export type BusinessInput = {
  name: string;
  industry: string;
  website?: string;
  location?: string;
  timezone?: string;
  currency?: string;
  locale?: string;
  description?: string;
  tone?: string;
  avoidKeywords?: string;
  connectedChannels?: string[];
  products?: BusinessProduct[];
  onboardingComplete?: boolean;
  theme?: "green" | "navy";
  /** null is an internal UI-draft command used to reset branding. */
  brandIdentity?: BrandIdentity | null;
};

export type BusinessLocalDraft = Pick<
  Business,
  | "website"
  | "location"
  | "description"
  | "tone"
  | "avoidKeywords"
  | "connectedChannels"
  | "theme"
>;
