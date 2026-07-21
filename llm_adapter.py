"""Tier-1 fallback: derive a reader for a file no registered adapter claims.

Reached only after the built-in readers (yt / HDF5 / FITS / GenericIO) and any
previously-frozen generated adapters all decline. Generation is a HANDSHAKE with
the session model — the LLM already driving this MCP session — so there is no
second API/model and no API key:

    1. adapters.get_adapter() gathers evidence about the file (name, extension,
       size, hex head/tail) and raises NeedsAdapterError(evidence) instead of
       calling out to a model.
    2. the MCP `inspect` tool surfaces that evidence to the session model, which
       reads instructions/writing-adapters.md and writes a small module with two
       functions only:
           inspect(filepath)              -> metadata dict
           read_array(filepath, location) -> one full numpy array
       It never writes load(): all selection/subsampling/orchestration is the
       framework's universal load below, shared by every generated adapter. The
       DatasetInfo is the format boundary — once inspect() fills it, downstream
       logic is format-blind.
    3. the model calls the `submit_adapter` tool -> conform_and_freeze() here:
       exec the module, run inspect() on the real file and validate the result,
       then read_array() on the first variable and check it returns real data.
    4. on success the module is frozen to generated_adapters/<ext>.py and
       registered, so the next file of this format skips the model entirely (it
       becomes Tier 0). On failure the violation is raised back so the model can
       fix it and resubmit — the old retry loop is now the conversation itself.

The model never hand-parses raw bytes — it only identifies the format and wires
up a trusted, installed reader library. Trust comes from the conformance run in
step 3, not from the model's say-so.

Security note: the submitted module runs via exec() in this process. Only use on
files/machines you trust.
"""

import os
import types

import numpy as np

from adapters import (
    FormatAdapter,
    DatasetInfo,
    register_generated_adapter,
    apply_selection,
)

HEADER_BYTES = 1024
TAIL_BYTES = 256
GENERATED_ADAPTERS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "generated_adapters")


