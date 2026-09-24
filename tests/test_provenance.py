"""Provenance records: identity, suppression, seeding, and the companion file.

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_provenance.py

Covers Stage 1 — the record is assembled and written as a companion file for
every format. The two assertions worth reading twice are `identity_blind_spot`,
which tests the documented limitation rather than hiding it, and
`suppressed_when_detached`, which is the guarantee that the remote reducer never
emits a record about its transient output.
"""

import json
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.runtime import provenance
from vislang.formats.dataset_info import DatasetInfo
from vislang.dsl import reset_sinks, collected_sinks
from vislang.dsl.forms import source, region, fields, save
from vislang.dsl.ast_serialize import describe_plan, to_plan, from_plan_json
from vislang.interpreter.planner import plan_pipeline
from vislang.interpreter.narrowing import reset_sampling, _get_particle_indices
from vislang.runtime.paths import REPO_ROOT

TMP = tempfile.mkdtemp(prefix="vislang_prov_test_")
PASS = []
SKIP = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def skip(name, why):
    SKIP.append(name)
    print(f"  -- {name} skipped: {why}")


def have_h5py():
    try:
        import h5py  # noqa: F401
        return None
    except Exception:
        return "h5py not installed"


def make_grid(path, shape=(32, 32, 32)):
    import h5py
    a = np.arange(int(np.prod(shape)), dtype=np.float32).reshape(shape)
    with h5py.File(path, "w") as f:
        f.create_dataset("rho", data=a)
        f.create_dataset("T", data=a * 2)
    return path


def run_spec(build, spec_text="# test spec\n"):
    """Execute one sink under an open provenance run, as cli_core does."""
    reset_sinks()
    with provenance.run("spec.py", spec_code=spec_text):
        return plan_pipeline(build(), dry_run=False)


# ---------------------------------------------------------------------------
# Source identity
# ---------------------------------------------------------------------------
def test_identity():
    p = os.path.join(TMP, "ident.bin")
    with open(p, "wb") as f:
        f.write(b"A" * 200_000)
    base = provenance.local_identity(p)
    check("identity_has_method", base["method"] == "size+mtime+head64k+tail64k-md5")
    check("identity_has_id", len(base["source_id"]) == 16, base["source_id"])
    check("identity_no_full_hash_by_default", base["content_sha256"] is None)
    check("identity_states_its_caveat", "same second" in base["caveat"])

    st = os.stat(p)

    def rewrite(offset, blob):
        with open(p, "r+b") as f:
            f.seek(offset)
            f.write(blob)
        os.utime(p, (st.st_atime, st.st_mtime))   # hold size AND mtime fixed
        return provenance.local_identity(p)

    # A change inside the head window is caught even with size and mtime frozen.
    head_changed = rewrite(0, b"B" * 16)
    check("identity_catches_head_change",
          head_changed["source_id"] != base["source_id"])
    check("identity_head_md5_changed",
          head_changed["head_md5"] != base["head_md5"])

    # The documented blind spot: an interior rewrite at identical size and mtime
    # is invisible. Asserted so the limitation is tested, not merely written down.
    mid = rewrite(100_000, b"C" * 16)
    check("identity_blind_spot_interior_rewrite",
          mid["source_id"] == head_changed["source_id"],
          "interior change should NOT be detected by the cheap identity")

    # The tail window is why we hash both ends rather than just the head.
    tail = rewrite(199_000, b"D" * 16)
    check("identity_catches_tail_change",
          tail["tail_md5"] != head_changed["tail_md5"])

    full = provenance.local_identity(p, full_hash=True)
    check("identity_full_hash_on_request",
          full["content_sha256"] and "sha256" in full["method"])
    check("identity_full_hash_is_sha256", len(full["content_sha256"]) == 64)

    diffs = dict((f, (a, b)) for f, a, b in
                 provenance.compare_identity(base, tail))
    check("compare_identity_names_fields", "head_md5" in diffs, diffs)
    check("compare_identity_empty_when_same",
          provenance.compare_identity(base, base) == [])


# ---------------------------------------------------------------------------
# Suppression: nothing is emitted unless a run is open
# ---------------------------------------------------------------------------
def test_suppressed_when_detached():
    reason = have_h5py()
    if reason:
        skip("suppressed_when_detached", reason)
        return
    d = os.path.join(TMP, "detached")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"))
    out = os.path.join(d, "o.hdf5")

    reset_sinks()
    plan_pipeline(save(source(src), out), dry_run=False)   # no run open

    hidden = [f for f in os.listdir(d) if f.startswith(".")]
    check("detached_writes_no_companion", hidden == [], hidden)
    check("detached_record_is_none",
          provenance.record(DatasetInfo("x", "HDF5", []), out, "hdf5") is None)

    # With the switch off, an open run still emits nothing.
    os.environ["VISLANG_PROVENANCE"] = "0"
    try:
        out2 = os.path.join(d, "o2.hdf5")
        run_spec(lambda: save(source(src), out2))
        check("disabled_writes_no_companion",
              not os.path.exists(provenance.sidecar_path(out2)))
    finally:
        os.environ.pop("VISLANG_PROVENANCE", None)


# ---------------------------------------------------------------------------
# The record, end to end through the planner
# ---------------------------------------------------------------------------
def test_record():
    reason = have_h5py()
    if reason:
        skip("record", reason)
        return
    d = os.path.join(TMP, "rec")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (32, 32, 32))
    out = os.path.join(d, "roi.hdf5")
    spec = 'src = source("g.hdf5")\nsave(region(src, x=(8, 24)), "roi.hdf5")\n'
    run_spec(lambda: save(region(fields(source(src), ["rho"]), x=(8, 24)), out),
             spec_text=spec)

    rec = provenance.record_for(out)
    check("record_found", rec is not None)
    check("record_versioned", rec["sieve_provenance"] == 1)

    check("producer_version", rec["producer"]["version"])
    check("producer_commit_present", "commit" in rec["producer"])
    check("producer_libraries_scoped",
          "numpy" in rec["producer"]["libraries"]
          and "h5py" in rec["producer"]["libraries"], rec["producer"]["libraries"])

    check("source_uri_is_authored", rec["source"]["uri"] == src)
    check("source_identity_present", rec["source"]["identity"]["source_id"])
    check("source_schema_lists_variables",
          set(rec["source"]["schema"]["variables"]) == {"rho", "T"})

    check("spec_embedded", rec["transform"]["spec"] == spec)
    check("transform_summary_names_source_and_sink",
          src in rec["transform"]["summary"]
          and "roi.hdf5" in rec["transform"]["summary"],
          rec["transform"]["summary"])

    # The lowered narrowing is the interpreter's decision, and post-ops must be
    # data rather than class names.
    low = rec["transform"]["lowered"]
    check("lowered_records_projection", low["project"] == ["rho"], low)
    check("lowered_records_crop", low["grid_ranges"][0] == [8, 24, 1], low)

    # Per-variable lineage: shape after vs shape in the source.
    v = rec["variables"]["rho"]
    check("variable_shape", v["shape"] == [16, 32, 32], v)
    check("variable_source_shape", v["source_shape"] == [32, 32, 32], v)
    check("variable_source_name", v["source_variable"] == "rho")

    check("output_path_is_what_was_written", rec["output"]["path"] == os.path.abspath(out))
    check("output_not_degraded", rec["output"]["degraded_from"] is None)

    # The plan must rebuild, which is what makes rerun possible. Rebuilding
    # registers a sink, so bracket it.
    reset_sinks()
    rebuilt = from_plan_json(json.dumps(rec["transform"]["plan"]))
    reset_sinks()
    check("plan_round_trips", rebuilt is not None and rebuilt.kind == "save")
    check("plan_rebuild_left_no_sinks", collected_sinks() == [])


