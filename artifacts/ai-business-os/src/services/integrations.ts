import { apiClient, type ApiClient } from "./api-client.ts";
import type { PageResponse } from "./api-types.ts";

export type IntegrationConnectorType =
  | "whatsapp_business"
  | "gmail"
  | "google_calendar"
  | "google_ads"
  | "meta_ads"
  | "facebook"
  | "instagram"
  | "microsoft_outlook";

export type IntegrationSetupStatus =
  | "available"
  | "provider_setup_required"
  | "coming_soon";

export type ConnectorDefinition = {
  connector_type: IntegrationConnectorType;
  display_name: string;
  description: string;
  category: "communication" | "productivity" | "calendar" | "advertising" | "social";
  authentication_type: "oauth2";
  capabilities: string[];
  read_capabilities: string[];
  future_write_capabilities: string[];
  requested_scopes: string[];
  webhook_support: boolean;
  external_writes_enabled: boolean;
  resource_types: string[];
  configuration_requirements: string[];
  resource_selection_required: boolean;
  setup_status: IntegrationSetupStatus;
};

export type SelectedIntegrationResource = {
  resource_type: string;
  external_reference: string;
  display_name: string;
};

export type IntegrationConnection = {
  id: string;
  business_id: string;
  connector_type: IntegrationConnectorType;
  display_name: string;
  status: "disconnected" | "pending" | "connected" | "degraded" | "reauth_required" | "disabled" | "revoked";
  authentication_state: "not_authorized" | "authorization_pending" | "authorized" | "failed" | "revoked";
  health: "not_checked" | "healthy" | "degraded" | "reauth_required" | "revoked";
  external_account_reference: string | null;
  external_account_display_name: string | null;
  selected_resources: SelectedIntegrationResource[];
  scopes_granted: string[];
  connected_by_user_id: string | null;
  connected_at: string | null;
  last_health_check_at: string | null;
  last_successful_sync_at: string | null;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
};

export type ExternalIntegrationResource = SelectedIntegrationResource & {
  parent_reference: string | null;
  metadata: Record<string, string> | null;
};

export type ExternalCalendarEvent = {
  external_event_id: string;
  external_calendar_reference: string;
  title: string;
  starts_at: string;
  ends_at: string;
  status: "confirmed" | "canceled";
  updated_at: string;
};

export type ExternalMailMessage = {
  external_message_reference: string;
  external_thread_reference: string;
  sender: string;
  subject: string;
  snippet: string;
};

export type ExternalMailMessageContent = ExternalMailMessage & {
  body_text: string;
};

export type IntegrationEvent = {
  id: string;
  business_id: string;
  integration_connection_id: string;
  connector_type: IntegrationConnectorType;
  external_event_id: string;
  event_type: "message_received" | "message_status_updated" | "email_received" | "calendar_event_changed" | "performance_data_available";
  status: "received" | "processed" | "failed" | "duplicate";
  normalized_payload: Record<string, unknown>;
  received_at: string;
  processed_at: string | null;
  failure_code: string | null;
  created_at: string;
};

type AuthorizationStart = {
  connector_type: IntegrationConnectorType;
  authorization_url: string;
  expires_at: string;
};

const businessPath = (businessId: string, path: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/integrations${path}`;

export function createIntegrationsApi(client: ApiClient) {
  return {
    registry: (businessId: string, signal?: AbortSignal) =>
      client.request<ConnectorDefinition[]>(businessPath(businessId, "/registry"), { signal }),
    connections: (businessId: string, signal?: AbortSignal) =>
      client.request<IntegrationConnection[]>(businessPath(businessId, "/connections"), { signal }),
    authorize: (businessId: string, connectorType: IntegrationConnectorType) =>
      client.request<AuthorizationStart>(businessPath(businessId, `/${encodeURIComponent(connectorType)}/authorize`), {
        method: "POST",
        json: { redirect_target: "/integrations" },
      }),
    reconnect: (businessId: string, connectionId: string) =>
      client.request<AuthorizationStart>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/reconnect`), {
        method: "POST",
        json: { redirect_target: "/integrations" },
      }),
    resources: (businessId: string, connectionId: string, signal?: AbortSignal) =>
      client.request<ExternalIntegrationResource[]>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/resources`), { signal }),
    calendarEvents: (
      businessId: string,
      connectionId: string,
      startsAt: string,
      endsAt: string,
      signal?: AbortSignal,
    ) => {
      const query = new URLSearchParams({
        starts_at: startsAt,
        ends_at: endsAt,
      });

      return client.request<ExternalCalendarEvent[]>(
        businessPath(
          businessId,
          `/connections/${encodeURIComponent(connectionId)}/calendar/events?${query.toString()}`,
        ),
        { signal },
      );
    },
    mailMessages: (
      businessId: string,
      connectionId: string,
      signal?: AbortSignal,
    ) =>
      client.request<ExternalMailMessage[]>(
        businessPath(
          businessId,
          `/connections/${encodeURIComponent(connectionId)}/mail/messages?limit=5`,
        ),
        { signal },
      ),
    mailMessage: (
      businessId: string,
      connectionId: string,
      messageReference: string,
      signal?: AbortSignal,
    ) =>
      client.request<ExternalMailMessageContent>(
        businessPath(
          businessId,
          `/connections/${encodeURIComponent(connectionId)}/mail/messages/${encodeURIComponent(messageReference)}`,
        ),
        { signal },
      ),
    selectResource: (
      businessId: string,
      connectionId: string,
      resource: Pick<ExternalIntegrationResource, "resource_type" | "external_reference">,
    ) => client.request<IntegrationConnection>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/resources/select`), {
      method: "POST",
      json: {
        resource_type: resource.resource_type,
        external_reference: resource.external_reference,
      },
    }),
    health: (businessId: string, connectionId: string) =>
      client.request<IntegrationConnection>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/health`), { method: "POST" }),
    disconnect: (businessId: string, connectionId: string) =>
      client.request<IntegrationConnection>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/disconnect`), { method: "POST" }),
    events: (businessId: string, connectionId: string, signal?: AbortSignal) =>
      client.request<PageResponse<IntegrationEvent>>(businessPath(businessId, `/connections/${encodeURIComponent(connectionId)}/events?page=1&page_size=25`), { signal }),
  };
}

export const integrationsApi = createIntegrationsApi(apiClient);
