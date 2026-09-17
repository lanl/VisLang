"""Declaratively narrow a dataset before it is loaded.

`subset` is the DSL's only narrowing verb. It is metadata-only — it reads NO
bulk data (unlike `load`). It does two things:

  - Projection: *removes* the variables you don't want, by trimming the
    `variables` list directly.
  - Slicing: *records* a grid/particle selection policy in `selected_dimensions`
    for `load` to apply at read time. (Slicing can't be a removal — "stride to
    64 cells per axis" is a how-to-read directive, and for read-full formats
    like GenericIO the subsample happens during/after the read.)

Args:
    dataset_info: DatasetInfo from inspect() (or a prior subset()).
    variables:    keep only these (must be a subset of what's currently listed).
    dimensions:   slice policy, e.g.
                  {'particles': 0.1}                   # random 10% of particles
                  {'particles': 5000}                  # random 5000 particles
                  {'particles': slice(0, 1000)}        # first 1000
                  {'grid': 64}                         # stride to ~64 cells/axis
                  {'grid': 0.25}                        # keep ~25% of cells/axis

Returns:
    A NEW DatasetInfo; the input is left untouched. No data is read.

Re-subsetting narrows monotonically: once a field is dropped you re-subset from
the original inspected info to get it back. `load(info)` then materializes
exactly what the (narrowed) info describes.
"""

import copy

from vislang.formats.adapters import _resolve_variables, _validate_dimension_selection


def subset(dataset_info, variables=None, dimensions=None):
    # Shallow copy: we only rebind metadata attributes, never mutate the input
    # or touch its arrays. A subsequent load() makes its own deep copy.
    out = copy.copy(dataset_info)

    if variables is not None:
        # Validates the request is a subset of what's currently available, then
        # keeps only those — the rest are removed from the description.
        out.variables = _resolve_variables(out, variables)

    if dimensions is not None:
        _validate_dimension_selection(dimensions)
        # Provided-only merge so subset(..., variables=) and subset(..., dimensions=)
        # compose; later calls refine per key.
        out.selected_dimensions = {**(out.selected_dimensions or {}), **dimensions}

    return out
