# The two specs Sieve ran for Case Study 2 (written by the ParaView plugin; paths shortened).
# Overview -- every timestep, five fields, stride 8 (Sieve Source, Timesteps empty):
src = source("ssh://gpu-server/…/mountain_headcurve80/")
view = fields(src, ["u", "v", "w", "theta", "rhof_1"])
view = subsample(view, 8)
save(view, "overview.vts")

# Detail -- the lee-slope box at full resolution over the chosen window (Sieve Detail):
view = timesteps(src, [4000] + list(range(9000, 46000, 1000)))    # as run: the 38 labels listed
view = fields(view, ["u", "v", "w", "theta"])
view = region(view, x=(-100, 200), y=(-120, 120), z=(0, 350), coords="world")
save(view, "detail.vts")
