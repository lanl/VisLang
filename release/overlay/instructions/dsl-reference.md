# DSL reference

The exact meaning of every form. For how to work with a scientist on a spec,
read `authoring-specs.md`.

A spec is a Python-syntax file, `spec.py`, run with `run_pipeline("spec.py")`.
The forms below are already in scope. Do not import them, or anything else.
Each form takes a node and returns a node, so forms chain. Calling a form reads
nothing. Only a sink (`save` or `render`) runs the chain. A spec with no sink
is a dry run: you get the plan and its predicted cost, and nothing is read.

Before any bulk read, every chain is checked against the source's schema: field
names, axis names, bounds, timestep coverage. Errors at this stage cost
milliseconds. Fix the spec instead of working around the check.

## source(uri, positions=None)

The dataset at the head of a chain.

- `uri` is one of:
  - a local file, as an absolute path;
  - a folder of timesteps (see below);
  - a remote file or folder, as `ssh://[user@]host/path` or `user@host:/path`
    (read `remote.md` first).
- Glob patterns (`*`, `?`, `[`) are rejected. Point at one file or one folder.
- `positions=("x", "y", "z")` names the coordinate variables of point data. Use
  it only when `inspect` could not detect them.

**A folder is a time series.** It holds one file per timestep, named `…#N`,
where N is the step (`run#8.hdf5`). The rest of the chain runs on every
selected step, and `save` writes one output per step. With no `fields`, every
variable of the first step is used.

## fields(node, keep)

Keep only the named variables (one name or a list). Unknown names fail the
check.

## region(node, ...)

Select part of the domain. Give exactly one of three forms.

**Fixed box:** `region(node, x=(lo, hi), y=(lo, hi), ...)`
- On a **grid**, bounds are integer cell indices, `[lo:hi)` per axis, and axes
  `x`, `y`, `z` are axes 0, 1, 2. `None` leaves that end open. Physical
  coordinates on grids are not supported.
- On **point data**, bounds are world coordinates on the position variables.
- `lo < hi` is required.

**Moving box over a series:** `region(series, center={N: (x, y[, z]), ...}, size=s)`
or `region(series, track="track.csv", size=s)`
- The box is centre ± size/2, in cell indices on grids and world coordinates
  on points.
- `size` is one number, or one per coordinate. `None` keeps that axis whole,
  as does an axis the centre leaves out.
- A track file has the header `step,x,y` or `step,x,y,z`, with one row per
  timestep. `#` comments and blank lines are allowed. A relative path is
  resolved against the server's working directory, so prefer absolute paths.
- Every selected timestep needs a centre. Use `timesteps(...)` to drop the
  others. A centre for a step that is not in the folder is an error.
- A grid box that crosses the edge is clipped, and the report says so. A box
  with nothing left is an error. Point boxes are not clipped, and periodic
  wrap-around is not handled.
- A single-file source rejects this form. Use a fixed box.

**Named boxes:** `region(node, center={tag: ...}, size=...)`
- On one file: `{tag: (x, y[, z])}`. Over a series: `{tag: {N: (x, y[, z])}}`,
  where each tag lists only the steps at which its box exists. Gaps are
  allowed.
- `size` is one value, or `{tag: size}` covering every tag.
- A track file names boxes with a leading `tag` column: `tag,step,x,y[,z]`, or
  `tag,x,y[,z]` for one file.
- A tag contains only letters, digits, `_`, `-` and `.`, and does not start with
  `.`. Two tags must differ by more than letter case. Do not mix timestep keys
  and tag keys.
- Sieve cannot check that `v1` at step 10 is the same object as `v1` at step 11.
  Whoever wrote the centres vouches for that.
- Output: one result per tag, as `<dir>/<tag>/timestep#N.<ext>` for a series or
  `<dir>/<tag>.<ext>` for one file, all in a single format.
- Only one named-box region per chain. `render` rejects named boxes.

## subsample(node, factor) | subsample(node, x=..., y=..., z=...)

