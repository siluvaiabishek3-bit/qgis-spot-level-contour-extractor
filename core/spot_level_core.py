"""
spot_level_core.py
-------------------
Core (non-GUI) logic for extracting regularly-gridded spot levels
(elevation points) from a DSM or DTM raster (GeoTIFF, IMG, ASC, etc.)
and exporting them as CSV, Shapefile, and/or KML.

This module has no GUI dependencies so it can be tested/used on its own,
e.g. from a script or notebook:

    from spot_level_core import extract_spot_levels
    result = extract_spot_levels("dsm.tif", grid_interval=2.0,
                                  output_dir="out", formats=("csv", "kml", "shp"))
    print(result.stats)
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from pyproj.crs import CRS

from .raster_utils import open_processing_dataset, RasterAccessError

try:
    import geopandas as gpd
    from shapely.geometry import Point
    _HAS_GEOPANDAS = True
except Exception:  # pragma: no cover
    _HAS_GEOPANDAS = False

WGS84 = CRS.from_epsg(4326)

SUPPORTED_FORMATS = ("csv", "shp", "kml")


class SpotLevelError(Exception):
    """Raised for any recoverable error in the extraction pipeline."""


@dataclass
class SpotLevelResult:
    """Container for the outcome of an extraction run."""

    dataframe: pd.DataFrame                 # columns: point_id, X, Y, Z, lon, lat
    crs: CRS
    grid_interval: float
    sampling_method: str
    stats: dict = field(default_factory=dict)
    written_files: dict = field(default_factory=dict)  # format -> path


def _band_nodata(dataset: "rasterio.io.DatasetReader", band: int) -> Optional[float]:
    """Return the nodata value for a band, handling per-band nodata arrays."""
    nodatavals = dataset.nodatavals
    if nodatavals and len(nodatavals) >= band and nodatavals[band - 1] is not None:
        return nodatavals[band - 1]
    return dataset.nodata


def read_raster_info(raster_path: str) -> dict:
    """Open a raster and return basic metadata without loading full pixel data."""
    if not os.path.isfile(raster_path):
        raise SpotLevelError(f"File not found: {raster_path}")

    with rasterio.open(raster_path) as ds:
        info = {
            "path": raster_path,
            "driver": ds.driver,
            "width": ds.width,
            "height": ds.height,
            "band_count": ds.count,
            "crs": ds.crs,
            "bounds": ds.bounds,          # left, bottom, right, top
            "transform": ds.transform,
            "res": ds.res,                # (x_res, y_res) in CRS units
            "nodata": _band_nodata(ds, 1),
            "is_projected": bool(ds.crs and ds.crs.is_projected),
            "units": (ds.crs.linear_units if ds.crs else None),
        }
    return info


def generate_grid_coordinates(
    bounds,
    interval: float,
    align_to_round_grid: bool = True,
):
    """
    Build 1D arrays of X and Y grid-line coordinates covering `bounds`
    (a rasterio BoundingBox: left, bottom, right, top) spaced `interval`
    CRS units (normally metres) apart.

    If align_to_round_grid is True, grid lines are snapped to clean
    multiples of `interval` (e.g. 300000, 300002, 300004 ...) instead of
    starting exactly at the raster's own (usually arbitrary) corner -
    this is the convention most survey/engineering spot-level grids use.
    """
    if interval <= 0:
        raise SpotLevelError("Grid interval must be a positive number of metres.")

    left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top

    if align_to_round_grid:
        x_start = math.ceil(left / interval) * interval
        y_start = math.ceil(bottom / interval) * interval
    else:
        x_start = left
        y_start = bottom

    if x_start > right or y_start > top:
        raise SpotLevelError(
            "Grid interval is larger than the raster extent - no grid points would fall inside it."
        )

    xs = np.arange(x_start, right + 1e-9, interval)
    ys = np.arange(y_start, top + 1e-9, interval)
    return xs, ys


def sample_points(
    ds,
    xs: Iterable[float],
    ys: Iterable[float],
    band: int = 1,
    method: str = "nearest",
) -> np.ndarray:
    """
    Sample elevation (or any single-band raster value) at the given
    X, Y coordinates (in the dataset's own CRS) from an already-open
    rasterio dataset (or WarpedVRT).

    method: "nearest"  - value of the pixel the point falls in (fast, matches
                          how most GIS "sample raster by grid" tools behave).
            "bilinear"  - bilinearly interpolated between the 4 surrounding
                          pixel centres (smoother surface).
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)

    if method == "nearest":
        gen = ds.sample(zip(xs, ys), indexes=band)
        values = np.array([v[0] for v in gen], dtype="float64")
    elif method == "bilinear":
        from scipy.ndimage import map_coordinates

        arr = ds.read(band).astype("float64")
        inv_transform = ~ds.transform
        cols, rows = inv_transform * (xs, ys)
        # map_coordinates expects (row, col) order, pixel-centre convention
        values = map_coordinates(
            arr, [rows - 0.5, cols - 0.5], order=1, mode="nearest"
        )
    else:
        raise SpotLevelError(f"Unknown sampling method: {method}")

    nodata = _band_nodata(ds, band)
    if nodata is not None:
        values = np.where(np.isclose(values, nodata, equal_nan=False), np.nan, values)
    return values


