from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .database import Database
from .utils import json_loads

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
WORD_RE = re.compile(r"[A-Za-z0-9_.\-/]{2,}")


def search_terms(query: str, *, max_terms: int = 18) -> list[str]:
    normalized = " ".join(query.strip().split())
    terms: list[str] = []
    if normalized:
        terms.append(normalized.lower())
    terms.extend(match.group(0).lower() for match in WORD_RE.finditer(normalized))
    for match in CHINESE_RE.finditer(normalized):
        text = match.group(0)
        if len(text) <= 6:
            terms.append(text)
        for size in (6, 5, 4, 3, 2):
            if len(text) < size:
                continue
            for start in range(0, len(text) - size + 1):
                terms.append(text[start : start + size])
                if len(terms) >= max_terms * 3:
                    break
    deduped = list(dict.fromkeys(term for term in terms if term.strip()))
    # 长词更能减少误命中。
    deduped.sort(key=lambda value: (-len(value), value))
    return deduped[:max_terms]


def _node_score(row: sqlite3.Row, terms: list[str], full_query: str) -> float:
    label = row["label"].lower()
    key = row["key"].lower()
    data = row["data_json"].lower()
    score = {1: 2.0, 2: 5.0, 3: 4.0}.get(row["layer"], 1.0)
    if full_query and full_query in label:
        score += 12
    if full_query and full_query in key:
        score += 8
    for term in terms:
        if term == label:
            score += 9
        elif term in label:
            score += 5
        if term == key:
            score += 6
        elif term in key:
            score += 3
        if term in data:
            score += 1
    if row["layer"] == 2 and row["kind"] == "concept":
        active = json_loads(row["data_json"], {}).get("active", True)
        if not active:
            score -= 6
    return score


def search_nodes(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    query: str,
    limit: int = 30,
) -> list[sqlite3.Row]:
    terms = search_terms(query)
    if not terms:
        return list(
            connection.execute(
                """
                SELECT * FROM nodes
                WHERE project_id=? AND NOT (layer=2 AND kind='concept' AND data_json LIKE '%\"active\":false%')
                ORDER BY layer DESC, updated_at DESC LIMIT ?
                """,
                (project_id, limit),
            )
        )

    candidates: dict[str, sqlite3.Row] = {}
    for term in terms:
        pattern = f"%{term}%"
        for row in connection.execute(
            """
            SELECT * FROM nodes
            WHERE project_id=? AND (
                lower(label) LIKE ? OR lower(key) LIKE ? OR lower(data_json) LIKE ?
            )
            LIMIT 200
            """,
            (project_id, pattern, pattern, pattern),
        ):
            candidates[row["id"]] = row
    full_query = " ".join(query.lower().split())
    ranked = sorted(candidates.values(), key=lambda row: (-_node_score(row, terms, full_query), row["label"]))
    return ranked[:limit]


def search_tasks(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    query: str,
    limit: int = 10,
) -> list[sqlite3.Row]:
    terms = search_terms(query)
    if not terms:
        return list(
            connection.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        )
    candidates: dict[str, sqlite3.Row] = {}
    for term in terms:
        pattern = f"%{term}%"
        for row in connection.execute(
            """
            SELECT * FROM tasks
            WHERE project_id=? AND (
                lower(title) LIKE ? OR lower(prompt) LIKE ? OR lower(summary) LIKE ?
            )
            ORDER BY created_at DESC LIMIT 100
            """,
            (project_id, pattern, pattern, pattern),
        ):
            candidates[row["id"]] = row

    def score(row: sqlite3.Row) -> tuple[float, str]:
        text = f"{row['title']}\n{row['prompt']}\n{row['summary']}".lower()
        value = sum((5 if term in row["title"].lower() else 1) for term in terms if term in text)
        return (-value, row["created_at"])

    return sorted(candidates.values(), key=score)[:limit]


