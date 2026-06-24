#!/usr/bin/env bash
# publish-github.sh — push a clean public release to GitHub with no commit history.
#
# Creates a temporary orphan branch from the current working tree, makes a
# single commit, force-pushes it to github/main, pushes the release tag, then
# cleans up. The GitLab history is never touched.
#
# Usage:
#   ./scripts/publish-github.sh v1.0.0
#
# Prerequisites:
#   - git remote "github" must point to the public GitHub repo
#   - Working tree must be clean
#   - The tag passed as argument must exist locally

set -euo pipefail

VERSION="${1:-}"
ORPHAN_BRANCH="__gh-publish-tmp__"
REMOTE="github"

# ── Preflight checks ────────────────────────────────────────────────────────

if [[ -z "$VERSION" ]]; then
  echo "Usage: $0 <version-tag>  (e.g. $0 v1.0.0)" >&2
  exit 1
fi

if ! git rev-parse "$VERSION" >/dev/null 2>&1; then
  echo "Tag '$VERSION' does not exist. Create it first:  git tag $VERSION" >&2
  exit 1
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "Remote '$REMOTE' not found. Add it:  git remote add github <url>" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash changes first." >&2
  exit 1
fi

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "Publishing $VERSION to $REMOTE ($(git remote get-url $REMOTE)) …"

# ── Create orphan branch ────────────────────────────────────────────────────

git checkout --orphan "$ORPHAN_BRANCH"
git add -A
git commit -m "chore: public release $VERSION"

# ── Push to GitHub ──────────────────────────────────────────────────────────

git push "$REMOTE" "$ORPHAN_BRANCH:main" --force
git push "$REMOTE" "$VERSION"

echo "✓ Pushed $VERSION to $REMOTE/main (no history)"

# ── Cleanup ─────────────────────────────────────────────────────────────────

git checkout "$CURRENT_BRANCH"
git branch -D "$ORPHAN_BRANCH"

echo "✓ Done — orphan branch cleaned up"
