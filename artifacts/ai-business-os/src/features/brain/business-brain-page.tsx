import { useState } from "react";
import { Brain, Check, CloudUpload, Database, FileText, Globe2, RefreshCw, Search, Sparkles, Upload, X } from "lucide-react";
import { Badge, Button, Card, PageHeader, SectionTitle } from "@/components/product-ui";
import { useBusiness } from "@/business-context";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { BrainSource } from "@/types/workspace";

export function BusinessBrainPage() {
  const { activeBusiness } = useBusiness();
  const { data, update, industry } = useWorkspaceData();
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState(false);
  const [category, setCategory] = useState("All");
  const sources = data.brainSources.filter((item) => category === "All" || item.category === category);
  const upload = (file?: File) => {
    const extension = file?.name.split(".").pop()?.toUpperCase();
    const type = (["PDF", "DOCX", "TXT", "CSV"].includes(extension ?? "") ? extension : "TXT") as BrainSource["type"];
    const item: BrainSource = { id: `source-${Date.now()}`, name: file?.name ?? "New business document.txt", category: "Documents", type, status: "Uploading", added: "Just now" };
    update((current) => ({ ...current, brainSources: [item, ...current.brainSources] }));
    window.setTimeout(() => update((current) => ({ ...current, brainSources: current.brainSources.map((source) => source.id === item.id ? { ...source, status: "Processing" } : source) })), 450);
    window.setTimeout(() => update((current) => ({ ...current, brainSources: current.brainSources.map((source) => source.id === item.id ? { ...source, status: "Processed" } : source) })), 1200);
  };
  const remove = (id: string) => update((current) => ({ ...current, brainSources: current.brainSources.filter((item) => item.id !== id) }));
  const search = () => { if (query.trim()) setAnswer(true); };
  const categories = ["All", "Website", "Documents", "Products", "FAQs", "Policies", "Brand Guidelines"];
  return <>
    <PageHeader eyebrow="Knowledge layer" title="Business Brain" subtitle={`The grounded context ${activeBusiness?.name}'s AI team uses to make decisions.`} action={<label className="btn btn-primary"><CloudUpload /> Upload source<input type="file" hidden accept=".pdf,.docx,.txt,.csv" onChange={(event) => upload(event.target.files?.[0])} /></label>} />
    <div className="tabs brain-tabs">{categories.map((item) => <button className={`tab ${category === item ? "active" : ""}`} onClick={() => setCategory(item)} key={item}>{item}</button>)}</div>
    <div className="brain-layout"><Card><SectionTitle title="Knowledge sources" action={<Badge tone="success">{data.brainSources.filter((item) => item.status === "Processed").length} processed</Badge>} /><div className="list">{sources.map((source) => <div className="source-row" key={source.id}><div className="source-icon">{source.type === "Website" ? <Globe2 /> : source.type === "CSV" ? <Database /> : <FileText />}</div><div className="row-main"><div className="row-title">{source.name}</div><div className="row-copy">{source.category} · {source.type} · {source.added}</div></div><span className={`source-status ${source.status.toLowerCase()}`}>{source.status === "Processed" ? <><Check /> Processed</> : source.status === "Failed" ? "Failed" : <><RefreshCw className="spin" /> {source.status}…</>}</span><button className="close-btn" onClick={() => remove(source.id)} aria-label={`Remove ${source.name}`}><X /></button></div>)}</div>{!sources.length && <div className="empty"><FileText /><h3>No sources in this category</h3><p>Upload a PDF, DOCX, TXT, or CSV to add grounded context.</p></div>}<div className="upload-drop"><Upload /><div><strong>PDF, DOCX, TXT, and CSV</strong><p>Prototype uploads simulate indexing states; no vector database is used.</p></div></div></Card><Card><SectionTitle title="Search your brain" action={<Brain />} /><div className="search-box brain-search"><Search /><input value={query} onChange={(event) => { setQuery(event.target.value); setAnswer(false); }} onKeyDown={(event) => { if (event.key === "Enter") search(); }} placeholder={industry === "Real Estate" ? "What do our fair-housing guidelines say?" : "What is our return policy?"} /><Button variant="green" className="btn-sm" onClick={search}>Search</Button></div>{answer ? <div className="answer"><div className="eyebrow">Answer</div><h2>{industry === "Real Estate" ? "Marketing and customer communication must describe the property and transaction fairly, without steering or language that indicates a protected-class preference." : "Returns are accepted within 14 days when products are unopened and kept chilled. The team can arrange pickup or issue store credit."}</h2><div className="supporting-context"><Sparkles /><div><strong>Supporting context</strong><p>{industry === "Real Estate" ? "Fair Housing Guidelines.docx · Communication policy, sections 2–4" : "Company Guide.pdf · Returns policy, section 4"}</p></div></div><div className="confidence">Confidence · 96% · Grounded in processed business sources</div></div> : <div className="empty"><Search /><h3>Ask your business anything</h3><p>Answers include a source, confidence, and supporting context.</p></div>}</Card></div>
  </>;
}

