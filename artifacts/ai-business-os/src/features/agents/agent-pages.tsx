import { useMemo, useState } from "react";
import { Activity, ArrowLeft, Bot, Check, Gauge, Save, Settings2, ShieldCheck, Sparkles, Target, Wrench } from "lucide-react";
import { Link } from "wouter";
import { Badge, Button, Card, PageHeader, SectionTitle } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import { slug } from "@/lib/product-utils";

const overviewIcons = { manager: Gauge, cmo: Sparkles, sales: Target, support: Bot, operations: Wrench, analytics: Activity };

export function AgentsOverviewPage() {
  const { data, update } = useWorkspaceData();
  const [filter, setFilter] = useState("All");
  const [activityFilter, setActivityFilter] = useState("All");
  return <>
    <PageHeader eyebrow="Virtual team" title="AI employees" subtitle="Configure the AI team, control autonomy, and inspect every important action." action={<Button onClick={() => setFilter(filter === "All" ? "Active" : "All")}><Settings2 /> {filter === "All" ? "Show active" : "Show all"}</Button>} />
    <div className="grid agent-grid">{data.agents.filter((agent) => filter === "All" || agent.active).map((agent) => { const Icon = overviewIcons[agent.id as keyof typeof overviewIcons] ?? Bot; return <Card className={`agent-card ${agent.active ? "active" : ""}`} key={agent.id}><div className="agent-head"><div className="agent-identity"><div className="agent-icon"><Icon /></div><div><h3>{agent.name}</h3><div className="agent-role">{agent.role}</div></div></div><button className={`switch ${agent.active ? "on" : ""}`} onClick={() => update((current) => ({ ...current, agents: current.agents.map((item) => item.id === agent.id ? { ...item, active: !item.active } : item) }))} aria-label={`Toggle ${agent.name}`}><i /></button></div><div className="stat-row"><span>Autonomy</span><Badge tone={agent.autonomy === "Autonomous" ? "success" : agent.autonomy === "Approval" ? "warning" : "neutral"}>{agent.autonomy}</Badge></div><div className="stat-row"><span>Tasks today</span><strong>{agent.tasks}</strong></div><div className="stat-row"><span>Success rate</span><strong>{agent.success}%</strong></div><div className="stat-row"><span>Last activity</span><strong>{agent.lastActivity}</strong></div><div className="agent-actions"><Link href={`/agents/${agent.id}`} className="btn btn-sm btn-secondary"><Settings2 /> Configure</Link><Link href={`/agents/${agent.id}/activity`} className="btn btn-sm btn-soft"><Activity /> Activity</Link></div></Card>; })}</div>
    <SectionTitle title="Agent activity" action={<div className="filters">{["All", "Completed", "Awaiting approval"].map((item) => <button className={`filter ${activityFilter === item ? "active" : ""}`} onClick={() => setActivityFilter(item)} key={item}>{item}</button>)}<Link href="/agents/activity" className="btn btn-sm btn-secondary">Full activity</Link></div>} />
    <Card><div className="list">{data.agentActivity.filter((item) => activityFilter === "All" || (activityFilter === "Awaiting approval" ? item.approval === "Pending" : item.status === activityFilter)).slice(0, 6).map((item) => <div className="list-row" key={item.id}><div className="activity-dot" /><div className="row-main"><div className="row-title">{item.agent} <span className="row-copy" style={{ display: "inline" }}>· {item.action}</span></div><div className="row-copy">{item.timestamp} · {item.entity}</div></div><Badge tone={item.approval === "Pending" ? "warning" : item.status === "Completed" ? "success" : "danger"}>{item.approval === "Pending" ? "Awaiting approval" : item.status}</Badge></div>)}</div></Card>
  </>;
}

export function AgentDetailPage({ params }: { params: { agentId: string } }) {
  const { data, update, recordAudit } = useWorkspaceData();
  const agent = data.agents.find((item) => item.id === params.agentId);
  const [notice, setNotice] = useState("");
  if (!agent) return <Card><div className="empty"><Bot /><h3>Agent not found</h3><p>This AI employee is not configured in the active business.</p><Link href="/agents" className="btn btn-green">Back to AI employees</Link></div></Card>;

  const save = (autonomy: typeof agent.autonomy, escalation: string) => {
    update((current) => ({ ...current, agents: current.agents.map((item) => item.id === agent.id ? { ...item, autonomy, escalation } : item) }));
    recordAudit({ actor: "Alexandra Andria", actorType: "Human user", action: "Updated agent configuration", entity: agent.name, after: `${autonomy} autonomy`, status: "Completed", source: "AI Agents" });
    setNotice("Agent configuration saved to this business.");
  };

  return <>
    <div className="back-row"><Link href="/agents"><ArrowLeft /> AI employees</Link></div>
    <PageHeader eyebrow="AI employee configuration" title={agent.name} subtitle={agent.role} action={<div className="toolbar"><Badge tone={agent.active ? "success" : "neutral"}>{agent.active ? "Active" : "Paused"}</Badge><Link href={`/agents/${agent.id}/activity`} className="btn btn-soft"><Activity /> View activity</Link></div>} />
    {notice && <div className="ai-banner"><Check /> {notice}</div>}
    <div className="grid agent-detail-grid">
      <Card className="agent-profile-card"><div className="agent-profile-icon"><Bot /></div><div className="eyebrow">Role</div><h2>{agent.role}</h2><div className="agent-health"><div><strong>{agent.tasks}</strong><span>Tasks today</span></div><div><strong>{agent.success}%</strong><span>Success rate</span></div><div><strong>{agent.lastActivity}</strong><span>Last activity</span></div></div></Card>
      <Card><SectionTitle title="Current goals" action={<Target />} /><div className="goal-list">{agent.goals.map((goal) => <div className="check-line" key={goal}><Check /> {goal}</div>)}</div><SectionTitle title="Tools available" action={<Wrench />} /><div className="chip-list">{agent.tools.map((tool) => <Badge tone="info" key={tool}>{tool}</Badge>)}</div></Card>
    </div>
    <div className="grid split-grid">
      <AgentConfiguration agent={agent} onSave={save} />
      <Card><SectionTitle title="Permissions" action={<ShieldCheck />} /><div className="permission-list">{[...new Set([...agent.permissions, "View audit history", "Create low-risk records", "Send external messages"])].map((permission, index) => <label className="permission" key={permission}>{permission}<input type="checkbox" defaultChecked={index < agent.permissions.length} /></label>)}</div><div className="risk-note"><ShieldCheck /><div><strong>Human oversight remains active</strong><p>External publishing, refunds, discounts, and irreversible actions always follow the workspace approval policy.</p></div></div></Card>
    </div>
  </>;
}

