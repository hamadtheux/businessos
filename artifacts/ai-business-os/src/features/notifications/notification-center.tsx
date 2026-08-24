import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Check, ChevronRight, CircleAlert, Sparkles, X } from "lucide-react";
import { useLocation } from "wouter";
import { useBusiness } from "@/business-context";
import { Badge, Button } from "@/components/product-ui";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import { operationsApi } from "@/services/operations";

const destination = (type: string | null, schedulingEnabled: boolean) => {
  if (type === "order") return "/orders";
  if (type === "crm_lead") return "/crm";
  if (type === "opportunity") return "/opportunities";
  if (type === "appointment") return schedulingEnabled ? "/scheduling" : "/dashboard";
  if (type === "conversation") return "/conversations";
  return "/dashboard";
};

export function NotificationCenter({ onClose }: { onClose: () => void }) {
  const { activeBusiness, activeBusinessId } = useBusiness();
  const client = useQueryClient();
  const [, setLocation] = useLocation();
  const query = useQuery({ queryKey: ["operations", activeBusinessId, "notifications"], queryFn: ({ signal }) => operationsApi.notifications.list(activeBusinessId, false, signal), enabled: Boolean(activeBusinessId) });
  const refresh = () => client.invalidateQueries({ queryKey: ["operations", activeBusinessId, "notifications"] });
  const markAll = useMutation({ mutationFn: () => operationsApi.notifications.readAll(activeBusinessId), onSuccess: () => void refresh() });
  const markRead = useMutation({ mutationFn: (id: string) => operationsApi.notifications.read(activeBusinessId, id), onSuccess: () => void refresh() });
  const schedulingEnabled = isBusinessFeatureEnabled(activeBusiness, "scheduling");
  const notifications = (query.data?.items ?? []).filter(
    (item) => item.related_entity_type !== "appointment" || schedulingEnabled,
  );
  const unread = notifications.filter((item) => !item.read).length;
  const open = (id: string, entityType: string | null) => {
    markRead.mutate(id);
    onClose();
    setLocation(destination(entityType, schedulingEnabled));
  };
  return <div className="drawer notification-drawer" onMouseDown={onClose}><aside className="drawer-panel" onMouseDown={(event) => event.stopPropagation()}><div className="drawer-head"><div><div className="eyebrow">Business alerts</div><h2>Notifications</h2><p className="subtle">{unread} unread in this business</p></div><button className="close-btn" onClick={onClose}><X /></button></div><div className="notification-toolbar"><Button variant="secondary" className="btn-sm" onClick={() => markAll.mutate()} disabled={!unread || markAll.isPending}><Check /> Mark all read</Button></div><div className="notification-list">{notifications.map((item) => { const Icon = item.priority === "high" ? CircleAlert : item.category.includes("report") ? Sparkles : Bell; return <button className={`notification-item ${item.read ? "read" : "unread"}`} onClick={() => open(item.id, item.related_entity_type)} key={item.id}><span className={`notification-icon ${item.priority}`}><Icon /></span><div className="row-main"><div className="notification-title-row"><strong>{item.title}</strong>{!item.read && <i />}</div><p>{item.message}</p><div className="notification-meta"><Badge tone={item.priority === "high" ? "danger" : item.priority === "medium" ? "warning" : "neutral"}>{item.priority}</Badge><span>{item.category} · {new Date(item.created_at).toLocaleString()}</span></div></div><ChevronRight /></button>; })}</div>{query.isLoading && <div className="empty"><p>Loading notifications…</p></div>}{query.isError && <div className="empty"><CircleAlert /><h3>Notifications unavailable</h3><Button onClick={() => void query.refetch()}>Try again</Button></div>}{!query.isLoading && !notifications.length && <div className="empty"><Bell /><h3>You’re all caught up</h3><p>Internal business alerts will appear here.</p></div>}</aside></div>;
}
