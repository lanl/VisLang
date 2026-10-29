"""Which coordinates a region is in — and a grid's world box as the cells it
selects.

`region()` takes the data's OWN coordinates, the ones ParaView shows on its
axes: world coordinates on point data and on a grid that states where it sits
(origin + spacing, or each point's position); cells on a grid that does not —
whose world coordinates are its cell indices anyway (origin 0, spacing 1, as
VTK reads it). `coords="world" | "cells"` pins one where it matters. The spec
states the intent; this module decides it against the file and records the
decision on the node (`coords` filled in), so the provenance record says how
the numbers were read even when the spec left it open.

On a grid, a region is rewritten here in the file's own ARRAY indices and
marked `coords="index"` — an internal value no spec writes. A world box
becomes the samples it selects (dataset_info.world_box_to_cells); cells on a
file Sieve placed among its source's cells (a cut of a grid with no geometry,
whose cell 100 is its array index 0) are shifted and scaled to the file's own
indices; plain cells are already array indices. Everything downstream —
lowering, the static bounds check, the extent catalog's keys, the plan shipped
to a remote — then sees array indices on grids, exactly as before. A grid
region leaves this module in "index" or, when this side cannot place it (a
structured grid whose points are bulk data it may not read, or a remote schema
that predates geometry), as written for the side that reads the file.

Resolution is idempotent: a region already in "index" passes through.
"""

import dataclasses
import math

from vislang.dsl.nodes import RegionNode
from vislang.formats.dataset_info import in_cells, world_box_to_cells

_AXES = ("x", "y", "z")
_AXIS_INDEX = {a: i for i, a in enumerate(_AXES)}


def decide(info, node):
    """The coordinates `node`'s numbers are in on `info`'s data: "world",
    "cells", or None when this side cannot tell (a schema that predates
    geometry), or "index" for a region this module already resolved. Raises
    when `coords=` asks for what the data cannot be."""
    from vislang.interpreter.planner import _modality      # lazy: planner imports us
    if node.coords == "index":
        return "index"
    if _modality(info) == "particles":
        if node.coords == "cells":
            raise ValueError("region(coords='cells'): point data has no cells — its "
                             "region is in world coordinates (the position "
                             "variables). Drop coords= or use coords='world'.")
        return "world"
    if node.coords == "cells":
        return "cells"
    if not getattr(info, "geometry_known", True):
        return node.coords                  # "world" kept, None stays undecided
    geometry = getattr(info, "geometry", None)
    has = bool(geometry) and not in_cells(geometry)   # a physical position
    if node.coords == "world" and not has:
        raise ValueError("region(coords='world'): this grid does not say where it "
                         "sits in space (no origin/spacing or point positions in "
                         "the file), so its only coordinates are cell indices. "
                         "Drop coords= or use coords='cells'.")
    return "world" if has else "cells"


def resolve_regions(info, middle, steps=None, *, read_points=True, label=None,
                    convert=True):
    """`middle` with every region's coordinates decided against `info` and, on
    a grid, every world region rewritten in cells. `read_points=False` keeps a
    structured grid's points unread (they are bulk data): its world regions
    stay as written for the side that reads the file. `label`, when given, is
    the timestep being lowered — a box placed per timestep only needs that
    timestep's centre resolved. `convert=False` decides the coordinates but
    converts nothing — for a series, where each file places a world box on its
    own geometry. Appends one line per conversion to `steps`."""
    out, modes = [], []
    for n in middle:
        if isinstance(n, RegionNode):
            mode = None if n.track_pending else decide(info, n)
            modes.append(None if mode == "index" else mode)
            n = _resolve(info, n, steps, read_points, label, convert)
        out.append(n)
    _record_modes(modes)
    return out


