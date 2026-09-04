import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BarChart3, Calendar, Check, Globe2, RefreshCw, Sparkles, Target, TrendingUp, Wand2, WandSparkles, X } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle, WorkspaceDrawer } from "@/components/product-ui";
import { CmoContentStudioCard } from "@/features/marketing/cmo-content-studio";
import { CmoDepartmentNav } from "@/features/marketing/marketing-pages";
import {
  AUDIENCE_GUIDANCE_MAX,
  OWNER_GOAL_MAX,
  channelGenerationNotice,
  createCreativeWithRecovery,
  creativeFormatForContent,
  creativePhaseForDisplay,
  creativeResultNotice,
  generateCampaignChannelDrafts,
  runCreativeOperationWithRecovery,
  type CreativeProgress,
} from "@/lib/cmo-ux";
import { businessDateRange } from "@/lib/operational-dates";
import { humanizeApiError } from "@/services/api-client";
import type { CreativeAsset, MarketingChannel, MarketingContent, MarketingContentType } from "@/services/api-types";
import { marketingApi } from "@/services/marketing";

function Kpi({ title, value, foot, icon, tone }: { title: string; value: string; foot: string; icon: ReactNode; tone: string }) {
  return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span>{foot}</span></div></Card>;
}

const channels: MarketingChannel[] = ["instagram", "facebook", "linkedin", "tiktok", "email", "whatsapp", "website", "meta", "google_ads"];
const contentTypes: MarketingContentType[] = ["social_post", "ad_copy", "email_draft", "whatsapp_draft", "blog_draft", "landing_page_copy", "headline", "cta", "content_package"];
const primaryPlatforms: MarketingChannel[] = ["instagram", "facebook", "linkedin", "tiktok"];
const channelLabels: Record<MarketingChannel, string> = {
  meta: "Meta",
  google_ads: "Google Ads",
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  tiktok: "TikTok",
  email: "Email",
  whatsapp: "WhatsApp",
  website: "Website",
  other: "Other",
};

function channelLabel(channel: MarketingChannel) {
  return channelLabels[channel];
}

