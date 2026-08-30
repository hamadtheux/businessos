import { apiClient } from "./api-client.ts";

export type BillingEntitlements = Record<string, boolean | number>;

export type BillingOverview = {
  business_id: string;
  subscription_id: string | null;
  plan_id: string;
  plan_version_id: string;
  plan_code: string;
  plan_name: string;
  plan_version: number;
  subscription_status: string;
  access_reason: string;
  billing_interval: "month" | "year";
  current_period_start: string;
  current_period_end: string;
  trial_started_at: string | null;
  trial_ends_at: string | null;
  cancel_at_period_end: boolean;
  entitlements: BillingEntitlements;
  provider_configured: boolean;
  test_plan_activation_enabled: boolean;
};

export type BillingUsage = {
  period_start: string;
  period_end: string;
  usage: Record<string, number>;
  limits: Record<string, number>;
  remaining: Record<string, number>;
  informational: Record<string, number>;
};

export type BillingPlan = {
  id: string;
  version_id: string;
  code: string;
  display_name: string;
  description: string;
  version: number;
  currency: string;
  monthly_price_minor: number | null;
  yearly_price_minor: number | null;
  trial_days: number;
  active: boolean;
  public: boolean;
  entitlements: BillingEntitlements;
};

export type PlanChangeIntent = {
  status:
    | "provider_unavailable"
    | "blocked"
    | "checkout_ready"
    | "test_activated";
  message: string;
  blockers: Array<{
    entitlement_key: string;
    current: number;
    target_limit: number;
  }>;
  checkout_url: string | null;
  billing: BillingOverview | null;
};

export function isCurrentBillingResponse(
  requestedBusinessId: string,
  responseBusinessId: string,
  requestVersion: number,
  currentVersion: number,
  activeBusinessId: string,
) {
  return (
    requestedBusinessId === responseBusinessId &&
    requestedBusinessId === activeBusinessId &&
    requestVersion === currentVersion
  );
}

export function shouldRequestPlanChange(
  currentPlanCode: string,
  targetPlanCode: string,
) {
  return currentPlanCode !== targetPlanCode;
}

export function billingPlanActionLabel(
  current: boolean,
  planCode: string,
  displayName: string,
  providerConfigured: boolean,
  testPlanActivationEnabled: boolean,
) {
  if (current) return "Selected";
  if (testPlanActivationEnabled && planCode !== "free") {
    return `Activate ${displayName}`;
  }
  return providerConfigured ? "Choose plan" : "Check availability";
}

function root(businessId: string) {
  return `/api/v1/businesses/${encodeURIComponent(businessId)}/billing`;
}

export function createBillingApi(client = apiClient) {
  return {
  overview: (businessId: string, signal?: AbortSignal) =>
    client.request<BillingOverview>(root(businessId), { signal }),
  usage: (businessId: string, signal?: AbortSignal) =>
    client.request<BillingUsage>(`${root(businessId)}/usage`, { signal }),
  plans: (businessId: string, signal?: AbortSignal) =>
    client.request<BillingPlan[]>(`${root(businessId)}/plans`, { signal }),
  changeIntent: (
    businessId: string,
    planCode: string,
    billingInterval: "month" | "year",
  ) =>
    client.request<PlanChangeIntent>(`${root(businessId)}/change-intent`, {
      method: "POST",
      json: { plan_code: planCode, billing_interval: billingInterval },
    }),
  cancel: (businessId: string, reason: string) =>
    client.request<{
      status: string;
      cancel_at_period_end: boolean;
      current_period_end: string;
    }>(`${root(businessId)}/cancel`, {
      method: "POST",
      json: { reason },
    }),
  reactivate: (businessId: string) =>
    client.request<{
      status: string;
      cancel_at_period_end: boolean;
      current_period_end: string;
    }>(`${root(businessId)}/reactivate`, { method: "POST" }),
  };
}

export const billingApi = createBillingApi();
