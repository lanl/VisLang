# Authoring specs

The spec is the record of what you and the scientist agreed to do with their
data. Keep it short enough that they can read it and correct it.

## The loop

1. **Inspect.** Call `inspect(path)` on any source you have not seen in this
   conversation. It reads metadata only. Do not write field names, axis names
   or ranges you have not seen in its output.
2. **Write `spec.py`.** Use this one file and edit it in place. Do not create a
   new file per request.
3. **Run.** Call `run_pipeline("spec.py")`. If you are unsure about cost or
   correctness, first run the spec with the sink removed. That dry run reports
   the plan and the cost and reads nothing.
4. **Look together.** Report what came out: the shape, the output path or
   viewer URL, and anything the report flagged.
5. **Change one thing.** Turn the scientist's next request into the smallest
   edit, say what you changed, and run again.

Requests map onto edits:

| They say | You change |
|---|---|
| "make it green" | `cmap="green"` |
| "more detail" | a smaller `subsample` factor |
| "just the core" | add or tighten a `region` |
| "only the hot gas" | add a `threshold` |
| "for ParaView" | the `save` extension to `.vti`, `.vts` or `.vtp` |
| "steps 10 to 20" | `timesteps(series, 10, 20)` |

## Shape of a good spec

```python
# Hot core of run 42 for the group meeting; stride 2 keeps it under 100 MB.
src  = source("/abs/path/run042/plt00100.hdf5")
core = region(fields(src, ["temperature"]), x=(128, 384), y=(128, 384))
render(subsample(core, 2), cmap="inferno")
```

- **Absolute paths everywhere.** The server's working directory is not yours.
- **Read left to right:** source, then narrowing, then one sink. One step per
  line once there are more than two.
- **Comment the why.** Explain why this stride, this bound, or this threshold,
  not what the form does. Comments are kept verbatim in the provenance record
  of every output.
- **Visual choices belong in the spec** (`cmap`, `opacity`, resolution), where
  the scientist can see and edit them.
- **Several boxes go in one `region(center={tag: ...})`**, not one spec per box.
  That way the data is read once, a remote run is one job, and ParaView opens
  every box from one index.

## Rules

- Do not import anything, open files, or call internal functions from a spec.
  It runs in a sandbox, and only the forms exist there.
- Do not invent values. If metadata the request depends on is missing, such as
  units, a box size or a timestep, say so and ask.
- Do not render at full resolution "just to see". Check the cost first
  (`rendering.md`). `subsample` and `region` make a render cheaper; `compress`
  does not.
- Narrow remote sources. A remote spec with no narrowing copies the whole file
  (`remote.md`).
- Trust the static check. If it says an axis or range is wrong, it is.
  Re-inspect, and don't keep adjusting until the error goes away.
- When a run is held (`NEEDS CONFIRM`, `NEEDS SESSION`, `NEEDS ALLOCATION`),
  stop and bring it to the scientist. A hold is a question for them, not an
  error for you to fix.
