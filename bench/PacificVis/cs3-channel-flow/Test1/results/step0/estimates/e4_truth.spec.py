# Case Study 3 (Test1), step 0 dry run: e4_truth
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/dns_10240x7680x1536_float64.raw")
truth = region(src, x=(4608, 5632), y=(3328, 4352), z=(1, 65))   # 1024 x 1024 x 64 cells at the lower wall, domain centre, 16-aligned
save(truth, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s2_truth.vti")
