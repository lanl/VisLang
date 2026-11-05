# Case Study 3 (Test1), step 5: where the reverse flow is -- the blocks of the block-minimum
# overview (step 4) whose minimum is below 0. The chain is step 4's, written the same way, plus a
# threshold after the block minimum, so the extent cache can serve it without reading the file.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
low = coarsen(inner, 16, how="min")
back = threshold(low, "u < 0")
save(back, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s5_backflow.vti")
