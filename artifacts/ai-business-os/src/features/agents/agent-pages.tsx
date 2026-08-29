import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import { Activity, AlertCircle, ArrowLeft, Bot, Check, Gauge, RefreshCw, Save, Settings2, ShieldCheck, Sparkles, Target, Wrench } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, EmptyState, PageHeader, SectionTitle } from "@/components/product-ui";
import { aiWorkforceApi, type AgentActivity, type AgentConfig, type AgentRole, type AutonomyMode } from "@/services/ai-workforce";

const roles: AgentRole[] = ["business_manager", "cmo", "sales", "support", "operations", "analytics"];
const icons: Record<AgentRole, typeof Bot> = { business_manager: Gauge, cmo: Sparkles, sales: Target, support: Bot, operations: Wrench, analytics: Activity };
const autonomyCopy: Record<AutonomyMode, string> = {
  manual: "AI recommends and drafts; you drive every business action.",
  supervised: "AI may do allowed internal work. Risky or external-looking actions still follow policy and approval.",
  autonomous: "AI may proceed only with server-approved low-risk internal work. Spend, destructive work, and external communication remain governed.",
};
const autonomyLabels: Record<AutonomyMode, string> = {
  manual: "Suggest",
  supervised: "Assist with approval",
  autonomous: "Operate within policy",
};

function tone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed" || status === "active") return "success";
  if (["needs_approval", "running"].includes(status)) return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return <Card><div className="empty"><AlertCircle /><h3>AI workforce data is unavailable</h3><p>{message}</p><Button onClick={retry}><RefreshCw /> Retry</Button></div></Card>;
}

export function AgentsOverviewPage() {
  const { activeBusinessId } = useBusiness();
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [activity, setActivity] = useState<AgentActivity[]>([]);
  const [activeOnly, setActiveOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const [configs, recent] = await Promise.all([
        aiWorkforceApi.agents.list(activeBusinessId, signal),
        aiWorkforceApi.agents.activity(activeBusinessId, { pageSize: 6 }, signal),
      ]);
      setAgents(configs);
      setActivity(recent.items);
      setError("");
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Unable to load AI employees.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [activeBusinessId]);

  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);

  const toggle = async (agent: AgentConfig) => {
    try {
      await aiWorkforceApi.agents.update(activeBusinessId, agent.role, { enabled: !agent.enabled });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update the agent.");
    }
  };

  return <>
    <PageHeader eyebrow="Virtual team" title="AI employees" subtitle="Configure real role capabilities, bounded autonomy, and inspect durable activity." action={<Button variant="tertiary" onClick={() => setActiveOnly((value) => !value)}><Settings2 /> {activeOnly ? "Show all" : "Show active"}</Button>} />
    {error && <ErrorState message={error} retry={() => void load()} />}
    {loading && !agents.length ? <div className="empty"><RefreshCw className="spin" /><h3>Loading AI workforce</h3></div> : <div className="grid agent-grid">{agents.filter((agent) => !activeOnly || agent.enabled).map((agent) => { const Icon = icons[agent.role]; return <Card className={`agent-card agent-${agent.role} ${agent.enabled ? "active" : "inactive"}`} key={agent.role}><div className="agent-head"><div className="agent-identity"><div className="agent-icon"><Icon /></div><div><h3>{agent.display_name}</h3><div className="agent-role">{agent.role_description}</div></div></div><button className={`switch ${agent.enabled ? "on" : ""}`} onClick={() => void toggle(agent)} aria-label={`Toggle ${agent.display_name}`} aria-pressed={agent.enabled}><i /></button></div><div className="stat-row"><span>Working mode</span><Badge tone={agent.autonomy_mode === "autonomous" ? "success" : agent.autonomy_mode === "supervised" ? "warning" : "neutral"}>{autonomyLabels[agent.autonomy_mode]}</Badge></div><div className="stat-row"><span>Completed</span><strong>{agent.metrics.completed_count}</strong></div><div className="stat-row"><span>Needs approval</span><strong>{agent.metrics.pending_approval_count}</strong></div><div className="stat-row"><span>Failed</span><strong>{agent.metrics.failed_count}</strong></div><div className="stat-row"><span>Last activity</span><strong>{agent.last_activity_at ? new Date(agent.last_activity_at).toLocaleString() : "No activity yet"}</strong></div><div className="agent-actions"><Link href={`/agents/${agent.role}`} className="btn btn-sm btn-secondary"><Settings2 /> Configure</Link><Link href={`/agents/${agent.role}/activity`} className="btn btn-sm btn-soft"><Activity /> Activity</Link></div></Card>; })}</div>}
    <SectionTitle title="Agent activity" action={<Link href="/agents/activity" className="btn btn-sm btn-secondary">Full activity</Link>} />
    <Card><div className="list">{activity.map((item) => <Link href={`/agents/${item.role}/activity`} className="list-row" key={item.id}><div className="activity-dot" /><div className="row-main"><div className="row-title">{item.role.replaceAll("_", " ")} <span className="row-copy inline-copy">· {item.task_summary}</span></div><div className="row-copy">{new Date(item.created_at).toLocaleString()} · {item.trigger}</div></div><Badge tone={tone(item.status)}>{item.status.replaceAll("_", " ")}</Badge></Link>)}{!loading && !activity.length && <EmptyState compact icon={<Activity />} title="No agent activity" description="Durable executions will appear here after your AI team runs a governed task." action={<Link href="/command" className="btn btn-sm btn-secondary">Run a command</Link>} />}</div></Card>
  </>;
}