def test_post_ops_are_data():
    """A threshold's variable, operator and value must survive into the record —
    the planner's trace echo reduces post-ops to class names, which would lose
    exactly the part that says what was filtered."""
    from vislang.interpreter.narrowing import RowMask, Predicate, BBox, RowSample
    op = RowMask(bbox=BBox(lo=(0, 0, 0), hi=(1, 1, 1)),
                 predicates=(Predicate("rho", ">", 1.5),))
    d = provenance._post_op(op)
    check("post_op_kind", d["op"] == "RowMask")
    check("post_op_keeps_predicate",
          d["predicates"] == [{"var": "rho", "op": ">", "value": 1.5}], d)
    check("post_op_keeps_bbox", d["bbox"]["hi"] == [1, 1, 1], d)
    check("post_op_keeps_factor", provenance._post_op(RowSample(0.1))["factor"] == 0.1)


# ---------------------------------------------------------------------------
# Seeded sampling
# ---------------------------------------------------------------------------
def test_seeded_sampling():
    reset_sampling(4242)
    a = _get_particle_indices({"particles": 0.1}, 10_000)
    reset_sampling(4242)
    b = _get_particle_indices({"particles": 0.1}, 10_000)
    reset_sampling(99)
    c = _get_particle_indices({"particles": 0.1}, 10_000)
    check("same_seed_same_rows", np.array_equal(a, b))
    check("different_seed_different_rows", not np.array_equal(a, c))
    check("seed_is_reported", reset_sampling(7) == 7)
    os.environ.pop("VISLANG_SAMPLE_SEED", None)


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
# Companion file placement
# ---------------------------------------------------------------------------
def test_sidecar_placement():
    check("sidecar_is_dot_prefixed",
          os.path.basename(provenance.sidecar_path("/a/b/roi.vti"))
          == ".roi.vti.sieve-prov.json")
    folder = os.path.join(TMP, "asfolder")
    os.makedirs(folder, exist_ok=True)
    check("folder_sidecar_name",
          provenance.sidecar_path(folder).endswith("/.sieve-provenance.json"))


def test_timeseries_folder():
    """A companion file inside an output folder must not be mistaken for a
    timestep. Timeseries discovery matches `#N` anywhere in a filename, so an
    undotted name would be enumerated as a second copy of that step."""
    reason = have_h5py()
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
          os.path.exists(os.path.join(out, ".sieve-provenance.json")))
    labels = [int(l) for l, _ in timestep_files(out)]
    check("folder_still_reads_as_timeseries", labels == [0, 1, 2], labels)
    rec = provenance.record_for(out)
    check("folder_record_readable", rec and rec["sieve_provenance"] == 1)


# ---------------------------------------------------------------------------
# Stage 2: the record inside the file
# ---------------------------------------------------------------------------
def _embed_case(ext, src, d):
    out = os.path.join(d, f"emb{ext}")
    run_spec(lambda: save(source(src), out))
    rec = provenance.record_for(out)
    return out, rec


def test_embedding():
    reason = have_h5py()
    if reason:
        skip("embedding", reason)
        return
    d = os.path.join(TMP, "embed")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))

    for ext, how in ((".hdf5", "hdf5-attrs"), (".npz", "npz-key")):
        out, rec = _embed_case(ext, src, d)
        check(f"embedded{ext}", rec is not None, ext)
        check(f"embedding_marker{ext}",
              rec["output"]["embedding"] == how, rec["output"]["embedding"])
        check(f"no_sidecar_when_embedded{ext}",
              not os.path.exists(provenance.sidecar_path(out)))

    try:
        import pyvista  # noqa: F401
    except Exception:
        skip("embedding_vtk", "pyvista not installed")
    else:
        for ext in (".vti", ".vtk", ".vtkhdf"):
            out, rec = _embed_case(ext, src, d)
            # Assert the CONTRACT (the record comes back), not the mechanism —
            # .vtkhdf ImageData needs a different one than .vti, because VTK's
            # own writer drops field data for grids.
            check(f"embedded{ext}", rec is not None, ext)
            check(f"no_sidecar_when_embedded{ext}",
                  not os.path.exists(provenance.sidecar_path(out)))

    # A record inside an npz must not surface as a variable, and must not break
    # the reader's attribute coercion.
    from vislang.formats.inspect import inspect_file
    npz = os.path.join(d, "emb.npz")
    info = inspect_file(npz)
    check("npz_record_hidden_from_variables",
          not any(v.startswith("sieve_") for v in info.variables), info.variables)
    check("npz_record_hidden_from_attributes",
          not any(k.startswith("sieve_") for k in info.attributes))


