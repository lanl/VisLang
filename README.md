# Sieve

Sieve, a declarative domain-specific language (DSL) and runtime system for scientific workflow data provisioning.
A Sieve spec allows users to specify what data to extract and abstracts away the implementation details on how to extract it.
A request is checked against the dataset's schema, different eligible operations are combined in to compute in a one read pass. When the data lives on a remote server,
the operations are performed next to the dataset and only the result is transferred back.

Here is a spec that crops a volume, samples it, and writes the result for a visualization tool to render:

```python
src = source("/abs/path/simulation.hdf5")
core = subsample(region(src, x=(0, 256)), 4)
save(core, "core.vti")
```

Each DSL form describes a step, where only `save` asks for a result. Before any expensive computation, the spec is cheaply validated against the source's schema and metadata.
Sieve is designed to be driven by an AI agent in collaboration with a domain scientist. The spec therefore runs in a sandboxed environment before it touches the data.

## Installation

Run Sieve with Python 3.10 or later. 

```console
$ git clone -b Sieve https://github.com/lanl/VisLang.git sieve
$ cd sieve
$ pip install -e ".[all]"
```

This installs two commands: `sieve`, for the terminal, and `vislang-mcp`, the MCP server for an AI assisted workflow. The  `all` brings in all optional reader and writers (h5py, astropy, pyvista and vtk, netCDF4)
and the browser viewer (k3d). To install less, name only the extras you need, such as `".[hdf5,vtk]"`. The yt reader, and pygio for GenericIO data come from conda.


## Usage with MCP

Sieve registered the MCP server with Claude Code.

```console
$ claude mcp add vislang -- /path/to/env/bin/vislang-mcp
```

Any other MCP client can launch `vislang-mcp` the same way. A request can then be described in plain langauge, and Sieve's MCP tools inspects the data, writes the spec in `spec.py`.
When `inspect` meets a format with no installed library, it returns a `NEEDS_ADAPTER` with the evidence about the file. The agent can also write a custom reader, and Sieve tests it against the data.
Only a reader that passes is kept, the next time `inspect` meets this format, the custom reader is re-used.

## Usage from the terminal

Anything the agent can do, you can do yourself. The `sieve` command runs on the same engine as the MCP server, and its reports read the same way. A typical sessions may follow three steps: look at the data, check the plan, then run it.

```console
$ sieve inspect /abs/path/simulation.hdf5
$ sieve estimate spec.py
$ sieve execute spec.py
```

`inspect` reads only metadata, so it returns quickly even on a very large dataset. It lists the variables, dimensions and attributes a spec can refer to. `estimate` checks the spec against that schema and reports the plan and its cost without reading any data. A misspelled field or an out-of-bounds region shows up here within milliseconds, not after a long load. `execute` runs the spec.

`sieve` exits with a non-zero status whenever a report signals a failure or a hold, so it works in shell scripts and CI jobs. Run `sieve --help` for the full list of commands.

## Writing a spec

A spec borrows Python syntax, conventionally named `spec.py`. It needs no imports. Start from a `source`, narrow it with a chain of forms, and finish with a sink that says what to do with the result.

| Form | What it asks for |
|---|---|
| `source(uri)` | A dataset: a file, a folder of timesteps, or an `ssh://` path |
| `fields(d, ["rho", "temperature"])` | Only these variables |
| `region(d, x=(0, 256), ...)` | One part of the domain: index ranges on a grid, world coordinates on particles |
| `subsample(d, 4)` | Lower resolution, by a stride or a fraction |
| `threshold(d, "rho > 1e12")` | Only the places where a condition holds |
| `timesteps(d, 5, 12)` | An inclusive range of steps from a time series |
| `compress(d, ["rho"], 1e-3)` | Lossy compression within an error bound |
| `save(d, "out.vti")` | Write the result to disk |
| `render(d, cmap="viridis")` | Open the result in the browser viewer |

The order of a spec carries meaning. `threshold` followed by `subsample` samples the cells that passed the threshold, while the reverse order applies the threshold to a sample. Sieve keeps the order you wrote and still chooses its own way to read the data. You describe the result, and Sieve decides how to get it.

A spec without a sink is a dry run: Sieve reports the plan and the predicted cost and reads nothing. It is the cheapest way to try out an idea.

## Working with remote machines

Sieve connects through your system's `ssh`, so the hosts and aliases in `~/.ssh/config` work unchanged. The remote machine needs its own copy of Sieve. List each host in `.vislang/hosts.json` by name or alias, with the location of that copy:

```json
{
  "myhost": {
    "python": "/home/me/miniconda3/envs/sieve/bin/python",
    "repo":   "/home/me/sieve"
  }
}
```

If a host needs an interactive login, open a session once:

```console
$ sieve connect myhost
```

A password dialog opens on your screen, and the password goes directly to OpenSSH. It never passes through Sieve, or through the AI agent when you work over MCP. Every later command reuses the session until it expires, or after eight hours by default.

On an HPC system, Sieve runs the reduction inside a Slurm allocation that you already hold. If no node is allocated, and Sieve is being used through the MCP, a user will be consulted to allocate a node. Otherwise, if a run reports that there is no allocation, start one and run again:

```console
$ ssh myhost 'salloc --no-shell -J vislang -N 1 -p <partition> -t 2:00:00'
```

## Estimating the job cost

Before any bulk read, Sieve estimates the cost of a run from metadata alone. For remote data, it also measures the connection and predicts how long the transfer will take. A run that exceeds the budget stops before any data moves and reports `NEEDS CONFIRM` along with the estimate. You can then narrow the spec, or approve the run as it is:

```console
$ sieve execute spec.py --confirm
```

## Provenance

Every file that `save` writes carries a record of how it was made: the source and its fingerprint, the exact spec, the libraries involved, and the Sieve version. The record goes inside the file when the format has room for it, or in a small `.sieve-prov.yaml` file beside it. When the source is itself a Sieve output, the record links to its parent, so you can trace a chain of derived files back to the original data.

The `provenance` MCP tool reads a record back, and can also return just the spec, ready to run again. Set `VISLANG_PROVENANCE=0` to turn records off.

## Supported formats

| Format | Read | Write | Install |
|---|:-:|:-:|---|
| HDF5 (`.h5`, `.hdf5`, `.hdf`) | ✓ | ✓ | `hdf5` extra |
| netCDF-4 (`.nc`) | ✓ | ✓ | `hdf5` to read, `netcdf` to write |
| VTK (`.vti`, `.vts`, `.vtp`, `.vtk`, `.vtkhdf`) | ✓ | ✓ | `vtk` extra |
| GenericIO (`.gio`) | ✓ | ✓ | pygio, from conda |
| NumPy (`.npz`) | ✓ | ✓ | included |
| FITS (`.fits`, `.fit`, `.fts`) | ✓ | | `fits` extra |
| Raw binary volumes (`.raw`) | ✓ | | included |
| Formats readable by yt | ✓ | | yt, from conda |

`save` chooses the output format from the file extension; converting between formats only takes a change of extension. If the extension isn't recognized, Sieve keeps the source's format.

The raw-volume reader started as a custom reader that an agent wrote and Sieve verified. Any format that `inspect` doesn't recognize can be added the same way, as described in [Usage with MCP](#usage-with-mcp).

## Where to read next

- [examples/](examples/): short, annotated specs covering rendering, saving, time series, remote reduction and dry runs.
- [instructions/](instructions/): guides to the DSL, writing specs, custom readers, HDF5 bindings, rendering and provenance. MCP clients can read these guides directly from the server.