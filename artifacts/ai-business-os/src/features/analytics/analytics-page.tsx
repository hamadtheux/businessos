import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BarChart3, CalendarDays, Check, Plus, RefreshCw, ShoppingBag, Sparkles, Target, TrendingUp, Users, X } from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import { operationsApi } from "@/services/operations";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import { getIndustryWorkspaceProfile } from "@/lib/industry-workspaces";
import { businessDateRange } from "@/lib/operational-dates";
import { marketingApi } from "@/services/marketing";
import type { MarketingChannel } from "@/services/api-types";
import { GrowthLearningPanel } from "./growth-learning-panel";

function Metric({ title, value, icon, tone }: { title: string; value: string; icon: React.ReactNode; tone: string }) {
  return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span>Selected period</span></div></Card>;
}
export function AnalyticsPage() {
  const { activeBusinessId, activeBusiness, billing } = useBusiness();
  const queryClient = useQueryClient();
  const [days, setDays] = useState(30);
  const [recording, setRecording] = useState(false);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const period = useMemo(() => businessDateRange(activeBusiness?.timezone || "UTC", days), [activeBusiness?.timezone, days]);
  const query = useQuery({ queryKey: ["operations", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => operationsApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const marketing = useQuery({ queryKey: ["marketing", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => marketingApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const campaigns = useQuery({ queryKey: ["marketing", activeBusinessId, "campaigns", "analytics-picker"], queryFn: ({ signal }) => marketingApi.campaigns.list(activeBusinessId, { pageSize: 100 }, signal), enabled: Boolean(activeBusinessId) });
  const record = useMutation({ mutationFn: (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); return marketingApi.performance.create(activeBusinessId, { campaign_id: String(form.get("campaign_id")), channel: String(form.get("channel")) as MarketingChannel, period_start: String(form.get("period_start")), period_end: String(form.get("period_end")), data_source: "manual", spend: String(form.get("spend") || "0"), revenue: String(form.get("revenue") || "0"), impressions: Number(form.get("impressions") || 0), reach: Number(form.get("reach") || 0), clicks: Number(form.get("clicks") || 0), leads: Number(form.get("leads") || 0), conversions: Number(form.get("conversions") || 0) }); }, onSuccess: () => { setRecording(false); setNotice("Performance was recorded; all ratios were calculated by the server."); setActionError(""); void queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId] }); }, onError: (reason) => setActionError(humanizeApiError(reason, "Performance record could not be saved. Check campaign channels and metric totals.")) });
  if (query.isError) return <><PageHeader eyebrow="Intelligence" title="Business analytics" subtitle="Database-backed operating and marketing signals for this business." /><Card><div className="empty"><AlertCircle /><h3>Operational analytics could not load</h3><p>{humanizeApiError(query.error, "Try again in a moment.")}</p><Button onClick={() => void query.refetch()}>Try again</Button></div></Card></>;
  const analytics = query.data;
  const currency = activeBusiness?.currency || "USD";
  const money = (value: string) => new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact" }).format(Number(value));
  const sources = analytics ? Object.entries(analytics.lead_source_counts).map(([name, value]) => ({ name, value })) : [];
  const colors = ["#1268F3", "#F2B622", "#4B8DFF", "#D89300", "#94A3B8"];
  const workspaceProfile = getIndustryWorkspaceProfile(activeBusiness?.industry);
  const terminology = workspaceProfile.terminology;
  const showCommerceAnalytics =
    workspaceProfile.dashboardVariant === "agriculture" ||
    workspaceProfile.dashboardVariant === "commerce" ||
    workspaceProfile.dashboardVariant === "generic";
  const schedulingEnabled = isBusinessFeatureEnabled(
    activeBusiness,
    "scheduling",
    billing?.entitlements ?? null,
  );
  const leadSingular =
    workspaceProfile.dashboardVariant === "healthcare" ? "Inquiry" : "Lead";
  const leadPlural =
    workspaceProfile.dashboardVariant === "healthcare" ? "Inquiries" : "Leads";
  return <>
    <PageHeader eyebrow="Intelligence" title="Business analytics" subtitle="See database-backed operations and marketing performance without fabricated values." action={<><select className="business-select" value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select><Button variant="primary" disabled={!campaigns.data?.items.length} onClick={() => setRecording(true)}><Plus /> Record marketing data</Button></>} />
    {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    {actionError && !recording && <div className="ai-banner"><AlertCircle /> {actionError}<button className="close-btn" onClick={() => setActionError("")}><X /></button></div>}
    {query.isLoading || !analytics ? <Card><div className="empty"><p>Calculating operational analytics…</p></div></Card> : <>
      <div className="grid analytics-kpi-grid">
        {showCommerceAnalytics && (
          <Metric
            title="Revenue"
            value={money(analytics.order_revenue)}
            icon={<TrendingUp />}
            tone="green"
          />
        )}
        {showCommerceAnalytics && (
          <Metric
            title="Orders"
            value={String(analytics.orders)}
            icon={<ShoppingBag />}
            tone="orange"
          />
        )}
        <Metric
          title={`New ${terminology.customerPlural.toLowerCase()}`}
          value={String(analytics.customers)}
          icon={<Users />}
          tone="brown"
        />
        <Metric
          title={`New ${leadPlural.toLowerCase()}`}
          value={String(analytics.leads)}
          icon={<Target />}
          tone="rose"
        />
        {showCommerceAnalytics && (
          <Metric
            title="Average order"
            value={money(analytics.average_order_value)}
            icon={<BarChart3 />}
            tone="green"
          />
        )}
        {schedulingEnabled && (
          <Metric
            title={terminology.bookingPlural}
            value={String(analytics.appointments)}
            icon={<CalendarDays />}
            tone="orange"
          />
        )}
        <Metric
          title="Opportunities"
          value={String(analytics.opportunities)}
          icon={<Sparkles />}
          tone="brown"
        />
        <Metric
          title="AI actions"
          value={String(analytics.ai_actions)}
          icon={<Sparkles />}
          tone="rose"
        />
      </div>
      <div className="grid analytics-main-grid">
        {showCommerceAnalytics && (
          <Card className="chart-box">
            <SectionTitle
              title="Revenue over time"
              action={<span>{period.start} – {period.end}</span>}
            />
            <div className="rechart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={analytics.revenue_series}>
                  <defs>
                    <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#1268F3" stopOpacity={0.24} />
                      <stop offset="95%" stopColor="#1268F3" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="#E2E8F0" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={10} />
                  <YAxis tickLine={false} axisLine={false} fontSize={10} />
                  <Tooltip />
                  <Area
                    type="monotone"
                    dataKey="revenue"
                    stroke="#1268F3"
                    strokeWidth={2.4}
                    fill="url(#revenueFill)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            {!analytics.revenue_series.length && (
              <div className="empty compact-empty">
                <TrendingUp />
                <p>No order revenue in this period.</p>
              </div>
            )}
          </Card>
        )}

        <Card className="chart-box">
          <SectionTitle title={`${leadSingular} acquisition`} />
          <div className="pie-chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={sources}
                  innerRadius={58}
                  outerRadius={82}
                  paddingAngle={4}
                  dataKey="value"
                >
                  {sources.map((entry, index) => (
                    <Cell
                      key={entry.name}
                      fill={colors[index % colors.length]}
                    />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
            <div className="pie-center">
              <strong>{analytics.leads}</strong>
              <span>{`new ${leadPlural.toLowerCase()}`}</span>
            </div>
          </div>
          <div className="legend">
            {sources.map((item, index) => (
              <span key={item.name}>
                <i style={{ background: colors[index % colors.length] }} />
                {item.name} {item.value}
              </span>
            ))}
          </div>
          {!sources.length && (
            <div className="empty compact-empty">
              <Target />
              <p>{`No ${leadSingular.toLowerCase()} sources in this period.`}</p>
            </div>
          )}
        </Card>
      </div>
      <div className="grid analytics-secondary-grid">
        {showCommerceAnalytics && (
          <Card className="chart-box">
            <SectionTitle title="Orders by day" />
            <div className="rechart-wrap small">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={analytics.revenue_series}>
                  <CartesianGrid vertical={false} stroke="#E2E8F0" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={10} />
                  <YAxis tickLine={false} axisLine={false} fontSize={10} />
                  <Tooltip />
                  <Bar dataKey="orders" fill="#4B8DFF" radius={[5, 5, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        )}

        <Card>
          <SectionTitle title="Pipeline distribution" />
          {Object.entries(analytics.crm_stage_counts).map(([name, count]) => (
            <div className="stat-row" key={name}>
              <span>{name}</span>
              <strong>{count}</strong>
            </div>
          ))}
          {!Object.keys(analytics.crm_stage_counts).length && (
            <p className="subtle">
              {`No ${leadPlural.toLowerCase()} in this period.`}
            </p>
          )}
        </Card>

        <Card>
          <SectionTitle title="Opportunity status" />
          {Object.entries(analytics.opportunity_status_counts).map(([name, count]) => (
            <div className="stat-row" key={name}>
              <span>{name}</span>
              <strong>{count}</strong>
            </div>
          ))}
          {!Object.keys(analytics.opportunity_status_counts).length && (
            <p className="subtle">No opportunities in this period.</p>
          )}
        </Card>
      </div>
      {marketing.isError ? <><PageHeader eyebrow="Marketing performance" title="Campaign intelligence" subtitle="Operational analytics above remain available." /><Card><div className="empty"><AlertCircle /><h3>Marketing performance could not load</h3><p>{humanizeApiError(marketing.error, "Stored operational analytics are unaffected.")}</p><Button onClick={() => void marketing.refetch()}>Retry marketing data</Button></div></Card></> : marketing.isLoading || !marketing.data ? <Card><div className="empty"><RefreshCw className="spin" /><p>Loading marketing performance…</p></div></Card> : <>
      <PageHeader eyebrow="Marketing performance" title="Campaign intelligence" subtitle="Descriptive manual/imported records with server-derived CTR, CPC, CPL, CPA, and ROAS. Attribution classes may differ and are not causal proof." />
      <div className="grid analytics-kpi-grid"><Metric title="Marketing spend" value={money(marketing.data.spend)} icon={<Target />} tone="orange" /><Metric title="Recorded revenue" value={money(marketing.data.revenue)} icon={<TrendingUp />} tone="green" /><Metric title="Recorded ROAS" value={`${Number(marketing.data.roas).toFixed(2)}x`} icon={<BarChart3 />} tone="brown" /><Metric title="Recorded conversions" value={String(marketing.data.conversions)} icon={<Sparkles />} tone="rose" /><Metric title="Impressions" value={marketing.data.impressions.toLocaleString()} icon={<Users />} tone="green" /><Metric title="CTR" value={`${Number(marketing.data.ctr).toFixed(2)}%`} icon={<BarChart3 />} tone="orange" /><Metric title="CPC" value={money(marketing.data.cpc)} icon={<Target />} tone="brown" /><Metric title="Leads" value={String(marketing.data.leads)} icon={<Users />} tone="rose" /></div>
      <div className="grid analytics-main-grid"><Card className="chart-box"><SectionTitle title="Marketing spend and revenue" /><div className="rechart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={marketing.data.trends}><CartesianGrid vertical={false} stroke="#E2E8F0" /><XAxis dataKey="label" tickLine={false} axisLine={false} fontSize={10} /><YAxis tickLine={false} axisLine={false} fontSize={10} /><Tooltip /><Area type="monotone" dataKey="revenue" stroke="#1268F3" fill="#EAF2FF" /><Area type="monotone" dataKey="spend" stroke="#F2B622" fill="#FFF6D8" /></AreaChart></ResponsiveContainer></div>{!marketing.data.trends.length && <div className="empty compact-empty"><BarChart3 /><p>No marketing performance in this period.</p></div>}</Card><Card><SectionTitle title="Channel comparison" />{marketing.data.channels.map((item) => <div className="list-row" key={item.label}><div className="row-main"><strong>{item.label}</strong><div className="row-copy">{item.clicks} clicks · {item.conversions} conversions</div></div><Badge tone={Number(item.roas) >= 1 ? "success" : "neutral"}>{Number(item.roas).toFixed(2)}x ROAS</Badge></div>)}{!marketing.data.channels.length && <div className="empty compact-empty"><Target /><p>No channel records yet.</p></div>}</Card></div>
      <div className="grid analytics-secondary-grid"><Card><SectionTitle title="Campaign comparison" />{marketing.data.campaigns.map((item) => <div className="stat-row" key={item.label}><span>{item.label}</span><strong>{money(item.revenue)} · {Number(item.roas).toFixed(2)}x</strong></div>)}{!marketing.data.campaigns.length && <p className="subtle">No campaign performance records.</p>}</Card><Card><SectionTitle title="Top content" />{marketing.data.top_content.map((item) => <div className="stat-row" key={item.content_id}><span>{item.title}</span><strong>{item.conversions} conversions</strong></div>)}{!marketing.data.top_content.length && <p className="subtle">No content-attributed performance records.</p>}</Card><Card><SectionTitle title="Cost efficiency" /><div className="stat-row"><span>Cost per lead</span><strong>{money(marketing.data.cpl)}</strong></div><div className="stat-row"><span>Cost per acquisition</span><strong>{money(marketing.data.cpa)}</strong></div><div className="stat-row"><span>Recorded reach</span><strong>{marketing.data.reach.toLocaleString()}</strong></div></Card></div>
      </>}
      <GrowthLearningPanel campaigns={campaigns.data} />
    </>}
    {recording && <Modal title="Record marketing performance" description="Enter source metrics only. CTR, CPC, CPM, CPL, CPA, and ROAS are calculated by the server." onClose={() => setRecording(false)}><form onSubmit={(event) => record.mutate(event)}><div className="form-grid"><div className="field"><label>Campaign</label><select name="campaign_id" required>{campaigns.data?.items.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name}</option>)}</select></div><div className="field"><label>Channel</label><select name="channel">{["instagram", "facebook", "linkedin", "tiktok", "email", "whatsapp", "website", "meta", "google_ads"].map((channel) => <option value={channel} key={channel}>{channel}</option>)}</select></div><div className="field"><label>Period start</label><input name="period_start" type="date" required defaultValue={period.start} /></div><div className="field"><label>Period end</label><input name="period_end" type="date" required defaultValue={period.end} /></div><div className="field"><label>Spend</label><input name="spend" type="number" min="0" step="0.0001" defaultValue="0" /></div><div className="field"><label>Revenue</label><input name="revenue" type="number" min="0" step="0.0001" defaultValue="0" /></div><div className="field"><label>Impressions</label><input name="impressions" type="number" min="0" defaultValue="0" /></div><div className="field"><label>Reach</label><input name="reach" type="number" min="0" defaultValue="0" /></div><div className="field"><label>Clicks</label><input name="clicks" type="number" min="0" defaultValue="0" /></div><div className="field"><label>Leads</label><input name="leads" type="number" min="0" defaultValue="0" /></div><div className="field"><label>Conversions</label><input name="conversions" type="number" min="0" defaultValue="0" /></div></div>{actionError && <p className="form-error">{actionError}</p>}<div className="modal-foot"><Button type="button" onClick={() => setRecording(false)}>Cancel</Button><Button variant="primary" type="submit" disabled={record.isPending}>{record.isPending ? "Calculating…" : "Save performance"}</Button></div></form></Modal>}
  </>;
}
