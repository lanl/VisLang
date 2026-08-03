# E1b: Narrowed snapshot — confirmed, executed
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ["native_fields/temperature", "native_fields/baryon_density"]), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_sub2.hdf5")
