# Remote data

A source written as `ssh://[user@]host/path` or `user@host:/path` stays on the
remote machine. The spec looks the same as a local one. What changes is where
the work happens.

## What a remote run does

- **With narrowing** (`fields`, `region`, `subsample`, `threshold`), Sieve sends
  the narrowing to a reducer that runs next to the files and brings back only
  the result. The sink (`save` or `render`) always runs locally, so every
  output gets its provenance record on this machine.
- **With no narrowing**, the whole file has to come across the network, so
  Sieve copies it into `.vislang/downloads/` and works locally. Tell the
  scientist before running a spec like this on a large file, and suggest a
  narrowing first.
- **A remote folder** is a time series, exactly as a local one is. Each step is
  reduced next to the data.
- **Repeated cuts are cached.** A local extent cache serves data already
  fetched, and fetches only what is missing.

`VISLANG_REMOTE` controls placement. `auto` (the default) reduces remotely when
possible and falls back to a whole-file copy. `off` always copies. `force`
fails instead of falling back, which helps when finding out why a remote reduce
did not happen.

## The remote needs its own Sieve

The reducer runs on the remote machine with that machine's own Sieve checkout
and Python. Both are set per host in `.vislang/hosts.json`, keyed by ssh alias
or hostname:

```json
{ "myhost": { "python": "/path/to/env/bin/python", "repo": "/path/to/sieve", "tmp": "/tmp" } }
```

`VISLANG_REMOTE_PYTHON`, `VISLANG_REMOTE_REPO` and `VISLANG_REMOTE_TMP`
override these values. If the repo is not configured, the run says so. Ask the
scientist for the path rather than guessing one.

## Sessions and passwords

Sieve uses the system `ssh`, so `~/.ssh/config` aliases, keys and jump hosts
all work.

- **`NEEDS_SESSION`** (from `inspect`) or **`Status: NEEDS SESSION`** (from a
  run): the host needs an interactive login. Call `connect(host)`. A password
  dialog opens on the scientist's screen, and the answer goes straight to ssh.
  Then repeat the step that was held.
- **Never ask for a password, and never accept one.** If the scientist types a
  secret into the chat, tell them it is not needed and that `connect` will
  prompt them privately. Never put a secret in a spec or a tool argument.
- **`UNREACHABLE`:** there is no network route to the host. That is a VPN or
  hostname problem, and a password cannot fix it. Say so, and do not offer to
  authenticate.
- A session lasts about 8 hours (`VISLANG_SESSION_PERSIST`) and serves every
  later inspect and run. Connect once per host, not once per spec.
  `disconnect(host)` closes it early.

## Slurm allocations

When `VISLANG_SRUN_JOBID` is set (to a job id, or `auto` to find one), each
remote reduce runs as a step inside a Slurm allocation that the scientist
already holds. Sieve never creates an allocation and never submits a batch
job.

**`Status: NEEDS ALLOCATION`** means no matching allocation is running. Nothing
was read. Then:

1. Show the scientist the proposed command from the report, for example:
   ```console
   ssh <host> 'salloc --no-shell -J vislang -N 1 -p <partition> -t 00:30:00'
   ```
2. Ask them to confirm the partition and walltime. An allocation spends
   shared HPC time that is charged to them. Do not change the partition on a
   guess.
3. Run it only after they approve. In this repo, a hook also asks before any
   `salloc` command.
4. Once the allocation is RUNNING, run the spec again. Every later step and
   timestep reuses it.

The scientist can also choose to go ahead without an allocation. Re-running
with `confirm=True` skips the reduce and copies the whole file instead. Offer
this only together with the size of that copy, and only use it when they pick
it.

Keep `-J` equal to `VISLANG_SRUN_NAME` when that variable is set, and keep
`--no-shell`. Otherwise Sieve cannot find the allocation.

## Cost

For remote sources, the cost estimate includes a transfer-time range measured
over the actual link, alongside the byte count. Over-budget runs are held the
same way as local ones (`NEEDS CONFIRM`). Show the estimate and let the
scientist choose.
