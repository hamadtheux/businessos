import { apiClient, type ApiClient } from "./api-client.ts";
import type {
  GrowthAttribution,
  GrowthExperiment,
  GrowthExperimentResult,
  GrowthExperimentStatus,
  GrowthLearning,
  GrowthMetric,
  PageResponse,
} from "./api-types.ts";


export type GrowthExperimentInput = {
  name: string;
  hypothesis: string;
  learning_key: string;
  experiment_type: "campaign" | "content";
  primary_metric: GrowthMetric;
  attribution_classification: GrowthAttribution;
  evaluation_window_days: number;
  minimum_sample_size: number;
  source_opportunity_id?: string | null;
  source_ai_action_id?: string | null;
  variants: Array<{
    variant_key: string;
    label: string;
    is_control: boolean;
    campaign_id: string;
    content_id?: string | null;
  }>;
};

const growthPath = (businessId: string, path: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/growth${path}`;

export function createGrowthLearningApi(client: ApiClient) {
  return {
    experiments: {
      list: (
        businessId: string,
        options: { page?: number; pageSize?: number; status?: GrowthExperimentStatus } = {},
        signal?: AbortSignal,
      ) => {
        const query = new URLSearchParams({
          page: String(options.page ?? 1),
          page_size: String(options.pageSize ?? 25),
        });
        if (options.status) query.set("status", options.status);
        return client.request<PageResponse<GrowthExperiment>>(
          growthPath(businessId, `/experiments?${query.toString()}`),
          { signal },
        );
      },
      get: (businessId: string, experimentId: string, signal?: AbortSignal) =>
        client.request<GrowthExperiment>(
          growthPath(businessId, `/experiments/${experimentId}`),
          { signal },
        ),
      create: (businessId: string, data: GrowthExperimentInput) =>
        client.request<GrowthExperiment>(growthPath(businessId, "/experiments"), {
          method: "POST",
          json: data,
        }),
      update: (
        businessId: string,
        experimentId: string,
        data: Partial<
          Pick<
            GrowthExperimentInput,
            "name" | "hypothesis" | "evaluation_window_days" | "minimum_sample_size"
          >
        >,
      ) =>
        client.request<GrowthExperiment>(
          growthPath(businessId, `/experiments/${experimentId}`),
          { method: "PATCH", json: data },
        ),
      transition: (
        businessId: string,
        experimentId: string,
        transition: "ready" | "start" | "complete" | "cancel",
      ) =>
        client.request<GrowthExperiment>(
          growthPath(businessId, `/experiments/${experimentId}/${transition}`),
          { method: "POST" },
        ),
      evaluate: (businessId: string, experimentId: string) =>
        client.request<GrowthExperimentResult>(
          growthPath(businessId, `/experiments/${experimentId}/evaluate`),
          { method: "POST" },
        ),
    },
    learnings: {
      list: (businessId: string, signal?: AbortSignal) =>
        client.request<PageResponse<GrowthLearning>>(
          growthPath(businessId, "/learnings?page=1&page_size=25"),
          { signal },
        ),
    },
  };
}

export const growthLearningApi = createGrowthLearningApi(apiClient);
