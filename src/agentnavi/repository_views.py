"""从仓库事实生成确定性的只读 Repository Understanding 数据。

本模块属于 Core：它只读取项目注册信息、SQLite 派生图和少量可信边界内的
项目文档，不依赖 MCP/UI，也不会把生成结果写回图谱。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .database import Database
from .utils import json_loads


MAX_DOCUMENTS = 6
MAX_DOCUMENT_BYTES = 48 * 1024
MAX_DOCUMENT_LINES = 400
MAX_TOTAL_DOCUMENT_BYTES = 128 * 1024
MAX_MODULES = 8
MAX_READING_ORDER = 7

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_ORDERED_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+?)\s*$")
_HTML_RE = re.compile(r"<[^>]*>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])"
)


@dataclass(frozen=True, slots=True)
class _Document:
    path: str
    lines: tuple[tuple[int, str], ...]
    truncated: bool


def _canonical_relative_path(value: str) -> bool:
    if (
        not value
        or value != value.strip()
        or "\\" in value
        or value.startswith(("/", "~"))
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
    ):
        return False
    path = PurePosixPath(value)
    return (
        str(path) == value
        and str(path) not in {"", "."}
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts)
    )


def _contains_posix_absolute(value: str) -> bool:
    for index, character in enumerate(value):
        if character != "/":
            continue
        if index == 0:
            return True
        previous = value[index - 1]
        if previous == ":" and index + 1 < len(value) and value[index + 1] == "/":
            continue
        if previous.isalnum() or previous in "._~-/":
            continue
        return True
    return False


def _safe_prose(value: str, *, limit: int = 320) -> str | None:
    """清理 Markdown 表面语法，并拒绝可能暴露本机路径的文档文本。"""

    text = _LINK_RE.sub(r"\1", value)
    text = _HTML_RE.sub("", text)
    text = re.sub(r"^\s*>\s?", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = " ".join(text.split()).strip(" -—:：")
    if not text or text in {"---", "***"}:
        return None
    lowered = text.lower()
    if (
        "file://" in lowered
        or "~/" in text
        or _WINDOWS_ABSOLUTE_RE.search(text)
        or _contains_posix_absolute(text)
    ):
        return None
    return text[:limit]


def _document_candidates(
    connection: sqlite3.Connection,
    project_id: str,
    root: Path,
) -> list[str]:
    candidates: set[str] = set()
    for fixed_path in (
        "README.md",
        "README.mdx",
        "README.rst",
        "README.txt",
        "docs/architecture.md",
        "docs/architecture.mdx",
        "docs/architecture.rst",
    ):
        if root.joinpath(*PurePosixPath(fixed_path).parts).exists():
            candidates.add(fixed_path)
    for row in connection.execute(
        """
        SELECT key FROM nodes
        WHERE project_id=? AND layer=1 AND kind='file'
        ORDER BY key COLLATE NOCASE, key
        """,
        (project_id,),
    ):
        path = str(row["key"])
        lowered = path.lower()
        name = PurePosixPath(path).name.lower()
        if (
            ("/" not in path and name in {"readme.md", "readme.mdx", "readme.rst", "readme.txt"})
            or lowered in {
                "docs/architecture.md",
                "docs/architecture.mdx",
                "docs/architecture.rst",
            }
            or lowered.startswith("docs/adr/")
            or lowered.startswith("docs/decisions/")
        ) and _canonical_relative_path(path):
            candidates.add(path)

    def priority(path: str) -> tuple[int, str, str]:
        lowered = path.lower()
        if PurePosixPath(path).name.lower().startswith("readme."):
            rank = 0
        elif lowered.startswith("docs/architecture."):
            rank = 1
        else:
            rank = 2
        return rank, lowered, path

    return sorted(candidates, key=priority)[:MAX_DOCUMENTS]


def _read_documents(
    root: Path,
    candidates: list[str],
) -> tuple[list[_Document], list[dict[str, Any]]]:
    documents: list[_Document] = []
    warnings: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return [], [
            {
                "code": "PROJECT_ROOT_UNAVAILABLE",
                "message": "项目目录当前不可读取，概览仅使用索引事实。",
                "evidence": [],
            }
        ]

    for relative in candidates:
        if total_bytes >= MAX_TOTAL_DOCUMENT_BYTES:
            warnings.append(
                {
                    "code": "DOCUMENT_BUDGET_REACHED",
                    "message": "项目文档读取达到总量上限，剩余文档未读取。",
                    "evidence": [],
                }
            )
            break
        candidate = resolved_root.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
            if not resolved.is_file():
                continue
            allowance = min(MAX_DOCUMENT_BYTES, MAX_TOTAL_DOCUMENT_BYTES - total_bytes)
            with resolved.open("rb") as handle:
                payload = handle.read(allowance + 1)
        except (OSError, RuntimeError, ValueError):
            warnings.append(
                {
                    "code": "DOCUMENT_OUTSIDE_PROJECT",
                    "message": f"已跳过不在项目边界内或不可读取的文档：{relative}",
                    "evidence": [],
                }
            )
            continue

        truncated = len(payload) > allowance
        payload = payload[:allowance]
        total_bytes += len(payload)
        decoded = payload.decode("utf-8", errors="replace")
        physical_lines = decoded.splitlines()
        if len(physical_lines) > MAX_DOCUMENT_LINES:
            truncated = True
            physical_lines = physical_lines[:MAX_DOCUMENT_LINES]

        visible_lines: list[tuple[int, str]] = []
        in_fence = False
        for number, raw_line in enumerate(physical_lines, start=1):
            if _FENCE_RE.match(raw_line):
                in_fence = not in_fence
                continue
            if not in_fence:
                visible_lines.append((number, raw_line))
        documents.append(_Document(relative, tuple(visible_lines), truncated))
        if truncated:
            warnings.append(
                {
                    "code": "DOCUMENT_TRUNCATED",
                    "message": f"文档按安全读取上限截断：{relative}",
                    "evidence": [
                        {
                            "kind": "document",
                            "summary": "文档读取达到上限。",
                            "layer": "L1",
                            "source": "repository-document",
                            "confidence": 1.0,
                            "path": relative,
                            "line_start": 1,
                        }
                    ],
                }
            )
    return documents, warnings


def _evidence(document: _Document, line: int, summary: str) -> dict[str, Any]:
    return {
        "kind": "document",
        "summary": summary,
        "layer": "L1",
        "source": "repository-document",
        "confidence": 1.0,
        "path": document.path,
        "line_start": line,
        "line_end": line,
    }


def _first_prose(
    documents: list[_Document],
    *,
    heading_terms: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]] | None:
    fallback: tuple[str, dict[str, Any]] | None = None
    for document in documents:
        matched_heading = not heading_terms
        for line_number, raw_line in document.lines:
            heading = _HEADING_RE.match(raw_line)
            if heading:
                title = " ".join(heading.group(1).lower().split())
                matched_heading = any(term in title for term in heading_terms)
                continue
            prose = _safe_prose(raw_line)
            if prose is None or _ORDERED_RE.match(raw_line):
                continue
            item = (prose, _evidence(document, line_number, "项目文档中的说明。"))
            if matched_heading:
                return item
            if fallback is None:
                fallback = item
    return None if heading_terms else fallback


def _workflow(documents: list[_Document]) -> list[dict[str, Any]]:
    groups: list[tuple[int, int, str, list[tuple[int, str]]]] = []
    for doc_index, document in enumerate(documents):
        current_heading = ""
        current: list[tuple[int, str]] = []
        for line_number, raw_line in document.lines:
            heading = _HEADING_RE.match(raw_line)
            if heading:
                if 5 <= len(current) <= 7:
                    groups.append((doc_index, 0, current_heading, current))
                current = []
                current_heading = heading.group(1).lower()
                continue
            ordered = _ORDERED_RE.match(raw_line)
            if ordered:
                prose = _safe_prose(ordered.group(1), limit=240)
                if prose:
                    current.append((line_number, prose))
                continue
            if raw_line.strip() and current:
                if 5 <= len(current) <= 7:
                    groups.append((doc_index, 0, current_heading, current))
                current = []
        if 5 <= len(current) <= 7:
            groups.append((doc_index, 0, current_heading, current))

    if not groups:
        return []
    preferred_terms = ("流程", "workflow", "route", "router", "查询", "工作")
    groups.sort(
        key=lambda item: (
            0 if any(term in item[2] for term in preferred_terms) else 1,
            item[0],
            item[3][0][0],
        )
    )
    document = documents[groups[0][0]]
    return [
        {
            "step": index,
            "title": text,
            "detail": text,
            "evidence": [_evidence(document, line, "项目文档中的工作流步骤。")],
        }
        for index, (line, text) in enumerate(groups[0][3], start=1)
    ]


def _modules(connection: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    rows = list(
        connection.execute(
            """
            SELECT n.id, n.key, n.label, n.data_json, n.confidence, n.source,
                   COUNT(DISTINCT f.id) AS linked_files
            FROM nodes n
            LEFT JOIN edges e
              ON e.project_id=n.project_id AND e.layer=2 AND e.source_id=n.id
            LEFT JOIN nodes f
              ON f.id=e.target_id AND f.layer=1 AND f.kind='file'
            WHERE n.project_id=? AND n.layer=2 AND n.kind='concept'
              AND n.data_json NOT LIKE '%"active":false%'
            GROUP BY n.id
            ORDER BY linked_files DESC, n.label COLLATE NOCASE, n.key, n.id
            """,
            (project_id,),
        )
    )[:MAX_MODULES]
    result: list[dict[str, Any]] = []
    for row in rows:
        paths = [
            str(path_row["key"])
            for path_row in connection.execute(
                """
                SELECT f.key FROM edges e JOIN nodes f ON f.id=e.target_id
                WHERE e.project_id=? AND e.layer=2 AND e.source_id=?
                  AND f.layer=1 AND f.kind='file'
                ORDER BY f.key COLLATE NOCASE, f.key
                LIMIT 3
                """,
                (project_id, row["id"]),
            )
            if _canonical_relative_path(str(path_row["key"]))
        ]
        data = json_loads(row["data_json"], {})
        count = int(row["linked_files"] or data.get("file_count") or 0)
        evidence = [
            {
                "kind": "graph",
                "summary": f"语义概念关联 {count} 个文件。",
                "layer": "L2",
                "source": str(row["source"]),
                "confidence": float(row["confidence"]),
                **({"path": paths[0]} if paths else {}),
            }
        ]
        result.append(
            {
                "id": str(row["id"]),
                "name": str(row["label"])[:160],
                "summary": f"关联 {count} 个仓库文件。",
                "paths": paths,
                "layer": "L2",
                "source": str(row["source"])[:120],
                "confidence": float(row["confidence"]),
                "evidence": evidence,
            }
        )
    return result


def _reading_order(
    connection: sqlite3.Connection,
    project_id: str,
    documents: list[_Document],
) -> list[dict[str, Any]]:
    selected: list[tuple[str, str]] = []
    for document in documents:
        reason = (
            "先建立项目目的与使用方式。"
            if PurePosixPath(document.path).name.lower().startswith("readme.")
            else "理解项目架构与关键设计决定。"
        )
        selected.append((document.path, reason))

    rows = list(
        connection.execute(
            """
            SELECT key FROM nodes
            WHERE project_id=? AND layer=1 AND kind='file'
            ORDER BY key COLLATE NOCASE, key
            """,
            (project_id,),
        )
    )
    paths = [str(row["key"]) for row in rows if _canonical_relative_path(str(row["key"]))]
    entry_patterns = (
        re.compile(r"^src/[^/]+/__main__\.py$"),
        re.compile(r"^src/[^/]+/cli\.py$"),
        re.compile(r"^(?:pyproject\.toml|package\.json|go\.mod|Cargo\.toml)$"),
        re.compile(r"^(?:tests?|specs?)/"),
    )
    reasons = (
        "从真实运行入口继续阅读。",
        "了解公开命令和主要调用链。",
        "确认构建、依赖与公开入口。",
        "用测试验证关键行为与边界。",
    )
    for pattern, reason in zip(entry_patterns, reasons):
        for path in paths:
            if pattern.search(path) and path not in {item[0] for item in selected}:
                selected.append((path, reason))
                break

    return [
        {
            "position": index,
            "path": path,
            "reason": reason,
            "evidence": [
                {
                    "kind": "repository-file",
                    "summary": "索引中的仓库文件。",
                    "layer": "L1",
                    "source": "repository-index",
                    "confidence": 1.0,
                    "path": path,
                }
            ],
        }
        for index, (path, reason) in enumerate(selected[:MAX_READING_ORDER], start=1)
    ]


def repository_overview_data(
    database: Database,
    project: sqlite3.Row,
) -> dict[str, Any]:
    """生成 Repository Overview Core 数据；全过程只读且先排序后截断。"""

    project_id = str(project["id"])
    with database.connect() as connection:
        document_paths = _document_candidates(
            connection,
            project_id,
            Path(project["root"]),
        )
        documents, warnings = _read_documents(Path(project["root"]), document_paths)
        purpose = _first_prose(
            documents,
            heading_terms=("一句话", "purpose", "定位", "是什么", "目标"),
        )
        problem = _first_prose(
            documents,
            heading_terms=("问题", "problem"),
        )
        explicit_solution = _first_prose(
            documents,
            heading_terms=("解决", "solution", "方案"),
        )
        solution = explicit_solution or purpose
        workflow = _workflow(documents)
        modules = _modules(connection, project_id)
        reading_order = _reading_order(connection, project_id, documents)
        stats = {
            "files": connection.execute(
                "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=1 AND kind='file'",
                (project_id,),
            ).fetchone()["count"],
            "concepts": connection.execute(
                """SELECT COUNT(*) AS count FROM nodes
                   WHERE project_id=? AND layer=2 AND kind='concept'
                     AND data_json NOT LIKE '%"active":false%'""",
                (project_id,),
            ).fetchone()["count"],
            "tasks": connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE project_id=?",
                (project_id,),
            ).fetchone()["count"],
            "documentsRead": len(documents),
        }

    if purpose is None:
        warnings.append(
            {
                "code": "PURPOSE_EVIDENCE_MISSING",
                "message": "未找到足够的项目目的文档证据，未生成推测性说明。",
                "evidence": [],
            }
        )
    if problem is None or solution is None:
        warnings.append(
            {
                "code": "NEED_EVIDENCE_MISSING",
                "message": "项目问题或解决方式证据不足，缺失部分保持为空。",
                "evidence": [],
            }
        )
    if not workflow:
        warnings.append(
            {
                "code": "WORKFLOW_EVIDENCE_MISSING",
                "message": "未找到有证据的 5–7 步主流程，未生成虚构步骤。",
                "evidence": [],
            }
        )

    return {
        "project": {
            "id": project_id,
            "name": str(project["name"]),
            "root": str(project["root"]),
            "kind": str(project["kind"]),
            "last_scan_at": project["last_scan_at"],
        },
        "purpose": (
            {"summary": purpose[0], "evidence": [purpose[1]]}
            if purpose is not None
            else {"summary": "", "evidence": []}
        ),
        "need": {
            "problem": (
                {"summary": problem[0], "evidence": [problem[1]]}
                if problem is not None
                else {"summary": "", "evidence": []}
            ),
            "solution": (
                {"summary": solution[0], "evidence": [solution[1]]}
                if solution is not None
                else {"summary": "", "evidence": []}
            ),
        },
        "workflow": workflow,
        "modules": modules,
        "readingOrder": reading_order,
        "stats": stats,
        "warnings": sorted(warnings, key=lambda item: (item["code"], item["message"])),
    }


__all__ = [
    "MAX_DOCUMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_DOCUMENT_LINES",
    "MAX_TOTAL_DOCUMENT_BYTES",
    "repository_overview_data",
]
