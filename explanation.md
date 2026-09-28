# Running the remote path, and how provenance comes together

Walkthrough against the Nyx dataset on gpu-server, plus what each piece is doing.
The interpreter internals (narrowing, lowering, fusion) are treated as known and
skipped; AST serialization gets more room, since it turns up inside the record.

`spec.py` still holds your MiraTitanU spec. Everything below was run against the
Nyx file with this spec in its place — paste it in when you want to reproduce,
and `git checkout spec.py` to get MiraTitanU back:

```python
# Nyx Lyman-alpha forest, 512^3, 13 fields (~6.98 GB).
# Two fields of thirteen, a 128^3 box, strided 2 -> ~2 MB on the wire.
NYX = ("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/"
       "nyx/NVB_C009_l10n512_S12345T692_z42.hdf5")

src = source(NYX)
box = region(
    fields(src, ["native_fields/baryon_density", "native_fields/temperature"]),
    x=(128, 256), y=(128, 256), z=(128, 256),
)
save(subsample(box, 2), ".vislang/nyx_roi.nc")
```

---

## 1. Run it

### Open a session

```bash
sieve connect gpu-server
```

A password dialog opens on your screen; ssh reads it directly. Nothing about it
reaches Sieve or the transcript. The session then serves every later `inspect`,
`estimate` and `execute` until it expires.

Without this, any remote command returns `NEEDS_SESSION` and reads nothing.

### Look at the source

```bash
sieve inspect "ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/nyx/NVB_C009_l10n512_S12345T692_z42.hdf5"
```

Metadata only — no bulk read. You get a 512³ grid and 13 variables, seven
`derived_fields/…` and six `native_fields/…`, each roughly 512 MB.

Note that pointing at the *directory* instead returns an error: a folder source
is a timeseries in Sieve's model and its files must be named `…#N`. This dataset
is one file, so address the file.

### Cost it before committing

```bash
sieve estimate spec.py
```

Nothing is materialized. The line worth reading is the fuse:

```
fuse -> grid_ranges=[(128, 256, 2), (128, 256, 2), (128, 256, 2)],
        project=['native_fields/baryon_density', 'native_fields/temperature']
Cost estimate (remote):
  over the wire: ~2 MB
```

Two fields of thirteen and a strided 128³ box, folded into a single strided read.
HDF5 supports hyperslab reads, so here `region` genuinely reduces what is read
off disk — unlike the GenericIO path, where a bbox can only be applied after the
columns are read.

### Execute

```bash
sieve execute spec.py
```

Measured on this data: **6.98 GB source → 2.1 MB over the wire, 49 s end to
end.** The tail of the output is the part this document is about:

```
[save] wrote 2 array(s) as netcdf4 -> .vislang/nyx_roi.nc; provenance in-file
```

### Read the record back

```bash
sieve provenance .vislang/nyx_roi.nc
```

```
nyx_roi.nc — 64×64×64, 2 variable(s) from NVB_C009_l10n512_S12345T692_z42.hdf5
  written  2026-09-24T00:37:54 by sieve 0.1.0 (a770d4e)
  format   netcdf4, via netcdf4-attrs

  source   ssh://gpu-server//mnt/.../NVB_C009_l10n512_S12345T692_z42.hdf5
           UNVERIFIABLE  remote source; not probed

  did      source(…) → fields[native_fields/baryon_density,native_fields/temperature]
           → region{x:(128,256),y:(128,256),z:(128,256)} → subsample(2)
           → save(.vislang/nyx_roi.nc)

  variables
    native_fields/baryon_density 64×64×64  [remote_reduce]
    native_fields/temperature    64×64×64  [remote_reduce]

  spec     11 lines embedded (sha 9d44f3d6d73d) — see --spec
```

Other things to try:

```bash
sieve provenance .vislang/nyx_roi.nc --json    # the whole record
sieve provenance .vislang/nyx_roi.nc --spec    # just the spec that made it
sieve rerun .vislang/nyx_roi.nc --out .vislang/again.nc
ncdump -h .vislang/nyx_roi.nc                  # the record as netCDF sees it
```

---

## 2. What happens where

The remote route for a single file, with the provenance hooks marked:

