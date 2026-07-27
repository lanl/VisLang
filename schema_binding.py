"""Binding path for self-describing containers whose *semantics* are unknown.

For a custom HDF5 file the container is fully known — h5py reads any HDF5 and
its internal map tells us where every dataset's bytes live. What we don't know
is the *binding*: which datasets are variables, what the dimensions are, where
the metadata lives. That interpretation is the one good job for an LLM, and it
is verifiable without executing any generated code:

    1. h5py deterministically extracts the schema tree (paths, shapes, dtypes,
       attribute keys/values) — small text, no bulk data.
    2. Fingerprint the schema (structure, not sizes) -> a signature.
    3. Seen the signature -> reuse the frozen, verified binding (no model).
       Fresh -> the MCP `inspect` tool offers the schema tree to the SESSION
       model, which proposes a binding SPEC (declarative data, never code).
    4. Verify every claim in the spec against the file's own metadata (the
       `submit_binding` tool -> verify_and_freeze_binding). Pass -> freeze it
       keyed by the signature. Fail -> reject with the specific violation so the
       model can fix and resubmit.

No exec, no run-and-pray: the spec is declarative and the file's metadata is the
oracle. The model never reads the data and never cuts bytes; binding is optional
enrichment — HDF5 always has a working generic listing without it.
"""

import os
import json
import hashlib

from datasetInfo import DatasetInfo

def _bindings_dir():
    """Where frozen HDF5 bindings live — resolved lazily so a runtime env
    override (VISLANG_BINDING_CACHE / VISLANG_HOME) is honored. Consolidated
    under <repo>/.vislang/bindings by default (see vislang_paths)."""
    from vislang_paths import bindings_dir
    return bindings_dir()


# ---------------------------------------------------------------------------
# Schema extraction (deterministic, h5py)
# ---------------------------------------------------------------------------
def _attr_value(v):
    """Coerce an HDF5 attribute to a JSON-friendly value (small ones only).

    Order matters: a numpy byte scalar (np.bytes_) is BOTH np.generic and bytes,
    and `.item()` yields a Python `bytes` — so unwrap numpy first, then decode the
    result. Otherwise the raw bytes survive into json.dumps and crash it
    (e.g. a `format: b'nyx-lyaf'` attribute)."""
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            if v.size > 16:
                return f"<array {tuple(v.shape)} {v.dtype}>"
            v = v.tolist()
        elif isinstance(v, np.generic):
            v = v.item()
    except Exception:
        pass
    return _decode_bytes(v)


