"""有界、可追溯的 VLA Impact Core 查询。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from .database import Database
from .privacy import contains_private_path, is_canonical_relative_path
from .query import _context_evidence, _fresh_context_paths
from .utils import json_loads, stable_id

IMPACT_ANCHOR_LIMIT = 8
IMPACT_ANCHOR_SCAN_LIMIT = 32
IMPACT_LANE_LIMIT = 8
IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT = 24
IMPACT_SEMANTIC_LIMIT = 8
IMPACT_SEMANTIC_PER_DIRECTION_SCAN_LIMIT = 24
IMPACT_SEMANTIC_MERGE_LIMIT = 64
IMPACT_HISTORY_LIMIT = 5
IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT = 40
IMPACT_TEST_LIMIT = 5
IMPACT_RAW_EVIDENCE_LIMIT = 64
IMPACT_FRESHNESS_PATH_LIMIT = 256
IMPACT_PHYSICAL_LOOKUP_LIMIT = 256


def _evidence(*, kind: str, summary: str, layer: str, source: str,
              confidence: float, path: str | None = None) -> dict[str, Any]:
    return _context_evidence(kind=kind, summary=summary, layer=layer, source=source,
                             confidence=confidence, path=path)


def _file_evidence(path: str) -> dict[str, Any]:
    return _evidence(kind="repository-file", summary="索引中的仓库文件。", layer="L1",
                     source="repository-index", confidence=1.0, path=path)


def _entity(row: sqlite3.Row | dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    kind = str(row["kind"])
    return {"id": str(row["id"]), "kind": kind, "label": str(row["label"]),
            **({"path": str(row["key"])} if kind == "file" else {}),
            "layer": f"L{int(row['layer'])}", "source": str(row["source"]),
            "confidence": float(row["confidence"]), "evidence": evidence}


def _edge(row: sqlite3.Row, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": str(row["id"]), "sourceId": str(row["source_id"]),
            "targetId": str(row["target_id"]), "relation": str(row["relation"]),
            "layer": f"L{int(row['layer'])}", "source": str(row["source"]),
            "confidence": float(row["confidence"]), "evidence": evidence}


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _resolve_focus(connection: sqlite3.Connection, project_id: str, selector: str) -> sqlite3.Row:
    normalized = selector.replace("\\", "/").strip()
    if not normalized or contains_private_path(normalized):
        raise ValueError("impact selector 无效。")
    exact = connection.execute(
        """SELECT * FROM nodes WHERE project_id=? AND
             ((layer=1 AND kind='file' AND key=?) OR
              (layer=2 AND kind='concept' AND (key=? OR label=?)))
             ORDER BY layer, id LIMIT 2""",
        (project_id, normalized, normalized, normalized),
    ).fetchall()
    if exact:
        return exact[0]
    pattern = f"%{_escape_like(normalized.lower())}%"
    matches = connection.execute(
        """SELECT * FROM nodes WHERE project_id=? AND
             ((layer=1 AND kind='file') OR (layer=2 AND kind='concept')) AND
             (lower(key) LIKE ? ESCAPE '!' OR lower(label) LIKE ? ESCAPE '!')
             ORDER BY CASE WHEN layer=1 THEN 0 ELSE 1 END,
                      length(key), key COLLATE NOCASE, key, id LIMIT 11""",
        (project_id, pattern, pattern),
    ).fetchall()
    if len(matches) != 1:
        raise LookupError("找不到唯一的影响分析目标。")
    return matches[0]


def _mapping_evidence(row: sqlite3.Row | dict[str, Any], path: str) -> list[dict[str, Any]]:
    return [_evidence(kind="concept-file-mapping",
                      summary=f"图谱记录概念与文件的 {row['relation']} 关系。",
                      layer="L2", source=str(row["source"]),
                      confidence=float(row["confidence"]), path=path)]


def _physical_evidence(row: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    source_path, target_path = str(row["source_path"]), str(row["target_path"])
    return [_evidence(kind="physical-relation",
                      summary=f"{source_path} {row['relation']} {target_path}",
                      layer="L1", source=str(row["source"]),
                      confidence=float(row["confidence"]), path=source_path)]


def _safe_history(row: sqlite3.Row) -> bool:
    return not any(contains_private_path(str(row[key] or "")) for key in
                   ("title", "status", "source", "task_source"))


def _lane_rows(connection: sqlite3.Connection, project_id: str,
               anchor_id: str, direction: str) -> list[sqlite3.Row]:
    indexed = "idx_edges_target" if direction == "incoming" else "idx_edges_source"
    endpoint = "target_id" if direction == "incoming" else "source_id"
    return connection.execute(
        f"""SELECT /* impact-physical-lane impact-lane-{direction} */ edge.*,
                   edge.rowid AS recorded_order,
                   source.key AS source_path, source.label AS source_label,
                   source.source AS source_node_source,
                   source.confidence AS source_node_confidence,
                   target.key AS target_path, target.label AS target_label,
                   target.source AS target_node_source,
                   target.confidence AS target_node_confidence
            FROM edges AS edge INDEXED BY {indexed}
            JOIN nodes source ON source.id=edge.source_id
              AND source.layer=1 AND source.kind='file'
            JOIN nodes target ON target.id=edge.target_id
              AND target.layer=1 AND target.kind='file'
            WHERE edge.project_id=? AND edge.layer=1 AND edge.{endpoint}=?
            ORDER BY edge.rowid DESC LIMIT ?""",
        (project_id, anchor_id, IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT + 1),
    ).fetchall()


def _history_rows(connection: sqlite3.Connection, project_id: str,
                  target_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT /* impact-history */ edge.*, edge.rowid AS recorded_order,
                  task_node.source AS task_source,
                  task_node.confidence AS task_confidence,
                  task.title, task.status
           FROM edges AS edge INDEXED BY idx_edges_target
           JOIN nodes task_node ON task_node.id=edge.source_id
             AND task_node.layer=3 AND task_node.kind='task'
           JOIN tasks task ON task.id=task_node.key
           WHERE edge.project_id=? AND edge.layer=3 AND edge.target_id=?
             AND edge.source='task-events' AND task_node.source='task-events'
           ORDER BY edge.rowid DESC LIMIT ?""",
        (project_id, target_id, IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT + 1),
    ).fetchall()


