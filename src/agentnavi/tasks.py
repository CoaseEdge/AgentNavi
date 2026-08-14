from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable

from .database import Database
from .utils import json_loads, path_is_within, utc_now

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def session_key(agent: str, external_session_id: str) -> str:
    return f"{agent}:{external_session_id}"


def upsert_session(
    database: Database,
    *,
    project_id: str,
    agent: str,
    external_session_id: str,
) -> sqlite3.Row:
    key = session_key(agent, external_session_id)
    now = utc_now()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                session_key, project_id, agent, external_session_id,
                created_at, updated_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(session_key) DO UPDATE SET
                project_id=excluded.project_id,
                agent=excluded.agent,
                external_session_id=excluded.external_session_id,
                updated_at=excluded.updated_at,
                ended_at=NULL
            """,
            (key, project_id, agent, external_session_id, now, now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM sessions WHERE session_key=?", (key,)).fetchone()
        assert row is not None
        return row


def end_session(database: Database, *, agent: str, external_session_id: str) -> None:
    key = session_key(agent, external_session_id)
    now = utc_now()
    with database.connect() as connection:
        active = connection.execute(
            """
            SELECT t.* FROM sessions s
            JOIN tasks t ON t.id=s.active_task_id
            WHERE s.session_key=?
            """,
            (key,),
        ).fetchone()
        if active and active["status"] not in TERMINAL_STATUSES:
            _close_task_in_connection(
                connection,
                task_id=active["id"],
                status="interrupted",
                summary=active["summary"],
            )
        connection.execute(
            "UPDATE sessions SET active_task_id=NULL, ended_at=?, updated_at=? WHERE session_key=?",
            (now, now, key),
        )
        connection.commit()


def create_task(
    database: Database,
    *,
    project_id: str,
    title: str,
    prompt: str = "",
    agent: str = "manual",
    external_session_id: str | None = None,
) -> sqlite3.Row:
    task_id = f"task_{uuid.uuid4().hex}"
    now = utc_now()
    normalized_title = " ".join(title.strip().split())[:160] or "未命名任务"
    key = session_key(agent, external_session_id) if external_session_id else None
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO tasks(
                id, project_id, session_key, agent, title, prompt,
                status, summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', '', ?, ?)
            """,
            (task_id, project_id, key, agent, normalized_title, prompt, now, now),
        )
        Database.upsert_node(
            connection,
            project_id=project_id,
            layer=3,
            kind="task",
            key=task_id,
            label=normalized_title,
            data={
                "prompt": prompt,
                "status": "running",
                "agent": agent,
                "session_key": key,
                "created_at": now,
            },
            source="task-events",
        )
        if key:
            connection.execute(
                "UPDATE sessions SET active_task_id=?, updated_at=? WHERE session_key=?",
                (task_id, now, key),
            )
        connection.commit()
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert row is not None
        return row


def active_task(
    database: Database,
    *,
    agent: str,
    external_session_id: str,
) -> sqlite3.Row | None:
    key = session_key(agent, external_session_id)
    with database.connect() as connection:
        return connection.execute(
            """
            SELECT t.* FROM sessions s
            JOIN tasks t ON t.id=s.active_task_id
            WHERE s.session_key=?
            """,
            (key,),
        ).fetchone()


def list_tasks(database: Database, project_id: str, *, limit: int = 20) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return list(
            connection.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        )


def _normalize_event_path(path: str, project_root: Path) -> str | None:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not path_is_within(resolved, project_root):
        return None
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return None


