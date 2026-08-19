import { useState } from "react";
import { ArrowRight, Check, ClipboardCheck, Lightbulb, Pencil, RefreshCw, Sparkles, X } from "lucide-react";
import { Badge, Button, Card, Modal, PageHeader } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { WorkspaceApproval } from "@/types/workspace";

export function ApprovalsPage() {
  const { data, update, recordAudit } = useWorkspaceData();
  const [filter, setFilter] = useState<WorkspaceApproval["status"]>("Pending");
  const [editing, setEditing] = useState<WorkspaceApproval | null>(null);
  const act = (item: WorkspaceApproval, status: "Approved" | "Rejected") => {
    update((current) => ({ ...current, approvals: current.approvals.map((approval) => approval.id === item.id ? { ...approval, status } : approval) }));
    recordAudit({ actor: "Alexandra Andria", actorType: "Human user", action: `${status} AI action`, entity: item.title, before: item.status, after: status, status: "Completed", approval: `Approval #${item.id}`, source: "Approval Center" });
  };
  const items = data.approvals.filter((item) => item.status === filter);
  return <>
    <PageHeader eyebrow="Human oversight" title="Approval Center" subtitle="High-impact actions stay visible, editable, and under your control." action={<Badge tone="warning">{data.approvals.filter((item) => item.status === "Pending").length} pending</Badge>} />
    <div className="tabs">{(["Pending", "Approved", "Rejected", "Expired"] as const).map((item) => <button className={`tab ${filter === item ? "active" : ""}`} onClick={() => setFilter(item)} key={item}>{item} <span>{data.approvals.filter((approval) => approval.status === item).length}</span></button>)}</div>
    <div className="approval-list">{items.map((item) => <Card className="approval-card" key={item.id}><div className="approval-top"><div><div className="eyebrow">{item.agent}</div><h2>{item.title}</h2></div><Badge tone={item.status === "Pending" ? "warning" : item.status === "Approved" ? "success" : "danger"}>{item.status}</Badge></div><div className="approval-reason"><strong>AI reasoning</strong><p>{item.reason}</p><span>Potential impact · {item.impact}</span></div>{item.status === "Pending" && <div className="toolbar"><Button variant="green" className="btn-sm" onClick={() => act(item, "Approved")}><Check /> Approve</Button><Button className="btn-sm" onClick={() => setEditing(item)}><Pencil /> Edit</Button><Button variant="danger" className="btn-sm" onClick={() => act(item, "Rejected")}><X /> Reject</Button></div>}</Card>)}{!items.length && <Card><div className="empty"><ClipboardCheck /><h3>No {filter.toLowerCase()} actions</h3><p>AI actions will appear here when they need your attention.</p></div></Card>}</div>
    {editing && <Modal title="Edit proposed action" description="Adjust the prototype action before making an approval decision." onClose={() => setEditing(null)}><div className="field"><label>Action</label><textarea value={editing.title} onChange={(event) => setEditing({ ...editing, title: event.target.value })} /></div><div className="field" style={{ marginTop: 14 }}><label>AI reasoning</label><textarea value={editing.reason} onChange={(event) => setEditing({ ...editing, reason: event.target.value })} /></div><div className="modal-foot"><Button onClick={() => setEditing(null)}>Cancel</Button><Button variant="green" onClick={() => { update((current) => ({ ...current, approvals: current.approvals.map((item) => item.id === editing.id ? editing : item) })); setEditing(null); }}><Check /> Save edit</Button></div></Modal>}
  </>;
}

export function OpportunitiesPage() {
  const { data, update } = useWorkspaceData();
  const review = (id: number) => update((current) => ({ ...current, opportunities: current.opportunities.map((item) => item.id === id ? { ...item, reviewed: true } : item) }));
  const create = (id: number) => {
    const item = data.opportunities.find((opportunity) => opportunity.id === id);
    if (!item) return;
    update((current) => ({ ...current, opportunities: current.opportunities.map((opportunity) => opportunity.id === id ? { ...opportunity, reviewed: true } : opportunity), approvals: [{ id: Date.now(), agent: "AI Business Manager", title: `Execute: ${item.title}`, reason: item.copy, status: "Pending", impact: item.impact }, ...current.approvals] }));
  };
  return <><PageHeader eyebrow="Intelligence" title="AI Opportunities" subtitle="The moments your AI team believes are worth your attention." action={<Button onClick={() => update((current) => ({ ...current, opportunities: current.opportunities.map((item) => ({ ...item, reviewed: false })) }))}><RefreshCw /> Refresh insights</Button>} /><div className="grid opportunity-grid">{data.opportunities.map((item) => <Card className={`opportunity ${item.category === "Marketing" ? "green" : item.category === "Problem" ? "brown" : ""}`} key={item.id}><div className="opportunity-head"><div className="eyebrow">{item.category}</div><Badge tone="info">Impact · {item.impact}</Badge></div><h2>{item.title}</h2><p className="opportunity-copy">{item.copy}</p><div className="toolbar">{item.reviewed ? <Badge tone="success"><Check /> Reviewed</Badge> : <Button className="btn-sm" onClick={() => review(item.id)}>Review</Button>}<Button variant="primary" className="btn-sm" onClick={() => create(item.id)}><Sparkles /> Create action <ArrowRight /></Button></div></Card>)}</div></>;
}

