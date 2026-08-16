from __future__ import annotations

from .api import ExtractionContext, ExtractionResult
from .scientific_columnar import _arrow_extract, _parquet_extract
from .scientific_common import SCIENTIFIC_EXTENSIONS
from .scientific_numpy import _npy_extract, _npz_extract
from .scientific_optional import _fits_extract, _hdf5_extract, _mat_extract, _netcdf_extract
from .scientific_sqlite import _sqlite_extract


def _roles_for_scientific_suffix(suffix: str) -> tuple[str, ...]:
    if suffix in {".sqlite", ".sqlite3", ".db"}:
        return ("database", "dataset", "structured_data")
    if suffix in {".parquet", ".feather", ".arrow"}:
        return ("dataset", "columnar_data", "scientific_data")
    if suffix in {".npy", ".npz", ".mat"}:
        return ("dataset", "scientific_data", "array_data")
    return ("dataset", "scientific_data")


class ScientificDataExtractor:
    extractor_id = "builtin.science"
    extractor_version = "2"
    priority = 85

    def matches(self, context: ExtractionContext) -> int:
        return 85 if context.suffix in SCIENTIFIC_EXTENSIONS else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
        if context.size > max(1, context.max_binary_file_bytes):
            return ExtractionResult(
                "builtin.science.budget",
                self.extractor_version,
                metadata={
                    "format": context.suffix.lstrip("."),
                    "size": context.size,
                    "skipped_due_to_size": True,
                    "max_binary_file_bytes": context.max_binary_file_bytes,
                },
                roles=_roles_for_scientific_suffix(context.suffix),
                warnings=(
                    f"科学数据文件为 {context.size} 字节，超过二进制解析预算 "
                    f"{context.max_binary_file_bytes} 字节，已跳过内部解析",
                ),
            )
        if context.suffix == ".npy":
            return _npy_extract(context)
        if context.suffix == ".npz":
            return _npz_extract(context)
        if context.suffix in {".sqlite", ".sqlite3", ".db"}:
            return _sqlite_extract(context)
        if context.suffix == ".parquet":
            return _parquet_extract(context)
        if context.suffix in {".feather", ".arrow"}:
            return _arrow_extract(context)
        if context.suffix in {".h5", ".hdf5", ".he5"}:
            return _hdf5_extract(context)
        if context.suffix in {".nc", ".nc4", ".cdf"}:
            return _netcdf_extract(context)
        if context.suffix == ".mat":
            return _mat_extract(context)
        if context.suffix in {".fits", ".fit", ".fts"}:
            return _fits_extract(context)
        return ExtractionResult(self.extractor_id, self.extractor_version)
