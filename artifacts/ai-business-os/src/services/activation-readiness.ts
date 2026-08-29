import { apiClient, type ApiClient } from "./api-client.ts";

export type ActivationReadinessState =
  | "ready"
  | "action_needed"
  | "not_applicable";

export type ActivationReadinessCheck = {
  id: string;
  label: string;
  state: ActivationReadinessState;
  required: boolean;
  detail: string;
  href: string;
  evidence: Record<string, string | number | boolean | null>;
};

export type ActivationReadiness = {
  activation_ready: boolean;
  overall_status: "ready" | "action_needed";
  ready_required_checks: number;
  required_checks: number;
  checks: ActivationReadinessCheck[];
  generated_at: string;
};

export function createActivationReadinessApi(client: ApiClient) {
  return {
    get: (businessId: string, signal?: AbortSignal) =>
      client.request<ActivationReadiness>(
        `/api/v1/businesses/${encodeURIComponent(businessId)}/activation-readiness`,
        { signal },
      ),
  };
}

export const activationReadinessApi = createActivationReadinessApi(apiClient);
