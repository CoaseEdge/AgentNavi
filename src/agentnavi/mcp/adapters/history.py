"""History Core 到 Timeline / Project Story 的严格公开投影。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
from typing import Any

from ..protocol import AgentNaviView
from .repo_overview import (
    _evidence_list, _evidence_reference, _mapping, _project, _sequence,
    _source_state, _text,
)
from .repo_tour import _entity, _relation

_RELATIONS = {"read", "modified", "tested", "searched", "affects"}


def _required(value: Any, field: str, limit: int = 480) -> str:
    result = _text(value, field, limit=limit)
    if not result.strip():
        raise ValueError(f"{field} 不得为空。")
    return result


def _timestamp(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    result = _required(value, field, 64)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None or not result.endswith("Z"):
        raise ValueError(f"{field} 必须是时间。")
    return result


def _relation_item(value: Any, task: dict[str, Any], field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    entity = _entity(item.get("entity"), f"{field}.entity")
    relation = _relation(item.get("relation"), f"{field}.relation")
    evidence = _evidence_list(item.get("evidence", []), f"{field}.evidence")
    order = item.get("recordedOrder")
    if (
        relation["layer"] != "L3" or relation["source"] != "task-events"
        or relation["relation"] not in _RELATIONS or relation["sourceId"] != task["id"]
        or relation["targetId"] != entity["id"] or relation["evidence"] != evidence
        or (entity["kind"], entity["layer"]) not in {("file", "L1"), ("concept", "L2")}
        or (entity["kind"] == "file" and not entity.get("path"))
        or isinstance(order, bool) or not isinstance(order, int) or order < 1
        or not evidence or len(evidence) > 3
        or any(entry["layer"] != "L3" or entry["source"] != "task-events" for entry in evidence)
    ):
        raise ValueError(f"{field} provenance 无效。")
    return {"entity": entity, "relation": relation, "recordedOrder": order, "evidence": evidence}


def _timeline_item(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    task = _entity(item.get("entity"), f"{field}.entity")
    evidence = _evidence_list(item.get("evidence", []), f"{field}.evidence")
    if (
        task["kind"] != "task" or task["layer"] != "L3" or task["source"] != "task-events"
        or task["evidence"] != evidence or not evidence or len(evidence) > 3
        or any(entry["layer"] != "L3" or entry["source"] != "task-events" for entry in evidence)
    ):
        raise ValueError(f"{field} task provenance 无效。")
    raw_relations = _sequence(item.get("relations", []), f"{field}.relations")
    if len(raw_relations) > 12:
        raise ValueError(f"{field}.relations 超过上限。")
    relations = [_relation_item(raw, task, f"{field}.relations[]") for raw in raw_relations]
    if len({entry["relation"]["id"] for entry in relations}) != len(relations):
        raise ValueError(f"{field}.relations id 重复。")
    return {
        "entity": task, "taskId": _required(item.get("taskId"), f"{field}.taskId", 240),
        "status": _required(item.get("status"), f"{field}.status", 40),
        "summary": _required(item.get("summary"), f"{field}.summary"),
        "createdAt": _timestamp(item.get("createdAt"), f"{field}.createdAt"),
        "updatedAt": _timestamp(item.get("updatedAt"), f"{field}.updatedAt"),
        "closedAt": _timestamp(item.get("closedAt"), f"{field}.closedAt", optional=True),
        "sortTime": _timestamp(item.get("sortTime"), f"{field}.sortTime"),
        "relations": relations, "evidence": evidence,
    }


def _history_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    if core_data.get("layout") != "task-timeline-story":
        raise ValueError("history.layout 无效。")
    mode = _required(core_data.get("selectedMode"), "history.selectedMode", 16)
    if mode not in {"timeline", "story"}:
        raise ValueError("history.selectedMode 无效。")
    raw_timeline = _sequence(core_data.get("timeline", []), "history.timeline")
    if len(raw_timeline) > 20:
        raise ValueError("history.timeline 超过上限。")
    timeline = [_timeline_item(item, "history.timeline[]") for item in raw_timeline]
    if any(
        (timeline[index]["sortTime"], timeline[index]["taskId"])
        < (timeline[index + 1]["sortTime"], timeline[index + 1]["taskId"])
        for index in range(len(timeline) - 1)
    ):
        raise ValueError("history.timeline 排序无效。")
    task_by_id = {entry["entity"]["id"]: entry for entry in timeline}
    if len(task_by_id) != len(timeline):
        raise ValueError("history.timeline task id 重复。")
    entity_registry: dict[str, str] = {}
    edge_registry: dict[str, str] = {}
    for timeline_item in timeline:
        for entity in [timeline_item["entity"], *(entry["entity"] for entry in timeline_item["relations"])]:
            signature = json.dumps(entity, ensure_ascii=False, sort_keys=True)
            if entity["id"] in edge_registry or entity_registry.setdefault(entity["id"], signature) != signature:
                raise ValueError("history entity registry 冲突。")
        for relation_item in timeline_item["relations"]:
            relation = relation_item["relation"]
            signature = json.dumps(relation, ensure_ascii=False, sort_keys=True)
            if relation["id"] in entity_registry or edge_registry.setdefault(relation["id"], signature) != signature:
                raise ValueError("history relation registry 冲突。")
    raw_story = _sequence(core_data.get("story", []), "history.story")
    if len(raw_story) > 12:
        raise ValueError("history.story 超过上限。")
    story = []
    disclaimer = _required(core_data.get("disclaimer"), "history.disclaimer")
    for raw in raw_story:
        item = _mapping(raw, "history.story[]")
        task = _entity(item.get("task"), "history.story.task")
        evidence = _evidence_list(item.get("evidence", []), "history.story.evidence")
        if task["id"] not in task_by_id or task != task_by_id[task["id"]]["entity"] or evidence != task["evidence"]:
            raise ValueError("history.story task 无效。")
        raw_groups = _sequence(item.get("groups", []), "history.story.groups")
        if len(raw_groups) > 5:
            raise ValueError("history.story groups 超过上限。")
        groups = []
        expected_groups: dict[str, dict[str, Any]] = {}
        for relation_item in task_by_id[task["id"]]["relations"]:
            relation_name = relation_item["relation"]["relation"]
            expected = expected_groups.setdefault(relation_name, {"paths": [], "concepts": [], "evidence": []})
            entity = relation_item["entity"]
            target_values = expected["paths"] if entity["kind"] == "file" else expected["concepts"]
            target = entity.get("path", entity["label"])
            if target not in target_values:
                target_values.append(target)
            evidence_item = relation_item["evidence"][0]
            if evidence_item not in expected["evidence"] and len(expected["evidence"]) < 3:
                expected["evidence"].append(evidence_item)
        for expected in expected_groups.values():
            expected["paths"].sort(key=lambda value: (value.lower(), value))
            expected["concepts"].sort(key=lambda value: (value.lower(), value))
        for raw_group in raw_groups:
            group = _mapping(raw_group, "history.story.groups[]")
            relation = _required(group.get("relation"), "history.story.relation", 120)
            paths = [_required(path, "history.story.path", 480) for path in _sequence(group.get("paths", []), "history.story.paths")]
            concepts = [_required(value, "history.story.concept", 240) for value in _sequence(group.get("concepts", []), "history.story.concepts")]
            group_evidence = _evidence_list(group.get("evidence", []), "history.story.group.evidence")
            if relation not in _RELATIONS or len(paths) > 12 or len(concepts) > 12 or len(group_evidence) > 3:
                raise ValueError("history.story group 无效。")
            expected = expected_groups.get(relation)
            if expected is None or paths != expected["paths"] or concepts != expected["concepts"] or group_evidence != expected["evidence"]:
                raise ValueError("history.story group 不是对应 L3 关系的严格聚合。")
            groups.append({"relation": relation, "paths": paths, "concepts": concepts, "evidence": group_evidence})
        if {group["relation"] for group in groups} != set(expected_groups):
            raise ValueError("history.story groups 不完整。")
        if item.get("explanationSource") != "l3-aggregation" or item.get("disclaimer") != disclaimer:
            raise ValueError("history.story aggregation 声明无效。")
        story.append({
            "id": _required(item.get("id"), "history.story.id", 240),
            "title": _required(item.get("title"), "history.story.title", 240),
            "summary": _required(item.get("summary"), "history.story.summary"),
            "sortTime": _timestamp(item.get("sortTime"), "history.story.sortTime"),
            "task": task, "groups": groups, "explanationSource": "l3-aggregation",
            "disclaimer": disclaimer, "evidence": evidence,
        })
    raw_detail = core_data.get("taskDetail")
    detail = _timeline_item(raw_detail, "history.taskDetail") if raw_detail is not None else None
    if detail is not None and detail["entity"]["id"] not in task_by_id:
        raise ValueError("history.taskDetail 不属于 timeline。")
    stats = _mapping(core_data.get("stats"), "history.stats")
    parsed_stats = {}
    for key in ("files", "tasks", "displayedTasks", "displayedRelations"):
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"history.stats.{key} 无效。")
        parsed_stats[key] = value
    return {
        "layout": "task-timeline-story", "revision": _required(core_data.get("revision"), "history.revision", 120),
        "selectedMode": mode, "disclaimer": disclaimer, "timeline": timeline,
        "story": story, "taskDetail": detail, "stats": parsed_stats,
    }


def history_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    if len(warnings) > 10:
        raise ValueError("history warnings 超过上限。")
    return AgentNaviView(
        view="history", project=_project(core_data), source_state=source_state,
        data=_history_payload(core_data), warnings=tuple(warnings),
    )


def history_text(core_data: Mapping[str, Any]) -> str:
    project = _project(core_data)
    data = _history_payload(core_data)
    _, warnings = _source_state(core_data)
    lines = ["[AgentNavi 项目历史]", f"项目：{project.name}（{project.id}）", f"说明：{data['disclaimer']}", "", "Task Timeline（新到旧）："]
    for item in data["timeline"]:
        lines.append(f"- {item['sortTime']} · {item['entity']['label']} [{item['status']}] — {item['summary']}")
        for relation in item["relations"]:
            target = relation["entity"].get("path", relation["entity"]["label"])
            lines.append(f"  - {relation['relation']['relation']} → {target}（Evidence：{_evidence_reference(relation['evidence'])}）")
    lines.extend(["", "Project Story："])
    for item in data["story"]:
        lines.append(f"- {item['sortTime']} · {item['title']}：{item['summary']}")
        for group in item["groups"]:
            targets = "、".join([*group["paths"], *group["concepts"]]) or "无可展示目标"
            lines.append(f"  - {group['relation']}：{targets}（Evidence：{_evidence_reference(group['evidence'])}）")
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["history_text", "history_view"]
