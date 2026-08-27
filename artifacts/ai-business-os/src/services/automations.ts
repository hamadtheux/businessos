import { apiClient, type ApiClient } from "./api-client.ts";

export type WorkflowStatus = "draft" | "active" | "paused" | "archived";
export type AutomationNodeType =
  | "trigger"
  | "condition"
  | "branch"
  | "action"
  | "delay"
  | "approval"
  | "ai"
  | "internal_operation"
  | "end";
export type WorkflowRunStatus =
  "queued" | "running" | "waiting" | "succeeded" | "failed" | "canceled";

export type AutomationWorkflow = {
  id: string;
  business_id: string;
  name: string;
  description: string | null;
  status: WorkflowStatus;
  current_version: number;
  trigger_type: string;
  enabled: boolean;
  timezone: string;
  schedule_definition: Record<string, unknown>;
  next_run_at: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  last_run_status: WorkflowRunStatus | null;
  last_run_at: string | null;
};

export type AutomationNode = {
  id: string;
  node_key: string;
  node_type: AutomationNodeType;
  name: string;
  configuration: Record<string, unknown>;
  position_x: number;
  position_y: number;
  order_index: number;
};

export type AutomationEdge = {
  id: string;
  edge_key: string;
  source_node_key: string;
  target_node_key: string;
  branch_label: string | null;
  order_index: number;
};

export type WorkflowDetail = AutomationWorkflow & {
  version_id: string;
  nodes: AutomationNode[];
  edges: AutomationEdge[];
};
export type AutomationCopilotResult = {
  workflow: WorkflowDetail;
  explanation: string;
  required_integrations: string[];
  missing_information: string[];
  stop_conditions: string[];
  proposed_actions: Array<{
    action_type: "send_email" | "send_whatsapp_message" | "send_customer_message";
    channel: "email" | "whatsapp" | "customer_message";
    condition: string;
    policy_behavior: string;
    execution_state: "withheld_pending_authoritative_inputs";
  }>;
  executable_actions_withheld: boolean;
};
export type PageResponse<T> = {
  items: T[];
  page: number;
  page_size: number;
  total: number;
};

export type SimulationTrace = {
  node_key: string;
  node_type: AutomationNodeType;
  name: string;
  status: "succeeded" | "planned" | "waiting" | "failed";
  branch_outcome: string | null;
  summary: string;
};
export type SimulationResult = {
  valid: boolean;
  completed: boolean;
  trace: SimulationTrace[];
  approvals: Array<Record<string, unknown>>;
  delays: Array<Record<string, unknown>>;
  planned_actions: Array<Record<string, unknown>>;
  errors: string[];
};

export type WorkflowRun = {
  id: string;
  business_id: string;
  workflow_id: string;
  workflow_version_id: string;
  trigger_event_id: string | null;
  trigger_type: "event" | "schedule" | "manual";
  status: WorkflowRunStatus;
  context_payload: Record<string, unknown>;
  current_node_key: string | null;
  waiting_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  failure_code: string | null;
  requested_by_user_id: string | null;
  created_at: string;
  updated_at: string;
  workflow_name: string | null;
  version: number | null;
};

export type NodeRun = {
  id: string;
  workflow_run_id: string;
  node_key: string;
  status:
    "running" | "succeeded" | "waiting" | "failed" | "skipped" | "canceled";
  attempt: number;
  started_at: string;
  completed_at: string | null;
  branch_outcome: string | null;
  result_summary: string | null;
  failure_code: string | null;
  resume_at: string | null;
  action_id: string | null;
  node_name: string | null;
  node_type: AutomationNodeType | null;
};

export type Approval = {
  id: string;
  business_id: string;
  action_id: string | null;
  workflow_node_run_id: string | null;
  requested_by_user_id: string | null;
  status: "pending" | "approved" | "rejected" | "expired" | "canceled";
  reason_code: string;
  requested_at: string;
  expires_at: string | null;
  decided_at: string | null;
  decided_by_user_id: string | null;
  decision_actor_id: string | null;
  decision_note: string | null;
  created_at: string;
  updated_at: string;
  target_type: "ai_action" | "workflow_node" | null;
  action: {
    id: string;
    action_type: string;
    description: string;
    risk_level: string;
    status: string;
    policy_decision: string | null;
    policy_reason_code: string | null;
    provider_channel: string;
    affected_entity: string;
    audience_or_recipient: string | null;
    budget_summary: string | null;
    payload_summary: Record<string, string | number | boolean>;
  } | null;
  workflow: {
    node_run_id: string;
    run_id: string;
    workflow_id: string;
    workflow_name: string;
    node_key: string;
    node_name: string;
    run_status: string;
  } | null;
};

