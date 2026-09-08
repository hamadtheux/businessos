import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createMarketingApi } from "../services/marketing.ts";

const businessA = "71000000-0000-4000-8000-000000000001";
const businessB = "72000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "73000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createMarketingApi(client);
}

test("marketing lists use tenant paths and bounded pagination", async () => {
  const urls: URL[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    urls.push(new URL(String(input)));
    return json({ items: [], total: 0, page: 2, page_size: 20 });
  });
  await api.campaigns.list(businessA, { page: 2, pageSize: 20, search: "Summer", status: "draft" });
  assert.equal(urls[0].pathname, `/api/v1/businesses/${businessA}/marketing/campaigns`);
  assert.equal(urls[0].searchParams.get("page_size"), "20");
  assert.equal(urls[0].searchParams.get("search"), "Summer");
  assert.equal(urls[0].searchParams.get("status"), "draft");
});

test("business switching creates independent marketing namespaces", async () => {
  const paths: string[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    paths.push(new URL(String(input)).pathname);
    return json({ items: [], total: 0, page: 1, page_size: 25 });
  });
  await api.competitors.list(businessA);
  await api.competitors.list(businessB);
  assert.deepEqual(paths, [`/api/v1/businesses/${businessA}/marketing/competitors`, `/api/v1/businesses/${businessB}/marketing/competitors`]);
});

test("campaign creation cannot send trusted currency or client totals", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.campaigns.create(businessA, { name: "Summer", objective: "Grow", audience_definition: "Existing customers", channels: ["instagram"], planned_budget: "2000", budget_mode: "lifetime" });
  assert.equal("currency" in body, false);
  assert.equal("total_allocated" in body, false);
  assert.equal(body.planned_budget, "2000");
});

test("performance creation sends source metrics but no derived metrics", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.performance.create(businessA, { campaign_id: "campaign", channel: "instagram", period_start: "2026-08-01", period_end: "2026-08-07", spend: "100", impressions: 1000, clicks: 20, conversions: 2, revenue: "300" });
  for (const field of ["ctr", "cpc", "cpm", "cpl", "cpa", "roas"]) assert.equal(field in body, false, field);
});

test("calendar scheduling remains an internal API record", async () => {
  let path = "";
  let body: unknown;
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    path = new URL(String(input)).pathname;
    body = JSON.parse(String(init?.body));
    return json({});
  });
  await api.calendar.create(businessA, "content-id", "2026-08-24T10:00:00Z");
  assert.equal(path, `/api/v1/businesses/${businessA}/marketing/calendar`);
  assert.deepEqual(body, { content_id: "content-id", scheduled_for: "2026-08-24T10:00:00Z" });
});

test("AI content generation uses the tenant-scoped POST endpoint", async () => {
  let request: { path: string; method: string; body: unknown } | null = null;
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    request = {
      path: new URL(String(input)).pathname,
      method: String(init?.method || "GET"),
      body: JSON.parse(String(init?.body)),
    };
    return json({});
  });
  const payload = {
    prompt: "Create an Instagram launch post",
    campaign_id: "campaign-one",
    channel: "instagram" as const,
    content_type: "social_post" as const,
    title: "Launch",
    language: "en",
  };

  await api.content.generate(businessA, payload);

  assert.deepEqual(request, {
    path: `/api/v1/businesses/${businessA}/marketing/content/generate`,
    method: "POST",
    body: payload,
  });
});

test("creative briefing, generation, and regeneration use tenant-scoped POST endpoints", async () => {
  const requests: Array<{ path: string; method: string }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({
      path: new URL(String(input)).pathname,
      method: String(init?.method || "GET"),
    });
    return json({});
  });

  await api.creative.brief(businessA, {
    campaign_id: "campaign-one",
    content_id: "content-one",
    asset_type: "social_square",
    instructions: "Create a campaign visual",
    aspect_ratio: "1:1",
    width: 1080,
    height: 1080,
    alt_text: "Campaign visual",
  });
  await api.creative.get(businessA, "creative-one");
  await api.creative.generate(businessA, "creative-one");
  await api.creative.regenerate(businessA, "creative-one");
  await api.creative.videoStrategy(businessA, {
    campaign_id: "campaign-one",
    content_id: "content-one",
    duration_seconds: 15,
    aspect_ratio: "9:16",
    instructions: "Create a grounded campaign video",
  });
  await api.creative.generateVideo(businessA, "video-one");

  assert.deepEqual(requests, [
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/brief`,
      method: "POST",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/creative-one`,
      method: "GET",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/creative-one/generate`,
      method: "POST",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/creative-one/regenerate`,
      method: "POST",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/video/strategy`,
      method: "POST",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/video-one/video/generate`,
      method: "POST",
    },
  ]);
});

test("plain regeneration sends no body while variation is explicit", async () => {
  const requests: Array<{ body: string | undefined }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({ body: typeof init?.body === "string" ? init.body : undefined });
    return json({});
  });

  await api.creative.regenerate(businessA, "creative-one");
  await api.creative.regenerate(
    businessA,
    "creative-one",
    "alternate_metaphor",
  );

  assert.deepEqual(requests, [
    { body: undefined },
    { body: JSON.stringify({ variation_mode: "alternate_metaphor" }) },
  ]);
});

