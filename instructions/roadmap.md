# Roadmap

Where VisLang is heading, so today's choices stay consistent with it.

## Query DSL with predicate pushdown — now realized
The declarative DSL this roadmap used to anticipate is **built**. Specs are now
written from *forms* (`source/fields/region/subsample/threshold/compress/
save/render`) that build an **AST**; an interpreter (`planner.py`) inspects the
source, static-checks the request against the schema *before any read*, classifies
every form on two axes (structural-vs-computed for pushdown; absolute-vs-relative
for reordering — `subsample` is the lone order-sensitive form), **fuses** the
structural narrowing into one selection (`narrowing.py`), and applies the computed
cuts post-read in written order — pushing crop+stride into HDF5/FITS hyperslabs
where the library allows. `load` left the spec grammar to become an executor step
(`materialize`); `subset`/`load`/`download`/`establish_connection` are hidden
physical ops.

The static check *is* the "verify the query against the schema before any read"
idea — typed against `DatasetInfo`, raising on a missing axis/variable or an
out-of-bounds range with no bulk read. See `vislang://instructions/soundness`.

## Format conversion and VTK — now realized
`save()` converts as well as preserves: an extension on the sink path is the
conversion request (`save(box, "roi.vti")`), including over a timeseries, where
it names the per-timestep format inside the output folder. VTK is a Tier-0
format on both ends — grids (ImageData/Rectilinear/Structured) and point sets
(PolyData); unstructured meshes are refused, since narrowing one needs
connectivity-aware `region`/`subsample`.

This needed **no interpreter change**: the narrowing layer was already
format-blind and branches on modality, not filetype. What it did need was
`DatasetInfo.geometry` (origin + spacing, optional, never synthesized) and for
`materialize` to shift it by the narrowing it applied, so a cropped block reports
where in the source it came from and lands in the right place in world space.

Two rules to keep: writers are **Tier-0 only** (convert out of an LLM-read
format, never into one — `vislang://instructions/soundness`), and conversion
never **resamples** (grid↔points is a computation, not a format change).

## Remaining work (staged)
Forms that parse and static-check but aren't materialized yet (they raise a clear
message), and known optimizations:
- **World-space `region`** on grids (physical coords via origin/spacing/extent) —
  index-space works today. `DatasetInfo.geometry` now carries the origin/spacing
  this needs, so it is a converter in front of `AxisRange`, not a change to the
  physical layer. Keep `AxisRange` interpretation-agnostic.
- **VTK extent pushdown**: the XML readers support `UpdateExtent`, so a region
  could push into the read instead of read-full-then-slice. Until then
  `VTKAdapter.supports_strided_read` stays False and `VTK` stays out of
  `estimate._READ_REDUCIBLE`, so the cost estimate keeps telling the truth.
- **Rectilinear output** (`.vtr`): a rectilinear source is read and written as
  a structured grid (`.vts`, every point listed — correct, but larger than
  per-axis coordinates). `.vtr` itself stays refused until geometry carries
  per-axis arrays.
- **Cost estimate for listed points**: the remote estimate prices the data
  variables; a curvilinear grid's positions (3 numbers per point, fetched once
  per box and then cached) are not counted yet.
- **Remote conversion as a costed choice**: conversion runs at the local sink
  today, because a `save()` is often assembled from cached extents plus a fresh
  fetch and a remotely-converted file cannot join that assembly. It can still be
  the better plan when there is no cache reuse and no local suffix work — the
  wire container is uncompressed npz, so a compressed VTK artifact is smaller.
  Make it a planner decision with a cost-model gate, and measure both paths.
- **yt cropped covering-grid**: build the covering grid over the cropped edges so a
  region pushes into yt instead of read-full-then-crop.
- **Full-content source hashing** is unbuilt; the input fingerprint is size +
  mtime + a sha256 of the first 64 KiB. The gap it leaves — a same-second,
  same-size rewrite with an unchanged head — is stated in
  `instructions/provenance.md`.

## Per-timestep regions — v1 realized
`region(series, center=[(N, x, y, z[, size]), …] | "f.csv", size=S)` cuts each timestep with
its own box (centre ± size/2, index space on grids, world space on points),
checked for coverage before any read and recorded per timestep in provenance.
Follow-ups:
- **Non-`#N` timeseries naming**: the wildfire run (`output.N.vts`) and HACC
  (per-step directories, where `#N` is a rank partition) are not `#N` folders;
  today they need a symlink folder. A naming pattern on `source()` would remove
  that step (local listing and the remote `find` both need it).
- **Periodic wrap-around** for cosmology boxes (a halo across the box edge needs
  a box in two pieces, which `BBox` cannot express).
- **Superset reuse for moving boxes**: the catalog keys each timestep on its
  centre and reuses only exact matches; a grown box could slice a cached one.

## Named boxes — v1 realized
`region(node, center={tag: [(N, …), …] | (x, y, z)} | "f.csv" with a tag
column)` cuts several named boxes per file — a different number at each
timestep — and writes one output per tag (`<dir>/<tag>/…`), with a ParaView
`.pvd` index and `TimeValue` so the boxes open together, time-aligned, gaps
shown. Every route runs them: local, a remote file, and the remote catalog
folder route (unit = (timestep, tag)) — which is also the route with no
`fields()`, requesting every variable of the series.
Follow-ups:
- **Tracking itself**: tags are identity the author (or an external tracker)
  vouches for. Deriving the centres in VisLang — feature detection per
  timestep plus correspondence across steps, with splits and merges — would be
  a new form with its own record of how matches were made.
- **Render named boxes together** in the browser viewer (today: one dataset
  per render; ParaView via the `.pvd` instead).
- **Superset reuse** across boxes: two overlapping boxes are two extents.
- **Box positions without source geometry**: a grid with no origin/spacing
  writes each box at the index origin, so boxes overlap in ParaView. Writing
  the crop's cell offset was considered and declined — it would put an origin
  in the file the source never gave.

## Provenance — now realized (`sieve-provenance/2`)
Every output carries a YAML record split into `logical` (the spec, the input's
fingerprint, choices resolved at run time, the output's `data_sha256`, the
Sieve build, and an explanation rendered from per-form templates) and
`realization` (when, by whom, how each column arrived). Embedded in HDF5/netCDF
attributes, VTK field data or a reserved npz key; a dot-prefixed YAML companion
where the container has no safe slot. A derived output links its parent by
`data_sha256`. Fractional `subsample` is seeded once per run and the seed
recorded. → `instructions/provenance.md`

Still open:
- `columns_from` reports one group per site. The mixed cache-then-stride
  groups sketched in `prov-ex/vts56.gio.sieve-prov.yaml` (`cached`, `then`,
  `yields`) need the catalog to report what it reused per column.
- The remote extent catalog does not key on the sampling seed, so a cached
  random subsample can disagree with the seed its record names.
- The no-`fields()` remote folder batch pulls a directory the remote wrote
  under a detached run, so that output carries no record.
- Specs with loops: record an *elaborated*, loop-free spec per output.

## Rendering
- Optionally restore live, camera-preserving updates on top of the k3d snapshot.
  (The particle/point path is already headless k3d — `render_points`.)

## Guiding constraints (unchanged)
Keep the soundness gate (`vislang://instructions/soundness`) in front of every new
LLM use, and keep `DatasetInfo` as the format boundary so new formats and new
query features compose without touching each other.
