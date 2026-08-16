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
            "2",
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
        "2",
        metadata=metadata,
        roles=("dataset", "scientific_data", "array_data"),
        resources=(resource,),
    )


def _npz_extract(context: ExtractionContext) -> ExtractionResult:
    resources: list[ExtractedResource] = []
    warnings: list[str] = []
    arrays: list[dict[str, object]] = []
    header_bytes = 0
    archive_entries = 0
    try:
        with zipfile.ZipFile(context.absolute_path) as archive:
            infos = archive.infolist()
            archive_entries = len(infos)
            if archive_entries > max(1, context.max_archive_entries):
                raise ValueError(
                    f"NPZ 包含 {archive_entries} 个 ZIP 项目，超过 "
                    f"{context.max_archive_entries} 项预算"
                )
            names = [info.filename for info in infos if info.filename.lower().endswith(".npy")]
            for name in names[:1000]:
                try:
                    with archive.open(name) as handle:
                        metadata = _npy_header(handle)  # type: ignore[arg-type]
                except (KeyError, OSError, ValueError, SyntaxError, UnicodeError) as exc:
                    warnings.append(f"{name}: {exc}")
                    continue
                next_header_bytes = header_bytes + int(metadata.get("header_bytes", 0))
                if next_header_bytes > max(1, context.max_archive_uncompressed_bytes):
                    warnings.append(
                        f"NPZ header 读取超过 {context.max_archive_uncompressed_bytes} 字节总预算，已提前停止"
                    )
                    break
                header_bytes = next_header_bytes
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
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        warnings.append(f"NPZ 解析失败：{exc}")
    return ExtractionResult(
        "builtin.science.npz",
        "2",
        metadata={
            "array_count": len(arrays),
            "arrays": arrays[:200],
            "archive_entries": archive_entries,
            "header_bytes_read": header_bytes,
        },
        roles=("dataset", "scientific_data", "array_archive"),
        resources=tuple(resources),
        warnings=tuple(dict.fromkeys(warnings[:100])),
    )