```
sieve execute spec.py
  │
  ├─ cli_core.do_execute reads spec.py
  │     opens timing.run(...)  and  provenance.run(...)          ← RUN SCOPE
  │     runs the spec text in the sandbox; sinks register themselves
  │
  ├─ planner.plan_pipeline(terminal)  — once per sink
  │     opens provenance.pipeline(terminal)                      ← PIPELINE SCOPE
  │       serializes the AST once, and takes the source URI from it
  │     sees an ssh:// URI → remote reduce route
  │
  ├─ remote/reduce.py
  │     probes the source: stat + md5 of the first 64 KiB, one ssh round trip
  │       provenance.note_source(identity=…, site="remote")      ← IDENTITY
  │     ships the narrowing prefix as validated JSON — never code
  │     the cluster runs its own plan_pipeline, writes a temp .npz
  │     pulls the .npz, loads the arrays, deletes both copies
  │
  └─ output/save.py  save_loaded(loaded, path)
        resolves format: ".nc" → netcdf4
        provenance.record(...) builds the finished record          ← ASSEMBLE
        _write_netcdf4 embeds it as netCDF attributes              ← EMBED
        nothing left over → no companion file needed
```

Two design points that fall out of this shape.

**The sink pulls the record; nothing is threaded down.** `save_loaded` asks for
it rather than receiving it as an argument. That covers every execution route
without adding parameters through five planner frames, and it means the recorded
output path is the one actually written — after extension resolution, and after
any format degradation.

**Silence is the default.** `provenance.record()` returns `None` when no run
scope is open. That single property does two jobs. The cluster-side reducer calls
`plan_pipeline` too, and writes a temp `.npz` that gets pulled and deleted — with
no run open there, it never stamps a record onto a throwaway file. And the
existing test suite drives the planner directly, so it keeps passing untouched.
`VISLANG_PROVENANCE=0` disables the whole thing.

---

## 3. Where each field comes from

The record is assembled at the sink, but the facts arrive throughout the run.

| Field | Collected |
|---|---|
| `producer` | at write time — version via `importlib.metadata`, git commit, and the versions of the libraries that touched this data |
| `run` | when the run scope opens — spec path, spec hash, spec text |
| `source.uri` | from the serialized AST (see below) |
| `source.identity` | the stat + head hash the remote probe already computes for the cache |
| `transform.plan` | serialized once when the pipeline scope opens |
| `transform.lowered` | from the fused Narrowing, when there is a local one |
| `variables` | at write time, from the materialized arrays |
| `output` | at write time, after format resolution |

Two of those deserve a note.

**`source.uri` comes from the AST, not from the file that was opened.** On the
fallback route where Sieve downloads a whole remote file first, the path it
physically reads is a local cache copy — so recording "what we opened" would name
`.vislang/downloads/…` instead of the `ssh://` address you wrote. The terminal
AST node keeps the authored URI on every route, so that is the source of truth.
What was physically read is recorded separately as `source.read_from`.

**`commit_dirty` ignores `spec.py`.** You edit the spec in place on every run and
it lives in the repo, so a repo-wide dirty check would be permanently true and
carry no information. The check is scoped to the code that actually runs — the
`vislang/` package and the launcher scripts. The spec's own state is already
captured exactly, by its hash and its embedded text.

---

## 4. AST serialization, and why the record holds three views

This is the part that overlaps with provenance, so it is worth being precise.

### The wire format

`ast_serialize.to_plan(terminal)` walks back from the sink through `upstream`
links and emits a flat, versioned dict — one entry per form, in written order:

```json
{"vislang_plan": 1,
 "chain": [
   {"kind": "source",    "uri": "ssh://gpu-server//mnt/.../NVB_C009_….hdf5",
                         "positions": null},
   {"kind": "fields",    "keep": ["native_fields/baryon_density",
                                  "native_fields/temperature"]},
   {"kind": "region",    "ranges": [["x",128,256],["y",128,256],["z",128,256]]},
   {"kind": "subsample", "uniform": 2, "per_axis": []},
   {"kind": "save",      "path": ".vislang/nyx_roi.nc"}
 ]}
```

Flat rather than nested because the chain is linear, and a list is trivially
validated element by element.

### It was already load-bearing before provenance

This is the format the remote reducer speaks. When Sieve ships work to the
cluster it sends *this*, not code — `reduce.py` rebuilds the prefix of the chain
with the source URI rewritten to the cluster-local path, serializes it, and the
remote side validates and rebuilds it. That is why the DSL can be driven by an
agent without the cluster ever executing agent-authored Python.

The validator treats the wire as hostile: allowlisted `kind` values, exact
per-kind key sets (missing *or* extra keys are rejected), types checked with
`bool` excluded where a number is expected, `source` required at index 0 and
forbidden anywhere else. Validation completes before any node is constructed, so
a rejected plan leaves no partial state behind.

Round-tripping is guaranteed by construction: `to_plan` runs the same validator
on its own output before returning, so anything it emits can be rebuilt by
`from_plan`.

### Why the record keeps plan, spec, and lowered

