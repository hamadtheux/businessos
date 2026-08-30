import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Bot,
  Check,
  Clipboard,
  Code2,
  Globe2,
  MessageCircle,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  ExternalLink,
  Link2,
  Users,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { PRODUCT_BRAND_COLORS } from "@/config/brand";
import { Badge, Button, Card, PageHeader } from "@/components/product-ui";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import { humanizeApiError } from "@/services/api-client";
import {
  chatbotApi,
  type ChatbotConfig,
  type ChatbotConfigUpdate,
  type ChatbotDeploymentTarget,
  type PublicChatbotCapability,
} from "@/services/chatbot";

const capabilities: Array<{
  id: PublicChatbotCapability;
  label: string;
  description: string;
  scheduling?: boolean;
}> = [
  { id: "answer_business_questions", label: "Business answers", description: "Answer from trusted Business Brain knowledge." },
  { id: "search_products_services", label: "Catalog search", description: "Find real products and services without loading the full catalog." },
  { id: "recommend_products_services", label: "Recommendations", description: "Recommend only real catalog matches and trusted prices." },
  { id: "capture_lead", label: "Lead capture", description: "Collect minimal contact details with your consent policy." },
  { id: "lookup_available_appointments", label: "Availability", description: "Show real slots from Scheduling.", scheduling: true },
  { id: "book_appointment", label: "Appointment booking", description: "Recheck and book a real slot safely.", scheduling: true },
  { id: "lookup_order_status", label: "Order status", description: "Require order and customer identity verification." },
  { id: "request_human_handoff", label: "Human handoff", description: "Escalate the conversation and notify the team." },
];

const isoDate = (date: Date) => date.toISOString().slice(0, 10);

function safeHttpUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function draftFromConfig(value: ChatbotConfig): ChatbotConfigUpdate {
  return {
    enabled: value.enabled,
    display_name: value.display_name,
    welcome_message: value.welcome_message,
    placeholder_text: value.placeholder_text,
    tone: value.tone,
    theme: value.theme,
    position: value.position,
    launcher_style: value.launcher_style,
    allowed_capabilities: [...value.allowed_capabilities],
    allowed_domains: [...value.allowed_domains],
    privacy_policy_url: value.privacy_policy_url,
    consent_text: value.consent_text,
    require_lead_consent: value.require_lead_consent,
    default_locale: value.default_locale,
    border_radius: value.border_radius,
  };
}

function deploymentState(target: ChatbotDeploymentTarget) {
  if (target.state === "installed" || target.state === "connected") {
    return { label: "Connected", tone: "success" as const };
  }
  if (target.state === "installation_supported") {
    return { label: "Ready to install", tone: "info" as const };
  }
  if (target.state === "available") {
    return { label: target.automatic_install ? "Connect" : "Manual installation", tone: "neutral" as const };
  }
  if (target.state === "connection_required") {
    return { label: "Provider configuration required", tone: "neutral" as const };
  }
  return { label: "Manual installation", tone: "neutral" as const };
}

