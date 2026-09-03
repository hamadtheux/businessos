import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const loaderUrl = new URL("../dist/public/widget-loader.js", import.meta.url);
const source = await readFile(loaderUrl, "utf8");

assert.doesNotMatch(
  source,
  /(^|;)\s*import(?:\s|\{|\*|["'])/m,
  "widget-loader.js must not contain static imports",
);
assert.doesNotMatch(
  source,
  /\bimport\s*\(/,
  "widget-loader.js must not contain dynamic imports",
);
assert.doesNotMatch(
  source,
  /(^|;)\s*export(?:\s|\{)/m,
  "widget-loader.js must not contain exports",
);
assert.doesNotMatch(
  source,
  /assets\/config-/,
  "widget-loader.js must not depend on a config chunk",
);
assert.match(source, /widget\.html/, "widget-loader.js must load widget.html");
assert.match(
  source,
  /\/api\/v1\/public\/widgets/,
  "widget-loader.js must retain the public widget API routes",
);

assert.doesNotThrow(
  () => new vm.Script(source, { filename: "widget-loader.js" }),
  "widget-loader.js must parse as a classic JavaScript script",
);

console.log("Verified self-contained classic widget-loader.js");
