"""AST <-> JSON wire layer: a pipeline travels to the remote executor as DATA.

WHY: REMOTE_COMPUTE_PLAN.md pushes the narrowing prefix of a pipeline onto the
host that owns the data, where vislang_exec runs it. Shipping the spec itself
would mean exec()ing text that crossed a network — off the table under the
soundness principle (never trust the wire, never run generated code). So
the linear node chain is flattened to a versioned plan.json here, and the wire
side is treated as hostile: `from_plan` validates every level of the document
— allowlisted kinds, exact per-kind key sets, type-checked values (JSON bools
are rejected wherever a number is expected), source at the head and nowhere
else — and only then rebuilds nodes by calling the dataclass constructors in
dsl_forms.nodes directly, upstream-first, re-tupling every list (the nodes are
frozen). Anything unexpected raises PlanValidationError before a single node
exists; there is no lenient mode.

The validator also mirrors the structural invariants forms.py enforces at
construction (glob-free uri, lo < hi, factor ranges, op allowlist), so the set
of rebuildable nodes equals the set of forms-constructible nodes — the planner
never sees a chain a local spec could not have produced. Semantic checks
("does axis x exist", "is the range in bounds") are NOT done here; they stay
in planner.validate, which the executor runs against the real file.

Side effect on rebuild: constructing a save/render node fires its sink
registration (nodes.register_sink via __post_init__). That is intended — the
executor calls dsl_forms.reset_sinks() before from_plan, then runs the
registered sinks exactly as a locally exec'd spec would.

Wire format (version PLAN_VERSION):
    {"vislang_plan": 1, "chain": [{"kind": "source", ...}, ...]}
chain[0] is the source; order is source -> terminal.
"""

import json

from dsl_forms.nodes import (
    SourceNode, FieldsNode, RegionNode, SubsampleNode,
    ThresholdNode, CompressNode, SaveNode, RenderNode, TimestepsNode, upstream_of,
)

PLAN_KEY = "vislang_plan"
PLAN_VERSION = 1

# comparison ops threshold() can produce — mirrors the allowlist in forms.py
_OPS = (">=", "<=", "==", "!=", ">", "<")


class PlanValidationError(ValueError):
    """The wire plan failed validation; nothing was rebuilt."""


# ---------------------------------------------------------------------------
# small type predicates (bool is an int subclass in Python — always exclude it
# where a number is expected, so JSON true/false can't smuggle past a check)
# ---------------------------------------------------------------------------
def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_seq(x):
    # json gives lists; hand-built plans may carry tuples — accept both.
    return isinstance(x, (list, tuple))


def _as_list(x):
    # tuples -> lists for the wire; leave anything else for the validator.
    return list(x) if _is_seq(x) else x


# ---------------------------------------------------------------------------
# serialize: one node -> one wire step (values passed through unchanged;
# to_plan runs the shared validator on the result, so a node carrying garbage
# in an object-typed field fails loudly here, not as a json.dumps TypeError)
# ---------------------------------------------------------------------------
def _ser_source(n):
    return {"kind": "source", "uri": n.uri, "positions": _as_list(n.positions)
            if n.positions is not None else None}


def _ser_fields(n):
    return {"kind": "fields", "keep": list(n.keep)}


def _ser_region(n):
    return {"kind": "region", "ranges": [_as_list(r) for r in n.ranges]}


def _ser_subsample(n):
    return {"kind": "subsample", "uniform": n.uniform,
            "per_axis": [_as_list(p) for p in n.per_axis]}


def _ser_threshold(n):
    return {"kind": "threshold", "var": n.var, "op": n.op, "value": n.value}


def _ser_compress(n):
    return {"kind": "compress", "variables": list(n.variables),
            "error_bound": n.error_bound, "mode": n.mode}


def _ser_save(n):
    return {"kind": "save", "path": n.path}


def _ser_render(n):
    return {"kind": "render", "cmap": n.cmap, "opacity": _as_list(n.opacity)
            if n.opacity is not None else None}


