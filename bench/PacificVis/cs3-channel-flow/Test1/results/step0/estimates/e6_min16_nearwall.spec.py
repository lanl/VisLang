# Case Study 3 (Test1), step 0 dry run: e6_min16_nearwall
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/dns_10240x7680x1536_float64.raw")
lower = region(src, z=(1, 17))               # the first 16 levels above the lower wall (y+ 0.07-8.1)
save(coarsen(lower, 16, how="min"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s3_min16_lowerwall.vti")
