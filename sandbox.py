"""Sandboxed execution of spec (DSL) code via pydantic-monty.

VisLang executes agent-written "spec" code (a Python-syntax DSL) to build a
pipeline (``mcp_server.run_pipeline``). Spec code is *untrusted*: a
prompt-injected agent can submit malicious code through the same channel a
cooperative one uses. Historically ``run_pipeline`` ran the spec with a bare
``exec(compile(...))`` -- full host access, the vulnerability this module
closes. Instead we run the spec inside Monty and return the final top-level
variable bindings (name -> value), with pipeline nodes surfaced as
:class:`NodeHandle` so the caller's dry-run leaf scan still works. The single
entry point is::

    execute(code, namespace) -> dict[str, value]

where *namespace* is the dict from ``dsl_forms.form_namespace()`` (the 8 form
constructors keyed by name).

Security model
--------------

`pydantic-monty <https://pypi.org/project/pydantic-monty/>`_ is a Python
interpreter written in Rust with a restricted builtin/stdlib surface and
*enforceable resource limits*. Within the boundary this module enforces:

- **No host escape.** Imports are limited to Monty's own restricted modules.
  Verified against monty 0.0.18: ``import os`` yields a stub with no
  ``os.system`` / ``os.getcwd`` / ``os.environ`` (attribute access raises);
  ``open(...)`` is denied (``PermissionError``); ``import subprocess`` fails
  (``ModuleNotFoundError``); ``__import__`` is unavailable (``NameError``).
  All of these surface as :class:`SandboxError`.
- **Bounded cost.** Wall-clock, memory, allocations, and recursion depth are
  capped (see the ``MONTY_*`` constants and :func:`_monty_limits`), so a
  runaway or malicious spec is stopped rather than left to burn resources.
- **No trust in inbound data.** Handles that come *back* from the sandbox are
  reconstructed copies whose fields a hostile spec could doctor; the
  marshalling layer (below) never trusts them.

Form binding
------------

The wrapped forms are registered under their REAL names directly in Monty's
``external_functions``, so the spec calls them with no header and no imports --
matching how the forms were injected into the old ``exec`` namespace. This
works because none of the 8 form names (``source``, ``fields``, ``region``,
``subsample``, ``threshold``, ``compress``, ``save``, ``render``) is a Monty
builtin. Monty resolves its own builtins at compile time and that resolution
would shadow an identically-named ``external_functions`` entry; a form whose
name collided with a future Monty builtin would therefore need to be bound
another way (e.g. registered under a non-colliding alias and rebound to its
real name at module scope, which shadows the builtin).

Marshalling model
-----------------

Form constructors return :class:`~dsl_forms.nodes.Node` instances that spec
code passes around. A ``Node`` carries a deep ``upstream`` chain, so a filter
chain is a deeply nested frozen structure. Serializing that whole structure
across the boundary on every crossing is quadratic and can hit Monty's
input-depth cap, and (being value-equal frozen dataclasses) distinct handles
could collapse. So ``Node`` never crosses the boundary. Instead the sandbox
only ever holds :class:`NodeHandle`, an opaque frozen dataclass with a single
``node_id`` field, registered via ``dataclass_registry``:

- **Outbound** (form result -> sandbox): every ``Node`` is recorded in a
  per-execute ``id(node) -> Node`` table and replaced by
  ``NodeHandle(id(node))`` (:func:`_to_handles`). The crossing is O(1)
  regardless of chain depth. Keying by ``id(node)`` (rather than a node field
  it lacks) and storing the node keeps it alive so its id cannot be reused.
- **Inbound** (sandbox -> form call, or a recovered binding): every
  ``NodeHandle`` is re-resolved to its canonical ``Node`` by ``node_id``
  against that table (:func:`_from_handles`). An id absent from the table
  (forged, mutated, or never produced by a form call) raises
  :class:`SandboxError`. The reconstructed handle's fields are never trusted --
  only the id, and only to look up the trusted original.

Both walks recurse into lists/tuples/dicts so nested handles are handled too.

Sinks
-----

``save``/``render`` register themselves as sinks at construction time (via
``Node.__post_init__`` -> ``register_sink``). Because the real host forms run
*inside* the wrappers (:func:`_make_wrapper`), those side effects fire
naturally on the host's registry. The CALLER (``run_pipeline``) owns
``reset_sinks()`` before and ``collected_sinks()`` after -- this module does
not touch the sink registry.

DSL surface
-----------

Spec code is full Python as Monty implements it: f-strings and expressions
work normally. ``math`` is the real (restricted) Monty ``math`` module, made
available by feeding ``import math`` as a separate REPL snippet *before* the
user code, so the user code's line numbers stay pristine. ``print`` is routed
to this module's logger (process stdout is reserved for the MCP protocol).
"""

