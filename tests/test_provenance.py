"""Provenance records (`sieve-provenance/2`): the YAML template, fingerprints,
the per-form explanation, where each container keeps its record, and the link
to a parent output.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_provenance.py

The assertions worth reading twice: `fingerprint_blind_spot`, which tests the
documented limitation rather than hiding it; `suppressed_when_detached`, the
guarantee that the remote reducer never emits a record about its transient
output; and `oversized_*`, a record too big for an HDF5 attribute, which used to
leave a pointer to a companion file that was never written.
"""

import os
import subprocess
import sys
import tempfile

import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.runtime import provenance
from vislang.formats.dataset_info import DatasetInfo
from vislang.dsl import reset_sinks
from vislang.dsl.forms import (source, region, fields, save, subsample, threshold)
from vislang.interpreter.planner import plan_pipeline
from vislang.interpreter.narrowing import reset_sampling, _get_particle_indices
from vislang.runtime.paths import REPO_ROOT

TMP = tempfile.mkdtemp(prefix="vislang_prov_test_")
PASS = []
SKIP = []

# The template's order, top level and within `logical` / `realization`.
TOP = ["format", "result_summary", "logical", "realization"]
LOGICAL = ["spec", "input", "resolved", "output", "sieve", "libraries", "explanation"]
REALIZATION = ["at", "took_s", "by", "columns_from", "compress", "env", "run"]


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def skip(name, why):
    SKIP.append(name)
    print(f"  -- {name} skipped: {why}")


def have(mod):
    try:
        __import__(mod)
        return None
    except Exception:
        return f"{mod} not installed"


def make_grid(path, shape=(32, 32, 32), dtype=np.float32):
    import h5py
    a = np.arange(int(np.prod(shape))).reshape(shape).astype(dtype)
    with h5py.File(path, "w") as f:
        f.create_dataset("rho", data=a)
        f.create_dataset("T", data=a * 2)
    return path


def make_particles(path, n=4000, seed=0):
    import h5py
    rng = np.random.default_rng(seed)
    with h5py.File(path, "w") as f:
        for c in ("x", "y", "z", "vx"):
            f.create_dataset(c, data=rng.uniform(0, 100, n).astype(np.float32))
        f.create_dataset("tag", data=rng.integers(0, 4, n).astype(np.int64))
    return path


XYZ = ("x", "y", "z")


def run_spec(build, spec_text="# test spec\n"):
    """Execute one sink under an open provenance run, as cli_core does."""
    reset_sinks()
    with provenance.run("spec.py", spec_code=spec_text):
        reset_sampling()
        return plan_pipeline(build(), dry_run=False)


def ordered(d, template):
    """The keys of `d` appear in the template's order (absent keys allowed)."""
    keys = list(d)
    return keys == [k for k in template if k in d] and set(keys) <= set(template)


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------
def test_fingerprint():
    p = os.path.join(TMP, "ident.bin")
    with open(p, "wb") as f:
        f.write(b"A" * 200_000)
    base = provenance.file_fingerprint(p)
    check("fingerprint_fields", list(base) == ["size", "mtime", "head64k_sha256"], base)
    check("fingerprint_hash_length", len(base["head64k_sha256"]) == 16)
    check("fingerprint_mtime_has_offset", base["mtime"][-6] in "+-", base["mtime"])

    st = os.stat(p)

    def rewrite(offset, blob):
        with open(p, "r+b") as f:
            f.seek(offset)
            f.write(blob)
        os.utime(p, (st.st_atime, st.st_mtime))   # hold size AND mtime fixed
        return provenance.file_fingerprint(p)

    head = rewrite(0, b"B" * 16)
    check("fingerprint_catches_head_change",
          head["head64k_sha256"] != base["head64k_sha256"])
    # The documented blind spot: past the head window, at identical size and
    # mtime, a rewrite is invisible. Asserted so the limitation is tested.
    mid = rewrite(100_000, b"C" * 16)
    check("fingerprint_blind_spot_interior_rewrite", mid == head)


