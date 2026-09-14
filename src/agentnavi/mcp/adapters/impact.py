"""Impact Core 到固定布局与模型文本的严格投影。"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView
from .repo_overview import _evidence_list, _mapping, _path, _project, _sequence, _source_state, _text
from .repo_tour import _entity, _relation

_ACTIONS = (("purpose", "它做什么"), ("callers", "谁调用它"),
            ("dependencies", "它依赖谁"), ("change", "如果修改它"),
            ("history", "过去谁改过它"))
_ANCHOR_MAPPINGS = {"implemented_by", "configured_by"}
_OWNERSHIP_MAPPINGS = _ANCHOR_MAPPINGS | {"tested_by"}


def _required(value: Any, field: str, limit: int = 320) -> str:
    result = _text(value, field, limit=limit)
    if not result.strip():
        raise ValueError(f"{field} 不得为空。")
    return result


def _evidence3(value: Any, field: str, required: bool = True) -> list[dict[str, Any]]:
    raw = _sequence(value, field)
    if len(raw) > 3:
        raise ValueError(f"{field} 最多包含 3 项。")
    result = _evidence_list(raw, field)
    if required and not result:
        raise ValueError(f"{field} 不得为空。")
    return result


def _sig(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _impact_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    if core_data.get("layout") != "incoming-focus-outgoing":
        raise ValueError("impact.layout 无效。")
    entities: dict[str, str] = {}
    edges: dict[str, str] = {}
    known_evidence: set[str] = set()

    def entity(value: Any, field: str) -> dict[str, Any]:
        raw = _mapping(value, field)
        raw_evidence = _sequence(raw.get("evidence", []), f"{field}.evidence")
        if not raw_evidence or len(raw_evidence) > 8:
            raise ValueError(f"{field}.evidence 无效。")
        parsed = _entity(value, field)
        signature = _sig({key: parsed.get(key) for key in
                          ("kind", "label", "path", "layer", "source", "confidence", "evidence")})
        if entities.setdefault(parsed["id"], signature) != signature or parsed["id"] in edges:
            raise ValueError("impact entity id 冲突。")
        known_evidence.update(_sig(item) for item in parsed["evidence"])
        return parsed

    def edge(value: Any, field: str) -> dict[str, Any]:
        raw = _mapping(value, field)
        _evidence3(raw.get("evidence", []), f"{field}.evidence")
        parsed = _relation(raw, field)
        signature = _sig(parsed)
        if edges.setdefault(parsed["id"], signature) != signature or parsed["id"] in entities:
            raise ValueError("impact edge id 冲突。")
        known_evidence.update(_sig(item) for item in parsed["evidence"])
        return parsed

    def evidence(value: Any, field: str, relation: dict[str, Any] | None = None,
                 required: bool = True) -> list[dict[str, Any]]:
        parsed = _evidence3(value, field, required)
        if relation is not None and parsed != relation["evidence"]:
            raise ValueError(f"{field} 与 relation evidence 不一致。")
        return parsed

    focus_raw = _mapping(core_data.get("focus"), "impact.focus")
    focus = entity(focus_raw.get("entity"), "impact.focus.entity")
    if (focus["kind"], focus["layer"]) not in {("file", "L1"), ("concept", "L2")} or \
            (focus["kind"] == "file" and not focus.get("path")):
        raise ValueError("impact focus 无效。")
    focus_evidence = evidence(focus_raw.get("evidence", []), "impact.focus.evidence")
    if focus_evidence != focus["evidence"]:
        raise ValueError("impact focus evidence 不一致。")

    raw_anchors = _sequence(core_data.get("anchorFiles", []), "impact.anchorFiles")
    if len(raw_anchors) > 8:
        raise ValueError("impact anchorFiles 超过上限。")
    anchors, anchor_by_path = [], {}
    for raw in raw_anchors:
        item = _mapping(raw, "impact.anchorFiles[]")
        file = entity(item.get("entity"), "impact.anchorFiles.entity")
        if file["kind"] != "file" or file["layer"] != "L1" or not file.get("path") or file["path"] in anchor_by_path:
            raise ValueError("impact anchor 无效。")
        mapping = edge(item["mapping"], "impact.anchorFiles.mapping") if item.get("mapping") is not None else None
        wrapper = evidence(item.get("evidence", []), "impact.anchorFiles.evidence", mapping)
        if focus["kind"] == "concept":
            if mapping is None or mapping["layer"] != "L2" or mapping["relation"] not in _ANCHOR_MAPPINGS or \
                    (mapping["sourceId"], mapping["targetId"]) != (focus["id"], file["id"]):
                raise ValueError("concept anchor mapping 无效。")
        elif mapping is not None or (file["id"], file["path"]) != (focus["id"], focus["path"]):
            raise ValueError("file identity anchor 无效。")
        entry = {"entity": file, "mapping": mapping, "evidence": wrapper}
        anchors.append(entry); anchor_by_path[file["path"]] = entry
    if focus["kind"] == "file" and len(anchors) > 1:
        raise ValueError("file focus anchor 数量无效。")

    raw_concepts = _sequence(core_data.get("focusConcepts", []), "impact.focusConcepts")
    if len(raw_concepts) > 8:
        raise ValueError("impact focusConcepts 超过上限。")
    concepts, concept_ids = [], set()
    for raw in raw_concepts:
        item = _mapping(raw, "impact.focusConcepts[]")
        concept = entity(item.get("entity"), "impact.focusConcepts.entity")
        mapping = edge(item["mapping"], "impact.focusConcepts.mapping") if item.get("mapping") is not None else None
        wrapper = evidence(item.get("evidence", []), "impact.focusConcepts.evidence", mapping)
        if concept["kind"] != "concept" or concept["layer"] != "L2" or concept["id"] in concept_ids:
            raise ValueError("focusConcept 无效。")
        if focus["kind"] == "concept":
            if concept["id"] != focus["id"] or mapping is not None:
                raise ValueError("concept identity 无效。")
        elif mapping is None or mapping["layer"] != "L2" or mapping["relation"] not in _OWNERSHIP_MAPPINGS or \
                (mapping["sourceId"], mapping["targetId"]) != (concept["id"], focus["id"]):
            raise ValueError("file concept mapping 无效。")
        concepts.append({"entity": concept, "mapping": mapping, "evidence": wrapper}); concept_ids.add(concept["id"])

    def lane(value: Any, direction: str) -> dict[str, Any]:
        item = _mapping(value, f"impact.{direction}[]")
        peer = entity(item.get("peer"), f"impact.{direction}.peer")
        relation = edge(item.get("relation"), f"impact.{direction}.relation")
        via = _path(item.get("viaPath"), f"impact.{direction}.viaPath")
        wrapper = evidence(item.get("evidence", []), f"impact.{direction}.evidence", relation)
        anchor = anchor_by_path.get(via)
        recorded = item.get("recordedOrder")
        expected = ((peer["id"], anchor["entity"]["id"]) if direction == "incoming" and anchor else
                    (anchor["entity"]["id"], peer["id"]) if anchor else None)
        if peer["kind"] != "file" or peer["layer"] != "L1" or not peer.get("path") or \
                relation["layer"] != "L1" or anchor is None or \
                (relation["sourceId"], relation["targetId"]) != expected or \
                isinstance(recorded, bool) or not isinstance(recorded, int) or recorded < 1:
            raise ValueError("impact lane provenance 无效。")
        return {"peer": peer, "relation": relation, "viaPath": via,
                "recordedOrder": recorded, "evidence": wrapper}

    raw_in = _sequence(core_data.get("incoming", []), "impact.incoming")
    raw_out = _sequence(core_data.get("outgoing", []), "impact.outgoing")
    if len(raw_in) > 8 or len(raw_out) > 8:
        raise ValueError("impact lane 超过上限。")
    incoming = [lane(item, "incoming") for item in raw_in]
    outgoing = [lane(item, "outgoing") for item in raw_out]

    raw_semantic = _sequence(core_data.get("semantic", []), "impact.semantic")
    if len(raw_semantic) > 8:
        raise ValueError("impact semantic 超过上限。")
    semantic = []
    for raw in raw_semantic:
        item = _mapping(raw, "impact.semantic[]")
        direction = item.get("direction")
        focus_id = _required(item.get("focusConceptId"), "semantic.focusConceptId", 240)
        peer = entity(item.get("peer"), "impact.semantic.peer")
        relation = edge(item.get("relation"), "impact.semantic.relation")
        wrapper = evidence(item.get("evidence", []), "impact.semantic.evidence", relation)
        expected = (focus_id, peer["id"]) if direction == "outgoing" else (peer["id"], focus_id)
        if direction not in {"incoming", "outgoing"} or focus_id not in concept_ids or peer["kind"] != "concept" or \
                peer["layer"] != "L2" or peer["id"] == focus_id or relation["layer"] != "L2" or \
                (relation["sourceId"], relation["targetId"]) != expected:
            raise ValueError("impact semantic provenance 无效。")
        semantic.append({"direction": direction, "focusConceptId": focus_id, "peer": peer,
                         "relation": relation, "evidence": wrapper})

    raw_history = _sequence(core_data.get("history", []), "impact.history")
    if len(raw_history) > 5:
        raise ValueError("impact history 超过上限。")
    valid_targets = {focus["id"], *concept_ids, *(item["entity"]["id"] for item in anchors)}
    history = []
    for raw in raw_history:
        item = _mapping(raw, "impact.history[]")
        task = entity(item.get("entity"), "impact.history.entity")
        relation = edge(item.get("relation"), "impact.history.relation")
        wrapper = evidence(item.get("evidence", []), "impact.history.evidence", relation)
        recorded = item.get("recordedOrder")
        if task["kind"] != "task" or task["layer"] != "L3" or task["source"] != "task-events" or \
                relation["layer"] != "L3" or relation["source"] != "task-events" or relation["sourceId"] != task["id"] or \
                relation["targetId"] not in valid_targets or any(e["layer"] != "L3" or e["source"] != "task-events" for e in wrapper) or \
                task["evidence"] != wrapper or \
                isinstance(recorded, bool) or not isinstance(recorded, int) or recorded < 1:
            raise ValueError("impact history provenance 无效。")
        history.append({"entity": task, "status": _required(item.get("status"), "history.status", 40),
                        "relation": relation, "recordedOrder": recorded, "evidence": wrapper})

    raw_tests = _sequence(core_data.get("testRecommendations", []), "impact.testRecommendations")
    if len(raw_tests) > 5:
        raise ValueError("impact tests 超过上限。")
    lane_edges = {item["relation"]["id"]: item["relation"] for item in incoming + outgoing}
    tests = []
    for raw in raw_tests:
        item = _mapping(raw, "impact.testRecommendations[]")
        basis, path = item.get("basis"), _path(item.get("path"), "test.path")
        file = entity(item.get("entity"), "test.entity")
        relation = edge(item.get("relation"), "test.relation")
        wrapper = evidence(item.get("evidence", []), "test.evidence", relation)
        source = entity(item["sourceConcept"], "test.sourceConcept") if item.get("sourceConcept") is not None else None
        if file["kind"] != "file" or file["layer"] != "L1" or file.get("path") != path:
            raise ValueError("test entity 无效。")
        if basis == "physical-tests":
            if source is not None or relation["layer"] != "L1" or relation["relation"] != "tests" or \
                    relation["sourceId"] != file["id"] or relation["targetId"] not in {a["entity"]["id"] for a in anchors} or \
                    lane_edges.get(relation["id"]) != relation:
                raise ValueError("physical-tests provenance 无效。")
        elif basis == "semantic-tested-by":
            if source is None or source["id"] not in concept_ids or source["kind"] != "concept" or source["layer"] != "L2" or \
                    relation["layer"] != "L2" or relation["relation"] != "tested_by" or \
                    (relation["sourceId"], relation["targetId"]) != (source["id"], file["id"]):
                raise ValueError("semantic-tested-by provenance 无效。")
        else:
            raise ValueError("test basis 无效。")
        tests.append({"basis": basis, "path": path, "reason": _required(item.get("reason"), "test.reason"),
                      "sourceConcept": source, "entity": file, "relation": relation, "evidence": wrapper})

    def derived(value: Any, field: str, risk: bool = False) -> dict[str, Any]:
        item = _mapping(value, field); wrapper = evidence(item.get("evidence", []), f"{field}.evidence", required=risk)
        if any(_sig(entry) not in known_evidence for entry in wrapper):
            raise ValueError(f"{field}.evidence 不属于可见事实。")
        result = {"kind": _required(item.get("kind"), f"{field}.kind", 80),
                  "summary": _required(item.get("summary"), f"{field}.summary"), "evidence": wrapper}
        if risk:
            if item.get("severity") not in {"low", "medium", "high"}: raise ValueError("risk severity 无效。")
            result["severity"] = item["severity"]
        return result

    raw_risks = _sequence(core_data.get("risks", []), "impact.risks")
    if len(raw_risks) > 5: raise ValueError("impact risks 超过上限。")
    risks = [derived(item, "impact.risks[]", True) for item in raw_risks]
    raw_actions = _sequence(core_data.get("actions", []), "impact.actions")
    if len(raw_actions) != 5: raise ValueError("impact actions 必须完整。")
    actions = []
    for raw, (kind, label) in zip(raw_actions, _ACTIONS, strict=True):
        parsed = derived(raw, "impact.actions[]"); item = _mapping(raw, "impact.actions[]")
        if parsed["kind"] != kind or item.get("label") != label: raise ValueError("impact action 无效。")
        actions.append({**parsed, "label": label})
    stats = _mapping(core_data.get("stats"), "impact.stats")
    return {"layout": "incoming-focus-outgoing", "revision": _required(core_data.get("revision"), "impact.revision", 240),
            "focus": {"entity": focus, "evidence": focus_evidence}, "anchorFiles": anchors,
            "focusConcepts": concepts, "incoming": incoming, "outgoing": outgoing, "semantic": semantic,
            "history": history, "testRecommendations": tests, "risks": risks, "actions": actions,
            "stats": {"files": int(stats.get("files", 0)), "concepts": int(stats.get("concepts", 0)), "tasks": int(stats.get("tasks", 0))}}


def impact_to_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    state, warnings = _source_state(core_data)
    return AgentNaviView(view="impact", project=_project(core_data), source_state=state,
                         data=_impact_payload(core_data), warnings=tuple(warnings))


def impact_text(core_data: Mapping[str, Any]) -> str:
    project, data = _project(core_data), _impact_payload(core_data)
    _, warnings = _source_state(core_data); focus = data["focus"]["entity"]
    def refs(items: list[dict[str, Any]]) -> str:
        return "；".join(
            f"{item['path']}" if item.get("path") else f"{item['layer']} · {item['source']}"
            for item in items
        ) or "当前无可展示 Evidence"
    lines = ["[AgentNavi 影响分析]", f"项目：{project.name}（{project.id}）",
             f"Focus：{focus['label']}", f"  Evidence：{refs(data['focus']['evidence'])}", "", "锚点文件："]
    lines += [f"- {i['entity']['path']} · {i['mapping']['relation'] if i['mapping'] else 'identity'}\n  Evidence：{refs(i['evidence'])}" for i in data["anchorFiles"]]
    lines += ["", "关联概念："] + [f"- {i['entity']['label']} · {i['mapping']['relation'] if i['mapping'] else 'identity'}\n  Evidence：{refs(i['evidence'])}" for i in data["focusConcepts"]]
    lines += ["", "Semantic（上）："] + [f"- {i['direction']} · {i['relation']['relation']} · {i['peer']['label']}\n  Evidence：{refs(i['evidence'])}" for i in data["semantic"]]
    lines += ["", "Incoming → Focus → Outgoing："] + [f"- IN · {i['peer']['path']} → {i['viaPath']}\n  Evidence：{refs(i['evidence'])}" for i in data["incoming"]] + [f"- OUT · {i['viaPath']} → {i['peer']['path']}\n  Evidence：{refs(i['evidence'])}" for i in data["outgoing"]]
    lines += ["", "建议测试："] + [f"- {i['path']}：{i['reason']}\n  Evidence：{refs(i['evidence'])}" for i in data["testRecommendations"]]
    lines += ["", "风险位置："] + [f"- [{i['severity']}] {i['summary']}\n  Evidence：{refs(i['evidence'])}" for i in data["risks"]]
    lines += ["", "History（下，按关系记录顺序）："] + [f"- {i['entity']['label']} [{i['status']}]\n  Evidence：{refs(i['evidence'])}" for i in data["history"]]
    lines += ["", "本地查看："] + [f"- {i['label']}：{i['summary']}\n  Evidence：{refs(i['evidence'])}" for i in data["actions"]]
    if warnings: lines += ["", "提示："] + [f"- {w.code}：{w.message}" for w in warnings]
    return "\n".join(lines)


__all__ = ["impact_text", "impact_to_view"]
