from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .database import Database
from .privacy import contains_private_path, is_canonical_relative_path
from .utils import json_loads, stable_id

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
                WHERE project_id=? AND NOT (layer=2 AND kind='concept' AND data_json LIKE '%"active":false%')
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

    # 长任务描述经常只保留较长 n-gram，例如“修改会员升级和支付逻辑”。
    # 此时短概念“会员升级”不会满足 query-term-in-label，但它本身明显包含于原任务。
    # 对活跃概念做一次有界的反向包含检查，并同时检查别名和关键词，
    # 可以恢复这类高价值命中，而不会把所有文件重新塞回候选集。
    full_query = " ".join(query.lower().split())
    if full_query:
        for row in connection.execute(
            """
            SELECT * FROM nodes
            WHERE project_id=? AND layer=2 AND kind='concept'
              AND data_json NOT LIKE '%"active":false%'
            LIMIT 5000
            """,
            (project_id,),
        ):
            data = json_loads(row["data_json"], {})
            values = [str(row["label"]), str(row["key"])]
            for field in ("aliases", "keywords", "headings", "link_labels", "symbols"):
                field_values = data.get(field, [])
                if isinstance(field_values, list):
                    values.extend(str(value) for value in field_values)
            if any(
                len(value.strip()) >= 2 and value.strip().lower() in full_query
                for value in values
            ):
                candidates[row["id"]] = row

    ranked = sorted(
        candidates.values(),
        key=lambda row: (
            -_node_score(row, terms, full_query),
            str(row["label"]).lower(), str(row["label"]), str(row["key"]), str(row["id"]),
        ),
    )
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
        SELECT e.id AS edge_id, e.relation, e.confidence, e.source,
               n.id, n.label, n.key, n.confidence AS entity_confidence,
               n.source AS entity_source
        FROM edges e JOIN nodes n ON n.id=e.target_id
        WHERE e.project_id=? AND e.layer=2 AND e.source_id=? AND n.layer=2 AND n.kind='concept'
        ORDER BY e.relation, n.label COLLATE NOCASE, n.key, n.id
        """,
        (project_id, concept_id),
    ):
        neighbors.append(
            {
                "direction": "outgoing",
                "edge_id": row["edge_id"],
                "relation": row["relation"],
                "id": row["id"],
                "label": row["label"],
                "key": row["key"],
                "confidence": row["confidence"],
                "source": row["source"],
                "entity_confidence": row["entity_confidence"],
                "entity_source": row["entity_source"],
            }
        )
    for row in connection.execute(
        """
        SELECT e.id AS edge_id, e.relation, e.confidence, e.source,
               n.id, n.label, n.key, n.confidence AS entity_confidence,
               n.source AS entity_source
        FROM edges e JOIN nodes n ON n.id=e.source_id
        WHERE e.project_id=? AND e.layer=2 AND e.target_id=? AND n.layer=2 AND n.kind='concept'
        ORDER BY e.relation, n.label COLLATE NOCASE, n.key, n.id
        """,
        (project_id, concept_id),
    ):
        neighbors.append(
            {
                "direction": "incoming",
                "edge_id": row["edge_id"],
                "relation": row["relation"],
                "id": row["id"],
                "label": row["label"],
                "key": row["key"],
                "confidence": row["confidence"],
                "source": row["source"],
                "entity_confidence": row["entity_confidence"],
                "entity_source": row["entity_source"],
            }
        )
    return neighbors


def _concept_files(connection: sqlite3.Connection, project_id: str, concept_id: str) -> list[dict[str, Any]]:
    return [
        {
            "path": row["key"],
            "relation": row["relation"],
            "language": json_loads(row["data_json"], {}).get("language", "unknown"),
            "file_id": row["file_id"],
            "file_label": row["file_label"],
            "file_confidence": row["file_confidence"],
            "file_source": row["file_source"],
            "edge_id": row["edge_id"],
            "edge_confidence": row["edge_confidence"],
            "edge_source": row["edge_source"],
        }
        for row in connection.execute(
            """
            SELECT n.id AS file_id, n.key, n.label AS file_label, n.data_json,
                   n.confidence AS file_confidence, n.source AS file_source,
                   e.id AS edge_id, e.relation,
                   e.confidence AS edge_confidence, e.source AS edge_source
            FROM edges e JOIN nodes n ON n.id=e.target_id
            WHERE e.project_id=? AND e.layer=2 AND e.source_id=?
              AND n.layer=1 AND n.kind='file'
            ORDER BY e.relation, n.key COLLATE NOCASE, n.key, e.id
            """,
            (project_id, concept_id),
        )
        if is_canonical_relative_path(str(row["key"]))
    ]


def _concepts_for_file(connection: sqlite3.Connection, project_id: str, file_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT DISTINCT n.*
            FROM edges e JOIN nodes n ON n.id=e.source_id
            WHERE e.project_id=? AND e.layer=2 AND e.target_id=?
              AND n.layer=2 AND n.kind='concept'
            ORDER BY n.label COLLATE NOCASE, n.label, n.key, n.id
            """,
            (project_id, file_id),
        )
    )


