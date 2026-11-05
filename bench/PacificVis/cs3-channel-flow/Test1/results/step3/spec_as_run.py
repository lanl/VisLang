# Case Study 3 (Test1), step 3: the ground-truth area's 16^3 block means and block minima --
# the same box as step 2, then coarsen. The box is cached from step 2, so both are expected to be
# computed locally from it, with nothing read next to the data.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
area = region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65))
save(coarsen(area, 16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s3_area_mean16.vti")
save(coarsen(area, 16, how="min"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s3_area_min16.vti")
