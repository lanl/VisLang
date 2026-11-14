# Writing an HDF5 binding (the `BINDING_AVAILABLE` offer)

Any HDF5 file can be read, but a generic one shows up as raw dataset paths:
`PartType1/Coordinates`, `/grid/data_0`. When `inspect` appends
`BINDING_AVAILABLE`, you may propose a **binding**, which maps those paths to
physical variables and dimensions. Specs can then say `x`, `density` and
`temperature`.

A binding is optional. The generic listing already works. Offer one when the
scientist will write more than one spec against this layout, or when the raw
paths make the spec hard to read.

A binding is declarative JSON, never code. You do not read data. The check
compares every claim against the file's own schema.

## Steps

1. Read the schema tree in the offer: every dataset with its shape and dtype,
   and each group's attributes.
2. Write the binding.
3. Call `submit_binding(filepath, binding_json)`. If it is rejected, fix the
   named violation and submit again.
4. Repeat `inspect`. The listing now uses your names.

## Shape

```json
{
  "dimensions": {
    "particles": {"source": "PartType1/ParticleIDs", "axis": 0}
  },
  "variables": [
    {"name": "x",  "source": "PartType1/Coordinates", "component": 0, "dim": "particles"},
    {"name": "y",  "source": "PartType1/Coordinates", "component": 1, "dim": "particles"},
    {"name": "z",  "source": "PartType1/Coordinates", "component": 2, "dim": "particles"},
    {"name": "id", "source": "PartType1/ParticleIDs", "dim": "particles"}
  ],
  "attributes_from": ["/Header"]
}
```

## Rules the check enforces

- Every `source` and every group must exist in the schema tree.
- A dimension needs a `source`, and its `axis` (default 0) must be within that
  dataset's rank.
- `variables` is non-empty, and every `name` is unique.
- `component` picks one column of a **2-D** dataset, such as `(N, 3)`. It must
  be smaller than the last axis. Leave it out for 1-D datasets.
- A variable with a `dim` must have that dimension's length as its leading
  axis.
- A 3-D dataset is a grid variable: give it a `"grid"` dim, or none, and no
  `component`.
- Each group in `attributes_from` must have attributes.

## Naming

- Use the names a scientist in that field would type: `x y z`, `vx vy vz`,
  `mass`, `id`, `density`, `temperature`.
- Split an `(N, 3)` coordinate or velocity dataset into three variables with
  `component` 0, 1 and 2.
- Header groups that hold `BoxSize`, `Redshift` or `Time` belong in
  `attributes_from`.
- If the meaning of a dataset is not clear from its name, shape and
  attributes, leave it out and ask. A binding that renames the wrong dataset
  passes the check but misleads every spec after it.

## After it passes

The binding is saved under `.vislang/bindings/`, keyed by the schema's
structure but not its sizes. Every file with the same layout gets the names
automatically, including larger or smaller runs. Each load checks the binding
against the file again, so a stale binding cannot feed wrong data.
`VISLANG_NO_BINDING=1` turns bindings off.
