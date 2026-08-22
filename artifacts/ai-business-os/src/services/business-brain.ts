import {
  ApiClient,
  ApiError,
  apiClient,
  humanizeApiError,
} from "./api-client.ts";
import type {
  BusinessBrainManifest,
  BusinessKnowledgeCategory,
  BusinessKnowledgeEntry,
  BusinessKnowledgeEntryCreate,
  BusinessKnowledgeEntryUpdate,
  BusinessKnowledgeStatus,
} from "./api-types.ts";

export type BusinessKnowledgeListFilters = {
  category?: BusinessKnowledgeCategory;
  status?: BusinessKnowledgeStatus;
};

export type BusinessKnowledgeEditableField =
  "category" | "title" | "content" | "status";

function brainPath(businessId: string) {
  return `/api/v1/businesses/${encodeURIComponent(businessId)}/brain`;
}

function knowledgePath(businessId: string) {
  return `${brainPath(businessId)}/knowledge`;
}

export function createBusinessBrainApi(client: ApiClient) {
  return {
    listKnowledge(
      businessId: string,
      filters: BusinessKnowledgeListFilters = {},
      signal?: AbortSignal,
    ) {
      const query = new URLSearchParams();
      if (filters.category) query.set("category", filters.category);
      if (filters.status) query.set("status", filters.status);
      const suffix = query.size ? `?${query.toString()}` : "";
      return client.request<BusinessKnowledgeEntry[]>(
        `${knowledgePath(businessId)}${suffix}`,
        { signal },
      );
    },

    createKnowledge(businessId: string, input: BusinessKnowledgeEntryCreate) {
      return client.request<BusinessKnowledgeEntry>(knowledgePath(businessId), {
        method: "POST",
        json: input,
      });
    },

    getKnowledge(businessId: string, entryId: string, signal?: AbortSignal) {
      return client.request<BusinessKnowledgeEntry>(
        `${knowledgePath(businessId)}/${encodeURIComponent(entryId)}`,
        { signal },
      );
    },

    updateKnowledge(
      businessId: string,
      entryId: string,
      input: BusinessKnowledgeEntryUpdate,
    ) {
      return client.request<BusinessKnowledgeEntry>(
        `${knowledgePath(businessId)}/${encodeURIComponent(entryId)}`,
        { method: "PATCH", json: input },
      );
    },

    archiveKnowledge(businessId: string, entryId: string) {
      return client.request<null>(
        `${knowledgePath(businessId)}/${encodeURIComponent(entryId)}`,
        { method: "DELETE" },
      );
    },

    getManifest(businessId: string, signal?: AbortSignal) {
      return client.request<BusinessBrainManifest>(
        `${brainPath(businessId)}/manifest`,
        { signal },
      );
    },
  };
}

export const businessBrainApi = createBusinessBrainApi(apiClient);

export type BusinessBrainApi = ReturnType<typeof createBusinessBrainApi>;

export function knowledgeValidationFields(error: unknown) {
  const fieldErrors: Partial<Record<BusinessKnowledgeEditableField, string>> =
    {};
  if (!(error instanceof ApiError) || error.status !== 422) return fieldErrors;
  const detail = error.data?.detail;
  if (!Array.isArray(detail)) return fieldErrors;

  for (const issue of detail) {
    const field = issue.loc.at(-1);
    if (field === "title") {
      fieldErrors.title = "Use a title between 1 and 250 characters.";
    } else if (field === "content") {
      fieldErrors.content = "Use content between 1 and 50,000 characters.";
    } else if (field === "category") {
      fieldErrors.category = "Choose a supported category.";
    } else if (field === "status") {
      fieldErrors.status = "Choose Active or Draft.";
    }
  }
  return fieldErrors;
}

export function humanizeBusinessBrainError(
  error: unknown,
  fallback = "We couldn't update Business Brain knowledge. Please try again.",
) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Your session could not be verified. Sign in and try again.";
    }
    if (error.status === 403) {
      return "Business Brain access is unavailable for this business.";
    }
    if (error.status === 404) {
      return "This business or knowledge entry is no longer available.";
    }
    if (error.status === 422) {
      return "Fix the highlighted details and try again.";
    }
    if (error.status === 503) {
      return "Business Brain is temporarily unavailable. Please try again.";
    }
    return fallback;
  }
  return humanizeApiError(error, fallback);
}
