type ClientEnvironment = {
  VITE_API_BASE_URL?: string;
  PROD?: boolean;
};

const clientEnvironment = (
  import.meta as ImportMeta & { readonly env?: ClientEnvironment }
).env;
const configuredBaseUrl = clientEnvironment?.VITE_API_BASE_URL?.trim();

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

if (configuredBaseUrl) {
  const parsed = new URL(configuredBaseUrl);
  if (
    parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || (clientEnvironment?.PROD && (
      parsed.protocol !== "https:" || isLocalProductionHost(parsed.hostname)
    ))
  ) {
    throw new Error("VITE_API_BASE_URL is not a safe public API origin.");
  }
}

export const API_BASE_URL = configuredBaseUrl
  ? configuredBaseUrl.replace(/\/+$/, "")
  : "";
