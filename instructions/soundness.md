# Soundness Principle

The governing rule for any LLM use in VisLang:

> **The LLM proposes an artifact (a declarative spec or trusted-library glue); a
> deterministic, hand-written check verifies it against ground truth before it
> is trusted; verified artifacts are frozen so the runtime path has no LLM.**

This is a deliberate stance: lean toward formal soundness, away from "the LLM
might do it right or might not."

## What the LLM may and may not do
- **May**: identify a file's format, wire up an already-installed trusted reader
  library, or propose a *declarative* binding (data, not code).
- **May not**: read the data bytes itself, hand-parse a byte layout, or have its
  output trusted without a check.

## Readers may be generated; writers may not

The trust ladder is **asymmetric on purpose: writers are Tier-0 only.**

Reading has a clean oracle — whether library L parses file F is a fact about F's
bytes, and `conform_and_freeze` settles it against the real file. Writing has no
equivalent. A generated writer could only be trusted through a round-trip check
(write, read back, compare every array and every piece of metadata), which is a
separate and much larger gate than the one we have. So the Tier-1 contract is
exactly `inspect` + `read_array` — there is no writer function to generate.

The consequence is a one-way door, and it is the right one: a format read
through a verified adapter can be converted **out of** freely, because once
`read_array` has passed its gate the data is plain numpy behind `DatasetInfo`
and every layer above is format-blind. It can never be converted **into**.

## Two kinds of determinism
1. **Runtime determinism** — the *freeze* step. After the first verified
   generation, the path is pure frozen code forever; no LLM at runtime.
2. **Acceptance determinism** — the *verification gate*. Hand-written, never
   LLM-generated, and it uses the file's own metadata as the oracle (e.g. a raw
   volume's expected byte count from its declared shape × dtype).

## The one irreducible behavioral run
Whether library L actually reads file F is a fact about F's bytes, not about the
code — so one behavioral check against the real file is unavoidable. What we
*remove* is the blind retry loop: constrain LLM output to a schema/spec and
statically check everything checkable (the library imports, the expression
parses, the schema conforms) before the single behavioral confirmation.

This is why the byte-layout-AST idea was abandoned (trusted libraries already
read production formats). The compile-time-verification idea found its home in the
**query DSL**, which is now built: the interpreter (`planner.py`) static-checks
each spec against the `DatasetInfo` schema *before any read* — a missing
axis/variable or an out-of-bounds region raises with zero bulk data touched. See
`vislang://instructions/roadmap`.
