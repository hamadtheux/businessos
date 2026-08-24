import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createIntegrationsApi } from "../services/integrations.ts";
import { recommendedIntegrationConnectors } from "./business-features.ts";

const businessA = "b1000000-0000-4000-8000-000000000001";
const businessB = "b2000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "b3000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createIntegrationsApi(client);
}

test("registry and connection reads use independent tenant paths", async () => {
  const paths: string[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    paths.push(new URL(String(input)).pathname);
    return json([]);
  });
  await api.registry(businessA);
  await api.connections(businessB);
  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/integrations/registry`,
    `/api/v1/businesses/${businessB}/integrations/connections`,
  ]);
});

test("authorization only sends the allowlisted internal redirect target", async () => {
  let path = "";
  let body: unknown;
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    path = new URL(String(input)).pathname;
    body = JSON.parse(String(init?.body));
    return json({ connector_type: "gmail", authorization_url: "https://provider.example/authorize", expires_at: "2026-08-23T12:10:00Z" });
  });
  await api.authorize(businessA, "gmail");
  assert.equal(path, `/api/v1/businesses/${businessA}/integrations/gmail/authorize`);
  assert.deepEqual(body, { redirect_target: "/integrations" });
});

test("resource selection sends only provider-validated identity fields", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.selectResource(businessA, "connection-id", { resource_type: "calendar", external_reference: "calendar-id" });
  assert.deepEqual(body, { resource_type: "calendar", external_reference: "calendar-id" });
  for (const forbidden of ["display_name", "metadata", "credential_reference", "access_token", "refresh_token"]) {
    assert.equal(forbidden in body, false, forbidden);
  }
});

test("health, reconnect, disconnect, and event history have explicit endpoints", async () => {
  const requests: Array<{ path: string; method: string }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({ path: new URL(String(input)).pathname, method: init?.method ?? "GET" });
    if (String(input).includes("events")) return json({ items: [], total: 0, page: 1, page_size: 25 });
    if (String(input).includes("reconnect")) return json({ connector_type: "gmail", authorization_url: "https://provider.example/authorize", expires_at: "2026-08-23T12:10:00Z" });
    return json({});
  });
  await api.health(businessA, "connection-id");
  await api.reconnect(businessA, "connection-id");
  await api.disconnect(businessA, "connection-id");
  await api.events(businessA, "connection-id");
  assert.deepEqual(requests.map((item) => item.method), ["POST", "POST", "POST", "GET"]);
  assert.deepEqual(requests.map((item) => item.path), [
    `/api/v1/businesses/${businessA}/integrations/connections/connection-id/health`,
    `/api/v1/businesses/${businessA}/integrations/connections/connection-id/reconnect`,
    `/api/v1/businesses/${businessA}/integrations/connections/connection-id/disconnect`,
    `/api/v1/businesses/${businessA}/integrations/connections/connection-id/events`,
  ]);
});

test("industry recommendations are deterministic and recalculated on business switch", () => {
  assert.deepEqual(recommendedIntegrationConnectors("E-commerce"), [
    "meta_ads", "google_ads", "instagram", "facebook", "gmail", "whatsapp_business",
  ]);
  assert.deepEqual(recommendedIntegrationConnectors("Dental"), [
    "google_calendar", "whatsapp_business", "gmail", "microsoft_outlook",
  ]);
  assert.deepEqual(recommendedIntegrationConnectors("E-commerce"), [
    "meta_ads", "google_ads", "instagram", "facebook", "gmail", "whatsapp_business",
  ]);
  assert.deepEqual(recommendedIntegrationConnectors("Unknown"), []);
});

test("integrations screen is API-backed, tenant-keyed, and contains no mock persistence", async () => {
  const source = await readFile(new URL("../features/integrations/integrations-page.tsx", import.meta.url), "utf8");
  for (const forbidden of ["useWorkspaceData", "workspaceRepository", "localStorage", "setTimeout", "Connect prototype", "Sync now"]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  for (const forbidden of ["access_token", "refresh_token", "credential_reference", "client_secret"]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  const queryKeys = [...source.matchAll(/queryKey:\s*\[([^\]]+)/g)].map((match) => match[1]);
  assert.ok(queryKeys.length >= 4);
  assert.ok(queryKeys.every((key) => key.includes("activeBusinessId")));
  assert.match(source, /route writes through policy, approval, spend controls, and the durable dispatcher/);
  assert.match(source, /No connector can write until the platform enables secure provider configuration/);
  assert.match(source, /Provider setup required/);
  assert.match(source, /reauth_required/);
  assert.match(source, /status === "disabled"/);
  assert.match(source, /Selected resources/);
  assert.match(source, /No provider account metadata recorded/);
});

test("integrations route is production plan-gated", async () => {
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  const settings = await readFile(new URL("../features/settings/settings-page.tsx", import.meta.url), "utf8");
  assert.match(app, /feature="integrations" component=\{IntegrationsPage\}/);
  assert.match(app, /path="\/integrations"[\s\S]{0,80}component=\{IntegrationsRoute\}/);
  assert.doesNotMatch(app, /workspaceModule\(IntegrationsPage\)/);
  assert.equal(app.includes("demoWorkspaceDataEnabled"), false);
  assert.equal(app.includes("workspaceModule("), false);
  assert.equal(settings.includes("useWorkspaceData"), false);
});
