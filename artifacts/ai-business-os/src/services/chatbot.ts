import { apiClient, type ApiClient } from "./api-client.ts";

export type PublicChatbotCapability =
  | "answer_business_questions"
  | "search_products_services"
  | "recommend_products_services"
  | "capture_lead"
  | "lookup_available_appointments"
  | "book_appointment"
  | "lookup_order_status"
  | "request_human_handoff";

export type ChatbotConfigUpdate = {
  enabled: boolean;
  display_name: string;
  welcome_message: string;
  placeholder_text: string;
  tone: "friendly" | "professional" | "concise" | "warm";
  theme: "light" | "dark" | "auto";
  position: "bottom_right" | "bottom_left";
  launcher_style: "bubble" | "pill";
  allowed_capabilities: PublicChatbotCapability[];
  allowed_domains: string[];
  privacy_policy_url: string | null;
  consent_text: string | null;
  require_lead_consent: boolean;
  default_locale: string;
  border_radius: number;
};

export type ChatbotConfig = ChatbotConfigUpdate & {
  id: string;
  business_id: string;
  widget_public_id: string;
  available_capabilities: PublicChatbotCapability[];
  embed_snippet: string;
  ai_runtime_status: "ready" | "configuration_required";
  lifecycle_status: "draft" | "ready" | "live" | "needs_ai_provider";
  created_at: string;
  updated_at: string;
};

export type ChatbotAnalytics = {
  period_start: string;
  period_end: string;
  sessions: number;
  conversations: number;
  messages: number;
  leads_captured: number;
  handoffs: number;
  appointments_booked: number;
  order_lookups: number;
  product_recommendations: number;
  ai_failures: number;
  average_response_duration_ms: number | null;
};

export type ChatbotDeploymentTarget = {
  target_type: "hosted" | "shopify" | "wordpress" | "wix" | "webflow" | "squarespace" | "google_tag_manager" | "other" | "manual_embed";
  display_name: string;
  state: "available" | "connection_required" | "connected" | "installation_supported" | "installed" | "needs_manual_step" | "unsupported";
  provider_key: string | null;
  deployment_target_key: string | null;
  provider_resource_reference: string | null;
  automatic_install: boolean;
  hosted_url: string | null;
  instructions: string[];
  verification_status: "not_checked" | "healthy" | "failed";
  installed_at: string | null;
  last_verified_at: string | null;
  failure_code: string | null;
};

export type ChatbotDeploymentList = {
  targets: ChatbotDeploymentTarget[];
  advanced_embed_snippet: string;
  ai_runtime_status: "ready" | "configuration_required";
  assistant_status: "draft" | "ready" | "live" | "needs_ai_provider";
};

const path = (businessId: string, tail = "") =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/chatbot${tail}`;

export function createChatbotApi(client: ApiClient) {
  return {
    get: (businessId: string, signal?: AbortSignal) =>
      client.request<ChatbotConfig>(path(businessId), { signal }),
    update: (businessId: string, data: ChatbotConfigUpdate) =>
      client.request<ChatbotConfig>(path(businessId), { method: "PUT", json: data }),
    rotateWidgetId: (businessId: string) =>
      client.request<ChatbotConfig>(path(businessId, "/widget-id/rotate"), { method: "POST" }),
    deployments: (businessId: string, signal?: AbortSignal) =>
      client.request<ChatbotDeploymentList>(path(businessId, "/deployments"), { signal }),
    installHosted: (businessId: string) =>
      client.request<ChatbotDeploymentTarget>(path(businessId, "/deployments/hosted"), { method: "POST" }),
    analytics: (
      businessId: string,
      periodStart: string,
      periodEnd: string,
      signal?: AbortSignal,
    ) => client.request<ChatbotAnalytics>(
      `${path(businessId, "/analytics")}?period_start=${encodeURIComponent(periodStart)}&period_end=${encodeURIComponent(periodEnd)}`,
      { signal },
    ),
  };
}

export const chatbotApi = createChatbotApi(apiClient);