def build_spot_level_dataframe(
    raster_path: str,
    grid_interval: float,
    band: int = 1,
    method: str = "nearest",
    align_to_round_grid: bool = True,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> tuple[pd.DataFrame, CRS]:
    """
    Extract a regular grid of spot levels from a raster.

    Returns (dataframe, crs). The dataframe has columns:
        point_id, X, Y, Z, lon, lat
    where X, Y, Z are in the raster's native CRS/units and lon/lat are
    WGS84 geographic coordinates (needed for KML / GPS use).

    Rows where the raster is nodata at that grid point are dropped.
    """

    def report(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    report(0.0, "Reading raster metadata...")
    if not os.path.isfile(raster_path):
        raise SpotLevelError(f"File not found: {raster_path}")

    try:
        with open_processing_dataset(raster_path, progress_callback=report) as (ds, note):
            if note:
                report(0.04, note)
            crs = CRS.from_user_input(ds.crs)

            report(0.05, "Building extraction grid...")
            xs_line, ys_line = generate_grid_coordinates(ds.bounds, grid_interval, align_to_round_grid)
            grid_x, grid_y = np.meshgrid(xs_line, ys_line)
            flat_x = grid_x.ravel()
            flat_y = grid_y.ravel()
            total_points = flat_x.size

            if total_points == 0:
                raise SpotLevelError("No grid points fall inside the raster extent.")
            if total_points > 2_000_000:
                raise SpotLevelError(
                    f"This grid interval would generate {total_points:,} points, which is too many. "
                    "Use a larger grid interval."
                )

            report(0.15, f"Sampling {total_points:,} grid points from raster (this may take a moment)...")

            # Sample in chunks so we can report progress on very large grids.
            chunk_size = 200_000
            z_values = np.empty(total_points, dtype="float64")
            for start in range(0, total_points, chunk_size):
                end = min(start + chunk_size, total_points)
                z_values[start:end] = sample_points(
                    ds, flat_x[start:end], flat_y[start:end], band=band, method=method
                )
                report(0.15 + 0.55 * (end / total_points), f"Sampled {end:,} / {total_points:,} points")
    except RasterAccessError as e:
        raise SpotLevelError(str(e)) from e

    valid_mask = ~np.isnan(z_values)
    flat_x, flat_y, z_values = flat_x[valid_mask], flat_y[valid_mask], z_values[valid_mask]

    if flat_x.size == 0:
        raise SpotLevelError(
            "Every grid point fell on a nodata pixel. Check the raster extent/nodata value."
        )

    report(0.75, "Reprojecting to WGS84 for lat/lon...")
    if crs.to_epsg() == 4326:
        lon, lat = flat_x, flat_y
    else:
        transformer = Transformer.from_crs(crs, WGS84, always_xy=True)
        lon, lat = transformer.transform(flat_x, flat_y)

    report(0.85, "Assembling results table...")
    df = pd.DataFrame(
        {
            "point_id": np.arange(1, flat_x.size + 1, dtype=int),
            "X": flat_x,
            "Y": flat_y,
            "Z": z_values,
            "lon": lon,
            "lat": lat,
        }
    )
    report(0.9, "Extraction complete.")
    return df, crs


def _crs_units(crs) -> str:
    try:
        pyproj_crs = CRS.from_user_input(crs)
        if pyproj_crs.axis_info:
            return pyproj_crs.axis_info[0].unit_name
    except Exception:
        return "unknown"
    return "unknown"


def _crs_label(crs) -> str:
    try:
        pyproj_crs = CRS.from_user_input(crs)
        name = pyproj_crs.name
        epsg = pyproj_crs.to_epsg()
        return f"{name} (EPSG:{epsg})" if epsg else name
    except Exception:
        return str(crs) if crs else "Unknown"


def compute_stats(df: pd.DataFrame, grid_interval: float, crs) -> dict:
    if df.empty:
        return {}
    return {
        "point_count": int(len(df)),
        "min_elevation": float(df["Z"].min()),
        "max_elevation": float(df["Z"].max()),
        "mean_elevation": float(df["Z"].mean()),
        "std_elevation": float(df["Z"].std()) if len(df) > 1 else 0.0,
        "grid_interval": grid_interval,
        "crs": _crs_label(crs),
        "crs_units": _crs_units(crs),
    }


# --------------------------------------------------------------------------
# Exporters
# --------------------------------------------------------------------------

def export_csv(df: pd.DataFrame, out_path: str) -> str:
    out = df.rename(
        columns={
            "point_id": "Point_ID",
            "X": "Easting_X",
            "Y": "Northing_Y",
            "Z": "Elevation_Z",
            "lon": "Longitude",
            "lat": "Latitude",
        }
    )
    out.to_csv(out_path, index=False, float_format="%.4f")
    return out_path


def export_shapefile(df: pd.DataFrame, crs: CRS, out_path: str) -> str:
    if not _HAS_GEOPANDAS:
        raise SpotLevelError("geopandas/shapely are required for Shapefile export.")
    geometry = [Point(x, y) for x, y in zip(df["X"], df["Y"])]
    gdf = gpd.GeoDataFrame(
        {
            "Point_ID": df["point_id"],
            "Elev_Z": df["Z"].round(4),
        },
        geometry=geometry,
        crs=crs,
    )
    if not out_path.lower().endswith(".shp"):
        out_path += ".shp"
    gdf.to_file(out_path, driver="ESRI Shapefile")
    return out_path


def export_kml(df: pd.DataFrame, out_path: str, label_prefix: str = "SL") -> str:
    """
    Write a KML file of point placemarks (lon/lat/elevation) with no
    external KML library dependency - plain XML.
    """
    if not out_path.lower().endswith(".kml"):
        out_path += ".kml"

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        "<name>Spot Levels</name>",
        "<Style id=\"spotLevelStyle\">",
        "  <IconStyle><scale>0.7</scale></IconStyle>",
        "  <LabelStyle><scale>0.7</scale></LabelStyle>",
        "</Style>",
    ]
    for row in df.itertuples(index=False):
        name = f"{label_prefix}{int(row.point_id)}: {row.Z:.2f}m"
        parts.append(
            "<Placemark>"
            f"<name>{name}</name>"
            "<styleUrl>#spotLevelStyle</styleUrl>"
            "<ExtendedData>"
            f"<Data name=\"Point_ID\"><value>{int(row.point_id)}</value></Data>"
            f"<Data name=\"Easting_X\"><value>{row.X:.4f}</value></Data>"
            f"<Data name=\"Northing_Y\"><value>{row.Y:.4f}</value></Data>"
            f"<Data name=\"Elevation_Z\"><value>{row.Z:.4f}</value></Data>"
            "</ExtendedData>"
            "<Point>"
            "<altitudeMode>absolute</altitudeMode>"
            f"<coordinates>{row.lon:.8f},{row.lat:.8f},{row.Z:.4f}</coordinates>"
            "</Point>"
            "</Placemark>"
        )
    parts.append("</Document>")
    parts.append("</kml>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


# --------------------------------------------------------------------------
# High-level orchestrator
# --------------------------------------------------------------------------

def extract_spot_levels(
    raster_path: str,
    grid_interval: float,
    output_dir: str,
    formats: Iterable[str] = ("csv",),
    band: int = 1,
    method: str = "nearest",
    align_to_round_grid: bool = True,
    base_name: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> SpotLevelResult:
    """
    End-to-end: read raster -> build grid -> sample -> export requested formats.
    Returns a SpotLevelResult with the dataframe, stats, and paths written.
    """
    formats = tuple(f.lower() for f in formats)
    for f in formats:
        if f not in SUPPORTED_FORMATS:
            raise SpotLevelError(f"Unsupported output format: {f}")
    if not formats:
        raise SpotLevelError("Select at least one output format (CSV, Shapefile, or KML).")

    os.makedirs(output_dir, exist_ok=True)
    base_name = base_name or (
        os.path.splitext(os.path.basename(raster_path))[0] + f"_spotlevels_{grid_interval}m"
    )

    df, crs = build_spot_level_dataframe(
        raster_path,
        grid_interval,
        band=band,
        method=method,
        align_to_round_grid=align_to_round_grid,
        progress_callback=progress_callback,
    )

    stats = compute_stats(df, grid_interval, crs)
    written = {}

    if progress_callback:
        progress_callback(0.92, "Writing output files...")

    if "csv" in formats:
        written["csv"] = export_csv(df, os.path.join(output_dir, base_name + ".csv"))
    if "shp" in formats:
        written["shp"] = export_shapefile(df, crs, os.path.join(output_dir, base_name + ".shp"))
    if "kml" in formats:
        written["kml"] = export_kml(df, os.path.join(output_dir, base_name + ".kml"))

    if progress_callback:
        progress_callback(1.0, "Done.")

    return SpotLevelResult(
        dataframe=df,
        crs=crs,
        grid_interval=grid_interval,
        sampling_method=method,
        stats=stats,
        written_files=written,
    )
