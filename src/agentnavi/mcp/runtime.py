"""只在创建 Server 时加载的 MCP SDK 运行时类型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from mcp.types import CallToolResult, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, RootModel, WithJsonSchema, model_validator

from .protocol import MAX_CONTEXT_WARNINGS, SCHEMA_VERSION


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


class ContextEvidenceOutput(_ExtensibleModel):
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    layer: Literal["L1", "L2", "L3"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    path: str | None = None
    line_start: int | None = Field(default=None, alias="lineStart", ge=1)
    line_end: int | None = Field(default=None, alias="lineEnd", ge=1)


class ContextConceptEntityOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    kind: Literal["concept"]
    label: str = Field(min_length=1)
    layer: Literal["L2"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ContextEvidenceOutput] = Field(min_length=1)


class ContextFileEntityOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    kind: Literal["file"]
    label: str = Field(min_length=1)
    path: str = Field(min_length=1)
    layer: Literal["L1"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ContextEvidenceOutput] = Field(min_length=1)


class ContextRelationOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    source_id: str = Field(alias="sourceId", min_length=1)
    target_id: str = Field(alias="targetId", min_length=1)
    relation: str = Field(min_length=1)
    layer: Literal["L2"]
    source: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ContextEvidenceOutput] = Field(min_length=1)


class ContextChainOutput(_ExtensibleModel):
    source_concept: ContextConceptEntityOutput = Field(alias="sourceConcept")
    concept_relation: ContextRelationOutput | None = Field(alias="conceptRelation")
    related_concept: ContextConceptEntityOutput | None = Field(alias="relatedConcept")
    file_relation: ContextRelationOutput = Field(alias="fileRelation")
    file: ContextFileEntityOutput
    evidence: list[ContextEvidenceOutput] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_chain(self) -> "ContextChainOutput":
        if (self.related_concept is None) != (self.concept_relation is None):
            raise ValueError("concept relation and entity must be paired")
        if self.file_relation.target_id != self.file.id:
            raise ValueError("file relation target mismatch")
        if self.file.id == self.source_concept.id:
            raise ValueError("concept and file ids must differ")
        if self.related_concept is None:
            if self.file_relation.source_id != self.source_concept.id:
                raise ValueError("direct file relation source mismatch")
        else:
            assert self.concept_relation is not None
            if (
                self.related_concept.id == self.source_concept.id
                or self.file.id == self.related_concept.id
            ):
                raise ValueError("chain entity ids must be unique across kinds")
            if {
                self.concept_relation.source_id, self.concept_relation.target_id
            } != {self.source_concept.id, self.related_concept.id}:
                raise ValueError("concept relation endpoint mismatch")
            if self.file_relation.source_id != self.related_concept.id:
                raise ValueError("one-hop file relation source mismatch")
        return self


class ContextActionOutput(_ExtensibleModel):
    kind: Literal["purpose", "relevance", "dependents", "history", "impact"]
    label: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence: list[ContextEvidenceOutput] = Field(max_length=3)


class ContextDependentOutput(_ExtensibleModel):
    path: str = Field(min_length=1)
    relation: str = Field(min_length=1)


class ContextHistoryOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: str = Field(min_length=1)
    created_at: str = Field(alias="createdAt", min_length=1)
    relation: str = Field(min_length=1)


class ContextNextStepOutput(_ExtensibleModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ContextReadingOutput(_ExtensibleModel):
    position: int = Field(strict=True, ge=1, le=12)
    path: str = Field(min_length=1)
    language: str = Field(min_length=1)
    why: str = Field(min_length=1)
    evidence: list[ContextEvidenceOutput] = Field(min_length=1, max_length=3)
    next_step: ContextNextStepOutput | None = Field(alias="nextStep")
    chains: list[ContextChainOutput] = Field(min_length=1, max_length=3)
    actions: list[ContextActionOutput] = Field(min_length=5, max_length=5)
    dependents: list[ContextDependentOutput] = Field(max_length=4)
    history: list[ContextHistoryOutput] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_actions(self) -> "ContextReadingOutput":
        expected = (
            ("purpose", "它做什么"),
            ("relevance", "为什么相关"),
            ("dependents", "谁依赖它"),
            ("history", "过去谁改过"),
            ("impact", "如果改它"),
        )
        actual = tuple((item.kind, item.label) for item in self.actions)
        if actual != expected:
            raise ValueError("context actions must use the fixed order and labels")
        if any(chain.file.path != self.path for chain in self.chains):
            raise ValueError("context chain file path must match its reading item")
        return self


class ContextNavigationOutput(_ExtensibleModel):
    revision: str = Field(min_length=1)
    reading_order: list[ContextReadingOutput] = Field(alias="readingOrder", max_length=12)

    @model_validator(mode="after")
    def validate_reading_order(self) -> "ContextNavigationOutput":
        paths = [item.path for item in self.reading_order]
        if len(paths) != len(set(paths)):
            raise ValueError("context reading paths must be unique")
        for index, item in enumerate(self.reading_order):
            if item.position != index + 1:
                raise ValueError("context reading positions must be consecutive")
            expected = paths[index + 1] if index + 1 < len(paths) else None
            actual = item.next_step.path if item.next_step is not None else None
            if actual != expected:
                raise ValueError("context next step must target the adjacent item")
        return self


class ContextDataOutput(_ExtensibleModel):
    stats: ContextStatsOutput
    concepts: list[ContextConceptOutput]
    files: list[ContextFileOutput]
    tasks: list[ContextTaskOutput]
    navigation: ContextNavigationOutput

    @model_validator(mode="after")
    def validate_navigation_candidates(self) -> "ContextDataOutput":
        candidate_paths = {item.path for item in self.files}
        for item in self.navigation.reading_order:
            if item.path not in candidate_paths:
                raise ValueError("context reading path must belong to candidate files")
            if any(dependent.path not in candidate_paths for dependent in item.dependents):
                raise ValueError("context dependent must belong to candidate files")
        return self


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
            "navigation": {"revision": "error", "readingOrder": []},
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
    warnings: list[WarningOutput] = Field(max_length=MAX_CONTEXT_WARNINGS)

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


class ArchitectureSummaryOutput(_ExtensibleModel):
    text: str
    explanation_source: Literal["derived-presentation"] = Field(alias="explanationSource")
    evidence: list[TourEvidenceOutput]


class ArchitectureComponentEntityOutput(TourEntityOutput):
    layer: Literal["L2"]


class ArchitectureConnectionOutput(TourRelationOutput):
    layer: Literal["L2"]


class ArchitectureEntryEntityOutput(TourEntityOutput):
    kind: Literal["file"]
    layer: Literal["L1"]


class ArchitectureComponentOutput(_ExtensibleModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    group: Literal["entry", "core", "support"]
    responsibility: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1, max_length=3)
    entity: ArchitectureComponentEntityOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ArchitectureEntryOutput(_ExtensibleModel):
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    entity: ArchitectureEntryEntityOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1)


class ArchitectureDataOutput(_ExtensibleModel):
    layout: Literal["cognitive-components"]
    summary: ArchitectureSummaryOutput
    components: list[ArchitectureComponentOutput] = Field(max_length=8)
    connections: list[ArchitectureConnectionOutput] = Field(max_length=12)
    entry_points: list[ArchitectureEntryOutput] = Field(alias="entryPoints", max_length=3)
    stats: OverviewStatsOutput


class ArchitectureViewOutput(_ExtensibleModel):
    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["architecture"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: ArchitectureDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return {
                "schemaVersion": SCHEMA_VERSION,
                "view": "architecture",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "layout": "cognitive-components",
                    "summary": {"text": "", "explanationSource": "derived-presentation", "evidence": []},
                    "components": [], "connections": [], "entryPoints": [],
                    "stats": {"files": 0, "concepts": 0, "tasks": 0, "documentsRead": 0},
                },
                "warnings": [],
            }
        return value


class FlowRequestTaskOutput(_ExtensibleModel):
    title: str = Field(min_length=1)
    source: Literal["request", "request-redacted"]

    @model_validator(mode="before")
    @classmethod
    def forbid_provenance(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and ({"entity", "evidence"} & value.keys()):
            raise ValueError("request exampleTask 不得包含 provenance。")
        return value


class FlowTaskEntityOutput(TourEntityOutput):
    kind: Literal["task"]
    layer: Literal["L3"]
    source: Literal["task-events"]


class FlowTaskEvidenceOutput(TourEvidenceOutput):
    layer: Literal["L3"]
    source: Literal["task-events"]


class FlowHistoryTaskOutput(_ExtensibleModel):
    title: str = Field(min_length=1)
    source: Literal["task-events"]
    entity: FlowTaskEntityOutput
    evidence: list[FlowTaskEvidenceOutput] = Field(min_length=1)


FlowTaskOutput = Annotated[
    FlowRequestTaskOutput | FlowHistoryTaskOutput,
    Field(discriminator="source"),
]


class FlowKeyFileEntityOutput(TourEntityOutput):
    kind: Literal["file"]
    layer: Literal["L1"]


class FlowKeyFileRelationOutput(TourRelationOutput):
    layer: Literal["L2"]


class FlowKeyFileOutput(_ExtensibleModel):
    path: str = Field(min_length=1)
    module_id: str = Field(alias="moduleId", min_length=1)
    module_name: str = Field(alias="moduleName", min_length=1)
    entity: FlowKeyFileEntityOutput
    relation: FlowKeyFileRelationOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1)


class FlowStepOutput(_ExtensibleModel):
    step: int = Field(ge=1, le=7)
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    input: str = Field(min_length=1)
    output: str = Field(min_length=1)
    key_files: list[FlowKeyFileOutput] = Field(alias="keyFiles", max_length=3)
    why: str = Field(min_length=1)
    next_step: str | None = Field(alias="nextStep")
    explanation_source: Literal["derived-presentation"] = Field(alias="explanationSource")
    evidence: list[TourEvidenceOutput] = Field(min_length=1)


class FlowDataOutput(_ExtensibleModel):
    layout: Literal["numbered-task-flow"]
    example_task: FlowTaskOutput | None = Field(alias="exampleTask")
    steps: list[FlowStepOutput] = Field(max_length=7)
    stats: OverviewStatsOutput


class FlowViewOutput(_ExtensibleModel):
    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["flow"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: FlowDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return {
                "schemaVersion": SCHEMA_VERSION,
                "view": "flow",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "layout": "numbered-task-flow", "exampleTask": None, "steps": [],
                    "stats": {"files": 0, "concepts": 0, "tasks": 0, "documentsRead": 0},
                },
                "warnings": [],
            }
        return value


class ImpactFocusOutput(_ExtensibleModel):
    entity: TourEntityOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactAnchorOutput(_ExtensibleModel):
    entity: ArchitectureEntryEntityOutput
    mapping: ArchitectureConnectionOutput | None
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactFocusConceptOutput(_ExtensibleModel):
    entity: ArchitectureComponentEntityOutput
    mapping: ArchitectureConnectionOutput | None
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactLaneOutput(_ExtensibleModel):
    peer: ArchitectureEntryEntityOutput
    relation: TourRelationOutput
    via_path: str = Field(alias="viaPath", min_length=1)
    recorded_order: int = Field(alias="recordedOrder", ge=1)
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactSemanticOutput(_ExtensibleModel):
    direction: Literal["incoming", "outgoing"]
    focus_concept_id: str = Field(alias="focusConceptId", min_length=1)
    peer: ArchitectureComponentEntityOutput
    relation: ArchitectureConnectionOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_endpoints(self) -> "ImpactSemanticOutput":
        if self.focus_concept_id == self.peer.id:
            raise ValueError("semantic endpoints must differ")
        expected = (
            (self.focus_concept_id, self.peer.id)
            if self.direction == "outgoing" else (self.peer.id, self.focus_concept_id)
        )
        if (self.relation.source_id, self.relation.target_id) != expected:
            raise ValueError("semantic endpoints mismatch")
        return self


class ImpactHistoryOutput(_ExtensibleModel):
    entity: FlowTaskEntityOutput
    status: str = Field(min_length=1)
    relation: TourRelationOutput
    recorded_order: int = Field(alias="recordedOrder", ge=1)
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactTestOutput(_ExtensibleModel):
    basis: Literal["physical-tests", "semantic-tested-by"]
    path: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_concept: ArchitectureComponentEntityOutput | None = Field(alias="sourceConcept")
    entity: ArchitectureEntryEntityOutput
    relation: TourRelationOutput
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactRiskOutput(_ExtensibleModel):
    kind: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=1)
    evidence: list[TourEvidenceOutput] = Field(min_length=1, max_length=3)


class ImpactActionOutput(_ExtensibleModel):
    kind: Literal["purpose", "callers", "dependencies", "change", "history"]
    label: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence: list[TourEvidenceOutput] = Field(max_length=3)


class ImpactDataOutput(_ExtensibleModel):
    layout: Literal["incoming-focus-outgoing"]
    revision: str = Field(min_length=1)
    focus: ImpactFocusOutput
    anchor_files: list[ImpactAnchorOutput] = Field(alias="anchorFiles", max_length=8)
    focus_concepts: list[ImpactFocusConceptOutput] = Field(alias="focusConcepts", max_length=8)
    incoming: list[ImpactLaneOutput] = Field(max_length=8)
    outgoing: list[ImpactLaneOutput] = Field(max_length=8)
    semantic: list[ImpactSemanticOutput] = Field(max_length=8)
    history: list[ImpactHistoryOutput] = Field(max_length=5)
    test_recommendations: list[ImpactTestOutput] = Field(alias="testRecommendations", max_length=5)
    risks: list[ImpactRiskOutput] = Field(max_length=5)
    actions: list[ImpactActionOutput] = Field(min_length=5, max_length=5)
    stats: ContextStatsOutput

    @model_validator(mode="after")
    def validate_actions_and_edges(self) -> "ImpactDataOutput":
        expected = (("purpose", "它做什么"), ("callers", "谁调用它"),
                    ("dependencies", "它依赖谁"), ("change", "如果修改它"),
                    ("history", "过去谁改过它"))
        if tuple((item.kind, item.label) for item in self.actions) != expected:
            raise ValueError("impact actions must use fixed semantics")
        if not ((self.focus.entity.kind == "file" and self.focus.entity.layer == "L1" and self.focus.entity.path)
                or (self.focus.entity.kind == "concept" and self.focus.entity.layer == "L2")):
            raise ValueError("impact focus provenance invalid")
        anchors = {item.entity.path: item.entity.id for item in self.anchor_files}
        concept_ids = {item.entity.id for item in self.focus_concepts}
        if len(anchors) != len(self.anchor_files):
            raise ValueError("anchor paths must be unique")
        if self.focus.entity.kind == "file":
            if len(self.anchor_files) > 1 or any(
                item.mapping is not None or item.entity.id != self.focus.entity.id
                or item.entity.path != self.focus.entity.path for item in self.anchor_files
            ):
                raise ValueError("file identity anchor invalid")
        elif any(
            item.mapping is None or item.mapping.layer != "L2"
            or item.mapping.relation not in {"implemented_by", "configured_by"}
            or item.mapping.source_id != self.focus.entity.id
            or item.mapping.target_id != item.entity.id for item in self.anchor_files
        ):
            raise ValueError("concept anchor mapping invalid")
        if self.focus.entity.kind == "concept":
            if any(item.entity.id != self.focus.entity.id or item.mapping is not None
                   for item in self.focus_concepts):
                raise ValueError("concept identity mapping invalid")
        elif any(item.mapping is None or item.mapping.layer != "L2"
                 or item.mapping.relation not in {"implemented_by", "configured_by", "tested_by"}
                 or item.mapping.source_id != item.entity.id
                 or item.mapping.target_id != self.focus.entity.id for item in self.focus_concepts):
            raise ValueError("file concept mapping invalid")
        if any(item.via_path not in anchors or item.relation.source_id != item.peer.id
               or item.relation.target_id != anchors[item.via_path] for item in self.incoming):
            raise ValueError("incoming endpoints mismatch")
        if any(item.via_path not in anchors or item.relation.source_id != anchors[item.via_path]
               or item.relation.target_id != item.peer.id for item in self.outgoing):
            raise ValueError("outgoing endpoints mismatch")
        if any(item.focus_concept_id not in concept_ids for item in self.semantic):
            raise ValueError("semantic focus concept must be visible")
        if any(item.evidence != item.relation.evidence
               for item in self.incoming + self.outgoing + self.semantic + self.history + self.test_recommendations):
            raise ValueError("wrapper evidence mismatch")
        valid_targets = {self.focus.entity.id, *anchors.values(), *concept_ids}
        if any(item.entity.source != "task-events" or item.relation.source != "task-events"
               or item.relation.source_id != item.entity.id
               or item.relation.target_id not in valid_targets for item in self.history):
            raise ValueError("history provenance invalid")
        lane_edges = {item.relation.id: item.relation for item in self.incoming + self.outgoing}
        for item in self.test_recommendations:
            if item.entity.path != item.path:
                raise ValueError("test path mismatch")
            if item.basis == "physical-tests":
                if item.source_concept is not None or item.relation.layer != "L1" \
                        or item.relation.relation != "tests" or item.relation.source_id != item.entity.id \
                        or item.relation.target_id not in anchors.values() or lane_edges.get(item.relation.id) != item.relation:
                    raise ValueError("physical test provenance invalid")
            elif item.source_concept is None or item.source_concept.id not in concept_ids \
                    or item.relation.layer != "L2" or item.relation.relation != "tested_by" \
                    or item.relation.source_id != item.source_concept.id or item.relation.target_id != item.entity.id:
                raise ValueError("semantic test provenance invalid")
        entity_signatures: dict[str, tuple[Any, ...]] = {}
        edge_ids: set[str] = set()
        visible_entities = [self.focus.entity, *(item.entity for item in self.anchor_files),
                            *(item.entity for item in self.focus_concepts),
                            *(item.peer for item in self.incoming + self.outgoing),
                            *(item.peer for item in self.semantic), *(item.entity for item in self.history),
                            *(item.entity for item in self.test_recommendations),
                            *(item.source_concept for item in self.test_recommendations if item.source_concept)]
        for entity in visible_entities:
            signature = (entity.kind, entity.layer, entity.path, entity.label, entity.source, entity.confidence)
            if entity.id in edge_ids or (entity.id in entity_signatures and entity_signatures[entity.id] != signature):
                raise ValueError("visible entity registry conflict")
            entity_signatures[entity.id] = signature
        visible_edges = [*(item.mapping for item in self.anchor_files if item.mapping),
                         *(item.mapping for item in self.focus_concepts if item.mapping),
                         *(item.relation for item in self.incoming + self.outgoing + self.semantic + self.history + self.test_recommendations)]
        edge_signatures: dict[str, tuple[Any, ...]] = {}
        for edge in visible_edges:
            signature = (edge.source_id, edge.target_id, edge.relation, edge.layer, edge.source, edge.confidence)
            if edge.id in entity_signatures or (edge.id in edge_signatures and edge_signatures[edge.id] != signature):
                raise ValueError("visible edge registry conflict")
            edge_signatures[edge.id] = signature
            edge_ids.add(edge.id)
        return self


class ImpactViewOutput(_ExtensibleModel):
    schema_version: Literal["agentnavi.vla.v1"] = Field(alias="schemaVersion")
    view: Literal["impact"]
    project: ProjectOutput
    source_state: SourceStateOutput = Field(alias="sourceState")
    data: ImpactDataOutput
    warnings: list[WarningOutput]

    @model_validator(mode="before")
    @classmethod
    def allow_public_error_for_mcp_2_0(cls, value: Any) -> Any:
        if _is_public_error(value):
            return {
                "schemaVersion": SCHEMA_VERSION, "view": "impact",
                "project": {"id": "error", "name": "error", "kind": "internal"},
                "sourceState": {"status": "partial"},
                "data": {
                    "layout": "incoming-focus-outgoing", "revision": "error",
                    "focus": {"entity": {"id": "error", "kind": "concept", "label": "error", "layer": "L2", "source": "internal", "confidence": 0, "evidence": [{"kind": "error", "summary": "error", "layer": "L2", "source": "internal", "confidence": 0}]}, "evidence": [{"kind": "error", "summary": "error", "layer": "L2", "source": "internal", "confidence": 0}]},
                    "anchorFiles": [], "focusConcepts": [],
                    "incoming": [], "outgoing": [], "semantic": [], "history": [],
                    "testRecommendations": [], "risks": [],
                    "actions": [{"kind": kind, "label": label, "summary": "error", "evidence": []} for kind, label in (("purpose", "它做什么"), ("callers", "谁调用它"), ("dependencies", "它依赖谁"), ("change", "如果修改它"), ("history", "过去谁改过它"))],
                    "stats": {"files": 0, "concepts": 0, "tasks": 0},
                }, "warnings": [],
            }
        return value


class VisualizeViewOutput(
    RootModel[
        ContextViewOutput | RepositoryOverviewViewOutput | RepositoryTourViewOutput
        | ArchitectureViewOutput | FlowViewOutput | ImpactViewOutput
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
IMPACT_TOOL_RESULT = Annotated[CallToolResult, ImpactViewOutput]
VISUALIZE_TOOL_RESULT = Annotated[CallToolResult, VisualizeViewOutput]

# MCPServer 会在调用函数之前按类型注解验证输入。这里用 Any 接住原始值，
# 保证所有错误都能进入 AgentNavi 的公开错误边界；WithJsonSchema 只负责让
# tools/list 继续发布精确的 string / const 合同，不依赖 SDK 私有实现。
VISUALIZE_VIEW_INPUT = Annotated[
    Any,
    WithJsonSchema(
        {
            "type": "string",
            "enum": ["context", "repo-overview", "repo-tour", "architecture", "flow", "impact"],
        }
    ),
]
REQUIRED_TEXT_INPUT = Annotated[
    Any,
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 4096}),
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
    "ArchitectureViewOutput",
    "FlowViewOutput",
    "IMPACT_TOOL_RESULT",
    "ImpactViewOutput",
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
