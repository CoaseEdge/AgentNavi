from __future__ import annotations

from .api import ExtractedResource, ExtractionContext, ExtractionResult
from .scientific_common import _module_available


def _parquet_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("pyarrow"):
        return ExtractionResult(
            "optional.science.parquet",
            "2",
            metadata={"format": context.suffix.lstrip("."), "optional_dependency": "pyarrow"},
            roles=("dataset", "columnar_data"),
            warnings=("安装 pyarrow 后可提取列、Schema、行组和统计信息",),
        )
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        parquet = pq.ParquetFile(context.absolute_path)
        schema = parquet.schema_arrow
        resources = tuple(
            ExtractedResource(
                "column",
                f"column:{index}:{field.name}",
                field.name,
                {"index": index, "type": str(field.type), "nullable": field.nullable},
            )
            for index, field in enumerate(schema)
        )
        metadata = {
            "column_count": len(schema),
            "columns": [field.name for field in schema],
            "row_group_count": parquet.metadata.num_row_groups if parquet.metadata else None,
            "row_count": parquet.metadata.num_rows if parquet.metadata else None,
            "created_by": parquet.metadata.created_by if parquet.metadata else None,
        }
        return ExtractionResult(
            "optional.science.parquet",
            "2",
            metadata=metadata,
            roles=("dataset", "columnar_data", "scientific_data"),
            resources=resources,
        )
    except Exception as exc:  # optional library errors vary by version
        return ExtractionResult(
            "optional.science.parquet",
            "2",
            roles=("dataset", "columnar_data"),
            warnings=(f"Parquet 解析失败：{exc}",),
        )


def _arrow_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("pyarrow"):
        return ExtractionResult(
            "optional.science.arrow",
            "2",
            metadata={"format": context.suffix.lstrip("."), "optional_dependency": "pyarrow"},
            roles=("dataset", "columnar_data"),
            warnings=("安装 pyarrow 后可提取 Arrow/Feather Schema",),
        )
    try:
        import pyarrow as pa  # type: ignore[import-not-found]
        import pyarrow.ipc as ipc  # type: ignore[import-not-found]

        with pa.memory_map(str(context.absolute_path), "r") as source:
            try:
                reader = ipc.open_file(source)
                schema = reader.schema
                batch_count: int | None = reader.num_record_batches
                container = "file"
            except (pa.ArrowInvalid, OSError):
                source.seek(0)
                reader = ipc.open_stream(source)
                schema = reader.schema
                batch_count = None
                container = "stream"
        resources = tuple(
            ExtractedResource(
                "column",
                f"column:{index}:{field.name}",
                field.name,
                {"index": index, "type": str(field.type), "nullable": field.nullable},
            )
            for index, field in enumerate(schema)
        )
        return ExtractionResult(
            "optional.science.arrow",
            "2",
            metadata={
                "column_count": len(schema),
                "columns": schema.names,
                "record_batch_count": batch_count,
                "ipc_container": container,
            },
            roles=("dataset", "columnar_data", "scientific_data"),
            resources=resources,
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.arrow",
            "2",
            roles=("dataset", "columnar_data"),
            warnings=(f"Arrow/Feather 解析失败：{exc}",),
        )
