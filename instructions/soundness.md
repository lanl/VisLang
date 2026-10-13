# Soundness

Sieve gives scientists data they will draw conclusions from, so a plausible
answer is not enough. Every rule in the other guides follows from one
principle:

> **You propose. A deterministic check decides. Only what passed is kept, and
> the run path never depends on a model.**

## What you may do

- Identify a file's format from evidence.
- Wire up an installed, trusted library to read it (`writing-adapters.md`).
- Propose declarative data, such as a spec or an HDF5 binding, that a check can
  compare against the file (`writing-bindings.md`).

## What you may not do

- Read or interpret data bytes yourself, or hand-parse a byte layout.
- Fill a missing value with a guess: a box size, units, a timestep, a
  coordinate system. Say what is missing and ask.
- Treat anything you produced as trusted before its check has passed.
- Get around a check, for example by loosening a spec until an error goes away
  or by resubmitting a reader without fixing the reported violation.

## The trust ladder for readers

1. **Installed readers** (yt, VTK, HDF5, FITS, GenericIO, npz) are tried first,
   in that order.
2. **Saved generated readers**: readers you or another session wrote, which
   passed `submit_adapter` and were kept.
3. **Nothing matched:** `inspect` returns `NEEDS_ADAPTER` and you write one.

Headerless raw bytes are refused unless a declared convention can be checked,
such as shape and dtype in the filename, checked against the file size.

## Readers can be generated; writers cannot

Whether a library reads a file is a fact that one run against the real file can
settle. Whether a generated writer produced a faithful file has no equally
cheap check. That is why `save` only uses built-in writers. A format read
through a generated reader can be converted out of, but never written into.

## Checks happen before data moves

- **A spec** is checked against the source's schema before any bulk read.
- **Cost** is estimated from metadata before any bulk read or transfer, and an
  over-budget run waits for the scientist.
- **Readers and bindings** are settled at authoring time, through `inspect`.
  The run path only uses what was already saved, and fails if something is
  missing.

When a check fails, the message names the real problem. Fix that problem, or
take the question to the scientist.
