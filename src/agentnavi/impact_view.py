"""有界、可追溯的 VLA Impact Core 查询。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from .database import Database
from .privacy import contains_private_path, is_canonical_relative_path
from .query import _fresh_context_paths, _context_evidence
from .utils import json_loads, stable_id


IMPACT_LANE_LIMIT = 8
IMPACT_SEMANTIC_LIMIT = 8
IMPACT_HISTORY_LIMIT = 5
IMPACT_HISTORY_SCAN_LIMIT = 40
IMPACT_TEST_LIMIT = 5
IMPACT_RAW_EVIDENCE_LIMIT = 64
IMPACT_PATH_BUDGET = 32
IMPACT_PHYSICAL_LOOKUP_LIMIT = 256


def _node_entity(row: sqlite3.Row, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    layer = f"L{int(row['layer'])}"
    path = str(row["key"]) if row["kind"] == "file" else None
    return {
        "id": str(row["id"]), "kind": str(row["kind"]), "label": str(row["label"]),
        **({"path": path} if path is not None else {}),
        "layer": layer, "source": str(row["source"]),
        "confidence": float(row["confidence"]), "evidence": evidence,
    }


def _edge(row: sqlite3.Row, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row["id"]), "sourceId": str(row["source_id"]),
        "targetId": str(row["target_id"]), "relation": str(row["relation"]),
        "layer": f"L{int(row['layer'])}", "source": str(row["source"]),
        "confidence": float(row["confidence"]), "evidence": evidence,
    }


def _file_evidence(path: str, *, summary: str = "索引中的当前仓库文件。") -> dict[str, Any]:
    return _context_evidence(
        kind="repository-file", summary=summary, layer="L1",
        source="repository-index", confidence=1.0, path=path,
    )


def _safe_task(row: sqlite3.Row) -> bool:
    return not any(
        contains_private_path(str(row[key] or ""))
        for key in ("title", "status", "source", "task_node_source")
    )


def _resolve_focus(connection: sqlite3.Connection, project_id: str, selector: str) -> sqlite3.Row:
    normalized = selector.replace("\\", "/").strip()
    if not normalized or contains_private_path(normalized):
        raise ValueError("impact selector 必须是非空的仓库相对路径或概念名称。")
    exact = connection.execute(
        """SELECT * FROM nodes WHERE project_id=? AND
             ((layer=1 AND kind='file' AND key=?) OR
              (layer=2 AND kind='concept' AND (key=? OR label=?)))
             ORDER BY layer, id LIMIT 2""",
        (project_id, normalized, normalized, normalized),
    ).fetchall()
    if exact:
        return exact[0]
    pattern = f"%{normalized.lower()}%"
    matches = connection.execute(
        """SELECT * FROM nodes WHERE project_id=? AND
             ((layer=1 AND kind='file') OR (layer=2 AND kind='concept')) AND
             (lower(key) LIKE ? OR lower(label) LIKE ?)
             ORDER BY CASE WHEN layer=1 THEN 0 ELSE 1 END,
                      length(key), key COLLATE NOCASE, key, id LIMIT 11""",
        (project_id, pattern, pattern),
    ).fetchall()
    if not matches:
        raise LookupError("找不到可分析的文件或概念。")
    if len(matches) > 1:
        raise LookupError("匹配到多个目标，请提供更完整的仓库相对路径或概念名称。")
    return matches[0]


def _source_state(project: sqlite3.Row) -> dict[str, Any]:
    indexed_at = project["last_scan_at"]
    return {
        "status": "ready" if indexed_at else "partial",
        "revision": stable_id(str(project["id"]), str(indexed_at or ""), prefix="impact_source_"),
        "indexed_at": indexed_at,
    }


def impact_view_data(database: Database, project: sqlite3.Row, selector: str) -> dict[str, Any]:
    """在同一 SQLite read snapshot 内生成固定 Impact 布局。"""

    project_id = str(project["id"])
    warnings: list[dict[str, Any]] = []
    with database.connect() as connection:
        connection.execute("BEGIN")
        snapshot_project = connection.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if snapshot_project is None:
            raise LookupError("项目不存在。")
        project = snapshot_project
        focus = _resolve_focus(connection, project_id, selector)

        # Concept 使用第一个稳定且可验证的映射文件作为物理影响锚点。
        if int(focus["layer"]) == 1:
            anchor_candidates = [focus]
        else:
            anchor_candidates = connection.execute(
                """SELECT file.* FROM edges edge JOIN nodes file ON file.id=edge.target_id
                   WHERE edge.project_id=? AND edge.layer=2 AND edge.source_id=?
                     AND edge.relation IN ('implemented_by','configured_by','tested_by')
                     AND file.layer=1 AND file.kind='file'
                   ORDER BY CASE edge.relation WHEN 'implemented_by' THEN 0 WHEN 'configured_by' THEN 1 ELSE 2 END,
                            file.key COLLATE NOCASE, file.key, edge.id LIMIT 9""",
                (project_id, focus["id"]),
            ).fetchall()
        safe_anchor_candidates = [row for row in anchor_candidates if is_canonical_relative_path(str(row["key"]))]

        if int(focus["layer"]) == 2:
            focus_concepts = [focus]
        else:
            focus_concepts = connection.execute(
                """SELECT concept.* FROM edges edge JOIN nodes concept ON concept.id=edge.source_id
                   WHERE edge.project_id=? AND edge.layer=2 AND edge.target_id=?
                     AND concept.layer=2 AND concept.kind='concept'
                   ORDER BY concept.label COLLATE NOCASE, concept.label, concept.id LIMIT 9""",
                (project_id, focus["id"]),
            ).fetchall()
        focus_concept_ids = {str(row["id"]) for row in focus_concepts[:8]}
        semantic_rows: list[sqlite3.Row] = []
        if focus_concept_ids:
            placeholders = ",".join("?" for _ in focus_concept_ids)
            semantic_rows = connection.execute(
                f"""SELECT edge.*, source.label AS source_label, source.source AS source_node_source,
                            source.confidence AS source_node_confidence,
                            target.label AS target_label, target.source AS target_node_source,
                            target.confidence AS target_node_confidence
                     FROM edges edge
                     JOIN nodes source ON source.id=edge.source_id AND source.layer=2 AND source.kind='concept'
                     JOIN nodes target ON target.id=edge.target_id AND target.layer=2 AND target.kind='concept'
                     WHERE edge.project_id=? AND edge.layer=2 AND
                           (edge.source_id IN ({placeholders}) OR edge.target_id IN ({placeholders}))
                     ORDER BY edge.relation, source.label COLLATE NOCASE, source.label,
                              target.label COLLATE NOCASE, target.label, edge.id LIMIT ?""",
                (project_id, *focus_concept_ids, *focus_concept_ids, IMPACT_RAW_EVIDENCE_LIMIT + 1),
            ).fetchall()
        semantic_node_ids = sorted({str(row[key]) for row in semantic_rows for key in ("source_id", "target_id")})
        mapping_rows: list[sqlite3.Row] = []
        if semantic_node_ids:
            placeholders = ",".join("?" for _ in semantic_node_ids)
            mapping_rows = connection.execute(
                f"""SELECT edge.source_id AS concept_id, file.key AS path
                     FROM edges edge JOIN nodes file ON file.id=edge.target_id
                     WHERE edge.project_id=? AND edge.layer=2
                       AND edge.relation IN ('implemented_by','configured_by','tested_by','documented_by')
                       AND edge.source_id IN ({placeholders})
                       AND file.layer=1 AND file.kind='file'
                     ORDER BY edge.source_id, file.key COLLATE NOCASE, file.key, edge.id
                     LIMIT ?""",
                (project_id, *semantic_node_ids, IMPACT_PATH_BUDGET * 2 + 1),
            ).fetchall()

        candidate_l1: list[sqlite3.Row] = []
        if safe_anchor_candidates:
            anchor_ids = [str(row["id"]) for row in safe_anchor_candidates[:8]]
            placeholders = ",".join("?" for _ in anchor_ids)
            lane_select = f"""SELECT /* impact-physical-lane */ edge.*, source.key AS source_path, source.label AS source_label,
                            source.source AS source_node_source, source.confidence AS source_node_confidence,
                            target.key AS target_path, target.label AS target_label,
                            target.source AS target_node_source, target.confidence AS target_node_confidence
                     FROM edges edge
                     JOIN nodes source ON source.id=edge.source_id AND source.layer=1 AND source.kind='file'
                     JOIN nodes target ON target.id=edge.target_id AND target.layer=1 AND target.kind='file'
                     WHERE edge.project_id=? AND edge.layer=1 AND {{predicate}}
                     ORDER BY edge.relation, source.key COLLATE NOCASE, source.key,
                              target.key COLLATE NOCASE, target.key, edge.id
                     LIMIT ?"""
            # 两个方向各自稳定排序、各自预留一条截断哨兵，避免一侧挤掉另一侧。
            candidate_l1.extend(connection.execute(
                lane_select.format(predicate=f"edge.target_id IN ({placeholders})"),
                (project_id, *anchor_ids, IMPACT_LANE_LIMIT + 1),
            ).fetchall())
            candidate_l1.extend(connection.execute(
                lane_select.format(predicate=f"edge.source_id IN ({placeholders})"),
                (project_id, *anchor_ids, IMPACT_LANE_LIMIT + 1),
            ).fetchall())
            candidate_l1 = list({str(row["id"]): row for row in candidate_l1}.values())
        lane_paths = sorted({
            str(path) for row in candidate_l1 for path in (row["source_path"], row["target_path"])
            if is_canonical_relative_path(str(path))
        }, key=lambda p: (p.lower(), p))
        semantic_paths: set[str] = {
            str(row["path"]) for row in mapping_rows if is_canonical_relative_path(str(row["path"]))
        }
        for row in semantic_rows:
            raw = json_loads(str(row["data_json"]), {}).get("evidence", [])
            if isinstance(raw, list):
                for item in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                    if isinstance(item, dict):
                        for key in ("source", "target"):
                            path = item.get(key)
                            if isinstance(path, str) and is_canonical_relative_path(path):
                                semantic_paths.add(path)
        ordered_paths = [str(row["key"]) for row in safe_anchor_candidates]
        ordered_paths.extend(lane_paths)
        ordered_paths.extend(sorted(semantic_paths, key=lambda p: (p.lower(), p)))
        all_paths = list(dict.fromkeys(ordered_paths))
        path_budget_truncated = len(all_paths) > IMPACT_PATH_BUDGET or len(mapping_rows) > IMPACT_PATH_BUDGET * 2
        all_paths = all_paths[:IMPACT_PATH_BUDGET]
        fresh_paths, unverifiable, symlinks = _fresh_context_paths(connection, project, all_paths)
        anchors = [row for row in safe_anchor_candidates if str(row["key"]) in fresh_paths]
        anchor_ids = {str(row["id"]) for row in anchors}
        anchor_paths = {str(row["key"]) for row in anchors}
        if not anchors:
            warnings.append({"code": "IMPACT_FOCUS_STALE", "message": "目标没有可验证的当前文件锚点，物理影响保持为空。", "evidence": []})
        if unverifiable:
            warnings.append({"code": "IMPACT_FRESHNESS_BUDGET", "message": "部分路径超过 freshness I/O 预算，未进入影响解释。", "evidence": []})
        if symlinks:
            warnings.append({"code": "IMPACT_SYMLINK_UNVERIFIABLE", "message": "部分路径包含符号链接，未进入影响解释。", "evidence": []})
        if path_budget_truncated:
            warnings.append({"code": "IMPACT_PATH_BUDGET", "message": f"freshness 候选达到 {IMPACT_PATH_BUDGET} 条路径预算，局部结果可能不完整。", "evidence": []})

        incoming: list[dict[str, Any]] = []
        outgoing: list[dict[str, Any]] = []
        for row in candidate_l1:
            source_path, target_path = str(row["source_path"]), str(row["target_path"])
            if source_path not in fresh_paths or target_path not in fresh_paths:
                continue
            evidence = [_context_evidence(
                kind="physical-relation", summary=f"{source_path} {row['relation']} {target_path}",
                layer="L1", source=str(row["source"]), confidence=float(row["confidence"]),
                path=source_path,
            )]
            relation = _edge(row, evidence)
            if str(row["target_id"]) in anchor_ids:
                peer = {
                    "id": str(row["source_id"]), "kind": "file", "label": str(row["source_label"]),
                    "path": source_path, "layer": "L1", "source": str(row["source_node_source"]),
                    "confidence": float(row["source_node_confidence"]), "evidence": [_file_evidence(source_path)],
                }
                incoming.append({"peer": peer, "relation": relation, "viaPath": target_path, "evidence": evidence})
            if str(row["source_id"]) in anchor_ids:
                peer = {
                    "id": str(row["target_id"]), "kind": "file", "label": str(row["target_label"]),
                    "path": target_path, "layer": "L1", "source": str(row["target_node_source"]),
                    "confidence": float(row["target_node_confidence"]), "evidence": [_file_evidence(target_path)],
                }
                outgoing.append({"peer": peer, "relation": relation, "viaPath": source_path, "evidence": evidence})
        incoming.sort(key=lambda item: (item["peer"]["path"].lower(), item["peer"]["path"], item["relation"]["relation"], item["relation"]["id"]))
        outgoing.sort(key=lambda item: (item["peer"]["path"].lower(), item["peer"]["path"], item["relation"]["relation"], item["relation"]["id"]))
        lane_truncated = len(incoming) > IMPACT_LANE_LIMIT or len(outgoing) > IMPACT_LANE_LIMIT
        incoming, outgoing = incoming[:IMPACT_LANE_LIMIT], outgoing[:IMPACT_LANE_LIMIT]
        if lane_truncated:
            warnings.append({"code": "IMPACT_PHYSICAL_TRUNCATED", "message": f"Incoming/Outgoing 每区最多展示 {IMPACT_LANE_LIMIT} 条真实物理关系。", "evidence": []})

        component_paths: dict[str, set[str]] = defaultdict(set)
        for row in mapping_rows:
            path = str(row["path"])
            if path in fresh_paths:
                component_paths[str(row["concept_id"])].add(path)
        physical_lookup: dict[tuple[str, str, str], sqlite3.Row] = {}
        physical_lookup_truncated = False
        if fresh_paths:
            paths = sorted(fresh_paths, key=lambda p: (p.lower(), p))
            placeholders = ",".join("?" for _ in paths)
            rows = connection.execute(
                f"""SELECT /* impact-physical-lookup */ edge.*, source.key AS source_path, target.key AS target_path
                     FROM edges edge JOIN nodes source ON source.id=edge.source_id
                     JOIN nodes target ON target.id=edge.target_id
                     WHERE edge.project_id=? AND edge.layer=1
                       AND source.layer=1 AND source.kind='file' AND target.layer=1 AND target.kind='file'
                       AND source.key IN ({placeholders}) AND target.key IN ({placeholders})
                     ORDER BY source.key COLLATE NOCASE, source.key, target.key COLLATE NOCASE,
                              target.key, edge.relation, edge.id LIMIT ?""",
                (project_id, *paths, *paths, IMPACT_PHYSICAL_LOOKUP_LIMIT + 1),
            ).fetchall()
            physical_lookup_truncated = len(rows) > IMPACT_PHYSICAL_LOOKUP_LIMIT
            for item in rows[:IMPACT_PHYSICAL_LOOKUP_LIMIT]:
                physical_lookup.setdefault((str(item["source_path"]), str(item["target_path"]), str(item["relation"])), item)
        semantic: list[dict[str, Any]] = []
        semantic_evidence_dropped = False
        semantic_raw_truncated = False
        for row in semantic_rows:
            evidence: list[dict[str, Any]] = []
            if str(row["source"]) == "human-overlay":
                evidence = [_context_evidence(kind="human-decision", summary="人工确认的语义影响关系。", layer="L2", source="human-overlay", confidence=float(row["confidence"]))]
            else:
                raw = json_loads(str(row["data_json"]), {}).get("evidence", [])
                if not isinstance(raw, list):
                    raw = []
                semantic_raw_truncated = semantic_raw_truncated or len(raw) > IMPACT_RAW_EVIDENCE_LIMIT
                for candidate in raw[:IMPACT_RAW_EVIDENCE_LIMIT]:
                    if not isinstance(candidate, dict):
                        continue
                    source_path, target_path, physical_relation = candidate.get("source"), candidate.get("target"), candidate.get("physical_relation")
                    if not all(isinstance(value, str) for value in (source_path, target_path, physical_relation)):
                        continue
                    if source_path not in fresh_paths or target_path not in fresh_paths:
                        continue
                    if (
                        source_path not in component_paths.get(str(row["source_id"]), set())
                        or target_path not in component_paths.get(str(row["target_id"]), set())
                    ):
                        continue
                    physical = physical_lookup.get((source_path, target_path, physical_relation))
                    if physical is None:
                        continue
                    evidence.append(_context_evidence(kind="physical-relation", summary=f"{source_path} {physical_relation} {target_path}", layer="L1", source=str(physical["source"]), confidence=float(physical["confidence"]), path=source_path))
                    if len(evidence) == 3:
                        break
            if not evidence:
                semantic_evidence_dropped = True
                continue
            outgoing_semantic = str(row["source_id"]) in focus_concept_ids
            focus_concept_row = next(
                item for item in focus_concepts
                if str(item["id"]) == str(row["source_id"] if outgoing_semantic else row["target_id"])
            )
            peer_id = str(row["target_id"] if outgoing_semantic else row["source_id"])
            peer = {
                "id": peer_id, "kind": "concept", "label": str(row["target_label"] if outgoing_semantic else row["source_label"]),
                "layer": "L2", "source": str(row["target_node_source"] if outgoing_semantic else row["source_node_source"]),
                "confidence": float(row["target_node_confidence"] if outgoing_semantic else row["source_node_confidence"]), "evidence": evidence,
            }
            semantic.append({
                "direction": "outgoing" if outgoing_semantic else "incoming",
                "focusConcept": _node_entity(focus_concept_row, evidence),
                "peer": peer, "relation": _edge(row, evidence), "evidence": evidence,
            })
        semantic_display_truncated = len(semantic) > IMPACT_SEMANTIC_LIMIT
        semantic = semantic[:IMPACT_SEMANTIC_LIMIT]
        if semantic_display_truncated or len(semantic_rows) > IMPACT_RAW_EVIDENCE_LIMIT:
            warnings.append({"code": "IMPACT_SEMANTIC_TRUNCATED", "message": f"Semantic 最多展示 {IMPACT_SEMANTIC_LIMIT} 条关系。", "evidence": []})
        if semantic_evidence_dropped:
            warnings.append({"code": "IMPACT_SEMANTIC_EVIDENCE_INSUFFICIENT", "message": "部分自动语义关系缺少方向一致的当前物理证据，已跳过。", "evidence": []})
        if semantic_raw_truncated:
            warnings.append({"code": "IMPACT_SEMANTIC_EVIDENCE_TRUNCATED", "message": f"每条语义边最多核验 {IMPACT_RAW_EVIDENCE_LIMIT} 项原始证据。", "evidence": []})
        if physical_lookup_truncated:
            warnings.append({"code": "IMPACT_PHYSICAL_LOOKUP_TRUNCATED", "message": f"语义证据物理边核验达到 {IMPACT_PHYSICAL_LOOKUP_LIMIT} 条预算。", "evidence": []})

        history_target_ids = sorted(anchor_ids | focus_concept_ids)
        history_rows: list[sqlite3.Row] = []
        if history_target_ids:
            placeholders = ",".join("?" for _ in history_target_ids)
            history_rows = connection.execute(
                f"""SELECT edge.*, task_node.label AS task_label, task_node.source AS task_node_source,
                            task_node.confidence AS task_node_confidence, task.title, task.status, edge.rowid AS recorded_order
                     FROM edges edge INDEXED BY idx_edges_target
                     JOIN nodes task_node ON task_node.id=edge.source_id AND task_node.layer=3 AND task_node.kind='task'
                     JOIN tasks task ON task.id=task_node.key
                     WHERE edge.project_id=? AND edge.layer=3 AND edge.source='task-events'
                       AND task_node.source='task-events' AND edge.target_id IN ({placeholders})
                     ORDER BY edge.rowid DESC, edge.id DESC LIMIT ?""",
                (project_id, *history_target_ids, IMPACT_HISTORY_SCAN_LIMIT + 1),
            ).fetchall()
        safe_history = [row for row in history_rows if _safe_task(row)]
        history = []
        for row in safe_history[:IMPACT_HISTORY_LIMIT]:
            evidence = [_context_evidence(kind="task-relation", summary="事件日志重放得到的任务关联。", layer="L3", source=str(row["source"]), confidence=float(row["confidence"]))]
            task_entity = {
                "id": str(row["source_id"]), "kind": "task", "label": str(row["title"]),
                "layer": "L3", "source": str(row["task_node_source"]),
                "confidence": float(row["task_node_confidence"]), "evidence": evidence,
            }
            history.append({"entity": task_entity, "status": str(row["status"]), "relation": _edge(row, evidence), "recordedOrder": int(row["recorded_order"]), "evidence": evidence})
        if len(history_rows) > IMPACT_HISTORY_LIMIT or len(safe_history) != len(history_rows):
            warnings.append({"code": "IMPACT_HISTORY_TRUNCATED", "message": f"History 按关系记录顺序最多扫描 {IMPACT_HISTORY_SCAN_LIMIT + 1} 项、展示 {IMPACT_HISTORY_LIMIT} 项；过滤或预算可能使结果不完整。", "evidence": []})

        # 测试建议只能来自真实 L1 tests 关系或 L2 tested_by 映射。
        test_candidates: list[dict[str, Any]] = []
        for item in incoming + outgoing:
            path = str(item["peer"]["path"])
            if item["relation"]["relation"] in {"tests", "tested_by"} or path.startswith("tests/") or "/test" in path.lower():
                test_candidates.append({"path": path, "reason": "真实物理测试关系指向当前影响范围。", "entity": item["peer"], "relation": item["relation"], "evidence": item["evidence"]})
        test_candidates.sort(key=lambda item: (item["path"].lower(), item["path"], item["relation"]["id"]))
        tests = test_candidates[:IMPACT_TEST_LIMIT]
        if len(test_candidates) > IMPACT_TEST_LIMIT:
            warnings.append({"code": "IMPACT_TESTS_TRUNCATED", "message": f"测试建议最多展示 {IMPACT_TEST_LIMIT} 项。", "evidence": []})

        focus_evidence = (
            [_file_evidence(str(focus["key"]), summary="索引中的仓库文件；freshness 状态见 warnings。")]
            if int(focus["layer"]) == 1 else
            [_context_evidence(kind="semantic-node", summary="索引中的概念节点。", layer="L2", source=str(focus["source"]), confidence=float(focus["confidence"]))]
        )
        focus_entity = _node_entity(focus, focus_evidence)
        anchor_entity = _node_entity(anchors[0], [_file_evidence(str(anchors[0]["key"]))]) if anchors else None
        risks: list[dict[str, Any]] = []
        if incoming:
            risks.append({"kind": "incoming-dependents", "severity": "high" if len(incoming) >= 4 else "medium", "summary": f"当前有 {len(incoming)} 条已展示的入向物理关系，修改可能波及调用方。", "evidence": incoming[0]["evidence"]})
        if outgoing:
            risks.append({"kind": "outgoing-dependencies", "severity": "medium", "summary": f"当前有 {len(outgoing)} 条已展示的出向物理关系，需要核对依赖契约。", "evidence": outgoing[0]["evidence"]})
        if semantic:
            risks.append({"kind": "semantic-coupling", "severity": "medium", "summary": f"当前有 {len(semantic)} 条有证据的语义关联，需要同步核对概念边界。", "evidence": semantic[0]["evidence"]})
        if history:
            risks.append({"kind": "historical-change", "severity": "low", "summary": f"当前有 {len(history)} 条任务关联记录可供回看。", "evidence": history[0]["evidence"]})
        if any(warning["code"].endswith(("TRUNCATED", "BUDGET")) for warning in warnings):
            risks.append({"kind": "bounded-result", "severity": "medium", "summary": "有界查询已触发截断；当前视图不能视为完整影响证明。", "evidence": focus_evidence})

        actions = [
            {"kind": "purpose", "label": "它做什么", "summary": f"聚焦真实{ '文件' if int(focus['layer']) == 1 else '概念'}：{focus['label']}。", "evidence": focus_evidence},
            {"kind": "callers", "label": "谁调用它", "summary": f"当前有界结果展示 {len(incoming)} 条入向物理关系。", "evidence": incoming[0]["evidence"] if incoming else []},
            {"kind": "dependencies", "label": "它依赖谁", "summary": f"当前有界结果展示 {len(outgoing)} 条出向物理关系。", "evidence": outgoing[0]["evidence"] if outgoing else []},
            {"kind": "change", "label": "如果修改它", "summary": f"优先核对 {len(incoming)} 个调用方向、{len(outgoing)} 个依赖方向和 {len(tests)} 个真实测试证据。", "evidence": (incoming + outgoing)[0]["evidence"] if incoming or outgoing else focus_evidence},
            {"kind": "history", "label": "过去谁改过它", "summary": f"当前有界结果展示 {len(history)} 条按关系记录顺序排列的任务关联。", "evidence": history[0]["evidence"] if history else []},
        ]
        stats = {
            "files": connection.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND layer=1 AND kind='file'", (project_id,)).fetchone()[0],
            "concepts": connection.execute("SELECT COUNT(*) FROM nodes WHERE project_id=? AND layer=2 AND kind='concept'", (project_id,)).fetchone()[0],
            "tasks": connection.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (project_id,)).fetchone()[0],
        }

    source_state = _source_state(project)
    if source_state["status"] == "partial":
        warnings.append({"code": "SOURCE_NOT_INDEXED", "message": "项目尚未完成索引，当前影响结果可能不完整。", "evidence": []})
    revision = stable_id(project_id, str(project["last_scan_at"] or ""), str(focus_entity), str(incoming), str(outgoing), str(semantic), str(history), prefix="impact_")
    return {
        "project": {"id": project_id, "name": str(project["name"]), "root": str(project["root"]), "kind": str(project["kind"]), "last_scan_at": project["last_scan_at"]},
        "sourceState": source_state,
        "layout": "incoming-focus-outgoing",
        "revision": revision,
        "focus": {"entity": focus_entity, "anchorFile": anchor_entity, "evidence": focus_evidence},
        "incoming": incoming, "outgoing": outgoing, "semantic": semantic,
        "history": history, "testRecommendations": tests, "risks": risks[:5],
        "actions": actions, "stats": stats, "warnings": warnings,
    }


__all__ = ["impact_view_data"]