test("CMO creative studio exposes honest visual lifecycle states and immutable regeneration", async () => {
  const [panel, studio, page, social, helpers, contentDrawer] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-creative-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-studio.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("./cmo-ux.ts", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-generator-drawer.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(panel, /creative-\$\{mediaType\}-empty-state/);
  assert.match(panel, /creative-generation-progress/);
  for (const state of [
    "creative-provider-required",
    "creative-failed-state",
    "creative-ready-preview",
  ]) {
    assert.match(panel, new RegExp(state));
  }
  assert.match(page, /CREATIVE_GENERATION_POLL_MS/);
  assert.match(social, /CREATIVE_GENERATION_POLL_MS/);
  assert.match(page, /marketingApi\.creative\.get/);
  assert.match(social, /marketingApi\.creative\.get/);
  assert.match(page, /activeCreativeGeneration\.businessId === activeBusinessId/);
  assert.match(social, /activeCreativeGeneration\.businessId === activeBusinessId/);
  assert.match(panel, /creative-loading-\$\{phase\}/);
  assert.match(panel, /Preparing creative direction/);
  assert.match(panel, /Generating branded image/);
  assert.match(panel, /Create a branded image/);
  assert.match(panel, /Create a branded campaign video/);
  assert.match(panel, /Video generation isn’t connected yet/);
  assert.match(panel, /Nothing will be published automatically/);
  assert.match(panel, /Variations/);
  assert.match(panel, /Regenerate/);
  assert.doesNotMatch(panel, /Create another/);
  assert.match(panel, /role="group" aria-label="Creative media type"/);
  assert.match(panel, /aria-expanded=\{historyOpen\}/);
  assert.match(panel, /aria-controls=\{`creative-history-\$\{mediaType\}`\}/);
  assert.match(panel, /testId=\{`creative-loading-\$\{phase\}`\} live="polite"/);
  assert.match(panel, /testId="creative-error" live="assertive"/);
  assert.match(panel, /Previous media remains read-only and is never overwritten/);
  assert.match(panel, /safeCreativeMediaUrl/);
  assert.match(panel, /onError=\{\(\) => setFailedPreviewId/);
  assert.match(panel, /creative-preview-unavailable/);
  assert.match(panel, /button-retry-preview/);
  assert.match(panel, /Creative is ready, but the preview could not be loaded/);
  assert.match(panel, /Immutable \{mediaType\} history/);
  assert.match(panel, /Previous \$\{mediaType\} creative/);
  assert.match(panel, /button-reload-creatives/);
  assert.match(panel, /creative-operation-error/);
  assert.match(panel, /button-retry-creative-operation/);
  assert.match(panel, /disabled=\{isPending\}/);
  assert.doesNotMatch(panel, /placeholder artwork|data:image/);
  for (const forbidden of ["server-side", "OpenAI", "API key", "raw visual"]) {
    assert.equal(panel.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
  }
  assert.match(studio, /button-approve-content/);
  assert.match(studio, /button-schedule-content/);
  assert.match(studio, /button-prepare-publish/);
  assert.equal(studio.match(/Create another/g)?.length, 1);
  assert.match(studio, /onClick=\{onGenerate\}/);
  assert.match(studio, /onEditDirection=\{onEditCreativeDirection\}/);
  assert.match(studio, /onHistory/);
  assert.match(studio, /creatives=\{creatives\}/);
  assert.match(page, /Saving creates a new immutable version/);
  assert.doesNotMatch(page, /editingContent\.version \+ 1/);
  assert.match(contentDrawer, /primaryPlatforms/);
  assert.match(page, /generateCampaignChannelDrafts/);
  assert.match(contentDrawer, /maxLength=\{OWNER_GOAL_MAX\}/);
  assert.match(contentDrawer, /maxLength=\{AUDIENCE_GUIDANCE_MAX\}/);
  assert.doesNotMatch(page, /Promise\.all\(selectedChannels\.map/);
  assert.match(page, /createCreativeWithRecovery/);
  assert.match(page, /runCreativeOperationWithRecovery/);
  assert.match(page, /creativePhaseForDisplay/);
  assert.match(page, /creatives=\{creativeAssets\.data\}/);
  assert.match(page, /publishingCapability/);
  assert.match(page, /marketingApi\.content\.preparePublish/);
  assert.match(page, /navigate\(`\/marketing\/content\?\$\{params\.toString\(\)\}`\)/);
  assert.match(page, /humanizeApiError/);
  assert.match(social, /creatives=\{assets\.data\}/);
  assert.match(social, /creativePhaseForDisplay/);
  assert.match(social, /button-regenerate-creative|onRegenerate/);
  assert.match(social, /Publishing requires approval and a supported connected channel/);
  assert.match(social, /createCreativeWithRecovery/);
  assert.match(social, /marketingApi\.creative\.brief/);
  assert.match(social, /marketingApi\.creative\.generate/);
  assert.match(social, /marketingApi\.creative\.regenerate/);
  assert.match(social, /regenerateVisual\.mutate\(\{ asset \}\)/);
  assert.match(social, /regenerateVisual\.mutate\(\{ asset, mode: "alternate_metaphor" \}\)/);
  assert.match(social, /creativeOperationLock\.current/);
  assert.match(social, /if \(postWorkspaceBusy \|\| creativeOperationLock\.current\) return/);
  assert.match(social, /creativeOperationLock\.current = false/);
  assert.match(social, /setCreativeActionError\(\s*humanizeApiError/);
  assert.match(social, /void invalidate\(\)/);
  assert.doesNotMatch(social, /AIAction policy/);
  assert.match(helpers, /finally \{/);
  assert.match(helpers, /refreshAfterCreativeOperation/);
  assert.match(helpers, /creativeFormatForContent/);
  assert.match(helpers, /SHARED CAMPAIGN DIRECTION/);
  assert.match(helpers, /trusted Business Brain context/);

  for (const control of [
    "button-regenerate-content",
    "button-approve-content",
    "button-schedule-content",
    "Save new version",
    "Version history",
  ]) {
    assert.match(`${studio}\n${page}`, new RegExp(control));
  }
});

test("AI CMO content details use a governed right-side post workspace", async () => {
  const [social, panel, productUi, styles] = await Promise.all([
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-creative-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/product-ui.tsx", import.meta.url), "utf8"),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  const drawerStart = social.indexOf("<WorkspaceDrawer\n        open={Boolean(selected)}");
  const drawerEnd = social.indexOf("{schedule &&", drawerStart);
  assert.ok(drawerStart >= 0 && drawerEnd > drawerStart);
  const drawer = social.slice(drawerStart, drawerEnd);
  const footerStart = social.indexOf("const postWorkspaceFooter");
  const footerEnd = social.indexOf("\n\n  return (", footerStart);
  assert.ok(footerStart >= 0 && footerEnd > footerStart);
  const footer = social.slice(footerStart, footerEnd);
  const busyStart = social.indexOf("const postWorkspaceBusy");
  const busyEnd = social.indexOf("const startCreativeOperation", busyStart);
  assert.ok(busyStart >= 0 && busyEnd > busyStart);
  const busyDefinition = social.slice(busyStart, busyEnd);

  for (const pendingState of [
    "creativeOperationPending",
    "edit.isPending",
    "createBrief.isPending",
    "regenerate.isPending",
    "move.isPending",
    "preparePublish.isPending",
  ]) {
    assert.equal(busyDefinition.includes(pendingState), true, pendingState);
  }

  assert.match(drawer, /className="cmo-post-workspace-drawer"/);
  assert.match(drawer, /testId="cmo-post-workspace-drawer"/);
  assert.match(drawer, /closeDisabled=\{postWorkspaceBusy\}/);
  assert.doesNotMatch(drawer, /<Modal/);
  for (const value of [
    "selected?.title",
    "selected.channel",
    "selected.status",
    "selected.version",
    "selected.title",
    "selected.body",
    "selected.cta",
  ]) {
    assert.equal(drawer.includes(value), true, value);
  }

  const orderedSections = ["Post preview", "Visual creative", "Version history", "Governance"];
  let previousIndex = -1;
  for (const section of orderedSections) {
    const index = drawer.indexOf(section);
    assert.ok(index > previousIndex, section);
    previousIndex = index;
  }

  assert.match(drawer, /data-testid="cmo-post-preview"/);
  assert.match(drawer, /<CmoCreativePanel/);
  assert.match(drawer, /onCreate=\{\(mediaType\) => startCreativeOperation/);
  assert.match(drawer, /if \(mediaType === "video"\) createVideo\.mutate\(selected\)/);
  assert.match(drawer, /actionError=\{creativeActionError\}/);
  assert.match(drawer, /onRetry=\{\(asset\) => startCreativeOperation/);
  assert.match(drawer, /onRegenerate=\{\(asset\) => startCreativeOperation/);
  assert.match(drawer, /isPending=\{postWorkspaceBusy\}/);
  assert.match(drawer, /phase=\{creativePhase\}/);
  assert.match(panel, /data-testid=\{`button-create-\$\{mediaType\}`\}/);
  assert.match(panel, /testId="creative-operation-error"/);
  assert.match(panel, /data-testid="button-retry-creative-operation"/);
  assert.match(drawer, /postWorkspaceMode === "edit"/);
  assert.match(drawer, /postWorkspaceMode === "creative_brief"/);
  assert.match(drawer, /postWorkspaceMode === "edit"\s*\? "Edit post"/);
  assert.match(drawer, /postWorkspaceMode === "creative_brief"\s*\? "Advanced creative brief"/);
  assert.match(drawer, /data-testid="cmo-edit-post-workspace"/);
  assert.match(drawer, /data-testid="cmo-creative-brief-workspace"/);
  assert.match(drawer, /id="cmo-edit-post-form"/);
  assert.match(drawer, /id="cmo-creative-brief-form"/);
  assert.match(drawer, /onSubmit=\{submitContentVersion\}/);
  assert.match(drawer, /onSubmit=\{submitCreativeBrief\}/);
  assert.doesNotMatch(social, /\{editing && selected|\{briefing && selected/);
  assert.doesNotMatch(social, /edit\.mutate\(event\)|createBrief\.mutate\(event\)/);

  const editMutation = social.slice(
    social.indexOf("const edit = useMutation"),
    social.indexOf("const createSchedule", social.indexOf("const edit = useMutation")),
  );
  assert.match(editMutation, /mutationFn: \(values: ContentVersionFormValues\)/);
  assert.match(editMutation, /marketingApi\.content\.edit\(activeBusinessId, values\.contentId/);
  assert.match(editMutation, /title: values\.title/);
  assert.match(editMutation, /body: values\.body/);
  assert.match(editMutation, /cta: values\.cta/);
  assert.match(editMutation, /setPostWorkspaceMode\("overview"\)/);
  assert.match(editMutation, /earlier versions remain available/);
  assert.doesNotMatch(editMutation, /FormEvent|FormData|currentTarget|preventDefault/);

  const briefMutation = social.slice(
    social.indexOf("const createBrief = useMutation"),
    social.indexOf("const createCreative = useMutation", social.indexOf("const createBrief = useMutation")),
  );
  assert.match(briefMutation, /mutationFn: \(values: CreativeBriefFormValues\)/);
  assert.match(briefMutation, /marketingApi\.creative\.brief\(activeBusinessId/);
  assert.match(briefMutation, /content_id: values\.contentId/);
  assert.match(briefMutation, /setPostWorkspaceMode\("overview"\)/);
  assert.match(briefMutation, /humanizeApiError/);
  assert.match(briefMutation, /onSettled: \(\) => refreshCreatives\(\)/);
  assert.doesNotMatch(briefMutation, /FormEvent|FormData|currentTarget|preventDefault/);

  const editSubmit = social.slice(
    social.indexOf("const submitContentVersion"),
    social.indexOf("const submitCreativeBrief", social.indexOf("const submitContentVersion")),
  );
  assert.match(editSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(editSubmit.indexOf("new FormData(event.currentTarget)") < editSubmit.indexOf("edit.mutate({"));
  assert.match(editSubmit, /event\.preventDefault\(\)/);
  assert.match(editSubmit, /contentId: selected\.id/);

  const scheduleBlock = social.slice(
    social.indexOf("const createSchedule = useMutation", social.indexOf("export function SocialManagementPage")),
    social.indexOf("const reschedule = useMutation", social.indexOf("export function SocialManagementPage")),
  );
  const scheduleMutation = scheduleBlock.slice(0, scheduleBlock.indexOf("const submitSchedule"));
  const scheduleSubmit = scheduleBlock.slice(scheduleBlock.indexOf("const submitSchedule"));
  assert.match(scheduleMutation, /mutationFn: \(values: ContentScheduleFormValues\)/);
  assert.match(scheduleMutation, /values\.contentId/);
  assert.match(scheduleMutation, /values\.scheduledFor/);
  assert.doesNotMatch(scheduleMutation, /FormEvent|FormData|currentTarget|preventDefault/);
  assert.match(scheduleSubmit, /event\.preventDefault\(\)/);
  assert.match(scheduleSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(scheduleSubmit.indexOf("new FormData(event.currentTarget)") < scheduleSubmit.indexOf("createSchedule.mutate({"));
  assert.match(scheduleSubmit, /const scheduledValue = String\(form\.get\("scheduled_for"\)/);
  assert.match(scheduleSubmit, /scheduledFor: scheduledDate\.toISOString\(\)/);
  assert.match(scheduleSubmit, /contentId: schedule\.id/);
  assert.match(social, /<form onSubmit=\{submitSchedule\}>/);
  assert.doesNotMatch(social, /createSchedule\.mutate\(event\)/);

  const briefSubmit = social.slice(
    social.indexOf("const submitCreativeBrief"),
    social.indexOf("const SelectedPlatformIcon", social.indexOf("const submitCreativeBrief")),
  );
  assert.match(briefSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(briefSubmit.indexOf("new FormData(event.currentTarget)") < briefSubmit.indexOf("createBrief.mutate({"));
  assert.match(briefSubmit, /event\.preventDefault\(\)/);
  assert.match(briefSubmit, /contentId: selected\.id/);

  assert.match(footer, /form="cmo-edit-post-form"/);
  assert.match(footer, /Save new version/);
  assert.match(footer, /form="cmo-creative-brief-form"/);
  assert.match(footer, /Prepare \$\{creativeBriefMedia\} strategy/);
  assert.match(footer, /Back to post/);
  assert.equal((footer.match(/disabled=\{postWorkspaceBusy\}/g) || []).length, 10);
  assert.match(drawer, /setPostWorkspaceMode\("creative_brief"\);[\s\S]*disabled=\{postWorkspaceBusy\}[\s\S]*Advanced brief/);
  assert.match(footer, /disabled=\{edit\.isPending\}[\s\S]*form="cmo-edit-post-form"[\s\S]*disabled=\{edit\.isPending\}/);
  assert.match(footer, /disabled=\{createBrief\.isPending \|\| createVideoBrief\.isPending\}[\s\S]*form="cmo-creative-brief-form"[\s\S]*disabled=\{createBrief\.isPending \|\| createVideoBrief\.isPending\}/);
  assert.match(social, /if \(postWorkspaceBusy \|\| creativeOperationLock\.current\) return/);

  assert.match(drawer, /versions\.data\?\.map/);
  assert.match(drawer, /onClick=\{\(\) => setSelected\(version\)\}/);
  assert.match(drawer, /Version \{version\.version\}/);
  assert.match(drawer, /version\.ai_generated/);
  assert.match(drawer, /version\.created_at/);
  assert.match(drawer, /version\.status/);

  const publishGuard = footer.slice(
    footer.indexOf("{selectedProviderWriteReady &&"),
    footer.indexOf("</div>\n    </div>", footer.indexOf("{selectedProviderWriteReady &&")),
  );
  assert.match(publishGuard, /selectedProviderWriteReady/);
  assert.doesNotMatch(publishGuard, /\["facebook", "instagram"\]\.includes\(selected\.channel\)/);
  assert.match(social, /const selectedPublishCapability = publishingCapability\(/);
  assert.match(publishGuard, /\["approved", "scheduled", "ready_to_publish"\]\.includes\(selected\.status\)/);
  assert.match(publishGuard, /preparePublish\.mutate\(selected\)/);
  assert.doesNotMatch(footer.slice(0, footer.indexOf("{selectedProviderWriteReady &&")), /preparePublish\.mutate/);
  assert.match(footer, /selected\.status === "draft"/);
  assert.match(footer, /status: "review"/);
  assert.match(footer, /selected\.status === "review"/);
  assert.match(footer, /status: "approved"/);
  assert.match(footer, /selected\.status === "approved"/);
  assert.match(footer, /setSchedule\(selected\)/);

  assert.match(productUi, /className\?: string/);
  assert.match(productUi, /className=\{cx\("workspace-drawer-panel", className\)\}/);
  assert.match(styles, /\.cmo-post-workspace-drawer \{\s*width: clamp\(600px, 46vw, 760px\)/);
  assert.match(styles, /\.workspace-drawer-panel \{[\s\S]*grid-template-rows: auto minmax\(0, 1fr\) auto/);
  assert.match(styles, /\.workspace-drawer-body \{[\s\S]*overflow-y: auto/);
  assert.match(styles, /\.workspace-drawer-footer \{[\s\S]*position: sticky/);
  assert.match(styles, /\.cmo-creative-image \{[\s\S]*object-fit: contain/);
  assert.match(styles, /@media \(max-width: 680px\)[\s\S]*\.workspace-drawer-panel \{[\s\S]*width: 100vw/);
  assert.match(styles, /@media \(max-width: 680px\)[\s\S]*\.workspace-drawer-panel \.field input,[\s\S]*font-size: 16px/);
});

test("AI CMO content library keeps previews compact and actions capability-driven", async () => {
  const [social, contentUi, styles] = await Promise.all([
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-ui.tsx", import.meta.url), "utf8"),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  assert.match(social, /<ContentLibraryCard/);
  assert.match(social, /marketingApi\.creative\.list\(activeBusinessId, undefined, undefined, signal\)/);
  assert.match(contentUi, /safeCreativeMediaUrl\(creative\?\.storage_reference\)/);
  assert.match(contentUi, /creativeMediaPresentation\(creative\)/);
  assert.match(contentUi, /data-state=\{previewMissing \? "preview_unavailable" : creative\.generation_status\}/);
  assert.match(contentUi, /onError=\{\(\) => setFailedPreviewKey\(previewKey\)\}/);
  assert.match(contentUi, /role=\{presentation\.active \? "status" : undefined\}/);
  assert.doesNotMatch(contentUi, /readableContentValue\(creative\.generation_status\)/);
  assert.match(contentUi, /className="cmo-content-card-open"/);
  assert.match(contentUi, /aria-label=\{`Open \$\{content\.title\}`\}/);
  assert.match(contentUi, /content\.status === "approved" && capability\.canPrepare/);
  assert.match(contentUi, /content\.status === "approved"[\s\S]*onSchedule/);
  assert.match(contentUi, /<details className="cmo-ai-disclosure">/);
  assert.match(styles, /\.cmo-content-card \.social-copy \{[\s\S]*-webkit-line-clamp: 3/);
  assert.match(styles, /\.cmo-content-card-title \{[\s\S]*-webkit-line-clamp: 2/);
  assert.match(styles, /\.cmo-content-card-media-state \{[\s\S]*text-align: center/);
  assert.match(styles, /@media \(max-width: 470px\)[\s\S]*\.cmo-content-card-main\.has-media \{[\s\S]*grid-template-columns: 1fr/);
});

test("content creation surfaces keep capability, loading, and calendar states truthful", async () => {
  const [page, social, styles] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  const plan = social.slice(
    social.indexOf('<Card className="intelligence-hero cmo-content-plan">'),
    social.indexOf('<div className="social-channel-strip">'),
  );
  assert.match(plan, /contentPlan\.isLoading/);
  assert.match(plan, /contentPlan\.isError/);
  assert.match(plan, /humanizeApiError\(\s*contentPlan\.error/);
  assert.match(plan, /void contentPlan\.refetch\(\)/);
  assert.match(plan, /"Retry plan"/);
  assert.doesNotMatch(plan, /"Scheduling…"/);

  const capabilityStrip = social.slice(
    social.indexOf('<div className="social-channel-strip">'),
    social.indexOf('<div className="cmo-content-library-heading">'),
  );
  for (const label of [
    "Publishing ready",
    "Checking readiness",
    "Readiness unknown",
    "Connection required",
    "Planning only",
  ]) {
    assert.equal(capabilityStrip.includes(label), true, label);
  }
  assert.match(capabilityStrip, /capability\.state === "checking"/);
  assert.match(capabilityStrip, /capability\.state === "unverified"/);
  assert.doesNotMatch(capabilityStrip, /setup_status\.replaceAll/);
  assert.match(social, /Publishing readiness could not be verified/);
  assert.match(social, /void refreshPublishingReadiness\(\)/);

  assert.match(social, /libraryAssets\.isError/);
  assert.match(social, /Creative previews could not load/);
  assert.match(social, /void libraryAssets\.refetch\(\)/);
  assert.match(social, /Loading creative previews…/);
  assert.match(social, /className="cmo-content-loading-card"/);
  assert.match(social, /Loading content library…/);

  const calendar = social.slice(
    social.indexOf('<Card className="cmo-calendar-management">'),
    social.indexOf("<CmoContentGeneratorDrawer", social.indexOf('<Card className="cmo-calendar-management">')),
  );
  assert.match(calendar, /role="tablist" aria-label="Calendar range"/);
  assert.match(calendar, /aria-selected=\{calendarDays === days\}/);
  assert.match(calendar, /className="cmo-calendar-reschedule"/);
  assert.match(calendar, /disabled=\{reschedule\.isPending \|\| unschedule\.isPending\}/);
  assert.match(calendar, /className="cmo-calendar-management-loading" role="status" aria-live="polite"/);

  assert.match(page, /queryKey: \["integrations", activeBusinessId\]/);
  assert.match(page, /onClick=\{\(\) => void refreshWorkspace\(\)\}/);
  assert.match(page, /analytics\.isLoading \? <Card>/);
  assert.match(page, /Loading recorded performance…/);

  assert.match(styles, /\.cmo-content-query-state \{[\s\S]*min-height: 52px/);
  assert.match(styles, /\.cmo-calendar-reschedule \{[\s\S]*width: 190px/);
  assert.match(styles, /@media \(max-width: 1100px\) \{[\s\S]*\.cmo-studio-calendar-grid \{[\s\S]*grid-template-columns: 1fr/);
  assert.match(styles, /@media \(max-width: 680px\) \{[\s\S]*\.cmo-calendar-manage-row \{[\s\S]*grid-template-columns: 24px minmax\(0, 1fr\)/);
});

test("AI CMO creation flows use the accessible responsive workspace drawer", async () => {
  const [page, studio, social, contentDrawer, productUi, sheet, styles] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-studio.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-generator-drawer.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/product-ui.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/sheet.tsx", import.meta.url), "utf8"),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /Generate strategy/);
  assert.match(page, /New content/);
  assert.match(page, /button-generate-strategy/);
  assert.match(page, /button-generate-content/);
  assert.match(page, /actionClassName="cmo-overview-actions"/);

  const headerActions = page.slice(
    page.indexOf('actionClassName="cmo-overview-actions"'),
    page.indexOf("<CmoDepartmentNav"),
  );
  assert.match(headerActions, /cmo-overview-action cmo-overview-action-secondary/);
  assert.match(headerActions, /cmo-overview-action cmo-overview-action-primary/);
  assert.match(headerActions, /<Target \/>\s*Generate strategy/);
  assert.match(headerActions, /<Plus \/>\s*New content/);
  assert.doesNotMatch(headerActions, /icon-(?:badge|chip)|cmo-action-context|<span/);

  const studioEmptyState = studio.slice(
    studio.indexOf("if (!content)"),
    studio.indexOf("const groundingSummary"),
  );
  assert.match(studioEmptyState, /className="cmo-card-cta" onClick=\{onGenerate\}/);
  assert.match(studioEmptyState, /<WandSparkles \/>\s*New content/);
  assert.doesNotMatch(studioEmptyState, /<Wand2 \/>|<Sparkles \/>/);
  assert.match(page, /className="cmo-card-cta" onClick=\{openStrategyDrawer\}/);
  assert.match(page, /<LinkButton href="\/campaigns\?new=1">Prepare campaign<\/LinkButton>/);
  assert.match(page, /className="btn btn-primary cmo-card-cta"/);

  assert.match(styles, /\.cmo-overview-actions \{[\s\S]*gap: 12px/);
  assert.match(styles, /\.cmo-overview-action \{[\s\S]*height: 44px[\s\S]*padding: 0 17px[\s\S]*gap: 8px[\s\S]*font-size: 13px[\s\S]*font-weight: 600/);
  assert.match(styles, /\.cmo-overview-action svg \{[\s\S]*width: 18px[\s\S]*height: 18px/);
  assert.match(styles, /\.cmo-card-cta \{[\s\S]*height: 40px[\s\S]*padding: 0 15px[\s\S]*gap: 7px[\s\S]*font-size: 12px[\s\S]*font-weight: 600/);
  assert.match(styles, /\.cmo-card-cta svg \{[\s\S]*width: 16px[\s\S]*height: 16px[\s\S]*margin: 0[\s\S]*color: currentColor/);

  const strategyOpen = page.indexOf("open={showPlanGenerator}");
  const strategyStart = page.lastIndexOf("<WorkspaceDrawer", strategyOpen);
  const strategyEnd = page.indexOf("{editingPlan", strategyStart);
  const strategy = page.slice(strategyStart, strategyEnd);
  assert.match(strategy, /<WorkspaceDrawer/);
  assert.doesNotMatch(strategy, /<Modal/);
  assert.match(strategy, /open=\{showPlanGenerator\}/);
  assert.match(strategy, /title="Create marketing strategy"/);
  assert.match(strategy, /testId="cmo-strategy-workspace-drawer"/);
  assert.match(strategy, /closeDisabled=\{generatePlan\.isPending\}/);

  for (const field of [
    "title",
    "budget",
    "goal",
    "audience",
    "period_start",
    "period_end",
    "channels",
  ]) {
    assert.match(strategy, new RegExp(`name="${field}"`), field);
  }
  for (const channel of [
    "instagram",
    "facebook",
    "linkedin",
    "tiktok",
    "email",
    "whatsapp",
    "website",
    "meta",
    "google_ads",
  ]) {
    assert.match(page, new RegExp(`"${channel}"`), channel);
  }
  assert.match(page, /marketingApi\.plans\.generate\(activeBusinessId/);
  assert.match(page, /goal: String\(form\.get\("goal"\)\)/);
  assert.match(page, /target_audience: String\(form\.get\("audience"\)\)/);

  const pageScheduleBlock = page.slice(
    page.indexOf("const createSchedule = useMutation"),
    page.indexOf("const generatePlan = useMutation"),
  );
  const pageScheduleMutation = pageScheduleBlock.slice(
    0,
    pageScheduleBlock.indexOf("const submitSchedule"),
  );
  const pageScheduleSubmit = pageScheduleBlock.slice(
    pageScheduleBlock.indexOf("const submitSchedule"),
  );
  assert.match(pageScheduleMutation, /mutationFn: \(values: ScheduleContentValues\)/);
  assert.match(pageScheduleMutation, /values\.contentId/);
  assert.match(pageScheduleMutation, /values\.scheduledFor/);
  assert.doesNotMatch(pageScheduleMutation, /FormEvent|FormData|currentTarget|preventDefault/);
  assert.match(pageScheduleSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(pageScheduleSubmit.indexOf("new FormData(event.currentTarget)") < pageScheduleSubmit.indexOf("createSchedule.mutate({"));
  assert.match(pageScheduleSubmit, /scheduledFor: scheduledDate\.toISOString\(\)/);
  assert.match(page, /<form onSubmit=\{submitSchedule\}>/);

  const generatePlanBlock = page.slice(
    page.indexOf("const generatePlan = useMutation"),
    page.indexOf("const updatePlan = useMutation"),
  );
  const generatePlanMutation = generatePlanBlock.slice(
    0,
    generatePlanBlock.indexOf("const submitGeneratePlan"),
  );
  const generatePlanSubmit = generatePlanBlock.slice(
    generatePlanBlock.indexOf("const submitGeneratePlan"),
  );
  assert.match(generatePlanMutation, /mutationFn: \(values: GeneratePlanValues\)/);
  assert.doesNotMatch(generatePlanMutation, /FormEvent|FormData|currentTarget|preventDefault/);
  assert.match(generatePlanSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(generatePlanSubmit.indexOf("new FormData(event.currentTarget)") < generatePlanSubmit.indexOf("generatePlan.mutate({"));
  assert.match(generatePlanSubmit, /form\.getAll\("channels"\)\.map\(String\)/);
  assert.match(generatePlanSubmit, /if \(!selected\.length\)/);
  assert.match(generatePlanSubmit, /channels: selected/);
  assert.match(page, /onSubmit=\{submitGeneratePlan\}/);

  const updatePlanBlock = page.slice(
    page.indexOf("const updatePlan = useMutation"),
    page.indexOf("const movePlan = useMutation"),
  );
  const updatePlanMutation = updatePlanBlock.slice(
    0,
    updatePlanBlock.indexOf("const submitUpdatePlan"),
  );
  const updatePlanSubmit = updatePlanBlock.slice(
    updatePlanBlock.indexOf("const submitUpdatePlan"),
  );
  assert.match(updatePlanMutation, /mutationFn: \(values: UpdatePlanValues\)/);
  assert.match(updatePlanMutation, /values\.planId/);
  assert.doesNotMatch(updatePlanMutation, /FormEvent|FormData|currentTarget|preventDefault/);
  assert.match(updatePlanSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(updatePlanSubmit.indexOf("new FormData(event.currentTarget)") < updatePlanSubmit.indexOf("updatePlan.mutate({"));
  assert.match(updatePlanSubmit, /form\.get\("measurement_goals"\)/);
  assert.match(updatePlanSubmit, /\.split\("\\n"\)[\s\S]*\.map\(\(item\) => item\.trim\(\)\)[\s\S]*\.filter\(Boolean\)/);
  assert.match(page, /<form onSubmit=\{submitUpdatePlan\}>/);
  assert.doesNotMatch(page, /(?:createSchedule|generatePlan|updatePlan)\.mutate\(event\)/);

  assert.match(contentDrawer, /<WorkspaceDrawer/);
  assert.doesNotMatch(contentDrawer, /<Modal/);
  assert.match(contentDrawer, /open=\{open\}/);
  assert.match(page, /<CmoContentGeneratorDrawer[\s\S]*open=\{showContentGenerator\}/);
  assert.match(contentDrawer, /testId="cmo-content-workspace-drawer"/);
  assert.match(page, /generateCampaignChannelDrafts/);
  const generationHandler = page.slice(
    page.indexOf("const generateContent = useMutation"),
    page.indexOf("const editContent = useMutation"),
  );
  const mutationHandler = generationHandler;
  const submitHandler = contentDrawer.slice(contentDrawer.indexOf("const submit"));
  assert.match(mutationHandler, /mutationFn: \(input: CampaignGenerationInput\)/);
  assert.match(mutationHandler, /generateCampaignChannelDrafts\(\s*input,/);
  assert.doesNotMatch(mutationHandler, /FormEvent|FormData|currentTarget|preventDefault/);
  assert.match(submitHandler, /event\.preventDefault\(\)/);
  assert.match(submitHandler, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(submitHandler.indexOf("new FormData(event.currentTarget)") >= 0);
  assert.match(submitHandler, /form\.getAll\("platforms"\)/);
  assert.match(submitHandler, /form\.get\("additional_channel"\)/);
  assert.match(submitHandler, /channels: \[\.\.\.new Set\(selected\)\]/);
  for (const field of [
    "prompt",
    "audience",
    "content_type",
    "campaign_id",
    "title",
    "language",
  ]) {
    assert.match(submitHandler, new RegExp(`form\\.get\\("${field}"\\)`), field);
  }
  assert.match(contentDrawer, /value=\{channel\}\s+defaultChecked=\{channel === "instagram"\}/);
  assert.match(contentDrawer, /onSubmit=\{submit\}/);
  assert.doesNotMatch(page, /generateContent\.mutate\(event\)/);
  assert.match(generationHandler, /channelGenerationNotice\(outcome\)/);
  assert.match(generationHandler, /if \(outcome\.successes\.length === 0\)/);
  assert.match(generationHandler, /setShowContentGenerator\(false\)/);
  assert.match(generationHandler, /const firstFailure = outcome\.failures\[0\]\?\.reason/);
  assert.match(
    generationHandler,
    /humanizeApiError\(\s*firstFailure,\s*"AI content generation could not be completed\. No channel drafts were created\.",\s*\)/,
  );
  assert.match(generationHandler, /onSettled: \(\) => refresh\(\)/);
  assert.doesNotMatch(page, /\{showPlanGenerator && \(\s*<WorkspaceDrawer/);
  assert.doesNotMatch(page, /\{showContentGenerator && \(\s*<WorkspaceDrawer/);

  const editMutation = page.slice(
    page.indexOf("const editContent = useMutation"),
    page.indexOf("const regenerate = useMutation"),
  );
  assert.match(editMutation, /mutationFn: \(values: \{/);
  assert.match(editMutation, /marketingApi\.content\.edit\(activeBusinessId, values\.contentId/);
  assert.match(editMutation, /title: values\.title/);
  assert.match(editMutation, /body: values\.body/);
  assert.match(editMutation, /cta: values\.cta/);
  const editMutationSetup = editMutation.slice(0, editMutation.indexOf("const submitContentEdit"));
  assert.doesNotMatch(editMutationSetup, /mutationFn: async \(event|new FormData\(event\.currentTarget\)/);
  const editSubmit = editMutation.slice(editMutation.indexOf("const submitContentEdit"));
  assert.match(editSubmit, /event\.preventDefault\(\)/);
  assert.match(editSubmit, /const form = new FormData\(event\.currentTarget\)/);
  assert.ok(editSubmit.indexOf("new FormData(event.currentTarget)") < editSubmit.indexOf("editContent.mutate({"));
  assert.match(editSubmit, /cta: cta \|\| null/);
  assert.match(editMutation, /onSuccess: async \(item\)/);
  assert.match(editMutation, /setEditingContent\(null\)/);
  assert.match(editMutation, /await refresh\(\)/);

  assert.match(productUi, /open: boolean/);
  assert.match(productUi, /<Sheet open=\{open\} modal/);
  assert.match(productUi, /role="dialog"/);
  assert.match(productUi, /aria-modal="true"/);
  assert.match(productUi, /<SheetTitle/);
  assert.match(productUi, /onEscapeKeyDown/);
  assert.match(productUi, /onPointerDownOutside/);
  assert.match(productUi, /closeClassName="workspace-drawer-close"/);
  assert.match(productUi, /closeTestId="button-close-workspace-drawer"/);
  assert.match(sheet, /aria-label=\{closeLabel\}/);
  assert.match(sheet, /closeTestId\?: string/);
  assert.match(sheet, /data-testid=\{closeTestId\}/);
  assert.doesNotMatch(sheet, /data-testid="button-close-workspace-drawer"/);

  const drawerStyles = styles.slice(
    styles.indexOf("/* Focused creation workspaces"),
    styles.indexOf(".conversation-layout", styles.indexOf("/* Focused creation workspaces")),
  );
  assert.match(styles, /width: clamp\(560px, 40vw, 680px\)/);
  assert.match(styles, /height: 100dvh/);
  assert.match(styles, /\.workspace-drawer-body[\s\S]*overflow-y: auto/);
  assert.match(styles, /\.workspace-drawer-footer[\s\S]*position: sticky/);
  assert.match(styles, /@media \(max-width: 680px\)[\s\S]*width: 100vw/);
  assert.match(styles, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(styles, /\.cmo-channel-option:has\(input:checked\)/);
  assert.match(styles, /\.workspace-drawer-panel\[data-state="closed"\][\s\S]*workspaceDrawerOut/);
  assert.match(styles, /\.workspace-drawer-backdrop\[data-state="closed"\][\s\S]*workspaceBackdropOut/);
  assert.match(drawerStyles, /\.workspace-drawer-description[\s\S]*font-size: 13px/);
  assert.match(drawerStyles, /\.cmo-form-section-heading h3[\s\S]*font-size: 14px/);
  assert.match(drawerStyles, /\.workspace-drawer-panel \.field label[\s\S]*font-size: 12px/);
  assert.match(drawerStyles, /\.workspace-drawer-panel \.field input,[\s\S]*font-size: 14px/);
  assert.match(drawerStyles, /\.cmo-channel-option[\s\S]*font-size: 12px/);
  assert.match(drawerStyles, /\.cmo-assurance-item p[\s\S]*font-size: 11px/);
  assert.doesNotMatch(drawerStyles, /font-size: 8px/);
  assert.match(
    drawerStyles,
    /@media \(max-width: 680px\)[\s\S]*\.workspace-drawer-panel \.field input,[\s\S]*font-size: 16px/,
  );

  assert.match(
    page,
    /const openStrategyDrawer = \(\) => \{\s*setError\(""\);\s*setShowPlanGenerator\(true\);/,
  );
  assert.match(
    page,
    /const openContentDrawer = \(\) => \{\s*setError\(""\);\s*setShowContentGenerator\(true\);/,
  );
  assert.match(page, /onClick=\{openStrategyDrawer\}/);
  assert.match(page, /onClick=\{openContentDrawer\}/);

  assert.match(social, /button-new-content/);
  assert.match(social, /cmo-compact-action/);
  assert.match(social, /New content/);
  assert.match(social, /setShowContentGenerator\(true\)/);
  assert.match(social, /<CmoContentGeneratorDrawer/);
  assert.match(page, /const canAuthorizeOffer = \["owner", "admin"\]\.includes\([\s\S]*activeBusiness\?\.membershipRole/);
  assert.match(social, /const canAuthorizeOffer = \["owner", "admin"\]\.includes\([\s\S]*activeBusiness\?\.membershipRole/);
  assert.match(page, /<CmoContentGeneratorDrawer[\s\S]*canAuthorizeOffer=\{canAuthorizeOffer\}/);
  assert.match(social, /<CmoContentGeneratorDrawer[\s\S]*canAuthorizeOffer=\{canAuthorizeOffer\}/);
  assert.match(contentDrawer, /canAuthorizeOffer: boolean/);
  assert.match(contentDrawer, /disabled=\{!canAuthorizeOffer\}/);
  assert.match(contentDrawer, /\{canAuthorizeOffer && \(/);
  assert.match(contentDrawer, /Owner or administrator access is required to authorize a promotional offer\./);
  assert.match(contentDrawer, /offer_authorized/);
  assert.match(contentDrawer, /I confirm this offer is authorized for this business\./);
  assert.doesNotMatch(contentDrawer, /business-owner|authenticated owner|owner campaign input/i);
  assert.match(contentDrawer, /if \(offer && !offerAuthorized\)/);
  assert.match(contentDrawer, /Confirm that this offer is authorized for this business\./);
  assert.ok(submitHandler.indexOf("if (offer && !offerAuthorized)") < submitHandler.indexOf("onSubmit({"));

  const socialGeneration = social.slice(
    social.indexOf("const generateContent = useMutation", social.indexOf("export function SocialManagementPage")),
    social.indexOf("const createSchedule = useMutation", social.indexOf("export function SocialManagementPage")),
  );
  assert.match(socialGeneration, /const first = outcome\.successes\[0\]/);
  assert.match(socialGeneration, /if \(!first\)[\s\S]*return;/);
  assert.match(socialGeneration, /setShowContentGenerator\(false\)/);
  assert.match(socialGeneration, /setSelected\(first\)/);
  assert.ok(socialGeneration.indexOf("if (!first)") < socialGeneration.indexOf("setSelected(first)"));
  assert.ok(socialGeneration.indexOf("setShowContentGenerator(false)") < socialGeneration.indexOf("setSelected(first)"));
});

test("completed marketing screens contain no workspace or localStorage dependency", async () => {
  const files = [
    "../features/marketing/cmo-page.tsx", "../features/marketing/marketing-pages.tsx",
    "../features/intelligence/intelligence-pages.tsx", "../features/analytics/analytics-page.tsx",
  ];
  for (const file of files) {
    const source = await readFile(new URL(file, import.meta.url), "utf8");
    assert.equal(source.includes("useWorkspaceData"), false, file);
    assert.equal(source.includes("workspaceRepository"), false, file);
    assert.equal(source.includes("localStorage"), false, file);
    const queryKeys = [...source.matchAll(/queryKey:\s*\[([^\]]+)/g)].map((match) => match[1]);
    assert.ok(queryKeys.length > 0, file);
    assert.ok(queryKeys.every((key) => key.includes("activeBusinessId")), file);
  }
});

test("marketing routes are production-ungated and avoid false launch language", async () => {
  const [app, marketing, helpers] = await Promise.all([
    readFile(new URL("../App.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("./cmo-ux.ts", import.meta.url), "utf8"),
  ]);
  for (const component of ["CmoPage", "CampaignsPage", "SocialManagementPage", "CompetitorIntelligencePage", "TrendIntelligencePage"]) {
    assert.match(app, new RegExp(`component=\\{${component}\\}`));
    assert.doesNotMatch(app, new RegExp(`workspaceModule\\(${component}\\)`));
  }
  assert.equal(marketing.includes("Start prototype campaign"), false);
  assert.equal(marketing.includes("launched successfully"), false);
  assert.match(marketing, /integrationsApi\.registry/);
  assert.match(marketing, /integrationsApi\.connections/);
  assert.match(marketing, /publishingCapability/);
  assert.match(helpers, /external_writes_enabled/);
  assert.match(helpers, /Publishing isn’t available for this channel yet/);
  assert.doesNotMatch(marketing, /Authenticated external writes are provider-disabled/);
});
