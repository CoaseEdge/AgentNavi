"""从仓库事实生成确定性的只读 Repository Understanding 数据。

本模块属于 Core：它只读取项目注册信息、SQLite 派生图和少量可信边界内的
项目文档，不依赖 MCP/UI，也不会把生成结果写回图谱。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tomllib
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .database import Database
from .privacy import contains_private_path, is_canonical_relative_path
from .utils import json_loads, stable_id


MAX_DOCUMENTS = 6
MAX_DOCUMENT_BYTES = 48 * 1024
MAX_DOCUMENT_LINES = 400
MAX_TOTAL_DOCUMENT_BYTES = 128 * 1024
MAX_MODULES = 8
MAX_READING_ORDER = 7
MAX_FRESHNESS_PATHS = 48
MAX_FRESHNESS_FILE_BYTES = 256 * 1024
MAX_TOTAL_FRESHNESS_BYTES = 1024 * 1024
TOUR_HISTORY_PAGE_SIZE = 32
MAX_TOUR_HISTORY_PAGES = 5
MAX_TOUR_HISTORY_CANDIDATES = TOUR_HISTORY_PAGE_SIZE * MAX_TOUR_HISTORY_PAGES
MAX_TOUR_HISTORY_SQL_QUERIES = MAX_TOUR_HISTORY_PAGES * 2
MAX_ARCHITECTURE_COMPONENTS = 8
MAX_ARCHITECTURE_CONNECTIONS = 12
MAX_ARCHITECTURE_ENTRY_POINTS = 3
MAX_FLOW_FILES = 18
MAX_STRUCTURE_MAPPING_CANDIDATES = 48
MAX_STRUCTURE_MAPPING_QUERIES = 1
MAX_ARCHITECTURE_RAW_EVIDENCE_CANDIDATES = 64
MAX_ARCHITECTURE_PHYSICAL_EDGE_QUERIES = 1
_STRUCTURE_FILE_RELATIONS = (
    "implemented_by", "tested_by", "documented_by", "configured_by",
)

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_ORDERED_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+?)\s*$")
_HTML_RE = re.compile(r"<[^>]*>")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_FIXED_DOCUMENT_PATHS = (
    "README.md",
    "README.mdx",
    "README.rst",
    "README.txt",
    "docs/architecture.md",
    "docs/architecture.mdx",
    "docs/architecture.rst",
)


@dataclass(frozen=True, slots=True)
class _Document:
    path: str
    lines: tuple[tuple[int, str], ...]
    truncated: bool


@dataclass(slots=True)
class _FreshnessBudget:
    """限制用于核对索引快照的磁盘读取量，与展示内容预算相互独立。"""

    remaining: int

    def claim(self, size: int) -> bool:
        if size < 0 or size > MAX_FRESHNESS_FILE_BYTES or size > self.remaining:
            return False
        self.remaining -= size
        return True


def _safe_prose(value: str, *, limit: int = 320) -> str | None:
    """清理 Markdown 表面语法，并拒绝可能暴露本机路径的文档文本。"""

    text = _LINK_RE.sub(r"\1", value)
    text = _HTML_RE.sub("", text)
    text = re.sub(r"^\s*>\s?", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = " ".join(text.split()).strip(" -—:：")
    if not text or text in {"---", "***"}:
        return None
    if contains_private_path(text):
        return None
    return text[:limit]


def _document_candidates(
    connection: sqlite3.Connection,
    project_id: str,
) -> list[str]:
    candidates: set[str] = set()
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
        ) and is_canonical_relative_path(path):
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


def _snapshot_revision(
    project_id: str,
    last_scan_at: str | None,
    states: dict[str, sqlite3.Row],
) -> str | None:
    if last_scan_at is None:
        return None
    parts = [
        f"{path}:{row['size']}:{row['mtime_ns']}:{row['digest']}"
        for path, row in sorted(states.items())
    ]
    return stable_id(project_id, last_scan_at, *parts, prefix="snapshot_")


def _read_snapshot_file(
    path: Path,
    state: sqlite3.Row | None,
    *,
    cache_limit: int,
    budget: _FreshnessBudget,
) -> bytes | None:
    """在 freshness I/O 预算内完成 digest 校验，并仅缓存所需前缀。"""

    if state is None or not str(state["digest"]):
        return None
    hasher = hashlib.blake2s()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                before.st_size != state["size"]
                or before.st_mtime_ns != state["mtime_ns"]
            ):
                return None
            if not budget.claim(before.st_size):
                return None
            cached = bytearray()
            remaining_bytes = before.st_size
            while remaining_bytes:
                chunk = handle.read(min(64 * 1024, remaining_bytes))
                if not chunk:
                    return None
                hasher.update(chunk)
                remaining = cache_limit + 1 - len(cached)
                if remaining > 0:
                    cached.extend(chunk[:remaining])
                remaining_bytes -= len(chunk)
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError:
        return None
    if (
        before.st_dev != after.st_dev
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
        or current.st_size != after.st_size
        or current.st_mtime_ns != after.st_mtime_ns
        or after.st_size != state["size"]
        or after.st_mtime_ns != state["mtime_ns"]
        or hasher.hexdigest() != str(state["digest"])
    ):
        return None
    return bytes(cached)


def _resolved_project_file(resolved_root: Path, relative: str) -> Path:
    candidate = resolved_root.joinpath(*PurePosixPath(relative).parts)
    resolved = candidate.resolve(strict=True)
    resolved.relative_to(resolved_root)
    if not resolved.is_file():
        raise OSError
    return resolved


def _fresh_path(
    resolved_root: Path,
    relative: str,
    states: dict[str, sqlite3.Row],
    freshness: dict[str, bool],
    budget: _FreshnessBudget,
) -> bool:
    if relative in freshness:
        return freshness[relative]
    try:
        resolved = _resolved_project_file(resolved_root, relative)
        fresh = _read_snapshot_file(
            resolved,
            states.get(relative),
            cache_limit=0,
            budget=budget,
        ) is not None
    except (OSError, RuntimeError, ValueError):
        fresh = False
    freshness[relative] = fresh
    return fresh


def _read_documents(
    root: Path,
    candidates: list[str],
    states: dict[str, sqlite3.Row],
    budget: _FreshnessBudget,
) -> tuple[list[_Document], list[dict[str, Any]], bool, dict[str, bool]]:
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
        ], True, {}

    freshness: dict[str, bool] = {}

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
        try:
            resolved = _resolved_project_file(resolved_root, relative)
            allowance = min(MAX_DOCUMENT_BYTES, MAX_TOTAL_DOCUMENT_BYTES - total_bytes)
            payload = _read_snapshot_file(
                resolved,
                states.get(relative),
                cache_limit=allowance,
                budget=budget,
            )
            if payload is None:
                freshness[relative] = False
                warnings.append(
                    {
                        "code": "SOURCE_SNAPSHOT_STALE",
                        "message": f"索引后文档已变化，已跳过：{relative}",
                        "evidence": [],
                    }
                )
                continue
            freshness[relative] = True
        except (RuntimeError, ValueError):
            freshness[relative] = False
            warnings.append(
                {
                    "code": "DOCUMENT_OUTSIDE_PROJECT",
                    "message": f"已跳过不在项目边界内或不可读取的文档：{relative}",
                    "evidence": [],
                }
            )
            continue
        except OSError:
            freshness[relative] = False
            warnings.append(
                {
                    "code": "SOURCE_SNAPSHOT_STALE",
                    "message": f"索引中的文档已删除或不可读取，已跳过：{relative}",
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
    return documents, warnings, any(
        warning["code"] in {"SOURCE_SNAPSHOT_STALE", "DOCUMENT_OUTSIDE_PROJECT"}
        for warning in warnings
    ), freshness


def _evidence(
    document: _Document,
    line: int,
    summary: str,
    *,
    line_end: int | None = None,
) -> dict[str, Any]:
    return {
        "kind": "document",
        "summary": summary,
        "layer": "L1",
        "source": "repository-document",
        "confidence": 1.0,
        "path": document.path,
        "line_start": line,
        "line_end": line_end or line,
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


def _workflow(documents: list[_Document]) -> tuple[list[dict[str, Any]], bool]:
    groups: list[tuple[int, str, list[tuple[int, int, str, str]]]] = []
    for doc_index, document in enumerate(documents):
        current_heading = ""
        current: list[tuple[int, int, str, str]] = []
        for line_number, raw_line in document.lines:
            heading = _HEADING_RE.match(raw_line)
            if heading:
                if len(current) >= 5:
                    groups.append((doc_index, current_heading, current))
                current = []
                current_heading = heading.group(1).lower()
                continue
            ordered = _ORDERED_RE.match(raw_line)
            if ordered:
                prose = _safe_prose(ordered.group(1), limit=240)
                if prose:
                    current.append((line_number, line_number, prose, prose))
                continue
            if current and raw_line.startswith(("  ", "\t")):
                continuation = _safe_prose(raw_line, limit=240)
                if continuation:
                    line, _, title, detail = current[-1]
                    current[-1] = (
                        line,
                        line_number,
                        title,
                        f"{detail} {continuation}"[:320],
                    )
                continue
            if raw_line.strip() and current:
                if len(current) >= 5:
                    groups.append((doc_index, current_heading, current))
                current = []
        if len(current) >= 5:
            groups.append((doc_index, current_heading, current))

    if not groups:
        return [], False
    preferred_terms = ("流程", "workflow", "route", "router", "查询", "工作")
    groups.sort(
        key=lambda item: (
            0 if any(term in item[1] for term in preferred_terms) else 1,
            item[0],
            item[2][0][0],
        )
    )
    document = documents[groups[0][0]]
    selected = groups[0][2]
    return [
        {
            "step": index,
            "title": title,
            "detail": detail,
            "evidence": [
                _evidence(
                    document,
                    line,
                    "项目文档中的工作流步骤。",
                    line_end=line_end,
                )
            ],
        }
        for index, (line, line_end, title, detail) in enumerate(selected[:7], start=1)
    ], len(selected) > 7


def _execution_workflow(
    documents: list[_Document],
) -> tuple[list[dict[str, Any]], bool]:
    """只接受明示的运行主流程，避免把安装或阅读清单当成执行链。"""

    execution_terms = (
        "主流程", "执行流程", "任务流程", "请求流程", "数据流", "运行流程",
        "main flow", "execution flow", "task flow", "request flow", "data flow",
        "runtime flow", "processing flow", "workflow",
    )
    excluded_terms = (
        "事件", "生命周期", "安装", "设置", "发布", "迁移", "清单", "检查表",
        "持续集成", "流水线", "event", "lifecycle", "installation", "install",
        "setup", "release", "migration", "checklist", "continuous integration",
        "ci workflow", "ci flow", "pipeline",
    )
    groups: list[tuple[int, int, list[tuple[int, int, str, str]]]] = []
    for doc_index, document in enumerate(documents):
        path = document.path.lower()
        if not (
            PurePosixPath(path).name.startswith("readme")
            or path in {"docs/architecture.md", "docs/architecture.mdx", "docs/architecture.rst"}
        ):
            continue
        accepted_heading = False
        group_start = 0
        current: list[tuple[int, int, str, str]] = []
        for line_number, raw_line in document.lines:
            heading = _HEADING_RE.match(raw_line)
            if heading:
                if accepted_heading and len(current) >= 5:
                    groups.append((doc_index, group_start, current))
                title = " ".join(
                    re.sub(r"[^\w\u4e00-\u9fff]+", " ", heading.group(1).lower()).split()
                )
                compact_title = title.replace(" ", "")
                ci_heading = (
                    re.search(r"\bci(?: cd)?\b", title) is not None
                    or re.search(r"\bc i(?: c d)?\b", title) is not None
                    or re.search(r"\bcicd\b", title) is not None
                )
                accepted_heading = (
                    any(
                        term in title or term.replace(" ", "") in compact_title
                        for term in execution_terms
                    )
                    and not ci_heading
                    and not any(
                        term in title or term.replace(" ", "") in compact_title
                        for term in excluded_terms
                    )
                )
                group_start = line_number
                current = []
                continue
            ordered = _ORDERED_RE.match(raw_line)
            if accepted_heading and ordered:
                prose = _safe_prose(ordered.group(1), limit=240)
                if prose:
                    current.append((line_number, line_number, prose, prose))
                continue
            if accepted_heading and current and raw_line.startswith(("  ", "\t")):
                continuation = _safe_prose(raw_line, limit=240)
                if continuation:
                    line, _, title, detail = current[-1]
                    current[-1] = (
                        line, line_number, title, f"{detail} {continuation}"[:320],
                    )
                continue
            if accepted_heading and current and raw_line.strip():
                if len(current) >= 5:
                    groups.append((doc_index, group_start, current))
                current = []
        if accepted_heading and len(current) >= 5:
            groups.append((doc_index, group_start, current))

    if not groups:
        return [], False
    groups.sort(key=lambda item: (item[0], item[1]))
    document = documents[groups[0][0]]
    selected = groups[0][2]
    steps = [
        {
            "step": index,
            "title": title,
            "detail": detail,
            "evidence": [
                _evidence(
                    document, line, "项目文档明示的执行流程步骤。",
                    line_end=line_end,
                )
            ],
        }
        for index, (line, line_end, title, detail) in enumerate(selected[:7], start=1)
    ]
    return steps, len(selected) > 7


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
                """,
                (project_id, row["id"]),
            )
            if is_canonical_relative_path(str(path_row["key"]))
        ][:3]
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


