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

if (violations.length > 0) {
  process.stderr.write(`${violations.join("\n")}\n`);
  process.exitCode = 1;
}
