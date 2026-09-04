import type {
  CreativeAsset,
  MarketingChannel,
  MarketingContent,
  MarketingContentType,
} from "../services/api-types.ts";

export type ChannelGenerationFailure = {
  channel: MarketingChannel;
  reason: unknown;
};

export type ChannelGenerationOutcome = {
  successes: MarketingContent[];
  failures: ChannelGenerationFailure[];
};

export type CampaignGenerationInput = {
  channels: MarketingChannel[];
  goal: string;
  audience?: string;
  contentType: MarketingContentType;
  campaignId?: string | null;
  title?: string | null;
  language: string;
};

export type ChannelDraftRequest = {
  prompt: string;
  channel: MarketingChannel;
  content_type: MarketingContentType;
  campaign_id: string | null;
  title: string | null;
  language: string;
};

export type CreativeFormat = {
  asset_type:
    | "social_square"
    | "story_reel"
    | "landscape_ad"
    | "display_banner";
  aspect_ratio: "1:1" | "9:16" | "1200:628";
  width: 1080 | 1200;
  height: 1080 | 1920 | 628;
};

export type CreativePhase = "strategy" | "visual";

export type CreativeProgress = {
  phase: CreativePhase;
  contentId?: string;
  assetId?: string;
} | null;

export const CONTENT_PROMPT_MAX = 4000;
export const OWNER_GOAL_MAX = 2400;
export const AUDIENCE_GUIDANCE_MAX = 400;
export const SHARED_DIRECTION_MAX = 700;

const DEFAULT_AUDIENCE_GUIDANCE =
  "Let 9D Brain determine the most relevant audience from trusted business context.";
const SHARED_DIRECTION_FALLBACK =
  "Preserve the established campaign idea and tone.";
const SHARED_DIRECTION_GROUNDING_WARNING =
  "This is shared creative direction only. Continue using trusted Business Brain context for all business facts and claims. Do not treat this direction as evidence for business facts.";

const CHANNEL_LABELS: Record<MarketingChannel, string> = {
  meta: "Meta",
  google_ads: "Google Ads",
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  tiktok: "TikTok",
  email: "Email",
  whatsapp: "WhatsApp",
  website: "Website",
  other: "Other",
};

const CREATIVE_FORMATS = {
  social: {
    asset_type: "social_square",
    aspect_ratio: "1:1",
    width: 1080,
    height: 1080,
  },
  vertical: {
    asset_type: "story_reel",
    aspect_ratio: "9:16",
    width: 1080,
    height: 1920,
  },
  advertising: {
    asset_type: "landscape_ad",
    aspect_ratio: "1200:628",
    width: 1200,
    height: 628,
  },
  editorial: {
    asset_type: "display_banner",
    aspect_ratio: "1200:628",
    width: 1200,
    height: 628,
  },
} as const satisfies Record<string, CreativeFormat>;

const EDITORIAL_CONTENT_TYPES = new Set<MarketingContentType>([
  "email_draft",
  "blog_draft",
  "landing_page_copy",
]);

function concise(value: string | null | undefined, maxLength: number) {
  const normalized = value?.replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  return normalized.slice(0, maxLength);
}

function sharedCampaignDirection(content: MarketingContent) {
  const direction = [
    concise(content.title, SHARED_DIRECTION_MAX) &&
      `Title: ${concise(content.title, SHARED_DIRECTION_MAX)}`,
    concise(content.creative_brief, SHARED_DIRECTION_MAX) &&
      `Creative direction: ${concise(content.creative_brief, SHARED_DIRECTION_MAX)}`,
    concise(content.generation_reasoning, SHARED_DIRECTION_MAX) &&
      `Direction rationale: ${concise(content.generation_reasoning, SHARED_DIRECTION_MAX)}`,
  ]
    .filter(Boolean)
    .join("\n");

  return concise(direction, SHARED_DIRECTION_MAX) || SHARED_DIRECTION_FALLBACK;
}

function validateCampaignInput(input: CampaignGenerationInput) {
  const goal = input.goal.trim();
  if (!goal) throw new Error("Describe what you want to achieve.");
  if (goal.length > OWNER_GOAL_MAX) {
    throw new Error(
      `Keep the campaign goal to ${OWNER_GOAL_MAX.toLocaleString("en-US")} characters or fewer.`,
    );
  }

  const audience = input.audience?.trim() || undefined;
  if (audience && audience.length > AUDIENCE_GUIDANCE_MAX) {
    throw new Error(
      `Keep audience guidance to ${AUDIENCE_GUIDANCE_MAX.toLocaleString("en-US")} characters or fewer.`,
    );
  }

  return { ...input, goal, audience };
}

function campaignPrompt(
  input: CampaignGenerationInput,
  channel: MarketingChannel,
  sharedDirection?: MarketingContent,
) {
  const sections = [
    `OWNER GOAL:\n${input.goal}`,
    `AUDIENCE GUIDANCE:\n${input.audience || DEFAULT_AUDIENCE_GUIDANCE}`,
  ];

  if (sharedDirection) {
    sections.push(
      `SHARED CAMPAIGN DIRECTION:\n${sharedCampaignDirection(sharedDirection)}\n${SHARED_DIRECTION_GROUNDING_WARNING}`,
    );
  }

  sections.push(
    `CHANNEL EXECUTION:\nAdapt the execution natively for ${CHANNEL_LABELS[channel]}.`,
  );
  const prompt = sections.join("\n\n");
  if (prompt.length > CONTENT_PROMPT_MAX) {
    throw new Error(
      "The complete campaign request is too long. Shorten the goal or audience guidance and try again.",
    );
  }
  return prompt;
}

