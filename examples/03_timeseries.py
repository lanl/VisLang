# A folder is a time series.
#
# One file per timestep, named `…#N` where N is the step. The interpreter maps
# the rest of the chain over every selected step, and `timesteps` picks an
# inclusive range of N.
#
# `render` over a series is not supported — a viewer shows one thing. Either
# select a single timestep, or `save` the range, which writes one file per step.
#
# This works the same way for a remote folder: the interpreter detects it with a
# metadata-only `stat` over ssh, then reduces each timestep next to the data.

series = source("/abs/path/nyx_series/")

window = timesteps(series, 5, 12)
window = fields(window, ["density"])
window = subsample(window, 4)

save(window, "/abs/path/out/density_5_to_12.hdf5")
