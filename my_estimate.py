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

import glob
import os

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
    """
    info = inspect_source(filepath)
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
