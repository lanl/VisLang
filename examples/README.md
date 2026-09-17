# Examples

Specs to copy from. Each one is complete and teaches a single idea. Replace the
placeholder paths with real ones before running.

| File | Shows |
|---|---|
| `01_render_volume.py` | the smallest spec; `subsample` as the cheap-overview lever |
| `02_narrow_and_save.py` | chaining narrowings, and why written order is meaning |
| `03_timeseries.py` | a folder as a series, and selecting a range of timesteps |
| `04_remote_reduce.py` | an `ssh://` source, and the allocation the reducer needs |
| `05_dry_run.py` | no sink — the inferred plan and predicted cost, reading nothing |

## Running one

Working specs go in `spec.py` at the repo root, edited in place. That single
file is the shared artifact between you and the model — keep it small and
legible rather than accumulating one file per question.

```bash
sieve execute spec.py        # from a terminal
```

From an MCP client, call `run_pipeline("spec.py")`.

Start with `05_dry_run.py`'s shape on any unfamiliar dataset: drop the sink, see
the plan and the estimate, then add the sink once the numbers look sane.
