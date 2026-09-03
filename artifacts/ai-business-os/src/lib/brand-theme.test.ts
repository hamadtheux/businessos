import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  brandIdentityFromDraft,
  brandThemeStyle,
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
  PRODUCT_BRAND_GRADIENT,
  PRODUCT_LOGO_PATH,
  PRODUCT_NAME,
} from "../config/brand.ts";
import { businessInitials } from "./tenant-logo.ts";

test("publishes the approved blue and gold palette with the supplied official logo", async () => {
  assert.equal(PRODUCT_NAME, "9D Brain");
  assert.equal(PRODUCT_LOGO_PATH, "/brand/9d-brain-logo.png");
  assert.deepEqual(PRODUCT_BRAND_COLORS, {
    950: "#071E41",
    900: "#092A68",
    700: "#0747C9",
    500: "#1268F3",
    400: "#4B8DFF",
    300: "#A9C9FF",
    100: "#EAF2FF",
    primary: "#1268F3",
    hover: "#0D56D9",
    active: "#0747C9",
    foreground: "#FFFFFF",
    focus: "#1268F3",
    gold: "#F2B622",
    goldDeep: "#D89300",
    goldSoft: "#FFF6D8",
    text: "#101828",
    mutedText: "#667085",
  });
  assert.equal(
    PRODUCT_BRAND_GRADIENT,
    "linear-gradient(135deg, #1268F3 0%, #0747C9 42%, #F2B622 72%, #D89300 100%)",
  );

  const [logo, masterLogo, componentSource, styles, widgetStyles] =
    await Promise.all([
      readFile(new URL("../../public/brand/9d-brain-logo.png", import.meta.url)),
      readFile(
        new URL("../../public/brand/9d-brain-logo-master.png", import.meta.url),
      ),
      readFile(new URL("../components/product-brand.tsx", import.meta.url), "utf8"),
      readFile(new URL("../index.css", import.meta.url), "utf8"),
      readFile(new URL("../widget/widget.css", import.meta.url), "utf8"),
    ]);

  for (const image of [logo, masterLogo]) {
    assert.deepEqual(
      [...image.subarray(0, 8)],
      [137, 80, 78, 71, 13, 10, 26, 10],
    );
  }

  assert.deepEqual(
    [logo.readUInt32BE(16), logo.readUInt32BE(20)],
    [512, 512],
  );
  assert.ok(logo.byteLength > 10_000);
  assert.ok(logo.byteLength < masterLogo.byteLength);

  assert.deepEqual(
    [masterLogo.readUInt32BE(16), masterLogo.readUInt32BE(20)],
    [1254, 1254],
  );
  assert.equal(
    masterLogo[25],
    2,
    "the supplied RGB master logo must remain untouched",
  );
  assert.equal(
    createHash("sha256").update(masterLogo).digest("hex"),
    "fadf026b07a6c0e67f036b5d628ec762cfe62188a4ca1e3d2410402251bc725d",
  );
  assert.match(componentSource, /ProductLogoSize = "sm" \| "md" \| "lg"/);
  assert.match(componentSource, /`product-logo-\$\{size\}`/);
  assert.match(styles, /\.product-logo-md\s*\{[^}]*width:\s*38px/);
  assert.match(styles, /\.product-logo img\s*\{[^}]*object-fit:\s*contain/);
  assert.doesNotMatch(styles, /\.product-logo img\s*\{[^}]*170%/);
  assert.doesNotMatch(widgetStyles, /\.widget-product-logo img\s*\{[^}]*170%/);

  const brandedPages = await Promise.all(
    [
      "../../index.html",
      "../../public/privacy.html",
      "../../public/terms.html",
      "../../public/data-deletion.html",
    ].map((path) => readFile(new URL(path, import.meta.url), "utf8")),
  );

  for (const source of brandedPages) {
    assert.ok(source.includes(PRODUCT_LOGO_PATH));
  }

  const platformEntrypoints = await Promise.all(
    [
      "../../index.html",
      "../../hosted.html",
      "../../widget.html",
      "../../public/privacy.html",
      "../../public/terms.html",
      "../../public/data-deletion.html",
    ].map((path) => readFile(new URL(path, import.meta.url), "utf8")),
  );

  for (const source of platformEntrypoints) {
    assert.ok(source.includes("/brand/9d-brain-favicon.png"));
    assert.equal(source.includes("9d-brain-logo-mark.png"), false);
    assert.equal(source.includes("favicon.svg"), false);
  }
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

test("a tenant matching the product blue still receives an isolated custom theme", () => {
  const product = deriveBrandTheme();
  const tenant = deriveBrandTheme({
    primaryColor: PRODUCT_BRAND_COLORS.primary,
    secondaryColor: "#42526D",
    accentColor: "#A855F7",
  });

  assert.equal(tenant.brandPrimary, PRODUCT_BRAND_COLORS.primary);
  assert.equal(tenant.brandSecondary, "#42526D");
  assert.equal(tenant.brandAccent, "#A855F7");
  assert.notEqual(tenant.sidebarBackground, product.sidebarBackground);
  const tenantGradient = String(
    (brandThemeStyle(tenant) as unknown as Record<string, unknown>)[
      "--brand-gradient"
    ],
  );
  assert.match(tenantGradient, /#1268F3 0%/);
  assert.match(tenantGradient, /#A855F7 72%/);
  assert.match(tenantGradient, /#42526D 100%/);
});

test("unbranded businesses use the official product default and retain explicit real-estate theming", () => {
  const product = deriveBrandTheme(undefined, "green");
  assert.equal(product.brandPrimary, PRODUCT_BRAND_COLORS.primary);
  assert.equal(product.brandPrimaryHover, PRODUCT_BRAND_COLORS.hover);
  assert.equal(product.brandPrimaryActive, PRODUCT_BRAND_COLORS.active);
  assert.equal(product.focusRing, PRODUCT_BRAND_COLORS.focus);
  assert.equal(product.sidebarBackground, PRODUCT_BRAND_COLORS[950]);
  assert.equal(product.sidebarActiveBackground, PRODUCT_BRAND_COLORS[100]);
  assert.equal(product.brandAccent, PRODUCT_BRAND_COLORS.gold);
  assert.equal(product.brandAccentSoft, PRODUCT_BRAND_COLORS.goldSoft);
  assert.equal(product.brandGradient, PRODUCT_BRAND_GRADIENT);
  assert.equal(deriveBrandTheme(undefined, "navy").brandPrimary, "#1E3A8A");
});

test("production branding sources contain no retired platform-green palette values", async () => {
  const sources = await Promise.all(
    [
      "../config/brand.ts",
      "../index.css",
      "../components/app-bootstrap-screen.css",
      "../features/public/public-home.css",
      "../features/public/public-home-hero.css",
      "../features/public/public-home.tsx",
      "../features/public/public-pages.tsx",
      "../widget/widget.css",
      "../widget/loader.ts",
      "../../index.html",
      "../../hosted.html",
      "../../widget.html",
      "../../public/privacy.html",
      "../../public/terms.html",
      "../../public/data-deletion.html",
    ].map((path) => readFile(new URL(path, import.meta.url), "utf8")),
  );
  const retiredColors = [
    "043B1F",
    "055528",
    "1D863A",
    "47B345",
    "7FCA48",
    "B3EC57",
    "ECFAA2",
    "176B31",
    "115127",
  ].map((value) => `#${value}`);
  const productionBranding = sources.join("\n").toUpperCase();

  for (const color of retiredColors) {
    assert.equal(productionBranding.includes(color), false, `${color} remains`);
  }

  const scopedBrandSurfaces = await Promise.all(
    [
      "../features/analytics/analytics-page.tsx",
      "../features/command/command-center-page.tsx",
      "../features/public/public-home.css",
    ].map((path) => readFile(new URL(path, import.meta.url), "utf8")),
  );
  const retiredSurfaceAccents = [
    "#15803D",
    "#16803D",
    "#9BC9A6",
    "#73AD80",
    "#DCEFE0",
    "#E8EEE8",
    "#62B968",
    "#69BB68",
    "#69B96A",
    "RGBA(99, 180, 114",
    "RGBA(76, 166, 83",
    "RGBA(76, 171, 82",
  ];
  const scopedBranding = scopedBrandSurfaces.join("\n").toUpperCase();

  for (const color of retiredSurfaceAccents) {
    assert.equal(scopedBranding.includes(color), false, `${color} remains`);
  }
});

test("semantic success and connected states retain green meaning", async () => {
  const styles = await readFile(new URL("../index.css", import.meta.url), "utf8");

  assert.match(
    styles,
    /\.status\.success\s*\{[^}]*color:\s*#267444[^}]*background:\s*#ddf2e3/is,
  );
  assert.match(
    styles,
    /\.status-dot\.is-live\s*\{[^}]*background:\s*#16a34a/is,
  );
  assert.doesNotMatch(
    styles,
    /\.app-shell\[data-custom-brand="true"\]\s+\.status\.success/,
  );
});

test("a tenant logo alone does not become a custom workspace color theme", () => {
  assert.equal(hasCustomBrandColors({}), false);
  assert.equal(hasCustomBrandColors({ primaryColor: "#123456" }), true);
  assert.equal(hasCustomBrandColors({ accentColor: "#654321" }), true);
});

test("business initials provide a tenant-only fallback for multiword and single-word names", () => {
  assert.equal(businessInitials("Burd Egg"), "BE");
  assert.equal(businessInitials("Nova Health"), "NH");
  assert.equal(businessInitials("PropApp"), "P");
  assert.equal(businessInitials("Acme"), "A");
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
  assert.match(sidebar, /<TenantLogo/);
  assert.match(sidebar, /businessName=\{activeBusiness\.name\}/);
  assert.match(sidebar, /tenantKey=\{activeBusiness\.id\}/);
  assert.match(sidebar, /logoUrl=\{resolveTenantLogoSource\(activeBusiness\.brandIdentity\)\}/);
  assert.match(sidebar, /<div className="brand-copy">\{activeBusiness\.name\}<\/div>/);
  assert.match(sidebar, /<div className="brand-sub">Workspace<\/div>/);
  assert.doesNotMatch(sidebar, /ProductBrand|ProductLogo/);

  assert.ok(selector);
  assert.match(selector, /<TenantLogo/);
  assert.match(selector, /businessName=\{activeBusiness\.name\}/);
  assert.match(selector, /tenantKey=\{activeBusiness\.id\}/);
  assert.match(selector, /logoUrl=\{resolveTenantLogoSource\(activeBusiness\.brandIdentity\)\}/);
  assert.doesNotMatch(selector, /ProductBrand|ProductLogo|Globe2/);
  assert.match(source, /Powered by \{PRODUCT_NAME\}/);
});
