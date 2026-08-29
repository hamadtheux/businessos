import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createChatbotApi, type ChatbotConfigUpdate } from "../services/chatbot.ts";

const businessA = "c1000000-0000-4000-8000-000000000001";
const businessB = "c2000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "c3000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const login: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createChatbotApi(client);
}

const update: ChatbotConfigUpdate = {
  enabled: true,
  display_name: "Acme AI",
  welcome_message: "How can we help?",
  placeholder_text: "Ask a question",
  tone: "friendly",
  theme: "light",
  position: "bottom_right",
  launcher_style: "bubble",
  allowed_capabilities: ["answer_business_questions", "capture_lead"],
  allowed_domains: ["example.com"],
  privacy_policy_url: "https://example.com/privacy",
  consent_text: "I agree to be contacted.",
  require_lead_consent: true,
  default_locale: "en",
  border_radius: 18,
};

test("management API is tenant-keyed and sends no client-owned widget identity", async () => {
  const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(login);
    requests.push({ path: new URL(String(input)).pathname, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return json({});
  });
  await api.get(businessA);
  await api.update(businessB, update);
  assert.deepEqual(requests.map(({ path, method }) => ({ path, method })), [
    { path: `/api/v1/businesses/${businessA}/chatbot`, method: "GET" },
    { path: `/api/v1/businesses/${businessB}/chatbot`, method: "PUT" },
  ]);
  const body = requests[1].body ?? {};
  assert.equal("business_id" in body, false);
  assert.equal("widget_public_id" in body, false);
  assert.equal("embed_snippet" in body, false);
  assert.deepEqual(body.allowed_domains, ["example.com"]);
});

test("chatbot screen is API-backed, tenant-keyed, and scheduling uses central policy", async () => {
  const source = await readFile(new URL("../features/chatbot/chatbot-page.tsx", import.meta.url), "utf8");
  for (const forbidden of ["localStorage", "workspaceRepository", "useWorkspaceData", "mock", "business_id:", "widget_public_id:"]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  assert.match(source, /queryKey: \["chatbot", activeBusinessId/);
  assert.match(source, /isBusinessFeatureEnabled\(activeBusiness, "scheduling"\)/);
  assert.match(source, /item\.scheduling \|\| schedulingEnabled/);
  assert.match(source, /navigator\.clipboard\.writeText/);
  assert.match(source, /Live style preview/);
  assert.match(source, /Real database aggregates/);
});

test("widget loader isolates host CSS and bootstraps from the actual host origin", async () => {
  const source = await readFile(new URL("../widget/loader.ts", import.meta.url), "utf8");
  assert.match(source, /attachShadow\(\{ mode: "closed" \}\)/);
  assert.match(source, /credentials: "omit"/);
  assert.match(source, /iframe\.sandbox\.add\("allow-scripts", "allow-forms", "allow-same-origin"\)/);
  assert.match(source, /postMessage\(\{ type: "aibos:widget-init"/);
  assert.match(source, /event\.origin !== frameOrigin/);
  assert.match(source, /button\.focus\(\)/);
  assert.match(source, /frame\.hidden = true/);
  for (const forbidden of ["businessId", "apiKey", "access_token", "refresh_token", "localStorage", "innerHTML = config"] ) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
});

test("widget renders all visitor and AI content as React text, never arbitrary HTML", async () => {
  const source = await readFile(new URL("../widget/widget-app.tsx", import.meta.url), "utf8");
  assert.equal(source.includes("dangerouslySetInnerHTML"), false);
  assert.equal(source.includes("innerHTML"), false);
  assert.match(source, /maxLength=\{2000\}/);
  assert.match(source, /aria-live="polite"/);
  assert.match(source, /Talk to a person/);
  assert.match(source, /exact email or phone/);
  assert.match(source, /Availability comes directly/);
  assert.match(source, /handoffRequested/);
  assert.match(source, /Waiting for human assistance/);
  assert.match(source, /WIDGET_API_BASE_URL/);
  assert.match(source, /trustedApiOrigin/);
  assert.match(source, /apiUrl\.origin !== trustedApiOrigin/);
  assert.equal(source.includes("apiUrl.origin !== window.location.origin"), false);
});

test("public widget API uses only opaque widget/session identity and dedicated routes", async () => {
  const source = await readFile(new URL("../widget/public-api.ts", import.meta.url), "utf8");
  assert.match(source, /\/api\/v1\/public\/widgets/);
  assert.match(source, /Authorization: `Bearer \$\{this\.options\.token\}`/);
  for (const forbidden of ["/businesses/", "/customers", "/analytics", "document.cookie", "credentials: \"include\""]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  for (const tail of ["/messages", "/lead", "/handoff", "/order-status", "/availability", "/appointments"]) {
    assert.equal(source.includes(tail), true, tail);
  }
});

test("production build declares separate app, widget, and stable loader entries", async () => {
  const vite = await readFile(new URL("../../vite.config.ts", import.meta.url), "utf8");
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("../components/app-shell.tsx", import.meta.url), "utf8");
  assert.match(vite, /widget: path\.resolve\(import\.meta\.dirname, 'widget\.html'\)/);
  assert.match(vite, /loader: path\.resolve\(import\.meta\.dirname, 'src\/widget\/loader\.ts'\)/);
  assert.match(vite, /'widget-loader\.js'/);
  assert.match(app, /feature="website_chatbot" component=\{ChatbotPage\}/);
  assert.match(app, /path="\/chatbot" component=\{ChatbotRoute\}/);
  assert.match(shell, /href: "\/chatbot", label: "Website Chatbot"/);
});
