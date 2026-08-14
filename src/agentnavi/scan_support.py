from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from .config import Settings
from .database import Database
from .utils import json_loads, run_git, utc_now

TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kt",
    ".kts",
    ".lua",
    ".md",
    ".mdx",
    ".mjs",
    ".cjs",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "restructuredtext",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
}

JS_IMPORT_RE = re.compile(
    r"(?:import\s+(?:[^'\"]+?\s+from\s+)?|export\s+[^'\"]*?\s+from\s+|require\s*\(|import\s*\()"
    r"['\"](?P<path>[^'\"]+)['\"]",
    re.MULTILINE,
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\((?P<path>[^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*\s*$", re.MULTILINE)
MARKDOWN_LINK_LABEL_RE = re.compile(r"!?\[(?P<label>[^\]]+)\]\([^)]+\)")
JS_SYMBOL_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+(?P<name>[A-Za-z_$][\w$]*)"
    r"|(?:export\s+)?(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
WIKI_LINK_RE = re.compile(r"!?(?:\[\[)(?P<path>[^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s+(.+?)\s*$", re.MULTILINE)

GENERIC_BINARY_NAMES = {"license", "copying", "notice", "makefile", "dockerfile"}


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    absolute_path: Path
    size: int
    mtime_ns: int
    digest: str
    language: str
    is_text: bool
    skipped_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScanReport:
    project_id: str
    total_files: int
    changed_files: int
    deleted_files: int
    physical_nodes: int
    physical_edges: int
    semantic_nodes: int = 0
    semantic_edges: int = 0


def language_for(path: str) -> str:
    pure = PurePosixPath(path)
    suffix = pure.suffix.lower()
    if suffix:
        return LANGUAGE_BY_EXTENSION.get(suffix, suffix.lstrip(".") or "unknown")
    return pure.name.lower() if pure.name.lower() in GENERIC_BINARY_NAMES else "unknown"


def _is_probably_text(path: Path, max_file_bytes: int) -> tuple[bool, str | None]:
    try:
        size = path.stat().st_size
    except OSError:
        return False, "无法读取文件状态"
    if size > max_file_bytes:
        return False, f"文件超过 {max_file_bytes} 字节"
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS or path.name.lower() in GENERIC_BINARY_NAMES:
        return True, None
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False, "无法读取文件"
    if b"\x00" in sample:
        return False, "二进制文件"
    return True, None


def _discover_with_git(root: Path) -> list[str] | None:
    completed = run_git(root, "ls-files", "-co", "--exclude-standard", "-z", timeout=20)
    if completed is None or completed.returncode != 0:
        return None
    return sorted(
        {
            item.replace("\\", "/")
            for item in completed.stdout.split("\0")
            if item and (root / item).is_file()
        }
    )


def discover_files(root: Path, settings: Settings) -> list[str]:
    git_files = _discover_with_git(root)
    if git_files is not None:
        return [path for path in git_files if Path(path).name not in settings.ignored_files]

    discovered: list[str] = []
    ignored_dirs = set(settings.ignored_directories)
    ignored_files = set(settings.ignored_files)
    for current_root, directories, files in os.walk(root):
        directories[:] = [directory for directory in directories if directory not in ignored_dirs]
        current = Path(current_root)
        for filename in files:
            if filename in ignored_files:
                continue
            absolute = current / filename
            try:
                relative = absolute.relative_to(root).as_posix()
            except ValueError:
                continue
            discovered.append(relative)
    return sorted(set(discovered))


def _digest(path: Path) -> str:
    hasher = hashlib.blake2s()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None
    except OSError:
        return None


def _normalize_relative(path: PurePosixPath) -> str | None:
    parts: list[str] = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _candidate_variants(base: str, extensions: Iterable[str]) -> list[str]:
    candidates = [base]
    pure = PurePosixPath(base)
    if not pure.suffix:
        candidates.extend(f"{base}{extension}" for extension in extensions)
        candidates.extend(f"{base}/index{extension}" for extension in extensions)
        candidates.extend(f"{base}/__init__.py" for _ in [0])
    return list(dict.fromkeys(candidate.strip("/") for candidate in candidates if candidate))


def resolve_relative_reference(
    source_path: str,
    reference: str,
    all_paths: set[str],
    *,
    extensions: tuple[str, ...],
) -> str | None:
    reference = reference.strip().split("#", 1)[0].split("?", 1)[0]
    if not reference or reference.startswith(("http://", "https://", "mailto:", "data:", "obsidian://")):
        return None
    source_parent = PurePosixPath(source_path).parent
    if reference.startswith("/"):
        normalized = _normalize_relative(PurePosixPath(reference.lstrip("/")))
    else:
        normalized = _normalize_relative(source_parent / reference)
    if normalized is None:
        return None
    for candidate in _candidate_variants(normalized, extensions):
        if candidate in all_paths:
            return candidate
    return None


def _resolve_python_module(
    source_path: str,
    module: str | None,
    level: int,
    aliases: list[str],
    all_paths: set[str],
) -> list[str]:
    source_parent = PurePosixPath(source_path).parent
    modules: list[str] = []
    if module:
        modules.append(module)
    if not module:
        modules.extend(alias for alias in aliases if alias != "*")
    elif aliases:
        modules.extend(f"{module}.{alias}" for alias in aliases if alias != "*")

    prefixes: list[PurePosixPath]
    if level:
        base = source_parent
        for _ in range(max(level - 1, 0)):
            base = base.parent
        prefixes = [base]
    else:
        prefixes = [PurePosixPath(""), PurePosixPath("src"), PurePosixPath("lib"), PurePosixPath("app")]

    resolved: list[str] = []
    for module_name in modules or [""]:
        module_path = PurePosixPath(*[part for part in module_name.split(".") if part])
        for prefix in prefixes:
            normalized = _normalize_relative(prefix / module_path)
            if normalized is None:
                continue
            for candidate in _candidate_variants(normalized, (".py",)):
                if candidate in all_paths:
                    resolved.append(candidate)
                    break
    return list(dict.fromkeys(resolved))


def parse_python_dependencies(source_path: str, text: str, all_paths: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], []
    edges: list[tuple[str, str]] = []
    external: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_python_module(source_path, alias.name, 0, [], all_paths)
                if resolved:
                    edges.extend(("imports", target) for target in resolved)
                else:
                    external.append(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            aliases = [alias.name for alias in node.names]
            resolved = _resolve_python_module(source_path, node.module, node.level, aliases, all_paths)
            if resolved:
                edges.extend(("imports", target) for target in resolved)
            elif node.module:
                external.append(node.module.split(".", 1)[0])
    return list(dict.fromkeys(edges)), sorted(set(external))


def parse_javascript_dependencies(source_path: str, text: str, all_paths: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
    edges: list[tuple[str, str]] = []
    external: list[str] = []
    extensions = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")
    for match in JS_IMPORT_RE.finditer(text):
        reference = match.group("path")
        if reference.startswith(".") or reference.startswith("/"):
            target = resolve_relative_reference(source_path, reference, all_paths, extensions=extensions)
            if target:
                edges.append(("imports", target))
        else:
            external.append(reference.split("/", 1)[0] if not reference.startswith("@") else "/".join(reference.split("/")[:2]))
    return list(dict.fromkeys(edges)), sorted(set(external))


def parse_markdown_dependencies(source_path: str, text: str, all_paths: set[str]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    extensions = (".md", ".mdx", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf")
    basename_index: dict[str, list[str]] = {}
    stem_index: dict[str, list[str]] = {}
    for path in all_paths:
        pure = PurePosixPath(path)
        basename_index.setdefault(pure.name.lower(), []).append(path)
        stem_index.setdefault(pure.stem.lower(), []).append(path)

    for regex in (MARKDOWN_LINK_RE, WIKI_LINK_RE):
        for match in regex.finditer(text):
            reference = match.group("path").strip()
            target = resolve_relative_reference(source_path, reference, all_paths, extensions=extensions)
            if target is None:
                key = PurePosixPath(reference).name.lower()
                candidates = basename_index.get(key, []) or stem_index.get(PurePosixPath(key).stem, [])
                if len(candidates) == 1:
                    target = candidates[0]
            if target and target != source_path:
                edges.append(("references", target))
    return list(dict.fromkeys(edges))


def _test_target(source_path: str, all_paths: set[str]) -> str | None:
    pure = PurePosixPath(source_path)
    name = pure.name
    candidate_names: list[str] = []
    if name.startswith("test_") and pure.suffix == ".py":
        candidate_names.append(name[len("test_") :])
    for marker in (".test.", ".spec."):
        if marker in name:
            candidate_names.append(name.replace(marker, ".", 1))
    if not candidate_names:
        return None

    basename_matches: list[str] = []
    for candidate_name in candidate_names:
        basename_matches.extend(path for path in all_paths if PurePosixPath(path).name == candidate_name)
    if not basename_matches:
        return None

    source_parts = set(pure.parts)
    scored = sorted(
        set(basename_matches),
        key=lambda path: (
            -len(source_parts.intersection(PurePosixPath(path).parts)),
            len(PurePosixPath(path).parts),
            path,
        ),
    )
    return scored[0]


def _text_metadata(path: str, text: str) -> dict[str, object]:
    """提取低成本、可搜索的结构化元数据，不保存整段文件正文。"""

    suffix = PurePosixPath(path).suffix.lower()
    metadata: dict[str, object] = {}
    if suffix in {".md", ".mdx", ".rst"}:
        headings = [
            re.sub(r"\s+", " ", match.group("title")).strip()[:160]
            for match in MARKDOWN_HEADING_RE.finditer(text)
        ][:30]
        labels = [
            re.sub(r"\s+", " ", match.group("label")).strip()[:100]
            for match in MARKDOWN_LINK_LABEL_RE.finditer(text)
        ][:30]
        if headings:
            metadata["title"] = headings[0]
            metadata["headings"] = headings
        if labels:
            metadata["link_labels"] = list(dict.fromkeys(labels))
    elif suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            docstring = ast.get_docstring(tree, clean=True)
            if docstring:
                metadata["title"] = docstring.splitlines()[0].strip()[:160]
            symbols = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ][:80]
            if symbols:
                metadata["symbols"] = symbols
    elif suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"}:
        symbols = [
            match.group("name") or match.group("var")
            for match in JS_SYMBOL_RE.finditer(text)
        ][:80]
        if symbols:
            metadata["symbols"] = list(dict.fromkeys(symbols))
    elif suffix == ".json" and PurePosixPath(path).name in {"package.json", "manifest.json"}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            if parsed.get("name"):
                metadata["title"] = str(parsed["name"])[:160]
            if parsed.get("description"):
                metadata["description"] = str(parsed["description"])[:500]
    return metadata


def _file_node_data(
    record: FileRecord,
    *,
    external_dependencies: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "path": record.path,
        "language": record.language,
        "size": record.size,
        "mtime_ns": record.mtime_ns,
        "digest": record.digest,
        "is_text": record.is_text,
    }
    if record.skipped_reason:
        data["skipped_reason"] = record.skipped_reason
    if external_dependencies:
        data["external_dependencies"] = external_dependencies[:100]
    if metadata:
        data.update(metadata)
    return data