export function AgentDetailPage({ params }: { params: { agentId: string } }) {
  const { activeBusinessId } = useBusiness();
  const role = roles.includes(params.agentId as AgentRole) ? params.agentId as AgentRole : null;
  const [agent, setAgent] = useState<AgentConfig | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [autonomy, setAutonomy] = useState<AutonomyMode>("manual");
  const [instructions, setInstructions] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!role) return;
    try {
      const value = await aiWorkforceApi.agents.get(activeBusinessId, role);
      setAgent(value); setDisplayName(value.display_name); setAutonomy(value.autonomy_mode);
      setInstructions(value.custom_instructions ?? ""); setSelected(value.capabilities.map((item) => item.key)); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load this agent."); }
  }, [activeBusinessId, role]);
  useEffect(() => { void load(); }, [load]);

  if (!role) return <Card><div className="empty"><Bot /><h3>Agent not found</h3><Link href="/agents" className="btn btn-green">Back to AI employees</Link></div></Card>;
  if (error && !agent) return <ErrorState message={error} retry={() => void load()} />;
  if (!agent) return <div className="empty"><RefreshCw className="spin" /><h3>Loading agent configuration</h3></div>;

  const save = async () => {
    setSaving(true); setError(""); setNotice("");
    try {
      await aiWorkforceApi.agents.update(activeBusinessId, role, { display_name: displayName, autonomy_mode: autonomy, custom_instructions: instructions || null, capabilities: selected });
      await load(); setNotice("Agent configuration saved. Server policy remains authoritative.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save configuration."); }
    finally { setSaving(false); }
  };

  const reset = async () => {
    setSaving(true); setError(""); setNotice("");
    try {
      await aiWorkforceApi.agents.reset(activeBusinessId, role);
      await load(); setNotice("Agent configuration reset to server defaults.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to reset configuration."); }
    finally { setSaving(false); }
  };

  return <>
    <div className="back-row"><Link href="/agents"><ArrowLeft /> AI employees</Link></div>
    <PageHeader eyebrow="AI employee configuration" title={agent.display_name} subtitle={agent.role_description} action={<div className="toolbar"><Badge tone={agent.enabled ? "success" : "neutral"}>{agent.enabled ? "Active" : "Disabled"}</Badge><Link href={`/agents/${agent.role}/activity`} className="btn btn-soft"><Activity /> View activity</Link></div>} />
    {notice && <div className="ai-banner"><Check />{notice}</div>}{error && <div className="risk-note"><AlertCircle /><p>{error}</p></div>}
    <div className="grid agent-detail-grid"><Card className="agent-profile-card"><div className="agent-profile-icon"><Bot /></div><div className="eyebrow">Role</div><h2>{agent.role.replaceAll("_", " ")}</h2><div className="agent-health"><div><strong>{agent.metrics.execution_count}</strong><span>Executions</span></div><div><strong>{agent.metrics.failed_count}</strong><span>Failed</span></div><div><strong>{agent.metrics.pending_approval_count}</strong><span>Pending approvals</span></div></div></Card><Card><SectionTitle title="Performance" action={<Target />} /><div className="stat-row"><span>Completed</span><strong>{agent.metrics.completed_count}</strong></div><div className="stat-row"><span>Average duration</span><strong>{agent.metrics.average_duration_ms === null ? "No data" : `${agent.metrics.average_duration_ms} ms`}</strong></div><div className="stat-row"><span>Proposed actions</span><strong>{agent.metrics.proposed_action_count}</strong></div><div className="stat-row"><span>Tokens</span><strong>{agent.metrics.input_tokens + agent.metrics.output_tokens}</strong></div></Card></div>
    <div className="grid split-grid"><Card><SectionTitle title="Configuration" action={<Settings2 />} /><div className="field"><label>Display name</label><input value={displayName} maxLength={100} onChange={(event) => setDisplayName(event.target.value)} /></div><div className="field"><label>Working mode</label><select value={autonomy} onChange={(event) => setAutonomy(event.target.value as AutonomyMode)}><option value="manual">Suggest</option><option value="supervised">Assist with approval</option><option value="autonomous">Operate within policy</option></select></div><div className={`autonomy-explainer ${autonomy}`}><Gauge /><div><strong>{autonomyLabels[autonomy]}</strong><p>{autonomyCopy[autonomy]}</p></div></div><div className="field" style={{ marginTop: 18 }}><label>Business preferences</label><textarea value={instructions} maxLength={2000} onChange={(event) => setInstructions(event.target.value)} placeholder="Tone and business preferences only. Do not enter secrets." /><span className="subtle">{instructions.length}/2000 · Cannot override system safety, policy, or tenant boundaries.</span></div><div className="toolbar"><Button variant="green" onClick={() => void save()} disabled={saving || !displayName.trim()}><Save /> {saving ? "Saving…" : "Save configuration"}</Button><Button onClick={() => void reset()} disabled={saving}><RefreshCw /> Reset defaults</Button></div></Card><Card><SectionTitle title="Allowed skills" action={<ShieldCheck />} /><div className="permission-list">{agent.default_capabilities.map((capability) => <label className="permission" key={capability}>{capability.replaceAll("_", " ")}<input type="checkbox" checked={selected.includes(capability)} onChange={() => setSelected((current) => current.includes(capability) ? current.filter((item) => item !== capability) : [...current, capability])} /></label>)}</div><div className="risk-note"><ShieldCheck /><div><strong>Human oversight remains active</strong><p>Skill selection can only narrow the role’s server allowlist. It never weakens mandatory approval, spend, communication, or destructive-action policy.</p></div></div></Card></div>
  </>;
}

