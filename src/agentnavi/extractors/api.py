from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Hashable, Protocol, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """A read-only view of one project file presented to extractors.

    Extractors return data only. They never receive a database connection and must
    not mutate the indexed project. Resource budgets are part of the context so
    built-in and third-party extractors enforce the same deterministic limits.
    """

    project_id: str
    project_root: Path
    relative_path: str
    absolute_path: Path
    all_paths: frozenset[str]
    language: str
    size: int
    digest: str
    is_text: bool
    text: str | None
    max_file_bytes: int
    max_binary_file_bytes: int = 256 * 1024 * 1024
    max_archive_entries: int = 10_000
    max_archive_uncompressed_bytes: int = 64 * 1024 * 1024
    max_line_chars: int = 1024 * 1024
    max_stream_chars: int = 64 * 1024 * 1024

    @property
    def suffix(self) -> str:
        return self.absolute_path.suffix.lower()

    @property
    def name(self) -> str:
        return self.absolute_path.name


@dataclass(frozen=True, slots=True)
class ExtractedResource:
    """A navigable sub-resource contained in one file.

    ``key`` is stable within the parent file. The scanner prefixes it with the
    project-relative file path before creating an L1 node.
    """

    kind: str
    key: str
    label: str
    data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceRelation:
    """An L1 relation involving resources within the current file.

    ``source_key=None`` means the parent file. ``target_key`` refers to a
    resource returned by the same extraction result.
    """

    relation: str
    target_key: str
    source_key: str | None = None
    data: dict[str, object] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class FileDependency:
    relation: str
    target_path: str
    data: dict[str, object] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    extractor_id: str
    extractor_version: str
    metadata: dict[str, object] = field(default_factory=dict)
    roles: tuple[str, ...] = ()
    dependencies: tuple[FileDependency, ...] = ()
    external_dependencies: tuple[str, ...] = ()
    resources: tuple[ExtractedResource, ...] = ()
    resource_relations: tuple[ResourceRelation, ...] = ()
    warnings: tuple[str, ...] = ()


class Extractor(Protocol):
    extractor_id: str
    extractor_version: str
    priority: int

    def matches(self, context: ExtractionContext) -> int:
        """Return a score from 0 to 100. Zero means unsupported."""

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """Extract deterministic metadata and relationships without side effects."""


def merge_results(results: list[ExtractionResult]) -> ExtractionResult:
    """Merge compatible extractor output deterministically.

    More specific extractors should use higher priority and are passed first by
    the registry. First metadata writer wins for scalar keys; list-like data is
    expected to use distinct keys or be merged by the extractor itself.
    """

    if not results:
        return ExtractionResult("generic", "1")

    metadata: dict[str, object] = {}
    roles: list[str] = []
    dependencies: list[FileDependency] = []
    external: list[str] = []
    resources: list[ExtractedResource] = []
    resource_relations: list[ResourceRelation] = []
    warnings: list[str] = []
    extractor_ids: list[str] = []

    for result in results:
        extractor_ids.append(f"{result.extractor_id}@{result.extractor_version}")
        for key, value in result.metadata.items():
            metadata.setdefault(key, value)
        roles.extend(result.roles)
        dependencies.extend(result.dependencies)
        external.extend(result.external_dependencies)
        resources.extend(result.resources)
        resource_relations.extend(result.resource_relations)
        warnings.extend(result.warnings)

    def unique(items: list[T], key: Callable[[T], Hashable]) -> tuple[T, ...]:
        seen: set[object] = set()
        output: list[T] = []
        for item in items:
            marker = key(item)
            if marker in seen:
                continue
            seen.add(marker)
            output.append(item)
        return tuple(output)

    metadata["extractors"] = extractor_ids
    return ExtractionResult(
        extractor_id="+".join(item.split("@", 1)[0] for item in extractor_ids),
        extractor_version="+".join(item.split("@", 1)[1] for item in extractor_ids),
        metadata=metadata,
        roles=tuple(dict.fromkeys(roles)),
        dependencies=unique(
            dependencies,
            lambda item: (item.relation, item.target_path),
        ),
        external_dependencies=tuple(dict.fromkeys(external)),
        resources=unique(resources, lambda item: (item.kind, item.key)),
        resource_relations=unique(
            resource_relations,
            lambda item: (item.source_key, item.relation, item.target_key),
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )
