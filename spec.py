src = source(
    "pascalgrosset@darwin-fe.lanl.gov:/vast/projects/exasky/data/hacc/Argonne_L360_HACC001/STEP499/m000.full.mpicosmo.499"
)

# The 4 most massive FOF halos at STEP499 (by fof_halo_mass, from
# m000-499.fofproperties), each cropped to a fixed 10 Mpc/h box around its
# center.
boxes = region(
    fields(src, ["x", "y", "z", "phi"]),
    size=10,
    center={
        "halo1_762035800": (197.44525, 198.12784, 163.34323),
        "halo2_714849479": (187.2333, 206.40918, 47.364296),
        "halo3_202269393": (60.99268, 228.24454, 180.28488),
        "halo4_363914997": (97.8209, 8.511652, 173.9137),
    },
)

save(boxes, "/Users/pascalgrosset/projects/autonomous_vis/VisLang/.vislang/downloads/top4_halos.vtp")
