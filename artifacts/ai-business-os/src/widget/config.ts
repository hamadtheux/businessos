type WidgetEnvironment = {
  VITE_WIDGET_API_BASE_URL?: string;
  VITE_WIDGET_APP_URL?: string;
  PROD?: boolean;
};

const environment = (
  import.meta as ImportMeta & { readonly env?: WidgetEnvironment }
).env;

function isLocalProductionHost(hostname: string) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    host === "localhost" ||
    host === "::1" ||
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.startsWith("127.") ||
    host.startsWith("10.") ||
    host.startsWith("192.168.")
  ) return true;
  const match = /^172\.(\d{1,2})\./.exec(host);
  return Boolean(match && Number(match[1]) >= 16 && Number(match[1]) <= 31);
}

function validatePublicUrl(
  name: "VITE_WIDGET_API_BASE_URL" | "VITE_WIDGET_APP_URL",
  value: string | undefined,
): string {
  const configured = value?.trim();

  if (!configured) {
    return "";
  }

  let parsed: URL;

  try {
    parsed = new URL(configured);
  } catch {
    throw new Error(`${name} must be a valid absolute URL.`);
  }

  if (
    !["http:", "https:"].includes(parsed.protocol)
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || (environment?.PROD && (
      parsed.protocol !== "https:" || isLocalProductionHost(parsed.hostname)
    ))
  ) {
    throw new Error(`${name} is not a safe public URL.`);
  }

  return parsed.toString();
}

export const WIDGET_API_BASE_URL = validatePublicUrl(
  "VITE_WIDGET_API_BASE_URL",
  environment?.VITE_WIDGET_API_BASE_URL,
).replace(/\/+$/, "");

export const WIDGET_APP_URL = validatePublicUrl(
  "VITE_WIDGET_APP_URL",
  environment?.VITE_WIDGET_APP_URL,
);
