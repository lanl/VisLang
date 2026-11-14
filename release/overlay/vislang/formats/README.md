# Reading your data

Sieve reads HDF5, netCDF-4, VTK, GenericIO, NumPy `.npz`, FITS, raw binary
volumes, and anything yt can open. The library behind each format is an
optional install (see the main [README](../../README.md)).

**Sieve never guesses at a file.** If no installed reader recognizes your file,
`inspect` stops and says so instead of trying to decode it. If you work
through an AI agent, the agent can write a reader for the format using a
library you already have. Sieve tests that reader against your actual file and
keeps it only if it passes. After that, every file of that kind opens without
the agent.

**Friendlier names for HDF5.** Some HDF5 files show only raw paths such as
`PartType1/Coordinates`. An agent can propose a mapping to names like `x`,
`density` and `temperature`. Sieve checks the mapping against the file before
using it, and reuses it for every file with the same layout.

- Custom readers: [`instructions/writing-adapters.md`](../../instructions/writing-adapters.md)
- HDF5 names: [`instructions/writing-bindings.md`](../../instructions/writing-bindings.md)
- Why Sieve works this way: [`instructions/soundness.md`](../../instructions/soundness.md)
