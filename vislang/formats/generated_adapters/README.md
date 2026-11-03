# generated_adapters/

Readers written by the session model through the `NEEDS_ADAPTER` handshake and
frozen here by `submit_adapter` once they pass the conformance gate
(`llm_adapter.conform_and_freeze`), one `<ext>.py` per format. They are loaded
at start-up by `llm_adapter.load_cached_adapters` and tried after the built-in
readers. None ships with the repo: a format common enough to need one in every
checkout becomes a built-in reader in `adapters.py` (as npz and raw did).
