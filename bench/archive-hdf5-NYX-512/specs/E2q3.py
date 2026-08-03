# E2q3: Narrower time range (per-timestep reuse)
save(subsample(fields(timesteps(source("ssh://darwin/projects/autonomousvis/ashrestha/nyx_series"), 1, 1), ["native_fields/temperature", "native_fields/baryon_density"]), 4), "/Users/ashrestha/Projects/VisLang/bench/results/out/e2q3")
