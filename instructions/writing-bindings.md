# Writing an HDF5 semantic binding (the `BINDING_AVAILABLE` offer)

`inspect` showed a **generic** HDF5 listing (raw dataset paths) and offered
`BINDING_AVAILABLE`. For HDF5 the *container* is known (h5py reads any HDF5) but
the *semantics* are not — which datasets are variables, what the dimensions are,
where the global attributes live. You propose that mapping; `submit_binding`
verifies it against the file's own metadata and freezes it. Binding is **optional
enrichment** — the generic listing already works — but it makes specs read in
physical names (`x`, `density`, `temperature`) instead of raw paths.

This is declarative data, never code: nothing is exec'd, and you never read bulk
data or cut bytes. The file's own schema is the oracle.

## What to do

1. Read the **HDF5 schema tree** in the offer (every dataset path with shape +
   dtype, and each group's attribute keys/values).
2. Propose a binding JSON of the shape below.
3. Call `submit_binding(filepath, binding_json)`.

## The binding shape

```json
{
  "dimensions": {
    "particles": {"source": "<dataset path>", "axis": 0}
  },
  "variables": [
    {"name": "x",  "source": "PartType1/Coordinates", "component": 0, "dim": "particles"},
    {"name": "id", "source": "PartType1/ParticleIDs", "dim": "particles"}
  ],
  "attributes_from": ["/Header"]
}
```

## Rules the verifier (`verify_binding`) enforces

- Only reference **dataset paths and groups that appear in the schema**.
- Each dimension needs a `source` dataset; its `axis` (default 0) must be in range.
- `variables` is a non-empty list; every `name` is a unique non-empty string.
- `component` selects one column of a **2-D** dataset (e.g. `Coordinates (N, 3)`):
  `0=x, 1=y, 2=z`. It is only valid for 2-D sources, and `0 <= component < shape[-1]`.
  Omit it for 1-D datasets.
- A variable with a `dim` must have that dimension's length as its **leading
  axis** (`shape[0] == dimension length`). A 3-D dataset `(nx, ny, nz)` is a grid
  variable — give it a `"grid"` dim (or omit `dim`), and no `component`.
- Every group in `attributes_from` must exist and have attributes.

## Guidance

- Give variables clear **physical names**: `x, y, z, vx, vy, vz, mass, id,
  density, temperature, …`.
- `(N, 3)` coordinate/velocity datasets → three variables via `component` 0/1/2.
- Group attributes (e.g. `/Header`) carry things like `BoxSize`, `Redshift`,
  `Time` — list those groups in `attributes_from`.

## What `submit_binding` does

Runs `verify_binding` against the file's schema. On success the binding is frozen
in `binding_cache/<signature>.json` (keyed by a structure-only schema signature,
so files of the same schema with different sizes reuse it) and future
inspects/runs of that schema get the rich names. On failure it returns the exact
violation — fix the binding and resubmit. A frozen binding is also **re-verified**
against the live file on every load, so a stale cache can never feed wrong data.
