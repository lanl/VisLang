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


def region(node, **axes):
    """Spatial selection — "which part of the box". Lowers two ways by modality:
    grids take index-space crops [a:b] per axis (structural: pushed into the
    read as a hyperslab); point data takes a world-coordinate bounding box on
    the position variables (computed: applied as a row mask after the read)."""
    _require_node(node, "region")
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
