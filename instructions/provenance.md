# Provenance

Every output `save` writes carries a record of how it was made. The record
holds the spec, the input it read, what came out, and which version of Sieve
wrote it. Sieve writes it automatically, and no model writes any part of it.
`VISLANG_PROVENANCE=0` turns records off.

## When to use it

- **"Where did this file come from?"** Call `provenance(filepath)` and
  summarize the answer: the source, the steps, and when and by whom.
- **"Run that again" or "do the same on run 43":**
  `provenance(filepath, spec_only=True)` returns the original spec. Write it to
  `spec.py`, change only what was asked, and run it.
- **A file from another tool:** `provenance` reports that there is no record.
  Say so, and do not reconstruct a history from guesses.
- `provenance` reads the record only. It does not check whether the source has
  changed since. To check that, compare the record's input fingerprint with a
  fresh `inspect` and the file's size and modification time.

## What a record holds

The record is YAML in the format `sieve-provenance/2`:

```yaml
format: sieve-provenance/2
result_summary: ...        # rows x columns, dtypes, sizes in and out
logical:                   # what any correct run of this spec on this input agrees on
  spec: ...                # the spec, verbatim, comments included
  spec_explanation: ...    # each step in plain words, plus a "Values:" verdict
  input:                   # uri, format, fields, fingerprint(s), derived_from
  resolved:                # choices made while running (seed, boxes, tag)
  output:                  # format, fields and dtypes, fingerprint(s), geometry
  sieve:                   # version, commit, uncommitted changes
  libraries: ...
realization:               # how this run went: when, how long, who, where data came from
```

A missing field means it does not apply. Fields are never written as null.

- **The `Values:` line** says which columns are still exact. `compress` changes
  values, and so does `threshold` on an integer grid, because of the float32
  cast. Mention it whenever the scientist is about to rely on exact values.
- **Input fingerprints** hold the size, the modification time, and a hash of
  the first 64 KiB. These are cheap, and they catch truncated or regenerated
  files. They do not catch a same-size rewrite that leaves the head unchanged.
- **Output fingerprints** hash the column values, not the file. The same
  result saved as `.npz` and as `.hdf5` has the same `data_sha256`.
- **Ancestry:** when the source is itself a Sieve output,
  `input.derived_from` names its parent's `data_sha256`. Follow the chain one
  record at a time.
- **Named boxes:** each tag's output has its own record, with the tag in
  `resolved.region_tag`.

## Where it is stored

Inside the file where the format allows: an HDF5 or netCDF attribute (with a
`history` line), VTK field data (ParaView shows it under Information), or an
npz key. Legacy `.vtk`, GenericIO, and records too large for an attribute go in
a companion file named `.<name>.sieve-prov.yaml` beside the output. A series
folder also gets `<dir>/.sieve-provenance.yaml`. `provenance` finds the record
wherever it is.

## Reproducing random subsamples

A fractional `subsample` over points draws rows at random. The seed is
recorded in `resolved.subsample_random_seed`. To reproduce the same rows, run
the spec again with `VISLANG_SAMPLE_SEED` set to that value. Integer strides,
and any subsample of a grid, are deterministic.

There is one known gap: a random subsample served from the remote extent cache
carries the rows of the run that cached it, while the record names the current
run's seed.

## Before sharing a file

Records contain absolute paths, `user@host`, and the spec text.
`VISLANG_PROVENANCE_REDACT=1` drops the user and host and shortens the input
path to its basename. The spec is kept as written, so remind the scientist to
read it before sending the file outside their group.
