"""Impact Core 到固定影响布局与模型文本的严格投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView
from .repo_overview import _evidence_list, _mapping, _path, _project, _sequence, _source_state, _text
from .repo_tour import _entity, _relation


_ACTIONS = (
    ("purpose", "它做什么"), ("callers", "谁调用它"),
    ("dependencies", "它依赖谁"), ("change", "如果修改它"),
    ("history", "过去谁改过它"),
)


def _required(value: Any, field: str, *, limit: int = 320) -> str:
    result = _text(value, field, limit=limit)
    if not result.strip():
        raise ValueError(f"{field} 不得为空。")
    return result


def _impact_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    if core_data.get("layout") != "incoming-focus-outgoing":
        raise ValueError("impact.layout 无效。")
    focus_raw = _mapping(core_data.get("focus"), "impact.focus")
    focus = _entity(focus_raw.get("entity"), "impact.focus.entity")
    if focus["kind"] not in {"file", "concept"} or focus["layer"] not in {"L1", "L2"}:
        raise ValueError("impact focus 必须是真实 L1 file 或 L2 concept。")
    anchor_raw = focus_raw.get("anchorFile")
    anchor = _entity(anchor_raw, "impact.focus.anchorFile") if anchor_raw is not None else None
    if anchor is not None and (anchor["kind"] != "file" or anchor["layer"] != "L1" or not anchor.get("path")):
        raise ValueError("impact anchorFile provenance 无效。")
    focus_evidence = _evidence_list(focus_raw.get("evidence", []), "impact.focus.evidence")
    if not focus_evidence or len(focus_evidence) > 3:
        raise ValueError("impact focus evidence 无效。")

    def lane(value: Any, direction: str) -> dict[str, Any]:
        item = _mapping(value, f"impact.{direction}[]")
        peer = _entity(item.get("peer"), f"impact.{direction}.peer")
        relation = _relation(item.get("relation"), f"impact.{direction}.relation")
        via_path = _path(item.get("viaPath"), f"impact.{direction}.viaPath")
        evidence = _evidence_list(item.get("evidence", []), f"impact.{direction}.evidence")
        anchor_ids = {focus["id"], anchor["id"] if anchor else focus["id"]}
        if (
            peer["kind"] != "file" or peer["layer"] != "L1" or not peer.get("path")
            or relation["layer"] != "L1" or not evidence or len(evidence) > 3
            or (direction == "incoming" and (relation["sourceId"] != peer["id"] or relation["targetId"] not in anchor_ids))
            or (direction == "outgoing" and (relation["targetId"] != peer["id"] or relation["sourceId"] not in anchor_ids))
        ):
            raise ValueError("impact physical lane provenance 无效。")
        return {"peer": peer, "relation": relation, "viaPath": via_path, "evidence": evidence}

    raw_incoming = _sequence(core_data.get("incoming", []), "impact.incoming")
    raw_outgoing = _sequence(core_data.get("outgoing", []), "impact.outgoing")
    if len(raw_incoming) > 8 or len(raw_outgoing) > 8:
        raise ValueError("impact physical lane 超过上限。")
    incoming = [lane(item, "incoming") for item in raw_incoming]
    outgoing = [lane(item, "outgoing") for item in raw_outgoing]

    semantic = []
    raw_semantic = _sequence(core_data.get("semantic", []), "impact.semantic")
    if len(raw_semantic) > 8:
        raise ValueError("impact semantic 超过上限。")
    for raw in raw_semantic:
        item = _mapping(raw, "impact.semantic[]")
        direction = item.get("direction")
        focus_concept = _entity(item.get("focusConcept"), "impact.semantic.focusConcept")
        peer = _entity(item.get("peer"), "impact.semantic.peer")
        relation = _relation(item.get("relation"), "impact.semantic.relation")
        evidence = _evidence_list(item.get("evidence", []), "impact.semantic.evidence")
        if (
            direction not in {"incoming", "outgoing"}
            or focus_concept["kind"] != "concept" or focus_concept["layer"] != "L2"
            or peer["kind"] != "concept" or peer["layer"] != "L2"
            or peer["id"] == focus_concept["id"] or relation["layer"] != "L2"
            or (direction == "outgoing" and (relation["sourceId"] != focus_concept["id"] or relation["targetId"] != peer["id"]))
            or (direction == "incoming" and (relation["targetId"] != focus_concept["id"] or relation["sourceId"] != peer["id"]))
            or not evidence or len(evidence) > 3
        ):
            raise ValueError("impact semantic provenance 无效。")
        semantic.append({"direction": direction, "focusConcept": focus_concept, "peer": peer, "relation": relation, "evidence": evidence})

    history = []
    raw_history = _sequence(core_data.get("history", []), "impact.history")
    if len(raw_history) > 5:
        raise ValueError("impact history 超过上限。")
    for raw in raw_history:
        item = _mapping(raw, "impact.history[]")
        entity = _entity(item.get("entity"), "impact.history.entity")
        relation = _relation(item.get("relation"), "impact.history.relation")
        evidence = _evidence_list(item.get("evidence", []), "impact.history.evidence")
        recorded = item.get("recordedOrder")
        if (
            entity["kind"] != "task" or entity["layer"] != "L3"
            or entity["source"] != "task-events" or relation["layer"] != "L3"
            or relation["source"] != "task-events"
            or any(entry["layer"] != "L3" or entry["source"] != "task-events" for entry in evidence)
            or isinstance(recorded, bool) or not isinstance(recorded, int)
            or recorded < 1 or not evidence
        ):
            raise ValueError("impact history provenance 无效。")
        history.append({"entity": entity, "status": _required(item.get("status"), "history.status", limit=40), "relation": relation, "recordedOrder": recorded, "evidence": evidence})

    tests = []
    raw_tests = _sequence(core_data.get("testRecommendations", []), "impact.testRecommendations")
    if len(raw_tests) > 5:
        raise ValueError("impact testRecommendations 超过上限。")
    lane_relation_ids = {item["relation"]["id"] for item in incoming + outgoing}
    for raw in raw_tests:
        item = _mapping(raw, "impact.testRecommendations[]")
        path = _path(item.get("path"), "test.path")
        entity = _entity(item.get("entity"), "test.entity")
        relation = _relation(item.get("relation"), "test.relation")
        evidence = _evidence_list(item.get("evidence", []), "test.evidence")
        if entity["kind"] != "file" or entity["layer"] != "L1" or entity.get("path") != path or relation["layer"] not in {"L1", "L2"} or relation["id"] not in lane_relation_ids or not evidence:
            raise ValueError("impact test recommendation provenance 无效。")
        tests.append({"path": path, "reason": _required(item.get("reason"), "test.reason"), "entity": entity, "relation": relation, "evidence": evidence})

    risks = []
    raw_risks = _sequence(core_data.get("risks", []), "impact.risks")
    if len(raw_risks) > 5:
        raise ValueError("impact risks 超过上限。")
    for raw in raw_risks:
        item = _mapping(raw, "impact.risks[]")
        severity = item.get("severity")
        evidence = _evidence_list(item.get("evidence", []), "risk.evidence")
        if severity not in {"low", "medium", "high"} or not evidence:
            raise ValueError("impact risk 必须有事实证据。")
        risks.append({"kind": _required(item.get("kind"), "risk.kind", limit=80), "severity": severity, "summary": _required(item.get("summary"), "risk.summary"), "evidence": evidence})

    raw_actions = _sequence(core_data.get("actions", []), "impact.actions")
    if len(raw_actions) != 5:
        raise ValueError("impact actions 必须完整。")
    actions = []
    for raw, (kind, label) in zip(raw_actions, _ACTIONS, strict=True):
        item = _mapping(raw, "impact.actions[]")
        evidence = _evidence_list(item.get("evidence", []), "action.evidence")
        if item.get("kind") != kind or item.get("label") != label or len(evidence) > 3:
            raise ValueError("impact action 语义或顺序无效。")
        actions.append({"kind": kind, "label": label, "summary": _required(item.get("summary"), "action.summary"), "evidence": evidence})
    stats = _mapping(core_data.get("stats"), "impact.stats")
    return {
        "layout": "incoming-focus-outgoing", "revision": _required(core_data.get("revision"), "impact.revision", limit=240),
        "focus": {"entity": focus, "anchorFile": anchor, "evidence": focus_evidence},
        "incoming": incoming, "outgoing": outgoing, "semantic": semantic, "history": history,
        "testRecommendations": tests, "risks": risks, "actions": actions,
        "stats": {"files": int(stats.get("files", 0)), "concepts": int(stats.get("concepts", 0)), "tasks": int(stats.get("tasks", 0))},
    }


def impact_to_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(view="impact", project=_project(core_data), source_state=source_state, data=_impact_payload(core_data), warnings=tuple(warnings))


def impact_text(core_data: Mapping[str, Any]) -> str:
    project = _project(core_data)
    data = _impact_payload(core_data)
    _, warnings = _source_state(core_data)
    focus = data["focus"]["entity"]
    lines = ["[AgentNavi 影响分析]", f"项目：{project.name}（{project.id}）", f"Focus：{focus['label']}" + (f" · {focus['path']}" if focus.get("path") else "")]
    lines.extend(["", "Semantic（上）："])
    lines.extend(f"- {item['direction']} · {item['relation']['relation']} · {item['peer']['label']}" for item in data["semantic"])
    lines.extend(["", "Incoming → Focus → Outgoing："])
    lines.extend(f"- IN · {item['peer']['path']} —{item['relation']['relation']}→ Focus" for item in data["incoming"])
    lines.extend(f"- OUT · Focus —{item['relation']['relation']}→ {item['peer']['path']}" for item in data["outgoing"])
    lines.extend(["", "建议测试："])
    lines.extend(f"- {item['path']}：{item['reason']}" for item in data["testRecommendations"])
    lines.extend(["", "风险位置："])
    lines.extend(f"- [{item['severity']}] {item['summary']}" for item in data["risks"])
    lines.extend(["", "History（下，按关系记录顺序）："])
    lines.extend(f"- {item['entity']['label']} [{item['status']}]" for item in data["history"])
    lines.extend(["", "本地查看："])
    lines.extend(f"- {item['label']}：{item['summary']}" for item in data["actions"])
    if warnings:
        lines.extend(["", "提示："])
        lines.extend(f"- {warning.code}：{warning.message}" for warning in warnings)
    return "\n".join(lines)


__all__ = ["impact_text", "impact_to_view"]
