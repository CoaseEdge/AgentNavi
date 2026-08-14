from __future__ import annotations

import sqlite3
from pathlib import Path, PurePosixPath

from .database import Database
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
    _text_metadata,
    discover_files,
    language_for,
    parse_javascript_dependencies,
    parse_markdown_dependencies,
    parse_python_dependencies,
)
from .utils import utc_now

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

    with database.connect() as connection:
        states = {
            row["path"]: row
            for row in connection.execute("SELECT * FROM file_state WHERE project_id=?", (project_id,))
        }
        deleted_paths = set(states).difference(current_set)

        # 删除目标文件前，把所有依赖它的源文件加入重扫集合，避免移动文件后留下过时解析。
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
            changed = (
                full
                or previous is None
                or previous["mtime_ns"] != stat.st_mtime_ns
                or previous["size"] != stat.st_size
            )
            if not changed and relative not in dependent_sources:
                continue
            changed_paths.add(relative)
            is_text, skipped_reason = _is_probably_text(absolute, settings.max_file_bytes)
            try:
                digest = _digest(absolute)
            except OSError:
                digest = ""
                is_text = False
                skipped_reason = "无法读取文件"
            records[relative] = FileRecord(
                path=relative,
                absolute_path=absolute,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                digest=digest,
                language=language_for(relative),
                is_text=is_text,
                skipped_reason=skipped_reason,
            )

        changed_paths.update(path for path in dependent_sources if path in current_set)
        for path in changed_paths:
            if path in records:
                continue
            absolute = root / path
            try:
                stat = absolute.stat()
            except OSError:
                continue
            is_text, skipped_reason = _is_probably_text(absolute, settings.max_file_bytes)
            try:
                digest = _digest(absolute)
            except OSError:
                digest = ""
                is_text = False
                skipped_reason = "无法读取文件"
            records[path] = FileRecord(
                path=path,
                absolute_path=absolute,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                digest=digest,
                language=language_for(path),
                is_text=is_text,
                skipped_reason=skipped_reason,
            )

        if full:
            connection.execute("DELETE FROM edges WHERE project_id=? AND layer=1", (project_id,))
        else:
            for path in changed_paths:
                node_id = Database.node_id(project_id, 1, "file", path)
                connection.execute("DELETE FROM edges WHERE project_id=? AND layer=1 AND source_id=?", (project_id, node_id))

        for deleted in deleted_paths:
            node_id = Database.node_id(project_id, 1, "file", deleted)
            connection.execute("DELETE FROM nodes WHERE id=?", (node_id,))
            connection.execute("DELETE FROM file_state WHERE project_id=? AND path=?", (project_id, deleted))

        # 第一次扫描或 full 扫描时，先建立所有文件节点，随后解析依赖。
        if full or not states:
            for relative in current_paths:
                absolute = root / relative
                try:
                    stat = absolute.stat()
                except OSError:
                    continue
                record = records.get(relative)
                if record is None:
                    is_text, skipped_reason = _is_probably_text(absolute, settings.max_file_bytes)
                    try:
                        digest = _digest(absolute)
                    except OSError:
                        digest = ""
                        is_text = False
                        skipped_reason = "无法读取文件"
                    record = FileRecord(
                        path=relative,
                        absolute_path=absolute,
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        digest=digest,
                        language=language_for(relative),
                        is_text=is_text,
                        skipped_reason=skipped_reason,
                    )
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

        physical_edge_count = 0
        for relative in sorted(changed_paths):
            record = records.get(relative)
            if record is None:
                continue
            external_dependencies: list[str] = []
            dependencies: list[tuple[str, str]] = []
            metadata: dict[str, object] = {}
            if record.is_text:
                text = _read_text(record.absolute_path)
                if text is not None:
                    metadata = _text_metadata(relative, text)
                    suffix = PurePosixPath(relative).suffix.lower()
                    if suffix == ".py":
                        dependencies, external_dependencies = parse_python_dependencies(relative, text, current_set)
                    elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"}:
                        dependencies, external_dependencies = parse_javascript_dependencies(relative, text, current_set)
                    elif suffix in {".md", ".mdx", ".rst"}:
                        dependencies = parse_markdown_dependencies(relative, text, current_set)

            test_target = _test_target(relative, current_set)
            if test_target:
                dependencies.append(("tests", test_target))

            source_node = Database.upsert_node(
                connection,
                project_id=project_id,
                layer=1,
                kind="file",
                key=relative,
                label=relative,
                data=_file_node_data(record, external_dependencies=external_dependencies, metadata=metadata),
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

            for relation, target_path in dict.fromkeys(dependencies):
                if target_path == relative or target_path not in current_set:
                    continue
                target_node = Database.node_id(project_id, 1, "file", target_path)
                if connection.execute("SELECT 1 FROM nodes WHERE id=?", (target_node,)).fetchone() is None:
                    target_absolute = root / target_path
                    try:
                        target_stat = target_absolute.stat()
                    except OSError:
                        continue
                    target_data = {
                        "path": target_path,
                        "language": language_for(target_path),
                        "size": target_stat.st_size,
                        "mtime_ns": target_stat.st_mtime_ns,
                        "digest": states.get(target_path, {}).get("digest", "") if isinstance(states.get(target_path), dict) else "",
                        "is_text": target_absolute.suffix.lower() in TEXT_EXTENSIONS,
                    }
                    target_node = Database.upsert_node(
                        connection,
                        project_id=project_id,
                        layer=1,
                        kind="file",
                        key=target_path,
                        label=target_path,
                        data=target_data,
                        source="filesystem",
                    )
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=1,
                    source_id=source_node,
                    relation=relation,
                    target_id=target_node,
                    data={"source_path": relative, "target_path": target_path},
                    source="parser",
                )
                physical_edge_count += 1

        Database.set_project_scan_time(connection, project_id)
        connection.commit()

        total_edges = connection.execute(
            "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=1", (project_id,)
        ).fetchone()["count"]

    return ScanReport(
        project_id=project_id,
        total_files=len(current_paths),
        changed_files=len(changed_paths),
        deleted_files=len(deleted_paths),
        physical_nodes=len(current_paths),
        physical_edges=total_edges,
    )


def parse_patch_paths(command: str) -> list[str]:
    return [match.group(1).strip().replace("\\", "/") for match in PATCH_PATH_RE.finditer(command)]
