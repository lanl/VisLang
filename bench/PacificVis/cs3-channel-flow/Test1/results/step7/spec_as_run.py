# Case Study 3 (Test1), step 7 (post hoc, added after step 4's result): the large-scale field at two
# more heights, as 16 x 16-cell block means of one plane per wall, the same request as step 3b.
#   y+ 281 (z = 147 / 1388): Mathis et al. (2009)'s outer site, fixed in the design beforehand;
#   y+ 519 (z = 207 / 1328): y = 0.1 delta, Lenaers et al. (2012)'s height (y+ 100 at Re_tau 1000) in outer units.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
save(coarsen(region(src, z=(147, 148)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s7_lower_yp281.vti")
save(coarsen(region(src, z=(1388, 1389)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s7_upper_yp281.vti")
save(coarsen(region(src, z=(207, 208)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s7_lower_yp519.vti")
save(coarsen(region(src, z=(1328, 1329)), x=16, y=16, how="mean"), "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s7_upper_yp519.vti")
