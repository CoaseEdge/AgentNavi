from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .database import Database
from .eventlog import append_event
from .utils import humanize_identifier, json_loads, path_is_within, utc_now

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def session_key(agent: str, external_session_id: str) -> str:
    return f"{agent}:{external_session_id}"


def _project_row(database: Database, project_id: str) -> sqlite3.Row:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise LookupError(f"找不到项目：{project_id}")
    return row


def _upsert_session_in_connection(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    agent: str,
    external_session_id: str,
    now: str,
) -> sqlite3.Row:
    key = session_key(agent, external_session_id)
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
    row = connection.execute("SELECT * FROM sessions WHERE session_key=?", (key,)).fetchone()
    assert row is not None
    return row


def upsert_session(
    database: Database,
    *,
    project_id: str,
    agent: str,
    external_session_id: str,
) -> sqlite3.Row:
    project = _project_row(database, project_id)
    envelope = append_event(
        database,
        event_type="session_upsert",
        project=project,
        payload={"agent": agent, "external_session_id": external_session_id},
    )
    with database.connect() as connection:
        row = _upsert_session_in_connection(
            connection,
            project_id=project_id,
            agent=agent,
            external_session_id=external_session_id,
            now=envelope["created_at"],
        )
        Database.mark_log_event(
            connection,
            event_id=envelope["event_id"],
            project_id=project_id,
            event_type=envelope["event_type"],
        )
        connection.commit()
        return row


def _task_concept_snapshot_in_connection(
    connection: sqlite3.Connection,
    project_id: str,
    task_id: str,
) -> list[dict[str, str]]:
    task_node = Database.node_id(project_id, 3, "task", task_id)
    file_nodes = [
        row["target_id"]
        for row in connection.execute(
            """
            SELECT target_id FROM edges
            WHERE project_id=? AND layer=3 AND source_id=?
              AND relation IN ('modified', 'tested', 'read')
            """,
            (project_id, task_node),
        )
    ]
    if not file_nodes:
        return []
    placeholders = ",".join("?" for _ in file_nodes)
    return [
        {"key": row["key"], "label": row["label"]}
        for row in connection.execute(
            f"""
            SELECT DISTINCT n.key, n.label
            FROM edges e JOIN nodes n ON n.id=e.source_id
            WHERE e.project_id=? AND e.layer=2
              AND e.relation IN ('implemented_by', 'tested_by', 'documented_by', 'configured_by')
              AND e.target_id IN ({placeholders})
              AND n.kind='concept'
            ORDER BY n.key
            """,
            (project_id, *file_nodes),
        )
    ]


def _end_session_in_connection(
    connection: sqlite3.Connection,
    *,
    agent: str,
    external_session_id: str,
    now: str,
    active_task_id: str | None = None,
    active_summary: str = "",
    affected_concepts: list[dict[str, str]] | None = None,
) -> None:
    key = session_key(agent, external_session_id)
    active = None
    if active_task_id:
        active = connection.execute("SELECT * FROM tasks WHERE id=?", (active_task_id,)).fetchone()
    if active is None:
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
            summary=active_summary or active["summary"],
            now=now,
            affected_concepts=affected_concepts,
        )
    connection.execute(
        "UPDATE sessions SET active_task_id=NULL, ended_at=?, updated_at=? WHERE session_key=?",
        (now, now, key),
    )


def end_session(database: Database, *, agent: str, external_session_id: str) -> None:
    key = session_key(agent, external_session_id)
    with database.connect() as connection:
        session = connection.execute(
            """
            SELECT s.*, p.name AS project_name, p.root AS project_root,
                   p.kind AS project_kind, p.created_at AS project_created_at
            FROM sessions s JOIN projects p ON p.id=s.project_id
            WHERE s.session_key=?
            """,
            (key,),
        ).fetchone()
        if session is None:
            return
        active = connection.execute(
            "SELECT * FROM tasks WHERE id=?", (session["active_task_id"],)
        ).fetchone() if session["active_task_id"] else None
        affected = (
            _task_concept_snapshot_in_connection(connection, session["project_id"], active["id"])
            if active
            else []
        )
    project = {
        "id": session["project_id"],
        "name": session["project_name"],
        "root": session["project_root"],
        "kind": session["project_kind"],
        "created_at": session["project_created_at"],
    }
    envelope = append_event(
        database,
        event_type="session_end",
        project=project,
        payload={
            "agent": agent,
            "external_session_id": external_session_id,
            "active_task_id": active["id"] if active else None,
            "active_summary": active["summary"] if active else "",
            "affected_concepts": affected,
        },
    )
    with database.connect() as connection:
        _end_session_in_connection(
            connection,
            agent=agent,
            external_session_id=external_session_id,
            now=envelope["created_at"],
            active_task_id=active["id"] if active else None,
            active_summary=active["summary"] if active else "",
            affected_concepts=affected,
        )
        Database.mark_log_event(
            connection,
            event_id=envelope["event_id"],
            project_id=session["project_id"],
            event_type=envelope["event_type"],
        )
        connection.commit()


