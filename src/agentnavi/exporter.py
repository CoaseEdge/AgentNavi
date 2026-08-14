from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .database import Database
from .registry import list_projects, resolve_project
from .utils import json_loads, slugify, stable_id, utc_now


@dataclass(frozen=True, slots=True)
class ExportReport:
    destination: Path
    projects: int
    concepts: int
    tasks: int
    files_written: int


def _note_name(label: str, key: str) -> str:
    return f"{slugify(label, fallback='note')}--{stable_id(key)[:8]}"


def _wiki(path_without_suffix: str, label: str) -> str:
    return f"[[{path_without_suffix}|{label}]]"


def _file_uri(path: Path) -> str:
    # Path.as_uri 对绝对路径编码正确；quote 只用于极少数不可打印字符兜底。
    try:
        return path.resolve().as_uri()
    except ValueError:
        return "file://" + quote(str(path.resolve()))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _project_note_path(project_id: str, project_name: str) -> str:
    return f"AgentNavi/Projects/{_note_name(project_name, project_id)}"


def _concept_note_path(project_id: str, label: str, key: str) -> str:
    return f"AgentNavi/Concepts/{slugify(project_id)}/{_note_name(label, key)}"


def _task_note_path(project_id: str, title: str, task_id: str) -> str:
    return f"AgentNavi/Tasks/{slugify(project_id)}/{_note_name(title, task_id)}"


