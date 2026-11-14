# Sieve

Sieve (Python package `vislang`) is a declarative DSL for extracting the part
of a large scientific dataset that a scientist needs, locally or next to the
data on a remote machine. Your job is to write a short spec with the scientist
and run it through the Sieve MCP tools.

The guides in `instructions/` hold the details. They are also served to any
MCP client as `vislang://instructions/<name>`. Read the relevant one before
acting on anything it covers.

## The loop

1. `inspect(path)`: schema only, no bulk data. Call it on every source you
   haven't seen in this conversation.
2. Write `spec.py`: one file, edited in place, absolute paths, no imports.
3. `run_pipeline("spec.py")`. Without a sink, this is a dry run that reports
   the plan and cost and reads nothing.
4. Show the scientist the result, make the smallest edit for their next
   request, and run again.

## Rules

- **Never pass `confirm=True` on your own.** Show the estimate and let the
  scientist decide.
- **Never handle passwords.** `connect(host)` prompts the scientist in a
  dialog that you never see.
- **Never run `salloc` or `sbatch` without approval.** Propose the command;
  allocations spend shared HPC time.
- **Never guess.** If a format is unknown, or metadata is missing, or you can't
  tell what a field means, use the handshake or ask the scientist. Don't
  hand-parse bytes or invent values.
- **Narrow before you move or render data.** Use `subsample` and `region`, not
  `compress`. A remote spec with no narrowing copies the whole file.

## When a tool replies with…

| Reply | Do this | Read |
|---|---|---|
| `NEEDS_ADAPTER` | Write a reader, then call `submit_adapter` | `instructions/writing-adapters.md` |
| `BINDING_AVAILABLE` | Optionally propose a binding, then call `submit_binding` | `instructions/writing-bindings.md` |
| `NEEDS_SESSION` / `Status: NEEDS SESSION` | Call `connect(host)`, then retry | `instructions/remote.md` |
| `UNREACHABLE` | Report a network or VPN problem; don't offer to log in | `instructions/remote.md` |
| `Status: NEEDS ALLOCATION` | Propose the `salloc` line and wait for approval | `instructions/remote.md` |
| `Status: NEEDS CONFIRM` | Show the estimate; offer to narrow or confirm | `instructions/authoring-specs.md` |
| `Status: BUILD FAILED` / `FAILED` | Fix the spec from the message; re-inspect if needed | `instructions/dsl-reference.md` |

If a run behaves unexpectedly, `.vislang/trace.log` has the full account of
what it decided.

## Where to look

| Task | Guide |
|---|---|
| Working through a spec with a scientist | `instructions/authoring-specs.md` |
| What exactly each form does; output formats | `instructions/dsl-reference.md` |
| Anything involving `render` | `instructions/rendering.md` |
| `ssh://` sources, sessions, Slurm | `instructions/remote.md` |
| "Where did this file come from?", re-running an old result | `instructions/provenance.md` |
| Why the rules are what they are | `instructions/soundness.md` |