def _semantic_rows(connection: sqlite3.Connection, project_id: str,
                   concept_id: str, direction: str) -> list[sqlite3.Row]:
    indexed = "idx_edges_source" if direction == "outgoing" else "idx_edges_target"
    endpoint = "source_id" if direction == "outgoing" else "target_id"
    return connection.execute(
        f"""SELECT /* impact-semantic-{direction} */ edge.*, edge.rowid AS recorded_order,
                   source.label AS source_label, source.source AS source_node_source,
                   source.confidence AS source_node_confidence,
                   target.label AS target_label, target.source AS target_node_source,
                   target.confidence AS target_node_confidence
            FROM edges edge INDEXED BY {indexed}
            JOIN nodes source ON source.id=edge.source_id
              AND source.layer=2 AND source.kind='concept'
            JOIN nodes target ON target.id=edge.target_id
              AND target.layer=2 AND target.kind='concept'
            WHERE edge.project_id=? AND edge.layer=2 AND edge.{endpoint}=?
            ORDER BY edge.rowid DESC LIMIT ?""",
        (project_id, concept_id, IMPACT_SEMANTIC_PER_DIRECTION_SCAN_LIMIT + 1),
    ).fetchall()


def impact_view_data(database: Database, project: sqlite3.Row, selector: str) -> dict[str, Any]:
    """在一个 SQLite read snapshot 内生成固定、有界 Impact 视图。"""
    project_id = str(project["id"])
    warnings: list[dict[str, Any]] = []
    with database.connect() as connection:
        connection.execute("BEGIN")
        project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise LookupError("项目不存在。")
        focus = _resolve_focus(connection, project_id, selector)

        # Anchor raw scan；safe/fresh filter 均在 display cap 之前。
        anchor_rows: list[sqlite3.Row] = []
        if int(focus["layer"]) == 1:
            anchor_candidates: list[sqlite3.Row | dict[str, Any]] = [focus]
        else:
            anchor_rows = connection.execute(
                """SELECT /* impact-anchor-mappings */ edge.*,
                          file.id AS file_id, file.key AS file_key,
                          file.label AS file_label, file.source AS file_source,
                          file.confidence AS file_confidence
                   FROM edges edge INDEXED BY idx_edges_source
                   JOIN nodes file ON file.id=edge.target_id
                     AND file.layer=1 AND file.kind='file'
                   WHERE edge.project_id=? AND edge.layer=2 AND edge.source_id=?
                     AND edge.relation IN ('implemented_by','configured_by')
                   ORDER BY edge.rowid DESC LIMIT ?""",
                (project_id, focus["id"], IMPACT_ANCHOR_SCAN_LIMIT + 1),
            ).fetchall()
            anchor_candidates = [
                {"id": row["file_id"], "layer": 1, "kind": "file", "key": row["file_key"],
                 "label": row["file_label"], "source": row["file_source"],
                 "confidence": row["file_confidence"], "mapping": row}
                for row in anchor_rows[:IMPACT_ANCHOR_SCAN_LIMIT]
            ]
        if len(anchor_rows) > IMPACT_ANCHOR_SCAN_LIMIT:
            warnings.append({"code": "IMPACT_ANCHOR_SCAN_TRUNCATED", "message": f"锚点扫描达到 {IMPACT_ANCHOR_SCAN_LIMIT} 项预算，当前结果不完整。", "evidence": []})
        safe_candidates = [row for row in anchor_candidates if is_canonical_relative_path(str(row["key"]))]
        if len(safe_candidates) != len(anchor_candidates):
            warnings.append({"code": "IMPACT_ANCHOR_UNSAFE_FILTERED", "message": "部分锚点路径不安全，已过滤。", "evidence": []})

        raw_lanes: dict[str, list[sqlite3.Row]] = {"incoming": [], "outgoing": []}
        lane_scan_truncated = False
        for anchor in safe_candidates:
            for direction in ("incoming", "outgoing"):
                rows = _lane_rows(connection, project_id, str(anchor["id"]), direction)
                lane_scan_truncated = lane_scan_truncated or len(rows) > IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT
                raw_lanes[direction].extend(rows[:IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT])

        # File focus 的概念必须由真实 L2 mapping 绑定。
        if int(focus["layer"]) == 2:
            focus_concept_rows: list[tuple[sqlite3.Row, sqlite3.Row | None]] = [(focus, None)]
        else:
            rows = connection.execute(
                """SELECT /* impact-focus-concepts */ concept.*, edge.id AS mapping_id,
                          edge.source_id AS mapping_source_id, edge.target_id AS mapping_target_id,
                          edge.relation AS mapping_relation, edge.source AS mapping_source,
                          edge.confidence AS mapping_confidence
                   FROM edges edge INDEXED BY idx_edges_target
                   JOIN nodes concept ON concept.id=edge.source_id
                     AND concept.layer=2 AND concept.kind='concept'
                   WHERE edge.project_id=? AND edge.layer=2 AND edge.target_id=?
                     AND edge.relation IN ('implemented_by','configured_by','tested_by')
                   ORDER BY edge.rowid DESC LIMIT 33""",
                (project_id, focus["id"]),
            ).fetchall()
            focus_concept_candidates = []
            seen_focus_concepts: set[str] = set()
            for row in rows[:32]:
                if str(row["id"]) not in seen_focus_concepts:
                    seen_focus_concepts.add(str(row["id"])); focus_concept_candidates.append((row, row))
            focus_concept_rows = focus_concept_candidates[:8]
            if len(rows) > 32 or len(focus_concept_candidates) > 8:
                warnings.append({"code": "IMPACT_FOCUS_CONCEPTS_TRUNCATED", "message": "Focus 关联概念最多展示 8 项。", "evidence": []})
        focus_concept_ids = {str(row[0]["id"]) for row in focus_concept_rows}

        semantic_candidates: list[sqlite3.Row] = []
        semantic_scan_truncated = False
        for concept_id in sorted(focus_concept_ids):
            for direction in ("incoming", "outgoing"):
                rows = _semantic_rows(connection, project_id, concept_id, direction)
                semantic_scan_truncated = semantic_scan_truncated or len(rows) > IMPACT_SEMANTIC_PER_DIRECTION_SCAN_LIMIT
                semantic_candidates.extend(rows[:IMPACT_SEMANTIC_PER_DIRECTION_SCAN_LIMIT])
        semantic_rows = []
        seen_semantic: set[str] = set()
        for row in sorted(semantic_candidates, key=lambda item: (-int(item["recorded_order"]), str(item["id"]))):
            if str(row["id"]) not in seen_semantic:
                seen_semantic.add(str(row["id"])); semantic_rows.append(row)
        if semantic_scan_truncated:
            warnings.append({"code": "IMPACT_SEMANTIC_SCAN_TRUNCATED", "message": f"每个焦点概念每方向最多扫描 {IMPACT_SEMANTIC_PER_DIRECTION_SCAN_LIMIT} 条语义记录。", "evidence": []})
        if len(semantic_rows) > IMPACT_SEMANTIC_MERGE_LIMIT:
            warnings.append({"code": "IMPACT_SEMANTIC_MERGE_TRUNCATED", "message": f"Semantic 合并候选最多保留 {IMPACT_SEMANTIC_MERGE_LIMIT} 项。", "evidence": []})
        semantic_rows = semantic_rows[:IMPACT_SEMANTIC_MERGE_LIMIT]
        tested_by_rows: list[sqlite3.Row] = []
        if focus_concept_ids:
            marks = ",".join("?" for _ in focus_concept_ids)
            tested_by_rows = connection.execute(
                f"""SELECT /* impact-tested-by */ edge.*,
                           test.id AS test_id, test.key AS test_path,
                           test.label AS test_label, test.source AS test_source,
                           test.confidence AS test_confidence
                    FROM edges edge JOIN nodes test ON test.id=edge.target_id
                    WHERE edge.project_id=? AND edge.layer=2
                      AND edge.relation='tested_by' AND edge.source_id IN ({marks})
                      AND test.layer=1 AND test.kind='file'
                    ORDER BY edge.rowid DESC LIMIT ?""",
                (project_id, *focus_concept_ids, IMPACT_TEST_LIMIT + 1),
            ).fetchall()

        anchor_freshness_paths = [str(row["key"]) for row in safe_candidates]
        freshness_paths = list(anchor_freshness_paths)
        for lane_rows in raw_lanes.values():
            for row in lane_rows:
                freshness_paths.extend((str(row["source_path"]), str(row["target_path"])))
        freshness_paths.extend(str(row["test_path"]) for row in tested_by_rows)
        for row in semantic_rows:
            raw = json_loads(str(row["data_json"]), {}).get("evidence", [])
            if isinstance(raw, list):
                for item in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                    if isinstance(item, dict):
                        freshness_paths.extend(str(item.get(key, "")) for key in ("source", "target"))
        ordered_anchors = list(dict.fromkeys(path for path in anchor_freshness_paths if is_canonical_relative_path(path)))
        other_paths = sorted(
            {path for path in freshness_paths if is_canonical_relative_path(path)} - set(ordered_anchors),
            key=lambda value: (value.lower(), value),
        )
        freshness_paths = ordered_anchors + other_paths
        freshness_scan_truncated = len(freshness_paths) > IMPACT_FRESHNESS_PATH_LIMIT
        freshness_paths = freshness_paths[:IMPACT_FRESHNESS_PATH_LIMIT]
        fresh_paths, unverifiable, symlinks = _fresh_context_paths(connection, project, freshness_paths)
        if freshness_scan_truncated:
            warnings.append({"code": "IMPACT_FRESHNESS_SCAN_TRUNCATED", "message": f"Freshness 路径扫描达到 {IMPACT_FRESHNESS_PATH_LIMIT} 项预算。", "evidence": []})
        if unverifiable:
            warnings.append({"code": "IMPACT_FRESHNESS_IO_BUDGET", "message": "部分路径超过 freshness I/O 字节预算，已过滤。", "evidence": []})
        if symlinks:
            warnings.append({"code": "IMPACT_SYMLINK_FILTERED", "message": "部分符号链接路径无法可靠验证，已过滤。", "evidence": []})

        fresh_anchor_candidates = [row for row in safe_candidates if str(row["key"]) in fresh_paths]
        if len(fresh_anchor_candidates) != len(safe_candidates):
            warnings.append({"code": "IMPACT_ANCHOR_STALE_FILTERED", "message": "部分锚点与索引快照不一致，已过滤。", "evidence": []})
        anchors = fresh_anchor_candidates[:IMPACT_ANCHOR_LIMIT]
        if len(fresh_anchor_candidates) > IMPACT_ANCHOR_LIMIT:
            warnings.append({"code": "IMPACT_ANCHORS_DISPLAY_TRUNCATED", "message": f"锚点最多展示 {IMPACT_ANCHOR_LIMIT} 项；未展示锚点的影响不在当前视图中。", "evidence": []})
        anchor_ids = {str(row["id"]) for row in anchors}
        anchor_paths = {str(row["key"]) for row in anchors}

        anchor_files = []
        for row in anchors:
            file_ev = [_file_evidence(str(row["key"]))]
            mapping = None
            evidence = file_ev
            if int(focus["layer"]) == 2:
                mapping_row = row["mapping"]
                mapping_ev = _mapping_evidence(mapping_row, str(row["key"]))
                mapping = _edge(mapping_row, mapping_ev)
                evidence = mapping_ev
            anchor_files.append({"entity": _entity(row, file_ev), "mapping": mapping,
                                 "evidence": evidence})

        lanes: dict[str, list[dict[str, Any]]] = {"incoming": [], "outgoing": []}
        lane_stale_filtered = False
        lane_unsafe_filtered = False
        for direction, rows in raw_lanes.items():
            seen: set[str] = set()
            for row in sorted(rows, key=lambda item: (-int(item["recorded_order"]), str(item["id"]))):
                if str(row["id"]) in seen:
                    continue
                seen.add(str(row["id"]))
                source_path, target_path = str(row["source_path"]), str(row["target_path"])
                if not is_canonical_relative_path(source_path) or not is_canonical_relative_path(target_path):
                    lane_unsafe_filtered = True
                    continue
                anchor_id = str(row["target_id"] if direction == "incoming" else row["source_id"])
                via_path = target_path if direction == "incoming" else source_path
                if anchor_id not in anchor_ids or via_path not in anchor_paths:
                    continue
                if source_path not in fresh_paths or target_path not in fresh_paths:
                    lane_stale_filtered = True
                    continue
                ev = _physical_evidence(row)
                peer_path = source_path if direction == "incoming" else target_path
                peer = {"id": str(row["source_id"] if direction == "incoming" else row["target_id"]),
                        "kind": "file", "label": str(row["source_label"] if direction == "incoming" else row["target_label"]),
                        "path": peer_path, "layer": "L1",
                        "source": str(row["source_node_source"] if direction == "incoming" else row["target_node_source"]),
                        "confidence": float(row["source_node_confidence"] if direction == "incoming" else row["target_node_confidence"]),
                        "evidence": [_file_evidence(peer_path)]}
                lanes[direction].append({"peer": peer, "relation": _edge(row, ev),
                                         "viaPath": via_path, "recordedOrder": int(row["recorded_order"]),
                                         "evidence": ev})
        if lane_scan_truncated:
            warnings.append({"code": "IMPACT_LANE_SCAN_TRUNCATED", "message": f"每锚点每方向最多扫描 {IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT} 条记录，当前结果可能不完整。", "evidence": []})
        if lane_stale_filtered:
            warnings.append({"code": "IMPACT_LANE_STALE_FILTERED", "message": "部分物理关系端点与索引快照不一致，已过滤。", "evidence": []})
        if lane_unsafe_filtered:
            warnings.append({"code": "IMPACT_LANE_UNSAFE_FILTERED", "message": "部分物理关系端点路径不安全，已过滤。", "evidence": []})
        for direction in ("incoming", "outgoing"):
            if len(lanes[direction]) > IMPACT_LANE_LIMIT:
                warnings.append({"code": f"IMPACT_{direction.upper()}_DISPLAY_TRUNCATED", "message": f"{direction.title()} 按关系记录顺序最多展示 {IMPACT_LANE_LIMIT} 项。", "evidence": []})
            lanes[direction] = lanes[direction][:IMPACT_LANE_LIMIT]
        if any(item["code"] in {"IMPACT_INCOMING_DISPLAY_TRUNCATED", "IMPACT_OUTGOING_DISPLAY_TRUNCATED"}
               for item in warnings):
            warnings.append({"code": "IMPACT_PHYSICAL_TRUNCATED", "message": "物理影响按固定展示上限截断。", "evidence": []})

        focus_evidence = ([_file_evidence(str(focus["key"]))] if int(focus["layer"]) == 1 else
                          [_evidence(kind="semantic-node", summary="索引中的概念节点。", layer="L2",
                                     source=str(focus["source"]), confidence=float(focus["confidence"]))])
        focus_entity = _entity(focus, focus_evidence)
        focus_concepts = []
        focus_concept_by_id: dict[str, dict[str, Any]] = {}
        for row, mapping_row in focus_concept_rows:
            if mapping_row is None:
                concept_ev = focus_evidence
                mapping = None
            else:
                mapping_data = {"relation": mapping_row["mapping_relation"],
                                "source": mapping_row["mapping_source"],
                                "confidence": mapping_row["mapping_confidence"]}
                concept_ev = _mapping_evidence(mapping_data, str(focus["key"]))
                mapping = {"id": str(mapping_row["mapping_id"]),
                           "sourceId": str(mapping_row["mapping_source_id"]),
                           "targetId": str(mapping_row["mapping_target_id"]),
                           "relation": str(mapping_row["mapping_relation"]), "layer": "L2",
                           "source": str(mapping_row["mapping_source"]),
                           "confidence": float(mapping_row["mapping_confidence"]),
                           "evidence": concept_ev}
            entry = {"entity": _entity(row, concept_ev), "mapping": mapping,
                     "evidence": concept_ev}
            focus_concepts.append(entry)
            focus_concept_by_id[str(row["id"])] = entry

        component_paths: dict[str, set[str]] = defaultdict(set)
        component_file_ids: dict[tuple[str, str], str] = {}
        raw_component_pairs: set[tuple[str, str]] = set()
        for semantic_row in semantic_rows:
            raw = json_loads(str(semantic_row["data_json"]), {}).get("evidence", [])
            if not isinstance(raw, list):
                continue
            for item in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                if isinstance(item, dict):
                    source_path, target_path = item.get("source"), item.get("target")
                    if isinstance(source_path, str) and source_path in fresh_paths:
                        raw_component_pairs.add((str(semantic_row["source_id"]), source_path))
                    if isinstance(target_path, str) and target_path in fresh_paths:
                        raw_component_pairs.add((str(semantic_row["target_id"]), target_path))
        mapping_candidates: dict[str, tuple[str, str, str]] = {}
        mapping_pair_truncated = len(raw_component_pairs) > IMPACT_PHYSICAL_LOOKUP_LIMIT
        for concept_id, path in sorted(raw_component_pairs)[:IMPACT_PHYSICAL_LOOKUP_LIMIT]:
            file_id = Database.node_id(project_id, 1, "file", path)
            for relation in ("implemented_by", "configured_by", "tested_by"):
                mapping_candidates[Database.edge_id(project_id, 2, concept_id, relation, file_id)] = (concept_id, path, file_id)
        if mapping_candidates:
            marks = ",".join("?" for _ in mapping_candidates)
            mapping_rows = connection.execute(
                f"SELECT /* impact-component-mapping-exact */ id FROM edges WHERE id IN ({marks})",
                tuple(mapping_candidates),
            ).fetchall()
            for mapping_row in mapping_rows:
                concept_id, path, file_id = mapping_candidates[str(mapping_row["id"])]
                component_paths[concept_id].add(path)
                component_file_ids[(concept_id, path)] = file_id
        physical_lookup: dict[tuple[str, str, str], sqlite3.Row | dict[str, Any]] = {}
        physical_lookup_truncated = False
        physical_candidates: dict[str, tuple[str, str, str]] = {}
        for semantic_row in semantic_rows:
            raw = json_loads(str(semantic_row["data_json"]), {}).get("evidence", [])
            if not isinstance(raw, list):
                continue
            for item in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                if not isinstance(item, dict):
                    continue
                source_path, target_path, relation = item.get("source"), item.get("target"), item.get("physical_relation")
                if not all(isinstance(value, str) for value in (source_path, target_path, relation)):
                    continue
                source_file = component_file_ids.get((str(semantic_row["source_id"]), source_path))
                target_file = component_file_ids.get((str(semantic_row["target_id"]), target_path))
                if source_file and target_file:
                    edge_id = Database.edge_id(project_id, 1, source_file, relation, target_file)
                    physical_candidates.setdefault(edge_id, (source_path, target_path, relation))
        if len(physical_candidates) > IMPACT_PHYSICAL_LOOKUP_LIMIT:
            physical_lookup_truncated = True
        candidate_items = list(physical_candidates.items())[:IMPACT_PHYSICAL_LOOKUP_LIMIT]
        if candidate_items:
            marks = ",".join("?" for _ in candidate_items)
            rows = connection.execute(
                f"""SELECT /* impact-physical-lookup-exact */ edge.*
                    FROM edges edge WHERE edge.id IN ({marks})""",
                tuple(edge_id for edge_id, _ in candidate_items),
            ).fetchall()
            paths_by_id = dict(candidate_items)
            for row in rows:
                source_path, target_path, relation = paths_by_id[str(row["id"])]
                physical_lookup[(source_path, target_path, relation)] = {
                    **dict(row), "source_path": source_path, "target_path": target_path,
                }

        semantic = []
        semantic_evidence_dropped = False
        raw_evidence_truncated = False
        for row in semantic_rows:
            outgoing = str(row["source_id"]) in focus_concept_ids
            focus_id = str(row["source_id"] if outgoing else row["target_id"])
            if focus_id not in focus_concept_by_id:
                continue
            ev: list[dict[str, Any]] = []
            if str(row["source"]) == "human-overlay":
                ev = [_evidence(kind="human-decision", summary="人工确认的语义影响关系。",
                                layer="L2", source="human-overlay",
                                confidence=float(row["confidence"]))]
            else:
                raw = json_loads(str(row["data_json"]), {}).get("evidence", [])
                if not isinstance(raw, list):
                    raw = []
                raw_evidence_truncated = raw_evidence_truncated or len(raw) > IMPACT_RAW_EVIDENCE_LIMIT
                for item in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                    if not isinstance(item, dict):
                        continue
                    source_path, target_path = item.get("source"), item.get("target")
                    relation = item.get("physical_relation")
                    if not all(isinstance(value, str) for value in (source_path, target_path, relation)):
                        continue
                    if (source_path not in component_paths.get(str(row["source_id"]), set()) or
                            target_path not in component_paths.get(str(row["target_id"]), set())):
                        continue
                    physical = physical_lookup.get((source_path, target_path, relation))
                    if physical is not None:
                        ev.append(_physical_evidence(physical)[0])
                    if len(ev) == 3:
                        break
            if not ev:
                semantic_evidence_dropped = True
                continue
            peer = {"id": str(row["target_id"] if outgoing else row["source_id"]),
                    "kind": "concept", "label": str(row["target_label"] if outgoing else row["source_label"]),
                    "layer": "L2", "source": str(row["target_node_source"] if outgoing else row["source_node_source"]),
                    "confidence": float(row["target_node_confidence"] if outgoing else row["source_node_confidence"]),
                    "evidence": ev}
            semantic.append({"direction": "outgoing" if outgoing else "incoming",
                             "focusConceptId": focus_id, "peer": peer,
                             "relation": _edge(row, ev), "evidence": ev})
        if len(semantic) > IMPACT_SEMANTIC_LIMIT:
            warnings.append({"code": "IMPACT_SEMANTIC_DISPLAY_TRUNCATED", "message": f"Semantic 按关系记录顺序最多展示 {IMPACT_SEMANTIC_LIMIT} 项。", "evidence": []})
        semantic = semantic[:IMPACT_SEMANTIC_LIMIT]
        if semantic_evidence_dropped:
            warnings.append({"code": "IMPACT_SEMANTIC_EVIDENCE_INSUFFICIENT", "message": "部分自动语义关系缺少方向一致的当前物理证据，已跳过。", "evidence": []})
        if raw_evidence_truncated:
            warnings.append({"code": "IMPACT_SEMANTIC_EVIDENCE_TRUNCATED", "message": f"每条语义关系最多核验 {IMPACT_RAW_EVIDENCE_LIMIT} 项原始证据。", "evidence": []})
        if physical_lookup_truncated:
            warnings.append({"code": "IMPACT_PHYSICAL_LOOKUP_TRUNCATED", "message": f"物理证据核验达到 {IMPACT_PHYSICAL_LOOKUP_LIMIT} 条预算。", "evidence": []})
        if mapping_pair_truncated:
            warnings.append({"code": "IMPACT_COMPONENT_MAPPING_TRUNCATED", "message": f"概念文件证据核验达到 {IMPACT_PHYSICAL_LOOKUP_LIMIT} 对预算。", "evidence": []})

        history_rows = []
        history_scan_truncated = False
        history_targets = sorted(anchor_ids | {str(focus["id"])} | focus_concept_ids)
        for target_id in history_targets:
            rows = _history_rows(connection, project_id, target_id)
            history_scan_truncated = history_scan_truncated or len(rows) > IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT
            history_rows.extend(rows[:IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT])
        history = []
        seen_history: set[str] = set()
        history_filtered = False
        for row in sorted(history_rows, key=lambda item: (-int(item["recorded_order"]), str(item["id"]))):
            if str(row["id"]) in seen_history:
                continue
            seen_history.add(str(row["id"]))
            if not _safe_history(row):
                history_filtered = True
                continue
            ev = [_evidence(kind="task-relation", summary="events.jsonl 重放得到的任务关联。",
                            layer="L3", source="task-events", confidence=float(row["confidence"]))]
            task = {"id": str(row["source_id"]), "kind": "task", "label": str(row["title"]),
                    "layer": "L3", "source": "task-events",
                    "confidence": float(row["task_confidence"]), "evidence": ev}
            history.append({"entity": task, "status": str(row["status"]),
                            "relation": _edge(row, ev), "recordedOrder": int(row["recorded_order"]),
                            "evidence": ev})
        if history_scan_truncated:
            warnings.append({"code": "IMPACT_HISTORY_SCAN_TRUNCATED", "message": f"每目标历史最多扫描 {IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT} 条记录。", "evidence": []})
        if history_filtered:
            warnings.append({"code": "IMPACT_HISTORY_UNSAFE_FILTERED", "message": "部分历史任务包含不安全文本，已过滤。", "evidence": []})
        if len(history) > IMPACT_HISTORY_LIMIT:
            warnings.append({"code": "IMPACT_HISTORY_DISPLAY_TRUNCATED", "message": f"History 按关系记录顺序最多展示 {IMPACT_HISTORY_LIMIT} 项。", "evidence": []})
        history = history[:IMPACT_HISTORY_LIMIT]

        tests = []
        test_stale_filtered = False
        for item in lanes["incoming"]:
            if item["relation"]["relation"] == "tests":
                tests.append({"basis": "physical-tests", "path": item["peer"]["path"],
                              "reason": "真实 L1 tests 关系指向当前锚点。", "sourceConcept": None,
                              "entity": item["peer"], "relation": item["relation"],
                              "evidence": item["evidence"]})
        for row in tested_by_rows:
            path = str(row["test_path"])
            source_entry = focus_concept_by_id.get(str(row["source_id"]))
            if source_entry is None or path not in fresh_paths or not is_canonical_relative_path(path):
                test_stale_filtered = test_stale_filtered or (
                    source_entry is not None and is_canonical_relative_path(path) and path not in fresh_paths
                )
                continue
            ev = _mapping_evidence(row, path)
            test_entity = {"id": str(row["test_id"]), "kind": "file", "label": str(row["test_label"]),
                           "path": path, "layer": "L1", "source": str(row["test_source"]),
                           "confidence": float(row["test_confidence"]), "evidence": [_file_evidence(path)]}
            tests.append({"basis": "semantic-tested-by", "path": path,
                          "reason": "真实 L2 tested_by 映射关联此测试文件。",
                          "sourceConcept": source_entry["entity"], "entity": test_entity,
                          "relation": _edge(row, ev), "evidence": ev})
        tests.sort(key=lambda item: (item["basis"], item["path"], item["relation"]["id"]))
        deduped_tests = []
        seen_test_edges: set[str] = set()
        for item in tests:
            if item["relation"]["id"] not in seen_test_edges:
                seen_test_edges.add(item["relation"]["id"])
                deduped_tests.append(item)
        if len(deduped_tests) > IMPACT_TEST_LIMIT or len(tested_by_rows) > IMPACT_TEST_LIMIT:
            warnings.append({"code": "IMPACT_TESTS_DISPLAY_TRUNCATED", "message": f"测试建议最多展示 {IMPACT_TEST_LIMIT} 项。", "evidence": []})
        tests = deduped_tests[:IMPACT_TEST_LIMIT]
        if test_stale_filtered:
            warnings.append({"code": "IMPACT_TEST_STALE_FILTERED", "message": "部分测试映射的文件与索引快照不一致，已过滤。", "evidence": []})

        risks = []
        if lanes["incoming"]:
            risks.append({"kind": "incoming-dependents", "severity": "high" if len(lanes["incoming"]) >= 4 else "medium", "summary": f"当前展示 {len(lanes['incoming'])} 条入向物理关系。", "evidence": lanes["incoming"][0]["evidence"]})
        if lanes["outgoing"]:
            risks.append({"kind": "outgoing-dependencies", "severity": "medium", "summary": f"当前展示 {len(lanes['outgoing'])} 条出向物理关系。", "evidence": lanes["outgoing"][0]["evidence"]})
        if semantic:
            risks.append({"kind": "semantic-coupling", "severity": "medium", "summary": f"当前展示 {len(semantic)} 条有证据的语义关系。", "evidence": semantic[0]["evidence"]})
        if history:
            risks.append({"kind": "historical-change", "severity": "low", "summary": f"当前展示 {len(history)} 条任务关联。", "evidence": history[0]["evidence"]})
        if any("TRUNCATED" in item["code"] or "BUDGET" in item["code"] for item in warnings):
            risks.append({"kind": "bounded-result", "severity": "medium", "summary": "有界查询已截断；当前视图不能视为完整影响证明。", "evidence": focus_evidence})

        actions = [
            {"kind": "purpose", "label": "它做什么", "summary": f"聚焦真实{'文件' if int(focus['layer']) == 1 else '概念'}：{focus['label']}。", "evidence": focus_evidence},
            {"kind": "callers", "label": "谁调用它", "summary": f"当前有界结果展示 {len(lanes['incoming'])} 条入向物理关系。", "evidence": lanes["incoming"][0]["evidence"] if lanes["incoming"] else []},
            {"kind": "dependencies", "label": "它依赖谁", "summary": f"当前有界结果展示 {len(lanes['outgoing'])} 条出向物理关系。", "evidence": lanes["outgoing"][0]["evidence"] if lanes["outgoing"] else []},
            {"kind": "change", "label": "如果修改它", "summary": f"优先核对 {len(lanes['incoming'])} 个调用方向、{len(lanes['outgoing'])} 个依赖方向和 {len(tests)} 项真实测试建议。", "evidence": (lanes["incoming"] + lanes["outgoing"])[0]["evidence"] if lanes["incoming"] or lanes["outgoing"] else focus_evidence},
            {"kind": "history", "label": "过去谁改过它", "summary": f"当前有界结果展示 {len(history)} 条按关系记录顺序排列的任务关联。", "evidence": history[0]["evidence"] if history else []},
        ]
        stats = {
            "files": connection.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND layer=1 AND kind='file'", (project_id,)).fetchone()[0],
            "concepts": connection.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND layer=2 AND kind='concept'", (project_id,)).fetchone()[0],
            "tasks": connection.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (project_id,)).fetchone()[0],
        }

    stale = bool(len(fresh_anchor_candidates) != len(safe_candidates) or lane_stale_filtered or
                 semantic_evidence_dropped or test_stale_filtered or unverifiable or symlinks or
                 (int(focus["layer"]) == 1 and str(focus["key"]) not in fresh_paths))
    source_state = {"status": "partial" if not project["last_scan_at"] else ("stale" if stale else "ready"),
                    "revision": stable_id(project_id, str(project["last_scan_at"] or ""), prefix="impact_source_"),
                    "indexed_at": project["last_scan_at"]}
    revision = stable_id(project_id, str(project["last_scan_at"] or ""), str(focus_entity),
                         str(anchor_files), str(lanes), str(semantic), str(history), prefix="impact_")
    return {"project": {"id": project_id, "name": str(project["name"]), "root": str(project["root"]),
                         "kind": str(project["kind"]), "last_scan_at": project["last_scan_at"]},
            "sourceState": source_state, "layout": "incoming-focus-outgoing", "revision": revision,
            "focus": {"entity": focus_entity, "evidence": focus_evidence},
            "anchorFiles": anchor_files, "focusConcepts": focus_concepts,
            "incoming": lanes["incoming"], "outgoing": lanes["outgoing"], "semantic": semantic,
            "history": history, "testRecommendations": tests, "risks": risks[:5],
            "actions": actions, "stats": stats, "warnings": warnings}


__all__ = ["IMPACT_ANCHOR_LIMIT", "IMPACT_ANCHOR_SCAN_LIMIT",
           "IMPACT_HISTORY_PER_TARGET_SCAN_LIMIT", "IMPACT_LANE_PER_ANCHOR_SCAN_LIMIT",
           "impact_view_data"]
