import assert from "node:assert/strict";
import test from "node:test";

import type {
  CreativeAsset,
  MarketingChannel,
  MarketingContent,
} from "../services/api-types.ts";
import { ApiError, humanizeApiError } from "../services/api-client.ts";
import {
  AUDIENCE_GUIDANCE_MAX,
  CONTENT_PROMPT_MAX,
  OWNER_GOAL_MAX,
  SHARED_DIRECTION_MAX,
  channelGenerationNotice,
  createCreativeWithRecovery,
  creativeFormatForContent,
  creativePhaseForDisplay,
  creativeResultNotice,
  generateCampaignChannelDrafts,
  runCreativeOperationWithRecovery,
  safeCreativeMediaUrl,
  type CreativeProgress,
} from "./cmo-ux.ts";

function content(
  channel: MarketingChannel,
  values: Partial<MarketingContent> = {},
): MarketingContent {
  return {
    id: `content-${channel}`,
    business_id: "business-one",
    campaign_id: null,
    channel,
    content_type: "social_post",
    title: `${channel} launch`,
    body: `Native ${channel} copy`,
    cta: "Explore",
    language: "en",
    status: "draft",
    ai_generated: true,
    version: 1,
    parent_content_id: null,
    root_content_id: `content-${channel}`,
    created_by_user_id: null,
    creative_brief: "Premium product-led campaign with clear negative space.",
    generation_reasoning: "Lead with the strongest supported customer benefit.",
    source_evidence: [],
    created_at: "2026-09-04T00:00:00Z",
    updated_at: "2026-09-04T00:00:00Z",
    ...values,
  };
}

const campaignInput = {
  goal: "Promote our new shoes",
  audience: "Returning customers",
  contentType: "social_post" as const,
  campaignId: "campaign-one",
  title: null,
  language: "en",
};

test("single-platform generation returns its durable success", async () => {
  const requests: MarketingChannel[] = [];
  const outcome = await generateCampaignChannelDrafts(
    { ...campaignInput, channels: ["instagram"] },
    async (request) => {
      requests.push(request.channel);
      return content(request.channel);
    },
  );

  assert.deepEqual(requests, ["instagram"]);
  assert.deepEqual(outcome.successes.map((item) => item.channel), ["instagram"]);
  assert.deepEqual(outcome.failures, []);
  assert.equal(
    channelGenerationNotice(outcome),
    "1 channel-specific draft is ready for internal review.",
  );
});

test("multi-platform generation completes sequential native variants", async () => {
  const channels: MarketingChannel[] = ["instagram", "facebook", "linkedin"];
  const requests: MarketingChannel[] = [];
  const outcome = await generateCampaignChannelDrafts(
    { ...campaignInput, channels },
    async (request) => {
      requests.push(request.channel);
      return content(request.channel);
    },
  );

  assert.deepEqual(requests, channels);
  assert.deepEqual(outcome.successes.map((item) => item.channel), channels);
  assert.deepEqual(outcome.failures, []);
});

test("multi-platform partial success preserves successful drafts", async () => {
  const privateFailure = new Error("private upstream detail");
  const outcome = await generateCampaignChannelDrafts(
    {
      ...campaignInput,
      channels: ["instagram", "facebook", "linkedin", "tiktok"],
    },
    async (request) => {
      if (request.channel === "linkedin") throw privateFailure;
      return content(request.channel);
    },
  );

  assert.deepEqual(
    outcome.successes.map((item) => item.channel),
    ["instagram", "facebook", "tiktok"],
  );
  assert.deepEqual(outcome.failures, [
    { channel: "linkedin", reason: privateFailure },
  ]);
  const notice = channelGenerationNotice(outcome);
  assert.equal(
    notice,
    "3 of 4 channel drafts are ready. LinkedIn could not be completed.",
  );
  assert.equal(notice?.includes(privateFailure.message), false);
});

test("an all-channel failure produces no misleading success notice", async () => {
  const failure = new Error("specific safe failure");
  const outcome = await generateCampaignChannelDrafts(
    { ...campaignInput, channels: ["instagram", "linkedin"] },
    async () => {
      throw failure;
    },
  );

  assert.deepEqual(outcome.successes, []);
  assert.deepEqual(outcome.failures.map((item) => item.channel), [
    "instagram",
    "linkedin",
  ]);
  assert.equal(outcome.failures[0]?.reason, failure);
  assert.equal(
    humanizeApiError(
      outcome.failures[0]?.reason,
      "AI content generation could not be completed. No channel drafts were created.",
    ),
    "AI content generation could not be completed. No channel drafts were created.",
  );
  assert.equal(channelGenerationNotice(outcome), null);
});

