from __future__ import annotations

import json
import re
import shlex
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any

from .database import Database
from .utils import humanize_identifier, json_loads, slugify

GENERIC_PREFIXES = {
    "src",
    "source",
    "lib",
    "libs",
    "app",
    "apps",
    "packages",
    "package",
    "modules",
    "module",
    "services",
    "service",
    "domains",
    "domain",
    "features",
    "feature",
    "components",
    "component",
    "internal",
}

GENERIC_SEGMENTS = {
    "test",
    "tests",
    "spec",
    "specs",
    "docs",
    "doc",
    "documentation",
    "config",
    "configs",
    "scripts",
    "script",
    "examples",
    "example",
    "fixtures",
    "fixture",
    "migrations",
    "migration",
}

GENERIC_STEMS = {
    "index",
    "main",
    "app",
    "init",
    "__init__",
    "utils",
    "utility",
    "helpers",
    "helper",
    "common",
    "shared",
    "core",
    "types",
    "constants",
    "config",
    "settings",
    "readme",
    "license",
}

CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".env"}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".pdf"}
TEST_MARKERS = ("test_", ".test.", ".spec.", "/tests/", "/test/", "/spec/")


def _clean_segment(value: str) -> str:
    value = re.sub(r"\.(test|spec)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^test_", "", value, flags=re.IGNORECASE)
    return value.strip("-_. ")


def concept_for_path(path: str, project_name: str) -> tuple[str, str]:
    pure = PurePosixPath(path)
    directories = list(pure.parts[:-1])
    stem = _clean_segment(pure.stem)

    # packages/<name>/...、apps/<name>/... 这类单仓库结构优先用容器后的名称。
    for index, segment in enumerate(directories[:-1]):
        if segment.lower() in {"packages", "apps", "services", "modules", "features", "domains"}:
            candidate = _clean_segment(directories[index + 1])
            if candidate and candidate.lower() not in GENERIC_SEGMENTS:
                return slugify(candidate), humanize_identifier(candidate)

    meaningful = [
        _clean_segment(segment)
        for segment in directories
        if segment.lower() not in GENERIC_PREFIXES and segment.lower() not in GENERIC_SEGMENTS
    ]
    meaningful = [segment for segment in meaningful if segment]
    if meaningful:
        candidate = meaningful[0]
    elif stem and stem.lower() not in GENERIC_STEMS:
        candidate = stem
    else:
        candidate = project_name
    return slugify(candidate), humanize_identifier(candidate)


def _file_relation(path: str) -> str:
    lowered = f"/{path.lower()}"
    name = PurePosixPath(path).name.lower()
    suffix = PurePosixPath(path).suffix.lower()
    if any(marker in lowered or marker in name for marker in TEST_MARKERS):
        return "tested_by"
    if suffix in DOC_EXTENSIONS or "/docs/" in lowered:
        return "documented_by"
    if suffix in CONFIG_EXTENSIONS or "/config/" in lowered:
        return "configured_by"
    return "implemented_by"


def _confidence_for_relation(count: int) -> float:
    return min(0.97, 0.55 + 0.08 * max(count, 1))


def _concept_display_metadata(
    files: list[sqlite3.Row],
    fallback_label: str,
) -> tuple[str, list[str], list[str]]:
    """从文档标题和结构化元数据中选择更接近人类语言的概念名称。"""

    doc_titles: list[str] = []
    other_titles: list[str] = []
    aliases: list[str] = [fallback_label]
    keywords: list[str] = []
    for file_row in files:
        data = json_loads(file_row["data_json"], {})
        title = str(data.get("title") or "").strip()
        if title:
            aliases.append(title)
            if _file_relation(file_row["key"]) == "documented_by":
                doc_titles.append(title)
            else:
                other_titles.append(title)
        for key in ("headings", "link_labels", "symbols"):
            values = data.get(key, [])
            if isinstance(values, list):
                keywords.extend(str(value).strip() for value in values if str(value).strip())
    candidates = doc_titles or other_titles
    label = sorted(candidates, key=lambda value: (len(value), value))[0] if candidates else fallback_label
    aliases = list(dict.fromkeys(item[:160] for item in aliases if item))
    keywords = list(dict.fromkeys(item[:160] for item in keywords if item))[:100]
    return label, aliases, keywords