def _say(msg):
    print(f"[VisLang] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Wrapping a generated module as a FormatAdapter
# ---------------------------------------------------------------------------
class GeneratedModuleAdapter(FormatAdapter):
    """Wraps a session-model-generated module (FILETYPE / EXTENSIONS / inspect /
    read_array) so it plugs into the same registry as hand-written adapters.

    load() here is UNIVERSAL: the generated code only knows how to pull one
    named array out of the file; variable resolution, particle subsampling,
    grid striding, and selection_info bookkeeping are framework code shared by
    every generated adapter.
    """

    def __init__(self, name, module, extensions):
        self.name = name
        self._module = module
        self._extensions = tuple(e.lower() for e in extensions if e)

    def can_handle(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        return bool(self._extensions) and ext in self._extensions

    def inspect(self, filepath):
        result = self._module.inspect(filepath)
        _validate_inspect(result)
        # filetype must be self.name (the registry key), not the module's raw
        # FILETYPE string — load() routes back via get_adapter_for_info().
        info = DatasetInfo(
            filepath, self.name, list(result["variables"]),
            dimensions=dict(result.get("dimensions", {}) or {}),
            attributes=dict(result.get("attributes", {}) or {}),
        )
        # Optional map var name -> how the library addresses it (defaults to
        # the name itself). Survives my_load's deepcopy like binding does.
        info.variable_locations = dict(result.get("variable_locations", {}) or {})
        return info

    def read_array(self, filepath, location, selection):
        # The generated module's read_array(filepath, location) returns the FULL
        # array by contract (no slicing — the framework owns selection). Apply
        # the selection here via the shared fallback.
        arr = self._module.read_array(filepath, location)
        return apply_selection(arr, selection)


# ---------------------------------------------------------------------------
# Conformance checks (hand-written, never generated) — the trust boundary
# ---------------------------------------------------------------------------
def _validate_inspect(result):
    if not isinstance(result, dict):
        raise ValueError(f"inspect() must return a dict, got {type(result).__name__}")
    for key in ("filetype", "variables", "dimensions", "attributes"):
        if key not in result:
            raise ValueError(f"inspect() result is missing the '{key}' key")
    if not isinstance(result["filetype"], str) or not result["filetype"].strip():
        raise ValueError("'filetype' must be a non-empty string")
    variables = result["variables"]
    if not isinstance(variables, (list, tuple)) or len(variables) == 0:
        raise ValueError("'variables' must be a non-empty list of names")
    if not all(isinstance(v, str) for v in variables):
        raise ValueError("'variables' must contain only strings")
    if not isinstance(result["dimensions"], dict):
        raise ValueError("'dimensions' must be a dict")
    if not isinstance(result["attributes"], dict):
        raise ValueError("'attributes' must be a dict")
    locations = result.get("variable_locations")
    if locations is not None:
        if not isinstance(locations, dict):
            raise ValueError("'variable_locations' must be a dict if present")
        unknown = set(locations) - set(variables)
        if unknown:
            raise ValueError(f"'variable_locations' has keys that are not variables: {unknown}")


def _check_read_array(mod, filepath, result):
    """Behavioral check: the generated read_array must return real data for
    the first declared variable."""
    var = result["variables"][0]
    location = (result.get("variable_locations") or {}).get(var, var)
    arr = np.asarray(mod.read_array(filepath, location))
    if arr.size == 0:
        raise ValueError(f"read_array({var!r}) returned an empty array")
    if arr.ndim == 0:
        raise ValueError(
            f"read_array({var!r}) returned a 0-d scalar; scalars belong in "
            f"'attributes', not 'variables'")
    return var, arr


# ---------------------------------------------------------------------------
# Evidence shown to the session model (via the inspect handshake)
# ---------------------------------------------------------------------------
def gather_adapter_evidence(filepath):
    """A text block of format clues for the session model: filename, extension,
    size, and a hex dump of the head (and tail, for large files). Metadata only —
    no bulk read. Surfaced by adapters.get_adapter through NeedsAdapterError."""
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(HEADER_BYTES)
        if size > HEADER_BYTES + TAIL_BYTES:
            f.seek(-TAIL_BYTES, os.SEEK_END)
            tail = f.read(TAIL_BYTES)
        else:
            tail = b""

    def hexdump(data, base=0):
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{base + i:08x}  {hexpart:<47}  |{asciipart}|")
        return "\n".join(lines)

    parts = [
        f"Filename: {os.path.basename(filepath)}",
        f"Extension: {os.path.splitext(filepath)[1] or '(none)'}",
        f"File size: {size} bytes",
        "",
        f"Hex dump of first {len(head)} bytes:",
        hexdump(head),
    ]
    if tail:
        parts += ["", f"Hex dump of last {len(tail)} bytes:",
                  hexdump(tail, base=size - len(tail))]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module handling: strip fences, exec, cache path, register
# ---------------------------------------------------------------------------
def _strip_fences(code):
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code


def _exec_module(code, modname):
    mod = types.ModuleType(modname)
    exec(compile(code, f"<{modname}>", "exec"), mod.__dict__)
    if not callable(getattr(mod, "inspect", None)):
        raise ValueError("generated module does not define inspect(filepath)")
    if not callable(getattr(mod, "read_array", None)):
        raise ValueError("generated module does not define read_array(filepath, location)")
    return mod


def _cache_path_for(filepath):
    ext = os.path.splitext(filepath)[1].lower().lstrip(".") or "noext"
    return os.path.join(GENERATED_ADAPTERS_DIR, f"{ext}.py")


def _wrap_and_register(mod, fallback_ext=None):
    import adapters as _adapters

    filetype = getattr(mod, "FILETYPE", None) or "LLMGenerated"
    exts = list(getattr(mod, "EXTENSIONS", []) or [])
    # The extension that actually produced this adapter is canonical — always
    # claim it, even if the module's EXTENSIONS list disagrees/omits it.
    if fallback_ext and fallback_ext not in exts:
        exts.append(fallback_ext)

    # Unique registry name: two formats claiming the same FILETYPE must not
    # silently shadow each other (the name is also the load() routing key).
    name = f"{filetype} (LLM)"
    if name in _adapters._GENERATED_BY_NAME and fallback_ext:
        name = f"{filetype}{fallback_ext} (LLM)"

    adapter = GeneratedModuleAdapter(name, mod, exts)
    register_generated_adapter(adapter)
    return adapter


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def load_cached_adapters():
    """Register any previously-frozen generated adapters. No LLM/API needed."""
    if not os.path.isdir(GENERATED_ADAPTERS_DIR):
        return
    for fname in sorted(os.listdir(GENERATED_ADAPTERS_DIR)):  # deterministic order
        if not fname.endswith(".py"):
            continue
        path = os.path.join(GENERATED_ADAPTERS_DIR, fname)
        try:
            with open(path) as f:
                mod = _exec_module(f.read(), f"vislang_gen_{fname[:-3]}")
            adapter = _wrap_and_register(mod, fallback_ext="." + fname[:-3])
            _say(f"Loaded frozen adapter {adapter.name!r} from {path}")
        except Exception as e:
            _say(f"Skipping cached adapter {path}: {type(e).__name__}: {e} "
                 f"(delete it to regenerate)")
            continue


def conform_and_freeze(filepath, module_code):
    """Validate a session-model-proposed adapter module against the real file,
    then freeze + register it. This is the single trust step of the handshake
    (called by the `submit_adapter` MCP tool):

      1. exec the module (must define inspect + read_array),
      2. run inspect() on the real file and structurally validate the result,
      3. run read_array() on the first variable and check it returns real data,
      4. freeze the source to generated_adapters/<ext>.py and register it.

    Returns a report dict describing what was frozen. Raises (ValueError or the
    generated code's own exception) with a clear message on ANY conformance
    failure, so the caller can hand the violation back to the model for a fix —
    nothing is frozen unless it passed against the real file.
    """
    code = _strip_fences(module_code)
    mod = _exec_module(code, "vislang_gen_candidate")

    # Conformance against the real file (the trust step).
    result = mod.inspect(filepath)
    _validate_inspect(result)
    var, arr = _check_read_array(mod, filepath, result)

    # Success — freeze the source and register the adapter (Tier 0 hereafter).
    os.makedirs(GENERATED_ADAPTERS_DIR, exist_ok=True)
    cache_path = _cache_path_for(filepath)
    with open(cache_path, "w") as f:
        f.write(code)
    fallback_ext = os.path.splitext(filepath)[1].lower() or None
    adapter = _wrap_and_register(mod, fallback_ext=fallback_ext)
    _say(f"✓ adapter {adapter.name!r} validated and frozen to {cache_path}.")

    return {
        "adapter_name": adapter.name,
        "filetype": getattr(mod, "FILETYPE", None),
        "extensions": list(getattr(mod, "EXTENSIONS", []) or []),
        "variables": list(result["variables"]),
        "dimensions": dict(result.get("dimensions", {}) or {}),
        "cache_path": cache_path,
        "checked_variable": var,
        "checked_shape": list(arr.shape),
        "checked_dtype": str(arr.dtype),
    }
