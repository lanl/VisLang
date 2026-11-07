# Step 3: the window and the box, at full resolution, four fields.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/")
view = timesteps(src, 9000, 45000)
view = fields(view, ["theta", "u", "v", "w"])
view = region(view, x=(-100, 200), y=(-120, 120), z=(0, 350), coords="world")
save(view, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/Test4/outputs/sieve/box.vts")
