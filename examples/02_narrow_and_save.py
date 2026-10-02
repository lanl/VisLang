# Several narrowings in a row, then write the result out.
#
# ORDER IS MEANING. Written this way, `subsample` samples the cells that
# survived the threshold. Swap the two lines and you instead threshold the
# sample — a different set of cells, and usually a different answer.
#
# Order is NOT execution, though. `fields` (projection) and `region` (crop) are
# structural, so the interpreter pushes them down into a single read; the
# threshold applies to what comes back. You describe the goal; it decides the
# lowering.
#
# `save` preserves the source format: this writes HDF5 because the source is
# HDF5 and the extension agrees.

src = source("/abs/path/simulation.hdf5")

cut = fields(src, ["temperature", "density"])
cut = region(cut, x=(100, 400), y=(100, 400))
cut = threshold(cut, "temperature > 500")
cut = subsample(cut, 2)

save(cut, "/abs/path/out/hot_core.hdf5")
