import type { CatalogItem } from "@/services/api-types";
import type {
  CommerceConnection,
  CommerceProvider,
  CommerceProviderDefinition,
  CommerceSyncRun,
  FeedDestination,
} from "@/services/commerce";

export type CatalogStatusTone = "success" | "neutral" | "warning" | "danger";

export const STORE_PROVIDER_KEYS: CommerceProvider[] = [
  "shopify",
  "woocommerce",
  "bigcommerce",
  "magento",
];

const providerLabels: Record<CommerceProvider, string> = {
  shopify: "Shopify",
  woocommerce: "WooCommerce",
  bigcommerce: "BigCommerce",
  magento: "Magento / Adobe Commerce",
  custom_api: "Custom API",
  csv: "CSV import",
  xml_feed: "XML feed",
  google_product_feed: "Google product feed",
  website: "Website",
  manual: "Manual",
};

export function commerceProviderLabel(provider: string) {
  return providerLabels[provider as CommerceProvider] ?? titleCase(provider);
}

export function providerAvailability(
  status: CommerceProviderDefinition["implementation_status"],
): { label: string; tone: CatalogStatusTone; selectable: boolean } {
  switch (status) {
    case "production_functional":
      return { label: "Available", tone: "success", selectable: true };
    case "code_ready_credentials_required":
      return { label: "Setup required", tone: "warning", selectable: true };
    case "partially_implemented":
      return {
        label: "Limited availability",
        tone: "warning",
        selectable: true,
      };
    case "not_implemented":
      return { label: "Not available", tone: "neutral", selectable: false };
  }
}

export function commerceConnectionStatus(
  connection: Pick<CommerceConnection, "status" | "health">,
): { label: string; tone: CatalogStatusTone } {
  if (connection.status === "syncing") {
    return { label: "Syncing", tone: "warning" };
  }
  if (connection.status === "connected" && connection.health === "healthy") {
    return { label: "Connected", tone: "success" };
  }
  if (
    connection.status === "configuration_required" ||
    connection.status === "connection_required"
  ) {
    return { label: "Setup required", tone: "warning" };
  }
  if (
    connection.status === "attention_required" ||
    connection.status === "authentication_expired" ||
    connection.status === "rate_limited" ||
    connection.status === "failed" ||
    connection.health === "degraded" ||
    connection.health === "reauth_required" ||
    connection.health === "rate_limited" ||
    connection.health === "failed"
  ) {
    return { label: "Needs attention", tone: "danger" };
  }
  return { label: "Not connected", tone: "neutral" };
}

export function syncRunStatus(status: CommerceSyncRun["status"]) {
  switch (status) {
    case "queued":
    case "running":
      return "Syncing";
    case "completed":
      return "Completed";
    case "completed_with_issues":
      return "Completed with issues";
    case "failed":
      return "Needs attention";
    case "configuration_required":
      return "Setup required";
  }
}

export function syncRunMode(mode: CommerceSyncRun["mode"]) {
  switch (mode) {
    case "initial":
      return "First sync";
    case "incremental":
      return "Latest changes";
    case "full":
      return "Full sync";
    case "manual_retry":
      return "Retry";
  }
}

export function feedDestinationStatus(status: FeedDestination["status"]): {
  label: string;
  tone: CatalogStatusTone;
} {
  switch (status) {
    case "connected":
      return { label: "Connected", tone: "success" };
    case "syncing":
      return { label: "Syncing", tone: "warning" };
    case "configuration_required":
    case "connection_required":
      return { label: "Setup required", tone: "warning" };
    case "attention_required":
      return { label: "Needs attention", tone: "danger" };
    case "disabled":
      return { label: "Not connected", tone: "neutral" };
  }
}

export function catalogSourceLabel(item: Pick<CatalogItem, "source">) {
  return commerceProviderLabel(item.source);
}

export function catalogStatusLabel(status: CatalogItem["status"]) {
  return titleCase(status);
}

export function catalogCountLabel(items: CatalogItem[]) {
  const count = items.length;
  if (items.every((item) => item.item_type === "product")) {
    return `${count.toLocaleString()} ${count === 1 ? "product" : "products"}`;
  }
  if (items.every((item) => item.item_type === "service")) {
    return `${count.toLocaleString()} ${count === 1 ? "service" : "services"}`;
  }
  return `${count.toLocaleString()} ${count === 1 ? "item" : "items"}`;
}

export function formatLastSync(value: string | null, now = Date.now()) {
  if (!value) return "Not synced yet";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Sync time unavailable";
  const elapsedMinutes = Math.max(0, Math.round((now - timestamp) / 60_000));
  if (elapsedMinutes < 1) return "Synced just now";
  if (elapsedMinutes < 60) return `Synced ${elapsedMinutes}m ago`;
  const elapsedHours = Math.round(elapsedMinutes / 60);
  if (elapsedHours < 24) return `Synced ${elapsedHours}h ago`;
  const elapsedDays = Math.round(elapsedHours / 24);
  return `Synced ${elapsedDays}d ago`;
}

function titleCase(value: string) {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
