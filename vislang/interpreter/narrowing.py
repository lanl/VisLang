"""The generalized narrowing model — the one structure the planner fuses every
narrowing form into, and the one the physical read paths consult.

Two layers, matching the interpreter's two-axis form taxonomy:

- **Pushdown (structural cuts)** — knowable from metadata, folded INTO the read:
  grid crop+stride (`grid_ranges`), particle row subsample (`particle_index`),
  and projection (`project`, the output variable set). Every read funnels
  through `Narrowing.indexer` (HDF5/FITS hyperslab) or `apply_selection`
  (read-full-then-slice fallback).

- **Post-read ops (computed cuts)** — need the data, applied AFTER the read in
  the exact order the spec wrote them (`post_ops`): `RowMask` (particle bbox /
  value predicates), `RowSample` (a subsample demoted because a computed cut
  preceded it in the spec — pushing it down would silently reorder), and
  `VoxelMask` (grid value threshold, NaN-fill so the cube keeps its shape).

It stays backward compatible: `narrowing_from_dimensions` rebuilds the exact
old behavior from a legacy `selected_dimensions` dict, so `load()` reads
byte-identically to before.
"""

from dataclasses import dataclass, field

import numpy as np

# Sentinel: arr[TAKE_ALL] / dset[TAKE_ALL] is a full read, so callers index
# unconditionally.
TAKE_ALL = slice(None)

_OPS = ("<", "<=", ">", ">=", "==", "!=")
_OP_FN = {"<": np.less, "<=": np.less_equal, ">": np.greater,
          ">=": np.greater_equal, "==": np.equal, "!=": np.not_equal}


# ---------------------------------------------------------------------------
# Structured pieces a Narrowing is built from
# ---------------------------------------------------------------------------

# (grid) region, (grid) subsample

# source("sim.h5")
#   |> region(x=(0, 50))          # crop x to cells 0–50
#   |> subsample(d, 2)            # keep every 2nd cell

# to

# [ AxisRange(start=0,    stop=50,   step=2),   # x: cropped AND strided
#   AxisRange(start=None, stop=None, step=2),   # y: full extent, strided
#   AxisRange(start=None, stop=None, step=2) ]  # z: full extent, strided
#  ... ]


@dataclass(frozen=True)
class AxisRange:
    """One grid axis: index-space crop [start:stop] with an optional stride.
    None start/stop mean open (full extent). Interpretation-agnostic so a future
    world-space region can convert to indices before building this."""
    start: int = None
    stop: int = None
    step: int = 1

    def to_slice(self):
        return slice(self.start, self.stop, self.step or 1)

# (grid, particle) threshold
@dataclass(frozen=True)
class Predicate:
    """A value filter: keep where `var` `op` `value` (scalar rhs)."""
    var: str
    op: str
    value: float


# (particle) region
@dataclass(frozen=True)
class BBox:
    """A coordinate-VALUE box on the position variables (info.positions)."""
    lo: tuple = (None, None, None)
    hi: tuple = (None, None, None)


# ---------------------------------------------------------------------------
# Post-read ops — computed cuts, applied in written order after the read
# ---------------------------------------------------------------------------

# (predicate) region, (particle) threshold
@dataclass(frozen=True)
class RowMask:
    """Drop particle/table rows failing a bbox and/or ANDed value predicates."""
    bbox: BBox = None
    predicates: tuple = ()


# (particle) subsample that couldn't be pushed down
@dataclass(frozen=True)
class RowSample:
    """A particle subsample demoted to post-read because a computed cut precedes
    it in the spec ("every Nth of what's left" is order-sensitive). int = stride,
    float in (0,1] = random fraction of the current rows."""
    factor: object = 1


# threshold
@dataclass(frozen=True)
class VoxelMask:
    """Grid value threshold: NaN-fill voxels failing the ANDed predicates.
    Shape-preserving (the cube stays dense), so it commutes with grid crops and
    strides — order relative to them never changes the result."""
    predicates: tuple = ()


# ---------------------------------------------------------------------------
# Legacy dict -> indices (verbatim from the old adapters.Selection helpers, so
# the back-compat path reproduces prior reads exactly)
# ---------------------------------------------------------------------------
def _get_particle_indices(dimensions, total_particles):
    """Convert a 'particles' dimension selection to indices.
    Returns None (load all), a slice, or a numpy array of indices."""
    if dimensions is None or 'particles' not in dimensions:
        return None
    selection = dimensions['particles']
    if isinstance(selection, slice):
        return selection
    elif isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float selection must be between 0 and 1, got {selection}")
        n_select = int(total_particles * selection)
        return np.random.choice(total_particles, size=n_select, replace=False)
    elif isinstance(selection, int):
        if selection > total_particles:
            raise ValueError(f"Cannot select {selection} particles from {total_particles}")
        return np.random.choice(total_particles, size=selection, replace=False)
    else:
        raise ValueError(f"Invalid dimension selection type: {type(selection)}")