test("an all-channel ApiError keeps the first failure available to the existing humanizer", async () => {
  const failure = new ApiError(503, {
    detail: { code: "provider_unavailable" },
  });
  const outcome = await generateCampaignChannelDrafts(
    { ...campaignInput, channels: ["instagram", "linkedin"] },
    async () => {
      throw failure;
    },
  );

  assert.deepEqual(outcome.successes, []);
  assert.equal(outcome.failures[0]?.reason, failure);
  assert.equal(
    humanizeApiError(
      outcome.failures[0]?.reason,
      "AI content generation could not be completed. No channel drafts were created.",
    ),
    "The external provider is temporarily degraded. Your internal data remains available.",
  );
});

test("the first success supplies shared direction to later variants", async () => {
  const prompts = new Map<MarketingChannel, string>();
  await generateCampaignChannelDrafts(
    { ...campaignInput, channels: ["instagram", "linkedin"] },
    async (request) => {
      prompts.set(request.channel, request.prompt);
      return content(request.channel);
    },
  );

  assert.doesNotMatch(prompts.get("instagram") || "", /SHARED CAMPAIGN DIRECTION/);
  assert.match(prompts.get("linkedin") || "", /SHARED CAMPAIGN DIRECTION/);
  assert.match(prompts.get("linkedin") || "", /instagram launch/);
  assert.match(prompts.get("linkedin") || "", /trusted Business Brain context/);
  assert.match(prompts.get("linkedin") || "", /Do not treat this direction as evidence/);
  assert.match(prompts.get("linkedin") || "", /Adapt the execution natively for LinkedIn/);
});

test("maximum owner and audience inputs stay intact within the final prompt budget", async () => {
  const goal = "G".repeat(OWNER_GOAL_MAX);
  const audience = "A".repeat(AUDIENCE_GUIDANCE_MAX);
  const prompts: string[] = [];

  await generateCampaignChannelDrafts(
    {
      ...campaignInput,
      channels: ["instagram", "google_ads"],
      goal,
      audience,
    },
    async (request) => {
      prompts.push(request.prompt);
      return content(request.channel, {
        title: "T".repeat(2000),
        creative_brief: "B".repeat(2000),
        generation_reasoning: "R".repeat(2000),
      });
    },
  );

  assert.equal(prompts.length, 2);
  assert.equal(
    prompts[0].split("OWNER GOAL:\n")[1].split("\n\nAUDIENCE GUIDANCE:")[0],
    goal,
  );
  assert.equal(
    prompts[0].split("AUDIENCE GUIDANCE:\n")[1].split("\n\nCHANNEL EXECUTION:")[0],
    audience,
  );
  assert.ok(prompts[0].length <= CONTENT_PROMPT_MAX);
  assert.ok(prompts[1].length <= CONTENT_PROMPT_MAX);
  assert.deepEqual(prompts.map((prompt) => prompt.length), [2897, 3808]);
  assert.match(prompts[1], /SHARED CAMPAIGN DIRECTION/);
  assert.match(
    prompts[1],
    /This is shared creative direction only\. Continue using trusted Business Brain context for all business facts and claims\. Do not treat this direction as evidence for business facts\./,
  );
  assert.equal(
    prompts[1]
      .split("SHARED CAMPAIGN DIRECTION:\n")[1]
      .split("\nThis is shared creative direction only.")[0].length,
    SHARED_DIRECTION_MAX,
  );
  assert.equal(prompts[1].includes(goal), true);
});

test("an over-limit owner goal fails locally before generation", async () => {
  let generationCalls = 0;

  await assert.rejects(
    generateCampaignChannelDrafts(
      {
        ...campaignInput,
        channels: ["instagram"],
        goal: "G".repeat(OWNER_GOAL_MAX + 1),
      },
      async (request) => {
        generationCalls += 1;
        return content(request.channel);
      },
    ),
    /2,400 characters or fewer/,
  );
  assert.equal(generationCalls, 0);
});

test("over-limit audience guidance fails locally before generation", async () => {
  let generationCalls = 0;

  await assert.rejects(
    generateCampaignChannelDrafts(
      {
        ...campaignInput,
        channels: ["instagram"],
        audience: "A".repeat(AUDIENCE_GUIDANCE_MAX + 1),
      },
      async (request) => {
        generationCalls += 1;
        return content(request.channel);
      },
    ),
    /400 characters or fewer/,
  );
  assert.equal(generationCalls, 0);
});

