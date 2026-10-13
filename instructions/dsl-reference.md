# DSL Reference

A spec is a plain Python file — always `spec.py`, edited in place — executed by
`run_pipeline("spec.py")`. The form names below are injected into the spec's
namespace — **do not import them**. Each form takes a node and returns a node, so
they chain. Calling a form runs nothing; it builds an AST node. Only a **sink**
(`render`, `save`) triggers execution.

How a run works: the interpreter walks the chain, inspects the `source`,
**static-checks** the request against the schema (raising before any bulk read),
**fuses** the narrowing forms into one read, then materializes and runs the sink.
A spec with no sink is dry-run (the inferred plan is reported; nothing is read).

## source(uri, positions=None) -> node
Starts a chain; names the dataset.
- `uri` — a local path to **one file** (globs are rejected); a remote
  `ssh://[user@]host/path` / `user@host:/path`; or a **folder** (a *timeseries* —
  see below).
- `positions` — `('x','y','z')` override naming the coordinate variables when
  auto-detection can't tell (point data with unusual names).

**A folder is a timeseries.** One file per timestep, named `…#N` where N is the
timestep number (e.g. `run#8.hdf5`). The interpreter maps the rest of the chain
over every timestep (same narrowing per file) and, for `save`, writes one file
per timestep. Select a range with `timesteps(...)`. `render` over a series is not
supported — pick a single timestep or use `save`.

## fields(node, keep) -> node
Keep only `keep` (a name or list of names); the rest are dropped. Validated
against the schema (unknown names raise).

## region(node, x=(a,b), y=(c,d), …) -> node
Crop to a per-axis range.
- **Grids:** index-space `[a:b]` per named axis (`x`,`y`,`z` → axes 0,1,2). `None`
  is an open end. Pushed into the read as a hyperslab where the format allows
  (HDF5/FITS); else read-then-crop.
- **Point data:** a **world-coordinate** bounding box on the coordinate variables
  (`info.positions`) — keep points whose coords fall in the box.

### A box that moves per timestep: region(node, center=..., size=...) | region(node, track=..., size=...)
Over a **folder (timeseries)**, give each timestep its own box by its centre:
```python
box = region(series, center={279: (524.99, 174.51, 781.43),
                             300: (526.10, 175.02, 780.88)}, size=30)
box = region(series, track="halo_track.csv", size=30)      # the same, from a file
```
- The box is **centre ± size/2**, in the coordinates `region` already uses:
  **cell indices on grids**, **world coordinates on point data**.
- `center` maps the `#N` timestep label to `(x, y)` or `(x, y, z)`. `size` is
  one number or one per coordinate; `None` keeps that axis whole, and an axis
  the centre leaves out is kept whole (a fire tracked on `(x, y)` keeps every
  vertical level).
- `track` is a CSV with header `step,x,y` or `step,x,y,z` (blank lines and `#`
  comments allowed), one row per timestep, path relative to the working
  directory. The planner reads it once, before any data read, inlines its rows
  into the plan (so a remote run never needs the file) and records its sha256
  in provenance.
- Give exactly one of axis ranges, `center=` or `track=`; `size` only goes with
  the latter two. A single-file source raises — use a fixed `region(x=…)`.
- **Checked before any bulk read:** every selected timestep needs a centre (drop
  timesteps with `timesteps(...)`; a track longer than the selection is fine),
  and a centre for a timestep that is not in the folder raises.
- A grid box crossing the edge is **clipped** to the grid and the clip is
  reported; a box with nothing left raises. Point boxes are not clipped, and
  periodic wrap-around (a halo straddling a cosmology box edge) is not handled.
- The dry run lists each timestep's box; every output's provenance records them
  under `resolved.region_boxes`. Over a remote folder the extent catalog keys
  each timestep on its own centre, so it reuses a cached box only on an exact
  match.

### Several named boxes: region(node, center={tag: …}, size=...)
Name each box with a **tag** (a string), the way you would assign it to a
variable, and every box is cut and written to its own output:
```python
# Over a timeseries: each tag lists the timesteps its box exists at.
boxes = region(series, size=20, center={
    "v1": {10: (50, 60, 70), 11: (52, 61, 70), 12: (54, 62, 71)},
    "v2": {10: (200, 40, 90), 12: (198, 42, 88)},          # gone at #11
})
save(boxes, "roi.vti")   # roi/v1/timestep#10…#12.vti, roi/v2/timestep#10,#12.vti, roi/roi.pvd

# On one file: one centre per tag; size may differ per tag.
save(region(snapshot, center={"core": (150, 150, 150), "jet": (40, 151, 150)},
            size={"core": 40, "jet": (10, 10, 60)}), "boxes.vti")   # boxes/core.vti, boxes/jet.vti
```
- The key type says what you mean: `{N: centre}` is one unnamed moving box,
  `{tag: …}` names boxes. Mixing the two raises, as does mixing per-timestep
  tags with single-centre tags.
