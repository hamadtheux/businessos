import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  mountPublicLandingChatbot,
  PUBLIC_CHATBOT_LOADER_URL,
  PUBLIC_CHATBOT_WIDGET_ID,
} from "../features/public/public-chatbot.ts";

type Listener = () => void;

class FakeElement {
  async = false;
  removed = false;
  src = "";
  readonly tagName: string;
  readonly attributes = new Map<string, string>();
  readonly listeners = new Map<string, Listener[]>();

  constructor(tagName: string) {
    this.tagName = tagName;
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type: string) {
    for (const listener of this.listeners.get(type) ?? []) listener();
  }

  remove() {
    this.removed = true;
  }
}

class FakeDocument {
  readonly elements: FakeElement[] = [];
  readonly body = {
    appendChild: (element: FakeElement) => {
      this.elements.push(element);
      return element;
    },
  };

  createElement(tagName: string) {
    return new FakeElement(tagName);
  }

  querySelector(selector: string) {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string) {
    return this.elements.filter((element) => {
      if (element.removed) return false;
      const isLoader =
        element.tagName === "script" &&
        element.src === PUBLIC_CHATBOT_LOADER_URL &&
        element.attributes.get("data-widget-id") ===
          PUBLIC_CHATBOT_WIDGET_ID;
      const isHost =
        element.attributes.get("data-aibos-widget-host") ===
        PUBLIC_CHATBOT_WIDGET_ID;

      return (
        (selector.includes("script[") && isLoader) ||
        (selector.includes("data-aibos-widget-host") && isHost)
      );
    });
  }

  appendWidgetHost() {
    const host = new FakeElement("div");
    host.setAttribute("data-aibos-widget-host", PUBLIC_CHATBOT_WIDGET_ID);
    this.elements.push(host);
    return host;
  }
}

test("public landing embed uses the exact production loader and opaque widget ID", async () => {
  const embed = await readFile(
    new URL("../features/public/public-chatbot.ts", import.meta.url),
    "utf8",
  );
  const publicPages = await readFile(
    new URL("../features/public/public-pages.tsx", import.meta.url),
    "utf8",
  );
  const app = await readFile(new URL("../App.tsx", import.meta.url), "utf8");

  assert.equal(
    PUBLIC_CHATBOT_LOADER_URL,
    "https://9dbrain.com/widget-loader.js",
  );
  assert.equal(
    PUBLIC_CHATBOT_WIDGET_ID,
    "7FjvNAaibDpoFqAVxJW5gVss6yWIUIm5eK3CXlsLbAo",
  );
  assert.doesNotMatch(embed, /localhost|127\.0\.0\.1/);
  for (const forbidden of ["businessId", "tenantId", "apiKey", "authToken"]) {
    assert.equal(embed.includes(forbidden), false, forbidden);
  }
  assert.match(
    publicPages,
    /function PublicHomePage\(\)[\s\S]{0,160}mountPublicLandingChatbot\(\)/,
  );
  assert.equal(app.includes(PUBLIC_CHATBOT_LOADER_URL), false);
  assert.equal(app.includes(PUBLIC_CHATBOT_WIDGET_ID), false);
  assert.match(app, /path="\/" component=\{PublicHomePage\}/);
  assert.match(app, /path="\/dashboard" component=\{BusinessDashboardPage\}/);
});

test("repeated public mounts share one loader and final cleanup removes widget artifacts", () => {
  const fakeDocument = new FakeDocument();
  const targetDocument = fakeDocument as unknown as Document;

  const cleanupFirst = mountPublicLandingChatbot(targetDocument);
  const cleanupSecond = mountPublicLandingChatbot(targetDocument);
  const scripts = fakeDocument.querySelectorAll("script[");

  assert.equal(scripts.length, 1);
  assert.equal(scripts[0].src, PUBLIC_CHATBOT_LOADER_URL);
  assert.equal(
    scripts[0].attributes.get("data-widget-id"),
    PUBLIC_CHATBOT_WIDGET_ID,
  );
  assert.equal(scripts[0].async, true);

  const host = fakeDocument.appendWidgetHost();
  cleanupFirst();
  assert.equal(scripts[0].removed, false);
  assert.equal(host.removed, false);

  cleanupSecond();
  assert.equal(scripts[0].removed, true);
  assert.equal(host.removed, true);
});

test("a stale async load cannot remove a newer active public widget", () => {
  const fakeDocument = new FakeDocument();
  const targetDocument = fakeDocument as unknown as Document;

  const cleanupOldMount = mountPublicLandingChatbot(targetDocument);
  const oldScript = fakeDocument.querySelectorAll("script[")[0];
  cleanupOldMount();

  const cleanupActiveMount = mountPublicLandingChatbot(targetDocument);
  const activeHost = fakeDocument.appendWidgetHost();
  oldScript.dispatch("load");
  assert.equal(activeHost.removed, false);

  cleanupActiveMount();
  assert.equal(activeHost.removed, true);
});
