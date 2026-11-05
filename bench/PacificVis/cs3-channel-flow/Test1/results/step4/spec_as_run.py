# Case Study 3 (Test1), step 4: the block-minimum overview -- each 16^3 block's minimum of u over
# the whole channel (wall planes left out). Reads every cell of the 966 GB file next to the data;
# returns the same 640 x 480 x 96 grid as the stride overview.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
low = coarsen(inner, 16, how="min")
save(low, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s4_min16.vti")
