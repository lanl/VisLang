# Case Study 2 -- the wind around a moving wildfire.
# The box follows the fire (theta > 400 K, padded 30 m; fire_roi_bounds.csv), widened for the
# wind by pad_boxes.py: 100 m upwind, 150 m downwind, 100 m each side, up to 650 m.
fire = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/")

steps = timesteps(fire, [30000, 60000, 91000])
wind = fields(steps, ["u", "v", "w", "theta", "rhof_1"])
around_fire = region(wind, bounds="bench/PacificVis/cs2-wildfire/Test1/data/wind_boxes.csv")

save(around_fire, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/Test1/outputs/around_fire.vts")