export function AgentActivityPage({ params }: { params?: { agentId?: string } }) {
  const { activeBusinessId } = useBusiness();
  const initialRole = roles.includes(params?.agentId as AgentRole) ? params?.agentId as AgentRole : "all";
  const [role, setRole] = useState<AgentRole | "all">(initialRole);
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<AgentActivity[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<AgentActivity | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const value = await aiWorkforceApi.agents.activity(activeBusinessId, { page, pageSize: 25, role: role === "all" ? undefined : role, status: status === "all" ? undefined : status }, signal);
      setItems(value.items); setTotal(value.total); setError("");
    } catch (reason) { if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Unable to load agent activity."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [activeBusinessId, page, role, status]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  const totalPages = Math.max(1, Math.ceil(total / 25));
  const roleOptions = useMemo(() => roles, []);
  return <>
    <div className="back-row"><Link href="/agents"><ArrowLeft /> AI employees</Link></div>
    <PageHeader eyebrow="AI team governance" title="Agent Activity" subtitle="Durable execution summaries, safe metadata, proposals, approvals, and failures—never hidden reasoning." action={<Badge tone="success"><Activity /> Execution ledger</Badge>} />
    {error && <ErrorState message={error} retry={() => void load()} />}
    <Card className="table-card" pad={false}><div className="table-toolbar"><div className="filters"><select className="business-select" value={role} onChange={(event) => { setRole(event.target.value as AgentRole | "all"); setPage(1); }}><option value="all">All agents</option>{roleOptions.map((item) => <option value={item} key={item}>{item.replaceAll("_", " ")}</option>)}</select><select className="business-select" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="all">All statuses</option><option value="completed">Completed</option><option value="needs_approval">Needs approval</option><option value="failed">Failed</option><option value="running">Running</option><option value="blocked">Blocked</option></select></div><span className="subtle">{total} executions</span></div><div className="table-scroll"><table><thead><tr><th>Time</th><th>Agent</th><th>Task</th><th>Trigger</th><th>Duration</th><th>Status</th></tr></thead><tbody>{items.map((item) => <tr key={item.id} onClick={() => setSelected(item)} className="clickable-row"><td>{new Date(item.created_at).toLocaleString()}</td><td><strong>{item.role.replaceAll("_", " ")}</strong></td><td>{item.task_summary}</td><td>{item.trigger}</td><td>{item.duration_ms === null ? "—" : `${item.duration_ms} ms`}</td><td><Badge tone={tone(item.status)}>{item.status.replaceAll("_", " ")}</Badge></td></tr>)}</tbody></table>{loading && <div className="empty compact-empty"><RefreshCw className="spin" /><p>Loading activity…</p></div>}{!loading && !items.length && <div className="empty"><Activity /><h3>No matching activity</h3><p>Change the filters or submit a Command Center request.</p></div>}</div><div className="table-toolbar"><Button className="btn-sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</Button><span className="subtle">Page {page} of {totalPages}</span><Button className="btn-sm" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>Next</Button></div></Card>
    {selected && <Card className="card-pad" style={{ marginTop: 16 }}><SectionTitle title={`${selected.role.replaceAll("_", " ")} execution detail`} action={<Button className="btn-sm" onClick={() => setSelected(null)}>Close</Button>} /><p>{selected.summary ?? (selected.failure_code ? `Safe failure: ${selected.failure_code}` : "No terminal summary yet.")}</p><div className="grid split-grid"><div><div className="stat-row"><span>Input tokens</span><strong>{selected.input_tokens ?? "—"}</strong></div><div className="stat-row"><span>Output tokens</span><strong>{selected.output_tokens ?? "—"}</strong></div><div className="stat-row"><span>Delegation depth</span><strong>{selected.delegation_depth}</strong></div></div><div>{selected.proposed_actions.map((action) => <div className="list-row" key={action.id}><div className="row-main"><div className="row-title">{action.description}</div><div className="row-copy">{action.action_type.replaceAll("_", " ")} · {action.risk_level} risk</div></div>{action.approval ? <Link href="/approvals" className="btn btn-sm btn-secondary">Approval</Link> : <Badge tone={tone(action.status)}>{action.status}</Badge>}</div>)}{!selected.proposed_actions.length && <p className="subtle">No actions proposed.</p>}</div></div></Card>}
  </>;
}
