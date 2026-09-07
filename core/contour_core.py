"""
contour_core.py
-------------------
Core (non-GUI) logic for generating elevation contour lines from a DSM
or DTM raster and exporting them as Shapefile, DXF, DWG, and/or KML.

Contour extraction uses `contourpy` (the same contouring engine
matplotlib itself uses internally), applied directly to the raster's
pixel-centre coordinate grid - no separate GDAL contour bindings needed.

DWG export note
----------------
There is no free/open way to *write* a native .dwg file in pure Python -
DWG is Autodesk's proprietary format. This module writes a real DXF file
(which AutoCAD/Civil 3D open natively and can "Save As" a DWG in one
click), and additionally attempts an automatic DXF -> DWG conversion via
the free "ODA File Converter" desktop application from the Open Design
Alliance, if it's installed on this machine
(https://www.opendesign.com/guestfiles/oda_file_converter). If it isn't
installed, DWG export is skipped with a clear explanation rather than
failing the whole run - the DXF (a perfectly usable substitute) is still
written.
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import numpy as np
import rasterio
import contourpy
from pyproj import Transformer
from pyproj.crs import CRS
from shapely.geometry import LineString

from .raster_utils import open_processing_dataset, RasterAccessError

try:
    import geopandas as gpd
    _HAS_GEOPANDAS = True
except Exception:  # pragma: no cover
    _HAS_GEOPANDAS = False

try:
    from scipy.ndimage import gaussian_filter
    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    _HAS_SCIPY = False

import ezdxf
from ezdxf.addons import odafc

WGS84 = CRS.from_epsg(4326)
SUPPORTED_CONTOUR_FORMATS = ("shp", "dxf", "dwg", "kml")


class ContourError(Exception):
    """Raised for any recoverable error in the contour pipeline."""


@dataclass
class ContourResult:
    lines: list                              # [{"elevation": float, "coords": Nx2 ndarray}, ...]
    crs: CRS
    interval: float
    stats: dict = field(default_factory=dict)
    written_files: dict = field(default_factory=dict)   # format -> path
    skipped_files: dict = field(default_factory=dict)   # format -> reason


def _band_nodata(dataset, band: int) -> Optional[float]:
    nodatavals = dataset.nodatavals
    if nodatavals and len(nodatavals) >= band and nodatavals[band - 1] is not None:
        return nodatavals[band - 1]
    return dataset.nodata


def _crs_label(crs) -> str:
    try:
        pyproj_crs = CRS.from_user_input(crs)
        name = pyproj_crs.name
        epsg = pyproj_crs.to_epsg()
        return f"{name} (EPSG:{epsg})" if epsg else name
    except Exception:
        return str(crs) if crs else "Unknown"


def generate_contour_lines(
    raster_path: str,
    interval: float,
    band: int = 1,
    smooth_sigma: float = 0.0,
    base_level: Optional[float] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
):
    """
    Read a raster and compute contour line segments at a fixed elevation
    interval. Returns (lines, crs, data_min, data_max) where `lines` is a
    list of {"elevation": float, "coords": Nx2 ndarray in the raster's
    native CRS}.
    """

    def report(pct, msg):
        if progress_callback:
            progress_callback(pct, msg)

    if interval <= 0:
        raise ContourError("Contour interval must be a positive number of metres.")

    report(0.0, "Reading raster...")
    if not os.path.isfile(raster_path):
        raise ContourError(f"File not found: {raster_path}")

    try:
        with open_processing_dataset(raster_path, progress_callback=report) as (ds, note):
            if note:
                report(0.02, note)
            crs = CRS.from_user_input(ds.crs)
            transform = ds.transform
            width, height = ds.width, ds.height
            arr = ds.read(band).astype("float64")
            nodata = _band_nodata(ds, band)
    except RasterAccessError as e:
        raise ContourError(str(e)) from e

    if nodata is not None:
        arr = np.where(np.isclose(arr, nodata, equal_nan=False), np.nan, arr)

    valid = np.isfinite(arr)
    if not valid.any():
        raise ContourError("This raster has no valid (non-nodata) elevation values.")

    report(0.1, "Building coordinate grid...")
    cols, rows = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
    X, Y = transform * (cols, rows)

    if smooth_sigma and smooth_sigma > 0:
        if not _HAS_SCIPY:
            raise ContourError("Smoothing requires scipy, which is not installed.")
        report(0.15, f"Smoothing surface (sigma={smooth_sigma})...")
        fill_value = float(np.nanmean(arr[valid]))
        filled = np.where(valid, arr, fill_value)
        smoothed = gaussian_filter(filled, sigma=smooth_sigma)
        arr = np.where(valid, smoothed, np.nan)

    data_min = float(np.nanmin(arr))
    data_max = float(np.nanmax(arr))

    if base_level is None:
        base_level = math.floor(data_min / interval) * interval

    levels = np.arange(base_level, data_max + interval, interval)
    levels = levels[(levels >= data_min - 1e-9) & (levels <= data_max + 1e-9)]
    if levels.size == 0:
        raise ContourError(
            f"No contour levels fall within the raster's elevation range "
            f"({data_min:.2f} - {data_max:.2f} m) at a {interval} m interval. "
            "Try a smaller interval."
        )

    report(0.25, f"Computing {len(levels)} contour level(s)...")
    cg = contourpy.contour_generator(x=X, y=Y, z=arr, line_type="Separate")

    lines = []
    for i, level in enumerate(levels):
        segs = cg.lines(float(level))
        for seg in segs:
            if seg.shape[0] < 2:
                continue
            lines.append({"elevation": float(level), "coords": np.asarray(seg, dtype="float64")})
        report(0.25 + 0.55 * (i + 1) / len(levels), f"Level {level:g} m: {len(segs)} segment(s)")

    if not lines:
        raise ContourError(
            "No contour lines were generated at this interval - try a smaller interval "
            "or check the raster has real elevation variation."
        )

    report(0.82, "Contour computation complete.")
    return lines, crs, data_min, data_max


def _simplify_lines(lines: list, tolerance: float) -> list:
    if tolerance <= 0:
        return lines
    simplified = []
    for line in lines:
        geom = LineString(line["coords"]).simplify(tolerance, preserve_topology=False)
        coords = np.asarray(geom.coords, dtype="float64")
        if coords.shape[0] >= 2:
            simplified.append({"elevation": line["elevation"], "coords": coords})
    return simplified


def compute_contour_stats(lines: list, interval: float, crs, data_min: float, data_max: float) -> dict:
    if not lines:
        return {}
    elevations = sorted({round(l["elevation"], 6) for l in lines})
    return {
        "segment_count": len(lines),
        "level_count": len(elevations),
        "min_level": min(elevations),
        "max_level": max(elevations),
        "data_min": data_min,
        "data_max": data_max,
        "interval": interval,
        "crs": _crs_label(crs),
    }


# --------------------------------------------------------------------------
# Exporters
# --------------------------------------------------------------------------

def export_contours_shp(lines: list, crs, out_path: str) -> str:
    if not _HAS_GEOPANDAS:
        raise ContourError("geopandas/shapely are required for Shapefile export.")
    geometry = [LineString(l["coords"]) for l in lines]
    gdf = gpd.GeoDataFrame(
        {"Contour_ID": range(1, len(lines) + 1), "Elev_Z": [round(l["elevation"], 4) for l in lines]},
        geometry=geometry,
        crs=crs,
    )
    if not out_path.lower().endswith(".shp"):
        out_path += ".shp"
    gdf.to_file(out_path, driver="ESRI Shapefile")
    return out_path


def export_contours_kml(lines: list, crs, out_path: str, label_prefix: str = "C") -> str:
    """Write contour lines as WGS84 KML LineString placemarks (no external KML library)."""
    if not out_path.lower().endswith(".kml"):
        out_path += ".kml"

    if CRS.from_user_input(crs).to_epsg() == 4326:
        transformer = None
    else:
        transformer = Transformer.from_crs(crs, WGS84, always_xy=True)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "<Document>",
        "<name>Contours</name>",
        '<Style id="contourStyle"><LineStyle><width>2</width></LineStyle></Style>',
    ]
    for i, line in enumerate(lines, start=1):
        coords = line["coords"]
        z = line["elevation"]
        if transformer is not None:
            lon, lat = transformer.transform(coords[:, 0], coords[:, 1])
        else:
            lon, lat = coords[:, 0], coords[:, 1]
        coord_str = " ".join(f"{x:.8f},{y:.8f},{z:.4f}" for x, y in zip(lon, lat))
        parts.append(
            "<Placemark>"
            f"<name>{label_prefix}{i}: {z:.2f}m</name>"
            "<styleUrl>#contourStyle</styleUrl>"
            f"<ExtendedData><Data name=\"Elevation_Z\"><value>{z:.4f}</value></Data></ExtendedData>"
            "<LineString>"
            "<altitudeMode>absolute</altitudeMode>"
            f"<coordinates>{coord_str}</coordinates>"
            "</LineString>"
            "</Placemark>"
        )
    parts.append("</Document>")
    parts.append("</kml>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return out_path


def build_dxf_document(lines: list):
    """Build an in-memory ezdxf Drawing with one LWPOLYLINE per contour
    line, each at a constant elevation and on a per-level layer."""
    doc = ezdxf.new(setup=True)
    msp = doc.modelspace()
    for line in lines:
        z = line["elevation"]
        layer = f"CONTOUR_{z:g}".replace(".", "_")
        points = [(float(x), float(y)) for x, y in line["coords"]]
        msp.add_lwpolyline(points, dxfattribs={"elevation": z, "layer": layer})
    return doc


def export_contours_dxf(doc, out_path: str) -> str:
    if not out_path.lower().endswith(".dxf"):
        out_path += ".dxf"
    doc.saveas(out_path)
    return out_path


def export_contours_dwg(doc, out_path: str) -> str:
    """Convert the given ezdxf Drawing to DWG via the free ODA File
    Converter application. Raises ContourError with clear instructions
    if that converter isn't installed on this machine."""
    if not out_path.lower().endswith(".dwg"):
        out_path += ".dwg"
    if not odafc.is_installed():
        raise ContourError(
            "DWG export needs the free 'ODA File Converter' application installed on "
            "this PC (it cannot be installed via pip - download it from "
            "https://www.opendesign.com/guestfiles/oda_file_converter). "
            "A DXF file was written instead - AutoCAD/Civil 3D opens a DXF directly "
            "and can 'Save As' a DWG in one step."
        )
    try:
        odafc.export_dwg(doc, out_path)
    except odafc.ODAFCError as e:
        raise ContourError(f"DWG conversion failed: {e}")
    return out_path


