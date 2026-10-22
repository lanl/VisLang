"""Regions placed by a centre — a box that follows a known centre through a
timeseries, or stays put.

`region(series, center=[(N, x, y[, z][, size]), …], size=S)` or `region(series,
center="file.csv", size=S)` asks for a different box at every timestep: centre ±
size/2, in the coordinates `region()` already uses for that modality (grids:
cell indices; points: world coordinates on the position variables). A centre
written as a bare (x, y[, z]) is a box that stays put — the one box on a file,
the same box at every timestep of a series.

Several NAMED boxes — `center={tag: …}` with either kind of box per tag, or a
track file with a leading `tag` column — are the same thing per tag: each tag
is an ordinary box that exists at the timesteps it lists (every timestep, for a
box that stays put).

This module owns the host-side steps that make that sound:

- `resolve_tracks` reads a track file ONCE, at planning time, and inlines its
  rows into the node. Forms read no data and the sandbox cannot open files, so
  the planner is the first place the file can be read; and inlining means the
  plan shipped to a remote host carries the centres as data, never a path that
  only exists here. The file's hash rides along for provenance.
- `check_coverage` refuses, before any bulk read, a series where a selected
  timestep has no centre or a centre names a timestep that does not exist;
  `tag_plan` is its counterpart for named boxes, where a gap is allowed.
- `box_for_step` turns one timestep's centre into a concrete fixed-box
  RegionNode, so lowering, the static bounds check, and geometry tracking run
  exactly as they do for an ordinary region. `tag_view` first turns one named
  box into exactly that kind of region.
"""

import csv
import dataclasses
import io
import math
import os

from vislang.dsl.forms import resolve_center
from vislang.dsl.nodes import RegionNode

_AXES = ("x", "y", "z")


def per_step_regions(nodes):
    """The region nodes in `nodes` whose box changes per timestep."""
    return [n for n in nodes if isinstance(n, RegionNode) and n.per_step]


# ---------------------------------------------------------------------------
# track files
# ---------------------------------------------------------------------------
_HEADERS = {}
for _cols, _kind in ((("step", "x", "y"), "step"), (("tag", "step", "x", "y"), "tag_step"),
                     (("tag", "x", "y"), "tag")):
    for _h in (_cols, _cols + ("z",)):
        _HEADERS[_h] = _kind
        _HEADERS[_h + ("size",)] = _kind


def read_track(path):
    """Read a track CSV. The header says which kind it is:

        step,x,y[,z][,size]       one box per timestep
        tag,step,x,y[,z][,size]   named boxes over a timeseries (rows sharing a
                                  step are separate boxes at that step)
        tag,x,y[,z][,size]        named boxes that stay put

    A `size` cell overrides size= for its row (left blank, it does not).
    Returns (center, sizes, sha256, ndim): `center` written exactly as a spec would
    write it inline — rows (step, x, y[, z][, size]) or {tag: rows | centre} —
    so resolve_center checks both the same way; `sizes` is {tag: size} from
    the size column of a `tag,x,y…` file, whose centres have no row to carry
    it; `ndim` is the centre dimension the header names (2 or 3)."""
    from vislang.runtime.provenance import short_sha256
    full = os.path.abspath(path)
    try:
        with open(full, "rb") as f:
            blob = f.read()
    except OSError as e:
        raise FileNotFoundError(f"region(center={path!r}): cannot read {full}: "
                                f"{e.strerror or e}") from None
    where = f"region(center={path!r})"
    reader = csv.reader(io.StringIO(blob.decode("utf-8-sig")))
    header = kind = None
    rows, seen = [], set()
    for lineno, rec in enumerate(reader, 1):
        rec = [c.strip() for c in rec]
        if not rec or all(not c for c in rec) or rec[0].startswith("#"):
            continue                                  # blank line / comment
        if header is None:
            header = tuple(c.lower() for c in rec)
            kind = _HEADERS.get(header)
            if kind is None:
                raise ValueError(f"{where}: header must be 'step,x,y[,z][,size]', "
                                 f"'tag,step,x,y[,z][,size]' or 'tag,x,y[,z][,size]', "
                                 f"got {','.join(rec)!r}")
            continue
        if len(rec) != len(header):
            raise ValueError(f"{where}: line {lineno} has {len(rec)} value(s), "
                             f"the header has {len(header)}")
        tag = rec[0] if kind != "step" else None
        rest = rec[1:] if kind != "step" else rec
        label = None
        if kind != "tag":
            try:
                label = int(rest[0])
            except ValueError:
                raise ValueError(f"{where}: line {lineno}: step must be an integer "
                                 f"timestep label, got {rest[0]!r}") from None
            rest = rest[1:]
        cell = rest.pop() if header[-1] == "size" else ""
        try:
            coords = tuple(float(c) for c in rest)
            size = float(cell) if cell else None
        except ValueError:
            raise ValueError(f"{where}: line {lineno}: coordinates and size must be "
                             f"numbers, got {rec!r}") from None
        if not all(math.isfinite(c) for c in coords + ((size,) if size else ())):
            raise ValueError(f"{where}: line {lineno}: values must be finite")
        if (tag, label) in seen:
            at = f" at timestep {label}" if label is not None else ""
            who = f"tag {tag!r} has" if tag is not None else "there is"
            raise ValueError(f"{where}: line {lineno}: {who} more than one centre{at}")
        seen.add((tag, label))
        rows.append((tag, label, coords, None if size is None else (size,) * len(coords)))
    if header is None:
        raise ValueError(f"{where}: the file is empty")
    if not rows:
        raise ValueError(f"{where}: no centres given")
    sizes = {}
    if kind == "step":
        center = [(lab, *c) + ((sz,) if sz else ()) for _, lab, c, sz in rows]
    elif kind == "tag_step":
        center = {}
        for tag, lab, c, sz in rows:
            center.setdefault(tag, []).append((lab, *c) + ((sz,) if sz else ()))
    else:
        center = {tag: c for tag, _, c, _ in rows}
        sizes = {tag: sz for tag, _, _, sz in rows if sz}
    ndim = 3 if "z" in header else 2
    return center, sizes, short_sha256(blob), ndim


