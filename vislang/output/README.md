# `output/` — the sinks

The only forms that cause execution.

| File | What it holds |
|---|---|
| `render.py` | headless k3d scene, served to the browser over a local HTTP port |
| `save.py` | write the result to disk, preserving the source's format |

`render` is headless by necessity — there is no GPU or X server on a compute
node — so the look is fixed in the spec (`cmap`, `opacity`) rather than adjusted
interactively. Keep the grid small enough that the browser stays responsive; the
lever for that is `subsample` or `region`.

`save` resolves the output format in this order: the path's extension if we can
write it (`.npz`, `.hdf5`, `.gio`), else the source's original format, else npz
with a note. Over a time series it writes one file per timestep.

Reference: [`instructions/rendering.md`](../../instructions/rendering.md)
