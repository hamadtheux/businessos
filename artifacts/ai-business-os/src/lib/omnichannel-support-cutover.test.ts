import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createOperationsApi } from "../services/operations.ts";

const businessId = "71000000-0000-4000-8000-000000000001";
const user: UserPublic = {
  id: "72000000-0000-4000-8000-000000000002",
  email: "owner@example.test",
  first_name: "Business",
  last_name: "Owner",
  status: "active",
  is_email_verified: true,
  created_at: "2026-09-02T00:00:00Z",
};
const session: UserLoginResponse = {
  access_token: "memory-only-token",
  token_type: "bearer",
  expires_in: 900,
  user,
};

function json(value: unknown) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createOperationsApi(client);
}

test("conversation list sends channel and status filters through tenant API", async () => {
  let requestUrl = new URL("https://example.test");
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    requestUrl = new URL(String(input));
    return json({ items: [], total: 0, page: 1, page_size: 50 });
  });

  await api.conversations.list(businessId, {
    page: 1,
    pageSize: 50,
    search: "face wash",
    status: "open",
    channel: "facebook",
  });

  assert.equal(requestUrl.pathname, `/api/v1/businesses/${businessId}/conversations`);
  assert.equal(requestUrl.searchParams.get("channel"), "facebook");
  assert.equal(requestUrl.searchParams.get("status"), "open");
  assert.equal(requestUrl.searchParams.get("search"), "face wash");
});

test("external reply and AI-human controls use explicit server endpoints", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({
      path: new URL(String(input)).pathname,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return json({});
  });

  const clientRequestId = "11111111-1111-4111-8111-111111111111";
  await api.conversations.send(businessId, "conversation-1", {
    content: "We can help with that.",
    client_request_id: clientRequestId,
  });
  await api.conversations.control(businessId, "conversation-1", "take_over");
  await api.conversations.control(businessId, "conversation-1", "resume_ai");
  await api.conversations.read(businessId, "conversation-1");

  assert.deepEqual(requests.map((item) => item.path), [
    `/api/v1/businesses/${businessId}/conversations/conversation-1/send`,
    `/api/v1/businesses/${businessId}/conversations/conversation-1/control`,
    `/api/v1/businesses/${businessId}/conversations/conversation-1/control`,
    `/api/v1/businesses/${businessId}/conversations/conversation-1/read`,
  ]);
  assert.ok(requests.every((item) => item.method === "POST"));
  assert.deepEqual(requests[0].body, {
    content: "We can help with that.",
    client_request_id: clientRequestId,
  });
  assert.deepEqual(requests[1].body, { action: "take_over" });
  assert.deepEqual(requests[2].body, { action: "resume_ai" });
});

test("support list, metrics, and resolution use tenant-scoped APIs", async () => {
  const urls: URL[] = [];
  const bodies: unknown[] = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    urls.push(new URL(String(input)));
    bodies.push(init?.body ? JSON.parse(String(init.body)) : null);
    if (String(input).endsWith("/metrics")) return json({ open_issues: 0, ai_handling: 0, escalated: 0, waiting_for_customer: 0, resolved_today: 0 });
    if ((init?.method ?? "GET") === "PATCH") return json({});
    return json({ items: [], total: 0, page: 1, page_size: 25 });
  });

  await api.support.list(businessId, { status: "escalated", priority: "high", channel: "instagram" });
  await api.support.metrics(businessId);
  await api.support.update(businessId, "case-1", { status: "resolved", resolution_summary: "Replacement dispatched." });

  assert.equal(urls[0].pathname, `/api/v1/businesses/${businessId}/support/cases`);
  assert.equal(urls[0].searchParams.get("status"), "escalated");
  assert.equal(urls[0].searchParams.get("priority"), "high");
  assert.equal(urls[0].searchParams.get("channel"), "instagram");
  assert.equal(urls[1].pathname, `/api/v1/businesses/${businessId}/support/metrics`);
  assert.equal(urls[2].pathname, `/api/v1/businesses/${businessId}/support/cases/case-1`);
  assert.deepEqual(bodies[2], { status: "resolved", resolution_summary: "Replacement dispatched." });
});

test("production inbox and support screens expose real states without mock persistence", async () => {
  const conversations = await readFile(new URL("../features/operations/conversations-page.tsx", import.meta.url), "utf8");
  const support = await readFile(new URL("../features/support/customer-support-page.tsx", import.meta.url), "utf8");
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("../components/app-shell.tsx", import.meta.url), "utf8");

  for (const source of [conversations, support]) {
    for (const forbidden of ["useWorkspaceData", "workspaceRepository", "localStorage", "mock", "fake"] ) {
      assert.equal(source.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
    }
    assert.match(source, /refetchInterval: 10_000/);
    assert.match(source, /sender_type === "ai"/);
    assert.match(source, /take_over/);
    assert.match(source, /resume_ai/);
  }
  for (const channel of ["facebook", "instagram", "whatsapp", "website", "email"]) {
    assert.match(conversations, new RegExp(`value: "${channel}"`));
  }
  assert.doesNotMatch(conversations, /recorded internally|New record|does not send externally/i);
  assert.match(app, /path="\/support" component=\{CustomerSupportRoute\}/);
  assert.match(shell, /href: "\/support", label: "Customer Support"/);
});

test("mobile CSS makes list and selected thread separate navigable views", async () => {
  const css = await readFile(new URL("../index.css", import.meta.url), "utf8");
  assert.match(css, /@media \(max-width: 860px\)[\s\S]*mobile-thread-open/);
  assert.match(css, /mobile-case-open/);
  assert.match(css, /mobile-thread-back/);
});
