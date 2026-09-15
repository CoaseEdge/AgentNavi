"""AgentNavi MCP Server 的延迟加载入口。

本模块可在未安装 MCP extra 时导入；只有真正创建或运行 Server 时才加载 SDK。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..config import Settings
from ..database import ensure_database
from .adapters.architecture import architecture_text, architecture_view
from .adapters.context import context_text, context_view
from .adapters.flow import flow_text, flow_view
from .adapters.impact import impact_text, impact_to_view
from .adapters.history import history_text, history_view
from .adapters.repo_overview import repo_overview_text, repo_overview_view
from .adapters.repo_tour import repo_tour_text, repo_tour_view
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


async def _complete_required_tool_arguments(ctx: Any, call_next: Any) -> Any:
    """在 SDK schema 校验前补 None，让公开参数边界处理缺失字段。"""

    if ctx.method != "tools/call" or not isinstance(ctx.params, Mapping):
        return await call_next(ctx)
    params = dict(ctx.params)
    name = params.get("name")
    raw_arguments = params.get("arguments")
    if name not in {"agentnavi_context", "agentnavi_impact", "agentnavi_history", "agentnavi_visualize"}:
        return await call_next(ctx)
    arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
    if name == "agentnavi_history" and not set(arguments) <= {
        "query", "task_id", "mode", "project_id", "workspace"
    }:
        # SDK 参数校验之前把未知字段折叠为一个公开可处理的无效 mode，
        # 避免 ValidationError 回显原始输入。
        arguments = {"mode": "__invalid__", "query": None}
    if name != "agentnavi_impact":
        arguments.setdefault("query", None)
    else:
        arguments.setdefault("selector", None)
    if name == "agentnavi_visualize":
        arguments.setdefault("view", None)
    params["arguments"] = arguments
    return await call_next(replace(ctx, params=params))


def create_server(*, home: str | Path | None = None) -> Any:
    """创建注册了只读 reasoning、presentation tool 与 App 资源的 Server。"""

    from mcp.server import MCPServer
    from mcp.server.apps import Apps, ResourceCsp
    from mcp.types import CallToolResult, TextContent

    from .runtime import (
        CONTEXT_TOOL_RESULT,
        IMPACT_TOOL_RESULT,
        HISTORY_MODE_INPUT,
        HISTORY_INPUT_SCHEMA,
        HISTORY_OPTIONAL_TEXT_INPUT,
        HISTORY_TOOL_RESULT,
        OPTIONAL_TEXT_INPUT,
        REQUIRED_TEXT_INPUT,
        VISUALIZE_TOOL_RESULT,
        VISUALIZE_INPUT_SCHEMA,
        VISUALIZE_VIEW_INPUT,
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

    def call_impact(
        selector: Any,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成只读、有界且可追溯的影响分析。"""

        try:
            checked_selector = _validated_text(selector, "selector", required=True)
            checked_project_id = _validated_text(project_id, "project_id", required=False)
            checked_workspace = _validated_text(workspace, "workspace", required=False)
            assert checked_selector is not None
            project = resolve_project(database, project_id=checked_project_id, workspace=checked_workspace)
            from ..impact_view import impact_view_data

            try:
                core_data = impact_view_data(database, project, checked_selector)
            except (LookupError, ValueError):
                raise AgentNaviMCPError(
                    "INVALID_ARGUMENT", details={"field": "selector"}
                ) from None
            structured = impact_to_view(core_data)
            text = impact_text(core_data)
            return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent=structured.to_dict())
        except Exception as exc:
            return error_result(exc)

    def agentnavi_impact(
        selector: Any,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """分析一个真实文件或概念的有界影响。"""

        return call_impact(selector, project_id, workspace)

    def call_history(
        query: Any = None,
        task_id: Any = None,
        mode: Any = "timeline",
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成有界的 Task Timeline 与 Project Story。"""

        try:
            checked_query = _validated_text(query, "query", required=False)
            checked_task_id = _validated_text(task_id, "task_id", required=False)
            checked_project_id = _validated_text(project_id, "project_id", required=False)
            checked_workspace = _validated_text(workspace, "workspace", required=False)
            if mode not in {"timeline", "story"}:
                raise AgentNaviMCPError("INVALID_ARGUMENT", details={"field": "mode"})
            project = resolve_project(database, project_id=checked_project_id, workspace=checked_workspace)
            from ..history_view import history_view_data

            core_data = history_view_data(
                database, project, checked_query or "", task_id=checked_task_id,
                selected_mode=mode,
            )
            structured = history_view(core_data)
            text = history_text(core_data)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=structured.to_dict(),
            )
        except Exception as exc:
            return error_result(exc)

    def agentnavi_history(
        query: Any = None,
        task_id: Any = None,
        mode: Any = "timeline",
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """查询任务时间线、任务详情与项目事实故事。"""

        return call_history(query, task_id, mode, project_id, workspace)

    def agentnavi_visualize(
        view: Any,
        query: Any = None,
        project_id: Any = None,
        workspace: Any = None,
    ) -> Any:
        """生成 MCP App 与无 UI Host 都可消费的只读 VLA 结果。"""

        try:
            if view not in {"context", "repo-overview", "repo-tour", "architecture", "flow", "impact", "history"}:
                raise AgentNaviMCPError(
                    "INVALID_ARGUMENT", details={"field": "view"}
                )
            if view == "context":
                return call_context(query, project_id, workspace)

            checked_query = _validated_text(query, "query", required=False)
            if view == "impact" and checked_query is None:
                raise AgentNaviMCPError("INVALID_ARGUMENT", details={"field": "query"})
            checked_project_id = _validated_text(
                project_id, "project_id", required=False
            )
            checked_workspace = _validated_text(
                workspace, "workspace", required=False
            )
            project = resolve_project(
                database,
                project_id=checked_project_id,
                workspace=checked_workspace,
            )
            if view == "impact":
                assert checked_query is not None
                return call_impact(checked_query, project_id, workspace)
            if view == "history":
                return call_history(checked_query, None, "timeline", project_id, workspace)
            if view == "repo-overview":
                from ..repository_views import repository_overview_data

                core_data = repository_overview_data(database, project)
                structured = repo_overview_view(core_data)
                text = repo_overview_text(core_data)
            elif view == "repo-tour":
                from ..repository_views import repository_tour_data

                core_data = repository_tour_data(database, project)
                structured = repo_tour_view(core_data)
                text = repo_tour_text(core_data)
            elif view == "architecture":
                from ..repository_views import repository_architecture_data

                core_data = repository_architecture_data(database, project)
                structured = architecture_view(core_data)
                text = architecture_text(core_data)
            else:
                from ..repository_views import repository_flow_data

                core_data = repository_flow_data(database, project, checked_query)
                structured = flow_view(core_data)
                text = flow_text(core_data)
            return CallToolResult(
                content=[TextContent(type="text", text=text)],
                structuredContent=structured.to_dict(),
            )
        except Exception as exc:
            return error_result(exc)

    for tool in (agentnavi_context, agentnavi_impact, agentnavi_history, agentnavi_visualize):
        tool.__annotations__["project_id"] = OPTIONAL_TEXT_INPUT
        tool.__annotations__["workspace"] = OPTIONAL_TEXT_INPUT
    agentnavi_context.__annotations__["query"] = REQUIRED_TEXT_INPUT
    agentnavi_context.__annotations__["return"] = CONTEXT_TOOL_RESULT
    agentnavi_impact.__annotations__["selector"] = REQUIRED_TEXT_INPUT
    agentnavi_impact.__annotations__["return"] = IMPACT_TOOL_RESULT
    agentnavi_history.__annotations__["query"] = HISTORY_OPTIONAL_TEXT_INPUT
    agentnavi_history.__annotations__["task_id"] = HISTORY_OPTIONAL_TEXT_INPUT
    agentnavi_history.__annotations__["mode"] = HISTORY_MODE_INPUT
    agentnavi_history.__annotations__["return"] = HISTORY_TOOL_RESULT
    agentnavi_visualize.__annotations__["query"] = OPTIONAL_TEXT_INPUT
    agentnavi_visualize.__annotations__["view"] = VISUALIZE_VIEW_INPUT
    agentnavi_visualize.__annotations__["return"] = VISUALIZE_TOOL_RESULT

    apps = Apps()
    apps.add_html_resource(
        APP_URI,
        files("agentnavi.mcp.resources")
        .joinpath("agentnavi-app.html")
        .read_text(encoding="utf-8"),
        name="AgentNavi ContextMap",
        title="AgentNavi ContextMap",
        description="项目概览、仓库导览、架构、任务流、Context、Impact 与 History 的只读视图。",
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
        description="展示只读项目概览、仓库导览、架构、任务流、ContextMap、Impact 或 History。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )(agentnavi_visualize)

    server = MCPServer(
        "AgentNavi",
        extensions=[apps],
        middleware=[_complete_required_tool_arguments],
    )

    # SDK v2 通过 Annotated[CallToolResult, ReturnType] 同时保留手工生成的
    # content，并为 structuredContent 发布和执行 outputSchema 校验。
    server.add_tool(
        agentnavi_context,
        name="agentnavi_context",
        description="按任务查询相关概念、候选文件与历史任务。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )
    server.add_tool(
        agentnavi_impact,
        name="agentnavi_impact",
        description="分析一个真实文件或概念的入向、出向、语义、历史、测试与风险。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )
    server.add_tool(
        agentnavi_history,
        name="agentnavi_history",
        description="查询有界任务时间线、任务详情与按 L3 关系聚合的项目故事。",
        annotations=context_tool_annotations(),
        structured_output=True,
    )

    history_tool = server._tool_manager.get_tool("agentnavi_history")
    if history_tool is None:  # pragma: no cover - SDK 注册失败的防御分支
        raise RuntimeError("agentnavi_history 注册失败")
    history_tool.parameters = HISTORY_INPUT_SCHEMA

    # FastMCP 2.x 从单个函数参数生成扁平 schema，无法表达 query 是否必填取决于
    # view。保留 handler 的公开错误防线，同时用公开 tools/list 合同发布判别联合。
    visualize_tool = server._tool_manager.get_tool("agentnavi_visualize")
    if visualize_tool is None:  # pragma: no cover - SDK 注册失败的防御分支
        raise RuntimeError("agentnavi_visualize 注册失败")
    visualize_tool.parameters = VISUALIZE_INPUT_SCHEMA

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
