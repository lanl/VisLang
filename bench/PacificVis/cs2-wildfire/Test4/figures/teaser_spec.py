src  = source("ssh://host/wildfire/")
view = timesteps(src, 9000, 45000)
view = fields(view,
              ["theta", "u", "v", "w"])
view = region(view, x=(-100, 200),
              y=(-120, 120), z=(0, 350),
              coords="world")
save(view, "box.vts")