export function CmoPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const activeTab = new URLSearchParams(window.location.search).get("tab") ?? "Overview";
  const [showContentGenerator, setShowContentGenerator] = useState(false);
  const [showPlanGenerator, setShowPlanGenerator] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const [editingContent, setEditingContent] = useState<MarketingContent | null>(null);
  const [historyContent, setHistoryContent] = useState<MarketingContent | null>(null);
  const [schedule, setSchedule] = useState<MarketingContent | null>(null);
  const [creativeProgress, setCreativeProgress] = useState<CreativeProgress>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const period = useMemo(() => businessDateRange(activeBusiness?.timezone || "UTC", 30), [activeBusiness?.timezone]);
  const calendarEnd = useMemo(() => new Date(Date.now() + 30 * 86400000).toISOString(), []);

  const plans = useQuery({ queryKey: ["marketing", activeBusinessId, "plans", "cmo"], queryFn: ({ signal }) => marketingApi.plans.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const content = useQuery({ queryKey: ["marketing", activeBusinessId, "content", "cmo"], queryFn: ({ signal }) => marketingApi.content.list(activeBusinessId, { pageSize: 10 }, {}, signal), enabled: Boolean(activeBusinessId) });
  const analytics = useQuery({ queryKey: ["marketing", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => marketingApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const calendar = useQuery({ queryKey: ["marketing", activeBusinessId, "calendar", "cmo"], queryFn: ({ signal }) => marketingApi.calendar.list(activeBusinessId, new Date().toISOString(), calendarEnd, {}, signal), enabled: Boolean(activeBusinessId) });
  const campaigns = useQuery({ queryKey: ["marketing", activeBusinessId, "campaigns", "cmo"], queryFn: ({ signal }) => marketingApi.campaigns.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const primary = content.data?.items[0];
  const creativeAssets = useQuery({
    queryKey: ["marketing", activeBusinessId, "creative-assets", "cmo", primary?.id],
    queryFn: ({ signal }) => marketingApi.creative.list(activeBusinessId, primary?.campaign_id || undefined, primary!.id, signal),
    enabled: Boolean(activeBusinessId && primary),
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
  const refreshCreatives = () => queryClient.invalidateQueries({
    queryKey: ["marketing", activeBusinessId, "creative-assets"],
  });

  const generateContent = useMutation({
    mutationFn: async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const selected = form.getAll("platforms").map(String) as MarketingChannel[];
      const additional = String(form.get("additional_channel") || "") as MarketingChannel;
      if (additional) selected.push(additional);
      const selectedChannels = [...new Set(selected)];
      if (!selectedChannels.length) throw new Error("Choose at least one platform.");
      const goal = String(form.get("prompt") || "").trim();
      const audience = String(form.get("audience") || "").trim();
      return generateCampaignChannelDrafts({
        channels: selectedChannels,
        goal,
        audience,
        contentType: String(form.get("content_type") || "social_post") as MarketingContentType,
        campaignId: String(form.get("campaign_id")) || null,
        title: String(form.get("title")) || null,
        language: String(form.get("language") || "en"),
      }, (request) => marketingApi.content.generate(activeBusinessId, request));
    },
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
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();

      if (!editingContent) {
        throw new Error("Choose content to edit.");
      }

      const form = new FormData(event.currentTarget);
      const title = String(form.get("title") || "").trim();
      const body = String(form.get("body") || "").trim();
      const cta = String(form.get("cta") || "").trim();

      if (!title || !body) {
        throw new Error("Title and content are required.");
      }

      return marketingApi.content.edit(
        activeBusinessId,
        editingContent.id,
        {
          title,
          body,
          cta: cta || null,
        },
      );
    },
    onSuccess: (item) => {
      setEditingContent(null);
      setNotice(
        `Version ${item.version} was saved as a manual revision. Previous versions remain unchanged.`,
      );
      setError("");
      void refresh();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "The content revision could not be saved.",
        ),
      ),
  });

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
    mutationFn: (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const value = new FormData(event.currentTarget).get("scheduled_for"); return marketingApi.calendar.create(activeBusinessId, schedule!.id, new Date(String(value)).toISOString()); },
    onSuccess: () => { setSchedule(null); setNotice("Content was added to the internal calendar. External connection is still required to publish."); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Content could not be scheduled. Approve it first.")),
  });
  const generatePlan = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const selected = form.getAll("channels").map(String) as MarketingChannel[];
      if (!selected.length) throw new Error("Choose at least one channel.");
      return marketingApi.plans.generate(activeBusinessId, { goal: String(form.get("goal")), title: String(form.get("title")) || null, target_audience: String(form.get("audience")), channels: selected, budget_guidance: String(form.get("budget")) || null, period_start: String(form.get("period_start")) || null, period_end: String(form.get("period_end")) || null });
    },
    onSuccess: (plan) => { setShowPlanGenerator(false); setNotice(`AI CMO plan “${plan.title}” is ready for review.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "AI CMO strategy generation could not be completed.")),
  });
  const updatePlan = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const current = plans.data!.items[0];
      const form = new FormData(event.currentTarget);
      return marketingApi.plans.update(activeBusinessId, current.id, {
        title: String(form.get("title")), objective: String(form.get("objective")),
        target_audience: String(form.get("target_audience")), positioning: String(form.get("positioning")),
        key_message: String(form.get("key_message")), offer: String(form.get("offer")) || null,
        content_strategy: String(form.get("content_strategy")) || null,
        measurement_goals: String(form.get("measurement_goals") || "").split("\n").map((item) => item.trim()).filter(Boolean),
      });
    },
    onSuccess: () => { setEditingPlan(false); setNotice("Marketing plan changes were saved."); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "Marketing plan could not be updated.")),
  });
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
    onSuccess: (asset) => { setNotice(creativeResultNotice(asset)); setError(""); },
    onError: () => setError("The visual creative could not be completed. Refresh to see saved progress and try again."),
  });
  const retryCreative = useMutation({
    mutationFn: (asset: CreativeAsset) => runCreativeOperationWithRecovery({
      progress: { phase: "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => marketingApi.creative.generate(activeBusinessId, asset.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => { setNotice(creativeResultNotice(asset)); setError(""); },
    onError: () => setError("The visual creative could not be completed. Refresh to see saved progress and try again."),
  });
  const regenerateCreative = useMutation({
    mutationFn: (asset: CreativeAsset) => runCreativeOperationWithRecovery({
      progress: { phase: "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => marketingApi.creative.regenerate(activeBusinessId, asset.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => { setNotice(`${creativeResultNotice(asset)} The previous creative remains in history.`); setError(""); },
    onError: () => setError("A new creative version could not be completed. Refresh to see any saved history before retrying."),
  });

  const creativePhase = creativePhaseForDisplay(
    creativeProgress,
    primary?.id,
    creativeAssets.data?.[0]?.id,
  );

  const metrics = analytics.data;
  const currency = metrics?.currency || activeBusiness?.currency || "USD";
  const money = (value: string) => new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact" }).format(Number(value));
  const initialLoading =
    plans.isLoading && content.isLoading && analytics.isLoading;
  const hasPartialFailure =
    plans.isError || content.isError || analytics.isError ||
    calendar.isError || campaigns.isError || creativeAssets.isError;
  const openStrategyDrawer = () => {
    setError("");
    setShowPlanGenerator(true);
  };
  const openContentDrawer = () => {
    setError("");
    setShowContentGenerator(true);
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
            <WandSparkles />
            Generate content
          </Button>
        </>
      }
    />
    <CmoDepartmentNav active={activeTab} />
    {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    {error && <div className="ai-banner"><AlertCircle /> {error}<button className="close-btn" onClick={() => setError("")}><X /></button></div>}
    {hasPartialFailure && <div className="ai-banner"><AlertCircle />Some marketing sections could not refresh. Available internal planning data remains usable.<Button className="btn-sm" onClick={() => void refresh()}>Retry failed sections</Button></div>}
    {initialLoading ? <Card><div className="empty"><RefreshCw className="spin" /><p>Assembling the marketing workspace…</p></div></Card> : <>
      {analytics.isError ? <Card><div className="empty"><BarChart3 /><h3>Recorded performance could not load</h3><p>{humanizeApiError(analytics.error, "Retry the performance section. Internal plans and content are still available.")}</p><Button onClick={() => void analytics.refetch()}>Retry performance</Button></div></Card> : <div className="grid kpi-grid"><Kpi title="Reach" value={(metrics?.reach ?? 0).toLocaleString()} foot="Recorded in selected period" icon={<Globe2 />} tone="green" /><Kpi title="Click-through rate" value={`${Number(metrics?.ctr ?? 0).toFixed(2)}%`} foot={`${metrics?.clicks ?? 0} recorded clicks`} icon={<TrendingUp />} tone="orange" /><Kpi title="Leads" value={String(metrics?.leads ?? 0)} foot="Attributed records only" icon={<Target />} tone="brown" /><Kpi title="Revenue / ROAS" value={`${money(metrics?.revenue ?? "0")} · ${Number(metrics?.roas ?? 0).toFixed(2)}x`} foot={`${money(metrics?.spend ?? "0")} recorded spend`} icon={<BarChart3 />} tone="rose" /></div>}
      <div className="grid split-grid"><Card><SectionTitle title="Current strategy" action={<Badge tone={plans.data?.items[0]?.status === "active" ? "success" : "warning"}>{plans.data?.items[0]?.status || "No plan"}</Badge>} />{plans.isError ? <div className="empty"><AlertCircle /><h3>Strategy could not load</h3><p>{humanizeApiError(plans.error, "Retry this section.")}</p><Button onClick={() => void plans.refetch()}>Retry strategy</Button></div> : plans.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading strategy…</p></div> : plans.data?.items[0] ? <><div className="eyebrow">{plans.data.items[0].generated_by === "ai" ? "AI CMO conclusion" : "User strategy"}</div><h2>{plans.data.items[0].title}</h2><p className="detail-copy">{plans.data.items[0].positioning}</p><div className="recommendation-strip"><Sparkles /><div><div className="eyebrow">Key message</div><p>{plans.data.items[0].key_message}</p></div></div><div className="chip-list">{plans.data.items[0].channels.map((channel) => <Badge tone="info" key={channel}>{channel}</Badge>)}</div><div className="toolbar" style={{ marginTop: 14 }}><Button className="btn-sm" onClick={() => setEditingPlan(true)}>Review & edit</Button>{plans.data.items[0].status === "ready" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("active")}>Activate strategy</Button>}{plans.data.items[0].status === "active" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("completed")}>Complete strategy</Button>}{plans.data.items[0].status === "completed" && <Button className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("archived")}>Archive</Button>}</div></> : <div className="empty"><Target /><h3>Your AI marketing workspace is ready</h3><p>Generate a strategy from the trusted Business Brain. Channel connections are optional until execution.</p><Button variant="primary" className="cmo-card-cta" onClick={openStrategyDrawer}>Generate marketing plan</Button></div>}</Card><Card><SectionTitle title="Campaign operating system" action={<Badge>{campaigns.data?.total ?? 0} campaigns</Badge>} />{campaigns.isError ? <div className="empty"><AlertCircle /><h3>Campaign drafts could not load</h3><p>{humanizeApiError(campaigns.error, "Retry campaign planning.")}</p><Button onClick={() => void campaigns.refetch()}>Retry campaigns</Button></div> : campaigns.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading campaigns…</p></div> : <>{campaigns.data?.items.slice(0, 5).map((campaign) => <div className="list-row" key={campaign.id}><Target /><div className="row-main"><strong>{campaign.name}</strong><div className="row-copy">{campaign.objective}</div></div><Badge tone={campaign.status === "active" ? "success" : campaign.status === "awaiting_approval" ? "warning" : "neutral"}>{campaign.status.replaceAll("_", " ")}</Badge></div>)}{!campaigns.data?.items.length && <div className="empty"><Target /><h3>No campaign drafts</h3><p>Campaign planning works before Meta or Google is connected.</p><LinkButton href="/campaigns?new=1">Prepare campaign</LinkButton></div>}</>}<div className="ai-banner"><AlertCircle />Connect Meta or Google Ads only when you are ready for governed external execution.</div></Card></div>
      <div className="grid split-grid">
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
          creativeError={creativeAssets.isError ? humanizeApiError(creativeAssets.error, "Retry loading creative history.") : null}
          creativePhase={creativePhase}
          onRetry={() => void content.refetch()}
          onGenerate={openContentDrawer}
          onRegenerate={(item) => regenerate.mutate(item)}
          onApprove={(item) => approve.mutate(item)}
          onSchedule={(item) => setSchedule(item)}
          onEdit={(item) => {
            setError("");
            setEditingContent(item);
          }}
          onHistory={(item) => {
            setError("");
            setHistoryContent(item);
          }}
          onCreateCreative={() => {
            if (primary) createCreative.mutate(primary);
          }}
          onReloadCreative={() => void creativeAssets.refetch()}
          onRetryCreative={(asset) => retryCreative.mutate(asset)}
          onRegenerateCreative={(asset) => regenerateCreative.mutate(asset)}
        />
        <Card><SectionTitle title="Content calendar" action={<Badge>{calendar.data?.length ?? 0} upcoming</Badge>} />{calendar.isError ? <div className="empty"><AlertCircle /><h3>Calendar could not load</h3><p>{humanizeApiError(calendar.error, "Retry the internal calendar.")}</p><Button onClick={() => void calendar.refetch()}>Retry calendar</Button></div> : calendar.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading calendar…</p></div> : <>{calendar.data?.slice(0, 8).map((item) => { const contentItem = content.data?.items.find((value) => value.id === item.content_id); return <div className="list-row" key={item.id}><div style={{ width: 86, color: "#938c83", fontSize: 10 }}>{new Date(item.scheduled_for).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</div><div className="row-main"><div className="row-title">{contentItem?.title || `${item.channel} content`}</div><div className="row-copy">{new Date(item.scheduled_for).toLocaleTimeString()} · {item.timezone}</div></div><Badge tone="success">{item.status.replaceAll("_", " ")}</Badge></div>; })}{!calendar.data?.length && <div className="empty"><Calendar /><h3>No content scheduled</h3><p>Approved content can be added to the internal calendar without a publishing provider.</p></div>}</>}</Card></div>
    </>}
    <WorkspaceDrawer
        open={showContentGenerator}
        eyebrow="AI CMO"
        title="Create campaign content"
        description="Share the goal in plain language. 9D Brain handles the marketing strategy, copy, and channel adaptation."
        onClose={() => setShowContentGenerator(false)}
        closeDisabled={generateContent.isPending}
        testId="cmo-content-workspace-drawer"
        footer={
          <div className="cmo-drawer-footer-actions">
            <Button
              type="button"
              disabled={generateContent.isPending}
              onClick={() => {
                setShowContentGenerator(false);
                setError("");
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="cmo-content-generator-form"
              disabled={generateContent.isPending}
            >
              {generateContent.isPending ? (
                <>
                  <RefreshCw className="spin" />
                  Creating draft…
                </>
              ) : (
                <>
                  <Sparkles />
                  Create campaign content
                </>
              )}
            </Button>
          </div>
        }
      >
        <form
          id="cmo-content-generator-form"
          className="cmo-drawer-form"
          onSubmit={(event) => generateContent.mutate(event)}
        >
          <div className="cmo-drawer-intro">
            <div>
              <div className="eyebrow">Grounded generation</div>
              <h3>
                {activeBusiness?.name
                  ? `Create for ${activeBusiness.name}`
                  : "Create marketing content"}
              </h3>
              <p>
                AI CMO uses trusted Business Brain context and permitted memory.
                Industry privacy rules remain enforced automatically.
              </p>
            </div>

            <div className="chip-list">
              <Badge tone="info">
                <Sparkles />
                AI CMO
              </Badge>
              <Badge tone="success">
                <Check />
                Review first
              </Badge>
            </div>
          </div>

          <section className="cmo-form-section" aria-labelledby="cmo-content-brief-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-content-brief-heading">Campaign brief</h3>
              <p>Set the outcome and add audience guidance only when you need to.</p>
            </div>
            <div className="cmo-drawer-grid">
            <div className="field full">
              <label htmlFor="cmo-content-prompt">
                What do you want to achieve?
              </label>
              <textarea
                id="cmo-content-prompt"
                name="prompt"
                required
                maxLength={OWNER_GOAL_MAX}
                autoFocus
                placeholder="Example: Promote our new shoes"
                className="cmo-goal-input"
              />
              <span className="cmo-field-help">
                A short request is enough. Only business facts supported by
                trusted context will be used.
              </span>
            </div>

            <div className="field full">
              <label htmlFor="cmo-content-audience">
                Audience <span className="cmo-optional">optional</span>
              </label>
              <input
                id="cmo-content-audience"
                name="audience"
                maxLength={AUDIENCE_GUIDANCE_MAX}
                placeholder="Let 9D Brain choose"
              />
            </div>
            </div>
          </section>

          <section className="cmo-form-section" aria-labelledby="cmo-content-platforms-heading">
            <div className="cmo-form-section-heading">
              <h3 id="cmo-content-platforms-heading">Platforms</h3>
              <p>Each selected platform receives its own native copy variant.</p>
            </div>
              <div className="cmo-channel-grid" aria-label="Campaign platforms">
                {primaryPlatforms.map((channel) => (
                  <label className="cmo-channel-option" key={channel}>
                    <input
                      type="checkbox"
                      name="platforms"
                      value={channel}
                      defaultChecked={channel === "instagram"}
                    />
                    <span>{channelLabel(channel)}</span>
                  </label>
                ))}
              </div>
          </section>

            <details
              className="cmo-advanced-controls"
            >
              <summary>
                Advanced controls
              </summary>
              <div className="cmo-drawer-grid">

            <div className="field">
              <label htmlFor="cmo-content-campaign">Campaign</label>
              <select
                id="cmo-content-campaign"
                name="campaign_id"
                disabled={campaigns.isLoading}
                defaultValue=""
              >
                <option value="">
                  {campaigns.isLoading
                    ? "Loading campaigns…"
                    : "Standalone content"}
                </option>
                {campaigns.data?.items.map((campaign) => (
                  <option value={campaign.id} key={campaign.id}>
                    {campaign.name}
                  </option>
                ))}
              </select>
              <span className="cmo-field-help">
                Optional campaign context
              </span>
            </div>

            <div className="field">
              <label htmlFor="cmo-content-channel">Additional channel</label>
              <select
                id="cmo-content-channel"
                name="additional_channel"
                defaultValue=""
              >
                <option value="">None</option>
                {channels.map((channel) => (
                  <option key={channel} value={channel}>
                    {channelLabel(channel)}
                  </option>
                ))}
              </select>
              <span className="cmo-field-help">
                Email, website, ads, and other supported formats remain available
              </span>
            </div>

            <div className="field">
              <label htmlFor="cmo-content-type">Content type</label>
              <select
                id="cmo-content-type"
                name="content_type"
                defaultValue="social_post"
              >
                {contentTypes.map((type) => (
                  <option key={type} value={type}>
                    {type.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <span className="cmo-field-help">
                Choose the format you need
              </span>
            </div>

            <div className="field">
              <label htmlFor="cmo-content-language">Language</label>
              <input
                id="cmo-content-language"
                name="language"
                defaultValue={activeBusiness?.locale || "en"}
                maxLength={16}
                spellCheck={false}
              />
              <span className="cmo-field-help">
                Uses your business locale by default
              </span>
            </div>

            <div className="field full">
              <label htmlFor="cmo-content-title">
                Title override
                <span className="cmo-optional">
                  optional
                </span>
              </label>
              <input
                id="cmo-content-title"
                name="title"
                maxLength={180}
                placeholder="Leave blank and let AI CMO create the title"
              />
            </div>
              </div>
            </details>

          <div className="cmo-assurance-grid">
            <div className="cmo-assurance-item">
              <div className="cmo-assurance-title">
                <Check size={13} />
                Business Brain
              </div>
              <p>
                Uses trusted business facts and branding available to the CMO.
              </p>
            </div>

            <div className="cmo-assurance-item">
              <div className="cmo-assurance-title">
                <Target size={13} />
                Campaign aware
              </div>
              <p>
                Selected campaign objectives and authorized offers are considered.
              </p>
            </div>

            <div className="cmo-assurance-item">
              <div className="cmo-assurance-title">
                <AlertCircle size={13} />
                Approval protected
              </div>
              <p>
                Generation creates a draft only. Nothing is published automatically.
              </p>
            </div>
          </div>

          {campaigns.isError && (
            <div className="ai-banner warning">
              <AlertCircle />
              Campaigns could not load. You can still create standalone content.
            </div>
          )}

          {error && (
            <p className="form-error">
              {error}
            </p>
          )}

        </form>
      </WorkspaceDrawer>
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
        <form onSubmit={(event) => editContent.mutate(event)}>
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
          onSubmit={(event) => generatePlan.mutate(event)}
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
    {editingPlan && plans.data?.items[0] && <Modal wide title="Review marketing plan" description="Edit the usable AI conclusions before activating the strategy." onClose={() => setEditingPlan(false)}><form onSubmit={(event) => updatePlan.mutate(event)}><div className="form-grid"><div className="field full"><label>Title</label><input name="title" required defaultValue={plans.data.items[0].title} /></div><div className="field full"><label>Objective</label><textarea name="objective" required maxLength={1000} defaultValue={plans.data.items[0].objective} /></div><div className="field full"><label>Target audience</label><textarea name="target_audience" required maxLength={2000} defaultValue={plans.data.items[0].target_audience} /></div><div className="field full"><label>Positioning</label><textarea name="positioning" required maxLength={3000} defaultValue={plans.data.items[0].positioning} /></div><div className="field full"><label>Key message</label><textarea name="key_message" required maxLength={3000} defaultValue={plans.data.items[0].key_message} /></div><div className="field full"><label>Offer</label><textarea name="offer" maxLength={2000} defaultValue={plans.data.items[0].offer || ""} /></div><div className="field full"><label>Content strategy</label><textarea name="content_strategy" maxLength={5000} defaultValue={plans.data.items[0].content_strategy || ""} /></div><div className="field full"><label>Measurement goals (one per line)</label><textarea name="measurement_goals" defaultValue={plans.data.items[0].measurement_goals.join("\n")} /></div></div><div className="modal-foot"><Button type="button" onClick={() => setEditingPlan(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={updatePlan.isPending}>{updatePlan.isPending ? "Saving…" : "Save reviewed plan"}</Button></div></form></Modal>}
    {schedule && <Modal title="Schedule content" description="Creates an internal calendar item; no social platform is contacted." onClose={() => setSchedule(null)}><form onSubmit={(event) => createSchedule.mutate(event)}><div className="field"><label>Date and time</label><input name="scheduled_for" type="datetime-local" required /></div><div className="modal-foot"><Button type="button" onClick={() => setSchedule(null)}>Cancel</Button><Button variant="primary" type="submit" disabled={createSchedule.isPending}><Calendar /> Schedule internally</Button></div></form></Modal>}
  </>;
}

function LinkButton({ href, children }: { href: string; children: ReactNode }) {
  return <a className="btn btn-primary cmo-card-cta" href={href}>{children}</a>;
}
