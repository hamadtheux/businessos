import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BarChart3, Calendar, Check, Globe2, Plus, RefreshCw, Send, Sparkles, Target, TrendingUp, Wand2, X } from "lucide-react";
import { useLocation } from "wouter";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle, WorkspaceDrawer } from "@/components/product-ui";
import { CmoContentGeneratorDrawer } from "@/features/marketing/cmo-content-generator-drawer";
import { CmoCalendarCompanion } from "@/features/marketing/cmo-content-ui";
import { CmoContentStudioCard } from "@/features/marketing/cmo-content-studio";
import { CmoDepartmentNav } from "@/features/marketing/marketing-pages";
import {
  channelGenerationNotice,
  createCreativeWithRecovery,
  creativeFormatForContent,
  CREATIVE_GENERATION_POLL_MS,
  creativePhaseForDisplay,
  creativeResultNotice,
  generateCampaignChannelDrafts,
  isCreativeGenerationActive,
  publishingCapability,
  runCreativeOperationWithRecovery,
  videoFormatForContent,
  type CampaignGenerationInput,
  type CreativeProgress,
} from "@/lib/cmo-ux";
import { businessDateRange } from "@/lib/operational-dates";
import { humanizeApiError } from "@/services/api-client";
import type { CreativeAsset, MarketingChannel, MarketingContent } from "@/services/api-types";
import { integrationsApi } from "@/services/integrations";
import { marketingApi } from "@/services/marketing";

function Kpi({ title, value, foot, icon, tone }: { title: string; value: string; foot: string; icon: ReactNode; tone: string }) {
  return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span>{foot}</span></div></Card>;
}

const channels: MarketingChannel[] = ["instagram", "facebook", "linkedin", "tiktok", "email", "whatsapp", "website", "meta", "google_ads"];
const channelLabels: Record<MarketingChannel, string> = {
  meta: "Meta", google_ads: "Google Ads", instagram: "Instagram",
  facebook: "Facebook", linkedin: "LinkedIn", tiktok: "TikTok",
  email: "Email", whatsapp: "WhatsApp", website: "Website", other: "Other",
};

function channelLabel(channel: MarketingChannel) {
  return channelLabels[channel];
}

type ScheduleContentValues = {
  contentId: string;
  scheduledFor: string;
};

type GeneratePlanValues = {
  goal: string;
  title: string | null;
  target_audience: string;
  channels: MarketingChannel[];
  budget_guidance: string | null;
  period_start: string | null;
  period_end: string | null;
};

type UpdatePlanValues = {
  planId: string;
  title: string;
  objective: string;
  target_audience: string;
  positioning: string;
  key_message: string;
  offer: string | null;
  content_strategy: string | null;
  measurement_goals: string[];
};

