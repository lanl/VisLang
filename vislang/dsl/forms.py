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
    the chain over the timesteps; select a range with timesteps(node, start, stop)."""
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
        raise ValueError("region(center=/track=...) needs size=, the box edge "
                         "length (a number, or one per axis; None keeps an axis whole)")
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


def check_centers(rows, where="region(center=...)"):
    """Validate centre rows (label, cx, cy[, cz]) and return them sorted by label.
    Shared with the track-file reader so both paths enforce the same rules."""
    rows = [tuple(r) for r in rows]
    if not rows:
        raise ValueError(f"{where}: no centres given")
    ndim = len(rows[0]) - 1
    if ndim not in (2, 3):
        raise ValueError(f"{where}: a centre is (x, y) or (x, y, z), got "
                         f"{len(rows[0]) - 1} coordinate(s)")
    seen = set()
    for r in rows:
        label, coords = r[0], r[1:]
        if isinstance(label, bool) or not isinstance(label, int):
            raise TypeError(f"{where}: timestep labels must be integers (the N in "
                            f"…#N), got {label!r}")
        if label in seen:
            raise ValueError(f"{where}: timestep {label} has more than one centre")
        seen.add(label)
        if len(coords) != ndim:
            raise ValueError(f"{where}: timestep {label} has {len(coords)} "
                             f"coordinate(s), the others have {ndim}")
        if not all(_is_number(c) for c in coords):
            raise TypeError(f"{where}: timestep {label} centre must be numbers, "
                            f"got {coords!r}")
    return tuple(sorted(rows))


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
    is kept as a 1-tuple and expanded by the planner (track.resolve_tracks)."""
    if _is_number(size):
        _check_size(size, 1)
        return (size,)
    ndim = len(size) if isinstance(size, (tuple, list)) else 3
    return _check_size(size, ndim if ndim in (2, 3) else 3)


def _check_tag_sizes(size, tags, where):
    """A `size={tag: …}` dict, checked against the tags it must cover exactly."""
    for t in size:
        _check_tag(t, f"{where} size=")
    missing = [t for t in tags if t not in size]
    extra = [t for t in size if t not in tags]
    if missing:
        raise ValueError(f"{where}: size= has no entry for tag(s) "
                         f"{', '.join(map(repr, missing))}")
    if extra:
        raise ValueError(f"{where}: size= names tag(s) "
                         f"{', '.join(map(repr, extra))} that have no centre")


def build_tags(center, size, where="region(center=...)"):
    """Validate named boxes and return RegionNode.tags.

    `center` is {tag: (x, y[, z])} for one file, or {tag: {N: (x, y[, z])}} over
    a timeseries (every tag the same kind); `size` is one number, one per axis,
    or {tag: either}. Shared with the track-file reader so both enforce the
    same rules. Tags keep the order written: it is the order of the outputs."""
    tags = list(center)
    if not tags:
        raise ValueError(f"{where}: no centres given")
    seen = {}
    for t in tags:
        _check_tag(t, where)
        if t.lower() in seen:
            # A case-insensitive filesystem (macOS, Windows) would write both
            # into one folder.
            raise ValueError(f"{where}: tags {seen[t.lower()]!r} and {t!r} differ "
                             f"only in case, so their outputs would collide")
        seen[t.lower()] = t
    series = [isinstance(center[t], dict) for t in tags]
    if any(series) and not all(series):
        raise ValueError(f"{where}: give every tag a {{timestep: (x, y[, z])}} dict "
                         f"(a timeseries) or every tag one (x, y[, z]) centre (one "
                         f"file), not a mix")
    rows_by_tag = {}
    for t in tags:
        v = center[t]
        tw = f"{where} tag {t!r}"
        if isinstance(v, dict):
            rows = check_centers([(k, *c) if isinstance(c, (tuple, list)) else (k, c)
                                  for k, c in v.items()], tw)
        else:
            if not isinstance(v, (tuple, list)):
                raise TypeError(f"{tw}: expected a centre (x, y[, z]) or a dict "
                                f"{{timestep: (x, y[, z])}}, got {type(v).__name__}")
            if len(v) not in (2, 3):
                raise ValueError(f"{tw}: a centre is (x, y) or (x, y, z), got "
                                 f"{len(v)} coordinate(s)")
            if not all(_is_number(c) for c in v):
                raise TypeError(f"{tw}: centre must be numbers, got {tuple(v)!r}")
            rows = ((None, *v),)
        rows_by_tag[t] = rows
    ndims = {len(rows[0]) - 1 for rows in rows_by_tag.values()}
    if len(ndims) > 1:
        raise ValueError(f"{where}: every tag needs the same number of coordinates, "
                         f"got {sorted(ndims)}")
    ndim = ndims.pop()
    if isinstance(size, dict):
        _check_tag_sizes(size, tags, where)
        sizes = {t: _check_size(size[t], ndim) for t in tags}
    else:
        shared = _check_size(size, ndim)
        sizes = {t: shared for t in tags}
    return tuple((t, sizes[t], rows_by_tag[t]) for t in tags)


