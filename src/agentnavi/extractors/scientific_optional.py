from __future__ import annotations

from .api import ExtractedResource, ExtractionContext, ExtractionResult, ResourceRelation
from .scientific_common import _module_available

def _hdf5_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("h5py"):
        return ExtractionResult(
            "optional.science.hdf5",
            "1",
            metadata={"format": "hdf5", "optional_dependency": "h5py"},
            roles=("dataset", "scientific_data", "hierarchical_data"),
            warnings=("安装 h5py 后可提取 group、dataset、shape 和 dtype",),
        )
    try:
        import h5py  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        with h5py.File(context.absolute_path, "r") as handle:
            def visitor(name: str, item: object) -> None:
                if len(resources) >= 1000:
                    return
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

            handle.visititems(visitor)
            attrs = {str(key): str(value)[:500] for key, value in list(handle.attrs.items())[:100]}
        warning = ("HDF5 包含超过 1000 个对象，仅索引前 1000 个",) if len(resources) >= 1000 else ()
        return ExtractionResult(
            "optional.science.hdf5",
            "1",
            metadata={"object_count": len(resources), "root_attributes": attrs},
            roles=("dataset", "scientific_data", "hierarchical_data"),
            resources=tuple(resources),
            warnings=warning,
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.hdf5",
            "1",
            roles=("dataset", "scientific_data"),
            warnings=(f"HDF5 解析失败：{exc}",),
        )


def _netcdf_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("netCDF4"):
        return ExtractionResult(
            "optional.science.netcdf",
            "1",
            metadata={"format": "netcdf", "optional_dependency": "netCDF4"},
            roles=("dataset", "scientific_data", "multidimensional_data"),
            warnings=("安装 netCDF4 后可提取维度、变量、单位和属性",),
        )
    try:
        import netCDF4  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        with netCDF4.Dataset(context.absolute_path, "r") as dataset:
            dimensions = {name: len(value) for name, value in dataset.dimensions.items()}
            for name, variable in list(dataset.variables.items())[:1000]:
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
            attributes = {name: str(dataset.getncattr(name))[:500] for name in dataset.ncattrs()[:100]}
        return ExtractionResult(
            "optional.science.netcdf",
            "1",
            metadata={"dimensions": dimensions, "variable_count": len(resources), "attributes": attributes},
            roles=("dataset", "scientific_data", "multidimensional_data"),
            resources=tuple(resources),
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.netcdf",
            "1",
            roles=("dataset", "scientific_data"),
            warnings=(f"NetCDF 解析失败：{exc}",),
        )


def _mat_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("scipy"):
        return ExtractionResult(
            "optional.science.matlab",
            "1",
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
            "1",
            metadata={"variable_count": len(variables), "variables": [name for name, _, _ in variables[:200]]},
            roles=("dataset", "scientific_data", "array_data"),
            resources=resources,
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.matlab",
            "1",
            roles=("dataset", "scientific_data"),
            warnings=(f"MAT 解析失败：{exc}",),
        )


def _fits_extract(context: ExtractionContext) -> ExtractionResult:
    if not _module_available("astropy"):
        return ExtractionResult(
            "optional.science.fits",
            "1",
            metadata={"format": "fits", "optional_dependency": "astropy"},
            roles=("dataset", "scientific_data", "astronomy_data"),
            warnings=("安装 astropy 后可提取 HDU、shape、header 和表结构",),
        )
    try:
        from astropy.io import fits  # type: ignore[import-not-found]

        resources: list[ExtractedResource] = []
        with fits.open(context.absolute_path, memmap=True, lazy_load_hdus=True) as hdus:
            for index, hdu in enumerate(hdus[:1000]):
                shape = list(getattr(getattr(hdu, "data", None), "shape", ()) or ())
                resources.append(
                    ExtractedResource(
                        "fits_hdu",
                        f"hdu:{index}",
                        str(getattr(hdu, "name", "") or f"HDU {index}"),
                        {"index": index, "class": type(hdu).__name__, "shape": shape},
                    )
                )
        return ExtractionResult(
            "optional.science.fits",
            "1",
            metadata={"hdu_count": len(resources)},
            roles=("dataset", "scientific_data", "astronomy_data"),
            resources=tuple(resources),
        )
    except Exception as exc:
        return ExtractionResult(
            "optional.science.fits",
            "1",
            roles=("dataset", "scientific_data"),
            warnings=(f"FITS 解析失败：{exc}",),
        )

