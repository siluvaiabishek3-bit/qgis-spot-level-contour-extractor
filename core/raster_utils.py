"""
raster_utils.py
-------------------
Shared helper used by both spot_level_core.py and contour_core.py:
transparently reprojects a raster that's in a geographic CRS (degrees,
e.g. plain WGS84 lat/lon) into an appropriate UTM zone before any
grid/contour math runs, so that "2 m" always means 2 real metres on the
ground - regardless of what CRS the input DSM/DTM happens to be in.

No file is written to disk for this - it uses GDAL's WarpedVRT, a
virtual/in-memory reprojected view of the source dataset that behaves
like a normal rasterio dataset (same .read(), .sample(), .transform,
.crs, .bounds, .nodata attributes) but returns already-reprojected
pixels on read.
"""

from __future__ import annotations

import math
from contextlib import contextmanager

import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.enums import Resampling
from pyproj import Transformer
from pyproj.crs import CRS

WGS84 = CRS.from_epsg(4326)


class RasterAccessError(Exception):
    """Raised when a raster can't be opened or has no usable CRS."""


def utm_crs_for_bounds(bounds, src_crs) -> CRS:
    """
    Pick the appropriate UTM zone CRS (EPSG:326xx / 327xx) for a raster's
    extent, based on the longitude/latitude of its centre point.
    """
    src_crs = CRS.from_user_input(src_crs)
    cx = (bounds.left + bounds.right) / 2
    cy = (bounds.bottom + bounds.top) / 2
    if src_crs.to_epsg() == 4326:
        lon, lat = cx, cy
    else:
        transformer = Transformer.from_crs(src_crs, WGS84, always_xy=True)
        lon, lat = transformer.transform(cx, cy)
    zone = int(math.floor((lon + 180) / 6) + 1)
    zone = min(max(zone, 1), 60)
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


@contextmanager
def open_processing_dataset(raster_path: str, progress_callback=None):
    """
    Open a raster for grid/contour processing.

    If its CRS is geographic (degrees), transparently reproject it (via
    an in-memory WarpedVRT - no file written to disk) into the
    appropriate UTM zone, so every downstream interval/spacing value is
    a real metre distance. If it's already a projected CRS, it's opened
    as-is with no reprojection overhead.

    Yields (dataset, note) where `dataset` is a rasterio-dataset-like
    object (either the plain file or a WarpedVRT) and `note` is an empty
    string, or an explanatory message if reprojection happened.
    """
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise RasterAccessError(
                "This raster has no coordinate reference system (CRS) defined. "
                "Assign a CRS in QGIS/ArcGIS before running either tool."
            )

        if src.crs.is_geographic:
            target_crs = utm_crs_for_bounds(src.bounds, src.crs)
            if progress_callback:
                progress_callback(
                    0.02,
                    f"Raster is in a geographic CRS - reprojecting to {target_crs.to_string()} "
                    "for accurate metre-based spacing...",
                )
            transform, width, height = calculate_default_transform(
                src.crs, target_crs, src.width, src.height, *src.bounds
            )
            with WarpedVRT(
                src, crs=target_crs, transform=transform, width=width, height=height,
                resampling=Resampling.bilinear, nodata=src.nodata,
            ) as vrt:
                note = (
                    f"Auto-reprojected from {src.crs.to_string()} (geographic) to "
                    f"{target_crs.to_string()} (UTM) for metre-based processing."
                )
                yield vrt, note
        else:
            yield src, ""


def describe_target_crs(info: dict) -> str | None:
    """
    Given a read_raster_info()-style dict, return a short description of
    the UTM zone that would be auto-used for processing if the raster's
    CRS is geographic, or None if no reprojection is needed.
    """
    crs = info.get("crs")
    bounds = info.get("bounds")
    if crs is None or bounds is None:
        return None
    crs = CRS.from_user_input(crs)
    if not crs.is_geographic:
        return None
    target = utm_crs_for_bounds(bounds, crs)
    return target.to_string()
