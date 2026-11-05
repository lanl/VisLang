# Case Study 3, Test1 (2026-11-04 to 11-05)

The one run of Case Study 3. The record of what was tested and found is `../CASE_STUDY_BRIEF.md`;
the design is `../DESIGN.md`.

| Folder | Contents |
|---|---|
| `results/step0` … `step7` | One folder per request, each with a README: spec as run, estimate, run report, run record, numbers, ParaView check |
| `results/eval/` | Evaluation on the ground-truth area, and the pre-registered large-scale analysis |
| `results/baseline/` | The scripts without Sieve, as run on the server: bytes, time, equality with Sieve |
| `baseline/` | The baseline scripts (`stride.py`, `blockmin.py`), their launcher and measurement wrappers |
| `scripts/` | Analysis, conversion, figure and ParaView-check scripts |
| `data/` | `channel5200-y.txt`, the JHTDB wall-normal grid |
| `figures/` | `fig_cs3_overviews.{pdf,png}` |
| `outputs/` | Sieve's outputs (`.vti`, `.nc`) and their `.npy` conversions; not in git |

## Reproduce
- **Sieve requests:** copy `results/stepN/spec_as_run.py` to `spec.py` and run
  `./sieve estimate spec.py`, then `./sieve execute spec.py`. Add `--confirm` for step 4, which is
  held over the read budget.
  - Over a connected ssh session (`./sieve connect gpu-server`).
  - Steps 3, 5 and 6 are served from the extent cache only if the requests they derive from
    (2 and 4) are cached.
- **Numbers:** `python scripts/record_step.py results/stepN <timings.jsonl lines before the run>`.
- **Analysis** (cvision env, inputs converted by `scripts/vti2npy.py` in the vislang env):
  ```
  python scripts/evaluate_area.py outputs data/channel5200-y.txt results/eval
  python scripts/largescale.py outputs results/eval
  python scripts/largescale_posthoc.py outputs results/step7
  python scripts/make_figure.py outputs results figures
  ```
- **ParaView checks:** `pvpython scripts/check_vti_pv.py <file.vti>` and
  `pvpython scripts/check_nc_pv.py <file.nc>`.
