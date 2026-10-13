"""explain.py — show the interpreter's work.

For each pipeline in a spec, prints three things side by side:
  1. the INPUT AST — the node chain the forms built (what you wrote);
  2. the interpreter's DECISIONS — the dry-run plan (which cuts push into the
     read, which become post-read ops, the written-order gating);
  3. the LOWERED NARROWING — the plan object the interpreter emits.

It reads NO bulk data (metadata inspect + static check + lowering only),
renders nothing, writes nothing — safe to run on any spec.

    python explain.py [spec.py]          # defaults to spec.py
    python explain.py --json [spec.py]   # input AST as plan.json too
"""

import dataclasses
import sys

from vislang.dsl import (form_namespace, reset_sinks, collected_sinks,
                       leaf_nodes, upstream_of)
from vislang.interpreter.planner import plan_pipeline, format_result


def _chain(terminal):
    nodes, n = [], terminal
    while n is not None:
        nodes.append(n)
        n = upstream_of(n)
    nodes.reverse()
    return nodes


def show_ast(terminal):
    for depth, node in enumerate(_chain(terminal)):
        fields = {f.name: getattr(node, f.name)
                  for f in dataclasses.fields(node) if f.name != "upstream"}
        args = ", ".join(f"{k}={v!r}" for k, v in fields.items())
        arrow = "" if depth == 0 else "-> "
        print("    " + "  " * depth + f"{arrow}{node.kind}({args})")


def show_narrowing(n):
    if n is None:
        print("    (remote source — the Narrowing is built on the remote, not here)")
        return
    proj = list(n.project) if n.project is not None else "None  (all variables)"
    gr = ([(r.start, r.stop, r.step) for r in n.grid_ranges]
          if n.grid_ranges else None)
    print(f"    project        : {proj}")
    print(f"    grid_ranges    : {gr}        # crop+stride pushed INTO the read")
    print(f"    particle_index : {n.particle_index!r}   # row pushdown")
    if n.post_ops:
        print("    post_ops       : (applied AFTER the read, in this order)")
        for i, op in enumerate(n.post_ops):
            print(f"        [{i}] {op}")
    else:
        print("    post_ops       : []   (no computed cuts)")


def main():
    argv = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    path = argv[0] if argv else "spec.py"

    with open(path) as f:
        src = f.read()
    reset_sinks()
    ctx = form_namespace()
    exec(compile(src, path, "exec"), ctx)          # builds nodes; no read, no render
    targets = collected_sinks() or leaf_nodes(ctx)
    if not targets:
        print(f"no pipeline found in {path}")
        return

    for i, t in enumerate(targets, 1):
        print(f"\n{'=' * 70}\n{path}  —  pipeline {i}/{len(targets)}  (sink: {t.kind})\n{'=' * 70}")
        print("\n-- INPUT AST (what the spec built) --")
        show_ast(t)
        if as_json:
            import json
            from vislang.dsl.ast_serialize import to_plan
            print("\n-- INPUT AST as plan.json --")
            print("    " + json.dumps(to_plan(t)))

        print("\n-- INTERPRETER DECISIONS (dry run — metadata only) --")
        result = plan_pipeline(t, dry_run=True)
        print(format_result(result))

        print("\n-- LOWERED NARROWING (the plan the interpreter writes) --")
        show_narrowing(result.get("narrowing"))


if __name__ == "__main__":
    main()
