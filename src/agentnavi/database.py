from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import Settings
from .utils import json_dumps, stable_id, utc_now

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'generic',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    layer INTEGER NOT NULL CHECK(layer BETWEEN 1 AND 3),
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    label TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'derived',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, layer, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_nodes_project_layer ON nodes(project_id, layer);
CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(project_id, label);
CREATE INDEX IF NOT EXISTS idx_nodes_key ON nodes(project_id, key);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    layer INTEGER NOT NULL CHECK(layer BETWEEN 1 AND 3),
    source_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation TEXT NOT NULL,
    target_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    data_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL DEFAULT 'derived',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, layer, source_id, relation, target_id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(project_id, layer, source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(project_id, layer, target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(project_id, relation);

CREATE TABLE IF NOT EXISTS file_state (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    digest TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, path)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_key TEXT,
    agent TEXT NOT NULL DEFAULT 'manual',
    title TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_created ON tasks(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_key, status);

CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    external_session_id TEXT NOT NULL,
    active_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    session_key TEXT,
    agent TEXT NOT NULL DEFAULT 'manual',
    event_type TEXT NOT NULL,
    tool_name TEXT,
    path TEXT,
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS applied_log_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applied_log_project ON applied_log_events(project_id, applied_at);

CREATE TABLE IF NOT EXISTS applied_overlay_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applied_overlay_project
    ON applied_overlay_events(project_id, applied_at);

CREATE TABLE IF NOT EXISTS semantic_overlays (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT '',
    object_key TEXT NOT NULL DEFAULT '',
    value_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_semantic_overlays_project
    ON semantic_overlays(project_id, enabled, action, subject_key);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    suite TEXT NOT NULL,
    case_key TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    mode TEXT NOT NULL,
    task_text TEXT NOT NULL DEFAULT '',
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    expected_files_json TEXT NOT NULL DEFAULT '[]',
    candidate_files_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    success INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_benchmark_suite
    ON benchmark_runs(project_id, suite, run_kind, mode, created_at DESC);
"""


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def initialize(self) -> None:
        self.settings.ensure_layout()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            row = connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is not None and int(row["value"]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库 schema 版本 {row['value']} 高于当前程序支持的 {SCHEMA_VERSION}"
                )
            connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.settings.home.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.settings.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def node_id(project_id: str, layer: int, kind: str, key: str) -> str:
        return stable_id(project_id, layer, kind, key, prefix="node_")

    @staticmethod
    def edge_id(project_id: str, layer: int, source_id: str, relation: str, target_id: str) -> str:
        return stable_id(project_id, layer, source_id, relation, target_id, prefix="edge_")

    @staticmethod
    def upsert_node(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        layer: int,
        kind: str,
        key: str,
        label: str,
        data: Any | None = None,
        confidence: float = 1.0,
        source: str = "derived",
    ) -> str:
        now = utc_now()
        node_id = Database.node_id(project_id, layer, kind, key)
        connection.execute(
            """
            INSERT INTO nodes(
                id, project_id, layer, kind, key, label, data_json,
                confidence, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, layer, kind, key) DO UPDATE SET
                label=excluded.label,
                data_json=excluded.data_json,
                confidence=excluded.confidence,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                node_id,
                project_id,
                layer,
                kind,
                key,
                label,
                json_dumps(data or {}),
                confidence,
                source,
                now,
                now,
            ),
        )
        return node_id

    @staticmethod
    def upsert_edge(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        layer: int,
        source_id: str,
        relation: str,
        target_id: str,
        data: Any | None = None,
        confidence: float = 1.0,
        source: str = "derived",
    ) -> str:
        now = utc_now()
        edge_id = Database.edge_id(project_id, layer, source_id, relation, target_id)
        connection.execute(
            """
            INSERT INTO edges(
                id, project_id, layer, source_id, relation, target_id,
                data_json, confidence, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, layer, source_id, relation, target_id) DO UPDATE SET
                data_json=excluded.data_json,
                confidence=excluded.confidence,
                source=excluded.source,
                updated_at=excluded.updated_at
            """,
            (
                edge_id,
                project_id,
                layer,
                source_id,
                relation,
                target_id,
                json_dumps(data or {}),
                confidence,
                source,
                now,
                now,
            ),
        )
        return edge_id

    @staticmethod
    def delete_layer(connection: sqlite3.Connection, project_id: str, layer: int) -> None:
        connection.execute("DELETE FROM edges WHERE project_id=? AND layer=?", (project_id, layer))
        connection.execute("DELETE FROM nodes WHERE project_id=? AND layer=?", (project_id, layer))

    @staticmethod
    def reset_layer3(connection: sqlite3.Connection, project_id: str | None = None) -> None:
        """清空可由事件日志重建的 L3 状态，不触碰 L1/L2。"""

        if project_id is None:
            connection.execute("DELETE FROM edges WHERE layer=3")
            connection.execute("DELETE FROM nodes WHERE layer=3")
            connection.execute("DELETE FROM events")
            connection.execute("DELETE FROM sessions")
            connection.execute("DELETE FROM tasks")
            connection.execute("DELETE FROM applied_log_events")
            connection.execute(
                """
                DELETE FROM nodes
                WHERE layer=2 AND kind='concept' AND source='task-event-replay'
                  AND data_json LIKE '%"replay_placeholder":true%'
                  AND NOT EXISTS (SELECT 1 FROM edges WHERE source_id=nodes.id OR target_id=nodes.id)
                """
            )
            return
        connection.execute("DELETE FROM edges WHERE project_id=? AND layer=3", (project_id,))
        connection.execute("DELETE FROM nodes WHERE project_id=? AND layer=3", (project_id,))
        connection.execute("DELETE FROM events WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM sessions WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        connection.execute("DELETE FROM applied_log_events WHERE project_id=?", (project_id,))
        connection.execute(
            """
            DELETE FROM nodes
            WHERE project_id=? AND layer=2 AND kind='concept' AND source='task-event-replay'
              AND data_json LIKE '%"replay_placeholder":true%'
              AND NOT EXISTS (SELECT 1 FROM edges WHERE source_id=nodes.id OR target_id=nodes.id)
            """,
            (project_id,),
        )

    @staticmethod
    def mark_log_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        project_id: str,
        event_type: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO applied_log_events(event_id, project_id, event_type, applied_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (event_id, project_id, event_type, utc_now()),
        )

    @staticmethod
    def log_event_applied(connection: sqlite3.Connection, event_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM applied_log_events WHERE event_id=?", (event_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def mark_overlay_event(
        connection: sqlite3.Connection,
        *,
        event_id: str,
        project_id: str,
        event_type: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO applied_overlay_events(event_id, project_id, event_type, applied_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (event_id, project_id, event_type, utc_now()),
        )

    @staticmethod
    def overlay_event_applied(connection: sqlite3.Connection, event_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM applied_overlay_events WHERE event_id=?", (event_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def delete_nodes(connection: sqlite3.Connection, node_ids: Sequence[str]) -> None:
        if not node_ids:
            return
        placeholders = ",".join("?" for _ in node_ids)
        connection.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", tuple(node_ids))

    @staticmethod
    def fetch_project(connection: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    @staticmethod
    def set_project_scan_time(connection: sqlite3.Connection, project_id: str) -> None:
        now = utc_now()
        connection.execute(
            "UPDATE projects SET last_scan_at=?, updated_at=? WHERE id=?",
            (now, now, project_id),
        )


def ensure_database(settings: Settings) -> Database:
    database = Database(settings)
    database.initialize()
    return database
