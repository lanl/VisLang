#!/usr/bin/env python3
"""Run the Case Study session and write the paper's tables + a readable report.

Every measurement goes through the real `run_pipeline` — the same entry point an
interactive session uses — so nothing here is a special benchmark path. The
harness (vislang_timing.py) records each run; `summarize.py` tabulates them.

Three run groups, in order, plus one table derived from them:

  E1 movement       one 19.5 GiB remote Nyx TIMESERIES (3 x 512^3, 13 fields),
                    narrowed to 2 fields in a 120^3 box above the mean baryon
                    density. Run twice, BOTH confirmed, differing only in where
                    the narrowing executes: E1a reduces on darwin and pulls the
                    result, E1b sets VISLANG_REMOTE=off so the whole series
                    crosses the wire and the identical chain runs here. The
                    denominator is measured by the same runtime on the same link
                    minutes later, not asserted.
  E1local baseline  the SAME narrowing request as E1, on a 6.98 GB remote Nyx
                    snapshot (512^3, 13 fields -> 2 fields at stride 2), run with
                    VISLANG_REMOTE=off so the whole file crosses the wire and the
                    narrowing runs locally. Also a HELD/confirmed pair. This is
                    the denominator for "reduce next to the data", measured by
                    the same runtime rather than asserted. Needs no allocation.
  E2 iteration      eight successive questions in one scientist's session,
                    converging on whether this feedback prescription ejected
                    ENRICHED gas: survey, control, three distinct value lineages
                    (wind / hotter wind / halo), two per-variable deltas INSIDE
                    those value lineages, and one request for FEWER variables
                    than are cached. This is the catalog/reuse evidence. Reuse
                    is never shown by re-asking a question — every row is a
                    question a person would actually ask next.
  E3 mistake cost   four ill-posed requests (bad field, threshold on a variable
                    that does not exist, per-axis subsample on point data, and a
                    region far outside the box), measuring the seconds and bytes
                    spent BEFORE rejection — and, for the last, the cost of a
                    request that CANNOT be rejected from metadata.
  estimate table    not a group and not extra runs: every remote run above
                    records its predicted cost next to what it actually moved,
                    which summarize.py tabulates as the cost-gate accuracy row.

Isolation: the driver uses its OWN catalog root and timings file under
bench/results/, so a cold start is genuinely cold and the user's own
`.vislang/cache` is never touched or polluted.

Usage:
    python bench/case_study.py                  # everything (needs an allocation)
    python bench/case_study.py --only E2 E3     # selected groups
    python bench/case_study.py --keep-cache     # don't wipe the bench catalog first
    python bench/case_study.py --dry            # print the specs, run nothing
"""

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

RESULTS = os.path.join(REPO, "bench", "results")
SPECS = os.path.join(REPO, "bench", "specs")
OUT = os.path.join(RESULTS, "out")

# The remote dataset under test: one snapshot of a HACC hydro run from the
# SCIDAC/FLAMINGO subgrid-parameter sweep. The directory name IS the hypothesis —
# FSN_0.5387 (supernova feedback efficiency) and VEL_149.279 (wind velocity) are
# the parameters whose effect the session goes looking for.
#
# GenericIO writes a snapshot as a small header file plus eight ~1 GB rank
# partitions named `<base>#0 … <base>#7`. We point at the BASE file: pygio follows
# it to the parts. Note this collides with Sieve's own `…#N` = timestep
# convention, which is why the folder is NOT used as a timeseries here.
#
# SNAP_BYTES is the real denominator — the header is 2,064 B, so `source_bytes`
# as recorded by the timing harness is the header, NOT the data. Every
# reduction factor derived from the recorded value must be corrected against
# this constant (header + the eight parts, measured with `stat -Lc %s`).
_HACC = ("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/"
         "128MPC_RUNS_FLAMINGO_DESIGN_3A/"
         "FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output")
SNAP = f"{_HACC}/m000p.full.mpicosmo.624"      # final step: metals have moved
SNAP_BYTES = 8_925_812_397                     # 8.31 GiB across 8 partitions
N_PARTICLES = 268_435_456
STRIDE = 3                                     # int => deterministic row stride

# The survey projection and its two extensions. `subsample` MUST be an int: a
# float is an unseeded np.random.choice (narrowing.py:117), so a cached column
# and a freshly-fetched one would be drawn from different rows and silently
# misalign on the per-variable delta path.
F_BASE = ["x", "y", "z", "rho"]                 # where is the gas, how dense
F_UU = F_BASE + ["uu"]                          # + internal energy (temp proxy)
F_ZMET = F_UU + ["zmet"]                        # + metallicity (the science)
F_VEL = F_ZMET + ["vx", "vy", "vz"]             # + velocity (is it outflowing?)
F_FIG = ["x", "y", "z", "zmet"]                 # a FIGURE: fewer vars than cached

# The two cuts isolating feedback-ejected gas: HOT (shock-heated by the wind)
# and DIFFUSE (already out of the halo). Both are chosen from the percentiles of
# the survey data ALREADY pulled by E1/E2q1 — picking them costs nothing remote,
# which is the point. Filled in by `--derive-cuts` after the survey runs.
CUT_UU = None                                   # uu >  CUT_UU     (hot)
CUT_RHO = None                                  # rho < CUT_RHO    (diffuse)
CUT_UU2 = None                                  # a STRICTLY narrower retune
CUT_UU_COLD = None                              # uu <  CUT_UU_COLD   (cold)
CUT_RHO_DENSE = None                            # rho > CUT_RHO_DENSE (dense)
CUTS_FILE = os.path.join(RESULTS, "cuts.json")

# The env a real session runs under (mirrors .mcp.json), plus this driver's
# isolation. VISLANG_REMOTE=force is a SAFETY setting as much as a measurement
# one: it fails loudly rather than silently falling back to fetching 20.9 GB.
ENV = {
    "VISLANG_SRUN_JOBID": "auto",
    "VISLANG_SRUN_NAME": "vislang",
    "VISLANG_REMOTE_PYTHON": "/vast/home/ashrestha/.conda/envs/autoviz/bin/python",
    "VISLANG_REMOTE_REPO": "/vast/projects/autonomousvis/ashrestha/code/VisLang",
    "VISLANG_REMOTE": "force",
    "VISLANG_TIMING": "1",
    "VISLANG_CACHE": os.path.join(RESULTS, "cache"),
    "VISLANG_TIMINGS_FILE": os.path.join(RESULTS, "timings.jsonl"),
    "VISLANG_TRACE_FILE": os.path.join(RESULTS, "trace.log"),
}