def _file_evidence(
    path: str,
    summary: str,
    *,
    line: int | None = None,
) -> dict[str, Any]:
    evidence = {
        "kind": "repository-file",
        "summary": summary,
        "layer": "L1",
        "source": "repository-index",
        "confidence": 1.0,
        "path": path,
    }
    if line is not None:
        evidence.update({"line_start": line, "line_end": line})
    return evidence


def _project_script_line(text: str, key: str) -> int | None:
    in_scripts = False
    for number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_scripts = stripped == "[project.scripts]"
            continue
        if not in_scripts or not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        candidate = stripped.split("=", 1)[0].strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}:
            candidate = candidate[1:-1]
        if candidate == key:
            return number
    return None


def _manifest_entry(
    root: Path,
    paths: list[str],
    states: dict[str, sqlite3.Row],
    freshness: dict[str, bool],
    budget: _FreshnessBudget,
) -> tuple[str | None, tuple[str, str, dict[str, Any]] | None]:
    def read_manifest(relative: str) -> tuple[bytes, Path] | None:
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        try:
            resolved_root = root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
            if not resolved.is_file():
                return None
            payload = _read_snapshot_file(
                resolved,
                states.get(relative),
                cache_limit=MAX_DOCUMENT_BYTES,
                budget=budget,
            )
        except (OSError, RuntimeError, ValueError):
            freshness[relative] = False
            return None
        freshness[relative] = payload is not None
        if payload is None or len(payload) > MAX_DOCUMENT_BYTES:
            return None
        return payload, resolved

    path_set = set(paths)
    if "pyproject.toml" in path_set:
        manifest_data = read_manifest("pyproject.toml")
        if manifest_data is not None:
            try:
                text = manifest_data[0].decode("utf-8")
                project = tomllib.loads(text).get("project", {})
                scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
                if isinstance(scripts, dict):
                    for name, value in sorted(scripts.items()):
                        if not isinstance(name, str) or not isinstance(value, str):
                            continue
                        module = value.split(":", 1)[0].strip()
                        candidates = (
                            f"src/{module.replace('.', '/')}.py",
                            f"{module.replace('.', '/')}.py",
                        )
                        for candidate in candidates:
                            if candidate not in path_set:
                                continue
                            line = _project_script_line(text, name)
                            return "pyproject.toml", (
                                candidate,
                                f"从 manifest 声明的公开命令 {name} 进入调用链。",
                                _file_evidence(
                                    "pyproject.toml",
                                    "manifest 中的公开命令声明。",
                                    line=line,
                                ),
                            )
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                pass
        return "pyproject.toml", None

    if "package.json" in path_set:
        manifest_data = read_manifest("package.json")
        if manifest_data is not None:
            try:
                package = json.loads(manifest_data[0].decode("utf-8"))
                values: list[str] = []
                if isinstance(package, dict):
                    for field in ("bin", "main", "module"):
                        value = package.get(field)
                        if isinstance(value, str):
                            values.append(value)
                        elif isinstance(value, dict):
                            values.extend(
                                item for item in value.values() if isinstance(item, str)
                            )
                for candidate in sorted(set(values)):
                    normalized = candidate.removeprefix("./")
                    if is_canonical_relative_path(normalized) and normalized in path_set:
                        return "package.json", (
                            normalized,
                            "从 package manifest 声明的公开入口进入调用链。",
                            _file_evidence(
                                "package.json", "package manifest 中的入口声明。"
                            ),
                        )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        return "package.json", None
    return None, None


def _reading_order(
    connection: sqlite3.Connection,
    project_id: str,
    documents: list[_Document],
    root: Path,
    states: dict[str, sqlite3.Row],
    modules: list[dict[str, Any]],
    freshness: dict[str, bool],
    budget: _FreshnessBudget,
) -> list[dict[str, Any]]:
    rows = list(
        connection.execute(
            """SELECT key FROM nodes
               WHERE project_id=? AND layer=1 AND kind='file'
               ORDER BY key COLLATE NOCASE, key""",
            (project_id,),
        )
    )
    paths = [
        str(row["key"])
        for row in rows
        if is_canonical_relative_path(str(row["key"]))
    ]
    selected: list[tuple[str, str, dict[str, Any]]] = []

    def add(path: str, reason: str, evidence: dict[str, Any] | None = None) -> None:
        if path in {item[0] for item in selected} or path not in paths:
            return
        selected.append(
            (
                path,
                reason,
                evidence or _file_evidence(path, "索引中的仓库文件。"),
            )
        )

    read_documents = {document.path for document in documents}
    readme = next(
        (path for path in paths if "/" not in path and PurePosixPath(path).name.lower().startswith("readme.")),
        None,
    )
    if readme in read_documents:
        add(readme, "先建立项目目的与使用方式。")
    architecture = next(
        (path for path in paths if path.lower().startswith("docs/architecture.")),
        None,
    )
    if architecture in read_documents:
        add(architecture, "理解项目架构与主流程。")

    manifest, entry = _manifest_entry(root, paths, states, freshness, budget)
    if manifest is not None:
        add(manifest, "确认构建配置、依赖与公开命令。")
    if entry is not None:
        add(*entry)
    else:
        fallback = next(
            (
                path
                for pattern in (
                    re.compile(r"^src/[^/]+/__main__\.py$"),
                    re.compile(r"^src/[^/]+/cli\.py$"),
                )
                for path in paths
                if pattern.search(path)
            ),
            None,
        )
        if fallback:
            add(fallback, "按文件名识别的运行入口（fallback），需结合 manifest 核对。")

    core_path = next(
        (
            path
            for module in modules
            for path in module["paths"]
            if path.startswith(("src/", "lib/", "app/"))
            and path not in {item[0] for item in selected}
        ),
        None,
    )
    if core_path:
        add(core_path, "阅读核心语义模块的实现。")
    test_path = next(
        (path for path in paths if path.startswith(("test/", "tests/", "spec/", "specs/"))),
        None,
    )
    if test_path:
        add(test_path, "用测试核对关键行为与边界。")
    adr = next(
        (
            document.path
            for document in documents
            if document.path.startswith(("docs/adr/", "docs/decisions/"))
        ),
        None,
    )
    if adr:
        add(adr, "补充理解首个关键架构决定。")

    return [
        {
            "position": index,
            "path": path,
            "reason": reason,
            "evidence": [evidence],
        }
        for index, (path, reason, evidence) in enumerate(
            selected[:MAX_READING_ORDER], start=1
        )
    ]


