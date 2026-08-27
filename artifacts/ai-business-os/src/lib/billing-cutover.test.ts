import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { ApiClient, ApiError, humanizeApiError } from "../services/api-client.ts";
import {
  createBillingApi,
  isCurrentBillingResponse,
  shouldRequestPlanChange,
} from "../services/billing.ts";

const businessId = "d1000000-0000-4000-8000-000000000001";

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("billing reads are tenant scoped and mutations use typed payloads", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const client = new ApiClient("https://api.example.test", async (input, init) => {
    if (String(input).endsWith("/auth/login")) {
      return json({ access_token: "token", token_type: "bearer", expires_in: 900, user: { id: "u", email: "owner@example.test", first_name: "Owner", last_name: null, status: "active", is_email_verified: true, created_at: new Date().toISOString() } });
    }
    requests.push({ path: new URL(String(input)).pathname, method: String(init?.method ?? "GET"), body: init?.body ? JSON.parse(String(init.body)) : null });
    return json(String(input).endsWith("change-intent") ? { status: "provider_unavailable", message: "Not configured", blockers: [], checkout_url: null } : {});
  });
  await client.login({ email: "owner@example.test", password: "password-password" });
  const api = createBillingApi(client);
  await api.overview(businessId);
  await api.usage(businessId);
  await api.plans(businessId);
  await api.changeIntent(businessId, "growth", "year");
  await api.cancel(businessId, "Owner requested cancellation.");
  await api.reactivate(businessId);

  const root = `/api/v1/businesses/${businessId}/billing`;
  assert.deepEqual(requests.map((item) => [item.path, item.method]), [
    [root, "GET"], [`${root}/usage`, "GET"], [`${root}/plans`, "GET"],
    [`${root}/change-intent`, "POST"], [`${root}/cancel`, "POST"], [`${root}/reactivate`, "POST"],
  ]);
  assert.deepEqual(requests[3]?.body, { plan_code: "growth", billing_interval: "year" });
  assert.deepEqual(requests[4]?.body, { reason: "Owner requested cancellation." });
});

