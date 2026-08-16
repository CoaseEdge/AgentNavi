from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import PurePosixPath

from .database import Database
from .utils import json_loads

ROLE_RELATIONS = {
    "test": "tested_by",
    "document": "documented_by",
    "configuration": "configured_by",
    "manifest": "configured_by",
    "dataset": "data_provided_by",
    "scientific_data": "data_provided_by",
    "database": "data_provided_by",
    "notebook": "analyzed_by",
    "analysis": "analyzed_by",
    "media_asset": "uses_asset",
    "generated_output": "produced_by",
}
TEST_MARKERS = ("test_", ".test.", ".spec.", "/tests/", "/test/", "/spec/")
KEYWORD_FIELDS = (
    "columns",
    "top_level_keys",
    "sections",
    "read_tables",
    "write_tables",
    "variables",
    "sheets",
    "objects",
)


def _relation_for_roles(path: str, roles: list[str]) -> str | None:
    lowered = f"/{path.lower()}"
    name = PurePosixPath(path).name.lower()
    if any(marker in lowered or marker in name for marker in TEST_MARKERS):
        return "tested_by"
    for role in roles:
        relation = ROLE_RELATIONS.get(role)
        if relation:
            return relation
    return None


def apply_extractor_semantics(
    connection: sqlite3.Connection,
    database: Database,
    project: sqlite3.Row,
) -> None:
    """Overlay deterministic extractor roles onto the automatically rebuilt L2 graph.

    This remains a derived layer: it rewrites only heuristic concept-to-file edges. External
    semantic providers keep their explicit mappings, and human semantic overlays are applied
    afterwards by the engine.
    """

    project_id = project["id"]
    rows = list(
        connection.execute(
            """
            SELECT e.*, target.key AS file_path, target.data_json AS file_data,
                   source.kind AS source_kind
            FROM edges e
            JOIN nodes target ON target.id=e.target_id
            JOIN nodes source ON source.id=e.source_id
            WHERE e.project_id=? AND e.layer=2
              AND e.source='semantic-heuristic'
              AND target.kind='file' AND source.kind='concept'
            """,
            (project_id,),
        )
    )
    for row in rows:
        file_data = json_loads(row["file_data"], {})
        roles = [str(item) for item in file_data.get("roles", []) if str(item)]
        desired = _relation_for_roles(row["file_path"], roles)
        if not desired or desired == row["relation"]:
            continue
        edge_data = json_loads(row["data_json"], {})
        edge_data["roles"] = roles
        connection.execute("DELETE FROM edges WHERE id=?", (row["id"],))
        Database.upsert_edge(
            connection,
            project_id=project_id,
            layer=2,
            source_id=row["source_id"],
            relation=desired,
            target_id=row["target_id"],
            data=edge_data,
            confidence=max(float(row["confidence"]), 0.8),
            source="semantic-heuristic",
        )

    concepts = list(
        connection.execute(
            "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND kind='concept'",
            (project_id,),
        )
    )
    for concept in concepts:
        file_rows = list(
            connection.execute(
                """
                SELECT target.data_json
                FROM edges e
                JOIN nodes target ON target.id=e.target_id
                WHERE e.project_id=? AND e.layer=2 AND e.source_id=? AND target.kind='file'
                """,
                (project_id, concept["id"]),
            )
        )
        if not file_rows:
            continue
        role_counts: Counter[str] = Counter()
        keywords: list[str] = []
        for file_row in file_rows:
            data = json_loads(file_row["data_json"], {})
            role_counts.update(str(item) for item in data.get("roles", []) if str(item))
            for field in KEYWORD_FIELDS:
                values = data.get(field, [])
                if isinstance(values, list):
                    keywords.extend(str(value).strip() for value in values if str(value).strip())
        data = json_loads(concept["data_json"], {})
        data["roles"] = dict(role_counts.most_common())
        existing = [str(item) for item in data.get("keywords", []) if str(item)]
        data["keywords"] = list(dict.fromkeys((*existing, *(item[:160] for item in keywords))))[:150]
        data["inference"] = "path-grouping+extractor-metadata"
        Database.upsert_node(
            connection,
            project_id=project_id,
            layer=2,
            kind="concept",
            key=concept["key"],
            label=concept["label"],
            data=data,
            confidence=concept["confidence"],
            source=concept["source"],
        )
