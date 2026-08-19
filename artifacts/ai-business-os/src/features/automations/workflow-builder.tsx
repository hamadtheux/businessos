import { useMemo, useState, type FormEvent } from "react";
import { AlertCircle, ArrowDown, ArrowUp, Bell, Bot, Check, CheckCircle2, Copy, Database, GitBranch, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Save, Settings2, TestTube2, Trash2, X, Zap } from "lucide-react";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import type { Workflow, WorkflowNode } from "@/types/workspace";

const nodeTypes: WorkflowNode["type"][] = ["Trigger", "AI Decision", "Condition", "Database", "API", "Action", "Notification", "Approval", "Delay", "Branch"];
const nodeIcons = { Trigger: Zap, "AI Decision": Bot, Condition: GitBranch, Database, API: Settings2, Action: Play, Notification: Bell, Approval: CheckCircle2, Delay: RefreshCw, Branch: GitBranch };

export function WorkflowBuilderPage() {
  const { data, update, recordAudit } = useWorkspaceData();
  const [selectedId, setSelectedId] = useState(data.workflows[0]?.id ?? "");
  const selected = data.workflows.find((item) => item.id === selectedId) ?? data.workflows[0];
  const [editing, setEditing] = useState<WorkflowNode | null>(null);
  const [adding, setAdding] = useState(false);
  const [test, setTest] = useState<{ state: "idle" | "running" | "success" | "failed"; failedNode?: string }>({ state: "idle" });
  const [notice, setNotice] = useState("");
  const [simulateFailure, setSimulateFailure] = useState(false);

  const mutateWorkflow = (updater: (workflow: Workflow) => Workflow) => update((current) => ({ ...current, workflows: current.workflows.map((item) => item.id === selected.id ? updater(item) : item) }));
  const reorder = (index: number, direction: number) => mutateWorkflow((workflow) => { const nodes = [...workflow.nodes]; const target = index + direction; if (target < 0 || target >= nodes.length) return workflow; [nodes[index], nodes[target]] = [nodes[target], nodes[index]]; return { ...workflow, nodes }; });
  const remove = (id: string) => mutateWorkflow((workflow) => ({ ...workflow, nodes: workflow.nodes.filter((node) => node.id !== id) }));
  const duplicate = () => {
    const copy: Workflow = { ...selected, id: `workflow-${Date.now()}`, name: `${selected.name} copy`, enabled: false, nodes: selected.nodes.map((node) => ({ ...node, id: `${node.id}-${Date.now()}` })) };
    update((current) => ({ ...current, workflows: [...current.workflows, copy] }));
    setSelectedId(copy.id);
    setNotice("Workflow duplicated as a draft.");
  };
  const saveNode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const node: WorkflowNode = { id: editing?.id ?? `node-${Date.now()}`, type: String(form.get("type")) as WorkflowNode["type"], label: String(form.get("label")), config: String(form.get("config")), branch: form.get("branch") ? String(form.get("branch")) as "YES" | "NO" : undefined };
    mutateWorkflow((workflow) => ({ ...workflow, nodes: editing ? workflow.nodes.map((item) => item.id === editing.id ? node : item) : [...workflow.nodes, node] }));
    setEditing(null); setAdding(false);
  };
  const runTest = () => {
    setTest({ state: "running" });
    window.setTimeout(() => setTest(simulateFailure ? { state: "failed", failedNode: selected.nodes.find((node) => node.type === "API")?.id ?? selected.nodes[2]?.id } : { state: "success" }), 950);
  };
  const save = () => {
    recordAudit({ actor: "Alexandra Andria", actorType: "Human user", action: "Saved workflow", entity: selected.name, after: `${selected.nodes.length} nodes`, status: "Completed", source: "Automations" });
    setNotice("Workflow saved to this business.");
  };

  if (!selected) return <Card><div className="empty"><Zap /><h3>No workflows yet</h3><p>Create a workflow to give repetitive work a dependable path.</p></div></Card>;
  return <>
    <PageHeader eyebrow="Operations · Editable prototype" title="Automation Builder" subtitle="Design a safe workflow, test every node, and enable it only when the path is clear." action={<div className="toolbar"><Button onClick={duplicate}><Copy /> Duplicate</Button><Button variant="soft" onClick={runTest}><TestTube2 /> Test</Button><Button variant="primary" onClick={save}><Save /> Save workflow</Button></div>} />
    {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    <div className="workflow-layout"><Card className="workflow-sidebar"><SectionTitle title="Workflows" action={<Button variant="soft" className="btn-sm" onClick={() => { const workflow: Workflow = { id: `workflow-${Date.now()}`, name: "Untitled workflow", description: "Describe what this workflow should accomplish.", enabled: false, nodes: [] }; update((current) => ({ ...current, workflows: [...current.workflows, workflow] })); setSelectedId(workflow.id); }}><Plus /></Button>} />{data.workflows.map((workflow) => <button className={`workflow-list-item ${workflow.id === selected.id ? "active" : ""}`} onClick={() => setSelectedId(workflow.id)} key={workflow.id}><span className="workflow-list-icon"><Zap /></span><div className="row-main"><strong>{workflow.name}</strong><span>{workflow.nodes.length} nodes</span></div><Badge tone={workflow.enabled ? "success" : "neutral"}>{workflow.enabled ? "Enabled" : "Draft"}</Badge></button>)}</Card>
      <div className="workflow-main"><Card className="workflow-builder-head"><div><div className="eyebrow">Selected workflow</div><h2>{selected.name}</h2><p className="subtle">{selected.description}</p></div><div className="toolbar"><Badge tone={selected.enabled ? "success" : "neutral"}>{selected.enabled ? "Enabled" : "Disabled"}</Badge><Button variant={selected.enabled ? "danger" : "green"} className="btn-sm" onClick={() => mutateWorkflow((workflow) => ({ ...workflow, enabled: !workflow.enabled }))}>{selected.enabled ? "Disable" : "Enable"}</Button></div></Card>
        <Card className="workflow-canvas"><div className="workflow-canvas-grid" />{selected.nodes.map((node, index) => { const Icon = nodeIcons[node.type]; return <div className="builder-node-wrap" key={node.id}><div className={`builder-node ${node.type === "Condition" || node.type === "Branch" ? "branch-node" : ""}`}><div className="builder-node-icon"><Icon /></div><div className="row-main"><div className="builder-node-type">{node.type}{node.branch && <Badge tone={node.branch === "YES" ? "success" : "warning"}>{node.branch}</Badge>}</div><strong>{node.label}</strong><span>{node.config}</span></div><div className="builder-node-actions"><button onClick={() => reorder(index, -1)} disabled={index === 0} aria-label="Move node up"><ArrowUp /></button><button onClick={() => reorder(index, 1)} disabled={index === selected.nodes.length - 1} aria-label="Move node down"><ArrowDown /></button><button onClick={() => setEditing(node)} aria-label="Edit node"><Pencil /></button><button onClick={() => remove(node.id)} aria-label="Remove node"><Trash2 /></button></div></div>{index < selected.nodes.length - 1 && <div className="builder-connector"><i /><ArrowDown /></div>}</div>; })}<Button variant="soft" className="add-node-button" onClick={() => setAdding(true)}><Plus /> Add node</Button>{!selected.nodes.length && <div className="empty"><GitBranch /><h3>Build the first step</h3><p>Add a trigger, then connect decisions, conditions, approvals, and actions.</p></div>}</Card>
        <Card className="workflow-test-panel"><div className="workflow-test-head"><div><div className="eyebrow">Safe test mode</div><h2>No external action will occur</h2></div><label className="simulate-toggle"><input type="checkbox" checked={simulateFailure} onChange={(event) => setSimulateFailure(event.target.checked)} /> Simulate API failure</label></div>{test.state === "idle" && <p className="subtle">Test the trigger, AI decision, data access, API boundaries, and final action before enabling.</p>}{test.state === "running" && <div className="test-list">{selected.nodes.map((node) => <div className="test-step" key={node.id}><RefreshCw className="spin" /> Checking {node.label}…</div>)}</div>}{test.state === "success" && <div className="test-list">{selected.nodes.map((node) => <div className="test-step" key={node.id}><CheckCircle2 /> {node.label} passed</div>)}<div className="ai-banner"><CheckCircle2 /> Test completed. Every prototype node is ready.</div></div>}{test.state === "failed" && <div><div className="test-list">{selected.nodes.map((node) => <div className={`test-step ${node.id === test.failedNode ? "failed" : ""}`} key={node.id}>{node.id === test.failedNode ? <AlertCircle /> : <CheckCircle2 />} {node.label} {node.id === test.failedNode ? "failed" : "passed"}</div>)}</div><div className="failure-card"><AlertCircle /><div className="row-main"><strong>API node could not reach the prototype connection</strong><p>Suggested fix: open Integrations, reconnect the required service, then retry from this exact node.</p></div><Button variant="danger" className="btn-sm" onClick={runTest}><RefreshCw /> Retry</Button></div></div>}</Card>
      </div>
    </div>
    {(editing || adding) && <Modal title={editing ? "Configure node" : "Add node"} description="Every node stays editable in this frontend prototype." onClose={() => { setEditing(null); setAdding(false); }}><form onSubmit={saveNode}><div className="form-grid"><div className="field"><label>Node type</label><select name="type" defaultValue={editing?.type ?? "Action"}>{nodeTypes.map((type) => <option key={type}>{type}</option>)}</select></div><div className="field"><label>Branch</label><select name="branch" defaultValue={editing?.branch ?? ""}><option value="">Main path</option><option>YES</option><option>NO</option></select></div><div className="field full"><label>Label</label><input name="label" required defaultValue={editing?.label ?? ""} placeholder="What happens here?" /></div><div className="field full"><label>Configuration</label><textarea name="config" required defaultValue={editing?.config ?? ""} placeholder="Human-readable node configuration" /></div></div><div className="modal-foot"><Button type="button" onClick={() => { setEditing(null); setAdding(false); }}>Cancel</Button><Button variant="primary" type="submit">Save node</Button></div></form></Modal>}
  </>;
}

