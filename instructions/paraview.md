# Sieve inside ParaView

The ParaView plugin (`plugins/paraview/SieveParaView.py`) lets a scientist
explore local or remote data the way visualization is usually done — overview
first, zoom and filter, details on demand — without writing specs by hand.
Every step the user takes in ParaView is still a Sieve spec: the plugin writes
it, Sieve checks, prices, gates, caches and records it. This page is for
helping someone who uses the plugin, and for evaluating it.

## What the user sees

| Object | What it does | The spec it writes |
|---|---|---|
| **Sieve Source** | a coarse overview of a file or a folder of `…#N` timesteps, local or `ssh://`/`user@host:` | `source` → `timesteps(N, N)` (series) → `fields` → `threshold` → `subsample(stride)` → `save` |
| **Sieve Detail** | a filter under an overview (or a detail): the native box widget, fetched at full resolution | the same, with `region(x=…, y=…, z=…, coords=…)` — always explicit |
| **Sieve Spec** | any spec the user types (named boxes, a track…) | the text itself |

Buttons: **Fetch Anyway** (approve a held request: over budget, or a fallback
whole-file copy), **Cancel**, **Connect** (opens the password dialog),
**Prefetch** (every timestep of a series in one request — within the Source's
*First/Last Timestep*; on a Detail, the box across time), **Promote To Spec** (copy the spec into a Sieve Spec to edit).
*Status* says what happened; *Spec* shows the spec that ran.

The box becomes a region in the data's own coordinates — the numbers on
ParaView's axes: world coordinates on points and on grids that state where
they sit (bounds rounded outward, never inward), cells on grids that do not
(`[ceil(lo), floor(hi)+1)`, clipped to the cells the file holds). Overviews and
details of grids with no geometry are placed in their source's cells, so they
overlay exactly. See `dsl-reference.md` → `region`.

## How it runs

The plugin never imports Sieve. Each request is one `sieve api run` process
(`vislang/server/api.py`) with a request, result and progress-events file in
its own step folder; requests run one at a time; ParaView polls them through
its live-source timer, so it never freezes. A held request (`budget`, `fetch`,
`allocation`, `session`) shows in *Status* with what lifts it — the plugin
never runs `salloc`: it shows the command to ask for.

## The session folder

`<sieve home>/paraview/<session id>/` (`.vislang/paraview/…` in the repo):

    session.json     the settings in force (VISLANG_*), ParaView version
    session.jsonl    one line per request: action (inspect / overview / detail /
                     spec / prefetch), status and holds, spec path, run id (joins
                     .vislang/timings.jsonl and the outputs' provenance), route,
                     wire and probe bytes, cache reuse, the estimate, queue and
                     process seconds, memo hits (revisits shown without a run)
    NNN-<what>/      spec.py, the output, request.json, result.json, events.jsonl

To help a user: read `session.jsonl` to see what they did and what was held
and why; a step's `spec.py` is a normal spec — run, edit or explain it like
any other.

## Evaluating it

    python bench/replay_session.py <session dir>        # cold, warm, no-catalog, full-download
    pvpython bench/pv_session_figure.py <session dir> fig.png [--timestep N]

`replay_session.py` replays a session's specs with the think time removed,
each setup isolated (its own cache, downloads, timings): time to first image,
per-step latency, bytes moved (cumulative, against the full-download
baseline), cache reuse, estimate against actual, and whether every output
reproduces the session's data bit for bit (`data_sha256`). Results go to
`bench/results/replay-<session>/`. `pv_session_figure.py` draws the overview
with each detail in place, from the files the session wrote.

## Limits

- One request at a time (Sieve's catalog is shared and locked, but ssh and
  Slurm steps are serialized deliberately).
- A box that moves per timestep, or several named boxes, are written in a
  Sieve Spec (Promote To Spec, then edit), not drawn.
- ParaView 6.1 is the version it is built and tested on.
