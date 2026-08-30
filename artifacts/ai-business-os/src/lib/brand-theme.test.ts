import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  brandIdentityFromDraft,
  contrastRatio,
  deriveBrandTheme,
  hasCustomBrandColors,
  isValidHex,
  normalizeHex,
  resolveBrandIdentity,
  safeForeground,
} from "./brand-theme.ts";
import {
  PRODUCT_BRAND_COLORS,
  PRODUCT_LOGO_PATH,
  PRODUCT_NAME,
} from "../config/brand.ts";
import { initials } from "./product-utils.ts";

test("publishes the approved palette and normalized authoritative logo", async () => {
  assert.equal(PRODUCT_NAME, "9D Brain");
  assert.equal(PRODUCT_LOGO_PATH, "/brand/9d-brain-logo-mark.png");
  assert.deepEqual(PRODUCT_BRAND_COLORS, {
    950: "#043B1F",
    900: "#055528",
    700: "#1D863A",
    500: "#47B345",
    400: "#7FCA48",
    300: "#B3EC57",
    100: "#ECFAA2",
    primary: "#1D863A",
    hover: "#176B31",
    active: "#115127",
    foreground: "#FFFFFF",
    focus: "#7FCA48",
  });

  const [original, normalized, componentSource, styles, widgetStyles] =
    await Promise.all([
      readFile(
        new URL("../../public/brand/9d-brain-logo.png", import.meta.url),
      ),
      readFile(
        new URL(
          "../../public/brand/9d-brain-logo-mark.png",
          import.meta.url,
        ),
      ),
      readFile(
        new URL("../components/product-brand.tsx", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../index.css", import.meta.url), "utf8"),
      readFile(new URL("../widget/widget.css", import.meta.url), "utf8"),
    ]);

  for (const logo of [original, normalized]) {
    assert.deepEqual(
      [...logo.subarray(0, 8)],
      [137, 80, 78, 71, 13, 10, 26, 10],
    );
    assert.ok(logo.byteLength > 100_000);
  }
  assert.deepEqual(
    [original.readUInt32BE(16), original.readUInt32BE(20)],
    [1254, 1254],
  );
  assert.deepEqual(
    [normalized.readUInt32BE(16), normalized.readUInt32BE(20)],
    [832, 832],
  );
  assert.match(componentSource, /ProductLogoSize = "sm" \| "md" \| "lg"/);
  assert.match(componentSource, /`product-logo-\$\{size\}`/);
  assert.match(styles, /\.product-logo-md\s*\{[^}]*width:\s*38px/);
  assert.match(styles, /\.product-logo img\s*\{[^}]*object-fit:\s*contain/);
  assert.doesNotMatch(styles, /\.product-logo img\s*\{[^}]*170%/);
  assert.doesNotMatch(widgetStyles, /\.widget-product-logo img\s*\{[^}]*170%/);
});

test("normalizes supported HEX forms and rejects unsafe values", () => {
  assert.equal(normalizeHex(" #1a6 "), "#11AA66");
  assert.equal(normalizeHex("#176b45"), "#176B45");
  assert.equal(normalizeHex("176B45"), null);
  assert.equal(isValidHex("#FFFF00"), true);
  assert.equal(isValidHex("javascript:red"), false);
});

test("chooses readable foregrounds for difficult brand colors", () => {
  const colors = [
    "#FFFF00",
    "#FFFFFF",
    "#000000",
    "#FF4FA3",
    "#39FF14",
    "#071B40",
    "#E02020",
  ];

  for (const color of colors) {
    const foreground = safeForeground(color);
    assert.ok(
      contrastRatio(color, foreground) >= 4.5,
      `${color} should be readable against ${foreground}`,
    );
  }
});

test("derives accessible application and sidebar interaction tokens", () => {
  for (const primaryColor of [
    "#FFFF00",
    "#FFFFFF",
    "#000000",
    "#071B40",
    "#E02020",
  ]) {
    const tokens = deriveBrandTheme({ primaryColor });
    assert.ok(contrastRatio(tokens.brandPrimary, tokens.brandOnPrimary) >= 4.5);
    assert.ok(
      contrastRatio(
        tokens.sidebarActiveBackground,
        tokens.sidebarActiveForeground,
      ) >= 4.5,
    );
    assert.ok(
      contrastRatio(tokens.sidebarBackground, tokens.sidebarForeground) >= 7,
    );
    assert.ok(
      contrastRatio(tokens.brandPrimarySoft, tokens.brandPrimaryInk) >= 4.5,
    );
    assert.ok(
      contrastRatio(tokens.selectionBackground, tokens.selectionForeground) >=
        4.5,
    );
    assert.notEqual(tokens.sidebarBackground, tokens.brandPrimary);
  }
});

test("derives optional support colors without storing derived tokens", () => {
  const source = brandIdentityFromDraft({
    primaryColor: "#176B45",
    secondaryColor: "",
    accentColor: "",
  });
  const resolved = resolveBrandIdentity(source);

  assert.deepEqual(source, {
    primaryColor: "#176B45",
    secondaryColor: undefined,
    accentColor: undefined,
    logo: undefined,
    logoUrl: undefined,
  });
  assert.match(resolved.secondary, /^#[0-9A-F]{6}$/);
  assert.match(resolved.accent, /^#[0-9A-F]{6}$/);
  assert.notEqual(resolved.secondary, source.primaryColor);
  assert.notEqual(resolved.accent, source.primaryColor);
});

test("tenant source colors produce isolated deterministic themes", () => {
  const farm = deriveBrandTheme({ primaryColor: "#176B45" });
  const realEstate = deriveBrandTheme({ primaryColor: "#172554" }, "navy");
  const farmAgain = deriveBrandTheme({ primaryColor: "#176B45" });

  assert.notEqual(farm.brandPrimary, realEstate.brandPrimary);
  assert.notEqual(farm.sidebarBackground, realEstate.sidebarBackground);
  assert.deepEqual(farm, farmAgain);
});

test("unbranded businesses use the official product default and retain explicit real-estate theming", () => {
  const product = deriveBrandTheme(undefined, "green");
  assert.equal(product.brandPrimary, PRODUCT_BRAND_COLORS.primary);
  assert.equal(product.brandPrimaryHover, PRODUCT_BRAND_COLORS.hover);
  assert.equal(product.brandPrimaryActive, PRODUCT_BRAND_COLORS.active);
  assert.equal(product.focusRing, PRODUCT_BRAND_COLORS.focus);
  assert.equal(product.sidebarBackground, PRODUCT_BRAND_COLORS[950]);
  assert.equal(deriveBrandTheme(undefined, "navy").brandPrimary, "#1E3A8A");
});

test("a tenant logo alone does not become a custom workspace color theme", () => {
  assert.equal(hasCustomBrandColors({}), false);
  assert.equal(hasCustomBrandColors({ primaryColor: "#123456" }), true);
  assert.equal(hasCustomBrandColors({ accentColor: "#654321" }), true);
});

test("business initials provide a tenant-only fallback for multiword and single-word names", () => {
  assert.equal(initials("Burd Egg"), "BE");
  assert.equal(initials("Nova Health"), "NH");
  assert.equal(initials("PropApp"), "PR");
  assert.equal(initials("Acme"), "AC");
});

test("authenticated sidebar and selector reuse the active tenant identity", async () => {
  const source = await readFile(
    new URL("../components/app-shell.tsx", import.meta.url),
    "utf8",
  );
  const sidebar = source.match(
    /data-testid="sidebar-business-brand"[\s\S]*?<div className="nav-list">/,
  )?.[0];
  const selector = source.match(
    /data-testid="button-business-selector"[\s\S]*?<\/button>/,
  )?.[0];

  assert.ok(sidebar);
  assert.match(sidebar, /<BusinessBrandMark/);
  assert.match(sidebar, /businessName=\{activeBusiness\.name\}/);
  assert.match(sidebar, /identity=\{activeBusiness\.brandIdentity\}/);
  assert.match(sidebar, /<div className="brand-copy">\{activeBusiness\.name\}<\/div>/);
  assert.match(sidebar, /<div className="brand-sub">Workspace<\/div>/);
  assert.doesNotMatch(sidebar, /ProductBrand|ProductLogo/);

  assert.ok(selector);
  assert.match(selector, /<BusinessBrandMark/);
  assert.match(selector, /businessName=\{activeBusiness\.name\}/);
  assert.match(selector, /identity=\{activeBusiness\.brandIdentity\}/);
  assert.doesNotMatch(selector, /ProductBrand|ProductLogo|Globe2/);
  assert.match(source, /Powered by \{PRODUCT_NAME\}/);
});
