import { WIDGET_API_BASE_URL, WIDGET_APP_URL } from "./config.ts";

type LoaderConfig = {
  widget_id: string;
  display_name: string;
  welcome_message: string;
  primary_color: string;
  logo_url: string | null;
  position: "bottom_right" | "bottom_left";
  launcher_style: "bubble" | "pill";
  [key: string]: unknown;
};

type LoaderSession = { session_token: string; expires_at: string; locale: string };

(() => {
  const script = document.currentScript as HTMLScriptElement | null;
  const widgetId = script?.dataset.widgetId?.trim() ?? "";
  if (!script || !/^[A-Za-z0-9_-]{40,96}$/.test(widgetId) || document.querySelector(`[data-aibos-widget-host="${CSS.escape(widgetId)}"]`)) return;

  const scriptUrl = new URL(script.src, document.baseURI);
  const apiBase = (WIDGET_API_BASE_URL || scriptUrl.origin).replace(/\/+$/, "");
  const widgetAppUrl = WIDGET_APP_URL || new URL("widget.html", scriptUrl).toString();
  let config: LoaderConfig | null = null;
  let session: LoaderSession | null = null;
  let frame: HTMLIFrameElement | null = null;
  let busy = false;

  const host = document.createElement("div");
  host.dataset.aibosWidgetHost = widgetId;
  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = `:host{all:initial}.wrap{position:fixed;z-index:2147483000;right:20px;bottom:20px;font-family:Inter,system-ui,sans-serif}.wrap.left{right:auto;left:20px}.launch{all:unset;box-sizing:border-box;display:flex;align-items:center;justify-content:center;gap:9px;min-width:56px;height:56px;padding:0 17px;border-radius:999px;background:var(--brand,#1268F3);color:#fff;cursor:pointer;box-shadow:0 14px 36px rgba(15,23,42,.28);font:600 14px/1 system-ui,sans-serif}.launch.circle{width:56px;min-width:56px;padding:0}.launch:focus-visible{outline:3px solid #fff;outline-offset:3px}.launch[disabled]{opacity:.65;cursor:wait}.launch svg{width:22px;height:22px}.panel{position:absolute;right:0;bottom:70px;width:min(390px,calc(100vw - 28px));height:min(680px,calc(100dvh - 108px));border:0;border-radius:18px;background:#fff;box-shadow:0 22px 70px rgba(15,23,42,.3)}.left .panel{right:auto;left:0}@media(max-width:520px){.wrap,.wrap.left{right:10px;left:10px;bottom:10px}.panel,.left .panel{position:fixed;inset:8px;width:calc(100vw - 16px);height:calc(100dvh - 16px);border-radius:16px}.launch{margin-left:auto}.left .launch{margin-left:0}}@media(prefers-reduced-motion:no-preference){.launch{transition:transform .16s ease,box-shadow .16s ease}.launch:hover{transform:translateY(-2px);box-shadow:0 18px 42px rgba(15,23,42,.34)}}`;
  const wrap = document.createElement("div");
  wrap.className = "wrap";
  const button = document.createElement("button");
  button.className = "launch circle";
  button.type = "button";
  button.setAttribute("aria-label", "Open business chat");
  button.setAttribute("aria-expanded", "false");
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 4h16v12H8l-4 4V4Zm4 5h8v2H8V9Zm0 4h5v2H8v-2Z"/></svg>';
  wrap.append(button);
  root.append(style, wrap);
  document.body.append(host);

  const bootstrap = async () => {
    const response = await fetch(`${apiBase}/api/v1/public/widgets/${encodeURIComponent(widgetId)}/config`, { credentials: "omit", mode: "cors" });
    if (!response.ok) throw new Error("Widget unavailable");
    config = await response.json() as LoaderConfig;
    wrap.classList.toggle("left", config.position === "bottom_left");
    wrap.style.setProperty("--brand", config.primary_color);
    button.classList.toggle("circle", config.launcher_style !== "pill");
    button.setAttribute("aria-label", `Open chat with ${config.display_name}`);
    if (config.launcher_style === "pill") button.append(document.createTextNode(" Chat with us"));
  };

  const createSession = async () => {
    if (session && Date.parse(session.expires_at) > Date.now() + 30_000) return session;
    const response = await fetch(`${apiBase}/api/v1/public/widgets/${encodeURIComponent(widgetId)}/sessions`, { method: "POST", credentials: "omit", mode: "cors" });
    if (!response.ok) throw new Error("Session unavailable");
    session = await response.json() as LoaderSession;
    return session;
  };

  const sessionIsFresh = () => Boolean(
    session && Date.parse(session.expires_at) > Date.now() + 30_000,
  );

  const close = () => {
    if (frame) frame.hidden = true;
    button.hidden = false;
    button.setAttribute("aria-expanded", "false");
    button.focus();
  };

  const open = async () => {
    if (busy || !config) return;
    if (frame && sessionIsFresh()) {
      frame.hidden = false;
      button.hidden = true;
      button.setAttribute("aria-expanded", "true");
      frame.focus();
      return;
    }
    frame?.remove();
    frame = null;
    session = null;
    busy = true;
    button.disabled = true;
    try {
      const activeSession = await createSession();
      const iframe = document.createElement("iframe");
      iframe.className = "panel";
      iframe.title = `Chat with ${config.display_name}`;
      iframe.sandbox.add("allow-scripts", "allow-forms", "allow-same-origin");
      iframe.setAttribute("aria-label", `Chat with ${config.display_name}`);
      frame = iframe;
      const frameOrigin = new URL(widgetAppUrl, scriptUrl).origin;
      let readyTimeout = 0;
      const ready = (event: MessageEvent) => {
        if (event.source !== iframe.contentWindow || event.origin !== frameOrigin || event.data?.type !== "aibos:widget-ready") return;
        window.removeEventListener("message", ready);
        window.clearTimeout(readyTimeout);
        iframe.contentWindow?.postMessage({ type: "aibos:widget-init", widgetId, apiBase, hostOrigin: window.location.origin, config, sessionToken: activeSession.session_token }, frameOrigin);
        iframe.focus();
      };
      window.addEventListener("message", ready);
      iframe.addEventListener("load", () => {
        readyTimeout = window.setTimeout(() => window.removeEventListener("message", ready), 10_000);
      }, { once: true });
      iframe.src = widgetAppUrl;
      wrap.append(iframe);
      button.hidden = true;
      button.setAttribute("aria-expanded", "true");
      button.setAttribute("aria-label", `Open chat with ${config.display_name}`);
    } catch {
      button.setAttribute("aria-label", "Business chat is temporarily unavailable");
    } finally {
      busy = false;
      button.disabled = false;
    }
  };

  window.addEventListener("message", (event) => {
    if (!frame || event.source !== frame.contentWindow || event.origin !== new URL(widgetAppUrl, scriptUrl).origin) return;
    if (event.data?.type === "aibos:widget-close") close();
  });
  button.addEventListener("click", () => void open());
  void bootstrap().catch(() => host.remove());
})();
