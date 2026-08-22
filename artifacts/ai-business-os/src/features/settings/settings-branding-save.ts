import {
  brandIdentityFromDraft,
  type BrandIdentityDraft,
} from "../../lib/brand-theme.ts";
import type { BrandIdentity, Business } from "../../types/business.ts";

type BrandingUpdater = (
  businessId: string,
  identity: BrandIdentity | null,
) => Promise<Business>;
type LogoUploader = (businessId: string, file: File) => Promise<Business>;
type LogoDeleter = (businessId: string) => Promise<Business>;

export async function saveSettingsBranding(
  businessId: string,
  draft: BrandIdentityDraft,
  hasCustomBrand: boolean,
  updateBranding: BrandingUpdater,
  uploadLogo: LogoUploader,
  deleteLogo: LogoDeleter,
  previousLogoUrl: string | undefined,
): Promise<Business> {
  const colorsSaved = await updateBranding(
    businessId,
    hasCustomBrand ? brandIdentityFromDraft(draft) : null,
  );
  if (draft.logo) {
    return uploadLogo(businessId, draft.logo.file);
  }
  if (previousLogoUrl && !draft.logoUrl) {
    return deleteLogo(businessId);
  }
  return colorsSaved;
}

export async function resetSettingsBranding(
  businessId: string,
  updateBranding: BrandingUpdater,
  deleteLogo: LogoDeleter,
): Promise<Business> {
  const colorsReset = await updateBranding(businessId, null);
  if (colorsReset.brandIdentity?.logoUrl) {
    return deleteLogo(businessId);
  }
  return colorsReset;
}
