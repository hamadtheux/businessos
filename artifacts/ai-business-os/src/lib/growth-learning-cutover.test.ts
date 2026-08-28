import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { ApiClient } from "../services/api-client.ts";
import type { UserLoginResponse, UserPublic } from "../services/api-types.ts";
import {
  createGrowthLearningApi,
  type GrowthExperimentInput,
} from "../services/growth-learning.ts";


const businessA = "91000000-0000-4000-8000-000000000001";
const businessB = "92000000-0000-4000-8000-000000000002";
const user: UserPublic = {
  id: "93000000-0000-4000-8000-000000000003",
  email: "owner@example.test",
  first_name: "Business",
  last_name: "Owner",
  status: "active",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};
const session: UserLoginResponse = {
  access_token: "memory-only-token",
  token_type: "bearer",
  expires_in: 900,
  user,
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function authenticated(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only" });
  return createGrowthLearningApi(client);
}

function experimentInput(): GrowthExperimentInput {
  return {
    name: "Provider CTR comparison",
    hypothesis: "The challenger may show a higher observed CTR.",
    learning_key: "creative_family_ctr",
    experiment_type: "campaign",
    primary_metric: "ctr",
    attribution_classification: "provider_attributed",
    evaluation_window_days: 14,
    minimum_sample_size: 1_000,
    variants: [
      {
        variant_key: "control",
        label: "Control",
        is_control: true,
        campaign_id: "campaign-control",
      },
      {
        variant_key: "challenger",
        label: "Challenger",
        is_control: false,
        campaign_id: "campaign-challenger",
      },
    ],
  };
}

test("growth reads are tenant scoped and bounded", async () => {
  const urls: URL[] = [];
  const api = await authenticated(async (input) => {
    if (String(input).endsWith("/login")) return json(session);
    urls.push(new URL(String(input)));
    return json({ items: [], total: 0, page: 2, page_size: 10 });
  });

  await api.experiments.list(businessA, {
    page: 2,
    pageSize: 10,
    status: "evaluated",
  });
  await api.learnings.list(businessB);

  assert.equal(
    urls[0].pathname,
    `/api/v1/businesses/${businessA}/growth/experiments`,
  );
  assert.equal(urls[0].searchParams.get("page"), "2");
  assert.equal(urls[0].searchParams.get("page_size"), "10");
  assert.equal(urls[0].searchParams.get("status"), "evaluated");
  assert.equal(
    urls[1].pathname,
    `/api/v1/businesses/${businessB}/growth/learnings`,
  );
  assert.equal(urls[1].searchParams.get("page_size"), "25");
});

test("growth creation sends definitions, never client-authored outcomes", async () => {
  let path = "";
  let body: Record<string, unknown> = {};
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    path = new URL(String(input)).pathname;
    body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return json({});
  });

  await api.experiments.create(businessA, experimentInput());

  assert.equal(
    path,
    `/api/v1/businesses/${businessA}/growth/experiments`,
  );
  assert.equal(body.primary_metric, "ctr");
  assert.equal(body.attribution_classification, "provider_attributed");
  for (const forbidden of [
    "currency",
    "result",
    "classification",
    "predicted_uplift",
    "confidence",
    "provider_credentials",
  ]) {
    assert.equal(forbidden in body, false, forbidden);
  }
});

test("growth lifecycle and evaluation use explicit server endpoints", async () => {
  const requests: Array<{ path: string; method: string }> = [];
  const api = await authenticated(async (input, init) => {
    if (String(input).endsWith("/login")) return json(session);
    requests.push({
      path: new URL(String(input)).pathname,
      method: String(init?.method),
    });
    return json({});
  });

  await api.experiments.transition(businessA, "experiment-id", "ready");
  await api.experiments.transition(businessA, "experiment-id", "start");
  await api.experiments.transition(businessA, "experiment-id", "complete");
  await api.experiments.evaluate(businessA, "experiment-id");

  assert.deepEqual(
    requests.map((request) => request.path),
    [
      `/api/v1/businesses/${businessA}/growth/experiments/experiment-id/ready`,
      `/api/v1/businesses/${businessA}/growth/experiments/experiment-id/start`,
      `/api/v1/businesses/${businessA}/growth/experiments/experiment-id/complete`,
      `/api/v1/businesses/${businessA}/growth/experiments/experiment-id/evaluate`,
    ],
  );
  assert.ok(requests.every((request) => request.method === "POST"));
});

test("Analytics growth UI is a real API cutover with conservative evidence language", async () => {
  const [analytics, panel, service] = await Promise.all([
    readFile(
      new URL("../features/analytics/analytics-page.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../features/analytics/growth-learning-panel.tsx",
        import.meta.url,
      ),
      "utf8",
    ),
    readFile(new URL("../services/growth-learning.ts", import.meta.url), "utf8"),
  ]);

  assert.match(analytics, /<GrowthLearningPanel campaigns=\{campaigns\.data\}/);
  assert.match(panel, /growthLearningApi\.experiments\.list/);
  assert.match(panel, /growthLearningApi\.experiments\.create/);
  assert.match(panel, /growthLearningApi\.experiments\.evaluate/);
  assert.match(panel, /Observed directional difference/);
  assert.match(panel, /No statistical significance or causal claim is made/);
  assert.match(panel, /Evidence quality/);
  assert.match(panel, /Memory weighting/);
  assert.match(panel, /up to, not including/);
  assert.match(service, /\/growth\$\{path\}/);

  for (const source of [analytics, panel, service]) {
    assert.doesNotMatch(source, /localStorage|sessionStorage|workspaceRepository/);
    assert.doesNotMatch(
      source,
      /statistically significant winner|proves causality|guaranteed uplift|predicted uplift/i,
    );
  }
});
