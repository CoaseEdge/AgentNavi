from __future__ import annotations

import re
from pathlib import PurePosixPath

from .api import ExtractionContext, ExtractionResult, FileDependency
from .code_common import _resolve_qualified_name, _resolve_suffix_path, _resource_symbols, _unique

def _go_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    imports = re.findall(r'(?m)^\s*(?:[A-Za-z_][\w]*\s+)?["`]([^"`]+)["`]\s*$', _go_import_body(text))
    dependencies: list[FileDependency] = []
    external: list[str] = []
    module_name = ""
    go_mod = context.project_root / "go.mod"
    try:
        match = re.search(r"(?m)^\s*module\s+(\S+)", go_mod.read_text(encoding="utf-8"))
        module_name = match.group(1).strip() if match else ""
    except OSError:
        pass

    for item in imports:
        target: str | None = None
        if item.startswith("."):
            target = _resolve_suffix_path(item, context, extensions=(".go",))
        elif module_name and item.startswith(module_name):
            package_path = item[len(module_name) :].strip("/")
            package_files = sorted(
                path
                for path in context.all_paths
                if PurePosixPath(path).suffix == ".go"
                and not PurePosixPath(path).name.endswith("_test.go")
                and PurePosixPath(path).parent.as_posix().endswith(package_path)
            )
            if package_files:
                target = package_files[0]
        if target:
            dependencies.append(FileDependency("imports", target, {"module": item}))
        else:
            external.append(item)

    symbols: list[tuple[str, str]] = []
    symbols.extend(("type", name) for name in re.findall(r"(?m)^\s*type\s+([A-Za-z_]\w*)\s+", text))
    symbols.extend(("function", name) for name in re.findall(r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", text))
    package = re.search(r"(?m)^\s*package\s+([A-Za-z_]\w*)", text)
    metadata: dict[str, object] = {"symbols": [name for _, name in symbols[:100]]}
    if package:
        metadata["package"] = package.group(1)
    return ExtractionResult(
        "builtin.code.go",
        "1",
        metadata=metadata,
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


def _go_import_body(text: str) -> str:
    lines: list[str] = []
    for single in re.finditer(r'(?m)^\s*import\s+(?:[A-Za-z_]\w*\s+)?(["`][^"`]+["`])', text):
        lines.append(single.group(1))
    for block in re.finditer(r"(?ms)^\s*import\s*\((.*?)\)", text):
        lines.extend(block.group(1).splitlines())
    return "\n".join(lines)


def _rust_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    dependencies: list[FileDependency] = []
    external: list[str] = []
    parent = PurePosixPath(context.relative_path).parent
    for module in re.findall(r"(?m)^\s*(?:pub\s+)?mod\s+([A-Za-z_]\w*)\s*;", text):
        for candidate in ((parent / f"{module}.rs").as_posix(), (parent / module / "mod.rs").as_posix()):
            if candidate in context.all_paths:
                dependencies.append(FileDependency("imports", candidate, {"module": module}))
                break
    for path in re.findall(r"(?m)^\s*(?:pub\s+)?use\s+([^;]+);", text):
        target = _resolve_qualified_name(path.split("::{", 1)[0], context, suffixes=(".rs",))
        if target:
            dependencies.append(FileDependency("imports", target, {"use": path.strip()}))
        else:
            root = re.split(r"::", path.strip(), maxsplit=1)[0]
            if root not in {"crate", "self", "super", "std", "core", "alloc"}:
                external.append(root)
    external.extend(re.findall(r"(?m)^\s*extern\s+crate\s+([A-Za-z_]\w*)", text))
    symbols: list[tuple[str, str]] = []
    for kind, pattern in (
        ("function", r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),
        ("struct", r"(?m)^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)"),
        ("enum", r"(?m)^\s*(?:pub\s+)?enum\s+([A-Za-z_]\w*)"),
        ("trait", r"(?m)^\s*(?:pub\s+)?trait\s+([A-Za-z_]\w*)"),
    ):
        symbols.extend((kind, name) for name in re.findall(pattern, text))
    return ExtractionResult(
        "builtin.code.rust",
        "1",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )


