# Step 0: verify the data (metadata and small reads on gpu-server only)

Date: 2026-11-04. Nothing crossed the wire except the printed text below. Reads on the server:
about 0.25 GB (axis profile), 2 GiB sequential plus 157 MB of strided rows (read-rate probe).

| File | What it shows |
|---|---|
| `axis_profile.txt` | Per level of the 1536 axis: mean and std of the value over 16 whole x-rows. 0 at both outer planes, 1.103 at the centre, std peak 0.127 near both walls, symmetric. Along the 7680 axis the std is flat. **The 1536 axis is wall-normal and the quantity is streamwise velocity u (units of bulk velocity), not pressure.** Script: `../../scripts/axis_profile.py` |
| `yplus_check.txt` | The JHTDB wall-normal grid (`../../data/channel5200-y.txt`) in wall units, checked against the data: at z = 8 the grid gives y+ = 2.74 and the measured mean gives U+ = 2.75 (viscous sublayer, U+ = y+). Block rows of 16 cells from z = 1: the first spans y+ 0.07–8.12. Script: `../../scripts/yplus_check.py` |
| `read_rate.txt` | Sequential cold NFS read: 2 GiB in 14.47 s (148 MB/s). Strided memory-map copy, 1920 rows of 80 KiB (157,286,400 B): 16.18 s, `read_bytes` +738,721,792 B (4.7× read-ahead). Second sequential read of the same 2 GiB was cached (3.4 GB/s). The first run of this probe (overwritten) used `np.asarray` on a memmap slice, which copies nothing; fixed to `np.array`. Script: `../../scripts/read_rate.py` |
| `page_cache.txt` | `fincore`: 3.4 GB of the 966 GB file resident after the profile and probe (the file is otherwise cold) |
| `estimates/` | `sieve estimate` (dry run) of each planned request: spec as estimated + output |

`ls`/`stat` (2026-11-04): `/mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/`
holds `dns_10240x7680x1536_float64.raw`, 966,367,641,600 B (= 10240·7680·1536·8), mtime 2019-04-19,
and `wget.log` (downloaded 2026-11-02/03 from `http://klacansky.com/open-scivis-datasets/dns/…`, 20.2 MB/s,
12 h 39 m). The filesystem is NFS v4.2 (rsize 64 KiB). gpu-server: 48 cores, 503 GB RAM, no Slurm.

## Symlink for the variable's name (2026-11-04, approved)

`u_10240x7680x1536_float64.raw -> dns_10240x7680x1536_float64.raw` in the same folder on
gpu-server. The raw reader names the variable after the filename, so specs read `"u < 0"`
instead of `"dns < 0"`; nothing was copied or moved. Sieve stats remote files through links
(`stat -L`), so the cache identity and the cost estimate use the target's 966,367,641,600 B and
mtime, not the link's 31 B. `inspect` on the link: one variable `u`, grid (10240, 7680, 1536),
float64. Every CS3 spec from step 1 on uses the link's path. (The dry runs in `estimates/` were
made before the link and say `dns`.)
