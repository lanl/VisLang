# E3c: Per-axis subsample on point data (remote)
save(subsample(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), x=2), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3c")