Reduce resolution. An `int` keeps every n-th element (n ≥ 1). A `float` in
(0, 1] keeps that fraction. On point data, a fraction draws rows at random,
with a seed recorded in provenance. Per-axis factors apply to grids. Use one
factor on point data.

## threshold(node, "var op value")

Keep the elements where the condition holds. The operators are `< <= > >= ==
!=`, and the right-hand side is a number.
- On point data, the failing rows are dropped.
- On grids, the failing voxels become NaN and the grid keeps its shape.
  Integer grids are cast to float32 to hold the NaN.
- The variable does not have to be in `fields`. It is read for the test and
  then dropped.
- Several thresholds combine with AND.

**Order is meaning.** `threshold` then `subsample` samples the survivors. The
reverse order applies the threshold to a sample. Sieve keeps the written
order. It still merges crops, strides and projections into one read wherever
that doesn't change the answer.

## timesteps(node, start, stop)

Keep the steps of a series whose `#N` lies in `[start, stop]`, inclusive. Use
it on a folder source only.

## compress(node, variables, error_bound, mode="auto")

Error-bounded lossy compression of the named variables, using SPERR for floats
and Zstd for integers.
- **HDF5 sources only.** Any other source is an error.
- **Must come last**, directly before the sink. Narrowing after `compress` is
  an error.
- `mode` is one of `"auto"`, `"absolute"`, `"relative"` or `"precision"`. In
  precision mode, `error_bound` is 16 or 32, a cast to that float width.
- Compression reduces storage only. It does not make a render cheaper.

## save(node, path)  — sink

Write the result. The extension of `path` chooses the format:

| Extension | Writes |
|---|---|
| `.h5` `.hdf5` `.hdf` | HDF5 |
| `.nc` `.nc4` | netCDF-4 |
| `.vti` | VTK uniform grid (origin and spacing) |
| `.vts` | VTK structured grid (a position for every point) |
| `.vtp` | VTK point set |
| `.vtk` `.vtkhdf` | VTK, any of the above |
| `.npz` | NumPy |
| `.gio` | GenericIO |

With no recognized extension, the source's format is kept. Formats that have no
writer (FITS, yt, raw, formats read through generated readers) fall back to
`.npz` with a note. An extension is therefore how you convert, for example
HDF5 to `.vti` for ParaView.

- **Conversion never resamples.** A grid cannot become a point set, or the
  reverse.
- **Geometry follows the cut.** A cropped or strided grid lands at the right
  place in world space. A grid with per-point positions (curvilinear) can be
  saved as `.vts` or `.npz` but not `.vti`. HDF5 and netCDF keep the values but
  not the positions, and the provenance record says so.
- **`.vtu` is not supported** in either direction.
- **GenericIO** needs equal-length 1-D columns and the box geometry from the
  source header. If those are missing, a path with no extension falls back to
  `.npz`, while an explicit `.gio` path is an error.
- **A series** writes `<dir>/timestep#N.<ext>`, which is itself a valid series
  folder. `save(series, "roi.vti")` writes `roi/timestep#0.vti`, and so on. A
  `.vti`, `.vts` or `.vtp` series also gets `<dir>/<dir>.pvd`; open that file
  in ParaView.
- Every output carries a provenance record (`provenance.md`).

## render(node, cmap=None, opacity=None)  — sink

Show the result in the browser viewer and print its URL. 3-D fields render as
volumes, and point data as a point cloud with a density volume.
- `cmap` is `"green"`, any matplotlib colormap name, or `None`.
- `opacity` is a flat k3d transfer function `[t0, a0, t1, a1, ...]`.
- Only one dataset per render. A series and named boxes are both rejected:
  select one timestep, or `save` them instead.
- Read `rendering.md` before rendering anything large.

## Example

```python
src = source("/abs/path/simulation.hdf5")

hot = fields(src, ["temperature", "density"])
hot = region(hot, x=(100, 400), y=(100, 400))
hot = threshold(hot, "temperature > 500")   # sample the hot cells, not the box
hot = subsample(hot, 2)

save(hot, "/abs/path/out/hot_core.vti")
```