def _concept_neighbors(connection: sqlite3.Connection, project_id: str, concept_id: str) -> list[dict[str, Any]]:
    neighbors: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT e.relation, e.confidence, e.source, n.id, n.label, n.key
        FROM edges e JOIN nodes n ON n.id=e.target_id
        WHERE e.project_id=? AND e.layer=2 AND e.source_id=? AND n.layer=2 AND n.kind='concept'
        """,
        (project_id, concept_id),
    ):
        neighbors.append(
            {
                "direction": "outgoing",
                "relation": row["relation"],
                "id": row["id"],
                "label": row["label"],
                "key": row["key"],
                "confidence": row["confidence"],
                "source": row["source"],
            }
        )
    for row in connection.execute(
        """
        SELECT e.relation, e.confidence, e.source, n.id, n.label, n.key
        FROM edges e JOIN nodes n ON n.id=e.source_id
        WHERE e.project_id=? AND e.layer=2 AND e.target_id=? AND n.layer=2 AND n.kind='concept'
        """,
        (project_id, concept_id),
    ):
        neighbors.append(
            {
                "direction": "incoming",
                "relation": row["relation"],
                "id": row["id"],
                "label": row["label"],
                "key": row["key"],
                "confidence": row["confidence"],
                "source": row["source"],
            }
        )
    return neighbors


def _concept_files(connection: sqlite3.Connection, project_id: str, concept_id: str) -> list[dict[str, Any]]:
    return [
        {
            "path": row["key"],
            "relation": row["relation"],
            "language": json_loads(row["data_json"], {}).get("language", "unknown"),
        }
        for row in connection.execute(
            """
            SELECT n.key, n.data_json, e.relation
            FROM edges e JOIN nodes n ON n.id=e.target_id
            WHERE e.project_id=? AND e.layer=2 AND e.source_id=?
              AND n.layer=1 AND n.kind='file'
            ORDER BY e.relation, n.key
            """,
            (project_id, concept_id),
        )
    ]


def _concepts_for_file(connection: sqlite3.Connection, project_id: str, file_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT DISTINCT n.*
            FROM edges e JOIN nodes n ON n.id=e.source_id
            WHERE e.project_id=? AND e.layer=2 AND e.target_id=?
              AND n.layer=2 AND n.kind='concept'
            """,
            (project_id, file_id),
        )
    )


def context_data(
    database: Database,
    project: sqlite3.Row,
    query: str,
    *,
    concept_limit: int = 5,
    file_limit: int = 12,
    task_limit: int = 5,
) -> dict[str, Any]:
    project_id = project["id"]
    with database.connect() as connection:
        matches = search_nodes(connection, project_id=project_id, query=query, limit=40)
        concepts: dict[str, sqlite3.Row] = {
            row["id"]: row for row in matches if row["layer"] == 2 and row["kind"] == "concept"
        }
        direct_files: dict[str, sqlite3.Row] = {
            row["id"]: row for row in matches if row["layer"] == 1 and row["kind"] == "file"
        }
        for file_id in list(direct_files):
            for concept in _concepts_for_file(connection, project_id, file_id):
                concepts.setdefault(concept["id"], concept)

        if not concepts and not query.strip():
            for row in connection.execute(
                """
                SELECT * FROM nodes
                WHERE project_id=? AND layer=2 AND kind='concept'
                  AND data_json NOT LIKE '%\"active\":false%'
                ORDER BY updated_at DESC, label LIMIT ?
                """,
                (project_id, concept_limit),
            ):
                concepts[row["id"]] = row

        selected_concepts = list(concepts.values())[:concept_limit]
        concept_entries: list[dict[str, Any]] = []
        relevant_files: dict[str, dict[str, Any]] = {
            row["key"]: {
                "path": row["key"],
                "relation": "direct_match",
                "language": json_loads(row["data_json"], {}).get("language", "unknown"),
            }
            for row in direct_files.values()
        }
        for concept in selected_concepts:
            files = _concept_files(connection, project_id, concept["id"])
            for file_entry in files:
                relevant_files.setdefault(file_entry["path"], file_entry)
            neighbors = _concept_neighbors(connection, project_id, concept["id"])[:10]
            # 只展开一跳邻居，并受总文件上限约束；这样能带出直接依赖，避免退化成全仓库扫描。
            for neighbor in neighbors[:4]:
                if len(relevant_files) >= file_limit:
                    break
                for neighbor_file in _concept_files(connection, project_id, neighbor["id"])[:4]:
                    enriched = dict(neighbor_file)
                    enriched["relation"] = (
                        f"经 {concept['label']} {neighbor['relation']} {neighbor['label']}"
                    )
                    relevant_files.setdefault(enriched["path"], enriched)
                    if len(relevant_files) >= file_limit:
                        break
            concept_entries.append(
                {
                    "id": concept["id"],
                    "key": concept["key"],
                    "label": concept["label"],
                    "confidence": concept["confidence"],
                    "source": concept["source"],
                    "neighbors": neighbors,
                    "files": files[:file_limit],
                }
            )

        task_matches: dict[str, sqlite3.Row] = {
            row["id"]: row
            for row in search_tasks(connection, project_id=project_id, query=query, limit=task_limit)
        }
        if selected_concepts:
            placeholders = ",".join("?" for _ in selected_concepts)
            concept_ids = [row["id"] for row in selected_concepts]
            for row in connection.execute(
                f"""
                SELECT DISTINCT t.*
                FROM edges e
                JOIN nodes task_node ON task_node.id=e.source_id AND task_node.kind='task'
                JOIN tasks t ON t.id=task_node.key
                WHERE e.project_id=? AND e.layer=3 AND e.relation='affects'
                  AND e.target_id IN ({placeholders})
                ORDER BY t.created_at DESC LIMIT ?
                """,
                (project_id, *concept_ids, task_limit),
            ):
                task_matches.setdefault(row["id"], row)

        tasks = [
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
            for row in list(task_matches.values())[:task_limit]
        ]

        stats = {
            "files": connection.execute(
                "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=1 AND kind='file'",
                (project_id,),
            ).fetchone()["count"],
            "concepts": connection.execute(
                "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=2 AND kind='concept' AND data_json NOT LIKE '%\"active\":false%'",
                (project_id,),
            ).fetchone()["count"],
            "tasks": connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE project_id=?",
                (project_id,),
            ).fetchone()["count"],
        }

    return {
        "project": {
            "id": project_id,
            "name": project["name"],
            "root": project["root"],
            "kind": project["kind"],
            "last_scan_at": project["last_scan_at"],
        },
        "query": query,
        "stats": stats,
        "concepts": concept_entries,
        "files": list(relevant_files.values())[:file_limit],
        "tasks": tasks,
    }


