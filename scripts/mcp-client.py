#!/usr/bin/env python3
# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
scripts/mcp-client.py

Print the OpenCode MCP client config to stdout, with the first MCP_API_KEYS
key injected as Authorization: Bearer header (when set in .env).

Usage:
    python scripts/mcp-client.py
    make mcp-client
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ENV_FILE = REPO_ROOT / ".env"

_DEFAULTS = {"MCP_PORT": "8000", "MCP_API_KEYS": ""}


def _read_env() -> dict[str, str]:
    values = dict(_DEFAULTS)
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            values[key.strip()] = val.strip()
    return values


def main() -> None:
    env = _read_env()

    port = env.get("MCP_PORT", "8000")
    raw_keys = env.get("MCP_API_KEYS", "")
    first_key = next(
        (k.strip() for k in raw_keys.split(",") if k.strip()),
        None,
    )

    server: dict = {
        "type": "remote",
        "url": f"http://localhost:{port}/mcp",
        "enabled": True,
    }

    if first_key:
        server["headers"] = {"Authorization": f"Bearer {first_key}"}
    else:
        # MCP_API_KEYS not set — auth disabled on server side
        sys.stderr.write(
            "warning: MCP_API_KEYS not set in .env — "
            "Authorization header omitted (dev mode)\n"
        )

    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {"aci-mcp": server},
    }

    print(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
