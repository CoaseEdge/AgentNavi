import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const css = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");

function luminance(hex) {
  const channels = hex.match(/[0-9a-f]{2}/gi).map((part) => Number.parseInt(part, 16) / 255);
  const linear = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(foreground, background) {
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

test("small text color tokens meet WCAG AA contrast", () => {
  assert.match(css, /--muted:\s*#4e5a53/);
  assert.match(css, /--amber-text:\s*#72500d/);
  assert.ok(contrast("#4e5a53", "#e7e2d6") >= 4.5);
  assert.ok(contrast("#4e5a53", "#d9d5ca") >= 4.5);
  assert.ok(contrast("#abb5ae", "#101512") >= 4.5);
  assert.ok(contrast("#72500d", "#e7e2d6") >= 4.5);
  assert.ok(contrast("#72500d", "#d9d5ca") >= 4.5);
  assert.ok(contrast("#d6a85f", "#101512") >= 4.5);
});

test("390px layout does not hide connection or error status", () => {
  const narrow = css.slice(css.indexOf("@media (max-width: 460px)"));
  assert.doesNotMatch(narrow, /connection-state\s+span/);
  assert.doesNotMatch(narrow, /error-panel[^}]*display:\s*none/);
});
