import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Bell,
  Bot,
  Check,
  CheckCircle2,
  Copy,
  GitBranch,
  History,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  TestTube2,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import {
  AUTOPILOT_PACKS,
  hasHealthyConnection,
  hasWriteReadyConnection,
} from "@/lib/phase8a-product";
import { integrationsApi } from "@/services/integrations";
import { processingApi } from "@/services/processing";
import {
  automationsApi,
  type AutomationNode,
  type AutomationNodeType,
  type AutomationCopilotResult,
  type SimulationResult,
  type WorkflowRun,
} from "@/services/automations";

const labels: Record<AutomationNodeType, string> = {
  trigger: "Trigger",
  condition: "Condition",
  branch: "Branch",
  action: "Action",
  delay: "Delay",
  approval: "Approval",
  ai: "AI Decision",
  internal_operation: "Notification",
  end: "End",
};
const nodeTypes: AutomationNodeType[] = [
  "trigger",
  "ai",
  "condition",
  "branch",
  "internal_operation",
  "action",
  "approval",
  "delay",
  "end",
];
const nodeIcons: Record<AutomationNodeType, typeof Zap> = {
  trigger: Zap,
  ai: Bot,
  condition: GitBranch,
  branch: GitBranch,
  internal_operation: Bell,
  action: Play,
  approval: CheckCircle2,
  delay: RefreshCw,
  end: Check,
};
const when = (value: string | null) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";

const waitingReasonLabels: Record<string, string> = {
  approval: "waiting for approval",
  action_execution: "waiting for provider result",
  delay: "waiting for scheduled time",
  retry: "waiting for safe retry",
};
const waitingReasonLabel = (value: string | null) =>
  waitingReasonLabels[value ?? ""] ??
  value?.replaceAll("_", " ") ??
  "completed path";

function defaultConfiguration(
  type: AutomationNodeType,
  triggerType: string,
): Record<string, unknown> {
  if (type === "trigger") return { kind: "trigger", trigger_type: triggerType };
  if (type === "condition")
    return {
      kind: "condition",
      condition: { field: "lead.estimated_value", operator: "gt", value: 5000 },
    };
  if (type === "branch")
    return {
      kind: "branch",
      condition: { field: "lead.estimated_value", operator: "gt", value: 5000 },
      true_label: "true",
      false_label: "false",
    };
  if (type === "action")
    return {
      kind: "action",
      action_type: "send_customer_message",
      description: "Prepare a customer message for governed review.",
      payload: { message: "Draft message" },
      context_bindings: { customer_ref: "event_customer_ref" },
      risk_level: "medium",
      requires_approval: true,
    };
  if (type === "delay")
    return { kind: "delay", mode: "duration", seconds: 600, offset_seconds: 0 };
  if (type === "approval")
    return { kind: "approval", reason_code: "workflow_review_required" };
  if (type === "ai")
    return {
      kind: "ai",
      role: "operations",
      task: "Summarize the safe workflow context and recommend the next internal step.",
      allow_action_proposals: false,
    };
  if (type === "internal_operation")
    return {
      kind: "internal_operation",
      operation: "create_notification",
      parameters: {
        category: "automation",
        title: "Workflow update",
        message: "A workflow reached this step.",
        priority: "medium",
      },
      max_attempts: 1,
      retry_delay_seconds: 60,
    };
  return { kind: "end", outcome: "success" };
}

