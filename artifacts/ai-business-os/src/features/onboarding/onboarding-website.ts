export const WEBSITE_INPUT_ERROR =
  "Enter a valid website, such as https://yourbusiness.com";

export type NormalizedWebsiteInput = {
  value: string;
  error: string | null;
};

const HTTP_URL_PREFIX = /^https?:\/\//i;
const URL_SCHEME_PREFIX = /^[a-z][a-z\d+.-]*:/i;
const DOMAIN_LABEL = /^[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?$/i;

function isReasonableBareDomain(hostname: string): boolean {
  const labels = hostname.split(".");
  const topLevelDomain = labels.at(-1) ?? "";

  return (
    labels.length >= 2 &&
    topLevelDomain.length >= 2 &&
    /[a-z]/i.test(topLevelDomain) &&
    labels.every((label) => DOMAIN_LABEL.test(label))
  );
}

function hasAuthorityCredentials(value: string): boolean {
  const authority = value
    .replace(HTTP_URL_PREFIX, "")
    .split(/[/?#]/, 1)[0];
  return authority.includes("@");
}

function isAllowedHttpUrl(url: URL, source: string): boolean {
  return (
    (url.protocol === "http:" || url.protocol === "https:") &&
    Boolean(url.hostname) &&
    !url.username &&
    !url.password &&
    !hasAuthorityCredentials(source)
  );
}

export function normalizeWebsiteInput(value: string): NormalizedWebsiteInput {
  const trimmed = value.trim();
  if (!trimmed) return { value: "", error: null };

  if (HTTP_URL_PREFIX.test(trimmed)) {
    try {
      const url = new URL(trimmed);
      if (isAllowedHttpUrl(url, trimmed) && trimmed.length <= 2048) {
        return { value: trimmed, error: null };
      }
    } catch {
      // Report malformed explicit URLs without trying to repair them.
    }
    return { value: trimmed, error: WEBSITE_INPUT_ERROR };
  }

  if (URL_SCHEME_PREFIX.test(trimmed) || trimmed.includes("://")) {
    return { value: trimmed, error: WEBSITE_INPUT_ERROR };
  }

  try {
    const url = new URL(`https://${trimmed}`);
    const normalized = `https://${trimmed}`;
    if (
      isAllowedHttpUrl(url, trimmed) &&
      isReasonableBareDomain(url.hostname) &&
      normalized.length <= 2048
    ) {
      return { value: normalized, error: null };
    }
  } catch {
    // Bare-domain normalization is intentionally conservative.
  }

  return { value: trimmed, error: WEBSITE_INPUT_ERROR };
}
