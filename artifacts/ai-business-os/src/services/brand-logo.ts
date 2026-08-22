import {
  BRAND_LOGO_MAX_BYTES,
  BRAND_LOGO_MAX_MEGABYTES,
  type BrandLogo,
} from "../types/business.ts";

const acceptedLogoTypes = ["image/png", "image/jpeg", "image/webp"] as const;

export const BRAND_LOGO_ACCEPT = ".png,.jpg,.jpeg,.webp";

export function validateBrandLogo(file: Pick<File, "name" | "size" | "type">) {
  if (
    !acceptedLogoTypes.includes(file.type as (typeof acceptedLogoTypes)[number])
  ) {
    return "Choose a PNG, JPG, JPEG, or WebP image.";
  }
  if (file.size <= 0) return "This image appears to be empty.";
  if (file.size > BRAND_LOGO_MAX_BYTES) {
    return `Choose an image smaller than ${BRAND_LOGO_MAX_MEGABYTES} MB so this preview stays fast.`;
  }
  return null;
}

export function readBrandLogo(file: File): BrandLogo {
  const validationError = validateBrandLogo(file);
  if (validationError) throw new Error(validationError);

  return {
    file,
    previewUrl: URL.createObjectURL(file),
    name: file.name,
    mimeType: file.type as BrandLogo["mimeType"],
    size: file.size,
  };
}

export function revokeBrandLogo(logo: BrandLogo | undefined): void {
  if (logo?.previewUrl.startsWith("blob:")) {
    URL.revokeObjectURL(logo.previewUrl);
  }
}
