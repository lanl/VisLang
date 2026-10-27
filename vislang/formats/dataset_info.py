import numpy as np


def uniform_geometry(origin, spacing):
    """A uniform-grid geometry, or None if either part is unusable.

    Returning None rather than substituting a default is the point: a caller
    that cannot state the origin should leave `geometry` unset, not assert an
    origin the file never gave."""
    def _three(v):
        try:
            a = np.asarray(v, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return None
        if a.size != 3 or not np.all(np.isfinite(a)):
            return None
        return tuple(float(x) for x in a)

    o, s = _three(origin), _three(spacing)
    if o is None or s is None:
        return None
    return {"kind": "uniform", "origin": o, "spacing": s}


def geometry_from_edges(left_edge, right_edge, dimensions):
    """Uniform geometry from a domain bounding box + its cell counts (yt's
    `domain_left_edge`/`domain_right_edge`/`domain_dimensions`)."""
    try:
        lo = np.asarray(left_edge, dtype=float).reshape(-1)
        hi = np.asarray(right_edge, dtype=float).reshape(-1)
        n = np.asarray(dimensions, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None
    if not (lo.size == hi.size == n.size == 3) or np.any(n <= 0):
        return None
    return uniform_geometry(lo, (hi - lo) / n)


def structured_geometry(points):
    """A curvilinear-grid geometry — every grid point's own (x, y, z) — or None
    if `points` is not a grid of 3-vectors.

    `points` is indexed like the grid's arrays, (nx, ny, nz, 3), axis 0 = x. This
    is what a terrain-following grid needs: x and y may be regular while z
    follows the ground, so no origin + spacing can say where a point is."""
    try:
        pts = np.asarray(points)
    except (TypeError, ValueError):
        return None
    if pts.ndim < 2 or pts.shape[-1] != 3 or not np.issubdtype(pts.dtype, np.number):
        return None
    return {"kind": "structured", "points": pts}


def structured_pending():
    """Inspect's marker for a source whose points are listed one by one. The
    points are bulk data (3 numbers per grid point), so inspect only says they
    exist; materialize reads them, cut by the same box as the data."""
    return {"kind": "structured", "points": None}


def narrowed_geometry(geometry, grid_ranges):
    """`geometry` as it applies to a block cut out by `grid_ranges`.

    Uniform:
        origin_out  = origin_in + start * spacing_in
        spacing_out = spacing_in * step
    Structured: the same index cut applied to the per-point positions.

    so a cropped, strided block still reports where in the source it came from
    and lands in the right place in world space. `grid_ranges` is read
    duck-typed (`.start` / `.stop` / `.step`, which python slices also have) so
    this stays independent of the interpreter layer that defines AxisRange.
    Returns `geometry` unchanged when there is nothing to apply."""
    if not geometry or not grid_ranges:
        return geometry
    if geometry.get("kind") == "structured":
        pts = geometry.get("points")
        if pts is None or len(grid_ranges) != pts.ndim - 1:
            return geometry          # no points yet, or rank mismatch: never guess
        cut = tuple(slice(getattr(r, "start", None), getattr(r, "stop", None),
                          getattr(r, "step", None) or 1) for r in grid_ranges)
        # A copy, so the cut does not keep the whole source's points alive.
        return structured_geometry(np.ascontiguousarray(pts[cut]))
    if geometry.get("kind") != "uniform":
        return geometry
    origin, spacing = geometry["origin"], geometry["spacing"]
    if len(grid_ranges) != len(origin):
        return geometry              # rank mismatch: say nothing rather than guess
    out_o, out_s = [], []
    for axis, r in enumerate(grid_ranges):
        start = getattr(r, "start", None) or 0
        step = getattr(r, "step", None) or 1
        out_o.append(origin[axis] + start * spacing[axis])
        out_s.append(spacing[axis] * step)
    return uniform_geometry(out_o, out_s)


# ---------------------------------------------------------------------------
# Geometry as arrays: how it rides in a file, over the wire, and in the cache
# ---------------------------------------------------------------------------
# In an .npz that save() writes, under readable reserved keys (the `sieve_`
# prefix is never a variable):
#   uniform     sieve_origin (3,), sieve_spacing (3,)
#   structured  sieve_points (nx, ny, nz, 3)
GEOMETRY_ARRAY_KEYS = ("sieve_origin", "sieve_spacing", "sieve_points")

# Between the remote reducer and this side, and in the extent catalog, a cut's
# geometry is ONE array under this reserved name, so it can be requested,
# cached and reused like a variable: shape (0,) = index space (no geometry),
# (2, 3) = uniform [origin, spacing], (..., 3) with ndim >= 3 = structured.
GEOMETRY_KEY = "sieve_geometry"


def geometry_to_arrays(geometry):
    """{reserved key: array} for an .npz; {} for index space."""
    if not geometry:
        return {}
    if geometry.get("kind") == "uniform":
        return {"sieve_origin": np.asarray(geometry["origin"], dtype=float),
                "sieve_spacing": np.asarray(geometry["spacing"], dtype=float)}
    if geometry.get("kind") == "structured" and geometry.get("points") is not None:
        return {"sieve_points": np.asarray(geometry["points"])}
    return {}


def encode_geometry(geometry):
    """The geometry of one cut as the single array GEOMETRY_KEY carries."""
    arrays = geometry_to_arrays(geometry)
    if "sieve_points" in arrays:
        return arrays["sieve_points"]
    if "sieve_origin" in arrays:
        return np.stack([arrays["sieve_origin"], arrays["sieve_spacing"]])
    return np.zeros((0,))


def decode_geometry(arr):
    """Inverse of encode_geometry: a geometry dict, or None for index space."""
    arr = np.asarray(arr)
    if arr.ndim == 2 and arr.shape == (2, 3):
        return uniform_geometry(arr[0], arr[1])
    if arr.ndim >= 3 and arr.shape[-1] == 3:
        return structured_geometry(arr)
    return None


def geometry_from_arrays(arrays):
    """The geometry stored among `arrays` (an .npz's members, a remote reply),
    in either form above, or None when it holds none."""
    if GEOMETRY_KEY in arrays:
        return decode_geometry(arrays[GEOMETRY_KEY])
    if "sieve_points" in arrays:
        return structured_geometry(arrays["sieve_points"])
    if "sieve_origin" in arrays and "sieve_spacing" in arrays:
        return uniform_geometry(arrays["sieve_origin"], arrays["sieve_spacing"])
    return None


# ---------------------------------------------------------------------------
# Geometry in a schema: what inspect can say without reading bulk data
# ---------------------------------------------------------------------------
# A schema travels as JSON (the remote inspect report, the extent catalog), so
# it carries the geometry's description, never the points themselves:
#   None                                   index space — the file states none
#   {"kind": "uniform", origin, spacing}   complete: origin + spacing ARE it
#   {"kind": "structured"}                 the points exist; they are bulk data
def geometry_descriptor(geometry):
    """The JSON-safe description of `geometry` for a schema (see above)."""
    if not geometry:
        return None
    if geometry.get("kind") == "uniform":
        return {"kind": "uniform", "origin": [float(v) for v in geometry["origin"]],
                "spacing": [float(v) for v in geometry["spacing"]]}
    if geometry.get("kind") == "structured":
        return {"kind": "structured"}
    return None


def geometry_from_descriptor(desc):
    """Inverse of geometry_descriptor: a geometry dict (a structured one with
    its points still to be read), or None for index space."""
    if not isinstance(desc, dict):
        return None
    if desc.get("kind") == "uniform":
        return uniform_geometry(desc.get("origin"), desc.get("spacing"))
    if desc.get("kind") == "structured":
        return structured_pending()
    return None


# ---------------------------------------------------------------------------
# World coordinates -> cells: how a region in the data's own coordinates
# becomes the index crop the read layer takes
# ---------------------------------------------------------------------------
# A grid sample sits where narrowed_geometry and every writer put it: uniform
# sample i at origin + i * spacing, a structured sample at its listed point.
# A world box keeps the samples whose position lies inside it, bounds
# INCLUSIVE — the rule a particle region already applies to its rows, so one
# box means the same thing on every kind of data.
_WORLD_EPS = 1e-9


def world_box_to_cells(geometry, grid, box):
    """The index block a world-coordinate box selects on a grid.

    `box` is {world axis 0|1|2: (lo, hi)}, either bound None for open. Returns
    ({index axis: (start, stop)}, clipped) — `stop` exclusive, only the index
    axes the box narrows — where `clipped` names the world axes whose bound
    reached past the grid's own extent (clipped to it, as a particle box is).
    Raises ValueError when no sample lies inside, and when the geometry cannot
    answer (none, or a structured grid whose points are not read yet).

    Uniform: exact, per axis. Structured (terrain-following, …): the smallest
    block of cells holding every point inside the box — a block that may also
    hold points outside it, since a curved grid's rows do not follow the box."""
    if not geometry:
        raise ValueError("this grid does not say where it sits in space")
    grid = tuple(int(n) for n in grid)
    if geometry.get("kind") == "uniform":
        return _uniform_box_to_cells(geometry, grid, box)
    if geometry.get("kind") == "structured":
        if geometry.get("points") is None:
            raise ValueError("this grid's point positions are not read yet")
        return _structured_box_to_cells(np.asarray(geometry["points"]), grid, box)
    raise ValueError(f"unknown geometry kind {geometry.get('kind')!r}")


def _uniform_box_to_cells(geometry, grid, box):
    origin, spacing = geometry["origin"], geometry["spacing"]
    cells, clipped = {}, []
    for axis, (lo, hi) in sorted(box.items()):
        if axis >= len(grid):
            raise ValueError(f"axis {'xyz'[axis]} is not one of this "
                             f"{len(grid)}-D grid's axes")
        o, s, n = float(origin[axis]), float(spacing[axis]), grid[axis]
        if s == 0:
            raise ValueError(f"axis {'xyz'[axis]}: grid spacing is 0")
        first, last = o, o + (n - 1) * s                  # sample positions
        dlo, dhi = min(first, last), max(first, last)     # the axis' extent
        if (lo is not None and lo < dlo) or (hi is not None and hi > dhi):
            clipped.append("xyz"[axis])
        # Sample i is inside when lo <= o + i*s <= hi; a negative spacing
        # walks the axis backwards, which swaps which bound gives the first i.
        t_lo = -np.inf if lo is None else (lo - o) / s
        t_hi = np.inf if hi is None else (hi - o) / s
        if s < 0:
            t_lo, t_hi = t_hi, t_lo
        start = max(0, int(np.ceil(t_lo - _WORLD_EPS)) if np.isfinite(t_lo) else 0)
        stop = min(n, int(np.floor(t_hi + _WORLD_EPS)) + 1 if np.isfinite(t_hi) else n)
        if start >= stop:
            raise ValueError(
                f"the box on {'xyz'[axis]} ({_bound(lo)}, {_bound(hi)}) holds no "
                f"grid sample; this grid spans {dlo:g} to {dhi:g} on {'xyz'[axis]}")
        cells[axis] = (start, stop)
    return cells, clipped


def _structured_box_to_cells(points, grid, box):
    if points.shape[:-1] != grid:
        raise ValueError(f"the grid's points {points.shape[:-1]} do not match "
                         f"its shape {grid}")
    inside = np.ones(grid, dtype=bool)
    clipped = []
    for axis, (lo, hi) in sorted(box.items()):
        if axis > 2:
            raise ValueError(f"world axis {axis} is not one of x, y, z")
        coord = points[..., axis]
        if (lo is not None and lo < coord.min()) or (hi is not None and hi > coord.max()):
            clipped.append("xyz"[axis])
        if lo is not None:
            inside &= coord >= lo
        if hi is not None:
            inside &= coord <= hi
    if not inside.any():
        spans = ", ".join(f"{'xyz'[a]} {points[..., a].min():g} to "
                          f"{points[..., a].max():g}" for a in sorted(box))
        raise ValueError(f"the box holds no grid point; this grid spans {spans}")
    cells = {}
    for axis in range(len(grid)):
        other = tuple(a for a in range(len(grid)) if a != axis)
        hits = np.flatnonzero(inside.any(axis=other))
        start, stop = int(hits[0]), int(hits[-1]) + 1
        if (start, stop) != (0, grid[axis]):
            cells[axis] = (start, stop)
    return cells, clipped


def _bound(v):
    return "open" if v is None else f"{v:g}"


class DatasetInfo:
    """Container for dataset metadata and optionally loaded data."""
    
    def __init__(self, filepath, filetype, variables, dimensions=None, attributes=None,
                 itemsizes=None):
        # Metadata (always populated by inspect)
        self.filepath = filepath
        self.filetype = filetype
        self.variables = variables
        self.dimensions = dimensions or {}
        self.attributes = attributes or {}

        # Per-variable element size in bytes (var -> itemsize), captured by inspect
        # from the same header it already reads for shapes — no bulk read. Used by
        # the cost estimator for honest byte math; absent means "unknown", and the
        # estimator falls back to a 4-byte (float32) assumption. This is live
        # metadata only: it is NEVER persisted to the extent catalog.
        self.itemsizes = itemsizes or {}

        # Semantic role binding resolved by inspect: which variables are the
        # spatial coordinates, as ('x','y','z'). None when the data has no
        # explicit coordinate variables (e.g. a grid — its coordinates are
        # implicit in the array shape). Like `dimensions`, this is modality-
        # specific metadata, not a field every dataset fills.
        self.positions = None

        # Where this dataset sits in space, or None. Grids carry their geometry
        # implicitly in the array shape, which is enough to READ them but not to
        # WRITE one into a format that places its data in space (VTK). Shape:
        #
        #   {'kind': 'uniform', 'origin': (x,y,z), 'spacing': (dx,dy,dz)}
        #   {'kind': 'structured', 'points': (nx,ny,nz,3) array}
        #
        # The second is a curvilinear grid (e.g. terrain-following), whose
        # points no origin + spacing can describe. Inspect leaves `points` None
        # (structured_pending(): they are bulk data); materialize reads them.
        #
        # None means index space — a legitimate result, and the honest one when
        # the source header does not say. NEVER synthesize a source origin that
        # was not in the file: the distinction is the same one _genericio_blocker
        # exists to keep (a default the target format defines is fine; inventing
        # metadata the source never stated is not). A consumer may default a
        # missing origin to (0,0,0) itself, because index space IS a real
        # vtkImageData; it may not claim the source said so.
        #
        # materialize() rewrites this to track the narrowing it applied, so a
        # cropped block reports where in the source it came from. That record is
        # what makes save(..., '.vti') land in the right place.
        self.geometry = None

        # False only for a stand-in built from a schema that predates geometry
        # (an old catalog entry): there `geometry = None` means "not reported",
        # not "index space", so a region's coordinates cannot be decided from
        # it. Every inspect of a real file leaves this True.
        self.geometry_known = True

        # Pending narrowing recorded by subset() (metadata only, applied by load).
        # Projection trims `variables` directly; only the slice policy needs a
        # field, since "stride to 64 cells" is a how-to-read directive, not a
        # removable field.
        self.selected_dimensions = None  # e.g. {'grid': 64} or {'particles': 0.1}

        # Data (populated by load)
        self.data = {}  # {variable_name: numpy_array}
        self.loaded = False
        self.selection_info = {}
    
    def _region_coords_text(self):
        """What a region()'s numbers mean on this data — the line an author
        needs to write one in the coordinates ParaView will show."""
        grid = (self.dimensions or {}).get("grid")
        if self.positions or "particles" in (self.dimensions or {}):
            return "world (the position variables)"
        if not grid:
            return None
        geometry = self.geometry
        if not geometry:
            if not self.geometry_known:
                return None
            return ("cells — the file does not say where the grid sits, so "
                    "its world coordinates are its indices")
        if geometry.get("kind") == "uniform":
            o, s = geometry["origin"], geometry["spacing"]
            spans = ", ".join(
                f"{'xyz'[a]} {min(o[a], o[a] + (n - 1) * s[a]):g} to "
                f"{max(o[a], o[a] + (n - 1) * s[a]):g}"
                for a, n in enumerate(grid[:len(o)]))
            return (f"world — origin {tuple(o)}, spacing {tuple(s)}; spans "
                    f"{spans} (coords='cells' for indices)")
        return ("world — each grid point's position is listed (curvilinear); "
                "a box selects the smallest block of cells holding it "
                "(coords='cells' for indices)")

    def __str__(self):
        output = []
        output.append(f"File: {self.filepath}")
        output.append(f"Type: {self.filetype}")
        
        # Show inspection info
        output.append(f"\nAvailable Variables ({len(self.variables)}):")
        for var in self.variables:
            output.append(f"  - {var}")
        
        if self.dimensions:
            output.append(f"\nDimensions:")
            for dim, size in self.dimensions.items():
                output.append(f"  - {dim}: {size}")

        if self.positions:
            output.append(f"\nCoordinates: {self.positions}")

        region_line = self._region_coords_text()
        if region_line:
            output.append(f"\nregion() coordinates: {region_line}")

        if self.selected_dimensions:
            output.append(f"\nPending dimension selection (applied by load):")
            for dim, sel in self.selected_dimensions.items():
                output.append(f"  - {dim}: {sel}")

        # Show loaded data if present
        if self.loaded:
            output.append(f"\n{'='*50}")
            output.append(f"LOADED DATA:")
            output.append(f"\nLoaded Variables ({len(self.data)}):")
            for var, arr in self.data.items():
                output.append(f"  - {var}: shape={arr.shape}, dtype={arr.dtype}")
            
            if self.selection_info:
                output.append(f"\nSelection Applied:")
                for key, val in self.selection_info.items():
                    output.append(f"  - {key}: {val}")
        else:
            output.append(f"\n[Data not loaded - call load() to populate]")
        
        if self.attributes:
            output.append(f"\nAttributes:")
            for attr, value in self.attributes.items():
                output.append(f"  - {attr}: {value}")
        
        return "\n".join(output)