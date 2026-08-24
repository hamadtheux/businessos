import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  businessFeatureRouteRedirect,
  filterBusinessFeatureItems,
  isBusinessFeatureContentVisible,
  isBusinessFeatureEnabled,
  isBusinessFeatureEnabledForIndustry,
  type BusinessFeatureItem,
} from "./business-features.ts";
import { ONBOARDING_INDUSTRIES } from "./business-industries.ts";

const navigation: Array<BusinessFeatureItem & { href: string }> = [
  { href: "/dashboard" },
  { href: "/scheduling", feature: "scheduling" },
];

const visibleHrefs = (industry: string) =>
  filterBusinessFeatureItems({ industry }, navigation).map((item) => item.href);

test("scheduling policy uses the exact canonical onboarding industries", () => {
  assert.deepEqual(ONBOARDING_INDUSTRIES, [
    "Farm/Agriculture",
    "Real Estate",
    "E-commerce",
    "Hospital",
    "Clinic",
    "Medical Practice",
    "Dental",
    "Professional Services",
    "Other",
  ]);

  const schedulingIndustries = new Set([
    "Hospital",
    "Clinic",
    "Medical Practice",
    "Dental",
    "Professional Services",
  ]);

  for (const industry of ONBOARDING_INDUSTRIES) {
    assert.equal(
      isBusinessFeatureEnabledForIndustry(industry, "scheduling"),
      schedulingIndustries.has(industry),
      industry,
    );
  }
});

test("E-commerce hides and disables Scheduling while Dental shows and enables it", () => {
  assert.deepEqual(visibleHrefs("E-commerce"), ["/dashboard"]);
  assert.equal(
    isBusinessFeatureEnabled({ industry: "E-commerce" }, "scheduling"),
    false,
  );
  assert.deepEqual(visibleHrefs("Dental"), ["/dashboard", "/scheduling"]);
  assert.equal(
    isBusinessFeatureEnabled({ industry: "Dental" }, "scheduling"),
    true,
  );
});

test("business switching recalculates Scheduling visibility without leaked state", () => {
  assert.deepEqual(visibleHrefs("E-commerce"), ["/dashboard"]);
  assert.deepEqual(visibleHrefs("Dental"), ["/dashboard", "/scheduling"]);
  assert.deepEqual(visibleHrefs("E-commerce"), ["/dashboard"]);
});

test("unsupported businesses are redirected away from Scheduling routes", () => {
  assert.equal(
    businessFeatureRouteRedirect({ industry: "E-commerce" }, "/scheduling"),
    "/dashboard",
  );
  assert.equal(
    businessFeatureRouteRedirect(
      { industry: "Farm/Agriculture" },
      "/scheduling/providers",
    ),
    "/dashboard",
  );
  assert.equal(
    businessFeatureRouteRedirect({ industry: "Dental" }, "/scheduling"),
    null,
  );
});

test("scheduling Command Center content is visible only for enabled businesses", () => {
  for (const prompt of [
    "Show tomorrow's appointments",
    "Find available appointment slots",
    "Which doctor is free tomorrow?",
    "Show tomorrow's schedule",
  ]) {
    assert.equal(
      isBusinessFeatureContentVisible({ industry: "E-commerce" }, prompt),
      false,
      prompt,
    );
    assert.equal(
      isBusinessFeatureContentVisible({ industry: "Dental" }, prompt),
      true,
      prompt,
    );
  }
  assert.equal(
    isBusinessFeatureContentVisible(
      { industry: "E-commerce" },
      "Show leads needing follow-up",
    ),
    true,
  );
});

test("production navigation and route declarations consume the feature policy", async () => {
  const shell = await readFile(
    new URL("../components/app-shell.tsx", import.meta.url),
    "utf8",
  );
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");
  assert.match(shell, /feature: "scheduling"/);
  assert.match(
    shell,
    /filterBusinessFeatureItems\(\s*activeBusiness,\s*group\.items,\s*billing\?\.entitlements \?\? null,\s*\)/,
  );
  assert.match(shell, /isWorkspaceModuleVisible\(activeBusiness\.industry, module\)/);
  assert.match(app, /BusinessFeatureRoute/);
  assert.match(app, /feature="scheduling"/);
  assert.match(app, /WorkspaceModuleRoute/);
  assert.match(app, /CatalogWorkspaceRoute/);
});

test("all production scheduling entry points use the centralized policy", async () => {
  const files = [
    "../features/command/command-center-page.tsx",
    "../features/dashboard/dashboard-page.tsx",
    "../features/analytics/analytics-page.tsx",
    "../features/reports/daily-report.tsx",
    "../features/notifications/notification-center.tsx",
    "../features/automations/workflow-builder.tsx",
  ];
  for (const file of files) {
    const source = await readFile(new URL(file, import.meta.url), "utf8");
    assert.match(source, /@\/lib\/business-features/, file);
  }
});
