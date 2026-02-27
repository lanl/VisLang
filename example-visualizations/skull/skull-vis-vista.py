import pyvista as pv
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

# Load volume from NRRD file
volume = pv.read('skull.nhdr')
print('Loaded volume with dimensions:', volume.dimensions)

# Opacity transfer function (tuned for bone-like structures)
# Maps scalar values to opacity
opacity = np.zeros(256)
# 0-50: fully transparent
# 50-80: ramp up to 0.05
for i in range(50, 80):
    opacity[i] = 0.05 * (i - 50) / (80 - 50)
# 80-120: ramp up to 0.3
for i in range(80, 120):
    opacity[i] = 0.05 + 0.25 * (i - 80) / (120 - 80)
# 120-255: ramp up to 0.8
for i in range(120, 256):
    opacity[i] = 0.3 + 0.5 * (i - 120) / (255 - 120)

# PyVista's apply_opacity assigns directly to uint8 lookup table when
# the list has exactly n_colors elements, so scale from 0.0-1.0 to 0-255.
opacity_mapping = (opacity * 255).astype(np.uint8).tolist()

# Set up the plotter for off-screen rendering
plotter = pv.Plotter(off_screen=True, window_size=(800, 800))
plotter.background_color = (0.1, 0.1, 0.1)

# Add volume with custom transfer functions
# PyVista's add_volume wraps VTK volume rendering
actor = plotter.add_volume(
    volume,
    scalars=volume.active_scalars_name,
    opacity=opacity_mapping,
    cmap=LinearSegmentedColormap.from_list('bone', [
        (0/255,   (0.0, 0.0, 0.0)),   # 0: black
        (80/255,  (0.9, 0.7, 0.6)),   # 80: warm bone
        (120/255, (1.0, 0.9, 0.8)),   # 120: light bone
        (255/255, (1.0, 1.0, 1.0)),   # 255: white
    ]),
    clim=[0, 255],
    shade=True,
    ambient=0.2,
    diffuse=0.9,
    specular=0.2,
    show_scalar_bar=False,
    mapper='gpu',
)

# Front-on view: look down the -X axis with Z up
plotter.view_yz(negative=True)

# Render and save to PNG
out_file = 'skull-vis-vista.png'
plotter.screenshot(out_file)
print('Saved volume render to', out_file)
