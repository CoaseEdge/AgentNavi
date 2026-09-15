"""只在创建 Server 时加载的 MCP SDK 运行时类型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

from .protocol import SCHEMA_VERSION


class _ExtensibleModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class ProjectOutput(_ExtensibleModel):
    id: str
    name: str
    kind: str


class SourceStateOutput(_ExtensibleModel):
    status: Literal["ready", "partial", "stale"]
    revision: str | None = None
    indexed_at: str | None = Field(default=None, alias="indexedAt")


class ContextStatsOutput(_ExtensibleModel):
    files: int
    concepts: int
    tasks: int


class ContextFileOutput(_ExtensibleModel):
    path: str
    relation: str
    language: str


class ContextNeighborOutput(_ExtensibleModel):
    direction: str
    relation: str
    id: str
    key: str
    label: str
    confidence: float
    source: str


class ContextConceptOutput(_ExtensibleModel):
    id: str
    key: str
    label: str
    confidence: float
    source: str
    neighbors: list[ContextNeighborOutput]
    files: list[ContextFileOutput]


class ContextTaskOutput(_ExtensibleModel):
    id: str
    title: str
    status: str
    summary: str
    created_at: str


class ContextDataOutput(_ExtensibleModel):
    stats: ContextStatsOutput
    concepts: list[ContextConceptOutput]
    files: list[ContextFileOutput]
    tasks: list[ContextTaskOutput]


class WarningOutput(_ExtensibleModel):
    code: str
    message: str
    evidence: list[dict[str, Any]]


class ContextViewOutput(_ExtensibleModel):
    """用于 SDK tool discovery 的成功 Context envelope schema。

    S01 的标准库 DTO 仍是 wire 真相与严格隐私边界；此模型只把同一顶层合同
    暴露给 MCP SDK。SDK 2.0 会错误地对 ``isError`` 结果也执行成功 schema
    校验，因此 before-validator 只识别已经由 Error DTO 生成的公开错误，并为
    该次内部校验提供无敏感信息的替身。SDK 不会用替身改写实际 wire 结果。
    """

    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["context"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: ContextDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if (
            isinstance(value, Mapping)
            and "schemaVersion" not in value
            and set(value) == {"code", "message", "retryable", "details"}
            and value.get("code")
            in {
                "PROJECT_REQUIRED",
                "PROJECT_NOT_FOUND",
                "INVALID_ARGUMENT",
                "INTERNAL_ERROR",
            }
            and isinstance(value.get("message"), str)
            and isinstance(value.get("retryable"), bool)
            and isinstance(value.get("details"), Mapping)
        ):
            return {
                "schemaVersion": SCHEMA_VERSION,
                "view": "context",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "stats": {"files": 0, "concepts": 0, "tasks": 0},
                    "concepts": [],
                    "files": [],
                    "tasks": [],
                },
                "warnings": [],
            }
        return value


CONTEXT_TOOL_RESULT = Annotated[CallToolResult, ContextViewOutput]

# MCPServer 会在调用函数之前按类型注解验证输入。这里用 Any 接住原始值，
# 保证所有错误都能进入 AgentNavi 的公开错误边界；WithJsonSchema 只负责让
# tools/list 继续发布精确的 string / const 合同，不依赖 SDK 私有实现。
CONTEXT_VIEW_INPUT = Annotated[
    Any,
    WithJsonSchema({"type": "string", "const": "context"}),
]
REQUIRED_TEXT_INPUT = Annotated[
    Any,
    WithJsonSchema({"type": "string", "minLength": 1}),
]
OPTIONAL_TEXT_INPUT = Annotated[
    Any,
    WithJsonSchema(
        {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "default": None,
        }
    ),
]


def context_tool_annotations() -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


__all__ = [
    "CONTEXT_TOOL_RESULT",
    "CONTEXT_VIEW_INPUT",
    "OPTIONAL_TEXT_INPUT",
    "REQUIRED_TEXT_INPUT",
    "ContextViewOutput",
    "context_tool_annotations",
]
