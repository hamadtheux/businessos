import { apiClient, type ApiClient } from "./api-client.ts";

export type CommerceProvider =
  | "shopify"
  | "woocommerce"
  | "bigcommerce"
  | "magento"
  | "custom_api"
  | "csv"
  | "xml_feed"
  | "google_product_feed"
  | "website"
  | "manual";

export type CommerceProviderDefinition = {
  provider: CommerceProvider;
  display_name: string;
  authentication: "local_import" | "provider_configuration";
  capabilities: string[];
  configured: boolean;
  implementation_status:
    | "production_functional"
    | "code_ready_credentials_required"
    | "partially_implemented"
    | "not_implemented";
};

export type CommerceConnection = {
  id: string;
  business_id: string;
  integration_connection_id: string | null;
  provider: CommerceProvider;
  display_name: string;
  external_account_id: string | null;
  store_url: string | null;
  status:
    | "configuration_required"
    | "connection_required"
    | "connected"
    | "syncing"
    | "attention_required"
    | "authentication_expired"
    | "rate_limited"
    | "failed"
    | "disabled";
  health:
    | "not_checked"
    | "healthy"
    | "degraded"
    | "reauth_required"
    | "rate_limited"
    | "failed"
    | "disabled";
  capabilities: string[];
  last_sync_started_at: string | null;
  last_sync_completed_at: string | null;
  last_success_at: string | null;
  failure_code: string | null;
  consecutive_failures: number;
  store_name: string | null;
  created_at: string;
  updated_at: string;
};

export type CommerceSyncRun = {
  id: string;
  business_id: string;
  connection_id: string;
  mode: "initial" | "incremental" | "full" | "manual_retry";
  idempotency_key: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "completed_with_issues"
    | "failed"
    | "configuration_required";
  started_at: string | null;
  completed_at: string | null;
  products_created: number;
  products_updated: number;
  products_archived: number;
  variants_processed: number;
  customers_created: number;
  customers_updated: number;
  orders_created: number;
  orders_updated: number;
  refunds_processed: number;
  fulfillments_processed: number;
  pages_processed: number;
  warnings: number;
  failures: number;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
};

export type CommerceSyncIssue = {
  id: string;
  business_id: string;
  sync_run_id: string;
  external_object_id: string | null;
  severity: "warning" | "error";
  code: string;
  message: string;
  created_at: string;
};

export type CommerceImportPreview = {
  file_type: "csv" | "xml_feed" | "google_product_feed";
  detected_fields: string[];
  products: Array<{ external_object_id: string; name: string }>;
  failures: Array<{
    item_number: number;
    external_object_id: string | null;
    code: string;
    message: string;
  }>;
  truncated: boolean;
};

export type CommerceImportResult = {
  sync_run_id: string;
  status: "completed" | "completed_with_issues" | "failed";
  products_created: number;
  products_updated: number;
  products_failed: number;
  failures: CommerceImportPreview["failures"];
};

