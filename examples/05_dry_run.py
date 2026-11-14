# A spec with no sink is a dry run.
#
# Nothing is read. You get the plan the interpreter inferred: which cuts fold
# into the read, which become post-read operations, the lowered narrowing, and
# the predicted cost. This is the cheapest way to find out that an axis name is
# wrong or a region is out of bounds — the static check runs against the real
# schema, so it fails in milliseconds instead of after a long load.
#
# Add `render(...)` or `save(...)` around the last expression when the plan
# looks right.

src = source("/abs/path/simulation.hdf5")

subsample(region(fields(src, ["temperature"]), x=(0, 256)), 4)
