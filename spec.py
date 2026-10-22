series = source(
    "ssh://gpu-server//mnt/na1/eecs/research/harp/data/ashrestha/vislang/MiraTitanU/Grid/M000/L2100/HACC000/analysis/Halos/b0168/Halopart/STEP279/"
)

boxes = region(
    fields(timesteps(series, 3, 33), ["x", "y", "z", "id", "fof_halo_tag"]),
    size={"halo": 20, "field": 40},
    center={
        "halo": [
            # step   x      y      z    size
            (3,  525.0, 174.5, 781.4),          # 20 (tag size)
            (11, 525.0, 174.5, 781.4),          # 20
            (19, 525.0, 174.5, 781.4, 25),      # halo grows
            (27, 525.0, 174.5, 781.4, 30),
            (33, 525.0, 174.5, 781.4, 35),
        ],
        "field": [
            # step   x      y      z    size
            (3,  480.0, 150.0, 760.0),          # 40 (tag size)
            (11, 485.0, 150.0, 760.0),
            (19, 490.0, 150.0, 760.0),
            (27, 495.0, 150.0, 760.0),
            (33, 500.0, 150.0, 760.0),
        ],
    },
)

save(boxes, "/Users/agoosh11/Projects/VisLang/unOrg/tagged_regions/boxes.vtp")
