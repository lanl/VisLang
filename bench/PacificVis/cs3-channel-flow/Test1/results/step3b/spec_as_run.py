# Case Study 3 (Test1), step 3b: the large-scale streamwise velocity at y+ ~ 100 (the height
# Lenaers et al. 2012 used), as 16 x 16-cell block means of one plane per wall:
# z = 81 (y+ 100.44, lower wall) and z = 1454 (y+ 100.44, upper wall).
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
save(coarsen(region(src, z=(81, 82)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s3b_lower_yp100.vti")
save(coarsen(region(src, z=(1454, 1455)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s3b_upper_yp100.vti")
