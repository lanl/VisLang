"""Materialize a dataset under a Narrowing.

Three entry points share one read core:
  - `load(info)` — the legacy/declarative path. Builds a Narrowing from whatever
    `subset()` recorded in `info.selected_dimensions` and reads it. Byte-identical
    to the pre-Narrowing loader.
  - `materialize(info, narrowing)` — the interpreter's path. Reads under a
    planner-built Narrowing: the structural cuts (projection, grid crop+stride,
    particle subsample) are pushed into the read; the computed cuts arrive as
    `narrowing.post_ops` and are applied AFTER the read in the exact order the
    spec wrote them (RowMask / RowSample / VoxelMask).
  - `materialize_boxes(info, narrowings)` — several boxes of ONE file (named
    boxes). Where the format would read the whole file per box anyway (point
    data, whose box is a post-read mask; a grid reader without strided reads),
    the file is read once and every box is cut from that one read. Each result
    equals `materialize(info, narrowing)` for its own narrowing.

The read-set is `project` (the output variables) plus whatever the post-read
ops need (threshold vars, bbox position vars) — mask-only extras are read, used,
then dropped (positions are kept: render needs them). The per-format work lives
in adapters.py; this module copies metadata and routes to the adapter.
"""

import copy

import numpy as np

from vislang.formats.adapters import (get_adapter_for_info, _resolve_variables,
                                     build_selection_info, adapter_capabilities)
