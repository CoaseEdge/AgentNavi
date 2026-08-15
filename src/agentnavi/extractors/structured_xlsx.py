from __future__ import annotations

import re
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET

from .api import ExtractedResource, ExtractionContext, ExtractionResult, ResourceRelation

def _safe_zip_read(archive: zipfile.ZipFile, name: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError(f"{name} 解压后超过 {max_bytes} 字节")
    if info.compress_size and info.file_size / max(info.compress_size, 1) > 200:
        raise ValueError(f"{name} 压缩率异常")
    return archive.read(name)


def _xlsx_extract(context: ExtractionContext) -> ExtractionResult:
    resources: list[ExtractedResource] = []
    relations: list[ResourceRelation] = []
    metadata: dict[str, object] = {}
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(context.absolute_path) as archive:
            workbook = ET.fromstring(_safe_zip_read(archive, "xl/workbook.xml"))
            namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            rel_namespace = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
            sheets = []
            for sheet in workbook.findall("m:sheets/m:sheet", namespace):
                name = sheet.attrib.get("name", "")
                relation_id = sheet.attrib.get(f"{{{rel_namespace['r']}}}id", "")
                sheets.append((name, relation_id))
            metadata["sheets"] = [name for name, _ in sheets]
            metadata["sheet_count"] = len(sheets)
            for index, (name, relation_id) in enumerate(sheets[:500]):
                resources.append(
                    ExtractedResource(
                        "worksheet",
                        f"sheet:{index}:{name}",
                        name or f"Sheet {index + 1}",
                        {"index": index, "relationship_id": relation_id},
                    )
                )

            formula_refs: Counter[tuple[str, str]] = Counter()
            for worksheet_name in sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name))[:500]:
                try:
                    root = ET.fromstring(_safe_zip_read(archive, worksheet_name))
                except (KeyError, ET.ParseError):
                    continue
                for formula in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f"):
                    if not formula.text:
                        continue
                    for referenced in re.findall(r"(?:'([^']+)'|([A-Za-z_][\w .-]*))!\$?[A-Z]{1,3}\$?\d+", formula.text):
                        sheet_name = referenced[0] or referenced[1]
                        formula_refs[(worksheet_name, sheet_name)] += 1
            sheet_key_by_name = {resource.label: resource.key for resource in resources}
            for (_, target_name), count in formula_refs.items():
                target_key = sheet_key_by_name.get(target_name)
                if target_key:
                    relations.append(ResourceRelation("formula_depends_on", target_key, data={"count": count}))

            external_links = [name for name in archive.namelist() if name.startswith("xl/externalLinks/") and name.endswith(".xml")]
            metadata["external_link_parts"] = len(external_links)
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        warnings.append(f"XLSX 解析失败：{exc}")
    return ExtractionResult(
        "builtin.structured.xlsx",
        "1",
        metadata=metadata,
        roles=("dataset", "spreadsheet", "structured_data"),
        resources=tuple(resources),
        resource_relations=tuple(relations),
        warnings=tuple(warnings),
    )

