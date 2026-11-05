# Baseline: the stride overview, by hand on the server. Every 16th cell of the channel,
# wall planes left out (z = 1 .. 1534), written as .npy for copying back.
import sys
import numpy as np

path, out = sys.argv[1], sys.argv[2]
nx, ny, nz = 10240, 7680, 1536
u = np.memmap(path, dtype="<f8", mode="r", shape=(nz, ny, nx))   # file order: x fastest
np.save(out, np.array(u[1:1535:16, ::16, ::16]))