Three representations of the same run, and they answer different questions.

**`transform.spec`** is the text you wrote, comments included. This is what a
human wants six months later, and it is what `--spec` prints.

**`transform.plan`** is the serialized AST above — what you *asked for*, in a
form a machine can validate and re-execute. This is what `rerun` runs. The spec
text cannot serve that purpose: re-running it would mean executing recovered
source, whereas the plan goes through the same validator any shipped plan does.

**`transform.lowered`** is what the interpreter *did* — which cuts it folded into
the read, which it applied afterwards, with the post-op predicates serialized as
data rather than as class names. The gap between `plan` and `lowered` is the
fusion behaviour, recorded per artifact.

### The sink-registration trap

Constructing a `SaveNode` registers it in a module-level sink registry — that is
how the sandbox collects sinks after running your spec, since the spec never
returns anything. Rebuilding a plan therefore has a side effect: it registers a
sink.

That is fine for the one-shot cluster reducer, but `rerun` may be running inside
a long-lived MCP process, where a stray registration would be picked up by the
*next* `run_pipeline` and executed as if you had asked for it. So `rerun` brackets
the rebuild with `reset_sinks()` on both sides, and a test asserts the registry is
empty afterwards.

---

## 5. Where the record ends up

Inside the file where the container has somewhere to put it, beside it where it
does not. Identical content either way, and `sieve provenance` resolves all of
them, so you never have to know which applied.

| Output | Mechanism |
|---|---|
| `.nc` | netCDF attributes, plus a conventional `history` entry |
| `.hdf5` | HDF5 attributes, on the root and per variable |
| `.vti` `.vtp` `.vtk` | VTK field data — ParaView shows it under Information |
| `.vtkhdf` | HDF5 attribute, written after VTK closes the file |
| `.npz` | a reserved `sieve_`-prefixed key |
| GenericIO | companion file — pygio exposes no metadata parameter |

Companion files are `.<name>.sieve-prov.json`, pretty-printed, beside the output.
The leading dot is functional: timeseries discovery matches `#N` anywhere in a
filename, so an undotted companion inside an output folder would be enumerated as
a duplicate timestep and then fail to load as data.

The Nyx run above wrote `.nc`, so the record went in as netCDF attributes —
hence `provenance in-file` rather than a companion path in the save message. You
can see it with `ncdump -h`, and `h5dump -A` works too, because a netCDF-4 file
is an HDF5 file with a convention layered on top.

---

## 6. Two honest gaps on this route

**`lowered` is `null` after a remote reduce.** The fused Narrowing is built on
the cluster, so no local one exists to record. Compare the local run, which
reports `read grid_ranges=[[128,256,2],…]`, against the Nyx record above, which
has no such line. The `plan` is complete either way — only the interpreter's
lowering decision is missing. Closing this means having the reducer return its
narrowing alongside the data.

**Remote sources report `UNVERIFIABLE`.** `sieve provenance` deliberately does not
open an ssh connection; it is meant to be cheap and to work offline. The identity
is recorded at write time and *is* checked by `sieve rerun`, which needs the
connection anyway. So the record can always tell you what the source looked like;
it just will not re-probe a remote one on demand.

---

## 7. Things worth trying

```bash
# Convert the same result and compare where the record lands.
#   save(..., ".vislang/nyx_roi.vti")   -> VTK field data, opens in ParaView
#   save(..., ".vislang/nyx_roi.npz")   -> a reserved key inside the archive

# Chain a derivation and watch the ancestry accumulate.
#   src = source(".vislang/nyx_roi.nc")
#   save(region(src, x=(0, 32)), ".vislang/nyx_small.nc")
sieve provenance .vislang/nyx_small.nc

# Re-run, then break the source deliberately and re-run again — it refuses,
# naming the field that changed, and writes nothing.
sieve rerun .vislang/nyx_roi.nc --out .vislang/again.nc
```

The chained case is the interesting one, and it is verified on this data:

```
  derived through 1 earlier step(s):
    <- ssh://gpu-server//mnt/.../NVB_C009_l10n512_S12345T692_z42.hdf5
```

A local 2 MB file, two narrowings removed, still names the 6.98 GB original on
the cluster. That one took a fix to work: netCDF stores the record as
fixed-length `NC_CHAR`, which h5py reads back as **bytes**, where its own
variable-length attributes come back as `str`. The inheritance path only decoded
`str`, so chaining through a `.nc` silently produced an empty ancestry while the
same chain through `.hdf5` worked. There is now a regression test asserting the
attribute really is bytes on that path, so the cause is pinned rather than just
the symptom.
