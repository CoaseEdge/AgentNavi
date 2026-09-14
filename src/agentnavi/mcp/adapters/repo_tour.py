"""Guided Repository Tour Core 数据到文本与 VLA DTO 的严格投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView
from .repo_overview import (
    _evidence_list,
    _evidence_reference,
    _mapping,
    _number,
    _path,
    _project,
    _sequence,
    _source_state,
    _text,
    _warning,
)

_DEPTHS = ("one-minute", "five-minutes", "source-deep-dive")
_STOP_KINDS = {
    "purpose", "why", "workflow", "module", "data-structure", "task",
    "file", "history", "symbol", "dependency", "test", "task-history",
    "evidence",
}


def _entity(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    layer = _text(item.get("layer"), f"{field}.layer", limit=2)
    if layer not in {"L1", "L2", "L3"}:
        raise ValueError(f"{field}.layer 无效。")
    path = item.get("path")
    return {
        "id": _text(item.get("id"), f"{field}.id", limit=240),
        "kind": _text(item.get("kind"), f"{field}.kind", limit=80),
        "label": _text(item.get("label"), f"{field}.label", limit=240),
        **({"path": _path(path, f"{field}.path")} if path is not None else {}),
        "layer": layer,
        "source": _text(item.get("source"), f"{field}.source", limit=120),
        "confidence": _number(item.get("confidence"), f"{field}.confidence"),
        "evidence": _evidence_list(item.get("evidence", []), f"{field}.evidence"),
    }


def _relation(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    layer = _text(item.get("layer"), f"{field}.layer", limit=2)
    if layer not in {"L1", "L2", "L3"}:
        raise ValueError(f"{field}.layer 无效。")
    return {
        "id": _text(item.get("id"), f"{field}.id", limit=240),
        "sourceId": _text(item.get("sourceId"), f"{field}.sourceId", limit=240),
        "targetId": _text(item.get("targetId"), f"{field}.targetId", limit=240),
        "relation": _text(item.get("relation"), f"{field}.relation", limit=120),
        "layer": layer,
        "source": _text(item.get("source"), f"{field}.source", limit=120),
        "confidence": _number(item.get("confidence"), f"{field}.confidence"),
        "evidence": _evidence_list(item.get("evidence", []), f"{field}.evidence"),
    }


def _stop(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    kind = _text(item.get("kind"), f"{field}.kind", limit=80)
    if kind not in _STOP_KINDS:
        raise ValueError(f"{field}.kind 无效。")
    return {
        "id": _text(item.get("id"), f"{field}.id", limit=240),
        "kind": kind,
        "title": _text(item.get("title"), f"{field}.title", limit=240),
        "plainLanguage": _text(item.get("plainLanguage"), f"{field}.plainLanguage"),
        "technicalExplanation": _text(
            item.get("technicalExplanation"), f"{field}.technicalExplanation"
        ),
        "evidence": _evidence_list(item.get("evidence", []), f"{field}.evidence"),
        "entity": _entity(item.get("entity"), f"{field}.entity"),
        "relations": [
            _relation(relation, f"{field}.relations[]")
            for relation in _sequence(item.get("relations", []), f"{field}.relations")[:4]
        ],
    }


def _tour_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(core_data, "repo-tour")
    raw_tiers = _sequence(data.get("tiers", []), "repo-tour.tiers")
    tiers_by_depth = {
        _text(_mapping(tier, "repo-tour.tiers[]").get("depth"), "tiers.depth", limit=32):
        _mapping(tier, "repo-tour.tiers[]")
        for tier in raw_tiers[:3]
    }
    tiers = []
    for depth in _DEPTHS:
        tier = tiers_by_depth.get(depth)
        if tier is None:
            raise ValueError(f"缺少固定 Tour 深度：{depth}")
        tiers.append(
            {
                "depth": depth,
                "label": _text(tier.get("label"), "tiers.label", limit=80),
                "stops": [
                    _stop(stop, f"tiers.{depth}.stops[]")
                    for stop in _sequence(tier.get("stops", []), "tiers.stops")[:12]
                ],
            }
        )
    stats = _mapping(data.get("stats"), "repo-tour.stats")
    return {
        "tiers": tiers,
        "stats": {
            "files": int(_number(stats.get("files"), "stats.files")),
            "concepts": int(_number(stats.get("concepts"), "stats.concepts")),
            "tasks": int(_number(stats.get("tasks"), "stats.tasks")),
            "documentsRead": int(
                _number(stats.get("documentsRead"), "stats.documentsRead")
            ),
        },
    }


def repo_tour_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="repo-tour",
        project=_project(core_data),
        source_state=source_state,
        data=_tour_payload(core_data),
        warnings=tuple(warnings),
    )


def repo_tour_text(core_data: Mapping[str, Any]) -> str:
    """独立生成模型 fallback，不调用 view adapter。"""

    project = _project(core_data)
    payload = _tour_payload(core_data)
    warnings = [
        _warning(item)
        for item in _sequence(core_data.get("warnings", []), "repo-tour.warnings")
    ]
    lines = ["[AgentNavi 仓库导览]", f"项目：{project.name}（{project.id}）"]
    for tier in payload["tiers"]:
        lines.extend(["", tier["label"] + "："])
        if not tier["stops"]:
            lines.append("- 暂无足够的可追溯证据。")
            continue
        for index, stop in enumerate(tier["stops"], start=1):
            lines.extend(
                [
                    f"{index}. [{stop['kind']}] {stop['title']}：{stop['plainLanguage']}",
                    f"   技术说明：{stop['technicalExplanation']}",
                    f"   证据：{_evidence_reference(stop['evidence'])}",
                ]
            )
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["repo_tour_text", "repo_tour_view"]