def export_obsidian(
    database: Database,
    *,
    destination: str | Path | None = None,
    project_selector: str | None = None,
    clean: bool = True,
) -> ExportReport:
    """把派生图谱投影为可浏览的 Obsidian Markdown。

    只管理目标 Vault 下的 ``AgentNavi/`` 子目录，不会向被索引项目写文件。
    """

    vault = Path(destination or database.settings.obsidian_vault).expanduser().resolve()
    generated_root = vault / "AgentNavi"
    if clean and generated_root.exists():
        shutil.rmtree(generated_root)
    generated_root.mkdir(parents=True, exist_ok=True)

    projects = [resolve_project(database, project_selector)] if project_selector else list_projects(database)
    total_concepts = 0
    total_tasks = 0
    files_written = 0
    project_links: list[str] = []

    with database.connect() as connection:
        for project in projects:
            project_id = project["id"]
            root = Path(project["root"]).resolve()
            project_path = _project_note_path(project_id, project["name"])
            project_links.append(_wiki(project_path, project["name"]))

            concept_rows = list(
                connection.execute(
                    """
                    SELECT * FROM nodes
                    WHERE project_id=? AND layer=2 AND kind='concept'
                      AND data_json NOT LIKE '%"active":false%'
                    ORDER BY label COLLATE NOCASE, key
                    """,
                    (project_id,),
                )
            )
            task_rows = list(
                connection.execute(
                    "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC",
                    (project_id,),
                )
            )
            total_concepts += len(concept_rows)
            total_tasks += len(task_rows)

            concept_path_by_id = {
                row["id"]: _concept_note_path(project_id, row["label"], row["key"])
                for row in concept_rows
            }
            task_path_by_id = {
                row["id"]: _task_note_path(project_id, row["title"], row["id"])
                for row in task_rows
            }

            for concept in concept_rows:
                concept_path = concept_path_by_id[concept["id"]]
                data = json_loads(concept["data_json"], {})
                lines = [
                    "---",
                    "agentnavi_type: concept",
                    f"project: {project_id}",
                    f"concept_key: {concept['key']}",
                    f"confidence: {concept['confidence']}",
                    f"source: {concept['source']}",
                    f"generated_at: {utc_now()}",
                    "---",
                    "",
                    f"# {concept['label']}",
                    "",
                    f"项目：{_wiki(project_path, project['name'])}",
                    "",
                    "> 本页由 AgentNavi 自动生成。真实事实仍以项目仓库、Git 与 Agent 事件为准。",
                ]

                related = list(
                    connection.execute(
                        """
                        SELECT e.relation, e.confidence, e.source,
                               target.id AS target_id, target.label AS target_label
                        FROM edges e JOIN nodes target ON target.id=e.target_id
                        WHERE e.project_id=? AND e.layer=2 AND e.source_id=?
                          AND target.layer=2 AND target.kind='concept'
                        ORDER BY e.relation, target.label
                        """,
                        (project_id, concept["id"]),
                    )
                )
                incoming = list(
                    connection.execute(
                        """
                        SELECT e.relation, e.confidence, e.source,
                               source.id AS source_id, source.label AS source_label
                        FROM edges e JOIN nodes source ON source.id=e.source_id
                        WHERE e.project_id=? AND e.layer=2 AND e.target_id=?
                          AND source.layer=2 AND source.kind='concept'
                        ORDER BY e.relation, source.label
                        """,
                        (project_id, concept["id"]),
                    )
                )
                if related or incoming:
                    lines.extend(["", "## 语义关系", ""])
                    for row in related:
                        path = concept_path_by_id.get(row["target_id"])
                        if path:
                            lines.append(
                                f"- **{row['relation']} →** {_wiki(path, row['target_label'])} "
                                f"（置信度 {row['confidence']:.2f}，{row['source']}）"
                            )
                    for row in incoming:
                        path = concept_path_by_id.get(row["source_id"])
                        if path:
                            lines.append(
                                f"- **← {row['relation']}** {_wiki(path, row['source_label'])} "
                                f"（置信度 {row['confidence']:.2f}，{row['source']}）"
                            )

                file_rows = list(
                    connection.execute(
                        """
                        SELECT e.relation, e.confidence, file.key, file.data_json
                        FROM edges e JOIN nodes file ON file.id=e.target_id
                        WHERE e.project_id=? AND e.layer=2 AND e.source_id=?
                          AND file.layer=1 AND file.kind='file'
                        ORDER BY e.relation, file.key
                        """,
                        (project_id, concept["id"]),
                    )
                )
                if file_rows:
                    lines.extend(["", "## 关联文件", ""])
                    for row in file_rows:
                        absolute = root / row["key"]
                        lines.append(
                            f"- **{row['relation']}** [{row['key']}]({_file_uri(absolute)})"
                        )

                task_links = list(
                    connection.execute(
                        """
                        SELECT DISTINCT t.*
                        FROM edges e
                        JOIN nodes task_node ON task_node.id=e.source_id AND task_node.kind='task'
                        JOIN tasks t ON t.id=task_node.key
                        WHERE e.project_id=? AND e.layer=3 AND e.relation='affects'
                          AND e.target_id=?
                        ORDER BY t.created_at DESC LIMIT 20
                        """,
                        (project_id, concept["id"]),
                    )
                )
                if task_links:
                    lines.extend(["", "## 相关任务", ""])
                    for task in task_links:
                        path = task_path_by_id.get(task["id"])
                        if path:
                            lines.append(f"- {_wiki(path, task['title'])} · {task['status']}")

                if data:
                    lines.extend(["", "## 派生信息", "", "```json"])
                    import json

                    lines.append(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
                    lines.append("```")

                _write(vault / f"{concept_path}.md", "\n".join(lines))
                files_written += 1

            for task in task_rows:
                task_path = task_path_by_id[task["id"]]
                task_node_id = Database.node_id(project_id, 3, "task", task["id"])
                lines = [
                    "---",
                    "agentnavi_type: task",
                    f"project: {project_id}",
                    f"task_id: {task['id']}",
                    f"status: {task['status']}",
                    f"agent: {task['agent']}",
                    f"created_at: {task['created_at']}",
                    f"closed_at: {task['closed_at'] or ''}",
                    "---",
                    "",
                    f"# {task['title']}",
                    "",
                    f"项目：{_wiki(project_path, project['name'])}",
                    "",
                    "## 原始任务",
                    "",
                    task["prompt"] or "（未记录）",
                ]
                if task["summary"]:
                    lines.extend(["", "## 执行结果", "", task["summary"]])

                affected = list(
                    connection.execute(
                        """
                        SELECT concept.id, concept.label
                        FROM edges e JOIN nodes concept ON concept.id=e.target_id
                        WHERE e.project_id=? AND e.layer=3 AND e.source_id=?
                          AND e.relation='affects' AND concept.kind='concept'
                        ORDER BY concept.label
                        """,
                        (project_id, task_node_id),
                    )
                )
                if affected:
                    lines.extend(["", "## 影响概念", ""])
                    for concept in affected:
                        path = concept_path_by_id.get(concept["id"])
                        if path:
                            lines.append(f"- {_wiki(path, concept['label'])}")

                touched = list(
                    connection.execute(
                        """
                        SELECT e.relation, file.key, e.data_json
                        FROM edges e JOIN nodes file ON file.id=e.target_id
                        WHERE e.project_id=? AND e.layer=3 AND e.source_id=?
                          AND file.kind='file'
                        ORDER BY e.relation, file.key
                        """,
                        (project_id, task_node_id),
                    )
                )
                if touched:
                    lines.extend(["", "## 文件轨迹", ""])
                    for row in touched:
                        absolute = root / row["key"]
                        edge_data = json_loads(row["data_json"], {})
                        count = edge_data.get("count", 1)
                        lines.append(
                            f"- **{row['relation']} ×{count}** [{row['key']}]({_file_uri(absolute)})"
                        )

                events = list(
                    connection.execute(
                        """
                        SELECT * FROM events WHERE task_id=? ORDER BY id
                        """,
                        (task["id"],),
                    )
                )
                if events:
                    lines.extend(["", "## 事件时间线", ""])
                    for event in events:
                        path_text = f" · `{event['path']}`" if event["path"] else ""
                        tool_text = f" · {event['tool_name']}" if event["tool_name"] else ""
                        lines.append(f"- {event['created_at']} · **{event['event_type']}**{tool_text}{path_text}")

                _write(vault / f"{task_path}.md", "\n".join(lines))
                files_written += 1

            project_lines = [
                "---",
                "agentnavi_type: project",
                f"project_id: {project_id}",
                f"kind: {project['kind']}",
                f"generated_at: {utc_now()}",
                "---",
                "",
                f"# {project['name']}",
                "",
                f"仓库：[{project['root']}]({_file_uri(root)})",
                f"最近索引：{project['last_scan_at'] or '尚未索引'}",
                "",
                "## 概念",
                "",
            ]
            project_lines.extend(
                f"- {_wiki(concept_path_by_id[row['id']], row['label'])}"
                for row in concept_rows
            )
            project_lines.extend(["", "## 最近任务", ""])
            project_lines.extend(
                f"- {_wiki(task_path_by_id[row['id']], row['title'])} · {row['created_at'][:10]} · {row['status']}"
                for row in task_rows[:50]
            )
            _write(vault / f"{project_path}.md", "\n".join(project_lines))
            files_written += 1

    index_lines = [
        "---",
        "agentnavi_type: index",
        f"generated_at: {utc_now()}",
        "---",
        "",
        "# AgentNavi",
        "",
        "> 这是 AgentNavi 从外部图谱数据库自动生成的 Obsidian 视图。可以删除并重新导出。",
        "",
        "## 项目",
        "",
    ]
    index_lines.extend(f"- {link}" for link in project_links)
    _write(vault / "AgentNavi" / "首页.md", "\n".join(index_lines))
    files_written += 1

    return ExportReport(
        destination=vault,
        projects=len(projects),
        concepts=total_concepts,
        tasks=total_tasks,
        files_written=files_written,
    )