def _filter_fresh_output_paths(
    root: Path,
    states: dict[str, sqlite3.Row],
    freshness: dict[str, bool],
    modules: list[dict[str, Any]],
    reading_order: list[dict[str, Any]],
    budget: _FreshnessBudget,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """只核对最终会展示的有界路径集合，并剔除失效路径证据。"""

    references = {
        path
        for module in modules
        for path in module["paths"]
    }
    references.update(item["path"] for item in reading_order)
    for item in [*modules, *reading_order]:
        references.update(
            evidence["path"]
            for evidence in item["evidence"]
            if isinstance(evidence.get("path"), str)
        )

    ordered_references = sorted(references)
    inspectable = set(ordered_references[:MAX_FRESHNESS_PATHS])
    try:
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved_root = root
        inspectable = set()
    for relative in ordered_references:
        if relative not in inspectable:
            freshness[relative] = False
        else:
            _fresh_path(resolved_root, relative, states, freshness, budget)

    stale_paths = [path for path in ordered_references if not freshness.get(path, False)]
    filtered_modules: list[dict[str, Any]] = []
    for module in modules:
        paths = [path for path in module["paths"] if freshness.get(path, False)]
        if not paths:
            continue
        count = len(paths)
        item = {
            **module,
            "summary": f"关联 {count} 个仓库文件。",
            "paths": paths,
            "evidence": [
                {
                    "kind": "graph",
                    "summary": f"语义概念关联 {count} 个已核对文件。",
                    "layer": "L2",
                    "source": module["source"],
                    "confidence": module["confidence"],
                    "path": paths[0],
                }
            ],
        }
        filtered_modules.append(item)

    filtered_reading = [
        dict(item)
        for item in reading_order
        if freshness.get(item["path"], False)
        and all(
            not isinstance(evidence.get("path"), str)
            or freshness.get(evidence["path"], False)
            for evidence in item["evidence"]
        )
    ]
    for position, item in enumerate(filtered_reading, start=1):
        item["position"] = position

    warnings = [
        {
            "code": "SOURCE_SNAPSHOT_STALE",
            "message": f"索引后输出路径已变化或不可验证，已剔除：{path}",
            "evidence": [],
        }
        for path in stale_paths
    ]
    return filtered_modules, filtered_reading, warnings, bool(stale_paths)


def _repository_overview_data(
    database: Database,
    project: sqlite3.Row,
    *,
    connection: sqlite3.Connection | None = None,
    snapshot_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成 Repository Overview Core 数据；全过程只读且先排序后截断。"""

    project_id = str(project["id"])
    manager = database.connect() if connection is None else nullcontext(connection)
    with manager as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN")
        snapshot_project = connection.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if snapshot_project is None:
            raise LookupError("项目快照不存在。")
        root = Path(snapshot_project["root"])
        last_scan_at = snapshot_project["last_scan_at"]
        states = {
            str(row["path"]): row
            for row in connection.execute(
                "SELECT * FROM file_state WHERE project_id=? ORDER BY path",
                (project_id,),
            )
        }
        revision = _snapshot_revision(project_id, last_scan_at, states)
        document_paths = _document_candidates(connection, project_id)
        warnings: list[dict[str, Any]] = []
        snapshot_stale = False
        freshness: dict[str, bool] = {}
        freshness_budget = _FreshnessBudget(remaining=MAX_TOTAL_FRESHNESS_BYTES)
        if last_scan_at is None:
            documents: list[_Document] = []
        else:
            documents, document_warnings, snapshot_stale, freshness = _read_documents(
                root,
                document_paths,
                states,
                freshness_budget,
            )
            warnings.extend(document_warnings)
            for fixed_path in _FIXED_DOCUMENT_PATHS:
                if fixed_path in states:
                    continue
                try:
                    exists = root.joinpath(*PurePosixPath(fixed_path).parts).exists()
                except OSError:
                    exists = False
                if exists:
                    snapshot_stale = True
                    warnings.append(
                        {
                            "code": "SOURCE_SNAPSHOT_STALE",
                            "message": "扫描后发现新的概览文档，需重新扫描后再使用其证据。",
                            "evidence": [],
                        }
                    )
                    break

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
        solution = explicit_solution
        workflow, workflow_truncated = _workflow(documents)
        modules = _modules(connection, project_id)
        reading_order = _reading_order(
            connection,
            project_id,
            documents,
            root,
            states,
            modules,
            freshness,
            freshness_budget,
        )
        if last_scan_at is not None:
            modules, reading_order, path_warnings, paths_stale = (
                _filter_fresh_output_paths(
                    root,
                    states,
                    freshness,
                    modules,
                    reading_order,
                    freshness_budget,
                )
            )
            warnings.extend(path_warnings)
            snapshot_stale = snapshot_stale or paths_stale
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

        source_status = (
            "partial"
            if last_scan_at is None
            else "stale"
            if snapshot_stale
            else "ready"
        )
        if snapshot_context is not None:
            snapshot_context.update(
                {
                    "documents": documents,
                    "freshness": dict(freshness),
                    "root": root,
                    "states": states,
                    "freshnessBudget": freshness_budget,
                }
            )

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
    if workflow_truncated:
        warnings.append(
            {
                "code": "WORKFLOW_TRUNCATED",
                "message": "文档流程超过 7 步，已按稳定顺序展示前 7 步。",
                "evidence": [],
            }
        )

    return {
        "project": {
            "id": project_id,
            "name": str(snapshot_project["name"]),
            "root": str(snapshot_project["root"]),
            "kind": str(snapshot_project["kind"]),
            "last_scan_at": last_scan_at,
        },
        "sourceState": {
            "status": source_status,
            "revision": revision,
            "indexed_at": last_scan_at,
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


def repository_overview_data(
    database: Database,
    project: sqlite3.Row,
) -> dict[str, Any]:
    return _repository_overview_data(database, project)


_TOUR_DEPTHS = (
    ("one-minute", "1 分钟", 4),
    ("five-minutes", "5 分钟", 8),
    ("source-deep-dive", "深入源码", 12),
)


def _tour_stop(
    stop_id: str,
    kind: str,
    title: str,
    plain_language: str,
    technical_explanation: str,
    evidence: list[dict[str, Any]],
    *,
    entity: dict[str, Any] | None = None,
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """只有可追溯证据完整时才构造 Tour stop。"""

    if not plain_language or not technical_explanation or not evidence:
        return None
    return {
        "id": stop_id,
        "kind": kind,
        "title": title,
        "plainLanguage": plain_language,
        "technicalExplanation": technical_explanation,
        "evidence": evidence,
        "entity": entity,
        "relations": relations or [],
    }


def _tour_entity(
    entity_id: str,
    kind: str,
    label: str,
    evidence: list[dict[str, Any]],
    *,
    path: str | None = None,
    layer: str | None = None,
    source: str | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    first = evidence[0]
    return {
        "id": entity_id,
        "kind": kind,
        "label": label,
        "path": path,
        "layer": layer or first["layer"],
        "source": source or first["source"],
        "confidence": confidence if confidence is not None else first["confidence"],
        "evidence": evidence,
    }


def _tour_graph_facts(
    connection: sqlite3.Connection,
    project_id: str,
    fresh_paths: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """从已通过 Overview freshness 核对的路径中有界读取 Tour 图事实。"""

    if not fresh_paths:
        return {"symbols": [], "data_structures": [], "dependencies": [], "tests": []}
    ordered_paths = sorted(fresh_paths)
    placeholders = ",".join("?" for _ in ordered_paths)
    symbols: list[dict[str, Any]] = []
    data_structures: list[dict[str, Any]] = []
    for row in connection.execute(
        f"""
        SELECT resource.id, resource.label, resource.data_json,
               resource.confidence, resource.source, parent.key AS parent_path
        FROM nodes resource
        JOIN edges containment
          ON containment.project_id=resource.project_id
         AND containment.layer=1 AND containment.relation='contains'
         AND containment.target_id=resource.id
        JOIN nodes parent
          ON parent.id=containment.source_id AND parent.layer=1 AND parent.kind='file'
        WHERE resource.project_id=? AND resource.layer=1 AND resource.kind='symbol'
          AND parent.key IN ({placeholders})
        ORDER BY CASE WHEN json_extract(resource.data_json, '$.symbol_kind') IN
                      ('class', 'type', 'interface', 'struct', 'record', 'enum',
                       'trait', 'protocol', 'object', 'module') THEN 0 ELSE 1 END,
                 parent.key COLLATE NOCASE, parent.key,
                 resource.label COLLATE NOCASE, resource.key, resource.id
        LIMIT 24
        """,
        (project_id, *ordered_paths),
    ):
        path = str(row["parent_path"])
        if path not in fresh_paths:
            continue
        data = json_loads(row["data_json"], {})
        symbol_kind = str(data.get("symbol_kind") or "symbol")
        evidence = [_file_evidence(path, "文件索引中的源码符号。")]
        item = {
            "id": str(row["id"]),
            "label": str(row["label"])[:160],
            "symbol_kind": symbol_kind,
            "path": path,
            "source": str(row["source"])[:120],
            "confidence": float(row["confidence"]),
            "evidence": evidence,
        }
        symbols.append(item)
        if symbol_kind in {
            "class", "type", "interface", "struct", "record", "enum",
            "trait", "protocol", "object", "module",
        }:
            data_structures.append(item)
        if len(symbols) >= 6 and len(data_structures) >= 3:
            break

    def edge_facts(relations: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        relation_placeholders = ",".join("?" for _ in relations)
        rows = connection.execute(
            f"""
            SELECT e.id, e.source_id, e.target_id, e.relation, e.confidence, e.source,
                   source.key AS source_path, target.key AS target_path
            FROM edges e
            JOIN nodes source
              ON source.id=e.source_id AND source.layer=1 AND source.kind='file'
            JOIN nodes target
              ON target.id=e.target_id AND target.layer=1 AND target.kind='file'
            WHERE e.project_id=? AND e.layer=1
              AND e.relation IN ({relation_placeholders})
              AND source.key IN ({placeholders})
              AND target.key IN ({placeholders})
            ORDER BY e.relation, source.key COLLATE NOCASE, source.key,
                     target.key COLLATE NOCASE, target.key, e.id
            LIMIT ?
            """,
            (
                project_id, *relations, *ordered_paths, *ordered_paths, limit,
            ),
        )
        return [
            {
                "id": str(row["id"]),
                "source_id": str(row["source_id"]),
                "target_id": str(row["target_id"]),
                "relation": str(row["relation"]),
                "source_path": str(row["source_path"]),
                "target_path": str(row["target_path"]),
                "source": str(row["source"])[:120],
                "confidence": float(row["confidence"]),
                "evidence": [
                    _file_evidence(str(row["source_path"]), "索引中的文件关系。")
                ],
            }
            for row in rows
        ]

    dependencies = edge_facts(("imports", "depends_on", "references"), 4)
    tests = edge_facts(("tests",), 4)

    return {
        "symbols": symbols[:6],
        "data_structures": data_structures[:3],
        "dependencies": dependencies,
        "tests": tests,
    }


def _iter_safe_completed_tasks(
    connection: sqlite3.Connection,
    project_id: str,
    *,
    page_size: int = 16,
) -> Iterator[dict[str, Any]]:
    """按稳定 cursor 分页，在安全过滤后才交付已完成任务。"""

    cursor_time: str | None = None
    cursor_id: str | None = None
    while True:
        rows = list(
            connection.execute(
                """
                /* repository-tour-task-example-page */
                SELECT task.id, task.title, task.status, task.summary,
                       task.created_at, task.updated_at, task.closed_at,
                       task_node.id AS node_id,
                       COALESCE(task.closed_at, task.updated_at, task.created_at) AS sort_time
                FROM tasks task
                JOIN nodes task_node
                  ON task_node.project_id=task.project_id
                 AND task_node.layer=3 AND task_node.kind='task'
                 AND task_node.key=task.id
                WHERE task.project_id=? AND task.status='completed'
                  AND (
                    ? IS NULL
                    OR COALESCE(task.closed_at, task.updated_at, task.created_at) < ?
                    OR (
                      COALESCE(task.closed_at, task.updated_at, task.created_at) = ?
                      AND task.id < ?
                    )
                  )
                ORDER BY sort_time DESC, task.id DESC
                LIMIT ?
                """,
                (
                    project_id,
                    cursor_time,
                    cursor_time,
                    cursor_time,
                    cursor_id,
                    page_size,
                ),
            )
        )
        if not rows:
            return
        for row in rows:
            title = _safe_prose(str(row["title"]), limit=240)
            detail = _safe_prose(str(row["summary"] or row["status"]), limit=320)
            if title and detail:
                yield {**dict(row), "safe_title": title, "safe_detail": detail}
        last = rows[-1]
        cursor_time = str(last["sort_time"])
        cursor_id = str(last["id"])
        if len(rows) < page_size:
            return


def _tour_task_history(
    connection: sqlite3.Connection,
    project_id: str,
    evidence_paths: list[str],
) -> tuple[list[dict[str, Any]], bool]:
    """批量分页读取任务边，严格限制候选、页数与 SQL 数量。"""

    if not evidence_paths:
        return [], False
    path_placeholders = ",".join("?" for _ in evidence_paths)
    cursor_time: str | None = None
    cursor_id: str | None = None
    history: list[dict[str, Any]] = []
    for page_index in range(MAX_TOUR_HISTORY_PAGES):
        task_rows = list(
            connection.execute(
                """
                /* repository-tour-history-task-page */
                SELECT task.id, task.title, task.status, task.summary,
                       task_node.id AS node_id,
                       COALESCE(task.closed_at, task.updated_at, task.created_at) AS sort_time
                FROM tasks task
                JOIN nodes task_node
                  ON task_node.project_id=task.project_id
                 AND task_node.layer=3 AND task_node.kind='task'
                 AND task_node.key=task.id
                WHERE task.project_id=? AND task.status='completed'
                  AND (
                    ? IS NULL
                    OR COALESCE(task.closed_at, task.updated_at, task.created_at) < ?
                    OR (
                      COALESCE(task.closed_at, task.updated_at, task.created_at) = ?
                      AND task.id < ?
                    )
                  )
                ORDER BY sort_time DESC, task.id DESC
                LIMIT ?
                """,
                (
                    project_id,
                    cursor_time,
                    cursor_time,
                    cursor_time,
                    cursor_id,
                    TOUR_HISTORY_PAGE_SIZE,
                ),
            )
        )
        if not task_rows:
            return history, False
        safe_tasks: list[dict[str, Any]] = []
        for row in task_rows:
            title = _safe_prose(str(row["title"]), limit=240)
            detail = _safe_prose(str(row["summary"] or row["status"]), limit=320)
            if title and detail:
                safe_tasks.append(
                    {**dict(row), "safe_title": title, "safe_detail": detail}
                )
        if safe_tasks:
            task_by_node = {str(task["node_id"]): task for task in safe_tasks}
            node_ids = list(task_by_node)
            node_placeholders = ",".join("?" for _ in node_ids)
            rank_order = " ".join(
                f"WHEN ? THEN {index}" for index in range(len(node_ids))
            )
            edge_rows = connection.execute(
                f"""
                /* repository-tour-history-edge-batch */
                SELECT edge.id AS edge_id, edge.source_id, edge.target_id,
                       edge.relation, edge.source, edge.confidence,
                       file.key AS path
                FROM edges edge
                JOIN nodes file
                  ON file.id=edge.target_id
                 AND file.layer=1 AND file.kind='file'
                WHERE edge.project_id=? AND edge.layer=3
                  AND edge.source_id IN ({node_placeholders})
                  AND edge.relation IN ('read', 'modified', 'tested', 'searched')
                  AND file.key IN ({path_placeholders})
                ORDER BY CASE edge.source_id {rank_order} ELSE {len(node_ids)} END,
                         edge.relation, file.key COLLATE NOCASE, file.key, edge.id
                LIMIT ?
                """,
                (
                    project_id,
                    *node_ids,
                    *evidence_paths,
                    *node_ids,
                    3 - len(history),
                ),
            )
            for row in edge_rows:
                task = task_by_node[str(row["source_id"])]
                history.append(
                    {
                        "title": task["safe_title"],
                        "summary": task["safe_detail"],
                        "edge_id": str(row["edge_id"]),
                        "source_id": str(row["source_id"]),
                        "target_id": str(row["target_id"]),
                        "relation": str(row["relation"]),
                        "source": str(row["source"]),
                        "confidence": float(row["confidence"]),
                        "path": str(row["path"]),
                    }
                )
            if len(history) >= 3:
                return history, False
        last = task_rows[-1]
        cursor_time = str(last["sort_time"])
        cursor_id = str(last["id"])
        if len(task_rows) < TOUR_HISTORY_PAGE_SIZE:
            return history, False
        if page_index + 1 == MAX_TOUR_HISTORY_PAGES:
            return history, True
    raise AssertionError("Tour history 分页必须在显式预算内结束。")


def _tour_node_entity(
    connection: sqlite3.Connection,
    project_id: str,
    node_id: str,
    evidence: list[dict[str, Any]],
    *,
    path: str | None = None,
) -> dict[str, Any] | None:
    """用同一读事务中的 node 事实构造 EntityRef，不借用 Evidence 来填充 provenance。"""

    row = connection.execute(
        """SELECT id, layer, kind, label, source, confidence
           FROM nodes WHERE project_id=? AND id=?""",
        (project_id, node_id),
    ).fetchone()
    if row is None:
        return None
    return _tour_entity(
        str(row["id"]),
        str(row["kind"]),
        str(row["label"]),
        evidence,
        path=path,
        layer=f"L{int(row['layer'])}",
        source=str(row["source"]),
        confidence=float(row["confidence"]),
    )


def repository_tour_data(
    database: Database,
    project: sqlite3.Row,
) -> dict[str, Any]:
    """在一个数据库 read snapshot 中生成固定三档 Guided Repository Tour。"""

    with database.connect() as connection:
        connection.execute("BEGIN")
        snapshot_context: dict[str, Any] = {}
        overview = _repository_overview_data(
            database,
            project,
            connection=connection,
            snapshot_context=snapshot_context,
        )
        purpose = overview["purpose"]
        problem = overview["need"]["problem"]
        solution = overview["need"]["solution"]
        workflow = overview["workflow"]
        modules = overview["modules"]
        reading_order = overview["readingOrder"]
        if overview["sourceState"]["status"] == "partial":
            modules = []
            reading_order = []
        evidence_paths = {
            evidence["path"]
            for collection in (
                [purpose, problem, solution],
                workflow,
                modules,
                reading_order,
            )
            for item in collection
            for evidence in item["evidence"]
            if isinstance(evidence.get("path"), str)
        }
        evidence_paths.update(
            path for module in modules for path in module["paths"]
        )
        evidence_paths.update(entry["path"] for entry in reading_order)
        project_id = str(project["id"])
        graph_facts = _tour_graph_facts(connection, project_id, evidence_paths)
        ordered_evidence_paths = sorted(evidence_paths)
        task_example = next(_iter_safe_completed_tasks(connection, project_id), None)
        task_history, history_truncated = _tour_task_history(
            connection, project_id, ordered_evidence_paths
        )

        def document_entity(stop_id: str, kind: str, value: dict[str, Any]) -> dict[str, Any] | None:
            if not value["evidence"]:
                return None
            evidence = value["evidence"]
            return _tour_entity(
                stable_id(str(project["id"]), stop_id, prefix="tour_"),
                kind,
                value["summary"],
                evidence,
                path=evidence[0].get("path"),
            )

        purpose_stop = _tour_stop(
            "purpose", "purpose", "是什么", purpose["summary"],
            "从项目文档的明确目的说明建立整体心智模型。", purpose["evidence"],
            entity=document_entity("purpose", "concept", purpose),
        )
        why_evidence = [*problem["evidence"], *solution["evidence"]]
        why_stop = _tour_stop(
            "why", "why", "为什么", problem["summary"], solution["summary"], why_evidence,
            entity=(
                _tour_entity(
                    stable_id(str(project["id"]), "why", prefix="tour_"),
                    "concept", problem["summary"], why_evidence,
                    path=why_evidence[0].get("path"),
                )
                if why_evidence else None
            ),
        )
        workflow_evidence = [
            evidence for step in workflow for evidence in step["evidence"]
        ]
        workflow_stop = (
            _tour_stop(
                "core-flow", "workflow", "核心流程",
                " → ".join(step["title"] for step in workflow),
                "；".join(
                    f"{step['step']}. {step['detail']}" for step in workflow
                ),
                workflow_evidence,
                entity=_tour_entity(
                    stable_id(str(project["id"]), "core-flow", prefix="tour_"),
                    "workflow", "核心流程", workflow_evidence,
                    path=workflow_evidence[0].get("path"),
                ),
            )
            if 5 <= len(workflow) <= 7 and workflow_evidence else None
        )
        selected_modules = modules[:3]
        module_paths = list(dict.fromkeys(
            path for module in selected_modules for path in module["paths"]
        ))[:6]
        module_evidence = [
            evidence
            for module in selected_modules
            for evidence in module["evidence"]
        ]
        module_stop = (
            _tour_stop(
                "main-modules", "module", "主要模块",
                "、".join(module["name"] for module in selected_modules),
                "；".join(
                    f"{module['name']}：{module['summary']}"
                    for module in selected_modules
                ) + f"。关键实现：{'、'.join(module_paths)}",
                module_evidence,
                entity=_tour_entity(
                    stable_id(str(project["id"]), "main-modules", prefix="tour_"),
                    "module-group", "主要模块", module_evidence,
                    path=module_paths[0], layer="L2", source="repository-tour",
                    confidence=min(module["confidence"] for module in selected_modules),
                ),
            )
            if selected_modules and module_paths and module_evidence else None
        )

        data_structure_stops = [
            _tour_stop(
                f"data-{item['id']}", "data-structure", "核心数据结构",
                item["label"], f"{item['symbol_kind']} · {item['path']}", item["evidence"],
                entity=_tour_node_entity(
                    connection, project_id, item["id"], item["evidence"],
                    path=item["path"],
                ),
            )
            for item in graph_facts["data_structures"][:1]
        ]
        task_stops = []
        if task_example is not None:
            evidence = [{
                "kind": "task-record", "summary": f"任务记录 {task_example['id']}",
                "layer": "L3", "source": "task-events", "confidence": 1.0,
            }]
            task_stops.append(_tour_stop(
                f"task-{task_example['id']}", "task", "真实任务示例",
                task_example["safe_title"], task_example["safe_detail"], evidence,
                entity=_tour_node_entity(
                    connection, project_id, task_example["node_id"], evidence,
                ),
            ))
        reading_by_path = {entry["path"]: entry for entry in reading_order}
        source_entries: list[dict[str, Any]] = []

        def add_source_path(path: str, reason: str) -> None:
            if path not in evidence_paths or path in {item["path"] for item in source_entries}:
                return
            reading = reading_by_path.get(path)
            source_entries.append(
                {
                    "position": len(source_entries) + 1,
                    "path": path,
                    "reason": reading["reason"] if reading is not None else reason,
                    "evidence": (
                        reading["evidence"]
                        if reading is not None
                        else [_file_evidence(path, "已核对的源码导航路径。")]
                    ),
                }
            )

        code_suffixes = (
            ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
            ".kt", ".swift", ".rb", ".php", ".c", ".cc", ".cpp", ".h",
        )
        for entry in reading_order:
            if entry["path"].endswith(code_suffixes) and (
                "入口" in entry["reason"]
                or PurePosixPath(entry["path"]).name in {"cli.py", "__main__.py", "main.py"}
            ):
                add_source_path(entry["path"], "公开代码入口。")
        for item in graph_facts["symbols"]:
            add_source_path(item["path"], "符号所在源码文件。")
        for category in ("dependencies", "tests"):
            for item in graph_facts[category]:
                add_source_path(item["source_path"], "文件关系的来源端。")
                add_source_path(item["target_path"], "文件关系的目标端。")
        for module_item in modules:
            for path in module_item["paths"]:
                if path.endswith(code_suffixes):
                    add_source_path(path, "主要模块的源码文件。")
        for entry in reading_order:
            if entry["path"].endswith(code_suffixes):
                add_source_path(entry["path"], "建议阅读的源码文件。")
        for entry in reading_order:
            add_source_path(entry["path"], entry["reason"])

        key_file_stops = [
            _tour_stop(
                f"key-file-{entry['position']}", "file", "关键文件",
                entry["path"], entry["reason"], entry["evidence"],
                entity=_tour_node_entity(
                    connection, project_id,
                    Database.node_id(project_id, 1, "file", entry["path"]),
                    entry["evidence"], path=entry["path"],
                ),
            )
            for entry in source_entries[:1]
        ]
        history_stops = []
        for document in snapshot_context.get("documents", []):
            if not document.path.startswith(("docs/adr/", "docs/decisions/")):
                continue
            title = PurePosixPath(document.path).stem
            prose = None
            prose_line = None
            for line_number, raw_line in document.lines:
                heading = _HEADING_RE.match(raw_line)
                if heading and title == PurePosixPath(document.path).stem:
                    title = _safe_prose(heading.group(1), limit=160) or title
                    continue
                candidate = _safe_prose(raw_line)
                if candidate:
                    prose = candidate
                    prose_line = line_number
                    break
            if not prose or prose_line is None:
                continue
            evidence = [_evidence(document, prose_line, "设计决定文档的首段说明。")]
            history_stops.append(_tour_stop(
                f"history-{document.path}", "history", "设计历史", title, prose, evidence,
                entity=_tour_node_entity(
                    connection, project_id,
                    Database.node_id(project_id, 1, "file", document.path),
                    evidence, path=document.path,
                ),
            ))
            break

        file_stops = [
            _tour_stop(
                f"source-file-{entry['position']}", "file", entry["path"],
                entry["reason"], "该路径已通过当前索引快照核对。", entry["evidence"],
                entity=_tour_node_entity(
                    connection, project_id,
                    Database.node_id(project_id, 1, "file", entry["path"]),
                    entry["evidence"], path=entry["path"],
                ),
            )
            for entry in source_entries[:2]
        ]
        symbol_stops = [
            _tour_stop(
                f"symbol-{item['id']}", "symbol", item["label"],
                f"{item['symbol_kind']} in {item['path']}", "该符号由文件提取器识别。", item["evidence"],
                entity=_tour_node_entity(
                    connection, project_id, item["id"], item["evidence"],
                    path=item["path"],
                ),
            )
            for item in graph_facts["symbols"][:2]
        ]
        dependency_stops = [
            _tour_stop(
                f"dependency-{item['id']}", "dependency", "文件依赖",
                f"{item['source_path']} → {item['target_path']}", item["relation"], item["evidence"],
                entity=_tour_node_entity(
                    connection, project_id, item["source_id"], item["evidence"],
                    path=item["source_path"],
                ),
                relations=[{
                    "id": item["id"],
                    "sourceId": item["source_id"],
                    "targetId": item["target_id"],
                    "relation": item["relation"], "layer": "L1", "source": item["source"],
                    "confidence": item["confidence"], "evidence": item["evidence"],
                }],
            )
            for item in graph_facts["dependencies"][:2]
        ]
        test_stops = [
            _tour_stop(
                f"test-{item['id']}", "test", "对应测试",
                item["source_path"], f"验证 {item['target_path']}", item["evidence"],
                entity=_tour_node_entity(
                    connection, project_id, item["source_id"], item["evidence"],
                    path=item["source_path"],
                ),
                relations=[{
                    "id": item["id"],
                    "sourceId": item["source_id"],
                    "targetId": item["target_id"],
                    "relation": "tests", "layer": "L1", "source": item["source"],
                    "confidence": item["confidence"], "evidence": item["evidence"],
                }],
            )
            for item in graph_facts["tests"][:1]
        ]
        evidence_stop = (
            _tour_stop(
                "evidence", "evidence", "源码证据", first["path"],
                first["summary"], [first],
                entity=_tour_entity(
                    stable_id(str(project["id"]), first["path"], prefix="evidence_"),
                    "evidence", first["path"], [first], path=first["path"],
                ),
            )
            if (first := next(
                (
                    evidence
                    for entry in source_entries
                    for evidence in entry["evidence"]
                    if isinstance(evidence.get("path"), str)
                ),
                None,
            )) else None
        )
        task_history_stops = []
        for item in task_history:
            title = _safe_prose(item["title"], limit=240)
            detail = _safe_prose(item["summary"] or item["relation"], limit=320)
            if not title or not detail:
                continue
            evidence = [{
                "kind": "task-file-relation",
                "summary": f"任务通过 {item['relation']} 关联此文件。",
                "layer": "L3", "source": item["source"],
                "confidence": item["confidence"], "path": item["path"],
            }]
            task_history_stops.append(_tour_stop(
                f"task-history-{item['edge_id']}", "task-history", "任务历史",
                title, f"{detail} · {item['relation']} {item['path']}", evidence,
                entity=_tour_node_entity(
                    connection, project_id, item["source_id"], evidence,
                ),
                relations=[{
                    "id": item["edge_id"], "sourceId": item["source_id"],
                    "targetId": item["target_id"],
                    "relation": item["relation"], "layer": "L3",
                    "source": item["source"], "confidence": item["confidence"],
                    "evidence": evidence,
                }],
            ))

        tiers_by_depth: dict[str, list[dict[str, Any] | None]] = {
            "one-minute": [purpose_stop, why_stop, workflow_stop, module_stop],
            "five-minutes": [
                purpose_stop, why_stop, workflow_stop, module_stop,
                *data_structure_stops, *task_stops, *key_file_stops, *history_stops,
            ],
            "source-deep-dive": [
                *file_stops, *symbol_stops, *dependency_stops, *test_stops,
                *task_history_stops, evidence_stop,
            ],
        }
        required_kinds = {
            "one-minute": {"purpose", "why", "workflow", "module"},
            "five-minutes": {
                "purpose", "why", "workflow", "module", "data-structure",
                "task", "file", "history",
            },
            "source-deep-dive": {
                "file", "symbol", "dependency", "test", "task-history", "evidence",
            },
        }
        tiers: list[dict[str, Any]] = []
        tour_warnings: list[dict[str, Any]] = []
        if history_truncated:
            tour_warnings.append({
                "code": "TOUR_HISTORY_TRUNCATED",
                "message": (
                    f"任务历史已达 {MAX_TOUR_HISTORY_CANDIDATES} 个候选的扫描预算"
                    f"（最多 {MAX_TOUR_HISTORY_PAGES} 页 / "
                    f"{MAX_TOUR_HISTORY_SQL_QUERIES} 条 SQL），结果可能不完整。"
                ),
                "evidence": [],
            })
        for depth, label, limit in _TOUR_DEPTHS:
            stops = [item for item in tiers_by_depth[depth] if item is not None][:limit]
            tiers.append({"depth": depth, "label": label, "stops": stops})
            missing = sorted(required_kinds[depth] - {item["kind"] for item in stops})
            if history_truncated and depth == "source-deep-dive":
                missing = [kind for kind in missing if kind != "task-history"]
            if missing:
                tour_warnings.append({
                    "code": "TOUR_EVIDENCE_INSUFFICIENT",
                    "message": f"{label}导览缺少可追溯的 {', '.join(missing)} 事实，未生成推测性 stop。",
                    "evidence": [],
                })

        revision_parts = [str(overview["sourceState"].get("revision") or "")]
        for tier in tiers:
            for stop in tier["stops"]:
                revision_parts.append(
                    json.dumps(
                        stop,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
        source_state = {
            **overview["sourceState"],
            "revision": stable_id(*revision_parts, prefix="tour_snapshot_"),
        }

    return {
        "project": overview["project"],
        "sourceState": source_state,
        "tiers": tiers,
        "stats": overview["stats"],
        "warnings": sorted(
            [*overview["warnings"], *tour_warnings],
            key=lambda item: (item["code"], item["message"]),
        ),
    }


def _architecture_connection_evidence(
    edge: sqlite3.Row,
    fresh_paths: set[str],
    component_paths: dict[str, set[str]],
    physical_edges: set[tuple[str, str, str]],
) -> tuple[list[dict[str, Any]], bool]:
    if str(edge["source"]) == "human-overlay":
        return [{
            "kind": "human-decision",
            "summary": "人工确认的概念关系。",
            "layer": "L2",
            "source": "human-overlay",
            "confidence": float(edge["confidence"]),
        }], False
    data = json_loads(edge["data_json"], {})
    raw_evidence = data.get("evidence", [])
    if not isinstance(raw_evidence, list):
        return [], False
    truncated = len(raw_evidence) > MAX_ARCHITECTURE_RAW_EVIDENCE_CANDIDATES
    evidence: list[dict[str, Any]] = []
    for item in raw_evidence[:MAX_ARCHITECTURE_RAW_EVIDENCE_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        source_path = item.get("source")
        target_path = item.get("target")
        relation = item.get("physical_relation")
        if not all(isinstance(value, str) for value in (source_path, target_path, relation)):
            continue
        assert isinstance(source_path, str)
        assert isinstance(target_path, str)
        assert isinstance(relation, str)
        if (
            not is_canonical_relative_path(source_path)
            or not is_canonical_relative_path(target_path)
            or source_path not in fresh_paths
            or target_path not in fresh_paths
            or source_path not in component_paths.get(str(edge["source_id"]), set())
            or target_path not in component_paths.get(str(edge["target_id"]), set())
        ):
            continue
        if (source_path, target_path, relation) not in physical_edges:
            continue
        evidence.append({
            "kind": "physical-relation",
            "summary": f"{source_path} {relation} {target_path}",
            "layer": "L1",
            "source": "repository-index",
            "confidence": 1.0,
            "path": source_path,
        })
        if len(evidence) >= 3:
            break
    return evidence, truncated


def _architecture_physical_edges(
    connection: sqlite3.Connection,
    project_id: str,
    component_paths: dict[str, set[str]],
) -> set[tuple[str, str, str]]:
    paths = sorted(
        {path for selected in component_paths.values() for path in selected},
        key=lambda item: (item.lower(), item),
    )
    if not paths:
        return set()
    placeholders = ",".join("?" for _ in paths)
    rows = connection.execute(
        f"""-- repository-architecture-physical-edges
            SELECT source_file.key AS source_path,
                   target_file.key AS target_path,
                   physical.relation
            FROM edges physical
            JOIN nodes source_file
              ON source_file.id=physical.source_id AND source_file.layer=1
             AND source_file.kind='file'
            JOIN nodes target_file
              ON target_file.id=physical.target_id AND target_file.layer=1
             AND target_file.kind='file'
            WHERE physical.project_id=? AND physical.layer=1
              AND source_file.key IN ({placeholders})
              AND target_file.key IN ({placeholders})
            ORDER BY source_file.key COLLATE NOCASE, source_file.key,
                     target_file.key COLLATE NOCASE, target_file.key,
                     physical.relation, physical.id""",
        (project_id, *paths, *paths),
    )
    return {
        (str(row["source_path"]), str(row["target_path"]), str(row["relation"]))
        for row in rows
    }


def _structure_snapshot(
    database: Database,
    project: sqlite3.Row,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    snapshot_context: dict[str, Any] = {}
    overview = _repository_overview_data(
        database,
        project,
        connection=connection,
        snapshot_context=snapshot_context,
    )
    project_id = str(project["id"])
    modules = overview["modules"] if overview["sourceState"]["status"] != "partial" else []
    reading_order = (
        overview["readingOrder"]
        if overview["sourceState"]["status"] != "partial"
        else []
    )
    fresh_paths = {
        path for module in modules for path in module["paths"]
    } | {entry["path"] for entry in reading_order}
    fresh_paths.update(
        evidence["path"]
        for step in overview["workflow"]
        for evidence in step["evidence"]
        if isinstance(evidence.get("path"), str)
    )
    entry_paths = {
        entry["path"]
        for entry in reading_order
        if any(
            term in entry["reason"].lower()
            for term in ("入口", "entry point", "进入调用链")
        )
    }

    selected_modules = [
        module for module in modules[:MAX_ARCHITECTURE_COMPONENTS]
        if module["paths"] and module["evidence"]
    ]
    mapping_rows: list[sqlite3.Row] = []
    mapping_truncated = False
    if selected_modules:
        module_ids = [str(module["id"]) for module in selected_modules]
        module_placeholders = ",".join("?" for _ in module_ids)
        relation_placeholders = ",".join("?" for _ in _STRUCTURE_FILE_RELATIONS)
        mapping_parameters: list[Any] = [
            project_id, *module_ids, *_STRUCTURE_FILE_RELATIONS,
            MAX_STRUCTURE_MAPPING_CANDIDATES + 1,
        ]
        mapping_rows = list(connection.execute(
            f"""-- repository-structure-mappings
                SELECT concept.id AS concept_id, concept.key AS concept_key,
                       concept.label AS concept_label,
                       concept.data_json AS concept_data_json,
                       concept.source AS concept_source,
                       concept.confidence AS concept_confidence,
                       edge.id AS edge_id, edge.source_id, edge.target_id,
                       edge.relation, edge.source AS edge_source,
                       edge.confidence AS edge_confidence,
                       file.id AS file_id, file.key AS path, file.label AS file_label,
                       file.source AS file_source, file.confidence AS file_confidence
                FROM nodes concept
                JOIN edges edge
                  ON edge.project_id=concept.project_id AND edge.layer=2
                 AND edge.source_id=concept.id
                JOIN nodes file
                  ON file.project_id=concept.project_id AND file.id=edge.target_id
                 AND file.layer=1 AND file.kind='file'
                WHERE concept.project_id=? AND concept.layer=2 AND concept.kind='concept'
                  AND concept.id IN ({module_placeholders})
                  AND edge.relation IN ({relation_placeholders})
                ORDER BY concept.id, file.key COLLATE NOCASE, file.key,
                         CASE edge.relation
                           WHEN 'implemented_by' THEN 0 WHEN 'tested_by' THEN 1
                           WHEN 'documented_by' THEN 2 WHEN 'configured_by' THEN 3
                           ELSE 4 END,
                         edge.id
                LIMIT ?""",
            mapping_parameters,
        ))
        mapping_truncated = len(mapping_rows) > MAX_STRUCTURE_MAPPING_CANDIDATES
        mapping_rows = mapping_rows[:MAX_STRUCTURE_MAPPING_CANDIDATES]

    rows_by_module: dict[str, list[sqlite3.Row]] = {}
    seen_mappings: set[tuple[str, str]] = set()
    structure_freshness = snapshot_context.get("freshness", {})
    structure_states = snapshot_context.get("states", {})
    structure_budget = snapshot_context.get("freshnessBudget")
    structure_root = snapshot_context.get("root")
    try:
        resolved_structure_root = (
            structure_root.resolve(strict=True)
            if isinstance(structure_root, Path) else None
        )
    except (OSError, RuntimeError):
        resolved_structure_root = None
    for row in mapping_rows:
        module_id = str(row["concept_id"])
        path = str(row["path"])
        mapping_key = (module_id, path)
        if (
            path not in structure_freshness
            and resolved_structure_root is not None
            and isinstance(structure_budget, _FreshnessBudget)
        ):
            _fresh_path(
                resolved_structure_root, path, structure_states,
                structure_freshness, structure_budget,
            )
        if (
            mapping_key in seen_mappings
            or not structure_freshness.get(path, False)
            or not is_canonical_relative_path(path)
        ):
            continue
        fresh_paths.add(path)
        seen_mappings.add(mapping_key)
        rows_by_module.setdefault(module_id, []).append(row)

    components: list[dict[str, Any]] = []
    file_facts: list[dict[str, Any]] = []
    component_ids: list[str] = []
    component_paths: dict[str, set[str]] = {}
    for module in selected_modules:
        module_id = str(module["id"])
        rows = rows_by_module.get(module_id, [])[:3]
        if not rows:
            continue
        first_row = rows[0]
        relation_counts: dict[str, int] = {}
        data = json_loads(first_row["concept_data_json"], {})
        keywords = data.get("keywords", [])
        common_match_terms = {
            str(first_row["concept_key"]), str(first_row["concept_label"]),
            *(str(item) for item in keywords if isinstance(item, str)),
        }
        for row in rows:
            path = str(row["path"])
            evidence = [_file_evidence(path, "概念与已核对仓库文件的真实映射。")]
            entity = {
                "id": str(row["file_id"]),
                "kind": "file",
                "label": str(row["file_label"]),
                "path": path,
                "layer": "L1",
                "source": str(row["file_source"]),
                "confidence": float(row["file_confidence"]),
                "evidence": evidence,
            }
            relation = {
                "id": str(row["edge_id"]),
                "sourceId": str(row["source_id"]),
                "targetId": str(row["target_id"]),
                "relation": str(row["relation"]),
                "layer": "L2",
                "source": str(row["edge_source"]),
                "confidence": float(row["edge_confidence"]),
                "evidence": evidence,
            }
            relation_counts[relation["relation"]] = relation_counts.get(relation["relation"], 0) + 1
            file_facts.append({
                "path": path,
                "moduleId": module_id,
                "moduleName": str(first_row["concept_label"]),
                "entity": entity,
                "relation": relation,
                "evidence": evidence,
                "matchTerms": sorted({
                    *common_match_terms, path, PurePosixPath(path).stem,
                }),
            })
        selected_paths = {str(row["path"]) for row in rows}
        component_paths[module_id] = selected_paths
        if any(path in entry_paths for path in selected_paths):
            group = "entry"
        elif "implemented_by" in relation_counts:
            group = "core"
        else:
            group = "support"
        responsibility = "；".join(
            f"{relation} 关联 {count} 个已核对文件"
            for relation, count in sorted(relation_counts.items())
        )
        entity = {
            "id": module_id,
            "kind": "concept",
            "label": str(first_row["concept_label"]),
            "layer": "L2",
            "source": str(first_row["concept_source"]),
            "confidence": float(first_row["concept_confidence"]),
            "evidence": module["evidence"][:3],
        }
        components.append({
            "id": module_id,
            "name": str(first_row["concept_label"]),
            "group": group,
            "responsibility": responsibility,
            "paths": sorted(selected_paths, key=lambda item: (item.lower(), item))[:3],
            "entity": entity,
            "evidence": module["evidence"][:3],
        })
        component_ids.append(module_id)

    connections: list[dict[str, Any]] = []
    dropped_connections = False
    connection_evidence_truncated = False
    if component_ids:
        physical_edges = _architecture_physical_edges(
            connection, project_id, component_paths
        )
        placeholders = ",".join("?" for _ in component_ids)
        rows = connection.execute(
            f"""SELECT edge.* FROM edges edge
                WHERE edge.project_id=? AND edge.layer=2
                  AND edge.relation IN ('depends_on', 'related_to')
                  AND edge.source_id IN ({placeholders})
                  AND edge.target_id IN ({placeholders})
                ORDER BY edge.relation, edge.source_id, edge.target_id, edge.id""",
            (project_id, *component_ids, *component_ids),
        )
        for row in rows:
            evidence, raw_truncated = _architecture_connection_evidence(
                row, fresh_paths, component_paths, physical_edges
            )
            connection_evidence_truncated = (
                connection_evidence_truncated or raw_truncated
            )
            if not evidence:
                dropped_connections = True
                continue
            if len(connections) >= MAX_ARCHITECTURE_CONNECTIONS:
                dropped_connections = True
                continue
            connections.append({
                "id": str(row["id"]),
                "sourceId": str(row["source_id"]),
                "targetId": str(row["target_id"]),
                "relation": str(row["relation"]),
                "layer": "L2",
                "source": str(row["source"]),
                "confidence": float(row["confidence"]),
                "evidence": evidence,
            })

    entry_points: list[dict[str, Any]] = []
    for entry in reading_order:
        if entry["path"] not in entry_paths:
            continue
        entity = _tour_node_entity(
            connection,
            project_id,
            Database.node_id(project_id, 1, "file", entry["path"]),
            entry["evidence"],
            path=entry["path"],
        )
        if entity is None:
            continue
        entry_points.append({
            "path": entry["path"],
            "reason": entry["reason"],
            "entity": entity,
            "evidence": entry["evidence"],
        })
        if len(entry_points) >= MAX_ARCHITECTURE_ENTRY_POINTS:
            break

    return {
        "overview": overview,
        "documents": snapshot_context.get("documents", []),
        "components": components,
        "connections": connections,
        "entryPoints": entry_points,
        "fileFacts": file_facts,
        "droppedConnections": dropped_connections,
        "mappingTruncated": mapping_truncated,
        "connectionEvidenceTruncated": connection_evidence_truncated,
    }


def _understanding_revision(prefix: str, base_revision: str | None, *values: Any) -> str:
    parts = [base_revision or ""]
    parts.extend(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in values
    )
    return stable_id(*parts, prefix=prefix)


def repository_architecture_data(
    database: Database,
    project: sqlite3.Row,
) -> dict[str, Any]:
    with database.connect() as connection:
        connection.execute("BEGIN")
        snapshot = _structure_snapshot(database, project, connection)
        overview = snapshot["overview"]
        components = snapshot["components"]
        summary_evidence = [
            evidence
            for component in components
            for evidence in component["evidence"][:1]
        ][:8]
        summary = {
            "text": (
                "系统由 " + "、".join(component["name"] for component in components) + " 构成。"
                if components else "暂无足够的组件证据。"
            ),
            "explanationSource": "derived-presentation",
            "evidence": summary_evidence,
        }
        warnings: list[dict[str, Any]] = []
        if not components:
            warnings.append({
                "code": "ARCHITECTURE_EVIDENCE_INSUFFICIENT",
                "message": "缺少有文件证据的活跃组件，未生成推测性架构。",
                "evidence": [],
            })
        if snapshot["droppedConnections"]:
            warnings.append({
                "code": "ARCHITECTURE_CONNECTIONS_TRUNCATED",
                "message": "部分概念连接缺少可核验物理证据或超过上限，已剪除。",
                "evidence": [],
            })
        if snapshot["connectionEvidenceTruncated"]:
            warnings.append({
                "code": "ARCHITECTURE_CONNECTION_EVIDENCE_TRUNCATED",
                "message": (
                    "自动概念关系的原始 evidence 超过每条 "
                    f"{MAX_ARCHITECTURE_RAW_EVIDENCE_CANDIDATES} 项上限，已稳定截断。"
                ),
                "evidence": [],
            })
        if snapshot["mappingTruncated"]:
            warnings.append({
                "code": "REPOSITORY_STRUCTURE_MAPPINGS_TRUNCATED",
                "message": (
                    f"组件文件映射达到 {MAX_STRUCTURE_MAPPING_CANDIDATES} 个候选 / "
                    f"{MAX_STRUCTURE_MAPPING_QUERIES} 条 SQL 的预算，结果已稳定截断。"
                ),
                "evidence": [],
            })
        source_state = {
            **overview["sourceState"],
            "revision": _understanding_revision(
                "architecture_snapshot_",
                overview["sourceState"].get("revision"),
                summary,
                components,
                snapshot["connections"],
                snapshot["entryPoints"],
            ),
        }

    return {
        "project": overview["project"],
        "sourceState": source_state,
        "layout": "cognitive-components",
        "summary": summary,
        "components": components,
        "connections": snapshot["connections"],
        "entryPoints": snapshot["entryPoints"],
        "stats": overview["stats"],
        "warnings": sorted(
            [*overview["warnings"], *warnings],
            key=lambda item: (item["code"], item["message"]),
        ),
    }


def _flow_match_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", value.lower())
        if len(token) >= 2
    }


def repository_flow_data(
    database: Database,
    project: sqlite3.Row,
    query: str | None = None,
) -> dict[str, Any]:
    with database.connect() as connection:
        connection.execute("BEGIN")
        snapshot = _structure_snapshot(database, project, connection)
        overview = snapshot["overview"]
        workflow, truncated = _execution_workflow(snapshot["documents"])
        query_provided = isinstance(query, str)
        query_redacted = query_provided and contains_private_path(query)
        safe_query = (
            _safe_prose(query, limit=320)
            if query_provided and not query_redacted else None
        )
        example_task: dict[str, Any] | None = None
        if query_redacted:
            example_task = {
                "title": "[查询含路径，已隐藏]",
                "source": "request-redacted",
            }
        elif safe_query:
            example_task = {"title": safe_query, "source": "request"}
        elif not query_provided:
            task = next(_iter_safe_completed_tasks(connection, str(project["id"])), None)
            if task is not None:
                evidence = [{
                    "kind": "task-record",
                    "summary": f"任务记录 {task['id']}",
                    "layer": "L3",
                    "source": "task-events",
                    "confidence": 1.0,
                }]
                entity = _tour_node_entity(
                    connection, str(project["id"]), task["node_id"], evidence
                )
                if entity is not None:
                    example_task = {
                        "title": task["safe_title"],
                        "source": "task-events",
                        "entity": entity,
                        "evidence": evidence,
                    }

        steps: list[dict[str, Any]] = []
        used_paths: set[str] = set()
        unmatched: list[int] = []
        for index, step in enumerate(workflow):
            tokens = _flow_match_tokens(f"{step['title']} {step['detail']}")
            matches: list[tuple[int, str, dict[str, Any]]] = []
            for fact in snapshot["fileFacts"]:
                haystack = " ".join(fact["matchTerms"]).lower()
                score = sum(1 for token in tokens if token in haystack)
                if score > 0:
                    matches.append((-score, fact["path"].lower(), fact))
            matches.sort(key=lambda item: (item[0], item[1], item[2]["path"]))
            key_files: list[dict[str, Any]] = []
            for _, _, fact in matches:
                if fact["path"] in used_paths or len(used_paths) >= MAX_FLOW_FILES:
                    continue
                used_paths.add(fact["path"])
                key_files.append({
                    "path": fact["path"],
                    "moduleId": fact["moduleId"],
                    "moduleName": fact["moduleName"],
                    "entity": fact["entity"],
                    "relation": fact["relation"],
                    "evidence": fact["evidence"],
                })
                if len(key_files) >= 3:
                    break
            if not key_files:
                unmatched.append(int(step["step"]))
            previous = workflow[index - 1]["title"] if index > 0 else None
            following = workflow[index + 1]["title"] if index + 1 < len(workflow) else None
            steps.append({
                "step": int(step["step"]),
                "id": stable_id(
                    str(project["id"]), step["step"], step["title"], prefix="flow_step_"
                ),
                "title": step["title"],
                "purpose": step["detail"],
                "input": (
                    "进入主流程的请求或数据"
                    if previous is None else f"上一步“{previous}”的结果"
                ),
                "output": (
                    "主流程结果" if following is None else f"交给下一步“{following}”"
                ),
                "keyFiles": key_files,
                "why": f"项目文档将其列为第 {step['step']} 步。",
                "nextStep": following,
                "explanationSource": "derived-presentation",
                "evidence": step["evidence"],
            })

        warnings: list[dict[str, Any]] = []
        if query_redacted:
            warnings.append({
                "code": "FLOW_QUERY_REDACTED",
                "message": "任务描述包含本地绝对路径或文件 URI，已隐藏且未回退历史任务。",
                "evidence": [],
            })
        if snapshot["mappingTruncated"]:
            warnings.append({
                "code": "REPOSITORY_STRUCTURE_MAPPINGS_TRUNCATED",
                "message": (
                    f"组件文件映射达到 {MAX_STRUCTURE_MAPPING_CANDIDATES} 个候选 / "
                    f"{MAX_STRUCTURE_MAPPING_QUERIES} 条 SQL 的预算，结果已稳定截断。"
                ),
                "evidence": [],
            })
        if not workflow:
            warnings.append({
                "code": "FLOW_EXECUTION_EVIDENCE_MISSING",
                "message": "未在明示的主流程章节中找到 5–7 个连续执行步骤。",
                "evidence": [],
            })
        if truncated:
            warnings.append({
                "code": "FLOW_TRUNCATED",
                "message": "明示执行流程超过 7 步，已稳定保留前 7 步。",
                "evidence": [],
            })
        if unmatched:
            warnings.append({
                "code": "FLOW_KEY_FILES_INSUFFICIENT",
                "message": "以下步骤未命中可核验关键文件，已保持为空："
                + ", ".join(str(item) for item in unmatched),
                "evidence": [],
            })
        if example_task is None:
            warnings.append({
                "code": "FLOW_TASK_EVIDENCE_MISSING",
                "message": "未提供当前任务，也没有可安全展示的历史任务示例。",
                "evidence": [],
            })
        source_state = {
            **overview["sourceState"],
            "revision": _understanding_revision(
                "flow_snapshot_",
                overview["sourceState"].get("revision"),
                example_task,
                steps,
            ),
        }

    return {
        "project": overview["project"],
        "sourceState": source_state,
        "layout": "numbered-task-flow",
        "exampleTask": example_task,
        "steps": steps,
        "stats": overview["stats"],
        "warnings": sorted(
            [*overview["warnings"], *warnings],
            key=lambda item: (item["code"], item["message"]),
        ),
    }


__all__ = [
    "MAX_DOCUMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_DOCUMENT_LINES",
    "MAX_TOTAL_DOCUMENT_BYTES",
    "MAX_FRESHNESS_FILE_BYTES",
    "MAX_TOTAL_FRESHNESS_BYTES",
    "MAX_STRUCTURE_MAPPING_CANDIDATES",
    "MAX_STRUCTURE_MAPPING_QUERIES",
    "MAX_ARCHITECTURE_RAW_EVIDENCE_CANDIDATES",
    "MAX_ARCHITECTURE_PHYSICAL_EDGE_QUERIES",
    "repository_architecture_data",
    "repository_flow_data",
    "repository_overview_data",
    "repository_tour_data",
]
