"""The declarative DSL form layer.

`form_namespace()` is the dict of form constructors injected into a spec's exec
namespace by run_pipeline. The sink-registry helpers and node types are
re-exported for the interpreter (planner.py) and the MCP server.
"""

from . import forms
from .nodes import (
    Node, reset_sinks, collected_sinks, register_sink, upstream_of,
)

# The forms the spec may call (no imports needed in the spec itself).
_FORM_NAMES = ("source", "fields", "region", "subsample",
               "threshold", "timesteps", "compress", "save", "render")


def form_namespace():
    """{name: constructor} for exec(spec, namespace). Fresh dict per call."""
    return {name: getattr(forms, name) for name in _FORM_NAMES}


def leaf_nodes(namespace):
    """Nodes bound in a post-exec namespace that nothing else consumes.

    Used for the no-sink dry run: a dangling chain's tail is whatever the spec
    left in a variable and didn't feed into a later form.
    """
    nodes = [v for v in namespace.values() if isinstance(v, Node)]
    consumed = {id(upstream_of(n)) for n in nodes if upstream_of(n) is not None}
    return [n for n in nodes if id(n) not in consumed]


__all__ = [
    "form_namespace", "leaf_nodes", "Node",
    "reset_sinks", "collected_sinks", "register_sink", "upstream_of",
]
