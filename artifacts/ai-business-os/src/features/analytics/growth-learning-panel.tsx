import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Beaker,
  BrainCircuit,
  Check,
  Clock3,
  Plus,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import type {
  GrowthExperiment,
  GrowthMetric,
  MarketingCampaign,
  PageResponse,
} from "@/services/api-types";
import { growthLearningApi } from "@/services/growth-learning";


const METRIC_MINIMUMS: Record<GrowthMetric, number> = {
  ctr: 1_000,
  conversion_rate: 100,
  cpc: 100,
  cpa: 20,
  roas: 20,
};

const RESULT_LABELS = {
  insufficient_evidence: "Insufficient evidence",
  no_material_difference: "No material difference",
  observed_directional_difference: "Observed directional difference",
  mixed_result: "Mixed result",
} as const;

function words(value: string) {
  return value.replaceAll("_", " ");
}

function metricValue(value: string | null, metric: GrowthMetric, currency: string) {
  if (value === null) return "Not measured";
  const number = Number(value);
  if (metric === "ctr" || metric === "conversion_rate") return `${number.toFixed(2)}%`;
  if (metric === "roas") return `${number.toFixed(2)}x`;
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(number);
}

function statusTone(status: GrowthExperiment["status"]) {
  if (status === "evaluated") return "success" as const;
  if (status === "running") return "info" as const;
  if (status === "completed" || status === "ready") return "warning" as const;
  if (status === "canceled") return "neutral" as const;
  return "brown" as const;
}

