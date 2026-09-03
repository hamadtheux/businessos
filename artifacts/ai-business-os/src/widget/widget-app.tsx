import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ProductLogo } from "@/components/product-brand";
import { TenantLogo } from "@/components/tenant-logo";
import { PRODUCT_NAME } from "@/config/brand";
import { PublicWidgetApi, type AvailabilitySlot, type ChatResponse, type ProductCard, type PublicWidgetConfig } from "./public-api.ts";

type InitMessage = {
  type: "aibos:widget-init";
  widgetId: string;
  apiBase: string;
  hostOrigin: string;
  config: PublicWidgetConfig;
  sessionToken: string;
};
type ChatMessage = { id: number; sender: "assistant" | "visitor"; text: string; products?: ProductCard[] };
type Panel = "chat" | "lead" | "order" | "availability" | "booking";

function safeInit(event: MessageEvent): InitMessage | null {
  const value = event.data as Partial<InitMessage> | null;
  if (!value || value.type !== "aibos:widget-init" || event.source !== window.parent) return null;
  if (typeof value.hostOrigin !== "string" || value.hostOrigin !== event.origin) return null;
  if (typeof value.widgetId !== "string" || !/^[A-Za-z0-9_-]{40,96}$/.test(value.widgetId)) return null;
  if (typeof value.sessionToken !== "string" || value.sessionToken.length < 48) return null;
  if (!value.config || value.config.widget_id !== value.widgetId || typeof value.apiBase !== "string") return null;
  try {
    const apiUrl = new URL(value.apiBase, window.location.href);
    if (!/^https?:$/.test(apiUrl.protocol) || apiUrl.origin !== window.location.origin) return null;
  } catch { return null; }
  return value as InitMessage;
}