def _create_task_in_connection(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    title: str,
    prompt: str,
    agent: str,
    external_session_id: str | None,
    now: str,
) -> sqlite3.Row:
    normalized_title = " ".join(title.strip().split())[:160] or "未命名任务"
    key = session_key(agent, external_session_id) if external_session_id else None
    connection.execute(
        """
        INSERT INTO tasks(
            id, project_id, session_key, agent, title, prompt,
            status, summary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', '', ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (task_id, project_id, key, agent, normalized_title, prompt, now, now),
    )
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert task is not None
    Database.upsert_node(
        connection,
        project_id=project_id,
        layer=3,
        kind="task",
        key=task_id,
        label=task["title"],
        data={
            "prompt": task["prompt"],
            "status": task["status"],
            "summary": task["summary"],
            "agent": task["agent"],
            "session_key": task["session_key"],
            "created_at": task["created_at"],
            "closed_at": task["closed_at"],
        },
        source="task-events",
    )
    if key and task["status"] == "running":
        connection.execute(
            "UPDATE sessions SET active_task_id=?, updated_at=? WHERE session_key=?",
            (task_id, now, key),
        )
    return task


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
    normalized_title = " ".join(title.strip().split())[:160] or "未命名任务"
    project = _project_row(database, project_id)
    envelope = append_event(
        database,
        event_type="task_created",
        project=project,
        payload={
            "task_id": task_id,
            "title": normalized_title,
            "prompt": prompt,
            "agent": agent,
            "external_session_id": external_session_id,
        },
    )
    with database.connect() as connection:
        row = _create_task_in_connection(
            connection,
            project_id=project_id,
            task_id=task_id,
            title=normalized_title,
            prompt=prompt,
            agent=agent,
            external_session_id=external_session_id,
            now=envelope["created_at"],
        )
        Database.mark_log_event(
            connection,
            event_id=envelope["event_id"],
            project_id=project_id,
            event_type=envelope["event_type"],
        )
        connection.commit()
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


def _normalize_event_paths(paths: Iterable[str], project_root: Path) -> list[str]:
    return list(
        dict.fromkeys(
            normalized
            for path in paths
            if (normalized := _normalize_event_path(path, project_root)) is not None
        )
    )


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
    now: str,
) -> None:
    edge_id = Database.edge_id(project_id, 3, task_node, relation, file_node)
    existing = connection.execute("SELECT data_json FROM edges WHERE id=?", (edge_id,)).fetchone()
    data = json_loads(existing["data_json"], {}) if existing else {}
    data["path"] = path
    data["count"] = int(data.get("count", 0)) + 1
    data["last_seen_at"] = now
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


def _record_event_in_connection(
    connection: sqlite3.Connection,
    *,
    project: Mapping[str, Any] | sqlite3.Row,
    agent: str,
    event_type: str,
    external_session_id: str | None = None,
    task_id: str | None = None,
    tool_name: str | None = None,
    normalized_paths: Iterable[str] = (),
    data: Any | None = None,
    created_at: str,
) -> None:
    project_id = str(project["id"])
    project_root = Path(str(project["root"])).resolve()
    key = session_key(agent, external_session_id) if external_session_id else None
    resolved_task_id = task_id
    if resolved_task_id is None and key:
        row = connection.execute(
            "SELECT active_task_id FROM sessions WHERE session_key=?", (key,)
        ).fetchone()
        resolved_task_id = row["active_task_id"] if row else None

    paths = list(dict.fromkeys(str(path) for path in normalized_paths if str(path).strip()))
    path_values: list[str | None] = paths or [None]
    for path in path_values:
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
                created_at,
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
                    now=created_at,
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
    project_root = Path(project["root"]).resolve()
    normalized_paths = _normalize_event_paths(paths, project_root)
    envelope = append_event(
        database,
        event_type="task_event",
        project=project,
        payload={
            "task_id": task_id,
            "agent": agent,
            "external_session_id": external_session_id,
            "event_type": event_type,
            "tool_name": tool_name,
            "paths": normalized_paths,
            "data": data or {},
        },
    )
    with database.connect() as connection:
        _record_event_in_connection(
            connection,
            project=project,
            agent=agent,
            event_type=event_type,
            external_session_id=external_session_id,
            task_id=task_id,
            tool_name=tool_name,
            normalized_paths=normalized_paths,
            data=data,
            created_at=envelope["created_at"],
        )
        Database.mark_log_event(
            connection,
            event_id=envelope["event_id"],
            project_id=project["id"],
            event_type=envelope["event_type"],
        )
        connection.commit()


def _ensure_concept_snapshot_node(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    concept: Mapping[str, Any],
) -> str:
    key = str(concept.get("key") or "").strip()
    if not key:
        raise ValueError("受影响概念缺少 key")
    node_id = Database.node_id(project_id, 2, "concept", key)
    if connection.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
        return node_id
    return Database.upsert_node(
        connection,
        project_id=project_id,
        layer=2,
        kind="concept",
        key=key,
        label=str(concept.get("label") or humanize_identifier(key)),
        data={"active": True, "replay_placeholder": True},
        confidence=0.5,
        source="task-event-replay",
    )


def _link_task_concepts(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    task_id: str,
    concepts: Iterable[Mapping[str, Any]],
) -> None:
    task_node = Database.node_id(project_id, 3, "task", task_id)
    for concept in concepts:
        concept_id = _ensure_concept_snapshot_node(
            connection,
            project_id=project_id,
            concept=concept,
        )
        Database.upsert_edge(
            connection,
            project_id=project_id,
            layer=3,
            source_id=task_node,
            relation="affects",
            target_id=concept_id,
            source="task-events",
        )


def _derive_task_concepts(connection: sqlite3.Connection, project_id: str, task_id: str) -> None:
    concepts = _task_concept_snapshot_in_connection(connection, project_id, task_id)
    _link_task_concepts(
        connection,
        project_id=project_id,
        task_id=task_id,
        concepts=concepts,
    )


def _close_task_in_connection(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    status: str,
    summary: str,
    now: str | None = None,
    affected_concepts: list[dict[str, str]] | None = None,
) -> sqlite3.Row:
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        raise LookupError(f"找不到任务：{task_id}")
    timestamp = now or utc_now()
    closed_at = timestamp if status in TERMINAL_STATUSES else None
    connection.execute(
        """
        UPDATE tasks
        SET status=?, summary=?, updated_at=?, closed_at=?
        WHERE id=?
        """,
        (status, summary, timestamp, closed_at, task_id),
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
    if affected_concepts is None:
        _derive_task_concepts(connection, task["project_id"], task_id)
    else:
        _link_task_concepts(
            connection,
            project_id=task["project_id"],
            task_id=task_id,
            concepts=affected_concepts,
        )
    if task["session_key"]:
        connection.execute(
            "UPDATE sessions SET active_task_id=NULL, updated_at=? WHERE session_key=? AND active_task_id=?",
            (timestamp, task["session_key"], task_id),
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
        task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise LookupError(f"找不到任务：{task_id}")
        project = connection.execute(
            "SELECT * FROM projects WHERE id=?", (task["project_id"],)
        ).fetchone()
        assert project is not None
        affected = _task_concept_snapshot_in_connection(
            connection, task["project_id"], task_id
        )
    envelope = append_event(
        database,
        event_type="task_closed",
        project=project,
        payload={
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "affected_concepts": affected,
        },
    )
    with database.connect() as connection:
        row = _close_task_in_connection(
            connection,
            task_id=task_id,
            status=status,
            summary=summary,
            now=envelope["created_at"],
            affected_concepts=affected,
        )
        Database.mark_log_event(
            connection,
            event_id=envelope["event_id"],
            project_id=task["project_id"],
            event_type=envelope["event_type"],
        )
        connection.commit()
        return row


def get_task(database: Database, task_id: str) -> sqlite3.Row:
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise LookupError(f"找不到任务：{task_id}")
        return row
