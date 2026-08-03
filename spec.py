data = source("/Users/ashrestha/Projects/VisLang/saved_results/nyx_z42_sub2.hdf5")

dens = fields(data, ["native_fields/baryon_density"])       # 256^3, fits the browser budget
web = threshold(dens, "native_fields/baryon_density > 1.6")  # ~90th pct: carve the voids away

# Opacity keyed to where the data actually lives (render log10-scales the field,
# so the cosmic web sits at t~0.09-0.35 of the color range, not the top).
render(web, cmap="inferno", opacity=[
    0.00, 0.00,
    0.09, 0.02,
    0.15, 0.12,
    0.25, 0.35,
    0.40, 0.70,
    1.00, 1.00,
])
