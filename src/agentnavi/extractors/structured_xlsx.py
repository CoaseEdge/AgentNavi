from __future__ import annotations

import posixpath
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .api import ExtractedResource, ExtractionContext, ExtractionResult, ResourceRelation

WORKBOOK_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORKSHEET_RE = re.compile(r"xl/worksheets/sheet\d+\.xml$")
FORMULA_SHEET_RE = re.compile(
    r"(?:'((?:[^']|'')+)'|([A-Za-z_][\w .-]*))!\$?[A-Z]{1,3}\$?\d+"
)


@dataclass
class _ZipReadBudget:
    archive: zipfile.ZipFile
    max_total_bytes: int
    max_member_bytes: int = 8 * 1024 * 1024
    used_bytes: int = 0

    def read(self, name: str) -> bytes:
        info = self.archive.getinfo(name)
        if info.file_size > self.max_member_bytes:
            raise ValueError(f"{name} 解压后超过 {self.max_member_bytes} 字节")
        if info.compress_size and info.file_size / max(info.compress_size, 1) > 200:
            raise ValueError(f"{name} 压缩率异常")
        if self.used_bytes + info.file_size > self.max_total_bytes:
            raise ValueError(f"XLSX 解压读取超过 {self.max_total_bytes} 字节总预算")
        value = self.archive.read(name)
        self.used_bytes += len(value)
        return value


def _normalized_relationship_target(target: str) -> str:
    target = target.replace("\\", "/")
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("xl", target))
    return normalized.lstrip("/")


def _xlsx_extract(context: ExtractionContext) -> ExtractionResult:
    resources: list[ExtractedResource] = []
    relations: list[ResourceRelation] = []
    metadata: dict[str, object] = {}
    warnings: list[str] = []
    if context.size > max(1, context.max_binary_file_bytes):
        return ExtractionResult(
            "builtin.structured.xlsx",
            "2",
            metadata={
                "format": "xlsx",
                "size": context.size,
                "skipped_due_to_size": True,
                "max_binary_file_bytes": context.max_binary_file_bytes,
            },
            roles=("dataset", "spreadsheet", "structured_data"),
            warnings=(
                f"XLSX 文件为 {context.size} 字节，超过二进制解析预算 "
                f"{context.max_binary_file_bytes} 字节，已跳过内部解析",
            ),
        )

    try:
        with zipfile.ZipFile(context.absolute_path) as archive:
            infos = archive.infolist()
            if len(infos) > max(1, context.max_archive_entries):
                raise ValueError(
                    f"XLSX 包含 {len(infos)} 个 ZIP 项目，超过 "
                    f"{context.max_archive_entries} 项预算"
                )
            budget = _ZipReadBudget(
                archive,
                max_total_bytes=max(1, context.max_archive_uncompressed_bytes),
            )
            workbook = ET.fromstring(budget.read("xl/workbook.xml"))
            sheets: list[tuple[str, str]] = []
            namespace = {"m": WORKBOOK_NS}
            for sheet in workbook.findall("m:sheets/m:sheet", namespace):
                name = sheet.attrib.get("name", "")
                relation_id = sheet.attrib.get(f"{{{OFFICE_REL_NS}}}id", "")
                sheets.append((name, relation_id))
            metadata["sheets"] = [name for name, _ in sheets]
            metadata["sheet_count"] = len(sheets)
            if len(sheets) > 500:
                warnings.append("XLSX 超过 500 个工作表，仅索引前 500 个")
            indexed_sheets = sheets[:500]
            for index, (name, relation_id) in enumerate(indexed_sheets):
                resources.append(
                    ExtractedResource(
                        "worksheet",
                        f"sheet:{index}:{name}",
                        name or f"Sheet {index + 1}",
                        {"index": index, "relationship_id": relation_id},
                    )
                )

            relationship_targets: dict[str, str] = {}
            try:
                relationships = ET.fromstring(budget.read("xl/_rels/workbook.xml.rels"))
                for relation in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
                    relation_id = relation.attrib.get("Id", "")
                    target = relation.attrib.get("Target", "")
                    if relation_id and target:
                        relationship_targets[relation_id] = _normalized_relationship_target(target)
            except KeyError:
                warnings.append("XLSX 缺少 workbook relationships，使用顺序映射降级")

            source_key_by_path: dict[str, str] = {}
            if relationship_targets:
                for resource, (_, relation_id) in zip(resources, indexed_sheets):
                    target = relationship_targets.get(relation_id)
                    if target:
                        source_key_by_path[target] = resource.key
            else:
                worksheet_paths = sorted(
                    name for name in archive.namelist() if WORKSHEET_RE.match(name)
                )
                for resource, worksheet_path in zip(resources, worksheet_paths):
                    source_key_by_path[worksheet_path] = resource.key

            formula_refs: Counter[tuple[str, str]] = Counter()
            for worksheet_path, source_key in source_key_by_path.items():
                try:
                    root = ET.fromstring(budget.read(worksheet_path))
                except (KeyError, ET.ParseError, ValueError) as exc:
                    warnings.append(f"{worksheet_path} 解析失败：{exc}")
                    continue
                for formula in root.iter(f"{{{WORKBOOK_NS}}}f"):
                    if not formula.text:
                        continue
                    for referenced in FORMULA_SHEET_RE.findall(formula.text):
                        sheet_name = (referenced[0] or referenced[1]).replace("''", "'")
                        formula_refs[(source_key, sheet_name)] += 1

            sheet_key_by_name = {resource.label: resource.key for resource in resources}
            for (source_key, target_name), count in formula_refs.items():
                target_key = sheet_key_by_name.get(target_name)
                if target_key and source_key != target_key:
                    relations.append(
                        ResourceRelation(
                            "formula_depends_on",
                            target_key,
                            source_key=source_key,
                            data={"count": count},
                        )
                    )

            external_links = [
                name
                for name in archive.namelist()
                if name.startswith("xl/externalLinks/") and name.endswith(".xml")
            ]
            metadata["external_link_parts"] = len(external_links)
            metadata["archive_entries"] = len(infos)
            metadata["decompressed_bytes_read"] = budget.used_bytes
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
        warnings.append(f"XLSX 解析失败：{exc}")
    return ExtractionResult(
        "builtin.structured.xlsx",
        "2",
        metadata=metadata,
        roles=("dataset", "spreadsheet", "structured_data"),
        resources=tuple(resources),
        resource_relations=tuple(relations),
        warnings=tuple(dict.fromkeys(warnings)),
    )
