"""Sandbox execution verification (sandbox.execute via pydantic-monty).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_sandbox.py

Covers: valid chained build with a real host upstream chain, kwargs forms
across the boundary, dry-run binding recovery + leaf_nodes, form-validation
errors surfacing as SandboxError with the form's own text and a line number,
syntax errors, security blocks (os/open/subprocess/__import__), handle
forgery, and reset_sinks isolation.

Uses only pydantic_monty + dsl_forms (both pure-python, no numpy/h5py), so it
runs without the heavy MCP/render dependencies installed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import execute, SandboxError, NodeHandle, _from_handles
from dsl_forms import form_namespace, reset_sinks, collected_sinks, leaf_nodes
from dsl_forms.nodes import (
    Node, SourceNode, FieldsNode, RegionNode, SubsampleNode, RenderNode,
)

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def run(code):
    """reset_sinks() then execute a spec, returning the bindings dict."""
    reset_sinks()
    return execute(code, form_namespace())


# ---------------------------------------------------------------------------
def test_valid_build():
    print("== valid chained build ==")
    spec = ("render(subsample(fields(source('data.h5'), "
            "['baryon_density']), 4), cmap='viridis')")
    run(spec)
    sinks = collected_sinks()
    check("one sink registered", len(sinks) == 1, f"{len(sinks)}")
    r = sinks[0]
    check("sink is RenderNode", isinstance(r, RenderNode), type(r).__name__)
    check("render cmap", r.cmap == "viridis", repr(r.cmap))

    # The .upstream chain must be real host Nodes so the planner can walk it.
    sub = r.upstream
    check("upstream is host SubsampleNode",
          isinstance(sub, SubsampleNode), type(sub).__name__)
    check("subsample uniform factor", sub.uniform == 4, repr(sub.uniform))
    fld = sub.upstream
    check("next is FieldsNode", isinstance(fld, FieldsNode), type(fld).__name__)
    check("fields keep", fld.keep == ("baryon_density",), repr(fld.keep))
    src = fld.upstream
    check("root is SourceNode", isinstance(src, SourceNode), type(src).__name__)
    check("source uri", src.uri == "data.h5", repr(src.uri))
    # every link is a genuine Node (not a NodeHandle leaking through)
    check("chain nodes are host Nodes",
          all(isinstance(n, Node) for n in (r, sub, fld, src)))


def test_kwargs_forms():
    print("== kwargs forms across the boundary ==")
    spec = (
        "d = source('data.h5')\n"
        "r = region(d, x=(1, 2), y=(3, 4))\n"
        "s = subsample(d, x=5, y=5)\n"
    )
    b = run(spec)
    r = b["r"]
    check("region node", isinstance(r, RegionNode), type(r).__name__)
    check("region ranges",
          r.ranges == (("x", 1, 2), ("y", 3, 4)), repr(r.ranges))
    s = b["s"]
    check("subsample node", isinstance(s, SubsampleNode), type(s).__name__)
    check("subsample per_axis",
          s.per_axis == (("x", 5), ("y", 5)), repr(s.per_axis))
    check("subsample no uniform", s.uniform is None, repr(s.uniform))


def test_dry_run_recovery():
    print("== dry-run binding recovery ==")
    spec = (
        "d = source('data.h5')\n"
        "narrowed = subsample(d, 2)\n"   # consumes d; narrowed is the tail
    )
    b = run(spec)
    check("no sinks (dry run)", collected_sinks() == [])
    check("bindings has both names", set(b) == {"d", "narrowed"}, repr(set(b)))
    leaves = leaf_nodes(b)
    check("one leaf", len(leaves) == 1, f"{len(leaves)}")
    check("leaf is the tail (narrowed)",
          leaves[0] is b["narrowed"], type(leaves[0]).__name__)
    check("consumed intermediate d excluded from leaves",
          b["d"] not in leaves)


def test_form_validation_error():
    print("== form validation error surfaces ==")
    try:
        run("bad = source(123)")
        check("source(123) raises", False, "no error raised")
    except SandboxError as e:
        msg = str(e)
        check("source error text present",
              "source() needs a path/URI string" in msg, msg)
        check("source error has line number", "line" in msg, msg)

    try:
        run("d = source('data.h5')\nt = threshold(d, 'no operator')")
        check("threshold bad predicate raises", False, "no error raised")
    except SandboxError as e:
        msg = str(e)
        check("threshold error text present",
              "no comparison operator" in msg, msg)
        check("threshold error has line number", "line" in msg, msg)


def test_syntax_error():
    print("== syntax error ==")
    try:
        run("d = source('data.h5'\nx = = 5")
        check("malformed spec raises SyntaxError", False, "no error raised")
    except SyntaxError:
        check("malformed spec raises SyntaxError", True)
    except SandboxError as e:
        check("malformed spec raises SyntaxError", False,
              f"got SandboxError instead: {e}")


def test_security_blocks(tmpdir):
    print("== security blocks ==")
    marker_os = os.path.join(tmpdir, "os_system_marker")
    marker_open = os.path.join(tmpdir, "open_marker")
    cases = {
        "import os / os.system": f"import os\nos.system('touch {marker_os}')",
        "open() for write": f"open({marker_open!r}, 'w')",
        "import subprocess": "import subprocess\nsubprocess.run(['echo', 'hi'])",
        "__import__('os')": "__import__('os').system('echo hi')",
    }
    for label, spec in cases.items():
        try:
            run(spec)
            check(f"blocked: {label}", False, "spec ran without error")
        except SandboxError:
            check(f"blocked: {label}", True)
        except SyntaxError as e:
            check(f"blocked: {label}", False, f"got SyntaxError: {e}")
    check("os.system created no file", not os.path.exists(marker_os))
    check("open created no file", not os.path.exists(marker_open))


def test_handle_forgery():
    print("== handle forgery ==")
    try:
        _from_handles(NodeHandle(999999), {})
        check("forged handle raises", False, "no error raised")
    except SandboxError as e:
        check("forged handle raises", "unknown node id" in str(e), str(e))


def test_reset_isolation():
    print("== reset_sinks isolation ==")
    run("render(source('a.h5'))")
    check("first spec: one sink", len(collected_sinks()) == 1)
    run("render(source('b.h5'))")   # run() calls reset_sinks() first
    check("second spec: still one sink (no leak)",
          len(collected_sinks()) == 1, f"{len(collected_sinks())}")


def main():
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="vislang_sandbox_test_")
    test_valid_build()
    test_kwargs_forms()
    test_dry_run_recovery()
    test_form_validation_error()
    test_syntax_error()
    test_security_blocks(tmpdir)
    test_handle_forgery()
    test_reset_isolation()
    print(f"\nALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
