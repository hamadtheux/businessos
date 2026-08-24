import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const primaryRoutes = [
  "/dashboard",
  "/command-center",
  "/conversations",
  "/orders",
  "/customers",
  "/crm",
  "/scheduling",
  "/catalog",
  "/chatbot",
  "/marketing",
  "/marketing/content",
  "/marketing/calendar",
  "/marketing/campaigns",
  "/marketing/social",
  "/competitors",
  "/trends",
  "/agents",
  "/automations",
  "/approvals",
  "/opportunities",
  "/analytics",
  "/daily-report",
  "/integrations",
  "/business-brain",
  "/audit",
  "/settings",
  "/billing",
] as const;

test("the production router declares every primary acceptance route", async () => {
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");

  for (const route of primaryRoutes) {
    assert.match(
      app,
      new RegExp(`path=["']${route.replaceAll("/", "\\/")}["']`),
      route,
    );
  }

  assert.doesNotMatch(app, /We couldn't open your businesses/);
});

test("runtime startup and marketing failures remain scoped and recoverable", async () => {
  const [apiConfig, businessContext, marketingOverview] = await Promise.all([
    readFile(new URL("../config/api.ts", import.meta.url), "utf8"),
    readFile(new URL("../business-context.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(apiConfig, /:\s*"";/);
  assert.match(businessContext, /Branding is optional presentation data/);
  assert.match(businessContext, /setBusinesses\(loaded\)/);
  assert.doesNotMatch(marketingOverview, /Marketing overview unavailable/);
  assert.doesNotMatch(
    marketingOverview,
    /Marketing services are temporarily unavailable/,
  );
});
