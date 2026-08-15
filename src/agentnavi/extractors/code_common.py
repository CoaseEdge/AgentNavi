from __future__ import annotations

from pathlib import PurePosixPath

from .api import ExtractedResource, ExtractionContext
from ..scan_support import resolve_relative_reference

SUPPORTED_EXTENSIONS = {
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".sh",
    ".bash",
    ".zsh",
    ".lua",
}


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _resource_symbols(symbols: list[tuple[str, str]]) -> tuple[ExtractedResource, ...]:
    output: list[ExtractedResource] = []
    for kind, name in symbols[:100]:
        clean = name.strip()
        if not clean:
            continue
        output.append(
            ExtractedResource(
                kind="symbol",
                key=f"symbol:{kind}:{clean}",
                label=clean,
                data={"symbol_kind": kind},
            )
        )
    return tuple(output)


def _basename_index(all_paths: frozenset[str], suffixes: set[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in all_paths:
        pure = PurePosixPath(path)
        if pure.suffix.lower() not in suffixes:
            continue
        index.setdefault(pure.name.lower(), []).append(path)
    return index


def _resolve_suffix_path(
    reference: str,
    context: ExtractionContext,
    *,
    extensions: tuple[str, ...],
) -> str | None:
    return resolve_relative_reference(
        context.relative_path,
        reference,
        set(context.all_paths),
        extensions=extensions,
    )


def _resolve_qualified_name(
    qualified: str,
    context: ExtractionContext,
    *,
    suffixes: tuple[str, ...],
) -> str | None:
    """Resolve package-like names by comparing project-relative suffixes.

    This is deliberately conservative: a result is returned only when exactly
    one plausible project file matches.
    """

    cleaned = qualified.strip().strip(";").replace("::", "/").replace(".", "/")
    cleaned = cleaned.replace("\\", "/").strip("/")
    if not cleaned:
        return None
    parts = [part for part in cleaned.split("/") if part and part not in {"crate", "self", "super"}]
    if not parts:
        return None
    candidates: list[str] = []
    for path in context.all_paths:
        pure = PurePosixPath(path)
        if pure.suffix.lower() not in suffixes:
            continue
        stem_parts = list(pure.with_suffix("").parts)
        for width in range(min(len(parts), len(stem_parts)), 0, -1):
            if [item.lower() for item in stem_parts[-width:]] == [item.lower() for item in parts[-width:]]:
                candidates.append(path)
                break
        if pure.stem.lower() == parts[-1].lower():
            candidates.append(path)
    unique = sorted(set(candidates))
    return unique[0] if len(unique) == 1 else None


