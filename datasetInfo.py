import numpy as np

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