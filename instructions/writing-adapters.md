# Writing a format adapter (the `NEEDS_ADAPTER` handshake)

`inspect` returned `NEEDS_ADAPTER`: no installed reader recognizes this file, so
**you** write a small reader module and submit it. There is no separate model —
you are the generator; `submit_adapter` is the deterministic verifier. Trust
comes from the conformance run against the real file, never from your say-so.

## What to do

1. Read the **file evidence** in the handshake (extension, size, hex head/tail).
   Identify the format.
2. Pick the appropriate **already-installed** Python reader library for it
   (e.g. `numpy`, `netCDF4`, `pyarrow`, `h5py`, `scipy.io`, `astropy.io.fits`,
   `zarr`, `asdf`, `pygio`). **Never hand-parse raw bytes** — wire up the library.
3. Write a self-contained module with **exactly** this contract.
4. Call `submit_adapter(filepath, module_code)`.

## The contract — two functions, nothing else

```python
FILETYPE = "<short format name>"        # e.g. "NetCDF"
EXTENSIONS = ["<.ext>", ...]            # extensions this format uses

def inspect(filepath):
    """Metadata only — no bulk read where the library can avoid it."""
    return {
        "filetype": FILETYPE,
        "variables": [<field/column/dataset names>],   # non-empty, real data fields
        "dimensions": {...},   # {"particles": N} or {"grid": (nx, ny, nz)}
        "attributes": {...},   # JSON-friendly scalars/metadata (units, times, …)
        # OPTIONAL — only when a variable's name differs from how the library
        # addresses it (e.g. a column index or an HDU tuple):
        # "variable_locations": {"<variable>": <location token>},
    }

def read_array(filepath, location):
    """Return the ONE full numpy array for this variable/location.
    NO slicing, NO subsetting — the framework owns all selection. `location`
    is the variable name, or its variable_locations token if you supplied one."""
    ...
```

**Do NOT** write `load()`, and do NOT subsample or select inside `read_array`.
Variable resolution, region/subsample/threshold, and selection bookkeeping are
universal framework code (`my_load.py` + `narrowing.py`), shared by every
adapter. `DatasetInfo` is the format boundary: once `inspect` fills it,
everything downstream is format-blind.

## Rules the verifier enforces

- Import the reader library **inside** the functions; assume it is installed.
- `variables` must be **non-empty** and contain the real data fields. A scalar
  stored in the file (e.g. `box_size`) belongs in `attributes`, not `variables`.
- Particle-like 1-D columns of equal length N → `dimensions = {"particles": N}`.
  A 3-D array `(nx, ny, nz)` → `dimensions = {"grid": (nx, ny, nz)}`.
- All metadata values must be JSON-serializable (`.item()` / `float()` numpy scalars).
- **Read real metadata from the file; never invent values.** If required
  metadata is genuinely missing, `raise ValueError(...)` with a clear message —
  never fall back to a hardcoded guess.
- `variable_locations`, if present, may only have keys that are in `variables`.

## What `submit_adapter` checks (the trust step)

1. The module exec's and defines `inspect` + `read_array`.
2. `inspect(filepath)` runs on the real file and its result validates structurally.
3. `read_array(<first variable>)` returns a **non-empty, non-0-d** numpy array.

On success it is frozen to `generated_adapters/<ext>.py` and registered — future
files of this format skip the handshake entirely (Tier 0). On failure you get the
violation + traceback: fix the module and call `submit_adapter` again.

## Worked examples

Two frozen adapters show the shape end-to-end:
- `generated_adapters/npz.py` — numpy `.npz`, splits arrays into variables.
- `generated_adapters/raw.py` — headerless `<name>_<nx>x<ny>x<nz>_<dtype>.raw`,
  parses shape/dtype from the filename and **verifies the byte count** before
  trusting it (the sanctioned raw-bytes exception: a declared, checkable convention).
