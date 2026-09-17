"""Materialize a dataset under a Narrowing.

Two entry points share one read core:
  - `load(info)` — the legacy/declarative path. Builds a Narrowing from whatever
    `subset()` recorded in `info.selected_dimensions` and reads it. Byte-identical
    to the pre-Narrowing loader.
  - `materialize(info, narrowing)` — the interpreter's path. Reads under a
    planner-built Narrowing: the structural cuts (projection, grid crop+stride,
    particle subsample) are pushed into the read; the computed cuts arrive as
    `narrowing.post_ops` and are applied AFTER the read in the exact order the
    spec wrote them (RowMask / RowSample / VoxelMask).

The read-set is `project` (the output variables) plus whatever the post-read
ops need (threshold vars, bbox position vars) — mask-only extras are read, used,
then dropped (positions are kept: render needs them). The per-format work lives
in adapters.py; this module copies metadata and routes to the adapter.
"""

import copy

import numpy as np

from vislang.formats.adapters import get_adapter_for_info, _resolve_variables, build_selection_info
from vislang.interpreter.narrowing import (narrowing_from_dimensions, build_row_mask, row_mask_from,
                       voxel_mask_from, post_op_read_vars,
                       RowMask, RowSample, VoxelMask)


def load(dataset_info):
    """Materialize exactly what `info` describes, narrowed by the legacy
    `selected_dimensions` policy `subset()` recorded."""
    narrowing = narrowing_from_dimensions(
        getattr(dataset_info, 'selected_dimensions', None),
        dataset_info.dimensions.get('particles', 0),
        getattr(dataset_info, 'positions', None))
    return materialize(dataset_info, narrowing)


def materialize(dataset_info, narrowing):
    """Read `dataset_info` under a (planner-built) Narrowing."""
    loaded = copy.deepcopy(dataset_info)
    adapter = get_adapter_for_info(loaded)

    # Output variables: the planner's projection, else everything listed.
    if getattr(narrowing, 'project', None) is not None:
        variables = _resolve_variables(loaded, list(narrowing.project))
    else:
        variables = _resolve_variables(loaded, None)
    locations = getattr(loaded, 'variable_locations', None) or {}
    positions = getattr(loaded, 'positions', None) or ()

    # Read-set = output ∪ mask needs. A bbox/threshold may test a variable the
    # user didn't keep — read it too, then drop it after masking.
    needed = _mask_only_vars(narrowing, loaded)
    needed |= post_op_read_vars(getattr(narrowing, 'post_ops', ()), positions)
    extra = sorted(needed - set(variables))
    missing = [v for v in extra if v not in loaded.variables]
    if missing:
        raise ValueError(f"threshold/region needs variable(s) {missing} "
                         f"not present in this dataset")
    read_vars = variables + extra

    read_all = getattr(adapter, 'read_all', None)
    if read_all is not None:
        wanted = {var: locations.get(var, var) for var in read_vars}
        for var, arr in read_all(loaded.filepath, wanted, narrowing).items():
            loaded.data[var] = np.asarray(arr)
    else:
        for var in read_vars:
            location = locations.get(var, var)
            loaded.data[var] = np.asarray(
                adapter.read_array(loaded.filepath, location, narrowing))

    # Computed cuts, in the order the spec wrote them.
    post_ops = getattr(narrowing, 'post_ops', ())
    if post_ops:
        _apply_post_ops(loaded.data, post_ops, positions)
    elif narrowing.has_row_mask:
        # Legacy row mask (bbox + value predicates set directly on the Narrowing).
        mask = build_row_mask(narrowing, loaded.data, positions or None)
        if mask is not None:
            _take_rows(loaded.data, mask)

    for v in extra:                       # drop mask-only vars, but keep coords (render needs them)
        if v not in positions:
            loaded.data.pop(v, None)

    loaded.loaded = True
    loaded.variables = variables
    loaded.selection_info = build_selection_info(loaded, variables, narrowing)
    return loaded


# ---------------------------------------------------------------------------
# Post-read op application (written-order faithful)
# ---------------------------------------------------------------------------
def _apply_post_ops(data, post_ops, positions):
    for op in post_ops:
        if isinstance(op, RowMask):
            mask = row_mask_from(op.bbox, op.predicates, data, positions or None)
            if mask is not None:
                _take_rows(data, mask)
        elif isinstance(op, RowSample):
            n = _row_count(data)
            if isinstance(op.factor, int):
                index = slice(None, None, op.factor)
            else:
                k = max(1, int(round(n * op.factor)))
                index = np.sort(np.random.choice(n, size=k, replace=False))
            for var in list(data):
                arr = data[var]
                if getattr(arr, 'ndim', 0) in (1, 2) and arr.shape[0] == n:
                    data[var] = arr[index]
        elif isinstance(op, VoxelMask):
            mask = voxel_mask_from(op.predicates, data)
            if mask is None:
                continue
            for var in list(data):
                arr = data[var]
                if getattr(arr, 'shape', None) == mask.shape:
                    if not np.issubdtype(arr.dtype, np.floating):
                        arr = arr.astype(np.float32)   # ints can't hold NaN
                    arr[~mask] = np.nan
                    data[var] = arr
        else:
            raise ValueError(f"unknown post-read op {op!r}")


def _take_rows(data, mask):
    """Keep the rows where `mask` holds, on every row-aligned array."""
    for var in list(data):
        arr = data[var]
        if getattr(arr, 'ndim', 0) >= 1 and arr.shape and arr.shape[0] == mask.shape[0]:
            data[var] = arr[mask]


def _row_count(data):
    """The current particle/table row count: the most common leading dimension
    among the 1-D/2-D arrays (grid cubes are 3-D and don't participate)."""
    from collections import Counter
    counts = Counter(arr.shape[0] for arr in data.values()
                     if getattr(arr, 'ndim', 0) in (1, 2) and arr.shape)
    if not counts:
        raise ValueError("subsample after a threshold/region mask needs row "
                         "(particle/table) data")
    return counts.most_common(1)[0][0]


def _mask_only_vars(narrowing, info):
    """Variables the legacy row mask needs to read (positions for a bbox,
    predicate vars). The ordered post_ops layer resolves its own needs via
    narrowing.post_op_read_vars."""
    needed = set()
    if narrowing.bbox is not None and getattr(info, 'positions', None):
        needed.update(info.positions)
    needed.update(p.var for p in narrowing.predicates)
    return needed
