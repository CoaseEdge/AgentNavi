from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping

from .database import Database
from .utils import humanize_identifier, json_dumps, json_loads, slugify, stable_id, utc_now


OVERLAY_LOG_SCHEMA_VERSION = 1
OVERLAY_EVENT_TYPES = {"correction_upsert", "correction_remove"}


@dataclass(slots=True)
class OverlayReplayReport:
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
class OverlayVerifyReport:
    path: Path
    lines: int = 0
    valid: int = 0
    invalid: int = 0
    duplicate_ids: int = 0
    projects: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        value["errors"] = self.errors or []
        return value


@dataclass(slots=True)
class OverlayBackfillReport:
    path: Path
    projects: int = 0
    events_written: int = 0
    events_skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


def _project_snapshot(project: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(project["id"]),
        "name": str(project["name"]),
        "root": str(project["root"]),
        "kind": str(project["kind"] or "generic"),
        "created_at": str(project["created_at"] or ""),
    }


def _build_overlay_envelope(
    *,
    event_type: str,
    project: Mapping[str, Any] | sqlite3.Row,
    payload: Mapping[str, Any],
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if event_type not in OVERLAY_EVENT_TYPES:
        raise ValueError(f"不支持的人工校正日志事件：{event_type}")
    return {
        "schema_version": OVERLAY_LOG_SCHEMA_VERSION,
        "event_id": event_id or f"overlaylog_{uuid.uuid4().hex}",
        "event_type": event_type,
        "created_at": created_at or utc_now(),
        "project": _project_snapshot(project),
        "payload": dict(payload),
    }


def _append_overlay_event(
    database: Database,
    *,
    event_type: str,
    project: Mapping[str, Any] | sqlite3.Row,
    payload: Mapping[str, Any],
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    envelope = _build_overlay_envelope(
        event_type=event_type,
        project=project,
        payload=payload,
        event_id=event_id,
        created_at=created_at,
    )
    destination = database.settings.semantic_overlay_log_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        if database.settings.event_log_fsync:
            os.fsync(handle.fileno())
    return envelope


def _iter_overlay_log(path: str | Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
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


def _validate_overlay_envelope(envelope: Mapping[str, Any]) -> str | None:
    if int(envelope.get("schema_version", 0)) != OVERLAY_LOG_SCHEMA_VERSION:
        return f"不支持的 schema_version：{envelope.get('schema_version')}"
    if not str(envelope.get("event_id") or "").strip():
        return "缺少 event_id"
    if str(envelope.get("event_type") or "") not in OVERLAY_EVENT_TYPES:
        return f"不支持的 event_type：{envelope.get('event_type')}"
    project = envelope.get("project")
    if not isinstance(project, dict) or not project.get("id") or not project.get("root"):
        return "缺少项目快照"
    payload = envelope.get("payload", {})
    if not isinstance(payload, dict):
        return "payload 必须是 JSON 对象"
    event_type = str(envelope.get("event_type") or "")
    if event_type == "correction_upsert":
        if not payload.get("correction_id") or not payload.get("action") or not payload.get("subject_key"):
            return "correction_upsert 缺少 correction_id、action 或 subject_key"
    elif event_type == "correction_remove" and not payload.get("correction_id"):
        return "correction_remove 缺少 correction_id"
    return None


def verify_semantic_overlay_log(path: str | Path) -> OverlayVerifyReport:
    source = Path(path).expanduser().resolve()
    report = OverlayVerifyReport(path=source, errors=[])
    seen: set[str] = set()
    projects: set[str] = set()
    for line_number, envelope, parse_error in _iter_overlay_log(source):
        report.lines += 1
        if parse_error or envelope is None:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{parse_error}")
            continue
        error = _validate_overlay_envelope(envelope)
        if error:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{error}")
            continue
        event_id = str(envelope["event_id"])
        if event_id in seen:
            report.duplicate_ids += 1
        seen.add(event_id)
        projects.add(str(envelope["project"]["id"]))
        report.valid += 1
    report.projects = len(projects)
    return report


def _ensure_overlay_project(
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
            f"人工校正日志中的项目 id={project_id} 与现有 root={root} 冲突"
        )
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
            str(snapshot.get("created_at") or event_time),
            event_time,
        ),
    )
    row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    assert row is not None
    return row

ACTIONS = {
    "create_concept",
    "rename_concept",
    "add_alias",
    "merge_concept",
    "accept_concept",
    "reject_concept",
    "add_edge",
    "accept_edge",
    "reject_edge",
    "map_file",
    "unmap_file",
}

_CONCEPT_ACTIONS = {
    "create_concept",
    "rename_concept",
    "add_alias",
    "merge_concept",
    "accept_concept",
    "reject_concept",
    "add_edge",
    "accept_edge",
    "reject_edge",
    "map_file",
    "unmap_file",
}


def _concept_key(value: str) -> str:
    return slugify(value.strip())


def _file_key(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/").strip().lstrip("./")).as_posix()


def _normalized_inputs(
    action: str,
    subject_key: str,
    relation: str = "",
    object_key: str = "",
    value: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    if action not in ACTIONS:
        raise ValueError(f"不支持的人工校正动作：{action}")
    subject = _concept_key(subject_key) if action in _CONCEPT_ACTIONS else subject_key.strip()
    relation_value = relation.strip()
    object_value = object_key.strip()
    data = dict(value or {})

    if action in {"create_concept", "rename_concept"}:
        label = str(data.get("label") or "").strip()
        if not label:
            raise ValueError(f"{action} 需要 value.label")
        data["label"] = label
    elif action == "add_alias":
        alias = str(data.get("alias") or "").strip()
        if not alias:
            raise ValueError("add_alias 需要 value.alias")
        data["alias"] = alias
    elif action == "merge_concept":
        object_value = _concept_key(object_value)
        if not object_value or object_value == subject:
            raise ValueError("merge_concept 需要不同的目标概念")
    elif action in {"add_edge", "accept_edge", "reject_edge"}:
        object_value = _concept_key(object_value)
        if not relation_value or not object_value:
            raise ValueError(f"{action} 需要 relation 和目标概念")
    elif action in {"map_file", "unmap_file"}:
        object_value = _file_key(object_value)
        relation_value = relation_value or "implemented_by"
        if not object_value:
            raise ValueError(f"{action} 需要文件路径")
    elif action in {"accept_concept", "reject_concept"}:
        if not subject:
            raise ValueError(f"{action} 需要概念 key")

    if not subject:
        raise ValueError("人工校正需要 subject_key")
    return subject, relation_value, object_value, data


def _correction_id(
    project_id: str,
    *,
    action: str,
    subject_key: str,
    relation: str,
    object_key: str,
    value: Mapping[str, Any],
) -> str:
    # alias 可以有多个；其他动作代表一个槽位，后写入的值应覆盖旧值，
    # 不能因为 label/note 变化而叠出多条互相冲突的校正。
    discriminator = json_dumps(dict(value)) if action == "add_alias" else ""
    return stable_id(
        project_id,
        action,
        subject_key,
        relation,
        object_key,
        discriminator,
        prefix="overlay_",
    )


def _delete_conflicting_corrections(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    action: str,
    subject_key: str,
    relation: str,
    object_key: str,
) -> None:
    if action in {"create_concept", "rename_concept", "merge_concept"}:
        connection.execute(
            "DELETE FROM semantic_overlays WHERE project_id=? AND action=? AND subject_key=?",
            (project_id, action, subject_key),
        )
    elif action in {"accept_concept", "reject_concept"}:
        connection.execute(
            """
            DELETE FROM semantic_overlays
            WHERE project_id=? AND action IN ('accept_concept', 'reject_concept')
              AND subject_key=?
            """,
            (project_id, subject_key),
        )
    elif action in {"add_edge", "accept_edge", "reject_edge"}:
        connection.execute(
            """
            DELETE FROM semantic_overlays
            WHERE project_id=? AND action IN ('add_edge', 'accept_edge', 'reject_edge')
              AND subject_key=? AND relation=? AND object_key=?
            """,
            (project_id, subject_key, relation, object_key),
        )
    elif action in {"map_file", "unmap_file"}:
        connection.execute(
            """
            DELETE FROM semantic_overlays
            WHERE project_id=? AND action IN ('map_file', 'unmap_file')
              AND subject_key=? AND relation=? AND object_key=?
            """,
            (project_id, subject_key, relation, object_key),
        )


def _upsert_correction_in_connection(
    connection: sqlite3.Connection,
    *,
    correction_id: str,
    project_id: str,
    action: str,
    subject_key: str,
    relation: str,
    object_key: str,
    value: Mapping[str, Any],
    note: str,
    now: str,
) -> sqlite3.Row:
    existing = connection.execute(
        "SELECT created_at FROM semantic_overlays WHERE id=?", (correction_id,)
    ).fetchone()
    created_at = existing["created_at"] if existing else now
    _delete_conflicting_corrections(
        connection,
        project_id=project_id,
        action=action,
        subject_key=subject_key,
        relation=relation,
        object_key=object_key,
    )
    connection.execute(
        """
        INSERT INTO semantic_overlays(
            id, project_id, action, subject_key, relation, object_key,
            value_json, note, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            action=excluded.action,
            subject_key=excluded.subject_key,
            relation=excluded.relation,
            object_key=excluded.object_key,
            value_json=excluded.value_json,
            note=excluded.note,
            enabled=1,
            updated_at=excluded.updated_at
        """,
        (
            correction_id,
            project_id,
            action,
            subject_key,
            relation,
            object_key,
            json_dumps(dict(value)),
            note.strip(),
            created_at,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM semantic_overlays WHERE id=?", (correction_id,)
    ).fetchone()
    assert row is not None
    return row


def _correction_payload(row: Mapping[str, Any] | sqlite3.Row) -> dict[str, Any]:
    raw_value = row["value_json"]
    value = json_loads(raw_value, {}) if isinstance(raw_value, str) else dict(raw_value or {})
    return {
        "correction_id": str(row["id"]),
        "action": str(row["action"]),
        "subject_key": str(row["subject_key"]),
        "relation": str(row["relation"] or ""),
        "object_key": str(row["object_key"] or ""),
        "value": value,
        "note": str(row["note"] or ""),
    }


def _apply_overlay_envelope(
    connection: sqlite3.Connection,
    envelope: Mapping[str, Any],
) -> None:
    event_id = str(envelope["event_id"])
    if Database.overlay_event_applied(connection, event_id):
        return
    event_time = str(envelope.get("created_at") or utc_now())
    project = _ensure_overlay_project(connection, envelope["project"], event_time=event_time)
    payload = envelope.get("payload", {})
    event_type = str(envelope["event_type"])
    if event_type == "correction_upsert":
        action = str(payload["action"])
        subject, relation, object_key, value = _normalized_inputs(
            action,
            str(payload.get("subject_key") or ""),
            str(payload.get("relation") or ""),
            str(payload.get("object_key") or ""),
            payload.get("value") if isinstance(payload.get("value"), dict) else {},
        )
        correction_id = str(payload.get("correction_id") or _correction_id(
            project["id"],
            action=action,
            subject_key=subject,
            relation=relation,
            object_key=object_key,
            value=value,
        ))
        _upsert_correction_in_connection(
            connection,
            correction_id=correction_id,
            project_id=project["id"],
            action=action,
            subject_key=subject,
            relation=relation,
            object_key=object_key,
            value=value,
            note=str(payload.get("note") or ""),
            now=event_time,
        )
    elif event_type == "correction_remove":
        correction_id = str(payload.get("correction_id") or "")
        if not correction_id:
            raise ValueError("correction_remove 缺少 correction_id")
        connection.execute(
            "DELETE FROM semantic_overlays WHERE project_id=? AND id=?",
            (project["id"], correction_id),
        )
    else:  # pragma: no cover - validation 已限制
        raise ValueError(f"不支持的人工校正日志事件：{event_type}")
    Database.mark_overlay_event(
        connection,
        event_id=event_id,
        project_id=project["id"],
        event_type=event_type,
    )


def replay_semantic_overlay_log(
    database: Database,
    *,
    path: str | Path | None = None,
    reset: bool = False,
    project_id: str | None = None,
    strict: bool = False,
) -> OverlayReplayReport:
    source = Path(path or database.settings.semantic_overlay_log_path).expanduser().resolve()
    report = OverlayReplayReport(path=source, errors=[])
    if not source.exists():
        return report
    if reset:
        with database.connect() as connection:
            if project_id:
                connection.execute("DELETE FROM semantic_overlays WHERE project_id=?", (project_id,))
                connection.execute("DELETE FROM applied_overlay_events WHERE project_id=?", (project_id,))
            else:
                connection.execute("DELETE FROM semantic_overlays")
                connection.execute("DELETE FROM applied_overlay_events")
            connection.commit()
    projects: set[str] = set()
    for line_number, envelope, parse_error in _iter_overlay_log(source):
        report.total += 1
        if parse_error or envelope is None:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{parse_error}")
            if strict:
                raise RuntimeError(report.errors[-1])
            continue
        error = _validate_overlay_envelope(envelope)
        if error:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行：{error}")
            if strict:
                raise RuntimeError(report.errors[-1])
            continue
        envelope_project_id = str(envelope["project"]["id"])
        if project_id and envelope_project_id != project_id:
            report.skipped += 1
            continue
        projects.add(envelope_project_id)
        event_id = str(envelope["event_id"])
        try:
            with database.connect() as connection:
                if Database.overlay_event_applied(connection, event_id):
                    report.skipped += 1
                    continue
                _apply_overlay_envelope(connection, envelope)
                connection.commit()
            report.applied += 1
        except (KeyError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
            report.invalid += 1
            report.errors.append(f"第 {line_number} 行（{event_id}）：{exc}")
            if strict:
                raise RuntimeError(report.errors[-1]) from exc
    report.projects = len(projects)
    return report


def backfill_semantic_overlay_log(
    database: Database,
    *,
    project_id: str | None = None,
) -> OverlayBackfillReport:
    destination = database.settings.semantic_overlay_log_path
    logged_corrections: set[str] = set()
    event_ids: set[str] = set()
    for _, envelope, error in _iter_overlay_log(destination):
        if error or not envelope:
            continue
        event_ids.add(str(envelope.get("event_id") or ""))
        if envelope.get("event_type") == "correction_upsert":
            correction_id = envelope.get("payload", {}).get("correction_id")
            if correction_id:
                logged_corrections.add(str(correction_id))
    report = OverlayBackfillReport(path=destination)
    with database.connect() as connection:
        if project_id:
            projects = list(connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)))
        else:
            projects = list(connection.execute("SELECT * FROM projects ORDER BY id"))
    for project in projects:
        report.projects += 1
        with database.connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT * FROM semantic_overlays WHERE project_id=? ORDER BY rowid",
                    (project["id"],),
                )
            )
        for row in rows:
            if row["id"] in logged_corrections:
                report.events_skipped += 1
                continue
            event_id = stable_id("overlay-backfill", row["id"], prefix="overlaylog_")
            if event_id in event_ids:
                report.events_skipped += 1
                continue
            envelope = _append_overlay_event(
                database,
                event_type="correction_upsert",
                project=project,
                payload=_correction_payload(row),
                event_id=event_id,
                created_at=row["created_at"],
            )
            event_ids.add(event_id)
            logged_corrections.add(row["id"])
            with database.connect() as connection:
                Database.mark_overlay_event(
                    connection,
                    event_id=envelope["event_id"],
                    project_id=project["id"],
                    event_type=envelope["event_type"],
                )
                connection.commit()
            report.events_written += 1
    return report


def add_correction(
    database: Database,
    *,
    project_id: str,
    action: str,
    subject_key: str,
    relation: str = "",
    object_key: str = "",
    value: Mapping[str, Any] | None = None,
    note: str = "",
) -> sqlite3.Row:
    subject, relation_value, object_value, data = _normalized_inputs(
        action, subject_key, relation, object_key, value
    )
    correction_id = _correction_id(
        project_id,
        action=action,
        subject_key=subject,
        relation=relation_value,
        object_key=object_value,
        value=data,
    )
    with database.connect() as connection:
        project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise LookupError(f"找不到项目：{project_id}")
    envelope = _append_overlay_event(
        database,
        event_type="correction_upsert",
        project=project,
        payload={
            "correction_id": correction_id,
            "action": action,
            "subject_key": subject,
            "relation": relation_value,
            "object_key": object_value,
            "value": data,
            "note": note.strip(),
        },
    )
    with database.connect() as connection:
        row = _upsert_correction_in_connection(
            connection,
            correction_id=correction_id,
            project_id=project_id,
            action=action,
            subject_key=subject,
            relation=relation_value,
            object_key=object_value,
            value=data,
            note=note,
            now=envelope["created_at"],
        )
        Database.mark_overlay_event(
            connection,
            event_id=envelope["event_id"],
            project_id=project_id,
            event_type=envelope["event_type"],
        )
        connection.commit()
        return row


def list_corrections(
    database: Database,
    project_id: str,
    *,
    include_disabled: bool = False,
) -> list[sqlite3.Row]:
    with database.connect() as connection:
        if include_disabled:
            return list(
                connection.execute(
                    "SELECT * FROM semantic_overlays WHERE project_id=? ORDER BY rowid",
                    (project_id,),
                )
            )
        return list(
            connection.execute(
                """
                SELECT * FROM semantic_overlays
                WHERE project_id=? AND enabled=1 ORDER BY rowid
                """,
                (project_id,),
            )
        )


def remove_correction(database: Database, project_id: str, correction_id: str) -> sqlite3.Row:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM semantic_overlays WHERE project_id=? AND id=?",
            (project_id, correction_id),
        ).fetchone()
        project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise LookupError(f"找不到人工校正：{correction_id}")
    if project is None:
        raise LookupError(f"找不到项目：{project_id}")
    envelope = _append_overlay_event(
        database,
        event_type="correction_remove",
        project=project,
        payload={"correction_id": correction_id},
    )
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM semantic_overlays WHERE project_id=? AND id=?",
            (project_id, correction_id),
        )
        Database.mark_overlay_event(
            connection,
            event_id=envelope["event_id"],
            project_id=project_id,
            event_type=envelope["event_type"],
        )
        connection.commit()
    return row