def _get_grid_step(dimensions, axis_size):
    """Convert a 'grid' dimension selection to a per-axis stride (int)."""
    if dimensions is None or 'grid' not in dimensions:
        return 1
    selection = dimensions['grid']
    if isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float grid selection must be between 0 and 1, got {selection}")
        return max(1, int(round(1 / selection)))
    elif isinstance(selection, int):
        if selection <= 0:
            raise ValueError(f"Grid resolution must be positive, got {selection}")
        return max(1, axis_size // selection)
    elif isinstance(selection, slice):
        return selection.step or 1
    else:
        raise ValueError(f"Invalid grid selection type: {type(selection)}")


# ---------------------------------------------------------------------------
# The Narrowing
# ---------------------------------------------------------------------------
class Narrowing:
    """A resolved, format-blind narrowing, built ONCE per materialize() call.

    `indexer(ndim, leading_len)` returns a numpy index object (or TAKE_ALL) for
    grid crop+stride and particle subsample — the part expressible as a basic
    index (the pushdown layer). `post_ops` is the ordered computed layer the
    planner lowered from the spec: RowMask / RowSample / VoxelMask, applied by
    materialize in written order after the read. `project` names the output
    variables (None = all); the read-set is project ∪ whatever post_ops need.
    `bbox`/`predicates` remain for the legacy row-mask path (subset()/load()).
    `dimensions` is a free-form echo for selection_info.
    """

    def __init__(self, *, grid_ranges=None, particle_index=None, bbox=None,
                 predicates=(), post_ops=(), project=None, dimensions=None,
                 total_particles=0, positions=None):
        self.grid_ranges = grid_ranges          # list[AxisRange] (len == ndim) or None
        self.particle_index = particle_index     # slice | np.ndarray | None
        self.bbox = bbox                          # BBox or None  (legacy mask path)
        self.predicates = tuple(predicates)       # tuple[Predicate]  (legacy mask path)
        self.post_ops = tuple(post_ops)            # ordered RowMask|RowSample|VoxelMask
        self.project = tuple(project) if project is not None else None  # output vars
        self.dimensions = dimensions or {}        # echo for selection_info
        self.total_particles = total_particles or 0
        self.positions = positions

    def indexer(self, ndim, leading_len):
        # NEW: per-axis grid crop + stride (region / per-axis subsample).
        if ndim == 3 and self.grid_ranges:
            return tuple(r.to_slice() for r in self.grid_ranges)
        # LEGACY: modality-wide grid stride from the dimensions dict.
        if ndim == 3 and self.dimensions and 'grid' in self.dimensions:
            step = _get_grid_step(self.dimensions, leading_len)
            if step <= 1:
                return TAKE_ALL
            s = slice(None, None, step)
            return (s, s, s)
        # Particle / table rows: same index for every aligned variable.
        if (ndim in (1, 2) and self.particle_index is not None
                and self.total_particles and leading_len == self.total_particles):
            return self.particle_index if ndim == 1 else (self.particle_index, slice(None))
        return TAKE_ALL

    @property
    def has_row_mask(self):
        return self.bbox is not None or bool(self.predicates)


def narrowing_from_dimensions(dimensions, total_particles, positions=None):
    """Back-compat builder: a Narrowing equivalent to the old Selection built
    from a legacy `selected_dimensions` dict. Keeps load() byte-identical."""
    total = total_particles or 0
    pidx = _get_particle_indices(dimensions, total) if total else None
    return Narrowing(grid_ranges=None, particle_index=pidx, dimensions=dimensions,
                     total_particles=total, positions=positions)


# ---------------------------------------------------------------------------
# Read-full-then-slice fallback + row mask
# ---------------------------------------------------------------------------
def apply_selection(arr, selection):
    """Apply a Narrowing's basic index to a materialized array (used by adapters
    whose library can't push the selection into the read)."""
    arr = np.asarray(arr)
    idx = selection.indexer(arr.ndim, arr.shape[0] if arr.ndim else 0)
    return arr if idx is TAKE_ALL else arr[idx]


def row_mask_from(bbox, predicates, data, positions):
    """1-D boolean mask over particle/table rows from a bbox + ANDed predicates,
    evaluated against the CURRENT `data` arrays. Returns None if nothing applies."""
    mask = None
    if bbox is not None and positions:
        for axis, vname in enumerate(positions):
            lo, hi = bbox.lo[axis], bbox.hi[axis]
            col = np.asarray(data[vname])
            m = np.ones(len(col), dtype=bool)
            if lo is not None:
                m &= col >= lo
            if hi is not None:
                m &= col <= hi
            mask = m if mask is None else (mask & m)
    for p in predicates:
        m = _OP_FN[p.op](np.asarray(data[p.var]), p.value)
        mask = m if mask is None else (mask & m)
    return mask


def build_row_mask(narrowing, data, positions):
    """Legacy entry point: mask from the Narrowing's own bbox/predicates."""
    return row_mask_from(narrowing.bbox, narrowing.predicates, data, positions)


def voxel_mask_from(predicates, data):
    """N-D boolean mask (True = keep) over a grid from ANDed value predicates.
    Every predicate variable must be a loaded grid field of one common shape."""
    mask = None
    for p in predicates:
        arr = np.asarray(data[p.var])
        if arr.ndim < 2:
            raise ValueError(f"threshold on a grid needs a grid field; "
                             f"{p.var!r} has shape {arr.shape}")
        m = _OP_FN[p.op](arr, p.value)
        if mask is not None and m.shape != mask.shape:
            raise ValueError("threshold predicates reference grid fields of "
                             f"different shapes: {m.shape} vs {mask.shape}")
        mask = m if mask is None else (mask & m)
    return mask


def post_op_read_vars(post_ops, positions):
    """Variables the post-read ops need present at mask time (the read-set
    extension): predicate vars, plus the position vars for any bbox."""
    needed = set()
    for op in post_ops:
        if isinstance(op, (RowMask, VoxelMask)):
            needed.update(p.var for p in getattr(op, 'predicates', ()))
        if isinstance(op, RowMask) and op.bbox is not None and positions:
            needed.update(positions)
    return needed


# ---------------------------------------------------------------------------
# Static check (metadata only — runs before any bulk read)
# ---------------------------------------------------------------------------
def validate_narrowing(info, n):
    """Validate a planner-built Narrowing against an inspected schema. Raises a
    clear error before any data is read. Generalizes _validate_dimension_selection.

    NOTE: `info` must carry the FULL inspected variable list (pre-projection) —
    a threshold/bbox may reference a variable outside `project` (read for the
    mask, dropped after)."""
    dims = info.dimensions or {}

    if n.grid_ranges is not None:
        grid = dims.get('grid')
        if not (isinstance(grid, (tuple, list)) and len(grid) == len(n.grid_ranges)):
            raise ValueError(f"region/subsample: dataset grid {grid!r} does not have "
                             f"{len(n.grid_ranges)} axes")
        for ax, (r, size) in enumerate(zip(n.grid_ranges, grid)):
            a = 0 if r.start is None else r.start
            b = size if r.stop is None else r.stop
            if not (0 <= a < b <= size):
                raise ValueError(f"region axis {ax}: [{a}:{b}] out of bounds 0..{size}")
            if r.step < 1:
                raise ValueError(f"subsample axis {ax}: step {r.step} must be >= 1")

    if n.project is not None:
        missing = set(n.project) - set(info.variables)
        if missing:
            raise ValueError(f"fields: variables not in dataset: {sorted(missing)}")

    def _check_bbox():
        if not info.positions:
            raise ValueError("region on points needs coordinate variables "
                             "(info.positions); set inspect(positions=('x','y','z'))")
        for v in info.positions:
            if v not in info.variables:
                raise ValueError(f"position variable {v!r} not in dataset")

    def _check_predicates(preds, where):
        for p in preds:
            if p.var not in info.variables:
                raise ValueError(f"{where} variable {p.var!r} not in {info.variables}")
            if p.op not in _OPS:
                raise ValueError(f"{where} operator {p.op!r} invalid; one of {list(_OPS)}")

    # Legacy mask path (subset()/load()) still sets these directly.
    if n.bbox is not None:
        _check_bbox()
    _check_predicates(n.predicates, "threshold")

    # Ordered computed layer.
    for op in n.post_ops:
        if isinstance(op, RowMask):
            if op.bbox is not None:
                _check_bbox()
            _check_predicates(op.predicates, "threshold")
        elif isinstance(op, VoxelMask):
            _check_predicates(op.predicates, "threshold")
        elif isinstance(op, RowSample):
            f = op.factor
            if isinstance(f, bool) or not isinstance(f, (int, float)):
                raise ValueError(f"subsample factor must be int or float, got {f!r}")
            if isinstance(f, int) and f < 1:
                raise ValueError(f"subsample stride must be >= 1, got {f}")
            if isinstance(f, float) and not (0 < f <= 1):
                raise ValueError(f"subsample fraction must be in (0, 1], got {f}")
        else:
            raise ValueError(f"unknown post-read op {op!r}")