def test_data_sha256():
    a = {"x": np.arange(10, dtype=np.float32), "y": np.ones(10, np.float32)}
    h = provenance.data_sha256(a)
    check("data_hash_length", len(h) == 16)
    check("data_hash_key_order_free",
          provenance.data_sha256({"y": a["y"], "x": a["x"]}) == h)
    b = dict(a, x=a["x"].copy())
    b["x"][3] = 99
    check("data_hash_sees_values", provenance.data_sha256(b) != h)
    check("data_hash_sees_dtype",
          provenance.data_sha256(dict(a, x=a["x"].astype(np.float64))) != h)


# ---------------------------------------------------------------------------
# Suppression: nothing is emitted unless a run is open
# ---------------------------------------------------------------------------
def test_suppressed_when_detached():
    reason = have("h5py")
    if reason:
        skip("suppressed_when_detached", reason)
        return
    import h5py
    d = os.path.join(TMP, "detached")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"))
    out = os.path.join(d, "o.hdf5")

    reset_sinks()
    plan_pipeline(save(source(src), out), dry_run=False)   # no run open

    hidden = [f for f in os.listdir(d) if f.startswith(".")]
    check("detached_writes_no_companion", hidden == [], hidden)
    with h5py.File(out) as f:
        check("detached_embeds_nothing", "sieve_provenance" not in f.attrs)
    check("detached_record_is_none",
          provenance.record(DatasetInfo("x", "HDF5", []), out, "hdf5") is None)

    # With the switch off, an open run still emits nothing.
    os.environ["VISLANG_PROVENANCE"] = "0"
    try:
        out2 = os.path.join(d, "o2.hdf5")
        run_spec(lambda: save(source(src), out2))
        check("disabled_records_nothing", provenance.record_for(out2) is None)
    finally:
        os.environ.pop("VISLANG_PROVENANCE", None)


# ---------------------------------------------------------------------------
# The record, end to end through the planner
# ---------------------------------------------------------------------------
def test_record_grid():
    reason = have("h5py")
    if reason:
        skip("record_grid", reason)
        return
    d = os.path.join(TMP, "rec")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (32, 32, 32))
    out = os.path.join(d, "roi.hdf5")
    spec = ('# A slab of rho.\nsrc = source("g.hdf5")\n\n'
            'save(region(fields(src, ["rho"]), x=(8, 24)), "roi.hdf5")\n')
    run_spec(lambda: save(region(fields(source(src), ["rho"]), x=(8, 24)), out),
             spec_text=spec)

    text = provenance.record_text(out)
    rec = provenance.record_for(out)
    check("record_found", rec is not None)
    check("record_format", rec["format"] == "sieve-provenance/2")
    check("record_top_order", list(rec) == TOP, list(rec))
    lg, rz = rec["logical"], rec["realization"]
    check("logical_order", ordered(lg, LOGICAL), list(lg))
    check("realization_order", ordered(rz, REALIZATION), list(rz))
    check("spec_verbatim", lg["spec"] == spec, repr(lg["spec"]))
    check("spec_is_a_block", "  spec: |\n    # A slab of rho.\n" in text)
    check("no_nulls_written", "null" not in text and ": ~" not in text)

    inp = lg["input"]
    check("input_uri_is_authored", inp["uri"] == src)
    check("input_format", inp["format"] == "hdf5")
    check("input_columns", set(inp["columns"]) == {"rho", "T"}, inp["columns"])
    check("input_fingerprint", inp["fingerprint"] == provenance.file_fingerprint(src))
    check("no_parent_link_for_plain_source", "derived_from" not in inp)
    check("no_resolved_when_nothing_open", "resolved" not in lg)

    out_ = lg["output"]
    check("output_shape", out_["shape"] == [16, 32, 32], out_)
    check("output_columns", out_["columns"] == {"rho": "float32"})
    check("output_hash", len(out_["fingerprint"]["data_sha256"]) == 16)
    check("sieve_build", lg["sieve"]["version"] and "commit" in lg["sieve"])
    check("libraries_scoped", set(lg["libraries"]) >= {"h5py", "numpy"}, lg["libraries"])

    ex = lg["explanation"]
    check("explain_fields", "1. fields     keep rho; drop T" in ex, ex)
    check("explain_grid_region", "x index 8 to 23" in ex, ex)
    check("explain_save_preserved", "3. save       write one HDF5 file\n" in ex, ex)
    check("explain_values", ex.rstrip().endswith("Values: all exact."), ex)

    check("summary_first_line",
          rec["result_summary"].startswith("16×32×32 grid × 1 columns"),
          rec["result_summary"])
    check("realization_run", len(rz["run"]) == 12)
    check("realization_columns_from", rz["columns_from"] == {"local": {"columns": ["rho"]}})


