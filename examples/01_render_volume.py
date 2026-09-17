# The smallest useful spec: look at a volume without loading all of it.
#
# `subsample(…, 2)` takes every second sample on every axis — an 8x reduction in
# what reaches the browser. Reach for this (or `region`) whenever a render feels
# slow; `compress` is the wrong lever, because it trades fidelity for size, not
# for time.
#
# The filename carries the shape and dtype. That convention is the ONLY way a
# headerless raw file is read — the size must match, or it is refused rather
# than guessed at.

render(
    subsample(source("/abs/path/heptane_302x302x302_uint8.raw"), 2),
    cmap="green",
)
