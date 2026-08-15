from __future__ import annotations

import zipfile
from pathlib import Path

from .api import ExtractedResource, ExtractionContext, ExtractionResult
from .scientific_common import _npy_header

def _npy_extract(context: ExtractionContext) -> ExtractionResult:
    try:
        with context.absolute_path.open("rb") as handle:
            metadata = _npy_header(handle)
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        return ExtractionResult(
            "builtin.science.npy",
            "1",
            roles=("dataset", "scientific_data", "array_data"),
            warnings=(f"NPY 解析失败：{exc}",),
        )
    resource = ExtractedResource(
        "array",
        "array:root",
        context.name,
        {key: value for key, value in metadata.items() if key in {"dtype", "shape", "fortran_order"}},
    )
    return ExtractionResult(
        "builtin.science.npy",
        "1",
        metadata=metadata,
        roles=("dataset", "scientific_data", "array_data"),
        resources=(resource,),
    )


def _npz_extract(context: ExtractionContext) -> ExtractionResult:
    resources: list[ExtractedResource] = []
    warnings: list[str] = []
    arrays: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(context.absolute_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".npy")]
            for name in names[:1000]:
                try:
                    with archive.open(name) as handle:
                        metadata = _npy_header(handle)  # type: ignore[arg-type]
                except (KeyError, OSError, ValueError, SyntaxError, UnicodeError) as exc:
                    warnings.append(f"{name}: {exc}")
                    continue
                label = Path(name).stem
                arrays.append({"name": label, **metadata})
                resources.append(
                    ExtractedResource(
                        "array",
                        f"array:{name}",
                        label,
                        {"archive_entry": name, "dtype": metadata.get("dtype"), "shape": metadata.get("shape")},
                    )
                )
            if len(names) > 1000:
                warnings.append("NPZ 包含超过 1000 个数组，仅索引前 1000 个")
    except (OSError, zipfile.BadZipFile) as exc:
        warnings.append(f"NPZ 解析失败：{exc}")
    return ExtractionResult(
        "builtin.science.npz",
        "1",
        metadata={"array_count": len(arrays), "arrays": arrays[:200]},
        roles=("dataset", "scientific_data", "array_archive"),
        resources=tuple(resources),
        warnings=tuple(warnings[:100]),
    )

