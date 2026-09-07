"""
plugin.py
-------------------
Main plugin class. Registers the Processing provider (Spot Levels and
Contours algorithms) on load, and checks once that the third-party
packages the core logic needs (rasterio, geopandas, contourpy, ezdxf,
pyogrio) are importable in QGIS's own Python environment - these are not
bundled with QGIS by default, so a clear one-time message points the user
at the install command instead of a confusing crash the first time they
run an algorithm.
"""

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QMessageBox

REQUIRED_PACKAGES = (
    "rasterio", "geopandas", "pyproj", "shapely", "scipy", "contourpy", "ezdxf", "pyogrio",
)


def _missing_packages():
    missing = []
    for name in REQUIRED_PACKAGES:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    return missing


class SpotLevelContourPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        # Check required third-party packages BEFORE importing the provider.
        # The provider imports the algorithm modules, which import the core
        # extraction logic, which imports rasterio/geopandas/etc. at module
        # level - so on a fresh install (no deps yet) that import happens
        # first and QGIS would show a raw traceback instead of this friendly
        # message, and would also auto-disable the plugin on the crash.
        missing = _missing_packages()
        if missing:
            self._warn_missing_packages(missing)
            return

        from .provider import SpotLevelContourProvider
        self.provider = SpotLevelContourProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None

    def _warn_missing_packages(self, missing):
        import sys
        import os

        py_exe = sys.executable
        cmd = f'"{py_exe}" -m pip install {" ".join(missing)}'
        msg = (
            "Spot Level & Contour Extractor Tool loaded, but the following Python "
            f"packages are missing from QGIS's Python environment: {', '.join(missing)}.\n\n"
            "The algorithms will fail until these are installed. Open the OSGeo4W Shell "
            "(Windows) or a terminal with QGIS's own Python on your PATH and run:\n\n"
            f"{cmd}\n\n"
            "Then restart QGIS."
        )
        try:
            QMessageBox.warning(None, "Spot Level & Contour Extractor Tool - missing packages", msg)
        except Exception:
            # Headless / no GUI context (e.g. QGIS Server) - fall back to the log.
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(msg, "Spot Level & Contour Extractor Tool", Qgis.Warning)
