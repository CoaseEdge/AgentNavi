"""Architecture Core 数据到固定认知布局与文本的严格投影。"""

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
)
from .repo_tour import _entity, _relation


def _required_text(value: Any, field: str, *, limit: int = 320) -> str:
    candidate = _text(value, field, limit=limit)
    if not candidate.strip():
        raise ValueError(f"{field} 不得为空。")
    return candidate


def _stats(value: Any) -> dict[str, int]:
    item = _mapping(value, "architecture.stats")
    return {
        "files": int(_number(item.get("files"), "stats.files")),
        "concepts": int(_number(item.get("concepts"), "stats.concepts")),
        "tasks": int(_number(item.get("tasks"), "stats.tasks")),
        "documentsRead": int(_number(item.get("documentsRead"), "stats.documentsRead")),
    }


def _architecture_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    data = _mapping(core_data, "architecture")
    if data.get("layout") != "cognitive-components":
        raise ValueError("architecture.layout 无效。")
    summary = _mapping(data.get("summary"), "architecture.summary")
    if summary.get("explanationSource") != "derived-presentation":
        raise ValueError("architecture.summary.explanationSource 无效。")
    raw_components = _sequence(data.get("components", []), "architecture.components")
    if len(raw_components) > 8:
        raise ValueError("architecture.components 超过上限。")
    components = []
    component_ids: set[str] = set()
    for raw in raw_components:
        item = _mapping(raw, "architecture.components[]")
        component_id = _text(item.get("id"), "component.id", limit=240)
        group = _text(item.get("group"), "component.group", limit=16)
        if not component_id or component_id in component_ids or group not in {"entry", "core", "support"}:
            raise ValueError("architecture component id/group 无效。")
        paths = [_path(path, "component.paths[]") for path in _sequence(item.get("paths", []), "component.paths")]
        if not paths or len(paths) > 3 or len(paths) != len(set(paths)):
            raise ValueError("architecture component paths 无效。")
        raw_evidence = _sequence(item.get("evidence", []), "component.evidence")
        if len(raw_evidence) > 3:
            raise ValueError("architecture component evidence 超过上限。")
        evidence = _evidence_list(raw_evidence, "component.evidence")
        entity = _entity(item.get("entity"), "component.entity")
        if not evidence or entity["id"] != component_id or entity["layer"] != "L2":
            raise ValueError("architecture component provenance 无效。")
        components.append({
            "id": component_id,
            "name": _required_text(item.get("name"), "component.name", limit=240),
            "group": group,
            "responsibility": _required_text(item.get("responsibility"), "component.responsibility"),
            "paths": paths,
            "entity": entity,
            "evidence": evidence,
        })
        component_ids.add(component_id)

    raw_connections = _sequence(data.get("connections", []), "architecture.connections")
    if len(raw_connections) > 12:
        raise ValueError("architecture.connections 超过上限。")
    connections = [_relation(item, "architecture.connections[]") for item in raw_connections]
    connection_ids = [item["id"] for item in connections]
    if len(connection_ids) != len(set(connection_ids)) or any(
        item["layer"] != "L2" or item["sourceId"] not in component_ids or item["targetId"] not in component_ids
        for item in connections
    ):
        raise ValueError("architecture connection endpoint/id 无效。")

    raw_entries = _sequence(data.get("entryPoints", []), "architecture.entryPoints")
    if len(raw_entries) > 3:
        raise ValueError("architecture.entryPoints 超过上限。")
    entries = []
    for raw in raw_entries:
        item = _mapping(raw, "architecture.entryPoints[]")
        evidence = _evidence_list(item.get("evidence", []), "entryPoint.evidence")
        path = _path(item.get("path"), "entryPoint.path")
        entity = _entity(item.get("entity"), "entryPoint.entity")
        if not evidence:
            raise ValueError("architecture entryPoint evidence 不得为空。")
        if entity["kind"] != "file" or entity["layer"] != "L1" or entity.get("path") != path:
            raise ValueError("architecture entryPoint entity/path 无效。")
        entries.append({
            "path": path,
            "reason": _required_text(item.get("reason"), "entryPoint.reason"),
            "entity": entity,
            "evidence": evidence,
        })
    if len({item["path"] for item in entries}) != len(entries):
        raise ValueError("architecture entryPoint path 必须唯一。")
    return {
        "layout": "cognitive-components",
        "summary": {
            "text": _required_text(summary.get("text"), "summary.text"),
            "explanationSource": "derived-presentation",
            "evidence": _evidence_list(summary.get("evidence", []), "summary.evidence"),
        },
        "components": components,
        "connections": connections,
        "entryPoints": entries,
        "stats": _stats(data.get("stats")),
    }


def architecture_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="architecture",
        project=_project(core_data),
        source_state=source_state,
        data=_architecture_payload(core_data),
        warnings=tuple(warnings),
    )


def architecture_text(core_data: Mapping[str, Any]) -> str:
    project = _project(core_data)
    payload = _architecture_payload(core_data)
    _, warnings = _source_state(core_data)
    lines = ["[AgentNavi 系统架构]", f"项目：{project.name}（{project.id}）"]
    lines.append(payload["summary"]["text"] or "暂无足够的组件证据。")
    for group, label in (("entry", "入口"), ("core", "核心"), ("support", "支撑")):
        selected = [item for item in payload["components"] if item["group"] == group]
        if not selected:
            continue
        lines.extend(["", label + "："])
        lines.extend(
            f"- {item['name']}：{item['responsibility']}（{'、'.join(item['paths'])}）"
            for item in selected
        )
    if payload["connections"]:
        component_names = {
            item["id"]: item["name"] for item in payload["components"]
        }
        lines.extend(["", "真实组件关系："])
        lines.extend(
            f"- {component_names[edge['sourceId']]}（{edge['sourceId']}） "
            f"--{edge['relation']}--> "
            f"{component_names[edge['targetId']]}（{edge['targetId']}） · "
            f"{_evidence_reference(edge['evidence'])}"
            for edge in payload["connections"]
        )
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["architecture_text", "architecture_view"]
