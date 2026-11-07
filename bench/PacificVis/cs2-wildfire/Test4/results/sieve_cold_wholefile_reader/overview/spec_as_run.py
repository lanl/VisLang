# Step 1: the overview, every timestep, every 8th grid point along each axis, five fields.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/")
view = fields(src, ["rhof_1", "theta", "u", "v", "w"])
view = subsample(view, 8)
save(view, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/Test4/outputs/sieve/overview.vts")
