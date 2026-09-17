# MiraTitanU STEP279 -- halo 11521140891 in its neighbourhood.
#
# STAGE 2 of 2: render the cached region. Stage 1 (see git history) pulled a
# +/-15 Mpc/h box about the halo centre (525.0, 174.5, 781.4) out of the full
# GenericIO header and parked it here, so iterating on the look costs nothing.
#
# WHAT STAGE 1 LEARNED, for next time: reading all 32 partitions (~29 GB, 28
# min) instead of just #3 -- the one that owns this halo -- added 1,743 of
# 227,631 particles, 0.77%. HACC's rank decomposition is spatial, so a halo's
# neighbours almost all live in its own partition. #3 alone is a 1.15 GB, 21 s
# read. Prefer the owning partition; the header only earns its cost if you
# need a provably complete field.
#
# 227,631 points is under render.py's _POINT_WARN (1.5 M), so no subsample --
# every particle is drawn. render_points derives a `density` scalar from its
# own histogram and colours by it, which is what separates the core from the
# infalling material; the raw data here is positions and nothing else.
render(source(".vislang/halo_nbhd.npz"), cmap="inferno")
