import { useMemo, useState, type FormEvent } from "react";
import {
  ArrowRight,
  BarChart3,
  Bookmark,
  Check,
  Eye,
  Globe2,
  Lightbulb,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Target,
  TrendingUp,
  Wand2,
  X,
} from "lucide-react";
import { useLocation } from "wouter";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { Competitor } from "@/types/workspace";
import { slug } from "@/lib/product-utils";

export function CompetitorIntelligencePage() {
  const { data, update, recordAudit, industry } = useWorkspaceData();
  const [, setLocation] = useLocation();
  const [selected, setSelected] = useState<Competitor | null>(null);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");

  const analyze = (competitor: Competitor) => {
    update((current) => ({
      ...current,
      competitors: current.competitors.map((item) => item.id === competitor.id ? { ...item, status: "Analyzing" } : item),
    }));
    window.setTimeout(() => {
      update((current) => ({
        ...current,
        competitors: current.competitors.map((item) => item.id === competitor.id ? { ...item, status: "Ready", lastAnalyzed: "Just now" } : item),
      }));
      setNotice(`${competitor.name} analysis is ready.`);
    }, 900);
  };

  const add = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name"));
    const item: Competitor = {
      id: slug(name) || `competitor-${Date.now()}`,
      name,
      website: String(form.get("website")),
      industry: String(form.get("industry")),
      status: "Needs analysis",
      positioning: "Analysis not started",
      products: "Analysis not started",
      pricing: "Analysis not started",
      marketing: "Analysis not started",
      offers: "Analysis not started",
      strengths: [],
      weaknesses: [],
      opportunities: [],
      lastAnalyzed: "Never",
      summary: "Run an analysis to create a grounded competitor summary.",
    };
    update((current) => ({ ...current, competitors: [item, ...current.competitors] }));
    recordAudit({ actor: "Current user", actorType: "Human user", action: "Added competitor", entity: name, after: "Ready for analysis", status: "Completed", source: "Competitor Intelligence" });
    setAdding(false);
    setNotice(`${name} added to your watchlist.`);
  };

  const createAction = (competitor: Competitor) => {
    update((current) => ({
      ...current,
      opportunities: [{ id: Date.now(), title: competitor.opportunities[0] ?? `Respond to ${competitor.name}`, copy: competitor.summary, category: "Competitor", impact: "Strategic", reviewed: false }, ...current.opportunities],
    }));
    setNotice("Action created in Opportunities.");
  };

  return (
    <>
      <PageHeader eyebrow="AI CMO · Research" title="Competitor Intelligence" subtitle={`Understand how ${industry === "Real Estate" ? "other property teams" : "the local market"} position, price, and promote their offer.`} action={<Button variant="primary" onClick={() => setAdding(true)} data-testid="button-add-competitor"><Plus /> Add competitor</Button>} />
      {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
      <Card className="intelligence-hero">
        <div className="intelligence-hero-icon"><Sparkles /></div>
        <div className="row-main"><div className="eyebrow">AI market summary</div><h2>{industry === "Real Estate" ? "Competitors lead with aspiration and market data, while fewer combine fast response with personal guidance." : "Competitors are heavily promoting convenience while few are talking about local freshness."}</h2><p className="subtle">Recommended action: {industry === "Real Estate" ? "Create a campaign that pairs practical buyer analysis with rapid personal follow-up." : "Create a campaign around same-day local harvest."}</p></div>
        <Button variant="green" onClick={() => setLocation("/campaigns?new=1")}><Target /> Create campaign</Button>
      </Card>
      <div className="grid competitor-grid">
        {data.competitors.map((item) => (
          <Card className="competitor-card" key={item.id}>
            <div className="competitor-head"><div className="integration-icon"><Globe2 /></div><div className="row-main"><h2>{item.name}</h2><div className="row-copy">{item.website}</div></div><Badge tone={item.status === "Ready" ? "success" : item.status === "Analyzing" ? "warning" : "neutral"}>{item.status === "Analyzing" && <RefreshCw className="spin" />} {item.status}</Badge></div>
            <div className="mini-detail"><span>Positioning</span><strong>{item.positioning}</strong></div>
            <div className="mini-detail"><span>Pricing</span><strong>{item.pricing}</strong></div>
            <div className="competitor-insight"><Sparkles /><div><div className="eyebrow">AI summary</div><p>{item.summary}</p></div></div>
            <div className="row-copy">Last analyzed · {item.lastAnalyzed}</div>
            <div className="toolbar competitor-actions">
              <Button className="btn-sm" onClick={() => analyze(item)} disabled={item.status === "Analyzing"}><RefreshCw /> Analyze</Button>
              <Button variant="soft" className="btn-sm" onClick={() => setSelected(item)}><Eye /> View analysis</Button>
              <Button variant="secondary" className="btn-sm" onClick={() => createAction(item)}><Plus /> Create action</Button>
              <Button variant="primary" className="btn-sm" onClick={() => setLocation("/cmo?tab=Content")}><Wand2 /> Create content</Button>
            </div>
          </Card>
        ))}
      </div>
      {adding && <Modal title="Add competitor" description="Add a public business profile to the prototype watchlist. No private credentials are requested." onClose={() => setAdding(false)}><form onSubmit={add}><div className="form-grid"><div className="field full"><label>Competitor name</label><input name="name" required autoFocus placeholder="Business name" /></div><div className="field"><label>Website</label><input name="website" required placeholder="competitor.example" /></div><div className="field"><label>Industry</label><input name="industry" required defaultValue={industry} /></div></div><div className="modal-foot"><Button type="button" onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" type="submit">Add competitor</Button></div></form></Modal>}
      {selected && <Modal wide title={selected.name} description={`${selected.industry} · Last analyzed ${selected.lastAnalyzed}`} onClose={() => setSelected(null)}><div className="analysis-grid"><Card><div className="eyebrow">Positioning</div><p className="detail-copy">{selected.positioning}</p></Card><Card><div className="eyebrow">Products / services</div><p className="detail-copy">{selected.products}</p></Card><Card><div className="eyebrow">Pricing observations</div><p className="detail-copy">{selected.pricing}</p></Card><Card><div className="eyebrow">Marketing & content</div><p className="detail-copy">{selected.marketing}</p></Card><Card><div className="eyebrow">Offers</div><p className="detail-copy">{selected.offers}</p></Card><Card className="recommendation"><div className="eyebrow">AI summary</div><p className="detail-copy">{selected.summary}</p></Card></div><div className="grid three-grid intelligence-lists"><Card><SectionTitle title="Strengths" />{selected.strengths.map((item) => <div className="check-line" key={item}><Check /> {item}</div>)}</Card><Card><SectionTitle title="Weaknesses" />{selected.weaknesses.map((item) => <div className="check-line weak" key={item}><X /> {item}</div>)}</Card><Card><SectionTitle title="Opportunities" />{selected.opportunities.map((item) => <div className="check-line opportunity-line" key={item}><Lightbulb /> {item}</div>)}</Card></div></Modal>}
    </>
  );
}

export function TrendIntelligencePage() {
  const { data, update, industry } = useWorkspaceData();
  const [, setLocation] = useLocation();
  const [filter, setFilter] = useState("Active");
  const [notice, setNotice] = useState("");
  const trends = useMemo(() => data.trends.filter((item) => filter === "All" || (filter === "Saved" ? item.state === "Saved" : item.state !== "Ignored")), [data.trends, filter]);

  const state = (id: string, next: "Saved" | "Ignored") => update((current) => ({ ...current, trends: current.trends.map((item) => item.id === id ? { ...item, state: next } : item) }));

  return (
    <>
      <PageHeader eyebrow="AI CMO · Research" title="Trend Intelligence" subtitle={`Early signals selected for ${industry === "Real Estate" ? "property and market relevance" : "agriculture, local food, and customer demand"}.`} action={<div className="search-box"><Search /><input placeholder="Search trend signals" /></div>} />
      {notice && <div className="ai-banner"><Sparkles /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
      <div className="tabs">{["Active", "Saved", "All"].map((item) => <button className={`tab ${filter === item ? "active" : ""}`} onClick={() => setFilter(item)} key={item}>{item}</button>)}</div>
      <div className="grid trend-grid">
        {trends.map((trend) => (
          <Card className="trend-card" key={trend.id}>
            <div className="trend-head"><div><Badge tone={trend.strength === "Strong" ? "success" : trend.strength === "Rising" ? "warning" : "info"}><TrendingUp /> {trend.strength}</Badge><h2>{trend.topic}</h2><div className="row-copy">Source · {trend.source}</div></div><div className="relevance-score"><strong>{trend.relevance}</strong><span>relevance</span></div></div>
            <div className="trend-velocity"><BarChart3 /><div><span>Velocity / change</span><strong>{trend.velocity}</strong></div></div>
            <div className="mini-detail"><span>Industry relevance</span><strong>{trend.industryRelevance}</strong></div>
            <div className="why-card"><div className="eyebrow">Why it matters</div><p>{trend.why}</p></div>
            <div className="recommendation-strip"><Sparkles /><div><div className="eyebrow">AI recommendation</div><p>{trend.recommendation}</p></div></div>
            <div className="toolbar trend-actions">
              <Button variant="primary" className="btn-sm" onClick={() => setLocation("/cmo?tab=Content")}><Wand2 /> Create content</Button>
              <Button variant="green" className="btn-sm" onClick={() => setLocation("/campaigns?new=1")}><Target /> Create campaign</Button>
              <Button variant="secondary" className="btn-sm" onClick={() => state(trend.id, "Saved")}><Bookmark /> {trend.state === "Saved" ? "Saved" : "Save"}</Button>
              <Button variant="secondary" className="btn-sm" onClick={() => state(trend.id, "Ignored")}><X /> Ignore</Button>
              <Button variant="soft" className="btn-sm" onClick={() => { setNotice(`AI Manager connected “${trend.topic}” to current business performance and recommends acting this week.`); }}><Sparkles /> Ask AI Manager</Button>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
