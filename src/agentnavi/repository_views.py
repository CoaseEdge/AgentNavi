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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

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
) -> bytes | None:
    """一次打开完成完整 digest 校验，并仅缓存调用方需要的前缀。"""

    if state is None or not str(state["digest"]):
        return None
    hasher = hashlib.blake2s()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            cached = bytearray()
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                hasher.update(chunk)
                remaining = cache_limit + 1 - len(cached)
                if remaining > 0:
                    cached.extend(chunk[:remaining])
            after = os.fstat(handle.fileno())
    except OSError:
        return None
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ino != after.st_ino
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
) -> bool:
    if relative in freshness:
        return freshness[relative]
    try:
        resolved = _resolved_project_file(resolved_root, relative)
        fresh = _read_snapshot_file(
            resolved,
            states.get(relative),
            cache_limit=0,
        ) is not None
    except (OSError, RuntimeError, ValueError):
        fresh = False
    freshness[relative] = fresh
    return fresh


def _read_documents(
    root: Path,
    candidates: list[str],
    states: dict[str, sqlite3.Row],
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

    manifest, entry = _manifest_entry(root, paths, states, freshness)
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
            _fresh_path(resolved_root, relative, states, freshness)

    stale_paths = [path for path in ordered_references if not freshness.get(path, False)]
    filtered_modules: list[dict[str, Any]] = []
    for module in modules:
        item = dict(module)
        item["paths"] = [path for path in module["paths"] if freshness.get(path, False)]
        item["evidence"] = [
            evidence
            for evidence in module["evidence"]
            if not isinstance(evidence.get("path"), str)
            or freshness.get(evidence["path"], False)
        ]
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


def repository_overview_data(
    database: Database,
    project: sqlite3.Row,
) -> dict[str, Any]:
    """生成 Repository Overview Core 数据；全过程只读且先排序后截断。"""

    project_id = str(project["id"])
    with database.connect() as connection:
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
        if last_scan_at is None:
            documents: list[_Document] = []
        else:
            documents, document_warnings, snapshot_stale, freshness = _read_documents(
                root,
                document_paths,
                states,
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
        )
        if last_scan_at is not None:
            modules, reading_order, path_warnings, paths_stale = (
                _filter_fresh_output_paths(
                    root,
                    states,
                    freshness,
                    modules,
                    reading_order,
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


__all__ = [
    "MAX_DOCUMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_DOCUMENT_LINES",
    "MAX_TOTAL_DOCUMENT_BYTES",
    "repository_overview_data",
]