export function WidgetApp() {
  const [init, setInit] = useState<InitMessage | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [panel, setPanel] = useState<Panel>("chat");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [slots, setSlots] = useState<AvailabilitySlot[]>([]);
  const [selectedSlot, setSelectedSlot] = useState<AvailabilitySlot | null>(null);
  const [handoffRequested, setHandoffRequested] = useState(false);
  const counter = useRef(1);
  const inputRef = useRef<HTMLInputElement>(null);
  const messageListRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const value = safeInit(event);
      if (!value) return;
      setInit(value);
      setMessages([{ id: counter.current++, sender: "assistant", text: value.config.welcome_message }]);
      document.documentElement.lang = value.config.locale;
      requestAnimationFrame(() => inputRef.current?.focus());
    };
    window.addEventListener("message", receive);
    window.parent.postMessage({ type: "aibos:widget-ready" }, "*");
    return () => window.removeEventListener("message", receive);
  }, []);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && init) {
        window.parent.postMessage({ type: "aibos:widget-close" }, init.hostOrigin);
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [init]);

  useEffect(() => {
    if (panel === "chat") {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [panel]);

  useEffect(() => {
    const list = messageListRef.current;
    if (panel === "chat" && list) list.scrollTop = list.scrollHeight;
  }, [messages, busy, panel]);

  const api = useMemo(() => init ? new PublicWidgetApi({ baseUrl: init.apiBase.replace(/\/+$/, ""), widgetId: init.widgetId, token: init.sessionToken }) : null, [init]);
  const close = () => init && window.parent.postMessage({ type: "aibos:widget-close" }, init.hostOrigin);
  const addAssistant = (message: string, products?: ProductCard[]) => setMessages((items) => [...items, { id: counter.current++, sender: "assistant", text: message, products }]);

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const message = text.trim();
    if (!api || !message || busy) return;
    setMessages((items) => [...items, { id: counter.current++, sender: "visitor", text: message }]);
    setText(""); setError(""); setBusy(true);
    try {
      const response = await api.message(message);
      addAssistant(response.message, response.products);
      if (response.handoff_status === "requested") {
        setHandoffRequested(true);
        setPanel("chat");
      }
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); requestAnimationFrame(() => inputRef.current?.focus()); }
  };

  if (!init || !api) return <div className="widget-loading" role="status">Opening secure chat…</div>;
  const config = init.config;
  const style = { "--widget-brand": config.primary_color, "--widget-radius": `${config.border_radius}px` } as React.CSSProperties;
  const requestHandoff = async () => {
    setBusy(true); setError("");
    try {
      const value = await api.handoff();
      addAssistant(value.message);
      setHandoffRequested(true);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  return <main className={`widget-shell theme-${config.theme}`} style={style} aria-label={`Chat with ${config.business_name}`}>
    <header className="widget-header">
      <div className="widget-identity"><TenantLogo businessName={config.business_name} logoUrl={config.logo_url} className="widget-tenant-logo" /><div><strong>{config.display_name}</strong><span>AI assistant</span></div></div>
      <button className="icon-button" type="button" onClick={close} aria-label="Close chat">×</button>
    </header>

    {panel === "chat" && <>
      <section ref={messageListRef} className="message-list" aria-live="polite" aria-label="Conversation messages">
        {messages.map((message) => <article className={`message ${message.sender}`} key={message.id}><p>{message.text}</p>{message.products?.length ? <div className="product-list">{message.products.map((product) => <div className="product-card" key={product.reference}><span>{product.item_type} · {product.availability.replaceAll("_", " ")}</span><strong>{product.name}</strong>{product.description && <p>{product.description}</p>}<b>{product.price == null ? "Ask for pricing" : formatPrice(product.price, product.currency)}</b>{product.product_url && <a href={product.product_url} rel="noreferrer" target="_blank">View product</a>}</div>)}</div> : null}</article>)}
        {busy && <div className="typing" aria-label="Assistant is responding"><i /><i /><i /></div>}
      </section>
      <ActionBar config={config} onPanel={setPanel} onHandoff={() => void requestHandoff()} busy={busy} handoffRequested={handoffRequested} />
      {handoffRequested && <p className="handoff-state" role="status">Human assistance requested. This conversation is waiting for the business.</p>}
      {error && <p className="widget-error" role="alert">{error}</p>}
      <form className="composer" onSubmit={send}><label className="sr-only" htmlFor="widget-message">Message</label><input ref={inputRef} id="widget-message" value={text} maxLength={2000} onChange={(event) => setText(event.target.value)} placeholder={handoffRequested ? "Waiting for human assistance" : config.placeholder_text} autoComplete="off" disabled={handoffRequested} /><button type="submit" disabled={handoffRequested || busy || !text.trim()} aria-label="Send message">➤</button></form>
    </>}

    {panel === "lead" && <LeadForm config={config} api={api} onBack={() => setPanel("chat")} onComplete={(message) => { addAssistant(message); setPanel("chat"); }} />}
    {panel === "order" && <OrderForm api={api} onBack={() => setPanel("chat")} onComplete={(message) => { addAssistant(message); setPanel("chat"); }} />}
    {panel === "availability" && <AvailabilityForm config={config} api={api} onBack={() => setPanel("chat")} onSlots={(values) => setSlots(values)} onBook={(slot) => { setSelectedSlot(slot); setPanel("booking"); }} slots={slots} />}
    {panel === "booking" && selectedSlot && <BookingForm config={config} api={api} slot={selectedSlot} onBack={() => setPanel("availability")} onComplete={(message) => { addAssistant(message); setPanel("chat"); }} />}

    <footer className="widget-footer"><span className="widget-attribution">Powered by <span className="widget-product-logo"><ProductLogo decorative size="sm" /></span><strong>{PRODUCT_NAME}</strong></span>{config.privacy_policy_url && <a href={config.privacy_policy_url} target="_blank" rel="noreferrer noopener">Privacy</a>}</footer>
  </main>;
}

function ActionBar({ config, onPanel, onHandoff, busy, handoffRequested }: { config: PublicWidgetConfig; onPanel: (value: Panel) => void; onHandoff: () => void; busy: boolean; handoffRequested: boolean }) {
  const allowed = new Set(config.capabilities);
  return <nav className="action-bar" aria-label="Chat actions">
    {allowed.has("capture_lead") && <button type="button" onClick={() => onPanel("lead")}>Share details</button>}
    {allowed.has("lookup_order_status") && <button type="button" onClick={() => onPanel("order")}>Order status</button>}
    {allowed.has("lookup_available_appointments") && config.appointment_types.length > 0 && <button type="button" onClick={() => onPanel("availability")}>Appointments</button>}
    {allowed.has("request_human_handoff") && <button type="button" disabled={busy || handoffRequested} onClick={onHandoff}>{handoffRequested ? "Human requested" : "Talk to a person"}</button>}
  </nav>;
}

function FormFrame({ title, copy, onBack, children }: { title: string; copy: string; onBack: () => void; children: React.ReactNode }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  return <section className="widget-form"><button className="back-button" type="button" onClick={onBack}>← Back to chat</button><h2 ref={headingRef} tabIndex={-1}>{title}</h2><p>{copy}</p>{children}</section>;
}

function LeadForm({ config, api, onBack, onComplete }: { config: PublicWidgetConfig; api: PublicWidgetApi; onBack: () => void; onComplete: (message: string) => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try { const value = await api.lead({ name: String(form.get("name") || ""), email: optional(form.get("email")), phone: optional(form.get("phone")), message: optional(form.get("message")), consent: form.get("consent") === "on" }); onComplete(value.message); }
    catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); }
  };
  return <FormFrame title="Share your details" copy="The business can follow up using the contact method you provide." onBack={onBack}><form onSubmit={submit}><Field name="name" label="Name" required maxLength={160} /><Field name="email" label="Email" type="email" maxLength={320} /><Field name="phone" label="Phone" type="tel" maxLength={32} /><label><span>What can we help with? (optional)</span><textarea name="message" maxLength={1000} rows={3} /></label>{config.consent_text && <label className="consent"><input name="consent" type="checkbox" required={config.require_lead_consent} /><span>{config.consent_text}</span></label>}<FormError value={error} /><button className="primary-action" disabled={busy}>{busy ? "Sharing…" : "Share details"}</button></form></FormFrame>;
}

