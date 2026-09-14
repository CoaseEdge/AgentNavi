"""只在创建 Server 时加载的 MCP SDK 运行时类型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, RootModel, WithJsonSchema, model_validator

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


class OverviewStatementOutput(_ExtensibleModel):
    summary: str
    evidence: list[dict[str, Any]]


class OverviewNeedOutput(_ExtensibleModel):
    problem: OverviewStatementOutput
    solution: OverviewStatementOutput


class OverviewWorkflowOutput(_ExtensibleModel):
    step: int
    title: str
    detail: str
    evidence: list[dict[str, Any]]


class OverviewModuleOutput(_ExtensibleModel):
    id: str
    name: str
    summary: str
    paths: list[str]
    layer: Literal["L2"]
    source: str
    confidence: float
    evidence: list[dict[str, Any]]


class OverviewReadingOutput(_ExtensibleModel):
    position: int
    path: str
    reason: str
    evidence: list[dict[str, Any]]


class OverviewStatsOutput(_ExtensibleModel):
    files: int
    concepts: int
    tasks: int
    documents_read: int = Field(alias="documentsRead")


class RepositoryOverviewDataOutput(_ExtensibleModel):
    purpose: OverviewStatementOutput
    need: OverviewNeedOutput
    workflow: list[OverviewWorkflowOutput]
    modules: list[OverviewModuleOutput]
    reading_order: list[OverviewReadingOutput] = Field(alias="readingOrder")
    stats: OverviewStatsOutput


_PUBLIC_ERROR_CODES = {
    "PROJECT_REQUIRED",
    "PROJECT_NOT_FOUND",
    "INVALID_ARGUMENT",
    "INTERNAL_ERROR",
}


def _is_public_error(value: Any) -> bool:
    return bool(
        isinstance(value, Mapping)
        and "schemaVersion" not in value
        and set(value) == {"code", "message", "retryable", "details"}
        and value.get("code") in _PUBLIC_ERROR_CODES
        and isinstance(value.get("message"), str)
        and isinstance(value.get("retryable"), bool)
        and isinstance(value.get("details"), Mapping)
    )


def _context_error_placeholder() -> dict[str, Any]:
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


class ContextViewOutput(_ExtensibleModel):
    """reasoning tool 专用的 Context 成功 schema。

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
        if _is_public_error(value):
            return _context_error_placeholder()
        return value


class RepositoryOverviewViewOutput(_ExtensibleModel):
    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["repo-overview"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: RepositoryOverviewDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return {
                "schemaVersion": SCHEMA_VERSION,
                "view": "repo-overview",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "purpose": {"summary": "", "evidence": []},
                    "need": {
                        "problem": {"summary": "", "evidence": []},
                        "solution": {"summary": "", "evidence": []},
                    },
                    "workflow": [],
                    "modules": [],
                    "readingOrder": [],
                    "stats": {
                        "files": 0,
                        "concepts": 0,
                        "tasks": 0,
                        "documentsRead": 0,
                    },
                },
                "warnings": [],
            }
        return value


class TourEvidenceOutput(_ExtensibleModel):
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    layer: Literal["L1", "L2", "L3"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    path: str | None = None
    line_start: int | None = Field(default=None, alias="lineStart", ge=1)
    line_end: int | None = Field(default=None, alias="lineEnd", ge=1)


class TourEntityOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    label: str = Field(min_length=1)
    path: str | None = None
    layer: Literal["L1", "L2", "L3"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[TourEvidenceOutput] = Field(min_length=1)


class TourRelationOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    source_id: str = Field(alias="sourceId", min_length=1)
    target_id: str = Field(alias="targetId", min_length=1)
    relation: str = Field(min_length=1)
    layer: Literal["L1", "L2", "L3"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[TourEvidenceOutput] = Field(min_length=1)


class TourStopOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    kind: Literal[
        "purpose", "why", "workflow", "module", "data-structure", "task",
        "file", "history", "symbol", "dependency", "test", "task-history",
        "evidence",
    ]
    title: str = Field(min_length=1)
    plain_language: str = Field(alias="plainLanguage", min_length=1)
    technical_explanation: str = Field(alias="technicalExplanation", min_length=1)
    evidence: list[TourEvidenceOutput] = Field(min_length=1)
    entity: TourEntityOutput
    relations: list[TourRelationOutput] = Field(max_length=4)


class TourTierOutput(_ExtensibleModel):
    depth: Literal["one-minute", "five-minutes", "source-deep-dive"]
    label: str = Field(min_length=1)
    stops: list[TourStopOutput] = Field(max_length=12)


class RepositoryTourDataOutput(_ExtensibleModel):
    tiers: list[TourTierOutput] = Field(min_length=3, max_length=3)
    stats: OverviewStatsOutput


class RepositoryTourViewOutput(_ExtensibleModel):
    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["repo-tour"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: RepositoryTourDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return {
                "schemaVersion": SCHEMA_VERSION,
                "view": "repo-tour",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "tiers": [
                        {"depth": depth, "label": label, "stops": []}
                        for depth, label in (
                            ("one-minute", "1 分钟"),
                            ("five-minutes", "5 分钟"),
                            ("source-deep-dive", "深入源码"),
                        )
                    ],
                    "stats": {
                        "files": 0, "concepts": 0, "tasks": 0, "documentsRead": 0,
                    },
                },
                "warnings": [],
            }
        return value


class VisualizeViewOutput(
    RootModel[
        ContextViewOutput | RepositoryOverviewViewOutput | RepositoryTourViewOutput
    ]
):
    """Presentation tool 可返回的判别联合，顶层保持标准 Envelope。"""

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return _context_error_placeholder()
        return value


CONTEXT_TOOL_RESULT = Annotated[CallToolResult, ContextViewOutput]
VISUALIZE_TOOL_RESULT = Annotated[CallToolResult, VisualizeViewOutput]

# MCPServer 会在调用函数之前按类型注解验证输入。这里用 Any 接住原始值，
# 保证所有错误都能进入 AgentNavi 的公开错误边界；WithJsonSchema 只负责让
# tools/list 继续发布精确的 string / const 合同，不依赖 SDK 私有实现。
VISUALIZE_VIEW_INPUT = Annotated[
    Any,
    WithJsonSchema(
        {"type": "string", "enum": ["context", "repo-overview", "repo-tour"]}
    ),
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
    "VISUALIZE_TOOL_RESULT",
    "VISUALIZE_VIEW_INPUT",
    "OPTIONAL_TEXT_INPUT",
    "REQUIRED_TEXT_INPUT",
    "ContextViewOutput",
    "RepositoryOverviewViewOutput",
    "RepositoryOverviewDataOutput",
    "RepositoryTourViewOutput",
    "RepositoryTourDataOutput",
    "VisualizeViewOutput",
    "context_tool_annotations",
]
