# Case Study 3 (Test1), step 0 dry run: e1_stride16
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/dns_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))            # leave out the two wall planes (u = 0 there)
save(subsample(inner, 16), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s1_stride16.vti")
