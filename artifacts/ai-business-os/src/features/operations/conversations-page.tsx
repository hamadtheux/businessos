import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  Check,
  Facebook,
  Globe2,
  Headphones,
  Instagram,
  Mail,
  MessageCircle,
  MessageSquareText,
  Pause,
  Play,
  Search,
  Send,
  UserRound,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { Avatar, Badge, Button, Card, PageHeader } from "@/components/product-ui";
import { getIndustryTerminology } from "@/lib/industry-workspaces";
import { humanizeApiError } from "@/services/api-client";
import type {
  Conversation,
  ConversationChannel,
  ConversationHandlingState,
  ConversationMessage,
} from "@/services/api-types";
import { operationsApi } from "@/services/operations";

const channelFilters: ReadonlyArray<{
  value: "" | ConversationChannel;
  label: string;
}> = [
  { value: "", label: "All channels" },
  { value: "facebook", label: "Messenger" },
  { value: "instagram", label: "Instagram" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "website", label: "Website" },
  { value: "email", label: "Email" },
];

const statusFilters = [
  { value: "", label: "All" },
  { value: "open", label: "Open" },
  { value: "escalated", label: "Escalated" },
  { value: "resolved", label: "Resolved" },
] as const;

const handlingLabels: Record<ConversationHandlingState, string> = {
  ai_active: "AI active",
  ai_paused: "AI paused",
  human_takeover: "Human takeover",
  escalated: "Escalated",
};

