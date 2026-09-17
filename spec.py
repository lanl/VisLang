# MiraTitanU STEP279 -- halo 11521140891 and its neighbourhood.
#
# Halo found by a fof_halo_tag census over partition #3: the most populous of
# its 29,500 halos, 132,621 particles centred (524.9862, 174.5147, 781.4316).
# Partitions are disjoint BY HALO (#3 and #11 share 0 tags), so #3 holds it whole.
#
# COST, measured: the data is on NFS4 (69.166.50.113:/eecs/research), not local
# to gpu-server, so reads run 17-90 MB/s depending on contention.
#   this spec, as written  : ~29 GB  -> 1624 s   (all 32 partitions)
#   src ...bighaloparticles#3 : ~1.15 GB ->   21 s   (yields 225,888 of the
#                                                    227,631 particles: 99.2%)
# Prefer #3. The header only earns its cost if you need a provably complete
# field. region() does NOT reduce the read -- it is a post-read bbox
# (planner.py:15) and pygio has no row-level partial read (adapters.py:313),
# so the box size is a look knob, not a cost knob. fields() is the real lever:
# 3 of 8 columns, 29 GB instead of 97 GB.
src = source(
    "ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/"
    "MiraTitanU/Grid/M000/L2100/HACC000/analysis/Halos/b0168/Halopart/"
    "STEP279/m000-279.bighaloparticles"
)

box = region(
    fields(src, ["x", "y", "z"]),
    x=(509.99, 539.99), y=(159.51, 189.51), z=(766.43, 796.43),
)

save(box, ".vislang/halo_nbhd.npz")

# Then render the cache (swap the sink above for this; 227,631 pts, ~1 s):
#   render(source(".vislang/halo_nbhd.npz"), cmap="inferno")
