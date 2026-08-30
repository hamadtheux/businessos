import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { PRODUCT_LOGO_PATH } from "../config/brand.ts";
import { businessInitials, tenantLogoPresentation } from "./tenant-logo.ts";

const tenantLogoUrl = "https://cdn.example.test/customer-logo.png";

test("valid tenant logo renders as the customer logo presentation", () => {
  assert.deepEqual(tenantLogoPresentation("Burd Egg", tenantLogoUrl, null), {
    kind: "logo",
    key: JSON.stringify(["Burd Egg", tenantLogoUrl]),
    logoUrl: tenantLogoUrl,
  });
});

test("missing tenant logo renders business initials", () => {
  assert.deepEqual(tenantLogoPresentation("Burd Egg", undefined, null), {
    kind: "initials",
    initials: "BE",
  });
  assert.equal(businessInitials("Nova Health"), "NH");
  assert.equal(businessInitials("Acme"), "A");
});

test("failed tenant logo image renders initials", () => {
  const initial = tenantLogoPresentation("Burd Egg", tenantLogoUrl, null);
  assert.equal(initial.kind, "logo");
  if (initial.kind !== "logo") return;

  assert.deepEqual(
    tenantLogoPresentation("Burd Egg", tenantLogoUrl, initial.key),
    { kind: "initials", initials: "BE" },
  );
});

test("failed image leaves no broken-image alt text visible", async () => {
  const source = await readFile(
    new URL("../components/tenant-logo.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /presentation\.kind === "logo" \? \(/);
  assert.match(source, /alt=""/);
  assert.match(source, /aria-hidden="true"/);
  assert.match(source, /event\.currentTarget\.hidden = true/);
  assert.match(source, /setFailedLogoKey\(presentation\.key\)/);
  assert.doesNotMatch(source, /alt=\{`\$\{businessName\} logo`\}/);
});

test("switching businesses resets a prior logo failure", () => {
  const businessA = tenantLogoPresentation("Burd Egg", tenantLogoUrl, null);
  assert.equal(businessA.kind, "logo");
  if (businessA.kind !== "logo") return;

  const businessB = tenantLogoPresentation(
    "Nova Health",
    "https://cdn.example.test/nova.png",
    businessA.key,
  );
  assert.equal(businessB.kind, "logo");
});

test("Business A logo failure does not affect Business B using the same identity values", () => {
  const businessA = tenantLogoPresentation(
    "Acme",
    tenantLogoUrl,
    null,
    "business-a",
  );
  assert.equal(businessA.kind, "logo");
  if (businessA.kind !== "logo") return;

  assert.equal(
    tenantLogoPresentation("Acme", tenantLogoUrl, businessA.key, "business-b")
      .kind,
    "logo",
  );
});

test("9D Brain platform logo is never used as the tenant fallback", async () => {
  const [componentSource, modelSource] = await Promise.all([
    readFile(new URL("../components/tenant-logo.tsx", import.meta.url), "utf8"),
    readFile(new URL("./tenant-logo.ts", import.meta.url), "utf8"),
  ]);

  assert.equal(
    tenantLogoPresentation("Acme", undefined, null).kind,
    "initials",
  );
  assert.equal(componentSource.includes(PRODUCT_LOGO_PATH), false);
  assert.equal(modelSource.includes(PRODUCT_LOGO_PATH), false);
  assert.doesNotMatch(componentSource, /ProductLogo|ProductBrand/);
  assert.doesNotMatch(modelSource, /ProductLogo|ProductBrand/);
});

test("authenticated sidebar and business selector share tenant identity behavior", async () => {
  const source = await readFile(
    new URL("../components/app-shell.tsx", import.meta.url),
    "utf8",
  );
  const sidebar = source.match(
    /data-testid="sidebar-business-brand"[\s\S]*?<div className="nav-list">/,
  )?.[0];
  const selector = source.match(
    /data-testid="button-business-selector"[\s\S]*?<ChevronDown/,
  )?.[0];

  assert.ok(sidebar);
  assert.ok(selector);
  for (const identity of [sidebar, selector]) {
    assert.match(identity, /<TenantLogo/);
    assert.match(identity, /businessName=\{activeBusiness\.name\}/);
    assert.match(identity, /tenantKey=\{activeBusiness\.id\}/);
    assert.match(
      identity,
      /logoUrl=\{resolveTenantLogoSource\(activeBusiness\.brandIdentity\)\}/,
    );
    assert.doesNotMatch(identity, /ProductLogo|ProductBrand/);
  }
});
