# MiraTitanU STEP499 big-halo particles, one GenericIO partition (188.3 M rows).
# No fields(...): every variable is kept, so render lays down one point layer per
# scalar (vx, vy, vz, id, fof_halo_tag) over the shared x/y/z positions.
# stride 1000 -> ~188 k points: enough to read the structure, light for WebGL.
# Sourced from the local whole-file fetch of
#   ssh://gpu-server/mnt/na1/.../STEP499/m000-499.bighaloparticles#1539
# because a REMOTE run needs pygio in the remote env, which it lacks today
# (remote reduce -> ModuleNotFoundError: pygio). Same bytes, no ssh dependence.
src = source(
    "/Users/agoosh11/Projects/VisLang/.vislang/downloads/"
    "m000-499.bighaloparticles#1539"
)

render(subsample(src, 1000))
