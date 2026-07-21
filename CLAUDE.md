# VisLang

VisLang is a **declarative DSL** for reading, inspecting, narrowing, compressing,
and rendering scientific data. You (the model) collaborate with a human by
**writing and editing a small Python "spec"** built from DSL *forms*; the
`run_pipeline` tool executes it. The spec is the shared artifact — keep it small
and legible, in one file named `spec.py`, edited in place (never a new file per
request).

This file is the concise map. Read the pointed-to `instructions/*.md` (also
exposed as `vislang://instructions/*` resources) when you need depth.

## The model: forms describe a goal; the interpreter decides how

A form does **not** run anything — it builds an AST node. Only a **sink**
(`render` or `save`) triggers execution. On run, the interpreter (`planner.py`):

1. **inspects** the source for its schema,
2. **static-checks** the request against that schema *before any bulk read*
   (does the axis/variable exist? is the range in bounds?),
3. **fuses** the structural narrowing into one read (crop + stride + projection
   pushed down together; value cuts apply right after, in written order),
4. **materializes** and runs the sink.

Form order is the promise; how it's lowered is the interpreter's choice. A spec
with no sink is a **dry run** — you get the inferred plan, nothing is read.

## The forms (available in a spec with no imports)

`source(uri, positions=None)` · `fields(node, keep)` · `region(node, x=(a,b), …)`
· `subsample(node, f)` / `subsample(node, x=…, y=…)` · `threshold(node, "var > v")`
· `timesteps(node, start, stop)` · `compress(node, variables, error_bound)` ·
`save(node, path)` [sink] · `render(node, cmap=None, opacity=None)` [sink]. Full
semantics: `instructions/dsl-reference.md`; how to author well:
`instructions/authoring-specs.md`.

```python
render(subsample(source("/abs/path/heptane_302x302x302_uint8.raw"), 2), cmap="green")
```

**Folders are timeseries.** A `source` pointing at a folder is a time series — one
file per timestep, named `…#N` (N = timestep). This holds whether the folder is
**local or remote** (a remote folder is detected with a metadata-only `stat` over
ssh, then its `#N` files are mapped over next to the data). The interpreter maps
the rest of the chain over the timesteps; `timesteps(node, start, stop)` picks an
inclusive `#N` range. `render` over a series isn't supported (select one timestep,
or save the range); `save` writes one file per timestep. **`save` preserves the
source's format** — the output path's extension wins if known (`.npz`/`.hdf5`),
else the source's original format (HDF5 today; npz fallback for formats without a
writer). Multi-file loading lives in `my_save.py` + `planner._plan_folder` (local)
/ `planner._plan_remote_folder` (remote, reducing each timestep next to the data).

## MCP tools (called directly, not written in a spec)

- **`inspect(filepath, positions=None)`** — read a file's schema (metadata only).
  Use it before writing a spec. It may return one of two **handshakes** instead
  of a schema (see below).
- **`submit_adapter(filepath, module_code)`** — verify + freeze a reader you wrote
  for an unrecognized format (answers a `NEEDS_ADAPTER` handshake).
- **`submit_binding(filepath, binding_json)`** — verify + freeze a semantic
  binding you wrote for an HDF5 file (answers a `BINDING_AVAILABLE` offer).
- **`estimate_render_cost(filepath)`** — predict browser payload + disk-read cost
  and recommend a narrowing, metadata only.
- **`run_pipeline(spec_path)`** — execute `spec.py`.

## The LLM lives in *this* session — no external API

VisLang's LLM judgment runs on the **session model (you)**, never a separate API.
Because an MCP tool cannot call back into the session model, judgment happens via
a **handshake**: a tool returns evidence + a request; you reason (reading the
relevant `instructions/*.md`); you call a follow-up `submit_*` tool that runs a
**deterministic verifier** and freezes the result. Two cases today:

- **Unknown format** → `inspect` returns `NEEDS_ADAPTER` (format evidence). You
  write a reader module and `submit_adapter`; conformance against the real file is
  the trust step. Guide: `instructions/writing-adapters.md`.