export function CmoPage() {
  const [, navigate] = useLocation();
  const { activeBusinessId, activeBusiness } = useBusiness();
  const canAuthorizeOffer = ["owner", "admin"].includes(
    activeBusiness?.membershipRole ?? "",
  );
  const queryClient = useQueryClient();
  const activeTab = new URLSearchParams(window.location.search).get("tab") ?? "Overview";
  const [showContentGenerator, setShowContentGenerator] = useState(false);
  const [showPlanGenerator, setShowPlanGenerator] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const [editingContent, setEditingContent] = useState<MarketingContent | null>(null);
  const [historyContent, setHistoryContent] = useState<MarketingContent | null>(null);
  const [schedule, setSchedule] = useState<MarketingContent | null>(null);
  const [creativeProgress, setCreativeProgress] = useState<CreativeProgress>(null);
  const [activeCreativeGeneration, setActiveCreativeGeneration] = useState<{
    businessId: string;
    assetId: string;
    contentId?: string;
  } | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const period = useMemo(() => businessDateRange(activeBusiness?.timezone || "UTC", 30), [activeBusiness?.timezone]);
  const calendarEnd = useMemo(() => new Date(Date.now() + 30 * 86400000).toISOString(), []);

  const plans = useQuery({ queryKey: ["marketing", activeBusinessId, "plans", "cmo"], queryFn: ({ signal }) => marketingApi.plans.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const content = useQuery({ queryKey: ["marketing", activeBusinessId, "content", "cmo"], queryFn: ({ signal }) => marketingApi.content.list(activeBusinessId, { pageSize: 10 }, {}, signal), enabled: Boolean(activeBusinessId) });
  const analytics = useQuery({ queryKey: ["marketing", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => marketingApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const calendar = useQuery({ queryKey: ["marketing", activeBusinessId, "calendar", "cmo"], queryFn: ({ signal }) => marketingApi.calendar.list(activeBusinessId, new Date().toISOString(), calendarEnd, {}, signal), enabled: Boolean(activeBusinessId) });
  const campaigns = useQuery({ queryKey: ["marketing", activeBusinessId, "campaigns", "cmo"], queryFn: ({ signal }) => marketingApi.campaigns.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const integrationRegistry = useQuery({ queryKey: ["integrations", activeBusinessId, "registry", "social"], queryFn: ({ signal }) => integrationsApi.registry(activeBusinessId, signal), enabled: Boolean(activeBusinessId) });
  const integrationConnections = useQuery({ queryKey: ["integrations", activeBusinessId, "connections", "social"], queryFn: ({ signal }) => integrationsApi.connections(activeBusinessId, signal), enabled: Boolean(activeBusinessId) });
  const primary = content.data?.items[0];
  const creativeAssets = useQuery({
    queryKey: ["marketing", activeBusinessId, "creative-assets", "cmo", primary?.id],
    queryFn: ({ signal }) => marketingApi.creative.list(activeBusinessId, primary?.campaign_id || undefined, primary!.id, signal),
    enabled: Boolean(activeBusinessId && primary),
  });
  const activeCreativeAsset = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "creative-asset",
      activeCreativeGeneration?.assetId,
    ],
    queryFn: ({ signal }) => marketingApi.creative.get(
      activeCreativeGeneration!.businessId,
      activeCreativeGeneration!.assetId,
      signal,
    ),
    enabled: Boolean(
      activeCreativeGeneration &&
      activeCreativeGeneration.businessId === activeBusinessId,
    ),
    refetchInterval: (query) =>
      !query.state.data || isCreativeGenerationActive(query.state.data)
        ? CREATIVE_GENERATION_POLL_MS
        : false,
  });

  const versions = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "content-versions",
      historyContent?.root_content_id ?? historyContent?.id ?? "none",
    ],
    queryFn: ({ signal }) =>
      marketingApi.content.versions(
        activeBusinessId,
        historyContent!.id,
        signal,
      ),
    enabled: Boolean(activeBusinessId && historyContent),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId] });
  const refreshWorkspace = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId] }),
    queryClient.invalidateQueries({ queryKey: ["integrations", activeBusinessId] }),
  ]);
  const refreshCreatives = () => queryClient.invalidateQueries({
    queryKey: ["marketing", activeBusinessId, "creative-assets"],
  });
  const refreshCreativeRecord = async (asset: CreativeAsset) => {
    const refreshed = await queryClient.fetchQuery({
      queryKey: ["marketing", activeBusinessId, "creative-asset", asset.id],
      queryFn: ({ signal }) => marketingApi.creative.get(
        activeBusinessId,
        asset.id,
        signal,
      ),
      staleTime: 0,
    });
    queryClient.setQueriesData<CreativeAsset[]>(
      { queryKey: ["marketing", activeBusinessId, "creative-assets"] },
      (current) => current?.map((item) => item.id === refreshed.id ? refreshed : item),
    );
    return refreshed;
  };
  const observeCreativeGeneration = (asset: CreativeAsset) => {
    if (!isCreativeGenerationActive(asset)) return;
    setActiveCreativeGeneration({
      businessId: activeBusinessId,
      assetId: asset.id,
      contentId: asset.content_id || undefined,
    });
    setCreativeProgress({
      phase: asset.media_type === "video" ? "video_generation" : "visual",
      contentId: asset.content_id || undefined,
      assetId: asset.id,
    });
  };
  useEffect(() => {
    setActiveCreativeGeneration((current) =>
      current?.businessId === activeBusinessId ? current : null,
    );
  }, [activeBusinessId]);
  useEffect(() => {
    if (activeCreativeGeneration) return;
    const active = creativeAssets.data?.find(isCreativeGenerationActive);
    if (active) observeCreativeGeneration(active);
  }, [activeCreativeGeneration, creativeAssets.data]);
  useEffect(() => {
    const asset = activeCreativeAsset.data;
    if (!activeCreativeGeneration || !asset || isCreativeGenerationActive(asset)) {
      return;
    }
    setActiveCreativeGeneration(null);
    setCreativeProgress(null);
    setNotice(creativeResultNotice(asset));
    setError(
      asset.generation_status === "failed"
        ? "The creative could not be completed. Its grounded strategy remains ready to retry."
        : "",
    );
    void refreshCreatives();
  }, [activeCreativeAsset.data, activeCreativeGeneration]);

  const generateContent = useMutation({
    mutationFn: (input: CampaignGenerationInput) =>
      generateCampaignChannelDrafts(
        input,
        (request) => marketingApi.content.generate(activeBusinessId, request),
      ),
    onSuccess: (outcome) => {
      if (outcome.successes.length === 0) {
        const firstFailure = outcome.failures[0]?.reason;
        setNotice("");
        setError(
          humanizeApiError(
            firstFailure,
            "AI content generation could not be completed. No channel drafts were created.",
          ),
        );
        return;
      }
      setShowContentGenerator(false);
      setNotice(channelGenerationNotice(outcome) || "");
      setError("");
    },
    onError: (reason) => setError(humanizeApiError(reason, "AI content generation could not be completed.")),
    onSettled: () => refresh(),
  });
  const editContent = useMutation({
    mutationFn: (values: {
      contentId: string;
      title: string;
      body: string;
      cta: string | null;
    }) => marketingApi.content.edit(activeBusinessId, values.contentId, {
      title: values.title,
      body: values.body,
      cta: values.cta,
    }),
    onSuccess: async (item) => {
      setEditingContent(null);
      setNotice(
        `Version ${item.version} was saved as a manual revision. Previous versions remain unchanged.`,
      );
      setError("");
      await refresh();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "The content revision could not be saved.",
        ),
      ),
  });
  const submitContentEdit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingContent) {
      setError("Choose content to edit.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const title = String(form.get("title") || "").trim();
    const body = String(form.get("body") || "").trim();
    const cta = String(form.get("cta") || "").trim();
    if (!title || !body) {
      setError("Title and content are required.");
      return;
    }
    setError("");
    editContent.mutate({
      contentId: editingContent.id,
      title,
      body,
      cta: cta || null,
    });
  };

  const regenerate = useMutation({
    mutationFn: (item: MarketingContent) => marketingApi.content.generate(activeBusinessId, { prompt: `Regenerate this approved marketing direction as a distinct, fact-grounded variant: ${item.body}`, channel: item.channel, content_type: item.content_type, campaign_id: item.campaign_id, title: item.title, language: item.language, parent_content_id: item.id }),
    onSuccess: (item) => { setNotice(`Version ${item.version} was created; prior versions remain in history.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "The content version could not be regenerated.")),
  });
  const approve = useMutation({
    mutationFn: async (item: MarketingContent) => {
      if (item.status === "draft") {
        const review = await marketingApi.content.status(activeBusinessId, item.id, "review");
        return marketingApi.content.status(activeBusinessId, review.id, "approved");
      }
      return marketingApi.content.status(activeBusinessId, item.id, "approved");
    },
    onSuccess: (item) => { setNotice(`“${item.title}” is approved internally.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Content could not be approved.")),
  });
  const createSchedule = useMutation({
    mutationFn: (values: ScheduleContentValues) =>
      marketingApi.calendar.create(
        activeBusinessId,
        values.contentId,
        values.scheduledFor,
      ),
    onSuccess: () => { setSchedule(null); setNotice("Content was added to the internal calendar. No platform was contacted."); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Content could not be scheduled. Approve it first.")),
  });
  const submitSchedule = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!schedule) {
      setError("Choose content to schedule.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const scheduledValue = String(form.get("scheduled_for") || "").trim();
    const scheduledDate = new Date(scheduledValue);
    if (!scheduledValue || Number.isNaN(scheduledDate.getTime())) {
      setError("Choose a valid date and time.");
      return;
    }
    setError("");
    createSchedule.mutate({
      contentId: schedule.id,
      scheduledFor: scheduledDate.toISOString(),
    });
  };
  const preparePublish = useMutation({
    mutationFn: (item: MarketingContent) =>
      marketingApi.content.preparePublish(
        activeBusinessId,
        item.id,
        item.channel,
      ),
    onSuccess: (proposal) => {
      setNotice(
        `${proposal.action_status.replaceAll("_", " ")}: ${proposal.connector_message}`,
      );
      setError("");
      void refresh();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "A governed publish action could not be prepared.",
        ),
      ),
  });
  const generatePlan = useMutation({
    mutationFn: (values: GeneratePlanValues) =>
      marketingApi.plans.generate(activeBusinessId, values),
    onSuccess: (plan) => { setShowPlanGenerator(false); setNotice(`AI CMO plan “${plan.title}” is ready for review.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "AI CMO strategy generation could not be completed.")),
  });
  const submitGeneratePlan = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selected = form.getAll("channels").map(String) as MarketingChannel[];
    if (!selected.length) {
      setError("Choose at least one channel.");
      return;
    }
    setError("");
    generatePlan.mutate({
      goal: String(form.get("goal")),
      title: String(form.get("title")) || null,
      target_audience: String(form.get("audience")),
      channels: selected,
      budget_guidance: String(form.get("budget")) || null,
      period_start: String(form.get("period_start")) || null,
      period_end: String(form.get("period_end")) || null,
    });
  };
  const updatePlan = useMutation({
    mutationFn: (values: UpdatePlanValues) =>
      marketingApi.plans.update(activeBusinessId, values.planId, {
        title: values.title,
        objective: values.objective,
        target_audience: values.target_audience,
        positioning: values.positioning,
        key_message: values.key_message,
        offer: values.offer,
        content_strategy: values.content_strategy,
        measurement_goals: values.measurement_goals,
      }),
    onSuccess: () => { setEditingPlan(false); setNotice("Marketing plan changes were saved."); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Marketing plan could not be updated.")),
  });
  const submitUpdatePlan = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const current = plans.data?.items[0];
    if (!current) {
      setError("Choose a marketing plan to update.");
      return;
    }
    const form = new FormData(event.currentTarget);
    setError("");
    updatePlan.mutate({
      planId: current.id,
      title: String(form.get("title")),
      objective: String(form.get("objective")),
      target_audience: String(form.get("target_audience")),
      positioning: String(form.get("positioning")),
      key_message: String(form.get("key_message")),
      offer: String(form.get("offer")) || null,
      content_strategy: String(form.get("content_strategy")) || null,
      measurement_goals: String(form.get("measurement_goals") || "")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean),
    });
  };
  const movePlan = useMutation({
    mutationFn: (status: "active" | "completed" | "archived") => marketingApi.plans.status(activeBusinessId, plans.data!.items[0].id, status),
    onSuccess: (plan) => { setNotice(`Marketing plan is now ${plan.status}.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Marketing plan status could not be changed.")),
  });

  const createCreative = useMutation({
    mutationFn: (item: MarketingContent) => createCreativeWithRecovery({
      contentId: item.id,
      createBrief: () => marketingApi.creative.brief(activeBusinessId, {
        campaign_id: item.campaign_id,
        content_id: item.id,
        ...creativeFormatForContent(item),
        instructions: item.creative_brief || `Create a professional campaign visual for ${item.title}.`,
        alt_text: `Branded campaign creative for ${item.title}`,
      }),
      generate: (brief) => marketingApi.creative.generate(activeBusinessId, brief.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => { observeCreativeGeneration(asset); setNotice(creativeResultNotice(asset)); setError(""); },
    onError: () => setError("The visual creative could not be completed. Refresh to see saved progress and try again."),
  });
  const createVideo = useMutation({
    mutationFn: async (item: MarketingContent) => {
      setCreativeProgress({ phase: "video_strategy", contentId: item.id });
      try {
        const strategy = await marketingApi.creative.videoStrategy(activeBusinessId, {
          campaign_id: item.campaign_id,
          content_id: item.id,
          ...videoFormatForContent(item),
          instructions: item.creative_brief || `Create a professional campaign video for ${item.title}.`,
        });
        setCreativeProgress({ phase: "video_generation", contentId: item.id, assetId: strategy.id });
        return await marketingApi.creative.generateVideo(activeBusinessId, strategy.id);
      } finally {
        await refreshCreatives();
        setCreativeProgress(null);
      }
    },
    onSuccess: (asset) => { observeCreativeGeneration(asset); setNotice(creativeResultNotice(asset)); setError(""); },
    onError: () => setError("The video strategy could not be completed. Existing content and creative history remain available."),
  });
  const retryCreative = useMutation({
    mutationFn: (asset: CreativeAsset) => runCreativeOperationWithRecovery({
      progress: { phase: asset.media_type === "video" ? "video_generation" : "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => asset.media_type === "video" ? marketingApi.creative.generateVideo(activeBusinessId, asset.id) : marketingApi.creative.generate(activeBusinessId, asset.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => { observeCreativeGeneration(asset); setNotice(creativeResultNotice(asset)); setError(""); },
    onError: () => setError("The visual creative could not be completed. Refresh to see saved progress and try again."),
  });
  const regenerateCreative = useMutation({
    mutationFn: ({ asset, mode }: { asset: CreativeAsset; mode?: "alternate_metaphor" | "alternate_composition" }) => runCreativeOperationWithRecovery({
      progress: { phase: "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => marketingApi.creative.regenerate(activeBusinessId, asset.id, mode),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => { observeCreativeGeneration(asset); setNotice(`${creativeResultNotice(asset)} The previous creative remains in history.`); setError(""); },
    onError: () => setError("A new creative version could not be completed. Refresh to see any saved history before retrying."),
  });

  const creativePhase = creativePhaseForDisplay(
    creativeProgress,
    primary?.id,
    creativeAssets.data?.[0]?.id,
  );
  const primaryConnector = integrationRegistry.data?.find(
    (item) => item.connector_type === primary?.channel,
  );
  const primaryConnection = integrationConnections.data?.find(
    (item) => item.connector_type === primary?.channel,
  );
  const primaryPublishingCapability = publishingCapability({
    channel: primary?.channel ?? "other",
    definition: primaryConnector,
    connection: primaryConnection,
    pending: integrationRegistry.isPending || integrationConnections.isPending,
    failed: integrationRegistry.isError || integrationConnections.isError,
  });
  const creativePending =
    createCreative.isPending ||
    createVideo.isPending ||
    retryCreative.isPending ||
    regenerateCreative.isPending ||
    Boolean(activeCreativeGeneration);

  const metrics = analytics.data;
  const currency = metrics?.currency || activeBusiness?.currency || "USD";
  const money = (value: string) => new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact" }).format(Number(value));
  const initialLoading =
    plans.isLoading && content.isLoading && analytics.isLoading;
  const hasPartialFailure =
    plans.isError || content.isError || analytics.isError ||
    calendar.isError || campaigns.isError || creativeAssets.isError ||
    integrationRegistry.isError || integrationConnections.isError;
  const openStrategyDrawer = () => {
    setError("");
    setShowPlanGenerator(true);
  };
  const openContentDrawer = () => {
    setError("");
    setShowContentGenerator(true);
  };
  const openAdvancedCreativeDirection = (mediaType: "image" | "video") => {
    if (!primary) return;
    const params = new URLSearchParams({
      content: primary.id,
      creative: mediaType,
    });
    navigate(`/marketing/content?${params.toString()}`);
  };

  return <>
    <PageHeader
      eyebrow="AI CMO"
      title="AI Marketing Manager"
      subtitle="Grounded strategy, durable content, and internal campaign planning—never silent external execution."
      actionClassName="cmo-overview-actions"
      action={
        <>
          <Button
            variant="secondary"
            className="cmo-overview-action cmo-overview-action-secondary"
            onClick={openStrategyDrawer}
            data-testid="button-generate-strategy"
          >
            <Target />
            Generate strategy
          </Button>
          <Button
            variant="primary"
            className="cmo-overview-action cmo-overview-action-primary"
            onClick={openContentDrawer}
            data-testid="button-generate-content"
          >
            <Plus />
            New content
          </Button>
        </>
      }
    />
    <CmoDepartmentNav active={activeTab} />
    {notice && <div className="ai-banner" role="status" aria-live="polite"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    {error && <div className="ai-banner" role="alert" aria-live="assertive"><AlertCircle /> {error}<button className="close-btn" onClick={() => setError("")}><X /></button></div>}
    {hasPartialFailure && <div className="ai-banner" role="status" aria-live="polite"><AlertCircle />Some marketing sections could not refresh. Available internal planning data remains usable.<Button className="btn-sm" onClick={() => void refreshWorkspace()}>Retry failed sections</Button></div>}
    {initialLoading ? <Card><div className="empty"><RefreshCw className="spin" /><p>Assembling the marketing workspace…</p></div></Card> : <>
      {analytics.isError ? <Card><div className="empty"><BarChart3 /><h3>Recorded performance could not load</h3><p>{humanizeApiError(analytics.error, "Retry the performance section. Internal plans and content are still available.")}</p><Button onClick={() => void analytics.refetch()}>Retry performance</Button></div></Card> : analytics.isLoading ? <Card><div className="empty" role="status" aria-live="polite"><RefreshCw className="spin" /><p>Loading recorded performance…</p></div></Card> : <div className="grid kpi-grid"><Kpi title="Reach" value={(metrics?.reach ?? 0).toLocaleString()} foot="Recorded in selected period" icon={<Globe2 />} tone="green" /><Kpi title="Click-through rate" value={`${Number(metrics?.ctr ?? 0).toFixed(2)}%`} foot={`${metrics?.clicks ?? 0} recorded clicks`} icon={<TrendingUp />} tone="orange" /><Kpi title="Leads" value={String(metrics?.leads ?? 0)} foot="Attributed records only" icon={<Target />} tone="brown" /><Kpi title="Revenue / ROAS" value={`${money(metrics?.revenue ?? "0")} · ${Number(metrics?.roas ?? 0).toFixed(2)}x`} foot={`${money(metrics?.spend ?? "0")} recorded spend`} icon={<BarChart3 />} tone="rose" /></div>}
      <div className="grid split-grid"><Card><SectionTitle title="Current strategy" action={<Badge tone={plans.data?.items[0]?.status === "active" ? "success" : "warning"}>{plans.data?.items[0]?.status || "No plan"}</Badge>} />{plans.isError ? <div className="empty"><AlertCircle /><h3>Strategy could not load</h3><p>{humanizeApiError(plans.error, "Retry this section.")}</p><Button onClick={() => void plans.refetch()}>Retry strategy</Button></div> : plans.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading strategy…</p></div> : plans.data?.items[0] ? <><div className="eyebrow">{plans.data.items[0].generated_by === "ai" ? "AI CMO conclusion" : "User strategy"}</div><h2>{plans.data.items[0].title}</h2><p className="detail-copy">{plans.data.items[0].positioning}</p><div className="recommendation-strip"><Sparkles /><div><div className="eyebrow">Key message</div><p>{plans.data.items[0].key_message}</p></div></div><div className="chip-list">{plans.data.items[0].channels.map((channel) => <Badge tone="info" key={channel}>{channel}</Badge>)}</div><div className="toolbar" style={{ marginTop: 14 }}><Button className="btn-sm" onClick={() => setEditingPlan(true)}>Review & edit</Button>{plans.data.items[0].status === "ready" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("active")}>Activate strategy</Button>}{plans.data.items[0].status === "active" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("completed")}>Complete strategy</Button>}{plans.data.items[0].status === "completed" && <Button className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("archived")}>Archive</Button>}</div></> : <div className="empty"><Target /><h3>Your AI marketing workspace is ready</h3><p>Generate a strategy from the trusted Business Brain. Channel connections are optional until execution.</p><Button variant="primary" className="cmo-card-cta" onClick={openStrategyDrawer}>Generate marketing plan</Button></div>}</Card><Card><SectionTitle title="Campaign operating system" action={<Badge>{campaigns.data?.total ?? 0} campaigns</Badge>} />{campaigns.isError ? <div className="empty"><AlertCircle /><h3>Campaign drafts could not load</h3><p>{humanizeApiError(campaigns.error, "Retry campaign planning.")}</p><Button onClick={() => void campaigns.refetch()}>Retry campaigns</Button></div> : campaigns.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading campaigns…</p></div> : <>{campaigns.data?.items.slice(0, 5).map((campaign) => <div className="list-row" key={campaign.id}><Target /><div className="row-main"><strong>{campaign.name}</strong><div className="row-copy">{campaign.objective}</div></div><Badge tone={campaign.status === "active" ? "success" : campaign.status === "awaiting_approval" ? "warning" : "neutral"}>{campaign.status.replaceAll("_", " ")}</Badge></div>)}{!campaigns.data?.items.length && <div className="empty"><Target /><h3>No campaign drafts</h3><p>Campaign planning works before Meta or Google is connected.</p><LinkButton href="/campaigns?new=1">Prepare campaign</LinkButton></div>}</>}<div className="ai-banner"><AlertCircle />Connect Meta or Google Ads only when you are ready for governed external execution.</div></Card></div>
      <div className="grid split-grid cmo-studio-calendar-grid">
        <CmoContentStudioCard
          content={primary}
          businessName={activeBusiness?.name}
          isLoading={content.isLoading}
          error={
            content.isError
              ? humanizeApiError(
                  content.error,
                  "Retry content. Publishing connections are not required for drafting.",
                )
              : null
          }
          isRegenerating={regenerate.isPending}
          isApproving={approve.isPending}
          creative={creativeAssets.data?.[0]}
          creatives={creativeAssets.data}
          isCreativeLoading={creativeAssets.isLoading}
          isCreativePending={creativePending}
          creativeError={creativeAssets.isError ? humanizeApiError(creativeAssets.error, "Retry loading creative history.") : null}
          creativePhase={creativePhase}
          onRetry={() => void content.refetch()}
          onGenerate={openContentDrawer}
          onRegenerate={(item) => regenerate.mutate(item)}
          onApprove={(item) => approve.mutate(item)}
          onSchedule={(item) => {
            setError("");
            setSchedule(item);
          }}
          publishingCapability={primaryPublishingCapability}
          isPreparingPublish={preparePublish.isPending}
          onPreparePublish={(item) => preparePublish.mutate(item)}
          onConnectChannel={primaryConnector ? () => navigate("/integrations") : undefined}
          onEdit={(item) => {
            setError("");
            setEditingContent(item);
          }}
          onHistory={(item) => {
            setError("");
            setHistoryContent(item);
          }}
          onCreateCreative={(mediaType) => {
            if (primary) {
              if (mediaType === "video") createVideo.mutate(primary);
              else createCreative.mutate(primary);
            }
          }}
          onEditCreativeDirection={openAdvancedCreativeDirection}
          onReloadCreative={(asset) => asset ? refreshCreativeRecord(asset) : creativeAssets.refetch()}
          onRetryCreative={(asset) => retryCreative.mutate(asset)}
          onRegenerateCreative={(asset) => regenerateCreative.mutate({ asset })}
          onVariationCreative={(asset) => regenerateCreative.mutate({ asset, mode: "alternate_metaphor" })}
        />
        <CmoCalendarCompanion
          items={calendar.data}
          content={content.data?.items}
          isLoading={calendar.isLoading}
          error={calendar.isError ? humanizeApiError(calendar.error, "Retry the internal calendar.") : null}
          onRetry={() => void calendar.refetch()}
          onSchedule={(item) => {
            setError("");
            setSchedule(item);
          }}
        />
      </div>
    </>}
    <CmoContentGeneratorDrawer
      open={showContentGenerator}
      canAuthorizeOffer={canAuthorizeOffer}
      businessName={activeBusiness?.name}
      businessLocale={activeBusiness?.locale}
      campaigns={campaigns.data?.items}
      campaignsLoading={campaigns.isLoading}
      campaignsError={campaigns.isError}
      pending={generateContent.isPending}
      error={error}
      onClose={() => {
        setShowContentGenerator(false);
        setError("");
      }}
      onSubmit={(input) => generateContent.mutate(input)}
    />
    {editingContent && (
      <Modal
        wide
        title="Edit content"
        description="Saving creates a new immutable version. The selected version remains unchanged in history."
        onClose={() => {
          setEditingContent(null);
          setError("");
        }}
      >
        <form onSubmit={submitContentEdit}>
          <div
            className="ai-banner"
            style={{ marginBottom: 18 }}
          >
            <Check />
            This is a manual revision. Existing Business Brain provenance and
            creative context remain attached to the new version.
          </div>

          <div className="form-grid">
            <div className="field full">
              <label htmlFor="cmo-edit-title">Title</label>
              <input
                id="cmo-edit-title"
                name="title"
                required
                maxLength={180}
                defaultValue={editingContent.title}
              />
            </div>

            <div className="field full">
              <label htmlFor="cmo-edit-body">Content</label>
              <textarea
                id="cmo-edit-body"
                name="body"
                required
                maxLength={20000}
                defaultValue={editingContent.body}
                style={{
                  minHeight: 220,
                  padding: 14,
                  fontSize: 12,
                  lineHeight: 1.65,
                  background: "#ffffff",
                }}
              />
            </div>

            <div className="field full">
              <label htmlFor="cmo-edit-cta">
                Call to action
                <span
                  style={{
                    marginLeft: 5,
                    color: "#98a2b3",
                    fontWeight: 500,
                  }}
                >
                  optional
                </span>
              </label>
              <input
                id="cmo-edit-cta"
                name="cta"
                maxLength={300}
                defaultValue={editingContent.cta || ""}
                placeholder="Example: Explore the collection"
              />
            </div>
          </div>

          {error && (
            <p className="form-error" style={{ marginTop: 14 }}>
              {error}
            </p>
          )}

          <div className="modal-foot">
            <Button
              type="button"
              onClick={() => {
                setEditingContent(null);
                setError("");
              }}
            >
              Cancel
            </Button>

            <Button
              variant="primary"
              type="submit"
              disabled={editContent.isPending}
            >
              {editContent.isPending ? (
                <>
                  <RefreshCw className="spin" />
                  Saving revision…
                </>
              ) : (
                <>
                  <Check />
                  Save new version
                </>
              )}
            </Button>
          </div>
        </form>
      </Modal>
    )}

    {historyContent && (
      <Modal
        wide
        title="Version history"
        description="Every revision is preserved. AI-generated and human-edited versions remain clearly identified."
        onClose={() => {
          setHistoryContent(null);
          setError("");
        }}
      >
        {versions.isLoading ? (
          <div className="empty">
            <RefreshCw className="spin" />
            <p>Loading content history…</p>
          </div>
        ) : versions.isError ? (
          <div className="empty">
            <AlertCircle />
            <h3>Version history could not load</h3>
            <p>
              {humanizeApiError(
                versions.error,
                "Retry loading this content history.",
              )}
            </p>
            <Button onClick={() => void versions.refetch()}>
              <RefreshCw />
              Retry history
            </Button>
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            {versions.data?.map((version) => (
              <div
                key={version.id}
                style={{
                  padding: 16,
                  border: "1px solid #e4e7ec",
                  borderRadius: 14,
                  background:
                    version.id === historyContent.id
                      ? "#f6f9ff"
                      : "#ffffff",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    gap: 12,
                    marginBottom: 12,
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div className="eyebrow">
                      Version {version.version}
                    </div>
                    <strong
                      style={{
                        display: "block",
                        color: "#101828",
                        fontSize: 13,
                        lineHeight: 1.4,
                      }}
                    >
                      {version.title}
                    </strong>
                  </div>

                  <div
                    className="chip-list"
                    style={{ justifyContent: "flex-end" }}
                  >
                    {version.id === historyContent.id && (
                      <Badge tone="info">Selected</Badge>
                    )}

                    <Badge tone={version.ai_generated ? "info" : "neutral"}>
                      {version.ai_generated
                        ? "AI generated"
                        : "Manual revision"}
                    </Badge>

                    <Badge>
                      {version.status.replaceAll("_", " ")}
                    </Badge>
                  </div>
                </div>

                <p
                  style={{
                    color: "#475467",
                    fontSize: 11,
                    lineHeight: 1.65,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {version.body}
                </p>

                {version.cta && (
                  <div
                    className="subtle"
                    style={{ marginTop: 10, fontSize: 10 }}
                  >
                    CTA · {version.cta}
                  </div>
                )}

                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    flexWrap: "wrap",
                    marginTop: 14,
                    paddingTop: 12,
                    borderTop: "1px solid #eaecf0",
                  }}
                >
                  <div className="subtle" style={{ fontSize: 9 }}>
                    {new Date(version.created_at).toLocaleString()}
                  </div>

                  <Button
                    variant="secondary"
                    className="btn-sm"
                    onClick={() => {
                      setHistoryContent(null);
                      setError("");
                      setEditingContent(version);
                    }}
                  >
                    Edit from this version
                  </Button>
                </div>
              </div>
            ))}

            {!versions.data?.length && (
              <div className="empty">
                <AlertCircle />
                <h3>No versions available</h3>
                <p>This content does not have a recorded version history yet.</p>
              </div>
            )}
          </div>
        )}

        <div className="modal-foot">
          <Button
            onClick={() => {
              setHistoryContent(null);
              setError("");
            }}
          >
            Close
          </Button>
        </div>
      </Modal>
    )}

    <WorkspaceDrawer
        open={showPlanGenerator}
        eyebrow="AI CMO"
        title="Create marketing strategy"
        description="Tell 9D Brain the goal. Your Business Brain supplies the context."
        onClose={() => setShowPlanGenerator(false)}
        closeDisabled={generatePlan.isPending}
        testId="cmo-strategy-workspace-drawer"
        footer={
          <div className="cmo-drawer-footer-actions">
            <Button
              type="button"
              disabled={generatePlan.isPending}
              onClick={() => setShowPlanGenerator(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="cmo-strategy-generator-form"
              disabled={generatePlan.isPending}
            >
              {generatePlan.isPending ? (
                <>
                  <RefreshCw className="spin" />
                  Building strategy…
                </>
              ) : (
                <>
                  <Sparkles />
                  Generate strategy
                </>
              )}
            </Button>
          </div>
        }
      >
        <form
          id="cmo-strategy-generator-form"
          className="cmo-drawer-form"
          onSubmit={submitGeneratePlan}
        >
          <div className="ai-banner">
            <Sparkles />
            This saves usable conclusions only—never hidden reasoning or external actions.
          </div>

          <section className="cmo-form-section" aria-labelledby="cmo-strategy-objective-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-strategy-objective-heading">Campaign objective</h3>
              <p>Describe the business outcome so AI CMO can build a grounded direction.</p>
            </div>
            <div className="cmo-drawer-grid">
              <div className="field full">
                <label htmlFor="cmo-strategy-title">
                  Plan title <span className="cmo-optional">optional</span>
                </label>
                <input id="cmo-strategy-title" name="title" maxLength={180} />
              </div>
              <div className="field full">
                <label htmlFor="cmo-strategy-goal">Marketing goal</label>
                <textarea
                  id="cmo-strategy-goal"
                  className="cmo-goal-input"
                  name="goal"
                  required
                  maxLength={4000}
                  autoFocus
                  placeholder="Example: Increase qualified demand for our new service"
                />
              </div>
            </div>
          </section>

          <section className="cmo-form-section" aria-labelledby="cmo-strategy-audience-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-strategy-audience-heading">Audience &amp; budget</h3>
              <p>Set practical guidance without replacing trusted business context.</p>
            </div>
            <div className="cmo-drawer-grid cmo-audience-budget-grid">
              <div className="field">
                <label htmlFor="cmo-strategy-audience">Target audience</label>
                <textarea
                  id="cmo-strategy-audience"
                  name="audience"
                  required
                  maxLength={2000}
                  placeholder="Use generic administrative/customer segmentation only"
                />
              </div>
              <div className="field">
                <label htmlFor="cmo-strategy-budget">Budget guidance</label>
                <input
                  id="cmo-strategy-budget"
                  name="budget"
                  type="number"
                  min="0"
                  max="1000000000"
                  step="0.01"
                  placeholder="Optional"
                />
              </div>
            </div>
          </section>

          <section className="cmo-form-section" aria-labelledby="cmo-strategy-timing-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-strategy-timing-heading">Timing</h3>
              <p>Add a planning window when the strategy is tied to specific dates.</p>
            </div>
            <div className="cmo-drawer-grid">
              <div className="field">
                <label htmlFor="cmo-strategy-period-start">Period start</label>
                <input id="cmo-strategy-period-start" name="period_start" type="date" />
              </div>
              <div className="field">
                <label htmlFor="cmo-strategy-period-end">Period end</label>
                <input id="cmo-strategy-period-end" name="period_end" type="date" />
              </div>
            </div>
          </section>

          <section className="cmo-form-section" aria-labelledby="cmo-strategy-channels-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-strategy-channels-heading">Channels</h3>
              <p>Select the channels the strategy should coordinate.</p>
            </div>
            <div className="cmo-channel-grid" aria-label="Strategy channels">
              {channels.map((channel) => (
                <label className="cmo-channel-option" key={channel}>
                  <input
                    type="checkbox"
                    name="channels"
                    value={channel}
                    defaultChecked={["instagram", "email"].includes(channel)}
                  />
                  <span>{channelLabel(channel)}</span>
                </label>
              ))}
            </div>
          </section>

          {error && <p className="form-error">{error}</p>}
        </form>
      </WorkspaceDrawer>
    {editingPlan && plans.data?.items[0] && <Modal wide title="Review marketing plan" description="Edit the usable AI conclusions before activating the strategy." onClose={() => setEditingPlan(false)}><form onSubmit={submitUpdatePlan}><div className="form-grid"><div className="field full"><label>Title</label><input name="title" required defaultValue={plans.data.items[0].title} /></div><div className="field full"><label>Objective</label><textarea name="objective" required maxLength={1000} defaultValue={plans.data.items[0].objective} /></div><div className="field full"><label>Target audience</label><textarea name="target_audience" required maxLength={2000} defaultValue={plans.data.items[0].target_audience} /></div><div className="field full"><label>Positioning</label><textarea name="positioning" required maxLength={3000} defaultValue={plans.data.items[0].positioning} /></div><div className="field full"><label>Key message</label><textarea name="key_message" required maxLength={3000} defaultValue={plans.data.items[0].key_message} /></div><div className="field full"><label>Offer</label><textarea name="offer" maxLength={2000} defaultValue={plans.data.items[0].offer || ""} /></div><div className="field full"><label>Content strategy</label><textarea name="content_strategy" maxLength={5000} defaultValue={plans.data.items[0].content_strategy || ""} /></div><div className="field full"><label>Measurement goals (one per line)</label><textarea name="measurement_goals" defaultValue={plans.data.items[0].measurement_goals.join("\n")} /></div></div><div className="modal-foot"><Button type="button" onClick={() => setEditingPlan(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={updatePlan.isPending}>{updatePlan.isPending ? "Saving…" : "Save reviewed plan"}</Button></div></form></Modal>}
    {schedule && <Modal title="Schedule content" description="Creates an internal calendar item; no social platform is contacted." onClose={() => setSchedule(null)}><form onSubmit={submitSchedule}><div className="field"><label>Date and time</label><input name="scheduled_for" type="datetime-local" required /></div><div className="modal-foot"><Button type="button" onClick={() => setSchedule(null)}>Cancel</Button><Button variant="primary" type="submit" disabled={createSchedule.isPending}><Calendar /> Schedule internally</Button></div></form></Modal>}
  </>;
}

function LinkButton({ href, children }: { href: string; children: ReactNode }) {
  return <a className="btn btn-primary cmo-card-cta" href={href}>{children}</a>;
}
