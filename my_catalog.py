"""Local extent catalog — the "track what we have" half of remote compute.

Phase 1 of REMOTE_COMPUTE_PLAN.md. Every array piece we materialize from a
remote source (a narrowed field, a reduced slab) is written to a local cache
and indexed here, so the next request can be answered as `need - have = fetch`
and we never move the same bytes over the wire twice. The catalog is a plain
cache index: it records what is on local disk and computes what's missing; it
does not care how the missing pieces arrive (remote reducer, whole-file
fallback) — that logic lives in the planner.

Layout under a cache root (default ``vislang_cache``):
    catalog.json          manifest: schemas + extent index (atomic rewrite)
    extents/<hex>.npy     one numpy array per cached extent

Keys are (source_id, variable, canonical narrow_key). A narrow_key is either the
``{'forms': [...]}`` description the remote reducer emits (``_narrow_key``) or a
pre-fused ``{'grid_ranges': [...]}``. Beyond exact-key hits, `lookup` reuses a
cached *superset* slab when both the request and a cached extent are pure grid
crops/strides and the cached ranges contain and phase-align with the request —
slicing a bigger local array beats refetching. A forms key is fused to per-axis
ranges via the source's cached schema, reusing planner._grid_ranges (the same
routine the read path uses, so a cached slice matches a fresh reduce). Reuse is
deliberately conservative: a false miss only costs a slower fetch, a false hit is
wrong data.
"""

import hashlib
import json
import os

import numpy as np

_MANIFEST = "catalog.json"
_EXTENT_DIR = "extents"


def make_source_id(uri, size, mtime, header_hash=""):
    """Stable short identity for a remote source without hashing its bytes."""
    blob = f"{uri}|{size}|{mtime}|{header_hash}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _canon(narrow_key):
    """Canonical JSON for a narrow_key dict (sorted keys -> stable extent key)."""
    return json.dumps(narrow_key, sort_keys=True, separators=(",", ":"))


def _fuse_forms(forms, grid_dims):
    """Per-axis [start, stop, step] for a pure grid crop/stride `forms` list over
    a grid of shape `grid_dims`, or None when it is not grid-sliceable: a
    `threshold` makes the result value-dependent, particle data has no grid, and
    an unknown axis can't be placed. Delegates the fuse to planner._grid_ranges —
    the SAME routine the read path uses — so a cached slice is bit-identical to a
    fresh remote reduce (no drift between "what we key" and "what we read")."""
    if not grid_dims or forms is None:
        return None
    if any(f[0] not in ("region", "subsample") for f in forms):
        return None                              # threshold / non-geometric form
    from planner import _grid_ranges              # lazy: avoid an import cycle
    from dsl_forms.nodes import RegionNode, SubsampleNode
    regions, subs = [], []
    for f in forms:
        if f[0] == "region":
            regions.append(RegionNode(upstream=None,
                                      ranges=tuple((a, lo, hi) for a, lo, hi in f[1])))
        else:                                    # subsample
            subs.append(SubsampleNode(upstream=None, uniform=f[1],
                                      per_axis=tuple((a, fac) for a, fac in f[2])))
    if not (regions or subs):
        return None
    try:
        ranges = _grid_ranges(regions, subs, grid_dims)
    except (ValueError, KeyError, TypeError):
        return None                              # unknown axis etc. — not reusable
    return None if ranges is None else [[r.start, r.stop, r.step] for r in ranges]


def _grid_ranges_of(narrow_key, grid_dims):
    """The per-axis [start,stop,step] this narrow_key selects on a grid, or None
    (→ exact-match-only reuse). Accepts either a pre-fused ``{'grid_ranges': [...]}``
    key or the ``{'forms': [...]}`` key the remote reducer emits (fused here with
    `grid_dims`). Any other shape — a `post_ops`/`particles` key, or a `forms`
    list containing a threshold — is not a sliceable grid extent and yields None."""
    if not isinstance(narrow_key, dict):
        return None
    keys = set(narrow_key)
    if keys == {"grid_ranges"} and isinstance(narrow_key["grid_ranges"], (list, tuple)):
        return list(narrow_key["grid_ranges"])
    if keys == {"forms"}:
        return _fuse_forms(narrow_key["forms"], grid_dims)
    return None


