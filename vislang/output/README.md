# `output/` — the sinks

The only forms that cause execution.

| File | What it holds |
|---|---|
| `render.py` | headless k3d scene, served to the browser over a local HTTP port |
| `save.py` | write the result to disk — convert on request, else preserve the source's format |

`render` is headless by necessity — there is no GPU or X server on a compute
node — so the look is fixed in the spec (`cmap`, `opacity`) rather than adjusted
interactively. Keep the grid small enough that the browser stays responsive; the
lever for that is `subsample` or `region`.

`save` resolves the output format in this order: the path's extension if we can
write it (`.npz`, `.hdf5`, `.gio`, `.vti`, `.vts`, `.vtp`, `.vtk`, `.vtkhdf`), else the
source's original format, else npz with a note. **The extension is how a
conversion is requested** — including over a time series, where it names the
per-timestep format inside the output folder (`save(node, "roi.vti")` writes
`roi/timestep#N.vti`). Otherwise a series preserves the source format.

Named boxes (`region(center={tag: …})`) write one output per tag under the
sink's directory — `<dir>/<tag>/timestep#N.<ext>` over a series,
`<dir>/<tag>.<ext>` on one file — with one format decided for every tag
before anything is written. A `.vti`/`.vts`/`.vtp` series stores each file's `#N` as
`TimeValue` and gets a `<dir>/<dir>.pvd` index, so ParaView opens the whole
output as one time-aligned dataset with a block per tag (a box missing at a
timestep is missing there, not frozen at its last frame).

A grid keeps its place in space: `.vti` writes origin + spacing, `.vts` every
point's position (a curvilinear grid — such a result is refused as `.vti`,
which would misplace it), `.npz` either form under reserved `sieve_*` keys.
Formats with no slot for listed points (HDF5, netCDF) say so in the record.

Two rules the writers keep. **Writers are Tier-0 only**: you can convert out of
an LLM-read format but never into one (`instructions/soundness.md`).
**Conversion never resamples**: a grid cannot be saved as a point set or the
reverse — that is a computation, not a format change, so it is a blocker.

A result that the requested format cannot hold degrades to npz with a printed
reason, unless the extension was explicit, in which case it raises: an extension
is a request, not a default.

Reference: [`instructions/rendering.md`](../../instructions/rendering.md)
