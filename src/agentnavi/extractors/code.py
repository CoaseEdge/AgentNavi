from __future__ import annotations

from .api import ExtractionContext, ExtractionResult
from .code_common import SUPPORTED_EXTENSIONS
from .code_go_rust import _go_extract, _rust_extract
from .code_jvm_native import _c_family_extract, _csharp_extract, _java_kotlin_extract
from .code_scripting import _lua_extract, _php_extract, _ruby_extract, _shell_extract, _swift_extract

class MultiLanguageCodeExtractor:
    extractor_id = "builtin.code.multilanguage"
    extractor_version = "1"
    priority = 90

    def matches(self, context: ExtractionContext) -> int:
        return 90 if context.suffix in SUPPORTED_EXTENSIONS and context.text is not None else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        if context.suffix == ".go":
            return _go_extract(context)
        if context.suffix == ".rs":
            return _rust_extract(context)
        if context.suffix in {".java", ".kt", ".kts"}:
            return _java_kotlin_extract(context)
        if context.suffix in {".c", ".cc", ".cpp", ".h", ".hpp"}:
            return _c_family_extract(context)
        if context.suffix == ".cs":
            return _csharp_extract(context)
        if context.suffix == ".rb":
            return _ruby_extract(context)
        if context.suffix == ".php":
            return _php_extract(context)
        if context.suffix == ".swift":
            return _swift_extract(context)
        if context.suffix in {".sh", ".bash", ".zsh"}:
            return _shell_extract(context)
        if context.suffix == ".lua":
            return _lua_extract(context)
        return ExtractionResult(self.extractor_id, self.extractor_version)
