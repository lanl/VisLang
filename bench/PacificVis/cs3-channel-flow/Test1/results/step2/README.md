# Step 2: the ground-truth area at full resolution (2026-11-05, run c39a639ab225, Sieve commit 9edd5e6)

Spec (`spec_as_run.py`): `region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65))`, 1024 x 1024 x 64
cells of the lower wall (y+ 0.07 to 68), placed at the domain centre and aligned to the 16-cell blocks
before any overview was seen (DESIGN.md). Saved as `outputs/s2_truth.vti` (not in git).

## Bytes and server time (`numbers.json`, from `timing_record.json`)

| | Estimated | Measured |
|---|---|---|
| Read on the server | **536,870,912 B** (`estimate.txt`, `sieve estimate` before the run) | **5,259,358,208 B** (9.80x) |
| Over the wire | 536,870,912 B | 536,871,998 B (+1,086 B placement) |
| Remote reduce time (`server_s`) | n/a | **60.93 s** |

- **The run's own estimate was wrong, and this exposed a Sieve bug.** The run logged 268,435,456 B
  (256 MiB) for both server read and wire, with the note "dtype not fully known — bytes assume
  4 B/element". The run took the file's schema from the extent cache, which did not keep element
  sizes. Fixed in Sieve commit `4ca9724`: the cache keeps them, with a regression test in
  `tests/test_server_read.py`. The pre-run `sieve estimate` used a live inspect and is correct; it
  is the estimate reported. Step 1 was unaffected (its first run read the schema live).
- Measured read: the cut reads 64 x 1024 = 65,536 pieces of 8 KiB (1024 float64 along x), each in a
  different 80 KiB row. The NFS client reads ahead of each, hence ~10x.
- Page cache before (`page_cache_before.txt`): 22,253,010,944 B resident, mostly step 1's read. Step 1
  read rows at z = 1, 17, 33, 49 for every 16th y. Some pages of this area may already have been in
  memory, which would lower the measured read. **This step's server read is therefore not a cold
  read.** It is reported as measured.

## The output in ParaView (`paraview_check.txt`)

`vtkImageData` 1024 x 1024 x 64, origin (4608, 3328, 1), spacing 1: in the source's cells. Array `u`
(double), range [-0.076063, 1.075359]. Provenance embedded.