def _decode_bytes(v):
    """Recursively decode bytes (incl. inside lists from byte arrays) to str."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, (list, tuple)):
        return [_decode_bytes(x) for x in v]
    return v


def extract_schema(filepath):
    """Walk an HDF5 file and return its schema tree (no bulk data read)."""
    import h5py

    datasets = {}
    group_attrs = {}
    group_attr_values = {}

    with h5py.File(filepath, 'r') as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                datasets[name] = {
                    "shape": list(obj.shape),
                    "ndim": obj.ndim,
                    "dtype": str(obj.dtype),
                }
            elif isinstance(obj, h5py.Group) and len(obj.attrs):
                group_attrs[name] = sorted(obj.attrs.keys())
                group_attr_values[name] = {k: _attr_value(obj.attrs[k]) for k in obj.attrs}

        f.visititems(visit)
        if len(f.attrs):
            group_attrs["/"] = sorted(f.attrs.keys())
            group_attr_values["/"] = {k: _attr_value(f.attrs[k]) for k in f.attrs}

    return {
        "datasets": datasets,
        "group_attrs": group_attrs,
        "group_attr_values": group_attr_values,
    }


# ---------------------------------------------------------------------------
# Signature (single hash over a canonical, size-normalized schema)
# ---------------------------------------------------------------------------
def canonical_schema(schema):
    """Structure-only view used for the signature: dataset paths + dtype + ndim
    + TRAILING shape dims (the leading count axis is dropped, so different N
    hash the same), plus sorted group attribute keys."""
    items = []
    for path in sorted(schema["datasets"]):
        d = schema["datasets"][path]
        trailing = list(d["shape"][1:])  # drop leading count axis (data, not layout)
        items.append([path, d["dtype"], d["ndim"], trailing])
    attrs = {g: sorted(keys) for g, keys in sorted(schema["group_attrs"].items())}
    return {"datasets": items, "group_attrs": attrs}


def schema_signature(schema):
    blob = json.dumps(canonical_schema(schema), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Verification (the oracle — deterministic, never LLM-generated)
# ---------------------------------------------------------------------------
def verify_binding(binding, schema):
    """Check every claim in a binding against the file's own metadata.
    Raises ValueError on the first violation; returns True if sound."""
    if not isinstance(binding, dict):
        raise ValueError("binding must be an object")

    datasets = schema["datasets"]
    group_attrs = schema.get("group_attrs", {})

    dims = binding.get("dimensions", {})
    if not isinstance(dims, dict):
        raise ValueError("binding.dimensions must be an object")

    # Resolve each dimension's length from its source dataset.
    dim_lengths = {}
    for dname, dspec in dims.items():
        if not isinstance(dspec, dict) or "source" not in dspec:
            raise ValueError(f"dimension {dname!r} must specify a 'source' dataset")
        src = dspec["source"]
        if src not in datasets:
            raise ValueError(f"dimension {dname!r} source {src!r} is not a dataset in the file")
        axis = dspec.get("axis", 0)
        shape = datasets[src]["shape"]
        if not (0 <= axis < len(shape)):
            raise ValueError(f"dimension {dname!r} axis {axis} out of range for {src} shape {shape}")
        dim_lengths[dname] = shape[axis]

    variables = binding.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError("binding.variables must be a non-empty list")

    seen = set()
    for v in variables:
        if not isinstance(v, dict):
            raise ValueError(f"variable entry must be an object: {v!r}")
        name = v.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"variable name must be a non-empty string: {v!r}")
        if name in seen:
            raise ValueError(f"duplicate variable name {name!r}")
        seen.add(name)

        src = v.get("source")
        if src not in datasets:
            raise ValueError(f"variable {name!r} source {src!r} is not a dataset in the file")
        shape = datasets[src]["shape"]

        comp = v.get("component")
        if comp is not None:
            # A component selects a column of a 2-D dataset (e.g. (N,3) -> x/y/z).
            # 1-D has no column; 3-D+ is a grid variable, not a component source.
            if len(shape) != 2:
                raise ValueError(
                    f"variable {name!r} has a component but source {src} is "
                    f"{len(shape)}-D {shape}; component is only for 2-D datasets")
            if not (0 <= comp < shape[-1]):
                raise ValueError(f"variable {name!r} component {comp} out of range for {src} shape {shape}")

        dim = v.get("dim")
        if dim is not None:
            if dim not in dim_lengths:
                raise ValueError(f"variable {name!r} references unknown dimension {dim!r}")
            if len(shape) == 0:
                raise ValueError(
                    f"variable {name!r} has dimension {dim!r} but source {src} is 0-D (scalar)")
            if shape[0] != dim_lengths[dim]:
                raise ValueError(
                    f"variable {name!r} leading length {shape[0]} != dimension "
                    f"{dim!r} length {dim_lengths[dim]}")

    for g in binding.get("attributes_from", []):
        if g not in group_attrs:
            raise ValueError(f"attributes_from group {g!r} has no attributes / not found")

    return True


# ---------------------------------------------------------------------------
# Build a DatasetInfo from a verified binding
# ---------------------------------------------------------------------------
def build_info(filepath, schema, binding):
    datasets = schema["datasets"]

    dimensions = {}
    for dname, dspec in binding.get("dimensions", {}).items():
        src = dspec["source"]
        axis = dspec.get("axis", 0)
        dimensions[dname] = datasets[src]["shape"][axis]

    # Grid variables are multi-D (nx, ny, nz). region/subsample need the full
    # shape TUPLE — the convention every other adapter uses — not a single-axis
    # length, so expose it here (overriding any single-axis 'grid' set above).
    grid_shapes = {tuple(datasets[v["source"]]["shape"])
                   for v in binding["variables"]
                   if v.get("component") is None
                   and len(datasets[v["source"]]["shape"]) >= 3}
    if len(grid_shapes) == 1:
        dimensions["grid"] = grid_shapes.pop()

    variables = [v["name"] for v in binding["variables"]]

    attributes = {}
    attr_values = schema.get("group_attr_values", {})
    for g in binding.get("attributes_from", []):
        for k, val in attr_values.get(g, {}).items():
            attributes[k] = val

    # Per-variable element size (bytes) from the schema's recorded dtype strings —
    # metadata only; used by the cost estimator. Unresolvable dtypes are skipped
    # (the estimator falls back to a 4-byte assumption).
    import numpy as np
    itemsizes = {}
    for v in binding["variables"]:
        dt = datasets.get(v["source"], {}).get("dtype")
        if dt:
            try:
                itemsizes[v["name"]] = np.dtype(dt).itemsize
            except TypeError:
                pass

    info = DatasetInfo(filepath, "HDF5", variables,
                       dimensions=dimensions, attributes=attributes,
                       itemsizes=itemsizes)
    # Per-variable read token consumed by HDF5Adapter.read_array. This is the
    # single location mechanism the universal load() uses (generic HDF5 has no
    # entry and defaults to the variable name = dataset path).
    info.variable_locations = {
        v["name"]: {"source": v["source"],
                    "component": v.get("component"),
                    "dim": v.get("dim")}
        for v in binding["variables"]
    }
    info.binding = binding  # kept for provenance; load() reads variable_locations
    return info


# ---------------------------------------------------------------------------
# Binding cache (signature -> verified binding)
# ---------------------------------------------------------------------------
def _cache_path(sig):
    return os.path.join(_bindings_dir(), f"{sig}.json")


def load_cached_binding(sig):
    path = _cache_path(sig)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("binding")
    except Exception:
        return None


def save_cached_binding(sig, schema, binding):
    os.makedirs(_bindings_dir(), exist_ok=True)
    with open(_cache_path(sig), "w") as f:
        json.dump({"signature": sig,
                   "canonical_schema": canonical_schema(schema),
                   "binding": binding}, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Schema evidence + submit path for the session model (the binding handshake)
# ---------------------------------------------------------------------------


def _strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def format_schema(schema):
    """Compact text of the schema tree (dataset paths/shapes/dtypes + group
    attribute values) shown to the session model so it can propose a binding."""
    return json.dumps({
        "datasets": {p: {"shape": d["shape"], "dtype": d["dtype"]}
                     for p, d in schema["datasets"].items()},
        "group_attributes": schema.get("group_attr_values", schema.get("group_attrs", {})),
    }, indent=2, default=str)


def schema_evidence(filepath):
    """The HDF5 schema tree as compact text (metadata only, no bulk read), for
    the inspect binding-offer. Pairs with verify_and_freeze_binding()."""
    return format_schema(extract_schema(filepath))


def has_cached_binding(filepath):
    """True if a frozen, verified binding already exists for this file's schema
    signature (so inspect need not offer to create one)."""
    try:
        sig = schema_signature(extract_schema(filepath))
    except Exception:
        return False
    return load_cached_binding(sig) is not None


def verify_and_freeze_binding(filepath, binding, schema=None):
    """The `submit_binding` trust step: verify a session-model-proposed binding
    against the file's OWN schema (the deterministic oracle), freeze it keyed by
    the schema signature, and return the enriched DatasetInfo. `binding` may be a
    dict or a JSON string (fenced or bare). Raises ValueError on any violation —
    nothing is frozen — so the model can fix the binding and resubmit.

    `schema` may be supplied pre-extracted (the shipped schema tree of a REMOTE
    file); when None it is read locally via extract_schema(filepath). Bindings are
    keyed by a structure-only signature, so one frozen from a remote file's schema
    serves any local/remote file of the same schema. build_info never reads the
    file, so a remote `filepath` is fine here."""
    if isinstance(binding, str):
        try:
            binding = json.loads(_strip_fences(binding))
        except json.JSONDecodeError as e:
            raise ValueError(f"binding is not valid JSON: {e}")
    if schema is None:
        schema = extract_schema(filepath)
    verify_binding(binding, schema)                     # oracle: raises on violation
    save_cached_binding(schema_signature(schema), schema, binding)
    return build_info(filepath, schema, binding)


# ---------------------------------------------------------------------------
# Entry point used by HDF5Adapter.inspect
# ---------------------------------------------------------------------------
def bind_hdf5(filepath):
    """Return a richly-bound DatasetInfo from a FROZEN binding, or None to let
    the caller fall back to the generic flat listing.

    There is no automatic proposer anymore: a new binding is created only through
    the `submit_binding` handshake (verify_and_freeze_binding), where the session
    model proposes and the deterministic oracle verifies. Behavior here is always
    frozen-or-None — no model is ever called — so both the local planner and the
    remote reducer stay deterministic and name-consistent."""
    schema = extract_schema(filepath)
    binding = load_cached_binding(schema_signature(schema))
    if binding is None:
        return None
    # Re-verify even a cached binding against THIS file's schema (guards against
    # signature collisions or schema drift). Cheap and deterministic.
    verify_binding(binding, schema)
    return build_info(filepath, schema, binding)
