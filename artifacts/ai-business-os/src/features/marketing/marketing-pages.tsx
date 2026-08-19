import { useMemo, useState, type FormEvent } from "react";
import { ArrowRight, BarChart3, Calendar, Check, Eye, Facebook, Instagram, Linkedin, Pause, Play, Plus, Send, Sparkles, Target, Wand2 } from "lucide-react";
import { Link } from "wouter";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { Campaign, SocialPost } from "@/types/workspace";

export const cmoTabs = [
  ["Overview", "/cmo"], ["Content", "/cmo?tab=Content"], ["Calendar", "/cmo?tab=Calendar"], ["Campaigns", "/campaigns"], ["Social", "/social"], ["Competitors", "/competitors"], ["Trends", "/trends"], ["Performance", "/analytics"],
] as const;

export function CmoDepartmentNav({ active }: { active: string }) {
  return <div className="department-tabs">{cmoTabs.map(([label, href]) => <Link className={`department-tab ${active === label ? "active" : ""}`} href={href} key={label}>{label}</Link>)}</div>;
}

export function CampaignsPage() {
  const { data, update, industry } = useWorkspaceData();
  const [selected, setSelected] = useState<Campaign | null>(null);
  const [creating, setCreating] = useState(() => new URLSearchParams(window.location.search).has("new"));
  const [filter, setFilter] = useState("All");
  const campaigns = useMemo(() => data.campaigns.filter((campaign) => filter === "All" || campaign.status === filter), [data.campaigns, filter]);

  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const campaign: Campaign = {
      id: `campaign-${Date.now()}`,
      name: String(form.get("name")),
      status: "Draft",
      goal: String(form.get("goal")),
      audience: String(form.get("audience")),
      channels: form.getAll("channels").map(String),
      content: String(form.get("content")),
      schedule: String(form.get("schedule")),
      kpis: ["Reach", "Qualified responses", "Conversions"],
      performance: "Not started",
      analysis: "AI analysis will appear after the campaign has activity.",
      recommendation: "Complete the content and submit it for approval.",
    };
    update((current) => ({ ...current, campaigns: [campaign, ...current.campaigns] }));
    setCreating(false);
    setSelected(campaign);
  };

  const changeStatus = (campaign: Campaign, status: Campaign["status"]) => {
    update((current) => ({ ...current, campaigns: current.campaigns.map((item) => item.id === campaign.id ? { ...item, status } : item) }));
    setSelected({ ...campaign, status });
  };

  return <>
    <PageHeader eyebrow="AI CMO · Strategy to execution" title="Campaigns" subtitle={`Plan, approve, run, and improve ${industry === "Real Estate" ? "listing and market" : "customer and product"} campaigns.`} action={<Button variant="primary" onClick={() => setCreating(true)} data-testid="button-create-campaign"><Plus /> Create campaign</Button>} />
    <CmoDepartmentNav active="Campaigns" />
    <div className="tabs">{["All", "Draft", "Awaiting approval", "Scheduled", "Running", "Completed", "Paused"].map((item) => <button className={`tab ${filter === item ? "active" : ""}`} onClick={() => setFilter(item)} key={item}>{item} <span>{data.campaigns.filter((campaign) => item === "All" || campaign.status === item).length}</span></button>)}</div>
    <div className="grid campaign-grid">{campaigns.map((campaign) => <Card className="campaign-card" key={campaign.id}><div className="campaign-card-head"><div className="campaign-mark"><Target /></div><Badge tone={campaign.status === "Running" ? "success" : campaign.status === "Awaiting approval" ? "warning" : campaign.status === "Paused" ? "danger" : "neutral"}>{campaign.status}</Badge></div><h2>{campaign.name}</h2><p className="subtle">{campaign.goal}</p><div className="campaign-meta"><div><span>Audience</span><strong>{campaign.audience}</strong></div><div><span>Schedule</span><strong>{campaign.schedule}</strong></div></div><div className="chip-list">{campaign.channels.map((channel) => <Badge tone="info" key={channel}>{channel}</Badge>)}</div><div className="toolbar"><Button variant="soft" className="btn-sm" onClick={() => setSelected(campaign)}><Eye /> View campaign</Button>{campaign.status === "Running" && <Button className="btn-sm" onClick={() => changeStatus(campaign, "Paused")}><Pause /> Pause</Button>}{campaign.status === "Paused" && <Button variant="green" className="btn-sm" onClick={() => changeStatus(campaign, "Running")}><Play /> Resume</Button>}</div></Card>)}</div>
    {creating && <Modal title="Create campaign" description="Prepare the strategy, content, and approval-ready plan." onClose={() => setCreating(false)}><form onSubmit={create}><div className="form-grid"><div className="field full"><label>Campaign name</label><input name="name" required placeholder={industry === "Real Estate" ? "Downtown buyer guide" : "Same-day harvest"} /></div><div className="field full"><label>Goal</label><input name="goal" required placeholder="What should this campaign achieve?" /></div><div className="field full"><label>Audience</label><input name="audience" required placeholder="A specific customer or lead segment" /></div><div className="field full"><label>Content / offer</label><textarea name="content" required placeholder="Core message, assets, and offer" /></div><div className="field full"><label>Schedule</label><input name="schedule" required placeholder="Mar 22–29" /></div><div className="field full"><label>Channels</label><div className="checkbox-row">{["Instagram", "Facebook", "LinkedIn", "X", "Email", "WhatsApp"].map((channel) => <label key={channel}><input type="checkbox" name="channels" value={channel} defaultChecked={["Instagram", "Email"].includes(channel)} /> {channel}</label>)}</div></div></div><div className="modal-foot"><Button type="button" onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" type="submit">Create draft</Button></div></form></Modal>}
    {selected && <Modal wide title={selected.name} description={`${selected.status} · ${selected.schedule}`} onClose={() => setSelected(null)}><div className="campaign-detail-hero"><div><div className="eyebrow">Goal</div><h2>{selected.goal}</h2><p>{selected.audience}</p></div><Badge tone={selected.status === "Running" ? "success" : selected.status === "Awaiting approval" ? "warning" : "neutral"}>{selected.status}</Badge></div><div className="analysis-grid"><Card><div className="eyebrow">Channels</div><div className="chip-list">{selected.channels.map((channel) => <Badge tone="info" key={channel}>{channel}</Badge>)}</div></Card><Card><div className="eyebrow">Schedule</div><p className="detail-copy">{selected.schedule}</p></Card><Card><div className="eyebrow">Content</div><p className="detail-copy">{selected.content}</p></Card><Card><div className="eyebrow">KPIs</div>{selected.kpis.map((kpi) => <div className="check-line" key={kpi}><Check /> {kpi}</div>)}</Card><Card><div className="eyebrow">Performance</div><p className="detail-copy">{selected.performance}</p></Card><Card className="recommendation"><div className="eyebrow">AI analysis</div><p className="detail-copy">{selected.analysis}</p><div className="rec-copy">Recommended optimization · {selected.recommendation}</div></Card></div><div className="toolbar" style={{ marginTop: 18 }}>{selected.status === "Draft" && <Button variant="green" onClick={() => changeStatus(selected, "Awaiting approval")}><Send /> Submit for approval</Button>}{selected.status === "Awaiting approval" && <Button variant="green" onClick={() => changeStatus(selected, "Scheduled")}><Check /> Approve & schedule</Button>}{selected.status === "Scheduled" && <Button variant="green" onClick={() => changeStatus(selected, "Running")}><Play /> Start prototype campaign</Button>}</div></Modal>}
  </>;
}

