"""Reader module for NumPy .npz archive format."""

FILETYPE = "npz"
EXTENSIONS = [".npz"]


def inspect(filepath):
    """Inspect a .npz file and return metadata."""
    import numpy as np
    
    with np.load(filepath, allow_pickle=False) as data:
        array_names = list(data.keys())
        
        # Separate variables (large particle arrays) from attributes (scalars/metadata)
        variables = []
        attributes = {}
        dimensions = {}
        
        # First pass: identify particle count and separate variables from attributes
        particle_count = None
        for name in array_names:
            arr = data[name]
            
            # If it's a large 1-D array, it's likely particle data
            if arr.ndim == 1 and arr.size > 1000:
                variables.append(name)
                if particle_count is None:
                    particle_count = int(arr.size)
            # If it's a scalar or very small array, treat as attribute
            elif arr.ndim == 0 or (arr.ndim == 1 and arr.size == 1):
                # Convert to JSON-serializable type
                if arr.ndim == 0:
                    attributes[name] = float(arr.item())
                else:
                    attributes[name] = float(arr[0])
            else:
                # For other cases, include as variable
                variables.append(name)
        
        # Set dimensions based on particle count
        if particle_count is not None:
            dimensions["particles"] = particle_count
        
        return {
            "filetype": FILETYPE,
            "variables": variables,
            "dimensions": dimensions,
            "attributes": attributes,
        }


def read_array(filepath, location):
    """Read a single array from the .npz file."""
    import numpy as np
    
    with np.load(filepath, allow_pickle=False) as data:
        return data[location][:]