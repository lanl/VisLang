import os
os.environ['GENERICIO_NO_MPI'] = 'true'

import napari
import pygio
import numpy as np

# Load ALL 8 GenericIO files
base_filename = "/Users/ashrestha/Documents/cosmologyData/hacc/m000p.full.mpicosmo.567"

print("Loading all 8 files...")
all_positions = []
all_masses = []
all_uu = []

# Load the 8 numbered files
for i in range(8):
    filename = f"{base_filename}#{i}"
    print(f"Loading file {i+1}/8: {filename}")
    data = pygio.read_genericio(filename)
    
    positions = np.stack([data['x'], data['y'], data['z']], axis=1)
    all_positions.append(positions)
    all_masses.append(data['mass'])
    all_uu.append(data['uu'])
    
    print(f"  ✓ {len(positions):,} particles")

# Concatenate all data
print("\nCombining all files...")
positions = np.vstack(all_positions)
data = {
    'mass': np.concatenate(all_masses),
    'uu': np.concatenate(all_uu)
}

print(f"\nTOTAL PARTICLES: {len(positions):,}")
print(f"X range: [{positions[:, 0].min():.2f}, {positions[:, 0].max():.2f}]")
print(f"Y range: [{positions[:, 1].min():.2f}, {positions[:, 1].max():.2f}]")
print(f"Z range: [{positions[:, 2].min():.2f}, {positions[:, 2].max():.2f}]")

# Rest of your code stays exactly the same from here...
# Option 1: SUBSAMPLE for point cloud (napari gets slow with too many points)
subsample_factor = 30  # Use 1/100th of particles

indices = np.random.choice(len(positions), len(positions)//subsample_factor, replace=False)
points_subsample = positions[indices]
print(f"Subsampled to: {len(points_subsample):,} points")

# Option 2: CREATE DENSITY GRIDS (better for large datasets)
grid_size = 128  # 256^3 voxels

print("Creating density grids...")

# Particle density grid
hist_density, edges = np.histogramdd(
    positions, 
    bins=grid_size, 
    range=[[0, 128], [0, 128], [0, 128]]
)

# Mass-weighted density (use ALL particles, not just subsample)
hist_mass, _ = np.histogramdd(
    positions, 
    bins=grid_size,
    range=[[0, 128], [0, 128], [0, 128]],
    weights=data['mass']  # Use all masses
)

# Temperature grid (average in each voxel)
hist_temp, _ = np.histogramdd(
    positions,
    bins=grid_size,
    range=[[0, 128], [0, 128], [0, 128]],
    weights=data['uu']  # internal energy as proxy for temperature
)
# Avoid division by zero
hist_temp = np.divide(hist_temp, hist_density, where=hist_density>0)


print("\n" + "="*50)
print("SPATIAL ALIGNMENT CHECK")
print("="*50)

# Check particle positions
print(f"\n📍 PARTICLE DATA:")
print(f"  Shape: {positions.shape}")
print(f"  X: [{positions[:, 0].min():.2f}, {positions[:, 0].max():.2f}]")
print(f"  Y: [{positions[:, 1].min():.2f}, {positions[:, 1].max():.2f}]")
print(f"  Z: [{positions[:, 2].min():.2f}, {positions[:, 2].max():.2f}]")

# Check grid data
print(f"\n📊 GRID DATA:")
print(f"  Density shape: {hist_density.shape}")
print(f"  Internal Energy shape: {hist_temp.shape}")
print(f"  Grid bin edges:")
print(f"    X: [{edges[0][0]:.2f}, {edges[0][-1]:.2f}]")
print(f"    Y: [{edges[1][0]:.2f}, {edges[1][-1]:.2f}]")
print(f"    Z: [{edges[2][0]:.2f}, {edges[2][-1]:.2f}]")

# Check if particles are inside grid
in_bounds = np.all(
    (positions >= [edges[0][0], edges[1][0], edges[2][0]]) &
    (positions <= [edges[0][-1], edges[1][-1], edges[2][-1]]),
    axis=1
)
print(f"\n✓ Particles inside grid: {in_bounds.sum():,} / {len(positions):,} ({100*in_bounds.mean():.1f}%)")
print("="*50 + "\n")


print("Creating viewer...")
viewer = napari.Viewer()

# Add density grids (volumetric)
viewer.add_image(
    np.log10(hist_density + 1),  # log scale for better visualization
    name='Particle Density (log)',
    colormap='viridis',
    opacity=0.6
)

viewer.add_image(
    np.log10(hist_mass + 1),
    name='Mass Density (log)',
    colormap='inferno',
    opacity=0.6,
    visible=True
)

viewer.add_image(
    hist_temp,
    name='Internal Energy',
    colormap='plasma',
    opacity=0.6,
    visible=True
)

# Add point cloud (subsampled)
viewer.add_points(
    points_subsample,
    name=f'Particles ({len(points_subsample):,})',
    size=0.3,
    opacity=0.5,
    visible=True  # Start hidden, toggle on if needed
)

print("Visualization ready!")
napari.run()