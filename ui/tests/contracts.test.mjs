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
  assert.match(css, /--danger-text:\s*#8f342c/);
  assert.ok(contrast("#4e5a53", "#e7e2d6") >= 4.5);
  assert.ok(contrast("#4e5a53", "#d9d5ca") >= 4.5);
  assert.ok(contrast("#abb5ae", "#101512") >= 4.5);
  assert.ok(contrast("#72500d", "#e7e2d6") >= 4.5);
  assert.ok(contrast("#72500d", "#d9d5ca") >= 4.5);
  assert.ok(contrast("#d6a85f", "#101512") >= 4.5);
  assert.ok(contrast("#8f342c", "#e7e2d6") >= 4.5);
  assert.ok(contrast("#ef8d83", "#101512") >= 4.5);
});

test("dark rail title and project facts use an independent AA foreground", () => {
  assert.match(css, /--rail-background:\s*#101512/);
  assert.match(css, /--rail-foreground:\s*#e7e2d6/);
  assert.match(css, /:root\[data-theme="dark"\][^{]*\{[^}]*--rail-background:\s*#0a0e0c/);
  assert.match(css, /:root\[data-theme="dark"\][^{]*\{[^}]*--rail-foreground:\s*#f1ede4/);
  assert.match(css, /\.status-rail\s+h1,[\s\S]*?\.project-facts\s+dd\s*\{[^}]*color:\s*var\(--rail-foreground\)/);
  assert.ok(contrast("#e7e2d6", "#101512") >= 4.5);
  assert.ok(contrast("#f1ede4", "#0a0e0c") >= 4.5);
  assert.ok(contrast("#bcc6bf", "#0a0e0c") >= 4.5);
});

test("390px layout does not hide connection or error status", () => {
  const narrow = css.slice(css.indexOf("@media (max-width: 460px)"));
  assert.doesNotMatch(narrow, /connection-state\s+span/);
  assert.doesNotMatch(narrow, /error-panel[^}]*display:\s*none/);
});

test("390px dark layout keeps rail title and facts on the AA token", () => {
  const narrow = css.slice(css.indexOf("@media (max-width: 760px)"));
  assert.doesNotMatch(narrow, /(?:status-rail\s+h1|project-facts\s+dd)[^}]*color:/);
  assert.ok(contrast("#f1ede4", "#0a0e0c") >= 4.5);
});
