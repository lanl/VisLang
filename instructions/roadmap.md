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
- **Rectilinear / curvilinear output** (`.vtr` / `.vts`): needs per-axis or
  explicit point coordinates, which `geometry` does not yet carry. Deliberately
  not in `_EXT_FORMAT` rather than accepted-then-failed.
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