test("creative creation refreshes a saved brief after generation fails", async () => {
  const phases: CreativeProgress[] = [];
  let savedBrief: { id: string } | null = null;
  let discoveredBrief: { id: string } | null = null;

  await assert.rejects(
    createCreativeWithRecovery({
      contentId: "content-instagram",
      createBrief: async () => {
        savedBrief = { id: "brief-one" };
        return savedBrief;
      },
      generate: async () => {
        throw new Error("network interrupted after brief creation");
      },
      refresh: async () => {
        discoveredBrief = savedBrief;
      },
      onProgress: (progress) => phases.push(progress),
    }),
    /network interrupted/,
  );

  assert.deepEqual(discoveredBrief, { id: "brief-one" });
  assert.deepEqual(phases, [
    { phase: "strategy", contentId: "content-instagram" },
    { phase: "visual", contentId: "content-instagram" },
    null,
  ]);
});

test("creative regeneration refreshes immutable history after interruption", async () => {
  let refreshes = 0;
  await assert.rejects(
    runCreativeOperationWithRecovery({
      progress: {
        phase: "visual",
        contentId: "content-instagram",
        assetId: "creative-one",
      },
      operation: async () => {
        throw new Error("response interrupted after revision creation");
      },
      refresh: async () => {
        refreshes += 1;
      },
      onProgress: () => undefined,
    }),
    /response interrupted/,
  );
  assert.equal(refreshes, 1);
});

test("creative progress is visible only for its matching item", () => {
  const progress = {
    phase: "visual",
    contentId: "content-one",
    assetId: "asset-one",
  } as const;
  assert.equal(
    creativePhaseForDisplay(progress, "content-one", "asset-one"),
    "visual",
  );
  assert.equal(
    creativePhaseForDisplay(progress, "content-two", "asset-one"),
    null,
  );
  assert.equal(
    creativePhaseForDisplay(progress, "content-one", "asset-two"),
    null,
  );
});

test("creative format mapping stays within supported deterministic formats", () => {
  assert.deepEqual(
    creativeFormatForContent({ channel: "tiktok", content_type: "social_post" }),
    { asset_type: "story_reel", aspect_ratio: "9:16", width: 1080, height: 1920 },
  );
  assert.deepEqual(
    creativeFormatForContent({ channel: "google_ads", content_type: "ad_copy" }),
    { asset_type: "landscape_ad", aspect_ratio: "1200:628", width: 1200, height: 628 },
  );
  assert.deepEqual(
    creativeFormatForContent({ channel: "email", content_type: "email_draft" }),
    { asset_type: "display_banner", aspect_ratio: "1200:628", width: 1200, height: 628 },
  );
  assert.deepEqual(
    creativeFormatForContent({ channel: "linkedin", content_type: "social_post" }),
    { asset_type: "social_square", aspect_ratio: "1:1", width: 1080, height: 1080 },
  );
});

test("provider-required creative copy stays customer-facing", () => {
  const notice = creativeResultNotice({
    generation_status: "provider_required",
  } as CreativeAsset);

  assert.match(notice, /temporarily unavailable/);
  assert.match(notice, /nothing has been lost/);
  for (const forbidden of ["server-side", "OpenAI", "API key"]) {
    assert.equal(notice.toLowerCase().includes(forbidden.toLowerCase()), false);
  }
});

test("creative media URL validation allows only supported relative media routes", () => {
  assert.equal(
    safeCreativeMediaUrl("https://cdn.example.test/final/creative.png"),
    "https://cdn.example.test/final/creative.png",
  );
  assert.equal(safeCreativeMediaUrl("/media/creative.png"), "/media/creative.png");
  assert.equal(
    safeCreativeMediaUrl("/api/v1/media/creative.png"),
    "/api/v1/media/creative.png",
  );
  assert.equal(
    safeCreativeMediaUrl("http://localhost:5174/media/creative.png"),
    "http://localhost:5174/media/creative.png",
  );
  assert.equal(
    safeCreativeMediaUrl("http://[::1]:5174/media/creative.png"),
    "http://[::1]:5174/media/creative.png",
  );
  for (const unsafe of [
    "javascript:alert(1)",
    "data:image/svg+xml,<svg onload=alert(1)>",
    "vbscript:msgbox(1)",
    "//untrusted.example/creative.png",
    "http://untrusted.example/creative.png",
    "/logout",
    "/settings",
    "/api/v1/businesses/business-one",
    "/random/creative.png",
    "/media/../logout",
    "/api/v1/media/../../settings",
    "https://user:password@cdn.example.test/creative.png",
    "/media/creative\\preview.png",
    "/media/creative.png\u0000.svg",
  ]) {
    assert.equal(safeCreativeMediaUrl(unsafe), null, unsafe);
  }
});
