# MiraTitanU M000 HACC000, STEP499 big-halo particles, partition #3.
# 186.3M particles, 8 columns (~7.5 GB); stride 10 -> ~18.6M particles.
src = source(
    "ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/"
    "MiraTitanU/Grid/M000/L2100/HACC000/analysis/Halos/b0168/Halopart/"
    "STEP499/m000-499.bighaloparticles#3"
)

save(subsample(src, 10), "/Users/agoosh11/Projects/VisLang/unOrg/vts56.gio")
