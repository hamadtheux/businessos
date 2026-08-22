import {
  ApiClient,
  ApiError,
  apiClient,
  humanizeApiError,
} from "./api-client.ts";
import type {
  CatalogImportPreviewResponse,
  CatalogImportResult,
  CatalogItem,
  CatalogItemCreate,
  CatalogItemStatus,
  CatalogItemType,
  CatalogItemUpdate,
} from "./api-types.ts";

export type CatalogListFilters = {
  itemType?: CatalogItemType;
  status?: CatalogItemStatus;
};

function catalogPath(businessId: string) {
  return `/api/v1/businesses/${encodeURIComponent(businessId)}/catalog`;
}

function importBody(file: File) {
  const form = new FormData();
  form.append("file", file, file.name);
  return form;
}

export function createCatalogApi(client: ApiClient) {
  return {
    listCatalogItems(
      businessId: string,
      filters: CatalogListFilters = {},
      signal?: AbortSignal,
    ) {
      const query = new URLSearchParams();
      if (filters.itemType) query.set("item_type", filters.itemType);
      if (filters.status) query.set("status", filters.status);
      const suffix = query.size ? `?${query.toString()}` : "";
      return client.request<CatalogItem[]>(
        `${catalogPath(businessId)}${suffix}`,
        {
          signal,
        },
      );
    },

    createCatalogItem(businessId: string, input: CatalogItemCreate) {
      return client.request<CatalogItem>(catalogPath(businessId), {
        method: "POST",
        json: input,
      });
    },

    getCatalogItem(businessId: string, itemId: string) {
      return client.request<CatalogItem>(
        `${catalogPath(businessId)}/${encodeURIComponent(itemId)}`,
      );
    },

    updateCatalogItem(
      businessId: string,
      itemId: string,
      input: CatalogItemUpdate,
    ) {
      return client.request<CatalogItem>(
        `${catalogPath(businessId)}/${encodeURIComponent(itemId)}`,
        { method: "PATCH", json: input },
      );
    },

    archiveCatalogItem(businessId: string, itemId: string) {
      return client.request<null>(
        `${catalogPath(businessId)}/${encodeURIComponent(itemId)}`,
        { method: "DELETE" },
      );
    },

    previewCatalogImport(businessId: string, file: File) {
      return client.request<CatalogImportPreviewResponse>(
        `${catalogPath(businessId)}/import/preview`,
        { method: "POST", body: importBody(file) },
      );
    },

    importCatalogFile(businessId: string, file: File) {
      return client.request<CatalogImportResult>(
        `${catalogPath(businessId)}/import`,
        { method: "POST", body: importBody(file) },
      );
    },
  };
}

export const catalogApi = createCatalogApi(apiClient);

export type CatalogApi = ReturnType<typeof createCatalogApi>;

export function catalogImportPreviewFromError(
  error: unknown,
): CatalogImportPreviewResponse | null {
  if (!(error instanceof ApiError)) return null;
  const data = error.data;
  if (
    !data?.file ||
    !data.detected_columns ||
    typeof data.total_rows !== "number" ||
    typeof data.valid_rows !== "number" ||
    typeof data.invalid_rows !== "number" ||
    !Array.isArray(data.preview_rows) ||
    !Array.isArray(data.errors) ||
    typeof data.preview_limit !== "number"
  ) {
    return null;
  }
  return data as CatalogImportPreviewResponse;
}

export function humanizeCatalogError(
  error: unknown,
  fallback = "We couldn't update the catalog. Please try again.",
) {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "This SKU is already used in this business.";
    }
    if (error.status === 403) {
      return "Catalog access is unavailable for this business.";
    }
    if (error.status === 404) {
      return "This business or catalog item is no longer available.";
    }
    if (error.status === 422) {
      return "Fix the highlighted details and try again. Nothing has been imported yet.";
    }
  }
  return humanizeApiError(error, fallback);
}

export async function commitCatalogImportAndReload(
  api: CatalogApi,
  businessId: string,
  file: File,
  filters: CatalogListFilters = {},
) {
  const result = await api.importCatalogFile(businessId, file);
  const items = await api.listCatalogItems(businessId, filters);
  return { result, items };
}
