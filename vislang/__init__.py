"""VisLang — a declarative DSL for reading, narrowing, and rendering scientific data.

A spec is built from *forms*; a form builds an AST node and runs nothing. Only a
sink (`render` or `save`) triggers execution, at which point the interpreter
inspects the source, static-checks the request against the real schema, fuses the
structural narrowing into a single read, and materializes.

The subpackages follow that pipeline:

    dsl/          forms and the AST they build, plus its JSON wire format
    formats/      the format boundary: readers, schemas, adapters, bindings
    interpreter/  plan, check, fuse, load, narrow, compress, estimate
    output/       the sinks — render to a browser, save to disk
    remote/       push the narrowing next to the data over ssh
    runtime/      caches, tracing, timing, and the spec sandbox
    server/       the two front doors: the MCP server and the `sieve` CLI

Nothing downstream of `formats/` knows what a file format is.
"""

__version__ = "0.1.0"
