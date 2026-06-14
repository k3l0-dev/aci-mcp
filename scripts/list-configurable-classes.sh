#!/usr/bin/env bash
# List ACI classes where isConfigurable=true at the class (parent) level.
#
# Usage:
#   ./scripts/list-configurable-classes.sh [OPTIONS]
#
# Options:
#   -p, --package PKG   Filter by package prefix (e.g. fv, l3ext, vz)
#   -x, --exclude-rsrt  Exclude Rs/Rt relation classes
#   -c, --count         Print count only
#   -h, --help          Show this help
#
# Examples:
#   ./scripts/list-configurable-classes.sh
#   ./scripts/list-configurable-classes.sh --package fv
#   ./scripts/list-configurable-classes.sh --exclude-rsrt --count

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEMA_DIR="$REPO_ROOT/data/schemas"

# ── parse args ────────────────────────────────────────────────────────────────

PACKAGE=""
EXCLUDE_RSRT=false
COUNT_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--package)   PACKAGE="$2"; shift 2 ;;
        -x|--exclude-rsrt) EXCLUDE_RSRT=true; shift ;;
        -c|--count)     COUNT_ONLY=true; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── resolve schema version dir ────────────────────────────────────────────────

if [[ ! -d "$SCHEMA_DIR" ]]; then
    echo "error: $SCHEMA_DIR not found — run schema-collector first" >&2
    exit 1
fi

VERSION_DIR="$(ls -1 "$SCHEMA_DIR" | sort -V | tail -1)"
TARGET="$SCHEMA_DIR/$VERSION_DIR"

echo "# schema: $VERSION_DIR" >&2

# ── build jq filter ───────────────────────────────────────────────────────────

JQ_FILTER='to_entries[] | select(.value.isConfigurable == true) | .key'

if [[ -n "$PACKAGE" ]]; then
    JQ_FILTER="to_entries[] | select(.value.isConfigurable == true and (.key | startswith(\"${PACKAGE}:\"))) | .key"
fi

# ── run ───────────────────────────────────────────────────────────────────────

RESULT=$(find "$TARGET" -name "*.json" | sort | xargs -n50 jq -r "$JQ_FILTER" | sort)

if [[ "$EXCLUDE_RSRT" == true ]]; then
    RESULT=$(echo "$RESULT" | grep -Ev '^[a-z][a-z0-9]*:(Rs|Rt)[A-Z]')
fi

if [[ "$COUNT_ONLY" == true ]]; then
    echo "$RESULT" | grep -c .
else
    echo "$RESULT"
fi
