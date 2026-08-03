#!/usr/bin/env bash
# `sieve` launcher — the VisLang terminal CLI.
#
# The engine needs the `autoviz` conda env (h5py, yt, k3d, paramiko, …), so this
# shim picks that interpreter and runs cli.py from the repo, no matter where you
# invoke it from. Put this file (or a symlink to it) on your PATH:
#
#     ln -s "$(pwd)/sieve" ~/.local/bin/sieve
#
# Override the interpreter with VISLANG_PYTHON=/path/to/python if your env lives
# elsewhere; otherwise it falls back to the known autoviz path, then `conda run`.
set -euo pipefail

# Resolve symlinks to find the REPO, not the link. `~/.local/bin/sieve` is a link
# to this file, and $BASH_SOURCE is the link's own path — so following it is what
# makes the documented install above actually work. Hand-rolled rather than
# `readlink -f`, which is GNU-only (BSD/macOS readlink has no -f).
src="${BASH_SOURCE[0]}"
while [[ -L "$src" ]]; do
    dir="$(cd -P "$(dirname "$src")" && pwd)"
    src="$(readlink "$src")"
    [[ "$src" != /* ]] && src="$dir/$src"        # a relative link is relative to its dir
done
here="$(cd -P "$(dirname "$src")" && pwd)"

if [[ -n "${VISLANG_PYTHON:-}" ]]; then
    exec "$VISLANG_PYTHON" "$here/cli.py" "$@"
elif [[ -x "/opt/homebrew/Caskroom/miniconda/base/envs/autoviz/bin/python" ]]; then
    exec "/opt/homebrew/Caskroom/miniconda/base/envs/autoviz/bin/python" "$here/cli.py" "$@"
elif command -v conda >/dev/null 2>&1; then
    exec conda run --no-capture-output -n autoviz python "$here/cli.py" "$@"
else
    exec python3 "$here/cli.py" "$@"
fi