def region(node, center=None, size=None, track=None, **axes):
    """Spatial selection — "which part of the box". Lowers two ways by modality:
    grids take index-space crops [a:b] per axis (structural: pushed into the
    read as a hyperslab); point data takes a world-coordinate bounding box on
    the position variables (computed: applied as a row mask after the read).

    Over a timeseries the box can move: `center={N: (x, y[, z])}` or
    `track="file.csv"` (columns step,x,y[,z]) gives the centre per timestep, and
    `size` the box edge length in the same units, so the box is centre ± size/2.

    Several NAMED boxes: key `center` by tag (a string) — {tag: (x, y[, z])} on
    one file, {tag: {N: (x, y[, z])}} over a timeseries, where each tag lists
    only the timesteps its box exists at — or give a track file a leading `tag`
    column. `size` may then be {tag: size}. save() writes one output per tag.
    The track file is read by the planner, not here — forms read no data."""
    _require_node(node, "region")
    given = [name for name, v in (("axis ranges", axes), ("center=", center),
                                  ("track=", track)) if v]
    if len(given) > 1:
        raise ValueError(f"region(): give one of axis ranges, center= or track=, "
                         f"not {' and '.join(given)}")
    if center is not None or track is not None:
        if track is not None:
            if not isinstance(track, str) or not track:
                raise TypeError(f"region(track=...): expected a CSV path, got {track!r}")
            if isinstance(size, dict):
                # Which tags the file holds is only known once it is read; the
                # planner checks this dict against them (resolve_tracks).
                for t, s in size.items():
                    _check_tag(t, "region(track=..., size=...)")
                    _track_size(s)
                return RegionNode(upstream=node, track=track,
                                  tag_sizes=tuple((t, _track_size(s))
                                                  for t, s in size.items()))
            return RegionNode(upstream=node, size=_track_size(size), track=track)
        if not isinstance(center, dict):
            raise TypeError(f"region(center=...): expected a dict "
                            f"{{timestep: (x, y[, z])}} or {{tag: …}}, got "
                            f"{type(center).__name__}")
        named = [isinstance(k, str) for k in center]
        if center and all(named):
            return RegionNode(upstream=node, tags=build_tags(center, size))
        if any(named):
            raise ValueError("region(center=...): keys are either timestep numbers "
                             "(one box) or tags (named boxes), not a mix")
        if isinstance(size, dict):
            raise ValueError("region(size={tag: …}) needs named boxes: "
                             "center={tag: …}")
        rows = check_centers([(k, *v) if isinstance(v, (tuple, list)) else (k, v)
                              for k, v in center.items()])
        return RegionNode(upstream=node, centers=rows,
                          size=_check_size(size, len(rows[0]) - 1))
    if size is not None:
        raise ValueError("region(size=...) only applies with center= or track=")
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
                                f"None (grid: index-space ints; points: world coords), got {b!r}")
        if lo is not None and hi is not None and not lo < hi:
            raise ValueError(f"region({axis}=({lo},{hi})): need lo < hi")
        ranges.append((axis, lo, hi))
    return RegionNode(upstream=node, ranges=tuple(ranges))


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


def timesteps(node, start, stop):
    """Time-axis selection for a FOLDER (timeseries) source: keep the timesteps
    whose `#N` label is within [start, stop], INCLUSIVE (the integer in each
    filename, e.g. run#8.hdf5 -> 8). The interpreter reads this before mapping
    the rest of the chain over the selected files. On a single-file source it is
    a no-op (there is one timestep); mismatches surface at plan time."""
    _require_node(node, "timesteps")
    for v, name in ((start, "start"), (stop, "stop")):
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError(f"timesteps(): {name} must be an integer timestep "
                            f"label, got {v!r}")
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
