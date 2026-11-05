# Case Study 3 (Test1), step 6: reproduce step 4 from its output. The spec below is the one the
# provenance tool recovered from outputs/s4_min16.vti (results/step6/recovered_spec.py), with the
# output renamed so the original is kept; a second save writes the same result as netCDF-4.
src = source("ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/TurbulentChannelFLow/u_10240x7680x1536_float64.raw")
inner = region(src, z=(1, 1535))
low = coarsen(inner, 16, how="min")
save(low, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s6_replay_min16.vti")
save(low, "/Users/agoosh11/Projects/VisLang/bench/PacificVis/cs3-channel-flow/Test1/outputs/s6_min16.nc")
