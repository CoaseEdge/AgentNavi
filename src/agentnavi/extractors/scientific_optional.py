from __future__ import annotations

from itertools import islice

from .api import ExtractedResource, ExtractionContext, ExtractionResult
from .scientific_common import _module_available


def _hdf5_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("h5py"):
        return ExtractionResult(
            "optional.science.hdf5",
            "2",
            metadata={"format": "hdf5", "optional_dependency": "h5py"},
            roles=("dataset", "scientific_data", "hierarchical_data"),
            warnings=("安装 h5py 后可提取 group、dataset、shape 和 dtype",),
        )
    try:
        import h5py  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        truncated = False
        with h5py.File(context.absolute_path, "r") as handle:
            def visitor(name: str, item: object) -> str | None:
                nonlocal truncated
                if len(resources) >= 1000:
                    truncated = True
                    return "resource-limit"
                if isinstance(item, h5py.Dataset):
                    resources.append(
                        ExtractedResource(
                            "scientific_dataset",
                            f"dataset:{name}",
                            name or "/",
                            {"shape": list(item.shape), "dtype": str(item.dtype), "compression": item.compression},
                        )
                    )
                elif isinstance(item, h5py.Group):
                    resources.append(ExtractedResource("scientific_group", f"group:{name}", name or "/", {}))
                return None

            handle.visititems(visitor)
            attrs: dict[str, str] = {}
            for key in islice(handle.attrs.keys(), 100):
                attrs[str(key)] = str(handle.attrs[key])[:500]
        warning = ("HDF5 包含超过 1000 个对象，仅索引前 1000 个",) if truncated else ()
        return ExtractionResult(
            "optional.science.hdf5",
            "2",
            metadata={"object_count": len(resources), "root_attributes": attrs},
            roles=("dataset", "scientific_data", "hierarchical_data"),
            resources=tuple(resources),
            warnings=warning,
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.hdf5",
            "2",
            roles=("dataset", "scientific_data"),
            warnings=(f"HDF5 解析失败：{exc}",),
        )


def _netcdf_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("netCDF4"):
        return ExtractionResult(
            "optional.science.netcdf",
            "2",
            metadata={"format": "netcdf", "optional_dependency": "netCDF4"},
            roles=("dataset", "scientific_data", "multidimensional_data"),
            warnings=("安装 netCDF4 后可提取维度、变量、单位和属性",),
        )
    try:
        import netCDF4  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        warnings: list[str] = []
        with netCDF4.Dataset(context.absolute_path, "r") as dataset:
            dimension_names = iter(dataset.dimensions)
            dimensions = {
                name: len(dataset.dimensions[name])
                for name in islice(dimension_names, 1000)
            }
            try:
                next(dimension_names)
            except StopIteration:
                pass
            else:
                warnings.append("NetCDF 超过 1000 个维度，仅索引前 1000 个")

            variable_names = iter(dataset.variables)
            for name in islice(variable_names, 1000):
                variable = dataset.variables[name]
                resources.append(
                    ExtractedResource(
                        "scientific_variable",
                        f"variable:{name}",
                        name,
                        {
                            "dtype": str(variable.dtype),
                            "dimensions": list(variable.dimensions),
                            "shape": list(variable.shape),
                            "units": str(getattr(variable, "units", ""))[:200],
                        },
                    )
                )
            try:
                next(variable_names)
            except StopIteration:
                pass
            else:
                warnings.append("NetCDF 超过 1000 个变量，仅索引前 1000 个")
            attributes = {
                name: str(dataset.getncattr(name))[:500]
                for name in islice(dataset.ncattrs(), 100)
            }
        return ExtractionResult(
            "optional.science.netcdf",
            "2",
            metadata={"dimensions": dimensions, "variable_count": len(resources), "attributes": attributes},
            roles=("dataset", "scientific_data", "multidimensional_data"),
            resources=tuple(resources),
            warnings=tuple(warnings),
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.netcdf",
            "2",
            roles=("dataset", "scientific_data"),
            warnings=(f"NetCDF 解析失败：{exc}",),
        )


def _mat_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("scipy"):
        return ExtractionResult(
            "optional.science.matlab",
            "2",
            metadata={"format": "matlab", "optional_dependency": "scipy"},
            roles=("dataset", "scientific_data", "array_data"),
            warnings=("安装 scipy 后可提取 MAT 变量名、shape 和数据类型；v7.3 文件还需要 h5py",),
        )
    try:
        from scipy.io import whosmat  # type: ignore[import-not-found]

        variables = whosmat(context.absolute_path)
        resources = tuple(
            ExtractedResource(
                "scientific_variable",
                f"variable:{name}",
                name,
                {"shape": list(shape), "class": class_name},
            )
            for name, shape, class_name in variables[:1000]
        )
        return ExtractionResult(
            "optional.science.matlab",
            "2",
            metadata={"variable_count": len(variables), "variables": [name for name, _, _ in variables[:200]]},
            roles=("dataset", "scientific_data", "array_data"),
            resources=resources,
            warnings=("MAT 超过 1000 个变量，仅索引前 1000 个",) if len(variables) > 1000 else (),
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.matlab",
            "2",
            roles=("dataset", "scientific_data"),
            warnings=(f"MAT 解析失败：{exc}",),
        )


def _fits_shape_from_header(header: object) -> list[int]:
    try:
        naxis = int(header.get("NAXIS", 0))  # type: ignore[attr-defined]
        return [
            int(header.get(f"NAXIS{axis}", 0))  # type: ignore[attr-defined]
            for axis in range(naxis, 0, -1)
        ]
    except (AttributeError, TypeError, ValueError):
        return []


def _fits_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("astropy"):
        return ExtractionResult(
            "optional.science.fits",
            "2",
            metadata={"format": "fits", "optional_dependency": "astropy"},
            roles=("dataset", "scientific_data", "astronomy_data"),
            warnings=("安装 astropy 后可提取 HDU、shape、header 和表结构",),
        )
    try:
        from astropy.io import fits  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        truncated = False
        with fits.open(context.absolute_path, memmap=True, lazy_load_hdus=True) as hdus:
            for index, hdu in enumerate(hdus):
                if index >= 1000:
                    truncated = True
                    break
                resources.append(
                    ExtractedResource(
                        "fits_hdu",
                        f"hdu:{index}",
                        str(getattr(hdu, "name", "") or f"HDU {index}"),
                        {
                            "index": index,
                            "class": type(hdu).__name__,
                            "shape": _fits_shape_from_header(hdu.header),
                        },
                    )
                )
        return ExtractionResult(
            "optional.science.fits",
            "2",
            metadata={"hdu_count": len(resources)},
            roles=("dataset", "scientific_data", "astronomy_data"),
            resources=tuple(resources),
            warnings=("FITS 超过 1000 个 HDU，仅索引前 1000 个",) if truncated else (),
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.fits",
            "2",
            roles=("dataset", "scientific_data"),
            warnings=(f"FITS 解析失败：{exc}",),
        )
