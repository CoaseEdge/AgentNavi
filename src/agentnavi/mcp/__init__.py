"""AgentNavi 的可选 MCP 接口。

协议 DTO 本身只依赖 Python 标准库；MCP SDK 由后续 Server 层延迟导入。
"""

from .protocol import (
    SCHEMA_VERSION,
    SUPPORTED_VIEWS,
    AgentNaviView,
    EntityRef,
    Envelope,
    Error,
    Evidence,
    GraphEdge,
    Project,
    SourceState,
    Warning,
)

__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_VIEWS",
    "AgentNaviView",
    "EntityRef",
    "Envelope",
    "Error",
    "Evidence",
    "GraphEdge",
    "Project",
    "SourceState",
    "Warning",
]
