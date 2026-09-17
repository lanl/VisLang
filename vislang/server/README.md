# `server/` — the two front doors

| File | What it holds |
|---|---|
| `cli_core.py` | the shared engine both front ends call — all the logic lives here |
| `mcp_server.py` | the MCP tool surface, driven by an LLM session |
| `cli.py` | the `sieve` terminal CLI, driven by a human |

Neither front end holds logic of its own. `mcp_server.py` contributes the tool
docstrings a model reads; `cli.py` contributes argument parsing; `cli_core.py`
does the work. A capability added to one and not the other is a bug.

Both are launched through thin scripts at the repo root (`mcp_server.py`,
`cli.py`) so that `.mcp.json` and the `sieve` symlink point at stable paths.

## The handshake

VisLang's LLM judgment runs on the **session model**, never a separate API key.
An MCP tool cannot call back into the session that invoked it, so anything
requiring judgment is split in two:

1. a tool returns **evidence plus a request** — `inspect` answers with
   `NEEDS_ADAPTER` for an unknown format, or appends `BINDING_AVAILABLE` for an
   HDF5 file it can only read generically;
2. the model reasons, reading the relevant `instructions/*.md`, and calls
   `submit_adapter` or `submit_binding`;
3. a **deterministic verifier** checks the proposal against the real file and
   freezes it, or rejects it.

The model proposes; the verifier decides. These artifacts are established at
authoring time only — the run path reads frozen results and errors when one is
missing, so nothing is generated mid-run.

Reference: [`instructions/soundness.md`](../../instructions/soundness.md)
