# `plugins/paraview/` — Sieve inside ParaView

Overview first, zoom and filter, details on demand — on local or remote data,
without leaving ParaView. Every step is a real Sieve spec: checked, priced,
held over budget until approved, served from the cache when it can be, and
recorded with provenance.

| File | What it holds |
|---|---|
| `SieveParaView.py` | the plugin ParaView loads: **Sieve Source**, **Sieve Detail**, **Sieve Spec** |
| `sieve_pv_core.py` | the engine, no ParaView in it: runs `sieve api` requests one at a time, polls them, remembers results, writes the session trace |

## Load it

ParaView 6.1: **Tools → Manage Plugins → Load New…**, pick `SieveParaView.py`
(tick *Auto Load* to keep it). The three objects appear under **Sources →
Sieve** and **Filters → Sieve**. In pvpython: `LoadPlugin(path, ns=globals())`.

The plugin runs the `sieve` launcher of the repository it sits in (override
with `SIEVE_CMD`), which runs Sieve's own Python (`VISLANG_PYTHON`, else the
conda env). ParaView's Python settings are scrubbed from that process. When
ParaView is started from the Dock it does not see your shell's `VISLANG_*`
settings: start its binary from a terminal to keep them
(`/Applications/ParaView-6.1.0-RC2.app/Contents/MacOS/paraview &` — `open -a`
does not pass the shell's environment on).
Each session's `session.json` records which were in force.

## Use it

1. **Sieve Source** — set *Source* to a file or a folder of `…#N` timesteps:
   a path, or `ssh://host/path` / `user@host:/path`. *Apply*: the panel shows
   what it is (grid, points, timesteps, which coordinates a region takes)
   and what an overview at the automatic stride would cost. Nothing is
   fetched yet. Pick *Fields*, *Stride* (0 = automatic) or a *Value Filter*
   (`rho > 1.5`) and *Apply*, or press *Fetch Overview* to take it as it is.
   Press *Reset Camera* once it arrives (ParaView framed the empty first
   output).
2. **Sieve Detail** — select the overview, add the filter, drag the box over a
   feature, *Apply*: that box comes at full resolution, drawn in place. Put a
   detail under a detail to zoom again.
3. **Time** — the slider walks the timesteps; each is fetched once, then shown
   at once. *Prefetch* fetches a whole series in one request (on a Detail: the
   box across every timestep).
4. **Sieve Spec** — type any spec ending in `save(...)` (named boxes, a box
   that follows a track…).

The panel's *Status* says what happened — fetched (bytes against the
prediction, cache reuse), or held and why: over budget (*Fetch Anyway*
approves it), no session (*Connect* opens the password dialog), no Slurm
allocation (it shows the `salloc` to ask for; the plugin never runs it).
*Spec* shows the spec that ran; it is saved beside its output.

## Where things land

`<sieve home>/paraview/<session>/` (`.vislang/paraview/…` in the repo):
`session.json`, `session.jsonl` (one line per request — spec, estimate, bytes,
seconds, cache reuse, run id; what the evaluation replays), and one numbered
folder per request with `spec.py`, the output, and the request/result/events
files.

## Tests

`tests/test_pv_core.py` (the engine, plain Python) and
`tests/test_paraview_plugin.py`, which runs `tests/paraview/pv_plugin_checks.py`
in pvpython: every kind of data, time, prefetch, the box, holds and
*Fetch Anyway*, *Sieve Spec*, the background path driven as ParaView's timer
drives it, and a simulated remote (`tests/paraview/fake_sieve`).

## GUI checklist (what only the GUI can show — about 10 minutes)

pvpython cannot run ParaView's live-source timer, the box widget or the
password dialog. With the plugin loaded in the ParaView GUI:

1. **Live update.** Sieve Source on a remote file (or a large local one),
   *Apply*: *Status* describes it and fetches nothing. *Fetch Overview*:
   ParaView stays usable while *Status* says "Fetching — …"; the overview
   appears by itself when it arrives.
2. **Fields list.** After the first *Apply*, *Fields* lists the variables.
3. **Time slider.** On a folder, the slider shows the `#N` timesteps after the
   first *Apply*; moving it shows each timestep (revisits are instant).
4. **Box widget.** Add Sieve Detail: a box sits on the overview's bounds.
   Dragging it updates *Box Corner* / *Box Size*; it cannot be rotated;
   *Apply* puts the full-resolution piece exactly inside the box.
5. **Buttons.** Set *Budget GB* to 0.000001, *Apply*: *Status* says it is over
   budget; *Fetch Anyway* runs it. *Cancel* during a fetch stops it.
6. **Connect.** On a remote source with no session, *Connect* opens the
   password dialog (macOS may ask to allow ParaView to control System Events —
   allow it); afterwards *Apply* works.
7. **No leftovers.** Quit ParaView mid-fetch: no `sieve api` process is left
   running (`ps aux | grep "sieve api"`). Deleting a Sieve object mid-fetch
   lets that request finish (it warms the cache) and shows nothing.
