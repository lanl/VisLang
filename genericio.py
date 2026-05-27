import os
os.environ['GENERICIO_NO_MPI'] = 'true'

import numpy as np
import pygio


# Use the BASE filename (without #N suffix)
filename = "/Users/ashrestha/Documents/cosmologyData/hacc/m000p.full.mpicosmo.567"

print("Inspecting GenericIO file...")
print("=" * 60)

# Get variable names
vars = pygio.read_variable_names(filename)
print(f"Variables: {vars}\n")

# Get total number of elements across all partitions
total = pygio.read_total_num_elems(filename)
print(f"Total particles: {total:,}\n")

# Get physical scale and origin
scale = pygio.read_phys_scale(filename)
origin = pygio.read_phys_origin(filename)
print(f"Physical scale: {scale}")
print(f"Physical origin: {origin}\n")

# Read actual data
print("Reading data from all partitions...")
data = pygio.read_genericio(filename)

for key, value in data.items():
    print(f"{key:15s}: shape={str(value.shape):20s} dtype={value.dtype}")


