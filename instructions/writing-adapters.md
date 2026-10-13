# Writing a reader (the `NEEDS_ADAPTER` handshake)

`inspect` returned `NEEDS_ADAPTER`: no installed reader recognizes the file.
You write a small reader module, and `submit_adapter` tests it against the real
file. Only a reader that passes is kept, and from then on it serves every file
with that extension without your help.

## Steps

1. **Identify the format** from the evidence in the handshake: name, extension,
   size, and a hex dump of the head and tail. Magic bytes and embedded strings
   usually settle it.
2. **Choose an installed library** that reads it, such as `netCDF4`, `h5py`,
   `zarr`, `pyarrow`, `scipy.io`, `astropy.io.fits`, `asdf`, `pygio` or `numpy`.
   If no library fits, stop and tell the scientist. Do not hand-parse bytes.
3. **Tell the scientist** what you found and that you are writing a reader for
   it, which will be saved for later files of this kind.
4. **Write the module** to the contract below and call
   `submit_adapter(filepath, module_code)`.
5. **If it is rejected**, read the violation, fix the module, and submit again.
   Then repeat the original `inspect`.

## The contract

```python
FILETYPE = "NetCDF3"                 # short format name
EXTENSIONS = [".nc3"]                # extensions this reader claims

def inspect(filepath):
    """Metadata only. Avoid bulk reads where the library allows."""
    import netCDF4
    ...
    return {
        "filetype": FILETYPE,
        "variables": ["density", "temperature"],  # real data fields, non-empty
        "dimensions": {"grid": (nx, ny, nz)},     # or {"particles": n}
        "attributes": {"time": 1.25, "units": "cgs"},
        # Optional, when a variable's name is not how the library addresses it:
        # "variable_locations": {"density": ("HDU", 1)},
    }

def read_array(filepath, location):
    """Return the full numpy array for one variable. No slicing."""
    import netCDF4
    ...
```

- `location` is the variable name, or its `variable_locations` entry.
- `read_array` returns the **whole** array. Sieve does all cropping, striding
  and thresholding itself. Do not subsample or select here, and do not write a
  `load` function.
- Equal-length 1-D columns are `{"particles": n}`. A 3-D array is
  `{"grid": (nx, ny, nz)}`.
- A scalar stored in the file (a box size, a redshift) goes in `attributes`,
  not `variables`.
- Import the library inside the functions.
- Attribute values must be plain JSON values. Convert numpy scalars with
  `.item()`.

## What the check verifies, and what it cannot

`submit_adapter` runs the module, calls `inspect` on the real file, checks the
structure of the result, and reads the first variable to make sure it returns
a non-empty array that is not 0-d.

It cannot tell whether your metadata is **true**. That part is up to you:

- Read every value from the file. Never fill a gap with a plausible constant.
  If something required is missing, `raise ValueError` with a clear message.
- List the real data fields, not just the first one that happens to work.
- Headerless raw data is accepted only under a declared convention that can be
  checked. See `generated_adapters/raw.py`: the shape and dtype come from the
  filename (`name_302x302x302_uint8.raw`), and it refuses a file whose size
  does not match.

## Keep readers inert

A reader is ordinary Python that runs inside the Sieve server with full access
to the machine. It is not sandboxed the way specs are. Each time the server
starts, it loads every saved reader. So a reader must only read:

- no network access, subprocesses or file writes;
- nothing outside `filepath`;
- no state kept at import time beyond constants.

## After it passes

The module is saved as `vislang/formats/generated_adapters/<ext>.py` and claims
files by extension. Submitting a reader for an extension that already has one
replaces the old one. Installed readers are always tried first, so a generated
reader never takes over a format that Sieve already reads.

Writers are never generated. A format read through your reader can be saved
to any built-in format (HDF5, VTK, `.npz`, and so on), but not back into its
own format.
