import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createAutomationsApi } from "../services/automations.ts";

const businessA = "81000000-0000-4000-8000-000000000001";
const businessB = "82000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "83000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
async function authenticated(fetcher: typeof fetch) { const client = new ApiClient("https://api.example.test", fetcher); await client.login({ email: user.email, password: "form-only" }); return createAutomationsApi(client); }

test("workflow and run requests are tenant scoped and paginated", async () => {
  const urls: URL[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    urls.push(new URL(String(input)));
    return json({ items: [], total: 0, page: 1, page_size: 100 });
  });
  await api.workflows.list(businessA);
  await api.runs.list(businessB, "workflow-id");
  assert.equal(urls[0].pathname, `/api/v1/businesses/${businessA}/automations/workflows`);
  assert.equal(urls[0].searchParams.get("page_size"), "100");
  assert.equal(urls[1].pathname, `/api/v1/businesses/${businessB}/automations/runs`);
  assert.equal(urls[1].searchParams.get("workflow_id"), "workflow-id");
  assert.equal(urls[1].searchParams.get("page_size"), "25");
});

test("simulation sends only explicit test inputs and forced failure identity", async () => {
  let body: Record<string, unknown> = {};
  let requestPath = "";
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requestPath = new URL(String(input)).pathname;
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({ valid: true, completed: true, trace: [], approvals: [], delays: [], planned_actions: [], errors: [] });
  });
  await api.workflows.simulate(businessA, "workflow-id", { payload: { lead: { estimated_value: 7500 } }, run_ai: false, forced_failure_node_key: "node-id" });
  assert.equal(requestPath, `/api/v1/businesses/${businessA}/automations/workflows/workflow-id/simulate`);
  assert.deepEqual(body, { payload: { lead: { estimated_value: 7500 } }, run_ai: false, forced_failure_node_key: "node-id" });
  for (const forbidden of ["execute", "dispatch", "connector", "api_key"]) assert.equal(forbidden in body, false);
});

test("approval decisions reuse the one business approval queue", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({ path: new URL(String(input)).pathname, method: String(init?.method), body: JSON.parse(String(init?.body)) });
    return json({});
  });
  await api.approvals.approve(businessA, "approval-id", "Reviewed");
  await api.approvals.reject(businessA, "approval-id", null);
  assert.deepEqual(requests, [
    { path: `/api/v1/businesses/${businessA}/approvals/approval-id/approve`, method: "POST", body: { decision_note: "Reviewed" } },
    { path: `/api/v1/businesses/${businessA}/approvals/approval-id/reject`, method: "POST", body: { decision_note: null } },
  ]);
});

test("automation and approvals screens are production API cutovers", async () => {
  const builder = await readFile(new URL("../features/automations/workflow-builder.tsx", import.meta.url), "utf8");
  const approvals = await readFile(new URL("../features/governance/action-pages.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  for (const source of [builder, approvals]) {
    assert.equal(source.includes("useWorkspaceData"), false);
    assert.equal(source.includes("workspaceRepository"), false);
    assert.equal(source.includes("localStorage"), false);
    assert.match(source, /activeBusinessId/);
  }
  assert.match(builder, /automationsApi\.workflows\.simulate/);
  assert.match(builder, /automationsApi\.runs\.nodes/);
  assert.match(approvals, /automationsApi\.approvals\.approve/);
  assert.match(app, /feature="automations" component=\{WorkflowBuilderPage\}/);
  assert.match(app, /path="\/automations" component=\{AutomationsRoute\}/);
  assert.match(app, /path="\/approvals" component=\{ApprovalsPage\}/);
  assert.doesNotMatch(app, /workspaceModule\(WorkflowBuilderPage\)/);
  assert.doesNotMatch(app, /workspaceModule\(ApprovalsPage\)/);
});
