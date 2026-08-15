from __future__ import annotations

import hashlib
import importlib.metadata
from dataclasses import dataclass
from typing import Iterable

from .api import ExtractionContext, ExtractionResult, Extractor, merge_results
from .code import MultiLanguageCodeExtractor
from .legacy import JavaScriptExtractor, MarkdownExtractor, PythonExtractor
from .scientific import ScientificDataExtractor
from .structured import StructuredTextExtractor


ENTRY_POINT_GROUP = "agentnavi.extractors"


@dataclass(frozen=True, slots=True)
class ExtractorDescriptor:
    extractor_id: str
    version: str
    priority: int
    source: str
    load_error: str | None = None


class ExtractionRegistry:
    def __init__(self, extractors: Iterable[Extractor], *, load_errors: Iterable[str] = ()) -> None:
        deduplicated: dict[str, Extractor] = {}
        for extractor in extractors:
            existing = deduplicated.get(extractor.extractor_id)
            if existing is None or extractor.priority > existing.priority:
                deduplicated[extractor.extractor_id] = extractor
        self._extractors = tuple(
            sorted(
                deduplicated.values(),
                key=lambda item: (-item.priority, item.extractor_id),
            )
        )
        self.load_errors = tuple(load_errors)

    @property
    def signature(self) -> str:
        payload = "\n".join(
            f"{item.extractor_id}:{item.extractor_version}:{item.priority}"
            for item in self._extractors
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def descriptors(self) -> list[ExtractorDescriptor]:
        return [
            ExtractorDescriptor(
                extractor_id=item.extractor_id,
                version=item.extractor_version,
                priority=item.priority,
                source=item.__class__.__module__,
            )
            for item in self._extractors
        ]

    def matching(self, context: ExtractionContext) -> list[tuple[int, Extractor]]:
        matches: list[tuple[int, Extractor]] = []
        for extractor in self._extractors:
            try:
                score = max(0, min(int(extractor.matches(context)), 100))
            except Exception:
                continue
            if score:
                matches.append((score, extractor))
        return sorted(matches, key=lambda pair: (-pair[0], -pair[1].priority, pair[1].extractor_id))

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        results: list[ExtractionResult] = []
        for _, extractor in self.matching(context):
            try:
                result = extractor.extract(context)
            except Exception as exc:  # Extractors are fail-open by design.
                result = ExtractionResult(
                    extractor.extractor_id,
                    extractor.extractor_version,
                    warnings=(f"提取器 {extractor.extractor_id} 失败：{type(exc).__name__}: {exc}",),
                )
            results.append(result)
        merged = merge_results(results)
        if self.load_errors:
            merged = ExtractionResult(
                merged.extractor_id,
                merged.extractor_version,
                metadata=merged.metadata,
                roles=merged.roles,
                dependencies=merged.dependencies,
                external_dependencies=merged.external_dependencies,
                resources=merged.resources,
                resource_relations=merged.resource_relations,
                warnings=tuple(dict.fromkeys((*merged.warnings, *self.load_errors))),
            )
        return merged


def _builtins() -> list[Extractor]:
    return [
        PythonExtractor(),
        JavaScriptExtractor(),
        MarkdownExtractor(),
        MultiLanguageCodeExtractor(),
        StructuredTextExtractor(),
        ScientificDataExtractor(),
    ]


def _coerce_plugin(value: object) -> list[Extractor]:
    if isinstance(value, type):
        value = value()
    if callable(value) and not hasattr(value, "extractor_id"):
        value = value()
    if isinstance(value, (list, tuple, set)):
        return [item for item in value if hasattr(item, "extractor_id")]
    return [value] if hasattr(value, "extractor_id") else []


def load_registry(*, include_plugins: bool = True) -> ExtractionRegistry:
    extractors: list[Extractor] = _builtins()
    errors: list[str] = []
    if include_plugins:
        try:
            entry_points = importlib.metadata.entry_points()
            selected = entry_points.select(group=ENTRY_POINT_GROUP)
        except Exception as exc:
            selected = ()
            errors.append(f"无法发现外部提取器：{exc}")
        for entry_point in selected:
            try:
                extractors.extend(_coerce_plugin(entry_point.load()))
            except Exception as exc:
                errors.append(f"外部提取器 {entry_point.name} 加载失败：{exc}")
    return ExtractionRegistry(extractors, load_errors=errors)