export function WorkflowBuilderPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const schedulingEnabled = isBusinessFeatureEnabled(activeBusiness, "scheduling");
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [editing, setEditing] = useState<AutomationNode | null>(null);
  const [adding, setAdding] = useState(false);
  const [creating, setCreating] = useState(false);
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null);
  const [simulation, setSimulation] = useState<SimulationResult | null>(null);
  const [simulateFailure, setSimulateFailure] = useState(false);
  const [testPayload, setTestPayload] = useState(
    '{"event":{"type":"manual_test","entity_type":"test","entity_id":null},"lead":{"estimated_value":7500}}',
  );
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [copilotResult, setCopilotResult] = useState<AutomationCopilotResult | null>(null);
  const [copilotPrompt, setCopilotPrompt] = useState("");

  const workflows = useQuery({
    queryKey: ["automations", activeBusinessId, "workflows"],
    queryFn: ({ signal }) =>
      automationsApi.workflows.list(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  useEffect(() => {
    const first = workflows.data?.items[0]?.id ?? "";
    if (
      !selectedId ||
      !workflows.data?.items.some((item) => item.id === selectedId)
    )
      setSelectedId(first);
  }, [selectedId, workflows.data]);
  const detail = useQuery({
    queryKey: ["automations", activeBusinessId, "workflow", selectedId],
    queryFn: ({ signal }) =>
      automationsApi.workflows.get(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
  });
  const runs = useQuery({
    queryKey: ["automations", activeBusinessId, "runs", selectedId],
    queryFn: ({ signal }) =>
      automationsApi.runs.list(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
    refetchInterval: 5_000,
  });
  const nodeHistory = useQuery({
    queryKey: ["automations", activeBusinessId, "run-nodes", selectedRun?.id],
    queryFn: ({ signal }) =>
      automationsApi.runs.nodes(activeBusinessId, selectedRun!.id, signal),
    enabled: Boolean(activeBusinessId && selectedRun),
    refetchInterval: 5_000,
  });
  useEffect(() => {
    if (!selectedRun) return;
    const current = runs.data?.items.find((run) => run.id === selectedRun.id);
    if (
      current &&
      (current.status !== selectedRun.status ||
        current.waiting_reason !== selectedRun.waiting_reason ||
        current.failure_code !== selectedRun.failure_code)
    ) {
      setSelectedRun(current);
    }
  }, [runs.data, selectedRun]);
  const processing = useQuery({
    queryKey: ["processing", activeBusinessId],
    queryFn: ({ signal }) => processingApi.health(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 15_000,
  });
  const recentJobs = useQuery({
    queryKey: ["processing", activeBusinessId, "jobs"],
    queryFn: ({ signal }) => processingApi.jobs(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 15_000,
  });
  const connections = useQuery({
    queryKey: ["integrations", activeBusinessId, "connections", "automation-packs"],
    queryFn: ({ signal }) => integrationsApi.connections(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const connectorRegistry = useQuery({
    queryKey: ["integrations", activeBusinessId, "registry", "automation-packs"],
    queryFn: ({ signal }) => integrationsApi.registry(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const selected = detail.data;
  const compiledGovernedAction = copilotResult?.proposed_actions.some(
    (action) =>
      action.execution_state ===
      "governed_action_compiled_pending_approval",
  ) ?? false;
  const requestedExternalAction = Boolean(
    copilotResult?.proposed_actions.length,
  );
  const preparedPack = AUTOPILOT_PACKS.find(
    (pack) => pack.title === copilotResult?.workflow.name,
  );
  const preparedConnectionOptions = preparedPack?.connectionOptions ??
    copilotResult?.required_integrations.flatMap((requirement) =>
      requirement === "gmail_or_outlook"
        ? ["gmail", "microsoft_outlook"]
        : [requirement],
    ) ?? [];
  const preparedWriteReady = preparedConnectionOptions.length === 0 ||
    hasWriteReadyConnection(
      connections.data ?? [],
      connectorRegistry.data ?? [],
      preparedConnectionOptions,
    );
  const processingState = processing.isError
    ? "unavailable"
    : (processing.data?.status ?? "checking");
  const processingLabel =
    processingState === "healthy"
      ? "Healthy"
      : processingState === "degraded"
        ? "Degraded"
        : processingState === "unavailable"
          ? "Worker offline"
          : "Checking";
  const processingCopy =
    processingState === "healthy"
      ? "Active workflows resume automatically through durable PostgreSQL processing."
      : processingState === "degraded"
        ? "Durable processing is available, but one or more worker signals need attention."
        : processingState === "unavailable"
          ? "The background worker is not reporting healthy. Queued work will wait until processing recovers."
          : "Checking the durable worker and scheduler health.";
  const orderedNodes = useMemo(
    () =>
      [...(selected?.nodes ?? [])].sort(
        (a, b) =>
          a.order_index - b.order_index || a.node_key.localeCompare(b.node_key),
      ),
    [selected?.nodes],
  );
  const refresh = () => {
    void client.invalidateQueries({
      queryKey: ["automations", activeBusinessId],
    });
    void client.invalidateQueries({
      queryKey: ["processing", activeBusinessId],
    });
  };
  const mutation = useMutation({
    mutationFn: async (operation: () => Promise<unknown>) => operation(),
    onSuccess: () => {
      setError("");
      refresh();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "The workflow change could not be saved."),
      ),
  });
  const copilot = useMutation({
    mutationFn: ({ prompt, name }: { prompt: string; name?: string }) => {
      return automationsApi.copilot.compile(activeBusinessId, {
        prompt,
        name,
        timezone: activeBusiness?.timezone || "UTC",
      });
    },
    onSuccess: (result) => {
      setCopilotResult(result);
      setSelectedId(result.workflow.id);
      setNotice("The automation is prepared as a deterministic draft. Review its prerequisites and dry-run it before activation; no external action was sent.");
      setError("");
      refresh();
    },
    onError: (reason) => setError(humanizeApiError(reason, "Automation Copilot could not compile this request safely.")),
  });
  const simulate = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("Select a workflow first.");
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(testPayload) as Record<string, unknown>;
      } catch {
        throw new Error("Test payload must be valid JSON.");
      }
      const forced = simulateFailure
        ? (orderedNodes.find(
            (node) =>
              node.node_type === "action" ||
              node.node_type === "internal_operation",
          )?.node_key ?? orderedNodes[1]?.node_key)
        : null;
      return automationsApi.workflows.simulate(activeBusinessId, selected.id, {
        payload,
        run_ai: false,
        forced_failure_node_key: forced,
      });
    },
    onSuccess: (result) => {
      setSimulation(result);
      setError("");
    },
    onError: (reason) => {
      setSimulation(null);
      setError(
        humanizeApiError(
          reason,
          reason instanceof Error ? reason.message : "Simulation failed.",
        ),
      );
    },
  });

  const duplicate = () =>
    selected &&
    mutation.mutate(() =>
      automationsApi.workflows
        .duplicate(activeBusinessId, selected.id)
        .then((copy) => {
          setSelectedId(copy.id);
          setNotice(
            "Workflow duplicated as a draft; run history was not copied.",
          );
        }),
    );
  const changeStatus = (next: "active" | "paused" | "archived") =>
    selected &&
    mutation.mutate(() =>
      automationsApi.workflows
        .status(activeBusinessId, selected.id, next)
        .then(() => {
          setNotice(
            next === "active"
              ? "Workflow activated. Durable events may now queue runs."
              : `Workflow ${next}.`,
          );
        }),
    );
  const remove = (nodeKey: string) =>
    selected &&
    mutation.mutate(() =>
      automationsApi.nodes.remove(activeBusinessId, selected.id, nodeKey),
    );
  const reorder = (index: number, direction: number) => {
    if (!selected) return;
    const target = index + direction;
    if (target < 0 || target >= orderedNodes.length) return;
    const first = orderedNodes[index],
      second = orderedNodes[target];
    mutation.mutate(async () => {
      await automationsApi.nodes.update(
        activeBusinessId,
        selected.id,
        first.node_key,
        { order_index: second.order_index },
      );
      await automationsApi.nodes.update(
        activeBusinessId,
        selected.id,
        second.node_key,
        { order_index: first.order_index },
      );
    });
  };
  const saveNode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    const form = new FormData(event.currentTarget);
    const type = String(form.get("type")) as AutomationNodeType;
    let configuration: Record<string, unknown>;
    try {
      configuration = JSON.parse(String(form.get("configuration"))) as Record<
        string,
        unknown
      >;
    } catch {
      setError("Node configuration must be valid JSON.");
      return;
    }
    const name = String(form.get("name"));
    const branchLabel = String(form.get("branch_label")) || null;
    mutation.mutate(async () => {
      if (editing)
        await automationsApi.nodes.update(
          activeBusinessId,
          selected.id,
          editing.node_key,
          { node_type: type, name, configuration },
        );
      else {
        const node = await automationsApi.nodes.create(
          activeBusinessId,
          selected.id,
          {
            node_key: crypto.randomUUID(),
            node_type: type,
            name,
            configuration,
            position_x: 0,
            position_y: orderedNodes.length * 140,
            order_index: orderedNodes.length,
          },
        );
        const previous = orderedNodes.at(-1);
        if (previous)
          await automationsApi.edges.create(activeBusinessId, selected.id, {
            source_node_key: previous.node_key,
            target_node_key: node.node_key,
            branch_label: branchLabel,
            order_index: 0,
          });
      }
      setEditing(null);
      setAdding(false);
      setNotice("Workflow saved as a new immutable version.");
    });
  };

  if (workflows.isError)
    return (
      <>
        <PageHeader
          eyebrow="Operations"
          title="Automation Builder"
          subtitle="Durable, governed workflows for this business."
        />
        <Card>
          <div className="empty">
            <AlertCircle />
            <h3>Workflows unavailable</h3>
            <p>{humanizeApiError(workflows.error, "Try again in a moment.")}</p>
            <Button onClick={() => void workflows.refetch()}>Try again</Button>
          </div>
        </Card>
      </>
    );
  return (
    <>
      <PageHeader
        eyebrow="Operations · Easy automation"
        title="Business Autopilot"
        subtitle={`Turn common business routines into governed, dry-runnable workflows for ${activeBusiness?.name || "this business"}.`}
        action={
          <div className="toolbar">
            <Button
              onClick={duplicate}
              disabled={!selected || mutation.isPending}
            >
              <Copy /> Duplicate
            </Button>
            <Button
              variant="soft"
              onClick={() => simulate.mutate()}
              disabled={!selected || simulate.isPending}
            >
              <TestTube2 /> {simulate.isPending ? "Testing…" : "Test"}
            </Button>
            <Button
              variant="primary"
              onClick={() =>
                selected &&
                mutation.mutate(() =>
                  automationsApi.workflows
                    .validate(activeBusinessId, selected.id)
                    .then((result) => {
                      setNotice(
                        result.valid
                          ? "Graph validation passed."
                          : `Validation: ${result.errors.join(", ")}`,
                      );
                    }),
                )
              }
              disabled={!selected}
            >
              <Save /> Validate
            </Button>
          </div>
        }
      />
      {notice && (
        <div className="ai-banner">
          <Check /> {notice}
          <button className="close-btn" onClick={() => setNotice("")}>
            <X />
          </button>
        </div>
      )}
      {error && (
        <div className="failure-card">
          <AlertCircle />
          <div className="row-main">
            <strong>Action could not be completed</strong>
            <p>{error}</p>
          </div>
          <Button className="btn-sm" onClick={() => setError("")}>
            <X />
          </Button>
        </div>
      )}
      <Card data-testid="recommended-automation-packs">
        <SectionTitle title="Recommended automation packs · Choose an autopilot" action={<Badge tone="info">Uses the existing workflow engine</Badge>} />
        <p className="detail-copy">“Turn on” prepares a tenant-owned workflow for safety review. Provider delivery remains withheld until identity, consent, connection, policy, and approval checks pass.</p>
        <div className="autopilot-grid">
          {AUTOPILOT_PACKS.map((pack) => {
            const connected = hasHealthyConnection(connections.data ?? [], pack.connectionOptions);
            const writeReady = hasWriteReadyConnection(
              connections.data ?? [],
              connectorRegistry.data ?? [],
              pack.connectionOptions,
            );
            const providerState = pack.support === "setup_required"
              ? "Setup required"
              : connections.isPending || connectorRegistry.isPending
                ? "Checking provider"
                : connections.isError || connectorRegistry.isError
                  ? "Provider unavailable"
                  : writeReady
                    ? "Provider write ready"
                    : connected
                      ? "Provider write blocked"
                      : "Connection needed";
            const preparing = copilot.isPending && copilot.variables?.name === pack.title;
            return <div className={`autopilot-pack pack-${pack.support}`} key={pack.id}>
              <div className="autopilot-pack-head"><span className="autopilot-icon"><Zap /></span><Badge tone={pack.support === "setup_required" ? "neutral" : writeReady ? "success" : "warning"}>{providerState}</Badge></div>
              <h3>{pack.title}</h3>
              <p>{pack.outcome}</p>
              <div className="autopilot-trigger"><strong>Starts when</strong><span>{pack.trigger}</span></div>
              {pack.setupReason && <div className="row-copy">{pack.setupReason}</div>}
              <div className="autopilot-actions">
                {pack.support === "preparation_supported" && pack.prompt ? <Button variant="green" className="btn-sm" disabled={copilot.isPending} onClick={() => copilot.mutate({ prompt: pack.prompt!, name: pack.title })}><Zap /> {preparing ? "Preparing…" : "Turn on"}</Button> : <Link href={pack.id === "social-recommendation" ? "/marketing/social" : "/integrations"} className="btn btn-sm btn-secondary">View setup</Link>}
                {pack.connectionOptions.length > 0 && <span className="subtle">{writeReady ? "Healthy connection and write capability verified" : connected ? "Connection is healthy; provider writes are not accepted" : pack.connectionOptions.map((item) => item.replaceAll("_", " ")).join(" or ")}</span>}
              </div>
            </div>;
          })}
        </div>
      </Card>
      <Card className="intelligence-hero" data-testid="automation-copilot">
        <div className="intelligence-hero-icon"><Bot /></div>
        <div className="row-main">
          <div className="eyebrow">Need something different?</div>
          <h2>Describe the routine in plain language</h2>
          <form onSubmit={(event) => { event.preventDefault(); copilot.mutate({ prompt: copilotPrompt }); }}>
            <div className="field">
              <textarea name="prompt" required minLength={8} maxLength={4000} value={copilotPrompt} onChange={(event) => setCopilotPrompt(event.target.value)} placeholder="When someone abandons checkout, wait two hours, prepare WhatsApp if they consented, and stop immediately after purchase." />
            </div>
            <div className="toolbar"><Button variant="green" type="submit" disabled={copilot.isPending || copilotPrompt.trim().length < 8}><Bot /> {copilot.isPending ? "Compiling safely…" : "Prepare custom automation"}</Button><Button type="button" onClick={() => setAdvanced((value) => !value)}><Settings2 /> {advanced ? "Hide advanced editor" : "Advanced editor"}</Button></div>
          </form>
        </div>
      </Card>
      {copilotResult && <Card>
        <SectionTitle title="Turn-on review" action={<Badge tone={requestedExternalAction ? compiledGovernedAction && preparedWriteReady ? "success" : "warning" : "success"}>{requestedExternalAction ? compiledGovernedAction ? preparedWriteReady ? "Approval + provider required" : "Connection or write access required" : "Setup required" : "Internal draft"}</Badge>} />
        <p className="detail-copy">{copilotResult.explanation}</p>
        <div className="analysis-grid">
          <div><div className="eyebrow">Required connections</div><p>{copilotResult.required_integrations.join(", ") || "None"}</p></div>
          <div><div className="eyebrow">Missing information</div><p>{copilotResult.missing_information.join(" · ") || "None"}</p></div>
          <div><div className="eyebrow">Stop conditions</div><p>{copilotResult.stop_conditions.join(" · ")}</p></div>
        </div>
        {copilotResult.proposed_actions.length > 0 && <><div className="eyebrow">Planned governed actions</div>{copilotResult.proposed_actions.map((action, index) => { const compiled = action.execution_state === "governed_action_compiled_pending_approval"; return <div className="list-row" key={`${action.action_type}-${index}`}><div className="row-main"><strong>{index + 1}. {action.channel.replaceAll("_", " ")}</strong><div className="row-copy">{action.condition} {action.policy_behavior}</div></div><Badge tone={compiled ? "info" : "warning"}>{compiled ? "Governed action compiled" : "Setup required"}</Badge></div>; })}</>}
        <div className="toolbar"><Button variant="soft" onClick={() => simulate.mutate()} disabled={simulate.isPending || !selected}><TestTube2 /> Run safe dry-run</Button><Button onClick={() => setAdvanced(true)}><GitBranch /> Review visual workflow</Button><Button variant="green" disabled={mutation.isPending || (requestedExternalAction && !compiledGovernedAction)} onClick={() => mutation.mutate(async () => { const validation = await automationsApi.workflows.validate(activeBusinessId, copilotResult.workflow.id); if (!validation.valid) throw new Error(`Validation failed: ${validation.errors.join(", ")}`); await automationsApi.workflows.status(activeBusinessId, copilotResult.workflow.id, "active"); setNotice(preparedWriteReady ? "Business Autopilot is active. Runs create governed actions that wait for mandatory approval before durable provider dispatch; no message was sent during activation." : "Business Autopilot is active — connection or provider write access is still required. Runs remain governed and provider delivery will fail closed until setup is accepted."); })}><Check /> {requestedExternalAction && !compiledGovernedAction ? "Setup required" : preparedWriteReady ? "Complete turn on" : "Activate — connection required"}</Button></div>
      </Card>}
      <Card className={`processing-card processing-${processingState}`}>
        <div className="processing-card-head">
          <div>
            <div className="eyebrow">Durable workflow engine</div>
            <h2>Processing status</h2>
          </div>
          <Badge
            tone={
              processingState === "healthy"
                ? "success"
                : processingState === "degraded" || processingState === "checking"
                  ? "warning"
                  : "danger"
            }
          >
            {processingLabel}
          </Badge>
        </div>
        <div className="processing-metrics">
          <div><span>Queued</span><strong>{processing.data?.counts.queued ?? "—"}</strong></div>
          <div><span>Running</span><strong>{processing.data?.counts.processing ?? "—"}</strong></div>
          <div><span>Delayed</span><strong>{processing.data?.automation_event_backlog ?? "—"}</strong></div>
          <div><span>Needs attention</span><strong>{(processing.data?.counts.failed ?? 0) + (processing.data?.counts.dead_letter ?? 0)}</strong></div>
        </div>
        {recentJobs.data?.items.some((job) => job.status === "failed" || job.status === "dead_letter") && (
          <div className="approval-list" style={{ marginTop: 12 }}>
            {recentJobs.data.items
              .filter((job) => job.status === "failed" || job.status === "dead_letter")
              .slice(0, 3)
              .map((job) => (
                <div className="workflow-list-item" key={job.id}>
                  <span className="workflow-list-icon"><AlertCircle /></span>
                  <div className="row-main">
                    <strong>{job.job_type.replaceAll("_", " ")}</strong>
                    <span>{job.failure_code ?? job.status} · attempt {job.attempt_count}/{job.max_attempts}</span>
                  </div>
                  <Button
                    className="btn-sm"
                    disabled={mutation.isPending}
                    onClick={() => mutation.mutate(() => processingApi.retry(activeBusinessId, job.id))}
                  >
                    Retry
                  </Button>
                </div>
              ))}
          </div>
        )}
        <div className="processing-note">
          <span>{processingCopy}</span>
          <small>External sending, publishing, and spend remain disabled.</small>
        </div>
      </Card>
      <div className="workflow-layout" style={{ display: advanced ? undefined : "none" }} data-testid="advanced-workflow-editor">
        <Card className="workflow-sidebar">
          <SectionTitle
            title="Workflows"
            action={
              <Button
                variant="soft"
                className="btn-sm"
                onClick={() => setCreating(true)}
              >
                <Plus /> New workflow
              </Button>
            }
          />
          {workflows.isLoading && <p className="subtle">Loading workflows…</p>}
          {workflows.data?.items.map((workflow) => (
            <button
              className={`workflow-list-item ${workflow.id === selectedId ? "active" : ""}`}
              onClick={() => {
                setSelectedId(workflow.id);
                setSelectedRun(null);
                setSimulation(null);
              }}
              key={workflow.id}
            >
              <span className="workflow-list-icon">
                <Zap />
              </span>
              <div className="row-main">
                <strong>{workflow.name}</strong>
                <span>
                  {workflow.trigger_type.replaceAll("_", " ")} ·{" "}
                  {workflow.last_run_status ?? "never run"}
                </span>
              </div>
              <Badge
                tone={
                  workflow.status === "active"
                    ? "success"
                    : workflow.status === "paused"
                      ? "warning"
                      : "neutral"
                }
              >
                {workflow.status}
              </Badge>
            </button>
          ))}
          {workflows.data && !workflows.data.items.length && (
            <EmptyState
              compact
              icon={<Zap />}
              title="No workflows yet"
              description="Create a durable draft to begin."
              action={<Button variant="green" className="btn-sm" onClick={() => setCreating(true)}><Plus /> Create workflow</Button>}
            />
          )}
        </Card>
        <div className="workflow-main">
          {detail.isLoading ? (
            <Card>
              <div className="empty">
                <RefreshCw className="spin" />
                <p>Loading workflow graph…</p>
              </div>
            </Card>
          ) : selected ? (
            <>
              <Card className="workflow-builder-head">
                <div>
                  <div className="eyebrow">
                    Version {selected.current_version} ·{" "}
                    {selected.trigger_type.replaceAll("_", " ")}
                  </div>
                  <h2>{selected.name}</h2>
                  <p className="subtle">
                    {selected.description || "No description yet."}
                  </p>
                </div>
                <div className="toolbar">
                  <Badge
                    tone={
                      selected.status === "active"
                        ? "success"
                        : selected.status === "paused"
                          ? "warning"
                          : "neutral"
                    }
                  >
                    {selected.status}
                  </Badge>
                  {selected.status === "active" ? (
                    <Button
                      variant="danger"
                      className="btn-sm"
                      onClick={() => changeStatus("paused")}
                    >
                      Pause
                    </Button>
                  ) : (
                    selected.status !== "archived" && (
                      <Button
                        variant="green"
                        className="btn-sm"
                        onClick={() => changeStatus("active")}
                      >
                        Enable
                      </Button>
                    )
                  )}
                  {selected.status !== "archived" && (
                    <Button
                      className="btn-sm"
                      onClick={() => changeStatus("archived")}
                    >
                      Archive
                    </Button>
                  )}
                </div>
              </Card>
              <Card className="workflow-canvas">
                <div className="workflow-canvas-grid" />
                {orderedNodes.map((node, index) => {
                  const Icon = nodeIcons[node.node_type];
                  const incoming = selected.edges.find(
                    (edge) => edge.target_node_key === node.node_key,
                  );
                  return (
                    <div className="builder-node-wrap" key={node.node_key}>
                      <div
                        className={`builder-node ${node.node_type === "condition" || node.node_type === "branch" ? "branch-node" : ""}`}
                      >
                        <div className="builder-node-icon">
                          <Icon />
                        </div>
                        <div className="row-main">
                          <div className="builder-node-type">
                            {labels[node.node_type]}
                            {incoming?.branch_label && (
                              <Badge
                                tone={
                                  incoming.branch_label === "true"
                                    ? "success"
                                    : "warning"
                                }
                              >
                                {incoming.branch_label}
                              </Badge>
                            )}
                          </div>
                          <strong>{node.name}</strong>
                          <span>{JSON.stringify(node.configuration)}</span>
                        </div>
                        <div className="builder-node-actions">
                          <button
                            onClick={() => reorder(index, -1)}
                            disabled={index === 0 || mutation.isPending}
                            aria-label="Move node up"
                          >
                            <ArrowUp />
                          </button>
                          <button
                            onClick={() => reorder(index, 1)}
                            disabled={
                              index === orderedNodes.length - 1 ||
                              mutation.isPending
                            }
                            aria-label="Move node down"
                          >
                            <ArrowDown />
                          </button>
                          <button
                            onClick={() => setEditing(node)}
                            aria-label="Edit node"
                          >
                            <Pencil />
                          </button>
                          <button
                            onClick={() => remove(node.node_key)}
                            aria-label="Remove node"
                          >
                            <Trash2 />
                          </button>
                        </div>
                      </div>
                      {index < orderedNodes.length - 1 && (
                        <div className="builder-connector">
                          <i />
                          <ArrowDown />
                        </div>
                      )}
                    </div>
                  );
                })}
                <Button
                  variant="soft"
                  className="add-node-button"
                  onClick={() => setAdding(true)}
                  disabled={selected.status === "archived"}
                >
                  <Plus /> Add node
                </Button>
                {!orderedNodes.length && (
                  <div className="empty">
                    <GitBranch />
                    <h3>Build the first step</h3>
                    <p>
                      Add one trigger, connected steps, and at least one End
                      node.
                    </p>
                  </div>
                )}
              </Card>
              <Card className="workflow-test-panel">
                <div className="workflow-test-head">
                  <div>
                    <div className="eyebrow">Safe test mode</div>
                    <h2>No business mutation or external action will occur</h2>
                  </div>
                  <label className="simulate-toggle">
                    <input
                      type="checkbox"
                      checked={simulateFailure}
                      onChange={(event) =>
                        setSimulateFailure(event.target.checked)
                      }
                    />{" "}
                    Force a node failure
                  </label>
                </div>
                <div className="field">
                  <label>Strict test payload (JSON)</label>
                  <textarea
                    value={testPayload}
                    onChange={(event) => setTestPayload(event.target.value)}
                  />
                </div>
                {!simulation && (
                  <p className="subtle">
                    The backend validates the exact immutable version, resolves
                    branches, and reports planned actions, approvals, and
                    delays.
                  </p>
                )}
                {simulation && (
                  <div className="test-list">
                    {simulation.trace.map((step) => (
                      <div
                        className={`test-step ${step.status === "failed" ? "failed" : ""}`}
                        key={step.node_key}
                      >
                        {step.status === "failed" ? (
                          <AlertCircle />
                        ) : step.status === "waiting" ||
                          step.status === "planned" ? (
                          <RefreshCw />
                        ) : (
                          <CheckCircle2 />
                        )}{" "}
                        <div>
                          <strong>{step.name}</strong>
                          <div className="row-copy">
                            {step.summary}
                            {step.branch_outcome
                              ? ` · branch ${step.branch_outcome}`
                              : ""}
                          </div>
                        </div>
                      </div>
                    ))}
                    {simulation.planned_actions.length > 0 && (
                      <div className="ai-banner">
                        <Settings2 /> {simulation.planned_actions.length}{" "}
                        action(s) planned only; dispatch is disabled.
                      </div>
                    )}
                    {simulation.approvals.length > 0 && (
                      <p className="subtle">
                        Approval points: {simulation.approvals.length}
                      </p>
                    )}
                    {simulation.delays.length > 0 && (
                      <p className="subtle">
                        Durable delays: {simulation.delays.length}
                      </p>
                    )}
                    {simulation.errors.length > 0 && (
                      <div className="failure-card">
                        <AlertCircle />
                        <div>
                          <strong>Simulation stopped safely</strong>
                          <p>{simulation.errors.join(", ")}</p>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </Card>
              <Card>
                <SectionTitle
                  title="Run history"
                  action={
                    <Badge tone="neutral">{runs.data?.total ?? 0} runs</Badge>
                  }
                />
                {runs.isLoading && <p className="subtle">Loading activity…</p>}
                <div className="approval-list">
                  {runs.data?.items.map((run) => (
                    <button
                      className="workflow-list-item"
                      onClick={() => setSelectedRun(run)}
                      key={run.id}
                    >
                      <span className="workflow-list-icon">
                        <History />
                      </span>
                      <div className="row-main">
                        <strong>
                          {run.trigger_type} · version{" "}
                          {run.version ?? selected.current_version}
                        </strong>
                        <span>
                          {when(run.started_at || run.created_at)} ·{" "}
                          {run.failure_code || waitingReasonLabel(run.waiting_reason)}
                        </span>
                      </div>
                      <Badge
                        tone={
                          run.status === "succeeded"
                            ? "success"
                            : run.status === "failed"
                              ? "danger"
                              : run.status === "waiting"
                                ? "warning"
                                : "neutral"
                        }
                      >
                        {run.status}
                      </Badge>
                    </button>
                  ))}
                </div>
                {runs.data && !runs.data.items.length && (
                  <div className="empty">
                    <History />
                    <h3>No runs yet</h3>
                    <p>
                      Durable run history appears after an event or manual queue
                      operation.
                    </p>
                  </div>
                )}
              </Card>
            </>
          ) : (
            <Card className="workflow-welcome-card">
              <EmptyState
                icon={<Zap />}
                title="Select or create a workflow"
                description="Choose a workflow from the list, or create a durable draft for this business."
                action={<Button variant="green" onClick={() => setCreating(true)}><Plus /> Create workflow</Button>}
              />
            </Card>
          )}
        </div>
      </div>
      {creating && (
        <Modal
          title="Create workflow"
          description="The workflow starts as a durable draft."
          onClose={() => setCreating(false)}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              mutation.mutate(() =>
                automationsApi.workflows
                  .create(activeBusinessId, {
                    name: String(form.get("name")),
                    description: String(form.get("description")) || null,
                    trigger_type: String(form.get("trigger_type")),
                    timezone: activeBusiness?.timezone || "UTC",
                  })
                  .then((workflow) => {
                    setSelectedId(workflow.id);
                    setCreating(false);
                  }),
              );
            }}
          >
            <div className="form-grid">
              <div className="field full">
                <label>Name</label>
                <input name="name" required maxLength={180} />
              </div>
              <div className="field full">
                <label>Description</label>
                <textarea name="description" maxLength={2000} />
              </div>
              <div className="field full">
                <label>Trigger</label>
                <select name="trigger_type" defaultValue="manual_test">
                  <option value="manual_test">Manual test</option>
                  <option value="customer_created">Customer created</option>
                  <option value="lead_created">Lead created</option>
                  <option value="lead_stage_changed">Lead stage changed</option>
                  <option value="order_created">Order created</option>
                  <option value="order_status_changed">
                    Order status changed
                  </option>
                  <option value="inbound_message_recorded">
                    Inbound message recorded
                  </option>
                  {schedulingEnabled && (
                    <option value="appointment_created">
                      Appointment created
                    </option>
                  )}
                  <option value="campaign_status_changed">
                    Campaign status changed
                  </option>
                  <option value="opportunity_created">
                    Opportunity created
                  </option>
                  <option value="ai_execution_completed">
                    AI execution completed
                  </option>
                </select>
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setCreating(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={mutation.isPending}
              >
                Create draft
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {(editing || adding) && selected && (
        <Modal
          title={editing ? "Configure node" : "Add node"}
          description="Configuration is validated against the selected allowlisted node type."
          onClose={() => {
            setEditing(null);
            setAdding(false);
          }}
        >
          <NodeForm
            node={editing}
            triggerType={selected.trigger_type}
            onSubmit={saveNode}
            pending={mutation.isPending}
          />
        </Modal>
      )}
      {selectedRun && (
        <Modal
          wide
          title={`Run ${selectedRun.id.slice(0, 8)}`}
          description={`${selectedRun.status} · started ${when(selectedRun.started_at || selectedRun.created_at)}`}
          onClose={() => setSelectedRun(null)}
        >
          <div className="test-list">
            {nodeHistory.isLoading && <p>Loading node history…</p>}
            {nodeHistory.data?.items.map((item) => (
              <div
                className={`test-step ${item.status === "failed" ? "failed" : ""}`}
                key={item.id}
              >
                {item.status === "failed" ? (
                  <AlertCircle />
                ) : item.status === "waiting" ? (
                  <RefreshCw />
                ) : (
                  <CheckCircle2 />
                )}
                <div>
                  <strong>
                    {item.node_name || item.node_type} · attempt {item.attempt}
                  </strong>
                  <div className="row-copy">
                    {item.result_summary || item.failure_code || item.status}
                    {item.resume_at ? ` · resumes ${when(item.resume_at)}` : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {selectedRun.status === "waiting" ||
          selectedRun.status === "queued" ? (
            <div className="modal-foot">
              {selectedRun.waiting_reason !== "action_execution" && <Button
                  variant="danger"
                  onClick={() =>
                    mutation.mutate(() =>
                      automationsApi.runs
                        .cancel(activeBusinessId, selectedRun.id)
                        .then(() => setSelectedRun(null)),
                    )
                  }
                >
                  Cancel run
                </Button>}
              <p className="subtle">
                {selectedRun.waiting_reason === "action_execution"
                  ? "Provider dispatch has already been queued or started, so canceling the workflow cannot safely retract it. The run will resume from the durable provider result."
                  : "Waiting and queued runs resume automatically when eligible."}
              </p>
            </div>
          ) : null}
        </Modal>
      )}
    </>
  );
}

function NodeForm({
  node,
  triggerType,
  onSubmit,
  pending,
}: {
  node: AutomationNode | null;
  triggerType: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  pending: boolean;
}) {
  const [type, setType] = useState<AutomationNodeType>(
    node?.node_type ?? "action",
  );
  return (
    <form onSubmit={onSubmit}>
      <div className="form-grid">
        <div className="field">
          <label>Node type</label>
          <select
            name="type"
            value={type}
            onChange={(event) =>
              setType(event.target.value as AutomationNodeType)
            }
          >
            {nodeTypes.map((value) => (
              <option value={value} key={value}>
                {labels[value]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>Incoming branch</label>
          <select name="branch_label" defaultValue="">
            <option value="">Main path</option>
            <option value="true">TRUE</option>
            <option value="false">FALSE</option>
          </select>
        </div>
        <div className="field full">
          <label>Label</label>
          <input
            name="name"
            required
            defaultValue={node?.name ?? labels[type]}
          />
        </div>
        <div className="field full">
          <label>Typed configuration (JSON)</label>
          <textarea
            key={`${node?.node_key ?? "new"}-${type}`}
            name="configuration"
            required
            defaultValue={JSON.stringify(
              node?.node_type === type
                ? node.configuration
                : defaultConfiguration(type, triggerType),
              null,
              2,
            )}
          />
        </div>
      </div>
      <div className="modal-foot">
        <Button variant="primary" type="submit" disabled={pending}>
          {pending ? "Saving…" : "Save node"}
        </Button>
      </div>
    </form>
  );
}