const path = (businessId: string, suffix: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/automations${suffix}`;
const approvalPath = (businessId: string, suffix = "") =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/approvals${suffix}`;

export function createAutomationsApi(client: ApiClient) {
  return {
    copilot: {
      compile: (businessId: string, data: { prompt: string; name?: string | null; timezone?: string }) =>
        client.request<AutomationCopilotResult>(path(businessId, "/copilot/compile"), {
          method: "POST", json: data,
        }),
      refine: (businessId: string, workflowId: string, instruction: string) =>
        client.request<AutomationCopilotResult>(path(businessId, `/copilot/${workflowId}/refine`), {
          method: "POST", json: { instruction },
        }),
    },
    workflows: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<PageResponse<AutomationWorkflow>>(
          path(businessId, "/workflows?page=1&page_size=100"),
          { signal },
        ),
      get: (businessId: string, workflowId: string, signal?: AbortSignal) =>
        client.request<WorkflowDetail>(
          path(businessId, `/workflows/${workflowId}`),
          { signal },
        ),
      create: (
        businessId: string,
        data: {
          name: string;
          description?: string | null;
          trigger_type: string;
          timezone?: string;
          schedule_definition?: Record<string, unknown> | null;
        },
      ) =>
        client.request<AutomationWorkflow>(path(businessId, "/workflows"), {
          method: "POST",
          json: data,
        }),
      update: (
        businessId: string,
        workflowId: string,
        data: Partial<
          Pick<
            AutomationWorkflow,
            | "name"
            | "description"
            | "trigger_type"
            | "timezone"
            | "schedule_definition"
          >
        >,
      ) =>
        client.request<AutomationWorkflow>(
          path(businessId, `/workflows/${workflowId}`),
          { method: "PATCH", json: data },
        ),
      duplicate: (businessId: string, workflowId: string) =>
        client.request<AutomationWorkflow>(
          path(businessId, `/workflows/${workflowId}/duplicate`),
          { method: "POST" },
        ),
      status: (
        businessId: string,
        workflowId: string,
        value: "active" | "paused" | "archived",
      ) =>
        client.request<AutomationWorkflow>(
          path(businessId, `/workflows/${workflowId}/status`),
          { method: "POST", json: { status: value } },
        ),
      validate: (businessId: string, workflowId: string) =>
        client.request<{
          valid: boolean;
          errors: string[];
          warnings: string[];
        }>(path(businessId, `/workflows/${workflowId}/validation`)),
      simulate: (
        businessId: string,
        workflowId: string,
        data: {
          payload: Record<string, unknown>;
          run_ai?: boolean;
          forced_failure_node_key?: string | null;
        },
      ) =>
        client.request<SimulationResult>(
          path(businessId, `/workflows/${workflowId}/simulate`),
          { method: "POST", json: data },
        ),
    },
    nodes: {
      create: (
        businessId: string,
        workflowId: string,
        data: Omit<AutomationNode, "id">,
      ) =>
        client.request<AutomationNode>(
          path(businessId, `/workflows/${workflowId}/nodes`),
          { method: "POST", json: data },
        ),
      update: (
        businessId: string,
        workflowId: string,
        nodeKey: string,
        data: Partial<Omit<AutomationNode, "id" | "node_key">>,
      ) =>
        client.request<AutomationNode>(
          path(businessId, `/workflows/${workflowId}/nodes/${nodeKey}`),
          { method: "PATCH", json: data },
        ),
      remove: (businessId: string, workflowId: string, nodeKey: string) =>
        client.request<void>(
          path(businessId, `/workflows/${workflowId}/nodes/${nodeKey}`),
          { method: "DELETE" },
        ),
    },
    edges: {
      create: (
        businessId: string,
        workflowId: string,
        data: {
          source_node_key: string;
          target_node_key: string;
          branch_label?: string | null;
          order_index?: number;
        },
      ) =>
        client.request<AutomationEdge>(
          path(businessId, `/workflows/${workflowId}/edges`),
          { method: "POST", json: data },
        ),
      update: (
        businessId: string,
        workflowId: string,
        edgeKey: string,
        data: Partial<
          Pick<
            AutomationEdge,
            "target_node_key" | "branch_label" | "order_index"
          >
        >,
      ) =>
        client.request<AutomationEdge>(
          path(businessId, `/workflows/${workflowId}/edges/${edgeKey}`),
          { method: "PATCH", json: data },
        ),
      remove: (businessId: string, workflowId: string, edgeKey: string) =>
        client.request<void>(
          path(businessId, `/workflows/${workflowId}/edges/${edgeKey}`),
          { method: "DELETE" },
        ),
    },
    runs: {
      list: (businessId: string, workflowId: string, signal?: AbortSignal) =>
        client.request<PageResponse<WorkflowRun>>(
          path(
            businessId,
            `/runs?workflow_id=${encodeURIComponent(workflowId)}&page=1&page_size=25`,
          ),
          { signal },
        ),
      nodes: (businessId: string, runId: string, signal?: AbortSignal) =>
        client.request<PageResponse<NodeRun>>(
          path(businessId, `/runs/${runId}/nodes?page=1&page_size=100`),
          { signal },
        ),
      queue: (
        businessId: string,
        workflowId: string,
        payload: Record<string, unknown>,
      ) =>
        client.request<WorkflowRun>(
          path(businessId, `/workflows/${workflowId}/runs`),
          { method: "POST", json: { payload } },
        ),
      cancel: (businessId: string, runId: string) =>
        client.request<WorkflowRun>(path(businessId, `/runs/${runId}/cancel`), {
          method: "POST",
        }),
    },
    approvals: {
      list: (
        businessId: string,
        status: Approval["status"],
        signal?: AbortSignal,
      ) =>
        client.request<{ items: Approval[] }>(
          approvalPath(businessId, `?status=${status}&limit=200`),
          { signal },
        ),
      get: (businessId: string, approvalId: string, signal?: AbortSignal) =>
        client.request<Approval>(approvalPath(businessId, `/${approvalId}`), {
          signal,
        }),
      approve: (
        businessId: string,
        approvalId: string,
        decision_note: string | null,
      ) =>
        client.request<Approval>(
          approvalPath(businessId, `/${approvalId}/approve`),
          { method: "POST", json: { decision_note } },
        ),
      reject: (
        businessId: string,
        approvalId: string,
        decision_note: string | null,
      ) =>
        client.request<Approval>(
          approvalPath(businessId, `/${approvalId}/reject`),
          { method: "POST", json: { decision_note } },
        ),
    },
  };
}

export const automationsApi = createAutomationsApi(apiClient);
