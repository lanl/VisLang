"""Per-timestep regions — a box that follows a known centre through a timeseries.

`region(series, center={N: (x, y[, z])}, size=S)` or `region(series,
track="file.csv", size=S)` asks for a different box at every timestep: centre ±
size/2, in the coordinates `region()` already uses for that modality (grids:
cell indices; points: world coordinates on the position variables).

This module owns the three host-side steps that make that sound:

- `resolve_tracks` reads a track file ONCE, at planning time, and inlines its
  rows into the node. Forms read no data and the sandbox cannot open files, so
  the planner is the first place the file can be read; and inlining means the
  plan shipped to a remote host carries the centres as data, never a path that
  only exists here. The file's hash rides along for provenance.
- `check_coverage` refuses, before any bulk read, a series where a selected
  timestep has no centre or a centre names a timestep that does not exist.
- `box_for_step` turns one timestep's centre into a concrete fixed-box
  RegionNode, so lowering, the static bounds check, and geometry tracking run
  exactly as they do for an ordinary region.
"""

import csv
import dataclasses
import io
import math
import os

from vislang.dsl.forms import check_centers
from vislang.dsl.nodes import RegionNode

_AXES = ("x", "y", "z")


def per_step_regions(nodes):
    """The region nodes in `nodes` whose box changes per timestep."""
    return [n for n in nodes if isinstance(n, RegionNode) and n.per_step]


# ---------------------------------------------------------------------------
# track files
# ---------------------------------------------------------------------------
def load_track(path):
    """Read a track CSV (header `step,x,y[,z]`, one row per timestep).
    Returns (rows, sha256) where rows are sorted (label, cx, cy[, cz]) tuples."""
    from vislang.runtime.provenance import short_sha256
    full = os.path.abspath(path)
    try:
        with open(full, "rb") as f:
            blob = f.read()
    except OSError as e:
        raise FileNotFoundError(f"region(track={path!r}): cannot read {full}: "
                                f"{e.strerror or e}") from None
    where = f"region(track={path!r})"
    reader = csv.reader(io.StringIO(blob.decode("utf-8-sig")))
    header = None
    rows = []
    for lineno, rec in enumerate(reader, 1):
        rec = [c.strip() for c in rec]
        if not rec or all(not c for c in rec) or rec[0].startswith("#"):
            continue                                  # blank line / comment
        if header is None:
            header = [c.lower() for c in rec]
            if header not in (["step", "x", "y"], ["step", "x", "y", "z"]):
                raise ValueError(f"{where}: header must be 'step,x,y' or "
                                 f"'step,x,y,z', got {','.join(rec)!r}")
            continue
        if len(rec) != len(header):
            raise ValueError(f"{where}: line {lineno} has {len(rec)} value(s), "
                             f"the header has {len(header)}")
        try:
            label = int(rec[0])
        except ValueError:
            raise ValueError(f"{where}: line {lineno}: step must be an integer "
                             f"timestep label, got {rec[0]!r}") from None
        try:
            coords = tuple(float(c) for c in rec[1:])
        except ValueError:
            raise ValueError(f"{where}: line {lineno}: coordinates must be "
                             f"numbers, got {rec[1:]!r}") from None
        if not all(math.isfinite(c) for c in coords):
            raise ValueError(f"{where}: line {lineno}: coordinates must be finite")
        rows.append((label, *coords))
    if header is None:
        raise ValueError(f"{where}: the file is empty")
    return check_centers(rows, where), short_sha256(blob)


def _expand_size(size, ndim, where):
    """A track's size was stored before its dimension was known: a 1-tuple is
    the scalar form; anything else must already match the centre dimension."""
    if len(size) == 1:
        return tuple(size) * ndim
    if len(size) != ndim:
        raise ValueError(f"{where}: size has {len(size)} entries but the track "
                         f"has {ndim}-D centres")
    return tuple(size)