def test_record_points_and_seed():
    """A random subsample's seed is recorded, and pinning it reproduces the
    same rows — checked through data_sha256, the thing records link by."""
    reason = have("h5py")
    if reason:
        skip("record_points", reason)
        return
    d = os.path.join(TMP, "pts")
    os.makedirs(d, exist_ok=True)
    src = make_particles(os.path.join(d, "p.hdf5"))

    def build(out):
        n = fields(source(src, positions=XYZ), ["x", "y", "z", "vx"])
        n = region(n, x=(10.0, 90.0))
        n = threshold(n, "tag != 2")
        return save(subsample(n, 0.25), out)

    a = os.path.join(d, "a.npz")
    run_spec(lambda: build(a))
    rec = provenance.record_for(a)
    lg = rec["logical"]
    seed = lg["resolved"]["subsample_random_seed"]
    check("seed_recorded_as_string", isinstance(seed, str) and seed.isdigit(), seed)
    ex = lg["explanation"]
    check("explain_point_region", "x between 10.0 and 90.0" in ex, ex)
    check("explain_threshold_note",
          "keep rows where tag != 2.0\n" in ex
          and "(read for this test only; not in the output)" in ex, ex)
    check("explain_random", "keep a random 25% of the rows" in ex, ex)
    check("explain_conversion", "write one npz file, converted from HDF5" in ex, ex)
    check("output_rows", lg["output"]["rows"] > 0)

    os.environ["VISLANG_SAMPLE_SEED"] = seed
    try:
        b = os.path.join(d, "b.npz")
        run_spec(lambda: build(b))
    finally:
        os.environ.pop("VISLANG_SAMPLE_SEED", None)
    check("pinned_seed_reproduces_rows",
          provenance.record_for(b)["logical"]["output"]["fingerprint"]
          == lg["output"]["fingerprint"])

    c = os.path.join(d, "c.npz")
    run_spec(lambda: save(subsample(source(src, positions=XYZ), 10), c))
    lc = provenance.record_for(c)["logical"]
    check("stride_has_no_seed", "resolved" not in lc, lc.get("resolved"))
    check("explain_stride", "keep every 10th row: rows 0, 10, 20, …" in lc["explanation"])


def test_seeded_sampling():
    reset_sampling(4242)
    a = _get_particle_indices({"particles": 0.1}, 10_000)
    reset_sampling(4242)
    b = _get_particle_indices({"particles": 0.1}, 10_000)
    reset_sampling(99)
    c = _get_particle_indices({"particles": 0.1}, 10_000)
    check("same_seed_same_rows", np.array_equal(a, b))
    check("different_seed_different_rows", not np.array_equal(a, c))
    os.environ.pop("VISLANG_SAMPLE_SEED", None)


# ---------------------------------------------------------------------------
# The explanation, from fields alone (no planner, no data)
# ---------------------------------------------------------------------------
HALO_CHAIN = [
    {"kind": "source", "uri": "ssh://h//d/m000-279.bighaloparticles#3"},
    {"kind": "fields", "keep": ["x", "y", "z", "vx", "vy", "vz"]},
    {"kind": "region", "ranges": [["x", 509.99, 539.99], ["y", 159.51, 189.51],
                                  ["z", 766.43, 796.43]]},
    {"kind": "threshold", "var": "fof_halo_tag", "op": "!=", "value": 11521140891},
    {"kind": "subsample", "uniform": 0.25, "per_axis": []},
    {"kind": "compress", "variables": ["vx", "vy", "vz"], "error_bound": 1.0,
     "mode": "auto"},
    {"kind": "save", "path": "/u/halo_env.vtp"},
]
HALO_COMP = {v: {"method": "SPERR", "mode": "absolute", "error_bound": 1.0}
             for v in ("vx", "vy", "vz")}
