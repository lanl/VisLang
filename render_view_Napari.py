import napari
import h5py
import numpy as np

# Open your HDF5 file
with h5py.File('/Users/ashrestha/Documents/cosmologyData/512/NVB_C009_l10n512_S12345T692_z42.hdf5', 'r') as f:
    
    # Load dark matter density
    dark_matter = f['native_fields']['dark_matter_density'][:]
    
    # Load baryon density
    baryon = f['native_fields']['baryon_density'][:]
    
    # Load temperature
    temperature = f['native_fields']['temperature'][:]

# Create viewer and add all layers
viewer = napari.Viewer()

viewer.add_image(dark_matter, name='Dark Matter Density', colormap='viridis', opacity=0.8)
viewer.add_image(baryon, name='Baryon Density', colormap='inferno', opacity=0.8, visible=False)
viewer.add_image(temperature, name='Temperature', colormap='plasma', opacity=0.8, visible=False)

napari.run()