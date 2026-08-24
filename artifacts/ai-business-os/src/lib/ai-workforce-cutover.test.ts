import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createAIWorkforceApi } from "../services/ai-workforce.ts";

const businessA = "91000000-0000-4000-8000-000000000001";
const businessB = "92000000-0000-4000-8000-000000000002";
const user: UserPublic = { id: "93000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };
function json(value: unknown) { return new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } }); }
async function authenticated(fetcher: typeof fetch) { const client = new ApiClient("https://api.example.test", fetcher); await client.login({ email: user.email, password: "form-only" }); return createAIWorkforceApi(client); }

test("agent configuration and activity are tenant scoped", async () => {
  const paths: string[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    paths.push(new URL(String(input)).pathname);
    return json([]);
  });
  await api.agents.list(businessA);
  await api.agents.capabilities(businessB);
  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/agents`,
    `/api/v1/businesses/${businessB}/agents/capabilities`,
  ]);
});

test("agent updates send bounded configuration only", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.agents.update(businessA, "sales", { autonomy_mode: "supervised", custom_instructions: "Be concise", capabilities: ["read_crm"] });
  assert.deepEqual(body, { autonomy_mode: "supervised", custom_instructions: "Be concise", capabilities: ["read_crm"] });
  for (const forbidden of ["business_id", "system_prompt", "api_key", "policy", "tools"]) assert.equal(forbidden in body, false);
});

test("command execution cannot inject capabilities or connector controls", async () => {
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });
  await api.commands.execute(businessA, "Show leads needing follow-up");
  assert.deepEqual(body, { command: "Show leads needing follow-up", trigger_source: "command_center", context_references: [] });
  for (const forbidden of ["role", "capabilities", "tools", "provider", "model", "execute", "dispatch"]) assert.equal(forbidden in body, false);
});

test("Command Center and agent screens are production API cutovers", async () => {
  const command = await readFile(new URL("../features/command/command-center-page.tsx", import.meta.url), "utf8");
  const agents = await readFile(new URL("../features/agents/agent-pages.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  for (const source of [command, agents]) {
    assert.equal(source.includes("useWorkspaceData"), false);
    assert.equal(source.includes("workspaceRepository"), false);
    assert.equal(source.includes("localStorage"), false);
    assert.match(source, /aiWorkforceApi/);
  }
  for (const component of ["CommandCenterPage", "AgentsOverviewPage", "AgentDetailPage", "AgentActivityPage"]) {
    assert.doesNotMatch(app, new RegExp(`workspaceModule\\(${component}\\)`));
  }
  assert.doesNotMatch(command, /Reasoning/);
  assert.doesNotMatch(agents, /Live prototype|success rate|accuracy/i);
});
