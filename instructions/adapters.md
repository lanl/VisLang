# Adapters & the Trust Ladder

Format dispatch lives in `adapters.py`. Each adapter implements one contract:
`can_handle` (cheap recognition), `inspect` (metadata → `DatasetInfo`), and
`read_array` (one selected numpy array). All orchestration — variable
resolution, narrowing, selection bookkeeping — is universal framework code in
`vislang/interpreter/load.py` + `narrowing.py`; adapters only know how to recognize a file and
fetch one array, applying the narrowing via `indexer`/`apply_selection`.
`DatasetInfo` is the format boundary: once `inspect` fills it, everything
downstream is format-blind.

## The trust ladder
- **Tier 0 — installed, hand-written readers (full trust).** `yt` first (it
  auto-detects most simulation formats with proper fields/units), then magic-byte
  fallbacks: VTK (pyvista), HDF5 (h5py), netCDF-3 (netCDF4), FITS (astropy),
  GenericIO/HACC (pygio), and size-checked raw volumes (numpy memmap). Registry
  order is `[yt, VTK, HDF5, netCDF, FITS, GenericIO, npz, raw]`; first
  `can_handle` wins.
  VTK precedes HDF5 because `.vtkhdf` *is* HDF5 underneath and `HDF5Adapter`
  matches that magic regardless of extension — `VTKAdapter` only claims an
  HDF5-magic file that actually has a `/VTKHDF` root group, so plain HDF5 is
  unaffected. netCDF-4 is HDF5 underneath and stays with `HDF5Adapter`; the
  netCDF reader claims only the classic `CDF` signature (CDF-1, the 64-bit
  offset CDF-2, CDF-5). It lists coordinate variables (1-D, named after their
  dimension) as axes, not fields, and states a uniform geometry from them only
  when every grid axis is evenly spaced and increasing; otherwise the grid is
  placed in its cell indices. There is no netCDF-3 writer: a netCDF-3 source
  saved with no extension falls back to npz (with a note); `.nc` writes
  netCDF-4 when asked for.
- **Tier 1 — no built-in reader, but a trusted library exists.** `get_adapter`
  raises `NeedsAdapterError` and the `inspect` tool returns a `NEEDS_ADAPTER`
  handshake. The **session model** (not a separate API) identifies the format and
  writes a small module with only `inspect` + `read_array` using that library,
  then calls `submit_adapter`, which runs the hand-written conformance gate
  (`llm_adapter.conform_and_freeze`) against the real file and, on pass, **freezes**
  it to `generated_adapters/<ext>.py` and registers it — Tier 0 for future files
  (no model at all). Guide: `vislang://instructions/writing-adapters`.
- **Tier 2 — headerless / handmade raw bytes with no library.** Generally NOT
  supported; raise `UnsupportedFormatError` rather than guess a byte layout. The
  one sanctioned exception is a **declared, verifiable convention**: the
  built-in `RawAdapter` parses `<name>_<nx>x<ny>x<nz>_<dtype>.raw` (the Open
  SciVis Datasets' naming: x fastest, little-endian) and **checks the file's byte
  count equals nx·ny·nz·itemsize** before reading anything; a mismatch raises.
  The filename + size check is the deterministic oracle. The file is
  memory-mapped, so a box or stride reads only the pages it touches.

## The binding path (custom HDF5) — `schema_binding.py`
For HDF5 the *container* is known (h5py reads any HDF5) but the *semantics* are
not. So: fingerprint the schema → cache hit reuses a frozen declarative binding
(no model) → miss shows a generic listing plus a `BINDING_AVAILABLE` offer, and
the **session model** proposes a JSON binding (data, not code) that MUST pass
`verify_binding` (a deterministic oracle against the file's own metadata) via the
`submit_binding` tool before it is frozen. Binding is optional enrichment — the
generic listing works without it. No exec, no run-and-pray. Guide:
`vislang://instructions/writing-bindings`.

## Readers ladder up; writers do not

The ladder above is for **reading**. Writing has no Tier 1: `save()` dispatches
to hand-written writers only (npz, HDF5, GenericIO, VTK), because a generated
writer has no deterministic oracle the way a generated reader does. A format you
can only read through a Tier-1 adapter can still be *converted out of* — past
`DatasetInfo` nothing knows what a file format is — it just cannot be written
back. See `vislang://instructions/soundness` for why it works this way.
