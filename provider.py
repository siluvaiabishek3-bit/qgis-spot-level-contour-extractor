"""
provider.py
-------------------
Registers the plugin's two Processing algorithms (Spot Levels, Contours)
under a "Spot Level & Contour Extractor" group in the Processing Toolbox.
"""

import os

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithms.spot_levels_algorithm import SpotLevelsAlgorithm
from .algorithms.contours_algorithm import ContoursAlgorithm


class SpotLevelContourProvider(QgsProcessingProvider):

    def id(self):
        return "spotlevelcontour"

    def name(self):
        return "Spot Level & Contour Extractor"

    def icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.isfile(icon_path):
            return QIcon(icon_path)
        return super().icon()

    def loadAlgorithms(self):
        self.addAlgorithm(SpotLevelsAlgorithm())
        self.addAlgorithm(ContoursAlgorithm())