HALO_EXPLANATION = """\
Steps
  1. fields     keep x, y, z, vx, vy, vz; drop id, fof_halo_tag
  2. region     keep rows with (bounds inclusive)
                  x between 509.99 and 539.99
                  y between 159.51 and 189.51
                  z between 766.43 and 796.43
  3. threshold  keep rows where fof_halo_tag != 11521140891
                (read for this test only; not in the output)
  4. subsample  keep a random 25% of the rows
  5. compress   vx, vy, vz with SPERR, absolute error bound 1.0
                (mode "auto" chose absolute for each)
  6. save       write VTK PolyData, converted from GenericIO:
                x, y, z as point coordinates,
                and every column as point data

Values: x, y, z exact; vx, vy, vz within 1.0 of the source.
"""


def test_explain_golden():
    """The halo template's steps, rebuilt from recorded fields. A change to a
    form's wording shows up here as a diff, not as silent drift."""
    got = provenance.explain(
        HALO_CHAIN,
        input_columns=["x", "y", "z", "vx", "vy", "vz", "id", "fof_halo_tag"],
        output_columns=["x", "y", "z", "vx", "vy", "vz"],
        in_filetype="GenericIO", fmt="vtk", ext=".vtp", comp=HALO_COMP,
        modes={"vx": "absolute", "vy": "absolute", "vz": "absolute"},
        grid=False, positions=XYZ)
    check("explain_halo_golden", got == HALO_EXPLANATION, "\n" + got)

    grid_chain = [{"kind": "source", "uri": "g.hdf5"},
                  {"kind": "subsample", "uniform": None,
                   "per_axis": [["x", 2], ["y", 4]]},
                  {"kind": "threshold", "var": "rho", "op": ">", "value": 1.5},
                  {"kind": "save", "path": "o.vti"}]
    got = provenance.explain(
        grid_chain, input_columns=["rho", "n"], output_columns=["rho", "n"],
        in_filetype="HDF5", fmt="vtk", ext=".vti", comp={}, modes={}, grid=True,
        geometry={"origin": [0.0, 0.0, 0.0], "spacing": [2.0, 4.0, 1.0]},
        cast=["n"])
    check("explain_grid_per_axis",
          "keep every 2nd cell along x, every 4th cell along y" in got, got)
    check("explain_grid_threshold",
          "keep cells where rho > 1.5; set the rest to NaN" in got, got)
    check("explain_grid_geometry", "origin (0.0, 0.0, 0.0), spacing (2.0, 4.0, 1.0)" in got, got)
    check("explain_cast_values", "n cast to float32 to hold NaN" in got, got)


# ---------------------------------------------------------------------------
# YAML: the layout may never change a value
# ---------------------------------------------------------------------------
def test_yaml_round_trip():
    tricky = {
        "format": "sieve-provenance/2",
        "result_summary": "3 rows × 1 columns\n  float32  x\n",
        "logical": {
            "spec": "  # starts indented\n\tx = 1\n\n\n",
            "input": {"uri": "a: b, #c", "columns": ["x", "y", "yes", "null", "1e5",
                                                     "007", "on", "a,b", "[q]"]},
            "output": {"fingerprint": {"data_sha256": "1234567890123456"},
                       "columns": {"x": "float32"}},
            "sieve": {"version": "0.1.0", "commit": "0153def"},
            "explanation": "no trailing newline",
        },
        "realization": {"at": "2026-09-29T10:41:05-07:00", "took_s": 3,
                        "max": 1e-05, "nan": float("nan"), "big": 10 ** 18,
                        "flag": True, "timesteps": {3: {"size": 1}, 10: {"size": 2}}},
    }
    text = provenance.to_yaml(tricky)
    back = yaml.safe_load(text)
    nan = back["realization"].pop("nan")
    expect = provenance._plain(tricky)
    expect["realization"].pop("nan")
    check("yaml_round_trips", back == expect, text)
    check("yaml_nan", nan != nan)
    check("yaml_not_json_fallback", not text.lstrip().startswith("{"), text[:80])
    check("yaml_hash_quoted", 'data_sha256: "1234567890123456"' in text, text)

    a = provenance.to_yaml({"result_summary": "1 rows × 2\n", "spec": "s = 'é'\n"},
                           ascii=True)
    check("yaml_ascii_only", a.isascii(), a)
    back = yaml.safe_load(a)
    check("yaml_ascii_folds_our_symbols", back["result_summary"] == "1 rows x 2\n")
    check("yaml_ascii_keeps_user_text", back["spec"] == "s = 'é'\n")