def _axis_slice(req, had):
    """Slice (into the cached axis) serving req from had, or None.

    req/had are [start, stop, step] with None meaning full extent (start=0,
    stop=end-of-axis). Conservative: any case we can't prove is a miss.
    """
    if len(req) != 3 or len(had) != 3:
        return None
    a, b, k = req
    A, B, K = had
    a0 = 0 if a is None else a
    A0 = 0 if A is None else A
    k = 1 if k is None else k
    K = 1 if K is None else K
    ok_int = all(isinstance(v, int) and v >= 0 for v in (a0, A0))
    if not (ok_int and isinstance(k, int) and isinstance(K, int) and k >= 1 and K >= 1):
        return None
    if A0 > a0:
        return None                       # cached starts after the request
    if B is not None and (b is None or not isinstance(b, int) or b > B):
        return None                       # cached stop bounded; request isn't proven inside
    if K == 1:
        # cached is unstrided: any stride can be carved out, but only when the
        # request's phase lands on the cached origin (spec'd conservatism).
        if (a0 - A0) % k != 0:
            return None
        stop = None if b is None else max(b - A0, 0)
        return slice(a0 - A0, stop, k)
    if K == k:
        if A0 != a0:
            return None                   # same stride must share its phase exactly
        stop = None if b is None else -(-(b - a0) // k)   # ceil: element count
        return slice(0, stop, 1)
    return None


class ExtentCatalog:
    """JSON-manifest index over locally cached array extents."""

    def __init__(self, root="vislang_cache"):
        self.root = root
        self.extent_dir = os.path.join(root, _EXTENT_DIR)
        self.manifest_path = os.path.join(root, _MANIFEST)
        os.makedirs(self.extent_dir, exist_ok=True)
        self._manifest = self._load_manifest()

    # -- manifest persistence -------------------------------------------------

    def _load_manifest(self):
        """Read the manifest; a missing or corrupt one just starts empty."""
        try:
            with open(self.manifest_path) as f:
                m = json.load(f)
            if isinstance(m, dict) and isinstance(m.get("schemas"), dict) \
                    and isinstance(m.get("extents"), dict):
                return m
        except (OSError, ValueError):
            pass
        return {"version": 1, "schemas": {}, "extents": {}}

    def _save_manifest(self):
        """Atomic rewrite: never leave a half-written catalog.json behind."""
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._manifest, f, indent=1)
        os.replace(tmp, self.manifest_path)

    # -- schema ---------------------------------------------------------------

    def store_schema(self, source_id, schema):
        """schema = {"variables": [...], "dimensions": {...}, "positions": [...]|None}."""
        self._manifest["schemas"][source_id] = schema
        self._save_manifest()

    def schema(self, source_id):
        return self._manifest["schemas"].get(source_id)

    # -- extents --------------------------------------------------------------

    def store(self, source_id, var, narrow_key, arr):
        """Cache arr as the extent produced by narrow_key; overwrite same key."""
        key = _canon(narrow_key)
        hexid = hashlib.sha256(f"{source_id}\0{var}\0{key}".encode()).hexdigest()[:16]
        np.save(os.path.join(self.extent_dir, hexid + ".npy"), np.asarray(arr))
        entries = self._manifest["extents"].setdefault(source_id, {}).setdefault(var, [])
        entries[:] = [e for e in entries if e["key"] != key]
        entries.append({"key": key, "narrow": narrow_key, "file": hexid + ".npy",
                        "shape": list(np.asarray(arr).shape), "dtype": str(arr.dtype)})
        self._save_manifest()

    def _read(self, entry):
        try:
            return np.load(os.path.join(self.extent_dir, entry["file"]),
                           allow_pickle=False)
        except (OSError, ValueError):
            return None                   # manifest points at a lost file: miss

    def lookup(self, source_id, var, narrow_key):
        """Cached array for this exact request, or a slice of a containing
        pure-grid extent. None on any doubt — a miss is never wrong data.

        Grid superset reuse fuses each key's forms to per-axis ranges using the
        source's cached schema (grid shape); a key we can't fuse — a threshold,
        particle data, or a source with no cached schema — falls back to
        exact-match only."""
        entries = self._manifest["extents"].get(source_id, {}).get(var, [])
        key = _canon(narrow_key)
        for e in entries:
            if e["key"] == key:
                return self._read(e)
        grid = ((self.schema(source_id) or {}).get("dimensions") or {}).get("grid")
        req = _grid_ranges_of(narrow_key, grid)
        if req is None:
            return None
        for e in entries:
            had = _grid_ranges_of(e["narrow"], grid)
            if had is None or len(had) != len(req):
                continue
            slices = [_axis_slice(r, h) for r, h in zip(req, had)]
            if any(s is None for s in slices):
                continue
            arr = self._read(e)
            if arr is not None and arr.ndim == len(slices):
                return arr[tuple(slices)]
        return None

    def delta(self, source_id, variables, narrow_key):
        """Partition a request: ({var: cached array}, [vars we must fetch])."""
        have, missing = {}, []
        for var in variables:
            arr = self.lookup(source_id, var, narrow_key)
            if arr is None:
                missing.append(var)
            else:
                have[var] = arr
        return have, missing

    def invalidate(self, source_id):
        """Drop schema and all extents for a stale source (changed size/mtime)."""
        self._manifest["schemas"].pop(source_id, None)
        for entries in self._manifest["extents"].pop(source_id, {}).values():
            for e in entries:
                try:
                    os.remove(os.path.join(self.extent_dir, e["file"]))
                except OSError:
                    pass
        self._save_manifest()
