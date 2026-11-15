# Saving and viewing results

**`save`** writes your result to disk. The file extension chooses the format,
so converting is as simple as changing the extension:

```python
save(core, "/abs/path/out/core.vti")     # open in ParaView
save(core, "/abs/path/out/core.hdf5")    # keep as HDF5
```

- Supported outputs: HDF5, netCDF-4, VTK (`.vti`, `.vts`, `.vtp`, `.vtk`,
  `.vtkhdf`), NumPy `.npz`, and GenericIO.
- A cropped or strided grid keeps its correct position in space.
- A time series is written as one file per step. VTK series also get a `.pvd`
  index, so ParaView opens them as a single animated dataset.
- Every saved file records how it was made: the source, the spec, and the
  version of Sieve.

**`render`** opens your result in the browser as a 3-D volume or a point
cloud. It needs no graphics card or display, so it also works on a remote
compute node through an SSH tunnel. Running the spec again refreshes the page
in place.

- Options and tips: [`instructions/rendering.md`](../../instructions/rendering.md)
- Output formats in detail: [`instructions/dsl-reference.md`](../../instructions/dsl-reference.md)
- Provenance records: [`instructions/provenance.md`](../../instructions/provenance.md)