export type FeedDestination = {
  id: string;
  business_id: string;
  provider: "google_merchant_center" | "meta_product_catalog";
  external_account_id: string | null;
  integration_connection_id: string | null;
  external_resource_id: string | null;
  managed: boolean;
  content_language: string;
  feed_label: string | null;
  display_name: string;
  status:
    | "configuration_required"
    | "connection_required"
    | "connected"
    | "syncing"
    | "attention_required"
    | "disabled";
  synchronized_count: number;
  submitted_count: number;
  eligible_count: number;
  limited_count: number;
  warning_count: number;
  rejected_count: number;
  last_synchronized_at: string | null;
  failure_code: string | null;
  safe_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type FeedProductStatus = {
  id: string;
  business_id: string;
  destination_id: string;
  catalog_item_id: string;
  external_product_id: string | null;
  status:
    | "attention_required"
    | "pending"
    | "submitted"
    | "processing"
    | "eligible"
    | "limited"
    | "warning"
    | "ineligible"
    | "rejected"
    | "error"
    | "archived"
    | "removed";
  missing_attributes: string[];
  warnings: string[];
  provider_error_code: string | null;
  provider_issues: Array<Record<string, unknown>>;
  owned_by_aibos: boolean;
  submitted_at: string | null;
  last_synchronized_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ProductGroup = {
  id: string;
  business_id: string;
  name: string;
  external_key: string;
  group_type:
    | "manual"
    | "category"
    | "collection"
    | "brand"
    | "tag"
    | "price"
    | "margin"
    | "best_sellers"
    | "new_products"
    | "promotion"
    | "custom_rule";
  rule: Record<string, unknown>;
  status: "draft" | "active" | "archived";
  created_by_user_id: string | null;
  catalog_item_ids: string[];
  created_at: string;
  updated_at: string;
};

export type ProductGroupDestination = {
  id: string;
  business_id: string;
  product_group_id: string;
  destination_id: string;
  external_reference: string | null;
  status: "pending" | "submitted" | "ready" | "attention_required" | "archived";
  failure_code: string | null;
  created_at: string;
  updated_at: string;
};

const path = (businessId: string, suffix: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/commerce${suffix}`;

export function createCommerceApi(client: ApiClient) {
  return {
    providers: (businessId: string, signal?: AbortSignal) =>
      client.request<CommerceProviderDefinition[]>(
        path(businessId, "/providers"),
        { signal },
      ),
    connections: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<CommerceConnection[]>(path(businessId, "/connections"), {
          signal,
        }),
      create: (
        businessId: string,
        data: {
          provider: CommerceProvider;
          display_name: string;
          external_account_id?: string | null;
          store_url?: string | null;
          integration_connection_id?: string | null;
        },
      ) =>
        client.request<CommerceConnection>(path(businessId, "/connections"), {
          method: "POST",
          json: data,
        }),
      configure: (
        businessId: string,
        connectionId: string,
        credentials: Record<string, string>,
      ) =>
        client.request<CommerceConnection>(
          path(
            businessId,
            `/connections/${encodeURIComponent(connectionId)}/configure`,
          ),
          {
            method: "POST",
            json: { credentials },
          },
        ),
      sync: (
        businessId: string,
        connectionId: string,
        mode: CommerceSyncRun["mode"] = "incremental",
      ) =>
        client.request<CommerceSyncRun>(
          path(
            businessId,
            `/connections/${encodeURIComponent(connectionId)}/sync`,
          ),
          {
            method: "POST",
            json: { mode, idempotency_key: `ui:${connectionId}:${Date.now()}` },
          },
        ),
      syncRuns: (
        businessId: string,
        connectionId: string,
        signal?: AbortSignal,
      ) =>
        client.request<CommerceSyncRun[]>(
          path(
            businessId,
            `/connections/${encodeURIComponent(connectionId)}/sync-runs`,
          ),
          { signal },
        ),
    },
    syncIssues: (businessId: string, syncRunId: string, signal?: AbortSignal) =>
      client.request<CommerceSyncIssue[]>(
        path(businessId, `/sync-runs/${encodeURIComponent(syncRunId)}/issues`),
        { signal },
      ),
    imports: {
      preview: (
        businessId: string,
        fileType: CommerceImportPreview["file_type"],
        file: File,
        mapping: Record<string, string> = {},
      ) => {
        const form = new FormData();
        form.append("file_type", fileType);
        form.append("mapping_json", JSON.stringify({ fields: mapping }));
        form.append("upload", file, file.name);
        return client.request<CommerceImportPreview>(
          path(businessId, "/imports/preview"),
          { method: "POST", body: form },
        );
      },
      apply: (
        businessId: string,
        connectionId: string,
        fileType: CommerceImportPreview["file_type"],
        file: File,
        idempotencyKey: string,
        mapping: Record<string, string> = {},
      ) => {
        const form = new FormData();
        form.append("file_type", fileType);
        form.append("idempotency_key", idempotencyKey);
        form.append("mapping_json", JSON.stringify({ fields: mapping }));
        form.append("upload", file, file.name);
        return client.request<CommerceImportResult>(
          path(
            businessId,
            `/connections/${encodeURIComponent(connectionId)}/imports`,
          ),
          { method: "POST", body: form },
        );
      },
    },
    feeds: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<FeedDestination[]>(
          path(businessId, "/feed-destinations"),
          { signal },
        ),
      create: (
        businessId: string,
        data: {
          provider: FeedDestination["provider"];
          display_name: string;
          external_account_id?: string | null;
          integration_connection_id?: string | null;
          external_resource_id?: string | null;
          content_language?: string;
          feed_label?: string | null;
          managed?: boolean;
        },
      ) =>
        client.request<FeedDestination>(
          path(businessId, "/feed-destinations"),
          {
            method: "POST",
            json: data,
          },
        ),
      evaluate: (businessId: string, destinationId: string) =>
        client.request<FeedDestination>(
          path(
            businessId,
            `/feed-destinations/${encodeURIComponent(destinationId)}/evaluate`,
          ),
          { method: "POST" },
        ),
      products: (
        businessId: string,
        destinationId: string,
        signal?: AbortSignal,
      ) =>
        client.request<FeedProductStatus[]>(
          path(
            businessId,
            `/feed-destinations/${encodeURIComponent(destinationId)}/products`,
          ),
          { signal },
        ),
      sync: (
        businessId: string,
        destinationId: string,
        reconcileOnly = false,
      ) =>
        client.request<FeedDestination>(
          path(
            businessId,
            `/feed-destinations/${encodeURIComponent(destinationId)}/sync`,
          ),
          {
            method: "POST",
            json: {
              idempotency_key: `ui-feed:${destinationId}:${Date.now()}`,
              reconcile_only: reconcileOnly,
            },
          },
        ),
    },
    productGroups: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<ProductGroup[]>(path(businessId, "/product-groups"), {
          signal,
        }),
      create: (
        businessId: string,
        data: {
          name: string;
          external_key: string;
          group_type?: ProductGroup["group_type"];
          rule?: Record<string, unknown>;
          catalog_item_ids: string[];
        },
      ) =>
        client.request<ProductGroup>(path(businessId, "/product-groups"), {
          method: "POST",
          json: data,
        }),
      sync: (
        businessId: string,
        productGroupId: string,
        destinationId: string,
      ) =>
        client.request<ProductGroupDestination>(
          path(
            businessId,
            `/product-groups/${encodeURIComponent(productGroupId)}/sync`,
          ),
          {
            method: "POST",
            json: {
              destination_id: destinationId,
              idempotency_key: `ui-group:${productGroupId}:${destinationId}`,
            },
          },
        ),
    },
  };
}

export const commerceApi = createCommerceApi(apiClient);
