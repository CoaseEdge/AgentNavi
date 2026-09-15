"""Semantic Review 的 Core 投影。

候选仍由现有 L2 查询产生；本模块只补充稳定的证据和服务器计算的动作集合，
不把 SQLite 行或项目根目录暴露给 MCP/UI。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .database import Database
from .semantic_overlays import list_review_candidates


def _revision(items: list[dict[str, Any]], include_reviewed: bool) -> str:
    raw = json.dumps({"items": items, "includeReviewed": include_reviewed}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def semantic_review_view_data(
    database: Database,
    project: sqlite3.Row,
    *,
    limit: int = 50,
    include_reviewed: bool = False,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("semantic review limit 必须是 1 到 100。")
    candidates = list_review_candidates(
        database, str(project["id"]), limit=limit, include_reviewed=include_reviewed
    )
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        subject = {
            "id": f"concept:{project['id']}:{candidate['subject_key']}",
            "kind": "concept",
            "label": str(candidate["label"]),
            "layer": "L2",
            "source": str(candidate["source"]),
            "confidence": float(candidate["confidence"]),
            "evidence": list(candidate.get("evidence", [])),
        }
        object_value = None
        if candidate["kind"] == "edge":
            object_value = {
                "id": f"concept:{project['id']}:{candidate['object_key']}",
                "kind": "concept",
                "label": str(candidate["object_label"]),
                "layer": "L2",
                "source": str(candidate["source"]),
                "confidence": float(candidate["confidence"]),
                "evidence": list(candidate.get("evidence", [])),
            }
        items.append({
            "reviewId": str(candidate["id"]),
            "subject": subject,
            "relation": str(candidate["relation"] or "concept-candidate"),
            "object": object_value,
            "confidence": float(candidate["confidence"]),
            "source": str(candidate["source"]),
            "evidence": list(candidate.get("evidence", [])),
            "allowedActions": list(candidate.get("allowed_actions", [])),
            "decision": candidate.get("decision"),
        })
    return {
        "layout": "semantic-review",
        "revision": _revision(items, include_reviewed),
        "reviewItems": items,
        "includeReviewed": include_reviewed,
        "stats": {
            "candidates": len(items),
            "pending": sum(item["decision"] is None for item in items),
            "reviewed": sum(item["decision"] is not None for item in items),
        },
    }


# 与其它 VLA Core 视图保持可发现的命名别名。
semantic_review_data = semantic_review_view_data


__all__ = ["semantic_review_data", "semantic_review_view_data"]
