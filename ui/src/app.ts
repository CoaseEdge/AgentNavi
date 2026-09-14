import { App } from "@modelcontextprotocol/ext-apps";
import { applyToolInput, applyToolResult } from "./bridge.js";
import { AgentNaviShell } from "./shell.js";
import "./styles.css";

const shell = new AgentNaviShell();
const app = new App(
  { name: "AgentNavi ContextMap", version: "0.3.0" },
  {},
  { autoResize: true, allowUnsafeEval: false },
);

// One-shot notifications can arrive during initialization. Register every listener first.
app.addEventListener("toolinput", ({ arguments: toolArguments }) => {
  applyToolInput(shell, toolArguments);
});

app.addEventListener("toolresult", (result) => {
  applyToolResult(shell, result);
});

app.addEventListener("hostcontextchanged", (context) => {
  shell.setTheme(context.theme);
});

void app
  .connect()
  .then(() => {
    shell.setTheme(app.getHostContext()?.theme);
    shell.setConnection("等待结果", true);
  })
  .catch(() => {
    shell.showError("无法连接 MCP Apps Host。");
  });