const platformIcons = { Instagram, Facebook, LinkedIn: Linkedin };

export function SocialManagementPage() {
  const { data, update } = useWorkspaceData();
  const [status, setStatus] = useState("Scheduled");
  const [selected, setSelected] = useState<SocialPost | null>(null);
  const posts = data.socialPosts.filter((post) => post.status === status);
  const approve = (post: SocialPost) => {
    const next: SocialPost = { ...post, status: "Scheduled", schedule: post.schedule === "Unscheduled" ? "Tomorrow · 10:00 AM" : post.schedule };
    update((current) => ({ ...current, socialPosts: current.socialPosts.map((item) => item.id === post.id ? next : item) }));
    setSelected(next);
  };

  return <>
    <PageHeader eyebrow="AI CMO · Distribution" title="Social Management" subtitle="A truthful prototype view of drafts, approvals, schedules, and published performance." action={<Link href="/cmo?tab=Content" className="btn btn-primary"><Wand2 /> Create post</Link>} />
    <CmoDepartmentNav active="Social" />
    <div className="social-channel-strip">{["Instagram", "Facebook", "LinkedIn", "X"].map((platform) => { const Icon = platformIcons[platform as keyof typeof platformIcons] ?? Sparkles; const connected = data.integrations.some((item) => item.name === platform && item.connected); return <Card key={platform} className="social-channel"><Icon /><div className="row-main"><strong>{platform}</strong><span>{connected ? "Prototype connection ready" : "Not connected"}</span></div><Badge tone={connected ? "success" : "neutral"}>{connected ? "Connected" : "Prototype"}</Badge></Card>; })}</div>
    <div className="tabs">{["Scheduled", "Published", "Draft", "Needs approval"].map((item) => <button className={`tab ${status === item ? "active" : ""}`} onClick={() => setStatus(item)} key={item}>{item} <span>{data.socialPosts.filter((post) => post.status === item).length}</span></button>)}</div>
    <div className="grid social-post-grid">{posts.map((post) => { const Icon = platformIcons[post.platform as keyof typeof platformIcons] ?? Sparkles; return <Card className="social-post" key={post.id}><div className="social-post-head"><span className="platform-icon"><Icon /></span><div><strong>{post.platform}</strong><div className="row-copy">{post.schedule}</div></div><Badge tone={post.status === "Published" ? "success" : post.status === "Needs approval" ? "warning" : "neutral"}>{post.status}</Badge></div><p className="social-copy">{post.content}</p>{post.status === "Published" && <div className="social-metrics"><div><strong>{post.reach.toLocaleString()}</strong><span>Reach</span></div><div><strong>{post.engagement}%</strong><span>Engagement</span></div><div><strong>{post.clicks}</strong><span>Clicks</span></div></div>}<div className="toolbar"><Button variant="soft" className="btn-sm" onClick={() => setSelected(post)}><Eye /> View detail</Button>{post.status === "Needs approval" && <Button variant="green" className="btn-sm" onClick={() => approve(post)}><Check /> Approve</Button>}</div></Card>; })}</div>
    {selected && <Modal title={`${selected.platform} post`} description={`${selected.status} · ${selected.schedule}`} onClose={() => setSelected(null)}><div className="social-preview"><div className="social-preview-brand"><span className="platform-icon"><Sparkles /></span><div><strong>Your business</strong><span>AI CMO prepared</span></div></div><p>{selected.content}</p></div><div className="grid three-grid social-detail-stats"><Card><strong>{selected.reach.toLocaleString()}</strong><span>Reach</span></Card><Card><strong>{selected.engagement}%</strong><span>Engagement</span></Card><Card><strong>{selected.clicks}</strong><span>Clicks</span></Card></div><Card className="recommendation"><SectionTitle title="AI analysis" action={<BarChart3 />} /><p className="detail-copy">{selected.analysis}</p></Card>{selected.status === "Needs approval" && <div className="modal-foot"><Button variant="green" onClick={() => approve(selected)}><Check /> Approve & schedule</Button></div>}</Modal>}
  </>;
}