def _ser_timesteps(n):
    return {"kind": "timesteps", "start": n.start, "stop": n.stop}


_SERIALIZERS = {
    "source": _ser_source, "fields": _ser_fields, "region": _ser_region,
    "subsample": _ser_subsample, "threshold": _ser_threshold,
    "compress": _ser_compress, "save": _ser_save, "render": _ser_render,
    "timesteps": _ser_timesteps,
}

# exact key set per kind — anything missing OR extra is rejected
_STEP_KEYS = {
    "source":    frozenset(("kind", "uri", "positions")),
    "fields":    frozenset(("kind", "keep")),
    "region":    frozenset(("kind", "ranges")),
    "subsample": frozenset(("kind", "uniform", "per_axis")),
    "threshold": frozenset(("kind", "var", "op", "value")),
    "compress":  frozenset(("kind", "variables", "error_bound", "mode")),
    "save":      frozenset(("kind", "path")),
    "render":    frozenset(("kind", "cmap", "opacity")),
    "timesteps": frozenset(("kind", "start", "stop")),
}


# ---------------------------------------------------------------------------
# validate: per-kind checks; `where` prefixes every error with chain position
# ---------------------------------------------------------------------------
def _fail(where, msg):
    raise PlanValidationError(f"{where}: {msg}")


def _check_factor(f, where, what):
    """Mirror of forms._check_factor: int stride >= 1 or float in (0, 1]."""
    if not _is_num(f):
        _fail(where, f"{what} must be a number, got {f!r}")
    if isinstance(f, int):
        if f < 1:
            _fail(where, f"{what}: integer stride must be >= 1, got {f}")
    elif not (0 < f <= 1):
        _fail(where, f"{what}: float fraction must be in (0, 1], got {f}")


def _val_source(s, where):
    uri = s["uri"]
    if not isinstance(uri, str) or not uri:
        _fail(where, f"'uri' must be a non-empty string, got {uri!r}")
    if any(c in uri for c in "*?["):
        _fail(where, f"'uri' must be a single file, not a glob: {uri!r}")
    p = s["positions"]
    if p is not None:
        if not _is_seq(p) or len(p) != 3 or not all(isinstance(v, str) for v in p):
            _fail(where, f"'positions' must be null or 3 variable names, got {p!r}")


def _val_fields(s, where):
    keep = s["keep"]
    if not _is_seq(keep) or not keep or not all(isinstance(v, str) for v in keep):
        _fail(where, f"'keep' must be a non-empty list of strings, got {keep!r}")


def _val_region(s, where):
    ranges = s["ranges"]
    if not _is_seq(ranges) or not ranges:
        _fail(where, f"'ranges' must be a non-empty list, got {ranges!r}")
    for r in ranges:
        if not _is_seq(r) or len(r) != 3:
            _fail(where, f"each range must be [axis, lo, hi], got {r!r}")
        axis, lo, hi = r
        if not isinstance(axis, str) or not axis:
            _fail(where, f"range axis must be a non-empty string, got {axis!r}")
        for b in (lo, hi):
            if b is not None and not _is_num(b):
                _fail(where, f"range bounds must be numbers or null, got {b!r}")
        if lo is not None and hi is not None and not lo < hi:
            _fail(where, f"range ({axis}): need lo < hi, got ({lo}, {hi})")


def _val_subsample(s, where):
    uniform, per_axis = s["uniform"], s["per_axis"]
    if not _is_seq(per_axis):
        _fail(where, f"'per_axis' must be a list, got {per_axis!r}")
    if uniform is not None and per_axis:
        _fail(where, "give a uniform factor OR per-axis factors, not both")
    if uniform is None and not per_axis:
        _fail(where, "needs a uniform factor or per-axis factors")
    if uniform is not None:
        _check_factor(uniform, where, "'uniform'")
    for p in per_axis:
        if not _is_seq(p) or len(p) != 2:
            _fail(where, f"each per_axis entry must be [axis, factor], got {p!r}")
        axis, f = p
        if not isinstance(axis, str) or not axis:
            _fail(where, f"per_axis axis must be a non-empty string, got {axis!r}")
        _check_factor(f, where, f"per_axis[{axis!r}] factor")


