"""
spot_levels_algorithm.py
-------------------
Processing algorithm: extract a regular grid of spot-level elevation
points from a DSM/DTM raster (any coordinate system - a geographic CRS
is auto-reprojected to UTM first) and export as CSV / Shapefile / KML.
"""

import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterBand,
    QgsProcessingParameterFileDestination,
    QgsProcessingOutputVectorLayer,
    QgsProcessingOutputFile,
    QgsProcessingException,
)
from qgis.PyQt.QtGui import QIcon

from ..core.spot_level_core import (
    build_spot_level_dataframe,
    compute_stats,
    export_csv,
    export_shapefile,
    export_kml,
    SpotLevelError,
)


class SpotLevelsAlgorithm(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    GRID_INTERVAL = "GRID_INTERVAL"
    SAMPLING_METHOD = "SAMPLING_METHOD"
    ALIGN_TO_ROUND_GRID = "ALIGN_TO_ROUND_GRID"
    BAND = "BAND"
    OUTPUT_CSV = "OUTPUT_CSV"
    OUTPUT_SHP = "OUTPUT_SHP"
    OUTPUT_KML = "OUTPUT_KML"

    METHODS = ["Nearest", "Bilinear"]

    def createInstance(self):
        return SpotLevelsAlgorithm()

    def name(self):
        return "extract_spot_levels"

    def displayName(self):
        return "Extract Spot Levels"

    def group(self):
        return "Spot Level & Contour Extractor"

    def groupId(self):
        return "spotlevelcontour"

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.png")
        if os.path.isfile(icon_path):
            return QIcon(icon_path)
        return super().icon()

    def shortHelpString(self):
        return (
            "Samples elevation at a regular grid across a DSM/DTM raster and exports the "
            "points as CSV, Shapefile, and/or KML.\n\n"
            "Works with any coordinate system: a raster in a geographic CRS (plain "
            "latitude/longitude) is automatically reprojected in memory to the appropriate "
            "UTM zone first, so the grid interval is always a true metre spacing on the "
            "ground, regardless of the input raster's native CRS.\n\n"
            "Tick at least one output format below - leave the others blank to skip them."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.INPUT, "Input DSM/DTM raster"))

        self.addParameter(QgsProcessingParameterBand(
            self.BAND, "Band", parentLayerParameterName=self.INPUT, defaultValue=1
        ))

        param = QgsProcessingParameterNumber(
            self.GRID_INTERVAL, "Grid interval (metres)",
            type=QgsProcessingParameterNumber.Double, defaultValue=2.0, minValue=0.0001,
        )
        self.addParameter(param)

        self.addParameter(QgsProcessingParameterEnum(
            self.SAMPLING_METHOD, "Sampling method", options=self.METHODS, defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.ALIGN_TO_ROUND_GRID, "Snap grid to round coordinates (survey-style grid)",
            defaultValue=True,
        ))

        for param_name, label, file_filter in (
            (self.OUTPUT_CSV, "Output CSV (leave blank to skip)", "CSV files (*.csv)"),
            (self.OUTPUT_SHP, "Output Shapefile (leave blank to skip)", "ESRI Shapefile (*.shp)"),
            (self.OUTPUT_KML, "Output KML (leave blank to skip)", "KML files (*.kml)"),
        ):
            out_param = QgsProcessingParameterFileDestination(
                param_name, label, fileFilter=file_filter, optional=True, createByDefault=False,
            )
            self.addParameter(out_param)

        self.addOutput(QgsProcessingOutputFile(self.OUTPUT_CSV, "Output CSV"))
        self.addOutput(QgsProcessingOutputVectorLayer(self.OUTPUT_SHP, "Output Shapefile"))
        self.addOutput(QgsProcessingOutputVectorLayer(self.OUTPUT_KML, "Output KML"))

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if layer is None:
            raise QgsProcessingException("No input raster provided.")
        raster_path = layer.source()

        grid_interval = self.parameterAsDouble(parameters, self.GRID_INTERVAL, context)
        method_idx = self.parameterAsEnum(parameters, self.SAMPLING_METHOD, context)
        method = "nearest" if method_idx == 0 else "bilinear"
        align = self.parameterAsBoolean(parameters, self.ALIGN_TO_ROUND_GRID, context)
        band = self.parameterAsInt(parameters, self.BAND, context) or 1

        csv_path = self.parameterAsFileOutput(parameters, self.OUTPUT_CSV, context)
        shp_path = self.parameterAsFileOutput(parameters, self.OUTPUT_SHP, context)
        kml_path = self.parameterAsFileOutput(parameters, self.OUTPUT_KML, context)

        if not any((csv_path, shp_path, kml_path)):
            raise QgsProcessingException(
                "Select at least one output (CSV, Shapefile, or KML) before running."
            )

        def progress_callback(pct, msg):
            feedback.setProgress(int(max(0.0, min(1.0, pct)) * 100))
            if msg:
                feedback.pushInfo(str(msg))

        try:
            df, crs = build_spot_level_dataframe(
                raster_path,
                grid_interval,
                band=band,
                method=method,
                align_to_round_grid=align,
                progress_callback=progress_callback,
            )
        except SpotLevelError as e:
            raise QgsProcessingException(str(e))

        stats = compute_stats(df, grid_interval, crs)
        feedback.pushInfo(
            f"Extracted {stats.get('point_count', 0):,} spot levels "
            f"(min {stats.get('min_elevation', 0):.2f} m, max {stats.get('max_elevation', 0):.2f} m, "
            f"CRS used: {stats.get('crs', '?')})."
        )

        results = {}
        if csv_path:
            for d in (os.path.dirname(csv_path),):
                os.makedirs(d, exist_ok=True) if d else None
            results[self.OUTPUT_CSV] = export_csv(df, csv_path)
        if shp_path:
            d = os.path.dirname(shp_path)
            if d:
                os.makedirs(d, exist_ok=True)
            results[self.OUTPUT_SHP] = export_shapefile(df, crs, shp_path)
        if kml_path:
            d = os.path.dirname(kml_path)
            if d:
                os.makedirs(d, exist_ok=True)
            results[self.OUTPUT_KML] = export_kml(df, kml_path)

        return results
