import { useState } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { AlertCircle, Check, ClipboardCheck, Eye, X } from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
} from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import { automationsApi, type Approval } from "@/services/automations";

const filters: Approval["status"][] = [
  "pending",
  "approved",
  "rejected",
  "expired",
];
const title = (item: Approval) =>
  item.action?.description ||
  (item.workflow
    ? `${item.workflow.workflow_name} · ${item.workflow.node_name}`
    : "Governed approval request");

export function ApprovalsPage() {
  const { activeBusinessId } = useBusiness();
  const client = useQueryClient();
  const [filter, setFilter] = useState<Approval["status"]>("pending");
  const [selectedId, setSelectedId] = useState("");
  const [decision, setDecision] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const lists = useQueries({
    queries: filters.map((status) => ({
      queryKey: ["approvals", activeBusinessId, status],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        automationsApi.approvals.list(activeBusinessId, status, signal),
      enabled: Boolean(activeBusinessId),
    })),
  });
  const selectedIndex = filters.indexOf(filter);
  const list = lists[selectedIndex];
  const selected = useQuery({
    queryKey: ["approvals", activeBusinessId, "detail", selectedId],
    queryFn: ({ signal }) =>
      automationsApi.approvals.get(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
  });
  const decide = useMutation({
    mutationFn: (choice?: "approve" | "reject") => {
      if (!selectedId) throw new Error("Choose an approval request.");
      return (choice ?? decision ?? "reject") === "approve"
        ? automationsApi.approvals.approve(
            activeBusinessId,
            selectedId,
            note.trim() || null,
          )
        : automationsApi.approvals.reject(
            activeBusinessId,
            selectedId,
            note.trim() || null,
          );
    },
    onSuccess: () => {
      setSelectedId("");
      setDecision(null);
      setNote("");
      setError("");
      void client.invalidateQueries({
        queryKey: ["approvals", activeBusinessId],
      });
      void client.invalidateQueries({
        queryKey: ["automations", activeBusinessId],
      });
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "The approval decision could not be recorded.",
        ),
      ),
  });
  const items = list.data?.items ?? [];
  return (
    <>
      <PageHeader
        eyebrow="Human oversight · Durable queue"
        title="Approval Center"
        subtitle="AIAction and workflow review requests share one governed, tenant-owned queue."
        action={
          <Badge tone="warning">
            {lists[0].data?.items.length ?? 0} pending
          </Badge>
        }
      />
      <div className="tabs">
        {filters.map((status) => (
          <button
            className={`tab ${filter === status ? "active" : ""}`}
            onClick={() => setFilter(status)}
            key={status}
          >
            {status}{" "}
            <span>
              {lists[filters.indexOf(status)].data?.items.length ?? 0}
            </span>
          </button>
        ))}
      </div>
      {list.isError ? (
        <Card>
          <div className="empty">
            <AlertCircle />
            <h3>Approvals unavailable</h3>
            <p>{humanizeApiError(list.error, "Try again in a moment.")}</p>
            <Button onClick={() => void list.refetch()}>Try again</Button>
          </div>
        </Card>
      ) : (
        <div className="approval-list">
          {items.map((item) => (
            <Card className="approval-card" key={item.id}>
              <div className="approval-top">
                <div>
                  <div className="eyebrow">
                    {item.target_type === "workflow_node"
                      ? "Workflow approval"
                      : item.action?.action_type?.replaceAll("_", " ") ||
                        "Governed AI action"}
                  </div>
                  <h2>{title(item)}</h2>
                </div>
                <Badge
                  tone={
                    item.status === "pending"
                      ? "warning"
                      : item.status === "approved"
                        ? "success"
                        : "danger"
                  }
                >
                  {item.status}
                </Badge>
              </div>
              <div className="approval-reason">
                <strong>Policy reason</strong>
                <p>{item.reason_code.replaceAll("_", " ")}</p>
                <span>
                  Requested{" "}
                  {new Intl.DateTimeFormat(undefined, {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(item.requested_at))}
                  {item.action
                    ? ` · ${item.action.risk_level} risk`
                    : " · internal workflow gate"}
                </span>
              </div>
              <div className="toolbar">
                <Button
                  className="btn-sm"
                  onClick={() => setSelectedId(item.id)}
                >
                  <Eye /> Details
                </Button>
                {item.status === "pending" && (
                  <>
                    <Button
                      variant="green"
                      className="btn-sm"
                      onClick={() => {
                        setSelectedId(item.id);
                        setDecision("approve");
                      }}
                    >
                      <Check /> Approve
                    </Button>
                    <Button
                      variant="danger"
                      className="btn-sm"
                      onClick={() => {
                        setSelectedId(item.id);
                        setDecision("reject");
                      }}
                    >
                      <X /> Reject
                    </Button>
                  </>
                )}
              </div>
            </Card>
          ))}
          {list.isLoading && (
            <Card>
              <div className="empty">
                <p>Loading approvals…</p>
              </div>
            </Card>
          )}
          {list.data && !items.length && (
            <Card>
              <div className="empty">
                <ClipboardCheck />
                <h3>No {filter} requests</h3>
                <p>
                  Governed AI actions and workflow gates appear here when they
                  need attention.
                </p>
              </div>
            </Card>
          )}
        </div>
      )}
      {selectedId && (
        <Modal
          title={selected.data ? title(selected.data) : "Approval detail"}
          description="Approving an AIAction records the human decision, revalidates policy and spend, and queues a durable attempt when its connector preconditions are satisfied."
          onClose={() => {
            setSelectedId("");
            setDecision(null);
            setNote("");
            setError("");
          }}
        >
          <>
            {selected.isLoading && <p>Loading approval context…</p>}
            {selected.isError && (
              <p className="form-error">
                {humanizeApiError(
                  selected.error,
                  "Approval detail could not be loaded.",
                )}
              </p>
            )}
            {selected.data && (
              <>
                <div className="analysis-grid">
                  <Card>
                    <div className="eyebrow">Target</div>
                    <strong>
                      {selected.data.target_type === "workflow_node"
                        ? "Workflow gate"
                        : "AIAction"}
                    </strong>
                  </Card>
                  <Card>
                    <div className="eyebrow">Status</div>
                    <Badge
                      tone={
                        selected.data.status === "pending"
                          ? "warning"
                          : selected.data.status === "approved"
                            ? "success"
                            : "danger"
                      }
                    >
                      {selected.data.status}
                    </Badge>
                  </Card>
                  <Card>
                    <div className="eyebrow">Reason</div>
                    <strong>{selected.data.reason_code}</strong>
                  </Card>
                  <Card>
                    <div className="eyebrow">Expires</div>
                    <strong>
                      {selected.data.expires_at
                        ? new Date(selected.data.expires_at).toLocaleString()
                        : "No expiry"}
                    </strong>
                  </Card>
                </div>
                {selected.data.action && (
                  <Card>
                    <div className="eyebrow">Governed action context</div>
                    <h2>
                      {selected.data.action.action_type.replaceAll("_", " ")}
                    </h2>
                    <p>{selected.data.action.description}</p>
                    <p className="subtle">
                      {selected.data.action.risk_level} risk · action status{" "}
                      {selected.data.action.status}
                    </p>
                    <div className="analysis-grid">
                      <div className="mini-detail">
                        <span>Provider / channel</span>
                        <strong>{selected.data.action.provider_channel}</strong>
                      </div>
                      <div className="mini-detail">
                        <span>Affected entity</span>
                        <strong>{selected.data.action.affected_entity}</strong>
                      </div>
                      <div className="mini-detail">
                        <span>Audience / recipient</span>
                        <strong>{selected.data.action.audience_or_recipient || "Not applicable"}</strong>
                      </div>
                      <div className="mini-detail">
                        <span>Budget</span>
                        <strong>{selected.data.action.budget_summary || "No spend in this action"}</strong>
                      </div>
                    </div>
                    {Object.keys(selected.data.action.payload_summary).length > 0 && (
                      <div className="approval-reason">
                        <strong>Safe payload summary</strong>
                        {Object.entries(selected.data.action.payload_summary).map(([key, value]) => (
                          <p key={key}>{key.replaceAll("_", " ")} · {String(value)}</p>
                        ))}
                      </div>
                    )}
                    <p className="subtle">
                      Policy · {selected.data.action.policy_decision || "not evaluated"}
                      {selected.data.action.policy_reason_code ? ` · ${selected.data.action.policy_reason_code.replaceAll("_", " ")}` : ""}
                    </p>
                  </Card>
                )}
                {selected.data.workflow && (
                  <Card>
                    <div className="eyebrow">Workflow context</div>
                    <h2>{selected.data.workflow.workflow_name}</h2>
                    <p>
                      {selected.data.workflow.node_name} · run{" "}
                      {selected.data.workflow.run_id.slice(0, 8)}
                    </p>
                    <p className="subtle">
                      Run remains {selected.data.workflow.run_status} until
                      explicitly resumed.
                    </p>
                  </Card>
                )}
                {selected.data.decision_note && (
                  <Card>
                    <div className="eyebrow">Decision note</div>
                    <p>{selected.data.decision_note}</p>
                  </Card>
                )}
                {selected.data.status === "pending" && (
                  <>
                    <div className="field" style={{ marginTop: 14 }}>
                      <label>Decision note (optional)</label>
                      <textarea
                        value={note}
                        maxLength={2000}
                        onChange={(event) => setNote(event.target.value)}
                        placeholder="Add concise review context"
                      />
                    </div>
                    <div className="modal-foot">
                      <Button
                        variant="danger"
                        disabled={decide.isPending}
                        onClick={() => {
                          setDecision("reject");
                          decide.mutate("reject");
                        }}
                      >
                        <X /> Reject
                      </Button>
                      <Button
                        variant="green"
                        disabled={decide.isPending}
                        onClick={() => {
                          setDecision("approve");
                          decide.mutate("approve");
                        }}
                      >
                        <Check /> Approve
                      </Button>
                    </div>
                  </>
                )}
                {selected.data.status === "approved" &&
                  selected.data.action?.status === "ready" && (
                    <div className="modal-foot">
                      <Button
                        variant="green"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate("approve")}
                      >
                        <Check /> Revalidate &amp; queue execution
                      </Button>
                    </div>
                  )}
                {error && <p className="form-error">{error}</p>}
              </>
            )}
          </>
        </Modal>
      )}
    </>
  );
}
