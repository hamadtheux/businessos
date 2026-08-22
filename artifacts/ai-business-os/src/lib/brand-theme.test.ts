import assert from "node:assert/strict";
import test from "node:test";
import {
  brandIdentityFromDraft,
  contrastRatio,
  deriveBrandTheme,
  isValidHex,
  normalizeHex,
  resolveBrandIdentity,
  safeForeground,
} from "./brand-theme.ts";

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

test("unbranded businesses retain the established farm and real-estate defaults", () => {
  assert.equal(deriveBrandTheme(undefined, "green").brandPrimary, "#15803D");
  assert.equal(deriveBrandTheme(undefined, "navy").brandPrimary, "#1E3A8A");
});
