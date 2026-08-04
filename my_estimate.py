"""Estimate the cost of rendering a dataset BEFORE loading it.

Rendering is headless k3d — the array is shipped to the browser and ray-marched
there — so a first "show me everything" view of a big dataset can overwhelm the
browser. This predicts the browser payload and the disk-read cost from
inspect()'s (cheap) metadata and recommends a `subset(...)` to keep the overview
responsive. It reads NO bulk data (inspect is metadata-only).

The numbers below mirror my_render.py so the estimate matches what render ships:
  - volumes: each 3-D field is cast to float32  -> 4 bytes / voxel
  - points:  cloud is every loaded point, xyz + 1 scalar attribute (float32)
             -> 16 bytes / point (thin upstream via subset), plus a fixed
             grid_size**3 float32 density volume (~8 MB at 128).
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

from my_inspect import inspect_source, is_remote

_VOL_BYTES_PER_VOXEL = 4          # k3d.volume input is float32 (my_render casts)
_PT_BYTES_PER_POINT = 16          # xyz + 1 attribute, float32 each
_DEFAULT_DENSITY_GRID = 128       # render_points density histogram is grid_size**3
_MB = 1024 ** 2

# Only HDF5 pushes a grid stride into the read (hyperslab). Every other reader
# reads the full array/columns and slices in memory, so a subset trims the
# browser payload + memory but NOT the disk I/O.
_READ_REDUCIBLE = {'HDF5'}


def _on_disk_mb(filepath):
    """Total on-disk size in MB, summing GenericIO-style partitions (file#0, file#1, …).
    A partitioned file's base path is just a tiny header, so getsize alone underreports.
    For a remote source, a single stat round-trip is used (partitions not summed)."""
    if is_remote(filepath):
        return _remote_on_disk_mb(filepath)
    total, found = 0, False
    for p in [filepath] + glob.glob(filepath + "#*"):
        try:
            total += os.path.getsize(p)
            found = True
        except OSError:
            pass
    return total / _MB if found else None


def _remote_on_disk_mb(uri):
    """Remote file size (MB) via one stat round-trip, or None if unreachable /
    no ssh key auth. Partitions (#0, #1, …) are not summed remotely."""
    try:
        from my_inspect import _remote_conn
        from my_download import remote_stat
        conn, remote_path = _remote_conn(uri)
        if conn is None:
            return None
        st = remote_stat(conn, remote_path)
        return (st[0] / _MB) if st else None
    except Exception:
        return None


def estimate_render_cost(filepath, budget_mb=256):
    """Return a dict describing render cost + a recommended subset. Reads no bulk data.

    budget_mb (target browser payload) is the single source of truth for the
    budget — the MCP tool does not duplicate it. 256 is an interim default; the
    plan is to *estimate* it (from browser/memory limits) rather than hardcode it.

    `allow_fetch=False` is load-bearing, exactly as on the planner's estimate path
    (planner.py:456): this function's whole contract is "reads no bulk data", and
    a remote source whose schema can only be had by fetching the file would
    otherwise download multi-GB to answer how expensive a read would be. Better to
    raise SchemaUnavailable and say so.
    """
    info = inspect_source(filepath, allow_fetch=False)
    file_mb = _on_disk_mb(filepath)

    dims = info.dimensions or {}
    report = {
        'filepath': filepath,
        'filetype': info.filetype,
        'budget_mb': budget_mb,
        'file_mb': file_mb,
        'read_reducible': info.filetype in _READ_REDUCIBLE,
        'recommended_dimensions': {},
        'recommended_subset': None,
    }

    grid = dims.get('grid')
    if isinstance(grid, (tuple, list)) and len(grid) == 3:
        _estimate_grid(report, info, tuple(grid), budget_mb)
    elif 'particles' in dims:
        _estimate_particles(report, info, dims['particles'], budget_mb)
    else:
        report['modality'] = 'unknown'
        report['payload_mb'] = None
        report['note'] = ("No grid/particle dimensions in metadata — cannot estimate a "
                          "render payload. Inspect the dataset and choose manually.")
    return report


def _estimate_grid(report, info, grid, budget_mb):
    n_fields = max(1, len(info.variables))      # upper bound: treat each var as a 3-D field
    voxels = grid[0] * grid[1] * grid[2]
    total_mb = voxels * _VOL_BYTES_PER_VOXEL * n_fields / _MB
    report.update(modality='volume', grid_shape=grid, n_fields=n_fields,
                  payload_mb=round(total_mb, 1))
    read_note = ("striding also cuts the disk read (HDF5 hyperslab)."
                 if report['read_reducible']
                 else "striding cuts the browser payload + memory, not the disk read.")
    if total_mb <= budget_mb:
        report['note'] = (f"~{total_mb:.0f} MB <= {budget_mb} MB budget — render full "
                          f"resolution ({n_fields} field(s) x {grid}).")
        return
    # Largest cells-per-axis c with c**3 * 4 * n_fields <= budget.
    c = int((budget_mb * _MB / (_VOL_BYTES_PER_VOXEL * n_fields)) ** (1.0 / 3.0))
    c = max(1, min(c, min(grid)))
    strided_mb = (c ** 3) * _VOL_BYTES_PER_VOXEL * n_fields / _MB
    report['recommended_dimensions'] = {'grid': c}
    report['recommended_subset'] = f"subset(info, dimensions={{'grid': {c}}})"
    report['note'] = (f"~{total_mb:.0f} MB full ({n_fields} field(s) x {grid} x 4B) exceeds "
                      f"{budget_mb} MB — stride to ~{c} cells/axis (~{strided_mb:.0f} MB); "
                      f"{read_note}")


def _estimate_particles(report, info, n, budget_mb):
    density_mb = (_DEFAULT_DENSITY_GRID ** 3) * _VOL_BYTES_PER_VOXEL / _MB
    full_cloud_mb = n * _PT_BYTES_PER_POINT / _MB   # every loaded point goes in the cloud
    payload_mb = density_mb + full_cloud_mb
    report.update(modality='points', n_particles=n,
                  payload_mb=round(payload_mb, 1),
                  density_volume_mb=round(density_mb, 1))
    read_note = (f"{info.filetype} reads full columns then subsamples, so this trims the "
                 f"browser payload + memory, NOT the disk read"
                 + (f" (~{report['file_mb']:.0f} MB read regardless)." if report['file_mb']
                    else "."))
    cloud_budget = max(0.0, budget_mb - density_mb)
    if full_cloud_mb <= cloud_budget:
        report['note'] = (f"~{payload_mb:.0f} MB (density {density_mb:.0f} MB + cloud "
                          f"{full_cloud_mb:.0f} MB) <= {budget_mb} MB — render all particles.")
        return
    target_n = cloud_budget * _MB / _PT_BYTES_PER_POINT
    frac = round(max(1e-4, min(1.0, target_n / n)), 4)
    report['recommended_dimensions'] = {'particles': frac}
    report['recommended_subset'] = f"subset(info, dimensions={{'particles': {frac}}})"
    report['note'] = (f"~{payload_mb:.0f} MB (cloud {full_cloud_mb:.0f} MB) exceeds {budget_mb} "
                      f"MB — subsample to ~{frac:g} of particles. NOTE: {read_note}")


def format_estimate(report):
    """Render an estimate dict as a readable report for the agent."""
    lines = [
        f"Render-cost estimate for {report['filepath']}",
        f"  format: {report['filetype']}   modality: {report.get('modality')}",
    ]
    if report.get('file_mb') is not None:
        lines.append(f"  file size: {report['file_mb']:.0f} MB   "
                     f"(disk read {'reducible by striding' if report['read_reducible'] else 'full / not reducible by subset'})")
    if report.get('payload_mb') is not None:
        lines.append(f"  estimated browser payload: ~{report['payload_mb']:.0f} MB "
                     f"(budget {report['budget_mb']} MB)")
    lines.append(f"  {report['note']}")
    if report['recommended_dimensions']:
        lines.append(f"  recommended: render({report['recommended_subset']})")
    else:
        lines.append("  recommended: render(info)  # whole dataset fits the budget")
    return "\n".join(lines)


# ===========================================================================
# Plan cost estimate + budget gate
# ===========================================================================
# Unlike estimate_render_cost (whole-file, pre-spec), this consumes the LOWERED
# plan — a DatasetInfo (metadata only) plus the fused Narrowing — so it knows the
# actually-selected shape, project set, timestep count, and site. It reads NO
# bulk data. Local and remote are asymmetric (see the plan file):
#   - LOCAL: cost is disk -> memory. We can't know disk speed without fabricating
#     a number, so we report BYTES ONLY and gate on the size budget.
#   - REMOTE: cost is bytes over ssh. The caller measures the link with a live
#     synthetic probe (my_download.measure_bandwidth) and passes net_bw_bps here,
#     so we report a measured time band and gate on size AND time.
# Budgets are env-configurable test placeholders (later ~1 hr / ~100 GB+).


def _budget_bytes():
    return int(float(os.environ.get("VISLANG_BUDGET_BYTES", 10 * 1024 ** 3)))


def _budget_seconds():
    return float(os.environ.get("VISLANG_BUDGET_SECONDS", 3500))


@dataclass
class CostEstimate:
    """What executing one lowered plan is predicted to cost. `read_mb` is disk
    read (local) or bytes over the wire (remote). Time is remote-only and only
    when a link speed was measured (else None)."""
    site: str                              # 'local' | 'remote'
    read_mb: float | None = None
    output_mb: float | None = None
    browser_payload_mb: float | None = None
    n_timesteps: int = 1
    time_lo_s: float | None = None
    time_hi_s: float | None = None
    confidence: str | None = None          # 'measured' | None
    over_budget: bool = False
    budget_reason: str | None = None       # 'size' | 'time' | 'size+time' | None
    notes: list = field(default_factory=list)


def _ceil_div(a, b):
    return -(-a // b)


def _grid_axis_count(r, dim):
    """Elements an AxisRange selects on an axis of length `dim`."""
    start = r.start or 0
    stop = dim if r.stop is None else min(r.stop, dim)
    step = r.step or 1
    span = max(0, stop - start)
    return _ceil_div(span, step) if span else 0


def _selected_grid_cells(narrowing, grid):
    if narrowing is not None and narrowing.grid_ranges:
        prod = 1
        for r, dim in zip(narrowing.grid_ranges, grid):
            prod *= _grid_axis_count(r, dim)
        return prod
    prod = 1
    for d in grid:
        prod *= d
    return prod


def _selected_particles(narrowing, total):
    if narrowing is None or narrowing.particle_index is None:
        return total
    idx = narrowing.particle_index
    if isinstance(idx, slice):
        return len(range(*idx.indices(total)))
    try:
        import numpy as np
        arr = np.asarray(idx)
        return int(arr.sum()) if arr.dtype == bool else int(arr.size)
    except Exception:
        return total


def _read_set(info, narrowing):
    """Variables actually read: the projection (or all), plus any vars the
    post-read ops need present at mask time."""
    if narrowing is not None and narrowing.project is not None:
        vars_ = list(narrowing.project)
        try:
            from narrowing import post_op_read_vars
            for v in post_op_read_vars(narrowing.post_ops, narrowing.positions):
                if v not in vars_:
                    vars_.append(v)
        except Exception:
            pass
        return vars_
    return list(info.variables)


def _bytes_mb(info, vars_, count):
    """Σ over vars of count × itemsize, in MB. Missing itemsize -> 4 B (float32)."""
    total = sum(count * info.itemsizes.get(v, _VOL_BYTES_PER_VOXEL) for v in vars_)
    return total / _MB


def _fmt_time(s):
    if s is None:
        return "?"
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.1f} min"
    return f"{s / 3600:.1f} h"


def estimate_plan_cost(*, info, narrowing, site, n_timesteps=1, sink_kind=None,
                       net_bw_bps=None, wire_vars=None, read_mb_override=None):
    """Estimate the cost of one lowered plan (per-timestep quantities × n_timesteps).

    site='local'  -> read_mb is disk read; no time; gate on size.
    site='remote' -> read_mb is bytes over the wire. Pass the reduced set via
                     wire_vars (reduce path) or read_mb_override (whole-file
                     fetch, per timestep). net_bw_bps (from measure_bandwidth)
                     enables a time band; without it, bytes only.
    """
    notes = []
    dims = info.dimensions or {}
    grid = dims.get('grid')
    read_set = _read_set(info, narrowing)

    if isinstance(grid, (tuple, list)) and len(grid) == 3:
        grid = tuple(grid)
        modality = 'volume'
        selected = _selected_grid_cells(narrowing, grid)
        full = grid[0] * grid[1] * grid[2]
    elif 'particles' in dims:
        modality = 'points'
        full = dims['particles']
        selected = _selected_particles(narrowing, full)
    else:
        modality, selected, full = 'unknown', None, None

    caps = {}
    try:
        from adapters import adapter_capabilities
        caps = adapter_capabilities(info)
    except Exception:
        pass
    strided = caps.get('strided_read', False)
    col_pushdown = caps.get('column_pushdown', False)

    output_mb = read_mb = None
    if selected is not None:
        output_mb = _bytes_mb(info, read_set, selected)
        if site == 'remote':
            if read_mb_override is not None:
                read_mb = read_mb_override
            else:
                read_mb = _bytes_mb(info, wire_vars if wire_vars is not None
                                    else read_set, selected)
        elif strided:
            read_mb = output_mb                      # stride pushed into the read
        elif col_pushdown:
            read_mb = _bytes_mb(info, read_set, full)
            notes.append(f"{info.filetype} reads full columns then slices — disk "
                         f"read is the full length, not the subsample.")
        else:
            read_mb = _bytes_mb(info, list(info.variables), full)
            notes.append(f"{info.filetype} reads the whole file then slices in memory.")
    elif read_mb_override is not None:
        read_mb = read_mb_override
    elif site == 'local':
        fmb = _on_disk_mb(info.filepath)
        if fmb is not None:
            read_mb = fmb
            notes.append("modality unknown — using on-disk file size for the gate.")

    browser_payload_mb = None
    if sink_kind == 'render' and selected is not None:
        if modality == 'volume':
            n_fields = max(1, len(read_set))
            browser_payload_mb = selected * _VOL_BYTES_PER_VOXEL * n_fields / _MB
        elif modality == 'points':
            density = (_DEFAULT_DENSITY_GRID ** 3) * _VOL_BYTES_PER_VOXEL / _MB
            browser_payload_mb = density + selected * _PT_BYTES_PER_POINT / _MB

    n = max(1, int(n_timesteps))
    if read_mb is not None:
        read_mb *= n
    if output_mb is not None:
        output_mb *= n
    if browser_payload_mb is not None:
        browser_payload_mb *= n

    time_lo_s = time_hi_s = confidence = None
    if site == 'remote' and read_mb is not None:
        if net_bw_bps:
            mid = (read_mb * _MB) / net_bw_bps
            time_lo_s, time_hi_s, confidence = mid / 2.0, mid * 2.0, 'measured'
        else:
            notes.append("network speed unavailable (no ssh-key probe) — "
                         "time not estimated.")

    over_size = read_mb is not None and (read_mb * _MB) > _budget_bytes()
    over_time = time_hi_s is not None and time_hi_s > _budget_seconds()
    reason = ('size+time' if over_size and over_time else
              'size' if over_size else 'time' if over_time else None)

    if any(v not in info.itemsizes for v in read_set):
        notes.append("dtype not fully known — bytes assume 4 B/element where missing.")

    return CostEstimate(site=site, read_mb=read_mb, output_mb=output_mb,
                        browser_payload_mb=browser_payload_mb, n_timesteps=n,
                        time_lo_s=time_lo_s, time_hi_s=time_hi_s, confidence=confidence,
                        over_budget=bool(over_size or over_time), budget_reason=reason,
                        notes=notes)


def format_plan_estimate(est):
    """Readable block for a CostEstimate (shown in the run report)."""
    lines = [f"Cost estimate ({est.site}):"]
    if est.n_timesteps > 1:
        lines.append(f"  timesteps: {est.n_timesteps}")
    if est.read_mb is not None:
        label = "over the wire" if est.site == 'remote' else "read from disk"
        lines.append(f"  {label}: ~{est.read_mb:.0f} MB")
    if est.output_mb is not None:
        lines.append(f"  result size: ~{est.output_mb:.0f} MB")
    if est.browser_payload_mb is not None:
        lines.append(f"  browser payload: ~{est.browser_payload_mb:.0f} MB")
    if est.time_lo_s is not None:
        lines.append(f"  est. time: ~{_fmt_time(est.time_lo_s)}–{_fmt_time(est.time_hi_s)} "
                     f"({est.confidence})")
    for note in est.notes:
        lines.append(f"  note: {note}")
    if est.over_budget:
        lines.append(f"  ⚠ OVER BUDGET ({est.budget_reason}) — confirm to run "
                     f"as-is, or narrow the spec.")
    return "\n".join(lines)
