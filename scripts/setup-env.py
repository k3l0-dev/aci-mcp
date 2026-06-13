#!/usr/bin/env python3
# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
scripts/setup-env.py

Bootstrap the repo .env and generate MCP API keys.

Modes
-----
  python scripts/setup-env.py              interactive .env setup (creates if missing)
  python scripts/setup-env.py --gen-keys   generate 1 API key and print it
  python scripts/setup-env.py --gen-keys 3 generate 3 API keys and print them
  python scripts/setup-env.py --add-keys   generate new keys and append to existing .env

No external dependencies — stdlib only.
"""

import argparse
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# ── Colours (no deps, degrade gracefully on Windows) ──────────────────────────

_NO_COLOUR = sys.platform == "win32" or not sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return text if _NO_COLOUR else f"\033[{code}m{text}\033[0m"


def green(t: str) -> str:  return _c("32", t)
def yellow(t: str) -> str: return _c("33", t)
def cyan(t: str) -> str:   return _c("36", t)
def bold(t: str) -> str:   return _c("1",  t)
def dim(t: str) -> str:    return _c("2",  t)


# ── Key generation ─────────────────────────────────────────────────────────────

def gen_key() -> str:
    """Generate a cryptographically random URL-safe bearer token (43 chars)."""
    return secrets.token_urlsafe(32)


def gen_keys(n: int) -> list[str]:
    return [gen_key() for _ in range(n)]


def print_keys(keys: list[str], label: str = "Generated API key") -> None:
    print()
    for i, key in enumerate(keys, 1):
        tag = f"{label} {i}" if len(keys) > 1 else label
        print(f"  {bold(tag)}")
        print(f"  {cyan(key)}")
        print()
    if len(keys) > 1:
        combined = ",".join(keys)
        print(f"  {bold('MCP_API_KEYS')} (ready to paste into .env):")
        print(f"  {green(combined)}")
        print()


# ── .env parsing / writing ─────────────────────────────────────────────────────

def _read_env(path: Path) -> dict[str, str]:
    """Read a .env file into an ordered dict, preserving comments as '' values."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            result[key.strip()] = val.strip()
    return result


def _set_env_key(path: Path, key: str, value: str) -> None:
    """Set or replace a single KEY=value line in the .env file."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^({re.escape(key)}\s*=)(.*)$", re.MULTILINE)
    if pattern.search(text):
        new_text = pattern.sub(rf"\g<1>{value}", text)
    else:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"
    path.write_text(new_text, encoding="utf-8")


# ── Interactive setup ──────────────────────────────────────────────────────────

_PLACEHOLDER = {
    "APIC_HOST":       "10.0.0.1",
    "APIC_USER":       "admin",
    "APIC_PASSWORD":   "changeme",
    "APIC_VERIFY_SSL": "false",
    "MCP_PORT":        "8000",
    "MCP_API_KEYS":    "",
    "MCP_DOMAIN":      "mcp.example.com",
}

_PROMPTS = {
    "APIC_HOST":     ("APIC hostname or IP", "10.0.0.1"),
    "APIC_USER":     ("APIC username",        "admin"),
    "APIC_PASSWORD": ("APIC password",        "changeme"),
}


def _ask(label: str, default: str) -> str:
    raw = input(f"  {label} [{dim(default)}]: ").strip()
    return raw if raw else default


def interactive_setup() -> None:
    print()
    print(bold("  aci-mcp — environment setup"))
    print()

    if ENV_FILE.exists():
        print(f"  {yellow('⚠')}  {ENV_FILE.name} already exists.")
        answer = input("  Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print(f"  Kept existing {ENV_FILE.name}.")
            _offer_key_generation()
            return
        print()

    # Build .env from example template
    if not ENV_EXAMPLE.exists():
        print(f"  {yellow('!')}  .env.example not found — writing minimal .env")
        content = _build_minimal()
    else:
        content = ENV_EXAMPLE.read_text(encoding="utf-8")

    # Replace placeholder values with user input (or keep defaults)
    print(f"  {bold('APIC connection')}  {dim('(press Enter to keep default)')}")
    for key, (label, default) in _PROMPTS.items():
        value = _ask(label, default)
        content = re.sub(
            rf"^({re.escape(key)}\s*=).*$",
            rf"\g<1>{value}",
            content,
            flags=re.MULTILINE,
        )

    ENV_FILE.write_text(content, encoding="utf-8")
    print()
    print(f"  {green('✓')}  {ENV_FILE} created.")

    _offer_key_generation()


def _offer_key_generation() -> None:
    print()
    answer = input(f"  Generate {bold('MCP_API_KEYS')}? [y/N] ").strip().lower()
    if answer != "y":
        print(f"  {dim('Skipped — MCP_API_KEYS left empty (auth disabled in dev mode).')}")
        print()
        return

    raw = input("  How many keys? [1] ").strip()
    n = int(raw) if raw.isdigit() and int(raw) > 0 else 1
    keys = gen_keys(n)
    _set_env_key(ENV_FILE, "MCP_API_KEYS", ",".join(keys))
    print_keys(keys, "API key")
    print(f"  {green('✓')}  MCP_API_KEYS written to {ENV_FILE.name}.")
    print()


def _build_minimal() -> str:
    lines = ["# aci-mcp — generated by scripts/setup-env.py", ""]
    for key, val in _PLACEHOLDER.items():
        lines.append(f"{key}={val}")
    return "\n".join(lines) + "\n"


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="setup-env",
        description="Bootstrap .env and generate MCP API keys.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python scripts/setup-env.py              interactive setup (creates .env if missing)
  python scripts/setup-env.py --gen-keys   generate 1 key and print to stdout
  python scripts/setup-env.py --gen-keys 3 generate 3 keys and print to stdout
  python scripts/setup-env.py --add-keys   generate keys and write into existing .env
        """,
    )
    parser.add_argument(
        "--gen-keys",
        nargs="?",
        const=1,
        type=int,
        metavar="N",
        help="print N new API keys to stdout (default 1) — does not touch .env",
    )
    parser.add_argument(
        "--add-keys",
        nargs="?",
        const=1,
        type=int,
        metavar="N",
        help="generate N keys and write MCP_API_KEYS into .env (creates .env if missing)",
    )
    args = parser.parse_args()

    if args.gen_keys is not None:
        keys = gen_keys(args.gen_keys)
        print_keys(keys, "API key")
        return

    if args.add_keys is not None:
        n = args.add_keys
        if not ENV_FILE.exists():
            print(f"  {yellow('!')}  .env not found — creating from .env.example with placeholders.")
            src = ENV_EXAMPLE if ENV_EXAMPLE.exists() else None
            ENV_FILE.write_text(
                src.read_text(encoding="utf-8") if src else _build_minimal(),
                encoding="utf-8",
            )
            print(f"  {green('✓')}  {ENV_FILE.name} created.")
        keys = gen_keys(n)
        _set_env_key(ENV_FILE, "MCP_API_KEYS", ",".join(keys))
        print_keys(keys, "API key")
        print(f"  {green('✓')}  MCP_API_KEYS written to {ENV_FILE.name}.")
        print()
        return

    # Default: interactive setup
    interactive_setup()


if __name__ == "__main__":
    main()
