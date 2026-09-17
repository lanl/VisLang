# `interpreter/` — from AST to one narrowed read

| File | What it holds |
|---|---|
| `planner.py` | `plan_pipeline` — site dispatch, static check, lowering, fusion, execution |
| `narrowing.py` | the selection primitives: `Narrowing`, `AxisRange`, `Predicate`, `BBox`, row masks |
| `load.py` | the universal load/materialize path, driven by a `Narrowing` |
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
