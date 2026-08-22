type ClientEnvironment = {
  VITE_API_BASE_URL?: string;
};

const clientEnvironment = (
  import.meta as ImportMeta & { readonly env?: ClientEnvironment }
).env;
const configuredBaseUrl = clientEnvironment?.VITE_API_BASE_URL?.trim();

export const API_BASE_URL = configuredBaseUrl
  ? configuredBaseUrl.replace(/\/+$/, "")
  : "";
