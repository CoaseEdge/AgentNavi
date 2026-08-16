from __future__ import annotations

import re
from pathlib import PurePosixPath

from .api import ExtractionContext, ExtractionResult, FileDependency
from .code_common import _resolve_qualified_name, _resolve_suffix_path, _resource_symbols, _unique


def _path_ends_with_parts(path: PurePosixPath, suffix_parts: tuple[str, ...]) -> bool:
    if not suffix_parts:
        return path.as_posix() in {"", "."}
    path_parts = tuple(part.lower() for part in path.parts)
    expected = tuple(part.lower() for part in suffix_parts)
    return len(path_parts) >= len(expected) and path_parts[-len(expected) :] == expected


def _go_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    text = context.text
    imports = re.findall(r'(?m)^\s*(?:[A-Za-z_][\w]*\s+)?["`]([^"`]+)["`]\s*$', _go_import_body(text))
    dependencies: list[FileDependency] = []
    external: list[str] = []
    warnings: list[str] = []
    ambiguous_local_imports: list[dict[str, object]] = []
    module_name = ""
    go_mod = context.project_root / "go.mod"
    try:
        match = re.search(r"(?m)^\s*module\s+(\S+)", go_mod.read_text(encoding="utf-8"))
        module_name = match.group(1).strip().rstrip("/") if match else ""
    except OSError:
        pass

    for item in imports:
        target: str | None = None
        is_local_module = bool(
            module_name and (item == module_name or item.startswith(f"{module_name}/"))
        )
        if item.startswith("."):
            target = _resolve_suffix_path(item, context, extensions=(".go",))
        elif is_local_module:
            package_path = item[len(module_name) :].strip("/")
            package_parts = PurePosixPath(package_path).parts if package_path else ()
            package_files = sorted(
                path
                for path in context.all_paths
                if PurePosixPath(path).suffix.lower() == ".go"
                and not PurePosixPath(path).name.endswith("_test.go")
                and _path_ends_with_parts(PurePosixPath(path).parent, package_parts)
            )
            if len(package_files) == 1:
                target = package_files[0]
            elif len(package_files) > 1:
                ambiguous_local_imports.append(
                    {
                        "module": item,
                        "candidate_count": len(package_files),
                        "candidates": package_files[:20],
                    }
                )
                warnings.append(
                    f"Go 本地包 {item} 对应 {len(package_files)} 个文件，"
                    "未建立任意文件级依赖"
                )
                continue
        if target:
            dependencies.append(FileDependency("imports", target, {"module": item}))
        elif not is_local_module:
            external.append(item)

    symbols: list[tuple[str, str]] = []
    symbols.extend(("type", name) for name in re.findall(r"(?m)^\s*type\s+([A-Za-z_]\w*)\s+", text))
    symbols.extend(("function", name) for name in re.findall(r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", text))
    package = re.search(r"(?m)^\s*package\s+([A-Za-z_]\w*)", text)
    metadata: dict[str, object] = {"symbols": [name for _, name in symbols[:100]]}
    if package:
        metadata["package"] = package.group(1)
    if ambiguous_local_imports:
        metadata["ambiguous_local_imports"] = ambiguous_local_imports
    return ExtractionResult(
        "builtin.code.go",
        "2",
        metadata=metadata,
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
        warnings=tuple(dict.fromkeys(warnings)),
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
        "2",
        metadata={"symbols": [name for _, name in symbols[:100]]},
        roles=("source_code",),
        dependencies=tuple(dependencies),
        external_dependencies=_unique(external),
        resources=_resource_symbols(symbols),
    )
