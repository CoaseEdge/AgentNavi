import { parseContextView, parsePublicError, parseTaskQuery } from "./protocol.js";
import type { AgentNaviShell } from "./shell.js";

export interface ToolResultLike {
  structuredContent?: unknown;
  isError?: boolean;
}

export function applyToolInput(shell: AgentNaviShell, toolArguments: unknown): void {
  shell.beginRequest(parseTaskQuery(toolArguments));
}

export function applyToolResult(shell: AgentNaviShell, result: ToolResultLike): void {
  const view = parseContextView(result.structuredContent);
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
  private receivedToolActivity = false;

  constructor(private readonly shell: AgentNaviShell) {}

  handleToolInput(toolArguments: unknown): void {
    this.receivedToolActivity = true;
    applyToolInput(this.shell, toolArguments);
  }

  handleToolResult(result: ToolResultLike): void {
    this.receivedToolActivity = true;
    applyToolResult(this.shell, result);
  }

  handleConnected(theme: unknown): void {
    this.shell.setTheme(theme);
    if (!this.receivedToolActivity) {
      this.shell.setConnection("等待结果", true);
    }
  }

  handleConnectionFailure(): void {
    if (!this.receivedToolActivity) {
      this.shell.showError("无法连接 MCP Apps Host。");
    }
  }
}
