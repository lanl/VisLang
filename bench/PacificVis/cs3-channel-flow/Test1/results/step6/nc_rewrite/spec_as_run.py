# Case Study 3 (Test1), step 6 (netCDF rewrite): the same netCDF-4 copy as step 6, written again
# after Sieve commits 2171fdd and the next (no CF axis attribute), which give netCDF-4 output coordinate variables for a placement in
# source cells. Same request as step 4, so it is served from the extent cache.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
low = coarsen(inner, 16, how="min")
save(low, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s6_min16.nc")
