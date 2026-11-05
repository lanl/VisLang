# Baseline: the block-minimum overview, by hand on the server. The minimum of u over each
# 16 x 16 x 16 block of cells, wall planes left out (z = 1 .. 1534), read 16 planes at a time.
import sys
import numpy as np

path, out = sys.argv[1], sys.argv[2]
nx, ny, nz, b = 10240, 7680, 1536, 16
u = np.memmap(path, dtype="<f8", mode="r", shape=(nz, ny, nx))
z0, z1 = 1, 1535
rows = []
for z in range(z0, z1, b):
    slab = np.array(u[z:min(z + b, z1)])                            # up to 16 planes, ~10 GB
    rows.append(slab.min(axis=0).reshape(ny // b, b, nx // b, b).min(axis=(1, 3)))
np.save(out, np.stack(rows))                                        # (96, 480, 640): z, y, x
