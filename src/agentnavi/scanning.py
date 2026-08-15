from __future__ import annotations

import sqlite3
from pathlib import Path

from .database import Database
from .extractors import ExtractionContext, FileDependency, load_registry
from .scan_support import (
    PATCH_PATH_RE,
    TEXT_EXTENSIONS,
    FileRecord,
    ScanReport,
    _digest,
    _file_node_data,
    _is_probably_text,
    _read_text,
    _test_target,
    discover_files,
    language_for,
)
from .utils import json_loads, utc_now


def _delete_resources_for_path(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    path: str,
) -> None:
    prefix = f"{path}#"
    rows = list(
        connection.execute(
            """
            SELECT id FROM nodes
            WHERE project_id=? AND layer=1 AND kind!='file'
              AND substr(key, 1, ?) = ?
            """,
            (project_id, len(prefix), prefix),
        )
    )
    for row in rows:
        connection.execute("DELETE FROM nodes WHERE id=?", (row["id"],))


def _record_for_path(root: Path, relative: str, max_file_bytes: int) -> FileRecord | None:
    absolute = root / relative
    try:
        stat = absolute.stat()
    except OSError:
        return None
    is_text, skipped_reason = _is_probably_text(absolute, max_file_bytes)
    try:
        digest = _digest(absolute)
    except OSError:
        digest = ""
        is_text = False
        skipped_reason = "无法读取文件"
    return FileRecord(
        path=relative,
        absolute_path=absolute,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        digest=digest,
        language=language_for(relative),
        is_text=is_text,
        skipped_reason=skipped_reason,
    )


