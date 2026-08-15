from __future__ import annotations

import sqlite3
from dataclasses import replace

from .database import Database
from .scanning import ScanReport, scan_physical_layer
from .semantic_formats import apply_extractor_semantics
from .semantic_overlays import apply_semantic_overlays, replay_semantic_overlay_log
from .semantics import build_semantic_layer


def scan_project(database: Database, project: sqlite3.Row, *, full: bool = False) -> ScanReport:
    # 人工校正的权威副本位于外部 JSONL；项目重新关联或数据库重建后，
    # 扫描前先把尚未物化的校正恢复到 SQLite。
    replay_semantic_overlay_log(database, project_id=project["id"], strict=False)
    physical = scan_physical_layer(database, project, full=full)
    build_semantic_layer(database, project)
    # 提取器角色属于可重建的确定性语义提示；人工 Overlay 仍在最后叠加，优先级最高。
    with database.connect() as connection:
        apply_extractor_semantics(connection, database, project)
        apply_semantic_overlays(connection, database, project)
        connection.commit()
        semantic_nodes = connection.execute(
            "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=2",
            (project["id"],),
        ).fetchone()["count"]
        semantic_edges = connection.execute(
            "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=2",
            (project["id"],),
        ).fetchone()["count"]
    return replace(physical, semantic_nodes=semantic_nodes, semantic_edges=semantic_edges)
