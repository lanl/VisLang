"""The sinks — the only forms that cause execution.

`render` is headless: there is no GPU or X server on a compute node, so the look
is fixed in the spec (cmap, opacity) and a k3d scene is served to the browser.
The lever for a cheap overview is `subsample` or `region` — never `compress`,
which is for fidelity-bounded storage, not for speed.

`save` preserves the source's format: the output path's extension wins if it is
one we can write (`.npz`, `.hdf5`, `.gio`), else the source's original format,
else npz with a note. Over a time series it writes one file per timestep.
"""
