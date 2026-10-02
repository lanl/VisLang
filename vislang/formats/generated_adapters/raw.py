"""Reader module for raw binary volumetric data files."""

import os
import re

FILETYPE = "raw_volume"
EXTENSIONS = [".raw"]

def inspect(filepath):
    """
    Inspect a raw binary file and extract metadata from filename.
    
    Expected filename format: <name>_<nx>x<ny>x<nz>_<dtype>.raw
    """
    import numpy as np
    
    basename = os.path.basename(filepath)
    
    # Parse dimensions and dtype from filename
    # Pattern: something_302x302x302_uint8.raw
    pattern = r'_(\d+)x(\d+)x(\d+)_(\w+)\.raw$'
    match = re.search(pattern, basename)
    
    if not match:
        raise ValueError(
            f"Cannot parse dimensions and dtype from filename '{basename}'. "
            f"Expected format: <name>_<nx>x<ny>x<nz>_<dtype>.raw"
        )
    
    nx, ny, nz, dtype_str = match.groups()
    nx, ny, nz = int(nx), int(ny), int(nz)
    
    # Validate dtype
    try:
        dtype = np.dtype(dtype_str)
    except TypeError:
        raise ValueError(f"Invalid dtype '{dtype_str}' in filename")
    
    # Verify file size matches expected size
    expected_size = nx * ny * nz * dtype.itemsize
    actual_size = os.path.getsize(filepath)
    
    if actual_size != expected_size:
        raise ValueError(
            f"File size mismatch: expected {expected_size} bytes "
            f"for {nx}x{ny}x{nz} {dtype_str} array, but file is {actual_size} bytes"
        )
    
    # Extract variable name from filename (everything before dimensions)
    var_match = re.match(r'^(.+?)_\d+x\d+x\d+_\w+\.raw$', basename)
    if var_match:
        var_name = var_match.group(1)
    else:
        var_name = "volume"
    
    return {
        "filetype": FILETYPE,
        "variables": [var_name],
        "dimensions": {
            "grid": (nx, ny, nz)
        },
        "attributes": {
            "dtype": dtype_str,
            "shape": [nx, ny, nz],
            "filename": basename
        }
    }

def read_array(filepath, location):
    """
    Read the raw binary data as a numpy array.
    
    Args:
        filepath: Path to the .raw file
        location: Variable name (from inspect)
    
    Returns:
        numpy array with shape (nx, ny, nz)
    """
    import numpy as np
    
    # Get metadata to determine shape and dtype
    metadata = inspect(filepath)
    
    basename = os.path.basename(filepath)
    pattern = r'_(\d+)x(\d+)x(\d+)_(\w+)\.raw$'
    match = re.search(pattern, basename)
    
    nx, ny, nz, dtype_str = match.groups()
    nx, ny, nz = int(nx), int(ny), int(nz)
    dtype = np.dtype(dtype_str)
    
    # Read raw binary data
    data = np.fromfile(filepath, dtype=dtype)
    
    # Reshape to 3D grid
    data = data.reshape((nx, ny, nz))
    
    return data