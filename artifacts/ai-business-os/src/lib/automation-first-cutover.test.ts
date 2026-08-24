import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createChatbotApi } from "../services/chatbot.ts";
import { createMarketingApi } from "../services/marketing.ts";

const businessA = "aa000000-0000-4000-8000-000000000001";
const businessB = "bb000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "cc000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const login: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

async function clients(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return { marketing: createMarketingApi(client), chatbot: createChatbotApi(client) };
}

test("competitor discovery and candidate decisions stay tenant scoped", async () => {
  const requests: Array<{ path: string; method: string; body?: Record<string, unknown> }> = [];
  const { marketing } = await clients(async (input, init) => {
    if (String(input).endsWith("/login")) return json(login);
    requests.push({ path: new URL(String(input)).pathname, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return json({});
  });
  await marketing.competitorDiscovery.status(businessA);
  await marketing.competitorDiscovery.candidates(businessB, "suggested");
  await marketing.competitorDiscovery.setStatus(businessA, "candidate-id", "dismissed");
  assert.deepEqual(requests.map(({ path }) => path), [
    `/api/v1/businesses/${businessA}/marketing/competitor-discovery`,
    `/api/v1/businesses/${businessB}/marketing/competitor-candidates`,
    `/api/v1/businesses/${businessA}/marketing/competitor-candidates/candidate-id/status`,
  ]);
  assert.deepEqual(requests[2].body, { status: "dismissed" });
  assert.equal("business_id" in (requests[2].body ?? {}), false);
});

test("AI campaign generation needs only a goal while execution is a separate governed request", async () => {
  const requests: Array<{ path: string; body?: Record<string, unknown> }> = [];
  const { marketing } = await clients(async (input, init) => {
    if (String(input).endsWith("/login")) return json(login);
    requests.push({ path: new URL(String(input)).pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return json({});
  });
  await marketing.campaigns.generate(businessA, { goal: "Get more qualified leads", planned_budget: "500" });
  await marketing.campaigns.prepareAction(businessA, "campaign-id", "meta");
  assert.deepEqual(requests[0].body, { goal: "Get more qualified leads", planned_budget: "500" });
  assert.equal(requests[1].path, `/api/v1/businesses/${businessA}/marketing/campaigns/campaign-id/prepare-action`);
  assert.deepEqual(requests[1].body, { channel: "meta" });
});

test("automation-first screens preserve manual controls as advanced fallbacks", async () => {
  const intelligence = await readFile(new URL("../features/intelligence/intelligence-pages.tsx", import.meta.url), "utf8");
  const marketing = await readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8");
  const chatbot = await readFile(new URL("../features/chatbot/chatbot-page.tsx", import.meta.url), "utf8");
  for (const phrase of ["AI discovery status", "Suggested competitors", "Confirm & monitor", "No research provider is configured"]) assert.match(intelligence, new RegExp(phrase));
  for (const phrase of ["What do you want to achieve", "Weekly content plan", "Prepare governed publish", "Advanced · Build manually"]) assert.match(marketing, new RegExp(phrase));
  for (const phrase of ["Where is your website built", "Use hosted AI assistant", "Guided platform setup", "Advanced · Manual installation"]) assert.match(chatbot, new RegExp(phrase));
  assert.doesNotMatch(intelligence, /launched successfully|AI discovered 6/);
  assert.doesNotMatch(marketing, /published successfully|guaranteed sales/i);
});

test("chatbot deployment APIs are tenant scoped and hosted installation sends no spoofed state", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const { chatbot } = await clients(async (input, init) => {
    if (String(input).endsWith("/login")) return json(login);
    requests.push({ path: new URL(String(input)).pathname, method: init?.method ?? "GET", body: init?.body });
    return json({});
  });
  await chatbot.deployments(businessA);
  await chatbot.installHosted(businessB);
  assert.deepEqual(requests, [
    { path: `/api/v1/businesses/${businessA}/chatbot/deployments`, method: "GET", body: undefined },
    { path: `/api/v1/businesses/${businessB}/chatbot/deployments/hosted`, method: "POST", body: undefined },
  ]);
});

test("production build includes the hosted zero-code entry", async () => {
  const vite = await readFile(new URL("../../vite.config.ts", import.meta.url), "utf8");
  const hosted = await readFile(new URL("../widget/hosted.ts", import.meta.url), "utf8");
  assert.match(vite, /hosted: path\.resolve\(import\.meta\.dirname, 'hosted\.html'\)/);
  assert.match(hosted, /public\/hosted-widgets/);
  assert.doesNotMatch(hosted, /business_id|localStorage|access_token|refresh_token/);
});
