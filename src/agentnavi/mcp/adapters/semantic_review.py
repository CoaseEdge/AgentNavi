"""Semantic Review Core 到 MCP Apps / 文本的严格投影。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..protocol import AgentNaviView, EntityRef, Evidence
from .repo_overview import _mapping, _path, _project, _sequence, _source_state, _text


def _evidence(value: Any, field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _sequence(value, field)[:3]:
        item = _mapping(raw, f"{field}[]")
        result.append(Evidence(
            kind=_text(item.get("kind"), f"{field}.kind", limit=80),
            summary=_text(item.get("summary"), f"{field}.summary"),
            layer=_text(item.get("layer"), f"{field}.layer", limit=2),  # type: ignore[arg-type]
            source=_text(item.get("source"), f"{field}.source", limit=120),
            confidence=float(item.get("confidence")),
            path=_path(item.get("path"), f"{field}.path") if item.get("path") is not None else None,
        ).to_dict())
    if not result:
        raise ValueError(f"{field} 不得为空。")
    return result


def _entity(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    parsed = EntityRef(
        id=_text(item.get("id"), f"{field}.id", limit=240),
        kind="concept",
        label=_text(item.get("label"), f"{field}.label", limit=240),
        layer="L2",
        source=_text(item.get("source"), f"{field}.source", limit=120),
        confidence=float(item.get("confidence")),
        evidence=tuple(Evidence(**{
            "kind": entry["kind"], "summary": entry["summary"], "layer": entry["layer"],
            "source": entry["source"], "confidence": entry["confidence"], "path": entry.get("path"),
        }) for entry in _evidence(item.get("evidence", []), f"{field}.evidence")),
    )
    return parsed.to_dict()


def _payload(core_data: Mapping[str, Any]) -> dict[str, Any]:
    if core_data.get("layout") != "semantic-review":
        raise ValueError("semantic-review.layout 无效。")
    items: list[dict[str, Any]] = []
    for raw in _sequence(core_data.get("reviewItems", []), "semantic-review.reviewItems")[:100]:
        item = _mapping(raw, "semantic-review.reviewItems[]")
        actions = _sequence(item.get("allowedActions", []), "semantic-review.allowedActions")
        if any(action not in {"accept", "reject"} for action in actions):
            raise ValueError("semantic-review.allowedActions 无效。")
        subject = _entity(item.get("subject"), "semantic-review.subject")
        object_value = _entity(item["object"], "semantic-review.object") if item.get("object") is not None else None
        evidence = _evidence(item.get("evidence", []), "semantic-review.evidence")
        if subject["evidence"] != evidence and object_value is not None and object_value["evidence"] != evidence:
            raise ValueError("semantic-review evidence 必须绑定实体。")
        items.append({
            "reviewId": _text(item.get("reviewId"), "semantic-review.reviewId", limit=240),
            "subject": subject,
            "relation": _text(item.get("relation"), "semantic-review.relation", limit=120),
            "object": object_value,
            "confidence": float(item.get("confidence")),
            "source": _text(item.get("source"), "semantic-review.source", limit=120),
            "evidence": evidence,
            "allowedActions": list(actions),
            "decision": item.get("decision") if item.get("decision") in {None, "accepted", "rejected"} else None,
        })
    stats = _mapping(core_data.get("stats"), "semantic-review.stats")
    return {
        "layout": "semantic-review",
        "revision": _text(core_data.get("revision"), "semantic-review.revision", limit=120),
        "reviewItems": items,
        "includeReviewed": bool(core_data.get("includeReviewed", False)),
        "stats": {key: int(stats.get(key, 0)) for key in ("candidates", "pending", "reviewed")},
    }


def semantic_review_view(core_data: Mapping[str, Any]) -> AgentNaviView:
    source_state, warnings = _source_state(core_data)
    return AgentNaviView(
        view="semantic-review", project=_project(core_data), source_state=source_state,
        data=_payload(core_data), warnings=tuple(warnings),
    )


def semantic_review_text(core_data: Mapping[str, Any]) -> str:
    data = _payload(core_data)
    lines = [f"Semantic Review（{data['stats']['pending']} 个待审查候选）"]
    for item in data["reviewItems"]:
        target = item["object"]["label"] if item["object"] else "概念候选"
        lines.append(
            f"- {item['subject']['label']} —{item['relation']}→ {target} · "
            f"confidence={item['confidence']:.2f} · source={item['source']} · "
            f"actions={','.join(item['allowedActions']) or '已审查'}"
        )
    return "\n".join(lines)


__all__ = ["semantic_review_text", "semantic_review_view"]