def _resolve(info, node, steps, read_points, label, convert):
    if node.track_pending:
        return node                         # a track file not read yet
    mode = decide(info, node)
    from vislang.interpreter.planner import _modality
    if mode in (None, "index"):
        return node
    if _modality(info) == "particles":
        return dataclasses.replace(node, coords="world")
    if mode == "cells":
        return _cells_to_index(info, node, steps, label)
    # A world region on a grid: place it on the file's geometry.
    geometry = _geometry(info, read_points) if convert else None
    if geometry is None:
        return dataclasses.replace(node, coords="world")   # resolved where read
    grid = tuple(int(v) for v in info.dimensions["grid"])
    if node.tagged:
        tags = tuple((t, *_centres_to_cells(geometry, grid, size, rows, step_sizes,
                                            label, f"box {t!r}"))
                     for t, size, rows, step_sizes in node.tags)
        tag_ranges = tuple((t, _bounds_to_cells(geometry, grid, rows, label,
                                                f"box {t!r}", steps))
                           for t, rows in node.tag_ranges)
        _note(steps, f"region: named boxes placed in world coordinates on the "
                     f"file's {geometry['kind']} geometry")
        return dataclasses.replace(node, tags=tags, tag_ranges=tag_ranges,
                                   coords="index")
    if node.step_ranges:
        rows = _bounds_to_cells(geometry, grid, node.step_ranges, label, "region",
                                steps)
        _note(steps, f"region: per-timestep bounds placed in world coordinates on "
                     f"the file's {geometry['kind']} geometry")
        return dataclasses.replace(node, step_ranges=rows, coords="index")
    if node.per_step:
        size, rows, step_sizes = _centres_to_cells(geometry, grid, node.size,
                                                   node.centers, node.step_sizes,
                                                   label, "region")
        _note(steps, f"region: box centres placed in world coordinates on the "
                     f"file's {geometry['kind']} geometry")
        return dataclasses.replace(node, size=size, centers=rows,
                                   step_sizes=step_sizes, coords="index")
    return _ranges_to_cells(node, geometry, grid, steps)


def _cells_to_index(info, node, steps, label):
    """A region in cells as the file's own array indices. Plain cells already
    are; a file Sieve placed among its source's cells counts them from its
    origin, every `spacing` (its stride) — so the numbers keep meaning the
    source's cells, which is also what a viewer's axes show for it.

    Cells are whole indices: a bound like 6.0 is read as 6, and a fraction is
    refused here — before any read — rather than rounded one way or the other."""
    node = _whole_cells(node)
    geometry = getattr(info, "geometry", None)
    if not in_cells(geometry) or (all(v == 0 for v in geometry["origin"])
                                  and all(v == 1 for v in geometry["spacing"])):
        return dataclasses.replace(node, coords="index")
    grid = tuple(int(v) for v in info.dimensions["grid"])
    o, s = geometry["origin"], geometry["spacing"]
    if node.tagged:
        tags = tuple((t, *_centres_to_cells(geometry, grid, size, rows, step_sizes,
                                            label, f"box {t!r}"))
                     for t, size, rows, step_sizes in node.tags)
        tag_ranges = tuple((t, _source_cell_rows(rows, grid, o, s))
                           for t, rows in node.tag_ranges)
        return dataclasses.replace(node, tags=tags, tag_ranges=tag_ranges,
                                   coords="index")
    if node.step_ranges:
        # Each box in this file's indices, unclipped: box_for_step clips it
        # to the grid as it does any per-timestep box.
        return dataclasses.replace(node, coords="index", step_ranges=
                                   _source_cell_rows(node.step_ranges, grid, o, s))
    if node.per_step:
        size, rows, step_sizes = _centres_to_cells(geometry, grid, node.size,
                                                   node.centers, node.step_sizes,
                                                   label, "region")
        return dataclasses.replace(node, size=size, centers=rows,
                                   step_sizes=step_sizes, coords="index")
    ranges = []
    for axis, lo, hi in node.ranges:
        a = _grid_axis(axis, grid)
        n, first, step = grid[a], o[a], s[a]
        last = first + (n - 1) * step
        start, stop = _source_cells(lo, hi, first, step)
        start = 0 if start is None else start
        stop = n if stop is None else stop
        if start < 0 or stop > n or start >= stop:
            raise ValueError(
                f"region {axis}=({lo}, {hi}): this file holds cells {first:g} to "
                f"{last:g} of its source (every {step:g}) on {axis}, in the "
                f"source's cell numbering")
        ranges.append((axis, int(start), int(stop)))
    _note(steps, "region "
          + ", ".join(f"{a} [{_num(lo)}:{_num(hi)})" for a, lo, hi in node.ranges)
          + " (source cells) -> this file's indices "
          + ", ".join(f"{a} [{b}:{e}]" for a, b, e in ranges))
    return dataclasses.replace(node, ranges=tuple(ranges), coords="index")


