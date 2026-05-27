import yt
from my_yt_loader import load_my_cosmology_data

ds = load_my_cosmology_data("/Users/ashrestha/Documents/cosmologyData/512/NVB_C009_l10n512_S12345T692_z42.hdf5")

# p = yt.ProjectionPlot(ds, 'z', ('gas', 'density'))


# yt.ProjectionPlot(
#     ds,                    # Dataset to visualize
#     'z',                   # Axis to project along ('x', 'y', or 'z')
#     ('gas', 'density')     # Field to plot (field_type, field_name)
# )

# p.save('dark_matter_projection.png')


# s = yt.SlicePlot(ds, 'z', ('gas', 'temperature'))

# s.save('temperature_slice.png')

# yt.PhasePlot(data_source, x_field, y_field, z_field)
# yt.ProfilePlot(data_source, x_field, y_field)


rc = yt.volume_render(ds, ('gas', 'density'))

