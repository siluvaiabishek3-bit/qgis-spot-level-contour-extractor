"""
Spot Level & Contour Extractor Tool - QGIS plugin entry point.

QGIS calls classFactory(iface) to instantiate the plugin when it loads.
"""


def classFactory(iface):
    from .plugin import SpotLevelContourPlugin
    return SpotLevelContourPlugin(iface)
