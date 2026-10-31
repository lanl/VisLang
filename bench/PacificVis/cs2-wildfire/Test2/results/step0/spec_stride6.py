# Case Study 2 (Test2), step 0 -- price the overview: every timestep, five fields, stride 6.
fire = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/")
every = timesteps(fire, 1000, 91000)        # all 88
wind = fields(every, ["u", "v", "w", "theta", "rhof_1"])
coarse = subsample(wind, 6)
save(coarse, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/Test2/outputs/overview_s6.vts")
