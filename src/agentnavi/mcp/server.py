"""AgentNavi MCP Server 的延迟加载入口。

本模块可在未安装 MCP extra 时导入；只有真正创建或运行 Server 时才加载 SDK。
"""

from __future__ import annotations

from typing import Any


def create_server() -> Any:
    """创建尚未注册业务工具的 MCP Server，供后续 adapter 逐步扩展。"""

    from mcp.server import MCPServer

    return MCPServer("AgentNavi")


def run_stdio() -> None:
    """在 stdout 专用的 stdio 协议通道上运行 Server。"""

    create_server().run("stdio")


__all__ = ["create_server", "run_stdio"]
