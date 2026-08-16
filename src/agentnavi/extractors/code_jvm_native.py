from __future__ import annotations

import re

from .api import ExtractionContext, ExtractionResult, FileDependency
from .code_common import _resolve_qualified_name, _resolve_suffix_path, _resource_symbols, _unique

def _java_kotlin_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    suffixes = (".java", ".kt", ".kts")
    dependencies: list[FileDependency] = []
    external: list[str] = []
    for qualified in re.findall(r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)", text):
        raw = qualified.removesuffix(".*")
        target = _resolve_qualified_name(raw, context, suffixes=suffixes)
        if target:
            dependencies.append(FileDependency("imports", target, {"import": qualified}))
        else:
            external.append(raw.split(".", 1)[0])
    package = re.search(r"(?m)^\s*package\s+([\w.]+)", text)
    symbols: list[tuple[str, str]] = []
    for kind, pattern in (
        ("class", r"\bclass\s+([A-Za-z_]\w*)"),
        ("interface", r"\binterface\s+([A-Za-z_]\w*)"),
        ("enum", r"\benum\s+([A-Za-z_]\w*)"),
        ("object", r"\bobject\s+([A-Za-z_]\w*)"),
        ("function", r"\bfun\s+([A-Za-z_]\w*)\s*\("),
    ):
        symbols.extend((kind, name) for name in re.findall(pattern, text))
    metadata: dict[str, object] = {"symbols": [name for _, name in symbols[:100]]}
    if package:
        metadata["package"] = package.group(1)
    language = "kotlin" if context.suffix in {".kt", ".kts"} else "java"
    return ExtractionResult(
        f"builtin.code.{language}",
        "1",
        metadata=metadata,
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _c_family_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    dependencies: list[FileDependency] = []
    external: list[str] = []
    for quote, reference in re.findall(r"(?m)^\s*#\s*include\s*([<\"])([^>\"]+)[>\"]", text):
        target = _resolve_suffix_path(
            reference,
            context,
            extensions=(".h", ".hpp", ".c", ".cc", ".cpp"),
        )
        if target:
            dependencies.append(FileDependency("includes", target, {"include": reference}))
        elif quote == "<":
            external.append(reference)
    symbols: list[tuple[str, str]] = []
    symbols.extend(("type", name) for name in re.findall(r"\b(?:struct|class|enum)\s+([A-Za-z_]\w*)", text))
    # Deliberately conservative: only declarations with a body are treated as functions.
    symbols.extend(
        ("function", name)
        for name in re.findall(
            r"(?m)^\s*(?:[A-Za-z_]\w*[\w\s:*&<>]*\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{",
            text,
        )
        if name not in {"if", "for", "while", "switch"}
    )
    language = {".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp"}[context.suffix]
    return ExtractionResult(
        f"builtin.code.{language}",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _csharp_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    external = [item.split(".", 1)[0] for item in re.findall(r"(?m)^\s*using\s+(?:static\s+)?([\w.]+)\s*;", text)]
    namespace = re.search(r"\bnamespace\s+([\w.]+)", text)
    symbols: list[tuple[str, str]] = []
    for kind, pattern in (
        ("class", r"\bclass\s+([A-Za-z_]\w*)"),
        ("interface", r"\binterface\s+([A-Za-z_]\w*)"),
        ("record", r"\brecord\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)"),
        ("enum", r"\benum\s+([A-Za-z_]\w*)"),
    ):
        symbols.extend((kind, name) for name in re.findall(pattern, text))
    metadata: dict[str, object] = {"symbols": [name for _, name in symbols[:100]]}
    if namespace:
        metadata["namespace"] = namespace.group(1)
    return ExtractionResult(
        "builtin.code.csharp",
        "1",
        metadata=metadata,
        roles=("source_code",),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


