# Authoring Specs

A spec is the unit of collaboration between you and the human. It should read
like a short, honest description of *what to do with this dataset*.

**One file: `spec.py`.** Write every spec to `spec.py` and edit it in place — never
a new file per request. Then `run_pipeline("spec.py")`.

## Shape of a good spec
```python
# narrow upstream, end in a sink (render/save):
render(subsample(fields(source("/abs/path/data.ext"), ["density"]), 2), cmap=...)
```
For a first look at an unfamiliar dataset, skip the narrowing and just
`render(source(path))` to see the whole thing, then refine on the next turn.
- **It reads left-to-right as intent.** source → (fields/region/subsample/threshold)
  → render/save. Nothing runs until the sink. Order is honored where it matters:
  `threshold` then `subsample` samples the survivors; the reverse thresholds a sample.
- **Absolute paths.** Specs are exec'd by a long-running server with its own cwd.
- **Narrow large grids** (`subsample`/`region`) so a render stays responsive
  (see `vislang://instructions/rendering`).
- **Comment the *why*, not the obvious** — e.g. why a particular stride or error bound.

## Do
- For an unfamiliar or large file, `inspect(path)` (and `estimate_render_cost(path)`)
  first, then narrow so the first overview stays responsive.
- Let `source`/`inspect` choose the adapter; trust the trust ladder.
- Put rendering choices (colormap, opacity, resolution) *in the spec* so they
  are visible and editable by the human.
- Trust the static check: a bad axis, out-of-range region, or unknown
  variable/threshold raises *before any data is read* — fix the spec, don't guess.
- For several boxes, name them in one `region(center={tag: …})` rather than
  writing a region and a `save` per box: the file is read once, a remote run is
  one job, and the output folder has one series per tag plus a ParaView index.

## Don't
- Don't import readers, hand-parse bytes, or call `load`/`subset`/`download` in a
  spec — those are hidden physical ops the interpreter emits. Use the forms.
- Don't hardcode magic values you invented; if metadata is missing, say so.
- Don't render full resolution "just in case" — the most common cause of a
  sluggish or blank browser. `compress()` is for storage, not for cheapening a
  render: k3d ships the full-res array regardless, so `subsample`/`region` is the lever.

## The iteration loop
Write `spec.py` → `run_pipeline("spec.py")` → look at the result with the human →
edit that same `spec.py` (one line) → re-run. The view updates in place, so the
conversation stays at the spec level: "make it green" becomes `cmap='green'`;
"show more detail" becomes a smaller `subsample` factor; "just the core" becomes a
`region(...)`. Keep the diff small and explain each change.
