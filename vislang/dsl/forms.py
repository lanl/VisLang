"""The DSL forms — what the LLM and human write in spec.py.

Each form is a thin constructor: it does cheap *structural* validation (types
and shapes only) and returns an AST node. It reads no data and does not check
the schema — "does axis x exist", "is this range in bounds", "is this a real
variable" all need inspect(), so they happen later in the planner's static
check (planner.validate). Calling a form runs nothing; render/save additionally
register themselves as sinks (in nodes.py).

These names are injected into the spec namespace by form_namespace() — the spec
does not import them.
"""

import math
import re

from .nodes import (
    SourceNode, FieldsNode, RegionNode, SubsampleNode,
    ThresholdNode, TimestepsNode, CompressNode, SaveNode, RenderNode, Node,
)

_OPS = (">=", "<=", "==", "!=", ">", "<")


def _require_node(node, form):
    if not isinstance(node, Node):
        raise TypeError(f"{form}() expects a DSL node as its first argument, "
                        f"got {type(node).__name__}. Build one with source(...).")


def _check_factor(value, where):
    """A subsample factor: int stride >= 1, or float fraction in (0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{where}: factor must be int or float, got {value!r}")
    if isinstance(value, int):
        if value < 1:
            raise ValueError(f"{where}: integer stride must be >= 1, got {value}")
    elif not (0 < value <= 1):
        raise ValueError(f"{where}: float fraction must be in (0, 1], got {value}")


def _parse_predicate(expr):
    """'density > 0.5' -> ('density', '>', 0.5). Scalar right-hand side only."""
    if not isinstance(expr, str):
        raise TypeError(f"threshold() expects a string like 'density > 0.5', got {expr!r}")
    for op in _OPS:
        if op in expr:
            lhs, rhs = expr.split(op, 1)
            var, rhs = lhs.strip(), rhs.strip()
            if not var:
                raise ValueError(f"threshold('{expr}'): missing variable before {op!r}")
            try:
                value = float(rhs)
            except ValueError:
                raise ValueError(f"threshold('{expr}'): right-hand side {rhs!r} must be a number")
            return var, op, value
    raise ValueError(f"threshold('{expr}'): no comparison operator; use one of {list(_OPS)}")


# ---------------------------------------------------------------------------
def source(uri, positions=None):
    """The dataset at the head of a chain. `uri` is one concrete file (globs are
    rejected), a remote source (ssh://host/path or user@host:/path), or a FOLDER
    of files — a folder is treated as a TIMESTEP SERIES: one file per timestep,
    named `…#N` where N is the timestep number. The interpreter maps the rest of
    the chain over the timesteps; select them with timesteps(node, start, stop)
    or timesteps(node, [labels])."""
    if not isinstance(uri, str) or not uri:
        raise TypeError(f"source() needs a path/URI string, got {uri!r}")
    if any(c in uri for c in "*?["):
        raise ValueError(f"source() takes a single file or a folder, not a glob "
                         f"pattern: {uri!r}. Point it at one concrete path.")
    if positions is not None:
        positions = tuple(positions)
        if len(positions) != 3 or not all(isinstance(p, str) for p in positions):
            raise ValueError(f"positions must be 3 variable names, got {positions!r}")
    return SourceNode(uri=uri, positions=positions)


def fields(node, keep):
    _require_node(node, "fields")
    if isinstance(keep, str):
        keep = (keep,)
    keep = tuple(keep)
    if not keep or not all(isinstance(v, str) for v in keep):
        raise ValueError(f"fields() needs a non-empty list of variable names, got {keep!r}")
    return FieldsNode(upstream=node, keep=keep)


_CENTER_AXES = ("x", "y", "z")


def _is_number(v):
    return not isinstance(v, bool) and isinstance(v, (int, float))


def _check_size(size, ndim):
    """`size` -> per-axis tuple of length `ndim` (None = keep that axis whole)."""
    if size is None:
        raise ValueError("region(center=...) needs size=, the box edge length "
                         "(a number, or one per axis; None keeps an axis whole), "
                         "unless every row gives its own")
    per_axis = (size,) * ndim if _is_number(size) else size
    if not isinstance(per_axis, (tuple, list)) or len(per_axis) != ndim:
        raise ValueError(f"region(size={size!r}): give one number, or {ndim} "
                         f"(one per centre coordinate)")
    for s in per_axis:
        if s is not None and (not _is_number(s) or s <= 0):
            raise ValueError(f"region(size={size!r}): each size must be a positive "
                             f"number or None")
    if all(s is None for s in per_axis):
        raise ValueError(f"region(size={size!r}): every axis is None, which keeps "
                         f"the whole domain — drop region() instead")
    return tuple(per_axis)


# A tag names an output folder (or file), so it is held to what is safe as one
# path component everywhere: no separators, no leading dot (hidden, and `..`),
# and no `#`, which marks a timestep in a file name.
_TAG = re.compile(r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,127}")


def valid_tag(tag):
    """True when `tag` can name a box's output."""
    return isinstance(tag, str) and _TAG.fullmatch(tag) is not None


def _check_tag(tag, where):
    if not valid_tag(tag):
        raise ValueError(f"{where}: tag {tag!r} must be letters, digits, '_', '-' "
                         f"or '.', not starting with '.' (it names the box's "
                         f"output folder)")


def _track_size(size):
    """A size whose dimension is only known once a track file is read: a scalar
    is kept as a 1-tuple and expanded by the planner (track.resolve_tracks).
    None is allowed here — the file may give every row its own size."""
    if size is None:
        return ()
    if _is_number(size):
        _check_size(size, 1)
        return (size,)
    ndim = len(size) if isinstance(size, (tuple, list)) else 3
    return _check_size(size, ndim if ndim in (2, 3) else 3)


_OLD_FORM = ("region(center=...): a box over a timeseries is now a list of rows "
             "(step, x, y[, z][, size]), e.g. center=[(3, 1.0, 2.0, 3.0), (11, …)] "
             "or center={{\"halo\": [(3, 1.0, 2.0, 3.0), …]}}; got {got}")


def _box_spec(value, where):
    """One box as written -> ("fixed", (x, y[, z])) or ("rows", [row, …])."""
    if isinstance(value, dict):
        raise TypeError(_OLD_FORM.format(got="a {timestep: centre} dict"))
    if isinstance(value, (tuple, list)) and not value:
        raise ValueError(f"{where}: no centres given")
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{where}: expected a centre (x, y[, z]) or a list of rows "
                        f"(step, x, y[, z][, size]), got {value!r}")
    if all(isinstance(r, (tuple, list)) for r in value):
        return "rows", [tuple(r) for r in value]
    if any(isinstance(r, (tuple, list)) for r in value):
        raise TypeError(f"{where}: give one centre (x, y[, z]) or a list of rows "
                        f"(step, x, y[, z][, size]), not a mix: {value!r}")
    return "fixed", tuple(value)