def scan_physical_layer(
    database: Database,
    project: sqlite3.Row,
    *,
    full: bool = False,
) -> ScanReport:
    settings = database.settings
    root = Path(project["root"]).resolve()
    project_id = project["id"]
    current_paths = discover_files(root, settings)
    current_set = set(current_paths)
    registry = load_registry()
    extractor_signature = registry.signature

    with database.connect() as connection:
        states = {
            row["path"]: row
            for row in connection.execute(
                "SELECT * FROM file_state WHERE project_id=?",
                (project_id,),
            )
        }
        previous_file_data = {
            row["key"]: json_loads(row["data_json"], {})
            for row in connection.execute(
                "SELECT key, data_json FROM nodes WHERE project_id=? AND layer=1 AND kind='file'",
                (project_id,),
            )
        }
        deleted_paths = set(states).difference(current_set)

        # 删除目标文件前，把依赖它的源文件加入重扫集合，避免移动后留下过时解析。
        dependent_sources: set[str] = set()
        for deleted in deleted_paths:
            deleted_node = Database.node_id(project_id, 1, "file", deleted)
            for row in connection.execute(
                """
                SELECT n.key
                FROM edges e
                JOIN nodes n ON n.id=e.source_id
                WHERE e.project_id=? AND e.layer=1 AND e.target_id=? AND n.kind='file'
                """,
                (project_id, deleted_node),
            ):
                dependent_sources.add(row["key"])

        changed_paths: set[str] = set(current_paths) if full else set()
        records: dict[str, FileRecord] = {}
        for relative in current_paths:
            absolute = root / relative
            try:
                stat = absolute.stat()
            except OSError:
                continue
            previous = states.get(relative)
            previous_data = previous_file_data.get(relative, {})
            changed = (
                full
                or previous is None
                or previous["mtime_ns"] != stat.st_mtime_ns
                or previous["size"] != stat.st_size
                or previous_data.get("extractor_signature") != extractor_signature
            )
            if not changed and relative not in dependent_sources:
                continue
            changed_paths.add(relative)
            record = _record_for_path(root, relative, settings.max_file_bytes)
            if record is not None:
                records[relative] = record

        changed_paths.update(path for path in dependent_sources if path in current_set)
        for path in changed_paths:
            if path in records:
                continue
            record = _record_for_path(root, path, settings.max_file_bytes)
            if record is not None:
                records[path] = record

        if full:
            connection.execute("DELETE FROM edges WHERE project_id=? AND layer=1", (project_id,))
            connection.execute(
                "DELETE FROM nodes WHERE project_id=? AND layer=1 AND kind!='file'",
                (project_id,),
            )
        else:
            for path in changed_paths:
                node_id = Database.node_id(project_id, 1, "file", path)
                connection.execute(
                    "DELETE FROM edges WHERE project_id=? AND layer=1 AND source_id=?",
                    (project_id, node_id),
                )
                _delete_resources_for_path(connection, project_id=project_id, path=path)

        for deleted in deleted_paths:
            _delete_resources_for_path(connection, project_id=project_id, path=deleted)
            node_id = Database.node_id(project_id, 1, "file", deleted)
            connection.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            connection.execute(
                "DELETE FROM file_state WHERE project_id=? AND path=?",
                (project_id, deleted),
            )

        # 第一次扫描或 full 扫描时，先建立全部文件节点，随后解析依赖和内部资源。
        if full or not states:
            for relative in current_paths:
                record = records.get(relative) or _record_for_path(
                    root,
                    relative,
                    settings.max_file_bytes,
                )
                if record is None:
                    continue
                records[relative] = record
                Database.upsert_node(
                    connection,
                    project_id=project_id,
                    layer=1,
                    kind="file",
                    key=relative,
                    label=relative,
                    data=_file_node_data(record),
                    source="filesystem",
                )
                connection.execute(
                    """
                    INSERT INTO file_state(project_id, path, mtime_ns, size, digest, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, path) DO UPDATE SET
                        mtime_ns=excluded.mtime_ns,
                        size=excluded.size,
                        digest=excluded.digest,
                        updated_at=excluded.updated_at
                    """,
                    (project_id, relative, record.mtime_ns, record.size, record.digest, utc_now()),
                )

        for relative in sorted(changed_paths):
            record = records.get(relative)
            if record is None:
                continue
            text = _read_text(record.absolute_path) if record.is_text else None
            context = ExtractionContext(
                project_id=project_id,
                project_root=root,
                relative_path=relative,
                absolute_path=record.absolute_path,
                all_paths=frozenset(current_set),
                language=record.language,
                size=record.size,
                digest=record.digest,
                is_text=record.is_text,
                text=text,
                max_file_bytes=settings.max_file_bytes,
            )
            result = registry.extract(context)
            dependencies = list(result.dependencies)
            test_target = _test_target(relative, current_set)
            if test_target:
                dependencies.append(FileDependency("tests", test_target))

            metadata = dict(result.metadata)
            metadata["roles"] = list(result.roles)
            metadata["extractor_signature"] = extractor_signature
            metadata["extractor_id"] = result.extractor_id
            metadata["extractor_version"] = result.extractor_version
            if result.warnings:
                metadata["extractor_warnings"] = list(result.warnings[:100])

            source_node = Database.upsert_node(
                connection,
                project_id=project_id,
                layer=1,
                kind="file",
                key=relative,
                label=relative,
                data=_file_node_data(
                    record,
                    external_dependencies=list(result.external_dependencies),
                    metadata=metadata,
                ),
                source="filesystem",
            )
            connection.execute(
                """
                INSERT INTO file_state(project_id, path, mtime_ns, size, digest, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, path) DO UPDATE SET
                    mtime_ns=excluded.mtime_ns,
                    size=excluded.size,
                    digest=excluded.digest,
                    updated_at=excluded.updated_at
                """,
                (project_id, relative, record.mtime_ns, record.size, record.digest, utc_now()),
            )

            resource_nodes: dict[str, str] = {}
            for resource in result.resources[:5000]:
                full_key = f"{relative}#{resource.key}"
                resource_node = Database.upsert_node(
                    connection,
                    project_id=project_id,
                    layer=1,
                    kind=resource.kind,
                    key=full_key,
                    label=resource.label,
                    data={
                        "parent_path": relative,
                        "resource_key": resource.key,
                        "extractor_id": result.extractor_id,
                        **resource.data,
                    },
                    source="extractor",
                )
                resource_nodes[resource.key] = resource_node
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=1,
                    source_id=source_node,
                    relation="contains",
                    target_id=resource_node,
                    data={"parent_path": relative, "resource_key": resource.key},
                    source="extractor",
                )

            for relation in result.resource_relations[:10000]:
                target_node = resource_nodes.get(relation.target_key)
                if target_node is None:
                    continue
                relation_source = source_node if relation.source_key is None else resource_nodes.get(relation.source_key)
                if relation_source is None or relation_source == target_node:
                    continue
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=1,
                    source_id=relation_source,
                    relation=relation.relation,
                    target_id=target_node,
                    data=relation.data,
                    confidence=max(0.0, min(relation.confidence, 1.0)),
                    source="extractor",
                )

            for dependency in dependencies:
                target_path = dependency.target_path
                if target_path == relative or target_path not in current_set:
                    continue
                target_node = Database.node_id(project_id, 1, "file", target_path)
                if connection.execute("SELECT 1 FROM nodes WHERE id=?", (target_node,)).fetchone() is None:
                    target_record = records.get(target_path) or _record_for_path(
                        root,
                        target_path,
                        settings.max_file_bytes,
                    )
                    if target_record is None:
                        continue
                    target_node = Database.upsert_node(
                        connection,
                        project_id=project_id,
                        layer=1,
                        kind="file",
                        key=target_path,
                        label=target_path,
                        data=_file_node_data(target_record),
                        source="filesystem",
                    )
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=1,
                    source_id=source_node,
                    relation=dependency.relation,
                    target_id=target_node,
                    data={
                        "source_path": relative,
                        "target_path": target_path,
                        **dependency.data,
                    },
                    confidence=max(0.0, min(dependency.confidence, 1.0)),
                    source="extractor",
                )

        Database.set_project_scan_time(connection, project_id)
        connection.commit()

        total_nodes = connection.execute(
            "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=1",
            (project_id,),
        ).fetchone()["count"]
        total_edges = connection.execute(
            "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=1",
            (project_id,),
        ).fetchone()["count"]

    return ScanReport(
        project_id=project_id,
        total_files=len(current_paths),
        changed_files=len(changed_paths),
        deleted_files=len(deleted_paths),
        physical_nodes=total_nodes,
        physical_edges=total_edges,
    )


def parse_patch_paths(command: str) -> list[str]:
    return [match.group(1).strip().replace("\\", "/") for match in PATCH_PATH_RE.finditer(command)]
