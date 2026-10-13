"""The format boundary: files in, `DatasetInfo` out.

This is the only layer that knows what a file format is. Everything downstream
reads `DatasetInfo` and is format-blind.

Readers are chosen by a trust ladder, never by guessing at bytes:

    1. an installed, trusted library (yt, h5py, astropy, GenericIO)
    2. a verified, frozen adapter in `generated_adapters/`
    3. headerless raw, and only via a size-checked filename convention
    4. otherwise raise `NeedsAdapterError` — do not improvise

Tiers 2 and 3 exist because a session model may *propose* a reader, but nothing
runs until a deterministic verifier has checked it against the real file and
frozen the result. See `instructions/soundness.md`.
"""