def _whole_cells(node):
    """`node` with its cell bounds as ints: a whole-number float (6.0, from a
    CSV or arithmetic) names one cell exactly, so it becomes 6; a fraction
    (2.5) has no one meaning in cells [lo, hi) and raises, saying what to write.
    Covers the fixed box and boxes given by their bounds; a centre-placed box
    is rounded to whole cells by box_for_step, as documented."""
    def as_int(b):
        if b is None or (isinstance(b, int) and not isinstance(b, bool)):
            return b
        return int(b) if float(b).is_integer() else None

    def text(v):
        return "None" if v is None else f"{v:g}"

    def whole(ranges, lab=None, tag=None):
        out = []
        for axis, lo, hi in ranges:
            new = [as_int(b) for b in (lo, hi)]
            if any(b is not None and v is None for b, v in zip((lo, hi), new)):
                # The box rounded outward and inward — the two whole boxes
                # nearest what was written; the spec says which it meant.
                fl = lambda v, f: None if v is None else int(f(v))
                alts = []
                for a, b in ((fl(lo, math.floor), fl(hi, math.ceil)),
                             (fl(lo, math.ceil), fl(hi, math.floor))):
                    if (a is None or b is None or a < b) and (a, b) not in alts:
                        alts.append((a, b))
                who = ("region" + (f" box {tag!r}" if tag else "")
                       + (f" at timestep #{lab}" if lab is not None else ""))
                raise ValueError(
                    f"{who}: {axis}=({text(lo)}, {text(hi)}) is in cells, which are "
                    f"whole indices [lo, hi) — write "
                    + " or ".join(f"{axis}=({text(a)}, {text(b)})" for a, b in alts)
                    + "; a fraction is not rounded. (On a grid that states where it "
                    "sits, coords='world' reads the numbers as positions.)")
            out.append((axis, *new))
        return tuple(out)
    changes = {}
    if node.ranges:
        changes["ranges"] = whole(node.ranges)
    if node.step_ranges:
        changes["step_ranges"] = tuple((lab, whole(r, lab)) for lab, r in node.step_ranges)
    if node.tag_ranges:
        changes["tag_ranges"] = tuple(
            (t, tuple((lab, whole(r, lab, t)) for lab, r in rows))
            for t, rows in node.tag_ranges)
    return dataclasses.replace(node, **changes) if changes else node


def _grid_axis(axis, grid):
    if axis not in _AXIS_INDEX or _AXIS_INDEX[axis] >= len(grid):
        raise ValueError(f"region axis {axis!r} not one of this grid's axes")
    return _AXIS_INDEX[axis]