export function ChatbotPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const schedulingEnabled = isBusinessFeatureEnabled(activeBusiness, "scheduling");
  const [draft, setDraft] = useState<ChatbotConfigUpdate | null>(null);
  const [domains, setDomains] = useState("");
  const [status, setStatus] = useState("");
  const [copied, setCopied] = useState(false);
  const period = useMemo(() => {
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - 29);
    return { start: isoDate(start), end: isoDate(end) };
  }, []);
  const config = useQuery({
    queryKey: ["chatbot", activeBusinessId, "config"],
    queryFn: ({ signal }) => chatbotApi.get(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const analytics = useQuery({
    queryKey: ["chatbot", activeBusinessId, "analytics", period.start, period.end],
    queryFn: ({ signal }) => chatbotApi.analytics(activeBusinessId, period.start, period.end, signal),
    enabled: Boolean(activeBusinessId),
  });
  const deployments = useQuery({
    queryKey: ["chatbot", activeBusinessId, "deployments"],
    queryFn: ({ signal }) => chatbotApi.deployments(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });

  useEffect(() => {
    if (!config.data) return;
    setDraft(draftFromConfig(config.data));
    setDomains(config.data.allowed_domains.join("\n"));
    setStatus("");
    setCopied(false);
  }, [config.data]);

  const save = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("Chatbot configuration is not ready.");
      const allowedDomains = domains
        .split(/[\n,]/)
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean);
      return chatbotApi.update(activeBusinessId, { ...draft, allowed_domains: [...new Set(allowedDomains)] });
    },
    onSuccess: (value) => {
      queryClient.setQueryData(["chatbot", activeBusinessId, "config"], value);
      setStatus("Chatbot settings saved.");
    },
    onError: (error) => setStatus(humanizeApiError(error, "Chatbot settings could not be saved.")),
  });
  const rotate = useMutation({
    mutationFn: () => chatbotApi.rotateWidgetId(activeBusinessId),
    onSuccess: (value) => {
      queryClient.setQueryData(["chatbot", activeBusinessId, "config"], value);
      setStatus("Widget ID rotated. Replace the old embed snippet on your website.");
    },
    onError: (error) => setStatus(humanizeApiError(error, "Widget ID could not be rotated.")),
  });
  const installHosted = useMutation({
    mutationFn: () => chatbotApi.installHosted(activeBusinessId),
    onSuccess: (target) => {
      setStatus("Hosted AI assistant installed and verified on the platform-owned chat page.");
      void queryClient.invalidateQueries({ queryKey: ["chatbot", activeBusinessId] });
      const hostedUrl = safeHttpUrl(target.hosted_url);
      if (hostedUrl) void navigator.clipboard.writeText(hostedUrl);
    },
    onError: (error) => setStatus(humanizeApiError(error, "Hosted chat could not be installed.")),
  });

  if (config.isError) {
    return <div className="empty"><Bot /><h3>Chatbot settings unavailable</h3><p>{humanizeApiError(config.error, "Try again shortly.")}</p><Button onClick={() => void config.refetch()}>Try again</Button></div>;
  }
  if (config.isLoading || !config.data || !draft) {
    return <div className="empty"><RefreshCw className="spin" /><h3>Loading Website Chatbot</h3><p>Preparing the secure widget configuration…</p></div>;
  }
  const loadedConfig = config.data;

  const toggleCapability = (id: PublicChatbotCapability, enabled: boolean) => {
    setDraft((current) => {
      if (!current) return current;
      const next = new Set(current.allowed_capabilities);
      if (enabled) next.add(id); else next.delete(id);
      if (id === "recommend_products_services" && enabled) next.add("search_products_services");
      if (id === "search_products_services" && !enabled) next.delete("recommend_products_services");
      if (id === "book_appointment" && enabled) next.add("lookup_available_appointments");
      if (id === "lookup_available_appointments" && !enabled) next.delete("book_appointment");
      return { ...current, allowed_capabilities: [...next] };
    });
  };

  return (
    <div className="chatbot-page">
      <PageHeader
        eyebrow="Customer-facing AI"
        title="Website Chatbot"
        subtitle="Configure your assistant, preview the experience, then choose how to publish it."
        action={<Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}><Save />{save.isPending ? "Saving…" : "Save changes"}</Button>}
      />

      <div className="chatbot-status-strip">
        <div className="chatbot-status-primary">
          <span className={`status-dot ${loadedConfig.lifecycle_status === "live" ? "is-live" : ""}`} />
          <div>
            <strong>Assistant {loadedConfig.lifecycle_status.replaceAll("_", " ")}</strong>
            <span>{loadedConfig.enabled ? "Available to published visitor experiences" : "Visitor access is currently disabled"}</span>
          </div>
        </div>
        <div className="chatbot-runtime-status">
          <span>AI runtime</span>
          <Badge tone={loadedConfig.ai_runtime_status === "ready" ? "success" : "warning"}>{loadedConfig.ai_runtime_status === "ready" ? "Ready" : "Configuration required"}</Badge>
        </div>
      </div>

      {loadedConfig.ai_runtime_status === "configuration_required" && <div className="ai-banner"><AlertCircle />AI provider configuration required. Hosted deployment, assistant settings, and the safe embed remain available; visitor AI replies stay disabled until a server provider is configured.</div>}

      <section className="chatbot-journey-step">
        <div className="chatbot-step-heading">
          <span>1</span>
          <div><h2>Assistant</h2><p>Set the public identity and see changes before they are published.</p></div>
        </div>
        <div className="chatbot-grid chatbot-assistant-grid">
          <section className="card chatbot-settings-card">
          <div className="chatbot-section-head"><div><h3>Assistant identity</h3><p>Brand-safe public presentation and conversation tone.</p></div><label className="toggle-line"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>Enable chatbot</span></label></div>
          <div className="form-grid">
            <label className="field"><span>Chatbot name</span><input value={draft.display_name} maxLength={80} onChange={(event) => setDraft({ ...draft, display_name: event.target.value })} /></label>
            <label className="field"><span>Default language</span><input value={draft.default_locale} maxLength={8} placeholder="en" onChange={(event) => setDraft({ ...draft, default_locale: event.target.value })} /></label>
            <label className="field full"><span>Welcome message</span><textarea value={draft.welcome_message} maxLength={500} rows={3} onChange={(event) => setDraft({ ...draft, welcome_message: event.target.value })} /></label>
            <label className="field full"><span>Message placeholder</span><input value={draft.placeholder_text} maxLength={160} onChange={(event) => setDraft({ ...draft, placeholder_text: event.target.value })} /></label>
            <label className="field"><span>Tone</span><select value={draft.tone} onChange={(event) => setDraft({ ...draft, tone: event.target.value as ChatbotConfigUpdate["tone"] })}><option value="friendly">Friendly</option><option value="professional">Professional</option><option value="concise">Concise</option><option value="warm">Warm</option></select></label>
            <label className="field"><span>Theme</span><select value={draft.theme} onChange={(event) => setDraft({ ...draft, theme: event.target.value as ChatbotConfigUpdate["theme"] })}><option value="light">Light</option><option value="dark">Dark</option><option value="auto">Match device</option></select></label>
            <label className="field"><span>Position</span><select value={draft.position} onChange={(event) => setDraft({ ...draft, position: event.target.value as ChatbotConfigUpdate["position"] })}><option value="bottom_right">Bottom right</option><option value="bottom_left">Bottom left</option></select></label>
            <label className="field"><span>Launcher</span><select value={draft.launcher_style} onChange={(event) => setDraft({ ...draft, launcher_style: event.target.value as ChatbotConfigUpdate["launcher_style"] })}><option value="bubble">Chat bubble</option><option value="pill">Message pill</option></select></label>
          </div>
          </section>
          <WidgetPreview config={{ ...loadedConfig, ...draft }} />
        </div>

        <section className="card chatbot-safety-card">
          <div className="chatbot-section-head"><div><h3>Behavior & safety</h3><p>Control the server-owned allowlist, website domains, and visitor consent copy.</p></div><ShieldCheck /></div>
          <div className="chatbot-safety-grid">
            <div>
              <div className="chatbot-subheading"><strong>Public capabilities</strong><span>Visitors cannot add capabilities through a prompt.</span></div>
              <div className="capability-list">
                {capabilities.filter((item) => !item.scheduling || schedulingEnabled).map((item) => {
                  const available = loadedConfig.available_capabilities.includes(item.id);
                  const checked = available && draft.allowed_capabilities.includes(item.id);
                  return <label className={`capability-row ${available ? "" : "disabled"}`} key={item.id}><input type="checkbox" checked={checked} disabled={!available} onChange={(event) => toggleCapability(item.id, event.target.checked)} /><span><strong>{item.label}</strong><small>{item.description}</small></span></label>;
                })}
              </div>
            </div>
            <div className="chatbot-privacy-settings">
              <div className="chatbot-subheading"><strong>Website & privacy</strong><span>Hosted chat does not require an allowed hostname.</span></div>
              <label className="field"><span>Website domains</span><textarea value={domains} rows={3} placeholder={"example.com\nwww.example.com"} onChange={(event) => setDomains(event.target.value)} /></label>
              <label className="field"><span>Privacy policy URL</span><input type="url" value={draft.privacy_policy_url ?? ""} placeholder="https://example.com/privacy" onChange={(event) => setDraft({ ...draft, privacy_policy_url: event.target.value || null })} /></label>
              <label className="field"><span>Lead consent text</span><textarea value={draft.consent_text ?? ""} rows={3} maxLength={1000} onChange={(event) => setDraft({ ...draft, consent_text: event.target.value || null })} /></label>
              <label className="toggle-line"><input type="checkbox" checked={draft.require_lead_consent} onChange={(event) => setDraft({ ...draft, require_lead_consent: event.target.checked })} /><span>Require explicit consent before lead capture or booking</span></label>
            </div>
          </div>
        </section>
      </section>

      <section className="chatbot-journey-step">
        <div className="chatbot-step-heading">
          <span>2</span>
          <div><h2>Publish</h2><p>Launch the recommended hosted experience with no website changes.</p></div>
        </div>
        <section className="card chatbot-publish-card">
          {deployments.isError ? <p className="form-error">{humanizeApiError(deployments.error, "Deployment options could not be loaded.")}</p> : deployments.isLoading ? <p className="subtle">Loading deployment options…</p> : deployments.data?.targets.filter((target) => target.target_type === "hosted").map((target) => {
            const hostedUrl = safeHttpUrl(target.hosted_url);
            const installed = target.state === "installed";
            const aiReady = deployments.data.ai_runtime_status === "ready";
            return <div className="hosted-assistant-card" key={target.deployment_target_key ?? target.target_type}>
              <div className="hosted-assistant-icon"><Link2 /></div>
              <div className="row-main">
                <div className="eyebrow">Recommended · Use hosted AI assistant</div>
                <h3>Hosted AI Assistant</h3>
                <p>{installed ? aiReady ? "Your secure, shareable hosted assistant is live." : "Your hosted page is ready; AI replies wait for provider configuration." : "Launch instantly with a hosted chat page — no website changes required."}</p>
                {hostedUrl && <a className="hosted-url" href={hostedUrl} target="_blank" rel="noreferrer">{hostedUrl} <ExternalLink /></a>}
              </div>
              <div className="hosted-actions">
                {hostedUrl && <Button onClick={() => void navigator.clipboard.writeText(hostedUrl)}><Clipboard /> Copy link</Button>}
                {installed && hostedUrl ? <a className="btn btn-primary" href={hostedUrl} target="_blank" rel="noreferrer"><ExternalLink /> Open hosted assistant</a> : <Button variant="primary" disabled={installHosted.isPending || installed} onClick={() => installHosted.mutate()}><Sparkles />{installHosted.isPending ? "Launching…" : installed ? "Hosted assistant ready" : "Launch hosted assistant"}</Button>}
              </div>
            </div>;
          })}
          <p className="safe-note"><ShieldCheck />Hosted sessions use opaque IDs, short-lived session tokens, rate limits, and server-owned capabilities.</p>
        </section>
      </section>

      <section className="chatbot-journey-step">
        <div className="chatbot-step-heading">
          <span>3</span>
          <div><h2>Add to website</h2><p>Connect your website or use the manual embed only when the hosted option does not fit.</p></div>
        </div>
        <section className="card chatbot-deployment-card">
          <div className="chatbot-section-head"><div><div className="eyebrow">Guided platform setup</div><h3>Where is your website built?</h3><p>Automatic installation appears only when a real authenticated provider is available.</p></div><Globe2 /></div>
          {deployments.isError ? <p className="form-error">{humanizeApiError(deployments.error, "Website options could not be loaded.")}</p> : deployments.isLoading ? <p className="subtle">Loading website options…</p> : <>
            <div className="chatbot-provider-grid">{deployments.data?.targets.filter((target) => !["hosted", "manual_embed"].includes(target.target_type)).map((target) => {
              const state = deploymentState(target);
              return <div className="integration-tile" key={target.target_type}>
                <div className="integration-tile-icon"><Globe2 /></div>
                <div className="row-main"><strong>{target.display_name}</strong><span>{target.instructions[0] || "Use your website provider's custom-code settings."}</span></div>
                <Badge tone={state.tone}>{state.label}</Badge>
              </div>;
            })}</div>
            <details className="chatbot-advanced">
              <summary><span><strong>Advanced · Manual installation</strong><small>Developer embed for custom websites and unsupported providers.</small></span><Code2 /></summary>
              <div className="chatbot-code-panel">
                <div className="chatbot-code-head"><div><span>Embed code</span><small>Place before the closing body tag.</small></div><Button className="btn-sm" onClick={() => void navigator.clipboard.writeText(deployments.data?.advanced_embed_snippet || loadedConfig.embed_snippet).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); })}>{copied ? <Check /> : <Clipboard />}{copied ? "Copied" : "Copy code"}</Button></div>
                <pre><code>{deployments.data?.advanced_embed_snippet || loadedConfig.embed_snippet}</code></pre>
                <div className="chatbot-code-foot"><p className="safe-note"><ShieldCheck />The snippet contains an opaque public widget ID—never a business ID, API key, or employee session.</p><Button variant="ghost" className="btn-sm" onClick={() => rotate.mutate()} disabled={rotate.isPending}><RotateCcw />Rotate widget ID</Button></div>
              </div>
            </details>
          </>}
        </section>
      </section>

      <section className="card chatbot-analytics-card">
        <div className="chatbot-section-head"><div><h3>Last 30 days</h3><p>Real database aggregates. Empty activity remains zero.</p></div><Sparkles /></div>
        {analytics.isError ? <p className="form-error">{humanizeApiError(analytics.error, "Analytics could not be loaded.")}</p> : <div className="chatbot-metrics">
          {([
            ["Sessions", analytics.data?.sessions ?? 0, MessageCircle],
            ["Messages", analytics.data?.messages ?? 0, Bot],
            ["Leads", analytics.data?.leads_captured ?? 0, Users],
            ["Handoffs", analytics.data?.handoffs ?? 0, Users],
            ["Appointments", analytics.data?.appointments_booked ?? 0, Check],
            ["AI failures", analytics.data?.ai_failures ?? 0, ShieldCheck],
          ] as const).map(([label, value, Icon]) => <div className="chatbot-metric" key={label}><Icon /><strong>{value}</strong><span>{label}</span></div>)}
        </div>}
      </section>
      {status && <div className="chatbot-save-status" role="status">{status}</div>}
    </div>
  );
}

function WidgetPreview({ config }: { config: ChatbotConfig }) {
  const brandColor = `var(--brand-primary, ${PRODUCT_BRAND_COLORS.primary})`;
  return <section className="card preview-card"><div className="chatbot-section-head"><div><h3>Live style preview</h3><p>Draft branding only—no customer conversation is created.</p></div><Badge tone="neutral">Preview</Badge></div><div className={`widget-preview ${config.theme === "dark" ? "dark" : ""}`} style={{ borderRadius: config.border_radius }}><div className="widget-preview-head" style={{ background: brandColor }}><div className="preview-avatar"><Bot /></div><div><strong>{config.display_name}</strong><span>AI assistant</span></div></div><div className="widget-preview-body"><div className="preview-bubble">{config.welcome_message}</div><div className="preview-suggestion">Ask about products, services, or business hours</div></div><div className="widget-preview-compose"><span>{config.placeholder_text}</span><MessageCircle style={{ color: brandColor }} /></div></div></section>;
}
