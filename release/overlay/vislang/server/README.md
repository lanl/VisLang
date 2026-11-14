# The `sieve` command and the MCP server

You can use Sieve in two ways, and both behave identically:

- **`sieve`, in your terminal.** `inspect` a dataset, `estimate` a spec,
  `execute` it, or `connect` to a remote host. Run `sieve --help` for the full
  list. The command exits with a non-zero status when a run fails or is waiting
  on you, so it works in scripts.
- **`vislang-mcp`, for an AI agent.** Register it with Claude Code or any MCP
  client, and describe what you want in plain language. The agent inspects your
  data, writes the spec, and runs it through the same tools.

When a run needs your decision, both ways stop and ask instead of going ahead:

| You see | What it means |
|---|---|
| `NEEDS CONFIRM` | The run is over budget. Narrow the spec, or approve it with `--confirm` |
| `NEEDS SESSION` | The host needs a login. Run `sieve connect <host>` |
| `NEEDS ALLOCATION` | There is no Slurm allocation. Start the proposed one, or approve fetching the whole file |
| `NEEDS_ADAPTER` | No reader recognizes the file. An agent can write one for you |

Setup: the main [README](../../README.md)
