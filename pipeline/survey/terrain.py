"""Bare-earth elevation from USGS 1 arc-second GeoTIFFs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer


class Terrain:
    def __init__(self, directory: Path, tiles):
        """`tiles` are USGS tile names such as n33w098, from the metro's config."""
        self.tiles = []
        for name in tiles:
            path = directory / f"USGS_1_{name}.tif"
            src = rasterio.open(path)
            arr = src.read(1)
            nodata = src.nodata
            if nodata is not None:
                arr = np.where(arr == nodata, np.nan, arr)
            arr = np.where(arr < -1000, np.nan, arr)
            transformer = None
            if src.crs and not src.crs.is_geographic:
                transformer = Transformer.from_crs(4326, src.crs, always_xy=True)
            self.tiles.append(
                {
                    "arr": arr,
                    "transform": src.transform,
                    "bounds": src.bounds,
                    "crs_geographic": bool(src.crs and src.crs.is_geographic),
                    "transformer": transformer,
                    "height": src.height,
                    "width": src.width,
                    "src": src,
                }
            )

    def close(self) -> None:
        for tile in self.tiles:
            tile["src"].close()

    def sample_m(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        out = np.full(lon.shape, np.nan, dtype=np.float64)
        for tile in self.tiles:
            left, bottom, right, top = tile["bounds"]
            mask = (lon >= left) & (lon <= right) & (lat >= bottom) & (lat <= top) & np.isnan(out)
            if not np.any(mask):
                continue
            xs = lon[mask]
            ys = lat[mask]
            if tile["transformer"] is not None:
                xs, ys = tile["transformer"].transform(xs, ys)
            inv = ~tile["transform"]
            cols = inv.a * xs + inv.b * ys + inv.c
            rows = inv.d * xs + inv.e * ys + inv.f
            cols_i = np.rint(cols).astype(np.int32)
            rows_i = np.rint(rows).astype(np.int32)
            valid = (
                (rows_i >= 0)
                & (rows_i < tile["height"])
                & (cols_i >= 0)
                & (cols_i < tile["width"])
            )
            vals = np.full(xs.shape, np.nan)
            vals[valid] = tile["arr"][rows_i[valid], cols_i[valid]]
            out[mask] = vals
        return out