def test_npz_string_attr_regression():
    """A one-element string array in an npz used to crash inspect() outright —
    the reader coerced every small array to float. Independent of provenance."""
    from vislang.formats.inspect import inspect_file
    p = os.path.join(TMP, "stringattr.npz")
    np.savez(p, x=np.arange(2000, dtype=np.float32), note=np.array(["hello"]))
    info = inspect_file(p)
    check("npz_tolerates_string_attr", info.attributes.get("note") == "hello",
          info.attributes)


def test_hdf5_per_variable_attrs():
    reason = have_h5py()
    if reason:
        skip("hdf5_per_variable_attrs", reason)
        return
    import h5py
    d = os.path.join(TMP, "h5attrs")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))
    out = os.path.join(d, "o.hdf5")
    run_spec(lambda: save(region(source(src), x=(2, 6)), out))
    with h5py.File(out) as f:
        check("hdf5_root_record", "sieve_provenance" in f.attrs)
        check("hdf5_flat_attrs",
              "sieve_version" in f.attrs and "history" in f.attrs, dict(f.attrs))
        check("hdf5_var_source_shape",
              json.loads(f["rho"].attrs["sieve_source_shape"]) == [8, 8, 8],
              dict(f["rho"].attrs))


# ---------------------------------------------------------------------------
# Stage 3: netCDF-4
# ---------------------------------------------------------------------------
def test_netcdf():
    try:
        from netCDF4 import Dataset
    except Exception:
        skip("netcdf", "netCDF4 not installed")
        return
    if have_h5py():
        skip("netcdf", "h5py not installed")
        return
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
        check("nc_record", ds.getncattr("sieve_provenance"))
        check("nc_var_source_shape",
              json.loads(ds.variables["rho"].sieve_source_shape) == [8, 10, 12])

    # netCDF-4 is HDF5 underneath, so Sieve claims it by magic — but the
    # dimension datasets netcdf-c materializes must not appear as variables.
    info = inspect_file(out)
    check("nc_readback_variables_only", info.variables == ["rho"], info.variables)
    check("nc_readback_grid", info.dimensions.get("grid") == (4, 10, 12),
          info.dimensions)
    check("nc_record_readable", provenance.record_for(out) is not None)

    # No geometry on the source means no coordinate variables invented.
    with Dataset(out) as ds:
        check("nc_no_invented_coords", "x" not in ds.variables, list(ds.variables))


def test_netcdf_coords_from_geometry():
    """When the source states a geometry, it becomes real coordinate variables —
    and those are exactly the datasets the read-back skip has to handle."""
    try:
        from netCDF4 import Dataset
        import pyvista as pv
    except Exception:
        skip("netcdf_coords", "netCDF4 or pyvista not installed")
        return
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


