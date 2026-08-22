import { businessApi, humanizeApiError } from "./api-client.ts";
import { createBusinessOnboardingPayload } from "./business-model.ts";
import type { BusinessInput } from "../types/business.ts";

type BusinessOnboardingApi = Pick<typeof businessApi, "create" | "uploadLogo">;

export class BusinessLogoUploadAfterCreationError extends Error {
  readonly businessSaved = true;

  constructor(message: string) {
    super(message);
    this.name = "BusinessLogoUploadAfterCreationError";
  }
}

export async function persistBusinessOnboarding(
  api: BusinessOnboardingApi,
  businessId: string,
  input: BusinessInput,
) {
  const response = await api.create(
    createBusinessOnboardingPayload(businessId, input),
  );
  const pendingLogo = input.brandIdentity?.logo;
  if (pendingLogo) {
    try {
      await api.uploadLogo(businessId, pendingLogo.file);
    } catch (reason) {
      throw new BusinessLogoUploadAfterCreationError(
        humanizeApiError(
          reason,
          "The business was saved, but its logo could not be uploaded. Retry the logo or skip it for now.",
        ),
      );
    }
  }
  return response;
}