def build_semantic_layer(database: Database, project: sqlite3.Row) -> tuple[int, int]:
    project_id = project["id"]
    project_name = project["name"]
    with database.connect() as connection:
        file_rows = list(
            connection.execute(
                "SELECT * FROM nodes WHERE project_id=? AND layer=1 AND kind='file' ORDER BY key",
                (project_id,),
            )
        )
        file_by_id = {row["id"]: row for row in file_rows}
        file_to_concept: dict[str, str] = {}
        concept_files: dict[str, list[sqlite3.Row]] = defaultdict(list)
        concept_labels: dict[str, str] = {}

        for file_row in file_rows:
            concept_key, label = concept_for_path(file_row["key"], project_name)
            file_to_concept[file_row["id"]] = concept_key
            concept_files[concept_key].append(file_row)
            concept_labels.setdefault(concept_key, label)

        # L2 是派生层：重建其边，但保留节点 ID，避免无意义地破坏历史任务对概念的引用。
        connection.execute("DELETE FROM edges WHERE project_id=? AND layer=2", (project_id,))

        project_node = Database.upsert_node(
            connection,
            project_id=project_id,
            layer=2,
            kind="project",
            key=project_id,
            label=project_name,
            data={"root": project["root"], "kind": project["kind"], "active": True},
            source="project-registry",
        )

        desired_concept_ids: set[str] = set()
        for concept_key, files in sorted(concept_files.items()):
            languages = Counter(
                str(json_loads(file_row["data_json"], {}).get("language", "unknown")) for file_row in files
            )
            label, aliases, keywords = _concept_display_metadata(files, concept_labels[concept_key])
            concept_node = Database.upsert_node(
                connection,
                project_id=project_id,
                layer=2,
                kind="concept",
                key=concept_key,
                label=label,
                data={
                    "active": True,
                    "file_count": len(files),
                    "languages": dict(languages.most_common()),
                    "aliases": aliases,
                    "keywords": keywords,
                    "inference": "path-grouping+file-metadata",
                },
                confidence=0.78 if label != concept_labels[concept_key] else 0.72,
                source="semantic-heuristic",
            )
            desired_concept_ids.add(concept_node)
            Database.upsert_edge(
                connection,
                project_id=project_id,
                layer=2,
                source_id=project_node,
                relation="contains",
                target_id=concept_node,
                source="semantic-heuristic",
            )
            for file_row in files:
                relation = _file_relation(file_row["key"])
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=2,
                    source_id=concept_node,
                    relation=relation,
                    target_id=file_row["id"],
                    data={"path": file_row["key"]},
                    confidence=0.75,
                    source="semantic-heuristic",
                )

        # 文件依赖聚合为概念依赖。只在跨概念时建边，避免把底层噪声搬到语义层。
        # 同一方向上的“代码依赖 + 文档引用”只保留更强的 depends_on，引用仍作为证据保存，
        # 避免同时出现 depends_on 与 related_to 两条近义边而制造图谱噪声。
        aggregation: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for edge in connection.execute(
            "SELECT * FROM edges WHERE project_id=? AND layer=1", (project_id,)
        ):
            source_concept = file_to_concept.get(edge["source_id"])
            target_concept = file_to_concept.get(edge["target_id"])
            if not source_concept or not target_concept or source_concept == target_concept:
                continue
            source_file = file_by_id.get(edge["source_id"])
            target_file = file_by_id.get(edge["target_id"])
            evidence = {
                "source": source_file["key"] if source_file else edge["source_id"],
                "target": target_file["key"] if target_file else edge["target_id"],
                "physical_relation": edge["relation"],
            }
            aggregation[(source_concept, target_concept)].append(evidence)

        for (source_key, target_key), evidence in sorted(aggregation.items()):
            physical_relations = {item["physical_relation"] for item in evidence}
            relation = "related_to" if physical_relations == {"references"} else "depends_on"
            source_id = Database.node_id(project_id, 2, "concept", source_key)
            target_id = Database.node_id(project_id, 2, "concept", target_key)
            Database.upsert_edge(
                connection,
                project_id=project_id,
                layer=2,
                source_id=source_id,
                relation=relation,
                target_id=target_id,
                data={"count": len(evidence), "evidence": evidence[:20]},
                confidence=_confidence_for_relation(len(evidence)),
                source="semantic-heuristic",
            )

        # 旧概念不立即删除，而标为 inactive；任务历史仍可解释，下一次同名概念出现也能复用节点。
        for stale in connection.execute(
            "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND kind='concept'",
            (project_id,),
        ):
            if stale["id"] in desired_concept_ids:
                continue
            data = json_loads(stale["data_json"], {})
            data["active"] = False
            data["file_count"] = 0
            Database.upsert_node(
                connection,
                project_id=project_id,
                layer=2,
                kind="concept",
                key=stale["key"],
                label=stale["label"],
                data=data,
                confidence=stale["confidence"],
                source=stale["source"],
            )

        if database.settings.semantic_command:
            _apply_external_provider(connection, database, project, database.settings.semantic_command)

        connection.commit()
        node_count = connection.execute(
            "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=2", (project_id,)
        ).fetchone()["count"]
        edge_count = connection.execute(
            "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=2", (project_id,)
        ).fetchone()["count"]
        return node_count, edge_count


