import { Bell, Check, ChevronRight, CircleAlert, PlugZap, Sparkles, X } from "lucide-react";
import { useLocation } from "wouter";
import { Badge, Button } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";

export function NotificationCenter({ onClose }: { onClose: () => void }) {
  const { data, update } = useWorkspaceData();
  const [, setLocation] = useLocation();
  const unread = data.notifications.filter((item) => !item.read).length;
  const markAll = () => update((current) => ({ ...current, notifications: current.notifications.map((item) => ({ ...item, read: true })) }));
  const open = (id: string, href: string) => {
    update((current) => ({ ...current, notifications: current.notifications.map((item) => item.id === id ? { ...item, read: true } : item) }));
    onClose();
    setLocation(href);
  };
  return <div className="drawer notification-drawer" onMouseDown={onClose}><aside className="drawer-panel" onMouseDown={(event) => event.stopPropagation()}><div className="drawer-head"><div><div className="eyebrow">Business alerts</div><h2>Notifications</h2><p className="subtle">{unread} unread across this business</p></div><button className="close-btn" onClick={onClose}><X /></button></div><div className="notification-toolbar"><Button variant="secondary" className="btn-sm" onClick={markAll}><Check /> Mark all read</Button></div><div className="notification-list">{data.notifications.map((item) => { const Icon = item.type === "Integration disconnected" ? PlugZap : item.priority === "High" ? CircleAlert : item.type === "AI report ready" ? Sparkles : Bell; return <button className={`notification-item ${item.read ? "read" : "unread"}`} onClick={() => open(item.id, item.href)} key={item.id}><span className={`notification-icon ${item.priority.toLowerCase()}`}><Icon /></span><div className="row-main"><div className="notification-title-row"><strong>{item.title}</strong>{!item.read && <i />}</div><p>{item.detail}</p><div className="notification-meta"><Badge tone={item.priority === "High" ? "danger" : item.priority === "Medium" ? "warning" : "neutral"}>{item.priority}</Badge><span>{item.type} · {item.timestamp}</span></div></div><ChevronRight /></button>; })}</div>{!data.notifications.length && <div className="empty"><Bell /><h3>You’re all caught up</h3><p>Important AI, workflow, integration, and customer alerts will appear here.</p></div>}</aside></div>;
}

