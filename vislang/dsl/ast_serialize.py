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

from vislang.dsl.forms import valid_tag
from vislang.dsl.nodes import (
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


def _step_sizes_wire(step_sizes):
    return [[lab, _as_list(sz)] for lab, sz in step_sizes]


def _ser_region(n):
    out = {"kind": "region", "ranges": [_as_list(r) for r in n.ranges]}
    if n.tagged:
        # Named boxes: each tag's size and centre table travel inline.
        # A 4th entry, when present, lists the rows that carry their own size.
        out.update(tags=[[t, _as_list(size), [_as_list(r) for r in rows]]
                         + ([_step_sizes_wire(st)] if st else [])
                         for t, size, rows, st in n.tags],
                   track=n.track, track_sha=n.track_sha)
    elif n.per_step:
        # A moving box: the centre table travels inline (the planner resolves a
        # track file before shipping). A fixed box keeps the old shape exactly.
        out.update(centers=[_as_list(r) for r in n.centers],
                   size=_as_list(n.size), track=n.track, track_sha=n.track_sha)
        if n.step_sizes:
            out["step_sizes"] = _step_sizes_wire(n.step_sizes)
        if n.tag_sizes:
            # A track not read yet whose size names its boxes ({tag: size}).
            out["tag_sizes"] = [[t, _as_list(sz)] for t, sz in n.tag_sizes]
    return out


def _ser_subsample(n):
    return {"kind": "subsample", "uniform": n.uniform,
            "per_axis": [_as_list(p) for p in n.per_axis],
            "method": n.method}


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
    "subsample": frozenset(("kind", "uniform", "per_axis", "method")),
    "threshold": frozenset(("kind", "var", "op", "value")),
    "compress":  frozenset(("kind", "variables", "error_bound", "mode")),
    "save":      frozenset(("kind", "path")),
    "render":    frozenset(("kind", "cmap", "opacity")),
    "timesteps": frozenset(("kind", "start", "stop")),
}

