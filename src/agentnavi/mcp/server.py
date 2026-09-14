"""AgentNavi MCP Server 的延迟加载入口。

本模块可在未安装 MCP extra 时导入；只有真正创建或运行 Server 时才加载 SDK。
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import ensure_database
from .adapters.context import context_text, context_view
from .errors import AgentNaviMCPError, to_public_error
from .project_resolver import resolve_project
from .protocol import Error


APP_URI = "ui://agentnavi/app.html"
APP_MIME_TYPE = "text/html;profile=mcp-app"
APP_RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
            "frameDomains": [],
            "baseUriDomains": [],
        },
    }
}
APP_TOOL_META = {"ui": {"resourceUri": APP_URI}}


def _format_public_error(error: Error) -> str:
    retry = (
        "可在修正输入后重试。"
        if error.retryable
        else "当前请求不可直接重试，请检查服务状态。"
    )
    return f"[AgentNavi 错误]\n{error.code}：{error.message}\n{retry}"


def _validated_text(value: Any, field: str, *, required: bool) -> str | None:
    """在 SDK handler 内验证文本，避免外层 ValidationError 泄漏原始输入。"""

    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise AgentNaviMCPError("INVALID_ARGUMENT", details={"field": field})
    if not value.strip() or len(value) > 4096:
        raise AgentNaviMCPError("INVALID_ARGUMENT", details={"field": field})
    return value


def create_server(*, home: str | Path | None = None) -> Any:
    """创建注册了只读 reasoning、presentation tool 与 App 资源的 Server。"""

    from mcp.server import MCPServer
    from mcp.server.apps import Apps, ResourceCsp
    from mcp.types import CallToolResult, TextContent

    from .runtime import (
        CONTEXT_TOOL_RESULT,
        CONTEXT_VIEW_INPUT,
        OPTIONAL_TEXT_INPUT,
        REQUIRED_TEXT_INPUT,
        context_tool_annotations,
    )

    database = ensure_database(Settings.load(home))

    def error_result(exc: BaseException) -> Any:
        error = to_public_error(exc)
        return CallToolResult(
            content=[TextContent(type="text", text=_format_public_error(error))],
            structuredContent=error.to_dict(),
            isError=True,
        )

    def call_context(
        query: Any,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成模型文本与未来 UI 使用的独立 VLA Context 投影。"""

        try:
            checked_query = _validated_text(query, "query", required=True)
            checked_project_id = _validated_text(
                project_id, "project_id", required=False
            )
            checked_workspace = _validated_text(
                workspace, "workspace", required=False
            )
            assert checked_query is not None
            project = resolve_project(
                database,
                project_id=checked_project_id,
                workspace=checked_workspace,
            )
            from ..query import context_data

            core_data = context_data(database, project, checked_query)
            view = context_view(core_data)
            text = context_text(core_data)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=view.to_dict(),
            )
        except Exception as exc:
            return error_result(exc)

    def agentnavi_context(
        query: Any,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成只供模型推理使用的 Context 结果。"""

        return call_context(query, project_id, workspace)

    def agentnavi_visualize(
        view: Any,
        query: Any,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成 MCP App 与无 UI Host 都可消费的 Context 结果。"""

        try:
            if view != "context":
                raise AgentNaviMCPError(
                    "INVALID_ARGUMENT", details={"field": "view"}
                )
            return call_context(query, project_id, workspace)
        except Exception as exc:
            return error_result(exc)

    for tool in (agentnavi_context, agentnavi_visualize):
        tool.__annotations__["query"] = REQUIRED_TEXT_INPUT
        tool.__annotations__["project_id"] = OPTIONAL_TEXT_INPUT
        tool.__annotations__["workspace"] = OPTIONAL_TEXT_INPUT
        tool.__annotations__["return"] = CONTEXT_TOOL_RESULT
    agentnavi_visualize.__annotations__["view"] = CONTEXT_VIEW_INPUT

    apps = Apps()
    apps.add_html_resource(
        APP_URI,
        files("agentnavi.mcp.resources")
        .joinpath("agentnavi-app.html")
        .read_text(encoding="utf-8"),
        name="AgentNavi ContextMap",
        title="AgentNavi ContextMap",
        description="任务到概念与文件的只读项目导航图。",
        csp=ResourceCsp(
            connectDomains=[],
            resourceDomains=[],
            frameDomains=[],
            baseUriDomains=[],
        ),
        prefers_border=True,
    )
    apps.tool(
        resource_uri=APP_URI,
        name="agentnavi_visualize",
        description="用只读 ContextMap 展示任务、概念与候选文件。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )(agentnavi_visualize)

    server = MCPServer("AgentNavi", extensions=[apps])

    # SDK v2 通过 Annotated[CallToolResult, ReturnType] 同时保留手工生成的
    # content，并为 structuredContent 发布和执行 outputSchema 校验。
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


__all__ = [
    "APP_MIME_TYPE",
    "APP_RESOURCE_META",
    "APP_TOOL_META",
    "APP_URI",
    "create_server",
    "run_stdio",
]
