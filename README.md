# Spot Level & Contour Extractor Tool — QGIS Plugin

Adds two algorithms to the QGIS **Processing Toolbox**, under the
"Spot Level & Contour Extractor" group:

- **Extract Spot Levels** — samples elevation at a regular grid interval
  (any spacing you set, in metres) across a DSM/DTM raster and writes the
  points as CSV, Shapefile, and/or KML.
- **Generate Contours** — generates elevation contour lines at a chosen
  interval and writes them as Shapefile, DXF, DWG, and/or KML.

Both accept a raster in **any coordinate system**. If it's already a
projected CRS in metres (a UTM zone), it's used as-is. If it's a
geographic CRS (plain latitude/longitude degrees), the algorithm
automatically reprojects it in memory to the correct UTM zone before
running — so a "2 m grid" or "1 m interval" is always a true metre on
the ground, with no manual reprojection step.

## 1. Install the plugin (local testing, before publishing)

1. Find your QGIS profile's plugin folder:
   - Windows, QGIS 4.x: `C:\Users\<you>\AppData\Roaming\QGIS\QGIS4\profiles\default\python\plugins`
   - Windows, QGIS 3.x: `C:\Users\<you>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
   - (In QGIS: *Settings → User Profiles → Open Active Profile Folder*, then go into `python\plugins`.)
2. Copy this whole `spot_level_contour_extractor` folder into that
   `python\plugins` folder.
3. Restart QGIS, then open *Plugins → Manage and Install Plugins →
   Settings*, and tick **"Show also experimental plugins"** (this plugin
   ships as experimental until it's reviewed by the QGIS team). Then go to
   *Installed* and tick **Spot Level & Contour Extractor Tool** to enable it.
   - If the plugin fails to load once (e.g. a required package is still
     missing - see step 2 below), QGIS auto-disables it so it doesn't keep
     crashing on every restart. That disabled state does **not** clear
     itself once you fix the problem - after installing the packages below,
     come back to *Installed* and re-tick the checkbox.
4. Open the **Processing Toolbox** (*Processing → Toolbox*, or `Ctrl+Alt+T`)
   and expand **Spot Level & Contour Extractor** — the two algorithms
   appear there, and can also be used from the Python console, the
   Graphical Modeler, and Batch Processing.

## 2. Install the required Python packages (one-time)

QGIS does not bundle `rasterio`, `geopandas`, `scipy`, `contourpy`,
`ezdxf`, or `pyogrio` by default, so these need to be installed once into
QGIS's own Python environment (not your regular Python install — QGIS on
Windows ships its own, under its `Program Files` folder).

**Windows — use QGIS's own `python-qgis.bat` launcher, as Administrator.**
A plain `python3.exe` from QGIS's `bin` folder, or a plain (non-admin)
`pip install`, will not work reliably:
- `Program Files` isn't writable by a normal (non-admin) `pip install` — it
  silently succeeds but installs the packages into the wrong place (your
  personal user site-packages), which QGIS's own Python never looks at.
- QGIS's bare `python3.exe` needs environment variables that only
  `python-qgis.bat` sets up first; running it directly errors out.

Steps:
1. Search the Start Menu for **Command Prompt**, right-click it, and choose
   **Run as administrator**.
2. Run (adjust the QGIS version folder name if yours differs):
   ```
   "C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" -m pip install rasterio geopandas scipy contourpy ezdxf pyogrio
   ```
   If pip reports a package as "already satisfied" but the plugin still
   complains it's missing, force it to reinstall into the correct location:
   ```
   "C:\Program Files\QGIS 4.2.2\bin\python-qgis.bat" -m pip install --force-reinstall rasterio geopandas scipy contourpy ezdxf pyogrio
   ```
3. Restart QGIS, then re-enable the plugin if needed (see the note in
   step 1 above about QGIS auto-disabling a plugin after a failed load).

If a package is missing, the plugin shows a one-time popup naming exactly
which ones and the install command to run — it won't silently fail or
crash QGIS.

## 3. Publishing to the official QGIS Plugin Repository

Once you've tested it locally and are happy with it:

1. **Update `metadata.txt`** — set real `tracker`, `repository`, and
   `homepage` URLs (a GitHub repo works well for all three), bump
   `version` for each release, and set `experimental=False` once you're
   confident it's stable (new submissions are reviewed before that).
2. **License** — this plugin ships with a `LICENSE` file (GPL v3+),
   which is required: the official repository only accepts plugins under
   a GPL-compatible license, since QGIS itself is GPL-licensed.
3. **Zip it** — zip the `spot_level_contour_extractor` folder itself
   (so the zip's top-level entry is the folder, matching the plugin's
   internal name) — e.g. `spot_level_contour_extractor.zip`.
4. **Validate** — in QGIS, *Plugins → Manage and Install Plugins →
   Settings*, tick "Show also experimental plugins", then use
   *Install from ZIP* to sanity-check it installs cleanly from the zip
   you just built, exactly as another user would.
5. **Submit** — create an account at
   [plugins.qgis.org](https://plugins.qgis.org), then *Plugins → Share a
   plugin* and upload the zip with the metadata filled in. It appears
   immediately as "Experimental"; user feedback and a review from the
   QGIS plugin approval team can promote it to "stable/trusted" later.
6. **Maintain** — as new QGIS versions ship, re-test and update
   `qgisMinimumVersion`/`qgisMaximumVersion` in `metadata.txt` if needed.

## Files in this folder

- `metadata.txt` — plugin metadata QGIS reads (name, version, description, license fields, etc.) — **edit the tracker/repository/homepage URLs before publishing**.
- `__init__.py` — QGIS's plugin entry point (`classFactory`).
- `plugin.py` — main plugin class; registers the Processing provider and checks the required packages are installed.
- `provider.py` — registers the two algorithms under the Processing Toolbox group.
- `algorithms/spot_levels_algorithm.py` — the Extract Spot Levels algorithm.
- `algorithms/contours_algorithm.py` — the Generate Contours algorithm.
- `core/` — the extraction/export logic itself (`spot_level_core.py`, `contour_core.py`, `raster_utils.py`), reused from the standalone desktop version of this tool.
- `icon.png` — the plugin/algorithm icon.
- `LICENSE` — GPL v3, required for the official repository.
