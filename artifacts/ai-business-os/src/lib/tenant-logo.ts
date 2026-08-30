import { BRAND_LOGO_MAX_BYTES, type BrandIdentity } from "../types/business.ts";

const SUPPORTED_LOGO_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

export function businessInitials(businessName: string): string {
  const parts = businessName.trim().split(/\s+/).filter(Boolean);
  if (parts.length > 1) {
    return parts
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase();
  }

  return (
    (parts[0] ?? "Business")
      .replace(/[^a-z0-9]/gi, "")
      .slice(0, 1)
      .toUpperCase() || "B"
  );
}

export function normalizeTenantLogoUrl(logoUrl?: string | null) {
  const value = logoUrl?.trim();
  if (!value) return undefined;
  return /^(https?:\/\/|\/(?!\/)|blob:)/.test(value) ? value : undefined;
}

export function resolveTenantLogoSource(
  identity?: Pick<BrandIdentity, "logo" | "logoUrl">,
) {
  const logo = identity?.logo;
  if (
    logo &&
    logo.size > 0 &&
    logo.size <= BRAND_LOGO_MAX_BYTES &&
    SUPPORTED_LOGO_TYPES.has(logo.mimeType) &&
    logo.previewUrl.startsWith("blob:")
  ) {
    return logo.previewUrl;
  }

  return normalizeTenantLogoUrl(identity?.logoUrl);
}

export function tenantLogoKey(
  businessName: string,
  logoUrl?: string | null,
  tenantKey?: string,
) {
  const source = normalizeTenantLogoUrl(logoUrl);
  return source
    ? JSON.stringify([tenantKey ?? businessName.trim(), source])
    : null;
}

export type TenantLogoPresentation =
  | { kind: "logo"; key: string; logoUrl: string }
  | { kind: "initials"; initials: string };

export function tenantLogoPresentation(
  businessName: string,
  logoUrl: string | null | undefined,
  failedLogoKey: string | null,
  tenantKey?: string,
): TenantLogoPresentation {
  const source = normalizeTenantLogoUrl(logoUrl);
  const key = tenantLogoKey(businessName, source, tenantKey);

  if (source && key && failedLogoKey !== key) {
    return { kind: "logo", key, logoUrl: source };
  }

  return {
    kind: "initials",
    initials: businessInitials(businessName || "Business"),
  };
}
