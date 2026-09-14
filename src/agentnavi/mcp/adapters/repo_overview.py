"""Repository Overview Core 数据到文本与 VLA DTO 的严格投影。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ..protocol import AgentNaviView, Evidence, Project, SourceState, Warning

_NOT_INDEXED_MESSAGE = "项目尚未完成索引，当前概览可能不完整。"
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])"
)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} 必须是对象。")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TypeError(f"{field} 必须是数组。")
    return value


def _contains_posix_absolute(value: str) -> bool:
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


def _text(value: Any, field: str, *, limit: int = 320) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串。")
    candidate = value[:limit]
    if (
        "file://" in candidate.lower()
        or "~/" in candidate
        or _WINDOWS_ABSOLUTE_RE.search(candidate)
        or _contains_posix_absolute(candidate)
    ):
        return "[内容含路径，已隐藏]"
    return candidate


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} 必须是数值。")
    return value


def _path(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} 必须是字符串。")
    path = PurePosixPath(value)
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or value.startswith(("/", "~"))
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
        or str(path) != value
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ValueError(f"{field} 必须是 POSIX 相对路径。")
    return value


def _evidence(value: Any, field: str) -> Evidence:
    item = _mapping(value, field)
    line_start = item.get("line_start")
    line_end = item.get("line_end")
    return Evidence(
        kind=_text(item.get("kind"), f"{field}.kind", limit=80),
        summary=_text(item.get("summary"), f"{field}.summary"),
        layer=_text(item.get("layer"), f"{field}.layer", limit=2),  # type: ignore[arg-type]
        source=_text(item.get("source"), f"{field}.source", limit=120),
        confidence=float(_number(item.get("confidence"), f"{field}.confidence")),
        path=_path(item.get("path"), f"{field}.path") if item.get("path") is not None else None,
        line_start=int(_number(line_start, f"{field}.line_start")) if line_start is not None else None,
        line_end=int(_number(line_end, f"{field}.line_end")) if line_end is not None else None,
    )


def _evidence_list(value: Any, field: str) -> list[dict[str, Any]]:
    return [_evidence(item, f"{field}[]").to_dict() for item in _sequence(value, field)[:8]]


def _statement(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    return {
        "summary": _text(item.get("summary", ""), f"{field}.summary"),
        "evidence": _evidence_list(item.get("evidence", []), f"{field}.evidence"),
    }


def _workflow_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "repo-overview.workflow[]")
    return {
        "step": int(_number(item.get("step"), "workflow.step")),
        "title": _text(item.get("title"), "workflow.title", limit=240),
        "detail": _text(item.get("detail"), "workflow.detail"),
        "evidence": _evidence_list(item.get("evidence", []), "workflow.evidence"),
    }


def _module_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "repo-overview.modules[]")
    return {
        "id": _text(item.get("id"), "modules.id", limit=240),
        "name": _text(item.get("name"), "modules.name", limit=160),
        "summary": _text(item.get("summary"), "modules.summary"),
        "paths": [_path(path, "modules.paths[]") for path in _sequence(item.get("paths", []), "modules.paths")[:3]],
        "layer": _text(item.get("layer"), "modules.layer", limit=2),
        "source": _text(item.get("source"), "modules.source", limit=120),
        "confidence": _number(item.get("confidence"), "modules.confidence"),
        "evidence": _evidence_list(item.get("evidence", []), "modules.evidence"),
    }


def _reading_entry(value: Any) -> dict[str, Any]:
    item = _mapping(value, "repo-overview.readingOrder[]")
    return {
        "position": int(_number(item.get("position"), "readingOrder.position")),
        "path": _path(item.get("path"), "readingOrder.path"),
        "reason": _text(item.get("reason"), "readingOrder.reason"),
        "evidence": _evidence_list(item.get("evidence", []), "readingOrder.evidence"),
    }


def _warning(value: Any) -> Warning:
    item = _mapping(value, "repo-overview.warnings[]")
    return Warning(
        code=_text(item.get("code"), "warning.code", limit=80),
        message=_text(item.get("message"), "warning.message"),
        evidence=tuple(
            _evidence(entry, "warning.evidence[]")
            for entry in _sequence(item.get("evidence", []), "warning.evidence")[:8]
        ),
    )


def _overview_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    """逐字段复制公开 allowlist，拒绝 Core 中的任何附加字段。"""

    data = _mapping(core_data, "repo-overview")
    need = _mapping(data.get("need"), "repo-overview.need")
    stats = _mapping(data.get("stats"), "repo-overview.stats")
    return {
        "purpose": _statement(data.get("purpose"), "repo-overview.purpose"),
        "need": {
            "problem": _statement(need.get("problem"), "repo-overview.need.problem"),
            "solution": _statement(need.get("solution"), "repo-overview.need.solution"),
        },
        "workflow": [_workflow_entry(item) for item in _sequence(data.get("workflow", []), "repo-overview.workflow")[:7]],
        "modules": [_module_entry(item) for item in _sequence(data.get("modules", []), "repo-overview.modules")[:8]],
        "readingOrder": [
            _reading_entry(item)
            for item in _sequence(data.get("readingOrder", []), "repo-overview.readingOrder")[:7]
        ],
        "stats": {
            "files": int(_number(stats.get("files"), "stats.files")),
            "concepts": int(_number(stats.get("concepts"), "stats.concepts")),
            "tasks": int(_number(stats.get("tasks"), "stats.tasks")),
            "documentsRead": int(_number(stats.get("documentsRead"), "stats.documentsRead")),
        },
    }


def _project(core_data: Mapping[str, Any]) -> Project:
    raw = _mapping(core_data.get("project"), "repo-overview.project")
    return Project(
        id=_text(raw.get("id"), "project.id", limit=240),
        name=_text(raw.get("name"), "project.name", limit=240),
        kind=_text(raw.get("kind"), "project.kind", limit=80),
    )


def _source_state(core_data: Mapping[str, Any]) -> tuple[SourceState, list[Warning]]:
    raw = _mapping(core_data.get("project"), "repo-overview.project")
    warnings = [_warning(item) for item in _sequence(core_data.get("warnings", []), "repo-overview.warnings")]
    last_scan_at = raw.get("last_scan_at")
    if last_scan_at is None:
        warnings.append(Warning(code="SOURCE_NOT_INDEXED", message=_NOT_INDEXED_MESSAGE))
        return SourceState(status="partial"), warnings
    indexed_at = _text(last_scan_at, "project.last_scan_at", limit=64)
    if indexed_at.endswith("+00:00"):
        indexed_at = indexed_at[:-6] + "Z"
    return SourceState(status="ready", indexed_at=indexed_at), warnings


def repo_overview_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="repo-overview",
        project=_project(core_data),
        source_state=source_state,
        data=_overview_payload(core_data),
        warnings=tuple(warnings),
    )


def repo_overview_text(core_data: Mapping[str, Any]) -> str:
    """独立生成模型 fallback，不从 AgentNaviView 或 JSON 反向解析。"""

    project = _project(core_data)
    data = _overview_payload(core_data)
    warnings = [_warning(item) for item in _sequence(core_data.get("warnings", []), "repo-overview.warnings")]
    if _mapping(core_data.get("project"), "repo-overview.project").get("last_scan_at") is None:
        warnings.append(Warning(code="SOURCE_NOT_INDEXED", message=_NOT_INDEXED_MESSAGE))
    lines = ["[AgentNavi 项目概览]", f"项目：{project.name}（{project.id}）", "", "做什么：", data["purpose"]["summary"] or "暂无足够文档证据。"]
    lines.extend(["", "为什么：", f"- 问题：{data['need']['problem']['summary'] or '暂无足够文档证据。'}", f"- 方案：{data['need']['solution']['summary'] or '暂无足够文档证据。'}"])
    if data["workflow"]:
        lines.extend(["", "主流程："])
        lines.extend(f"{item['step']}. {item['title']}" for item in data["workflow"])
    if data["modules"]:
        lines.extend(["", "核心模块："])
        lines.extend(f"- {item['name']}：{item['summary']}" for item in data["modules"])
    if data["readingOrder"]:
        lines.extend(["", "建议阅读顺序："])
        lines.extend(f"{item['position']}. {item['path']}（{item['reason']}）" for item in data["readingOrder"])
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["repo_overview_text", "repo_overview_view"]
