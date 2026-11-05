# Baselines without Sieve (2026-11-05, gpu-server)

The scripts in `../../baseline/`, run on the server by `run_all.sh` through `run_measured.py`:
- `server_s` is the script's wall time on the server;
- `read_bytes` is the process's storage reads (`/proc/self/io`).

Server directory: `/mnt/na1/eecs/research/harp/data/ashrestha/vislang/cs3_baseline`.

## Cold start
Before each script the file's pages were evicted (`posix_fadvise(DONTNEED)`). `fincore` before and
after the eviction:
- stride: 326.6 GiB, then 0 B (`cache_before_stride.txt`);
- block minimum: 16.9 GiB (left by the stride script), then 0 B (`cache_before_blockmin.txt`).

## Results

| Route | Read on the server | `server_s` | Result | Equal to Sieve |
|---|---|---|---|---|
| Stride, every 16th cell (`stride.json`) | 18,119,434,240 B | 304.95 s | `stride16.npy`, 235,929,728 B | yes (`equality.txt`) |
| Block minimum, 16 x 16 x 16 (`blockmin.json`) | 965,194,276,864 B | 6,836.04 s | `blockmin16.npy`, 235,929,728 B | yes (`equality.txt`) |
| Sieve, step 1 stride (`../step1/numbers.json`) | 18,094,653,440 B | 376.36 s | `s1_stride16.vti` | (reference) |
| Sieve, step 4 block minimum (`../step4/numbers.json`) | 943,219,060,736 B (41.6 GB already cached) | 10,344.78 s | `s4_min16.vti` | (reference) |

- **Equality.** The sha256 of each baseline array, transposed to (x, y, z), equals that of Sieve's
  output (`equality.txt`): `8175a30f…` (stride), `cd85c162…` (block minimum).
- **Sieve's estimate against a cold pass.** Sieve estimated step 4's read at 965,109,350,400 B. The
  baseline, which reads the same planes from a cold cache, read 965,194,276,864 B (+0.009 %).
- **Read rate over the whole pass:** baseline about 141 MB/s; Sieve about 91 MB/s.

## Where the time goes: one slab (`decompose.jsonl`; `../../baseline/decompose_slab.py`)
- **What was measured.** One slab of 16 planes (z 753-768, 10,066,329,600 B) is read cold and
  block-reduced in each way.
  - Each run is in its own process, with the file's pages evicted first.
  - Order: base, Sieve, base, Sieve.
- **Sieve's way:** the raw reader's order-F memmap, then `block_reduce` from the server's VisLang at
  `b4a7a96` (`decompose_repo_commit.txt`). Sieve's step 4 reads in slabs of the same 16 planes
  (`_read_coarsened`, `vislang/interpreter/load.py`).

| Way | Read, B | Read, s | Reduce, s |
|---|---|---|---|
| Baseline | 10,066,591,744 | 64.03 / 67.12 | 0.86 / 1.20 |
| Sieve | 10,066,591,744 | 63.45 / 67.40 | 15.96 / 16.03 |

The block minima of the two ways are equal (last line of `decompose.jsonl`).

- **Reading costs the same either way**: the same bytes, at about 150-160 MB/s.
- **Sieve's reduction takes about 15 s more per slab.** `block_reduce` copies the slab twice so that
  every block's cells are reduced in one fixed order (a mean then gives the same bits however the
  data arrived).
- **How much of the gap this explains.** Over 96 slabs, the extra reduction time is about
  1,420-1,460 s, or about 41 % of the 3,509 s difference between the two full passes.
  - The rest is not explained by this measurement.
  - The full passes ran at different times (Sieve 04:13-07:06, baseline 07:32-09:26) on a shared
    server and file system.
- **The same on a laptop, no I/O** (`block_reduce_local.txt`;
  `../../baseline/bench_block_reduce_local.py`): a quarter slab (2560 x 7680 x 16, 2.5 GB in
  memory) takes 3.70 s with `block_reduce` and 0.46 s with the two-step minimum (8.1x); the results
  are equal.

`decompose_v1_invalid.json` is kept as a record and is not used. The first version ran both ways in
one process. Its second (baseline) read found the pages the first memmap still mapped, which
`DONTNEED` does not evict, so it read 0 B. The script was then changed to one process per way.

## Download baseline
Copying the file is 966,367,641,600 B (`stat`, the source's size in `../step4/numbers.json`,
`source_bytes`).