import ast as _ast
import logging
from dataclasses import dataclass

import pydantic_monty as pm

from dsl_forms.nodes import Node

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeHandle:
    """Opaque handle marshalled across the sandbox boundary in place of a Node.

    A ``Node`` carries its full ``upstream`` chain, so passing it across the
    boundary re-serializes the whole chain on every crossing (and distinct
    frozen nodes can compare equal). ``NodeHandle`` carries only the
    ``node_id`` (the host's ``id(node)``), which the host resolves back to the
    canonical ``Node`` via its per-execute table (see the module docstring's
    marshalling model). It is a frozen :func:`~dataclasses.dataclass` so Monty
    can register it (``dataclass_registry=[NodeHandle]``) and marshal it by
    field data; a copy coming back from the sandbox is never trusted beyond
    its id.
    """

    node_id: int


class SandboxError(Exception):
    """A spec failed at runtime inside the sandbox.

    Carries Monty's own (cleaned) message and line information. Syntax failures
    are raised as ``SyntaxError`` instead.
    """


# --------------------------------------------------------------------------
# Static analysis: names the spec assigns (for binding recovery)
# --------------------------------------------------------------------------

def _walk_target(node, add):
    """Add ``Name`` ids from an assignment target: a plain name, tuple/list
    unpacking (``a, b = ...``), or a starred remainder (``a, *rest = ...``)."""
    if isinstance(node, _ast.Name):
        add(node.id)
    elif isinstance(node, (_ast.Tuple, _ast.List)):
        for elt in node.elts:
            _walk_target(elt, add)
    elif isinstance(node, _ast.Starred):
        _walk_target(node.value, add)


class _AssignedNameCollector(_ast.NodeVisitor):
    """Collect the names bound by ``=`` assignment anywhere in *code*.

    Only the ``Assign`` form is tracked (``a = ...``, ``a, b = ...``); the DSL
    specs this recovers bindings for are flat chains of ``name = form(...)``. A
    collected name that turns out not to be a module global (e.g. one assigned
    only inside a nested ``def``) is harmless -- reading it back in
    :func:`execute` simply raises and is skipped.
    """

    def __init__(self):
        self.names = []
        self._seen = set()

    def visit_Assign(self, node):
        for target in node.targets:
            _walk_target(target, self._add)

    def _add(self, name):
        if name not in self._seen:
            self._seen.add(name)
            self.names.append(name)


def _collect_assigned_names(code):
    """Names bound by ``=`` assignment in *code*, de-duplicated in source order.

    Parsing untrusted code with ``ast.parse`` is safe (no execution)."""
    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return []

    collector = _AssignedNameCollector()
    collector.visit(tree)
    return collector.names


# --------------------------------------------------------------------------
# Marshalling: Node <-> NodeHandle across the boundary
# --------------------------------------------------------------------------