# The per-query overlay that turns the reduce OFF, so the same spec routes down
# the whole-file/whole-folder fetch instead. Used by E1b and the E1local group.
_OFF = {"VISLANG_REMOTE": "off"}                # no reduce next to the data


class Q:
    """One measured query: a spec, why it is in the paper, and what to expect.

    `expect` is prose, not an assertion — the point of the run is to find out. It
    is printed next to the outcome so a surprise is visible instead of buried.

    `env` is a per-query overlay on the session ENV, applied for that query only
    and restored after (run_one). It exists because a BASELINE is a different
    runtime setting of the same request, not a different request:
    E1local sets VISLANG_REMOTE=off so the identical spec routes through the
    whole-file fetch instead of the remote reduce. Keeping it per-query means the
    baseline and the reduced run can sit in one session and one table."""

    def __init__(self, qid, group, title, spec, why, expect, confirm=False,
                 env=None):
        self.qid, self.group, self.title = qid, group, title
        self.spec, self.why, self.expect, self.confirm = spec, why, expect, confirm
        self.env = env or {}


if os.path.exists(CUTS_FILE):                   # written by --derive-cuts
    with open(CUTS_FILE) as _f:
        _cuts = json.load(_f)
    CUT_UU, CUT_RHO, CUT_UU2 = _cuts["uu"], _cuts["rho"], _cuts["uu2"]
    CUT_UU_COLD, CUT_RHO_DENSE = _cuts["uu_cold"], _cuts["rho_dense"]


def _save(node_expr, name):
    return f'save({node_expr}, "{os.path.join(OUT, name)}")'


def _plain(fields, name):
    """An unfiltered request on the survey lineage: project, then stride."""
    return _save(f'subsample(fields(source("{SNAP}"), {fields!r}), {STRIDE})', name)


def _cut(fields, preds, name):
    """A filtered request, written in the order the science means it: project,
    then the ANDed cuts, THEN the stride — so the stride samples the SURVIVORS.

    That order is what makes a miss on this lineage REQUIRED rather than merely
    unimplemented. The survey lineage holds `all[::100]`; this asks for
    `filter(all)[::100]`. Masking the cached survey would yield
    `filter(all[::100])` — the same row COUNT, drawn from a different population,
    so not the answer that was asked for. Sieve is faithful to written order and
    will not substitute a statistically-equivalent result, because statistical
    equivalence is a domain judgement the runtime has no standing to make.

    (Had the stride been written BEFORE the cuts, this would be a pure row-mask
    of the cached extents and reusable in principle. The order you write
    determines what a later question can reuse.)

    NB the thresholds are NOT fused: planner.py:588 emits one RowMask per
    threshold and my_load.py:98 applies them in written order."""
    node = f'fields(source("{SNAP}"), {fields!r})'
    for var, op, val in preds:
        node = f'threshold({node}, "{var} {op} {val}")'
    return _save(f'subsample({node}, {STRIDE})', name)


# The three value lineages the session creates. Each is a distinct cache
# identity: remote_reduce._narrow_key records (var, op, value) verbatim.
def _wind(hot=None):
    return [("uu", ">", hot if hot is not None else CUT_UU),
            ("rho", "<", CUT_RHO)]


def _halo():
    return [("uu", "<", CUT_UU_COLD), ("rho", ">", CUT_RHO_DENSE)]


# --- E1: data movement — reduce next to the data, against fetching it -------
# ONE request, run twice, differing ONLY in where the narrowing executes. Both
# halves are confirmed — the budget gate is no longer demonstrated here, and its
# only remaining evidence in the study is E1a_local's confirm=False — so the
# pair isolates route from policy: E1a reduces on darwin and pulls the result,
# E1b sets VISLANG_REMOTE=off so the whole timeseries crosses the wire and the
# identical chain runs locally against the copy. The denominator is measured by
# the same runtime and the same spec, not asserted.
#
# The source is the FOLDER, so this is a 3-timestep series: E1a runs it as one
# batched remote job (planner._plan_remote_folder), while E1b's fallback pulls
# the entire directory — all three 6.98 GB files, 20.94 GB — into
# vislang_paths.downloads_dir() and then maps the chain over the copies
# (planner._plan_folder). E1b needs no Slurm allocation; E1a does.
#
# The `#N` files in nyx_series/ are SYMLINKS to /projects/exasky/…; transfer_dir
# uses `rsync -aL`, so E1b receives real files rather than dangling links.
NYX_SERIES = "ssh://darwin//projects/autonomousvis/ashrestha/nyx_series/"
NYX_SERIES_BYTES = 3 * 6_979_342_896            # 20.94 GB across 3 timesteps
E1_FIELDS = ["native_fields/baryon_density", "native_fields/dark_matter_density"]
# A 120^3 box offset into the volume (indices on the ORIGINAL 512^3 grid, which
# is where region/subsample compose — planner._grid_ranges), then the one cut
# whose constant is physically meaningful: baryon_density is in units of the
# cosmic mean (HDF5 attr `units: (mean)`, field mean = 1.000), so `> 1.0` is
# "denser than the cosmic mean" and keeps ~20% of voxels.
_E1_QUERY = (
    'threshold('
    'region('
    f'fields(timesteps(source("{NYX_SERIES}"), 0, 2), {E1_FIELDS!r}), '
    'x=(100, 220), y=(40, 160), z=(40, 160)), '
    '"native_fields/baryon_density > 1.0")'
)

# The request under test: the survey, on the multi-part GenericIO snapshot. The
# NYX timeseries variant of this pair is archived under
# bench/archive-hdf5-nyx-512-e1/ — the constants above stay so it can be switched
# back by pointing _E1_QUERY at them.
_E1_GENIO = f'subsample(fields(source("{SNAP}"), {F_BASE!r}), {STRIDE})'

