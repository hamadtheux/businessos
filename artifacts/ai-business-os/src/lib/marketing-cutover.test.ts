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

test("creative generation and regeneration use tenant-scoped POST endpoints", async () => {
  const requests: Array<{ path: string; method: string }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({
      path: new URL(String(input)).pathname,
      method: String(init?.method || "GET"),
    });
    return json({});
  });

  await api.creative.generate(businessA, "creative-one");
  await api.creative.regenerate(businessA, "creative-one");

  assert.deepEqual(requests, [
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/creative-one/generate`,
      method: "POST",
    },
    {
      path: `/api/v1/businesses/${businessA}/marketing/creative-assets/creative-one/regenerate`,
      method: "POST",
    },
  ]);
});

test("CMO creative studio exposes honest visual lifecycle states and immutable regeneration", async () => {
  const [panel, studio, page, social, helpers] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-creative-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-content-studio.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("./cmo-ux.ts", import.meta.url), "utf8"),
  ]);

  for (const state of [
    "creative-empty-state",
    "creative-provider-required",
    "creative-failed-state",
    "creative-ready-preview",
  ]) {
    assert.match(panel, new RegExp(state));
  }
  assert.match(panel, /creative-loading-\$\{phase\}/);
  assert.match(panel, /Creating the visual strategy/);
  assert.match(panel, /Designing your branded creative/);
  assert.match(panel, /Regenerate as new creative/);
  assert.match(panel, /Previous artwork remains in history|preserved in history/);
  assert.match(panel, /safeCreativeMediaUrl/);
  assert.match(panel, /onError=\{\(\) => setFailedPreviewId/);
  assert.match(panel, /creative-preview-unavailable/);
  assert.match(panel, /button-retry-preview/);
  assert.match(panel, /Creative is ready, but the preview could not be loaded/);
  assert.match(panel, /Creative history ·/);
  assert.match(panel, /Previous creative artwork/);
  assert.match(panel, /remains read-only here/);
  assert.match(panel, /button-reload-creatives/);
  assert.match(panel, /flexWrap: "wrap"/);
  assert.match(panel, /width: "100%"/);
  assert.doesNotMatch(panel, /placeholder artwork|data:image/);
  for (const forbidden of ["server-side", "OpenAI", "API key", "raw visual"]) {
    assert.equal(panel.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
  }
  assert.match(studio, /button-approve-content/);
  assert.match(studio, /button-schedule-content/);
  assert.match(studio, /onHistory/);
  assert.match(studio, /creatives=\{creatives\}/);
  assert.match(page, /Saving creates a new immutable version/);
  assert.doesNotMatch(page, /editingContent\.version \+ 1/);
  assert.match(page, /primaryPlatforms/);
  assert.match(page, /generateCampaignChannelDrafts/);
  assert.match(page, /maxLength=\{OWNER_GOAL_MAX\}/);
  assert.match(page, /maxLength=\{AUDIENCE_GUIDANCE_MAX\}/);
  assert.doesNotMatch(page, /Promise\.all\(selectedChannels\.map/);
  assert.match(page, /createCreativeWithRecovery/);
  assert.match(page, /runCreativeOperationWithRecovery/);
  assert.match(page, /creativePhaseForDisplay/);
  assert.match(page, /creatives=\{creativeAssets\.data\}/);
  assert.match(page, /humanizeApiError/);
  assert.match(social, /assets\.data\?\.slice\(1\)/);
  assert.match(social, /creativePhaseForDisplay/);
  assert.match(social, /button-regenerate-creative|onRegenerate/);
  assert.match(social, /Publishing always requires your approval and an available connected channel/);
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

test("AI CMO creation flows use the accessible responsive workspace drawer", async () => {
  const [page, social, productUi, sheet, styles] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/product-ui.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/ui/sheet.tsx", import.meta.url), "utf8"),
    readFile(new URL("../index.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /Generate strategy/);
  assert.match(page, /Generate content/);
  assert.match(page, /button-generate-strategy/);
  assert.match(page, /button-generate-content/);
  assert.match(page, /actionClassName="cmo-header-actions"/);

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

  const contentOpen = page.indexOf("open={showContentGenerator}");
  const contentStart = page.lastIndexOf("<WorkspaceDrawer", contentOpen);
  const contentEnd = page.indexOf("{editingContent", contentStart);
  const contentDrawer = page.slice(contentStart, contentEnd);
  assert.match(contentDrawer, /<WorkspaceDrawer/);
  assert.doesNotMatch(contentDrawer, /<Modal/);
  assert.match(contentDrawer, /open=\{showContentGenerator\}/);
  assert.match(contentDrawer, /testId="cmo-content-workspace-drawer"/);
  assert.match(page, /generateCampaignChannelDrafts/);
  assert.doesNotMatch(page, /\{showPlanGenerator && \(\s*<WorkspaceDrawer/);
  assert.doesNotMatch(page, /\{showContentGenerator && \(\s*<WorkspaceDrawer/);

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

  assert.match(social, /button-advanced-create-draft/);
  assert.match(social, /cmo-compact-action/);
  assert.match(social, /Create draft/);
  assert.match(social, /Advanced/);
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
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  const marketing = await readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8");
  for (const component of ["CmoPage", "CampaignsPage", "SocialManagementPage", "CompetitorIntelligencePage", "TrendIntelligencePage"]) {
    assert.match(app, new RegExp(`component=\\{${component}\\}`));
    assert.doesNotMatch(app, new RegExp(`workspaceModule\\(${component}\\)`));
  }
  assert.equal(marketing.includes("Start prototype campaign"), false);
  assert.equal(marketing.includes("launched successfully"), false);
  assert.match(marketing, /integrationsApi\.registry/);
  assert.match(marketing, /integrationsApi\.connections/);
  assert.match(marketing, /external_writes_enabled/);
  assert.match(marketing, /No connector is registered; internal planning only/);
  assert.doesNotMatch(marketing, /Authenticated external writes are provider-disabled/);
});
