"""AST node types for the declarative DSL, plus the sink registry.

A spec.py is plain Python; the form constructors (forms.py) are injected into
its namespace. Calling a form does NOT run anything — it builds one of these
nodes. The chain of nodes IS the AST. The interpreter (planner.py) walks it
*after* exec and decides how to lower it to physical ops.

Every node has a single `upstream` (the node it narrows/transforms), except
SourceNode which has none. Chains are linear; two sinks may share an upstream
(a small DAG), which the planner handles per-sink.

Sinks (`render`, `save`) are the *actions* — constructing one registers it here,
and the planner executes one pipeline per registered sink. A spec with no sink
describes data but asks for no output (the planner then dry-runs it).
"""

from dataclasses import dataclass, field


class Node:
    """Marker base for every AST node. `kind` is a short tag; `is_sink` flags
    the two actions that trigger execution."""
    kind = None
    is_sink = False


# --- the source (no upstream) ----------------------------------------------
@dataclass(frozen=True)
class SourceNode(Node):
    uri: str
    positions: tuple = None
    kind = "source"


# --- narrowing nodes (declarative goals) -----------------------------------
@dataclass(frozen=True)
class FieldsNode(Node):
    upstream: Node
    keep: tuple
    kind = "fields"


@dataclass(frozen=True)
class RegionNode(Node):
    # A fixed box (`ranges`: (axis, lo, hi) per axis), OR a box placed by its
    # centre: `centers` rows (label, cx, cy[, cz]) plus a per-axis `size`
    # (None = keep that axis whole), OR several NAMED boxes: `tags` rows
    # (tag, size, centres, step_sizes), each tag's centres and sizes shaped
    # like the unnamed box's. A centre row labelled None is a box that stays
    # put: the one box on a single file, and the same box at every timestep of
    # a series. `step_sizes` rows (label, size) override `size` at the
    # timesteps they name. `track` names the CSV the centres came from and
    # `track_sha` its hash; the planner inlines the rows before any read, so
    # the plan carries data, never a path the remote cannot see. `tag_sizes`
    # holds a `size={tag: …}` until a track file's tags are known.
    upstream: Node
    ranges: tuple = ()
    centers: tuple = ()
    size: tuple = ()
    step_sizes: tuple = ()
    track: str = None
    track_sha: str = None
    tags: tuple = ()
    tag_sizes: tuple = ()
    kind = "region"

    @property
    def per_step(self):
        """True when ONE box is placed by its centre (centres, or a track file
        not yet read). Named boxes are `tagged`, not per_step."""
        if self.tags:
            return False
        return bool(self.centers) or self.track is not None

    @property
    def tagged(self):
        """True for several named boxes, each written to its own output."""
        return bool(self.tags)

    @property
    def moves(self):
        """True when some box is placed per timestep (a labelled centre row),
        so the region needs a timeseries. A track file not yet read counts."""
        if self.track is not None and not self.centers and not self.tags:
            return True
        rows = ([r for _, _, rs, _ in self.tags for r in rs] if self.tags
                else self.centers)
        return any(r[0] is not None for r in rows)

    @property
    def tag_series(self):
        """True when some named box is placed per timestep (a timeseries)."""
        return bool(self.tags) and self.moves


@dataclass(frozen=True)
class SubsampleNode(Node):
    upstream: Node
    uniform: object = None
    per_axis: tuple = ()
    kind = "subsample"


@dataclass(frozen=True)
class ThresholdNode(Node):
    upstream: Node
    var: str
    op: str
    value: float
    kind = "threshold"


@dataclass(frozen=True)
class TimestepsNode(Node):
    # Time-axis selection for a FOLDER (timeseries) source: keep timesteps whose
    # `#N` label is in [start, stop] (inclusive). Read by the planner before it
    # maps the rest of the chain over the selected files; a no-op on a single file.
    upstream: Node
    start: int
    stop: int
    kind = "timesteps"


# --- transform -------------------------------------------------------------
@dataclass(frozen=True)
class CompressNode(Node):
    upstream: Node
    variables: tuple
    error_bound: object
    mode: str = "auto"
    kind = "compress"


# --- sinks (actions) -------------------------------------------------------
@dataclass(frozen=True)
class SaveNode(Node):
    upstream: Node
    path: str
    kind = "save"
    is_sink = True

    def __post_init__(self):
        register_sink(self)


@dataclass(frozen=True)
class RenderNode(Node):
    upstream: Node
    cmap: object = None
    opacity: object = None
    kind = "render"
    is_sink = True

    def __post_init__(self):
        register_sink(self)


# ---------------------------------------------------------------------------
# Sink registry — module-level, reset per run_pipeline call.
# ---------------------------------------------------------------------------
_SINKS = []


def register_sink(node):
    _SINKS.append(node)


def reset_sinks():
    _SINKS.clear()


def collected_sinks():
    return list(_SINKS)


def upstream_of(node):
    """The node `node` narrows, or None for a source. Uniform walk helper."""
    return getattr(node, "upstream", None)
