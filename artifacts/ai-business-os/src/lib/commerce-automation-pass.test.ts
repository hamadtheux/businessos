import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { ApiError, humanizeApiError } from "../services/api-client.ts";

test("commerce and Copilot remain tenant-scoped additions to the established flows", async () => {
  const [commerce, automations, catalog, catalogDialog, campaigns] = await Promise.all([
    readFile(new URL("../services/commerce.ts", import.meta.url), "utf8"),
    readFile(new URL("../features/automations/workflow-builder.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/catalog/industry-workspace-page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/catalog/catalog-item-dialog.tsx", import.meta.url), "utf8"),
    readFile(new URL("../features/marketing/marketing-pages.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(commerce, /businesses\/\$\{encodeURIComponent\(businessId\)\}\/commerce/);
  assert.match(commerce, /feed-destinations\/\$\{encodeURIComponent\(destinationId\)\}\/products/);
  assert.match(automations, /Automation Copilot/);
  assert.match(automations, /Advanced editor/);
  assert.match(automations, /automationsApi\.workflows\.simulate/);
  assert.match(automations, /Planned governed actions/);
  assert.match(automations, /Recommended automation packs/);
  assert.match(catalog, /Promote with AI/);
  assert.match(catalog, /Add manually/);
  assert.match(catalog, /CatalogImportDialog/);
  assert.match(catalogDialog, /Commerce details/);
  assert.match(campaigns, /catalog_item_ids/);
});

test("AI provider and platform failures are distinct from plan entitlement errors", () => {
  assert.equal(
    humanizeApiError(new ApiError(503, { detail: { code: "provider_unavailable" } }), "fallback"),
    "The external provider is temporarily degraded. Your internal data remains available.",
  );
  assert.equal(
    humanizeApiError(new ApiError(503, { detail: { code: "temporarily_unavailable" } }), "fallback"),
    "The service is temporarily unavailable. Please try again.",
  );
  assert.equal(
    humanizeApiError(new ApiError(403, { detail: { code: "feature_not_in_plan", entitlement_key: "marketing_cmo" } }), "fallback"),
    "Your current plan doesn't include AI CMO. Review Billing to compare plans.",
  );
});