# --------------------------------------------------------------------------
# High-level orchestrator
# --------------------------------------------------------------------------

def extract_contours(
    raster_path: str,
    interval: float,
    output_dir: str,
    formats: Iterable[str] = ("dxf",),
    band: int = 1,
    smooth_sigma: float = 0.0,
    simplify_tolerance: float = 0.0,
    base_level: Optional[float] = None,
    base_name: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> ContourResult:
    formats = tuple(f.lower() for f in formats)
    for f in formats:
        if f not in SUPPORTED_CONTOUR_FORMATS:
            raise ContourError(f"Unsupported output format: {f}")
    if not formats:
        raise ContourError("Select at least one output format (SHP, DXF, DWG, or KML).")

    os.makedirs(output_dir, exist_ok=True)
    base_name = base_name or (
        os.path.splitext(os.path.basename(raster_path))[0] + f"_contours_{interval}m"
    )

    lines, crs, data_min, data_max = generate_contour_lines(
        raster_path, interval, band=band, smooth_sigma=smooth_sigma,
        base_level=base_level, progress_callback=progress_callback,
    )

    if progress_callback:
        progress_callback(0.85, "Simplifying lines..." if simplify_tolerance > 0 else "Preparing export...")
    lines = _simplify_lines(lines, simplify_tolerance)
    if not lines:
        raise ContourError("Simplification removed every contour line - lower the simplify tolerance.")

    stats = compute_contour_stats(lines, interval, crs, data_min, data_max)
    written = {}
    skipped = {}

    if progress_callback:
        progress_callback(0.9, "Writing output files...")

    if "shp" in formats:
        written["shp"] = export_contours_shp(lines, crs, os.path.join(output_dir, base_name + ".shp"))
    if "kml" in formats:
        written["kml"] = export_contours_kml(lines, crs, os.path.join(output_dir, base_name + ".kml"))

    if "dxf" in formats or "dwg" in formats:
        doc = build_dxf_document(lines)
        if "dxf" in formats:
            written["dxf"] = export_contours_dxf(doc, os.path.join(output_dir, base_name + ".dxf"))
        if "dwg" in formats:
            try:
                written["dwg"] = export_contours_dwg(doc, os.path.join(output_dir, base_name + ".dwg"))
            except ContourError as e:
                skipped["dwg"] = str(e)
                if "dxf" not in written:
                    written["dxf"] = export_contours_dxf(doc, os.path.join(output_dir, base_name + ".dxf"))

    if progress_callback:
        progress_callback(1.0, "Done.")

    return ContourResult(
        lines=lines,
        crs=crs,
        interval=interval,
        stats=stats,
        written_files=written,
        skipped_files=skipped,
    )