E1 = [
    Q("E1a", "E1", "Reduced next to the data — one remote job",
      _save(_E1_GENIO, "e1_genio"),
      "The headline movement number: 4 of 17 variables at stride 3, reduced next "
      "to the data. The denominator is E1b below — the same spec, same runtime, "
      "same link, remote reduce switched off — NOT the 2,064 B header the harness "
      "records as source_bytes (see SNAP_BYTES and summarize's "
      "SOURCE_BYTES_OVERRIDE).",
      "~1,365 MiB over the wire (268,435,456/3 rows x 4 vars x 4 B = "
      "1,431,655,776 B). GenericIO is a COLUMN store, so the projection is pushed "
      "into the read and only 4 columns are touched (~4.29 GB of the 8.31 GiB); it "
      "cannot skip rows (supports_strided_read=False), so every row of those "
      "columns is read remotely and the stride applied after. The reduce saves "
      "MOVEMENT, not work.",
      confirm=True),
    Q("E1b", "E1", "Fetched whole, reduced locally — the baseline",
      _save(_E1_GENIO, "e1_genio"),
      "What an ad-hoc copy costs, produced by the same runtime and the same spec "
      "rather than asserted as a denominator. VISLANG_REMOTE=off routes the "
      "identical chain down the whole-file fetch. Note this pair could not be run "
      "at all until _fetch_remote learned to pull a multi-part snapshot's `#N` "
      "partitions: the named path is a 2,064 B header, so the fetch route used to "
      "copy 2 KB and then fail locally with no parts to read.",
      "~8.31 GiB over the wire (header + 8 rank partitions) for the same "
      "~1,365 MiB result, a ~6.2x transfer penalty — far smaller than NYX's 505x, "
      "because stride 3 keeps a third of every column. Local work is NOT expected "
      "to be large despite the unskippable 4.29 GB read: pygio reads whole columns "
      "sequentially and rsync has just left the file in the page cache, so the "
      "narrowing lands near memory bandwidth (measured: 1.5 s). The finding is "
      "that the work is cheap wherever it runs, and only its LOCATION decides "
      "whether 8.31 GiB or 1.33 GiB crosses the wire.",
      confirm=True, env=_OFF),
]

# --- E1local: the same movement question, WITHOUT remote reduce -------------
# A baseline, not a new capability. One real Nyx snapshot (512^3 grid, 13
# fields, 6.98 GB) narrowed to 2 fields at stride 2 — byte-identical to the
# request the NYX study's E1b answered WITH a remote reduce (see
# bench/archive-hdf5-NYX-512/queries.json: ~128 MB over the wire, 84 s
# end-to-end). Here VISLANG_REMOTE=off routes the same spec down the whole-file
# FETCH path instead (planner.py:400): the entire 6.98 GB crosses the network
# into ~/.vislang/downloads/, and `fields → subsample(2)` then runs LOCALLY
# against the downloaded copy. Nothing executes on darwin, so this path needs no
# Slurm allocation at all.
#
# Only WHERE the narrowing runs differs between the two routes, which is what
# makes the pair a clean measurement of what moving the computation to the data
# actually buys: 6.98 GB and a full local copy, against 128 MB and none.
#
# `#1` here is part of the FILENAME, not the timeseries convention — the source
# is a single file (remote_is_dir decides), so `…#N` is never parsed.
NYX_SNAP = ("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/"
            "nyx512#1.hdf5")
NYX_SNAP_BYTES = 6_979_342_896                  # 6.98 GB / 6.50 GiB, one stat
NYX_FIELDS = ["native_fields/temperature", "native_fields/baryon_density"]
# Where the whole-file fetch stages its copy (vislang_paths.downloads_dir): the
# local source E1c_local narrows, so the read can be timed on its own.
DOWNLOADED = os.path.join(REPO, ".vislang", "downloads", "nyx512#1.hdf5")
_NYX_QUERY = f'subsample(fields(source("{NYX_SNAP}"), {NYX_FIELDS!r}), 2)'

# The remote-reduce half of the comparison: the archived NYX-512 session ran this
# EXACT spec on this EXACT file WITH the reduce (its E1b), so its timings are the
# measured denominator for E1local. Kept as a path to a run log rather than copied
# numbers — if the archive moves, the comparison table disappears instead of
# quietly reporting stale values.
COMPARE_TIMINGS = os.path.join(REPO, "bench", "archive-hdf5-NYX-512",
                               "timings.jsonl")

E1local = [
    Q("E1a_local", "E1local", "Whole-file fetch — cost gate holds",
      _save(_NYX_QUERY, "e1_local_sub2.hdf5"),
      "On the fetch path the gate prices the WHOLE file from one stat plus a "
      "bandwidth probe — there is no narrowing to discount, because the "
      "narrowing happens after the bytes land. A HELD run is the claim that "
      "Sieve refuses the expensive route before paying for it, not after.",
      "HELD over budget (1 GiB / 3 s defaults); 0 bulk bytes; an estimate of "
      "~6.98 GB over the wire against a ~128 MB result.",
      confirm=False, env=_OFF),
    Q("E1b_local", "E1local", "Whole-file fetch — confirmed, reduced locally",
      _save(_NYX_QUERY, "e1_local_sub2.hdf5"),
      "The baseline the headline number is measured against: the same 2-of-13 "
      "fields at stride 2, but reduced HERE instead of there. This is what an "
      "ad-hoc copy costs, produced by the same runtime and the same spec rather "
      "than asserted as a denominator.",
      "~6.98 GB over the wire (the entire file) for the same ~128 MB result "
      "E1b of the NYX study got for ~128 MB — a ~52x transfer penalty; wall "
      "clock dominated entirely by the download, plus 6.98 GB of local disk.",
      confirm=True, env=_OFF),
    # The narrowing alone, against the copy E1b_local already fetched — a LOCAL
    # source, so no network and no gate on the transfer. Its only purpose is to
    # separate the two costs the fetch route bundles together: E1b_local's
    # materialize reads a file its own rsync wrote seconds earlier, so it is
    # served from the OS page cache. Run this after dropping the cache
    # (`sudo purge`) and the same read is priced against the disk instead.
    #
    # Which number belongs in the table depends on the question: warm is faithful
    # to THIS route (the narrowing always follows the download), while cold is
    # what a LATER query against an already-downloaded file would pay.
    Q("E1c_local", "E1local", "Local narrowing only — cold page cache",
      _save(f'subsample(fields(source("{DOWNLOADED}"), {NYX_FIELDS!r}), 2)',
            "e1_local_cold.hdf5"),
      "Isolates the narrowing from the transfer. E1b_local's 0.49 s materialize "
      "read a file the preceding rsync had just written through the page cache on "
      "a 51.5 GiB machine; this run prices the identical hyperslab against the "
      "disk, so the local-work row can be reported without a warm-cache asterisk.",
      "the same 128 MiB result and byte-identical output; a materialize slower "
      "than 0.49 s by whatever the page cache was worth — bounded by the 1,024 "
      "MiB actually read (2 of 13 variables), not by the 6,656 MiB downloaded.",
      confirm=True),
]