export function GrowthLearningPanel({
  campaigns,
}: {
  campaigns: PageResponse<MarketingCampaign> | undefined;
}) {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [metric, setMetric] = useState<GrowthMetric>("ctr");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const canGovern = ["owner", "admin"].includes(activeBusiness?.membershipRole ?? "");
  const experimentQuery = useQuery({
    queryKey: ["growth", activeBusinessId, "experiments"],
    queryFn: ({ signal }) =>
      growthLearningApi.experiments.list(activeBusinessId, { pageSize: 50 }, signal),
    enabled: Boolean(activeBusinessId),
  });
  const learningQuery = useQuery({
    queryKey: ["growth", activeBusinessId, "learnings"],
    queryFn: ({ signal }) => growthLearningApi.learnings.list(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["growth", activeBusinessId] });
  };
  const create = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      return growthLearningApi.experiments.create(activeBusinessId, {
        name: String(form.get("name")),
        hypothesis: String(form.get("hypothesis")),
        learning_key: String(form.get("learning_key")),
        experiment_type: "campaign",
        primary_metric: metric,
        attribution_classification: String(form.get("attribution")) as "provider_attributed" | "first_party_observed",
        evaluation_window_days: Number(form.get("window_days")),
        minimum_sample_size: Number(form.get("minimum_sample")),
        variants: [
          {
            variant_key: "control",
            label: "Control",
            is_control: true,
            campaign_id: String(form.get("control_campaign_id")),
          },
          {
            variant_key: "challenger",
            label: "Challenger",
            is_control: false,
            campaign_id: String(form.get("challenger_campaign_id")),
          },
        ],
      });
    },
    onSuccess: () => {
      setCreating(false);
      setError("");
      setNotice("Experiment draft saved. Its campaigns still require the existing approval and execution path.");
      refresh();
    },
    onError: (reason) =>
      setError(humanizeApiError(reason, "The experiment definition could not be saved.")),
  });
  const transition = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "ready" | "start" | "complete" | "cancel" }) =>
      growthLearningApi.experiments.transition(activeBusinessId, id, action),
    onSuccess: (value) => {
      setError("");
      setNotice(`Experiment is now ${words(value.status)}. No provider action was executed by this transition.`);
      refresh();
    },
    onError: (reason) =>
      setError(humanizeApiError(reason, "The experiment lifecycle could not be changed.")),
  });
  const evaluate = useMutation({
    mutationFn: (id: string) => growthLearningApi.experiments.evaluate(activeBusinessId, id),
    onSuccess: (result) => {
      setError("");
      setNotice(`Deterministic evaluation completed: ${RESULT_LABELS[result.classification]}.`);
      refresh();
    },
    onError: (reason) =>
      setError(humanizeApiError(reason, "Stored experiment evidence could not be evaluated.")),
  });

  const pending = transition.isPending || evaluate.isPending;
  const campaignItems = campaigns?.items ?? [];
  return <>
    <PageHeader
      eyebrow="Growth learning"
      title="Experiments and observed learning"
      subtitle="Compare durable campaign evidence without turning correlation into causation or letting AI declare a winner."
      action={canGovern ? <Button variant="primary" disabled={campaignItems.length < 2} onClick={() => setCreating(true)}><Plus /> New experiment</Button> : undefined}
    />
    {notice && <div className="ai-banner"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")}><X /></button></div>}
    {error && !creating && <div className="ai-banner"><AlertCircle /> {error}<button className="close-btn" onClick={() => setError("")}><X /></button></div>}
    <div className="growth-truth-grid">
      <Card><div className="eyebrow">Fact</div><strong>Stored performance rows</strong><p className="subtle">Only rows inside the frozen UTC-day window and created by its cutoff are measured.</p></Card>
      <Card><div className="eyebrow">Attribution</div><strong>Explicit evidence class</strong><p className="subtle">Provider-attributed and first-party-observed evidence remain separate.</p></Card>
      <Card><div className="eyebrow">Experiment result</div><strong>Deterministic server evaluation</strong><p className="subtle">Samples, formulas, overlap checks, and materiality are inspectable.</p></Card>
      <Card><div className="eyebrow">Recommendation</div><strong>Governed next step only</strong><p className="subtle">Learning informs future CMO and Sales reasoning; it never executes a campaign.</p></Card>
    </div>

    {experimentQuery.isLoading ? <Card><div className="empty compact-empty"><RefreshCw className="spin" /><p>Loading experiments…</p></div></Card> : experimentQuery.isError ? <Card><div className="empty compact-empty"><AlertCircle /><p>{humanizeApiError(experimentQuery.error, "Experiments could not load.")}</p><Button onClick={() => void experimentQuery.refetch()}>Try again</Button></div></Card> : <div className="growth-experiment-list">
      {experimentQuery.data?.items.map((experiment) => {
        const result = experiment.result;
        const metrics = result?.evidence.variant_metrics ?? [];
        return <Card key={experiment.id} className="growth-experiment-card">
          <SectionTitle title={experiment.name} action={<Badge tone={statusTone(experiment.status)}>{words(experiment.status)}</Badge>} />
          <p>{experiment.hypothesis}</p>
          <div className="growth-meta-row">
            <span><Beaker /> {words(experiment.primary_metric)}</span>
            <span><ShieldCheck /> {words(experiment.attribution_classification)}</span>
            <span><Clock3 /> {experiment.evaluation_window_days} complete UTC days</span>
          </div>
          <div className="growth-variant-grid">
            {experiment.variants.map((variant) => {
              const measured = metrics.find((item) => item.variant_id === variant.id);
              return <div className="growth-variant" key={variant.id}>
                <div><strong>{variant.label}</strong>{variant.is_control && <Badge tone="neutral">Control</Badge>}</div>
                <span>{measured ? `${measured.sample_size.toLocaleString()} ${measured.sample_basis}` : "Awaiting measured evidence"}</span>
                <strong>{measured ? metricValue(measured.metric_value, experiment.primary_metric, experiment.currency) : "—"}</strong>
                {measured && <Badge tone={measured.sufficient ? "success" : "warning"}>{measured.sufficient ? "Sample met" : words(measured.data_quality === "complete" ? "sample pending" : measured.data_quality)}</Badge>}
              </div>;
            })}
          </div>
          {result && <div className="growth-result-panel">
            <div><span className="eyebrow">Result classification</span><strong>{RESULT_LABELS[result.classification]}</strong></div>
            <div><span className="eyebrow">Evidence quality</span><strong>{Math.round(Number(result.evidence_quality) * 100)}%</strong></div>
            <div><span className="eyebrow">Directional leader</span><strong>{result.directional_leader_key ? words(result.directional_leader_key) : "None declared"}</strong></div>
            <p>{result.classification === "observed_directional_difference" ? "A material directional difference was observed. No statistical significance or causal claim is made." : result.classification === "insufficient_evidence" ? "At least one variant did not meet the required sample or data-quality contract." : "The evidence does not support a directional preference."}</p>
          </div>}
          {experiment.measurement_start && <div className="row-copy">UTC window: {new Date(experiment.measurement_start).toLocaleDateString()} up to, not including, {experiment.measurement_end ? new Date(experiment.measurement_end).toLocaleDateString() : "the open end"}{experiment.evaluation_cutoff ? ` · frozen cutoff ${new Date(experiment.evaluation_cutoff).toLocaleString()}` : ""}</div>}
          {canGovern && <div className="toolbar growth-actions">
            {experiment.status === "draft" && <><Button disabled={pending} variant="green" onClick={() => transition.mutate({ id: experiment.id, action: "ready" })}>Mark ready</Button><Button disabled={pending} variant="danger" onClick={() => transition.mutate({ id: experiment.id, action: "cancel" })}>Cancel</Button></>}
            {experiment.status === "ready" && <><Button disabled={pending} variant="green" onClick={() => transition.mutate({ id: experiment.id, action: "start" })}>Start measurement</Button><Button disabled={pending} variant="danger" onClick={() => transition.mutate({ id: experiment.id, action: "cancel" })}>Cancel</Button></>}
            {experiment.status === "running" && <><Button disabled={pending} variant="green" onClick={() => transition.mutate({ id: experiment.id, action: "complete" })}>Complete after window</Button><Button disabled={pending} variant="danger" onClick={() => transition.mutate({ id: experiment.id, action: "cancel" })}>Cancel</Button></>}
            {experiment.status === "completed" && <Button disabled={pending} variant="primary" onClick={() => evaluate.mutate(experiment.id)}><Beaker /> Evaluate stored facts</Button>}
          </div>}
        </Card>;
      })}
      {!experimentQuery.data?.items.length && <Card><div className="empty"><Beaker /><h3>No governed experiments yet</h3><p>Create a bounded campaign comparison. Starting it will not launch or modify either campaign.</p></div></Card>}
    </div>}

    <SectionTitle title="Accepted business learning" />
    {learningQuery.isLoading ? <Card><div className="empty compact-empty"><RefreshCw className="spin" /><p>Loading accepted learning…</p></div></Card> : learningQuery.isError ? <Card><div className="empty compact-empty"><AlertCircle /><p>Accepted learning could not load.</p></div></Card> : <div className="grid analytics-secondary-grid">
      {learningQuery.data?.items.map((learning) => <Card key={learning.id}><BrainCircuit /><p>{learning.content}</p><div className="growth-meta-row"><Badge tone="info">Observed learning</Badge><span>Memory weighting {Math.round(Number(learning.confidence) * 100)}/100</span></div></Card>)}
      {!learningQuery.data?.items.length && <Card><div className="empty compact-empty"><BrainCircuit /><p>No evidence-qualified learning has been accepted.</p></div></Card>}
    </div>}

    {creating && <Modal title="Create governed campaign experiment" description="This defines a comparison only. Campaign execution still uses existing policy, approval, connector, and spend controls." onClose={() => setCreating(false)}><form onSubmit={(event) => create.mutate(event)}>
      <div className="form-grid">
        <div className="field full"><label>Experiment name</label><input name="name" required maxLength={180} /></div>
        <div className="field full"><label>Hypothesis</label><textarea name="hypothesis" required maxLength={2000} placeholder="State a falsifiable expectation without claiming the outcome." /></div>
        <div className="field"><label>Learning key</label><input name="learning_key" required pattern="[a-z][a-z0-9_]{0,63}" placeholder="meta_creative_family" /></div>
        <div className="field"><label>Primary metric</label><select name="metric" value={metric} onChange={(event) => setMetric(event.target.value as GrowthMetric)}><option value="ctr">CTR</option><option value="conversion_rate">Conversion rate</option><option value="cpc">CPC</option><option value="cpa">CPA</option><option value="roas">ROAS</option></select></div>
        <div className="field"><label>Control campaign</label><select name="control_campaign_id" required>{campaignItems.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name}</option>)}</select></div>
        <div className="field"><label>Challenger campaign</label><select name="challenger_campaign_id" required defaultValue={campaignItems[1]?.id}>{campaignItems.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name}</option>)}</select></div>
        <div className="field"><label>Attribution evidence</label><select name="attribution"><option value="provider_attributed">Provider attributed</option><option value="first_party_observed">First-party observed</option></select></div>
        <div className="field"><label>Complete UTC days</label><input name="window_days" type="number" min="1" max="90" defaultValue="14" required /></div>
        <div className="field"><label>Minimum sample per variant</label><input name="minimum_sample" type="number" min={METRIC_MINIMUMS[metric]} defaultValue={METRIC_MINIMUMS[metric]} key={metric} required /></div>
      </div>
      <div className="ai-banner"><ShieldCheck /> Starting measurement never launches campaigns, changes spend, sends messages, or calls a provider.</div>
      {error && <p className="form-error">{error}</p>}
      <div className="modal-foot"><Button type="button" onClick={() => setCreating(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={create.isPending}>{create.isPending ? "Saving…" : "Save draft"}</Button></div>
    </form></Modal>}
  </>;
}
