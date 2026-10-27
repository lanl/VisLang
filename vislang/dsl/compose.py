"""Specs written for a front end — the same small DSL a person writes.

A graphical client (the ParaView plugin) never builds an AST and never decides
what a request means. It sends what the user set — a source, fields, a stride, a
value filter, a box dragged in a 3-D view — and this module writes the spec a
person would have written for it, one concern per line. The spec is then run
like any other: checked, priced, gated, cached, recorded.

    compose_spec(descriptor, params)    params -> spec text
    view_box_to_region(descriptor, box) a box in view coordinates -> region()
    check_spec(text)                    parse a spec (sandboxed) without running it

`descriptor` is what `api.inspect` reports for the source: its modality, grid,
geometry, timesteps and the coordinates its region takes. Pure functions: no
I/O, no data read.
"""

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

_AXES = ("x", "y", "z")

# Significant digits a world bound keeps in a spec: enough that rounding moves
# a bound far less than any grid spacing, few enough that the spec is readable.
_DIGITS = 6


def compose_spec(descriptor, params):
    """The spec text for one request.

    params:
        output      absolute path of the file (or series folder) to write
        positions   (x, y, z) variable names, when the user named them
        fields      variable names to keep, or None for all
        timestep    a timestep label (series only) — one timestep per request
        timesteps   (start, stop), inclusive — a range, for prefetching
        box         {"lo": [x, y, z], "hi": [x, y, z]} in view coordinates
        stride      keep every n-th cell / point (int >= 1)
        threshold   "var op value", or None
    Order is fixed and meaningful: fields, region, threshold, subsample — the
    value filter keeps what passes, then the stride thins what was kept.
    """
    uri = descriptor["uri"]
    positions = params.get("positions")
    lines = [f"src = source({uri!r}"
             + (f", positions={tuple(positions)!r}" if positions else "") + ")"]
    cur = "src"

    def step(expr):
        nonlocal cur
        lines.append(f"view = {expr}")
        cur = "view"

    if descriptor.get("timesteps") is not None:
        if params.get("timestep") is not None:
            t = int(params["timestep"])
            step(f"timesteps({cur}, {t}, {t})")
        elif params.get("timesteps") is not None:
            a, b = (int(v) for v in params["timesteps"])
            step(f"timesteps({cur}, {a}, {b})")
    if params.get("fields"):
        # Points are placed by their positions: a narrowed field list still
        # keeps them (a points file cannot be written, nor a box applied,
        # without them). Clients list only the other variables.
        keep = list(params["fields"])
        pos = list(positions or descriptor.get("positions") or []) \
            if descriptor.get("modality") == "particles" else []
        keep = [p for p in pos if p not in keep] + keep
        step(f"fields({cur}, {keep!r})")
    if params.get("box"):
        axes, coords = view_box_to_region(descriptor, params["box"])
        if axes:
            args = ", ".join(f"{a}=({_lit(lo)}, {_lit(hi)})" for a, (lo, hi) in axes)
            step(f"region({cur}, {args}, coords={coords!r})")
    if params.get("threshold"):
        step(f"threshold({cur}, {str(params['threshold']).strip()!r})")
    stride = params.get("stride")
    if stride is not None and int(stride) > 1:
        step(f"subsample({cur}, {int(stride)})")
    lines.append(f"save({cur}, {params['output']!r})")
    return "\n".join(lines) + "\n"


def view_box_to_region(descriptor, box):
    """A box in view coordinates (what ParaView's axes show) as region() axes.

    Returns ([(axis, (lo, hi)), …], coords): only the axes the box narrows.
      - points, and grids that state where they sit: world coordinates, the
        closed box rounded outward to a readable precision (never inward:
        rounding must not drop a sample the box holds);
      - grids that do not (and cuts placed among their source's cells): cells,
        half-open and whole — the samples at positions inside [lo, hi] are the
        cells ceil(lo) .. floor(hi), i.e. [ceil(lo), floor(hi) + 1) — clipped
        to the cells the file holds.
    Raises ValueError for a box that holds nothing."""
    lo, hi = list(box["lo"]), list(box["hi"])
    ndim = 2 if descriptor.get("grid") and len(descriptor["grid"]) == 2 else 3
    bounds = descriptor.get("bounds")             # [[lo, hi]] per axis, if known
    coords = descriptor["coords"]
    out = []
    for a in range(ndim):
        l, h = float(min(lo[a], hi[a])), float(max(lo[a], hi[a]))
        if coords == "cells":
            first, last = bounds[a]                # the cells this file holds
            start = max(math.ceil(l - 1e-9), int(first))
            stop = min(math.floor(h + 1e-9) + 1, int(last) + 1)
            if start >= stop:
                raise ValueError(f"the box holds no cell on {_AXES[a]}: "
                                 f"({l:g}, {h:g}) vs cells {first:g}..{last:g}")
            if (start, stop) != (int(first), int(last) + 1):
                out.append((_AXES[a], (start, stop)))
        else:
            if bounds is not None:
                if h < bounds[a][0] or l > bounds[a][1]:
                    raise ValueError(f"the box misses the data on {_AXES[a]}: "
                                     f"({l:g}, {h:g}) vs {bounds[a][0]:g}.."
                                     f"{bounds[a][1]:g}")
                if l <= bounds[a][0] and h >= bounds[a][1]:
                    continue                       # the whole axis: nothing to cut
            out.append((_AXES[a], (_round(l, ROUND_FLOOR), _round(h, ROUND_CEILING))))
    return out, coords


def check_spec(text):
    """Parse a spec in the sandbox — building forms, reading nothing — and
    return {"ok": True, "plans": [plan, …]} (one per sink, or per dangling
    leaf when there is none) or {"ok": False, "error": message}."""
    from vislang.dsl import form_namespace, reset_sinks, collected_sinks, leaf_nodes
    from vislang.dsl.ast_serialize import to_plan
    from vislang.runtime.sandbox import execute, SandboxError
    reset_sinks()
    try:
        ctx = execute(text, form_namespace())
        targets = collected_sinks() or leaf_nodes(ctx)
        plans = [to_plan(t) for t in targets]
    except (SandboxError, SyntaxError, ValueError, TypeError) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        reset_sinks()
    return {"ok": True, "plans": plans}


def _round(v, how):
    """`v` to _DIGITS significant digits, rounded the way `how` says."""
    if v == 0 or not math.isfinite(v):
        return v
    d = Decimal(repr(v))
    q = Decimal(1).scaleb(d.adjusted() - (_DIGITS - 1))
    return float(d.quantize(q, rounding=how))


def _lit(v):
    """A bound as it reads in a spec: a cell as an int, a world bound as a float."""
    return str(v) if isinstance(v, int) else repr(float(v))
