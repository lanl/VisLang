# MiraTitanU halo 11521140891 across two snapshots — a box that moves per step.
# Series = symlinks halo#N -> STEPN/m000-N.bighaloparticles#3 (one partition each).
# STEP279 centre from the fof_halo_tag census; STEP499 centre = densest 10 Mpc
# cell of the STEP499 #3 sample. Verified: 97.4% of the halo's particle IDs are
# in the STEP499 box, in one descendant halo (tag 11838708891).
series = source(
    "ssh://gpu-server//home.na1/ad.wsu.edu/aayush.shrestha/pvt/pvt/vislang_series/halo/"
)

box = region(
    fields(series, ["x", "y", "z", "id", "fof_halo_tag"]),
    center={279: (524.9862, 174.5147, 781.4316), 499: (523.0, 171.0, 783.0)},
    size=30,
)

save(box, "/Users/agoosh11/Projects/VisLang/unOrg/hacc/01_halo_track.npz")