function when(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function channelLabel(channel: ConversationChannel) {
  return channel === "facebook"
    ? "Messenger"
    : channel.charAt(0).toUpperCase() + channel.slice(1);
}

function ChannelIcon({ channel }: { channel: ConversationChannel }) {
  const icons: Partial<Record<ConversationChannel, ReactNode>> = {
    facebook: <Facebook />,
    instagram: <Instagram />,
    whatsapp: <MessageCircle />,
    website: <Globe2 />,
    email: <Mail />,
  };
  return <span className={`channel-icon channel-${channel}`}>{icons[channel] ?? <MessageSquareText />}</span>;
}

function handlingTone(state: ConversationHandlingState) {
  if (state === "ai_active") return "success" as const;
  if (state === "escalated") return "warning" as const;
  return "neutral" as const;
}

function MessageBubble({ message }: { message: ConversationMessage }) {
  const role =
    message.sender_type === "customer"
      ? "Customer"
      : message.sender_type === "ai"
        ? "AI agent"
        : message.sender_type === "user"
          ? "Human agent"
          : "System event";
  const Icon =
    message.sender_type === "customer"
      ? UserRound
      : message.sender_type === "ai"
        ? Bot
        : message.sender_type === "user"
          ? Headphones
          : MessageCircle;
  return (
    <div className={`omni-message ${message.direction} sender-${message.sender_type}`}>
      <div className="omni-message-label"><Icon /> {role}</div>
      <div className="omni-bubble">
        <p>{message.content}</p>
        <span>{when(message.sent_at)} · {message.delivery_status}</span>
      </div>
    </div>
  );
}

export function ConversationsPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const terminology = getIndustryTerminology(activeBusiness?.industry);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState<"" | ConversationChannel>("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState("");
  const [mobileThreadClosed, setMobileThreadClosed] = useState(false);
  const [message, setMessage] = useState("");
  const [pendingSend, setPendingSend] = useState<{
    conversationId: string;
    content: string;
    clientRequestId: string;
  } | null>(null);
  const [actionError, setActionError] = useState("");

  const conversations = useQuery({
    queryKey: ["operations", activeBusinessId, "conversations", search, status, channel, page],
    queryFn: ({ signal }) => operationsApi.conversations.list(activeBusinessId, {
      search,
      status: status || undefined,
      channel: channel || undefined,
      page,
      pageSize: 50,
    }, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 10_000,
  });
  const detail = useQuery({
    queryKey: ["operations", activeBusinessId, "conversation", selectedId],
    queryFn: ({ signal }) => operationsApi.conversations.get(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
    refetchInterval: 10_000,
  });

  useEffect(() => {
    setSelectedId("");
    setMobileThreadClosed(false);
    setMessage("");
    setPendingSend(null);
    setActionError("");
  }, [activeBusinessId]);

  useEffect(() => {
    const first = conversations.data?.items[0];
    if (!selectedId && first && !mobileThreadClosed) {
      setSelectedId(first.id);
    }
  }, [conversations.data, selectedId, mobileThreadClosed]);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["operations", activeBusinessId, "conversations"] });
    void queryClient.invalidateQueries({ queryKey: ["operations", activeBusinessId, "conversation", selectedId] });
  };
  const markRead = useMutation({
    mutationFn: (conversationId: string) => operationsApi.conversations.read(activeBusinessId, conversationId),
    onSuccess: refresh,
  });
  useEffect(() => {
    if (detail.data?.unread_count && !markRead.isPending) markRead.mutate(detail.data.id);
  }, [detail.data?.id, detail.data?.unread_count, markRead.isPending]);

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
      setActionError(humanizeApiError(error, "The message could not be sent.")),
  });
  const control = useMutation({
    mutationFn: (action: "take_over" | "resume_ai" | "pause_ai" | "escalate" | "resolve" | "reopen") =>
      operationsApi.conversations.control(activeBusinessId, selectedId, action),
    onSuccess: () => {
      setActionError("");
      refresh();
    },
    onError: (error) => setActionError(humanizeApiError(error, "The conversation could not be updated.")),
  });

  const selectConversation = (conversation: Conversation) => {
    setSelectedId(conversation.id);
    setMobileThreadClosed(false);
    setActionError("");
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = message.trim();
    if (!content || !detail.data?.can_send_externally || send.isPending) return;

    const request =
      pendingSend?.conversationId === selectedId &&
      pendingSend.content === content
        ? pendingSend
        : {
            conversationId: selectedId,
            content,
            clientRequestId: crypto.randomUUID(),
          };

    setPendingSend(request);
    send.mutate(request);
  };

  if (conversations.isError) {
    return <Card><div className="empty"><AlertCircle /><h3>We couldn't load Conversations</h3><p>{humanizeApiError(conversations.error, "Try again in a moment.")}</p><Button onClick={() => void conversations.refetch()}>Try again</Button></div></Card>;
  }

  const selected = detail.data;
  const customerFallback = `Unmatched ${terminology.customerSingular.toLowerCase()}`;
  return (
    <>
      <PageHeader
        eyebrow={`${terminology.customerPlural} communications`}
        title="Conversations"
        subtitle="One live, tenant-safe inbox for every connected customer channel."
      />
      <div className={`card conversation-layout enhanced-inbox omnichannel-inbox ${selectedId ? "mobile-thread-open" : ""}`}>
        <aside className="conversation-panel conversation-filter-panel" aria-label="Inbox filters">
          <div className="panel-label">Channels</div>
          {channelFilters.map((item) => (
            <button
              type="button"
              className={`channel ${channel === item.value ? "active" : ""}`}
              onClick={() => { setChannel(item.value); setPage(1); }}
              key={item.value || "all-channels"}
            >
              {item.value ? <ChannelIcon channel={item.value} /> : <MessageSquareText />}
              <span>{item.label}</span>
            </button>
          ))}
          <div className="panel-label status-panel-label">Status</div>
          {statusFilters.map((item) => (
            <button
              type="button"
              className={`channel ${status === item.value ? "active" : ""}`}
              onClick={() => { setStatus(item.value); setPage(1); }}
              key={item.value || "all-statuses"}
            >
              <span>{item.label}</span>
            </button>
          ))}
        </aside>

        <section className="conversation-panel conversation-list-panel" aria-label="Conversations">
          <div className="inbox-search">
            <Search />
            <label className="sr-only" htmlFor="conversation-search">Search conversations</label>
            <input
              id="conversation-search"
              value={search}
              onChange={(event) => { setSearch(event.target.value); setPage(1); }}
              placeholder="Search inbox"
            />
          </div>
          {conversations.isLoading && <div className="empty compact-empty"><p>Loading conversations…</p></div>}
          {conversations.data?.items.map((item) => (
            <button
              type="button"
              className={`convo-item ${selectedId === item.id ? "active" : ""}`}
              onClick={() => selectConversation(item)}
              aria-pressed={selectedId === item.id}
              key={item.id}
            >
              <div className="convo-line">
                <div className="conversation-avatar-wrap">
                  <Avatar name={item.customer_display_name || item.channel} />
                  <ChannelIcon channel={item.channel} />
                </div>
                <div className="row-main">
                  <div className="convo-name">{item.customer_display_name || customerFallback}</div>
                  <div className="convo-msg">{item.latest_message || "No messages yet"}</div>
                  <div className="conversation-list-meta">
                    <span>{handlingLabels[item.handling_state]}</span>
                    {item.status === "escalated" && <span className="escalation-flag">Needs attention</span>}
                  </div>
                </div>
                <div className="conversation-item-trailing">
                  <span className="time">{when(item.last_activity_at)}</span>
                  {item.unread_count > 0 && <span className="count" aria-label={`${item.unread_count} unread messages`}>{item.unread_count}</span>}
                </div>
              </div>
            </button>
          ))}
          {conversations.data && !conversations.data.items.length && (
            <div className="empty"><MessageCircle /><h3>No conversations found</h3><p>{search || channel || status ? "Try changing the search or filters." : "New verified channel messages will appear here."}</p></div>
          )}
          {conversations.data && conversations.data.total > conversations.data.page_size && (
            <div className="inbox-pagination">
              <Button className="btn-sm" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</Button>
              <span>Page {page}</span>
              <Button className="btn-sm" disabled={page * conversations.data.page_size >= conversations.data.total} onClick={() => setPage((value) => value + 1)}>Next</Button>
            </div>
          )}
        </section>

        <section className="conversation-panel conversation-thread-panel" aria-label="Conversation thread">
          {detail.isLoading && selectedId && <div className="empty"><p>Loading conversation…</p></div>}
          {selected ? (
            <>
              <header className="chat-head">
                <div className="conversation-contact">
                  <button
                    className="mobile-thread-back"
                    type="button"
                    onClick={() => {
                      setSelectedId("");
                      setMobileThreadClosed(true);
                      setActionError("");
                    }}
                    aria-label="Back to conversations"
                  >
                    <ArrowLeft />
                  </button>
                  <Avatar name={selected.customer_display_name || selected.channel} />
                  <div>
                    <div className="row-title">{selected.customer_display_name || customerFallback}</div>
                    <div className="row-copy"><ChannelIcon channel={selected.channel} /> {channelLabel(selected.channel)} · {selected.status}</div>
                  </div>
                </div>
                <div className="conversation-head-badges">
                  <Badge tone={handlingTone(selected.handling_state)}>{handlingLabels[selected.handling_state]}</Badge>
                  <Badge tone={selected.status === "escalated" ? "warning" : selected.status === "resolved" ? "neutral" : "success"}>{selected.status}</Badge>
                </div>
              </header>
              <div className="conversation-control-bar" aria-label="Conversation controls">
                {selected.status === "resolved" ? (
                  <Button
                    className="btn-sm"
                    onClick={() => control.mutate("reopen")}
                    disabled={control.isPending}
                  >
                    Reopen
                  </Button>
                ) : (
                  <>
                    {selected.handling_state === "ai_active" ? (
                      <>
                        <Button
                          className="btn-sm"
                          onClick={() => control.mutate("take_over")}
                          disabled={control.isPending}
                        >
                          <Headphones /> Take over
                        </Button>
                        <Button
                          className="btn-sm"
                          onClick={() => control.mutate("pause_ai")}
                          disabled={control.isPending}
                        >
                          <Pause /> Pause AI
                        </Button>
                      </>
                    ) : (
                      <Button
                        className="btn-sm"
                        variant="green"
                        onClick={() => control.mutate("resume_ai")}
                        disabled={control.isPending}
                      >
                        <Play /> Resume AI
                      </Button>
                    )}

                    {selected.status !== "escalated" && (
                      <Button
                        className="btn-sm"
                        onClick={() => control.mutate("escalate")}
                        disabled={control.isPending}
                      >
                        <AlertCircle /> Escalate
                      </Button>
                    )}

                    <Button
                      className="btn-sm"
                      onClick={() => control.mutate("resolve")}
                      disabled={control.isPending}
                    >
                      <Check /> Resolve
                    </Button>
                  </>
                )}
              </div>
              <div className="chat-body omni-thread">
                {selected.handling_state !== "ai_active" && (
                  <div className={`ai-banner ${selected.handling_state === "escalated" ? "warning" : ""}`}>
                    {selected.handling_state === "human_takeover" ? <Headphones /> : <Bot />}
                    Autonomous AI replies are {selected.handling_state === "escalated" ? "stopped while support handles this escalation" : "paused for this conversation"}.
                  </div>
                )}
                {selected.messages.map((item) => <MessageBubble message={item} key={item.id} />)}
                {!selected.messages.length && <div className="empty"><MessageCircle /><h3>No messages yet</h3><p>The verified channel history will appear here.</p></div>}
              </div>
              <form className="chat-compose omni-compose" onSubmit={submit}>
                <label className="sr-only" htmlFor="conversation-message">Reply through {channelLabel(selected.channel)}</label>
                <input
                  id="conversation-message"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder={selected.can_send_externally ? `Reply through ${channelLabel(selected.channel)}…` : "External reply unavailable"}
                  disabled={!selected.can_send_externally || send.isPending}
                />
                <Button variant="green" type="submit" disabled={!message.trim() || !selected.can_send_externally || send.isPending} aria-label="Send external reply"><Send /></Button>
              </form>
              {!selected.can_send_externally && <div className="send-unavailable"><AlertCircle /> {selected.send_unavailable_reason || "External sending is unavailable for this conversation."}</div>}
              {actionError && <p className="form-error conversation-error">{actionError}</p>}
            </>
          ) : (
            <div className="empty"><MessageCircle /><h3>Select a conversation</h3><p>Choose a customer thread to review messages and handling state.</p></div>
          )}
        </section>
      </div>
    </>
  );
}
