from __future__ import annotations

import configparser
import re
import tomllib
from collections import Counter
from xml.etree import ElementTree as ET

from .api import ExtractedResource, ExtractionContext, ExtractionResult, FileDependency
from .structured_common import PATH_KEY_RE, PATH_VALUE_RE, _path_dependencies_from_value, _resolve_reference, _roles_for_structured

def _toml_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    try:
        value = tomllib.loads(context.text)
    except tomllib.TOMLDecodeError as exc:
        return ExtractionResult(
            "builtin.structured.toml",
            "1",
            roles=_roles_for_structured(context),
            warnings=(f"TOML 解析失败：{exc}",),
        )
    sections = [str(key) for key in value.keys()]
    return ExtractionResult(
        "builtin.structured.toml",
        "1",
        metadata={"sections": sections[:200]},
        roles=_roles_for_structured(context),
        dependencies=tuple(_path_dependencies_from_value(value, context)),
    )


def _ini_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(context.text)
    except configparser.Error as exc:
        return ExtractionResult(
            "builtin.structured.ini",
            "1",
            roles=("configuration", "structured_data"),
            warnings=(f"INI 解析失败：{exc}",),
        )
    sections = parser.sections()
    mapping = {section: dict(parser.items(section)) for section in sections}
    resources = tuple(
        ExtractedResource(
            "config_section",
            f"section:{section}",
            section,
            {"keys": list(mapping[section])[:100]},
        )
        for section in sections[:100]
    )
    return ExtractionResult(
        "builtin.structured.ini",
        "1",
        metadata={"sections": sections[:200]},
        roles=("configuration", "structured_data"),
        dependencies=tuple(_path_dependencies_from_value(mapping, context)),
        resources=resources,
    )


def _yaml_extract(context: ExtractionContext) -> ExtractionResult:
    """Extract deterministic low-cost YAML structure without requiring PyYAML.

    The parser is intentionally shallow. It does not attempt to interpret YAML
    types, aliases, tags or merge semantics; it extracts keys and explicit path
    values while preserving a clear capability boundary.
    """

    assert context.text is not None
    keys: list[str] = []
    dependencies: list[FileDependency] = []
    for line_no, raw in enumerate(context.text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = re.match(r"^(?P<indent>\s*)(?:-\s*)?(?P<key>[^:#][^:]*?):\s*(?P<value>.*?)\s*$", raw)
        if not match:
            continue
        key = match.group("key").strip().strip("'\"")
        value = match.group("value").strip().split(" #", 1)[0].strip().strip("'\"")
        if len(match.group("indent")) == 0 and key not in keys:
            keys.append(key)
        if value and (PATH_KEY_RE.search(key) or PATH_VALUE_RE.match(value) or value.startswith(("./", "../"))):
            target = _resolve_reference(context, value)
            if target:
                dependencies.append(
                    FileDependency("references", target, {"key": key, "line": line_no, "raw_reference": value[:500]})
                )
    resources = tuple(
        ExtractedResource("yaml_key", f"key:{key}", key, {}) for key in keys[:100]
    )
    return ExtractionResult(
        "builtin.structured.yaml-shallow",
        "1",
        metadata={"top_level_keys": keys[:200], "yaml_parser": "shallow"},
        roles=_roles_for_structured(context),
        dependencies=tuple({(item.relation, item.target_path): item for item in dependencies}.values()),
        resources=resources,
    )


def _xml_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    try:
        root = ET.fromstring(context.text)
    except ET.ParseError as exc:
        return ExtractionResult(
            "builtin.structured.xml",
            "1",
            roles=_roles_for_structured(context),
            warnings=(f"XML 解析失败：{exc}",),
        )
    tag_counts: Counter[str] = Counter()
    dependencies: list[FileDependency] = []
    references: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        tag_counts[tag] += 1
        for attr, value in element.attrib.items():
            attr_name = attr.rsplit("}", 1)[-1]
            if attr_name.lower() in {"href", "src", "schemalocation", "location", "file", "path"} or PATH_VALUE_RE.match(value):
                references.append(value)
                target = _resolve_reference(context, value)
                if target:
                    dependencies.append(
                        FileDependency("references", target, {"element": tag, "attribute": attr_name})
                    )
    resources = tuple(
        ExtractedResource("xml_element", f"tag:{tag}", tag, {"count": count})
        for tag, count in tag_counts.most_common(100)
    )
    return ExtractionResult(
        "builtin.structured.xml",
        "1",
        metadata={
            "root_tag": root.tag.rsplit("}", 1)[-1],
            "element_counts": dict(tag_counts.most_common(100)),
            "external_references": references[:100],
        },
        roles=_roles_for_structured(context),
        dependencies=tuple({(item.relation, item.target_path): item for item in dependencies}.values()),
        resources=resources,
    )


