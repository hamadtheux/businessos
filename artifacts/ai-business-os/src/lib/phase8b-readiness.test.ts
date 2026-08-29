import assert from "node:assert/strict";
import test from "node:test";

import { providerReadinessBadges } from "./phase8b-readiness.ts";

const definition = {
  setup_status: "available" as const,
  webhook_support: true,
  external_writes_enabled: true,
  resource_selection_required: true,
};

test("OAuth is not presented as read, webhook, write, or production acceptance", () => {
  const labels = providerReadinessBadges(definition, {
    status: "connected",
    authentication_state: "authorized",
    health: "not_checked",
    last_health_check_at: null,
    selected_resources: [{ resource_type: "mailbox" }],
  }).map((item) => item.label);

  assert.ok(labels.includes("Authenticated"));
  assert.ok(labels.includes("Read not verified"));
  assert.ok(labels.includes("Webhook not verified"));
  assert.ok(labels.includes("Write unavailable"));
  assert.ok(labels.includes("Production acceptance not recorded"));
});

test("healthy provider evidence still preserves approval and production gates", () => {
  const labels = providerReadinessBadges(
    definition,
    {
      status: "connected",
      authentication_state: "authorized",
      health: "healthy",
      last_health_check_at: "2026-08-29T12:00:00Z",
      selected_resources: [{ resource_type: "mailbox" }],
    },
    { webhookAccepted: true },
  ).map((item) => item.label);

  assert.ok(labels.includes("Read verified"));
  assert.ok(labels.includes("Webhook verified"));
  assert.ok(labels.includes("Write configured · approval required"));
  assert.ok(labels.includes("Production acceptance not recorded"));
  assert.equal(labels.includes("Ready for production"), false);
});

test("missing platform configuration never appears connectable", () => {
  const labels = providerReadinessBadges(
    { ...definition, setup_status: "provider_setup_required" },
    null,
  ).map((item) => item.label);

  assert.ok(labels.includes("Not configured"));
  assert.ok(labels.includes("Authentication required"));
  assert.ok(labels.includes("Write unavailable"));
});
