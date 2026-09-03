import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { isOnboardingWebsiteValidationError } from "../features/onboarding/onboarding-save.ts";
import {
  normalizeWebsiteInput,
  WEBSITE_INPUT_ERROR,
} from "../features/onboarding/onboarding-website.ts";
import { ApiError } from "../services/api-client.ts";

test("optional website input is empty or normalized from a bare domain", () => {
  assert.deepEqual(normalizeWebsiteInput(""), { value: "", error: null });
  assert.deepEqual(normalizeWebsiteInput("  "), { value: "", error: null });
  assert.deepEqual(normalizeWebsiteInput("9dbrain.com"), {
    value: "https://9dbrain.com",
    error: null,
  });
  assert.deepEqual(normalizeWebsiteInput("www.9dbrain.com"), {
    value: "https://www.9dbrain.com",
    error: null,
  });
  assert.deepEqual(normalizeWebsiteInput("example.co.uk"), {
    value: "https://example.co.uk",
    error: null,
  });
});

test("valid absolute HTTP and HTTPS website URLs remain unchanged", () => {
  for (const value of [
    "https://9dbrain.com/about?source=onboarding",
    "http://example.com",
  ]) {
    assert.deepEqual(normalizeWebsiteInput(value), { value, error: null });
  }
});

test("malformed, non-HTTP, and credentialed website URLs are rejected", () => {
  for (const value of [
    "example",
    "www",
    "https//example.com",
    "https//",
    "hello world",
    "://example.com",
    "javascript:alert(1)",
    "data:text/plain,hello",
    "ftp://example.com",
    "https://user:secret@example.com",
    "https://@example.com",
    "example.com@evil.com",
  ]) {
    const result = normalizeWebsiteInput(value);
    assert.equal(result.error, WEBSITE_INPUT_ERROR, value);
  }
});

test("only a website-specific backend 422 maps back to Website", () => {
  const websiteError = new ApiError(422, {
    detail: [
      {
        loc: ["body", "website_url"],
        msg: "Value error",
        type: "value_error",
      },
    ],
  });
  const unrelatedError = new ApiError(422, {
    detail: [
      {
        loc: ["body", "name"],
        msg: "Value error",
        type: "value_error",
      },
    ],
  });

  assert.equal(isOnboardingWebsiteValidationError(websiteError), true);
  assert.equal(isOnboardingWebsiteValidationError(unrelatedError), false);
  assert.equal(
    isOnboardingWebsiteValidationError(
      new ApiError(422, { detail: "website_url failed internally" }),
    ),
    false,
  );
  assert.equal(
    isOnboardingWebsiteValidationError(new ApiError(500, null)),
    false,
  );
});

test("Business basics blocks an invalid website and renders its inline error", async () => {
  const source = await readFile(
    new URL("../features/onboarding/onboarding-page.tsx", import.meta.url),
    "utf8",
  );
  const nextStart = source.indexOf("const next = () => {");
  const nextEnd = source.indexOf("const retrySetup", nextStart);
  const nextHandler = source.slice(nextStart, nextEnd);

  assert.match(nextHandler, /if \(step === 0\)/);
  assert.match(nextHandler, /normalizeWebsiteInput\(form\.website\)/);
  assert.match(
    nextHandler,
    /if \(website\.error\) \{[\s\S]*setWebsiteError\(website\.error\);[\s\S]*websiteInputRef\.current\?\.focus\(\);[\s\S]*return;/,
  );
  assert.match(source, /aria-invalid=\{Boolean\(websiteError\)\}/);
  assert.match(source, /className="field-error"/);
  assert.match(source, /\{websiteError\}/);
});

test("website-specific save rejection reopens Business basics without resetting draft state", async () => {
  const source = await readFile(
    new URL("../features/onboarding/onboarding-page.tsx", import.meta.url),
    "utf8",
  );
  const recoveryStart = source.indexOf(
    "if (isOnboardingWebsiteValidationError(error))",
  );
  const recoveryEnd = source.indexOf("return;", recoveryStart);
  const recovery = source.slice(recoveryStart, recoveryEnd);

  assert.match(recovery, /setWebsiteError\(WEBSITE_INPUT_ERROR\)/);
  assert.match(recovery, /setSetupState\("idle"\)/);
  assert.match(recovery, /setStep\(0\)/);
  assert.doesNotMatch(recovery, /setForm|setCatalog|setBrandDraft|removeItem/);
});

test("landing Business Brain identity uses a locally constrained product logo", async () => {
  const [publicHome, productBrand] = await Promise.all([
    readFile(
      new URL("../features/public/public-home.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../components/product-brand.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(publicHome, /className="hero6-brain-box"/);
  assert.match(publicHome, /<strong>Business Brain<\/strong>/);
  assert.match(
    publicHome,
    /<ProductLogo[\s\S]*className="hero6-brain-product-logo"[\s\S]*size="lg"/,
  );
  assert.match(
    publicHome,
    /\.hero6-brain-logo \.hero6-brain-product-logo \{[\s\S]*width: 48px;[\s\S]*height: 48px;[\s\S]*\}/,
  );
  assert.match(
    publicHome,
    /\.hero6-brain-product-logo img \{[\s\S]*object-fit: contain;[\s\S]*\}/,
  );
  assert.doesNotMatch(productBrand, /hero6-brain/);
});
