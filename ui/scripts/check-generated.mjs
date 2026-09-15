import { readFile } from "node:fs/promises";

const built = await readFile(new URL("../dist/index.html", import.meta.url));
const tracked = await readFile(
  new URL("../../src/agentnavi/mcp/resources/agentnavi-app.html", import.meta.url),
);

if (!built.equals(tracked)) {
  process.stderr.write(
    "agentnavi-app.html 与 UI 源码不一致；请运行 `npm run generate --prefix ui`。\n",
  );
  process.exitCode = 1;
}
