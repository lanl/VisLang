# Step 3b: the large-scale planes at y+ ~ 100 (2026-11-05, run 5a6344f10d25, Sieve commit 2aa3b92)

Spec (`spec_as_run.py`): `coarsen(region(src, z=(81, 82)), x=16, y=16, how="mean")`, and the same at
z = (1454, 1455). One plane per wall at y+ 100.44, the height Lenaers et al. (2012) used for the large
scales. Each plane becomes 640 x 480 block means (16 x 16 cells = 204 x 102 viscous units). Saved as
`outputs/s3b_lower_yp100.vti`, `s3b_upper_yp100.vti`.

## Bytes and server time (`numbers.json`, from `timing_record.json`)

| Plane | Est. read on the server | Measured read on the server | Est. over the wire | Server time (`server_s`) |
|---|---|---|---|---|
| Lower, z = 81 | 629,145,600 B | 440,668,160 B | 2,457,600 B | 9.17 s |
| Upper, z = 1454 | 629,145,600 B | **629,407,744 B (+0.04 %)** | 2,457,600 B | 6.21 s |

- Over the wire, both: 4,917,372 B (`wire_bytes`; result 2 x 2,457,600 B plus placement).
- The upper plane was cold. It is one contiguous 629 MB stretch of the file, and the measured read
  matches the estimate to 0.04 %.
- The lower plane is also one of the stride overview's levels (z = 1 + 16 x 5). Step 1 had read every
  16th row of it, with read-ahead, so part of it was already in memory. Page cache before:
  27,512,225,792 B resident (`page_cache_before.txt`).

**Estimate vs measured, so far:**
- contiguous plane: 1.0004x;
- stride (every 16th row of 80 KiB): 4.80x;
- full-resolution area (8 KiB pieces of rows): 9.80x.

Sieve's estimate counts the bytes its reader requests. The NFS client fetches ahead of each request,
which costs most where requests are many and small.

## Output in ParaView (`paraview_check.txt`): 640 x 480 x 1 images, block centres, in source cells.
