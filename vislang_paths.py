"""One place that decides where VisLang's on-disk caches live.

Historically three caches scattered themselves across the cwd/repo:
`vislang_downloads/` (whole-file fetch fallback), `vislang_cache/` (the extent
catalog + transient pull staging), and `binding_cache/` (frozen HDF5 bindings).
Only one was gitignored, and the noisy fetch-fallback dir sat at the repo root.

Now everything lives under ONE gitignored root — `<repo>/.vislang/` by default,
or `$VISLANG_HOME` — with per-cache subdirs. The old per-cache env knobs still
win when set (back-compat), and a legacy top-level dir is migrated in once so a
populated catalog/binding cache isn't silently abandoned.
"""

import os

_ROOT = os.path.dirname(os.path.abspath(__file__))


def home():
    """Parent dir for all on-disk caches. `$VISLANG_HOME` overrides; the default
    `<repo>/.vislang` keeps everything in one gitignored place regardless of the
    process cwd (bindings must persist across cwds, as they did before)."""
    return os.environ.get("VISLANG_HOME") or os.path.join(_ROOT, ".vislang")


def _migrate(legacy, new):
    """Best-effort one-time move of a legacy top-level cache dir into the new
    consolidated home, so an existing populated catalog/binding cache survives
    the relocation. Silent on any failure — the cache simply rebuilds."""
    try:
        if legacy and os.path.isdir(legacy) and not os.path.exists(new):
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(legacy, new)
    except OSError:
        pass


def cache_root():
    """Extent-catalog root (+ transient pull staging). `$VISLANG_CACHE` wins for
    back-compat (tests set it); else `<home>/cache`, migrating a legacy
    `<repo>/vislang_cache` in once."""
    env = os.environ.get("VISLANG_CACHE")
    if env:
        return env
    new = os.path.join(home(), "cache")
    _migrate(os.path.join(_ROOT, "vislang_cache"), new)
    return new


def downloads_dir():
    """Whole-file fetch fallback staging. `$VISLANG_DOWNLOADS` overrides; else
    `<home>/downloads` (no more `vislang_downloads/` at the repo root). Kept
    persistent so `transfer`'s MD5-match skip still avoids re-downloads."""
    return os.environ.get("VISLANG_DOWNLOADS") or os.path.join(home(), "downloads")


def bindings_dir():
    """Frozen HDF5 semantic bindings. `$VISLANG_BINDING_CACHE` overrides; else
    `<home>/bindings`, migrating a legacy `<repo>/binding_cache` in once."""
    env = os.environ.get("VISLANG_BINDING_CACHE")
    if env:
        return env
    new = os.path.join(home(), "bindings")
    _migrate(os.path.join(_ROOT, "binding_cache"), new)
    return new
