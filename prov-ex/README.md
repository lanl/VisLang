# Provenance record examples

Hand-written records exploring a more readable provenance format,
`sieve-provenance/2`. Nothing writes this format yet. Today's records are
JSON in the layout of `vts56.gio.sieve-prov.json` (schema 1).

- `vts56.gio.sieve-prov.yaml` is the same `vts56.gio` record, redesigned.
  Its realization section imagines a partial cache hit.
- `halo_env.vtp.sieve-prov.yaml` extends the halo-neighbourhood spec from
  commit 83b535e with `threshold`, a random `subsample`, `compress` and a
  `.vtp` save, to show how the more complex steps read.

Each file opens with notes listing which values are made up or assumed.

## Why YAML

- The spec fits in a `|` block: exact text, comments and blank lines kept.
- Key order is ours to choose. Every mainstream loader keeps document order;
  PyYAML's `dump` needs `sort_keys=False`.
- A YAML 1.2 reader also reads today's JSON records.
- Costs: Python has no YAML library built in, and unquoted values can
  change type. Timestamps load as dates and all-digit hex as integers, and
  a comma inside `{…}` splits a value. A writer should quote every string.

## Two parts

**`logical`** holds everything needed to reproduce the output and check the
result:

- the spec
- the input's URI, format, columns and fingerprint
- the output's format, rows, columns and fingerprint
- `resolved`: choices the spec left open, made while running
- the Sieve build and the libraries that read, compute and write
- `explanation`

**`realization`** holds how this particular run went, which reproduction
doesn't need: when, how long, by whom, which columns came from the cache or
a remote read, codec details and measured errors, the Python version and
platform, and the run id for the local logs.

The test for `logical`: would any correct run of this spec on this input
agree? The fingerprints and the build pass. The cache and where things ran
don't. Versions are in `logical` because the spec means what that build's
forms and readers make of it.

## Keep only what nothing else gives

- **Absent means none.** No `null` fields, no empty lists.
- **No plan or AST.** For a straight-line spec it only restates the spec,
  and Sieve can rebuild it without reading any data.
- **The URI once**, in full under `input`, besides the spec's own split-up
  copy. The spec shows what was written; `input.uri` is the copyable form.
- **No output path, requested path or record id.** The spec's `save` path
  gives the output; records link to each other by the output's
  `data_sha256`.
- **No per-step row counts.** They neither reproduce nor check anything.
  They help diagnose a surprising result, and the local trace log already
  has them.
- **No schema comments in the records.** Explaining what each key means
  belongs in `instructions/provenance.md`, not repeated in every record.

## Fingerprints

- **Input:** `size`, `mtime`, and a hash of the first 64 KiB. This is a
  cheap stand-in for content identity. Size alone catches the commonest
  damage, a truncated copy.
- **Output:** `data_sha256`, a hash of the column values rather than the
  file's bytes, so it is the same whatever the file format or writer. A
  rerun checks against it, and it is what other records link by. Sidecar
  records also carry the file's `size` and head hash, to notice when record
  and file drift apart. An embedded record can't, since a file can't
  contain a hash of itself.
- One algorithm and one length everywhere: sha256, cut to 16 hex characters.
  That is plenty for catching accidental change.

## `resolved`

What the spec left open, recorded because a rerun needs it:

- `subsample_random_seed`: one seed per run. Every random subsample in the
  spec draws from the same generator, in spec order.
- `compress_mode` per variable: `mode="auto"` picks absolute or relative
  for each variable, and a bound means nothing without its mode.

## Human-readable parts

- **`result_summary`** at the top says what came out that the spec alone
  can't tell you: rows, columns by type, sizes in and out. It is plain text
  for people; tools compare fingerprints.
- **`explanation`** is generated from the fields by one template per form,
  so a checker can rebuild it and compare. It has only:
  - **Steps:** each form in spec order with what it does in plain words.
    `fields` names what it dropped, `threshold` notes when its variable is
    read only for the test, a stride shows which rows it keeps,
    `compress` gives codec, bound and mode, and a converting `save` says
    how columns map into the new format.
  - **Values:** a one-line verdict on which columns are still exact.
    `compress` changes values, and so does `threshold` on an integer grid,
    which is cast to float32 to hold NaN.

  We tried and dropped: a narrative paragraph, which restated the steps
  less clearly; Source, Result and Made-with sections, which the YAML
  already shows readably; and caveats or reproduction instructions, which
  read as out of place in a description.
- An unchecked, model-written note about intent is possible, but should go
  in its own field marked as unverified.

## Ideas not shown in these files

- **Specs with loops.** Record the authored spec plus an *elaborated* spec
  for this output: loop-free, all arguments literal, one form per line, and
  itself a valid spec, so running it must rebuild the same plan. Add the
  sink's line and loop variables (`staged_from`), and the run's other
  outputs by path and fingerprint.
- **Cached data reused after more work.** `columns_from` groups say how each
  output column arrived, together covering every column once. A cache group
  gives `cached`, `then` and `yields`, and `yields` must match the remote
  group's `read` so the rows line up. That composition assumes strides start
  at row 0. A value cut such as `threshold` would make the groups depend on
  each other, since all of them must keep the same rows.

## Open questions

- `data_sha256` isn't computed today, and linking records by it depends on
  it existing.
- Remote records don't record the fused read (`lowered`) today.
- `uncommitted_changes: true` means the commit doesn't identify the build.
  A hash of the diff would at least show whether two records share a build.
- Unchecked assumptions in the halo example: whether a point `region`
  includes its edges, how x, y, z map into a `.vtp`, and whether SPERR's
  absolute bound is guaranteed.
