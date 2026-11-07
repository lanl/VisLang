# Case Study 2, Test4 (2026-11-07)

The standalone run behind Case Study 2 (wildfire, temporal subsetting). What it sets out
to show, and why: `objectives.md`. Every figure and table the paper uses is produced here
from the files here; nothing is read from Test1–Test3.

| Folder | Contents |
|---|---|
| `specs/` | The two Sieve requests: `overview.py` (step 1) and `box.py` (step 3), exactly as run |
| `results/sieve_cold/{overview,box}/` | Each request from an empty extent cache and an evicted server page cache: spec as run, run report, run record (`timing_record.json`), `numbers.json` (estimate, bytes over the network, bytes read and time on the server) |
| `results/sieve_warm/` | The same two requests again over the cold run's cache (a revisit) |
| `results/server_route/` | The script route on the server: its time and reads (`cut_*.json`), the bytes it copies back (`cut_sizes.txt`), and the check that its rebuilt series equals Sieve's (`equal_*.txt`) |
| `results/eval/` | The evaluation pass (full resolution, every timestep): fronts at strides 1–16, wind profiles ahead of the front, lee ground; `summary.{json,md}` (scripts/analyze.py) |
| `results/` | `cost_table.{csv,md,tex}` (Table 1), `lines.txt` (lines of code per route) |
| `baseline/` | The routes without Sieve, as counted in Table 1: `server_route.sh` + `cut_series.py` + `assemble.py`; `two_pass.sh` + `cut_series.py`; `download_all.sh` + `cut_series.py` |
| `server/` | Helpers run on the server, not counted: the evaluation pass, the page-cache eviction, the measurement wrapper, the runner |
| `scripts/` | Local analysis, checks, figures, table |
| `data/` | `stat_series.txt` (size of every file in the series), `front_sieve_s8.csv`, `box_heights.csv` |
| `figures/` | The paper's figures (see below) |
| `outputs/` | Sieve's and the script route's `.vts` series; not in git |

## Reproduce

Over a connected session (`./sieve connect gpu-server`), from the repo root:

1. **Server runs** (evaluation pass, then the script route's two cuts, each from an
   evicted page cache): copy `server/*` and `baseline/cut_series.py` to
   `/mnt/na1/eecs/research/harp/data/ashrestha/vislang/cs2_test4/` and run
   `setsid nohup bash run_server.sh > run_server.log 2>&1 &` there. Copy back `eval/*.csv`,
   `eval.json`, `cut_*.json`, `cut_sizes.txt` and `stat_series.txt`.
2. **Sieve:** `bash bench/PacificVis/cs2-wildfire/Test4/scripts/run_sieve.sh cold`, then `… warm`.
3. **Script route, local half:** copy `cuts/{overview,box}` back and run
   `python baseline/assemble.py <copied> outputs/server_route/<step>`; check with
   `python scripts/verify_equal.py outputs/sieve/<step> outputs/server_route/<step> results/server_route/equal_<step>.txt`.
4. **Analysis and figures** (vislang env):
   ```
   python scripts/front_overview.py outputs/sieve/overview data/front_sieve_s8.csv
   python scripts/box_heights.py outputs/sieve/box data/box_heights.csv
   python scripts/analyze.py results/eval data/front_sieve_s8.csv results/eval
   python scripts/cost_table.py .
   python scripts/fig_speed.py results/eval/fronts.csv data/front_sieve_s8.csv results/cost_table.csv figures/fig_cs2_speed
   python scripts/fig_evidence.py results/eval data/box_heights.csv figures/fig_cs2_evidence
   ```
