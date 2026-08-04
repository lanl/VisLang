HACC = ("ssh://darwin/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.624")

snap = source(HACC)
snap = fields(snap, ["x", "y", "z", "rho"])
snap = subsample(snap, 3)
save(snap, "/Users/ashrestha/Projects/VisLang/forP/gio")
