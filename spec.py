# Render HACC GenericIO particle data

info   = inspect("/projects/exasky/data/hacc/SCIDAC_RUNS/128MPC_RUNS_FLAMINGO_DESIGN_3A/FSN_0.5387_VEL_149.279_TEXP_9.613_BETA_0.8710_SEED_1.387e5/output/m000p.full.mpicosmo.567")
loaded = load(info, variables=['x', 'y', 'z', 'hh', 'mass'], dimensions={'particles': 0.1})
render(loaded)
