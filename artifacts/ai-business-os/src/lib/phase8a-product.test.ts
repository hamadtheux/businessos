import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  AUTOPILOT_PACKS,
  buildFirstClientReadiness,
  hasHealthyConnection,
  hasWriteReadyConnection,
  isTodayInTimezone,
  readinessCounts,
} from "./phase8a-product.ts";

test("Business Autopilot exposes every requested pack without overstating compiler support", () => {
  assert.deepEqual(
    AUTOPILOT_PACKS.map((pack) => pack.id),
    [
      "order-confirmation",
      "shipping-update",
      "delivery-follow-up",
      "review-request",
      "abandoned-checkout",
      "new-lead-response",
      "lead-follow-up",
      "customer-reactivation",
      "support-escalation",
      "social-recommendation",
    ],
  );
  for (const pack of AUTOPILOT_PACKS) {
    if (pack.support === "preparation_supported") {
      assert.ok(pack.prompt);
      assert.equal(pack.setupReason, null);
    } else {
      assert.equal(pack.prompt, null);
      assert.ok(pack.setupReason);
    }
  }
  assert.equal(
    AUTOPILOT_PACKS.find((pack) => pack.id === "abandoned-checkout")?.support,
    "setup_required",
  );
  assert.equal(
    AUTOPILOT_PACKS.filter(
      (pack) => pack.support === "preparation_supported",
    ).length,
    5,
  );
});

test("connection readiness requires connected, authorized, and healthy state", () => {
  const options = ["gmail", "microsoft_outlook"];
  assert.equal(
    hasHealthyConnection(
      [
        {
          connector_type: "gmail",
          status: "connected",
          authentication_state: "authorized",
          health: "healthy",
        },
      ],
      options,
    ),
    true,
  );
  for (const connection of [
    {
      connector_type: "gmail",
      status: "degraded",
      authentication_state: "authorized",
      health: "degraded",
    },
    {
      connector_type: "gmail",
      status: "connected",
      authentication_state: "failed",
      health: "healthy",
    },
  ]) {
    assert.equal(hasHealthyConnection([connection], options), false);
  }
});

test("provider write readiness requires live connection and enabled write capability", () => {
  const connection = {
    connector_type: "gmail",
    status: "connected",
    authentication_state: "authorized",
    health: "healthy",
  };
  assert.equal(
    hasWriteReadyConnection(
      [connection],
      [{
        connector_type: "gmail",
        external_writes_enabled: true,
        setup_status: "available",
      }],
      ["gmail"],
    ),
    true,
  );
  assert.equal(
    hasWriteReadyConnection(
      [connection],
      [{
        connector_type: "gmail",
        external_writes_enabled: false,
        setup_status: "provider_setup_required",
      }],
      ["gmail"],
    ),
    false,
  );
});

test("first-client readiness distinguishes action needed from unverified state", () => {
  const items = buildFirstClientReadiness({
    profileReady: true,
    brainSourceCount: 4,
    catalogApplicable: true,
    catalogItemCount: 0,
    enabledAgentCount: 2,
    activeWorkflowCount: 0,
    healthyCommunicationConnection: false,
    healthySocialConnection: null,
    commerceApplicable: true,
    healthyCommerceConnection: true,
    brandingReady: false,
    processingHealthy: true,
  });
  const counts = readinessCounts(items);
  assert.deepEqual(counts, {
    ready: 5,
    actionNeeded: 4,
    unavailable: 1,
    checking: 0,
    total: 10,
  });
  assert.equal(items.find((item) => item.id === "social")?.state, "unavailable");
  assert.equal(items.find((item) => item.id === "catalog")?.state, "action_needed");
});

test("today aggregation uses the business timezone instead of the browser timezone", () => {
  const now = new Date("2026-08-29T20:30:00.000Z");
  assert.equal(isTodayInTimezone("2026-08-29T20:15:00.000Z", "Asia/Karachi", now), true);
  assert.equal(isTodayInTimezone("2026-08-28T20:15:00.000Z", "Asia/Karachi", now), false);
  assert.equal(isTodayInTimezone("2026-08-29T23:30:00.000Z", "America/New_York", now), true);
});

test("Phase 8A control-room screens depend on live tenant APIs and honest states", async () => {
  const dashboard = await readFile(
    new URL("../features/dashboard/dashboard-page.tsx", import.meta.url),
    "utf8",
  );
  const automations = await readFile(
    new URL("../features/automations/workflow-builder.tsx", import.meta.url),
    "utf8",
  );
  for (const dependency of [
    "operationsApi.analytics",
    "aiWorkforceApi.agents.activity",
    "automationsApi.approvals.list",
    "integrationsApi.connections",
    "activationReadinessApi.get",
    "processingApi.health",
  ]) {
    assert.match(dashboard, new RegExp(dependency.replace(".", "\\.")));
  }
  assert.doesNotMatch(dashboard, /useWorkspaceData|workspaceRepository/);
  assert.doesNotMatch(dashboard, /buildFirstClientReadiness|hasWriteReadyConnection/);
  assert.doesNotMatch(
    dashboard,
    /handled 147|80% ready|90% ready|fake metric/i,
  );
  assert.match(automations, /governed_action_compiled_pending_approval/);
  assert.match(automations, /Provider write blocked/);
  assert.match(automations, /waiting for provider result/);
  assert.doesNotMatch(automations, /customer_ref:\s*["']test-customer/);
});
