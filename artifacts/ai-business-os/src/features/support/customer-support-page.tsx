import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  Check,
  Clock3,
  Facebook,
  Globe2,
  Headphones,
  Instagram,
  Mail,
  MessageCircle,
  Package,
  Play,
  Search,
  Send,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { Link } from "wouter";
import { useBusiness } from "@/business-context";
import { Avatar, Badge, Button, Card, PageHeader } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import type {
  ConversationChannel,
  ConversationMessage,
  SupportCase,
  SupportCaseStatus,
} from "@/services/api-types";
import { operationsApi } from "@/services/operations";

const filters: ReadonlyArray<{ value: "" | SupportCaseStatus; label: string }> = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "ai_handling", label: "AI handling" },
  { value: "escalated", label: "Escalated" },
  { value: "waiting_for_customer", label: "Waiting" },
  { value: "resolved", label: "Resolved" },
];

function when(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function label(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function ChannelIcon({ channel }: { channel: ConversationChannel }) {
  const icons: Partial<Record<ConversationChannel, ReactNode>> = {
    facebook: <Facebook />,
    instagram: <Instagram />,
    whatsapp: <MessageCircle />,
    website: <Globe2 />,
    email: <Mail />,
  };
  return <span className={`channel-icon channel-${channel}`}>{icons[channel] ?? <MessageCircle />}</span>;
}

function MessageBubble({ message }: { message: ConversationMessage }) {
  const role = message.sender_type === "customer" ? "Customer" : message.sender_type === "ai" ? "AI agent" : message.sender_type === "user" ? "Human agent" : "System event";
  const Icon = message.sender_type === "customer" ? UserRound : message.sender_type === "ai" ? Bot : message.sender_type === "user" ? Headphones : MessageCircle;
  return (
    <div className={`omni-message ${message.direction} sender-${message.sender_type}`}>
      <div className="omni-message-label"><Icon /> {role}</div>
      <div className="omni-bubble"><p>{message.content}</p><span>{when(message.sent_at)} · {message.delivery_status}</span></div>
    </div>
  );
}

function metricCards(metrics: { open_issues: number; ai_handling: number; escalated: number; waiting_for_customer: number; resolved_today: number }) {
  return [
    ["Open issues", metrics.open_issues, <ShieldCheck />],
    ["AI handling", metrics.ai_handling, <Bot />],
    ["Escalated", metrics.escalated, <AlertCircle />],
    ["Waiting", metrics.waiting_for_customer, <Clock3 />],
    ["Resolved today", metrics.resolved_today, <Check />],
  ] as const;
}

export function CustomerSupportPage() {
  const { activeBusinessId } = useBusiness();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<"" | SupportCaseStatus>("");
  const [channel, setChannel] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [mobileCaseClosed, setMobileCaseClosed] = useState(false);
  const [message, setMessage] = useState("");
  const [pendingSend, setPendingSend] = useState<{
    conversationId: string;
    content: string;
    clientRequestId: string;
  } | null>(null);
  const [resolution, setResolution] = useState("");
  const [actionError, setActionError] = useState("");

  const cases = useQuery({
    queryKey: ["support", activeBusinessId, "cases", search, status, channel],
    queryFn: ({ signal }) => operationsApi.support.list(activeBusinessId, {
      search,
      status: status || undefined,
      channel: channel || undefined,
      page: 1,
      pageSize: 50,
    }, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 10_000,
  });
  const metrics = useQuery({
    queryKey: ["support", activeBusinessId, "metrics"],
    queryFn: ({ signal }) => operationsApi.support.metrics(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 10_000,
  });
  const detail = useQuery({
    queryKey: ["support", activeBusinessId, "case", selectedId],
    queryFn: ({ signal }) => operationsApi.support.get(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
    refetchInterval: 10_000,
  });

  useEffect(() => {
    setSelectedId("");
    setMobileCaseClosed(false);
    setMessage("");
    setPendingSend(null);
    setResolution("");
    setActionError("");
  }, [activeBusinessId]);

  useEffect(() => {
    const first = cases.data?.items[0];
    if (!selectedId && first && !mobileCaseClosed) {
      setSelectedId(first.id);
    }
  }, [cases.data, selectedId, mobileCaseClosed]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["support", activeBusinessId] });
    void queryClient.invalidateQueries({ queryKey: ["operations", activeBusinessId, "conversations"] });
  };
  const updateCase = useMutation({
    mutationFn: ({ caseId, data }: { caseId: string; data: Parameters<typeof operationsApi.support.update>[2] }) => operationsApi.support.update(activeBusinessId, caseId, data),
    onSuccess: () => { setResolution(""); setActionError(""); refresh(); },
    onError: (error) => setActionError(humanizeApiError(error, "The support case could not be updated.")),
  });
  const control = useMutation({
    mutationFn: ({ conversationId, action }: { conversationId: string; action: "take_over" | "resume_ai" | "escalate" | "reopen" }) => operationsApi.conversations.control(activeBusinessId, conversationId, action),
    onSuccess: () => { setActionError(""); refresh(); },
    onError: (error) => setActionError(humanizeApiError(error, "Conversation handling could not be updated.")),
  });
  const send = useMutation({
    mutationFn: (request: {
      conversationId: string;
      content: string;
      clientRequestId: string;
    }) =>
      operationsApi.conversations.send(
        activeBusinessId,
        request.conversationId,
        {
          content: request.content,
          client_request_id: request.clientRequestId,
        },
      ),
    onSuccess: () => {
      setMessage("");
      setPendingSend(null);
      setActionError("");
      refresh();
    },
    onError: (error) =>
      setActionError(humanizeApiError(error, "The reply could not be sent.")),
  });

  const selected = detail.data;
  const submitReply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = message.trim();
    if (
      !selected?.conversation?.can_send_externally ||
      !content ||
      send.isPending
    ) {
      return;
    }

    const request =
      pendingSend?.conversationId === selected.conversation_id &&
      pendingSend.content === content
        ? pendingSend
        : {
            conversationId: selected.conversation_id,
            content,
            clientRequestId: crypto.randomUUID(),
          };

    setPendingSend(request);
    send.mutate(request);
  };
  const resolve = () => {
    if (!selected || !resolution.trim()) return;
    updateCase.mutate({ caseId: selected.id, data: { status: "resolved", resolution_summary: resolution.trim() } });
  };

  if (cases.isError) {
    return <Card><div className="empty"><AlertCircle /><h3>We couldn't load Customer Support</h3><p>{humanizeApiError(cases.error, "Try again in a moment.")}</p><Button onClick={() => void cases.refetch()}>Try again</Button></div></Card>;
  }

  return (
    <>
      <PageHeader
        eyebrow="Customer communications"
        title="Customer Support"
        subtitle="Resolve customer issues with one operational layer over the original conversation."
      />
      <div className="support-metrics" aria-label="Support metrics">
        {metrics.data ? metricCards(metrics.data).map(([name, value, icon]) => <Card key={name}><span>{icon}</span><strong>{value}</strong><small>{name}</small></Card>) : <Card className="support-metrics-loading"><span>Loading support metrics…</span></Card>}
      </div>
      <div className="support-filter-bar">
        <div className="support-status-filters" role="group" aria-label="Filter by status">
          {filters.map((item) => <button type="button" className={status === item.value ? "active" : ""} onClick={() => setStatus(item.value)} key={item.value || "all"}>{item.label}</button>)}
        </div>
        <label className="support-channel-filter">Channel
          <select value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="">All channels</option>
            <option value="facebook">Messenger</option>
            <option value="instagram">Instagram</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="website">Website</option>
            <option value="email">Email</option>
          </select>
        </label>
      </div>
      <div className={`card support-workspace ${selectedId ? "mobile-case-open" : ""}`}>
        <section className="support-case-list" aria-label="Support cases">
          <div className="inbox-search"><Search /><label className="sr-only" htmlFor="support-search">Search support cases</label><input id="support-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search cases, customers, orders" /></div>
          {cases.isLoading && <div className="empty compact-empty"><p>Loading support cases…</p></div>}
          {cases.data?.items.map((item) => (
            <button type="button" className={`support-case-item ${selectedId === item.id ? "active" : ""}`} onClick={() => {
                  setSelectedId(item.id);
                  setMobileCaseClosed(false);
                  setActionError("");
                  setResolution("");
                }} aria-pressed={selectedId === item.id} key={item.id}>
              <div className="support-case-top"><span className="support-case-number">{item.case_number}</span><span className="time">{when(item.last_activity_at)}</span></div>
              <div className="support-case-customer"><ChannelIcon channel={item.channel} /><strong>{item.customer_display_name || "Unlinked customer"}</strong></div>
              <p>{item.issue_summary}</p>
              <div className="support-case-tags"><Badge tone={item.priority === "urgent" || item.priority === "high" ? "warning" : "neutral"}>{item.priority}</Badge><Badge tone={item.status === "escalated" ? "warning" : item.status === "resolved" ? "neutral" : "success"}>{label(item.status)}</Badge><span>{label(item.category)}</span></div>
            </button>
          ))}
          {cases.data && !cases.data.items.length && <div className="empty"><ShieldCheck /><h3>No support cases found</h3><p>{search || status || channel ? "Try changing the search or filters." : "AI and team escalations will appear here."}</p></div>}
        </section>

        <section className="support-thread" aria-label="Selected support case">
          {detail.isLoading && selectedId && <div className="empty"><p>Loading case…</p></div>}
          {selected ? (
            <>
              <header className="chat-head">
                <div className="conversation-contact"><button
                    className="mobile-thread-back"
                    type="button"
                    onClick={() => {
                      setSelectedId("");
                      setMobileCaseClosed(true);
                      setActionError("");
                      setResolution("");
                    }}
                    aria-label="Back to support cases"
                  >
                    <ArrowLeft />
                  </button><Avatar name={selected.customer_display_name || selected.channel} /><div><div className="row-title">{selected.customer_display_name || "Unlinked customer"}</div><div className="row-copy">{selected.case_number} · <ChannelIcon channel={selected.channel} /> {label(selected.channel)}</div></div></div>
                <Badge tone={selected.status === "escalated" ? "warning" : selected.status === "resolved" ? "neutral" : "success"}>{label(selected.status)}</Badge>
              </header>
              <div className="conversation-control-bar">
                {selected.status === "resolved" || selected.status === "closed" ? (
                  <Button
                    className="btn-sm"
                    onClick={() =>
                      control.mutate({
                        conversationId: selected.conversation_id,
                        action: "reopen",
                      })
                    }
                    disabled={control.isPending}
                  >
                    Reopen
                  </Button>
                ) : (
                  <>
                    {selected.conversation?.handling_state === "ai_active" ? (
                      <Button
                        className="btn-sm"
                        onClick={() =>
                          control.mutate({
                            conversationId: selected.conversation_id,
                            action: "take_over",
                          })
                        }
                        disabled={control.isPending}
                      >
                        <Headphones /> Take over
                      </Button>
                    ) : (
                      <Button
                        variant="green"
                        className="btn-sm"
                        onClick={() =>
                          control.mutate({
                            conversationId: selected.conversation_id,
                            action: "resume_ai",
                          })
                        }
                        disabled={control.isPending}
                      >
                        <Play /> Resume AI
                      </Button>
                    )}

                    {selected.status !== "escalated" && (
                      <Button
                        className="btn-sm"
                        onClick={() =>
                          control.mutate({
                            conversationId: selected.conversation_id,
                            action: "escalate",
                          })
                        }
                        disabled={control.isPending}
                      >
                        <AlertCircle /> Escalate
                      </Button>
                    )}
                  </>
                )}
              </div>
              <div className="chat-body omni-thread">
                <div className="support-issue-summary"><span>{label(selected.category)} · {label(selected.priority)} priority</span><strong>{selected.issue_summary}</strong>{selected.escalation_reason && <p><AlertCircle /> {selected.escalation_reason}</p>}</div>
                {selected.conversation?.messages.map((item) => <MessageBubble message={item} key={item.id} />)}
                {!selected.conversation?.messages.length && <div className="empty"><MessageCircle /><h3>No conversation messages</h3><p>The originating conversation has no recorded messages.</p></div>}
              </div>
              {selected.status !== "resolved" && selected.status !== "closed" && (
                <form className="chat-compose omni-compose" onSubmit={submitReply}>
                  <label className="sr-only" htmlFor="support-message">Reply through the original channel</label>
                  <input id="support-message" value={message} onChange={(event) => setMessage(event.target.value)} placeholder={selected.conversation?.can_send_externally ? "Reply through the original channel…" : "External reply unavailable"} disabled={!selected.conversation?.can_send_externally || send.isPending} />
                  <Button variant="green" type="submit" disabled={!message.trim() || !selected.conversation?.can_send_externally || send.isPending} aria-label="Send external support reply"><Send /></Button>
                </form>
              )}
              {selected.conversation && !selected.conversation.can_send_externally && <div className="send-unavailable"><AlertCircle /> {selected.conversation.send_unavailable_reason || "External sending is unavailable."}</div>}
              {actionError && <p className="form-error conversation-error">{actionError}</p>}
            </>
          ) : <div className="empty"><ShieldCheck /><h3>Select a support case</h3><p>Choose a case to review its original conversation and business context.</p></div>}
        </section>

        <aside className="support-context" aria-label="Support context">
          {selected ? (
            <>
              <div className="panel-label">Customer</div>
              <div className="context-section"><strong>{selected.customer_display_name || "Provider identity only"}</strong><p>{selected.customer_email || selected.customer_phone || "No verified canonical contact linked"}</p>{selected.customer_id && <Link href={`/customers?customer=${selected.customer_id}`}>View customer</Link>}</div>
              <div className="panel-label">Issue</div>
              <div className="context-section context-facts"><span><small>Status</small><strong>{label(selected.status)}</strong></span><span><small>Category</small><strong>{label(selected.category)}</strong></span><span><small>Priority</small><strong>{label(selected.priority)}</strong></span><span><small>Owner</small><strong>{selected.assigned_user_id ? "Team member" : selected.assigned_ai_role === "support" ? "Support AI" : "Unassigned"}</strong></span></div>
              <div className="panel-label">Related</div>
              <div className="context-section related-links">{selected.related_order_id ? <Link href={`/orders?order=${selected.related_order_id}`}><Package /> {selected.related_order_number || "View order"}</Link> : <p>No related order linked.</p>}{selected.related_product_name && <p><strong>Product</strong><br />{selected.related_product_name}</p>}</div>
              {selected.status !== "resolved" && selected.status !== "closed" && <div className="context-section support-resolution"><label htmlFor="resolution-summary">Resolution summary</label><textarea id="resolution-summary" value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="Record how this issue was resolved" maxLength={2000} /><Button variant="green" onClick={resolve} disabled={!resolution.trim() || updateCase.isPending}><Check /> Resolve case</Button></div>}
              {selected.resolution_summary && <div className="context-section"><strong>Resolution</strong><p>{selected.resolution_summary}</p></div>}
            </>
          ) : <div className="empty compact-empty"><p>Case context will appear here.</p></div>}
        </aside>
      </div>
    </>
  );
}
