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


def narrowed_geometry(geometry, grid_ranges):
    """`geometry` as it applies to a block cut out by `grid_ranges`.

        origin_out  = origin_in + start * spacing_in
        spacing_out = spacing_in * step

    so a cropped, strided block still reports where in the source it came from
    and lands in the right place in world space. `grid_ranges` is read
    duck-typed (`.start` / `.step`) so this stays independent of the interpreter
    layer that defines AxisRange. Returns `geometry` unchanged when there is
    nothing to apply."""
    if not geometry or geometry.get("kind") != "uniform" or not grid_ranges:
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

        # Pending narrowing recorded by subset() (metadata only, applied by load).
        # Projection trims `variables` directly; only the slice policy needs a
        # field, since "stride to 64 cells" is a how-to-read directive, not a
        # removable field.
        self.selected_dimensions = None  # e.g. {'grid': 64} or {'particles': 0.1}

        # Data (populated by load)
        self.data = {}  # {variable_name: numpy_array}
        self.loaded = False
        self.selection_info = {}
    
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