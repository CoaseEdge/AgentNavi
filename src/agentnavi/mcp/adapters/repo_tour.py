"""Guided Repository Tour Core 数据到文本与 VLA DTO 的严格投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView, EntityRef, Evidence, GraphEdge
from .repo_overview import (
    _evidence,
    _evidence_reference,
    _mapping,
    _number,
    _path,
    _project,
    _sequence,
    _source_state,
    _text,
)

_DEPTHS = ("one-minute", "five-minutes", "source-deep-dive")
_STOP_KINDS = {
    "purpose", "why", "workflow", "module", "data-structure", "task",
    "file", "history", "symbol", "dependency", "test", "task-history",
    "evidence",
}
_LIMITS = {"one-minute": 4, "five-minutes": 8, "source-deep-dive": 12}
_KINDS_BY_DEPTH = {
    "one-minute": {"purpose", "why", "workflow", "module"},
    "five-minutes": {
        "purpose", "why", "workflow", "module", "data-structure", "task",
        "file", "history",
    },
    "source-deep-dive": {
        "file", "symbol", "dependency", "test", "task-history", "evidence",
    },
}


def _evidence_objects(value: Any, field: str) -> tuple[Evidence, ...]:
    return tuple(
        _evidence(item, f"{field}[]")
        for item in _sequence(value, field)[:8]
    )


def _entity(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    layer = _text(item.get("layer"), f"{field}.layer", limit=2)
    if layer not in {"L1", "L2", "L3"}:
        raise ValueError(f"{field}.layer 无效。")
    path = item.get("path")
    evidence = _evidence_objects(item.get("evidence", []), f"{field}.evidence")
    if not evidence:
        raise ValueError(f"{field}.evidence 不得为空。")
    return EntityRef(
        id=_text(item.get("id"), f"{field}.id", limit=240),
        kind=_text(item.get("kind"), f"{field}.kind", limit=80),
        label=_text(item.get("label"), f"{field}.label", limit=240),
        path=_path(path, f"{field}.path") if path is not None else None,
        layer=layer,  # type: ignore[arg-type]
        source=_text(item.get("source"), f"{field}.source", limit=120),
        confidence=float(_number(item.get("confidence"), f"{field}.confidence")),
        evidence=evidence,
    ).to_dict()


def _relation(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    layer = _text(item.get("layer"), f"{field}.layer", limit=2)
    if layer not in {"L1", "L2", "L3"}:
        raise ValueError(f"{field}.layer 无效。")
    evidence = _evidence_objects(item.get("evidence", []), f"{field}.evidence")
    return GraphEdge(
        id=_text(item.get("id"), f"{field}.id", limit=240),
        source_id=_text(item.get("sourceId"), f"{field}.sourceId", limit=240),
        target_id=_text(item.get("targetId"), f"{field}.targetId", limit=240),
        relation=_text(item.get("relation"), f"{field}.relation", limit=120),
        layer=layer,  # type: ignore[arg-type]
        source=_text(item.get("source"), f"{field}.source", limit=120),
        confidence=float(_number(item.get("confidence"), f"{field}.confidence")),
        evidence=evidence,
    ).to_dict()


def _stop(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    kind = _text(item.get("kind"), f"{field}.kind", limit=80)
    if kind not in _STOP_KINDS:
        raise ValueError(f"{field}.kind 无效。")
    stop_id = _text(item.get("id"), f"{field}.id", limit=240)
    title = _text(item.get("title"), f"{field}.title", limit=240)
    plain = _text(item.get("plainLanguage"), f"{field}.plainLanguage")
    technical = _text(item.get("technicalExplanation"), f"{field}.technicalExplanation")
    evidence = _evidence_objects(item.get("evidence", []), f"{field}.evidence")
    if not all((stop_id, title, plain, technical)) or not evidence:
        raise ValueError(f"{field} 缺少必需内容或 Evidence。")
    return {
        "id": stop_id,
        "kind": kind,
        "title": title,
        "plainLanguage": plain,
        "technicalExplanation": technical,
        "evidence": [item.to_dict() for item in evidence],
        "entity": _entity(item.get("entity"), f"{field}.entity"),
        "relations": [
            _relation(relation, f"{field}.relations[]")
            for relation in _sequence(item.get("relations", []), f"{field}.relations")[:4]
        ],
    }


def _tour_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(core_data, "repo-tour")
    raw_tiers = _sequence(data.get("tiers", []), "repo-tour.tiers")
    if len(raw_tiers) != 3:
        raise ValueError("repo-tour.tiers 必须恰好包含三个固定深度。")
    tiers_by_depth = {
        _text(_mapping(tier, "repo-tour.tiers[]").get("depth"), "tiers.depth", limit=32):
        _mapping(tier, "repo-tour.tiers[]")
        for tier in raw_tiers
    }
    if len(tiers_by_depth) != 3 or set(tiers_by_depth) != set(_DEPTHS):
        raise ValueError("repo-tour.tiers 的 depth 必须唯一且固定。")
    tiers = []
    for depth in _DEPTHS:
        tier = tiers_by_depth.get(depth)
        if tier is None:
            raise ValueError(f"缺少固定 Tour 深度：{depth}")
        raw_stops = _sequence(tier.get("stops", []), "tiers.stops")
        if len(raw_stops) > _LIMITS[depth]:
            raise ValueError(f"{depth} stops 超过上限。")
        stops = [_stop(stop, f"tiers.{depth}.stops[]") for stop in raw_stops]
        if any(stop["kind"] not in _KINDS_BY_DEPTH[depth] for stop in stops):
            raise ValueError(f"{depth} 包含不允许的 stop kind。")
        tiers.append(
            {
                "depth": depth,
                "label": _text(tier.get("label"), "tiers.label", limit=80),
                "stops": stops,
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
    _, warnings = _source_state(core_data)
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