# keys a kind MAY carry on top of _STEP_KEYS (absent = the old wire shape)
_PER_STEP_KEYS = frozenset(("centers", "size", "track", "track_sha"))
_TAGGED_KEYS = frozenset(("tags", "track", "track_sha"))
_PER_STEP_EXTRA = frozenset(("step_sizes", "tag_sizes"))
_OPTIONAL_KEYS = {
    "region": _PER_STEP_KEYS | _TAGGED_KEYS | _PER_STEP_EXTRA,
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


def _val_track(s, where):
    track = s["track"]
    if track is not None and not (isinstance(track, str) and track):
        _fail(where, f"'track' must be a path string or null, got {track!r}")
    if s["track_sha"] is not None and not isinstance(s["track_sha"], str):
        _fail(where, f"'track_sha' must be a string or null, got {s['track_sha']!r}")


def _val_size(size, where, allow_empty=False):
    if not _is_seq(size) or (not size and not allow_empty) or not all(
            v is None or (_is_num(v) and v > 0) for v in size):
        _fail(where, f"'size' must be a list of positive numbers or nulls, got {size!r}")
    if size and all(v is None for v in size):
        _fail(where, "'size' keeps every axis whole; that is no box at all")


def _val_tag_sizes(tag_sizes, where):
    if not _is_seq(tag_sizes) or not tag_sizes:
        _fail(where, f"'tag_sizes' must be a non-empty list of [tag, size], "
                     f"got {tag_sizes!r}")
    for e in tag_sizes:
        if not _is_seq(e) or len(e) != 2 or not valid_tag(e[0]):
            _fail(where, f"each tag size must be [tag, size], got {e!r}")
        _val_size(e[1], where)


def _val_step_sizes(step_sizes, labels, ndim, where):
    """[[label, size], ...]: a size override per timestep, each naming one of
    the box's own labelled centres, shaped like the box's size."""
    if not _is_seq(step_sizes) or not step_sizes:
        _fail(where, f"'step_sizes' must be a non-empty list of [label, size], "
                     f"got {step_sizes!r}")
    seen = set()
    for e in step_sizes:
        if not _is_seq(e) or len(e) != 2 or e[0] is None or e[0] not in labels \
                or e[0] in seen:
            _fail(where, f"each step size must be [label, size] for one of the "
                         f"box's timesteps, once; got {e!r}")
        seen.add(e[0])
        _val_size(e[1], where)
        if ndim is not None and len(e[1]) != ndim:
            _fail(where, f"step size {e!r} has {len(e[1])} entries for "
                         f"{ndim}-D centres")


def _val_region_tagged(s, where):
    """Named boxes: [[tag, size, [[label|null, x, y(, z)], ...](, step_sizes)],
    ...]. The invariants forms.resolve_center enforces, so a rebuilt node is
    one a spec could have built: valid, distinct tags; each tag placed per
    timestep (integer labels, each once) or one centre that stays put (null
    label); per-row sizes only at a tag's own timesteps."""
    if s["ranges"]:
        _fail(where, "a region has either 'ranges' or 'tags', not both")
    if set(s) != _STEP_KEYS["region"] | _TAGGED_KEYS:
        _fail(where, "a region with named boxes needs exactly tags, track and track_sha")
    _val_track(s, where)
    tags = s["tags"]
    if not _is_seq(tags) or not tags:
        _fail(where, f"'tags' must be a non-empty list, got {tags!r}")
    seen, ndim = set(), None
    for e in tags:
        if not _is_seq(e) or len(e) not in (3, 4):
            _fail(where, f"each tag must be [tag, size, centres(, step_sizes)], "
                         f"got {e!r}")
        tag, size, rows = e[:3]
        if not valid_tag(tag):
            _fail(where, f"bad tag {tag!r}")
        if tag.lower() in seen:
            _fail(where, f"tag {tag!r} appears twice (tags are case-insensitive)")
        seen.add(tag.lower())
        if not _is_seq(rows) or not rows:
            _fail(where, f"tag {tag!r}: 'centres' must be a non-empty list")
        labels = []
        for r in rows:
            if not _is_seq(r) or len(r) not in (3, 4):
                _fail(where, f"tag {tag!r}: each centre must be [label, x, y(, z)], "
                             f"got {r!r}")
            if r[0] is not None and (isinstance(r[0], bool) or not isinstance(r[0], int)):
                _fail(where, f"tag {tag!r}: centre label must be an integer or "
                             f"null, got {r[0]!r}")
            if not all(_is_num(c) for c in r[1:]):
                _fail(where, f"tag {tag!r}: centre coordinates must be numbers, "
                             f"got {r!r}")
            if ndim is not None and len(r) - 1 != ndim:
                _fail(where, "every centre needs the same number of coordinates")
            ndim = len(r) - 1
            labels.append(r[0])
        if None in labels and len(labels) != 1:
            _fail(where, f"tag {tag!r}: a box that stays put has exactly one centre")
        if None not in labels and len(set(labels)) != len(labels):
            _fail(where, f"tag {tag!r}: a timestep label appears in more than one centre")
        _val_size(size, where)
        if len(size) != ndim:
            _fail(where, f"tag {tag!r}: 'size' has {len(size)} entries for "
                         f"{ndim}-D centres")
        if len(e) == 4:
            _val_step_sizes(e[3], set(labels), ndim, where)


def _val_region_per_step(s, where):
    if s["ranges"]:
        _fail(where, "a region has either 'ranges' or 'centers', not both")
    if not _STEP_KEYS["region"] | _PER_STEP_KEYS <= set(s) \
            <= _STEP_KEYS["region"] | _PER_STEP_KEYS | _PER_STEP_EXTRA:
        _fail(where, "a per-timestep region needs centers, size, track and track_sha")
    centers, size, track = s["centers"], s["size"], s["track"]
    _val_track(s, where)
    if "tag_sizes" in s:
        # Only an unread track can still be waiting on its tags' sizes.
        if centers or track is None or size or "step_sizes" in s:
            _fail(where, "'tag_sizes' belongs to a track file not yet read "
                         "(no centres, no shared size)")
        _val_tag_sizes(s["tag_sizes"], where)
        return
    if not centers and track is not None and "step_sizes" not in s:
        # A track file not yet read: the size may be left to its rows.
        _val_size(size, where, allow_empty=True)
        return
    if not _is_seq(centers) or (not centers and track is None):
        _fail(where, f"'centers' must be a list of [label, x, y(, z)] rows, got {centers!r}")
    ndim = None
    for r in centers:
        if not _is_seq(r) or len(r) not in (3, 4):
            _fail(where, f"each centre must be [label, x, y(, z)], got {r!r}")
        if r[0] is None and len(centers) == 1:
            pass                            # one box that stays put
        elif isinstance(r[0], bool) or not isinstance(r[0], int):
            _fail(where, f"centre label must be an integer, got {r[0]!r}")
        if not all(_is_num(c) for c in r[1:]):
            _fail(where, f"centre coordinates must be numbers, got {r!r}")
        if ndim is not None and len(r) - 1 != ndim:
            _fail(where, "every centre needs the same number of coordinates")
        ndim = len(r) - 1
    if len({r[0] for r in centers}) != len(centers):
        _fail(where, "a timestep label appears in more than one centre")
    _val_size(size, where)
    if ndim is not None and len(size) != ndim:
        _fail(where, f"'size' has {len(size)} entries for {ndim}-D centres")
    if "step_sizes" in s:
        _val_step_sizes(s["step_sizes"], {r[0] for r in centers}, ndim, where)


def _val_region(s, where):
    if "tags" in s:
        return _val_region_tagged(s, where)
    if set(s) & _OPTIONAL_KEYS["region"]:
        return _val_region_per_step(s, where)
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


_SUBSAMPLE_METHODS = ("stride", "mean", "min", "max")


def _val_subsample(s, where):
    uniform, per_axis = s["uniform"], s["per_axis"]
    method = s["method"]
    if method not in _SUBSAMPLE_METHODS:
        _fail(where, f"'method' must be one of {_SUBSAMPLE_METHODS}, got {method!r}")
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
        allowed = _STEP_KEYS[kind] | _OPTIONAL_KEYS.get(kind, frozenset())
        if not _STEP_KEYS[kind] <= set(step) <= allowed:
            missing = sorted(_STEP_KEYS[kind] - set(step))
            extra = sorted(set(step) - allowed)
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


def _build_step_sizes(rows):
    return tuple((lab, tuple(sz)) for lab, sz in rows)


def _build_region(s, up):
    if "tags" in s:
        return RegionNode(upstream=up, tags=tuple(
            (e[0], tuple(e[1]), tuple(tuple(r) for r in e[2]),
             _build_step_sizes(e[3] if len(e) == 4 else ())) for e in s["tags"]),
            track=s["track"], track_sha=s["track_sha"])
    if "centers" in s:
        return RegionNode(upstream=up, centers=tuple(tuple(r) for r in s["centers"]),
                          size=tuple(s["size"]),
                          step_sizes=_build_step_sizes(s.get("step_sizes") or ()),
                          track=s["track"],
                          track_sha=s["track_sha"],
                          tag_sizes=tuple((t, tuple(sz))
                                          for t, sz in s.get("tag_sizes") or ()))
    return RegionNode(upstream=up, ranges=tuple(tuple(r) for r in s["ranges"]))


def _build_subsample(s, up):
    return SubsampleNode(upstream=up, uniform=s["uniform"],
                         per_axis=tuple(tuple(p) for p in s["per_axis"]),
                         method=s["method"])


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


def describe_plan(plan):
    """One line describing a plan, rendered from the WIRE DICT rather than from
    AST nodes, so a provenance reader can regenerate it with no interpreter
    state in scope.

    Distinct from `planner._describe_ast`, which serves the run trace and is
    lossy for this purpose: it shortens the source to a basename and renders a
    sink as the bare word `save`. Here the source and the destination are the
    two things a reader most needs."""
    if not plan:
        return None
    parts = []
    for step in plan.get("chain") or []:
        kind = step.get("kind")
        if kind == "source":
            parts.append(f"source({step.get('uri')})")
        elif kind == "fields":
            parts.append("fields[" + ",".join(step.get("keep") or []) + "]")
        elif kind == "region" and step.get("tags") is not None:
            src = f",track={step['track']}" if step.get("track") else ""
            names = ",".join(str(e[0]) for e in step["tags"])
            sizes = {json.dumps(e[1]) for e in step["tags"]}
            size = step["tags"][0][1] if len(sizes) == 1 else "per tag"
            parts.append(f"region{{tags={names},size={size}{src}}}")
        elif kind == "region" and step.get("centers") is not None:
            src = f",track={step['track']}" if step.get("track") else ""
            parts.append(f"region{{center×{len(step['centers'])},"
                         f"size={step.get('size')}{src}}}")
        elif kind == "region":
            parts.append("region{" + ",".join(
                f"{a}:({lo},{hi})" for a, lo, hi in step.get("ranges") or []) + "}")
        elif kind == "subsample":
            per = step.get("per_axis") or []
            method = step.get("method") or "stride"
            suffix = f",method={method}" if method != "stride" else ""
            parts.append(f"subsample({step.get('uniform')}{suffix})" if not per else
                         "subsample{" + ",".join(f"{a}:{f}" for a, f in per)
                         + "}" + (f"[method={method}]" if method != "stride" else ""))
        elif kind == "threshold":
            parts.append(f"threshold({step.get('var')}"
                         f"{step.get('op')}{step.get('value')})")
        elif kind == "timesteps":
            parts.append(f"timesteps({step.get('start')},{step.get('stop')})")
        elif kind == "compress":
            parts.append("compress[" + ",".join(step.get("variables") or [])
                         + f"]@{step.get('error_bound')}")
        elif kind == "save":
            parts.append(f"save({step.get('path')})")
        elif kind == "render":
            parts.append(f"render(cmap={step.get('cmap')})")
        else:
            parts.append(str(kind))
    return " → ".join(parts)