# ---------------------------------------------------------------------------
# commit_dirty must ignore the spec
# ---------------------------------------------------------------------------
def test_commit_dirty_scope():
    """spec.py is edited in place on every run and lives in the repo, so a
    repo-wide dirty check would report dirty always. Verify the scope excludes
    it: with spec.py modified, it appears in an unscoped status and not in the
    scoped one."""
    spec = os.path.join(REPO_ROOT, "spec.py")
    if not os.path.exists(spec):
        skip("commit_dirty_scope", "no spec.py in the repo")
        return
    original = open(spec).read()
    try:
        with open(spec, "a") as f:
            f.write("\n# provenance scope probe\n")

        def porcelain(args):
            out = subprocess.run(["git", "-C", REPO_ROOT, "status", "--porcelain"] + args,
                                 capture_output=True, text=True, timeout=10)
            return out.stdout

        unscoped = porcelain(["--", "spec.py"])
        scoped = porcelain(["--", "vislang", "mcp_server.py", "cli.py",
                            "vislang_exec.py"])
        check("spec_edit_is_visible_unscoped", "spec.py" in unscoped, unscoped)
        check("spec_edit_excluded_from_scope", "spec.py" not in scoped, scoped)
    finally:
        with open(spec, "w") as f:
            f.write(original)


# ---------------------------------------------------------------------------
# Where the record lives
# ---------------------------------------------------------------------------
def test_sidecar_placement():
    check("sidecar_is_dot_prefixed",
          os.path.basename(provenance.sidecar_path("/a/b/roi.vti"))
          == ".roi.vti.sieve-prov.yaml")
    folder = os.path.join(TMP, "asfolder")
    os.makedirs(folder, exist_ok=True)
    check("folder_sidecar_name",
          provenance.sidecar_path(folder).endswith("/.sieve-provenance.yaml"))


def _embed_case(ext, src, d, build=None):
    out = os.path.join(d, f"emb{ext}")
    run_spec(build(out) if build else (lambda: save(source(src), out)))
    return out, provenance.record_for(out)


def test_embedding():
    reason = have("h5py")
    if reason:
        skip("embedding", reason)
        return
    from vislang.output.save import read_embedded
    d = os.path.join(TMP, "embed")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))

    for ext in (".hdf5", ".npz"):
        out, rec = _embed_case(ext, src, d)
        check(f"embedded{ext}", rec is not None and read_embedded(out), ext)
        check(f"no_sidecar_when_embedded{ext}",
              not os.path.exists(provenance.sidecar_path(out)))

    if have("pyvista"):
        skip("embedding_vtk", "pyvista not installed")
    else:
        for ext in (".vti", ".vtkhdf"):
            out, rec = _embed_case(ext, src, d)
            # Assert the CONTRACT (the record comes back), not the mechanism —
            # .vtkhdf ImageData needs a different one than .vti, because VTK's
            # own writer drops field data for grids.
            check(f"embedded{ext}", rec is not None, ext)
            check(f"no_sidecar_when_embedded{ext}",
                  not os.path.exists(provenance.sidecar_path(out)))
            check(f"vtk_record_ascii{ext}", read_embedded(out).isascii())
        # Legacy .vtk: the reader would scan free text for its keywords, so
        # the record goes beside the file instead.
        out, rec = _embed_case(".vtk", src, d)
        check("legacy_vtk_record_found", rec is not None)
        check("legacy_vtk_uses_sidecar", os.path.exists(provenance.sidecar_path(out))
              and read_embedded(out) is None)
        psrc = make_particles(os.path.join(d, "p.hdf5"), n=500)
        out, rec = _embed_case(".vtp", psrc, d,
                               build=lambda o: (lambda: save(
                                   source(psrc, positions=XYZ), o)))
        check("embedded.vtp", rec is not None)
        check("vtp_explains_points",
              "x, y, z as point coordinates," in rec["logical"]["explanation"])

    # A record inside an npz must not surface as a variable or an attribute.
    from vislang.formats.inspect import inspect_file
    info = inspect_file(os.path.join(d, "emb.npz"))
    check("npz_record_hidden_from_variables",
          not any(v.startswith("sieve_") for v in info.variables), info.variables)
    check("npz_record_hidden_from_attributes",
          not any(k.startswith("sieve_") for k in info.attributes))
    # Nor does an HDF5 one ride along as a source attribute.
    check("hdf5_record_not_a_source_attribute",
          "sieve_provenance" not in inspect_file(os.path.join(d, "emb.hdf5")).attributes)


