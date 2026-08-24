import { apiClient, type ApiClient } from "./api-client.ts";

export type AgentRole =
  | "business_manager"
  | "cmo"
  | "sales"
  | "support"
  | "operations"
  | "analytics";
export type AutonomyMode = "manual" | "supervised" | "autonomous";

export type AgentCapability = {
  key: string;
  category: "read" | "analysis" | "draft" | "governed_action";
  description: string;
};

export type AgentMetrics = {
  execution_count: number;
  completed_count: number;
  needs_approval_count: number;
  failed_count: number;
  average_duration_ms: number | null;
  proposed_action_count: number;
  pending_approval_count: number;
  approval_rate: number | null;
  input_tokens: number;
  output_tokens: number;
};

export type AgentConfig = {
  id: string;
  business_id: string;
  role: AgentRole;
  display_name: string;
  enabled: boolean;
  status: "active" | "disabled";
  health: "ready" | "not_configured";
  autonomy_mode: AutonomyMode;
  autonomy_description: string;
  custom_instructions: string | null;
  capabilities: AgentCapability[];
  default_capabilities: string[];
  role_description: string;
  metrics: AgentMetrics;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ApprovalLink = { id: string; status: string; reason_code: string };
export type AgentProposedAction = {
  id: string;
  execution_id: string;
  action_type: string;
  description: string;
  risk_level: string;
  status: string;
  policy_decision: string | null;
  requires_approval: boolean;
  approval: ApprovalLink | null;
};

export type AgentActivity = {
  id: string;
  business_id: string;
  command_id: string | null;
  parent_execution_id: string | null;
  role: AgentRole;
  trigger: string;
  status: string;
  task_summary: string;
  summary: string | null;
  failure_code: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: string | null;
  delegation_sequence: number;
  delegation_depth: number;
  proposed_actions: AgentProposedAction[];
  created_at: string;
  completed_at: string | null;
};

export type PageResponse<T> = { items: T[]; page: number; page_size: number; total: number };
export type CommandRoute = {
  primary_role: AgentRole;
  intent: string;
  required_capabilities: string[];
  relevant_modules: string[];
  delegation_roles: AgentRole[];
  clarification_required: boolean;
};
export type AICommand = {
  id: string;
  business_id: string;
  requested_by_user_id: string | null;
  command: string;
  status: "queued" | "running" | "completed" | "needs_approval" | "failed" | "canceled";
  route: CommandRoute;
  execution_id: string | null;
  summary: string | null;
  failure_code: string | null;
  executions: AgentActivity[];
  proposed_actions: AgentProposedAction[];
  created_at: string;
  completed_at: string | null;
};
export type SuggestedCommand = { command: string; reason: string; role: AgentRole };
export type DailyBrief = {
  generated_at: string;
  sections: Array<{ key: string; title: string; facts: string[] }>;
  recommended_priorities: string[];
};
export type CommandContextReference = {
  type: "customer" | "lead" | "order" | "conversation" | "appointment_type" | "provider" | "campaign" | "workflow" | "report";
  id: string;
};

const path = (businessId: string, suffix: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}${suffix}`;

export function createAIWorkforceApi(client: ApiClient) {
  return {
    agents: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<AgentConfig[]>(path(businessId, "/agents"), { signal }),
      get: (businessId: string, role: AgentRole, signal?: AbortSignal) =>
        client.request<AgentConfig>(path(businessId, `/agents/${role}`), { signal }),
      capabilities: (businessId: string, signal?: AbortSignal) =>
        client.request<AgentCapability[]>(path(businessId, "/agents/capabilities"), { signal }),
      update: (
        businessId: string,
        role: AgentRole,
        data: Partial<Pick<AgentConfig, "display_name" | "enabled" | "autonomy_mode">> & {
          custom_instructions?: string | null;
          capabilities?: string[];
        },
      ) => client.request<AgentConfig>(path(businessId, `/agents/${role}`), { method: "PATCH", json: data }),
      reset: (businessId: string, role: AgentRole) =>
        client.request<AgentConfig>(path(businessId, `/agents/${role}/reset`), { method: "POST" }),
      activity: (
        businessId: string,
        options: { page?: number; pageSize?: number; role?: AgentRole; status?: string } = {},
        signal?: AbortSignal,
      ) => {
        const query = new URLSearchParams({
          page: String(options.page ?? 1),
          page_size: String(options.pageSize ?? 25),
        });
        if (options.role) query.set("role", options.role);
        if (options.status) query.set("status", options.status);
        return client.request<PageResponse<AgentActivity>>(
          path(businessId, `/agents/activity?${query}`),
          { signal },
        );
      },
      activityDetail: (businessId: string, executionId: string, signal?: AbortSignal) =>
        client.request<AgentActivity>(path(businessId, `/agents/activity/${executionId}`), { signal }),
    },
    commands: {
      execute: (
        businessId: string,
        command: string,
        trigger_source: "command_center" | "dashboard" | "agent_detail" = "command_center",
        context_references: CommandContextReference[] = [],
      ) => client.request<AICommand>(path(businessId, "/commands"), {
        method: "POST",
        json: { command, trigger_source, context_references },
      }),
      list: (businessId: string, page = 1, signal?: AbortSignal) =>
        client.request<PageResponse<AICommand>>(path(businessId, `/commands?page=${page}&page_size=20`), { signal }),
      get: (businessId: string, commandId: string, signal?: AbortSignal) =>
        client.request<AICommand>(path(businessId, `/commands/${commandId}`), { signal }),
      cancel: (businessId: string, commandId: string) =>
        client.request<AICommand>(path(businessId, `/commands/${commandId}/cancel`), { method: "POST" }),
      suggestions: (businessId: string, signal?: AbortSignal) =>
        client.request<SuggestedCommand[]>(path(businessId, "/commands/suggestions"), { signal }),
      dailyBrief: (businessId: string, signal?: AbortSignal) =>
        client.request<DailyBrief>(path(businessId, "/commands/daily-brief"), { signal }),
    },
  };
}

export const aiWorkforceApi = createAIWorkforceApi(apiClient);