def format_context(data: dict[str, Any]) -> str:
    project = data["project"]
    stats = data["stats"]
    lines = [
        "[AgentNavi 项目上下文]",
        f"项目：{project['name']}（{project['id']}）",
        f"根目录：{project['root']}",
        f"索引：{stats['files']} 个文件 / {stats['concepts']} 个概念 / {stats['tasks']} 个历史任务",
    ]
    if data.get("query"):
        lines.append(f"当前查询：{data['query']}")

    concepts = data.get("concepts", [])
    if concepts:
        lines.append("\n相关概念：")
        for concept in concepts:
            neighbors = []
            for neighbor in concept["neighbors"][:5]:
                arrow = "→" if neighbor["direction"] == "outgoing" else "←"
                neighbors.append(f"{arrow}{neighbor['relation']} {neighbor['label']}")
            suffix = f"；{'；'.join(neighbors)}" if neighbors else ""
            lines.append(
                f"- {concept['label']}（置信度 {concept['confidence']:.2f}，来源 {concept['source']}）{suffix}"
            )

    files = data.get("files", [])
    if files:
        lines.append("\n建议优先读取的文件：")
        for file_entry in files:
            lines.append(f"- {file_entry['path']}（{file_entry['relation']}）")

    tasks = data.get("tasks", [])
    if tasks:
        lines.append("\n相关历史任务：")
        for task in tasks:
            summary = task["summary"].replace("\n", " ").strip()
            if len(summary) > 120:
                summary = summary[:117] + "..."
            suffix = f"：{summary}" if summary else ""
            lines.append(f"- {task['created_at'][:10]} · {task['title']} [{task['status']}]{suffix}")

    lines.extend(
        [
            "\n使用原则：",
            "1. 以上内容是导航索引，不替代真实文件；修改前仍要核对源文件。",
            "2. 优先沿“任务 → 概念 → 文件”下钻，不要无目的扫描整个仓库。",
            "3. 语义关系含推断置信度；低置信度关系需要额外验证。",
        ]
    )
    return "\n".join(lines)


def build_context(database: Database, project: sqlite3.Row, query: str) -> str:
    return format_context(context_data(database, project, query))