# ---------------------------------------------------------------------------
# Stage 4: the commands
# ---------------------------------------------------------------------------
def test_cli_provenance_and_rerun():
    reason = have_h5py()
    if reason:
        skip("cli", reason)
        return
    from vislang.server.cli_core import do_provenance, do_rerun
    import h5py

    d = os.path.join(TMP, "cli")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "g.hdf5"), (8, 8, 8))
    out = os.path.join(d, "roi.hdf5")
    spec = f'save(region(source({src!r}), x=(2, 6)), {out!r})\n'
    run_spec(lambda: save(region(source(src), x=(2, 6)), out), spec_text=spec)

    report = do_provenance(out)
    check("cli_reports_source", src in report)
    check("cli_reports_unchanged", "UNCHANGED" in report, report)
    check("cli_reports_transform", "region" in report)
    check("cli_spec_only", do_provenance(out, spec_only=True) == spec)
    check("cli_json_parses", json.loads(do_provenance(out, as_json=True))
          ["sieve_provenance"] == 1)
    check("cli_missing_record_is_an_error",
          do_provenance(src).startswith("ERROR"))

    # Rerunning in place would destroy the record mid-write.
    check("rerun_refuses_to_overwrite_itself",
          do_rerun(out).startswith("ERROR") and "overwrite" in do_rerun(out))

    again = os.path.join(d, "roi2.hdf5")
    rep = do_rerun(out, out=again)
    check("rerun_ok", rep.startswith("Status: OK"), rep)
    with h5py.File(out) as a, h5py.File(again) as b:
        check("rerun_reproduces_data", np.array_equal(a["rho"][:], b["rho"][:]))
    check("rerun_left_no_sinks", collected_sinks() == [])
    chained = provenance.record_for(again)
    check("rerun_records_its_origin",
          (chained["run"]["rerun_of"] or {}).get("record_id"), chained["run"])

    # Change the source: the rerun must refuse rather than quietly produce a
    # different result wearing the same provenance.
    with h5py.File(src, "r+") as f:
        f["rho"][0, 0, 0] = 999.0
    third = os.path.join(d, "roi3.hdf5")
    rep = do_rerun(out, out=third)
    check("rerun_refuses_changed_source",
          rep.startswith("Status: SOURCE CHANGED"), rep)
    check("rerun_names_what_changed", "head_md5" in rep or "mtime" in rep, rep)
    check("rerun_wrote_nothing", not os.path.exists(third))
    rep = do_rerun(out, out=third, force=True)
    check("rerun_force_proceeds", rep.startswith("Status: OK") and os.path.exists(third))


def test_derived_from_chain():
    """Narrowing an output again must keep the trail back to the original."""
    reason = have_h5py()
    if reason:
        skip("derived_from_chain", reason)
        return
    d = os.path.join(TMP, "chain")
    os.makedirs(d, exist_ok=True)
    src = make_grid(os.path.join(d, "orig.hdf5"), (16, 16, 16))
    first = os.path.join(d, "a.hdf5")
    run_spec(lambda: save(region(source(src), x=(0, 12)), first))
    second = os.path.join(d, "b.hdf5")
    run_spec(lambda: save(region(source(first), x=(0, 6)), second))

    rec = provenance.record_for(second)
    check("chain_immediate_source", rec["source"]["uri"] == first)
    chain = rec.get("derived_from") or []
    check("chain_has_ancestor", len(chain) >= 1, chain)
    check("chain_reaches_the_original",
          any((a.get("source") or {}).get("uri") == src for a in chain),
          [(a.get("source") or {}).get("uri") for a in chain])


if __name__ == "__main__":
    print("provenance")
    test_identity()
    test_suppressed_when_detached()
    test_record()
    test_post_ops_are_data()
    test_seeded_sampling()
    test_commit_dirty_scope()
    test_sidecar_placement()
    test_timeseries_folder()
    test_embedding()
    test_npz_string_attr_regression()
    test_hdf5_per_variable_attrs()
    test_netcdf()
    test_netcdf_coords_from_geometry()
    test_cli_provenance_and_rerun()
    test_derived_from_chain()
    print(f"\n{len(PASS)} passed, {len(SKIP)} skipped  (artifacts in {TMP})")