def test_npz_string_attr_regression():
    """A one-element string array in an npz used to crash inspect() outright —
    the reader coerced every small array to float. Independent of provenance."""
    from vislang.formats.inspect import inspect_file
    p = os.path.join(TMP, "stringattr.npz")
    np.savez(p, x=np.arange(2000, dtype=np.float32), note=np.array(["hello"]))
    info = inspect_file(p)
    check("npz_tolerates_string_attr", info.attributes.get("note") == "hello",
          info.attributes)


def test_hdf5_attrs():
    reason = have("h5py")
    if reason:
        skip("hdf5_attrs", reason)
        return
    import h5py
    d = os.path.join(TMP, "h5attrs")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))
    out = os.path.join(d, "o.hdf5")
    run_spec(lambda: save(region(source(src), x=(2, 6)), out))
    with h5py.File(out) as f:
        check("hdf5_root_record_is_yaml",
              str(f.attrs["sieve_provenance"]).startswith("format: sieve-provenance/2"))
        check("hdf5_history", "sieve" in str(f.attrs["history"]))
        check("hdf5_no_flat_attrs", "sieve_version" not in f.attrs, list(f.attrs))
        check("hdf5_no_per_variable_attrs", not list(f["rho"].attrs),
              dict(f["rho"].attrs))


def test_oversized_record():
    """An HDF5 attribute caps out near 64 KiB. The record goes to a companion
    file and the file keeps a pointer — and the companion must actually exist."""
    reason = have("h5py")
    if reason:
        skip("oversized", reason)
        return
    import h5py
    d = os.path.join(TMP, "big")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))
    spec = "# " + "x" * 70_000 + "\n"
    exts = [".hdf5"] + ([] if have("netCDF4") else [".nc"])
    for ext in exts:
        out = os.path.join(d, f"o{ext}")
        run_spec(lambda: save(source(src), out), spec_text=spec)
        with h5py.File(out) as f:
            raw = f.attrs["sieve_provenance"]
            stub = yaml.safe_load(raw.decode() if isinstance(raw, bytes) else raw)
        check(f"oversized_stub{ext}",
              stub.get("sidecar") == os.path.basename(provenance.sidecar_path(out)), stub)
        check(f"oversized_sidecar_written{ext}",
              os.path.exists(provenance.sidecar_path(out)))
        rec = provenance.record_for(out)
        check(f"oversized_record_found{ext}", rec and rec["logical"]["spec"] == spec)
        check(f"sidecar_fingerprints_the_file{ext}",
              rec["logical"]["output"]["fingerprint"]["size"] == os.path.getsize(out))
    if len(exts) == 1:
        skip("oversized.nc", "netCDF4 not installed")


def test_timeseries_folder():
    """A companion file inside an output folder must not be mistaken for a
    timestep. Timeseries discovery matches `#N` anywhere in a filename, so an
    undotted name would be enumerated as a second copy of that step."""
    reason = have("h5py")
    if reason:
        skip("timeseries_folder", reason)
        return
    from vislang.formats.inspect import timestep_files
    src = os.path.join(TMP, "series")
    os.makedirs(src, exist_ok=True)
    for t in range(3):
        make_grid(os.path.join(src, f"snap#{t}.hdf5"), (8, 8, 8))
    out = os.path.join(TMP, "series_out")
    run_spec(lambda: save(source(src), out))

    check("folder_record_written",
          os.path.exists(os.path.join(out, ".sieve-provenance.yaml")))
    labels = [int(l) for l, _ in timestep_files(out)]
    check("folder_still_reads_as_timeseries", labels == [0, 1, 2], labels)
    rec = provenance.record_for(out)
    lg = rec["logical"]
    check("folder_input_timesteps", sorted(lg["input"]["timesteps"]) == [0, 1, 2],
          lg["input"])
    check("folder_input_step_fingerprint",
          lg["input"]["timesteps"][1] == provenance.file_fingerprint(
              os.path.join(src, "snap#1.hdf5")))
    check("folder_output_timesteps",
          all(len(v["data_sha256"]) == 16 for v in lg["output"]["timesteps"].values()))
    check("folder_explains_per_timestep",
          "write one HDF5 file per timestep" in lg["explanation"], lg["explanation"])

    step = provenance.record_for(os.path.join(out, "timestep#2.hdf5"))
    check("step_record_names_its_own_input",
          step["logical"]["input"]["uri"] == os.path.join(src, "snap#2.hdf5"),
          step["logical"]["input"])
    check("step_hash_matches_folder",
          step["logical"]["output"]["fingerprint"]["data_sha256"]
          == lg["output"]["timesteps"][2]["data_sha256"])


