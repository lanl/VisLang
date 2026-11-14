# Sieve ParaView plugin

Adds three Sieve objects to ParaView for local or remote (ssh) data:

| Object | Kind | What it does |
|---|---|---|
| **Sieve Source** | source | a coarse overview of a file or a folder of `…#N` timesteps |
| **Sieve Detail** | filter | a box over an overview (or another detail), fetched at full resolution |
| **Sieve Spec** | source | runs a spec you type, ending in `save(...)` |

`SieveParaView.py` is the plugin; `sieve_pv_core.py` is its helper and must
stay beside it.

## Install

1. In ParaView (6.1), open **Tools → Manage Plugins → Load New…** and pick
   `plugins/paraview/SieveParaView.py`.
2. Expand the plugin's entry and tick **Auto Load** to load it on every start.
3. The objects appear under **Sources → Sieve** and **Filters → Sieve**.

In pvpython: `LoadPlugin("/path/to/SieveParaView.py", ns=globals())`.

The plugin runs the `sieve` launcher of the repository it sits in (override
with `SIEVE_CMD`), under Sieve's own Python (`VISLANG_PYTHON`, else the conda
env).

**Remote data or `VISLANG_*` settings:** ParaView started from the Dock or with
`open -a` does not see your shell's environment. Start it from a terminal
instead:

```bash
/Applications/ParaView-6.1.0-RC2.app/Contents/MacOS/paraview &
```

## Use

### Sieve Source

1. Set **Source** to a file or folder: a local path, `ssh://host/path`, or
   `user@host:/path`.
2. Press **Apply**. Nothing is fetched yet. The panel describes the data and
   the cost of an overview, and a SpreadSheet opens with three tables:
   **Summary**, **Fields** and **Timesteps**. **Show Metadata** reopens it.
3. Adjust the request if needed, then **Apply** to fetch it, or press
   **Fetch Overview** to fetch the defaults:
   - **Fields**: which variables to fetch.
   - **Stride**: `0` picks one automatically.
   - **Value Filter**: e.g. `rho > 1.5`.
   - **Timesteps**: see [Time series](#time-series).
4. Press **Reset Camera** once the overview arrives.

**Positions** (gear icon, advanced) names a point file's x, y, z variables,
only needed when Sieve cannot find them.

### Sieve Detail

Select an overview, add **Filters → Sieve → Sieve Detail**, drag the box
over a feature and press **Apply**. The box arrives at full resolution, in
place. **Box Corner** and **Box Size** set the box by number. Add a Detail
under a Detail to zoom further.

### Time series

On a folder source, **Timesteps** selects what to fetch (empty = all):

| Text | Selects |
|---|---|
| `1-9` | timesteps 1 to 9 |
| `1,4,9` | exactly these |
| `1-9/4` | every 4th from 1 to 9 |
| `/4` | every 4th of all |
| `1-9,20` | combinations |

**Apply** fetches the whole selection in one request; the time slider then
steps through it. A Detail under a series fetches its box at the same
timesteps.

### Sieve Spec

Type any spec ending in `save(...)` into **Spec Text** and press **Apply**.
The first saved file is shown. **Promote To Spec** on a Source or Detail
copies the spec it last ran into a new Sieve Spec for editing.

## Panel controls

| Control | What it does |
|---|---|
| **Budget GB / Budget Minutes** | requests over either budget are held, not run |
| **Estimate** | prices the last Applied request without reading anything |
| **Fetch Anyway** | approves a held request and runs it |
| **Cancel** | stops the running request |
| **Connect** | opens the ssh password dialog for a remote source |
| **Status** | what happened: bytes fetched vs. predicted, cache reuse, or why a request was held |
| **Spec** | the spec that ran |

If a remote run needs a Slurm allocation, **Status** shows the `salloc`
command to run. The plugin never runs it for you.

## Output

Each session writes to `.vislang/paraview/<session>/` in the repository
(or `$VISLANG_HOME/paraview/<session>/`): a `session.jsonl` log with one line
per request, and one numbered folder per request holding its `spec.py` and
output.
