import { ArrowRight, CheckCircle2, Headphones, Lightbulb, MessageCircle, Package, Sparkles, Target, TrendingUp, Users } from "lucide-react";
import { useLocation } from "wouter";
import { Badge, Button, Card, PageHeader, SectionTitle } from "@/components/product-ui";
import { useBusiness } from "@/business-context";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import { money } from "@/lib/product-utils";
import { demoWorkspaceDataEnabled } from "@/services/workspace-repository";

export function DailyReportPage() {
  const { activeBusiness } = useBusiness();
  const { data, industry, update } = useWorkspaceData();
  const [, setLocation] = useLocation();
  if (!demoWorkspaceDataEnabled) {
    return <>
      <PageHeader eyebrow="Daily AI Report" title="Your operating brief is waiting for live data" subtitle={`The workspace for ${activeBusiness?.name} is connected to real authentication and business records.`} />
      <Card><div className="empty"><Sparkles /><h3>No report data yet</h3><p>Daily reports will populate when the supporting business APIs are available.</p></div></Card>
    </>;
  }
  const taskCount = data.agentActivity.reduce((sum) => sum + 41, 20);
  const reportGroups = industry === "Real Estate" ? [
    { title: "Business", icon: TrendingUp, metrics: [["Pipeline", money(data.analytics.revenue, true)], ["Active listings", String(activeBusiness?.products.length ?? 0)], ["Viewings", "8"]] },
    { title: "Marketing", icon: Target, metrics: [["Reach", "24.8k"], ["Engagement", "8.1%"], ["Best content", "Investor explainer"]] },
    { title: "Sales", icon: Users, metrics: [["Leads", String(data.analytics.leads)], ["Conversion", `${data.analytics.conversion}%`], ["Pipeline moved", "$1.2m"]] },
    { title: "Support", icon: Headphones, metrics: [["Conversations", "29"], ["Response time", "1m 12s"], ["Escalations", "2"]] },
    { title: "Operations", icon: Package, metrics: [["Viewings", "8"], ["Documents", "3 pending"], ["Calendar conflicts", "0"]] },
  ] : [
    { title: "Business", icon: TrendingUp, metrics: [["Revenue", money(data.analytics.revenue, true)], ["Orders", String(data.analytics.orders)], ["Customers", String(data.analytics.customers)]] },
    { title: "Marketing", icon: Target, metrics: [["Reach", "18.4k"], ["Engagement", "6.8%"], ["Best content", "Harvest story"]] },
    { title: "Sales", icon: Users, metrics: [["Leads", String(data.analytics.leads)], ["Conversion", `${data.analytics.conversion}%`], ["Pipeline moved", "$4.1k"]] },
    { title: "Support", icon: Headphones, metrics: [["Conversations", "42"], ["Response time", "48 sec"], ["Auto-resolution", "94%"]] },
    { title: "Operations", icon: Package, metrics: [["Orders handled", "24"], ["Inventory issues", "1"], ["Deliveries", "18"]] },
  ];
  const insights = industry === "Real Estate" ? [
    ["Oak Hills buyer interest accelerated.", "Four pre-approved buyers saved or requested the property.", "$42,600 potential commission", "Review the viewing queue", "/crm"],
    ["Fast response is the conversion lever today.", "Three qualified leads have waited beyond your healthy baseline.", "$1.9m pipeline at risk", "Open lead follow-up", "/crm"],
    ["Investor education is outperforming listing tours.", "The last yield explainer produced 2.1× more saves.", "18 likely guide downloads", "Create investor content", "/cmo?tab=Content"],
    ["All eight viewing slots are conflict-free.", "AI Operations checked calendars, buffers, and agent coverage.", "Zero scheduling risk", "Review operations", "/properties"],
  ] : [
    ["Fresh Eggs are leading repeat revenue.", "Returning customers ordered eggs 22% more often this week.", "$680–$1,240 potential revenue", "Create a repeat-order action", "/opportunities"],
    ["Raw honey may run out in four days.", "Order velocity is above the current stock plan.", "$340 revenue at risk", "Review inventory", "/inventory"],
    ["Harvest stories are gaining traction.", "The last field-to-table post reached 32% above average.", "+4,800 estimated reach", "Create harvest content", "/cmo?tab=Content"],
    ["Three high-intent leads need a faster reply.", "First response time slipped beyond your conversion baseline.", "$4,130 pipeline at risk", "Open sales queue", "/crm"],
  ];

  const createAction = (title: string, copy: string) => update((current) => ({ ...current, opportunities: [{ id: Date.now(), title, copy, category: "Daily report", impact: "Prioritized", reviewed: false }, ...current.opportunities] }));

  return <>
    <PageHeader eyebrow="Daily AI Report · Today" title={`Good morning. Your AI Business Team completed ${taskCount} tasks.`} subtitle={`A concise operating brief for ${activeBusiness?.name}.`} action={<Badge tone="success"><CheckCircle2 /> Ready · 7:00 AM</Badge>} />
    <div className="report-summary"><Sparkles /><div><div className="eyebrow">Manager brief</div><h2>{industry === "Real Estate" ? "Buyer interest is healthy, with response time the clearest opportunity." : "Revenue momentum is healthy, with one inventory risk and two growth opportunities."}</h2><p>Your AI team connected activity across marketing, sales, support, and operations before preparing this report.</p></div></div>
    <div className="grid report-grid">{reportGroups.map(({ title, icon: Icon, metrics }) => <Card key={title} className="report-card"><div className="report-card-head"><div className="integration-icon"><Icon /></div><h2>{title}</h2></div>{metrics.map(([name, value]) => <div className="stat-row" key={name}><span>{name}</span><strong>{value}</strong></div>)}</Card>)}</div>
    <SectionTitle title="AI found 4 important things" action={<span>Prioritized by impact and urgency</span>} />
    <div className="grid report-insights">{insights.map(([title, why, impact, action, href], index) => <Card className="report-insight" key={title}><div className="insight-number">0{index + 1}</div><div className="row-main"><div className="eyebrow">What happened</div><h2>{title}</h2><div className="report-why"><strong>Why it matters</strong><p>{why}</p></div><Badge tone={index < 2 ? "warning" : "info"}>Potential impact · {impact}</Badge><div className="toolbar" style={{ marginTop: 16 }}><Button variant="green" className="btn-sm" onClick={() => { createAction(title, why); setLocation(href); }}>{action} <ArrowRight /></Button></div></div><Lightbulb /></Card>)}</div>
  </>;
}
