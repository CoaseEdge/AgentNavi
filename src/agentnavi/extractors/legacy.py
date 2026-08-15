from __future__ import annotations

from .api import ExtractionContext, ExtractionResult, FileDependency
from ..scan_support import (
    _text_metadata,
    parse_javascript_dependencies,
    parse_markdown_dependencies,
    parse_python_dependencies,
)


class PythonExtractor:
    extractor_id = "builtin.python"
    extractor_version = "1"
    priority = 100

    def matches(self, context: ExtractionContext) -> int:
        return 100 if context.suffix == ".py" and context.text is not None else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        assert context.text is not None
        dependencies, external = parse_python_dependencies(
            context.relative_path,
            context.text,
            set(context.all_paths),
        )
        return ExtractionResult(
            self.extractor_id,
            self.extractor_version,
            metadata=_text_metadata(context.relative_path, context.text),
            roles=("source_code",),
            dependencies=tuple(FileDependency(relation, target) for relation, target in dependencies),
            external_dependencies=tuple(external),
        )


class JavaScriptExtractor:
    extractor_id = "builtin.javascript"
    extractor_version = "1"
    priority = 100
    extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue"}

    def matches(self, context: ExtractionContext) -> int:
        return 100 if context.suffix in self.extensions and context.text is not None else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        assert context.text is not None
        dependencies, external = parse_javascript_dependencies(
            context.relative_path,
            context.text,
            set(context.all_paths),
        )
        return ExtractionResult(
            self.extractor_id,
            self.extractor_version,
            metadata=_text_metadata(context.relative_path, context.text),
            roles=("source_code",),
            dependencies=tuple(FileDependency(relation, target) for relation, target in dependencies),
            external_dependencies=tuple(external),
        )


class MarkdownExtractor:
    extractor_id = "builtin.markdown"
    extractor_version = "1"
    priority = 100
    extensions = {".md", ".mdx", ".rst"}

    def matches(self, context: ExtractionContext) -> int:
        return 100 if context.suffix in self.extensions and context.text is not None else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        assert context.text is not None
        dependencies = parse_markdown_dependencies(
            context.relative_path,
            context.text,
            set(context.all_paths),
        )
        return ExtractionResult(
            self.extractor_id,
            self.extractor_version,
            metadata=_text_metadata(context.relative_path, context.text),
            roles=("document",),
            dependencies=tuple(FileDependency(relation, target) for relation, target in dependencies),
        )
