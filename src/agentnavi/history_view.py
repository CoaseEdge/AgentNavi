"""有界、只读、可追溯的 VLA Task Timeline 与 Project Story。"""

from __future__ import annotations

import sqlite3
import math
import json
from datetime import UTC, datetime
from typing import Any

from .database import Database
from .privacy import contains_private_path, is_canonical_relative_path
from .query import _context_evidence
from .utils import stable_id

HISTORY_TASK_LIMIT = 20
HISTORY_TASK_SCAN_LIMIT = 256
HISTORY_RELATION_LIMIT = 12
HISTORY_RELATION_PAGE_SIZE = 32
HISTORY_RELATION_MAX_PAGES = 4
HISTORY_STORY_LIMIT = 12
HISTORY_WARNING_LIMIT = 10
_RELATIONS = {"read", "modified", "tested", "searched", "affects"}
_DISCLAIMER = "按 L3 任务关系聚合展示，不是原始工具调用的无损还原，也不据此推断因果。"


def _safe_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str) or contains_private_path(value):
        return None
    result = " ".join(value.split()).strip()
    return result[:limit] if result else None


def _canonical_time(value: Any) -> str | None:
    if not isinstance(value, str) or contains_private_path(value):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _time_sort_key(value: Any) -> float:
    canonical = _canonical_time(value)
    if canonical is None:
        return float("-inf")
    return datetime.fromisoformat(canonical[:-1] + "+00:00").timestamp()


def _task_evidence(task_id: str, summary: str) -> dict[str, Any]:
    return _context_evidence(
        kind="task-record", summary=f"任务 {task_id}：{summary}", layer="L3", source="task-events",
        confidence=1.0,
    )


def _task_rows(
    connection: sqlite3.Connection,
    project_id: str,
) -> tuple[list[sqlite3.Row], bool]:
    rows = connection.execute(
        f"""SELECT /* history-task-candidates */ task.id, task.title, task.status,
                   task.summary, task.created_at, task.updated_at, task.closed_at,
                   node.id AS node_id, node.label AS node_label,
                   node.source AS node_source, node.confidence AS node_confidence
            FROM tasks task INDEXED BY idx_tasks_project_authoritative_time
            JOIN nodes node ON node.project_id=task.project_id
             AND node.layer=3 AND node.kind='task' AND node.key=task.id
            WHERE task.project_id=?
            ORDER BY julianday(COALESCE(task.closed_at, task.updated_at, task.created_at)) DESC,
                     task.id DESC LIMIT ?""",
        (project_id, HISTORY_TASK_SCAN_LIMIT + 1),
    ).fetchall()
    return list(rows[:HISTORY_TASK_SCAN_LIMIT]), len(rows) > HISTORY_TASK_SCAN_LIMIT