# --- E2: the iterative session (the catalog claim) --------------------------
# One scientist, eight questions, converging on one thing: did this feedback
# prescription (FSN_0.5387 / VEL_149.279) eject ENRICHED gas into the IGM, or
# leave the metals locked in halos? The signature is gas that is hot, diffuse,
# metal-bearing AND outflowing — each query adds one term of that conjunction.
#
# Three value lineages are created (L2 wind, L3 hotter wind, L4 halo). Reuse is
# NOT demonstrated by re-asking a question: it appears when a later question
# needs variables a former one already brought home (q1, q2, q4, q7), and when a
# question asks for FEWER variables than are cached (q5) — the commonest real
# move in a session, and one a file-keyed cache cannot express at all.
E2 = [
    Q("E2q1", "E2", "Is any of the gas hot?",
      _plain(F_UU, "e2q1"),
      "The commonest follow-up: same view, one more variable. `fields` is "
      "deliberately excluded from the catalog key (remote_reduce._narrow_key), "
      "which is what makes the projection a reusable axis rather than part of "
      "the identity.",
      "4/5 reused; only uu crosses (~10.7 MiB).",
      confirm=True),
    Q("E2q2", "E2", "How enriched is the gas in general? (the control)",
      _plain(F_ZMET, "e2q2"),
      "The control population. Without unfiltered metallicity the session can "
      "report that some wind gas carries metals, but NOT that wind gas is "
      "PREFERENTIALLY enriched — which is the actual hypothesis. This row is "
      "what makes q3-q8 interpretable.",
      "5/6 reused; only zmet crosses.",
      confirm=True),
    Q("E2q3", "E2", "Isolate the wind candidates (new lineage)",
      _cut(F_ZMET, _wind(), "e2q3"),
      "The science question: which gas is hot AND diffuse — the signature of "
      "wind-ejected material? This misses, and the miss is REQUIRED, not a "
      "caching shortfall: the survey holds all[::100] while this asks for "
      "filter(all)[::100]. See _cut's docstring for why substituting the one "
      "for the other would answer a different question.",
      "0/6 reused. The cut values came from data already local (q1/q2), so "
      "CHOOSING the filter cost nothing remote; only applying it does.",
      confirm=True),
    Q("E2q4", "E2", "Are they actually outflowing?",
      _cut(F_VEL, _wind(), "e2q4"),
      "Hot and diffuse is suggestive; hot, diffuse and MOVING is a wind. This "
      "is the delta claim the NYX study could not make: per-variable reuse "
      "inside a VALUE-DEPENDENT lineage, not merely a structural one.",
      "6/9 reused; only the three velocity components cross. The two thresholds "
      "are not recomputed for the six columns already held.",
      confirm=True),
    Q("E2q5", "E2", "Make the metallicity figure (fewer variables than cached)",
      _cut(F_FIG, _wind(), "e2q5"),
      "Reuse in the direction nobody demonstrates: asking for LESS. Having "
      "pulled nine columns, the scientist now wants a position-vs-metallicity "
      "figure from four of them. It is a genuinely different request — not a "
      "re-issue — and because `fields` is not part of the key it is a total hit.",
      "4/4 reused; 0 bytes; no srun step.",
      confirm=True),
    Q("E2q6", "E2", "Does the signal strengthen when hotter? (retune)",
      _cut(F_ZMET, _wind(CUT_UU2), "e2q6"),
      "Tightening the temperature cut. The request is strictly narrower than "
      "q3/q4 — same variable, same direction, larger constant — and provably "
      "contained in what is cached, yet it refetches. Same root cause as q3: "
      "the stride is positional over the filtered rows, so containment could "
      "not be exploited here even if predicate reasoning existed.",
      "0/6 reused. A limitation, and a bounded one: containment would be sound "
      "only if no positional op followed the cut.",
      confirm=True),
    Q("E2q7", "E2", "Are the hottest ones outflowing too?",
      _cut(F_VEL, _wind(CUT_UU2), "e2q7"),
      "The same delta as q4, in a second, independently created value lineage. "
      "Showing the mechanism twice — in two lineages built by different "
      "predicates — is much stronger evidence than showing it once.",
      "6/9 reused; only the velocity components cross.",
      confirm=True),
    Q("E2q8", "E2", "Where did the metals that stayed behind end up?",
      _cut(F_ZMET, _halo(), "e2q8"),
      "The contrast population: gas that is NOT hot (below the wind's own "
      "temperature cut) and denser than the uniform background — the halo "
      "material feedback failed to eject. Its metallicity against q3's, both "
      "measured against q2's unfiltered control, is the actual test of the "
      "hypothesis, and it closes the investigation on a comparison rather than "
      "on a cache statistic.",
      "0/6 reused — a third lineage, a third distinct question.",
      confirm=True),
]


