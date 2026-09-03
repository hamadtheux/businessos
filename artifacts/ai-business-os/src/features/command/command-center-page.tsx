import { useCallback, useEffect, useState } from "react";
import { Link } from "wouter";
import { AlertCircle, Bot, Check, Clock3, Database, RefreshCw, Send, ShieldCheck, Sparkles, XCircle } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, PageHeader, SectionTitle } from "@/components/product-ui";
import { isBusinessFeatureContentVisible, isBusinessFeatureEnabled } from "@/lib/business-features";
import { aiWorkforceApi, type AICommand, type DailyBrief, type SuggestedCommand } from "@/services/ai-workforce";
import { humanizeApiError } from "@/services/api-client";

const roleLabel = (role: string) =>
  ({ business_manager: "Business Manager", cmo: "CMO", sales: "Sales", support: "Support", operations: "Operations", analytics: "Analytics" })[role] ?? role;

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  if (status === "completed") return "success";
  if (["needs_approval", "running", "queued"].includes(status)) return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}

export function CommandCenterPage() {
  const { activeBusiness, activeBusinessId } = useBusiness();
  const initialQuery = new URLSearchParams(window.location.search).get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [result, setResult] = useState<AICommand | null>(null);
  const [history, setHistory] = useState<AICommand[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestedCommand[]>([]);
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [loading, setLoading] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!activeBusinessId) return;
    setBooting(true);
    try {
      const [nextSuggestions, nextHistory, nextBrief] = await Promise.all([
        aiWorkforceApi.commands.suggestions(activeBusinessId, signal),
        aiWorkforceApi.commands.list(activeBusinessId, 1, signal),
        aiWorkforceApi.commands.dailyBrief(activeBusinessId, signal),
      ]);
      setSuggestions(nextSuggestions);
      setHistory(nextHistory.items);
      setBrief(nextBrief);
      setError("");
    } catch (reason) {
      if (signal?.aborted) return;
      setError(humanizeApiError(reason, "The Command Center could not load. Try again."));
    } finally {
      if (!signal?.aborted) setBooting(false);
    }
  }, [activeBusinessId]);

  useEffect(() => {
    const controller = new AbortController();
    setSuggestions([]);
    setHistory([]);
    setBrief(null);
    setResult(null);
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const ask = useCallback(async (text: string) => {
    const command = text.trim();
    if (!command || loading || !activeBusinessId) return;
    setQuery(command);
    setLoading(true);
    setError("");
    try {
      const value = await aiWorkforceApi.commands.execute(activeBusinessId, command);
      setResult(value);
      const nextHistory = await aiWorkforceApi.commands.list(activeBusinessId);
      setHistory(nextHistory.items);
    } catch (reason) {
      setError(humanizeApiError(reason, "The command failed safely. No action was dispatched."));
    } finally {
      setLoading(false);
    }
  }, [activeBusinessId, loading]);

  useEffect(() => {
    if (initialQuery) void ask(initialQuery);
    // Consume the URL handoff only on entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openHistory = async (commandId: string) => {
    if (!activeBusinessId) return;
    setLoading(true);
    setError("");
    try {
      setResult(await aiWorkforceApi.commands.get(activeBusinessId, commandId));
    } catch (reason) {
      setError(humanizeApiError(reason, "We couldn't load that command."));
    } finally {
      setLoading(false);
    }
  };

  const cancel = async (commandId: string) => {
    if (!activeBusinessId) return;
    setLoading(true);
    setError("");
    try {
      const canceled = await aiWorkforceApi.commands.cancel(activeBusinessId, commandId);
      setResult(canceled);
      await load();
    } catch (reason) {
      setError(humanizeApiError(reason, "Only queued commands can be canceled."));
    } finally {
      setLoading(false);
    }
  };

  const schedulingEnabled = isBusinessFeatureEnabled(activeBusiness, "scheduling");
  const visibleSuggestions = suggestions.filter((item) =>
    isBusinessFeatureContentVisible(activeBusiness, item.command),
  );
  const visibleHistory = history.filter((item) =>
    isBusinessFeatureContentVisible(activeBusiness, item.command),
  );
  const visibleBriefSections =
    brief?.sections
      .map((section) => ({
        ...section,
        facts: section.facts.filter((fact) =>
          isBusinessFeatureContentVisible(activeBusiness, fact),
        ),
      }))
      .filter((section) => section.facts.length) ?? [];
  const visiblePriorities =
    brief?.recommended_priorities.filter((priority) =>
      isBusinessFeatureContentVisible(activeBusiness, priority),
    ) ?? [];

  return <>
    <PageHeader eyebrow="Business Manager" title="AI Command Center" subtitle="Ask a business question or give your governed AI team a bounded task." />
    {error && <Card className="card-pad" style={{ maxWidth: 930, marginBottom: 14 }}><div className="ai-banner"><AlertCircle />{error}</div><Button className="btn-sm" onClick={() => void load()}><RefreshCw /> Retry</Button></Card>}
    <Card className="card-pad" style={{ maxWidth: 930 }}>
      <div className="command-input-shell"><Sparkles size={17} color="#1268F3" /><input value={query} maxLength={4000} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void ask(query); }} placeholder={schedulingEnabled ? "Ask about revenue, customers, orders, appointments, or give your AI team a task..." : "Ask about revenue, customers, orders, or give your AI team a task..."} data-testid="input-command-center" disabled={loading} /><Button variant="green" onClick={() => void ask(query)} disabled={loading || !query.trim()} data-testid="button-send-command">{loading ? <RefreshCw className="spin" /> : <Send />}</Button></div>
      <div className="eyebrow" style={{ marginTop: 20, marginBottom: 10 }}>Suggested from real business state</div>
      <div className="filters">{booting ? <span className="subtle">Loading suggestions…</span> : visibleSuggestions.map((item) => <button key={item.command} className="filter active" onClick={() => void ask(item.command)} disabled={loading} title={item.reason}>{item.command}</button>)}</div>
    </Card>
    {loading && !result && <div className="empty" style={{ maxWidth: 930, marginTop: 28 }}><RefreshCw className="spin" /><h3>Your AI team is working</h3><p>The request is bounded by server capability and delegation limits.</p></div>}
    {result && <CommandResult value={result} onRetry={() => void ask(result.command)} onCancel={() => void cancel(result.id)} />}
    {brief && <><SectionTitle title="Daily business brief" action={<Badge tone="success">Real business data</Badge>} /><Card className="card-pad" style={{ maxWidth: 930 }}><div className="grid split-grid">{visibleBriefSections.map((section) => <div key={section.key}><div className="eyebrow">{section.title}</div>{section.facts.map((fact) => <div className="list-row" key={fact}><Check size={14} /><div className="row-copy">{fact}</div></div>)}</div>)}</div><div className="eyebrow" style={{ marginTop: 16 }}>Recommended priorities</div><div className="filters">{visiblePriorities.map((priority) => <button className="filter active" key={priority} onClick={() => void ask(priority)}>{priority}</button>)}</div></Card></>}
    <SectionTitle title="Command history" action={<Badge tone="neutral">Durable history</Badge>} />
    <Card style={{ maxWidth: 930 }}><div className="list">{visibleHistory.map((item) => <button className="list-row activity-button" key={item.id} onClick={() => void openHistory(item.id)}><Clock3 /><div className="row-main"><div className="row-title">{item.command}</div><div className="row-copy">{roleLabel(item.route.primary_role)} · {new Date(item.created_at).toLocaleString()}</div></div><Badge tone={statusTone(item.status)}>{item.status.replaceAll("_", " ")}</Badge></button>)}{!booting && visibleHistory.length === 0 && <div className="empty compact-empty"><Sparkles /><h3>No commands yet</h3><p>Your first submitted command will appear here.</p></div>}</div></Card>
  </>;
}