def resolve_tracks(nodes):
    """Inline every track-file region in `nodes` (a list of middle nodes).
    Regions that already carry centres — including plans that arrive on a
    remote host already resolved — pass through untouched."""
    out = []
    for n in nodes:
        if isinstance(n, RegionNode) and n.track is not None and not n.centers:
            rows, sha = load_track(n.track)
            n = dataclasses.replace(
                n, centers=rows, track_sha=sha,
                size=_expand_size(n.size, len(rows[0]) - 1,
                                  f"region(track={n.track!r})"))
        out.append(n)
    return out


# ---------------------------------------------------------------------------
# checks + per-step boxes
# ---------------------------------------------------------------------------
def _labels_text(labels, limit=8):
    labels = sorted(labels)
    shown = ", ".join(f"#{lab}" for lab in labels[:limit])
    return shown + (f", … ({len(labels)} in all)" if len(labels) > limit else "")


def check_coverage(nodes, selected, available):
    """Every SELECTED timestep needs a centre, and every centre must name a
    timestep that exists in the folder. Raised before any bulk read, so a bad
    track costs one listing. A centre for an existing timestep that timesteps()
    left out is fine — the track can be longer than the selection."""
    selected, available = set(selected), set(available)
    for n in per_step_regions(nodes):
        have = {r[0] for r in n.centers}
        src = f"track {n.track!r}" if n.track else "center="
        unknown = have - available
        if unknown:
            raise ValueError(f"region({src}): no timestep file for "
                             f"{_labels_text(unknown)} in this folder")
        missing = selected - have
        if missing:
            raise ValueError(f"region({src}): no centre for timestep(s) "
                             f"{_labels_text(missing)}; add them, or drop those "
                             f"timesteps with timesteps(...)")


def box_for_step(node, label, grid=None):
    """One timestep's concrete box as a fixed-range RegionNode.

    `grid` is the grid shape for grid data (index space: the box is rounded to
    whole cells and clipped to the grid), or None for point data (world
    coordinates, not clipped — a particle box has no known domain to clip to).
    Returns (region_node, clipped) where `clipped` lists the axes cut back to
    the domain edge."""
    row = next((r for r in node.centers if r[0] == label), None)
    if row is None:                       # check_coverage should have caught it
        raise ValueError(f"region: no centre for timestep #{label}")
    center = row[1:]
    if grid is not None and len(center) > len(grid):
        raise ValueError(f"region: {len(center)}-D centres on a "
                         f"{len(grid)}-D grid (axes {', '.join(_AXES[:len(grid)])})")
    ranges, clipped = [], []
    for axis, c, s in zip(_AXES, center, node.size):
        if s is None:
            continue                       # keep this axis whole
        if grid is None:
            ranges.append((axis, c - s / 2, c + s / 2))
            continue
        n_cells = max(1, int(round(s)))
        lo = int(math.floor(c - n_cells / 2 + 0.5))
        hi = lo + n_cells
        dim = int(grid[_AXES.index(axis)])
        clo, chi = max(lo, 0), min(hi, dim)
        if clo >= chi:
            raise ValueError(
                f"region at timestep #{label}: the box on {axis} "
                f"[{lo}:{hi}] lies entirely outside the grid 0..{dim} "
                f"(centre {c:g}, size {s:g})")
        if (clo, chi) != (lo, hi):
            clipped.append(axis)
        ranges.append((axis, clo, chi))
    return RegionNode(upstream=node.upstream, ranges=tuple(ranges)), clipped


def describe(node):
    """Short human text for a per-step region, e.g. region{center×30, size=(30, 30, 30)}."""
    size = ", ".join("whole" if s is None else f"{s:g}" for s in node.size)
    where = []
    if node.centers:
        where.append(f"center×{len(node.centers)}")
    if node.track:
        where.append(f"track={os.path.basename(node.track)}")
    return f"region{{{', '.join(where)}, size=({size})}}"