def _infer_ndim(boxes, size, where, ndim=None):
    """How many coordinates a centre has. A row's length alone can be read two
    ways — (step, x, y, z) or (step, x, y, size) — so every unambiguous cue is
    gathered (a fixed centre, a row of 3 or 5 values, a per-axis size, a track
    file's header as `ndim`) and must agree; with no cue a 4-value row is 3-D."""
    cues = {ndim} if ndim else set()
    for kind, v in boxes.values():
        if kind == "fixed":
            cues.add(len(v))
            continue
        for r in v:
            rest = r[1:]
            if rest and isinstance(rest[-1], (tuple, list)):
                cues.add(len(rest) - 1)
            elif len(rest) in (2, 4):
                cues.add(len(rest) // 2 + 1)
    for s in (size.values() if isinstance(size, dict) else (size,)):
        if isinstance(s, (tuple, list)):
            cues.add(len(s))
    cues &= {2, 3}
    if len(cues) > 1:
        raise ValueError(f"{where}: centres and sizes disagree on 2-D vs 3-D; "
                         f"every centre needs the same number of coordinates")
    return cues.pop() if cues else 3


def _parse_rows(rows, ndim, where):
    """Rows (step, x, y[, z][, size]) -> (sorted centre rows (label, cx, cy[, cz]),
    {label: per-axis size} for the rows that carry their own)."""
    axes = ", ".join(_CENTER_AXES[:ndim])
    out, sizes, seen = [], {}, set()
    for r in rows:
        if not r:
            raise ValueError(f"{where}: empty row")
        label, rest = r[0], r[1:]
        if isinstance(label, bool) or not isinstance(label, int):
            raise TypeError(f"{where}: a row starts with its timestep (the N in "
                            f"…#N), an integer; got {label!r} in {r!r}")
        if label in seen:
            raise ValueError(f"{where}: timestep {label} has more than one centre")
        seen.add(label)
        if len(rest) == ndim + 1:
            coords = rest[:-1]
            sizes[label] = _check_size(rest[-1], ndim)
        elif len(rest) == ndim:
            coords = rest
        else:
            raise ValueError(f"{where}: row {r!r} should be (step, {axes}[, size])")
        if not all(_is_number(c) for c in coords):
            raise TypeError(f"{where}: timestep {label} centre must be numbers, "
                            f"got {tuple(coords)!r}")
        out.append((label, *coords))
    if not out:
        raise ValueError(f"{where}: no centres given")
    return tuple(sorted(out)), sizes


def _check_tags(tags, where, what="centres"):
    """The keys of a {tag: box} dict: present, strings, safe as a path
    component, and distinct even on a case-insensitive filesystem."""
    if not tags:
        raise ValueError(f"{where}: no {what} given")
    if not all(isinstance(t, str) for t in tags):
        if all(isinstance(t, int) and not isinstance(t, bool) for t in tags):
            raise TypeError(_OLD_FORM.format(got="a {timestep: centre} dict")
                            if what == "centres" else _OLD_BOUNDS)
        raise TypeError(f"{where}: keys name the boxes (strings), got "
                        f"{[t for t in tags if not isinstance(t, str)]!r}")
    seen = {}
    for t in tags:
        _check_tag(t, where)
        if t.lower() in seen:
            # A case-insensitive filesystem (macOS, Windows) would write
            # both into one folder.
            raise ValueError(f"{where}: tags {seen[t.lower()]!r} and {t!r} "
                             f"differ only in case, so their outputs would collide")
        seen[t.lower()] = t


def resolve_center(center, size, where="region(center=...)", ndim=None):
    """Validate a centre spec and return the RegionNode fields it fills.

    `center` is one box or {tag: box}; a box is a centre (x, y[, z]) — a box
    that stays put, on one file or at every timestep of a series — or a list
    of rows (step, x, y[, z][, size]) placing it per timestep, where a row's
    own size overrides the box's. `size` is one number, one per axis, or {tag:
    either}; it may be left out where every row carries a size. `ndim` fixes
    the centre dimension when it is known (a track file's header). Returns
    {"centers", "size", "step_sizes"} for one box, {"tags"} for named boxes
    (tags keep the order written: it is the order of the outputs). Shared
    with the track-file reader so both enforce the same rules."""
    if isinstance(center, dict):
        tags = list(center)
        _check_tags(tags, where)
        boxes = {t: _box_spec(center[t], f"{where} tag {t!r}") for t in tags}
    else:
        if isinstance(size, dict):
            raise ValueError(f"{where}: size={{tag: …}} needs named boxes: "
                             f"center={{tag: …}}")
        boxes = {None: _box_spec(center, where)}
    if isinstance(size, dict):
        for t in size:
            _check_tag(t, f"{where} size=")
        extra = [t for t in size if t not in boxes]
        if extra:
            raise ValueError(f"{where}: size= names tag(s) "
                             f"{', '.join(map(repr, extra))} that have no centre")
    ndim = _infer_ndim(boxes, size, where, ndim)
    out = []
    for t, (kind, v) in boxes.items():
        tw = where if t is None else f"{where} tag {t!r}"
        written = size.get(t) if isinstance(size, dict) else size
        default = None if written is None else _check_size(written, ndim)
        if kind == "fixed":
            if len(v) not in (2, 3):
                raise ValueError(f"{tw}: a centre is (x, y) or (x, y, z), got "
                                 f"{len(v)} value(s); a box per timestep is a list "
                                 f"of rows [(step, x, y, z), …]")
            if len(v) != ndim:
                raise ValueError(f"{tw}: a {len(v)}-D centre, but the others are "
                                 f"{ndim}-D")
            if not all(_is_number(c) for c in v):
                raise TypeError(f"{tw}: centre must be numbers, got {v!r}")
            rows, sizes = ((None, *v),), {}
        else:
            rows, sizes = _parse_rows(v, ndim, tw)
        if default is None:
            unsized = [r[0] for r in rows if r[0] not in sizes]
            if unsized:
                if isinstance(size, dict):
                    raise ValueError(f"{where}: size= has no entry for tag {t!r}")
                _check_size(None, ndim)                 # raises: size= needed
            default = sizes[rows[0][0]]
        step_sizes = tuple((lab, s) for lab, s in sorted(sizes.items())
                           if s != default)
        out.append((t, default, rows, step_sizes))
    if isinstance(center, dict):
        return {"tags": tuple(out)}
    _, default, rows, step_sizes = out[0]
    return {"centers": rows, "size": default, "step_sizes": step_sizes}


# ---------------------------------------------------------------------------
# boxes given by their bounds
# ---------------------------------------------------------------------------
_OLD_BOUNDS = ("region(bounds=...): a box per timestep is a list of rows "
               "(step, (xlo, xhi), (ylo, yhi)[, (zlo, zhi)]), e.g. "
               "bounds=[(3, (0, 10), (0, 10), (0, 10)), …]; a dict names boxes "
               "(string keys), not timesteps")


def _is_pair(v):
    return (isinstance(v, (tuple, list)) and len(v) == 2
            and not any(isinstance(b, (tuple, list)) for b in v))


def _is_bounds_row(v):
    return (isinstance(v, (tuple, list)) and len(v) >= 2
            and not isinstance(v[0], (tuple, list))
            and all(isinstance(p, (tuple, list)) for p in v[1:]))


def _bounds_box(value, where):
    """One bounds box as written -> ("fixed", (pair, …)) or ("rows", [row, …])."""
    if isinstance(value, dict):
        raise TypeError(_OLD_BOUNDS)
    if isinstance(value, (tuple, list)) and not value:
        raise ValueError(f"{where}: no boxes given")
    if not isinstance(value, (tuple, list)):
        raise TypeError(f"{where}: expected a box ((xlo, xhi), (ylo, yhi)[, (zlo, "
                        f"zhi)]) or a list of rows (step, (xlo, xhi), …), got "
                        f"{value!r}")
    if all(_is_pair(p) for p in value):
        return "fixed", tuple(value)
    if all(_is_bounds_row(r) for r in value):
        return "rows", [tuple(r) for r in value]
    raise TypeError(f"{where}: give one box ((xlo, xhi), (ylo, yhi)[, (zlo, zhi)]) "
                    f"or a list of rows (step, (xlo, xhi), (ylo, yhi)[, (zlo, zhi)]), "
                    f"got {value!r}")


def _bounds_ranges(pairs, ndim, where):
    """One box's (lo, hi) pairs, in x, y[, z] order -> fixed-box ranges
    ((axis, lo, hi), …), held to the checks region(x=(lo, hi)) makes. A pair of
    (None, None) keeps that axis whole, so it is left out — as an axis that is
    not written is."""
    if len(pairs) not in (2, 3):
        raise ValueError(f"{where}: a box is two or three (lo, hi) pairs, one per "
                         f"axis in x, y[, z] order, got {len(pairs)}")
    if ndim is not None and len(pairs) != ndim:
        raise ValueError(f"{where}: a {len(pairs)}-D box, but the others are "
                         f"{ndim}-D; every box needs the same number of axes")
    ranges = []
    for axis, pair in zip(_CENTER_AXES, pairs):
        if not _is_pair(pair):
            raise ValueError(f"{where}: {axis} bounds must be a (lo, hi) pair, got "
                             f"{pair!r}")
        lo, hi = pair
        for b in (lo, hi):
            if b is not None and (not _is_number(b) or not math.isfinite(b)):
                raise TypeError(f"{where}: {axis} bounds must be finite numbers or "
                                f"None, got {b!r}")
        if lo is not None and hi is not None and not lo < hi:
            raise ValueError(f"{where}: {axis}=({lo}, {hi}): need lo < hi")
        if lo is not None or hi is not None:
            ranges.append((axis, lo, hi))
    if not ranges:
        raise ValueError(f"{where}: every axis is (None, None), which keeps the "
                         f"whole domain — drop region(), or give that box a bound")
    return tuple(ranges)


def _bounds_rows(rows, ndim, where):
    """Rows (step, (lo, hi), …) -> sorted (label, ranges) rows."""
    out, seen = [], set()
    for r in rows:
        label = r[0]
        if isinstance(label, bool) or not isinstance(label, int):
            raise TypeError(f"{where}: a row starts with its timestep (the N in "
                            f"…#N), an integer; got {label!r} in {r!r}")
        if label in seen:
            raise ValueError(f"{where}: timestep {label} has more than one box")
        seen.add(label)
        out.append((label, _bounds_ranges(r[1:], ndim, f"{where} timestep {label}")))
    return tuple(sorted(out, key=lambda r: r[0]))


def resolve_bounds(bounds, where="region(bounds=...)", ndim=None):
    """Validate a bounds spec and return the RegionNode fields it fills.

    `bounds` is one box or {tag: box}; a box is ((xlo, xhi), (ylo, yhi)[,
    (zlo, zhi)]) — a box that stays put — or a list of rows (step, (xlo, xhi),
    …) giving it per timestep. Each box means exactly what region(x=(lo, hi),
    …) means at its timestep. Returns {"ranges"} for one box that stays put
    (it IS that fixed box), {"step_ranges"} for one box per timestep,
    {"tag_ranges"} for named boxes (in the order written). `ndim` fixes the
    box dimension when it is known (a track file's header). Shared with the
    track-file reader so both enforce the same rules."""
    if isinstance(bounds, dict):
        tags = list(bounds)
        _check_tags(tags, where, what="boxes")
        boxes = {t: _bounds_box(bounds[t], f"{where} tag {t!r}") for t in tags}
    else:
        boxes = {None: _bounds_box(bounds, where)}
    dims = set()
    for kind, v in boxes.values():
        dims |= {len(v)} if kind == "fixed" else {len(r) - 1 for r in v}
    if ndim is None:
        if len(dims & {2, 3}) > 1:
            raise ValueError(f"{where}: boxes disagree on 2-D vs 3-D; every box "
                             f"needs the same number of (lo, hi) pairs")
        ndim = (dims & {2, 3}).pop() if dims & {2, 3} else None
    out = []
    for t, (kind, v) in boxes.items():
        tw = where if t is None else f"{where} tag {t!r}"
        if kind == "fixed":
            rows = ((None, _bounds_ranges(v, ndim, tw)),)
        else:
            rows = _bounds_rows(v, ndim, tw)
        out.append((t, rows))
    if isinstance(bounds, dict):
        return {"tag_ranges": tuple(out)}
    rows = out[0][1]
    if rows[0][0] is None:
        return {"ranges": rows[0][1]}
    return {"step_ranges": rows}


def _mixed_tags(center, bounds, size, coords, node):
    """center={…} and bounds={…} in one region: named boxes of both kinds, the
    centre tags first. size= sizes the centre tags only."""
    if not (isinstance(center, dict) and isinstance(bounds, dict)):
        raise ValueError("region(): center= and bounds= together only name boxes: "
                         "give both as {tag: …} dicts with different tags, e.g. "
                         "center={\"a\": (x, y, z)}, size=s, bounds={\"b\": ((xlo, "
                         "xhi), (ylo, yhi), (zlo, zhi))}; otherwise give one of them")
    clash = sorted({t.lower() for t in center if isinstance(t, str)}
                   & {t.lower() for t in bounds if isinstance(t, str)})
    if clash:
        raise ValueError(f"region(): tag(s) {', '.join(map(repr, clash))} appear in "
                         f"both center= and bounds=; a name holds one box — rename "
                         f"one, or give it in one of them")
    if isinstance(size, dict):
        named = [t for t in size if t in bounds]
        if named:
            raise ValueError(f"region(size=...): {', '.join(map(repr, named))} "
                             f"is a bounds box, which needs no size; size= covers "
                             f"the center= tags only")
    tags = resolve_center(center, size)["tags"]
    tag_ranges = resolve_bounds(bounds)["tag_ranges"]
    return RegionNode(upstream=node, tags=tags, tag_ranges=tag_ranges, coords=coords)


COORDS = ("world", "cells")


def region(node, center=None, size=None, track=None, coords=None, bounds=None,
           **axes):
    """Spatial selection — "which part of the box", in the data's OWN
    coordinates: the ones ParaView shows on its axes.

        point data              world coordinates (the position variables)
        a grid that says where  world coordinates (its origin + spacing, or
        it sits in space        each point's position): x=(10.5, 22.0)
        a grid that does not    cells — and its world coordinates ARE its cell
                                indices (origin 0, spacing 1, as VTK reads it)

    A world box keeps the samples whose position lies inside it, bounds
    inclusive; on a grid it becomes an index crop pushed into the read. A cell
    range [a, b) is that crop given directly. `coords=` pins which is meant
    where it matters: "cells" crops by index even on a grid with geometry;
    "world" refuses a grid that does not say where it sits, instead of reading
    the numbers as cells. Left out, the planner decides from the file and
    records what it decided.

    Or place a box by its centre: centre ± size/2, `size` the edge length in
    the same units (one number, or one per axis). `center` is

        (x, y[, z])                        a box that stays put — on one file,
                                           or at every timestep of a series
        [(step, x, y[, z][, size]), …]     a box per timestep; a row's own
                                           size overrides size= there
        "track.csv"                        the same rows from a file (header
                                           step,x,y[,z][,size])
        {tag: either of the first two}     several NAMED boxes; save() writes
                                           one output per tag. size= may then
                                           be {tag: size}.

    Or give a box by its bounds — the box x=(lo, hi), … writes, at each
    timestep it names. `bounds` is shaped like `center`, one (lo, hi) pair per
    axis in x, y[, z] order (None open-ended, (None, None) the whole axis):

        ((xlo, xhi), (ylo, yhi)[, (zlo, zhi)])    one box (as x=, y=, z=)
        [(step, (xlo, xhi), …), …]               a box per timestep
        "boxes.csv"                              the same rows from a file
                                                 (header step,xmin,xmax,ymin,
                                                 ymax[,zmin,zmax])
        {tag: either of the first two}           several NAMED boxes

    No size= (a bounds box already says its extent), except beside
    center={tag: …}: centre tags and bounds tags may share one region when
    their names differ, the centre tags' outputs first.

    A track file names its boxes with a leading `tag` column. The file is read
    by the planner, not here — forms read no data. (`track=` is the older
    spelling of center="track.csv".)"""
    _require_node(node, "region")
    if coords is not None and coords not in COORDS:
        raise ValueError(f"region(coords={coords!r}): expected one of "
                         f"{', '.join(repr(c) for c in COORDS)}, or leave it out "
                         f"to use the data's own coordinates")
    if bounds is not None:
        return _region_bounds(node, bounds, center, size, track, coords, axes)
    if isinstance(center, str):
        if track is not None:
            raise ValueError("region(): give the track file once, as center=")
        center, track = None, center
    given = [name for name, v in (("axis ranges", axes), ("center=", center),
                                  ("track=", track)) if v]
    if len(given) > 1:
        raise ValueError(f"region(): give one of axis ranges or center=, "
                         f"not {' and '.join(given)}")
    if track is not None:
        if not isinstance(track, str) or not track:
            raise TypeError(f"region(center=...): expected a CSV path, got {track!r}")
        if isinstance(size, dict):
            # Which tags the file holds is only known once it is read; the
            # planner checks this dict against them (resolve_tracks).
            for t, s in size.items():
                _check_tag(t, "region(center=\"….csv\", size=...)")
                _track_size(s)
            return RegionNode(upstream=node, track=track, coords=coords,
                              tag_sizes=tuple((t, _track_size(s))
                                              for t, s in size.items()))
        return RegionNode(upstream=node, size=_track_size(size), track=track,
                          coords=coords)
    if center is not None:
        return RegionNode(upstream=node, coords=coords, **resolve_center(center, size))
    if size is not None:
        raise ValueError("region(size=...) only applies with center=")
    if not axes:
        raise ValueError("region() needs at least one axis range, e.g. region(d, x=(0, 50))")
    ranges = []
    for axis, rng in axes.items():
        if not (isinstance(rng, (tuple, list)) and len(rng) == 2):
            raise ValueError(f"region({axis}=...): expected a (lo, hi) pair, got {rng!r}")
        lo, hi = rng
        for b in (lo, hi):
            if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float))):
                raise TypeError(f"region({axis}=({lo},{hi})): bounds must be numbers or "
                                f"None, got {b!r}")
        if lo is not None and hi is not None and not lo < hi:
            raise ValueError(f"region({axis}=({lo},{hi})): need lo < hi")
        ranges.append((axis, lo, hi))
    return RegionNode(upstream=node, ranges=tuple(ranges), coords=coords)


