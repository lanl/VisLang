"""Per-timestep regions — a box that follows a known centre through a timeseries.

`region(series, center={N: (x, y[, z])}, size=S)` or `region(series,
track="file.csv", size=S)` asks for a different box at every timestep: centre ±
size/2, in the coordinates `region()` already uses for that modality (grids:
cell indices; points: world coordinates on the position variables).

Several NAMED boxes — `center={tag: {N: (x, y[, z])}}`, `{tag: (x, y[, z])}` on
one file, or a track file with a leading `tag` column — are the same thing per
tag: each tag is an ordinary moving box that exists at the timesteps it lists.

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
  box into exactly that kind of moving region.
"""

import csv
import dataclasses
import io
import math
import os

from vislang.dsl.forms import build_tags, check_centers
from vislang.dsl.nodes import RegionNode

_AXES = ("x", "y", "z")


def per_step_regions(nodes):
    """The region nodes in `nodes` whose box changes per timestep."""
    return [n for n in nodes if isinstance(n, RegionNode) and n.per_step]


# ---------------------------------------------------------------------------
# track files
# ---------------------------------------------------------------------------
_HEADERS = {("step", "x", "y"): "step", ("step", "x", "y", "z"): "step",
            ("tag", "step", "x", "y"): "tag_step",
            ("tag", "step", "x", "y", "z"): "tag_step",
            ("tag", "x", "y"): "tag", ("tag", "x", "y", "z"): "tag"}


def read_track(path):
    """Read a track CSV. The header says which kind it is:

        step,x,y[,z]       one box per timestep
        tag,step,x,y[,z]   named boxes over a timeseries (rows sharing a step
                           are separate boxes at that step)
        tag,x,y[,z]        named boxes on one file

    Returns (kind, rows, sha256): kind is "step", "tag_step" or "tag"; rows are
    sorted (label, cx, cy[, cz]) tuples for "step" and, in file order,
    (tag, label|None, cx, cy[, cz]) tuples for the named kinds."""
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
                raise ValueError(f"{where}: header must be 'step,x,y[,z]', "
                                 f"'tag,step,x,y[,z]' or 'tag,x,y[,z]', got "
                                 f"{','.join(rec)!r}")
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
        try:
            coords = tuple(float(c) for c in rest)
        except ValueError:
            raise ValueError(f"{where}: line {lineno}: coordinates must be "
                             f"numbers, got {rest!r}") from None
        if not all(math.isfinite(c) for c in coords):
            raise ValueError(f"{where}: line {lineno}: coordinates must be finite")
        if kind == "step":
            rows.append((label, *coords))
            continue
        if (tag, label) in seen:
            at = f" at timestep {label}" if label is not None else ""
            raise ValueError(f"{where}: line {lineno}: tag {tag!r} has more than "
                             f"one centre{at}")
        seen.add((tag, label))
        rows.append((tag, label, *coords))
    if header is None:
        raise ValueError(f"{where}: the file is empty")
    if kind == "step":
        return kind, check_centers(rows, where), short_sha256(blob)
    if not rows:
        raise ValueError(f"{where}: no centres given")
    return kind, tuple(rows), short_sha256(blob)


def load_track(path):
    """Read a one-box-per-timestep track CSV (header `step,x,y[,z]`).
    Returns (rows, sha256) where rows are sorted (label, cx, cy[, cz]) tuples."""
    kind, rows, sha = read_track(path)
    if kind != "step":
        raise ValueError(f"region(track={path!r}): this track names its boxes "
                         f"(a `tag` column); read it with read_track")
    return rows, sha


def _expand_size(size, ndim, where):
    """A track's size was stored before its dimension was known: a 1-tuple is
    the scalar form; anything else must already match the centre dimension."""
    if len(size) == 1:
        return tuple(size) * ndim
    if len(size) != ndim:
        raise ValueError(f"{where}: size has {len(size)} entries but the track "
                         f"has {ndim}-D centres")
    return tuple(size)


