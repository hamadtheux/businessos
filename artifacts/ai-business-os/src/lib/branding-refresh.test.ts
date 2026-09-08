import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { BRANDING_REFRESH_INTERVAL_MS, startBrandingRefresh } from "./branding-refresh.ts";

test("branding refresh renews visible views, deduplicates requests and cleans up", async () => {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  let tick: () => Promise<void> = async () => {};
  let cleared = false;
  const browser = Object.assign(new EventTarget(), {
    setInterval(callback: () => Promise<void>, delay: number) {
      tick = callback;
      assert.equal(delay, BRANDING_REFRESH_INTERVAL_MS);
      assert.ok(delay < 60_000);
      return 1;
    },
    clearInterval() { cleared = true; },
  });
  const page = Object.assign(new EventTarget(), { visibilityState: "visible" });
  Object.defineProperty(globalThis, "window", { configurable: true, value: browser });
  Object.defineProperty(globalThis, "document", { configurable: true, value: page });
  let calls = 0;
  let finish: () => void = () => {};
  const stop = startBrandingRefresh(() => {
    calls += 1;
    return new Promise<void>((resolve) => { finish = resolve; });
  });
  try {
    page.visibilityState = "hidden";
    await tick();
    assert.equal(calls, 0);
    page.visibilityState = "visible";
    const pending = tick();
    browser.dispatchEvent(new Event("focus"));
    browser.dispatchEvent(new Event("online"));
    assert.equal(calls, 1);
    finish(); await pending;
    page.dispatchEvent(new Event("visibilitychange"));
    assert.equal(calls, 2);
    finish(); await Promise.resolve();
    stop();
    browser.dispatchEvent(new Event("focus"));
    await tick();
    assert.equal(calls, 2);
    assert.ok(cleared);
  } finally {
    stop();
    if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow);
    else Reflect.deleteProperty(globalThis, "window");
    if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
});

test("branding renewal preserves tenant and mutation guards and widget conversation", () => {
  const source = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
  const business = source("../business-context.tsx");
  const renewal = business.slice(business.indexOf("const stop = startBrandingRefresh"), business.indexOf("const reloadBilling"));
  assert.match(renewal, /businessApi\.getBranding\(id\)/);
  assert.match(renewal, /cancelled \|\| snapshot !== businessesRef\.current \|\| !isCurrentBrandingResponse/);
  assert.match(renewal, /brandingVersion\.current/);
  assert.doesNotMatch(renewal, /uploadLogo|generate|setIsLoading/);
  const widget = source("../widget/widget-app.tsx");
  const handler = widget.slice(widget.indexOf("const refreshConfig"), widget.indexOf("const escape"));
  assert.match(handler, /event\.source !== window\.parent/);
  assert.match(handler, /event\.origin !== init\.hostOrigin/);
  assert.match(handler, /value\.config\?\.widget_id !== init\.widgetId/);
  assert.doesNotMatch(handler, /setMessages|sessionToken:/);
  assert.match(source("../components/tenant-logo.tsx"), /key=\{presentation\.key\}/);
});