def _apply_external_provider(
    connection: sqlite3.Connection,
    database: Database,
    project: sqlite3.Row,
    command: str,
) -> None:
    """调用可选的外部语义提供器。

    提供器从 stdin 读取 JSON，并在 stdout 返回：

    ``{"concepts": [...], "relations": [...]}``

    AgentNavi 不绑定任何模型厂商；调用者可接本地模型、云模型或纯规则程序。
    """

    project_id = project["id"]
    current_concepts = [
        {
            "key": row["key"],
            "label": row["label"],
            "data": json_loads(row["data_json"], {}),
        }
        for row in connection.execute(
            "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND kind='concept'",
            (project_id,),
        )
    ]
    physical_edges = [
        {
            "source": source["key"],
            "relation": edge["relation"],
            "target": target["key"],
        }
        for edge in connection.execute(
            "SELECT * FROM edges WHERE project_id=? AND layer=1 LIMIT 5000", (project_id,)
        )
        if (source := connection.execute("SELECT key FROM nodes WHERE id=?", (edge["source_id"],)).fetchone())
        and (target := connection.execute("SELECT key FROM nodes WHERE id=?", (edge["target_id"],)).fetchone())
    ]
    payload = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project["name"], "root": project["root"], "kind": project["kind"]},
        "concepts": current_concepts,
        "physical_edges": physical_edges,
    }
    try:
        completed = subprocess.run(
            shlex.split(command),
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            check=False,
            cwd=project["root"],
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if completed.returncode != 0 or not completed.stdout.strip():
        return
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return
    if not isinstance(result, dict):
        return

    concept_ids: dict[str, str] = {}
    for concept in result.get("concepts", []):
        if not isinstance(concept, dict) or not concept.get("key"):
            continue
        key = slugify(str(concept["key"]))
        label = str(concept.get("label") or humanize_identifier(key))
        confidence = float(concept.get("confidence", 0.7))
        concept_id = Database.upsert_node(
            connection,
            project_id=project_id,
            layer=2,
            kind="concept",
            key=key,
            label=label,
            data={"active": True, "provider_data": concept.get("data", {})},
            confidence=max(0.0, min(confidence, 1.0)),
            source="external-semantic-provider",
        )
        concept_ids[key] = concept_id
        for file_path in concept.get("files", []):
            file_node = Database.node_id(project_id, 1, "file", str(file_path))
            if connection.execute("SELECT 1 FROM nodes WHERE id=?", (file_node,)).fetchone():
                Database.upsert_edge(
                    connection,
                    project_id=project_id,
                    layer=2,
                    source_id=concept_id,
                    relation="implemented_by",
                    target_id=file_node,
                    data={"path": str(file_path)},
                    confidence=max(0.0, min(confidence, 1.0)),
                    source="external-semantic-provider",
                )

    for relation in result.get("relations", []):
        if not isinstance(relation, dict):
            continue
        source_key = slugify(str(relation.get("from", "")))
        target_key = slugify(str(relation.get("to", "")))
        if not source_key or not target_key:
            continue
        source_id = concept_ids.get(source_key) or Database.node_id(project_id, 2, "concept", source_key)
        target_id = concept_ids.get(target_key) or Database.node_id(project_id, 2, "concept", target_key)
        if connection.execute("SELECT 1 FROM nodes WHERE id=?", (source_id,)).fetchone() is None:
            continue
        if connection.execute("SELECT 1 FROM nodes WHERE id=?", (target_id,)).fetchone() is None:
            continue
        confidence = float(relation.get("confidence", 0.7))
        Database.upsert_edge(
            connection,
            project_id=project_id,
            layer=2,
            source_id=source_id,
            relation=str(relation.get("relation") or "related_to"),
            target_id=target_id,
            data={"evidence": relation.get("evidence", [])},
            confidence=max(0.0, min(confidence, 1.0)),
            source="external-semantic-provider",
        )
