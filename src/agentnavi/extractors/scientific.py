from __future__ import annotations

from .api import ExtractionContext, ExtractionResult
from .scientific_columnar import _arrow_extract, _parquet_extract
from .scientific_common import SCIENTIFIC_EXTENSIONS
from .scientific_numpy import _npy_extract, _npz_extract
from .scientific_optional import _fits_extract, _hdf5_extract, _mat_extract, _netcdf_extract
from .scientific_sqlite import _sqlite_extract

class ScientificDataExtractor:
    extractor_id = "builtin.science"
    extractor_version = "1"
    priority = 85

    def matches(self, context: ExtractionContext) -> int:
        return 85 if context.suffix in SCIENTIFIC_EXTENSIONS else 0

    def extract(self, context: ExtractionContext) -> ExtractionResult:
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
