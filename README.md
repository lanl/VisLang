# SIVA

> [!WARNING]
> SIVA is developed largely through AI-assisted programming and has not been
> reviewed in detail by humans. We make no guarantees about its security —
> see [Security](#security) for the measures in place and their limits.
> In sensitive environments, isolate the whole application;
> [`deploy/docker/`](deploy/docker/) is a worked example of running it in
> a container.

SIVA lets you build scientific visualizations by talking to an AI. You
describe what you want to see, the AI writes declarative pipeline code, and
you iterate together — exploring data, tuning parameters, and refining the
picture through conversation.

Here's what a pipeline file looks like — this volume-renders a CT scan of a
bonsai tree with a terrain colormap and gradient-enhanced opacity:

```python
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="bonsai.vti")

show(data, "bonsai", representation="Volume",
    color_by="density", scalar_range=(20, 200), lut="terrain",
    opacity_function=[(0, 0.0), (18, 0.0), (26, 0.01), (36, 0.05),
                      (48, 0.13), (60, 0.26), (80, 0.48),
                      (110, 0.68), (150, 0.84), (220, 0.96)],
    gradient_opacity=True, shade=True)

background(0.02, 0.02, 0.05)
```

Every pipeline file begins with `from siva.spec_api import *` — that header
makes the SIVA DSL forms (`source`, `filter`, `show`, `threshold`, `contour`,
…) available.

![Bonsai CT scan rendered with SIVA](docs/example.png)

SIVA has two parts:

1. **A pipeline DSL** — a concise Python syntax for VTK visualization.
   You describe sources, filters, and display properties declaratively;
   SIVA wires up the VTK objects for you.

2. **An MCP server** — exposes the DSL and data query tools to any AI assistant
   via [Model Context Protocol](https://modelcontextprotocol.io/). The assistant
   can load data, explore field statistics, execute pipeline code, adjust the
   camera, and see screenshots — all through tool calls. Pipeline code the
   assistant submits runs inside a restricted sandbox (see
   [Security](#security)).

## Setup

SIVA works with any MCP-compatible AI assistant (Claude Code, Claude Desktop,
etc.). Point your assistant at the SIVA server and start a conversation in
the directory where your data lives.

The steps below run SIVA directly on your machine, with no isolation beyond
the built-in pipeline-code sandbox: SIVA itself and your AI assistant run
with your user's full privileges (see [Security](#security)). For an
isolated setup, see [Running in Docker](#running-in-docker-isolated-setup)
below.

### 1. Install

With [uv](https://docs.astral.sh/uv/) (recommended — creates `.venv/` and
installs the locked dependencies in one step):

```bash
cd /path/to/SIVA
uv sync
```

Or with plain pip:

```bash
cd /path/to/SIVA
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure your AI assistant

Add SIVA to your project's `.mcp.json` (or your assistant's MCP settings),
pointing at the Python from the venv:

```json
{
  "mcpServers": {
    "SIVA": {
      "command": "/path/to/SIVA/.venv/bin/python",
      "args": ["-m", "siva.server", "--workdir", "/path/to/your/data"]
    }
  }
}
```

This opens a live VTK window where you can see the visualization update as
the AI builds it. `--workdir` sets the working directory for the session —
the server discovers data files there, and the pipeline files the AI writes
land there too. (Omit it to use the directory you launched the assistant
from.) To view in a browser instead of a native window — including from a
remote machine — see [Live views in the browser](#live-views-in-the-browser---trame).

With Claude Code, you can also pre-approve the SIVA tools so you aren't
prompted on every call — see [Security](#security) for the permission
allowlist and why it's scoped the way it is.

**Model tip:** With Claude Code, Opus at low reasoning effort has given the
best balance of speed and skill in our experience — smarter pipeline choices
than Sonnet without the latency of higher reasoning effort.

### 3. Start a conversation

Open a chat with your AI assistant (Claude Code, Claude Desktop, etc.) and
ask it to visualize your data:

> **You:** Load output.30000.vts and show me what's in it.
>
> **AI:** *(calls load(), describes fields, dimensions, value ranges)*
>
> **You:** Show me the temperature field — I want to see where it's hottest.
>
> **AI:** *(writes a pipeline with threshold + volume rendering, shows screenshot)*
>
> **You:** Can you add streamlines showing the wind flow through the hot region?
>
> **AI:** *(adds make_vector + stream_tracer to the pipeline, iterates)*

### Running in Docker (isolated setup)

[`deploy/docker/`](deploy/docker/) runs SIVA and Claude Code together in a
container: your data is mounted read-only, the views are served to your
browser, and the container's outbound network is restricted to an allowlist
(by default, only the Anthropic API). Rendering is software-only (CPU), so
it's slower than a local GPU for large data.

```bash
deploy/docker/run.sh ~/siva-work /path/to/dataset-dir
docker compose -f deploy/docker/docker-compose.yml exec siva claude
```

See [`deploy/docker/README.md`](deploy/docker/README.md) for the full
walkthrough and the setup's limitations.

## Security

There are two separate trust questions, with different answers.

**The pipeline code the AI writes at runtime.** SIVA treats spec code as
untrusted — a prompt-injected assistant can submit malicious code through
the same channel a cooperative one uses — and executes it inside
[pydantic-monty](https://pypi.org/project/pydantic-monty/), a restricted
Python interpreter: no filesystem, OS, or network access, no `exec`/`eval`,
restricted imports, and caps on execution time, memory, and recursion
depth. Dataset paths named in a pipeline are confined to the server's
working directory — absolute paths and `../` escapes are rejected, though
symlinks you place inside the directory are followed. Treat this as defense
in depth, not a guarantee: the sandbox and the confinement checks are part
of the same codebase the warning above is about.

**SIVA itself.** The server, the renderer, and the sandbox implementation
run unsandboxed, with your user's privileges, and were developed largely
through AI-assisted programming without detailed human review. They could
contain security flaws. This is why the recommendation is to isolate the
whole application, not just the pipeline channel.

The sandbox also constrains only the pipeline code submitted through SIVA.
What your assistant can do with its other tools — Claude Code's shell
access, for example — is governed by the assistant's own permission
settings, so those settings are part of a careful setup.

SIVA's MCP tools are designed to be safe to auto-allow: they read data and
write pipeline files and screenshots only within the server's working
directory, and pipeline execution goes through the sandbox. Beyond the MCP
tools, the assistant edits the `view-*.py` pipeline files directly (the
server hot-reloads them), so it needs read and write access in the working
directory. Nothing else — shell, network, files elsewhere — is needed to
drive SIVA. Since the working directory bounds both the path confinement
and these grants, give the server a dedicated one (`--workdir`) holding
only session files and the data you're visualizing — not your home
directory.

For Claude Code, that policy is a `.claude/settings.local.json` in the
directory you run the assistant from:

```json
{
  "permissions": {
    "allow": [
      "mcp__SIVA__*",
      "Edit(view-*.py)",
      "Write(view-*.py)"
    ]
  },
  "enabledMcpjsonServers": ["SIVA"]
}
```

This pre-approves the SIVA tools and pipeline-file edits (reads in the
working directory are allowed by default) and leaves everything else —
shell commands, other file writes — behind the normal permission prompts.
The Docker setup's `run.sh` generates a similar file. Prompts are a
least-privilege measure, not a container; for real isolation, run the
whole stack — SIVA and the assistant — in a container or VM with
restricted outbound network, as the
[Docker setup](#running-in-docker-isolated-setup) does.

## Live views in the browser (`--trame`)

Instead of a native VTK window, SIVA can serve each view as an interactive
3-D view in your browser via [trame](https://kitware.github.io/trame/),
rendered server-side and streamed over a websocket. Use this when SIVA runs
where your display isn't — a remote server or a container.

Install the trame extra and add `--trame`:

```bash
uv sync --extra trame        # or: pip install -e ".[trame]"
```

Note that `uv sync` makes the environment match exactly, so a later plain
`uv sync` removes the extra again — keep `--extra trame` on subsequent syncs.

```json
"args": ["-m", "siva.server", "--trame", "--trame-port", "8765",
         "--workdir", "/path/to/your/data"]
```

When the first view is ready, the AI relays a URL for the **view index
page** — here `http://localhost:8765/views`, one stable page listing every
live view, updating as views are added. Open it once and keep the tab; each
view opens in its own tab where you can rotate, zoom, and inspect while the AI
edits the pipeline. The AI can repeat the URL via `view_url()`; it's also in
`.siva/server.log`.

`--trame-port` pins the port everything is served on. Without it the server
picks a free port at random each time it starts, so the URL changes whenever
your assistant restarts the MCP server (e.g. on an editor reload); pinning it
keeps one bookmarkable URL. If the port is already in use the server fails to
start — pick another. You can change the interface the trame server binds to
with `--trame-host <ip>`. In a headless environment, the server must be run
with `xvfb-run -a`.

## Sample data

The repository includes several sample datasets under `datasets/`, each with a
`download.sh` that fetches the data and, where needed, converts it into a
SIVA-ready `.vti`/`.vts` file. The conversion step uses VTK, so create the venv
first (see [Install](#1-install) above), then run the script for the dataset
you want:

```bash
datasets/bonsai/download.sh      # the bonsai CT scan used in the example above
```

The result lands in `datasets/<name>/data/`. Point the server's `--workdir` at
that directory (or symlink the file into your working directory) so the AI can
load it. Other datasets include `cthead`, `foot`, `hydrogen_atom`, and
`wildfire`, plus a `synthetic` generator that needs no download.

## Running pipelines directly

You can also run pipeline files directly, without the MCP server.
Activate the venv (or use its Python) and run:

```bash
# Open an interactive VTK window
python -m siva.run pipeline.py

# Save a screenshot
python -m siva.run pipeline.py -o output.png

# Custom resolution
python -m siva.run pipeline.py -o output.png --size 3840x2160
```

Useful for batch rendering, testing pipelines, or using SIVA without
an AI assistant.

### Editor support

Every pipeline file begins with `from siva.spec_api import *`. That header
resolves to a generated stub that mirrors the DSL, so a Python language server
(Pylance, or any pyright setup) can provide autocomplete and hover docs for the
forms, class-specific `**props` completions for `source` and `filter`, and type
checking that flags misspelled verbs, unknown properties, and invalid colormap
or representation values. Point the editor at the SIVA virtualenv's interpreter.
The stub contains types only and never executes; the runtime binds the real DSL.

#### VS Code setup

To edit pipeline files in a folder outside the SIVA repo, add a
`.vscode/settings.json` there:

```json
{
  "python.defaultInterpreterPath": "/path/to/SIVA/.venv/bin/python"
}
```

Reload the window afterward (Developer: Reload Window). The interpreter is all
the language server needs; SIVA's editable install registers the package via a
plain path entry that Pylance follows statically.

Pylance may show one cosmetic warning on the header line ("Wildcard import
from a library not allowed"), because spec files star-import from an installed
package by design. Silence it with:

```json
  "python.analysis.diagnosticSeverityOverrides": {
    "reportWildcardImportFromLibrary": "none"
  }
```

None of this is needed when the pipeline files are inside the SIVA repo.

## What it supports

- **Data formats:** VTK structured grids (`.vts`), image data (`.vti`),
  polydata (`.vtp`), unstructured grids (`.vtu`), rectilinear grids (`.vtr`),
  raw binary volumes
- **Visualization techniques:** isosurfaces, thresholds, volume rendering,
  cross-section slices, streamlines, glyphs, colormapping, clipping
- **Derived quantities:** vector fields from scalar components, vorticity,
  gradient magnitude, individual vector components
- **Interactivity:** camera control, opacity adjustment, colormap changes,
  text annotations, multi-view support

## Documentation

| Document | Description |
| -------- | ----------- |
| [Getting Started](docs/getting-started.md) | Architecture overview, workflow walkthrough, key patterns |
| [DSL Reference](docs/dsl-reference.md) | Complete reference for pipeline forms (`source`, `threshold`, `show`, etc.) |
| [MCP Tool Reference](docs/mcp-reference.md) | Complete reference for interactive tools (`describe_data`, `wait_for_pipeline`, etc.) |
| [Server Instructions](docs/instructions.md) | The guidance string shown to AI assistants on connect |

Docs are auto-generated from source — run `python scripts/gen_docs.py` to regenerate
after code changes.

## Development

For contributors working on SIVA itself:

```
siva/
  server.py      MCP server and tool definitions
  dsl.py         DSL forms and pipeline interpreter
  renderer.py    VTK renderer
  queries.py     Query tool implementations
  filters.py     VTK filter creation and special-case handling
  colormaps.py   Colormap presets and field defaults
datasets/        Sample datasets, each with a download.sh script
scripts/         Development scripts (gen_docs.py, etc.)
docs/            Generated documentation
tests/           Test suite
```

```bash
# Install the project with dev dependencies (adds pytest)
uv sync                      # or: pip install -e . --group dev

# Run the test suite
python -m pytest tests/ -q

# Regenerate documentation
python scripts/gen_docs.py
```

See [CLAUDE.md](CLAUDE.md) for detailed development guidance.


## Copyright
© 2025. Triad National Security, LLC. All rights reserved.

This program was produced under U.S. Government contract 89233218CNA000001 for Los Alamos National Laboratory (LANL), which is operated by Triad National Security, LLC for the U.S. Department of Energy/National Nuclear Security Administration. All rights in the program are reserved by Triad National Security, LLC, and the U.S. Department of Energy/National Nuclear Security Administration. The Government is granted for itself and others acting on its behalf a nonexclusive, paid-up, irrevocable worldwide license in this material to reproduce, prepare. derivative works, distribute copies to the public, perform publicly and display publicly, and to permit others to do so.