import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Circle,
  Link2,
  MessageCircle,
  Package,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { Link } from "wouter";
import { useBusiness } from "@/business-context";
import { Avatar, Badge, Card, PageHeader, SectionTitle } from "@/components/product-ui";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import { getIndustryWorkspaceProfile, isWorkspaceModuleVisible } from "@/lib/industry-workspaces";
import { businessDateRange } from "@/lib/operational-dates";
import {
  isTodayInTimezone,
} from "@/lib/phase8a-product";
import { activationReadinessApi } from "@/services/activation-readiness";
import { aiWorkforceApi } from "@/services/ai-workforce";
import { automationsApi } from "@/services/automations";
import { integrationsApi } from "@/services/integrations";
import { operationsApi } from "@/services/operations";
import { processingApi } from "@/services/processing";

function Metric({ title, value, foot, icon, tone }: { title: string; value: string; foot: string; icon: ReactNode; tone: string }) {
  return <Card className="kpi"><div className="kpi-top"><span>{title}</span><div className={`kpi-icon ${tone}`}>{icon}</div></div><div className="kpi-value">{value}</div><div className="kpi-foot"><span>{foot}</span></div></Card>;
}

export function BusinessDashboardPage() {
  const { activeBusiness, activeBusinessId, billing } = useBusiness();
  const workspaceProfile = getIndustryWorkspaceProfile(activeBusiness?.industry);
  const terminology = workspaceProfile.terminology;
  const appointmentWorkspace = workspaceProfile.dashboardVariant === "healthcare" || workspaceProfile.dashboardVariant === "professional_services";
  const showOrders = isWorkspaceModuleVisible(activeBusiness?.industry, "orders");
  const entitlements = billing?.entitlements ?? null;
  const schedulingEnabled = isBusinessFeatureEnabled(activeBusiness, "scheduling", entitlements);
  const agentsEnabled = isBusinessFeatureEnabled(activeBusiness, "ai_agents", entitlements);
  const automationsEnabled = isBusinessFeatureEnabled(activeBusiness, "automations", entitlements);
  const integrationsEnabled = isBusinessFeatureEnabled(activeBusiness, "integrations", entitlements);
  const commandCenterEnabled = isBusinessFeatureEnabled(activeBusiness, "ai_command_center", entitlements);
  const period = useMemo(() => businessDateRange(activeBusiness?.timezone || "UTC", 30), [activeBusiness?.timezone]);

  const analytics = useQuery({ queryKey: ["operations", activeBusinessId, "analytics", period.start, period.end], queryFn: ({ signal }) => operationsApi.analytics(activeBusinessId, period.start, period.end, signal), enabled: Boolean(activeBusinessId) });
  const orders = useQuery({ queryKey: ["operations", activeBusinessId, "orders", "dashboard"], queryFn: ({ signal }) => operationsApi.orders.list(activeBusinessId, { pageSize: 5 }, signal), enabled: Boolean(activeBusinessId && showOrders) });
  const conversations = useQuery({ queryKey: ["operations", activeBusinessId, "conversations", "dashboard"], queryFn: ({ signal }) => operationsApi.conversations.list(activeBusinessId, { pageSize: 4 }, signal), enabled: Boolean(activeBusinessId) });
  const opportunities = useQuery({ queryKey: ["operations", activeBusinessId, "opportunities", "dashboard"], queryFn: ({ signal }) => operationsApi.opportunities.list(activeBusinessId, { status: "open", pageSize: 3 }, signal), enabled: Boolean(activeBusinessId) });
  const agentActivity = useQuery({ queryKey: ["ai-workforce", activeBusinessId, "activity", "dashboard"], queryFn: ({ signal }) => aiWorkforceApi.agents.activity(activeBusinessId, { pageSize: 100 }, signal), enabled: Boolean(activeBusinessId && agentsEnabled) });
  const dailyBrief = useQuery({ queryKey: ["ai-workforce", activeBusinessId, "daily-brief", "dashboard"], queryFn: ({ signal }) => aiWorkforceApi.commands.dailyBrief(activeBusinessId, signal), enabled: Boolean(activeBusinessId && commandCenterEnabled) });
  const approvals = useQuery({ queryKey: ["approvals", activeBusinessId, "pending", "dashboard"], queryFn: ({ signal }) => automationsApi.approvals.list(activeBusinessId, "pending", signal), enabled: Boolean(activeBusinessId) });
  const processing = useQuery({ queryKey: ["processing", activeBusinessId, "dashboard"], queryFn: ({ signal }) => processingApi.health(activeBusinessId, signal), enabled: Boolean(activeBusinessId && automationsEnabled), refetchInterval: 30_000 });
  const integrationConnections = useQuery({ queryKey: ["integrations", activeBusinessId, "connections", "dashboard"], queryFn: ({ signal }) => integrationsApi.connections(activeBusinessId, signal), enabled: Boolean(activeBusinessId && integrationsEnabled) });
  const activationReadiness = useQuery({ queryKey: ["activation-readiness", activeBusinessId], queryFn: ({ signal }) => activationReadinessApi.get(activeBusinessId, signal), enabled: Boolean(activeBusinessId), refetchInterval: 30_000 });

  const a = analytics.data;
  const currency = activeBusiness?.currency || "USD";
  const money = (value: string | number) => new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact" }).format(Number(value));
  const pendingOrders = orders.data?.items.filter((item) => !["completed", "canceled"].includes(item.status)).length ?? 0;
  const timezone = activeBusiness?.timezone || "UTC";
  const todayActivity = (agentActivity.data?.items ?? []).filter((item) => isTodayInTimezone(item.created_at, timezone));
  const completedToday = todayActivity.filter((item) => item.status === "completed").length;
  const failedToday = todayActivity.filter((item) => item.status === "failed").length;
  const waitingToday = todayActivity.filter((item) => item.status === "needs_approval").length;
  const aiActivityVerified = Boolean(agentActivity.data);
  const aiCompletionSummary = aiActivityVerified
    ? `${completedToday} AI task${completedToday === 1 ? "" : "s"} completed today`
    : "today’s AI activity count is not yet verified";
  const connections = integrationConnections.data ?? [];
  const integrationAttention = connections.filter((connection) => ["degraded", "reauth_required", "revoked"].includes(connection.status)).length;
  const processingAttention = processing.data ? (processing.data.counts.failed ?? 0) + (processing.data.counts.dead_letter ?? 0) + processing.data.attention.uncertain_actions + processing.data.attention.failed_workflows_24h + processing.data.attention.failed_webhooks_24h + processing.data.attention.provider_connections_attention + processing.data.attention.commerce_connections_attention + processing.data.attention.ai_failures_24h : 0;
  const readinessItems = (activationReadiness.data?.checks ?? []).filter((item) => item.state !== "not_applicable");
  const signalUnavailable = [analytics, conversations, opportunities, approvals, activationReadiness, ...(showOrders ? [orders] : []), ...(agentsEnabled ? [agentActivity] : []), ...(automationsEnabled ? [processing] : []), ...(integrationsEnabled ? [integrationConnections] : [])].some((query) => query.isError);
  const signalsChecking = [analytics, conversations, opportunities, approvals, activationReadiness, ...(showOrders ? [orders] : []), ...(agentsEnabled ? [agentActivity] : []), ...(automationsEnabled ? [processing] : []), ...(integrationsEnabled ? [integrationConnections] : [])].some((query) => query.isPending);
  const attentionItems = [
    ...(signalUnavailable ? [{ label: "One or more operating signals are unavailable", copy: "Retry the affected connection or service before treating the control room as complete.", href: "/settings", tone: "danger" as const }] : []),
    ...(approvals.data?.items.length ? [{ label: `${approvals.data.items.length} approval${approvals.data.items.length === 1 ? "" : "s"} waiting`, copy: "Review the complete action and policy context.", href: "/approvals", tone: "warning" as const }] : []),
    ...(processingAttention ? [{ label: `${processingAttention} processing job${processingAttention === 1 ? "" : "s"} failed`, copy: "Inspect durable job state before relying on automation.", href: "/automations", tone: "danger" as const }] : []),
    ...(integrationAttention ? [{ label: `${integrationAttention} connection${integrationAttention === 1 ? "" : "s"} need attention`, copy: "Reconnect or restore provider health.", href: "/integrations", tone: "danger" as const }] : []),
    ...(failedToday ? [{ label: `${failedToday} AI task${failedToday === 1 ? "" : "s"} failed today`, copy: "Open the execution ledger for safe failure detail.", href: "/agents/activity", tone: "danger" as const }] : []),
    ...(pendingOrders ? [{ label: `${pendingOrders} recent order${pendingOrders === 1 ? "" : "s"} still open`, copy: "Review operational status and ownership.", href: "/orders", tone: "warning" as const }] : []),
  ];
  const briefFacts = dailyBrief.data?.sections.flatMap((section) => section.facts).slice(0, 5) ?? [];
  const priorities = [...(dailyBrief.data?.recommended_priorities ?? []), ...readinessItems.filter((item) => item.state === "action_needed").map((item) => `${item.label}: ${item.detail}`)].slice(0, 4);

  return <>
    <PageHeader eyebrow="Today · Live business control room" title={`Good morning, ${activeBusiness?.name || "there"}`} subtitle="Understand what changed, what AI prepared, and what needs your decision." action={<Link href="/reports/daily" className="btn btn-secondary">Daily report <ArrowRight /></Link>} />
    {signalUnavailable && <Card><div className="ai-banner"><AlertCircle />Some readiness or operating signals could not be verified. Unavailable checks are shown explicitly below.</div></Card>}

    <div className="grid split-grid dashboard-control-grid">
      <Card className="operating-brief-card">
        <SectionTitle title="AI Operating Brief" action={<Badge tone={dailyBrief.data ? "success" : dailyBrief.isError ? "warning" : "neutral"}>{dailyBrief.data ? "Current" : dailyBrief.isError ? "Live fallback" : commandCenterEnabled ? "Loading" : "Live records"}</Badge>} />
        <div className="operating-brief-lead"><Sparkles /><h2>{attentionItems.length ? `${attentionItems.length} area${attentionItems.length === 1 ? "" : "s"} need attention; ${aiCompletionSummary}.` : signalsChecking ? "Checking live operating signals…" : `No current alerts; ${aiCompletionSummary}.`}</h2></div>
        {briefFacts.length ? <div className="brief-facts">{briefFacts.map((fact) => <div className="brief-fact" key={fact}><CheckCircle2 /><span>{fact}</span></div>)}</div> : signalUnavailable ? <p className="detail-copy">A complete operating brief cannot be verified until the unavailable live signals recover.</p> : signalsChecking ? <p className="detail-copy">Checking tenant-scoped operations, conversations, approvals, and AI activity.</p> : <p className="detail-copy">Based on live operations: {a?.orders ?? 0} orders, {a?.leads ?? 0} leads, {conversations.data?.total ?? 0} conversation records, and {approvals.data?.items.length ?? 0} pending approvals.</p>}
        <div className="subtle">Generated from tenant-scoped records · {timezone}</div>
      </Card>
      <Card>
        <SectionTitle title="Needs your attention" action={<Badge tone={attentionItems.length ? "warning" : "success"}>{attentionItems.length} open</Badge>} />
        <div className="list">{attentionItems.slice(0, 5).map((item) => <Link href={item.href} className="list-row attention-row" key={`${item.href}-${item.label}`}><AlertCircle /><div className="row-main"><div className="row-title">{item.label}</div><div className="row-copy">{item.copy}</div></div><Badge tone={item.tone}>Review</Badge></Link>)}{!attentionItems.length && signalsChecking && <div className="empty compact-empty"><Activity /><h3>Checking live signals</h3><p>No all-clear is shown until the current checks finish.</p></div>}{!attentionItems.length && !signalsChecking && <div className="empty compact-empty"><ShieldCheck /><h3>Nothing is waiting</h3><p>Approvals, processing, connections, and recent work show no current alerts.</p></div>}</div>
      </Card>
    </div>

    <div className="grid split-grid dashboard-control-grid">
      <Card>
        <SectionTitle title="AI work today" action={<Link href="/agents/activity" className="btn btn-sm btn-soft">Full activity <ArrowRight /></Link>} />
        <div className="ai-work-summary"><div><strong>{aiActivityVerified ? completedToday : "—"}</strong><span>Completed</span></div><div><strong>{aiActivityVerified ? waitingToday : "—"}</strong><span>Needs approval</span></div><div><strong>{aiActivityVerified ? failedToday : "—"}</strong><span>Failed</span></div></div>
        <div className="list">{todayActivity.slice(0, 4).map((item) => <Link href={`/agents/${item.role}/activity`} className="list-row" key={item.id}><Bot /><div className="row-main"><div className="row-title">{item.task_summary}</div><div className="row-copy">{item.role.replaceAll("_", " ")} · {new Date(item.created_at).toLocaleTimeString()}</div></div><Badge tone={item.status === "completed" ? "success" : item.status === "failed" ? "danger" : "warning"}>{item.status.replaceAll("_", " ")}</Badge></Link>)}{agentActivity.data && !todayActivity.length && <div className="empty compact-empty"><Bot /><h3>No AI work recorded today</h3><p>Run a Command Center task or activate a governed workflow when ready.</p></div>}{agentsEnabled && agentActivity.isLoading && <p className="subtle">Loading today’s durable activity…</p>}{agentsEnabled && agentActivity.isError && <div className="empty compact-empty"><AlertCircle /><h3>AI activity unavailable</h3><p>No work count is asserted until the execution ledger can be read.</p></div>}{!agentsEnabled && <div className="empty compact-empty"><Bot /><h3>AI employees are not enabled</h3><p>Review plan access before configuring the AI team.</p></div>}</div>
      </Card>
      <Card>
        <SectionTitle title="Recommended next actions" action={<Sparkles />} />
        <div className="list">{priorities.map((priority, index) => <div className="list-row" key={priority}><span className="priority-number">{index + 1}</span><div className="row-main"><div className="row-title">{priority}</div></div></div>)}{!priorities.length && opportunities.data?.items.map((item) => <Link href="/opportunities" className="list-row" key={item.id}><Target /><div className="row-main"><div className="row-title">{item.title}</div><div className="row-copy">{item.description}</div></div><ArrowRight /></Link>)}{!priorities.length && opportunities.data && !opportunities.data.items.length && <div className="empty compact-empty"><CheckCircle2 /><h3>No next action is being asserted</h3><p>Add trusted data or create a command when you want the AI team to investigate.</p></div>}</div>
      </Card>
    </div>

    <Card className="readiness-card">
      <SectionTitle title="First-client readiness" action={<div className="toolbar"><Badge tone={activationReadiness.data?.activation_ready ? "success" : activationReadiness.isError ? "danger" : "warning"}>{activationReadiness.data ? `${activationReadiness.data.ready_required_checks} of ${activationReadiness.data.required_checks} required checks ready` : activationReadiness.isError ? "Unavailable" : "Checking"}</Badge></div>} />
      <p className="detail-copy">This server-evaluated activation gate uses tenant records and current infrastructure/provider evidence. It is not a demo score, and OAuth alone never satisfies write or production acceptance.</p>
      {activationReadiness.isPending && <p className="subtle">Checking the production activation gate…</p>}
      {activationReadiness.isError && <div className="ai-banner"><AlertCircle />Activation readiness could not be verified. No ready state is being asserted.</div>}
      <div className="readiness-grid">{readinessItems.map((item) => <Link href={item.href} className={`readiness-item readiness-${item.state}`} key={item.id}>{item.state === "ready" ? <CheckCircle2 /> : <Circle />}<div className="row-main"><strong>{item.label}</strong><span>{item.detail}</span></div><ArrowRight /></Link>)}</div>
    </Card>

    <SectionTitle title="Business performance" action={<Badge tone={a ? "success" : analytics.isError ? "danger" : "neutral"}>{a ? "Recorded data" : analytics.isError ? "Unavailable" : "Loading"}</Badge>} />
    <div className="grid kpi-grid">{appointmentWorkspace ? <><Metric title={terminology.customerPlural} value={a ? String(a.customers) : "—"} foot="Current business records" icon={<Users />} tone="green" /><Metric title={terminology.providerPlural} value={schedulingEnabled ? a ? String(a.providers) : "—" : "Not enabled"} foot={schedulingEnabled ? `Available for ${terminology.bookingPlural.toLowerCase()}` : "Scheduling is not included in the current plan"} icon={<Users />} tone="orange" /><Metric title={terminology.bookingPlural} value={schedulingEnabled ? a ? String(a.appointments) : "—" : "Not enabled"} foot={schedulingEnabled ? "Recorded in the current 30-day period" : "Scheduling is not included in the current plan"} icon={<Activity />} tone="brown" /><Metric title="New leads" value={a ? String(a.leads) : "—"} foot="Created in 30 days" icon={<Target />} tone="rose" /></> : <><Metric title="30-day revenue" value={a ? money(a.order_revenue) : "—"} foot={a ? `${a.orders} non-canceled orders` : "Waiting for recorded order data"} icon={<TrendingUp />} tone="green" /><Metric title="Pending orders" value={orders.data ? String(pendingOrders) : "—"} foot="Need operational attention" icon={<Package />} tone="orange" /><Metric title="Conversations" value={conversations.data ? String(conversations.data.total) : "—"} foot="Customer communication records" icon={<MessageCircle />} tone="brown" /><Metric title="New leads" value={a ? String(a.leads) : "—"} foot="Created in 30 days" icon={<Target />} tone="rose" /></>}</div>

    <div className="grid split-grid dashboard-activity-grid">
      <Card><SectionTitle title="Open opportunities" action={<Link href="/opportunities" className="subtle">Review all</Link>} />{opportunities.data?.items.map((item) => <Link href="/opportunities" className="list-row" key={item.id}><Sparkles /><div className="row-main"><div className="row-title">{item.title}</div><div className="row-copy">{item.description}</div></div><Badge tone={item.priority === "urgent" || item.priority === "high" ? "warning" : "neutral"}>{item.priority}</Badge></Link>)}{opportunities.data && !opportunities.data.items.length && <div className="empty compact-empty"><CheckCircle2 /><p>The opportunity queue is clear.</p></div>}</Card>
      <Card><SectionTitle title="Recent conversations" action={<Link href="/conversations" className="subtle">View inbox</Link>} />{conversations.data?.items.map((item) => <Link href="/conversations" className="list-row" key={item.id}><Avatar name={item.customer_display_name || item.channel} /><div className="row-main"><div className="row-title">{item.customer_display_name || `Unmatched ${terminology.customerSingular.toLowerCase()}`}<span className="row-copy inline-copy"> · {item.channel}</span></div><div className="row-copy">{item.latest_message || "No message recorded"}</div></div><div className="time">{new Date(item.last_activity_at).toLocaleDateString()}</div></Link>)}{conversations.data && !conversations.data.items.length && <div className="empty compact-empty"><MessageCircle /><p>No conversations yet. Connect a supported channel or record a conversation to begin.</p></div>}</Card>
    </div>

    {showOrders && <><SectionTitle title="Recent orders" action={<Link href="/orders" className="btn btn-sm btn-secondary">View all <ArrowRight /></Link>} /><Card className="table-card" pad={false}><div className="table-scroll"><table><thead><tr><th>Order</th><th>{terminology.customerSingular}</th><th>Items</th><th>Value</th><th>Status</th><th>Time</th></tr></thead><tbody>{orders.data?.items.map((item) => <tr key={item.id}><td><strong>{item.order_number}</strong></td><td>{item.customer_display_name}</td><td>{item.lines.map((line) => `${line.description} × ${line.quantity}`).join(", ")}</td><td><strong>{money(item.total)}</strong></td><td><Badge tone={item.status === "completed" ? "success" : item.status === "canceled" ? "neutral" : "warning"}>{item.status}</Badge></td><td>{new Date(item.created_at).toLocaleDateString()}</td></tr>)}</tbody></table>{orders.data && !orders.data.items.length && <div className="empty"><Package /><h3>No orders yet</h3><p>Connect or import a real commerce source, or create an order, to populate this view.</p><Link href="/commerce" className="btn btn-sm btn-secondary"><Link2 /> Set up commerce</Link></div>}</div></Card></>}
  </>;
}
