import { parseAgentNaviView, parsePublicError, parseRequestedView, parseTaskQuery } from "./protocol.js";
import type { AgentNaviShell } from "./shell.js";

export interface ToolResultLike {
  structuredContent?: unknown;
  isError?: boolean;
}

export function applyToolInput(shell: AgentNaviShell, toolArguments: unknown): void {
  const requestedView = parseRequestedView(toolArguments);
  shell.beginRequest(
    parseTaskQuery(toolArguments) ?? (
      requestedView === "repo-overview"
        ? "项目概览"
        : requestedView === "repo-tour"
          ? "仓库导览"
          : requestedView === "architecture"
            ? "系统架构"
            : requestedView === "flow"
              ? "任务主流程"
            : requestedView === "impact"
              ? "影响分析"
          : undefined
    ),
    requestedView,
  );
}

export function applyToolResult(shell: AgentNaviShell, result: ToolResultLike): void {
  const view = parseAgentNaviView(result.structuredContent);
  if (view && !result.isError) {
    shell.render(view);
    shell.setConnection("已连接", true);
    return;
  }

  const error = parsePublicError(result.structuredContent);
  if (result.isError && error) {
    shell.showError(`${error.code} · ${error.message}`);
    return;
  }
  shell.showError(result.isError ? "查询失败，未返回可显示的结果。" : "返回的数据与当前视图不兼容。");
}

export class AgentNaviAppLifecycle {
  private toolState: "idle" | "pending" | "completed" = "idle";

  constructor(private readonly shell: AgentNaviShell) {}

  handleToolInput(toolArguments: unknown): void {
    this.toolState = "pending";
    applyToolInput(this.shell, toolArguments);
  }

  handleToolResult(result: ToolResultLike): void {
    this.toolState = "completed";
    applyToolResult(this.shell, result);
  }

  handleConnected(theme: unknown): void {
    this.shell.setTheme(theme);
    if (this.toolState === "idle") {
      this.shell.setConnection("等待结果", true);
    }
  }

  handleConnectionFailure(): void {
    if (this.toolState === "completed") {
      this.shell.setConnection("Host 已断开", false);
    } else {
      this.shell.showError("无法连接 MCP Apps Host。");
    }
  }
}