test("billing UI is server-backed, fail-closed, and truthful about provider state", async () => {
  const [page, context, shell, app] = await Promise.all([
    readFile(new URL("../features/billing/billing-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../business-context.tsx", import.meta.url), "utf8"),
    readFile(new URL("../components/app-shell.tsx", import.meta.url), "utf8"),
    readFile(new URL("../App.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(page, /billingApi\.usage/);
  assert.match(page, /billingApi\.plans/);
  assert.match(page, /Online checkout is not configured yet/);
  assert.match(page, /Taxes, invoices, payment methods, MRR, and ARR are not shown/);
  assert.doesNotMatch(page, /card number|bank account|fake invoice/i);
  assert.match(context, /billingApi\.overview/);
  assert.match(shell, /billing\?\.entitlements \?\? null/);
  assert.match(app, /path="\/billing" component=\{BillingPage\}/);
  assert.match(app, /setLocation\(`\/billing\?feature=\$\{feature\}`\)/);
});

test("plan CTAs surface the authoritative result beside the plan cards", async () => {
  const page = await readFile(new URL("../features/billing/billing-page.tsx", import.meta.url), "utf8");
  assert.match(page, /data-testid=\{`plan-change-feedback-\$\{plan\.code\}`\}/);
  assert.match(page, /aria-live="polite"/);
  assert.match(page, /cardFeedback\.blockers\.map/);
  assert.match(page, /currently in use;.*allowed on this plan/);
  assert.match(page, /checkout requires provider setup/);
  assert.match(page, /"Check availability"/);
  assert.match(page, /planFeedback\?\.businessId === activeBusinessId/);
  assert.match(page, /visiblePlanFeedback\?\.planCode === plan\.code/);
  assert.match(page, /invalidateQueries\(\{ queryKey: \["billing", activeBusinessId, "usage"\] \}\)/);
  assert.match(page, /invalidateQueries\(\{ queryKey: \["billing", activeBusinessId, "plans"\] \}\)/);
});

test("current plan never calls availability and billing responses stay tenant scoped", () => {
  assert.equal(shouldRequestPlanChange("pro", "pro"), false);
  assert.equal(shouldRequestPlanChange("pro", "growth"), true);
  assert.equal(isCurrentBillingResponse("business-a", "business-a", 4, 4, "business-a"), true);
  assert.equal(isCurrentBillingResponse("business-a", "business-b", 4, 4, "business-a"), false);
  assert.equal(isCurrentBillingResponse("business-a", "business-a", 3, 4, "business-a"), false);
  assert.equal(isCurrentBillingResponse("business-a", "business-a", 4, 4, "business-b"), false);
});

test("Pro survives refetch and re-login while tenant switching retains each plan", async () => {
  const businessA = "d1000000-0000-4000-8000-000000000001";
  const businessB = "d2000000-0000-4000-8000-000000000002";
  const overview = (business: string, plan: "free" | "pro") => ({
    business_id: business,
    subscription_id: `${business}-subscription`,
    plan_id: `${plan}-plan`,
    plan_version_id: `${plan}-version`,
    plan_code: plan,
    plan_name: plan === "pro" ? "Pro" : "Free",
    plan_version: 1,
    subscription_status: "active",
    access_reason: "subscription_active",
    billing_interval: "month",
    current_period_start: "2026-08-01T00:00:00Z",
    current_period_end: "2026-09-01T00:00:00Z",
    trial_started_at: null,
    trial_ends_at: null,
    cancel_at_period_end: false,
    entitlements: {},
    provider_configured: false,
  });
  const requested: string[] = [];
  const client = new ApiClient("https://api.example.test", async (input) => {
    const path = new URL(String(input)).pathname;
    requested.push(path);
    if (path.endsWith("/auth/login")) {
      return json({ access_token: "token", token_type: "bearer", expires_in: 900, user: { id: "u", email: "owner@example.test", first_name: "Owner", last_name: null, status: "active", is_email_verified: true, created_at: new Date().toISOString() } });
    }
    if (path.endsWith("/auth/logout")) return new Response(null, { status: 204 });
    if (path.endsWith(`/businesses/${businessA}/billing`)) return json(overview(businessA, "pro"));
    if (path.endsWith(`/businesses/${businessB}/billing`)) return json(overview(businessB, "free"));
    return json({ detail: "not found" }, 404);
  });
  await client.login({ email: "owner@example.test", password: "password-password" });
  const api = createBillingApi(client);
  assert.equal((await api.overview(businessA)).plan_code, "pro");
  assert.equal((await api.overview(businessA)).plan_code, "pro");
  assert.equal((await api.overview(businessB)).plan_code, "free");
  assert.equal((await api.overview(businessA)).plan_code, "pro");
  await client.logout();
  await client.login({ email: "owner@example.test", password: "password-password" });
  assert.equal((await api.overview(businessA)).plan_code, "pro");
  assert.equal(requested.filter((path) => path.endsWith("/change-intent")).length, 0);
});

test("a scoped 429 availability error cannot replace the Pro overview", async () => {
  let overviewPlan = "pro";
  const client = new ApiClient("https://api.example.test", async (input) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/auth/login")) {
      return json({ access_token: "token", token_type: "bearer", expires_in: 900, user: { id: "u", email: "owner@example.test", first_name: "Owner", last_name: null, status: "active", is_email_verified: true, created_at: new Date().toISOString() } });
    }
    if (path.endsWith("/change-intent")) return json({ detail: "Too many plan-change attempts. Try again shortly." }, 429);
    return json({ business_id: businessId, plan_code: overviewPlan });
  });
  await client.login({ email: "owner@example.test", password: "password-password" });
  const api = createBillingApi(client);
  assert.equal((await api.overview(businessId)).plan_code, "pro");
  await assert.rejects(() => api.changeIntent(businessId, "growth", "month"), ApiError);
  assert.equal(overviewPlan, "pro");
  assert.equal((await api.overview(businessId)).plan_code, "pro");
});

test("structured backend entitlement errors become upgrade guidance", () => {
  assert.equal(
    humanizeApiError(new ApiError(409, { detail: {
      code: "usage_limit_reached",
      entitlement_key: "max_ai_executions_month",
      current: 1_000,
      limit: 1_000,
      upgrade_required: true,
    } }), "fallback"),
    "You've used 1,000 / 1,000 ai executions this billing period. Review Billing to upgrade.",
  );
  assert.equal(
    humanizeApiError(new ApiError(403, { detail: {
      code: "feature_not_in_plan",
      entitlement_key: "advanced_analytics",
      upgrade_required: true,
    } }), "fallback"),
    "Your current plan doesn't include Advanced Analytics. Review Billing to compare plans.",
  );
  assert.equal(
    humanizeApiError(new ApiError(403, { detail: {
      code: "feature_not_in_plan",
      entitlement_key: "marketing_cmo",
      upgrade_required: true,
    } }), "AI content generation could not be completed."),
    "Your current plan doesn't include AI CMO. Review Billing to compare plans.",
  );
});
