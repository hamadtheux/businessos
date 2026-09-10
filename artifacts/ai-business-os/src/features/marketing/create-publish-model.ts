import type { MarketingContent } from "@/services/api-types";
import type {
  ConnectorDefinition,
  IntegrationConnection,
} from "@/services/integrations";
import type { CreatePublishPlatform } from "@/services/marketing";

export const CREATE_PUBLISH_PLATFORMS = [
  "instagram",
  "facebook",
  "linkedin",
  "tiktok",
  "youtube",
] as const satisfies readonly CreatePublishPlatform[];

export const PLATFORM_LABELS: Record<CreatePublishPlatform, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  tiktok: "TikTok",
  youtube: "YouTube",
};

export type PlatformReadiness = {
  state: "connected" | "disconnected" | "checking" | "coming_soon";
  label: string;
  canPublish: boolean;
};

export type EditablePlatformFields = {
  title: string;
  body: string;
  cta: string;
  hashtags: string;
  keywords: string;
  altText: string;
};

export function contentPlatform(content: MarketingContent): CreatePublishPlatform {
  const explicit = content.platform_fields?.platform;
  if (explicit && CREATE_PUBLISH_PLATFORMS.includes(explicit)) return explicit;
  if (
    content.channel === "instagram" ||
    content.channel === "facebook" ||
    content.channel === "linkedin" ||
    content.channel === "tiktok"
  ) {
    return content.channel;
  }
  return "instagram";
}

export function editableFields(content: MarketingContent): EditablePlatformFields {
  const fields = content.platform_fields;
  return {
    title: content.title,
    body:
      contentPlatform(content) === "youtube"
        ? fields?.description || content.body
        : fields?.caption || content.body,
    cta: content.cta || "",
    hashtags: (fields?.hashtags || []).join(" "),
    keywords: (fields?.keywords || []).join(", "),
    altText: fields?.alt_text || "",
  };
}

export function updatedPlatformFields(
  content: MarketingContent,
  fields: EditablePlatformFields,
): NonNullable<MarketingContent["platform_fields"]> {
  const platform = contentPlatform(content);
  return {
    platform,
    caption: platform === "youtube" ? null : fields.body,
    description: platform === "youtube" ? fields.body : null,
    hashtags: splitTerms(fields.hashtags, /\s+/),
    keywords: splitTerms(fields.keywords, /[,\n]+/),
    alt_text: fields.altText.trim() || null,
    media_disabled: content.platform_fields?.media_disabled || false,
    selected_media_asset_id:
      content.platform_fields?.selected_media_asset_id || null,
  };
}

export function platformReadiness(
  platform: CreatePublishPlatform,
  definitions: ConnectorDefinition[] | undefined,
  connections: IntegrationConnection[] | undefined,
  pending = false,
): PlatformReadiness {
  if (pending) {
    return { state: "checking", label: "Checking", canPublish: false };
  }

  if (platform !== "instagram" && platform !== "facebook") {
    return {
      state: "coming_soon",
      label: "Coming soon",
      canPublish: false,
    };
  }

  const definition = definitions?.find(
    (item) => item.connector_type === platform,
  );
  const connection = connections?.find(
    (item) => item.connector_type === platform,
  );

  if (!definition || definition.setup_status !== "available") {
    return {
      state: "coming_soon",
      label: "Coming soon",
      canPublish: false,
    };
  }

  if (!definition.external_writes_enabled) {
    return {
      state: "coming_soon",
      label: "Unavailable",
      canPublish: false,
    };
  }

  const supportsPublishing =
    definition.future_write_capabilities.includes(
      "future_publish_content",
    );

  const requiredWriteScope =
    platform === "facebook"
      ? "pages_manage_posts"
      : "instagram_content_publish";

  const scopeReady = Boolean(
    connection?.scopes_granted?.includes(requiredWriteScope),
  );

  const resourcesReady = Boolean(
    !definition.resource_selection_required ||
      connection?.selected_resources.length,
  );

  const connected = Boolean(
    supportsPublishing &&
      scopeReady &&
      connection?.status === "connected" &&
      connection.authentication_state === "authorized" &&
      connection.health === "healthy" &&
      resourcesReady,
  );

  if (connected) {
    return {
      state: "connected",
      label: "Connected",
      canPublish: true,
    };
  }

  if (connection && supportsPublishing && !scopeReady) {
    return {
      state: "disconnected",
      label: "Reconnect",
      canPublish: false,
    };
  }

  return {
    state: "disconnected",
    label: "Connect",
    canPublish: false,
  };
}