def _to_handles(value, table):
    """Replace outbound ``Node`` instances with ``NodeHandle`` copies.

    Called on form return values (the authoritative originals). Each ``Node``
    is recorded in the per-execute *table* by ``id(node)`` and replaced with an
    opaque ``NodeHandle(id(node))`` -- the only thing the sandbox ever holds.
    Storing the node keeps it alive so its id cannot be reused. Recurses into
    lists/tuples/dicts so nested handles are captured too.
    """
    if isinstance(value, Node):
        table[id(value)] = value
        return NodeHandle(id(value))
    if isinstance(value, list):
        return [_to_handles(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_to_handles(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _to_handles(v, table) for k, v in value.items()}
    return value


def _from_handles(value, table):
    """Resolve inbound ``NodeHandle`` copies to their canonical ``Node``.

    Monty hands a registered dataclass back as a reconstructed *copy* built
    from field data, which a hostile spec could doctor. We trust only the
    ``node_id`` and look up the canonical instance in *table*; an id absent
    from the table (forged, mutated, or never produced by a form call) raises
    :class:`SandboxError`. Recurses into lists/tuples/dicts.
    """
    if isinstance(value, NodeHandle):
        canonical = table.get(value.node_id)
        if canonical is None:
            raise SandboxError(
                f"spec referenced unknown node id {value.node_id}"
            )
        return canonical
    if isinstance(value, list):
        return [_from_handles(v, table) for v in value]
    if isinstance(value, tuple):
        return tuple(_from_handles(v, table) for v in value)
    if isinstance(value, dict):
        return {k: _from_handles(v, table) for k, v in value.items()}
    return value


def _make_wrapper(form, table):
    """Wrap a host form: resolve inbound handles, wrap outbound Nodes.

    Inbound ``NodeHandle`` arguments are re-resolved to their canonical host
    ``Node`` by ``node_id`` (:func:`_from_handles`) before the real form runs
    -- this is where ``register_sink`` side effects fire on the host registry.
    The result's ``Node`` handles are recorded in the per-execute *table* and
    replaced with ``NodeHandle`` copies (:func:`_to_handles`).
    """
    def wrapper(*args, **kwargs):
        args = tuple(_from_handles(a, table) for a in args)
        kwargs = {k: _from_handles(v, table) for k, v in kwargs.items()}
        result = form(*args, **kwargs)
        return _to_handles(result, table)

    return wrapper


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

# Resource limits. A few seconds of wall-clock and a few hundred MB is ample
# for *constructing* a spec (no bulk data is read at construct time); these
# bound a runaway or malicious spec. Exposed as module-level constants so a
# deployment can tune them without editing logic.
MONTY_MAX_DURATION_SECS = 5.0
MONTY_MAX_MEMORY = 256 * 1024 * 1024
MONTY_MAX_ALLOCATIONS = 50_000_000
MONTY_MAX_RECURSION_DEPTH = 200


def _monty_limits():
    """Build a ``pydantic_monty.ResourceLimits`` from the ``MONTY_*`` constants."""
    return pm.ResourceLimits(
        max_duration_secs=MONTY_MAX_DURATION_SECS,
        max_memory=MONTY_MAX_MEMORY,
        max_allocations=MONTY_MAX_ALLOCATIONS,
        max_recursion_depth=MONTY_MAX_RECURSION_DEPTH,
    )


def _make_print():
    """Return a print_callback that buffers fragments and logs whole lines.

    Monty invokes the callback once per print fragment (arguments, separators,
    and the trailing newline). We accumulate until a newline and then log.
    Process stdout is reserved for the MCP stdio protocol, so spec ``print()``
    must never hit real stdout.
    """
    buf = []

    def callback(stream, text):
        if text == "\n":
            logger.info("[spec print] %s", "".join(buf))
            buf.clear()
        else:
            buf.append(text)

    return callback


def execute(code, namespace):
    """Run *code* against *namespace* in Monty; return final bindings.

    *namespace* is the dict from ``dsl_forms.form_namespace()`` (form name ->
    constructor). Returns a dict of top-level variable name -> value with
    pipeline nodes resolved to canonical ``Node`` instances -- shape-equivalent
    to the old post-``exec`` namespace, so the caller's ``leaf_nodes(...)``
    dry-run scan still works.

    The forms are registered under their real names (see the module docstring).
    A syntax error raises ``SyntaxError``; a runtime failure (form validation
    error, blocked import, resource-limit breach, forged handle, ...) raises
    :class:`SandboxError`.
    """
    table = {}  # per-execute id(node) -> Node, populated by the wrappers
    forms = {name: value for name, value in namespace.items() if callable(value)}
    external = {name: _make_wrapper(form, table) for name, form in forms.items()}

    # A MontyRepl keeps namespace state across snippets, which lets us (a) feed
    # `import math` as its own snippet so the user code's line numbers stay
    # pristine, and (b) recover bindings by reading each name back afterwards.
    repl = pm.MontyRepl(
        script_name="spec.py",
        dataclass_registry=[NodeHandle],
        limits=_monty_limits(),
    )

    try:
        repl.feed_run("import math", external_functions=external)
    except pm.MontyError as e:  # pragma: no cover - static setup snippet
        raise SandboxError(f"internal: math setup failed: {_monty_message(e)}") from e

    try:
        # Monty raises syntax errors and runtime errors both from feed_run.
        repl.feed_run(
            code,
            external_functions=external,
            print_callback=_make_print(),
        )
    except pm.MontySyntaxError as e:
        raise SyntaxError(_monty_message(e)) from e
    except pm.MontyError as e:
        raise SandboxError(_monty_message(e)) from e

    # Recover bindings: read each assigned name back from the persistent REPL
    # namespace. Names that are unbound (a branch not taken) or hold
    # non-representable values raise inside feed_run and are skipped. Returned
    # handles are resolved back to canonical Nodes.
    bindings = {}
    for name in _collect_assigned_names(code):
        try:
            value = repl.feed_run(name, external_functions=external)
        except pm.MontyError:
            continue
        bindings[name] = _from_handles(value, table)
    return bindings


def _monty_message(exc):
    """Build a readable message from a Monty error, naming the spec line.

    Monty's ``str(exc)`` already names the underlying error kind (e.g.
    ``NameError: ...``) but omits the line; we append the user-code line from
    the structured traceback. No offset correction is needed: the user code is
    fed as its own REPL snippet, so its reported line numbers are already
    relative to the spec.
    """
    msg = str(exc).strip()
    try:
        frames = exc.traceback()
    except Exception:
        frames = None
    if frames:
        line = getattr(frames[-1], "line", None)
        if isinstance(line, int) and line >= 1:
            return f"{msg} (spec.py line {line})"
    return msg
