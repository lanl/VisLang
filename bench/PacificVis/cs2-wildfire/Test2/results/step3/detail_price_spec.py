# Case Study 2 (Test2), step 3 -- price the Detail: the lee-slope box at full resolution,
# over the window #9000-#45000 plus #4000 (before ignition).
fire = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/wildfire-sl/mountain_headcurve80/")
window = timesteps(fire, [4000] + list(range(9000, 46000, 1000)))
wind = fields(window, ["u", "v", "w", "theta"])
lee = region(wind, x=(-100, 200), y=(-120, 120), z=(0, 350))
save(lee, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs2-wildfire/Test2/outputs/detail_dryrun.vts")
