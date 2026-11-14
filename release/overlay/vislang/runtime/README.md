# Safety and records

**Specs run in a sandbox.** A spec is short code, and an AI agent may have
written it, so Sieve runs it in a restricted interpreter. There, a spec can
only build the steps you asked for. It can't open files, import modules, or
reach the rest of your system.

**Every output carries its history.** Each file `save` writes contains a record
of how it was made: the source and its fingerprint, the exact spec, the
libraries involved, and the version of Sieve. The record stays with the file
when you copy or share it, and if you made the file from another Sieve output,
the record links back to that parent.

**What Sieve keeps on disk.** Sieve keeps everything it stores in `.vislang/`
at the top of the repository. Set `VISLANG_HOME` to put it somewhere else.

| Path | Contents |
|---|---|
| `hosts.json` | Settings for your remote machines |
| `bindings/` | Saved HDF5 name mappings |
| `cache/` | Data already fetched from remote machines |
| `downloads/` | Whole files copied from remote machines |
| `trace.log` | A readable account of each run |
| `timings.jsonl` | How long each run's phases took, and how much data each one moved |

If a run surprises you, `trace.log` is the first place to look.

Records in detail: [`instructions/provenance.md`](../../instructions/provenance.md)
