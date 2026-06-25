#!/usr/bin/env bash
# download-schemas.sh — download ACI jsonmeta schemas from the GitHub release.
#
# The schema bundle (~200 MB compressed) is too large for the git repo and is
# distributed as a release asset. This script fetches and extracts it into
# data/schemas/ so the MCP server can serve get_schema() requests.
#
# Usage:
#   ./scripts/download-schemas.sh [version]
#
# Examples:
#   ./scripts/download-schemas.sh           # defaults to v1.0.0
#   ./scripts/download-schemas.sh v1.1.0

set -euo pipefail

REPO="k3l0-dev/aci-mcp"
VERSION="${1:-v1.0.0}"
ASSET="schemas-mo-apic.tar.gz"
URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
DEST="data/schemas"

# ── Resolve repo root ────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# ── Check if already present ─────────────────────────────────────────────────

if [[ -d "$DEST" ]] && [[ -n "$(ls -A "$DEST" 2>/dev/null)" ]]; then
  echo "✓ $DEST already exists and is non-empty. Nothing to do."
  echo "  Delete $DEST manually and re-run to force a fresh download."
  exit 0
fi

# ── Download ─────────────────────────────────────────────────────────────────

echo "Downloading schemas ${VERSION} from GitHub Releases …"
echo "  URL: ${URL}"

TMP="$(mktemp -t aci-mcp-schemas.XXXXXX.tar.gz)"
trap 'rm -f "$TMP"' EXIT

if command -v curl &>/dev/null; then
  curl -fL --progress-bar -o "$TMP" "$URL"
elif command -v wget &>/dev/null; then
  wget -O "$TMP" "$URL"
else
  echo "Error: curl or wget is required." >&2
  exit 1
fi

# ── Extract ──────────────────────────────────────────────────────────────────

echo "Extracting to ${DEST}/ …"
mkdir -p "$DEST"
tar -xzf "$TMP" -C "$DEST" --strip-components=1

echo "✓ Schemas installed at ${DEST}/"
