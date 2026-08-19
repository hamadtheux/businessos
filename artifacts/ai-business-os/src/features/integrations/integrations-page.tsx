import { useState } from "react";
import { Archive, Calendar, Check, Database, Globe2, Link2, Mail, MessageCircle, RefreshCw, ShieldCheck, ShoppingBag, Unplug, Users } from "lucide-react";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { WorkspaceIntegration } from "@/types/workspace";

const icons = { WhatsApp: MessageCircle, Instagram: Globe2, Facebook: Globe2, Email: Mail, Shopify: ShoppingBag, Stripe: Archive, "Google Calendar": Calendar, HubSpot: Users };

export function IntegrationsPage() {
  const { data, update, recordAudit } = useWorkspaceData();
  const [selected, setSelected] = useState<WorkspaceIntegration | null>(null);
  const [connectTarget, setConnectTarget] = useState<WorkspaceIntegration | null>(null);
  const connect = (integration: WorkspaceIntegration) => {
    const connected: WorkspaceIntegration = { ...integration, connected: true, account: `${integration.name.toLowerCase().replace(" ", ".")}@workspace.demo`, connectedDate: "Just now", permissions: ["Read business records", "Sync status changes"], dataAvailable: ["Activity", "Customer context", "Performance"], lastSync: "Just now", syncStatus: "Healthy" };
    update((current) => ({ ...current, integrations: current.integrations.map((item) => item.id === integration.id ? connected : item) }));
    recordAudit({ actor: "Alexandra Andria", actorType: "Human user", action: "Connected prototype integration", entity: integration.name, after: "Connection ready", status: "Completed", source: "Integrations" });
    setConnectTarget(null); setSelected(connected);
  };
  const disconnect = (integration: WorkspaceIntegration) => {
    const disconnected: WorkspaceIntegration = { ...integration, connected: false, account: "Not connected", connectedDate: "—", permissions: [], dataAvailable: [], lastSync: "Never", syncStatus: "Disconnected" };
    update((current) => ({ ...current, integrations: current.integrations.map((item) => item.id === integration.id ? disconnected : item) }));
    recordAudit({ actor: "Alexandra Andria", actorType: "Human user", action: "Disconnected prototype integration", entity: integration.name, before: integration.account, after: "Disconnected", status: "Completed", source: "Integrations" });
    setSelected(disconnected);
  };
  const sync = (integration: WorkspaceIntegration) => {
    const syncing = { ...integration, syncStatus: "Syncing" as const, lastSync: "Syncing now…" };
    setSelected(syncing);
    update((current) => ({ ...current, integrations: current.integrations.map((item) => item.id === integration.id ? syncing : item) }));
    window.setTimeout(() => {
      const healthy = { ...syncing, syncStatus: "Healthy" as const, lastSync: "Just now" };
      update((current) => ({ ...current, integrations: current.integrations.map((item) => item.id === integration.id ? healthy : item) }));
      setSelected(healthy);
    }, 750);
  };
  return <>
    <PageHeader eyebrow="Workspace connections" title="Connect your tools" subtitle="Bring the systems your business already uses into one operating view." action={<Badge tone="success"><ShieldCheck /> No credentials stored in the frontend</Badge>} />
    <div className="grid integration-grid">{data.integrations.map((integration) => { const Icon = icons[integration.name as keyof typeof icons] ?? Link2; return <Card className="integration-card" key={integration.id}><div className="integration-card-top"><div className="integration-icon"><Icon /></div>{integration.connected && <Badge tone="success"><Check /> Connected</Badge>}</div><h2>{integration.name}</h2><p className="integration-desc">{integration.description}</p>{integration.connected ? <Button variant="soft" className="btn-sm" onClick={() => setSelected(integration)}>View connection <Link2 /></Button> : <Button variant="secondary" className="btn-sm" onClick={() => setConnectTarget(integration)}>Connect <Link2 /></Button>}</Card>; })}</div>
    {connectTarget && <Modal title={`Connect ${connectTarget.name}`} description="This is a safe prototype connection. Real OAuth and credentials will be handled by the future backend." onClose={() => setConnectTarget(null)}><div className="connection-steps"><div className="connection-step"><span>1</span><div><strong>Choose the business account</strong><p>The secure backend will open the provider’s authorization screen.</p></div></div><div className="connection-step"><span>2</span><div><strong>Review permissions</strong><p>You will see exactly which records and actions are requested.</p></div></div><div className="connection-step"><span>3</span><div><strong>Start the first sync</strong><p>AI Business OS will report progress and any connection issue.</p></div></div></div><div className="prototype-note">No API secret, OAuth token, or password is requested or stored by this prototype.</div><div className="modal-foot"><Button onClick={() => setConnectTarget(null)}>Cancel</Button><Button variant="green" onClick={() => connect(connectTarget)}>Connect prototype</Button></div></Modal>}
    {selected && <Modal title={selected.name} description={`${selected.account} · Connected ${selected.connectedDate}`} onClose={() => setSelected(null)}><div className="integration-detail-status"><div className="integration-icon"><Database /></div><div className="row-main"><div className="eyebrow">Connection status</div><h2>{selected.connected ? "Connected and available" : "Disconnected"}</h2><p className="subtle">Last sync · {selected.lastSync}</p></div><Badge tone={selected.syncStatus === "Healthy" ? "success" : selected.syncStatus === "Syncing" ? "warning" : "danger"}>{selected.syncStatus === "Syncing" && <RefreshCw className="spin" />} {selected.syncStatus}</Badge></div><div className="analysis-grid"><Card><SectionTitle title="Permissions" />{selected.permissions.length ? selected.permissions.map((item) => <div className="check-line" key={item}><Check /> {item}</div>) : <p className="subtle">No permissions granted.</p>}</Card><Card><SectionTitle title="Data available" />{selected.dataAvailable.length ? selected.dataAvailable.map((item) => <div className="check-line" key={item}><Check /> {item}</div>) : <p className="subtle">Connect to make prototype data available.</p>}</Card></div><div className="toolbar" style={{ marginTop: 18 }}>{selected.connected ? <><Button variant="green" onClick={() => sync(selected)} disabled={selected.syncStatus === "Syncing"}><RefreshCw /> Sync now</Button><Button onClick={() => setConnectTarget(selected)}>Reconnect</Button><Button variant="danger" onClick={() => disconnect(selected)}><Unplug /> Disconnect</Button></> : <Button variant="green" onClick={() => { setSelected(null); setConnectTarget(selected); }}>Reconnect</Button>}</div></Modal>}
  </>;
}

