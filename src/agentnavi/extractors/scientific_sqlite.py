from __future__ import annotations

import sqlite3
from urllib.parse import quote

from .api import ExtractedResource, ExtractionContext, ExtractionResult, ResourceRelation

def _sqlite_extract(context: ExtractionContext) -> ExtractionResult:
    resources: list[ExtractedResource] = []
    relations: list[ResourceRelation] = []
    metadata: dict[str, object] = {}
    warnings: list[str] = []
    uri = f"file:{quote(str(context.absolute_path.resolve()))}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            metadata.update(
                {
                    "sqlite_user_version": version,
                    "page_count": page_count,
                    "page_size": page_size,
                    "estimated_database_bytes": page_count * page_size,
                }
            )
            objects = list(
                connection.execute(
                    """
                    SELECT type, name, tbl_name, sql
                    FROM sqlite_master
                    WHERE name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                    LIMIT 1000
                    """
                )
            )
            counts: dict[str, int] = {}
            for row in objects:
                kind = str(row["type"])
                name = str(row["name"])
                counts[kind] = counts.get(kind, 0) + 1
                data: dict[str, object] = {"table_name": row["tbl_name"]}
                if kind in {"table", "view"}:
                    escaped = name.replace("'", "''")
                    try:
                        columns = [
                            {
                                "name": item["name"],
                                "type": item["type"],
                                "notnull": bool(item["notnull"]),
                                "primary_key": bool(item["pk"]),
                            }
                            for item in connection.execute(f"PRAGMA table_info('{escaped}')")
                        ]
                    except sqlite3.DatabaseError:
                        columns = []
                    data["columns"] = columns[:500]
                    data["column_count"] = len(columns)
                resource_key = f"{kind}:{name}"
                resources.append(ExtractedResource(f"database_{kind}", resource_key, name, data))
                if kind in {"index", "trigger"} and row["tbl_name"]:
                    target_key = f"table:{row['tbl_name']}"
                    relations.append(ResourceRelation("attached_to", target_key, source_key=resource_key))
            metadata["object_counts"] = counts
            metadata["objects"] = [resource.label for resource in resources[:200]]
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        warnings.append(f"SQLite 解析失败：{exc}")
    return ExtractionResult(
        "builtin.science.sqlite",
        "1",
        metadata=metadata,
        roles=("database", "dataset", "structured_data"),
        resources=tuple(resources),
        resource_relations=tuple(relations),
        warnings=tuple(warnings),
    )

