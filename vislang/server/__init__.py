"""Two front doors onto one implementation.

    mcp_server.py   the MCP server, driven by an LLM session
    cli.py          the `sieve` terminal CLI, driven by a human
    cli_core.py     the shared engine both of them call

The split matters: a tool added to one front end but not the other is a bug, so
neither file holds logic of its own. `mcp_server.py` contributes the tool
docstrings the model reads, `cli.py` contributes argument parsing, and
`cli_core.py` does the work.

VisLang's LLM judgment runs on the *session model*, never a separate API. An MCP
tool cannot call back into that session, so anything needing judgment is a
**handshake**: the tool returns evidence plus a request, the model reasons and
calls a `submit_*` tool, and a deterministic verifier decides whether the result
is allowed to be frozen. See `instructions/soundness.md`.
"""
