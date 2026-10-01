#!/usr/bin/env bash
# Publish the user-facing subset of this repo into the Sieve release repo.
#
# One-way: VisLang (dev) -> Sieve (release); never merge back. What ships is
# listed in .publish-include. Only committed content ships (git archive of
# HEAD), so untracked scratch can't leak, and each release commit names the dev
# SHA it was built from. The release is staged and tested in a temp dir first;
# the Sieve repo is only touched once that passes.
#
#   tools/publish.sh [--target DIR] [--tag vX.Y] [--push] [--skip-tests] [--dry-run]
#
# --target defaults to $SIEVE_REPO, else ../Sieve (a separate git repo).
# --dry-run stages, checks, and tests, then lists what would ship.
set -euo pipefail

here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${SIEVE_REPO:-$here/../Sieve}"
tag="" push=0 tests=1 dry=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)     target="$2"; shift 2 ;;
        --tag)        tag="$2"; shift 2 ;;
        --push)       push=1; shift ;;
        --skip-tests) tests=0; shift ;;
        --dry-run)    dry=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$here"
if [[ $dry -eq 0 ]]; then
    [[ -z "$(git status --porcelain)" ]] \
        || { echo "dev tree has uncommitted changes; commit first" >&2; exit 1; }
    [[ -d "$target/.git" ]] \
        || { echo "no git repo at $target (git init it first)" >&2; exit 1; }
fi
sha="$(git rev-parse --short HEAD)"

# Parse the manifest: "path" ships as-is, "src -> dst" ships renamed.
paths=() renames=()
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    if [[ "$line" == *" -> "* ]]; then
        paths+=("${line%% -> *}"); renames+=("${line%% -> *}|${line##* -> }")
    else
        paths+=("$line")
    fi
done < .publish-include

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
git archive HEAD -- "${paths[@]}" | tar -x -C "$stage"
for r in ${renames[@]+"${renames[@]}"}; do
    src="${r%%|*}" dst="${r##*|}"
    mkdir -p "$stage/$(dirname "$dst")"
    mv "$stage/$src" "$stage/$dst"
done

# Refuse to ship dev-only references: personal paths, the AI dev notes.
leaks="${PUBLISH_LEAK_PATTERNS:-/Users/[A-Za-z0-9]|agoosh|CLAUDE\.md|\.claude/|unOrg}"
if grep -rInE "$leaks" "$stage"; then
    echo "refusing to publish: dev-only references above (patterns: $leaks)" >&2
    exit 1
fi

if [[ $tests -eq 1 ]]; then
    py="${VISLANG_PYTHON:-/opt/miniconda3/envs/vislang/bin/python}"
    # The tests are standalone scripts that fail by assertion.
    failed=()
    for t in "$stage"/tests/test_*.py; do
        (cd "$stage" && "$py" "$t" > /dev/null 2>&1) || failed+=("$(basename "$t")")
    done
    if [[ ${#failed[@]} -gt 0 ]]; then
        echo "failed in the release tree: ${failed[*]}; nothing published" >&2
        exit 1
    fi
    find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +
fi

if [[ $dry -eq 1 ]]; then
    (cd "$stage" && find . -type f -o -type l | sed 's|^\./||' | sort)
    echo "dry run: the files above would ship from dev@$sha" >&2
    exit 0
fi

rsync -a --delete --exclude=.git "$stage/" "$target/"
cd "$target"
git add -A
if git diff --cached --quiet; then
    echo "Sieve already matches dev@$sha; nothing to commit"
else
    git commit -q -m "Release ${tag:-update} (from VisLang@$sha)"
    echo "committed release from dev@$sha in $target"
fi
[[ -n "$tag" ]] && git tag -a "$tag" -m "Sieve $tag (from VisLang@$sha)"
if [[ $push -eq 1 ]]; then
    git push
    [[ -n "$tag" ]] && git push origin "$tag"
fi