def _region_bounds(node, bounds, center, size, track, coords, axes):
    """region(bounds=...): the checks on what may come with it, then the box."""
    if axes:
        raise ValueError(f"region(): give the box once — as bounds= or as axis "
                         f"ranges ({', '.join(f'{a}=' for a in axes)}), not both")
    if track is not None:
        raise ValueError("region(): track= is a centre file; a min/max file is "
                         "bounds=\"….csv\" alone")
    if center is not None:
        if isinstance(center, str) or isinstance(bounds, str):
            raise ValueError("region(): center= and bounds= together only name "
                             "boxes inline ({tag: …} dicts); a track file is given "
                             "alone — put every box in one file, or write both "
                             "dicts in the spec")
        return _mixed_tags(center, bounds, size, coords, node)
    if size is not None:
        raise ValueError("region(bounds=..., size=...): a bounds box already says "
                         "its extent, so it takes no size; drop size=, or place "
                         "the box by its centre with center=")
    if isinstance(bounds, str):
        if not bounds:
            raise TypeError("region(bounds=...): expected a CSV path, got ''")
        return RegionNode(upstream=node, track=bounds, track_kind="bounds",
                          coords=coords)
    return RegionNode(upstream=node, coords=coords, **resolve_bounds(bounds))


def subsample(node, factor=None, **axes):
    _require_node(node, "subsample")
    if factor is not None and axes:
        raise ValueError("subsample(): give a single uniform factor OR per-axis "
                         "factors, not both")
    if factor is None and not axes:
        raise ValueError("subsample() needs a factor, e.g. subsample(d, 2) or "
                         "subsample(d, x=5, y=5, z=5)")
    if factor is not None:
        _check_factor(factor, "subsample")
        return SubsampleNode(upstream=node, uniform=factor, per_axis=())
    per_axis = []
    for axis, f in axes.items():
        _check_factor(f, f"subsample({axis}=...)")
        per_axis.append((axis, f))
    return SubsampleNode(upstream=node, uniform=None, per_axis=tuple(per_axis))


