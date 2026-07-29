data = source("ssh://darwin/projects/exasky/data/nyx/highz/512/NVB_C009_l10n512_S12345T692_z42.hdf5")

few = fields(data, [
    "native_fields/temperature",
    "native_fields/baryon_density"])
small = subsample(few, 2)                          # every 2nd voxel -> 256^3

save(small, "/Users/ashrestha/Projects/VisLang/saved_results/nyx_z42_sub2.hdf5")