def _val_threshold(s, where):
    var, op, value = s["var"], s["op"], s["value"]
    if not isinstance(var, str) or not var:
        _fail(where, f"'var' must be a non-empty string, got {var!r}")
    if op not in _OPS:
        _fail(where, f"'op' must be one of {list(_OPS)}, got {op!r}")
    if not _is_num(value):
        _fail(where, f"'value' must be a number, got {value!r}")


def _val_compress(s, where):
    variables = s["variables"]
    if (not _is_seq(variables) or not variables
            or not all(isinstance(v, str) for v in variables)):
        _fail(where, f"'variables' must be a non-empty list of strings, "
                     f"got {variables!r}")
    if not _is_num(s["error_bound"]):
        _fail(where, f"'error_bound' must be a number, got {s['error_bound']!r}")
    if not isinstance(s["mode"], str) or not s["mode"]:
        _fail(where, f"'mode' must be a non-empty string, got {s['mode']!r}")


def _val_save(s, where):
    if not isinstance(s["path"], str) or not s["path"]:
        _fail(where, f"'path' must be a non-empty string, got {s['path']!r}")


def _val_render(s, where):
    cmap, opacity = s["cmap"], s["opacity"]
    if cmap is not None and not isinstance(cmap, str):
        _fail(where, f"'cmap' must be null or a string, got {cmap!r}")
    if opacity is not None:
        if not _is_seq(opacity) or not all(_is_num(v) for v in opacity):
            _fail(where, f"'opacity' must be null or a list of numbers, "
                         f"got {opacity!r}")


def _val_timesteps(s, where):
    """Mirror forms.timesteps: start/stop are integer labels (never bool, never
    float — an fp label can't index a `#N` file) with start <= stop."""
    for k in ("start", "stop"):
        v = s[k]
        if isinstance(v, bool) or not isinstance(v, int):
            _fail(where, f"'{k}' must be an integer timestep label, got {v!r}")
    if s["start"] > s["stop"]:
        _fail(where, f"need start <= stop, got ({s['start']}, {s['stop']})")


_VALIDATORS = {
    "source": _val_source, "fields": _val_fields, "region": _val_region,
    "subsample": _val_subsample, "threshold": _val_threshold,
    "compress": _val_compress, "save": _val_save, "render": _val_render,
    "timesteps": _val_timesteps,
}


def _validate_plan(plan):
    """Raise PlanValidationError unless `plan` is a well-formed wire document."""
    if not isinstance(plan, dict):
        raise PlanValidationError(f"plan must be a dict, got {type(plan).__name__}")
    if set(plan) != {PLAN_KEY, "chain"}:
        raise PlanValidationError(
            f"plan must have exactly the keys [{PLAN_KEY!r}, 'chain'], "
            f"got {sorted(plan)}")
    version = plan[PLAN_KEY]
    if not isinstance(version, int) or isinstance(version, bool) \
            or version != PLAN_VERSION:
        raise PlanValidationError(
            f"unsupported plan version {version!r} (expected {PLAN_VERSION})")
    chain = plan["chain"]
    if not _is_seq(chain) or not chain:
        raise PlanValidationError(f"'chain' must be a non-empty list, got {chain!r}")
    for i, step in enumerate(chain):
        where = f"chain[{i}]"
        if not isinstance(step, dict):
            _fail(where, f"each step must be a dict, got {type(step).__name__}")
        kind = step.get("kind")
        if kind not in _STEP_KEYS:
            _fail(where, f"unknown kind {kind!r} "
                         f"(allowed: {sorted(_STEP_KEYS)})")
        if i == 0 and kind != "source":
            _fail(where, f"chain must start with a source, got {kind!r}")
        if i > 0 and kind == "source":
            _fail(where, "source is only allowed at the head of the chain")
        if set(step) != _STEP_KEYS[kind]:
            missing = sorted(_STEP_KEYS[kind] - set(step))
            extra = sorted(set(step) - _STEP_KEYS[kind])
            _fail(where, f"({kind}) bad keys: missing {missing}, unknown {extra}")
        _VALIDATORS[kind](step, f"{where} ({kind})")


