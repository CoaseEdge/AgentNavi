import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const sourceRoot = fileURLToPath(new URL("../src/", import.meta.url));
const forbidden = [
  ["dynamic HTML", /\binnerHTML\b/],
  ["code evaluation", /\beval\s*\(|new\s+Function\s*\(/],
  ["network fetch", /\bfetch\s*\(/],
  ["XML HTTP", /\bXMLHttpRequest\b/],
  ["WebSocket", /\bWebSocket\b/],
  ["public URL", /https?:\/\//i],
  ["localhost", /\blocalhost\b|127\.0\.0\.1|\[::1\]/i],
];

async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? files(path) : [path];
    }),
  );
  return nested.flat();
}

const violations = [];
for (const path of await files(sourceRoot)) {
  const content = await readFile(path, "utf8");
  for (const [label, pattern] of forbidden) {
    if (pattern.test(content)) violations.push(`${path}: ${label}`);
  }
}

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
for (const directive of ["default-src 'none'", "connect-src 'none'", "base-uri 'none'", "form-action 'none'"]) {
  if (!html.includes(directive)) violations.push(`index.html: missing CSP ${directive}`);
}

const generated = await readFile(
  new URL("../../src/agentnavi/mcp/resources/agentnavi-app.html", import.meta.url),
  "utf8",
);
const upstreamZodProbe = "try{return Function(``),!0}catch{return!1}";
const expectedProbeHash = "6eb16ac0aef2027dec066a89cd64fff5bf210c19ef8c3fad1140232c546cd09d";
const reviewedProbes = generated.match(/try\{return Function\(``\),!0\}catch\{return!1\}/g) ?? [];
const functionCalls = generated.match(/(?<![\w.])Function\s*\(/g) ?? [];
const evalCalls = generated.match(/(?<![\w.])eval\s*\(/g) ?? [];
const newFunctionCalls = generated.match(/\bnew\s+Function\s*\(/g) ?? [];
const probeHash = createHash("sha256").update(reviewedProbes[0] ?? "").digest("hex");
const earlyJitless = "globalThis.__zod_globalConfig={jitless:true}";
if (
  reviewedProbes.length !== 1 ||
  functionCalls.length !== 1 ||
  evalCalls.length !== 0 ||
  newFunctionCalls.length !== 0 ||
  probeHash !== expectedProbeHash
) {
  violations.push(
    "generated app: dynamic-code footprint differs from the single reviewed Zod CSP probe",
  );
}
if (
  generated.indexOf(earlyJitless) < 0 ||
  generated.indexOf(earlyJitless) > generated.indexOf(upstreamZodProbe)
) {
  violations.push("generated app: Zod jitless config must run before the upstream probe is loaded");
}
if (!generated.includes("jitless:!0") && !generated.includes("jitless:true")) {
  violations.push("generated app: missing explicit Zod jitless runtime configuration");
}
if (!generated.includes("allowUnsafeEval:!1") && !generated.includes("allowUnsafeEval:false")) {
  violations.push("generated app: missing ext-apps allowUnsafeEval=false runtime gate");
}

if (violations.length > 0) {
  process.stderr.write(`${violations.join("\n")}\n`);
  process.exitCode = 1;
}
