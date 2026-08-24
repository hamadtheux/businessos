import { apiClient, type ApiClient } from "./api-client.ts";
import type {
  AdvertisingSpendPolicy,
  CampaignChannelPlan,
  CampaignStatus,
  CompetitorAnalysis,
  CompetitorCandidate,
  CompetitorCandidateEvidence,
  CompetitorDiscoveryRun,
  CompetitorDiscoveryStatus,
  CompetitorObservation,
  CreativeAsset,
  MarketingAnalytics,
  MarketingActionProposal,
  MarketingAudience,
  MarketingAutomationRun,
  MarketingCampaign,
  MarketingChannel,
  MarketingCompetitor,
  MarketingContent,
  MarketingContentStatus,
  MarketingContentType,
  MarketingPerformance,
  MarketingPlan,
  MarketingPlanStatus,
  MarketingTrend,
  MarketingTrendStatus,
  Opportunity,
  PageResponse,
  SocialSchedule,
} from "./api-types.ts";

type ListOptions = { page?: number; pageSize?: number; search?: string; status?: string };
type CampaignInput = {
  marketing_plan_id?: string | null; audience_id?: string | null; name: string; objective: string;
  description?: string | null; offer?: string | null; audience_definition: string;
  geographic_targeting?: string[]; channels: MarketingChannel[]; start_date?: string | null;
  end_date?: string | null; planned_budget: string; budget_mode?: "daily" | "lifetime";
};
type ContentInput = {
  campaign_id?: string | null; channel: MarketingChannel; content_type: MarketingContentType;
  title: string; body: string; cta?: string | null; language?: string;
};

