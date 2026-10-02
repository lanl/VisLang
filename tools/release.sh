#!/usr/bin/env bash
# Publish the user-facing subset of this repo to the Sieve branch.
#
# One-way: dev -> Sieve; never merge back. What ships is listed in
# release/manifest. Only committed content ships (git archive of HEAD), so
# untracked scratch can't leak, and each release commit names the dev SHA it was
# built from. Sieve's history holds only release commits.
#
# Documentation is written for users separately: every .md that ships comes from
# release/overlay/<same path>, or ships EMPTY as a placeholder marking where a
# user-facing doc is needed. A README.md and CLAUDE.md placeholder always ship.
# Non-.md files in release/overlay/ (e.g. .gitignore) replace the dev ones.
#
#   tools/release.sh [--dry-run] [--fresh] [--push] [--tag vX.Y] [--skip-tests]
#
# --dry-run   stage, test, and list what would ship; touch nothing.
# --fresh     start Sieve over as a single history-free commit (the first
#             release, or one that replaces a branch this script didn't make).
#             Replacing it needs a force-push; it's leased to the tip seen here.
# --push      push the release commit (and tag) to $SIEVE_REMOTE/$SIEVE_BRANCH.
#             Without it, the commit is built and its SHA printed for review.
#
# SIEVE_REMOTE defaults to origin, SIEVE_BRANCH to Sieve.
set -euo pipefail

here="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote="${SIEVE_REMOTE:-origin}" branch="${SIEVE_BRANCH:-Sieve}"
tag="" push=0 tests=1 dry=0 fresh=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    dry=1; shift ;;
        --fresh)      fresh=1; shift ;;
        --push)       push=1; shift ;;
        --tag)        tag="$2"; shift 2 ;;
        --skip-tests) tests=0; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "$here"
# Porcelain lists untracked files too, so a clean tree means the manifest and
# overlay read below are exactly the committed ones.
if [[ $dry -eq 0 && -n "$(git status --porcelain)" ]]; then
    echo "dev tree has uncommitted changes; commit first (or use --dry-run)" >&2
    exit 1
fi
sha="$(git rev-parse --short HEAD)"

paths=()
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -n "$line" ]] && paths+=("$line")
done < release/manifest

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stage="$work/tree"
mkdir "$stage"

# tests/ comes along only to check the release tree, then is dropped.
git archive HEAD -- "${paths[@]}" tests | tar -x -C "$stage"

if [[ $tests -eq 1 ]]; then
    py="${VISLANG_PYTHON:-/opt/miniconda3/envs/vislang/bin/python}"
    # Test it as users get it: a git checkout (provenance records the commit).
    git -C "$stage" init -q
    git -C "$stage" add -A
    git -C "$stage" -c user.name=release -c user.email=release@localhost commit -qm stage
    # Test datasets are gitignored in dev; link them in for the run only.
    for f in "$here"/tests/*.raw; do
        [[ -e "$f" ]] && ln -s "$f" "$stage/tests/"
    done
    # The tests are standalone scripts that fail by assertion.
    failed=()
    for t in "$stage"/tests/test_*.py; do
        (cd "$stage" && "$py" "$t" > /dev/null 2>&1) || failed+=("$(basename "$t")")
    done
    rm -rf "$stage/.git"
    if [[ ${#failed[@]} -gt 0 ]]; then
        echo "failed in the release tree: ${failed[*]}; nothing published" >&2
        exit 1
    fi
fi
rm -rf "$stage/tests"
find "$stage" -name __pycache__ -type d -prune -exec rm -rf {} +

# Dev docs never ship: empty every .md, then lay the user-facing overlay on top.
find "$stage" -name '*.md' -type f -exec sh -c ': > "$1"' _ {} \;
: > "$stage/README.md"
: > "$stage/CLAUDE.md"
[[ -d release/overlay ]] && cp -R release/overlay/. "$stage/"

# Refuse to ship dev-only references: personal paths, dev-only folders, papers.
leaks="${RELEASE_LEAK_PATTERNS:-/Users/[A-Za-z0-9]|agoosh|unOrg|bench/|prov-ex|paper/|PacificVis}"
if grep -rInE "$leaks" "$stage"; then
    echo "refusing to publish: dev-only references above (patterns: $leaks)" >&2
    exit 1
fi

if [[ $dry -eq 1 ]]; then
    (cd "$stage" && find . \( -type f -o -type l \) | sed 's|^\./||' | sort |
        while IFS= read -r f; do
            if [[ "$f" == *.md && ! -s "$f" ]]; then echo "$f  (empty)"; else echo "$f"; fi
        done)
    echo "dry run: the files above would ship to $remote/$branch from dev@$sha" >&2
    exit 0
fi

# Build the release commit from the staged tree with a private index, so the
# dev checkout, its index, and its branches are never touched.
gitdir="$(git rev-parse --absolute-git-dir)"
export GIT_INDEX_FILE="$work/index"
git --git-dir="$gitdir" --work-tree="$stage" -C "$stage" add -A -f .
tree="$(git write-tree)"
unset GIT_INDEX_FILE

old=""
if git ls-remote --exit-code --heads "$remote" "$branch" > /dev/null; then
    git fetch -q "$remote" "refs/heads/$branch"
    old="$(git rev-parse FETCH_HEAD)"
fi
parent=()
if [[ $fresh -eq 0 ]]; then
    [[ -n "$old" ]] \
        || { echo "$remote/$branch doesn't exist; the first release needs --fresh" >&2; exit 1; }
    git log -1 --format=%B "$old" | grep -q "(from VisLang@" \
        || { echo "$remote/$branch wasn't made by this script; replace it once with --fresh" >&2; exit 1; }
    if [[ "$(git rev-parse "$old^{tree}")" == "$tree" ]]; then
        echo "$remote/$branch already matches dev@$sha; nothing to release"
        exit 0
    fi
    parent=(-p "$old")
fi

commit="$(git commit-tree "$tree" ${parent[@]+"${parent[@]}"} \
    -m "Sieve ${tag:-release} (from VisLang@$sha)")"
[[ -n "$tag" ]] && git tag -a "$tag" -m "Sieve $tag (from VisLang@$sha)" "$commit"

if [[ $push -eq 0 ]]; then
    echo "built release $commit from dev@$sha (not pushed)"
    echo "  review:  git show --stat $commit"
    echo "  publish: re-run with --push"
    exit 0
fi
if [[ $fresh -eq 1 && -n "$old" ]]; then
    git push --force-with-lease="refs/heads/$branch:$old" "$remote" "$commit:refs/heads/$branch"
else
    git push "$remote" "$commit:refs/heads/$branch"
fi
[[ -n "$tag" ]] && git push "$remote" "refs/tags/$tag"
echo "released dev@$sha to $remote/$branch as $commit"
