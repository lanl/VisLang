# VisLang

This repo contains VTK structured-grid outputs (`.vts`) from wildfire simulation runs.

## Quick Start: Visualize `.vts` in VTK (Python)

1. Install VTK:

```bash
pip install vtk
```

2. Preview arrays available in a file:

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts --list-arrays
```

3. Open an interactive render window:

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts
```

If you are on a headless node (no `DISPLAY`), the script will skip interactive rendering by default and print guidance.

4. Color by a scalar array:

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts --scalar <scalar_name>
```

5. Overlay vector glyphs:

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts --vector <vector_name> --glyph
```

6. Save a PNG screenshot:

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts --scalar <scalar_name> --screenshot view.png
```

Headless/offscreen render (no GUI):

```bash
python scripts/visualize_vts.py backcurve40_output.25000.vts --scalar <scalar_name> --screenshot view.png --offscreen
```

If this fails with OpenGL/EGL errors, your node is missing software rendering backends.
You need either `OSMesa` or Mesa `swrast_dri.so` available on the system.

## Data Files

- `backcurve40_output.25000.vts`
- `headcurve40_output.30000.vts`

## Next Pipeline Step

After identifying useful fields visually, extract sampled/sliced data into tabular JSON and feed that into the Vega-Lite workflow.

