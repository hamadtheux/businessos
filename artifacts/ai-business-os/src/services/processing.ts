import { apiClient, type ApiClient } from "./api-client.ts";

export type JobStatus =
  | "queued"
  | "processing"
  | "succeeded"
  | "failed"
  | "dead_letter"
  | "canceled";

export type BackgroundJob = {
  id: string;
  job_type: string;
  status: JobStatus;
  attempt_count: number;
  max_attempts: number;
  available_at: string;
  completed_at: string | null;
  failure_code: string | null;
  created_at: string;
};

export type ProcessingHealth = {
  status: "healthy" | "degraded" | "unavailable";
  counts: Record<JobStatus, number>;
  automation_event_backlog: number;
  attention: {
    uncertain_actions: number;
    failed_actions_24h: number;
    failed_workflows_24h: number;
    failed_webhooks_24h: number;
    webhook_backlog: number;
    provider_connections_attention: number;
    commerce_connections_attention: number;
    ai_failures_24h: number;
  };
  oldest_queued_job_age_seconds: number | null;
  average_processing_latency_seconds: number | null;
  worker_last_heartbeat_at: string | null;
  scheduler_last_heartbeat_at: string | null;
};

const path = (businessId: string, suffix: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/processing${suffix}`;

export function createProcessingApi(client: ApiClient) {
  return {
    health: (businessId: string, signal?: AbortSignal) =>
      client.request<ProcessingHealth>(path(businessId, "/health"), { signal }),
    jobs: (businessId: string, signal?: AbortSignal) =>
      client.request<{
        items: BackgroundJob[];
        page: number;
        page_size: number;
        total: number;
      }>(path(businessId, "/jobs?page=1&page_size=10"), { signal }),
    retry: (businessId: string, jobId: string) =>
      client.request<BackgroundJob>(path(businessId, `/jobs/${jobId}/retry`), {
        method: "POST",
      }),
    cancel: (businessId: string, jobId: string) =>
      client.request<BackgroundJob>(path(businessId, `/jobs/${jobId}/cancel`), {
        method: "POST",
      }),
  };
}

export const processingApi = createProcessingApi(apiClient);