def _relation_rows(
    connection: sqlite3.Connection,
    project_id: str,
    task_node_id: str,
    task_id: str,
) -> tuple[list[dict[str, Any]], bool, bool]:
    result: list[dict[str, Any]] = []
    filtered = False
    exhausted = False
    cursor: int | None = None
    for _ in range(HISTORY_RELATION_MAX_PAGES):
        raw = connection.execute(
            """SELECT /* history-relation-page */ id, rowid AS recorded_order
               FROM edges INDEXED BY idx_edges_source
               WHERE project_id=? AND layer=3 AND source_id=?
                 AND (? IS NULL OR rowid < ?)
               ORDER BY rowid DESC LIMIT ?""",
            (project_id, task_node_id, cursor, cursor, HISTORY_RELATION_PAGE_SIZE),
        ).fetchall()
        if not raw:
            exhausted = True
            break
        ids = [str(row["id"]) for row in raw]
        marks = ",".join("?" for _ in ids)
        hydrated = connection.execute(
            f"""SELECT /* history-relation-hydrate */ edge.id, edge.source_id,
                       edge.target_id, edge.relation, edge.source, edge.confidence,
                       source.layer AS source_layer, source.kind AS source_kind,
                       source.key AS source_key, source.source AS source_node_source,
                       target.layer AS target_layer, target.kind AS target_kind,
                       target.key AS target_key, target.label AS target_label,
                       target.source AS target_source,
                       target.confidence AS target_confidence
                FROM edges edge
                JOIN nodes source ON source.id=edge.source_id
                 AND source.project_id=edge.project_id
                JOIN nodes target ON target.id=edge.target_id
                 AND target.project_id=edge.project_id
                WHERE edge.project_id=? AND edge.id IN ({marks})""",
            (project_id, *ids),
        ).fetchall()
        by_id = {str(row["id"]): row for row in hydrated}
        for raw_row in raw:
            row = by_id.get(str(raw_row["id"]))
            if (
                row is None or row["source"] != "task-events"
                or row["relation"] not in _RELATIONS
                or int(row["source_layer"]) != 3 or row["source_kind"] != "task"
                or row["source_key"] != task_id or row["source_node_source"] != "task-events"
                or row["source_id"] != task_node_id
            ):
                filtered = True
                continue
            is_file = int(row["target_layer"]) == 1 and row["target_kind"] == "file"
            is_concept = int(row["target_layer"]) == 2 and row["target_kind"] == "concept"
            path = str(row["target_key"]) if is_file else None
            label = _safe_text(row["target_label"], limit=240)
            relation = str(row["relation"])
            edge_id = _safe_text(row["id"], limit=240)
            target_id = _safe_text(row["target_id"], limit=240)
            target_source = _safe_text(row["target_source"], limit=120)
            domain_valid = is_concept if relation == "affects" else is_file
            if not edge_id or not target_id or not label or not target_source or not domain_valid or (
                path is not None and not is_canonical_relative_path(path)
            ) or any(
                isinstance(row[field], bool) or not isinstance(row[field], (int, float))
                or not math.isfinite(float(row[field])) or not 0 <= float(row[field]) <= 1
                for field in ("confidence", "target_confidence")
            ):
                filtered = True
                continue
            relation_evidence = _context_evidence(
                kind="task-relation",
                summary=f"任务 {task_id} 的关系 {edge_id} 记录 {relation}。",
                layer="L3", source="task-events", confidence=float(row["confidence"]),
                path=path,
            )
            entity_evidence = _context_evidence(
                kind="repository-file" if is_file else "semantic-node",
                summary="任务关联的仓库文件。" if is_file else "任务关联的概念节点。",
                layer="L1" if is_file else "L2", source=target_source,
                confidence=float(row["target_confidence"]), path=path,
            )
            result.append({
                "entity": {
                    "id": target_id, "kind": str(row["target_kind"]),
                    "label": label, **({"path": path} if path is not None else {}),
                    "layer": f"L{int(row['target_layer'])}", "source": target_source,
                    "confidence": float(row["target_confidence"]), "evidence": [entity_evidence],
                },
                "relation": {
                    "id": edge_id, "sourceId": task_node_id,
                    "targetId": target_id, "relation": relation,
                    "layer": "L3", "source": "task-events",
                    "confidence": float(row["confidence"]), "evidence": [relation_evidence],
                },
                "recordedOrder": int(raw_row["recorded_order"]),
                "evidence": [relation_evidence],
            })
            if len(result) > HISTORY_RELATION_LIMIT:
                break
        if len(result) > HISTORY_RELATION_LIMIT:
            break
        cursor = int(raw[-1]["recorded_order"])
        if len(raw) < HISTORY_RELATION_PAGE_SIZE:
            exhausted = True
            break
    return result[:HISTORY_RELATION_LIMIT], len(result) > HISTORY_RELATION_LIMIT or not exhausted, filtered


