"""Context Core 数据到模型文本和 VLA DTO 的安全投影。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...query import format_context
from ..protocol import AgentNaviView, Project, SourceState, Warning

_NOT_INDEXED_MESSAGE = "项目尚未完成索引，当前结果可能不完整。"
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])"
)


def _text(value: Any, field: str, *, limit: int = 240) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串。")
    return value[:limit]


def _path(value: Any, field: str) -> str:
    """完整保留路径，让 S01 协议负责规范化与隐私验证。"""

    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串。")
    return value


def _query_text(value: Any) -> str:
    """把含绝对形式 token 的查询替换为不泄漏路径的明确提示。"""

    if not isinstance(value, str):
        raise TypeError("context.query 必须是字符串。")
    lowered = value.lower()
    if (
        "file://" in lowered
        or "~/" in value
        or _WINDOWS_ABSOLUTE_RE.search(value)
        or _contains_posix_absolute(value)
    ):
        return "[查询含路径，已隐藏]"
    return value[:500]


def _contains_posix_absolute(value: str) -> bool:
    """识别独立 POSIX 路径 token，并避免把 HTTP(S) URL 当作路径。"""

    for index, character in enumerate(value):
        if character != "/":
            continue
        if index == 0:
            return True
        previous = value[index - 1]
        if previous == ":" and index + 1 < len(value) and value[index + 1] == "/":
            continue
        if previous.isalnum() or previous in "._~-/":
            continue
        return True
    return False


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} 必须是数值。")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} 必须是对象。")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} 必须是数组。")
    return value


def _file_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "context.files[]")
    return {
        "path": _path(item.get("path"), "context.files[].path"),
        "relation": _text(item.get("relation"), "context.files[].relation"),
        "language": _text(
            item.get("language", "unknown"),
            "context.files[].language",
            limit=80,
        ),
    }


def _neighbor_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "context.concepts[].neighbors[]")
    return {
        "direction": _text(item.get("direction"), "neighbor.direction", limit=16),
        "relation": _text(item.get("relation"), "neighbor.relation", limit=80),
        "id": _text(item.get("id"), "neighbor.id"),
        "key": _text(item.get("key"), "neighbor.key"),
        "label": _text(item.get("label"), "neighbor.label"),
        "confidence": _number(item.get("confidence"), "neighbor.confidence"),
        "source": _text(item.get("source"), "neighbor.source", limit=120),
    }


def _concept_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "context.concepts[]")
    return {
        "id": _text(item.get("id"), "context.concepts[].id"),
        "key": _text(item.get("key"), "context.concepts[].key"),
        "label": _text(item.get("label"), "context.concepts[].label"),
        "confidence": _number(item.get("confidence"), "context.concepts[].confidence"),
        "source": _text(item.get("source"), "context.concepts[].source", limit=120),
        # format_context 的文本合同最多展示五个一跳邻居；结构化投影保持同一上限。
        "neighbors": [
            _neighbor_entry(neighbor)
            for neighbor in _sequence(
                item.get("neighbors", []), "context.concepts[].neighbors"
            )[:5]
        ],
        "files": [
            _file_entry(file_entry)
            for file_entry in _sequence(item.get("files", []), "context.concepts[].files")[:12]
        ],
    }


def _task_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "context.tasks[]")
    return {
        "id": _text(item.get("id"), "context.tasks[].id"),
        "title": _text(item.get("title"), "context.tasks[].title", limit=160),
        "status": _text(item.get("status"), "context.tasks[].status", limit=40),
        "summary": _text(item.get("summary", ""), "context.tasks[].summary"),
        "created_at": _text(item.get("created_at"), "context.tasks[].created_at", limit=64),
    }


def _context_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    """按 Context view allowlist 逐字段复制，不透传 Core mapping。"""

    data = _mapping(core_data, "context")
    stats = _mapping(data.get("stats"), "context.stats")
    return {
        "stats": {
            "files": _number(stats.get("files"), "context.stats.files"),
            "concepts": _number(stats.get("concepts"), "context.stats.concepts"),
            "tasks": _number(stats.get("tasks"), "context.stats.tasks"),
        },
        "concepts": [
            _concept_entry(concept)
            for concept in _sequence(data.get("concepts", []), "context.concepts")[:5]
        ],
        "files": [
            _file_entry(file_entry)
            for file_entry in _sequence(data.get("files", []), "context.files")[:12]
        ],
        "tasks": [
            _task_entry(task)
            for task in _sequence(data.get("tasks", []), "context.tasks")[:5]
        ],
    }


def _project(core_data: Mapping[str, Any]) -> Project:
    raw_project = _mapping(core_data.get("project"), "context.project")
    return Project(
        id=_text(raw_project.get("id"), "context.project.id"),
        name=_text(raw_project.get("name"), "context.project.name"),
        kind=_text(raw_project.get("kind"), "context.project.kind", limit=80),
    )


def _source_state(core_data: Mapping[str, Any]) -> tuple[SourceState, tuple[Warning, ...]]:
    raw_project = _mapping(core_data.get("project"), "context.project")
    last_scan_at = raw_project.get("last_scan_at")
    if last_scan_at is None:
        return (
            SourceState(status="partial"),
            (Warning(code="SOURCE_NOT_INDEXED", message=_NOT_INDEXED_MESSAGE),),
        )
    indexed_at = _text(last_scan_at, "context.project.last_scan_at", limit=64)
    if indexed_at.endswith("+00:00"):
        indexed_at = indexed_at[:-6] + "Z"
    return SourceState(status="ready", indexed_at=indexed_at), ()


def context_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    """生成未来 UI 消费的结构化 Context view。"""

    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="context",
        project=_project(core_data),
        source_state=source_state,
        data=_context_payload(core_data),
        warnings=warnings,
    )


def context_text(core_data: Mapping[str, Any]) -> str:
    """独立生成模型文本，不从 AgentNaviView 或 JSON 反向解析。"""

    raw_project = _mapping(core_data.get("project"), "context.project")
    text_data = _context_payload(core_data)
    text_data["query"] = _query_text(core_data.get("query", ""))
    text_data["project"] = {
        "id": _text(raw_project.get("id"), "context.project.id"),
        "name": _text(raw_project.get("name"), "context.project.name"),
        "kind": _text(raw_project.get("kind"), "context.project.kind", limit=80),
    }
    text = format_context(text_data, include_project_root=False)
    if raw_project.get("last_scan_at") is None:
        text += f"\n\n提示：{_NOT_INDEXED_MESSAGE}"
    return text


__all__ = ["context_text", "context_view"]
