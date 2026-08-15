from __future__ import annotations

import json
from collections import Counter

from .api import ExtractedResource, ExtractionContext, ExtractionResult
from .structured_common import _path_dependencies_from_value, _roles_for_structured

def _json_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    try:
        value = json.loads(context.text)
    except json.JSONDecodeError as exc:
        return ExtractionResult(
            "builtin.structured.json",
            "1",
            roles=_roles_for_structured(context),
            warnings=(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）",),
        )

    metadata: dict[str, object] = {"json_root_type": type(value).__name__}
    resources: list[ExtractedResource] = []
    if isinstance(value, dict):
        keys = [str(key) for key in value.keys()][:200]
        metadata["top_level_keys"] = keys
        if context.name.lower() in {"package.json", "manifest.json"}:
            if value.get("name"):
                metadata["title"] = str(value["name"])[:160]
            if value.get("description"):
                metadata["description"] = str(value["description"])[:500]
        for key in keys[:80]:
            resources.append(
                ExtractedResource(
                    "json_key",
                    f"key:{key}",
                    key,
                    {"value_type": type(value.get(key)).__name__},
                )
            )
        if value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list):
            geometries = Counter(
                str((feature.get("geometry") or {}).get("type") or "unknown")
                for feature in value["features"]
                if isinstance(feature, dict)
            )
            metadata.update(
                {
                    "geojson": True,
                    "feature_count": len(value["features"]),
                    "geometry_types": dict(geometries),
                }
            )
    elif isinstance(value, list):
        metadata["item_count"] = len(value)
        sample_types = Counter(type(item).__name__ for item in value[:1000])
        metadata["item_types"] = dict(sample_types)

    dependencies = _path_dependencies_from_value(value, context)
    roles = list(_roles_for_structured(context))
    if metadata.get("geojson"):
        roles.extend(("dataset", "geospatial_data"))
    return ExtractionResult(
        "builtin.structured.json",
        "1",
        metadata=metadata,
        roles=tuple(dict.fromkeys(roles)),
        dependencies=tuple(dependencies),
        resources=tuple(resources),
    )


def _json_lines_extract(context: ExtractionContext) -> ExtractionResult:
    assert context.text is not None
    count = 0
    invalid = 0
    keys: Counter[str] = Counter()
    value_types: Counter[str] = Counter()
    dependencies: list[FileDependency] = []
    for line_no, line in enumerate(context.text.splitlines(), 1):
        if not line.strip():
            continue
        count += 1
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        value_types[type(value).__name__] += 1
        if isinstance(value, dict):
            keys.update(map(str, value.keys()))
        if line_no <= 5000:
            dependencies.extend(_path_dependencies_from_value(value, context, limit=20))
    return ExtractionResult(
        "builtin.structured.json-lines",
        "1",
        metadata={
            "record_count": count,
            "invalid_records": invalid,
            "common_keys": [key for key, _ in keys.most_common(100)],
            "record_types": dict(value_types),
        },
        roles=("dataset", "structured_data"),
        dependencies=tuple({(item.relation, item.target_path): item for item in dependencies}.values()),
        warnings=((f"发现 {invalid} 行无效 JSON" if invalid else ""),) if invalid else (),
    )