function AgentConfiguration({ agent, onSave }: { agent: ReturnType<typeof useWorkspaceData>["data"]["agents"][number]; onSave: (autonomy: typeof agent.autonomy, escalation: string) => void }) {
  const [autonomy, setAutonomy] = useState(agent.autonomy);
  const [escalation, setEscalation] = useState(agent.escalation);
  return <Card><SectionTitle title="Configuration" action={<Settings2 />} /><div className="field"><label>Autonomy</label><select value={autonomy} onChange={(event) => setAutonomy(event.target.value as typeof autonomy)}><option>Suggest</option><option>Approval</option><option>Autonomous</option></select></div><div className={`autonomy-explainer ${autonomy.toLowerCase()}`}><Gauge /><div><strong>{autonomy}</strong><p>{autonomy === "Suggest" ? "The agent analyzes and recommends, but never prepares or runs the action." : autonomy === "Approval" ? "The agent prepares the action and waits for a human decision before execution." : "Only approved low-risk action classes may run automatically. High-risk work still escalates."}</p></div></div><div className="field" style={{ marginTop: 18 }}><label>Escalation policy</label><textarea value={escalation} onChange={(event) => setEscalation(event.target.value)} /></div><Button variant="green" onClick={() => onSave(autonomy, escalation)}><Save /> Save configuration</Button></Card>;
}

export function AgentActivityPage({ params }: { params?: { agentId?: string } }) {
  const { data } = useWorkspaceData();
  const [agentId, setAgentId] = useState(params?.agentId ?? "All");
  const [status, setStatus] = useState("All");
  const [type, setType] = useState("All");
  const activity = useMemo(() => data.agentActivity.filter((item) => (agentId === "All" || item.agentId === agentId) && (status === "All" || item.status === status) && (type === "All" || item.type === type)), [data.agentActivity, agentId, status, type]);
  return <>
    <div className="back-row"><Link href="/agents"><ArrowLeft /> AI employees</Link></div>
    <PageHeader eyebrow="AI team governance" title="Agent Activity" subtitle="See what each AI employee changed, why it acted, and whether approval was required." action={<Badge tone="success"><Activity /> Live prototype history</Badge>} />
    <Card className="table-card" pad={false}><div className="table-toolbar"><div className="filters"><select className="business-select" value={agentId} onChange={(event) => setAgentId(event.target.value)}><option value="All">All agents</option>{data.agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.name}</option>)}</select><select className="business-select" value={status} onChange={(event) => setStatus(event.target.value)}><option>All</option><option>Completed</option><option>Failed</option><option>Running</option></select><select className="business-select" value={type} onChange={(event) => setType(event.target.value)}><option>All</option>{[...new Set(data.agentActivity.map((item) => item.type))].map((item) => <option key={item}>{item}</option>)}</select><select className="business-select"><option>Today</option><option>Last 7 days</option><option>Last 30 days</option></select></div></div><div className="table-scroll"><table><thead><tr><th>Timestamp</th><th>Agent</th><th>Action</th><th>Business entity</th><th>Action type</th><th>Status</th><th>Result</th><th>Approval</th></tr></thead><tbody>{activity.map((item) => <tr key={item.id}><td>{item.timestamp}</td><td><strong>{item.agent}</strong></td><td>{item.action}</td><td>{item.entity}</td><td>{item.type}</td><td><Badge tone={item.status === "Completed" ? "success" : item.status === "Failed" ? "danger" : "warning"}>{item.status}</Badge></td><td>{item.result}</td><td><Badge tone={item.approval === "Pending" ? "warning" : "neutral"}>{item.approval}</Badge></td></tr>)}</tbody></table></div></Card>
  </>;
}

export function agentHref(name: string) {
  return `/agents/${slug(name.replace(/^AI /, ""))}`;
}
