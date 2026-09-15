"""AgentNavi VLA 的稳定 wire protocol。

本模块位于 Core 与 MCP SDK 之间，只负责定义 JSON-safe DTO 和通用防御检查。
它不读取数据库、项目文件或外部日志，也不依赖 MCP SDK。字段名 denylist
不能理解数据的业务语义，因此后续每个 Adapter 仍必须按 view allowlist 构造 DTO。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Literal, TypeAlias


SCHEMA_VERSION = "agentnavi.vla.v1"
SUPPORTED_VIEWS = (
    "repo-overview",
    "repo-tour",
    "architecture",
    "flow",
    "context",
    "impact",
    "history",
    "semantic-review",
)

Layer: TypeAlias = Literal["L1", "L2", "L3"]
ViewName: TypeAlias = Literal[
    "repo-overview",
    "repo-tour",
    "architecture",
    "flow",
    "context",
    "impact",
    "history",
    "semantic-review",
]
SourceStatus: TypeAlias = Literal["ready", "partial", "stale"]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_ENVELOPE_FIELDS = frozenset(
    {"schemaVersion", "view", "project", "sourceState", "data", "warnings"}
)
_FORBIDDEN_KEYS = frozenset(
    {
        "root",
        "projectroot",
        "repositoryroot",
        "workspaceroot",
        "logpath",
        "eventlogpath",
        "overlaylogpath",
        "databasepath",
        "sqlitepath",
        "sqliterow",
        "rawsqliterow",
        "rawrow",
        "rawrows",
        "rawevent",
        "rawevents",
        "unfilteredevent",
        "unfilteredevents",
        "eventpayload",
        "sourcecontent",
        "rawsource",
        "rawsourcecontent",
        "filecontent",
        "fullsource",
        "fulltext",
        "sourcebody",
    }
)
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])")
_UNC_FORWARD_RE = re.compile(r"(?<!:)//[A-Za-z0-9._~-]+(?:/|\b)")
_RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


class _WireDTO:
    def to_dict(self) -> dict[str, JsonValue]:
        raise NotImplementedError


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串。")


def _validate_layer(layer: str) -> None:
    if layer not in {"L1", "L2", "L3"}:
        raise ValueError("layer 必须是 L1、L2 或 L3。")


def _validate_confidence(confidence: float) -> None:
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError("confidence 必须是 0 到 1 之间的有限数值。")


def _validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError(f"路径必须是 POSIX 相对路径：{path!r}")
    posix_path = PurePosixPath(path)
    if (
        posix_path.is_absolute()
        or PureWindowsPath(path).is_absolute()
        or bool(PureWindowsPath(path).drive)
        or path.startswith("file://")
        or path.startswith("~")
        or ".." in posix_path.parts
        or str(posix_path) in {"", "."}
        or str(posix_path) != path
    ):
        raise ValueError(f"路径必须是 POSIX 相对路径：{path!r}")
    return path


def _reject_absolute_path_text(value: str) -> None:
    lower = value.lower()
    if (
        "file://" in lower
        or "~/" in value
        or _UNC_FORWARD_RE.search(value)
        or _WINDOWS_ABSOLUTE_RE.search(value)
        or _contains_posix_absolute(value)
        or value == "/"
    ):
        raise ValueError("VLA 输出不得包含绝对路径或 file:// URI。")


def _contains_posix_absolute(value: str) -> bool:
    """识别独立出现的 POSIX 绝对路径，同时放过 URL 与相对路径。"""

    for index, character in enumerate(value):
        if character != "/" or index + 1 >= len(value) or value[index + 1].isspace():
            continue
        if index == 0:
            return True
        previous = value[index - 1]
        if previous == ":" and value[index + 1] == "/":
            continue
        if previous.isalnum() or previous in "._~-/":
            continue
        return True
    return False


def _normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _extension_semantic_key(key: str) -> str:
    """统一 camel/snake/kebab 变体，同时保留非 ASCII 字段身份。"""

    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in key
        if character not in {"_", "-"}
    )


def _normalize_json(value: Any, *, parent_key: str | None = None) -> JsonValue:
    """复制严格 JSON 值并执行 defense-in-depth canary，不代替 Adapter allowlist。"""

    if isinstance(value, _WireDTO):
        return value.to_dict()
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and parent_key is not None:
            _reject_absolute_path_text(value)
            normalized = _normalized_key(parent_key)
            if normalized == "path" or normalized.endswith("path") or normalized.endswith("paths"):
                _validate_relative_path(value)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 数值必须是有限值。")
        return value
    if isinstance(value, Mapping):
        output: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON 对象的字段名必须是字符串。")
            _reject_absolute_path_text(key)
            normalized = _normalized_key(key)
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"禁止输出字段：{key}")
            output[key] = _normalize_json(item, parent_key=key)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_json(item, parent_key=parent_key) for item in value]
    raise TypeError(f"值无法序列化为 JSON：{type(value).__name__}")


def _freeze_json(value: JsonValue) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _snapshot_mapping(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} 必须是 JSON 对象。")
    normalized = _normalize_json(value)
    assert isinstance(normalized, dict)
    return _freeze_json(normalized)


def _snapshot_evidence(value: Sequence[Evidence], field_name: str) -> tuple[Evidence, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field_name} 必须是 Evidence 数组。")
    result = tuple(value)
    if any(not isinstance(item, Evidence) for item in result):
        raise TypeError(f"{field_name} 只能包含 Evidence。")
    return result


def _merge_extensions(
    payload: dict[str, JsonValue],
    extensions: Mapping[str, Any],
    *,
    reserved: frozenset[str] | set[str],
) -> dict[str, JsonValue]:
    if not isinstance(extensions, Mapping):
        raise TypeError("extensions 必须是 JSON 对象。")
    normalized = _normalize_json(extensions)
    assert isinstance(normalized, dict)
    reserved_by_normalized = {_extension_semantic_key(key): key for key in reserved}
    extension_by_normalized: dict[str, str] = {}
    collisions: list[str] = []
    for key in normalized:
        semantic_key = _extension_semantic_key(key)
        if semantic_key in reserved_by_normalized:
            collisions.append(key)
        if semantic_key in extension_by_normalized:
            collisions.extend((extension_by_normalized[semantic_key], key))
        extension_by_normalized[semantic_key] = key
    if collisions:
        fields = "、".join(sorted(set(collisions)))
        raise ValueError(f"extensions 不能覆盖协议保留字段：{fields}")
    payload.update(normalized)
    result = _normalize_json(payload)
    assert isinstance(result, dict)
    return result


@dataclass(frozen=True, slots=True)
class Evidence(_WireDTO):
    kind: str
    summary: str
    layer: Layer
    source: str
    confidence: float
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.kind, "Evidence.kind")
        _require_text(self.summary, "Evidence.summary")
        _validate_layer(self.layer)
        _require_text(self.source, "Evidence.source")
        _validate_confidence(self.confidence)
        if self.path is not None:
            _validate_relative_path(self.path)
        if self.line_start is not None and (
            isinstance(self.line_start, bool)
            or not isinstance(self.line_start, int)
            or self.line_start < 1
        ):
            raise ValueError("line_start 必须大于等于 1。")
        if self.line_end is not None and (
            isinstance(self.line_end, bool)
            or not isinstance(self.line_end, int)
            or self.line_end < 1
        ):
            raise ValueError("line_end 必须大于等于 1。")
        if self.line_start is None and self.line_end is not None:
            raise ValueError("提供 line_end 时必须同时提供 line_start。")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end 不能小于 line_start。")
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "Evidence.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "kind": self.kind,
            "summary": self.summary,
            "layer": self.layer,
            "source": self.source,
            "confidence": float(self.confidence),
        }
        if self.path is not None:
            payload["path"] = self.path
        if self.line_start is not None:
            payload["lineStart"] = self.line_start
        if self.line_end is not None:
            payload["lineEnd"] = self.line_end
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={
                "kind",
                "summary",
                "layer",
                "source",
                "confidence",
                "path",
                "lineStart",
                "lineEnd",
            },
        )


@dataclass(frozen=True, slots=True)
class EntityRef(_WireDTO):
    id: str
    kind: str
    label: str
    layer: Layer
    source: str
    confidence: float
    evidence: tuple[Evidence, ...] = ()
    path: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "EntityRef.id")
        _require_text(self.kind, "EntityRef.kind")
        _require_text(self.label, "EntityRef.label")
        _validate_layer(self.layer)
        _require_text(self.source, "EntityRef.source")
        _validate_confidence(self.confidence)
        if self.path is not None:
            _validate_relative_path(self.path)
        object.__setattr__(
            self,
            "evidence",
            _snapshot_evidence(self.evidence, "EntityRef.evidence"),
        )
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "EntityRef.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "layer": self.layer,
            "source": self.source,
            "confidence": float(self.confidence),
            "evidence": _normalize_json(self.evidence),
        }
        if self.path is not None:
            payload["path"] = self.path
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={"id", "kind", "label", "layer", "source", "confidence", "evidence", "path"},
        )


@dataclass(frozen=True, slots=True)
class GraphEdge(_WireDTO):
    id: str
    source_id: str
    target_id: str
    relation: str
    layer: Layer
    source: str
    confidence: float
    evidence: tuple[Evidence, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "GraphEdge.id")
        _require_text(self.source_id, "GraphEdge.source_id")
        _require_text(self.target_id, "GraphEdge.target_id")
        _require_text(self.relation, "GraphEdge.relation")
        _validate_layer(self.layer)
        _require_text(self.source, "GraphEdge.source")
        _validate_confidence(self.confidence)
        object.__setattr__(
            self,
            "evidence",
            _snapshot_evidence(self.evidence, "GraphEdge.evidence"),
        )
        if not self.evidence:
            raise ValueError("GraphEdge.evidence 必须至少包含一个 Evidence。")
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "GraphEdge.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "id": self.id,
            "sourceId": self.source_id,
            "targetId": self.target_id,
            "relation": self.relation,
            "layer": self.layer,
            "source": self.source,
            "confidence": float(self.confidence),
            "evidence": _normalize_json(self.evidence),
        }
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={"id", "sourceId", "targetId", "relation", "layer", "source", "confidence", "evidence"},
        )


@dataclass(frozen=True, slots=True)
class Warning(_WireDTO):
    code: str
    message: str
    evidence: tuple[Evidence, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.code, "Warning.code")
        _require_text(self.message, "Warning.message")
        object.__setattr__(
            self,
            "evidence",
            _snapshot_evidence(self.evidence, "Warning.evidence"),
        )
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "Warning.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "evidence": _normalize_json(self.evidence),
        }
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={"code", "message", "evidence"},
        )


@dataclass(frozen=True, slots=True)
class Error(_WireDTO):
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.code, "Error.code")
        _require_text(self.message, "Error.message")
        if not isinstance(self.retryable, bool):
            raise TypeError("Error.retryable 必须是布尔值。")
        object.__setattr__(self, "details", _snapshot_mapping(self.details, "Error.details"))
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "Error.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": _normalize_json(self.details),
        }
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={"code", "message", "retryable", "details"},
        )


@dataclass(frozen=True, slots=True)
class Project(_WireDTO):
    """可公开的项目身份；刻意不包含项目根目录。"""

    id: str
    name: str
    kind: str
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.id, "Project.id")
        _require_text(self.name, "Project.name")
        _require_text(self.kind, "Project.kind")
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "Project.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"id": self.id, "name": self.name, "kind": self.kind}
        return _merge_extensions(payload, self.extensions, reserved={"id", "name", "kind"})


@dataclass(frozen=True, slots=True)
class SourceState(_WireDTO):
    status: SourceStatus
    revision: str | None = None
    indexed_at: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"ready", "partial", "stale"}:
            raise ValueError("SourceState.status 必须是 ready、partial 或 stale。")
        if self.revision is not None:
            _require_text(self.revision, "SourceState.revision")
        if self.indexed_at is not None:
            _require_text(self.indexed_at, "SourceState.indexed_at")
            if not _RFC3339_UTC_RE.fullmatch(self.indexed_at):
                raise ValueError("indexed_at 必须是以 Z 结尾的 RFC 3339 UTC 时间。")
            try:
                parsed = datetime.fromisoformat(self.indexed_at.removesuffix("Z") + "+00:00")
            except ValueError as exc:
                raise ValueError("indexed_at 必须是有效的 RFC 3339 UTC 时间。") from exc
            if parsed.tzinfo != timezone.utc:
                raise ValueError("indexed_at 必须是 UTC 时间。")
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "SourceState.extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"status": self.status}
        if self.revision is not None:
            payload["revision"] = self.revision
        if self.indexed_at is not None:
            payload["indexedAt"] = self.indexed_at
        return _merge_extensions(
            payload,
            self.extensions,
            reserved={"status", "revision", "indexedAt"},
        )


@dataclass(frozen=True, slots=True)
class AgentNaviView(_WireDTO):
    """统一 VLA envelope；data 必须由按 view allowlist 实现的 Adapter 提供。"""

    view: ViewName
    project: Project
    source_state: SourceState
    data: Mapping[str, Any]
    warnings: tuple[Warning, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.view not in SUPPORTED_VIEWS:
            raise ValueError(f"不支持的 VLA view：{self.view}")
        if not isinstance(self.project, Project):
            raise TypeError("project 必须是 Project DTO。")
        if not isinstance(self.source_state, SourceState):
            raise TypeError("source_state 必须是 SourceState DTO。")
        object.__setattr__(self, "data", _snapshot_mapping(self.data, "data"))
        if isinstance(self.warnings, (str, bytes, bytearray)) or not isinstance(
            self.warnings, Sequence
        ):
            raise TypeError("warnings 必须是 Warning 数组。")
        warnings = tuple(self.warnings)
        if any(not isinstance(item, Warning) for item in warnings):
            raise TypeError("warnings 只能包含 Warning。")
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(
            self,
            "extensions",
            _snapshot_mapping(self.extensions, "extensions"),
        )
        self.to_dict()

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schemaVersion": SCHEMA_VERSION,
            "view": self.view,
            "project": self.project.to_dict(),
            "sourceState": self.source_state.to_dict(),
            "data": _normalize_json(self.data),
            "warnings": _normalize_json(self.warnings),
        }
        return _merge_extensions(payload, self.extensions, reserved=_ENVELOPE_FIELDS)

    def to_json(self) -> str:
        """生成可重现的紧凑 JSON；相同语义映射不受插入顺序影响。"""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


Envelope = AgentNaviView


__all__ = [
    "SCHEMA_VERSION",
    "SUPPORTED_VIEWS",
    "AgentNaviView",
    "EntityRef",
    "Envelope",
    "Error",
    "Evidence",
    "GraphEdge",
    "JsonValue",
    "Layer",
    "Project",
    "SourceState",
    "SourceStatus",
    "ViewName",
    "Warning",
]
