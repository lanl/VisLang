# Case Study 2 (wildfire) — Test3: the paper run again, positions sent once

Same study as Test2 (same data, overview, window, box, analysis); one change in Sieve:
commit 1e9155b — a fixed grid's positions cross the wire once per series (not once per
timestep), and the remote bundle is compressed. Expected from one Test2 timestep (scaled,
not measured): overview ~0.046 GB (Test2: 0.146), Detail ~0.36 GB (Test2: 1.08).

Run with a fresh cache, so nothing is served from Test2's:
    VISLANG_CACHE=/Users/agoosh11/Projects/VisLang/.vislang/cache-test3 \
        /Applications/ParaView-6.1.0-RC2.app/Contents/MacOS/paraview &
and load `paraview/cs2_test3_overview_start.pvsm`: Test2's step-1 state
(`../Test2/paraview/cs2_test2_step1_overview_state.pvsm`, unchanged) with only the
saved Status and Spec text blanked — Source over all 88 timesteps, Stride 8,
u v w theta rhof_1, no Detail; terrain, contours and camera kept.
