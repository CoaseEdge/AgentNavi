from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TextIO

from .api import ExtractionContext, FileDependency
from ..scan_support import resolve_relative_reference

STRUCTURED_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".ndjson",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
    ".csv",
    ".tsv",
    ".sql",
    ".ipynb",
    ".xlsx",
}

PATH_KEY_RE = re.compile(
    r"(?:path|paths|file|files|source|sources|input|inputs|output|outputs|include|includes|"
    r"extends|schema|ref|href|src|uri|url|dataset|data_file)$",
    re.IGNORECASE,
)
PATH_VALUE_RE = re.compile(
    r"^(?:\.{0,2}/|/)?[^\n\r]+\.(?:json|jsonl|ya?ml|toml|ini|cfg|conf|xml|csv|tsv|sql|"
    r"txt|md|py|js|ts|go|rs|java|kt|cs|c|cpp|h|hpp|rb|php|swift|sh|ipynb|xlsx|npy|npz|"
    r"parquet|feather|arrow|h5|hdf5|nc|nc4|mat|fits|fit|fts|sqlite|sqlite3|db)$",
    re.IGNORECASE,
)


class BoundedLineIterator:
    """Iterate text lines without materializing an unbounded line or stream.

    Oversized physical lines are drained in bounded chunks and represented as a
    blank line. Once the total character budget is exhausted iteration stops.
    """

    def __init__(self, handle: TextIO, *, max_chars: int, max_total_chars: int) -> None:
        self.handle = handle
        self.max_chars = max(1, int(max_chars))
        self.max_total_chars = max(self.max_chars, int(max_total_chars))
        self.oversized_lines = 0
        self.total_chars = 0
        self.total_budget_reached = False
        self._stopped = False

    def __iter__(self) -> BoundedLineIterator:
        return self

    def _read_chunk(self) -> str:
        chunk = self.handle.readline(self.max_chars + 1)
        self.total_chars += len(chunk)
        if self.total_chars > self.max_total_chars:
            self.total_budget_reached = True
            self._stopped = True
        return chunk

    def __next__(self) -> str:
        if self._stopped:
            raise StopIteration
        line = self._read_chunk()
        if line == "":
            raise StopIteration
        if self.total_budget_reached:
            raise StopIteration
        if len(line) <= self.max_chars:
            return line

        while line and not line.endswith(("\n", "\r")):
            line = self._read_chunk()
            if self.total_budget_reached:
                break
        self.oversized_lines += 1
        return "\n"


def _resolve_reference(context: ExtractionContext, reference: str) -> str | None:
    reference = reference.strip().strip("'\"")
    if not reference or reference.startswith(("http://", "https://", "s3://", "gs://", "azure://")):
        return None
    extensions = tuple(
        sorted(
            {
                PurePosixPath(path).suffix.lower()
                for path in context.all_paths
                if PurePosixPath(path).suffix
            }
        )
    )
    target = resolve_relative_reference(
        context.relative_path,
        reference,
        set(context.all_paths),
        extensions=extensions,
    )
    if target:
        return target
    basename = PurePosixPath(reference.split("#", 1)[0]).name.lower()
    candidates = [path for path in context.all_paths if PurePosixPath(path).name.lower() == basename]
    return candidates[0] if len(candidates) == 1 else None


def _path_dependencies_from_value(
    value: object,
    context: ExtractionContext,
    *,
    key_hint: str = "",
    limit: int = 200,
) -> list[FileDependency]:
    output: list[FileDependency] = []

    def visit(item: object, key: str, depth: int) -> None:
        if len(output) >= limit or depth > 12:
            return
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                visit(child_value, str(child_key), depth + 1)
            return
        if isinstance(item, list):
            for child in item[:1000]:
                visit(child, key, depth + 1)
            return
        if not isinstance(item, str):
            return
        candidate = item.strip()
        if not candidate or not (PATH_KEY_RE.search(key) or PATH_VALUE_RE.match(candidate) or candidate.startswith(("./", "../"))):
            return
        target = _resolve_reference(context, candidate)
        if target:
            output.append(
                FileDependency(
                    "references",
                    target,
                    {"key": key, "raw_reference": candidate[:500]},
                )
            )

    visit(value, key_hint, 0)
    unique: dict[tuple[str, str], FileDependency] = {}
    for dependency in output:
        unique.setdefault((dependency.relation, dependency.target_path), dependency)
    return list(unique.values())


def _roles_for_structured(context: ExtractionContext) -> tuple[str, ...]:
    name = context.name.lower()
    path = f"/{context.relative_path.lower()}"
    if context.suffix in {".csv", ".tsv", ".jsonl", ".ndjson", ".xlsx"}:
        return ("dataset", "structured_data")
    if context.suffix == ".sql":
        return ("source_code", "data_query")
    if context.suffix == ".ipynb":
        return ("notebook", "analysis", "source_code")
    if any(token in name for token in ("config", "settings", "manifest", "schema", "lock")) or any(
        token in path for token in ("/config/", "/configs/", "/schemas/")
    ):
        return ("configuration", "structured_data")
    return ("structured_data",)
