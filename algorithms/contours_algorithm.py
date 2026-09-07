"""
contours_algorithm.py
-------------------
Processing algorithm: generate elevation contour lines from a DSM/DTM
raster (any coordinate system - a geographic CRS is auto-reprojected to
UTM first) and export as Shapefile / DXF / DWG / KML.
"""

import os

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterBand,
    QgsProcessingParameterFileDestination,
    QgsProcessingOutputVectorLayer,
    QgsProcessingOutputFile,
    QgsProcessingException,
)
from qgis.PyQt.QtGui import QIcon

from ..core.contour_core import (
    generate_contour_lines,
    _simplify_lines,
    compute_contour_stats,
    export_contours_shp,
    export_contours_kml,
    build_dxf_document,
    export_contours_dxf,
    export_contours_dwg,
    ContourError,
)


class ContoursAlgorithm(QgsProcessingAlgorithm):

    INPUT = "INPUT"
    BAND = "BAND"
    CONTOUR_INTERVAL = "CONTOUR_INTERVAL"
    SMOOTH_SIGMA = "SMOOTH_SIGMA"
    SIMPLIFY_TOLERANCE = "SIMPLIFY_TOLERANCE"
    OUTPUT_SHP = "OUTPUT_SHP"
    OUTPUT_DXF = "OUTPUT_DXF"
    OUTPUT_DWG = "OUTPUT_DWG"
    OUTPUT_KML = "OUTPUT_KML"

    def createInstance(self):
        return ContoursAlgorithm()

    def name(self):
        return "generate_contours"

    def displayName(self):
        return "Generate Contours"

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
            "Generates elevation contour lines from a DSM/DTM raster at a fixed interval "
            "and exports them as Shapefile, DXF, DWG, and/or KML.\n\n"
            "Works with any coordinate system: a raster in a geographic CRS (plain "
            "latitude/longitude) is automatically reprojected in memory to the appropriate "
            "UTM zone first, so the contour interval is always a true metre spacing.\n\n"
            "DWG export needs the free ODA File Converter application installed on this "
            "machine (it cannot be pip-installed). If it isn't found, DWG is skipped and a "
            "DXF is written instead - AutoCAD/Civil 3D can open that DXF directly and "
            "'Save As' a DWG in one step.\n\n"
            "Tick at least one output format below - leave the others blank to skip them."
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.INPUT, "Input DSM/DTM raster"))

        self.addParameter(QgsProcessingParameterBand(
            self.BAND, "Band", parentLayerParameterName=self.INPUT, defaultValue=1
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.CONTOUR_INTERVAL, "Contour interval (metres)",
            type=QgsProcessingParameterNumber.Type.Double, defaultValue=1.0, minValue=0.0001,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_SIGMA, "Surface smoothing sigma (pixels, 0 = none)",
            type=QgsProcessingParameterNumber.Type.Double, defaultValue=0.0, minValue=0.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SIMPLIFY_TOLERANCE, "Simplify tolerance (metres, 0 = keep every vertex)",
            type=QgsProcessingParameterNumber.Type.Double, defaultValue=0.0, minValue=0.0,
            optional=True,
        ))

        for param_name, label, file_filter in (
            (self.OUTPUT_SHP, "Output Shapefile (leave blank to skip)", "ESRI Shapefile (*.shp)"),
            (self.OUTPUT_DXF, "Output DXF (leave blank to skip)", "DXF files (*.dxf)"),
            (self.OUTPUT_DWG, "Output DWG (leave blank to skip)", "DWG files (*.dwg)"),
            (self.OUTPUT_KML, "Output KML (leave blank to skip)", "KML files (*.kml)"),
        ):
            out_param = QgsProcessingParameterFileDestination(
                param_name, label, fileFilter=file_filter, optional=True, createByDefault=False,
            )
            self.addParameter(out_param)

        self.addOutput(QgsProcessingOutputVectorLayer(self.OUTPUT_SHP, "Output Shapefile"))
        self.addOutput(QgsProcessingOutputFile(self.OUTPUT_DXF, "Output DXF"))
        self.addOutput(QgsProcessingOutputFile(self.OUTPUT_DWG, "Output DWG"))
        self.addOutput(QgsProcessingOutputVectorLayer(self.OUTPUT_KML, "Output KML"))

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if layer is None:
            raise QgsProcessingException("No input raster provided.")
        raster_path = layer.source()

        interval = self.parameterAsDouble(parameters, self.CONTOUR_INTERVAL, context)
        smooth_sigma = self.parameterAsDouble(parameters, self.SMOOTH_SIGMA, context) or 0.0
        simplify_tolerance = self.parameterAsDouble(parameters, self.SIMPLIFY_TOLERANCE, context) or 0.0
        band = self.parameterAsInt(parameters, self.BAND, context) or 1

        shp_path = self.parameterAsFileOutput(parameters, self.OUTPUT_SHP, context)
        dxf_path = self.parameterAsFileOutput(parameters, self.OUTPUT_DXF, context)
        dwg_path = self.parameterAsFileOutput(parameters, self.OUTPUT_DWG, context)
        kml_path = self.parameterAsFileOutput(parameters, self.OUTPUT_KML, context)

        if not any((shp_path, dxf_path, dwg_path, kml_path)):
            raise QgsProcessingException(
                "Select at least one output (Shapefile, DXF, DWG, or KML) before running."
            )

        def progress_callback(pct, msg):
            feedback.setProgress(int(max(0.0, min(1.0, pct)) * 100))
            if msg:
                feedback.pushInfo(str(msg))

        try:
            lines, crs, data_min, data_max = generate_contour_lines(
                raster_path, interval, band=band, smooth_sigma=smooth_sigma,
                base_level=None, progress_callback=progress_callback,
            )
            if simplify_tolerance > 0:
                lines = _simplify_lines(lines, simplify_tolerance)
                if not lines:
                    raise ContourError(
                        "Simplification removed every contour line - lower the simplify tolerance."
                    )
        except ContourError as e:
            raise QgsProcessingException(str(e))

        stats = compute_contour_stats(lines, interval, crs, data_min, data_max)
        feedback.pushInfo(
            f"Generated {stats.get('segment_count', 0)} contour segment(s) across "
            f"{stats.get('level_count', 0)} level(s) (CRS used: {stats.get('crs', '?')})."
        )

        results = {}

        if shp_path:
            d = os.path.dirname(shp_path)
            if d:
                os.makedirs(d, exist_ok=True)
            results[self.OUTPUT_SHP] = export_contours_shp(lines, crs, shp_path)

        if kml_path:
            d = os.path.dirname(kml_path)
            if d:
                os.makedirs(d, exist_ok=True)
            results[self.OUTPUT_KML] = export_contours_kml(lines, crs, kml_path)

        if dxf_path or dwg_path:
            doc = build_dxf_document(lines)
            if dxf_path:
                d = os.path.dirname(dxf_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                results[self.OUTPUT_DXF] = export_contours_dxf(doc, dxf_path)
            if dwg_path:
                d = os.path.dirname(dwg_path)
                if d:
                    os.makedirs(d, exist_ok=True)
                try:
                    results[self.OUTPUT_DWG] = export_contours_dwg(doc, dwg_path)
                except ContourError as e:
                    feedback.pushWarning(str(e))
                    if self.OUTPUT_DXF not in results:
                        results[self.OUTPUT_DXF] = export_contours_dxf(
                            doc, dxf_path or os.path.splitext(dwg_path)[0] + ".dxf"
                        )

        return results