def _tags_from_rows(node, kind, rows, where):
    """RegionNode.tags from a named track's rows, sized from the node."""
    center = {}
    for tag, label, *coords in rows:
        if kind == "tag":
            center[tag] = tuple(coords)
        else:
            center.setdefault(tag, {})[label] = tuple(coords)
    # The sizes were kept as written (1-tuple = a scalar) because the centre
    # dimension was unknown; build_tags expands and checks them like inline.
    def as_written(s):
        return s[0] if len(s) == 1 else tuple(s)
    size = ({t: as_written(s) for t, s in node.tag_sizes} if node.tag_sizes
            else as_written(node.size) if node.size else None)
    return build_tags(center, size, where)


def resolve_tracks(nodes):
    """Inline every track-file region in `nodes` (a list of middle nodes).
    Regions that already carry centres — including plans that arrive on a
    remote host already resolved — pass through untouched."""
    out = []
    for n in nodes:
        if (isinstance(n, RegionNode) and n.track is not None
                and not n.centers and not n.tags):
            where = f"region(track={n.track!r})"
            kind, rows, sha = read_track(n.track)
            if kind != "step":
                n = dataclasses.replace(n, tags=_tags_from_rows(n, kind, rows, where),
                                        track_sha=sha, size=(), tag_sizes=())
            elif n.tag_sizes:
                raise ValueError(f"{where}: size={{tag: …}} names boxes, but this "
                                 f"track has no `tag` column (header step,x,y[,z])")
            else:
                n = dataclasses.replace(
                    n, centers=rows, track_sha=sha,
                    size=_expand_size(n.size, len(rows[0]) - 1, where))
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
    return [t for t, _, _ in node.tags]


def tag_view(node, tag):
    """One named box as an ordinary per-timestep region (centres + size), so
    lowering, the bounds check and box_for_step treat it exactly as an unnamed
    moving box. On a single file its one centre carries label None."""
    for t, size, rows in node.tags:
        if t == tag:
            return RegionNode(upstream=node.upstream, centers=rows, size=size)
    raise KeyError(tag)


def for_tag(nodes, tag):
    """`nodes` with the named-box region replaced by tag `tag`'s own view."""
    return [tag_view(n, tag) if isinstance(n, RegionNode) and n.tagged else n
            for n in nodes]


def tag_plan(node, selected, available):
    """Which named boxes run at which timestep: ({label: [tags]}, skipped).

    A tag may skip timesteps (an object that leaves and comes back) and may list
    more of the run than timesteps() selected. Refused before any read: a tag
    naming a timestep with no file, or a selection where no tag has a box.
    `skipped` lists the tags with no box in the selection — they write nothing."""
    selected, available = set(selected), set(available)
    src = f"track {node.track!r}" if node.track else "center="
    plan, skipped = {}, []
    for tag, _size, rows in node.tags:
        labels = {r[0] for r in rows}
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


def _size_text(size):
    return "(" + ", ".join("whole" if s is None else f"{s:g}" for s in size) + ")"


def describe(node):
    """Short human text for a per-step or named-box region, e.g.
    region{center×30, size=(30, 30, 30)} or region{tags=v1,v2 (5 boxes), size=(20, 20, 20)}."""
    where = []
    if node.tagged:
        n = sum(len(rows) for _, _, rows in node.tags)
        where.append(f"tags={','.join(tag_names(node))}"
                     + (f" ({n} boxes)" if node.tag_series else ""))
        sizes = {tuple(s) for _, s, _ in node.tags}
        size = _size_text(next(iter(sizes))) if len(sizes) == 1 else "per tag"
    else:
        if node.centers:
            where.append(f"center×{len(node.centers)}")
        size = _size_text(node.size)
    if node.track:
        where.append(f"track={os.path.basename(node.track)}")
    return f"region{{{', '.join(where)}, size={size}}}"