function CommandResult({ value, onRetry, onCancel }: { value: AICommand; onRetry: () => void; onCancel: () => void }) {
  return <Card className="card-pad" style={{ maxWidth: 930, marginTop: 15 }}>
    <div className="ai-banner"><Bot />Routed to {roleLabel(value.route.primary_role)} · {value.route.intent.replaceAll("_", " ")}</div>
    <div className="toolbar" style={{ justifyContent: "space-between" }}><div><div className="eyebrow">Result</div><h2 style={{ fontSize: 18, maxWidth: 760, lineHeight: 1.5 }}>{value.summary ?? (value.status === "failed" ? "This command failed safely." : "The command is still processing.")}</h2></div><Badge tone={statusTone(value.status)}>{value.status.replaceAll("_", " ")}</Badge></div>
    {value.failure_code && <div className="risk-note"><XCircle /><div><strong>Safe failure</strong><p>Code: {value.failure_code}. No connector or action execution was started.</p></div></div>}
    <div className="grid split-grid" style={{ marginTop: 20 }}><div><div className="eyebrow">Data and capability route</div>{value.route.relevant_modules.map((item) => <div className="list-row" key={item}><Database size={14} /><div className="row-title">{item}</div><Check size={14} color="#4b9e61" /></div>)}{value.executions.map((execution) => execution.summary && <div className="list-row" key={execution.id}><Bot size={14} /><div className="row-main"><div className="row-title">{roleLabel(execution.role)}</div><div className="row-copy">{execution.summary}</div></div></div>)}</div><div><div className="eyebrow">Governed proposed actions</div>{value.proposed_actions.map((action) => <div className="card" style={{ padding: 12, marginBottom: 10 }} key={action.id}><div className="toolbar"><Badge tone={action.requires_approval ? "warning" : "neutral"}>{action.risk_level} risk</Badge><Badge tone={statusTone(action.status)}>{action.status.replaceAll("_", " ")}</Badge></div><div className="row-title" style={{ marginTop: 8 }}>{action.description}</div><p className="row-copy">{action.action_type.replaceAll("_", " ")}</p>{action.approval && <Link href="/approvals" className="btn btn-sm btn-secondary"><ShieldCheck /> Open approval queue</Link>}</div>)}{!value.proposed_actions.length && <div className="empty compact-empty"><ShieldCheck /><p>No business action was proposed.</p></div>}</div></div>
    {value.status === "failed" && <Button className="btn-sm" onClick={onRetry}><RefreshCw /> Retry command</Button>}
    {value.status === "queued" && <Button className="btn-sm" onClick={onCancel}><XCircle /> Cancel queued command</Button>}
    <div className="risk-note" style={{ marginTop: 14 }}><ShieldCheck /><div><strong>Policy remains in control</strong><p>External dispatch requires a configured tenant connector and is revalidated against approval, capability, connection, and spend policy before execution.</p></div></div>
  </Card>;
}