- **HDF5 with only a generic listing** → `inspect` appends `BINDING_AVAILABLE`
  (the schema tree). You propose a binding JSON and `submit_binding`;
  `verify_binding` against the file's own metadata is the oracle. Optional
  enrichment. Guide: `instructions/writing-bindings.md`.

LLM-derived artifacts (adapters, bindings) are established at **authoring time**
via `inspect`; the **run path is cache-only** and errors if one is missing.

## Program flow / where things live

- **Spec → AST**: `dsl_forms/forms.py` (form constructors), `dsl_forms/nodes.py`
  (node types + the per-run sink registry).
- **Run**: `mcp_server.py` `run_pipeline` execs the spec, collects sinks, calls
  `planner.plan_pipeline` per sink.
- **Plan/execute**: `planner.py` — site dispatch (`_plan_remote` / `_plan_local`),
  inspect → `validate_narrowing` static check → classify/lower forms → fuse into
  one `Narrowing` → `materialize` → compress → sink.
- **Inspect**: `my_inspect.py` `inspect_file` → `adapters.get_adapter` (the trust
  ladder) → `DatasetInfo` (the format boundary; everything downstream is
  format-blind). `datasetInfo.py`.
- **Adapters**: `adapters.py` (Tier-0 readers: yt, HDF5, FITS, GenericIO;
  `NeedsAdapterError`), `llm_adapter.py` (Tier-1 session-model handshake:
  `gather_adapter_evidence`, `conform_and_freeze`), `generated_adapters/` (frozen
  modules), `schema_binding.py` + `binding_cache/` (HDF5 semantics).
  → `instructions/adapters.md`, `instructions/soundness.md`.
- **Narrow/load/output**: `narrowing.py` (selection primitives), `my_load.py`
  (universal load/materialize), `my_compress.py`, `my_render.py` (headless k3d →
  browser; `instructions/rendering.md`).
- **Remote**: `my_download.py` (ssh/rsync/scp/paramiko + probes), `remote_reduce.py`
  (ship the narrowing prefix next to the data), `vislang_exec.py` (the remote
  executor), `my_catalog.py` (local extent cache: `need − have = fetch`).
- **Estimate**: `my_estimate.py`.

## Non-negotiable principles

1. **Soundness over guessing.** Never hand-parse raw bytes; never trust generated
   code on faith. You *propose* a verifiable artifact; a deterministic check
   validates it; only verified, frozen artifacts run. → `instructions/soundness.md`
2. **Use trusted readers, in tiers.** Installed library → verified frozen adapter
   → (headerless raw) only via a size-checked filename convention; else raise.
3. **Rendering is headless.** No GPU/X on compute nodes; the look is set in the
   spec. The lever for a cheap overview is `subsample`/`region`, never `compress`.
4. **Write minimal specs** to the single `spec.py`; one concern per line; narrow
   large grids so the browser stays responsive.

Where the project is headed: `instructions/roadmap.md`.

## Environment

The MCP server runs under a conda env (`autoviz`); use that interpreter for
checks. Remote compute targets an HPC host over ssh — see `remote_reduce.py` for
the `VISLANG_*` env knobs (remote python/repo, srun placement, cache root,
binding mode).

**No auto-allocation — propose, don't grab.** In srun mode
(`VISLANG_SRUN_JOBID=auto`) a remote reduce `srun`-steps into a *held* Slurm
allocation and never creates one (never `sbatch`). When a run reports no
allocation — "no RUNNING allocation matching name vislang … run salloc first"
(a hard error under `VISLANG_REMOTE=force`, else a fall back to a
whole-file(/folder) fetch under `.vislang/downloads/`) — **propose** the command to the user
and let them approve it; an allocation spends real, shared HPC time, so never run
it silently:

    ssh <host> 'salloc --no-shell -J vislang -N 1 -p skylake-gold -t <walltime>'

`-J vislang` MUST match `VISLANG_SRUN_NAME` and `--no-shell` holds it for reuse,
or auto-discovery won't find it. Recommend `-p skylake-gold`; never guess
`general`; confirm the partition + walltime with the user. Once it is RUNNING,
re-run the spec — every timestep then steps into the same held allocation.
