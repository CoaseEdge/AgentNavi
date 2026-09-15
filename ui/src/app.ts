import { App } from "@modelcontextprotocol/ext-apps";
import { z } from "zod/v4";
import { AgentNaviAppLifecycle } from "./bridge.js";
import { AgentNaviShell } from "./shell.js";
import "./styles.css";

// ext-apps 使用 Zod 校验 bridge 消息。显式关闭其 JIT，保证严格 CSP 下不会
// 执行 Function constructor；生成物中的单个 capability probe 另有 hash 合同。
z.config({ jitless: true });

const shell = new AgentNaviShell();
const lifecycle = new AgentNaviAppLifecycle(shell);
const app = new App(
  { name: "AgentNavi ContextMap", version: "0.3.0" },
  {},
  { autoResize: true, allowUnsafeEval: false },
);

shell.setSemanticReviewAction((reviewId, decision, projectId) => {
  void app.callServerTool({
    name: "agentnavi_review_decide",
    arguments: { review_id: reviewId, decision, project_id: projectId },
  }).then(() => app.callServerTool({
    name: "agentnavi_semantic_review",
    arguments: { project_id: projectId },
  }).then((result) => lifecycle.handleToolResult(result)))
    .catch(() => shell.showError("语义决定未能持久化，请稍后重试。"));
});

// One-shot notifications can arrive during initialization. Register every listener first.
app.addEventListener("toolinput", ({ arguments: toolArguments }) => {
  lifecycle.handleToolInput(toolArguments);
});

app.addEventListener("toolresult", (result) => {
  lifecycle.handleToolResult(result);
});

app.addEventListener("hostcontextchanged", (context) => {
  shell.setTheme(context.theme);
});

void app
  .connect()
  .then(() => {
    lifecycle.handleConnected(app.getHostContext()?.theme);
  })
  .catch(() => {
    lifecycle.handleConnectionFailure();
  });