# ---------------------------------------------------------------------------
# netCDF-4
# ---------------------------------------------------------------------------
def test_netcdf():
    if have("netCDF4") or have("h5py"):
        skip("netcdf", "netCDF4 or h5py not installed")
        return
    from netCDF4 import Dataset
    from vislang.formats.inspect import inspect_file
    import vislang.output.save as save_mod

    check("resolve_nc", save_mod._resolve("o.nc", "HDF5") == ("netcdf4", "o.nc", True))
    check("nc_is_not_a_preservation_target",
          "netcdf4" not in save_mod._FILETYPE_FORMAT.values())

    d = os.path.join(TMP, "nc")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 10, 12))
    out = os.path.join(d, "o.nc")
    run_spec(lambda: save(region(fields(source(src), ["rho"]), x=(2, 6)), out))

    with Dataset(out) as ds:
        check("nc_named_dimensions",
              {k: len(v) for k, v in ds.dimensions.items()} == {"x": 4, "y": 10, "z": 12},
              dict(ds.dimensions))
        check("nc_axis_order_is_stated", "index space" in ds.sieve_axis_order)
        check("nc_history", "sieve" in ds.history)
        check("nc_record", ds.getncattr("sieve_provenance").startswith("format:"))

    # netCDF-4 is HDF5 underneath, so Sieve claims it by magic — but the
    # dimension datasets netcdf-c materializes must not appear as variables.
    info = inspect_file(out)
    check("nc_readback_variables_only", info.variables == ["rho"], info.variables)
    check("nc_readback_grid", info.dimensions.get("grid") == (4, 10, 12),
          info.dimensions)
    rec = provenance.record_for(out)
    check("nc_record_readable", rec is not None)
    check("nc_explains_dims", "dimensions x, y, z in Sieve's index order"
          in rec["logical"]["explanation"], rec["logical"]["explanation"])

    with Dataset(out) as ds:
        check("nc_no_invented_coords", "x" not in ds.variables, list(ds.variables))


def test_netcdf_coords_from_geometry():
    """When the source states a geometry, it becomes real coordinate variables —
    and those are exactly the datasets the read-back skip has to handle."""
    if have("netCDF4") or have("pyvista"):
        skip("netcdf_coords", "netCDF4 or pyvista not installed")
        return
    from netCDF4 import Dataset
    import pyvista as pv
    from vislang.formats.inspect import inspect_file
    d = os.path.join(TMP, "nccoord")
    os.makedirs(d, exist_ok=True)
    g = pv.ImageData(dimensions=(6, 6, 6), origin=(100.0, 200.0, 300.0),
                     spacing=(2.0, 2.0, 2.0))
    g.point_data["rho"] = np.arange(216, dtype=np.float32)
    src = os.path.join(d, "src.vti")
    g.save(src)
    out = os.path.join(d, "o.nc")
    run_spec(lambda: save(source(src), out))
    with Dataset(out) as ds:
        check("nc_coords_written", "x" in ds.variables)
        check("nc_coords_from_origin_spacing",
              list(ds.variables["x"][:3]) == [100.0, 102.0, 104.0],
              list(ds.variables["x"][:3]))
    info = inspect_file(out)
    check("nc_coords_not_listed_as_variables", info.variables == ["rho"],
          info.variables)
    geom = provenance.record_for(out)["logical"]["output"]["geometry"]
    check("output_geometry_recorded", geom["origin"] == [100.0, 200.0, 300.0], geom)


