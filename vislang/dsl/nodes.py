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
    upstream: Node
    ranges: tuple
    kind = "region"


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