export function customerStatus(
  content: Pick<MarketingContent, "status">,
): "Draft" | "Ready" | "Scheduled" | "Needs attention" {
  if (content.status === "scheduled") return "Scheduled";
  if (["approved", "ready_to_publish"].includes(content.status)) return "Ready";
  if (content.status === "archived") return "Needs attention";
  return "Draft";
}

export function packageIdFromContent(content: MarketingContent) {
  const match = content.proposal_key?.match(
    /^create-publish:([0-9a-f-]{36}):(instagram|facebook|linkedin|tiktok|youtube)(?::v\d+)?$/i,
  );
  return match?.[1] || null;
}

type ZonedDateTimeParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
};

export function zonedDateTimeParts(
  date: Date,
  timezone: string,
): ZonedDateTimeParts {
  let parts: Intl.DateTimeFormatPart[];

  try {
    parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
  } catch {
    throw new Error("The business timezone is invalid. Update it in Business Settings.");
  }

  const value = (type: Intl.DateTimeFormatPartTypes) => {
    const part = parts.find((item) => item.type === type)?.value;
    const number = Number(part);
    if (!part || !Number.isInteger(number)) {
      throw new Error("The selected date and time could not be read.");
    }
    return number;
  };

  return {
    year: value("year"),
    month: value("month"),
    day: value("day"),
    hour: value("hour"),
    minute: value("minute"),
  };
}

export function localDateTimeValueInZone(date: Date, timezone: string): string {
  const parts = zonedDateTimeParts(date, timezone);
  const pad = (value: number) => String(value).padStart(2, "0");

  return (
    `${parts.year}-${pad(parts.month)}-${pad(parts.day)}` +
    `T${pad(parts.hour)}:${pad(parts.minute)}`
  );
}

export function localDateTimeToUtcIso(value: string, timezone: string): string {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value.trim());

  if (!match) {
    throw new Error("Choose a valid date and time.");
  }

  const [, yearText, monthText, dayText, hourText, minuteText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const requestedWallClock = Date.UTC(year, month - 1, day, hour, minute, 0, 0);
  const requestedDate = new Date(requestedWallClock);

  if (
    requestedDate.getUTCFullYear() !== year ||
    requestedDate.getUTCMonth() !== month - 1 ||
    requestedDate.getUTCDate() !== day ||
    requestedDate.getUTCHours() !== hour ||
    requestedDate.getUTCMinutes() !== minute
  ) {
    throw new Error("Choose a valid date and time.");
  }

  // Interpret the wall-clock fields in the business IANA timezone, never in
  // the browser/computer timezone.
  let instant = requestedWallClock;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const actual = zonedDateTimeParts(new Date(instant), timezone);
    const actualWallClock = Date.UTC(
      actual.year,
      actual.month - 1,
      actual.day,
      actual.hour,
      actual.minute,
      0,
      0,
    );
    const difference = requestedWallClock - actualWallClock;
    if (difference === 0) break;
    instant += difference;
  }

  const result = new Date(instant);
  if (localDateTimeValueInZone(result, timezone) !== value.trim()) {
    throw new Error(
      "That local time does not exist in the business timezone. Choose another time.",
    );
  }
  return result.toISOString();
}

function splitTerms(value: string, separator: RegExp) {
  return [...new Set(value.split(separator).map((item) => item.trim()).filter(Boolean))];
}
