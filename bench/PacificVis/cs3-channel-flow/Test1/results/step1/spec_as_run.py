# Case Study 3 (Test1), step 1: the stride overview -- every 16th cell of the channel,
# the two wall planes left out (u = 0 there to round-off).
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
quick = subsample(inner, 16)
save(quick, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s1_stride16.vti")
