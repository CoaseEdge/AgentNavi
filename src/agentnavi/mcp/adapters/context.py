"""Context Core 数据到模型文本和 VLA DTO 的安全投影。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...privacy import contains_private_path
from ...query import format_context
from ..protocol import AgentNaviView, Project, SourceState, Warning
from .repo_overview import _evidence, _evidence_list
from .repo_tour import _entity, _relation

_NOT_INDEXED_MESSAGE = "项目尚未完成索引，当前结果可能不完整。"
_ACTION_KINDS = (
    ("purpose", "它做什么"),
    ("relevance", "为什么相关"),
    ("dependents", "谁依赖它"),
    ("history", "过去谁改过"),
    ("impact", "如果改它"),
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
    if contains_private_path(value):
        return "[查询含路径，已隐藏]"
    return value[:500]


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


def _required_text(value: Any, field: str, *, limit: int = 320) -> str:
    result = _text(value, field, limit=limit)
    if not result.strip():
        raise ValueError(f"{field} 不得为空。")
    return result


def _chain_entry(value: Any, path: str) -> dict[str, Any]:
    item = _mapping(value, "context.navigation.chain")
    source = _entity(item.get("sourceConcept"), "chain.sourceConcept")
    related_raw = item.get("relatedConcept")
    relation_raw = item.get("conceptRelation")
    related = (
        _entity(related_raw, "chain.relatedConcept")
        if related_raw is not None else None
    )
    concept_relation = (
        _relation(relation_raw, "chain.conceptRelation")
        if relation_raw is not None else None
    )
    file_entity = _entity(item.get("file"), "chain.file")
    file_relation = _relation(item.get("fileRelation"), "chain.fileRelation")
    evidence = _evidence_list(item.get("evidence", []), "chain.evidence")
    if source["kind"] != "concept" or source["layer"] != "L2":
        raise ValueError("chain.sourceConcept 必须是 L2 concept。")
    if (
        file_entity["kind"] != "file" or file_entity["layer"] != "L1"
        or file_entity.get("path") != path or file_relation["layer"] != "L2"
        or file_relation["targetId"] != file_entity["id"] or not evidence
    ):
        raise ValueError("chain file provenance 无效。")
    if related is None or concept_relation is None:
        if related is not None or concept_relation is not None:
            raise ValueError("chain concept relation 必须成对出现。")
        if file_relation["sourceId"] != source["id"]:
            raise ValueError("direct chain endpoint 无效。")
    else:
        if related["kind"] != "concept" or related["layer"] != "L2":
            raise ValueError("chain.relatedConcept 必须是 L2 concept。")
        if concept_relation["layer"] != "L2" or {
            concept_relation["sourceId"], concept_relation["targetId"]
        } != {source["id"], related["id"]}:
            raise ValueError("chain concept relation endpoint 无效。")
        if file_relation["sourceId"] != related["id"]:
            raise ValueError("one-hop chain file endpoint 无效。")
    return {
        "sourceConcept": source,
        "conceptRelation": concept_relation,
        "relatedConcept": related,
        "fileRelation": file_relation,
        "file": file_entity,
        "evidence": evidence[:3],
    }


def _navigation_payload(value: Any, candidate_paths: set[str]) -> dict[str, Any]:
    item = _mapping(value, "context.navigation")
    raw_reading = _sequence(item.get("readingOrder", []), "navigation.readingOrder")
    if len(raw_reading) > 12:
        raise ValueError("navigation.readingOrder 超过候选上限。")
    reading: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_reading):
        entry = _mapping(raw, "navigation.readingOrder[]")
        position = entry.get("position")
        path = _path(entry.get("path"), "navigation.path")
        if (
            isinstance(position, bool) or not isinstance(position, int)
            or position != index + 1 or path in paths or path not in candidate_paths
        ):
            raise ValueError("navigation reading position/path 无效。")
        paths.add(path)
        evidence = _evidence_list(entry.get("evidence", []), "navigation.evidence")
        raw_chains = _sequence(entry.get("chains", []), "navigation.chains")
        if not evidence or not raw_chains or len(raw_chains) > 3:
            raise ValueError("navigation 缺少有界 Evidence/chain。")
        chains = [_chain_entry(chain, path) for chain in raw_chains]
        raw_actions = _sequence(entry.get("actions", []), "navigation.actions")
        if len(raw_actions) != len(_ACTION_KINDS):
            raise ValueError("navigation actions 必须完整。")
        actions = []
        for raw_action, (expected_kind, expected_label) in zip(
            raw_actions, _ACTION_KINDS, strict=True
        ):
            action = _mapping(raw_action, "navigation.actions[]")
            if action.get("kind") != expected_kind or action.get("label") != expected_label:
                raise ValueError("navigation action 语义或顺序无效。")
            action_evidence = _evidence_list(
                action.get("evidence", []), "navigation.action.evidence"
            )
            actions.append({
                "kind": expected_kind,
                "label": expected_label,
                "summary": _required_text(action.get("summary"), "navigation.action.summary"),
                "evidence": action_evidence[:3],
            })
        dependents = []
        for raw_dependent in _sequence(
            entry.get("dependents", []), "navigation.dependents"
        )[:4]:
            dependent = _mapping(raw_dependent, "navigation.dependents[]")
            dependent_path = _path(dependent.get("path"), "dependent.path")
            if dependent_path not in candidate_paths:
                raise ValueError("dependent 不得扩大候选集。")
            dependents.append({
                "path": dependent_path,
                "relation": _required_text(dependent.get("relation"), "dependent.relation", limit=80),
            })
        history = []
        for raw_history in _sequence(entry.get("history", []), "navigation.history")[:3]:
            history_item = _mapping(raw_history, "navigation.history[]")
            history.append({
                "id": _required_text(history_item.get("id"), "history.id", limit=240),
                "title": _required_text(history_item.get("title"), "history.title", limit=160),
                "status": _required_text(history_item.get("status"), "history.status", limit=40),
                "createdAt": _required_text(history_item.get("createdAt"), "history.createdAt", limit=64),
                "relation": _required_text(history_item.get("relation"), "history.relation", limit=80),
            })
        next_raw = entry.get("nextStep")
        next_step = None
        if next_raw is not None:
            next_item = _mapping(next_raw, "navigation.nextStep")
            next_step = {
                "path": _path(next_item.get("path"), "nextStep.path"),
                "reason": _required_text(next_item.get("reason"), "nextStep.reason"),
            }
        reading.append({
            "position": position,
            "path": path,
            "language": _required_text(entry.get("language"), "navigation.language", limit=80),
            "why": _required_text(entry.get("why"), "navigation.why"),
            "evidence": evidence[:3],
            "nextStep": next_step,
            "chains": chains,
            "actions": actions,
            "dependents": dependents,
            "history": history,
        })
    for index, entry in enumerate(reading):
        expected = reading[index + 1]["path"] if index + 1 < len(reading) else None
        actual = entry["nextStep"]["path"] if entry["nextStep"] is not None else None
        if actual != expected:
            raise ValueError("navigation.nextStep 必须指向紧邻阅读项。")
    return {
        "revision": _required_text(item.get("revision"), "navigation.revision", limit=240),
        "readingOrder": reading,
    }


def _context_payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    """按 Context view allowlist 逐字段复制，不透传 Core mapping。"""

    data = _mapping(core_data, "context")
    stats = _mapping(data.get("stats"), "context.stats")
    files = [
        _file_entry(file_entry)
        for file_entry in _sequence(data.get("files", []), "context.files")[:12]
    ]
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
        "files": files,
        "tasks": [
            _task_entry(task)
            for task in _sequence(data.get("tasks", []), "context.tasks")[:5]
        ],
        "navigation": _navigation_payload(
            data.get("navigation", {}), {entry["path"] for entry in files}
        ),
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
        source_warning = (Warning(code="SOURCE_NOT_INDEXED", message=_NOT_INDEXED_MESSAGE),)
        return SourceState(status="partial"), (*source_warning, *_core_warnings(core_data))
    indexed_at = _text(last_scan_at, "context.project.last_scan_at", limit=64)
    if indexed_at.endswith("+00:00"):
        indexed_at = indexed_at[:-6] + "Z"
    navigation = _mapping(core_data.get("navigation", {}), "context.navigation")
    revision = _text(navigation.get("revision", ""), "navigation.revision", limit=240)
    warnings = _core_warnings(core_data)
    status = (
        "stale"
        if any(
            warning.code in {
                "CONTEXT_NAVIGATION_STALE_FILES",
                "CONTEXT_NAVIGATION_FRESHNESS_BUDGET",
            }
            for warning in warnings
        )
        else "ready"
    )
    return (
        SourceState(status=status, indexed_at=indexed_at, revision=revision or None),
        warnings,
    )


def _core_warnings(core_data: Mapping[str, Any]) -> tuple[Warning, ...]:
    result = []
    for raw in _sequence(core_data.get("warnings", []), "context.warnings")[:8]:
        item = _mapping(raw, "context.warnings[]")
        result.append(Warning(
            code=_required_text(item.get("code"), "warning.code", limit=80),
            message=_required_text(item.get("message"), "warning.message"),
            evidence=tuple(
                _evidence(entry, "warning.evidence[]")
                for entry in _sequence(item.get("evidence", []), "warning.evidence")[:3]
            ),
        ))
    return tuple(result)


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