def _size_as_written(node):
    """The size= a track region was written with, before the centre dimension
    was known (a 1-tuple is the scalar form): a number, a tuple, {tag: …} or
    None."""
    def as_written(s):
        return s[0] if len(s) == 1 else tuple(s)
    if node.tag_sizes:
        return {t: as_written(s) for t, s in node.tag_sizes}
    return as_written(node.size) if node.size else None


def resolve_tracks(nodes):
    """Inline every track-file region in `nodes` (a list of middle nodes).
    Regions that already carry centres — including plans that arrive on a
    remote host already resolved — pass through untouched."""
    out = []
    for n in nodes:
        if (isinstance(n, RegionNode) and n.track is not None
                and not n.centers and not n.tags):
            where = f"region(center={n.track!r})"
            center, file_sizes, sha, ndim = read_track(n.track)
            size = _size_as_written(n)
            if isinstance(size, dict) and not isinstance(center, dict):
                raise ValueError(f"{where}: size={{tag: …}} names boxes, but this "
                                 f"track has no `tag` column (header step,x,y[,z])")
            if file_sizes:
                base = size
                size = (dict(base) if isinstance(base, dict) else
                        {t: base for t in center} if base is not None else {})
                size.update(file_sizes)
            n = dataclasses.replace(n, **{"size": (), "tag_sizes": (),
                                          "track_sha": sha,
                                          **resolve_center(center, size, where, ndim)})
        out.append(n)
    return out


# ---------------------------------------------------------------------------
# named boxes
# ---------------------------------------------------------------------------
def tagged_region(nodes):
    """The one region with named boxes in `nodes`, or None.

    Two would cut every box of the first into every box of the second, and no
    single name says what such a result is — so that is refused."""
    found = [n for n in nodes if isinstance(n, RegionNode) and n.tagged]
    if len(found) > 1:
        raise ValueError("only one region with named boxes (center={tag: …}) per "
                         "chain: a second would cut each box of the first by each "
                         "of its own. Put every tag in one region().")
    return found[0] if found else None


def tag_names(node):
    """The tags of a named-box region, in the order written."""
    return [t for t, _, _, _ in node.tags]


def tag_view(node, tag):
    """One named box as an ordinary centre-placed region (centres + sizes), so
    lowering, the bounds check and box_for_step treat it exactly as an unnamed
    box. A box that stays put has one centre, labelled None."""
    for t, size, rows, step_sizes in node.tags:
        if t == tag:
            return RegionNode(upstream=node.upstream, centers=rows, size=size,
                              step_sizes=step_sizes)
    raise KeyError(tag)