def impact_data(database: Database, project: sqlite3.Row, selector: str) -> dict[str, Any]:
    project_id = project["id"]
    normalized = selector.replace("\\", "/").strip()
    with database.connect() as connection:
        file_row = connection.execute(
            "SELECT * FROM nodes WHERE project_id=? AND layer=1 AND kind='file' AND key=?",
            (project_id, normalized),
        ).fetchone()
        if file_row is None:
            matches = list(
                connection.execute(
                    """
                    SELECT * FROM nodes
                    WHERE project_id=? AND layer=1 AND kind='file'
                      AND (key LIKE ? OR label LIKE ?)
                    ORDER BY length(key), key LIMIT 10
                    """,
                    (project_id, f"%{normalized}%", f"%{normalized}%"),
                )
            )
            if len(matches) == 1:
                file_row = matches[0]
            elif not matches:
                raise LookupError(f"找不到文件节点：{selector}")
            else:
                raise LookupError("匹配到多个文件，请使用更完整路径：" + "、".join(row["key"] for row in matches))

        outgoing = [
            {"relation": row["relation"], "path": row["key"]}
            for row in connection.execute(
                """
                SELECT e.relation, n.key FROM edges e JOIN nodes n ON n.id=e.target_id
                WHERE e.project_id=? AND e.layer=1 AND e.source_id=?
                ORDER BY e.relation, n.key
                """,
                (project_id, file_row["id"]),
            )
        ]
        incoming = [
            {"relation": row["relation"], "path": row["key"]}
            for row in connection.execute(
                """
                SELECT e.relation, n.key FROM edges e JOIN nodes n ON n.id=e.source_id
                WHERE e.project_id=? AND e.layer=1 AND e.target_id=?
                ORDER BY e.relation, n.key
                """,
                (project_id, file_row["id"]),
            )
        ]
        concepts = _concepts_for_file(connection, project_id, file_row["id"])
        semantic_impact: list[dict[str, Any]] = []
        for concept in concepts:
            semantic_impact.extend(_concept_neighbors(connection, project_id, concept["id"]))
        tasks = [
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "created_at": row["created_at"],
                "relation": row["relation"],
            }
            for row in connection.execute(
                """
                SELECT t.*, e.relation
                FROM edges e
                JOIN nodes task_node ON task_node.id=e.source_id AND task_node.kind='task'
                JOIN tasks t ON t.id=task_node.key
                WHERE e.project_id=? AND e.layer=3 AND e.target_id=?
                ORDER BY t.created_at DESC LIMIT 20
                """,
                (project_id, file_row["id"]),
            )
        ]

    return {
        "project": project["id"],
        "file": file_row["key"],
        "concepts": [{"key": row["key"], "label": row["label"]} for row in concepts],
        "outgoing": outgoing,
        "incoming": incoming,
        "semantic_impact": semantic_impact,
        "tasks": tasks,
    }


def format_impact(data: dict[str, Any]) -> str:
    lines = [f"文件：{data['file']}"]
    if data["concepts"]:
        lines.append("所属概念：" + "、".join(item["label"] for item in data["concepts"]))
    if data["outgoing"]:
        lines.append("\n它依赖或引用：")
        lines.extend(f"- {item['relation']} → {item['path']}" for item in data["outgoing"])
    if data["incoming"]:
        lines.append("\n可能受它影响的文件：")
        lines.extend(f"- {item['path']} → {item['relation']}" for item in data["incoming"])
    if data["semantic_impact"]:
        lines.append("\n语义层影响：")
        seen: set[tuple[str, str, str]] = set()
        for item in data["semantic_impact"]:
            key = (item["direction"], item["relation"], item["label"])
            if key in seen:
                continue
            seen.add(key)
            arrow = "→" if item["direction"] == "outgoing" else "←"
            lines.append(f"- {arrow} {item['relation']} {item['label']}（{item['confidence']:.2f}）")
    if data["tasks"]:
        lines.append("\n相关历史任务：")
        lines.extend(
            f"- {item['created_at'][:10]} · {item['title']} [{item['relation']}]" for item in data["tasks"]
        )
    return "\n".join(lines)


def history_data(database: Database, project: sqlite3.Row, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = search_tasks(connection, project_id=project["id"], query=query, limit=limit)
        return [dict(row) for row in rows]
