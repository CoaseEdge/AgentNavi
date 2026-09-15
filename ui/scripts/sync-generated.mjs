import { copyFile, mkdir } from "node:fs/promises";

const target = new URL("../../src/agentnavi/mcp/resources/agentnavi-app.html", import.meta.url);
await mkdir(new URL("../../src/agentnavi/mcp/resources/", import.meta.url), {
  recursive: true,
});
await copyFile(new URL("../dist/index.html", import.meta.url), target);
