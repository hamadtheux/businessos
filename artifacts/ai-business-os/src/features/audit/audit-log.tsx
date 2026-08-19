import { useMemo, useState } from "react";
import { Activity, Bot, CheckCircle2, Filter, Search, User } from "lucide-react";
import { Badge, Card, PageHeader } from "@/components/product-ui";
import { useBusiness } from "@/business-context";
import { useWorkspaceData } from "@/hooks/use-workspace-data";

export function AuditLogPage() {
  const { activeBusiness } = useBusiness();
  const { data } = useWorkspaceData();
  const [actor, setActor] = useState("All actors");
  const [status, setStatus] = useState("All statuses");
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => data.audit.filter((item) => (actor === "All actors" || item.actorType === actor) && (status === "All statuses" || item.status === status) && `${item.actor} ${item.action} ${item.entity}`.toLowerCase().includes(search.toLowerCase())), [data.audit, actor, status, search]);
  return <>
    <PageHeader eyebrow="Workspace governance" title="Audit Log" subtitle={`A clear history of important human, AI, and system activity in ${activeBusiness?.name}.`} />
    <Card className="table-card" pad={false}>
      <div className="table-toolbar"><div className="toolbar"><div className="search-box"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Action, agent, or entity" /></div><Filter /></div><div className="toolbar"><select className="business-select" value={actor} onChange={(event) => setActor(event.target.value)}><option>All actors</option><option>AI agent</option><option>Human user</option><option>System</option></select><select className="business-select" value={status} onChange={(event) => setStatus(event.target.value)}><option>All statuses</option><option>Completed</option><option>Pending</option><option>Failed</option></select><select className="business-select"><option>Today</option><option>Last 7 days</option><option>Last 30 days</option></select></div></div>
      <div className="table-scroll"><table className="audit-table"><thead><tr><th>Timestamp</th><th>User / AI agent</th><th>Action</th><th>Entity</th><th>Before / after</th><th>Business</th><th>Status</th><th>Approval</th><th>Source</th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id}><td>{item.timestamp}</td><td><div className="actor-cell"><span className={`actor-icon ${item.actorType === "AI agent" ? "ai" : ""}`}>{item.actorType === "AI agent" ? <Bot /> : item.actorType === "Human user" ? <User /> : <Activity />}</span><div><strong>{item.actor}</strong><div className="row-copy">{item.actorType}</div></div></div></td><td><strong>{item.action}</strong></td><td>{item.entity}</td><td><div className="change-summary">{item.before && <span>{item.before}</span>}{item.after && <><span className="change-arrow">→</span><strong>{item.after}</strong></>}</div></td><td>{activeBusiness?.name}</td><td><Badge tone={item.status === "Completed" ? "success" : item.status === "Failed" ? "danger" : "warning"}>{item.status === "Completed" && <CheckCircle2 />} {item.status}</Badge></td><td>{item.approval ?? "—"}</td><td>{item.source}</td></tr>)}</tbody></table>{!filtered.length && <div className="empty"><Activity /><h3>No activity matches these filters</h3><p>Adjust the actor, status, or search term.</p></div>}</div>
    </Card>
  </>;
}

