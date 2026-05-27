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
    print(f"\n{'='*60}")
    print(f"📁 Loading file {i+1}/8: {filename}")
    data = pygio.read_genericio(filename)
    
    positions = np.stack([data['x'], data['y'], data['z']], axis=1)
    all_positions.append(positions)
    all_masses.append(data['mass'])
    all_uu.append(data['uu'])
    
    print(f"  Particles: {len(positions):,}")
    print(f"  X range: [{positions[:, 0].min():.2f}, {positions[:, 0].max():.2f}]")
    print(f"  Y range: [{positions[:, 1].min():.2f}, {positions[:, 1].max():.2f}]")
    print(f"  Z range: [{positions[:, 2].min():.2f}, {positions[:, 2].max():.2f}]")

# Concatenate all data
print("\n" + "="*60)
print("🔗 COMBINING ALL FILES...")
print("="*60)
positions = np.vstack(all_positions)
data = {
    'mass': np.concatenate(all_masses),
    'uu': np.concatenate(all_uu)
}

print(f"\n✅ TOTAL PARTICLES: {len(positions):,}")
print(f"📏 COMBINED RANGE:")
print(f"  X: [{positions[:, 0].min():.2f}, {positions[:, 0].max():.2f}]")
print(f"  Y: [{positions[:, 1].min():.2f}, {positions[:, 1].max():.2f}]")
print(f"  Z: [{positions[:, 2].min():.2f}, {positions[:, 2].max():.2f}]")

# SUBSAMPLE
print("\n" + "="*60)
print("🎲 CREATING SUBSAMPLE...")
print("="*60)
subsample_factor = 100  # Use 1/100th of particles

indices = np.random.choice(len(positions), len(positions)//subsample_factor, replace=False)
points_subsample = positions[indices]

print(f"Subsample size: {len(points_subsample):,} points")
print(f"📏 SUBSAMPLE RANGE:")
print(f"  X: [{points_subsample[:, 0].min():.2f}, {points_subsample[:, 0].max():.2f}]")
print(f"  Y: [{points_subsample[:, 1].min():.2f}, {points_subsample[:, 1].max():.2f}]")
print(f"  Z: [{points_subsample[:, 2].min():.2f}, {points_subsample[:, 2].max():.2f}]")

# Check distribution across octants
print(f"\n🗺️  SUBSAMPLE SPATIAL DISTRIBUTION:")
for axis_name, axis_idx in [('X', 0), ('Y', 1), ('Z', 2)]:
    low = (points_subsample[:, axis_idx] < 64).sum()
    high = (points_subsample[:, axis_idx] >= 64).sum()
    print(f"  {axis_name}: [0-64): {low:,} | [64-128]: {high:,}")

# CREATE DENSITY GRIDS
grid_size = 256  # 256^3 voxels

print("\n" + "="*60)
print("📊 CREATING DENSITY GRIDS...")
print("="*60)

# Particle density grid
hist_density, edges = np.histogramdd(
    positions, 
    bins=grid_size, 
    range=[[0, 128], [0, 128], [0, 128]]
)

print(f"Density grid: {hist_density.shape}")
print(f"Non-zero voxels: {(hist_density > 0).sum():,} / {hist_density.size:,}")
print(f"Max density: {hist_density.max():.0f} particles/voxel")

# Mass-weighted density
hist_mass, _ = np.histogramdd(
    positions, 
    bins=grid_size,
    range=[[0, 128], [0, 128], [0, 128]],
    weights=data['mass']
)

# Temperature grid
hist_temp, _ = np.histogramdd(
    positions,
    bins=grid_size,
    range=[[0, 128], [0, 128], [0, 128]],
    weights=data['uu']
)
hist_temp = np.divide(hist_temp, hist_density, where=hist_density>0)

print("\n" + "="*60)
print("🔍 SPATIAL ALIGNMENT CHECK")
print("="*60)

print(f"\n📍 FULL PARTICLE DATA:")
print(f"  Shape: {positions.shape}")
print(f"  X: [{positions[:, 0].min():.2f}, {positions[:, 0].max():.2f}]")
print(f"  Y: [{positions[:, 1].min():.2f}, {positions[:, 1].max():.2f}]")
print(f"  Z: [{positions[:, 2].min():.2f}, {positions[:, 2].max():.2f}]")

print(f"\n📊 GRID DATA:")
print(f"  Density shape: {hist_density.shape}")
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
print("="*60 + "\n")

print("Creating viewer...")
# ============================================================
# COORDINATE SCALING FOR NAPARI
# ============================================================
print("\n" + "="*60)
print("🔧 SCALING POINTS TO MATCH GRID COORDINATES")
print("="*60)

# Scale points from physical [0,128] to voxel [0,256] coordinates
scale_factor = grid_size / 128.0  # 256 / 128 = 2.0
points_subsample_scaled = points_subsample * scale_factor

print(f"Scale factor: {scale_factor}")
print(f"Original physical range: [0, 128]")
print(f"Scaled voxel range: [0, {128*scale_factor}]")
print(f"\n📏 SCALED SUBSAMPLE RANGE:")
print(f"  X: [{points_subsample_scaled[:, 0].min():.1f}, {points_subsample_scaled[:, 0].max():.1f}]")
print(f"  Y: [{points_subsample_scaled[:, 1].min():.1f}, {points_subsample_scaled[:, 1].max():.1f}]")
print(f"  Z: [{points_subsample_scaled[:, 2].min():.1f}, {points_subsample_scaled[:, 2].max():.1f}]")
print("="*60 + "\n")

# ============================================================
# CREATE NAPARI VIEWER
# ============================================================
print("Creating viewer...")
viewer = napari.Viewer()

# Add density grids (volumetric)
viewer.add_image(
    np.log10(hist_density + 1),
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

# Add SCALED point cloud
viewer.add_points(
    points_subsample_scaled,  # ← SCALED VERSION!
    name=f'Particles ({len(points_subsample):,})',
    size=1.0,
    opacity=0.8,
    face_color='white',
    visible=True
)

print("✅ Visualization ready!")
print("The particles should now align with the density grids!\n")
napari.run()