import pyvista as pv
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

# Load volume from NRRD file
volume = pv.read('skull.nhdr')
print('Loaded volume with dimensions:', volume.dimensions)

# Transfer functions for bone-like structures, defined as
# (scalar_value, ...) control points interpolated over 0-255.
opacity_points = [
    #  scalar  opacity
    (    0,    0.0 ),
    (   50,    0.0 ),
    (   80,    0.05),
    (  120,    0.3 ),
    (  255,    0.8 ),
]

color_points = [
    #  scalar  R    G    B
    (    0,    0.0, 0.0, 0.0),  # black
    (   80,    0.9, 0.7, 0.6),  # warm bone
    (  120,    1.0, 0.9, 0.8),  # light bone
    (  255,    1.0, 1.0, 1.0),  # white
]

# Build opacity lookup: interpolate control points, then scale to uint8
# (PyVista assigns 256-element lists directly to its uint8 lookup table).
scalars = np.arange(256)
op_x = [p[0] for p in opacity_points]
op_y = [p[1] for p in opacity_points]
opacity = np.interp(scalars, op_x, op_y)
opacity_mapping = np.round(opacity * 255).astype(np.uint8).tolist()

# Build colormap from control points
cmap = LinearSegmentedColormap.from_list('bone', [
    (p[0] / 255, (p[1], p[2], p[3])) for p in color_points
])

# Set up the plotter for off-screen rendering
plotter = pv.Plotter(off_screen=True, window_size=(800, 800))
plotter.background_color = (0.1, 0.1, 0.1)

# Add volume with custom transfer functions
actor = plotter.add_volume(
    volume,
    scalars=volume.active_scalars_name,
    opacity=opacity_mapping,
    cmap=cmap,
    clim=[0, 255],
    shade=True,
    ambient=0.2,
    diffuse=0.9,
    specular=0.2,
    show_scalar_bar=False,
    mapper='gpu',
    opacity_unit_distance=1.0,
)
actor.prop.interpolation_type = 'linear'

# Front-on view: look down the -X axis with Z up
plotter.view_yz(negative=True)

# Render and save to PNG
out_file = 'skull-vis-vista.png'
plotter.screenshot(out_file)
print('Saved volume render to', out_file)
