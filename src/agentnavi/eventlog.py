from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .database import Database
from .utils import json_loads, stable_id, utc_now

EVENT_LOG_SCHEMA_VERSION = 1
SUPPORTED_EVENT_TYPES = {
    "session_upsert",
    "session_end",
    "task_created",
    "task_event",
    "task_closed",
}


@dataclass(slots=True)
class ReplayReport:
    path: Path
    total: int = 0
    applied: int = 0
    skipped: int = 0
    invalid: int = 0
    projects: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        value["errors"] = self.errors or []
        return value


@dataclass(slots=True)
class BackfillReport:
    path: Path
    projects: int = 0
    events_written: int = 0
    events_skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


@dataclass(slots=True)
class VerifyReport:
    path: Path
    lines: int = 0
    valid: int = 0
    invalid: int = 0
    duplicate_ids: int = 0
    projects: int = 0
    event_types: dict[str, int] | None = None
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        value["event_types"] = self.event_types or {}
        value["errors"] = self.errors or []
        return value


def _row_value(row: Mapping[str, Any] | sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def project_snapshot(project: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(_row_value(project, "id", "")),
        "name": str(_row_value(project, "name", "")),
        "root": str(_row_value(project, "root", "")),
        "kind": str(_row_value(project, "kind", "generic") or "generic"),
        "created_at": str(_row_value(project, "created_at", "") or ""),
    }


def build_envelope(
    *,
    event_type: str,
    project: Mapping[str, Any] | sqlite3.Row,
    payload: Mapping[str, Any] | None = None,
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"不支持的 L3 日志事件：{event_type}")
    snapshot = project_snapshot(project)
    if not snapshot["id"] or not snapshot["root"]:
        raise ValueError("事件日志需要完整的项目 id 和 root")
    return {
        "schema_version": EVENT_LOG_SCHEMA_VERSION,
        "event_id": event_id or f"log_{uuid.uuid4().hex}",
        "event_type": event_type,
        "created_at": created_at or utc_now(),
        "project": snapshot,
        "payload": dict(payload or {}),
    }


def append_event(
    database: Database,
    *,
    event_type: str,
    project: Mapping[str, Any] | sqlite3.Row,
    payload: Mapping[str, Any] | None = None,
    event_id: str | None = None,
    created_at: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """先把事实追加到 JSONL，再由调用方在同一数据库事务中应用。

    若数据库写入在日志落盘后失败，下一次 ``replay l3`` 可以补回该事件。
    """

    envelope = build_envelope(
        event_type=event_type,
        project=project,
        payload=payload,
        event_id=event_id,
        created_at=created_at,
    )
    destination = Path(path or database.settings.event_log_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        if database.settings.event_log_fsync:
            os.fsync(handle.fileno())
    return envelope


def iter_event_log(path: str | Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    source = Path(path).expanduser().resolve()
    if not source.exists():
        return
    with source.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                yield line_number, None, f"JSON 无效：{exc}"
                continue
            if not isinstance(value, dict):
                yield line_number, None, "事件必须是 JSON 对象"
                continue
            yield line_number, value, None


def validate_envelope(envelope: Mapping[str, Any]) -> str | None:
    if int(envelope.get("schema_version", 0)) != EVENT_LOG_SCHEMA_VERSION:
        return f"不支持的 schema_version：{envelope.get('schema_version')}"
    if not str(envelope.get("event_id", "")).strip():
        return "缺少 event_id"
    event_type = str(envelope.get("event_type", ""))
    if event_type not in SUPPORTED_EVENT_TYPES:
        return f"不支持的 event_type：{event_type}"
    project = envelope.get("project")
    if not isinstance(project, dict) or not project.get("id") or not project.get("root"):
        return "缺少项目快照"
    payload = envelope.get("payload", {})
    if not isinstance(payload, dict):
        return "payload 必须是 JSON 对象"
    event_type = str(envelope.get("event_type") or "")
    if event_type in {"session_upsert", "session_end"}:
        if not payload.get("agent") or not payload.get("external_session_id"):
            return f"{event_type} 缺少 agent 或 external_session_id"
    elif event_type == "task_created":
        if not payload.get("task_id"):
            return "task_created 缺少 task_id"
    elif event_type == "task_event":
        if not payload.get("event_type"):
            return "task_event 缺少内部 event_type"
    elif event_type == "task_closed":
        if not payload.get("task_id"):
            return "task_closed 缺少 task_id"
    return None


def verify_event_log(path: str | Path) -> VerifyReport:
    source = Path(path).expanduser().resolve()
    report = VerifyReport(path=source, event_types={}, errors=[])
    seen: set[str] = set()
    projects: set[str] = set()
    for line_number, envelope, parse_error in iter_event_log(source):
        report.lines += 1
        if parse_error or envelope is None:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{parse_error}")
            continue
        error = validate_envelope(envelope)
        if error:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{error}")
            continue
        event_id = str(envelope["event_id"])
        if event_id in seen:
            report.duplicate_ids += 1
        seen.add(event_id)
        project = envelope["project"]
        projects.add(str(project["id"]))
        event_type = str(envelope["event_type"])
        report.event_types[event_type] = report.event_types.get(event_type, 0) + 1
        report.valid += 1
    report.projects = len(projects)
    return report


def _ensure_project_in_connection(
    connection: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    *,
    event_time: str,
) -> sqlite3.Row:
    project_id = str(snapshot["id"])
    root = str(Path(str(snapshot["root"])).expanduser().resolve())
    existing_by_root = connection.execute("SELECT * FROM projects WHERE root=?", (root,)).fetchone()
    if existing_by_root is not None and existing_by_root["id"] != project_id:
        raise RuntimeError(
            f"事件中的项目 id={project_id} 与现有 root={root} 的 id={existing_by_root['id']} 冲突"
        )
    created_at = str(snapshot.get("created_at") or event_time)
    connection.execute(
        """
        INSERT INTO projects(id, name, root, kind, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            root=excluded.root,
            kind=excluded.kind,
            updated_at=excluded.updated_at
        """,
        (
            project_id,
            str(snapshot.get("name") or Path(root).name),
            root,
            str(snapshot.get("kind") or "generic"),
            created_at,
            event_time,
        ),
    )
    row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    assert row is not None
    return row


def _apply_envelope(connection: sqlite3.Connection, database: Database, envelope: Mapping[str, Any]) -> None:
    # 延迟导入避免 tasks -> eventlog -> tasks 的模块循环。
    from .tasks import (
        _close_task_in_connection,
        _create_task_in_connection,
        _end_session_in_connection,
        _record_event_in_connection,
        _upsert_session_in_connection,
    )

    event_id = str(envelope["event_id"])
    if Database.log_event_applied(connection, event_id):
        return
    event_type = str(envelope["event_type"])
    created_at = str(envelope.get("created_at") or utc_now())
    project = _ensure_project_in_connection(connection, envelope["project"], event_time=created_at)
    payload = envelope.get("payload", {})
    project_id = project["id"]

    if event_type == "session_upsert":
        _upsert_session_in_connection(
            connection,
            project_id=project_id,
            agent=str(payload.get("agent") or "manual"),
            external_session_id=str(payload.get("external_session_id") or "unknown-session"),
            now=created_at,
        )
    elif event_type == "session_end":
        _end_session_in_connection(
            connection,
            agent=str(payload.get("agent") or "manual"),
            external_session_id=str(payload.get("external_session_id") or "unknown-session"),
            now=created_at,
            active_task_id=str(payload.get("active_task_id") or "") or None,
            active_summary=str(payload.get("active_summary") or ""),
            affected_concepts=[
                item for item in payload.get("affected_concepts", []) if isinstance(item, dict)
            ],
        )
    elif event_type == "task_created":
        _create_task_in_connection(
            connection,
            project_id=project_id,
            task_id=str(payload["task_id"]),
            title=str(payload.get("title") or "未命名任务"),
            prompt=str(payload.get("prompt") or ""),
            agent=str(payload.get("agent") or "manual"),
            external_session_id=(
                str(payload.get("external_session_id"))
                if payload.get("external_session_id") is not None
                else None
            ),
            now=created_at,
        )
    elif event_type == "task_event":
        _record_event_in_connection(
            connection,
            project=project,
            agent=str(payload.get("agent") or "manual"),
            event_type=str(payload.get("event_type") or "note"),
            external_session_id=(
                str(payload.get("external_session_id"))
                if payload.get("external_session_id") is not None
                else None
            ),
            task_id=str(payload.get("task_id") or "") or None,
            tool_name=str(payload.get("tool_name") or "") or None,
            normalized_paths=[str(item) for item in payload.get("paths", [])],
            data=payload.get("data", {}),
            created_at=created_at,
        )
    elif event_type == "task_closed":
        _close_task_in_connection(
            connection,
            task_id=str(payload["task_id"]),
            status=str(payload.get("status") or "completed"),
            summary=str(payload.get("summary") or ""),
            now=created_at,
            affected_concepts=[
                item for item in payload.get("affected_concepts", []) if isinstance(item, dict)
            ],
        )
    else:  # pragma: no cover - validate_envelope 已拦截
        raise ValueError(f"不支持的事件：{event_type}")

    Database.mark_log_event(
        connection,
        event_id=event_id,
        project_id=project_id,
        event_type=event_type,
    )


def replay_event_log(
    database: Database,
    *,
    path: str | Path | None = None,
    reset: bool = False,
    project_id: str | None = None,
    strict: bool = False,
) -> ReplayReport:
    source = Path(path or database.settings.event_log_path).expanduser().resolve()
    report = ReplayReport(path=source, errors=[])
    if not source.exists():
        raise FileNotFoundError(f"事件日志不存在：{source}")

    if reset:
        with database.connect() as connection:
            Database.reset_layer3(connection, project_id=project_id)
            connection.commit()

    seen_projects: set[str] = set()
    for line_number, envelope, parse_error in iter_event_log(source):
        report.total += 1
        if parse_error or envelope is None:
            report.invalid += 1
            message = f"第 {line_number} 行：{parse_error}"
            report.errors.append(message)
            if strict:
                raise RuntimeError(message)
            continue
        error = validate_envelope(envelope)
        if error:
            report.invalid += 1
            message = f"第 {line_number} 行：{error}"
            report.errors.append(message)
            if strict:
                raise RuntimeError(message)
            continue
        snapshot = envelope["project"]
        envelope_project_id = str(snapshot["id"])
        if project_id and envelope_project_id != project_id:
            report.skipped += 1
            continue
        seen_projects.add(envelope_project_id)
        event_id = str(envelope["event_id"])
        try:
            with database.connect() as connection:
                if Database.log_event_applied(connection, event_id):
                    report.skipped += 1
                    continue
                _apply_envelope(connection, database, envelope)
                connection.commit()
            report.applied += 1
        except (KeyError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
            report.invalid += 1
            message = f"第 {line_number} 行（{event_id}）：{exc}"
            report.errors.append(message)
            if strict:
                raise RuntimeError(message) from exc
    report.projects = len(seen_projects)
    return report


def _affected_concepts_for_task(connection: sqlite3.Connection, project_id: str, task_id: str) -> list[dict[str, str]]:
    task_node = Database.node_id(project_id, 3, "task", task_id)
    return [
        {"key": row["key"], "label": row["label"]}
        for row in connection.execute(
            """
            SELECT DISTINCT n.key, n.label
            FROM edges e JOIN nodes n ON n.id=e.target_id
            WHERE e.project_id=? AND e.layer=3 AND e.source_id=?
              AND e.relation='affects' AND n.layer=2 AND n.kind='concept'
            ORDER BY n.key
            """,
            (project_id, task_node),
        )
    ]


def _existing_log_state(path: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    event_ids: set[str] = set()
    task_ids: set[str] = set()
    session_keys: set[str] = set()
    ended_session_keys: set[str] = set()
    if not path.exists():
        return event_ids, task_ids, session_keys, ended_session_keys
    for _, envelope, error in iter_event_log(path):
        if error is not None or not envelope:
            continue
        if envelope.get("event_id"):
            event_ids.add(str(envelope["event_id"]))
        payload = envelope.get("payload", {})
        if envelope.get("event_type") == "task_created" and payload.get("task_id"):
            task_ids.add(str(payload["task_id"]))
        if envelope.get("event_type") in {"session_upsert", "session_end"}:
            agent = str(payload.get("agent") or "manual")
            external = str(payload.get("external_session_id") or "unknown-session")
            key = f"{agent}:{external}"
            if envelope.get("event_type") == "session_upsert":
                session_keys.add(key)
            else:
                ended_session_keys.add(key)
    return event_ids, task_ids, session_keys, ended_session_keys


def _append_backfill_envelope(
    database: Database,
    *,
    project: sqlite3.Row,
    event_type: str,
    payload: Mapping[str, Any],
    event_id: str,
    created_at: str,
    existing_ids: set[str],
) -> bool:
    if event_id in existing_ids:
        with database.connect() as connection:
            Database.mark_log_event(
                connection,
                event_id=event_id,
                project_id=project["id"],
                event_type=event_type,
            )
            connection.commit()
        return False
    append_event(
        database,
        event_type=event_type,
        project=project,
        payload=payload,
        event_id=event_id,
        created_at=created_at,
    )
    existing_ids.add(event_id)
    with database.connect() as connection:
        Database.mark_log_event(
            connection,
            event_id=event_id,
            project_id=project["id"],
            event_type=event_type,
        )
        connection.commit()
    return True


def backfill_event_log(database: Database, *, project_id: str | None = None) -> BackfillReport:
    """把升级前已存在于 SQLite 的 L3 历史补写为可重放 JSONL。"""

    destination = database.settings.event_log_path
    (
        existing_ids,
        logged_task_ids,
        logged_session_keys,
        logged_ended_session_keys,
    ) = _existing_log_state(destination)
    report = BackfillReport(path=destination)
    with database.connect() as connection:
        if project_id:
            projects = list(connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)))
        else:
            projects = list(connection.execute("SELECT * FROM projects ORDER BY id"))

    for project in projects:
        report.projects += 1
        with database.connect() as connection:
            sessions = list(
                connection.execute(
                    "SELECT * FROM sessions WHERE project_id=? ORDER BY created_at, session_key",
                    (project["id"],),
                )
            )
            tasks = list(
                connection.execute(
                    "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at, id",
                    (project["id"],),
                )
            )
        for session in sessions:
            if session["session_key"] in logged_session_keys:
                report.events_skipped += 1
                continue
            event_id = stable_id("backfill", "session", session["session_key"], prefix="log_")
            written = _append_backfill_envelope(
                database,
                project=project,
                event_type="session_upsert",
                payload={
                    "agent": session["agent"],
                    "external_session_id": session["external_session_id"],
                },
                event_id=event_id,
                created_at=session["created_at"],
                existing_ids=existing_ids,
            )
            report.events_written += int(written)
            report.events_skipped += int(not written)

        for task in tasks:
            if task["id"] in logged_task_ids:
                report.events_skipped += 1
                continue
            external_session_id: str | None = None
            if task["session_key"]:
                with database.connect() as connection:
                    session = connection.execute(
                        "SELECT external_session_id FROM sessions WHERE session_key=?",
                        (task["session_key"],),
                    ).fetchone()
                external_session_id = session["external_session_id"] if session else None
            created_id = stable_id("backfill", "task-created", task["id"], prefix="log_")
            written = _append_backfill_envelope(
                database,
                project=project,
                event_type="task_created",
                payload={
                    "task_id": task["id"],
                    "title": task["title"],
                    "prompt": task["prompt"],
                    "agent": task["agent"],
                    "external_session_id": external_session_id,
                },
                event_id=created_id,
                created_at=task["created_at"],
                existing_ids=existing_ids,
            )
            report.events_written += int(written)
            report.events_skipped += int(not written)

            with database.connect() as connection:
                task_events = list(
                    connection.execute(
                        "SELECT * FROM events WHERE task_id=? ORDER BY id", (task["id"],)
                    )
                )
            for event in task_events:
                event_id = stable_id("backfill", "task-event", task["id"], event["id"], prefix="log_")
                payload = {
                    "task_id": task["id"],
                    "agent": event["agent"],
                    "external_session_id": external_session_id,
                    "event_type": event["event_type"],
                    "tool_name": event["tool_name"],
                    "paths": [event["path"]] if event["path"] else [],
                    "data": json_loads(event["data_json"], {}),
                }
                written = _append_backfill_envelope(
                    database,
                    project=project,
                    event_type="task_event",
                    payload=payload,
                    event_id=event_id,
                    created_at=event["created_at"],
                    existing_ids=existing_ids,
                )
                report.events_written += int(written)
                report.events_skipped += int(not written)

            if task["status"] != "running" or task["closed_at"]:
                with database.connect() as connection:
                    affected = _affected_concepts_for_task(
                        connection, project["id"], task["id"]
                    )
                closed_id = stable_id("backfill", "task-closed", task["id"], prefix="log_")
                written = _append_backfill_envelope(
                    database,
                    project=project,
                    event_type="task_closed",
                    payload={
                        "task_id": task["id"],
                        "status": task["status"],
                        "summary": task["summary"],
                        "affected_concepts": affected,
                    },
                    event_id=closed_id,
                    created_at=task["closed_at"] or task["updated_at"],
                    existing_ids=existing_ids,
                )
                report.events_written += int(written)
                report.events_skipped += int(not written)

        # 会话结束状态必须在任务事件之后重放，否则 active_task 尚未建立。
        for session in sessions:
            if not session["ended_at"]:
                continue
            if session["session_key"] in logged_ended_session_keys:
                report.events_skipped += 1
                continue
            event_id = stable_id("backfill", "session-end", session["session_key"], prefix="log_")
            written = _append_backfill_envelope(
                database,
                project=project,
                event_type="session_end",
                payload={
                    "agent": session["agent"],
                    "external_session_id": session["external_session_id"],
                    "active_task_id": None,
                    "active_summary": "",
                    "affected_concepts": [],
                },
                event_id=event_id,
                created_at=session["ended_at"],
                existing_ids=existing_ids,
            )
            report.events_written += int(written)
            report.events_skipped += int(not written)
    return report