def test_grid_threshold_cast():
    """An integer grid threshold is cast to float32 to hold NaN; the record
    says so rather than calling those values exact."""
    reason = have("h5py")
    if reason:
        skip("grid_threshold_cast", reason)
        return
    d = os.path.join(TMP, "cast")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8), dtype=np.int32)
    out = os.path.join(d, "o.hdf5")
    run_spec(lambda: save(threshold(source(src), "rho > 100"), out))
    ex = provenance.record_for(out)["logical"]["explanation"]
    check("cast_named_in_values", "cast to float32 to hold NaN" in ex, ex)


# ---------------------------------------------------------------------------
# Ancestry: a link by data_sha256, never a copy
# ---------------------------------------------------------------------------
def test_parent_link():
    reason = have("h5py")
    if reason:
        skip("parent_link", reason)
        return
    d = os.path.join(TMP, "chain")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "orig.hdf5"), (16, 16, 16))
    first = os.path.join(d, "a.hdf5")
    run_spec(lambda: save(region(source(src), x=(0, 12)), first),
             spec_text="# first step\n")
    second = os.path.join(d, "b.hdf5")
    run_spec(lambda: save(region(source(first), x=(0, 6)), second),
             spec_text="# second step\n")

    parent = provenance.record_for(first)["logical"]["output"]["fingerprint"]
    child = provenance.record_for(second)
    check("child_links_parent_by_hash",
          child["logical"]["input"]["derived_from"] == parent["data_sha256"],
          child["logical"]["input"])
    text = provenance.record_text(second)
    check("child_does_not_copy_parent", "# first step" not in text)

    # A GenericIO parent keeps its record beside the file, not in it; the
    # link must still be found.
    import vislang.output.save as save_mod
    loaded = DatasetInfo("p", "GenericIO", ["x", "y", "z"])
    loaded.data = {c: np.arange(100, dtype=np.float32) for c in "xyz"}
    loaded.attributes = {"phys_scale": [1.0, 1.0, 1.0]}
    if save_mod._genericio_blocker(loaded) is not None:
        skip("gio_parent_link", save_mod._genericio_blocker(loaded))
        return
    # Written straight through the sink, as the planner would, because the
    # writer needs a box size (phys_scale) that a test HDF5 file cannot carry.
    gio = os.path.join(d, "a.gio")
    reset_sinks()
    with provenance.run("spec.py", spec_code="# gio parent\n"):
        with provenance.pipeline(save(source(src), gio)):
            save_mod.save_loaded(loaded, gio)
    reset_sinks()
    check("gio_record_is_a_sidecar", os.path.exists(provenance.sidecar_path(gio)))
    child = os.path.join(d, "c.npz")
    run_spec(lambda: save(source(gio), child))
    check("gio_parent_linked",
          provenance.record_for(child)["logical"]["input"].get("derived_from")
          == provenance.record_for(gio)["logical"]["output"]["fingerprint"]["data_sha256"])


def test_mcp_reader():
    reason = have("h5py")
    if reason:
        skip("mcp_reader", reason)
        return
    from vislang.server.cli_core import do_provenance
    d = os.path.join(TMP, "mcp")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))
    out = os.path.join(d, "o.hdf5")
    spec = f"save(source({src!r}), {out!r})\n"
    run_spec(lambda: save(source(src), out), spec_text=spec)
    check("mcp_returns_yaml", do_provenance(out).startswith("format: sieve-provenance/2"))
    check("mcp_spec_only", do_provenance(out, spec_only=True) == spec)
    check("mcp_no_record_is_an_error", do_provenance(src).startswith("ERROR"))


if __name__ == "__main__":
    print("provenance")
    test_fingerprint()
    test_data_sha256()
    test_suppressed_when_detached()
    test_record_grid()
    test_record_points_and_seed()
    test_seeded_sampling()
    test_explain_golden()
    test_yaml_round_trip()
    test_commit_dirty_scope()
    test_sidecar_placement()
    test_embedding()
    test_npz_string_attr_regression()
    test_hdf5_attrs()
    test_oversized_record()
    test_timeseries_folder()
    test_netcdf()
    test_netcdf_coords_from_geometry()
    test_grid_threshold_cast()
    test_parent_link()
    test_mcp_reader()
    print(f"\n{len(PASS)} passed, {len(SKIP)} skipped  (artifacts in {TMP})")