_CONTEXT_ACTIONS = (
    ("purpose", "它做什么"),
    ("relevance", "为什么相关"),
    ("dependents", "谁依赖它"),
    ("history", "过去谁改过"),
    ("impact", "如果改它"),
)
_CONTEXT_HISTORY_SCAN_LIMIT = 120
_CONTEXT_FRESHNESS_FILE_BYTES = 256 * 1024
_CONTEXT_FRESHNESS_TOTAL_BYTES = 1024 * 1024


def _context_evidence(
    *,
    kind: str,
    summary: str,
    layer: str,
    source: str,
    confidence: float,
    path: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "layer": layer,
        "source": source,
        "confidence": float(confidence),
        **({"path": path} if path is not None else {}),
    }


def _context_entity(
    row: sqlite3.Row | dict[str, Any],
    *,
    kind: str,
    evidence: list[dict[str, Any]],
    path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": kind,
        "label": str(row["label"]),
        **({"path": path} if path is not None else {}),
        "layer": "L1" if kind == "file" else "L2",
        "source": str(row["source"]),
        "confidence": float(row["confidence"]),
        "evidence": evidence,
    }


def _context_chain(
    concept: sqlite3.Row,
    file_entry: dict[str, Any],
    *,
    neighbor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = str(file_entry["path"])
    mapping_evidence = _context_evidence(
        kind="concept-file-mapping",
        summary=f"图谱记录概念与文件的 {file_entry['relation']} 关系。",
        layer="L2",
        source=str(file_entry["edge_source"]),
        confidence=float(file_entry["edge_confidence"]),
        path=path,
    )
    file_evidence = _context_evidence(
        kind="repository-file",
        summary="索引中的仓库文件。",
        layer="L1",
        source="repository-index",
        confidence=1.0,
        path=path,
    )
    file_entity = {
        "id": str(file_entry["file_id"]),
        "kind": "file",
        "label": str(file_entry["file_label"]),
        "path": path,
        "layer": "L1",
        "source": str(file_entry["file_source"]),
        "confidence": float(file_entry["file_confidence"]),
        "evidence": [file_evidence],
    }
    file_relation = {
        "id": str(file_entry["edge_id"]),
        "sourceId": str(neighbor["id"] if neighbor is not None else concept["id"]),
        "targetId": str(file_entry["file_id"]),
        "relation": str(file_entry["relation"]),
        "layer": "L2",
        "source": str(file_entry["edge_source"]),
        "confidence": float(file_entry["edge_confidence"]),
        "evidence": [mapping_evidence],
    }
    concept_evidence = [mapping_evidence]
    source_entity = _context_entity(
        concept,
        kind="concept",
        evidence=concept_evidence,
    )
    related_entity = None
    concept_relation = None
    if neighbor is not None:
        semantic_evidence = _context_evidence(
            kind="semantic-relation",
            summary=(
                f"图谱记录 {concept['label']} 与 {neighbor['label']} 的 "
                f"{neighbor['relation']} 关系。"
            ),
            layer="L2",
            source=str(neighbor["source"]),
            confidence=float(neighbor["confidence"]),
        )
        related_entity = {
            "id": str(neighbor["id"]),
            "kind": "concept",
            "label": str(neighbor["label"]),
            "layer": "L2",
            "source": str(neighbor["entity_source"]),
            "confidence": float(neighbor["entity_confidence"]),
            "evidence": [semantic_evidence],
        }
        if neighbor["direction"] == "outgoing":
            source_id, target_id = str(concept["id"]), str(neighbor["id"])
        else:
            source_id, target_id = str(neighbor["id"]), str(concept["id"])
        concept_relation = {
            "id": str(neighbor["edge_id"]),
            "sourceId": source_id,
            "targetId": target_id,
            "relation": str(neighbor["relation"]),
            "layer": "L2",
            "source": str(neighbor["source"]),
            "confidence": float(neighbor["confidence"]),
            "evidence": [semantic_evidence],
        }
        concept_evidence = [semantic_evidence, mapping_evidence]
        source_entity["evidence"] = [semantic_evidence]
    return {
        "sourceConcept": source_entity,
        "conceptRelation": concept_relation,
        "relatedConcept": related_entity,
        "fileRelation": file_relation,
        "file": file_entity,
        "evidence": concept_evidence,
    }


def _fresh_context_paths(
    connection: sqlite3.Connection,
    project: sqlite3.Row,
    paths: list[str],
) -> tuple[set[str], set[str]]:
    if not paths:
        return set(), set()
    placeholders = ",".join("?" for _ in paths)
    states = {
        str(row["path"]): row
        for row in connection.execute(
            f"SELECT * FROM file_state WHERE project_id=? AND path IN ({placeholders})",
            (project["id"], *paths),
        )
    }
    try:
        root = Path(str(project["root"])).resolve(strict=True)
    except (OSError, RuntimeError):
        return set(), set(paths)
    fresh: set[str] = set()
    unverifiable: set[str] = set()
    remaining = _CONTEXT_FRESHNESS_TOTAL_BYTES
    for path in paths:
        state = states.get(path)
        if state is None or not is_canonical_relative_path(path):
            continue
        try:
            resolved = root.joinpath(*path.split("/")).resolve(strict=True)
            resolved.relative_to(root)
            with resolved.open("rb") as handle:
                before = os.fstat(handle.fileno())
                if (
                    before.st_size != state["size"]
                    or before.st_mtime_ns != state["mtime_ns"]
                ):
                    continue
                if (
                    before.st_size > _CONTEXT_FRESHNESS_FILE_BYTES
                    or before.st_size > remaining
                ):
                    unverifiable.add(path)
                    continue
                remaining -= before.st_size
                hasher = hashlib.blake2s()
                unread = before.st_size
                while unread:
                    chunk = handle.read(min(64 * 1024, unread))
                    if not chunk:
                        break
                    hasher.update(chunk)
                    unread -= len(chunk)
                after = os.fstat(handle.fileno())
            current = resolved.stat()
        except (OSError, RuntimeError, ValueError):
            continue
        if (
            unread == 0
            and before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
            and current.st_dev == after.st_dev
            and current.st_ino == after.st_ino
            and current.st_size == after.st_size
            and current.st_mtime_ns == after.st_mtime_ns
            and hasher.hexdigest() == str(state["digest"])
        ):
            fresh.add(path)
    return fresh, unverifiable


def _context_file_actions(
    connection: sqlite3.Connection,
    project_id: str,
    files: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """只为已选文件生成五个本地只读操作摘要。"""

    paths = [str(item["path"]) for item in files]
    if not paths:
        return {}, False
    placeholders = ",".join("?" for _ in paths)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_incoming: set[tuple[str, str, str]] = set()
    for row in connection.execute(
        f"""SELECT target.key AS target_path, source.key AS source_path,
                   edge.id, edge.relation, edge.source, edge.confidence
            FROM edges edge
            JOIN nodes source ON source.id=edge.source_id AND source.layer=1 AND source.kind='file'
            JOIN nodes target ON target.id=edge.target_id AND target.layer=1 AND target.kind='file'
            WHERE edge.project_id=? AND edge.layer=1
              AND source.key IN ({placeholders}) AND target.key IN ({placeholders})
            ORDER BY target.key COLLATE NOCASE, target.key,
                     source.key COLLATE NOCASE, source.key, edge.relation, edge.id""",
        (project_id, *paths, *paths),
    ):
        incoming_key = (
            str(row["target_path"]), str(row["source_path"]), str(row["relation"]),
        )
        if incoming_key not in seen_incoming:
            seen_incoming.add(incoming_key)
            incoming[incoming_key[0]].append({
                "path": incoming_key[1], "relation": incoming_key[2],
                "_evidence": _context_evidence(
                    kind="physical-relation",
                    summary=(
                        f"{incoming_key[1]} {incoming_key[2]} {incoming_key[0]}"
                    ),
                    layer="L1",
                    source=str(row["source"]),
                    confidence=float(row["confidence"]),
                    path=incoming_key[1],
                ),
            })

    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    cursor = connection.execute(
        f"""SELECT file.key AS path, task.id, task.title, task.status,
                   task.created_at, edge.relation, edge.source, edge.confidence
            FROM edges edge
            JOIN nodes file ON file.id=edge.target_id AND file.layer=1 AND file.kind='file'
            JOIN nodes task_node ON task_node.id=edge.source_id
              AND task_node.layer=3 AND task_node.kind='task'
            JOIN tasks task ON task.id=task_node.key
            WHERE edge.project_id=? AND edge.layer=3 AND file.key IN ({placeholders})
            ORDER BY COALESCE(task.closed_at, task.updated_at, task.created_at) DESC,
                     task.id, file.key, edge.relation, edge.id""",
        (project_id, *paths),
    )
    history_truncated = False
    for row in cursor:
        scanned += 1
        if scanned > _CONTEXT_HISTORY_SCAN_LIMIT:
            history_truncated = True
            break
        path = str(row["path"])
        title = str(row["title"])
        if len(history[path]) >= 3 or contains_private_path(title):
            continue
        history[path].append({
            "id": str(row["id"]),
            "title": title[:160],
            "status": str(row["status"])[:40],
            "createdAt": str(row["created_at"])[:64],
            "relation": str(row["relation"])[:80],
            "_evidence": _context_evidence(
                kind="task-file-relation",
                summary=f"历史任务以 {row['relation']} 关系关联此文件。",
                layer="L3",
                source=str(row["source"]),
                confidence=float(row["confidence"]),
                path=path,
            ),
        })

    result: dict[str, dict[str, Any]] = {}
    for item in files:
        path = str(item["path"])
        chains = item.get("_chains", [])
        primary = chains[0] if chains else None
        evidence = list(primary["evidence"][:3]) if primary is not None else []
        concept_label = (
            primary["relatedConcept"]["label"]
            if primary is not None and primary["relatedConcept"] is not None
            else primary["sourceConcept"]["label"] if primary is not None else None
        )
        dependencies = incoming.get(path, [])[:4]
        task_history = history.get(path, [])
        dependent_evidence = [entry["_evidence"] for entry in dependencies[:3]]
        history_evidence = [entry["_evidence"] for entry in task_history[:3]]
        summaries = {
            "purpose": (
                f"此文件通过 {primary['fileRelation']['relation']} 承载概念 {concept_label}。"
                if primary is not None else "尚无足够的概念映射解释此文件。"
            ),
            "relevance": (
                f"当前任务命中 {primary['sourceConcept']['label']}，图谱链将其连到此文件。"
                if primary is not None else "它是查询命中的文件候选，但缺少完整关系链。"
            ),
            "dependents": (
                "已选候选中依赖它的文件：" + "、".join(
                    f"{entry['path']}（{entry['relation']}）" for entry in dependencies
                ) if dependencies else "已选候选中未发现依赖它的文件。"
            ),
            "history": (
                "相关历史任务：" + "、".join(entry["title"] for entry in task_history)
                if task_history else "未找到安全且可追溯的历史修改任务。"
            ),
            "impact": (
                f"修改前先核对 {len(dependencies)} 个已选依赖方与 "
                f"{len(task_history)} 个历史任务，再用 Impact 查询完整影响。"
            ),
        }
        result[path] = {
            "actions": [
                {
                    "kind": kind,
                    "label": label,
                    "summary": summaries[kind],
                    "evidence": (
                        dependent_evidence if kind == "dependents"
                        else history_evidence if kind == "history"
                        else [*evidence, *dependent_evidence][:3] if kind == "impact"
                        else evidence
                    ),
                }
                for kind, label in _CONTEXT_ACTIONS
            ],
            "dependents": [
                {"path": entry["path"], "relation": entry["relation"]}
                for entry in dependencies
            ],
            "history": [
                {key: value for key, value in entry.items() if key != "_evidence"}
                for entry in task_history
            ],
        }
    return result, history_truncated


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
        connection.execute("BEGIN")
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

        match_rank = {str(row["id"]): index for index, row in enumerate(matches)}
        selected_concepts = sorted(
            concepts.values(),
            key=lambda row: (
                match_rank.get(str(row["id"]), len(matches)),
                str(row["label"]).lower(), str(row["key"]), str(row["id"]),
            ),
        )[:concept_limit]
        concept_entries: list[dict[str, Any]] = []
        relevant_files: dict[str, dict[str, Any]] = {
            str(row["key"]): {
                "path": str(row["key"]),
                "relation": "direct_match",
                "language": json_loads(row["data_json"], {}).get("language", "unknown"),
                "_rank": index,
                "_chains": [],
            }
            for index, row in enumerate(direct_files.values())
            if is_canonical_relative_path(str(row["key"]))
        }
        next_file_rank = len(relevant_files)
        for concept in selected_concepts:
            files = _concept_files(connection, project_id, concept["id"])
            for file_entry in files:
                selected = relevant_files.setdefault(
                    file_entry["path"],
                    {**file_entry, "_rank": next_file_rank, "_chains": []},
                )
                if selected["_rank"] == next_file_rank:
                    next_file_rank += 1
                chain = _context_chain(concept, file_entry)
                if chain["fileRelation"]["id"] not in {
                    item["fileRelation"]["id"] for item in selected["_chains"]
                }:
                    selected["_chains"].append(chain)
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
                    selected = relevant_files.setdefault(
                        enriched["path"],
                        {**enriched, "_rank": next_file_rank, "_chains": []},
                    )
                    if selected["_rank"] == next_file_rank:
                        next_file_rank += 1
                    chain = _context_chain(concept, neighbor_file, neighbor=neighbor)
                    if chain["fileRelation"]["id"] not in {
                        item["fileRelation"]["id"] for item in selected["_chains"]
                    }:
                        selected["_chains"].append(chain)
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

        ranked_tasks = sorted(task_matches.values(), key=lambda item: str(item["id"]))
        ranked_tasks.sort(key=lambda item: str(item["created_at"]), reverse=True)
        tasks = [
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
            for row in ranked_tasks[:task_limit]
        ]

        selected_file_items = sorted(
            relevant_files.values(),
            key=lambda item: (int(item["_rank"]), str(item["path"]).lower(), str(item["path"])),
        )[:file_limit]
        candidate_files = [
            {
                "path": item["path"],
                "relation": item["relation"],
                "language": item["language"],
            }
            for item in selected_file_items
        ]
        candidate_paths = [str(item["path"]) for item in selected_file_items]
        fresh_paths, unverifiable_paths = _fresh_context_paths(
            connection, project, candidate_paths
        )
        navigable_files = [
            item for item in selected_file_items
            if item["path"] in fresh_paths and item.get("_chains")
        ]
        relation_priority = {
            "implemented_by": 0,
            "configured_by": 1,
            "tested_by": 2,
            "documented_by": 3,
        }
        navigable_files.sort(key=lambda item: (
            0 if item["_chains"][0]["relatedConcept"] is None else 1,
            relation_priority.get(item["_chains"][0]["fileRelation"]["relation"], 4),
            int(item["_rank"]), str(item["path"]).lower(), str(item["path"]),
        ))
        action_data, history_truncated = _context_file_actions(
            connection, project_id, navigable_files
        )
        reading_order: list[dict[str, Any]] = []
        for index, item in enumerate(navigable_files, start=1):
            chain = item["_chains"][0]
            related = chain["relatedConcept"]
            if related is None:
                why = (
                    f"概念 {chain['sourceConcept']['label']} 通过 "
                    f"{chain['fileRelation']['relation']} 直接关联此文件。"
                )
            else:
                direction = chain["conceptRelation"]
                why = (
                    f"从 {chain['sourceConcept']['label']} 的一跳概念关系 "
                    f"{direction['relation']} 到 {related['label']}，再通过 "
                    f"{chain['fileRelation']['relation']} 定位此文件。"
                )
            next_path = (
                str(navigable_files[index]["path"])
                if index < len(navigable_files) else None
            )
            reading_order.append({
                "position": index,
                "path": str(item["path"]),
                "language": str(item["language"]),
                "why": why,
                "evidence": chain["evidence"][:3],
                "nextStep": (
                    {
                        "path": next_path,
                        "reason": "按当前任务相关性继续阅读下一个已选候选。",
                    }
                    if next_path is not None else None
                ),
                "chains": item["_chains"][:3],
                **action_data[str(item["path"])],
            })

        navigation_warnings: list[dict[str, Any]] = []
        stale_paths = [
            path for path in candidate_paths
            if path not in fresh_paths and path not in unverifiable_paths
        ]
        unexplained_paths = [
            str(item["path"]) for item in selected_file_items
            if item["path"] in fresh_paths and not item.get("_chains")
        ]
        if stale_paths:
            navigation_warnings.append({
                "code": "CONTEXT_NAVIGATION_STALE_FILES",
                "message": "部分候选文件与索引快照不一致，已从阅读解释中跳过。",
                "evidence": [],
            })
        if unverifiable_paths:
            navigation_warnings.append({
                "code": "CONTEXT_NAVIGATION_FRESHNESS_BUDGET",
                "message": "部分候选文件超过单文件或总 freshness I/O 预算，已从阅读解释中跳过。",
                "evidence": [],
            })
        if unexplained_paths:
            navigation_warnings.append({
                "code": "CONTEXT_NAVIGATION_EVIDENCE_INSUFFICIENT",
                "message": "部分文件候选缺少可追溯的概念映射，未生成推测性解释。",
                "evidence": [],
            })
        if history_truncated:
            navigation_warnings.append({
                "code": "CONTEXT_NAVIGATION_HISTORY_TRUNCATED",
                "message": f"历史关系扫描达到 {_CONTEXT_HISTORY_SCAN_LIMIT} 项预算，局部历史可能不完整。",
                "evidence": [],
            })

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
        "files": candidate_files,
        "tasks": tasks,
        "navigation": {
            "readingOrder": reading_order,
            "revision": stable_id(
                project_id,
                str(project["last_scan_at"] or ""),
                *(str(item) for item in reading_order),
                prefix="context_navigation_",
            ),
        },
        "warnings": navigation_warnings,
    }


def format_context(
    data: dict[str, Any],
    *,
    include_project_root: bool = True,
) -> str:
    project = data["project"]
    stats = data["stats"]
    lines = [
        "[AgentNavi 项目上下文]",
        f"项目：{project['name']}（{project['id']}）",
        f"索引：{stats['files']} 个文件 / {stats['concepts']} 个概念 / {stats['tasks']} 个历史任务",
    ]
    if include_project_root:
        lines.insert(2, f"根目录：{project['root']}")
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

    reading_order = data.get("navigation", {}).get("readingOrder", [])
    files = data.get("files", [])
    if reading_order:
        lines.append("\n建议优先读取的文件：")
        for item in reading_order:
            lines.append(f"{item['position']}. {item['path']}")
            lines.append(f"   Why：{item['why']}")
            evidence = item.get("evidence", [])
            if evidence:
                reference = evidence[0]
                location = f"（{reference['path']}）" if reference.get("path") else ""
                lines.append(f"   Evidence：{reference['summary']}{location}")
            next_step = item.get("nextStep")
            lines.append(
                f"   Next Step：{next_step['path']}"
                if next_step is not None else "   Next Step：完成当前阅读路径"
            )
            actions = item.get("actions", [])
            if actions:
                lines.append(
                    "   可查看：" + "；".join(
                        f"{action['label']}—{action['summary']}" for action in actions
                    )
                )
    elif files:
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