function OrderForm({ api, onBack, onComplete }: { api: PublicWidgetApi; onBack: () => void; onComplete: (message: string) => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget); try { const value = await api.orderStatus({ order_reference: String(form.get("reference") || ""), email: optional(form.get("email")), phone: optional(form.get("phone")) }); const tracking = value.fulfillments.find((item) => item.tracking_number); const refund = Number(value.refunded_amount) > 0 ? ` Refunded amount: ${value.refunded_amount}.` : ""; const trackingCopy = tracking ? ` Tracking: ${tracking.tracking_company ? `${tracking.tracking_company} ` : ""}${tracking.tracking_number}.` : ""; onComplete(`Order ${value.order_reference} is ${value.status.replaceAll("_", " ")}. Payment: ${value.payment_status.replaceAll("_", " ")}. Fulfillment: ${value.fulfillment_status.replaceAll("_", " ")}.${refund}${trackingCopy}`); } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); } };
  return <FormFrame title="Check order status" copy="For privacy, enter your order reference and the exact email or phone used on the order." onBack={onBack}><form onSubmit={submit}><Field name="reference" label="Order reference" required maxLength={40} /><Field name="email" label="Order email" type="email" maxLength={320} /><Field name="phone" label="Order phone" type="tel" maxLength={32} /><FormError value={error} /><button className="primary-action" disabled={busy}>{busy ? "Verifying…" : "Check status"}</button></form></FormFrame>;
}

function AvailabilityForm({ config, api, onBack, onSlots, onBook, slots }: { config: PublicWidgetConfig; api: PublicWidgetApi; onBack: () => void; onSlots: (slots: AvailabilitySlot[]) => void; onBook: (slot: AvailabilitySlot) => void; slots: AvailabilitySlot[] }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget); const start = new Date(); const end = new Date(start); end.setUTCDate(end.getUTCDate() + 14); try { const value = await api.availability({ appointment_type_reference: String(form.get("type")), window_start: start.toISOString(), window_end: end.toISOString(), desired_results: 8 }); onSlots(value.slots); if (!value.slots.length) setError("No available slots were found in the next 14 days."); } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); } };
  return <FormFrame title="Find an appointment" copy="Availability comes directly from the business scheduling calendar." onBack={onBack}><form onSubmit={submit}><label><span>Appointment type</span><select name="type" required>{config.appointment_types.map((item) => <option value={item.reference} key={item.reference}>{item.name} · {item.duration_minutes} min</option>)}</select></label><FormError value={error} /><button className="primary-action" disabled={busy}>{busy ? "Checking…" : "Find times"}</button></form>{slots.length > 0 && <div className="slot-list" aria-label="Available appointment times">{slots.map((slot) => <button type="button" onClick={() => onBook(slot)} key={slot.slot_reference}><strong>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(slot.starts_at))}</strong><span>{slot.provider_display_name}{slot.location_reference ? ` · ${slot.location_reference}` : ""}</span></button>)}</div>}</FormFrame>;
}

function BookingForm({ config, api, slot, onBack, onComplete }: { config: PublicWidgetConfig; api: PublicWidgetApi; slot: AvailabilitySlot; onBack: () => void; onComplete: (message: string) => void }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget); try { const value = await api.appointment({ slot_reference: slot.slot_reference, appointment_type_reference: slot.appointment_type_reference, provider_reference: slot.provider_reference, starts_at: slot.starts_at, name: String(form.get("name") || ""), email: optional(form.get("email")), phone: optional(form.get("phone")), consent: form.get("consent") === "on" }); onComplete(`Your ${value.appointment_type_name} appointment with ${value.provider_display_name} is confirmed for ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value.starts_at))}.`); } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(false); } };
  return <FormFrame title="Book this time" copy={`${slot.provider_display_name} · ${new Intl.DateTimeFormat(undefined, { dateStyle: "full", timeStyle: "short" }).format(new Date(slot.starts_at))}`} onBack={onBack}><form onSubmit={submit}><Field name="name" label="Name" required maxLength={160} /><Field name="email" label="Email" type="email" maxLength={320} /><Field name="phone" label="Phone" type="tel" maxLength={32} />{config.consent_text && <label className="consent"><input name="consent" type="checkbox" required={config.require_lead_consent} /><span>{config.consent_text}</span></label>}<FormError value={error} /><button className="primary-action" disabled={busy}>{busy ? "Rechecking time…" : "Confirm appointment"}</button></form></FormFrame>;
}

function Field({ name, label, type = "text", required = false, maxLength }: { name: string; label: string; type?: string; required?: boolean; maxLength: number }) { return <label><span>{label}</span><input name={name} type={type} required={required} maxLength={maxLength} /></label>; }
function FormError({ value }: { value: string }) { return value ? <p className="widget-error" role="alert">{value}</p> : null; }
function optional(value: FormDataEntryValue | null) { const text = String(value || "").trim(); return text || null; }
function errorMessage(value: unknown) { return value instanceof Error ? value.message : "This request could not be completed."; }
function formatPrice(value: string, currency: string) { try { return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(value)); } catch { return `${value} ${currency}`; } }
