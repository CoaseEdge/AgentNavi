from __future__ import annotations

import ast
import importlib.util
import io
import struct

SCIENTIFIC_EXTENSIONS = {
    ".npy",
    ".npz",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".parquet",
    ".feather",
    ".arrow",
    ".h5",
    ".hdf5",
    ".he5",
    ".nc",
    ".nc4",
    ".cdf",
    ".mat",
    ".fits",
    ".fit",
    ".fts",
}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _npy_header(handle: io.BufferedIOBase) -> dict[str, object]:
    magic = handle.read(6)
    if magic != b"\x93NUMPY":
        raise ValueError("不是 NPY 文件")
    version = handle.read(2)
    if len(version) != 2:
        raise ValueError("NPY 版本头不完整")
    major, minor = version
    if major == 1:
        raw_length = handle.read(2)
        if len(raw_length) != 2:
            raise ValueError("NPY header 长度不完整")
        header_length = struct.unpack("<H", raw_length)[0]
    elif major in {2, 3}:
        raw_length = handle.read(4)
        if len(raw_length) != 4:
            raise ValueError("NPY header 长度不完整")
        header_length = struct.unpack("<I", raw_length)[0]
    else:
        raise ValueError(f"不支持的 NPY 版本：{major}.{minor}")
    if header_length > 1_048_576:
        raise ValueError("NPY header 超过 1 MiB 安全上限")
    header = handle.read(header_length)
    encoding = "latin1" if major < 3 else "utf-8"
    parsed = ast.literal_eval(header.decode(encoding).strip())
    if not isinstance(parsed, dict):
        raise ValueError("NPY header 不是字典")
    shape = parsed.get("shape", ())
    return {
        "format_version": f"{major}.{minor}",
        "dtype": str(parsed.get("descr", "unknown")),
        "fortran_order": bool(parsed.get("fortran_order", False)),
        "shape": list(shape) if isinstance(shape, tuple) else shape,
        "dimensions": len(shape) if isinstance(shape, tuple) else None,
    }

