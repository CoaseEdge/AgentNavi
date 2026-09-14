import { App } from "@modelcontextprotocol/ext-apps";
import { parseContextView, parseTaskQuery } from "./protocol";
import { AgentNaviShell } from "./shell";
import "./styles.css";

const shell = new AgentNaviShell();
const app = new App(
  { name: "AgentNavi ContextMap", version: "0.3.0" },
  {},
  { autoResize: true, allowUnsafeEval: false },
);

// One-shot notifications can arrive during initialization. Register every listener first.
app.addEventListener("toolinput", ({ arguments: toolArguments }) => {
  shell.setQuery(parseTaskQuery(toolArguments));
});

app.addEventListener("toolresult", (result) => {
  const view = parseContextView(result.structuredContent);
  if (!view) {
    shell.setConnection(result.isError ? "查询失败" : "数据不兼容", false);
    return;
  }
  shell.render(view);
  shell.setConnection("已连接", true);
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
    shell.setConnection("无法连接 Host", false);
  });
