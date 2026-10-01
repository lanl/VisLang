# Provenance

Every output `save()` writes carries a record of how it was made: the spec, the
input it read, what came out, and which Sieve did it. The record is YAML in the
`sieve-provenance/2` format, written automatically. `VISLANG_PROVENANCE=0`
turns it off.

## Reading it back

The record is meant to be read as-is. Where it is a companion file, open it.
Where it is inside a binary file, the MCP `provenance(filepath)` tool returns
it, and `provenance(filepath, spec_only=True)` returns just the spec.
`vislang.runtime.provenance.record_for(path)` gives the same record as a dict,
wherever it is stored.

## Layout

```yaml
format: sieve-provenance/2
result_summary: |          # rows × columns, columns by dtype, sizes in and out
logical:
  spec: |                  # the spec.py text, verbatim (comments included)
  input:                   # uri, format, columns, fingerprint, derived_from
  resolved:                # choices the spec left open, made while running
  output:                  # format, rows (or shape), columns, geometry, fingerprint
  sieve:                   # version, commit, uncommitted_changes
  libraries:               # the libraries that read, computed and wrote
  explanation: |           # the steps in plain words, and a Values verdict
realization:               # at, took_s, by, columns_from, compress, env, run
```

`prov-ex/` holds two worked examples.

**`logical`** holds everything a reproduction needs and a check compares. The
test: would any correct run of this spec on this input agree?

**`realization`** is how this particular run went: when, how long, by whom, how
each column arrived (local read, extent cache, remote read, or a whole-file
fetch), codec details and measured errors, the Python version and platform, and
the run id that keys `.vislang/timings.jsonl`.

**Absent means none.** A field with no value is left out, never written as
null. There is no plan or AST, no output path and no record id. The spec gives
the save path, and records link by `data_sha256`.

## Nothing is written by a model

`result_summary` and `explanation` are rendered from recorded fields, one fixed
template per form, so a checker can rebuild them and compare.

- `fields` names what it dropped.
- `region` gives each axis's bounds: inclusive, in world coordinates, for
  points; index ranges for a grid.
- `threshold` notes when its variable is read only for the test.
- `subsample` shows which rows a stride keeps, or the random fraction.
- `compress` gives the codec, bound and mode, including what `mode="auto"`
  chose.
- `save` says how columns map when it converts formats.
- The final `Values:` line says which columns are still exact. `compress`
  changes values, and so does a `threshold` on an integer grid, which is cast
  to float32 to hold NaN.

Notes about intent belong in the spec's own `#` comments, which `logical.spec`
keeps verbatim.

## Fingerprints

- **Input:** `size`, `mtime` (ISO-8601 with offset), and `head64k_sha256`, a
  hash of the first 64 KiB. This is a cheap stand-in for content identity:
  size alone catches a truncated copy, and the head catches a regenerated file.
  It cannot detect a rewrite in the same second, at the same size, with the
  head unchanged. Remote sources get the same fingerprint from one ssh probe.
  A timeseries records one fingerprint per timestep under `input.timesteps`.
- **Output:** `data_sha256`, a hash of the column values (each column's name,
  dtype, shape and bytes), not of the file. It is the same whatever the output
  format, so the same result saved as `.npz` and `.hdf5` has one hash.
  Companion-file records also carry the file's `size` and `head64k_sha256`,
  to notice when record and file drift apart. An embedded record can't, since a
  file cannot hold a hash of itself.

All hashes are sha256, cut to 16 hex characters.

## Ancestry

When the source is itself a Sieve output, `input.derived_from` is that output's
`data_sha256`. The record does not copy its parent. The parent's own record
holds the next link, so the trail goes as far back as the files you still
have.

## Where it is stored

Inside the file where the container allows, beside it where it does not.

| Output | Mechanism |
|---|---|
| `.hdf5` | root attribute `sieve_provenance`, plus a CF-style `history` line |
| `.nc` | netCDF attribute `sieve_provenance`, plus `history` |
| `.vti` `.vtp` | VTK field data `sieve_provenance` (ASCII rendering); ParaView shows it in Information |
| `.vtkhdf` | HDF5 attribute and FieldData dataset, written after VTK closes the file |
| `.npz` | the reserved key `sieve_provenance` |
| `.vtk` (legacy) | companion file. Its reader scans field data for keywords such as `DIMENSIONS`, so free text is unsafe there |
| GenericIO | companion file. pygio exposes no metadata parameter |

A record too large for an HDF5 or netCDF attribute (about 60 KB, in practice a
very long spec) goes to the companion file. The file keeps a short pointer to
it.

Companion files are `.<name>.sieve-prov.yaml`. The leading dot is functional,
not cosmetic: timeseries discovery matches `#N` anywhere in a filename, so an
undotted companion inside an output folder would be enumerated as a duplicate
timestep. A timeseries folder gets one `<dir>/.sieve-provenance.yaml`, which
lists every step in and out, and each step embeds its own record where its
format allows.

## Reproducibility

A fractional `subsample(node, 0.25)` over points draws rows at random. Each run
resolves one seed, which every random subsample in the spec draws from in spec
order. The seed is recorded as `resolved.subsample_random_seed` and forwarded
to the remote reducer, so a draw made next to the data is the one the record
names. Re-running the whole spec with `VISLANG_SAMPLE_SEED` set to the recorded
value reproduces the same rows. Integer strides, and any subsample of a grid,
never involve the generator.

One known gap: the remote extent catalog does not key on the seed. A random
subsample served from the cache carries the rows of the run that cached it,
while the record names this run's seed.

## Sharing

The record embeds absolute paths, `user@host` and the spec text. Set
`VISLANG_PROVENANCE_REDACT=1` to drop `realization.by` and reduce the input URI
to its basename. The spec is kept as written, so review it before sharing.
