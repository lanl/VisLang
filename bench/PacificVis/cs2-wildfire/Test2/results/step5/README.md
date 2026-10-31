# Step 5 — baselines (2026-10-31), bytes only

## The two-pass scientist's scripts (`baseline/`)
| script | what it does | lines* |
|---|---|---|
| `pass1_download.sh` | rsync every 5th file of the series whole (18) | 9 |
| `pass1_overview.py` | read each whole file with VTK; front + wind ahead (cs2_front.measure) | 29 |
| `pass2_download.sh` | rsync #4000 and #9000–#45000, skipping files pass 1 holds (31) | 9 |
| `pass2_cut.py` | cut the same world box (smallest block holding every point inside), keep u v w theta, write .vts | 42 |
*non-blank, non-comment, no docstrings. The front/wind analysis (`scripts/cs2_front.py`,
74 lines) is shared by every route and not counted against any.

## How they were checked (on gpu-server, files read in place; nothing copied here)
- `pass1_overview.py` on #13000, #18000, #30000: fronts −101.06, 1.894, 54.112 m — the
  same as the full-resolution Detail gives at #18000 and #30000 (`step5_baseline_check_server.txt`).
- `pass2_cut.py` on #30000: 151 × 121 × 39, and sha256 of u, v, w, theta and the points
  identical to Sieve's Detail at #30000 (`step5_sieve_detail_30000_hashes.txt`).
  First attempt cut the wrong block: the source files number their x indices from 251
  (extent 251–850), and VTK's cut takes that numbering — a pitfall a hand-written script
  meets and Sieve's region does not. Fixed by offsetting with the file's extent.
- The download scripts, with rsync stubbed to print: pass 1 picks exactly the 18 files in
  `pass1_would_copy.txt`; with those held, pass 2 picks the 31 in `pass2_would_copy.txt`
  (38 Detail timesteps − 7 held). (The brief's "30" left out the #4000 control.)
- `pass2_cut.py` over all 38 window files on gpu-server (/tmp/vislang_cs2_baseline/cut):
  38 cuts, same block each, 606,297,192 bytes (`data/server_cut_sizes.txt`) — what the
  work-on-the-server route copies back.

## Cost table → `../cost_table.md` / `.csv` (built by `scripts/cost_table.py` from recorded files)
- Sieve total over the wire 1,229,648,212 B (overview 146,495,298 + Detail 1,083,152,914).
- Two-pass download 53,802,142,345 B (49 files) = 43.8× Sieve; it sees 18 timesteps in pass 1.
- Download everything 96,624,255,640 B = 78.6× Sieve.
- Work on the server: 606,297,192 B copied back = 0.5× Sieve — fewer bytes than Sieve
  (compressed .vts, positions once per file vs Sieve's uncompressed transfer). No byte
  claim against this route; the comparison is effort and reuse (72 lines of scripts,
  three manual steps, no catalog, no provenance).
- Read on the server: Sieve 138.35 GB (whole files, VTK XML cannot be read in part) —
  more than the two-pass route's 53.80 GB. Said plainly in the paper.
