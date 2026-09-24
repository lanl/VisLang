# Provenance

Every output `save()` writes carries a record of how it was made: the source it
came from, the transformation applied, and which Sieve did it. Written
automatically; `VISLANG_PROVENANCE=0` turns it off.

## Reading it back

```bash
sieve provenance roi.nc            # the record, with a live source check
sieve provenance roi.nc --json     # the raw record
sieve provenance roi.nc --spec     # just the spec that produced it
sieve rerun roi.nc --out roi2.nc   # re-execute that spec
```

`sieve provenance` finds the record wherever it lives — an attribute inside the
file, or the companion file beside it — so you never have to know which.

## What it holds

`producer` (Sieve version, git commit, and the versions of the libraries that
touched the data) · `run` (spec path and hash) · `source` (URI, schema, and an
identity that detects whether the file has changed since) · `transform` (the
spec text, the AST, the *lowered* narrowing the interpreter actually executed,
and the sampling seed) · `output` (what was written, and whether the requested
format had to degrade) · `variables` (per-variable shape, source variable, and
whether it was read or served from cache) · `derived_from` (the ancestry chain,
so re-narrowing an output keeps the trail back to the original).

`transform.plan` is what you asked for; `transform.lowered` is what the
interpreter did with it — which cuts were fused into the read and which ran
after. The gap between them is the reduction-fusion behaviour, recorded per
artifact.

## Where it is stored

Inside the file where the container allows, beside it where it does not.

| Output | Mechanism |
|---|---|
| `.nc` | netCDF attributes, plus a conventional `history` entry |
| `.hdf5` | HDF5 attributes, on the root and per variable |
| `.vti` `.vtp` `.vtk` | VTK field data — ParaView shows it in Information |
| `.vtkhdf` | HDF5 attribute, written after VTK closes the file |
| `.npz` | a reserved `sieve_`-prefixed key |
| GenericIO | companion file — pygio exposes no metadata parameter |

Companion files are pretty-printed JSON at `.<name>.sieve-prov.json`. The
leading dot is functional, not cosmetic: timeseries discovery matches `#N`
anywhere in a filename, so an undotted companion inside an output folder would
be enumerated as a duplicate timestep. Timeseries folders get one
`.sieve-provenance.json` for the folder, plus embedded records per step.

## Source identity

By default: size, whole-second mtime, and MD5 over the first and last 64 KiB —
metadata-scale, and enough to catch a file being regenerated, replaced,
truncated or extended. It cannot detect a rewrite in the same second, at the
same size, leaving head and tail unchanged. The record states that caveat in its
own text, so the limitation travels with the artifact.

`--hash` additionally records a full SHA-256. That detects silent corruption and
gives an identity that is the same for the same bytes anywhere — but it reads
the whole file, which is I/O-bound (minutes for a multi-GB file on a slow
mount), so it is opt-in.

`sieve rerun` refuses when the source no longer matches, naming the field that
changed. `--force` proceeds and invalidates any cached extents held against the
old bytes.

## Reproducibility

A fractional `subsample(node, 0.1)` draws rows at random. The generator is
seeded per run and the seed is recorded, so `rerun` reproduces the same rows
rather than a statistically similar sample. `VISLANG_SAMPLE_SEED` pins it
directly. Integer strides never involve the RNG.

## Sharing

The record embeds absolute paths, hostname, username and the spec text. Set
`VISLANG_PROVENANCE_REDACT=1` to drop the identifying fields and reduce paths to
basenames before sharing an artifact.
