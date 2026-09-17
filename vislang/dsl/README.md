# `dsl/` — the forms and the AST they build

A form runs nothing. It builds a node and returns it, so a spec is a value, not
a script. Only a sink (`save`, `render`) causes work.

| File | What it holds |
|---|---|
| `forms.py` | the nine form constructors a spec may call |
| `nodes.py` | the node types, plus the per-run sink registry |
| `ast_serialize.py` | AST ⇄ JSON, with the strict validator used on the wire |
| `__init__.py` | `form_namespace()` — the dict injected into a spec's exec namespace |

`ast_serialize.py` exists for one reason: a pipeline that travels to a remote
host travels as **data**. Rebuilding it there goes through an allowlist, so code
that crossed a network is never executed. Adding a form means adding it to that
allowlist too, or it will be rejected remotely.

Reference: [`instructions/dsl-reference.md`](../../instructions/dsl-reference.md)
· authoring guidance: [`instructions/authoring-specs.md`](../../instructions/authoring-specs.md)
