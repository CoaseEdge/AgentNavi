from __future__ import annotations

import re

from .api import ExtractionContext, ExtractionResult, FileDependency
from .code_common import _resolve_suffix_path, _resource_symbols, _unique

def _ruby_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    dependencies: list[FileDependency] = []
    external: list[str] = []
    for kind, reference in re.findall(r"(?m)^\s*(require_relative|require|load)\s*[\( ]?['\"]([^'\"]+)['\"]", text):
        target = None
        if kind == "require_relative" or reference.startswith("."):
            target = _resolve_suffix_path(reference, context, extensions=(".rb",))
        if target:
            dependencies.append(FileDependency("imports", target, {"require": reference}))
        else:
            external.append(reference)
    symbols: list[tuple[str, str]] = []
    symbols.extend(("class", name) for name in re.findall(r"(?m)^\s*class\s+([A-Za-z_:]\w*(?:::\w+)*)", text))
    symbols.extend(("module", name) for name in re.findall(r"(?m)^\s*module\s+([A-Za-z_:]\w*(?:::\w+)*)", text))
    symbols.extend(("function", name) for name in re.findall(r"(?m)^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?=]?)", text))
    return ExtractionResult(
        "builtin.code.ruby",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _php_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    dependencies: list[FileDependency] = []
    external: list[str] = []
    for reference in re.findall(r"(?i)\b(?:include|include_once|require|require_once)\s*\(?\s*['\"]([^'\"]+)['\"]", text):
        target = _resolve_suffix_path(reference, context, extensions=(".php", ".inc"))
        if target:
            dependencies.append(FileDependency("imports", target, {"include": reference}))
    external.extend(item.split("\\", 1)[0] for item in re.findall(r"(?m)^\s*use\s+([A-Za-z_\\][\w\\]+)", text))
    symbols: list[tuple[str, str]] = []
    symbols.extend(("class", name) for name in re.findall(r"\bclass\s+([A-Za-z_]\w*)", text))
    symbols.extend(("interface", name) for name in re.findall(r"\binterface\s+([A-Za-z_]\w*)", text))
    symbols.extend(("function", name) for name in re.findall(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", text))
    return ExtractionResult(
        "builtin.code.php",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _swift_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    external = re.findall(r"(?m)^\s*import\s+([A-Za-z_]\w*)", text)
    symbols: list[tuple[str, str]] = []
    for kind, pattern in (
        ("class", r"\bclass\s+([A-Za-z_]\w*)"),
        ("struct", r"\bstruct\s+([A-Za-z_]\w*)"),
        ("protocol", r"\bprotocol\s+([A-Za-z_]\w*)"),
        ("enum", r"\benum\s+([A-Za-z_]\w*)"),
        ("function", r"\bfunc\s+([A-Za-z_]\w*)\s*\("),
    ):
        symbols.extend((kind, name) for name in re.findall(pattern, text))
    return ExtractionResult(
        "builtin.code.swift",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _shell_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    dependencies: list[FileDependency] = []
    for reference in re.findall(r"(?m)^\s*(?:source|\.)\s+['\"]?([^\s'\"]+)", text):
        target = _resolve_suffix_path(reference, context, extensions=(".sh", ".bash", ".zsh"))
        if target:
            dependencies.append(FileDependency("sources", target, {"source": reference}))
    symbols = [("function", name) for name in re.findall(r"(?m)^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\)\s*\{", text)]
    return ExtractionResult(
        "builtin.code.shell",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code", "script"),
        dependencies=tuple(dependencies),
        resources=_resource_symbols(symbols),
    )


def _lua_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    external = re.findall(r"\brequire\s*\(?\s*['\"]([^'\"]+)['\"]", text)
    symbols = [("function", name) for name in re.findall(r"(?m)^\s*(?:local\s+)?function\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", text)]
    return ExtractionResult(
        "builtin.code.lua",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


