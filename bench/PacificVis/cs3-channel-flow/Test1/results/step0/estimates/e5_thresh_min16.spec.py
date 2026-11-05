# Case Study 3 (Test1), step 0 dry run: e5_thresh_min16
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/dns_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
save(threshold(coarsen(inner, 16, how="min"), "dns < 0"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s4_backflow_blocks.vti")