def for_tag(nodes, tag):
    """`nodes` with the named-box region replaced by tag `tag`'s own view."""
    return [tag_view(n, tag) if isinstance(n, RegionNode) and n.tagged else n
            for n in nodes]


def tag_plan(node, selected, available):
    """Which named boxes run at which timestep: ({label: [tags]}, skipped).

    A tag may skip timesteps (an object that leaves and comes back) and may list
    more of the run than timesteps() selected; a box that stays put is at every
    selected timestep. Refused before any read: a tag naming a timestep with no
    file, or a selection where no tag has a box.
    `skipped` lists the tags with no box in the selection — they write nothing."""
    selected, available = set(selected), set(available)
    src = f"center={node.track!r}" if node.track else "center="
    plan, skipped = {}, []
    for tag, _size, rows, _ in node.tags:
        labels = {r[0] for r in rows}
        if None in labels:
            labels = set(selected)
        unknown = labels - available
        if unknown:
            raise ValueError(f"region({src}): tag {tag!r} has no timestep file for "
                             f"{_labels_text(unknown)} in this folder")
        mine = sorted(labels & selected)
        if not mine:
            skipped.append(tag)
        for lab in mine:
            plan.setdefault(lab, []).append(tag)
    if not plan:
        raise ValueError(f"region({src}): no tag has a box in the selected "
                         f"timesteps ({_labels_text(selected)})")
    return dict(sorted(plan.items())), skipped


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
        if None in have:
            continue                    # a box that stays put: every timestep
        src = f"center={n.track!r}" if n.track else "center="
        unknown = have - available
        if unknown:
            raise ValueError(f"region({src}): no timestep file for "
                             f"{_labels_text(unknown)} in this folder")
        missing = selected - have
        if missing:
            raise ValueError(f"region({src}): no centre for timestep(s) "
                             f"{_labels_text(missing)}; add them, or drop those "
                             f"timesteps with timesteps(...)")


def box_at(node, label):
    """(centre, per-axis size) of a centre-placed region at timestep `label`, or
    None when it has no box there. A box that stays put (one centre labelled
    None) has its box at every label; a row's own size overrides the region's."""
    rows = node.centers
    row = next((r for r in rows if r[0] == label), None)
    if row is None and len(rows) == 1 and rows[0][0] is None:
        row = rows[0]
    if row is None:
        return None
    return tuple(row[1:]), dict(node.step_sizes).get(label, node.size)


def box_for_step(node, label, grid=None):
    """One timestep's concrete box as a fixed-range RegionNode.

    `grid` is the grid shape for grid data (index space: the box is rounded to
    whole cells and clipped to the grid), or None for point data (world
    coordinates, not clipped — a particle box has no known domain to clip to).
    Returns (region_node, clipped) where `clipped` lists the axes cut back to
    the domain edge."""
    at = box_at(node, label)
    if at is None:                        # check_coverage should have caught it
        raise ValueError(f"region: no centre for timestep #{label}")
    center, size = at
    if grid is not None and len(center) > len(grid):
        raise ValueError(f"region: {len(center)}-D centres on a "
                         f"{len(grid)}-D grid (axes {', '.join(_AXES[:len(grid)])})")
    ranges, clipped = [], []
    for axis, c, s in zip(_AXES, center, size):
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


def _size_text(size):
    return "(" + ", ".join("whole" if s is None else f"{s:g}" for s in size) + ")"


def describe(node):
    """Short human text for a centre-placed or named-box region, e.g.
    region{center×30, size=(30, 30, 30)} or region{tags=v1,v2 (5 boxes), size=(20, 20, 20)}."""
    where = []
    if node.tagged:
        n = sum(len(rows) for _, _, rows, _ in node.tags)
        where.append(f"tags={','.join(tag_names(node))}"
                     + (f" ({n} boxes)" if node.tag_series else ""))
        sizes = {tuple(s) for _, s, _, _ in node.tags}
        size = (_size_text(next(iter(sizes))) if len(sizes) == 1 else "per tag")
        if any(st for _, _, _, st in node.tags):
            size += ", some rows differ"
    else:
        if node.centers and node.centers[0][0] is None:
            where.append("center=(" + ", ".join(f"{c:g}" for c in node.centers[0][1:]) + ")")
        elif node.centers:
            where.append(f"center×{len(node.centers)}")
        size = _size_text(node.size) + (", some rows differ" if node.step_sizes else "")
    if node.track:
        where.append(f"track={os.path.basename(node.track)}")
    return f"region{{{', '.join(where)}, size={size}}}"