- A tag names its box's output, so it is letters, digits, `_`, `-` or `.`, not
  starting with `.`; two tags must differ by more than case.
- `size` is one number, one per coordinate, or `{tag: size}` naming every tag.
- A track file names its boxes with a leading `tag` column: `tag,step,x,y[,z]`
  over a timeseries (rows sharing a step are separate boxes) or `tag,x,y[,z]`
  for one file. `size={tag: …}` works with it too.
- **The tag is the box's identity**, and the interpreter cannot check it: it
  does not know that `v1` at #11 is the same object as `v1` at #10. Whoever
  wrote the centres — you, or the tracker that produced the CSV — vouches for
  that; provenance records the track file's hash.
- **Gaps are allowed**: a tag lists only the timesteps its box exists at, and
  timesteps where no tag has a box are not read. A tag naming a timestep with no
  file raises; a tag with no box inside `timesteps(...)` is skipped with a note;
  a selection where no tag has a box raises. All of this — and every box against
  the grid — is checked before any read.
- **Output**: one per tag under the sink path — a timeseries folder
  `<dir>/<tag>/timestep#N.<ext>` per tag (each one readable with `source()`), or
  `<dir>/<tag>.<ext>` on one file. One format is chosen for every tag. Each
  output carries its own provenance record: `resolved.region_tag` and that tag's
  boxes only.
- **Reads**: each file is inspected once. Point data, and grid formats that read
  whole then slice, are read once per file with every box cut from that read;
  HDF5/FITS grids read one hyperslab per box.
- One named-box region per chain. `render` refuses named boxes: render a plain
  region for one box, or open the saved boxes in ParaView.
- **Remote**: every box runs in one remote job. The extent catalog keys each box
  by its centre and size, not its tag, so renaming a tag still reuses it.

## subsample(node, factor) | subsample(node, x=…, y=…, z=…) -> node
Reduce resolution. A factor is an **int stride** (keep every f-th) or a **float
fraction** in (0,1]. A single `factor` is uniform; per-axis `x=/y=/z=` is for
grids. On point data use a single factor (stride or fraction of rows).
`region` and `subsample` on the same grid compose into one strided crop.

**Order matters next to `threshold`/point-`region`:** `subsample` means "every
Nth of *what's left*", so on point data `threshold(...)` then `subsample(...)`
samples the survivors, while the reverse samples first and thresholds the
sample. The interpreter is faithful to the written order (a subsample written
after a threshold/bbox runs post-read instead of being pushed into the read).

## threshold(node, "var op value") -> node
Keep elements where the predicate holds, e.g. `"density > 0.5"`. Operators:
`< <= > >= == !=`; the right-hand side is a scalar. Works on both modalities:
- **Point/table data:** drops the failing rows (a row mask).
- **Grids:** NaN-masks the failing voxels — the cube keeps its shape (masked
  voxels render dark/transparent; ints are cast to float32 to hold NaN).
The predicate variable does not need to be in `fields(...)` — it is read for
the mask, then dropped. Multiple `threshold`s AND together; combine with
`region` freely.

## timesteps(node, start, stop) -> node
Time-axis selection for a **folder (timeseries)** source: keep the timesteps
whose `#N` label is within `[start, stop]`, **inclusive** (the integer in each
filename). Read once before the interpreter maps the rest of the chain over the
selected files — it is not a per-file narrowing. Using it on a single-file
source raises a clear error.

## compress(node, variables, error_bound, mode="auto") -> node
Error-bounded compression of the named variables (SPERR/Zstd, in-memory).
Storage only — it does **not** cheapen a render.

## save(node, path) -> (sink)
Write the materialized result to disk. The output format follows `path`'s
extension when it's a known one (`.npz`, `.hdf5`/`.h5`, `.gio`, `.vti`, `.vts`,
`.vtp`, `.vtk`, `.vtkhdf`, `.nc`); with no recognized extension it **preserves the
source's format** (HDF5, npz, GenericIO, and VTK today; other formats fall back to `.npz`
with a note until a writer exists).

**An extension is a conversion request.** `save(box, "roi.vti")` over an HDF5
source writes VTK — that is how you hand a narrowed result to ParaView. Two
limits: conversion never **resamples** (a grid cannot become a point set or the
reverse — that is a computation, not a format change, and it raises), and a
format read through a generated adapter can be converted *out of* but never
*into* (writers are trusted-library only).

