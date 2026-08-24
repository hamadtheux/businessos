import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import { createProcessingApi } from "../services/processing.ts";

const businessId = "91000000-0000-4000-8000-000000000001";
const user: UserPublic = { id: "93000000-0000-4000-8000-000000000003", email: "owner@example.test", first_name: "Business", last_name: "Owner", status: "active", is_email_verified: true, created_at: "2026-01-01T00:00:00Z" };
const session: UserLoginResponse = { access_token: "memory-only-token", token_type: "bearer", expires_in: 900, user };

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
}

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createProcessingApi(client);
}

test("processing health and job reads are authenticated and tenant scoped", async () => {
  const paths: string[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    paths.push(new URL(String(input)).pathname);
    return json({ items: [], page: 1, page_size: 10, total: 0 });
  });
  await api.health(businessId);
  await api.jobs(businessId);
  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessId}/processing/health`,
    `/api/v1/businesses/${businessId}/processing/jobs`,
  ]);
});

test("only explicit safe retry and cancellation operations are exposed", async () => {
  const requests: Array<{ path: string; method: string; body: unknown }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({ path: new URL(String(input)).pathname, method: init?.method ?? "GET", body: init?.body });
    return json({});
  });
  await api.retry(businessId, "job-id");
  await api.cancel(businessId, "job-id");
  assert.deepEqual(requests, [
    { path: `/api/v1/businesses/${businessId}/processing/jobs/job-id/retry`, method: "POST", body: undefined },
    { path: `/api/v1/businesses/${businessId}/processing/jobs/job-id/cancel`, method: "POST", body: undefined },
  ]);
});

test("automation UI relies on durable processing and has no manual advance control", async () => {
  const source = await readFile(new URL("../features/automations/workflow-builder.tsx", import.meta.url), "utf8");
  const service = await readFile(new URL("../services/processing.ts", import.meta.url), "utf8");
  assert.match(source, /processingApi\.health/);
  assert.match(source, /resume automatically through durable PostgreSQL processing/);
  assert.doesNotMatch(source, /automationsApi\.runs\s*\.advance/);
  assert.doesNotMatch(service, /localStorage|json:\s*\{\s*job_type/);
});
