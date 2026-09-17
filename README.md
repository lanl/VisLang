# VisLang

A declarative DSL for reading, inspecting, narrowing, compressing, and rendering
scientific data — designed so that a language model can drive it safely.

You describe *what* you want from a dataset. The interpreter decides *how* to get
it: it reads the source's schema, checks your request against that schema before
touching any bulk data, fuses the narrowing into a single read, and only then
loads anything.

```python
render(subsample(source("/data/heptane_302x302x302_uint8.raw"), 2), cmap="green")
```

That is a complete program. It builds an AST; nothing executes until a *sink*
(`render` or `save`) asks for a result. A spec with no sink is a dry run — you
get the plan the interpreter inferred, and not one byte is read.

## Why it is shaped this way

Scientific datasets are large enough that a wrong guess is expensive. Two
principles follow:

**Check before you read.** A missing variable, a mistyped axis, or a region
outside the grid fails against the schema in milliseconds — not after a
twenty-minute load. Cost is estimated before the read, not discovered during it.

**Never guess at bytes.** A format is read by an installed, trusted library, or
by an adapter that has been verified against the real file and frozen, or not at
all. A language model may *propose* a reader; a deterministic verifier decides
whether it runs. Nothing is parsed on faith.

When the data lives on an HPC filesystem, the narrowing travels to the data
rather than the reverse: the interpreter ships the plan — as inert, validated
JSON, never as code — to a reducer running next to the files, and only the
reduced result comes back over the wire.

## The forms

Available in a spec with no imports:

| Form | Purpose |
|---|---|
| `source(uri, positions=None)` | the dataset; a folder is a time series |
| `fields(node, keep)` | keep only these variables |
| `region(node, x=(a,b), …)` | crop to a box |
| `subsample(node, f)` | stride, uniformly or per axis |
| `threshold(node, "var > v")` | keep cells or particles matching a predicate |
| `timesteps(node, start, stop)` | an inclusive range over a series |
| `compress(node, variables, error_bound)` | error-bounded compression |
| `save(node, path)` | **sink** — write out, preserving the source format |
| `render(node, cmap=, opacity=)` | **sink** — serve a viewer to the browser |

Written order is the promise: `threshold` then `subsample` samples the
survivors; the reverse thresholds the sample. How that order is *lowered* into
reads is the interpreter's business.

Full semantics live in [instructions/dsl-reference.md](instructions/dsl-reference.md).

## Install

```bash
pip install -e .              # the engine
pip install -e ".[all]"       # plus the optional format and render stack
```

`yt` and `pygio` (GenericIO) are conda packages in practice and come from your
environment. Missing readers are reported clearly rather than worked around.

## Using it

**From an MCP client** (Claude Code and similar). Point the client at
`mcp_server.py`; it exposes `inspect`, `estimate_render_cost`, `run_pipeline`,
and the two handshake tools. `CLAUDE.md` is the always-loaded index and
`instructions/*.md` are exposed as `vislang://instructions/*` resources.

**From a terminal**, via the `sieve` CLI:

```bash
ln -s "$(pwd)/sieve" ~/.local/bin/sieve
sieve inspect /path/to/data.hdf5    # schema only, no bulk read
sieve estimate spec.py              # the plan and predicted cost
sieve execute spec.py               # run it
```

Both front ends call the same engine in `vislang/server/cli_core.py`.

## Layout

```
vislang/
  dsl/          forms, the AST they build, and its JSON wire format
  formats/      the format boundary — readers, schemas, adapters, bindings
  interpreter/  plan, static-check, fuse, load, narrow, compress, estimate
  output/       the sinks: render to a browser, save to disk
  remote/       ship the narrowing to the data over ssh
  runtime/      caches, tracing, timing, and the spec sandbox
  server/       the MCP server and the sieve CLI
instructions/   the reference docs, also served as MCP resources
examples/       specs to start from
tests/          the suite; run any file directly
```

Each folder has a README with a file-by-file map; each `__init__.py` states the
rule that layer is responsible for. Nothing downstream of `formats/` knows what
a file format is.

## Copyright

© 2025. Triad National Security, LLC. All rights reserved.

This program was produced under U.S. Government contract 89233218CNA000001 for
Los Alamos National Laboratory (LANL), which is operated by Triad National
Security, LLC for the U.S. Department of Energy/National Nuclear Security
Administration. All rights in the program are reserved by Triad National
Security, LLC, and the U.S. Department of Energy/National Nuclear Security
Administration. The Government is granted for itself and others acting on its
behalf a nonexclusive, paid-up, irrevocable worldwide license in this material
to reproduce, prepare derivative works, distribute copies to the public, perform
publicly and display publicly, and to permit others to do so.

Released under the BSD-3 License — see [LICENSE](LICENSE).