# --- E3: what a rejected request costs -------------------------------------
# The first three are caught from metadata. The fourth is the interesting one:
# it is ill-posed in exactly the way the first three are, and Sieve CANNOT see it.
E3 = [
    Q("E3a", "E3", "Misspelled variable (remote)",
      _save(f'fields(source("{SNAP}"), ["zmett"])', "e3a"),
      "The most frequent authoring error. Where the rejection lands — local "
      "metadata, remote metadata, or after a read — is the measurement.",
      "rejected on schema (validate_narrowing, n.project), 0 bulk bytes; the "
      "seconds are the remote login-node inspect, not a read.",
      confirm=True),
    Q("E3b", "E3", "Threshold on a variable that does not exist (remote)",
      _save(f'threshold(fields(source("{SNAP}"), {F_BASE!r}), '
            f'"temperature > 1e5")', "e3b"),
      "A domain slip rather than a typo: this dataset has no `temperature` — the "
      "thermal variable is `uu`, an internal energy. The predicate references a "
      "variable OUTSIDE the projection, which is exactly the case "
      "validate_narrowing's docstring warns must still be checked.",
      "rejected on the predicate's variable, 0 bulk bytes.",
      confirm=True),
    Q("E3c", "E3", "Per-axis subsample on point data (remote)",
      _save(f'subsample(fields(source("{SNAP}"), {F_BASE!r}), x=2)', "e3c"),
      "The DSL admits per-axis subsample on GRIDS only; on point data a single "
      "factor is the documented form. Note `x` IS a real coordinate here, so this "
      "is not an unknown-axis error — the form simply has no meaning for particles.",
      "rejected before any read by an explicit modality guard (planner.py:568), "
      "with a diagnostic that names the actual problem and the correct form. Note "
      "this is NOT the grid unknown-axis check at planner.py:215 — that one is "
      "only reached on the grid branch — but a separate check written for the "
      "point-data path.",
      confirm=True),
    Q("E3d", "E3", "Region far outside the box — the uncheckable request",
      _save(f'subsample(region(fields(source("{SNAP}"), {F_BASE!r}), '
            f'x=(9000, 9999)), {STRIDE})', "e3d"),
      "The counterpart to NYX's out-of-bounds region, which was rejected against "
      "the 512^3 extent for 0 bytes. On point data a `region` is a world-space "
      "bbox and validate_narrowing._check_bbox only verifies that the coordinate "
      "VARIABLES exist — there is no extent to violate. So an equally ill-posed "
      "request is not rejected at all.",
      "NOT rejected: a full 8.31 GiB remote read that returns zero particles. The "
      "measurement is what an uncheckable mistake costs versus a checkable one.",
      confirm=True),
]

# E3 (the cost of a rejected request) is DEFINED above but not part of the
# session: the paper's HACC tables are E1 (movement) and E2 (the investigation).
# The rejection evidence is carried by the NYX study in archive-hdf5-NYX-512,
# which measured the same four cases. Re-enable by adding "E3": E3 here.
GROUPS = {"E1": E1, "E1local": E1local, "E2": E2}


def run_one(q, run_pipeline):
    """Write the spec, run it, and return a result row. A raised exception is a
    result too (E3 depends on it), so nothing here is allowed to abort the session."""
    path = os.path.join(SPECS, f"{q.qid}.py")
    with open(path, "w") as f:
        f.write(f"# {q.qid}: {q.title}\n{q.spec}\n")
    print(f"\n{'=' * 78}\n[{q.qid}] {q.title}\n  spec: {q.spec}")
    if q.env:
        print(f"  env: {' '.join(f'{k}={v}' for k, v in sorted(q.env.items()))}")
    # Per-query env overlay, restored afterwards so one query's runtime setting
    # cannot leak into the next (a leaked VISLANG_REMOTE=off would silently turn
    # a later reduced run into a whole-file fetch and the table would lie).
    saved = {k: os.environ.get(k) for k in q.env}
    os.environ.update(q.env)
    t0 = time.perf_counter()
    try:
        report = run_pipeline(path, confirm=q.confirm)
        err = None
    except Exception as e:                      # never abort the session
        report, err = "", f"{type(e).__name__}: {e}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    dt = time.perf_counter() - t0
    status = "EXCEPTION"
    for line in (report or "").splitlines():
        if line.startswith("Status:"):
            status = line.split(":", 1)[1].strip()
            break
    print(f"  -> {status} in {dt:.1f}s" + (f"  ({err})" if err else ""))
    return {"qid": q.qid, "group": q.group, "title": q.title, "spec": q.spec,
            "why": q.why, "expect": q.expect, "confirm": q.confirm,
            "status": status, "driver_s": round(dt, 2), "error": err,
            "report": report}


