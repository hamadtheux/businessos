import assert from "node:assert/strict";
import test from "node:test";

import type {
  CreativeAsset,
  MarketingChannel,
  MarketingContent,
} from "../services/api-types.ts";
import { ApiError, humanizeApiError } from "../services/api-client.ts";
import type {
  ConnectorDefinition,
  IntegrationConnection,
} from "../services/integrations.ts";
import {
  AUDIENCE_GUIDANCE_MAX,
  CREATIVE_GENERATION_POLL_MS,
  CONTENT_PROMPT_MAX,
  OWNER_GOAL_MAX,
  SHARED_DIRECTION_MAX,
  channelGenerationNotice,
  createCreativeWithRecovery,
  creativeFormatForContent,
  creativePhaseForDisplay,
  creativeResultNotice,
  generateCampaignChannelDrafts,
  isCreativeGenerationActive,
  isCreativePreviewFailureCurrent,
  publishingCapability,
  recommendedCreativeMediaForContent,
  runCreativeOperationWithRecovery,
  safeCreativeMediaUrl,
  videoFormatForContent,
  type CreativeProgress,
} from "./cmo-ux.ts";

type PrivateCreativeAssetKeys = Extract<
  keyof CreativeAsset,
  "provider_key" | "provider_job_reference" | "creative_metadata"
>;
const privateCreativeAssetKeysStayServerSide: PrivateCreativeAssetKeys extends never
  ? true
  : never = true;
void privateCreativeAssetKeysStayServerSide;

test("creative polling is bounded to active generation states", () => {
  assert.equal(CREATIVE_GENERATION_POLL_MS, 3_000);
  for (const generation_status of [
    "queued",
    "generating",
    "reviewing",
    "repairing",
  ] as const) {
    assert.equal(isCreativeGenerationActive({ generation_status }), true);
  }
  for (const generation_status of [
    "brief_ready",
    "provider_required",
    "ready",
    "failed",
    "archived",
  ] as const) {
    assert.equal(isCreativeGenerationActive({ generation_status }), false);
  }
});

test("creative preview failure is cleared only by a different asset or URL", () => {
  const failure = {
    creativeId: "creative-one",
    reference: "https://media.example.test/final.png?signature=expired",
  };

  assert.equal(
    isCreativePreviewFailureCurrent(
      failure,
      "creative-one",
      "https://media.example.test/final.png?signature=expired",
    ),
    true,
  );
  assert.equal(
    isCreativePreviewFailureCurrent(
      failure,
      "creative-one",
      "https://media.example.test/final.png?signature=fresh",
    ),
    false,
  );
  assert.equal(
    isCreativePreviewFailureCurrent(
      failure,
      "creative-two",
      failure.reference,
    ),
    false,
  );
});

const publicCreativeAsset: CreativeAsset = {
  id: "creative-one",
  business_id: "business-one",
  campaign_id: null,
  content_id: "content-one",
  asset_type: "social_square",
  media_type: "image",
  source_type: "ai_brief",
  instructions: "Create a grounded campaign visual.",
  visual_direction: "A product-led composition.",
  generation_status: "ready",
  storage_reference: "/media/creative-one.png",
  width: 1080,
  height: 1080,
  aspect_ratio: "1:1",
  alt_text: "Grounded campaign creative",
  duration_seconds: null,
  created_at: "2026-09-06T00:00:00Z",
  updated_at: "2026-09-06T00:00:00Z",
};

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

test("snapshotted campaign input preserves fields and deduplicates the default channel", async () => {
  const requests: Parameters<Parameters<typeof generateCampaignChannelDrafts>[1]>[0][] = [];
  const outcome = await generateCampaignChannelDrafts(
    {
      channels: ["instagram", "instagram"],
      goal: "Promote the autumn collection",
      audience: "Returning customers",
      contentType: "email_draft",
      campaignId: "campaign-autumn",
      title: "Autumn launch",
      language: "ur",
    },
    async (request) => {
      requests.push(request);
      return content(request.channel);
    },
  );

  assert.equal(requests.length, 1);
  assert.deepEqual(outcome.successes.map((item) => item.channel), ["instagram"]);
  assert.equal(requests[0].channel, "instagram");
  assert.equal(requests[0].content_type, "email_draft");
  assert.equal(requests[0].campaign_id, "campaign-autumn");
  assert.equal(requests[0].title, "Autumn launch");
  assert.equal(requests[0].language, "ur");
  assert.match(requests[0].prompt, /Promote the autumn collection/);
  assert.match(requests[0].prompt, /Returning customers/);
});

test("authorized business offer is sent as explicit provenance-bearing input", async () => {
  const requests: Parameters<Parameters<typeof generateCampaignChannelDrafts>[1]>[0][] = [];
  await generateCampaignChannelDrafts(
    {
      ...campaignInput,
      channels: ["instagram"],
      offer: "50% off",
      offerAuthorized: true,
    },
    async (request) => {
      requests.push(request);
      return content(request.channel);
    },
  );
  assert.equal(requests[0].offer, "50% off");
  assert.equal(requests[0].offer_authorized, true);
});

