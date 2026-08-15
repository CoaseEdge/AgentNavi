from __future__ import annotations

from .api import ExtractionContext, ExtractionResult
from .structured_common import STRUCTURED_EXTENSIONS
from .structured_config import _ini_extract, _toml_extract, _xml_extract, _yaml_extract
from .structured_json import _json_extract, _json_lines_extract
from .structured_tabular import _csv_extract, _notebook_extract, _sql_extract
from .structured_xlsx import _xlsx_extract

class StructuredTextExtractor:
    extractor_id = "builtin.structured"
    extractor_version = "1"
    priority = 80

    def matches(self, context: ExtractionContext) -> int:
        if context.suffix not in STRUCTURED_EXTENSIONS:
            return 0
        if context.suffix == ".xlsx":
            return 80
        return 80 if context.text is not None else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        if context.suffix == ".json":
            return _json_extract(context)
        if context.suffix in {".jsonl", ".ndjson"}:
            return _json_lines_extract(context)
        if context.suffix in {".yaml", ".yml"}:
            return _yaml_extract(context)
        if context.suffix == ".toml":
            return _toml_extract(context)
        if context.suffix in {".ini", ".cfg", ".conf"}:
            return _ini_extract(context)
        if context.suffix == ".xml":
            return _xml_extract(context)
        if context.suffix in {".csv", ".tsv"}:
            return _csv_extract(context)
        if context.suffix == ".sql":
            return _sql_extract(context)
        if context.suffix == ".ipynb":
            return _notebook_extract(context)
        if context.suffix == ".xlsx":
            return _xlsx_extract(context)
        return ExtractionResult(self.extractor_id, self.extractor_version)
