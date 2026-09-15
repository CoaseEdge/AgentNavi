"""AgentNavi MCP Server 的延迟加载入口。

本模块可在未安装 MCP extra 时导入；只有真正创建或运行 Server 时才加载 SDK。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import ensure_database
from .adapters.context import context_text, context_view
from .errors import to_public_error
from .project_resolver import resolve_project
from .protocol import Error


def _format_public_error(error: Error) -> str:
    retry = (
        "可在修正输入后重试。"
        if error.retryable
        else "当前请求不可直接重试，请检查服务状态。"
    )
    return f"[AgentNavi 错误]\n{error.code}：{error.message}\n{retry}"


def create_server(*, home: str | Path | None = None) -> Any:
    """创建注册了只读 Context reasoning tool 的 MCP Server。"""

    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent

    from .runtime import CONTEXT_TOOL_RESULT, context_tool_annotations

    database = ensure_database(Settings.load(home))
    server = MCPServer("AgentNavi")

    def agentnavi_context(
        query: str,
        project_id: str | None = None,
        workspace: str | None = None,
    ) -> Any:
        """生成模型文本与未来 UI 使用的独立 VLA Context 投影。"""

        try:
            project = resolve_project(
                database,
                project_id=project_id,
                workspace=workspace,
            )
            from ..query import context_data

            core_data = context_data(database, project, query)
            view = context_view(core_data)
            text = context_text(core_data)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=view.to_dict(),
            )
        except Exception as exc:
            error = to_public_error(exc)
            return CallToolResult(
                content=[TextContent(type="text", text=_format_public_error(error))],
                structuredContent=error.to_dict(),
                isError=True,
            )

    # SDK v2 通过 Annotated[CallToolResult, ReturnType] 同时保留手工生成的
    # content，并为 structuredContent 发布和执行 outputSchema 校验。
    agentnavi_context.__annotations__["return"] = CONTEXT_TOOL_RESULT
    server.add_tool(
        agentnavi_context,
        name="agentnavi_context",
        description="按任务查询相关概念、候选文件与历史任务。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )

    return server


def run_stdio(*, home: str | Path | None = None) -> None:
    """在 stdout 专用的 stdio 协议通道上运行 Server。"""

    create_server(home=home).run("stdio")


__all__ = ["create_server", "run_stdio"]
