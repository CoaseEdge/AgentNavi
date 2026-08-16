from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter

from .api import ExtractedResource, ExtractionContext, ExtractionResult, FileDependency, ResourceRelation
from .structured_common import BoundedLineIterator
from ..scan_support import parse_javascript_dependencies, parse_markdown_dependencies, parse_python_dependencies


def _infer_scalar_type(value: str) -> str:
    stripped = value.strip()
    if stripped == "":
        return "empty"
    if stripped.lower() in {"true", "false"}:
        return "boolean"
    try:
        int(stripped)
        return "integer"
    except ValueError:
        pass
    try:
        float(stripped)
        return "number"
    except ValueError:
        return "string"


def _csv_extract(context: ExtractionContext) -> ExtractionResult:
    delimiter = "\t" if context.suffix == ".tsv" else ","
    handle: io.TextIOBase | None = None
    line_iterator: BoundedLineIterator | None = None
    warnings: list[str] = []
    previous_field_limit = csv.field_size_limit()
    header: list[str] = []
    column_types: list[Counter[str]] = []
    row_count = 0
    truncated = False
    try:
        if context.text is not None:
            handle = io.StringIO(context.text)
        else:
            handle = context.absolute_path.open("r", encoding="utf-8-sig", errors="replace", newline="")
        sample = handle.read(min(65536, max(1, context.max_line_chars)))
        handle.seek(0)
        if context.suffix == ".csv":
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                pass

        csv.field_size_limit(max(previous_field_limit, max(1, context.max_line_chars)))
        line_iterator = BoundedLineIterator(
            handle,
            max_chars=context.max_line_chars,
            max_total_chars=context.max_stream_chars,
        )
        reader = csv.reader(line_iterator, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        column_types = [Counter() for _ in header]
        for row in reader:
            if not row:
                continue
            row_count += 1
            if row_count <= 5000:
                for index, value in enumerate(row[: len(header)]):
                    column_types[index][_infer_scalar_type(value)] += 1
            if row_count >= 100_000:
                truncated = True
                break
    except (OSError, csv.Error) as exc:
        warnings.append(f"CSV/TSV 读取未完成：{exc}")
    finally:
        csv.field_size_limit(previous_field_limit)
        if handle is not None:
            handle.close()

    oversized_lines = line_iterator.oversized_lines if line_iterator is not None else 0
    total_chars = line_iterator.total_chars if line_iterator is not None else 0
    stream_budget_reached = (
        line_iterator.total_budget_reached if line_iterator is not None else False
    )
    if oversized_lines:
        warnings.append(
            f"发现 {oversized_lines} 个超长记录，单行超过 {context.max_line_chars} 字符，已跳过"
        )
    if stream_budget_reached:
        warnings.append(
            f"CSV/TSV 扫描超过 {context.max_stream_chars} 字符总预算，已提前停止"
        )
    if truncated:
        warnings.append("行数超过 100000，仅记录下限")

    resources = []
    for index, name in enumerate(header[:500]):
        inferred = column_types[index].most_common(1)[0][0] if column_types[index] else "unknown"
        resources.append(
            ExtractedResource(
                "column",
                f"column:{index}:{name}",
                name or f"column_{index + 1}",
                {"index": index, "inferred_type": inferred},
            )
        )
    return ExtractionResult(
        "builtin.structured.csv",
        "2",
        metadata={
            "delimiter": delimiter,
            "columns": header[:500],
            "column_count": len(header),
            "row_count": row_count,
            "oversized_lines": oversized_lines,
            "stream_chars_read": total_chars,
            "stream_budget_reached": stream_budget_reached,
            "row_count_truncated": truncated,
            "streamed": context.text is None,
        },
        roles=("dataset", "tabular_data"),
        resources=tuple(resources),
        warnings=tuple(dict.fromkeys(warnings)),
    )


SQL_READ_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.$\-]*(?:\.[A-Za-z_][\w$\-]*)?)", re.IGNORECASE)
SQL_WRITE_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\s+"
    r"([A-Za-z_][\w.$\-]*(?:\.[A-Za-z_][\w$\-]*)?)",
    re.IGNORECASE,
)


