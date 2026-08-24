import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import { createOperationsApi } from "../services/operations.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { businessDateRange } from "./operational-dates.ts";

const businessA = "61000000-0000-4000-8000-000000000001";
const businessB = "62000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "63000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
async function authenticated(fetcher: typeof fetch) { const client = new ApiClient("https://api.example.test", fetcher); await client.login({ email: user.email, password: "form-only" }); return createOperationsApi(client); }

test("operational list requests carry tenant identity and bounded pagination", async () => {
  const urls: URL[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    urls.push(new URL(String(input)));
    return json({ items: [], total: 0, page: 2, page_size: 20 });
  });
  await api.customers.list(businessA, { page: 2, pageSize: 20, search: "Acme", status: "active" });
  assert.equal(urls[0].pathname, `/api/v1/businesses/${businessA}/customers`);
  assert.equal(urls[0].searchParams.get("page"), "2");
  assert.equal(urls[0].searchParams.get("page_size"), "20");
  assert.equal(urls[0].searchParams.get("search"), "Acme");
  assert.equal(urls[0].searchParams.get("status"), "active");
});

test("business switching generates independent tenant request paths", async () => {
  const paths: string[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    paths.push(new URL(String(input)).pathname);
    return json({ items: [], total: 0, page: 1, page_size: 25 });
  });
  await api.orders.list(businessA);
  await api.orders.list(businessB);
  assert.deepEqual(paths, [`/api/v1/businesses/${businessA}/orders`, `/api/v1/businesses/${businessB}/orders`]);
});

test("order creation sends lines and never sends a client total", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.orders.create(businessA, { customer_id: "customer-id", currency: "USD", adjustment_amount: "1.00", lines: [{ description: "Widget", quantity: 2, unit_price: "10.25" }] });
  assert.equal("total" in body, false);
  assert.equal("subtotal" in body, false);
  assert.deepEqual(body.lines, [{ description: "Widget", quantity: 2, unit_price: "10.25" }]);
});

test("conversation composer explicitly records internal messages", async () => {
  let body: unknown;
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body));
    return json({});
  });
  await api.conversations.message(businessA, "conversation-id", "Internal note");
  assert.deepEqual(body, { direction: "internal", content: "Internal note" });
});

test("completed production modules do not import workspace mock state", async () => {
  const files = [
    "../features/operations/operation-pages.tsx", "../features/notifications/notification-center.tsx",
    "../features/governance/opportunities-page.tsx", "../features/audit/audit-log.tsx",
    "../features/reports/daily-report.tsx", "../features/analytics/analytics-page.tsx",
    "../features/dashboard/dashboard-page.tsx", "../features/scheduling/scheduling-page.tsx",
  ];
  for (const file of files) {
    const source = await readFile(new URL(file, import.meta.url), "utf8");
    assert.equal(source.includes("useWorkspaceData"), false, file);
    assert.equal(source.includes("workspaceRepository"), false, file);
    assert.equal(source.includes("localStorage"), false, file);
  }
});

test("production routes are available and scheduling uses the business feature guard", async () => {
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("../components/app-shell.tsx", import.meta.url), "utf8");
  for (const component of ["CustomersPage", "OrdersPage", "CrmPage", "ConversationsPage", "OpportunitiesPage", "AnalyticsPage", "DailyReportPage", "AuditLogPage"]) {
    assert.match(app, new RegExp(`component=\\{${component}\\}`));
  }
  assert.match(app, /path="\/scheduling" component=\{SchedulingRoute\}/);
  assert.match(app, /feature="scheduling"/);
  assert.match(shell, /href: "\/scheduling"/);
});

test("business ID is part of every operational React Query namespace", async () => {
  const files = ["../features/operations/operation-pages.tsx", "../features/notifications/notification-center.tsx", "../features/governance/opportunities-page.tsx", "../features/audit/audit-log.tsx", "../features/reports/daily-report.tsx", "../features/analytics/analytics-page.tsx", "../features/dashboard/dashboard-page.tsx", "../features/scheduling/scheduling-page.tsx"];
  for (const file of files) {
    const source = await readFile(new URL(file, import.meta.url), "utf8");
    const queryKeys = [...source.matchAll(/queryKey:\s*\[([^\]]+)/g)].map((match) => match[1]);
    assert.ok(queryKeys.length > 0, file);
    assert.ok(queryKeys.every((key) => key.includes("activeBusinessId")), file);
  }
});

test("analytics periods follow the active business timezone", () => {
  const instant = new Date("2026-08-22T21:30:00Z");
  assert.deepEqual(businessDateRange("Asia/Karachi", 1, instant), { start: "2026-08-23", end: "2026-08-23" });
  assert.deepEqual(businessDateRange("America/Los_Angeles", 7, instant), { start: "2026-08-16", end: "2026-08-22" });
});