def write_session_md(rows, recs, path):
    """The human-readable companion to the CSVs: one narrated section per query,
    with the spec, what the run actually did, and the numbers pulled out of the
    timing record. The CSVs are for plotting; this is for reading and for writing
    the paper text from."""
    import summarize

    by_run = {}
    for r in recs:
        by_run.setdefault(r.get("spec"), []).append(r)

    def rec_for(qid):
        want = os.path.join(SPECS, f"{qid}.py")
        got = by_run.get(want) or []
        return got[-1] if got else None

    lines = [f"# Sieve case study — session of {datetime.now():%Y-%m-%d %H:%M}",
             "",
             "Every row was produced by `run_pipeline` (the same entry point an ",
             "interactive session uses) and measured by `vislang_timing.py`. ",
             "Bytes labelled *over the wire* are what actually crossed the network; ",
             "*source* is what a whole-file/whole-folder copy would have moved.",
             ""]
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)

    titles = {"E1": ("E1 — Data movement: one request, two routes (reduce next "
                     "to the data vs fetch whole and narrow locally)"),
              "E1local": ("E1local — The same request without remote reduce "
                          "(whole-file fetch, local narrowing)"),
              "E2": "E2 — An iterative session (catalog reuse)",
              "E3": "E3 — What a rejected request costs"}
    for g, rs in groups.items():
        lines += [f"## {titles.get(g, g)}", ""]
        for r in rs:
            rec = rec_for(r["qid"])
            lines += [f"### {r['qid']} — {r['title']}", "",
                      "```python", r["spec"], "```", "",
                      f"**Why it is in the paper.** {r['why']}", "",
                      f"**Expected.** {r['expect']}", "",
                      f"**Outcome.** `{r['status']}`"
                      + (f" — {r['error']}" if r["error"] else "")
                      + f", {r['driver_s']}s wall clock.", ""]
            if rec:
                lines += _fact_lines(rec, summarize)
            lines += [""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def _fact_lines(rec, summarize):
    """The measured facts of one run as a bullet list, in prose the paper can
    quote — derived entirely from the timing record, never hardcoded."""
    rows = summarize.rows([rec])
    if not rows:
        return []
    r = rows[0]
    out = []

    def mb(v):
        """MiB, labelled MiB — the CSVs use the same base (see bench/README.md)."""
        if not isinstance(v, (int, float)):
            return "n/a"
        return f"{v / 1024:.2f} GiB" if v >= 1024 else f"{v:.1f} MiB"

    if r["source_mib"]:
        out.append(f"- source: {mb(r['source_mib'])}"
                   + (f" across {r['n_timesteps']} timestep(s)"
                      if r["n_timesteps"] else ""))
    wire = r["wire_mib"] or 0.0
    out.append(f"- over the wire: {mb(wire)}"
               + (f" — **{r['reduction_x']}x less than a whole-source copy**"
                  if r["reduction_x"] else ""))
    if r["total_pairs"]:
        out.append(f"- catalog: reused {r['reused_pairs']}/{r['total_pairs']} "
                   f"(timestep,variable) extent(s)")
    elif r["want_vars"]:
        out.append(f"- catalog: reused {r['cached_vars']}/{r['want_vars']} "
                   f"variable extent(s)")
    if r["est_read_mib"] is not None:
        band = ""
        if r["est_time_lo_s"] and r["est_time_hi_s"]:
            band = (f", predicted {r['est_time_lo_s']:.0f}–"
                    f"{r['est_time_hi_s']:.0f} s")
        verdict = ""
        if r["pull_s"] and r["est_time_lo_s"] and r["est_time_hi_s"]:
            act = r["pull_s"]
            verdict = (f" — actual pull {act:.0f} s, "
                       + ("INSIDE the band"
                          if r["est_time_lo_s"] <= act <= r["est_time_hi_s"]
                          else "OUTSIDE the band"))
        out.append(f"- estimate: {mb(r['est_read_mib'])} over the wire{band}"
                   + ("  ⚠ over budget" if r["est_over_budget"] else "") + verdict)
    # EVERY phase, in the order it ran — for a rejected request the breakdown IS
    # the result (where the seconds went when no data moved).
    if rec.get("phases"):
        out.append("- phases: " + ", ".join(
            f"{p['name'].split('/')[-1]} {p['s']:.2f}s" for p in rec["phases"]
            if p.get("s") is not None and p.get("depth") == 1) or "(none)")
        top = [p for p in rec["phases"] if p.get("depth") == 0 and p.get("s")]
        if top:
            out.append("- top-level: " + ", ".join(
                f"{p['name']} {p['s']:.2f}s" for p in top))
    orch = [(k, r[k]) for k in ("ssh_exec", "ssh_query", "remote_jobs")
            if r.get(k)]
    if orch:
        out.append("- orchestration: " + ", ".join(f"{k}={v}" for k, v in orch))
    if r["route"]:
        out.append(f"- route: `{r['route']}`"
                   + (f", held on {r['held']}" if r["held"] else ""))
    if r["error"]:
        out.append(f"- **spent before rejection: {r['total_s']:.1f} s, "
                   f"{mb(wire)} moved**")
        out.append(f"- error: `{r['error'].splitlines()[0][:300]}`")
    return out


COLUMNS_MD = """# Column dictionary for the case-study CSVs

Written by `bench/case_study.py`; regenerate the tables any time with
`python bench/summarize.py --file bench/results/timings.jsonl --csv bench/results`.

Read `session.md` first — it narrates each query. These CSVs are the same runs in
plottable form.

## runs.csv — one row per pipeline
| column | meaning |
|---|---|
| `started` | ISO timestamp of the run |
| `site` | `local` or `remote` — where the data lived |
| `route` | what the planner chose: `remote_reduce`, `remote_folder_reduce`, `catalog_full_hit`, `whole_file_fetch`, `held_budget`, `held_allocation` |
| `forms` | the narrowing forms as written, in order |
| `n_timesteps` | timesteps selected |
| `source_mb` | size of the source a naive copy would have moved (the baseline denominator) |
| `wire_mb` | bytes that actually crossed the network |
| `reduction_x` | `source_mb / wire_mb` — the headline movement factor |
| `total_s` | end-to-end wall clock for the run |
| `inspect_s` | schema read (metadata only) |
| `remote_exec_s` | the reduce running next to the data |
| `pull_s` | transferring the reduced result home |
| `materialize_s` | assembling arrays in local memory |
| `sink_s` | writing the output file |
| `ssh_exec` | ssh command round trips — orchestration cost, hardware-independent |
| `remote_jobs` | scheduler (`srun`) steps launched — 1 means one batched job |
| `status` | `OK`, `FAILED`, `NEEDS CONFIRM`, `NEEDS ALLOCATION` |

## cache.csv — reuse across successive queries
`reused_pairs / total_pairs` counts (timestep, variable) extents on the folder
path; `cached_vars / want_vars` counts variables on the single-file path.
`reuse_pct` is the percentage served locally. Read down the rows in time order:
that column rising while `wire_mb` falls to zero *is* the iterative-workflow
result.

## estimate.csv — predicted vs actual
`est_read_mb` vs `wire_mb` with `err_pct` (positive = the estimate was high);
`est_time_lo_s`/`est_time_hi_s` vs the measured `pull_s`, with `in_band` =
`yes`/`low`/`high`. `probe_bw_mbps` is the link speed measured at gate time —
the input the time band was derived from, so a missed band can be attributed to
link variance rather than bad math. `est_over_budget` is whether the gate fired.

## errors.csv — cost of a rejected request
`total_s` is seconds spent before the rejection, `wire_mb` the bytes moved by
then (0.0 is the point), `error` the message the author saw. `site` tells you
whether the check was answered from local or remote metadata.
"""


def derive_cuts():
    """Choose the two cuts from the survey data ALREADY pulled, and write them to
    cuts.json for the E2 filtered queries to read.

    This is the methodological point, not a convenience: a scientist cannot know
    a sensible `uu` cut before seeing the distribution, and guessing one would be
    the unscientific move. E1b/E2q1 already brought 2.68 M sampled rows home, so
    the cut is chosen locally, at zero remote cost. `uu2` is deliberately a
    STRICTLY tighter cut than `uu`, so E2q5's request is provably contained in
    E2q3/q4's cached extents — which is what makes its refetch a limitation
    rather than an ordinary miss."""
    import glob
    import numpy as np

    cands = sorted(glob.glob(os.path.join(OUT, "e2q1*"))
                   + glob.glob(os.path.join(OUT, "e1_survey*")))
    if not cands:
        print(f"no survey output under {OUT} — run --only E1 E2 first "
              f"(E2q1 carries uu; E1b carries rho)")
        return 1

    def _columns(path):
        """{name: array} from whichever writer produced the survey output."""
        if os.path.isdir(path):
            for inner in sorted(glob.glob(os.path.join(path, "*"))):
                got = _columns(inner)
                if got:
                    return got
            return {}
        if path.endswith(".npz"):
            with np.load(path, allow_pickle=False) as z:
                return {k: z[k] for k in z.files}
        if path.endswith((".hdf5", ".h5")):
            import h5py
            out = {}
            with h5py.File(path, "r") as h:
                h.visititems(lambda n, o: out.__setitem__(n.split("/")[-1], o[:])
                             if isinstance(o, h5py.Dataset) else None)
            return out
        return {}

    cols = {}
    for p in cands:
        for k, v in _columns(p).items():
            cols.setdefault(k, v)
    if "uu" not in cols or "rho" not in cols:
        print(f"survey output has {sorted(cols)} — need both 'uu' and 'rho'. "
              f"Run E1 and E2q1 before deriving cuts.")
        return 1

    uu, rho = np.asarray(cols["uu"]).ravel(), np.asarray(cols["rho"]).ravel()

    def _sig2(v):
        """Two significant figures — a person picks a round cut, not a float.

        Cast to Python float FIRST: these columns are float32, and rounding a
        float32 leaves artifacts (3.3e10 comes out as 32999999488.0). The value
        ends up verbatim in the catalog key and in the paper, so it has to be the
        number a person would actually write."""
        from math import floor, log10
        v = float(v)
        if not np.isfinite(v) or v == 0:
            return v
        return float(round(v, -int(floor(log10(abs(v)))) + 1))

    cuts = {"uu": _sig2(np.percentile(uu, 90)),    # hot: top decile
            "rho": _sig2(np.percentile(rho, 25)),  # diffuse: bottom quartile
            "uu2": _sig2(np.percentile(uu, 97)),   # strictly tighter retune
            }
    # The contrast population: cold, dense — halo gas feedback failed to eject.
    # NOT chosen by percentile, because both variables have huge spikes that make
    # percentile cuts land on cliffs:
    #   * rho is IDENTICAL (85,937,750,016) from p50 through p80 — the uniform
    #     background density of unperturbed gas. Cutting just below it selects
    #     64.5% of particles, just above it 14.5%. A 1% move in the constant
    #     swings selectivity 4.5x, which is not a number to build a table on.
    #   * uu likewise spikes at its median 625, so `uu < p50` selects 2%, not 50%.
    # So the cuts are defined by MEANING instead, and both happen to sit on the
    # stable side of their cliff: "not hot" is the wind's own temperature cut
    # inverted, and "dense" is anything above the background plateau (14.5%,
    # varying by only 1 point out to rho > 1e11).
    cuts["uu_cold"] = cuts["uu"]                       # not hot: uu < the hot cut
    rho_p75 = float(np.percentile(rho, 75))
    cuts["rho_dense"] = _sig2(rho_p75)
    if cuts["rho_dense"] <= rho_p75:                   # rounded INTO the plateau
        cuts["rho_dense"] = _sig2(rho_p75 * 1.01)      # step above it
    if not cuts["uu2"] > cuts["uu"]:               # rounding must not break it
        cuts["uu2"] = cuts["uu"] * 2
    os.makedirs(RESULTS, exist_ok=True)
    with open(CUTS_FILE, "w") as f:
        json.dump(cuts, f, indent=2)

    n = uu.size
    print(f"survey rows: {n:,} ({n / N_PARTICLES:.2%} of the snapshot)")
    print(f"  uu    p50={np.percentile(uu, 50):.4g}  p90={np.percentile(uu, 90):.4g}"
          f"  p97={np.percentile(uu, 97):.4g}")
    print(f"  rho   p25={np.percentile(rho, 25):.4g}  p50={np.percentile(rho, 50):.4g}")
    print(f"  x     [{np.min(cols['x']):.4g}, {np.max(cols['x']):.4g}]"
          if "x" in cols else "  (no x column — check E3d's region bounds by hand)")
    wind = float(np.mean((uu > cuts["uu"]) & (rho < cuts["rho"])))
    hotw = float(np.mean((uu > cuts["uu2"]) & (rho < cuts["rho"])))
    halo = float(np.mean((uu < cuts["uu_cold"]) & (rho > cuts["rho_dense"])))
    print(f"\ncuts -> {cuts}")
    print(f"  wind (hot+diffuse)   selects {wind:.2%} of sampled rows")
    print(f"  hotter wind (retune) selects {hotw:.2%}")
    print(f"  halo (cold+dense)    selects {halo:.2%}")
    print(f"wrote {CUTS_FILE}; E2's filtered queries will use these.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(GROUPS),
                    help="run only these groups (default: all)")
    ap.add_argument("--keep-cache", action="store_true",
                    help="keep the bench catalog (default: wipe it for a cold start)")
    ap.add_argument("--dry", action="store_true", help="print the specs, run nothing")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the tables + session.md from existing results "
                         "(runs nothing, touches no remote)")
    ap.add_argument("--cold-each", action="store_true",
                    help="wipe the bench catalog before EVERY query. NO group here "
                         "needs this: it exists for an isolated cost-vs-N scaling "
                         "sweep, whose curve is confounded when query N reuses what "
                         "N-1 cached. It would make E2 meaningless (reuse across "
                         "queries IS the measurement), so leave it off.")
    ap.add_argument("--qids", nargs="+", metavar="QID",
                    help="run only these query ids (e.g. E2q3 E2q4 E2q5). Needed "
                         "when resuming a session: results merge by qid and the "
                         "LATEST run wins, so re-running a whole group would "
                         "overwrite an earlier query's result with a warm-cache "
                         "repeat of it.")
    ap.add_argument("--derive-cuts", action="store_true",
                    help="choose the E2 threshold values from the survey data "
                         "already pulled (E1b/E2q1) and write results/cuts.json. "
                         "Runs nothing remote.")
    args = ap.parse_args()

    if args.derive_cuts:
        return derive_cuts()

    queries = [q for g in (args.only or list(GROUPS)) for q in GROUPS[g]]
    if args.qids:
        want = set(args.qids)
        queries = [q for q in queries if q.qid in want]
        unknown = want - {q.qid for q in queries}
        if unknown:
            print(f"unknown query id(s): {sorted(unknown)}")
            return 1
    # The filtered E2 queries are unauthorable until the cuts exist — and the
    # cuts come from the survey's own output, so on a cold start they CANNOT
    # exist yet. Drop them with a loud note rather than shipping a spec with a
    # literal `None` in its predicate; the second pass picks them up.
    _NEEDS_CUTS = ("E2q3", "E2q4", "E2q5", "E2q6", "E2q7", "E2q8")
    if CUT_UU is None:
        deferred = [q.qid for q in queries if q.qid in _NEEDS_CUTS]
        if deferred:
            queries = [q for q in queries if q.qid not in _NEEDS_CUTS]
            print(f"deferring {', '.join(deferred)}: their cuts are derived from "
                  f"the survey this run produces. After it finishes:\n"
                  f"  python bench/case_study.py --derive-cuts\n"
                  f"  python bench/case_study.py --only E2 --keep-cache\n")
    if not queries:
        print("nothing to run.")
        return 1
    if args.dry:
        for q in queries:
            print(f"[{q.qid}] {q.title}\n    {q.spec}\n")
        return 0
    if args.report_only:
        os.environ.update(ENV)
        import summarize
        qpath = os.path.join(RESULTS, "queries.json")
        if not os.path.exists(qpath):
            print(f"nothing to report: no runs recorded under {RESULTS}.\n"
                  f"Run the session first (needs a held allocation):\n"
                  f"  python bench/case_study.py --only E1 E2 E3")
            return 1
        with open(qpath) as f:
            rows = json.load(f)
        # Refresh the AUTHORED fields (title/why/expect) from the current query
        # definitions. These are descriptions, not measurements: when the reading
        # of a result is sharpened after the fact, session.md should say the
        # better thing without re-running the query to change a comment. Measured
        # fields — status, driver_s, error, report — are never touched here.
        current = {q.qid: q for g in GROUPS.values() for q in g}
        for row in rows:
            q = current.get(row["qid"])
            if q is not None:
                row.update(title=q.title, why=q.why, expect=q.expect)
        with open(qpath, "w") as f:
            json.dump(rows, f, indent=2)
        return _emit(rows, summarize)

    for d in (RESULTS, SPECS, OUT):
        os.makedirs(d, exist_ok=True)
    if not args.keep_cache:
        # A cold start must be genuinely cold, and only the BENCH catalog is
        # touched — the user's own .vislang/cache is never in play. The timings
        # file is deliberately NOT rotated: records accumulate so a session split
        # across several commands still yields one report (rotate by hand to start
        # a fresh series).
        shutil.rmtree(ENV["VISLANG_CACHE"], ignore_errors=True)
    os.environ.update(ENV)

    from mcp_server import run_pipeline
    import summarize

    print(f"case study: {len(queries)} query(ies); results -> {RESULTS}"
          + ("  [cold cache before each query]" if args.cold_each else ""))
    rows = []
    for q in queries:
        if args.cold_each:
            shutil.rmtree(ENV["VISLANG_CACHE"], ignore_errors=True)
        rows.append(run_one(q, run_pipeline))

    # MERGE into any previous invocation's rows (latest run of a qid wins), so a
    # session split across several commands — a group re-run after a fix, a cold
    # sweep of one group — still produces ONE report covering everything.
    path = os.path.join(RESULTS, "queries.json")
    merged = {}
    if os.path.exists(path):
        with open(path) as f:
            for r in json.load(f):
                merged[r["qid"]] = r
    for r in rows:
        merged[r["qid"]] = r
    order = [q.qid for g in GROUPS.values() for q in g]
    rows = sorted(merged.values(),
                  key=lambda r: order.index(r["qid"]) if r["qid"] in order else 999)
    with open(path, "w") as f:
        json.dump(rows, f, indent=2)
    return _emit(rows, summarize)


