import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BarChart3, Calendar, Check, Globe2, RefreshCw, Sparkles, Target, TrendingUp, Wand2, X } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { CmoContentStudioCard } from "@/features/marketing/cmo-content-studio";
import { CmoDepartmentNav } from "@/features/marketing/marketing-pages";
import { businessDateRange } from "@/lib/operational-dates";
import { humanizeApiError } from "@/services/api-client";
import type { MarketingChannel, MarketingContent, MarketingContentType } from "@/services/api-types";
import { marketingApi } from "@/services/marketing";

function Kpi({ title, value, foot, icon, tone }: { title: string; value: string; foot: string; icon: ReactNode; tone: string }) {
  return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span>{foot}</span></div></Card>;
}

const channels: MarketingChannel[] = ["instagram", "facebook", "linkedin", "tiktok", "email", "whatsapp", "website", "meta", "google_ads"];
const contentTypes: MarketingContentType[] = ["social_post", "ad_copy", "email_draft", "whatsapp_draft", "blog_draft", "landing_page_copy", "headline", "cta", "content_package"];

export function CmoPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const activeTab = new URLSearchParams(window.location.search).get("tab") ?? "Overview";
  const [showContentGenerator, setShowContentGenerator] = useState(false);
  const [showPlanGenerator, setShowPlanGenerator] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const [schedule, setSchedule] = useState<MarketingContent | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const period = useMemo(() => businessDateRange(activeBusiness?.timezone || "UTC", 30), [activeBusiness?.timezone]);
  const calendarEnd = useMemo(() => new Date(Date.now() + 30 * 86400000).toISOString(), []);

  const plans = useQuery({ queryKey: ["marketing", activeBusinessId, "plans", "cmo"], queryFn: ({ signal }) => marketingApi.plans.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const content = useQuery({ queryKey: ["marketing", activeBusinessId, "content", "cmo"], queryFn: ({ signal }) => marketingApi.content.list(activeBusinessId, { pageSize: 10 }, {}, signal), enabled: Boolean(activeBusinessId) });
  const analytics = useQuery({ queryKey: ["marketing", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => marketingApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const calendar = useQuery({ queryKey: ["marketing", activeBusinessId, "calendar", "cmo"], queryFn: ({ signal }) => marketingApi.calendar.list(activeBusinessId, new Date().toISOString(), calendarEnd, {}, signal), enabled: Boolean(activeBusinessId) });
  const campaigns = useQuery({ queryKey: ["marketing", activeBusinessId, "campaigns", "cmo"], queryFn: ({ signal }) => marketingApi.campaigns.list(activeBusinessId, { pageSize: 10 }, signal), enabled: Boolean(activeBusinessId) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId] });

  const generateContent = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      return marketingApi.content.generate(activeBusinessId, {
        prompt: String(form.get("prompt")), channel: String(form.get("channel")) as MarketingChannel,
        content_type: String(form.get("content_type")) as MarketingContentType,
        campaign_id: String(form.get("campaign_id")) || null, title: String(form.get("title")) || null,
        language: String(form.get("language") || "en"),
      });
    },
    onSuccess: (item) => { setShowContentGenerator(false); setNotice(`“${item.title}” is ready for internal review.`); setError(""); void refresh(); },
    onError: (reason) => setError(humanizeApiError(reason, "AI content generation could not be completed.")),
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

  const primary = content.data?.items[0];
  const metrics = analytics.data;
  const currency = metrics?.currency || activeBusiness?.currency || "USD";
  const money = (value: string) => new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact" }).format(Number(value));
  const initialLoading =
    plans.isLoading && content.isLoading && analytics.isLoading;
  const hasPartialFailure =
    plans.isError || content.isError || analytics.isError ||
    calendar.isError || campaigns.isError;

  return <>
    <PageHeader eyebrow="AI CMO" title="AI Marketing Manager" subtitle="Grounded strategy, durable content, and internal campaign planning—never silent external execution." action={<div className="toolbar"><Button onClick={() => setShowPlanGenerator(true)}><Target /> Generate strategy</Button><Button variant="primary" onClick={() => setShowContentGenerator(true)} data-testid="button-generate-content"><Wand2 /> Generate content</Button></div>} />
    <CmoDepartmentNav active={activeTab} />
    {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    {error && <div className="ai-banner"><AlertCircle /> {error}<button className="close-btn" onClick={() => setError("")}><X /></button></div>}
    {hasPartialFailure && <div className="ai-banner"><AlertCircle />Some marketing sections could not refresh. Available internal planning data remains usable.<Button className="btn-sm" onClick={() => void refresh()}>Retry failed sections</Button></div>}
    {initialLoading ? <Card><div className="empty"><RefreshCw className="spin" /><p>Assembling the marketing workspace…</p></div></Card> : <>
      {analytics.isError ? <Card><div className="empty"><BarChart3 /><h3>Recorded performance could not load</h3><p>{humanizeApiError(analytics.error, "Retry the performance section. Internal plans and content are still available.")}</p><Button onClick={() => void analytics.refetch()}>Retry performance</Button></div></Card> : <div className="grid kpi-grid"><Kpi title="Reach" value={(metrics?.reach ?? 0).toLocaleString()} foot="Recorded in selected period" icon={<Globe2 />} tone="green" /><Kpi title="Click-through rate" value={`${Number(metrics?.ctr ?? 0).toFixed(2)}%`} foot={`${metrics?.clicks ?? 0} recorded clicks`} icon={<TrendingUp />} tone="orange" /><Kpi title="Leads" value={String(metrics?.leads ?? 0)} foot="Attributed records only" icon={<Target />} tone="brown" /><Kpi title="Revenue / ROAS" value={`${money(metrics?.revenue ?? "0")} · ${Number(metrics?.roas ?? 0).toFixed(2)}x`} foot={`${money(metrics?.spend ?? "0")} recorded spend`} icon={<BarChart3 />} tone="rose" /></div>}
      <div className="grid split-grid"><Card><SectionTitle title="Current strategy" action={<Badge tone={plans.data?.items[0]?.status === "active" ? "success" : "warning"}>{plans.data?.items[0]?.status || "No plan"}</Badge>} />{plans.isError ? <div className="empty"><AlertCircle /><h3>Strategy could not load</h3><p>{humanizeApiError(plans.error, "Retry this section.")}</p><Button onClick={() => void plans.refetch()}>Retry strategy</Button></div> : plans.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading strategy…</p></div> : plans.data?.items[0] ? <><div className="eyebrow">{plans.data.items[0].generated_by === "ai" ? "AI CMO conclusion" : "User strategy"}</div><h2>{plans.data.items[0].title}</h2><p className="detail-copy">{plans.data.items[0].positioning}</p><div className="recommendation-strip"><Sparkles /><div><div className="eyebrow">Key message</div><p>{plans.data.items[0].key_message}</p></div></div><div className="chip-list">{plans.data.items[0].channels.map((channel) => <Badge tone="info" key={channel}>{channel}</Badge>)}</div><div className="toolbar" style={{ marginTop: 14 }}><Button className="btn-sm" onClick={() => setEditingPlan(true)}>Review & edit</Button>{plans.data.items[0].status === "ready" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("active")}>Activate strategy</Button>}{plans.data.items[0].status === "active" && <Button variant="green" className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("completed")}>Complete strategy</Button>}{plans.data.items[0].status === "completed" && <Button className="btn-sm" disabled={movePlan.isPending} onClick={() => movePlan.mutate("archived")}>Archive</Button>}</div></> : <div className="empty"><Target /><h3>Your AI marketing workspace is ready</h3><p>Generate a strategy from the trusted Business Brain. Channel connections are optional until execution.</p><Button variant="primary" onClick={() => setShowPlanGenerator(true)}>Generate marketing plan</Button></div>}</Card><Card><SectionTitle title="Campaign operating system" action={<Badge>{campaigns.data?.total ?? 0} campaigns</Badge>} />{campaigns.isError ? <div className="empty"><AlertCircle /><h3>Campaign drafts could not load</h3><p>{humanizeApiError(campaigns.error, "Retry campaign planning.")}</p><Button onClick={() => void campaigns.refetch()}>Retry campaigns</Button></div> : campaigns.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading campaigns…</p></div> : <>{campaigns.data?.items.slice(0, 5).map((campaign) => <div className="list-row" key={campaign.id}><Target /><div className="row-main"><strong>{campaign.name}</strong><div className="row-copy">{campaign.objective}</div></div><Badge tone={campaign.status === "active" ? "success" : campaign.status === "awaiting_approval" ? "warning" : "neutral"}>{campaign.status.replaceAll("_", " ")}</Badge></div>)}{!campaigns.data?.items.length && <div className="empty"><Target /><h3>No campaign drafts</h3><p>Campaign planning works before Meta or Google is connected.</p><LinkButton href="/campaigns?new=1">Prepare campaign</LinkButton></div>}</>}<div className="ai-banner"><AlertCircle />Connect Meta or Google Ads only when you are ready for governed external execution.</div></Card></div>
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
          onRetry={() => void content.refetch()}
          onGenerate={() => setShowContentGenerator(true)}
          onRegenerate={(item) => regenerate.mutate(item)}
          onApprove={(item) => approve.mutate(item)}
          onSchedule={(item) => setSchedule(item)}
        />
        <Card><SectionTitle title="Content calendar" action={<Badge>{calendar.data?.length ?? 0} upcoming</Badge>} />{calendar.isError ? <div className="empty"><AlertCircle /><h3>Calendar could not load</h3><p>{humanizeApiError(calendar.error, "Retry the internal calendar.")}</p><Button onClick={() => void calendar.refetch()}>Retry calendar</Button></div> : calendar.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading calendar…</p></div> : <>{calendar.data?.slice(0, 8).map((item) => { const contentItem = content.data?.items.find((value) => value.id === item.content_id); return <div className="list-row" key={item.id}><div style={{ width: 86, color: "#938c83", fontSize: 10 }}>{new Date(item.scheduled_for).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</div><div className="row-main"><div className="row-title">{contentItem?.title || `${item.channel} content`}</div><div className="row-copy">{new Date(item.scheduled_for).toLocaleTimeString()} · {item.timezone}</div></div><Badge tone="success">{item.status.replaceAll("_", " ")}</Badge></div>; })}{!calendar.data?.length && <div className="empty"><Calendar /><h3>No content scheduled</h3><p>Approved content can be added to the internal calendar without a publishing provider.</p></div>}</>}</Card></div>
    </>}
    {showContentGenerator && (
      <Modal
        wide
        title="Create with AI"
        description="Turn a marketing goal into a grounded, review-ready draft using your existing business context."
        onClose={() => setShowContentGenerator(false)}
      >
        <form onSubmit={(event) => generateContent.mutate(event)}>
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: 16,
              padding: 16,
              marginBottom: 18,
              border: "1px solid #dbe7fb",
              borderRadius: 14,
              background:
                "linear-gradient(135deg, #f6f9ff 0%, #fbfcff 58%, #fffaf0 100%)",
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div className="eyebrow">Grounded generation</div>
              <h3
                style={{
                  margin: 0,
                  color: "#101828",
                  fontSize: 15,
                  lineHeight: 1.35,
                }}
              >
                {activeBusiness?.name
                  ? `Create for ${activeBusiness.name}`
                  : "Create marketing content"}
              </h3>
              <p
                style={{
                  marginTop: 6,
                  maxWidth: 620,
                  color: "#667085",
                  fontSize: 11,
                  lineHeight: 1.6,
                }}
              >
                AI CMO uses trusted Business Brain context and permitted memory.
                Industry privacy rules remain enforced automatically.
              </p>
            </div>

            <div
              className="chip-list"
              style={{
                justifyContent: "flex-end",
                flexShrink: 0,
              }}
            >
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

          <div className="form-grid">
            <div className="field full">
              <label htmlFor="cmo-content-prompt">
                What do you want to promote?
              </label>
              <textarea
                id="cmo-content-prompt"
                name="prompt"
                required
                maxLength={4000}
                autoFocus
                placeholder="Example: Promote our new summer collection to existing customers. Focus on quality and encourage them to explore the latest products."
                style={{
                  minHeight: 132,
                  padding: 14,
                  fontSize: 12,
                  lineHeight: 1.6,
                  background: "#ffffff",
                }}
              />
              <span
                className="subtle"
                style={{ fontSize: 9, lineHeight: 1.5 }}
              >
                Describe the goal, audience, offer, or message. AI will only use
                business facts supported by trusted context.
              </span>
            </div>

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
              <span className="subtle" style={{ fontSize: 9 }}>
                Optional campaign context
              </span>
            </div>

            <div className="field">
              <label htmlFor="cmo-content-channel">Channel</label>
              <select
                id="cmo-content-channel"
                name="channel"
                defaultValue="instagram"
              >
                {channels.map((channel) => (
                  <option key={channel} value={channel}>
                    {channel.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
              <span className="subtle" style={{ fontSize: 9 }}>
                AI adapts the draft to this channel
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
              <span className="subtle" style={{ fontSize: 9 }}>
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
              <span className="subtle" style={{ fontSize: 9 }}>
                Uses your business locale by default
              </span>
            </div>

            <div className="field full">
              <label htmlFor="cmo-content-title">
                Title override
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
                id="cmo-content-title"
                name="title"
                maxLength={180}
                placeholder="Leave blank and let AI CMO create the title"
              />
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
              gap: 10,
              marginTop: 18,
            }}
          >
            <div
              style={{
                padding: "12px 13px",
                border: "1px solid #e4e7ec",
                borderRadius: 11,
                background: "#fbfcfd",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 4,
                  color: "#344054",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                <Check size={13} />
                Business Brain
              </div>
              <p
                style={{
                  color: "#667085",
                  fontSize: 9,
                  lineHeight: 1.5,
                }}
              >
                Uses trusted business facts and branding available to the CMO.
              </p>
            </div>

            <div
              style={{
                padding: "12px 13px",
                border: "1px solid #e4e7ec",
                borderRadius: 11,
                background: "#fbfcfd",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 4,
                  color: "#344054",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                <Target size={13} />
                Campaign aware
              </div>
              <p
                style={{
                  color: "#667085",
                  fontSize: 9,
                  lineHeight: 1.5,
                }}
              >
                Selected campaign objectives and authorized offers are considered.
              </p>
            </div>

            <div
              style={{
                padding: "12px 13px",
                border: "1px solid #e4e7ec",
                borderRadius: 11,
                background: "#fbfcfd",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 4,
                  color: "#344054",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                <AlertCircle size={13} />
                Approval protected
              </div>
              <p
                style={{
                  color: "#667085",
                  fontSize: 9,
                  lineHeight: 1.5,
                }}
              >
                Generation creates a draft only. Nothing is published automatically.
              </p>
            </div>
          </div>

          {campaigns.isError && (
            <div className="ai-banner warning" style={{ marginTop: 16 }}>
              <AlertCircle />
              Campaigns could not load. You can still create standalone content.
            </div>
          )}

          {error && (
            <p className="form-error" style={{ marginTop: 14 }}>
              {error}
            </p>
          )}

          <div className="modal-foot">
            <Button
              type="button"
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
                  Generate content
                </>
              )}
            </Button>
          </div>
        </form>
      </Modal>
    )}
    {showPlanGenerator && <Modal title="Generate AI CMO strategy" description="This saves usable conclusions only—never hidden reasoning or external actions." onClose={() => setShowPlanGenerator(false)}><form onSubmit={(event) => generatePlan.mutate(event)}><div className="form-grid"><div className="field"><label>Plan title</label><input name="title" maxLength={180} /></div><div className="field"><label>Budget guidance</label><input name="budget" type="number" min="0" max="1000000000" step="0.01" /></div><div className="field full"><label>Marketing goal</label><textarea name="goal" required maxLength={4000} /></div><div className="field full"><label>Target audience</label><textarea name="audience" required maxLength={2000} placeholder="Use generic administrative/customer segmentation only" /></div><div className="field"><label>Period start</label><input name="period_start" type="date" /></div><div className="field"><label>Period end</label><input name="period_end" type="date" /></div><div className="field full"><label>Channels</label><div className="checkbox-row">{channels.map((channel) => <label key={channel}><input type="checkbox" name="channels" value={channel} defaultChecked={["instagram", "email"].includes(channel)} /> {channel.replaceAll("_", " ")}</label>)}</div></div></div><div className="modal-foot"><Button type="button" onClick={() => setShowPlanGenerator(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={generatePlan.isPending}><Sparkles /> {generatePlan.isPending ? "Building strategy…" : "Generate strategy"}</Button></div></form></Modal>}
    {editingPlan && plans.data?.items[0] && <Modal wide title="Review marketing plan" description="Edit the usable AI conclusions before activating the strategy." onClose={() => setEditingPlan(false)}><form onSubmit={(event) => updatePlan.mutate(event)}><div className="form-grid"><div className="field full"><label>Title</label><input name="title" required defaultValue={plans.data.items[0].title} /></div><div className="field full"><label>Objective</label><textarea name="objective" required maxLength={1000} defaultValue={plans.data.items[0].objective} /></div><div className="field full"><label>Target audience</label><textarea name="target_audience" required maxLength={2000} defaultValue={plans.data.items[0].target_audience} /></div><div className="field full"><label>Positioning</label><textarea name="positioning" required maxLength={3000} defaultValue={plans.data.items[0].positioning} /></div><div className="field full"><label>Key message</label><textarea name="key_message" required maxLength={3000} defaultValue={plans.data.items[0].key_message} /></div><div className="field full"><label>Offer</label><textarea name="offer" maxLength={2000} defaultValue={plans.data.items[0].offer || ""} /></div><div className="field full"><label>Content strategy</label><textarea name="content_strategy" maxLength={5000} defaultValue={plans.data.items[0].content_strategy || ""} /></div><div className="field full"><label>Measurement goals (one per line)</label><textarea name="measurement_goals" defaultValue={plans.data.items[0].measurement_goals.join("\n")} /></div></div><div className="modal-foot"><Button type="button" onClick={() => setEditingPlan(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={updatePlan.isPending}>{updatePlan.isPending ? "Saving…" : "Save reviewed plan"}</Button></div></form></Modal>}
    {schedule && <Modal title="Schedule content" description="Creates an internal calendar item; no social platform is contacted." onClose={() => setSchedule(null)}><form onSubmit={(event) => createSchedule.mutate(event)}><div className="field"><label>Date and time</label><input name="scheduled_for" type="datetime-local" required /></div><div className="modal-foot"><Button type="button" onClick={() => setSchedule(null)}>Cancel</Button><Button variant="primary" type="submit" disabled={createSchedule.isPending}><Calendar /> Schedule internally</Button></div></form></Modal>}
  </>;
}

function LinkButton({ href, children }: { href: string; children: ReactNode }) {
  return <a className="btn btn-primary" href={href}>{children}</a>;
}