def _ensure_file_node(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    project_root: Path,
    relative_path: str,
) -> str:
    node_id = Database.node_id(project_id, 1, "file", relative_path)
    if connection.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
        return node_id
    absolute = project_root / relative_path
    data: dict[str, Any] = {"path": relative_path, "placeholder": True}
    if absolute.exists() and absolute.is_file():
        try:
            stat = absolute.stat()
            data.update({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        except OSError:
            pass
    return Database.upsert_node(
        connection,
        project_id=project_id,
        layer=1,
        kind="file",
        key=relative_path,
        label=relative_path,
        data=data,
        confidence=0.5,
        source="task-event-placeholder",
    )


def _upsert_task_file_edge(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_node: str,
    relation: str,
    file_node: str,
    path: str,
) -> None:
    edge_id = Database.edge_id(project_id, 3, task_node, relation, file_node)
    existing = connection.execute("SELECT data_json FROM edges WHERE id=?", (edge_id,)).fetchone()
    data = json_loads(existing["data_json"], {}) if existing else {}
    data["path"] = path
    data["count"] = int(data.get("count", 0)) + 1
    data["last_seen_at"] = utc_now()
    Database.upsert_edge(
        connection,
        project_id=project_id,
        layer=3,
        source_id=task_node,
        relation=relation,
        target_id=file_node,
        data=data,
        source="task-events",
    )


def record_event(
    database: Database,
    *,
    project: sqlite3.Row,
    agent: str,
    event_type: str,
    external_session_id: str | None = None,
    task_id: str | None = None,
    tool_name: str | None = None,
    paths: Iterable[str] = (),
    data: Any | None = None,
) -> None:
    project_id = project["id"]
    project_root = Path(project["root"]).resolve()
    key = session_key(agent, external_session_id) if external_session_id else None
    with database.connect() as connection:
        resolved_task_id = task_id
        if resolved_task_id is None and key:
            row = connection.execute(
                "SELECT active_task_id FROM sessions WHERE session_key=?", (key,)
            ).fetchone()
            resolved_task_id = row["active_task_id"] if row else None

        normalized_paths = [
            normalized
            for path in paths
            if (normalized := _normalize_event_path(path, project_root)) is not None
        ]
        if not normalized_paths:
            normalized_paths = [None]

        for path in dict.fromkeys(normalized_paths):
            connection.execute(
                """
                INSERT INTO events(
                    project_id, task_id, session_key, agent, event_type,
                    tool_name, path, data_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    resolved_task_id,
                    key,
                    agent,
                    event_type,
                    tool_name,
                    path,
                    json.dumps(data or {}, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            if resolved_task_id and path:
                task_node = Database.node_id(project_id, 3, "task", resolved_task_id)
                file_node = _ensure_file_node(
                    connection,
                    project_id=project_id,
                    project_root=project_root,
                    relative_path=path,
                )
                relation = {
                    "read": "read",
                    "modify": "modified",
                    "write": "modified",
                    "test": "tested",
                    "search": "searched",
                }.get(event_type)
                if relation:
                    _upsert_task_file_edge(
                        connection,
                        project_id=project_id,
                        task_node=task_node,
                        relation=relation,
                        file_node=file_node,
                        path=path,
                    )
        connection.commit()


def _derive_task_concepts(connection: sqlite3.Connection, project_id: str, task_id: str) -> None:
    task_node = Database.node_id(project_id, 3, "task", task_id)
    file_nodes = [
        row["target_id"]
        for row in connection.execute(
            """
            SELECT target_id FROM edges
            WHERE project_id=? AND layer=3 AND source_id=? AND relation IN ('modified', 'tested', 'read')
            """,
            (project_id, task_node),
        )
    ]
    if not file_nodes:
        return
    placeholders = ",".join("?" for _ in file_nodes)
    concept_rows = connection.execute(
        f"""
        SELECT DISTINCT source_id
        FROM edges
        WHERE project_id=? AND layer=2
          AND relation IN ('implemented_by', 'tested_by', 'documented_by', 'configured_by')
          AND target_id IN ({placeholders})
        """,
        (project_id, *file_nodes),
    )
    for row in concept_rows:
        Database.upsert_edge(
            connection,
            project_id=project_id,
            layer=3,
            source_id=task_node,
            relation="affects",
            target_id=row["source_id"],
            source="task-events",
        )


def _close_task_in_connection(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    status: str,
    summary: str,
) -> sqlite3.Row:
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        raise LookupError(f"找不到任务：{task_id}")
    now = utc_now()
    closed_at = now if status in TERMINAL_STATUSES else None
    connection.execute(
        """
        UPDATE tasks
        SET status=?, summary=?, updated_at=?, closed_at=?
        WHERE id=?
        """,
        (status, summary, now, closed_at, task_id),
    )
    Database.upsert_node(
        connection,
        project_id=task["project_id"],
        layer=3,
        kind="task",
        key=task_id,
        label=task["title"],
        data={
            "prompt": task["prompt"],
            "status": status,
            "summary": summary,
            "agent": task["agent"],
            "session_key": task["session_key"],
            "created_at": task["created_at"],
            "closed_at": closed_at,
        },
        source="task-events",
    )
    _derive_task_concepts(connection, task["project_id"], task_id)
    if task["session_key"]:
        connection.execute(
            "UPDATE sessions SET active_task_id=NULL, updated_at=? WHERE session_key=? AND active_task_id=?",
            (now, task["session_key"], task_id),
        )
    row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row is not None
    return row


def close_task(
    database: Database,
    task_id: str,
    *,
    status: str = "completed",
    summary: str = "",
) -> sqlite3.Row:
    if status not in {*TERMINAL_STATUSES, "running"}:
        raise ValueError(f"不支持的任务状态：{status}")
    with database.connect() as connection:
        row = _close_task_in_connection(connection, task_id=task_id, status=status, summary=summary)
        connection.commit()
        return row


def get_task(database: Database, task_id: str) -> sqlite3.Row:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise LookupError(f"找不到任务：{task_id}")
        return row