def _sql_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    reads = list(dict.fromkeys(SQL_READ_RE.findall(context.text)))[:500]
    writes = list(dict.fromkeys(SQL_WRITE_RE.findall(context.text)))[:500]
    resources: list[ExtractedResource] = []
    relations: list[ResourceRelation] = []
    for table in dict.fromkeys((*reads, *writes)):
        key = f"table:{table}"
        resources.append(ExtractedResource("database_table", key, table, {}))
        relation = "reads" if table in reads else "writes"
        relations.append(ResourceRelation(relation, key))
        if table in reads and table in writes:
            relations.append(ResourceRelation("writes", key))
    return ExtractionResult(
        "builtin.structured.sql",
        "2",
        metadata={"read_tables": reads, "write_tables": writes},
        roles=("data_query", "source_code"),
        external_dependencies=tuple(dict.fromkeys((*reads, *writes))),
        resources=tuple(resources),
        resource_relations=tuple(relations),
    )


def _notebook_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    try:
        value = json.loads(context.text)
    except json.JSONDecodeError as exc:
        return ExtractionResult(
            "builtin.structured.notebook",
            "2",
            roles=("notebook", "analysis"),
            warnings=(f"Notebook JSON 解析失败：{exc.msg}",),
        )
    cells = value.get("cells", []) if isinstance(value, dict) else []
    dependencies: list[FileDependency] = []
    external: list[str] = []
    resources: list[ExtractedResource] = []
    code_count = 0
    markdown_count = 0
    kernel = ""
    if isinstance(value, dict):
        kernel = str(((value.get("metadata") or {}).get("kernelspec") or {}).get("language") or "")
    for index, cell in enumerate(cells[:1000]):
        if not isinstance(cell, dict):
            continue
        kind = str(cell.get("cell_type") or "unknown")
        source_value = cell.get("source", "")
        source = "".join(source_value) if isinstance(source_value, list) else str(source_value)
        resources.append(
            ExtractedResource(
                "notebook_cell",
                f"cell:{index}",
                f"Cell {index + 1} ({kind})",
                {
                    "index": index,
                    "cell_type": kind,
                    "execution_count": cell.get("execution_count"),
                    "summary": source.strip().splitlines()[0][:160] if source.strip() else "",
                },
            )
        )
        if kind == "code":
            code_count += 1
            if kernel.lower() in {"python", "python3", ""}:
                parsed, parsed_external = parse_python_dependencies(
                    context.relative_path,
                    source,
                    set(context.all_paths),
                )
                dependencies.extend(FileDependency(relation, target, {"cell": index}) for relation, target in parsed)
                external.extend(parsed_external)
            elif kernel.lower() in {"javascript", "typescript", "node"}:
                parsed, parsed_external = parse_javascript_dependencies(
                    context.relative_path,
                    source,
                    set(context.all_paths),
                )
                dependencies.extend(FileDependency(relation, target, {"cell": index}) for relation, target in parsed)
                external.extend(parsed_external)
        elif kind == "markdown":
            markdown_count += 1
            parsed = parse_markdown_dependencies(context.relative_path, source, set(context.all_paths))
            dependencies.extend(FileDependency(relation, target, {"cell": index}) for relation, target in parsed)
    return ExtractionResult(
        "builtin.structured.notebook",
        "2",
        metadata={
            "cell_count": len(cells),
            "code_cell_count": code_count,
            "markdown_cell_count": markdown_count,
            "kernel_language": kernel,
            "nbformat": value.get("nbformat") if isinstance(value, dict) else None,
        },
        roles=("notebook", "analysis", "source_code"),
        dependencies=tuple({(item.relation, item.target_path): item for item in dependencies}.values()),
        external_dependencies=tuple(dict.fromkeys(external)),
        resources=tuple(resources),
        warnings=("Notebook 超过 1000 个单元格，仅索引前 1000 个",)
        if len(cells) > 1000
        else (),
    )