from vislang.formats.dataset_info import cell_placement, narrowed_geometry
from vislang.interpreter.narrowing import (Narrowing, narrowing_from_dimensions, build_row_mask,
                       row_mask_from, voxel_mask_from, post_op_read_vars, sampling_rng,
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
    variables, extra = _read_set(loaded, narrowing)
    loaded.data.update(_read(loaded, variables + extra, narrowing))
    loaded.geometry = _source_geometry(dataset_info)
    return _settle(loaded, narrowing, variables, extra)


def _source_geometry(info):
    """The source's geometry, uncut, with listed point positions read in.

    Inspect only marks a curvilinear grid (its points are bulk data); here the
    adapter reads them whole, and _settle cuts them with the same index ranges
    as the arrays, so data and positions stay aligned. An adapter that cannot
    read them leaves index space — the honest answer, never an invented one."""
    geom = getattr(info, 'geometry', None)
    if not geom:
        # A grid that states no geometry is placed in its own cell indices, so
        # a cut is written where it sits among the source's cells (marked as
        # cells: no physical position is claimed). Only for a grid this side
        # read from the file — a stand-in for a remote source keeps the
        # geometry the remote returned with the data.
        grid = (getattr(info, 'dimensions', None) or {}).get('grid')
        if grid is not None and not getattr(info, 'positions', None) \
                and getattr(info, 'geometry_known', True):
            return cell_placement(len(grid))
        return geom
    if geom.get('kind') != 'structured' or geom.get('points') is not None:
        return geom
    reader = getattr(get_adapter_for_info(info), 'read_geometry', None)
    return reader(info.filepath) if reader is not None else None


def shares_read(dataset_info):
    """True when several boxes of this file are best cut from ONE read: point
    data (a box is a post-read mask over rows the read returns whole) and grids
    whose reader has no strided read (it reads the whole array per box anyway).
    A strided grid reader keeps one hyperslab read per box — smaller than the
    whole file, and never larger than the boxes asked for."""
    if getattr(dataset_info, 'positions', None) or \
            'particles' in (dataset_info.dimensions or {}):
        return True
    return not adapter_capabilities(dataset_info)['strided_read']


def materialize_boxes(dataset_info, narrowings):
    """[materialize(dataset_info, n) for n in narrowings], reading the file once
    when `shares_read` says the per-box reads would each be the whole file.

    The shared read carries everything the boxes have in common — the particle
    subsample pushed into the read, which the planner draws once per file for
    every box — over the union of the variables they need. Each box then
    gets its own grid crop (a copy, so one box's NaN mask cannot reach another)
    or its own post-read row masks, in written order."""
    narrowings = list(narrowings)
    if len(narrowings) < 2 or not shares_read(dataset_info) or not _same_pushdown(narrowings):
        return [materialize(dataset_info, n) for n in narrowings]
    sets = [_read_set(dataset_info, n) for n in narrowings]
    read_vars = []
    for variables, extra in sets:
        read_vars += [v for v in variables + extra if v not in read_vars]
    first = narrowings[0]
    base = _read(dataset_info, read_vars, Narrowing(
        particle_index=first.particle_index, project=tuple(read_vars),
        total_particles=first.total_particles, positions=first.positions,
        dimensions={k: v for k, v in (first.dimensions or {}).items()
                    if k != 'grid_ranges'}))
    geometry = _source_geometry(dataset_info)        # read once, cut per box
    out = []
    for narrowing, (variables, extra) in zip(narrowings, sets):
        loaded = copy.deepcopy(dataset_info)
        loaded.geometry = geometry
        for var in variables + extra:
            arr = base[var]
            if arr.ndim == 3:
                # Always a copy: a threshold NaN-masks a grid in place.
                arr = np.array(arr[narrowing.indexer(3, arr.shape[0])])
            loaded.data[var] = arr
        out.append(_settle(loaded, narrowing, variables, extra))
    return out


def _same_pushdown(narrowings):
    """True when the narrowings differ only in what a shared read can cut
    afterwards (grid crop/stride, post-read ops, projection)."""
    first = narrowings[0].particle_index
    for n in narrowings[1:]:
        idx = n.particle_index
        if (idx is None) != (first is None):
            return False
        if isinstance(idx, slice) or isinstance(first, slice):
            if idx != first:
                return False
        elif idx is not None and not np.array_equal(np.asarray(idx), np.asarray(first)):
            return False
        if n.has_row_mask or narrowings[0].has_row_mask:
            return False                   # the legacy mask path reads per box
    return True


def _read_set(loaded, narrowing):
    """(output variables, mask-only extras) a narrowing reads."""
    # Output variables: the planner's projection, else everything listed.
    if getattr(narrowing, 'project', None) is not None:
        variables = _resolve_variables(loaded, list(narrowing.project))
    else:
        variables = _resolve_variables(loaded, None)
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
    return variables, extra


def _read(info, read_vars, narrowing):
    """{var: array} for `read_vars`, read through the format's adapter with the
    narrowing's structural cuts pushed into the read."""
    adapter = get_adapter_for_info(info)
    locations = getattr(info, 'variable_locations', None) or {}
    read_all = getattr(adapter, 'read_all', None)
    if read_all is not None:
        wanted = {var: locations.get(var, var) for var in read_vars}
        return {var: np.asarray(arr)
                for var, arr in read_all(info.filepath, wanted, narrowing).items()}
    return {var: np.asarray(adapter.read_array(info.filepath, locations.get(var, var),
                                               narrowing))
            for var in read_vars}


def _settle(loaded, narrowing, variables, extra):
    """Apply the computed cuts and finish the result's metadata."""
    positions = getattr(loaded, 'positions', None) or ()
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
    # Track where the block we just read sits in the source. Without this a
    # cropped grid would be written out at the source's origin and land in the
    # wrong place in world space — and two timesteps' ROIs would not align.
    loaded.geometry = narrowed_geometry(getattr(loaded, 'geometry', None),
                                        getattr(narrowing, 'grid_ranges', None))
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
                # Seeded, for the same reason the pushdown path is: an unseeded
                # draw makes the spec that produced this output unreproducible.
                index = np.sort(sampling_rng().choice(n, size=k, replace=False))
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
                        from vislang.runtime import provenance
                        provenance.note_cast(var)      # the record says so
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
