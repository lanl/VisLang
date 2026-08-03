# E3b: Threshold on a variable that does not exist (remote)
save(threshold(fields(source("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624"), ['x', 'y', 'z', 'rho']), "temperature > 1e5"), "/Users/ashrestha/Projects/VisLang/bench/results/out/e3b")
