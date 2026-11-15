"""Turn an AST into one narrowed read.

`planner.plan_pipeline` is the entry point. Per sink it:

    1. inspects the source for its schema (metadata only),
    2. static-checks the request against that schema BEFORE any bulk read —
       a missing axis or an out-of-bounds range fails here, not after a
       20-minute load,
    3. fuses the structural narrowing into a single read: crop, stride, and
       projection push down together; value cuts apply right after,
    4. materializes, compresses if asked, and hands the result to the sink.

Written order is the promise. `threshold` then `subsample` samples the
survivors; the reverse thresholds the sample. How that order is *lowered* is
the interpreter's choice — which is what lets step 3 fuse anything at all.

A pipeline with no sink is a dry run: the plan is reported, nothing is read.
"""
