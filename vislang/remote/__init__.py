"""Move the question to the data, not the data to the question.

When a source lives on an HPC host, the interpreter ships the *narrowing prefix*
of the pipeline — serialized as inert JSON, never as code — to `executor.py`
running next to the data. What comes back over the wire is the reduced result.
`catalog.py` keeps the local extent cache, so a second request asks only for
`need − have`.

Two rules this package does not bend:

  - **Never run code that crossed the network.** A plan is data, validated
    against an allowlist by `dsl/ast_serialize.py` before it rebuilds an AST.
  - **Never allocate.** In srun mode the reducer steps into an allocation that
    is already held, and never creates one (never `sbatch`, never `salloc`).
    An allocation spends real, shared time — propose the command, let a human
    approve it. With no allocation the run either falls back to a whole-file
    fetch or, under `VISLANG_REMOTE=force`, fails loudly.

Env knobs and the srun placement logic are documented in `reduce.py`.
"""