**VTK targets.** `.vti` holds an evenly spaced grid (an origin and one spacing
per axis), `.vts` a grid with every point's own position, `.vtp` a point set,
and `.vtk`/`.vtkhdf` any of them. A cropped or strided grid records where it was
cut, so the output lands in the right place in world space and ROIs from
different timesteps line up. Unstructured meshes (`.vtu`) are not supported in
either direction — narrowing one needs connectivity-aware `region`/`subsample`.

**Curvilinear grids (`.vts` sources).** Some grids list each point's position
because no origin + spacing can describe them — a terrain-following grid has
regular x and y, but its z starts on the ground and so differs from column to
column. `region`/`subsample` cut those positions exactly as they cut the data,
and the result keeps them: with no extension it is written as `.vts`; asking
for `.vti` raises (it would put the data in the wrong place); `.npz` keeps the
positions beside the data (`sieve_points`). HDF5 and netCDF have no slot for
them — the values are written, and the record says the positions were not.

**`.npz` output** keeps a grid's place in space too, under reserved keys
(`sieve_origin` + `sieve_spacing`, or `sieve_points`), so a saved `.npz` can be
`source()`d and cut again and still land in the right place. The npz reader
sees a grid when every array shares one shape, particles when every array is
one column of the same length.

GenericIO output is a single
unpartitioned file with **no extension** (its snapshots are named by convention),
and it needs equal-length 1-D columns of a pygio-writable dtype plus the box
geometry from the source header — a result that can't satisfy that (a grid, an
unsupported dtype, a header with no `phys_scale`) degrades to `.npz` with a note
saying why, rather than inventing the missing metadata. An explicit `.gio` path
raises instead of degrading. A **folder (timeseries)** source writes one file per
timestep into the `path` directory, named `timestep#N.<ext>` — itself a valid
timeseries folder. A series converts the same way a single file does: an
extension on the path names the per-timestep format and the directory takes the
stem, so `save(series, "roi.vti")` writes `roi/timestep#0.vti`,
`roi/timestep#1.vti`, … The whole folder commits to one format before anything
is written, so a series is never a mix. Named boxes write one output per tag
under the directory (see `region`), all in one format.

**Opening a series in ParaView.** A `.vti`/`.vts`/`.vtp` series stores each file's
`#N` as `TimeValue` field data, and the folder gets an index, `<dir>/<dir>.pvd`
(a ParaView collection): open that one file. Each named box is its own block at
its own timesteps, so a box absent at a timestep is absent there — opened as
separate file series, ParaView would number each folder 0, 1, 2… and hold a
box's last file across a gap. Legacy `.vtk` and `.vtkhdf` get neither: ParaView
reads no `TimeValue` from legacy files, and its collection reader cannot list
either format.

**Every output carries a provenance record** — a YAML document with the spec,
the input's fingerprint, the output's `data_sha256`, and a plain-words
explanation of each step — embedded in the file where the format allows, in a
`.<name>.sieve-prov.yaml` beside it where it does not. Read it with the
`provenance` tool. See `vislang://instructions/provenance`.

## render(node, cmap=None, opacity=None) -> (sink)
Serve the headless k3d browser viewer; prints its URL. Renders everything the
node describes — narrow upstream to show less. `cmap` is `'green'`, a matplotlib
name, or `None`; `opacity` overrides the k3d transfer function. 3-D fields render
as volumes; point data as a cloud + density volume (coords from `info.positions`).
One dataset per render: named boxes (`region(center={tag: …})`) are refused —
`save` them and open the `.pvd` in ParaView. See `vislang://instructions/rendering`.

## MCP tools (called directly, not in the spec)
- `inspect(filepath, positions=None)` — the schema (variables, dimensions),
  metadata only. Use it to write the spec. Same engine as `source()`.
- `estimate_render_cost(filepath)` — predicted browser payload + disk cost.
- `run_pipeline(spec_path)` — execute the spec.

## Example spec
```python
# Crop a sub-box, stride it for the browser, color green.
render(subsample(region(source("/abs/heptane_302x302x302_uint8.raw"),
                        x=(0, 200), y=(0, 200)), 2),
       cmap="green")
```
Re-running an edited spec replaces the view in place. Discuss at the spec level —
change one line, re-run, look.

## Staged — parse and static-check but not yet materialized
These build valid nodes but raise a clear message at lowering until a later phase:
- **world-space** `region` on grids (physical coords via origin/spacing) — index-space today.