def threshold(node, expr):
    """Value selection — keep elements where "var op value" holds. On point data
    rows are dropped (row mask); on grids failing voxels are NaN-masked (the
    cube keeps its shape)."""
    _require_node(node, "threshold")
    var, op, value = _parse_predicate(expr)
    return ThresholdNode(upstream=node, var=var, op=op, value=value)


def timesteps(node, start, stop=None):
    """Time-axis selection for a FOLDER (timeseries) source, by `#N` label (the
    integer in each filename, e.g. run#8.hdf5 -> 8):

        timesteps(src, 1000, 5000)          # every label in [1000, 5000], inclusive
        timesteps(src, [1000, 3000, 9000])  # exactly these labels

    Only the selected files are read. The interpreter reads this before mapping
    the rest of the chain over them; it applies only to a folder source."""
    _require_node(node, "timesteps")

    def label(v, name):
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"timesteps(): {name} must be an integer timestep "
                            f"label, got {v!r}")
        return v

    if isinstance(start, (list, tuple)):
        if stop is not None:
            raise TypeError("timesteps(): give a list of labels, or start and "
                            "stop — not both")
        labels = tuple(sorted({label(v, "each label") for v in start}))
        if not labels:
            raise ValueError("timesteps(): the list of labels is empty")
        return TimestepsNode(upstream=node, start=labels[0], stop=labels[-1],
                             labels=labels)
    if stop is None:
        raise TypeError("timesteps(): give start and stop, or a list of labels")
    label(start, "start"), label(stop, "stop")
    if start > stop:
        raise ValueError(f"timesteps(): need start <= stop, got ({start}, {stop})")
    return TimestepsNode(upstream=node, start=start, stop=stop)


def compress(node, variables, error_bound, mode="auto"):
    _require_node(node, "compress")
    if isinstance(variables, str):
        variables = (variables,)
    variables = tuple(variables)
    if not variables or not all(isinstance(v, str) for v in variables):
        raise ValueError(f"compress() needs variable names, got {variables!r}")
    return CompressNode(upstream=node, variables=variables,
                        error_bound=error_bound, mode=mode)


def save(node, path):
    _require_node(node, "save")
    if not isinstance(path, str) or not path:
        raise TypeError(f"save() needs a destination path string, got {path!r}")
    return SaveNode(upstream=node, path=path)


def render(node, cmap=None, opacity=None):
    _require_node(node, "render")
    return RenderNode(upstream=node, cmap=cmap, opacity=opacity)