def _emit(rows, summarize):
    """Tables + the readable report, from whatever runs are already recorded.
    Separated from the session so `--report-only` can rebuild the paper artifacts
    without touching the remote (or the allocation)."""
    recs = summarize.load(ENV["VISLANG_TIMINGS_FILE"])
    all_rows = summarize.rows(recs)
    for name, (title, cols, sel) in summarize.TABLES.items():
        data = sel(all_rows)
        summarize.write_csv(os.path.join(RESULTS, f"{name}.csv"), cols, data)
        print(summarize.fmt_table(title, cols, data))
    # The paper tables in one readable file. When the E1local baseline ran, the
    # remote-reduce column of its comparison comes from the ARCHIVED NYX session's
    # own timings.jsonl — the same file, the same request, measured by the same
    # runtime — so the movement ratio has a measured denominator instead of an
    # assumed one. Absent that file, the comparison table is simply omitted.
    base_rows, base_label = None, ""
    if os.path.exists(COMPARE_TIMINGS):
        base_recs = summarize.load(COMPARE_TIMINGS)
        base_rows = summarize.rows(base_recs)
        started = sorted(r.get("started") or "" for r in base_recs if r.get("started"))
        base_label = (f"{os.path.basename(os.path.dirname(COMPARE_TIMINGS))}"
                      + (f", {started[-1][:10]}" if started else ""))
    contrib = summarize.write_results(os.path.join(RESULTS, "results.csv"), all_rows,
                                      base_rows, base_label)
    print(f"results.csv: E1 transfer band from {', '.join(contrib) or 'no transfers'}")
    write_session_md(rows, recs, os.path.join(RESULTS, "session.md"))
    with open(os.path.join(RESULTS, "COLUMNS.md"), "w") as f:
        f.write(COLUMNS_MD)
    print(f"\nwrote {RESULTS}/session.md (read this first), COLUMNS.md, results.csv, "
          f"runs.csv, cache.csv, estimate.csv, errors.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