def _concept(connection: sqlite3.Connection, project_id: str, key: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM nodes
        WHERE project_id=? AND layer=2 AND kind='concept' AND key=?
        """,
        (project_id, key),
    ).fetchone()


def _ensure_concept(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    key: str,
    label: str | None = None,
) -> sqlite3.Row:
    row = _concept(connection, project_id, key)
    if row is not None:
        data = json_loads(row["data_json"], {})
        data["active"] = True
        data["manual"] = True
        data["human_reviewed"] = True
        Database.upsert_node(
            connection,
            project_id=project_id,
            layer=2,
            kind="concept",
            key=key,
            label=label or row["label"],
            data=data,
            confidence=1.0,
            source="human-overlay",
        )
        refreshed = _concept(connection, project_id, key)
        assert refreshed is not None
        return refreshed
    Database.upsert_node(
        connection,
        project_id=project_id,
        layer=2,
        kind="concept",
        key=key,
        label=label or humanize_identifier(key),
        data={"active": True, "manual": True, "aliases": [], "human_reviewed": True},
        confidence=1.0,
        source="human-overlay",
    )
    row = _concept(connection, project_id, key)
    assert row is not None
    return row


def _rewrite_concept_node(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    label: str | None = None,
    active: bool | None = None,
    data_updates: Mapping[str, Any] | None = None,
) -> None:
    data = json_loads(row["data_json"], {})
    if active is not None:
        data["active"] = active
    data.update(dict(data_updates or {}))
    data["human_reviewed"] = True
    Database.upsert_node(
        connection,
        project_id=row["project_id"],
        layer=2,
        kind="concept",
        key=row["key"],
        label=label or row["label"],
        data=data,
        confidence=1.0,
        source="human-overlay",
    )


def _overlay_edge(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    source_id: str,
    relation: str,
    target_id: str,
    data: Mapping[str, Any] | None = None,
) -> None:
    payload = dict(data or {})
    payload["human_reviewed"] = True
    Database.upsert_edge(
        connection,
        project_id=project_id,
        layer=2,
        source_id=source_id,
        relation=relation,
        target_id=target_id,
        data=payload,
        confidence=1.0,
        source="human-overlay",
    )


def _merge_concepts(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    source_key: str,
    target_key: str,
) -> bool:
    source = _concept(connection, project_id, source_key)
    target = _concept(connection, project_id, target_key)
    if source is None or target is None:
        return False
    source_id = source["id"]
    target_id = target["id"]

    outgoing = list(
        connection.execute(
            "SELECT * FROM edges WHERE project_id=? AND layer=2 AND source_id=?",
            (project_id, source_id),
        )
    )
    incoming = list(
        connection.execute(
            "SELECT * FROM edges WHERE project_id=? AND layer=2 AND target_id=?",
            (project_id, source_id),
        )
    )
    for edge in outgoing:
        if edge["target_id"] == target_id:
            continue
        data = json_loads(edge["data_json"], {})
        data["merged_from"] = source_key
        _overlay_edge(
            connection,
            project_id=project_id,
            source_id=target_id,
            relation=edge["relation"],
            target_id=edge["target_id"],
            data=data,
        )
    for edge in incoming:
        if edge["source_id"] == target_id:
            continue
        data = json_loads(edge["data_json"], {})
        data["merged_from"] = source_key
        _overlay_edge(
            connection,
            project_id=project_id,
            source_id=edge["source_id"],
            relation=edge["relation"],
            target_id=target_id,
            data=data,
        )

    # 历史任务继续有效，但 affects 统一指向合并后的概念。
    task_edges = list(
        connection.execute(
            """
            SELECT * FROM edges
            WHERE project_id=? AND layer=3 AND target_id=? AND relation='affects'
            """,
            (project_id, source_id),
        )
    )
    for edge in task_edges:
        Database.upsert_edge(
            connection,
            project_id=project_id,
            layer=3,
            source_id=edge["source_id"],
            relation="affects",
            target_id=target_id,
            data={"merged_from": source_key},
            confidence=1.0,
            source="human-overlay",
        )

    connection.execute(
        "DELETE FROM edges WHERE project_id=? AND layer=2 AND (source_id=? OR target_id=?)",
        (project_id, source_id, source_id),
    )
    connection.execute(
        "DELETE FROM edges WHERE project_id=? AND layer=3 AND target_id=? AND relation='affects'",
        (project_id, source_id),
    )
    _rewrite_concept_node(
        connection,
        source,
        active=False,
        data_updates={"merged_into": target_key},
    )
    _overlay_edge(
        connection,
        project_id=project_id,
        source_id=source_id,
        relation="merged_into",
        target_id=target_id,
        data={"source_key": source_key, "target_key": target_key},
    )
    return True


def apply_semantic_overlays(
    connection: sqlite3.Connection,
    database: Database,
    project: sqlite3.Row,
) -> int:
    """把人工 Overlay 叠加到自动 L2 上。

    自动层每次可重建；人工层独立存储并在重建后重复应用，因此不会被下一次扫描覆盖。
    """

    del database  # 接口保留，便于未来访问配置；当前只需要 connection。
    project_id = project["id"]
    rows = list(
        connection.execute(
            """
            SELECT * FROM semantic_overlays
            WHERE project_id=? AND enabled=1
            ORDER BY rowid
            """,
            (project_id,),
        )
    )
    applied = 0

    # 先建立人工概念，供后续重命名、合并和关系引用。
    for row in rows:
        if row["action"] != "create_concept":
            continue
        value = json_loads(row["value_json"], {})
        _ensure_concept(
            connection,
            project_id=project_id,
            key=row["subject_key"],
            label=str(value.get("label") or humanize_identifier(row["subject_key"])),
        )
        applied += 1

    for row in rows:
        action = row["action"]
        subject_key = row["subject_key"]
        value = json_loads(row["value_json"], {})
        if action == "create_concept":
            continue
        concept = _concept(connection, project_id, subject_key)
        if action == "rename_concept" and concept is not None:
            _rewrite_concept_node(connection, concept, label=str(value["label"]))
            applied += 1
        elif action == "add_alias" and concept is not None:
            data = json_loads(concept["data_json"], {})
            aliases = [str(item) for item in data.get("aliases", [])]
            aliases.append(str(value["alias"]))
            _rewrite_concept_node(
                connection,
                concept,
                data_updates={"aliases": list(dict.fromkeys(aliases))},
            )
            applied += 1
        elif action == "accept_concept" and concept is not None:
            _rewrite_concept_node(
                connection,
                concept,
                active=True,
                data_updates={"review_decision": "accepted"},
            )
            applied += 1
        elif action == "reject_concept" and concept is not None:
            connection.execute(
                "DELETE FROM edges WHERE project_id=? AND layer=2 AND (source_id=? OR target_id=?)",
                (project_id, concept["id"], concept["id"]),
            )
            _rewrite_concept_node(
                connection,
                concept,
                active=False,
                data_updates={"review_decision": "rejected"},
            )
            applied += 1
        elif action == "merge_concept":
            applied += int(
                _merge_concepts(
                    connection,
                    project_id=project_id,
                    source_key=subject_key,
                    target_key=row["object_key"],
                )
            )

    # 拒绝规则先删除自动结果；接受或新增规则随后可以显式恢复。
    for row in rows:
        action = row["action"]
        if action not in {"reject_edge", "unmap_file"}:
            continue
        source = _concept(connection, project_id, row["subject_key"])
        if source is None:
            continue
        if action == "reject_edge":
            target = _concept(connection, project_id, row["object_key"])
            if target is None:
                continue
            connection.execute(
                """
                DELETE FROM edges
                WHERE project_id=? AND layer=2 AND source_id=? AND relation=? AND target_id=?
                """,
                (project_id, source["id"], row["relation"], target["id"]),
            )
            applied += 1
        else:
            file_id = Database.node_id(project_id, 1, "file", row["object_key"])
            connection.execute(
                """
                DELETE FROM edges
                WHERE project_id=? AND layer=2 AND source_id=? AND relation=? AND target_id=?
                """,
                (project_id, source["id"], row["relation"], file_id),
            )
            applied += 1

    for row in rows:
        action = row["action"]
        if action not in {"add_edge", "accept_edge", "map_file"}:
            continue
        source = _concept(connection, project_id, row["subject_key"])
        if source is None:
            continue
        if action in {"add_edge", "accept_edge"}:
            target = _concept(connection, project_id, row["object_key"])
            if target is None:
                continue
            existing = connection.execute(
                """
                SELECT data_json FROM edges
                WHERE project_id=? AND layer=2 AND source_id=? AND relation=? AND target_id=?
                """,
                (project_id, source["id"], row["relation"], target["id"]),
            ).fetchone()
            data = json_loads(existing["data_json"], {}) if existing else {}
            data["review_decision"] = "accepted" if action == "accept_edge" else "manual"
            _overlay_edge(
                connection,
                project_id=project_id,
                source_id=source["id"],
                relation=row["relation"],
                target_id=target["id"],
                data=data,
            )
            applied += 1
        else:
            file_id = Database.node_id(project_id, 1, "file", row["object_key"])
            if connection.execute("SELECT 1 FROM nodes WHERE id=?", (file_id,)).fetchone() is None:
                continue
            _overlay_edge(
                connection,
                project_id=project_id,
                source_id=source["id"],
                relation=row["relation"],
                target_id=file_id,
                data={"path": row["object_key"], "manual": True},
            )
            applied += 1
    return applied


def _review_id(
    project_id: str,
    item_kind: str,
    subject_key: str,
    relation: str = "",
    object_key: str = "",
) -> str:
    return stable_id(
        project_id,
        item_kind,
        subject_key,
        relation,
        object_key,
        prefix="review_",
    )


def _decision_maps(connection: sqlite3.Connection, project_id: str) -> tuple[dict[str, str], dict[tuple[str, str, str], str]]:
    concept_decisions: dict[str, str] = {}
    edge_decisions: dict[tuple[str, str, str], str] = {}
    for row in connection.execute(
        """
        SELECT * FROM semantic_overlays
        WHERE project_id=? AND enabled=1
          AND action IN ('accept_concept', 'reject_concept', 'accept_edge', 'reject_edge')
        """,
        (project_id,),
    ):
        if row["action"].endswith("concept"):
            concept_decisions[row["subject_key"]] = (
                "accepted" if row["action"].startswith("accept") else "rejected"
            )
        else:
            edge_decisions[(row["subject_key"], row["relation"], row["object_key"])] = (
                "accepted" if row["action"].startswith("accept") else "rejected"
            )
    return concept_decisions, edge_decisions


def list_review_candidates(
    database: Database,
    project_id: str,
    *,
    limit: int = 50,
    include_reviewed: bool = False,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    with database.connect() as connection:
        concept_decisions, edge_decisions = _decision_maps(connection, project_id)
        for row in connection.execute(
            """
            SELECT * FROM nodes
            WHERE project_id=? AND layer=2 AND kind='concept'
              AND data_json NOT LIKE '%\"active\":false%'
            ORDER BY confidence ASC, label, key
            """,
            (project_id,),
        ):
            decision = concept_decisions.get(row["key"])
            if decision and not include_reviewed:
                continue
            candidates.append(
                {
                    "id": _review_id(project_id, "concept", row["key"]),
                    "kind": "concept",
                    "subject_key": row["key"],
                    "label": row["label"],
                    "relation": "",
                    "object_key": "",
                    "object_label": "",
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "decision": decision,
                }
            )
        for row in connection.execute(
            """
            SELECT e.*, s.key AS subject_key, s.label AS subject_label,
                   t.key AS object_key, t.label AS object_label
            FROM edges e
            JOIN nodes s ON s.id=e.source_id AND s.kind='concept'
            JOIN nodes t ON t.id=e.target_id AND t.kind='concept'
            WHERE e.project_id=? AND e.layer=2
              AND e.relation NOT IN ('contains', 'merged_into')
            ORDER BY e.confidence ASC, s.key, e.relation, t.key
            """,
            (project_id,),
        ):
            key = (row["subject_key"], row["relation"], row["object_key"])
            decision = edge_decisions.get(key)
            if decision and not include_reviewed:
                continue
            candidates.append(
                {
                    "id": _review_id(project_id, "edge", *key),
                    "kind": "edge",
                    "subject_key": row["subject_key"],
                    "label": row["subject_label"],
                    "relation": row["relation"],
                    "object_key": row["object_key"],
                    "object_label": row["object_label"],
                    "confidence": row["confidence"],
                    "source": row["source"],
                    "decision": decision,
                }
            )
    if include_reviewed:
        existing_ids = {item["id"] for item in candidates}
        with database.connect() as connection:
            for row in connection.execute(
                """
                SELECT * FROM semantic_overlays
                WHERE project_id=? AND enabled=1
                  AND action IN ('accept_concept', 'reject_concept', 'accept_edge', 'reject_edge')
                ORDER BY rowid
                """,
                (project_id,),
            ):
                decision = "accepted" if row["action"].startswith("accept") else "rejected"
                if row["action"].endswith("concept"):
                    candidate_id = _review_id(project_id, "concept", row["subject_key"])
                    if candidate_id in existing_ids:
                        continue
                    concept = _concept(connection, project_id, row["subject_key"])
                    candidates.append(
                        {
                            "id": candidate_id,
                            "kind": "concept",
                            "subject_key": row["subject_key"],
                            "label": concept["label"] if concept else row["subject_key"],
                            "relation": "",
                            "object_key": "",
                            "object_label": "",
                            "confidence": concept["confidence"] if concept else 1.0,
                            "source": concept["source"] if concept else "human-overlay",
                            "decision": decision,
                        }
                    )
                    existing_ids.add(candidate_id)
                else:
                    candidate_id = _review_id(
                        project_id,
                        "edge",
                        row["subject_key"],
                        row["relation"],
                        row["object_key"],
                    )
                    if candidate_id in existing_ids:
                        continue
                    source = _concept(connection, project_id, row["subject_key"])
                    target = _concept(connection, project_id, row["object_key"])
                    candidates.append(
                        {
                            "id": candidate_id,
                            "kind": "edge",
                            "subject_key": row["subject_key"],
                            "label": source["label"] if source else row["subject_key"],
                            "relation": row["relation"],
                            "object_key": row["object_key"],
                            "object_label": target["label"] if target else row["object_key"],
                            "confidence": 1.0,
                            "source": "human-overlay",
                            "decision": decision,
                        }
                    )
                    existing_ids.add(candidate_id)

    candidates.sort(
        key=lambda item: (
            item["decision"] is not None,
            float(item["confidence"]),
            item["kind"],
            item["subject_key"],
            item["relation"],
            item["object_key"],
        )
    )
    return candidates[:limit]


def decide_review_candidate(
    database: Database,
    *,
    project_id: str,
    candidate_id: str,
    decision: str,
    note: str = "",
) -> sqlite3.Row:
    if decision not in {"accept", "reject"}:
        raise ValueError("review decision 只能是 accept 或 reject")
    candidates = list_review_candidates(
        database,
        project_id,
        limit=100000,
        include_reviewed=True,
    )
    candidate = next((item for item in candidates if item["id"] == candidate_id), None)
    if candidate is None:
        raise LookupError(f"找不到待审查项：{candidate_id}")
    if candidate["kind"] == "concept":
        action = "accept_concept" if decision == "accept" else "reject_concept"
    else:
        action = "accept_edge" if decision == "accept" else "reject_edge"
    return add_correction(
        database,
        project_id=project_id,
        action=action,
        subject_key=candidate["subject_key"],
        relation=candidate["relation"],
        object_key=candidate["object_key"],
        note=note,
    )
