# Case Study 3 (Test1), step 2: the ground-truth area at full resolution -- 1024 x 1024 cells of
# the lower wall at the domain centre (aligned to the 16-cell blocks), the first 64 levels
# (y+ 0.07 to 68). Placement fixed in DESIGN.md before any overview was seen.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
truth = region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65))
save(truth, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s2_truth.vti")
