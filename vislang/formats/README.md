# `formats/` — the format boundary

Files in, `DatasetInfo` out. This is the last layer that knows what a file
format is; everything downstream is format-blind.

| File | What it holds |
|---|---|
| `inspect.py` | `inspect_file` / `inspect_source` — schema only, no bulk read |
| `adapters.py` | the Tier-0 trusted readers (yt, HDF5, FITS, GenericIO) and `NeedsAdapterError` |
| `dataset_info.py` | `DatasetInfo`, the format-neutral schema every layer above reads |
| `llm_adapter.py` | the Tier-1 handshake: gather evidence, conform a proposed reader, freeze it |
| `schema_binding.py` | semantic bindings for HDF5 files that only expose a generic tree |
| `generated_adapters/` | frozen, verified adapter modules, loaded by path at run time |

## The rule

Readers are chosen by a trust ladder, never by inspecting bytes and hoping:

1. an installed, trusted library
2. a verified, frozen adapter from `generated_adapters/`
3. headerless raw — and only through a size-checked filename convention
4. otherwise raise `NeedsAdapterError`

Tiers 2 and 3 are why the handshake exists. A session model may *propose* a
reader or a binding; it never gets to bless one. `conform_and_freeze` runs the
proposal against the real file and `verify_binding` checks a proposed binding
against the file's own metadata. Only what passes is written down, and the run
path is **cache-only** — it reads frozen artifacts and errors if one is missing,
so nothing is generated mid-run.

Writing one: [`instructions/writing-adapters.md`](../../instructions/writing-adapters.md)
· [`instructions/writing-bindings.md`](../../instructions/writing-bindings.md)
· why it works this way: [`instructions/soundness.md`](../../instructions/soundness.md)
