import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, AlertCircle, Bot, CheckCircle2, Search, User } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, PageHeader } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import { operationsApi } from "@/services/operations";

export function AuditLogPage() {
  const { activeBusiness, activeBusinessId } = useBusiness();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const query = useQuery({ queryKey: ["operations", activeBusinessId, "audit", search, page], queryFn: ({ signal }) => operationsApi.audit(activeBusinessId, { search, page, pageSize: 25 }, signal), enabled: Boolean(activeBusinessId) });
  const pages = Math.max(1, Math.ceil((query.data?.total ?? 0) / (query.data?.page_size ?? 25)));
  return <>
    <PageHeader eyebrow="Workspace governance" title="Audit Log" subtitle={`A durable history of important human, AI, and system activity in ${activeBusiness?.name || "this business"}.`} />
    {query.isError ? <Card><div className="empty"><AlertCircle /><h3>Audit history unavailable</h3><p>{humanizeApiError(query.error, "Try again in a moment.")}</p><Button onClick={() => void query.refetch()}>Try again</Button></div></Card> : <Card className="table-card" pad={false}><div className="table-toolbar"><div className="search-box"><Search /><input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Event, action, or entity" /></div><span className="subtle">{query.data?.total ?? 0} events</span></div><div className="table-scroll"><table className="audit-table"><thead><tr><th>Timestamp</th><th>Actor</th><th>Event</th><th>Entity</th><th>Summary</th><th>Before / after</th><th>Status</th></tr></thead><tbody>{query.data?.items.map((item) => <tr key={item.id}><td>{new Date(item.created_at).toLocaleString()}</td><td><div className="actor-cell"><span className={`actor-icon ${item.actor_type === "ai" ? "ai" : ""}`}>{item.actor_type === "ai" ? <Bot /> : item.actor_type === "user" ? <User /> : <Activity />}</span><div><strong>{item.actor_type}</strong><div className="row-copy">{item.actor_user_id ? item.actor_user_id.slice(0, 8) : "service"}</div></div></div></td><td><strong>{item.event_type}</strong></td><td>{item.entity_type}{item.entity_id ? <div className="row-copy">{item.entity_id.slice(0, 8)}</div> : null}</td><td>{item.summary}</td><td><div className="change-summary">{item.before_value && <span>{item.before_value}</span>}{item.after_value && <><span className="change-arrow">→</span><strong>{item.after_value}</strong></>}</div></td><td><Badge tone={item.status === "completed" ? "success" : item.status === "failed" ? "danger" : "warning"}>{item.status === "completed" && <CheckCircle2 />} {item.status}</Badge></td></tr>)}</tbody></table>{query.isLoading && <div className="empty"><p>Loading audit history…</p></div>}{query.data && !query.data.items.length && <div className="empty"><Activity /><h3>No activity matches</h3><p>Change the search or create the first operational record.</p></div>}</div>{pages > 1 && <div className="table-toolbar"><span>Page {page} of {pages}</span><div className="toolbar"><Button className="btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button className="btn-sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button></div></div>}</Card>}
  </>;
}