test("unconfirmed business offer is rejected before any API request", async () => {
  let calls = 0;
  await assert.rejects(
    generateCampaignChannelDrafts(
      { ...campaignInput, channels: ["instagram"], offer: "50% off" },
      async (request) => {
        calls += 1;
        return content(request.channel);
      },
    ),
    /Confirm that this offer is authorized for this business/,
  );
  assert.equal(calls, 0);
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

test("public creative assets contain only the approved response fields", () => {
  assert.deepEqual(Object.keys(publicCreativeAsset).sort(), [
    "alt_text",
    "aspect_ratio",
    "asset_type",
    "business_id",
    "campaign_id",
    "content_id",
    "created_at",
    "duration_seconds",
    "generation_status",
    "height",
    "id",
    "instructions",
    "media_type",
    "source_type",
    "storage_reference",
    "updated_at",
    "visual_direction",
    "width",
  ]);
});

test("creative media recommendation is deterministic and user-overridable", () => {
  assert.equal(
    recommendedCreativeMediaForContent({ channel: "tiktok", content_type: "social_post" }),
    "video",
  );
  assert.equal(
    recommendedCreativeMediaForContent({ channel: "tiktok", content_type: "blog_draft" }),
    "image",
  );
  assert.equal(
    recommendedCreativeMediaForContent({ channel: "instagram", content_type: "social_post" }),
    "image",
  );
  assert.equal(
    recommendedCreativeMediaForContent({ channel: "email", content_type: "email_draft" }),
    "image",
  );
});

test("video format mapping reflects both channel and content type", () => {
  assert.deepEqual(
    videoFormatForContent({ channel: "instagram", content_type: "social_post" }),
    { aspect_ratio: "1:1", duration_seconds: 15 },
  );
  assert.deepEqual(
    videoFormatForContent({ channel: "tiktok", content_type: "social_post" }),
    { aspect_ratio: "9:16", duration_seconds: 15 },
  );
  assert.deepEqual(
    videoFormatForContent({ channel: "website", content_type: "landing_page_copy" }),
    { aspect_ratio: "16:9", duration_seconds: 30 },
  );
  assert.deepEqual(
    videoFormatForContent({ channel: "google_ads", content_type: "cta" }),
    { aspect_ratio: "16:9", duration_seconds: 8 },
  );
  assert.deepEqual(
    videoFormatForContent({ channel: "linkedin", content_type: "social_post" }),
    { aspect_ratio: "1:1", duration_seconds: 15 },
  );
});

test("publishing derives readiness from current connector capabilities and fails closed", () => {
  const definition: ConnectorDefinition = {
    connector_type: "instagram",
    display_name: "Instagram",
    description: "Governed social publishing",
    category: "social",
    authentication_type: "oauth2",
    capabilities: ["read_content_performance", "publish_social_post"],
    read_capabilities: [],
    future_write_capabilities: ["future_publish_content"],
    requested_scopes: [],
    webhook_support: false,
    external_writes_enabled: true,
    resource_types: ["page"],
    configuration_requirements: [],
    resource_selection_required: true,
    setup_status: "available",
  };
  const connection: IntegrationConnection = {
    id: "connection-one",
    business_id: "business-one",
    connector_type: "instagram",
    display_name: "Instagram",
    status: "connected",
    authentication_state: "authorized",
    health: "healthy",
    external_account_reference: "account-one",
    external_account_display_name: "Business account",
    selected_resources: [
      {
        resource_type: "page",
        external_reference: "page-one",
        display_name: "Business page",
      },
    ],
    scopes_granted: [],
    connected_by_user_id: "user-one",
    connected_at: "2026-09-06T00:00:00Z",
    last_health_check_at: "2026-09-06T00:00:00Z",
    last_successful_sync_at: null,
    failure_code: null,
    created_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
  };

  assert.equal(
    publishingCapability({ channel: "instagram", definition, connection }).canPrepare,
    true,
  );
  const futureOnly = publishingCapability({
    channel: "instagram",
    definition: {
      ...definition,
      capabilities: ["future_publish_content"],
      future_write_capabilities: ["future_publish_content"],
    },
    connection,
  });
  assert.equal(futureOnly.canPrepare, false);
  assert.equal(futureOnly.state, "unsupported");
  const writesDisabled = publishingCapability({
    channel: "instagram",
    definition: { ...definition, external_writes_enabled: false },
    connection,
  });
  assert.equal(writesDisabled.canPrepare, false);
  assert.equal(writesDisabled.state, "unsupported");
  const pending = publishingCapability({
    channel: "instagram",
    definition,
    connection,
    pending: true,
  });
  assert.equal(pending.canPrepare, false);
  assert.equal(pending.state, "checking");
  const failed = publishingCapability({
    channel: "instagram",
    definition,
    connection,
    failed: true,
  });
  assert.equal(failed.canPrepare, false);
  assert.equal(failed.state, "unverified");
  const unhealthy = publishingCapability({
    channel: "instagram",
    definition,
    connection: { ...connection, health: "degraded" },
  });
  assert.equal(unhealthy.canPrepare, false);
  assert.equal(unhealthy.state, "unavailable");
  const missingResource = publishingCapability({
    channel: "instagram",
    definition,
    connection: { ...connection, selected_resources: [] },
  });
  assert.equal(missingResource.canPrepare, false);
  assert.equal(missingResource.state, "unavailable");
  assert.equal(
    publishingCapability({ channel: "linkedin" }).state,
    "unsupported",
  );
});

test("provider-required creative copy stays customer-facing", () => {
  const notice = creativeResultNotice({
    generation_status: "provider_required",
    media_type: "video",
  } as CreativeAsset);

  assert.match(notice, /isn’t connected yet/);
  assert.match(notice, /strategy and storyboard are saved/);
  for (const forbidden of [
    "provider job",
    "job reference",
    "reconciliation",
    "pipeline",
    "internal state",
  ]) {
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