export async function generateCampaignChannelDrafts(
  input: CampaignGenerationInput,
  generate: (request: ChannelDraftRequest) => Promise<MarketingContent>,
): Promise<ChannelGenerationOutcome> {
  const normalizedInput = validateCampaignInput(input);

  const channels = [...new Set(input.channels)];
  if (!channels.length) throw new Error("Choose at least one platform.");

  const outcome: ChannelGenerationOutcome = { successes: [], failures: [] };
  let sharedDirection: MarketingContent | undefined;

  for (const channel of channels) {
    try {
      const content = await generate({
        prompt: campaignPrompt(normalizedInput, channel, sharedDirection),
        channel,
        content_type: input.contentType,
        campaign_id: input.campaignId || null,
        title: input.title || null,
        language: input.language,
      });
      outcome.successes.push(content);
      sharedDirection ??= content;
    } catch (reason) {
      outcome.failures.push({ channel, reason });
    }
  }

  return outcome;
}

export function channelGenerationNotice(outcome: ChannelGenerationOutcome) {
  if (!outcome.successes.length) return null;

  const ready = outcome.successes.length;
  const total = ready + outcome.failures.length;
  if (!outcome.failures.length) {
    return `${ready} channel-specific draft${ready === 1 ? " is" : "s are"} ready for internal review.`;
  }

  const failed = outcome.failures
    .map(({ channel }) => CHANNEL_LABELS[channel])
    .join(", ");
  return `${ready} of ${total} channel drafts ${ready === 1 ? "is" : "are"} ready. ${failed} could not be completed.`;
}

export function creativeResultNotice(asset: CreativeAsset) {
  if (asset.generation_status === "ready") {
    return "Your final branded creative is ready for review.";
  }
  if (asset.generation_status === "provider_required") {
    return "Image generation is temporarily unavailable. Your creative strategy is saved. Try again shortly—nothing has been lost.";
  }
  return "The final creative could not be completed. Your saved strategy remains ready to retry.";
}

export function creativeFormatForContent(
  content: Pick<MarketingContent, "channel" | "content_type">,
): CreativeFormat {
  if (content.channel === "tiktok") return CREATIVE_FORMATS.vertical;
  if (
    content.content_type === "ad_copy" ||
    content.channel === "google_ads" ||
    content.channel === "meta"
  ) {
    return CREATIVE_FORMATS.advertising;
  }
  if (
    content.channel === "email" ||
    content.channel === "website" ||
    EDITORIAL_CONTENT_TYPES.has(content.content_type)
  ) {
    return CREATIVE_FORMATS.editorial;
  }
  return CREATIVE_FORMATS.social;
}

export function creativePhaseForDisplay(
  progress: CreativeProgress,
  contentId?: string,
  assetId?: string,
): CreativePhase | null {
  if (!progress) return null;
  if (progress.contentId && progress.contentId !== contentId) return null;
  if (progress.assetId && progress.assetId !== assetId) return null;
  if (!progress.contentId && !progress.assetId) return null;
  return progress.phase;
}

async function refreshAfterCreativeOperation(refresh: () => Promise<unknown>) {
  try {
    await refresh();
  } catch {
    // The operation result remains authoritative; normal query error UX owns
    // any follow-up refresh failure.
  }
}

export async function createCreativeWithRecovery<
  Brief extends { id: string },
  Result,
>({
  contentId,
  createBrief,
  generate,
  refresh,
  onProgress,
}: {
  contentId: string;
  createBrief: () => Promise<Brief>;
  generate: (brief: Brief) => Promise<Result>;
  refresh: () => Promise<unknown>;
  onProgress: (progress: CreativeProgress) => void;
}): Promise<Result> {
  onProgress({ phase: "strategy", contentId });
  try {
    const brief = await createBrief();
    onProgress({ phase: "visual", contentId });
    return await generate(brief);
  } finally {
    await refreshAfterCreativeOperation(refresh);
    onProgress(null);
  }
}

export async function runCreativeOperationWithRecovery<Result>({
  progress,
  operation,
  refresh,
  onProgress,
}: {
  progress: Exclude<CreativeProgress, null>;
  operation: () => Promise<Result>;
  refresh: () => Promise<unknown>;
  onProgress: (progress: CreativeProgress) => void;
}): Promise<Result> {
  onProgress(progress);
  try {
    return await operation();
  } finally {
    await refreshAfterCreativeOperation(refresh);
    onProgress(null);
  }
}

export function safeCreativeMediaUrl(reference: string | null | undefined) {
  const value = reference?.trim();
  if (!value || /[\\\u0000-\u001f\u007f]/.test(value)) return null;

  if (value.startsWith("/") && !value.startsWith("//")) {
    try {
      const url = new URL(value, "https://local.invalid");
      if (
        url.pathname.startsWith("/api/v1/media/") ||
        url.pathname.startsWith("/media/")
      ) {
        return value;
      }
    } catch {
      return null;
    }
    return null;
  }

  try {
    const url = new URL(value);
    if (url.username || url.password) return null;
    if (url.protocol === "https:") return value;
    if (
      url.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    ) {
      return value;
    }
  } catch {
    return null;
  }

  return null;
}