const marketingPath = (businessId: string, path: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}/marketing${path}`;

function listQuery(options: ListOptions = {}, extra: Record<string, string | undefined> = {}) {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 25),
  });
  if (options.search) params.set("search", options.search);
  if (options.status) params.set("status", options.status);
  Object.entries(extra).forEach(([key, value]) => value && params.set(key, value));
  return params.toString();
}

export function createMarketingApi(client: ApiClient) {
  return {
    spendPolicy: {
      get: (id: string, signal?: AbortSignal) =>
        client.request<AdvertisingSpendPolicy | null>(
          marketingPath(id, "/spend-policy"),
          { signal },
        ),
      update: (
        id: string,
        data: {
          currency: string;
          max_single_campaign_budget: string;
          max_single_budget_change: string;
          daily_advertising_limit: string | null;
          monthly_ai_managed_limit: string | null;
          active: boolean;
          confirm_material_increase: boolean;
        },
      ) => client.request<AdvertisingSpendPolicy>(
        marketingPath(id, "/spend-policy"),
        { method: "PUT", json: data },
      ),
    },
    audiences: {
      list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<MarketingAudience>>(marketingPath(id, `/audiences?${listQuery(options)}`), { signal }),
      create: (id: string, data: Omit<MarketingAudience, "id" | "business_id" | "created_by_user_id" | "created_at" | "updated_at">) => client.request<MarketingAudience>(marketingPath(id, "/audiences"), { method: "POST", json: data }),
    },
    plans: {
      list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<MarketingPlan>>(marketingPath(id, `/plans?${listQuery(options)}`), { signal }),
      get: (id: string, planId: string, signal?: AbortSignal) => client.request<MarketingPlan>(marketingPath(id, `/plans/${planId}`), { signal }),
      create: (id: string, data: Omit<MarketingPlan, "id" | "business_id" | "currency" | "status" | "generated_by" | "created_by_user_id" | "created_at" | "updated_at">) => client.request<MarketingPlan>(marketingPath(id, "/plans"), { method: "POST", json: data }),
      update: (id: string, planId: string, data: Partial<Pick<MarketingPlan, "title" | "objective" | "target_audience" | "positioning" | "key_message" | "offer" | "channels" | "budget_guidance" | "period_start" | "period_end" | "content_strategy" | "measurement_goals">>) => client.request<MarketingPlan>(marketingPath(id, `/plans/${planId}`), { method: "PATCH", json: data }),
      status: (id: string, planId: string, status: MarketingPlanStatus) => client.request<MarketingPlan>(marketingPath(id, `/plans/${planId}/status`), { method: "POST", json: { status } }),
      generate: (id: string, data: { goal: string; title?: string | null; target_audience: string; channels: MarketingChannel[]; budget_guidance?: string | null; period_start?: string | null; period_end?: string | null }) => client.request<MarketingPlan>(marketingPath(id, "/plans/generate"), { method: "POST", json: data }),
    },
    campaigns: {
      list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<MarketingCampaign>>(marketingPath(id, `/campaigns?${listQuery(options)}`), { signal }),
      get: (id: string, campaignId: string, signal?: AbortSignal) => client.request<MarketingCampaign>(marketingPath(id, `/campaigns/${campaignId}`), { signal }),
      create: (id: string, data: CampaignInput) => client.request<MarketingCampaign>(marketingPath(id, "/campaigns"), { method: "POST", json: data }),
      generate: (id: string, data: { goal: string; name?: string | null; audience_definition?: string | null; channels?: MarketingChannel[]; planned_budget: string; budget_mode?: "daily" | "lifetime"; start_date?: string | null; end_date?: string | null }) => client.request<MarketingCampaign>(marketingPath(id, "/campaigns/generate"), { method: "POST", json: data }),
      update: (id: string, campaignId: string, data: Partial<CampaignInput>) => client.request<MarketingCampaign>(marketingPath(id, `/campaigns/${campaignId}`), { method: "PATCH", json: data }),
      duplicate: (id: string, campaignId: string) => client.request<MarketingCampaign>(marketingPath(id, `/campaigns/${campaignId}/duplicate`), { method: "POST" }),
      status: (id: string, campaignId: string, status: CampaignStatus) => client.request<MarketingCampaign>(marketingPath(id, `/campaigns/${campaignId}/status`), { method: "POST", json: { status } }),
      addChannel: (id: string, campaignId: string, data: Omit<CampaignChannelPlan, "id" | "business_id" | "campaign_id" | "status" | "created_at" | "updated_at">) => client.request<CampaignChannelPlan>(marketingPath(id, `/campaigns/${campaignId}/channels`), { method: "POST", json: data }),
      prepareAction: (id: string, campaignId: string, channel?: "meta" | "google_ads") => client.request<MarketingActionProposal>(marketingPath(id, `/campaigns/${campaignId}/prepare-action`), { method: "POST", json: { channel: channel ?? null } }),
    },
    content: {
      list: (id: string, options?: ListOptions, filters: { campaignId?: string; channel?: MarketingChannel } = {}, signal?: AbortSignal) => client.request<PageResponse<MarketingContent>>(marketingPath(id, `/content?${listQuery(options, { campaign_id: filters.campaignId, channel: filters.channel })}`), { signal }),
      get: (id: string, contentId: string, signal?: AbortSignal) => client.request<MarketingContent>(marketingPath(id, `/content/${contentId}`), { signal }),
      create: (id: string, data: ContentInput) => client.request<MarketingContent>(marketingPath(id, "/content"), { method: "POST", json: data }),
      generate: (id: string, data: { prompt: string; campaign_id?: string | null; channel: MarketingChannel; content_type: MarketingContentType; title?: string | null; language?: string; parent_content_id?: string | null }) => client.request<MarketingContent>(marketingPath(id, "/content/generate"), { method: "POST", json: data }),
      versions: (id: string, contentId: string, signal?: AbortSignal) => client.request<MarketingContent[]>(marketingPath(id, `/content/${contentId}/versions`), { signal }),
      edit: (id: string, contentId: string, data: { title: string; body: string; cta?: string | null }) => client.request<MarketingContent>(marketingPath(id, `/content/${contentId}/versions`), { method: "POST", json: data }),
      status: (id: string, contentId: string, status: MarketingContentStatus) => client.request<MarketingContent>(marketingPath(id, `/content/${contentId}/status`), { method: "POST", json: { status } }),
      preparePublish: (id: string, contentId: string, channel?: "facebook" | "instagram") => client.request<MarketingActionProposal>(marketingPath(id, `/content/${contentId}/prepare-publish`), { method: "POST", json: { channel: channel ?? null } }),
    },
    creative: {
      list: (id: string, campaignId?: string, contentId?: string, signal?: AbortSignal) => {
        const params = new URLSearchParams();
        if (campaignId) params.set("campaign_id", campaignId);
        if (contentId) params.set("content_id", contentId);
        return client.request<CreativeAsset[]>(marketingPath(id, `/creative-assets?${params}`), { signal });
      },
      brief: (id: string, data: { campaign_id?: string | null; content_id?: string | null; asset_type: string; instructions: string; aspect_ratio?: string | null; width?: number | null; height?: number | null; alt_text?: string | null }) => client.request<CreativeAsset>(marketingPath(id, "/creative-assets/brief"), { method: "POST", json: data }),
    },
    calendar: {
      list: (id: string, start: string, end: string, filters: { channel?: MarketingChannel; campaignId?: string } = {}, signal?: AbortSignal) => {
        const params = new URLSearchParams({ start_at: start, end_at: end });
        if (filters.channel) params.set("channel", filters.channel);
        if (filters.campaignId) params.set("campaign_id", filters.campaignId);
        return client.request<SocialSchedule[]>(marketingPath(id, `/calendar?${params}`), { signal });
      },
      create: (id: string, contentId: string, scheduledFor: string) => client.request<SocialSchedule>(marketingPath(id, "/calendar"), { method: "POST", json: { content_id: contentId, scheduled_for: scheduledFor } }),
      reschedule: (id: string, scheduleId: string, scheduledFor: string) => client.request<SocialSchedule>(marketingPath(id, `/calendar/${scheduleId}`), { method: "PATCH", json: { scheduled_for: scheduledFor } }),
      unschedule: (id: string, scheduleId: string) => client.request<SocialSchedule>(marketingPath(id, `/calendar/${scheduleId}/unschedule`), { method: "POST" }),
    },
    competitors: {
      list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<MarketingCompetitor>>(marketingPath(id, `/competitors?${listQuery(options)}`), { signal }),
      get: (id: string, competitorId: string, signal?: AbortSignal) => client.request<MarketingCompetitor>(marketingPath(id, `/competitors/${competitorId}`), { signal }),
      create: (id: string, data: { name: string; website_domain?: string | null; description?: string | null; notes?: string | null }) => client.request<MarketingCompetitor>(marketingPath(id, "/competitors"), { method: "POST", json: data }),
      update: (id: string, competitorId: string, data: Partial<Pick<MarketingCompetitor, "name" | "website_domain" | "description" | "active" | "notes">>) => client.request<MarketingCompetitor>(marketingPath(id, `/competitors/${competitorId}`), { method: "PATCH", json: data }),
      observations: (id: string, competitorId: string, options?: ListOptions, filters: { category?: CompetitorObservation["category"] } = {}, signal?: AbortSignal) => client.request<PageResponse<CompetitorObservation>>(marketingPath(id, `/competitors/${competitorId}/observations?${listQuery(options, { category: filters.category })}`), { signal }),
      addObservation: (id: string, competitorId: string, data: { observed_at: string; category: CompetitorObservation["category"]; title: string; summary: string; source_type?: "manual" | "import"; source_reference?: string | null; safe_metrics?: Record<string, string | number | null> }) => client.request<CompetitorObservation>(marketingPath(id, `/competitors/${competitorId}/observations`), { method: "POST", json: data }),
      analyses: (id: string, competitorId: string, signal?: AbortSignal) => client.request<CompetitorAnalysis[]>(marketingPath(id, `/competitors/${competitorId}/analyses`), { signal }),
      analyze: (id: string, competitorId: string) => client.request<CompetitorAnalysis>(marketingPath(id, `/competitors/${competitorId}/analyze`), { method: "POST" }),
      opportunity: (id: string, competitorId: string, analysisId: string, data: { title?: string | null; description?: string | null; priority?: "low" | "medium" | "high" | "urgent" } = {}) => client.request<Opportunity>(marketingPath(id, `/competitors/${competitorId}/analyses/${analysisId}/opportunity`), { method: "POST", json: data }),
    },
    competitorDiscovery: {
      status: (id: string, signal?: AbortSignal) => client.request<CompetitorDiscoveryStatus>(marketingPath(id, "/competitor-discovery"), { signal }),
      refresh: (id: string) => client.request<CompetitorDiscoveryRun>(marketingPath(id, "/competitor-discovery/refresh"), { method: "POST" }),
      candidates: (id: string, status?: CompetitorCandidate["status"], signal?: AbortSignal) => {
        const query = status ? `?status=${encodeURIComponent(status)}` : "";
        return client.request<CompetitorCandidate[]>(marketingPath(id, `/competitor-candidates${query}`), { signal });
      },
      evidence: (id: string, candidateId: string, signal?: AbortSignal) => client.request<CompetitorCandidateEvidence[]>(marketingPath(id, `/competitor-candidates/${candidateId}/evidence`), { signal }),
      setStatus: (id: string, candidateId: string, status: "confirmed" | "dismissed" | "monitoring") => client.request<CompetitorCandidate>(marketingPath(id, `/competitor-candidates/${candidateId}/status`), { method: "POST", json: { status } }),
    },
    automation: {
      get: (id: string, runType: "content_plan" | "campaign_opportunities" | "business_growth", signal?: AbortSignal) => client.request<MarketingAutomationRun | null>(marketingPath(id, `/automation/${runType}`), { signal }),
      refresh: (id: string, runType: "content_plan" | "campaign_opportunities" | "business_growth") => client.request<MarketingAutomationRun>(marketingPath(id, `/automation/${runType}/refresh`), { method: "POST" }),
    },
    trends: {
      list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<MarketingTrend>>(marketingPath(id, `/trends?${listQuery(options)}`), { signal }),
      create: (id: string, data: { title: string; category: string; description: string; source?: "manual" | "import"; source_reference?: string | null; observed_at: string; relevance_score: string; confidence?: string | null }) => client.request<MarketingTrend>(marketingPath(id, "/trends"), { method: "POST", json: data }),
      status: (id: string, trendId: string, status: MarketingTrendStatus) => client.request<MarketingTrend>(marketingPath(id, `/trends/${trendId}/status`), { method: "POST", json: { status } }),
      opportunity: (id: string, trendId: string, data: { title?: string | null; description?: string | null; priority?: "low" | "medium" | "high" | "urgent" } = {}) => client.request<Opportunity>(marketingPath(id, `/trends/${trendId}/opportunity`), { method: "POST", json: data }),
    },
    performance: {
      list: (id: string, options?: ListOptions, filters: { campaignId?: string; channel?: MarketingChannel; start?: string; end?: string } = {}, signal?: AbortSignal) => client.request<PageResponse<MarketingPerformance>>(marketingPath(id, `/performance?${listQuery(options, { campaign_id: filters.campaignId, channel: filters.channel, period_start: filters.start, period_end: filters.end })}`), { signal }),
      create: (id: string, data: { campaign_id: string; content_id?: string | null; channel: MarketingChannel; period_start: string; period_end: string; data_source?: "manual" | "import"; spend?: string; impressions?: number; reach?: number; clicks?: number; leads?: number; conversions?: number; revenue?: string }) => client.request<MarketingPerformance>(marketingPath(id, "/performance"), { method: "POST", json: data }),
      learn: (id: string, start: string, end: string) => client.request<{ created: boolean; conclusion: string | null; memory_id: string | null }>(marketingPath(id, `/performance/learn?period_start=${start}&period_end=${end}`), { method: "POST" }),
    },
    analytics: (id: string, start: string, end: string, signal?: AbortSignal) => client.request<MarketingAnalytics>(marketingPath(id, `/analytics?period_start=${start}&period_end=${end}`), { signal }),
  };
}

export const marketingApi = createMarketingApi(apiClient);