# ---------------------------------------------------------------------------
# rebuild: one validated wire step -> one node (upstream built first);
# every wire list becomes a tuple so the frozen dataclasses hold immutables
# ---------------------------------------------------------------------------
def _build_source(s, _):
    p = s["positions"]
    return SourceNode(uri=s["uri"], positions=None if p is None else tuple(p))


def _build_fields(s, up):
    return FieldsNode(upstream=up, keep=tuple(s["keep"]))


def _build_region(s, up):
    return RegionNode(upstream=up, ranges=tuple(tuple(r) for r in s["ranges"]))


def _build_subsample(s, up):
    return SubsampleNode(upstream=up, uniform=s["uniform"],
                         per_axis=tuple(tuple(p) for p in s["per_axis"]))


def _build_threshold(s, up):
    return ThresholdNode(upstream=up, var=s["var"], op=s["op"],
                         value=float(s["value"]))


def _build_compress(s, up):
    return CompressNode(upstream=up, variables=tuple(s["variables"]),
                        error_bound=s["error_bound"], mode=s["mode"])


def _build_save(s, up):
    return SaveNode(upstream=up, path=s["path"])       # registers as a sink


def _build_render(s, up):
    o = s["opacity"]
    return RenderNode(upstream=up, cmap=s["cmap"],
                      opacity=None if o is None else tuple(o))


def _build_timesteps(s, up):
    return TimestepsNode(upstream=up, start=s["start"], stop=s["stop"])


_BUILDERS = {
    "source": _build_source, "fields": _build_fields, "region": _build_region,
    "subsample": _build_subsample, "threshold": _build_threshold,
    "compress": _build_compress, "save": _build_save, "render": _build_render,
    "timesteps": _build_timesteps,
}


# ---------------------------------------------------------------------------
# public api
# ---------------------------------------------------------------------------
def to_plan(terminal_node):
    """Flatten the chain ending at `terminal_node` to a wire dict.

    Walks upstream_of() source-ward and reverses, so chain[0] is the source.
    The result is self-checked with the same validator from_plan uses — an
    emitted plan is guaranteed to round-trip.
    """
    nodes = []
    node = terminal_node
    while node is not None:
        nodes.append(node)
        node = upstream_of(node)
    nodes.reverse()
    steps = []
    for n in nodes:
        ser = _SERIALIZERS.get(getattr(n, "kind", None))
        if ser is None:
            raise PlanValidationError(
                f"cannot serialize node of kind {getattr(n, 'kind', None)!r} "
                f"({type(n).__name__})")
        steps.append(ser(n))
    plan = {PLAN_KEY: PLAN_VERSION, "chain": steps}
    _validate_plan(plan)
    return plan


def to_plan_json(terminal_node):
    """to_plan, as JSON text (the bytes that go over the wire)."""
    return json.dumps(to_plan(terminal_node))


def from_plan(plan):
    """Validate a wire dict strictly, then rebuild the chain; return the
    terminal node. Validation completes before any node is constructed, so a
    rejected plan registers no sinks and leaves no partial state."""
    _validate_plan(plan)
    node = None
    for step in plan["chain"]:
        node = _BUILDERS[step["kind"]](step, node)
    return node


def from_plan_json(text):
    """from_plan on JSON text; malformed JSON is a PlanValidationError too."""
    try:
        plan = json.loads(text)
    except (ValueError, TypeError) as e:
        raise PlanValidationError(f"plan is not valid JSON: {e}") from e
    return from_plan(plan)
