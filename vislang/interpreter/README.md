# `interpreter/` — from AST to one narrowed read

| File | What it holds |
|---|---|
| `planner.py` | `plan_pipeline` — site dispatch, static check, lowering, fusion, execution |
| `narrowing.py` | the selection primitives: `Narrowing`, `AxisRange`, `Predicate`, `BBox`, row masks |
| `approval.py` | what a run is approved to do past its gates — over budget, no Slurm allocation, a fallback copy of whole files — each approved separately |
| `coords.py` | which coordinates a `region` is in (the data's own, or `coords=`), and a grid's world box rewritten as the cells it selects — so everything after it sees cells on grids |
| `track.py` | per-timestep regions and named boxes: reads a track file at planning time, checks centre coverage (or, for named boxes, which tag runs where) before any read, and turns one timestep's centre into a concrete box |
| `load.py` | the universal load/materialize path, driven by a `Narrowing`; `materialize_boxes` cuts several boxes of one file from a single read where the format would read the whole file per box anyway |
| `subset.py` | metadata-only narrowing — trims the schema, reads nothing |
| `compress.py` | error-bounded compression of materialized arrays |
| `estimate.py` | predicted payload and disk-read cost, from metadata alone |
| `explain.py` | a debugging view: input AST, the interpreter's decisions, the lowered narrowing |

## The order of operations

Per sink, `plan_pipeline` does the same four things:

1. **inspect** the source for its schema (metadata only)
2. **static-check** the request against it — *before any bulk read*
3. **fuse** the structural narrowing into a single read: crop, stride, and
   projection push down together; value cuts apply immediately after
4. **materialize**, compress if asked, hand off to the sink

Step 2 is the point of the whole design. A missing axis or an out-of-bounds
region is an error in milliseconds, not a failure after a long load.

Step 3 is why the DSL is declarative. Written order is a promise about
*meaning* — `threshold` then `subsample` samples the survivors, and the reverse
thresholds the sample — but not about execution. Because the interpreter owns
the lowering, it can collapse several forms into one read without changing what
the spec means.

A pipeline with no sink stops after step 2 and reports the plan.

`estimate.py` is metadata-only by contract; if you find yourself wanting to read
data to estimate, the estimate is wrong. The lever for a cheap overview is
`subsample` or `region` — never `compress`, which trades fidelity for size, not
for time.

Run `explain.py` against a spec to see all three views side by side.
