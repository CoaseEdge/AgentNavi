"""Shared relation vocabulary for L2 concept-to-file mappings."""

from __future__ import annotations


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

# Keep a stable order because this literal is shared by SQLite partial-index
# predicates and the matching bounded Impact queries.
CONCEPT_FILE_MAPPING_RELATIONS = tuple(dict.fromkeys((
    "implemented_by",
    "configured_by",
    *ROLE_RELATIONS.values(),
)))
CONCEPT_FILE_MAPPING_RELATIONS_SQL = "(" + ",".join(
    f"'{relation}'" for relation in CONCEPT_FILE_MAPPING_RELATIONS
) + ")"
