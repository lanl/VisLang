# `remote/` — move the question to the data

| File | What it holds |
|---|---|
| `reduce.py` | ship the narrowing prefix next to the data; srun placement; the env knobs |
| `executor.py` | the reducer that runs on the remote host (launched via `vislang_exec.py` at the repo root) |
| `download.py` | ssh / rsync / scp transport, connection probes, the askpass session |
| `hosts.py` | per-host config — interpreter, repo path, tmp dir — from `.vislang/hosts.json` or env |
| `catalog.py` | the local extent cache, so a repeat request fetches only `need − have` |

## Two rules that do not bend

**Never run code that crossed the network.** A pipeline travels as JSON and is
rebuilt through `dsl/ast_serialize.py`'s allowlist. The executor is a fixed,
audited artifact; the request is inert data. The LLM binding path is disabled
outright inside the executor — a reducer must be deterministic.

**Never allocate.** Under `VISLANG_SRUN_JOBID=auto` a reduce `srun`-steps into a
Slurm allocation that is *already held*. It never runs `salloc`, never `sbatch`.
When no allocation is found the run says so and falls back to a whole-file
fetch, or fails outright under `VISLANG_REMOTE=force`.

An allocation spends real, shared time, so the command is **proposed** to a
human and never run for them:

```bash
ssh <host> 'salloc --no-shell -J vislang -N 1 -p <partition> -t <walltime>'
```

`-J vislang` must match `VISLANG_SRUN_NAME` and `--no-shell` holds it for reuse,
or auto-discovery will not find it. Confirm the partition and walltime with the
person first; never guess.

Transport shells out to the system `ssh`, `rsync`, and `scp` — there is no
Python SSH library in the dependency set, and the password for a session is
typed into a dialog on the user's own screen, never handled by the model.
