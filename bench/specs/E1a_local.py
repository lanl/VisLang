# E1a_local: Whole-file fetch — cost gate holds
save(subsample(fields(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series/nyx512#1.hdf5"), ['native_fields/temperature', 'native_fields/baryon_density']), 2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e1_local_sub2.hdf5")