def _source_cells(lo, hi, first, step):
    """Source cells [lo, hi) as this file's indices [start, stop): source cell
    c is index (c - first) / step, and the range keeps the indices whose source
    cell falls inside it. An open bound stays open (None)."""
    start = None if lo is None else int(-(-(lo - first) // step))
    stop = None if hi is None else int(-(-(hi - first) // step))
    return start, stop


def _source_cell_rows(rows, grid, origin, spacing):
    """Per-timestep bounds rows in source cells as this file's own indices."""
    out = []
    for lab, ranges in rows:
        new = []
        for axis, lo, hi in ranges:
            a = _grid_axis(axis, grid)
            new.append((axis, *_source_cells(lo, hi, origin[a], spacing[a])))
        out.append((lab, tuple(new)))
    return tuple(out)


def _geometry(info, read_points):
    """`info`'s geometry with any listed points read in, or None when they are
    bulk data this side was told not to read (or cannot: a remote stand-in).
    Points read here are kept on `info`, so materialize does not read them
    again."""
    geometry = getattr(info, "geometry", None)
    if not geometry:
        return None                         # not reported: decided where read
    if geometry.get("kind") != "structured" or geometry.get("points") is not None:
        return geometry
    from vislang.formats.inspect import is_remote
    if not read_points or is_remote(info.filepath):
        return None
    from vislang.interpreter.load import _source_geometry
    geometry = _source_geometry(info)
    if geometry is None or geometry.get("points") is None:
        raise ValueError("region: this grid lists each point's position, but they "
                         "could not be read to place a world-coordinate box")
    info.geometry = geometry
    return geometry


def _ranges_to_cells(node, geometry, grid, steps):
    box = {}
    for axis, lo, hi in node.ranges:
        if axis not in _AXIS_INDEX:
            raise ValueError(f"region axis {axis!r} not one of x, y, z")
        box[_AXIS_INDEX[axis]] = (lo, hi)
    cells, clipped = world_box_to_cells(geometry, grid, box)
    if not cells:
        # The box holds the whole grid: say so with one full axis rather than
        # an empty region, which no plan accepts.
        cells = {0: (0, grid[0])}
    ranges = tuple((_AXES[i], a, b) for i, (a, b) in sorted(cells.items()))
    _note(steps, "region "
          + ", ".join(f"{a} {_num(lo)}..{_num(hi)}" for a, lo, hi in node.ranges)
          + " (world) -> cells "
          + ", ".join(f"{a} [{s}:{e}]" for a, s, e in ranges)
          + (f"; clipped to the grid on {', '.join(clipped)}" if clipped else ""))
    _record(node.ranges, ranges, clipped)
    return dataclasses.replace(node, ranges=ranges, coords="index")


def _bounds_to_cells(geometry, grid, rows, label, who, steps):
    """A bounds box's per-timestep rows (label, ranges) in cells: each world
    box becomes the samples it selects (world_box_to_cells, bounds inclusive,
    clipped to the grid as a fixed world box is). Only timestep `label`'s row
    when it is given."""
    if label is not None:
        rows = (tuple(r for r in rows if r[0] == label)
                or tuple(r for r in rows if r[0] is None) or rows)
    out = []
    for lab, ranges in rows:
        box = {}
        for axis, lo, hi in ranges:
            if axis not in _AXIS_INDEX:
                raise ValueError(f"region axis {axis!r} not one of x, y, z")
            box[_AXIS_INDEX[axis]] = (lo, hi)
        at = f" at timestep #{lab}" if lab is not None else ""
        try:
            cells, clipped = world_box_to_cells(geometry, grid, box)
        except ValueError as e:
            raise ValueError(f"{who}{at}: {e}") from None
        if not cells:
            cells = {0: (0, grid[0])}       # the whole grid, as one full axis
        if clipped:
            _note(steps, f"{who}{at}: box clipped to the grid edge on "
                         f"{', '.join(clipped)}")
        out.append((lab, tuple((_AXES[i], a, b) for i, (a, b) in sorted(cells.items()))))
    return tuple(out)


def _centres_to_cells(geometry, grid, size, rows, step_sizes, label, who):
    """A centre-placed box's (size, rows, step_sizes) in cells.

    Uniform: every centre and size maps linearly — a centre to its fractional
    cell index, a size to a cell count — and box_for_step rounds them as for
    any box in cells, so every box of one size keeps one cell count at every
    timestep. Structured: each row's box is placed on the points and becomes
    the smallest block holding it, written as that block's centre and size (a
    row size of its own), which box_for_step reproduces exactly."""
    if label is not None:
        # Only this timestep's box is cut here; resolving the rest is waste
        # (a structured grid masks every point per row).
        mine = tuple(r for r in rows if r[0] == label) or \
            tuple(r for r in rows if r[0] is None)
        rows = mine or rows
    sizes = dict(step_sizes)
    if geometry["kind"] == "uniform":
        o, s = geometry["origin"], geometry["spacing"]

        def to_idx(c, a):
            return (c - o[a]) / s[a]

        def to_n(sz):
            return tuple(None if v is None else v / abs(s[a]) for a, v in enumerate(sz))
        new_rows = tuple((r[0], *(to_idx(c, a) for a, c in enumerate(r[1:])))
                         for r in rows)
        new_sizes = tuple((lab, to_n(sz)) for lab, sz in sizes.items()
                          if any(r[0] == lab for r in rows))
        return to_n(size), new_rows, new_sizes
    # structured: one block per row
    ndim = len(grid)
    out_rows, out_sizes = [], {}
    for r in rows:
        lab, centre = r[0], r[1:]
        sz = sizes.get(lab, size)
        box = {a: (c - v / 2, c + v / 2) for a, (c, v) in enumerate(zip(centre, sz))
               if v is not None}
        try:
            cells, _ = world_box_to_cells(geometry, grid, box)
        except ValueError as e:
            at = f" at timestep #{lab}" if lab is not None else ""
            raise ValueError(f"{who}{at}: {e}") from None
        out_rows.append((lab, *((cells[a][0] + cells[a][1]) / 2 if a in cells
                                 else (grid[a] - 1) / 2 for a in range(ndim))))
        out_sizes[lab] = tuple(cells[a][1] - cells[a][0] if a in cells else None
                               for a in range(ndim))
    first = out_sizes[out_rows[0][0]]
    step = tuple((r[0], out_sizes[r[0]]) for r in out_rows
                 if out_sizes[r[0]] != first)
    return first, tuple(out_rows), step


def _note(steps, line):
    if steps is not None and line not in steps:
        steps.append(line)


def _num(v):
    return "open" if v is None else f"{v:g}"


def _record_modes(modes):
    try:
        from vislang.runtime import provenance
        provenance.note_region_coords(modes)
    except Exception:
        pass


def _record(written, cells, clipped):
    try:
        from vislang.runtime import provenance
        provenance.note_region_cells(
            {a: [lo, hi] for a, lo, hi in written},
            {a: [s, e] for a, s, e in cells}, clipped)
    except Exception:
        pass
