import type { PublicSession, PublicWidgetConfig } from "./public-api.ts";

const root = document.getElementById("hosted-root")!;
const widgetId = new URLSearchParams(window.location.search).get("widget")?.trim() ?? "";

const fail = () => {
  const message = document.createElement("p");
  message.textContent = "This hosted assistant is not live yet. The business may need to enable it.";
  root.replaceChildren(message);
};

const bootstrap = async () => {
  if (!/^[A-Za-z0-9_-]{40,96}$/.test(widgetId)) return fail();
  const configResponse = await fetch(
    `/api/v1/public/hosted-widgets/${encodeURIComponent(widgetId)}/config`,
    { credentials: "omit" },
  );
  if (!configResponse.ok) return fail();
  const config = await configResponse.json() as PublicWidgetConfig;
  const sessionResponse = await fetch(
    `/api/v1/public/hosted-widgets/${encodeURIComponent(widgetId)}/sessions`,
    { method: "POST", credentials: "omit" },
  );
  if (!sessionResponse.ok) return fail();
  const session = await sessionResponse.json() as PublicSession;
  const frame = document.createElement("iframe");
  frame.title = `Chat with ${config.business_name}`;
  frame.sandbox.add("allow-scripts", "allow-forms", "allow-same-origin");
  const frameUrl = new URL("widget.html", window.location.href);
  const receive = (event: MessageEvent) => {
    if (
      event.source !== frame.contentWindow
      || event.origin !== window.location.origin
      || event.data?.type !== "aibos:widget-ready"
    ) return;
    frame.contentWindow?.postMessage({
      type: "aibos:widget-init",
      widgetId,
      apiBase: window.location.origin,
      hostOrigin: window.location.origin,
      config,
      sessionToken: session.session_token,
    }, window.location.origin);
  };
  window.addEventListener("message", receive);
  frame.src = frameUrl.toString();
  root.replaceChildren(frame);
};

void bootstrap().catch(fail);