def _task_row_by_id(
    connection: sqlite3.Connection, project_id: str, task_id: str
) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT /* history-requested-task */ task.id, task.title, task.status,
                  task.summary, task.created_at, task.updated_at, task.closed_at,
                  node.id AS node_id, node.label AS node_label,
                  node.source AS node_source, node.confidence AS node_confidence
           FROM tasks task JOIN nodes node ON node.project_id=task.project_id
            AND node.layer=3 AND node.kind='task' AND node.key=task.id
           WHERE task.project_id=? AND task.id=? LIMIT 1""",
        (project_id, task_id),
    ).fetchone()


def _safe_task_row(row: sqlite3.Row) -> bool:
    fields = {
        "id": _safe_text(row["id"], limit=240),
        "title": _safe_text(row["title"], limit=480),
        "status": _safe_text(row["status"], limit=40),
        "label": _safe_text(row["node_label"], limit=240),
        "node_id": _safe_text(row["node_id"], limit=240),
    }
    summary = _safe_text(row["summary"], limit=480) if row["summary"] else ""
    confidence = row["node_confidence"]
    valid = (
        row["node_source"] == "task-events"
        and all(fields.values())
        and (not row["summary"] or summary is not None)
        and _canonical_time(row["created_at"]) is not None
        and _canonical_time(row["updated_at"]) is not None
        and (row["closed_at"] is None or _canonical_time(row["closed_at"]) is not None)
        and not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and math.isfinite(float(confidence)) and 0 <= float(confidence) <= 1
    )
    return valid


def _task_matches_query(row: sqlite3.Row, query: str) -> bool:
    if not query:
        return True
    needle = query.casefold()
    return needle in str(row["title"]).casefold() or needle in str(row["summary"] or "").casefold()


def _timeline_item(
    connection: sqlite3.Connection,
    project_id: str,
    row: sqlite3.Row,
) -> tuple[dict[str, Any], bool, bool]:
    task_id = str(row["id"])
    relations, truncated, filtered = _relation_rows(
        connection, project_id, str(row["node_id"]), task_id
    )
    evidence = _task_evidence(task_id, "L3 任务记录。")
    item = {
        "entity": {
            "id": str(row["node_id"]), "kind": "task",
            "label": _safe_text(row["node_label"], limit=240),
            "layer": "L3", "source": "task-events",
            "confidence": float(row["node_confidence"]), "evidence": [evidence],
        },
        "taskId": task_id, "status": _safe_text(row["status"], limit=40),
        "summary": _safe_text(row["summary"], limit=480) or "暂无任务摘要。",
        "createdAt": _canonical_time(row["created_at"]),
        "updatedAt": _canonical_time(row["updated_at"]),
        "closedAt": _canonical_time(row["closed_at"]) if row["closed_at"] else None,
        "sortTime": _canonical_time(row["closed_at"] or row["updated_at"] or row["created_at"]),
        "relations": relations, "evidence": [evidence],
    }
    return item, truncated, filtered


def task_detail_projection(
    database: Database,
    project: sqlite3.Row,
    task_id: str,
    *,
    relation_limit: int = HISTORY_RELATION_LIMIT,
) -> dict[str, Any] | None:
    """复用 History Core 的安全 DTO 生成兼容的单任务详情。"""

    checked_id = _safe_text(task_id, limit=240)
    if (
        checked_id is None or isinstance(relation_limit, bool)
        or not isinstance(relation_limit, int) or relation_limit < 1
    ):
        return None
    with database.connect() as connection:
        connection.execute("BEGIN")
        row = _task_row_by_id(connection, str(project["id"]), checked_id)
        if row is None or not _safe_task_row(row):
            connection.rollback()
            return None
        item, truncated, filtered = _timeline_item(
            connection, str(project["id"]), row
        )
        connection.rollback()
    limit = min(int(relation_limit), HISTORY_RELATION_LIMIT)
    relations = item["relations"][:limit]
    return {
        "id": item["taskId"], "nodeId": item["entity"]["id"],
        "title": item["entity"]["label"], "status": item["status"],
        "summary": item["summary"], "createdAt": item["createdAt"],
        "updatedAt": item["updatedAt"], "closedAt": item["closedAt"],
        "sortTime": item["sortTime"], "source": item["entity"]["source"],
        "confidence": item["entity"]["confidence"], "relations": relations,
        "relationsTruncated": truncated or len(item["relations"]) > limit,
        "relationsFiltered": filtered,
    }


def history_view_data(
    database: Database,
    project: sqlite3.Row,
    query: str = "",
    *,
    task_id: str | None = None,
    selected_mode: str = "timeline",
) -> dict[str, Any]:
    if selected_mode not in {"timeline", "story"}:
        raise ValueError("history mode 无效。")
    checked_query = _safe_text(query, limit=4096) if query else ""
    if query and checked_query is None:
        raise ValueError("history query 无效。")
    if task_id is not None and _safe_text(task_id, limit=240) is None:
        raise ValueError("history task_id 无效。")
    project_id = str(project["id"])
    warnings: list[dict[str, Any]] = []
    with database.connect() as connection:
        connection.execute("BEGIN")
        candidates, scan_truncated = _task_rows(connection, project_id)
        requested_row = None
        if task_id:
            requested_row = _task_row_by_id(connection, project_id, task_id)
            if requested_row is not None and all(str(row["id"]) != task_id for row in candidates):
                candidates.append(requested_row)
        safe: list[sqlite3.Row] = []
        filtered = False
        for row in candidates:
            # task_id 是显式定位，不受 Timeline 的文本筛选影响；两条路径仍共用
            # 完全相同的安全投影验证。
            is_requested = bool(task_id and str(row["id"]) == task_id)
            if not _safe_task_row(row):
                filtered = True
            elif is_requested or _task_matches_query(row, checked_query or ""):
                safe.append(row)
        safe.sort(key=lambda row: (
            _time_sort_key(row["closed_at"] or row["updated_at"] or row["created_at"]), str(row["id"])
        ), reverse=True)
        requested_safe = next((row for row in safe if str(row["id"]) == task_id), None) if task_id else None
        if requested_safe is not None and requested_safe not in safe[:HISTORY_TASK_LIMIT]:
            display = [*safe[:HISTORY_TASK_LIMIT - 1], requested_safe]
            display.sort(key=lambda row: (
                _time_sort_key(row["closed_at"] or row["updated_at"] or row["created_at"]),
                str(row["id"]),
            ), reverse=True)
        else:
            display = safe[:HISTORY_TASK_LIMIT]
        if len(safe) > HISTORY_TASK_LIMIT:
            warnings.append({"code": "HISTORY_TIMELINE_TRUNCATED", "message": f"Timeline 最多展示 {HISTORY_TASK_LIMIT} 个任务。", "evidence": []})
        if scan_truncated:
            warnings.append({"code": "HISTORY_SCAN_TRUNCATED", "message": "任务候选扫描达到固定预算，当前结果可能不完整。", "evidence": []})
        if filtered:
            warnings.append({"code": "HISTORY_UNSAFE_FILTERED", "message": "部分任务或关系含不安全内容，已过滤。", "evidence": []})

        timeline: list[dict[str, Any]] = []
        relation_filtered = False
        relation_truncated = False
        for row in display:
            item, truncated, bad = _timeline_item(connection, project_id, row)
            relation_filtered = relation_filtered or bad
            relation_truncated = relation_truncated or truncated
            timeline.append(item)
        if relation_filtered:
            warnings.append({"code": "HISTORY_RELATION_FILTERED", "message": "部分 L3 关系无法安全验证，已过滤。", "evidence": []})
        if relation_truncated:
            warnings.append({"code": "HISTORY_RELATION_TRUNCATED", "message": f"单个任务最多展示 {HISTORY_RELATION_LIMIT} 条关系；扫描预算耗尽时结果可能不完整。", "evidence": []})

        story = []
        for item in timeline[:HISTORY_STORY_LIMIT]:
            grouped: dict[str, dict[str, Any]] = {}
            for relation in item["relations"]:
                name = relation["relation"]["relation"]
                group = grouped.setdefault(name, {"relation": name, "paths": [], "concepts": [], "evidence": []})
                entity = relation["entity"]
                target = group["paths"] if entity["kind"] == "file" else group["concepts"]
                value = entity.get("path", entity["label"])
                if value not in target:
                    target.append(value)
                if relation["evidence"][0] not in group["evidence"] and len(group["evidence"]) < 3:
                    group["evidence"].append(relation["evidence"][0])
            groups = sorted(grouped.values(), key=lambda value: value["relation"])
            for group in groups:
                group["paths"].sort(key=lambda value: (value.lower(), value))
                group["concepts"].sort(key=lambda value: (value.lower(), value))
            story.append({
                "id": stable_id(item["taskId"], str(groups), prefix="story_"),
                "title": item["entity"]["label"], "summary": item["summary"],
                "sortTime": item["sortTime"], "task": item["entity"],
                "groups": groups, "explanationSource": "l3-aggregation",
                "disclaimer": _DISCLAIMER, "evidence": item["evidence"],
            })
        detail = next((item for item in timeline if item["taskId"] == task_id), None) if task_id else None
        if task_id and detail is None:
            warnings.append({"code": "HISTORY_TASK_NOT_DISPLAYED", "message": "指定任务不在当前有界安全结果中。", "evidence": []})
        stats = {
            "files": int(connection.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND layer=1 AND kind='file'", (project_id,)).fetchone()[0]),
            "tasks": int(connection.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (project_id,)).fetchone()[0]),
            "displayedTasks": len(timeline),
            "displayedRelations": sum(len(item["relations"]) for item in timeline),
        }
        connection.rollback()
    if not timeline:
        warnings.append({"code": "HISTORY_EVIDENCE_INSUFFICIENT", "message": "当前有界结果中没有可安全展示的任务历史。", "evidence": []})
    public_projection = {
        "selectedMode": selected_mode, "disclaimer": _DISCLAIMER,
        "timeline": timeline, "story": story, "taskDetail": detail,
        "stats": stats, "warnings": warnings[:HISTORY_WARNING_LIMIT],
    }
    projection_digest = json.dumps(public_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    source_state = {
        "status": "partial" if not project["last_scan_at"] else "ready",
        "revision": stable_id(project_id, str(project["last_scan_at"] or ""), projection_digest, prefix="history_source_"),
        "indexed_at": project["last_scan_at"],
    }
    revision = stable_id(project_id, projection_digest, prefix="history_")
    return {
        "project": {"id": project_id, "name": str(project["name"]), "root": str(project["root"]), "kind": str(project["kind"]), "last_scan_at": project["last_scan_at"]},
        "sourceState": source_state, "layout": "task-timeline-story", "revision": revision,
        "selectedMode": selected_mode, "disclaimer": _DISCLAIMER,
        "timeline": timeline, "story": story, "taskDetail": detail,
        "stats": stats, "warnings": warnings[:HISTORY_WARNING_LIMIT],
    }


__all__ = [
    "HISTORY_RELATION_LIMIT", "HISTORY_TASK_LIMIT", "history_view_data",
    "task_detail_projection",
]
