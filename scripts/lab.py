#!/usr/bin/env python3
# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click>=8.1,<9.0",
#   "rich>=13.0,<14.0",
#   "pyfiglet>=1.0,<2.0",
# ]
# ///
"""
scripts/lab.py — ACI-MCP lab control CLI.

This is the primary development-loop controller for the aci-mcp project.
It wraps every repetitive dev task (starting the server, running tests,
collecting schemas, managing API keys) into a single, consistent command-line
interface with rich terminal output.

All commands read configuration from the repo-root .env file. The server
process is managed via a .lab.pid file so it can be started and stopped
independently across terminal sessions.

Usage:
    uv run scripts/lab.py <command> [options]
    make lab                              # alias for `up`

Commands:
    up       Start the lab (sync deps → launch MCP server → health check).
    down     Stop the background MCP server process.
    test     Run the pytest suite. Unit tests only by default; pass --live
             to also run integration tests against a live APIC sandbox.
    collect  Execute the full schema-collector pipeline to refresh
             data/class-descriptions.json from a live APIC.
    status   Print a dashboard: server liveness, schema age, env summary.
    keys     Generate cryptographically secure API keys and append them
             to MCP_API_KEYS in .env.

Dependencies (resolved automatically by uv via PEP 723 inline metadata):
    click, rich, pyfiglet
"""

import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import click
import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REPO_ROOT = Path(__file__).parent.parent
ENV_FILE = REPO_ROOT / ".env"
PID_FILE = REPO_ROOT / ".lab.pid"
MCP_DIR = REPO_ROOT / "mcp"
SCHEMA_FILE = REPO_ROOT / "data" / "class-descriptions.json"

console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────


def _splash() -> None:
    """Render the MCP-ACI ASCII-art banner with copyright line.

    Uses pyfiglet's 'doom' font (blocky, non-italic) rendered in bold yellow
    via Rich. The copyright notice is right-aligned below the art. Only called
    once, at the start of the `up` command, so it doesn't clutter other output.
    """
    art = pyfiglet.figlet_format("MCP-ACI", font="doom")
    console.print(Text(art.rstrip(), style="bold yellow"))
    console.print(
        "Khalid El-Ouiali · Monark AIOPS srl  © 2026",
        style="dim white",
        justify="right",
    )
    console.print()


def _read_env() -> dict[str, str]:
    """Parse the repo-root .env file and return its key-value pairs as a dict.

    Ignores blank lines and lines starting with '#'. Values are not
    shell-expanded (no variable substitution). Returns an empty dict if
    .env does not exist, so callers can handle the missing-file case
    explicitly without catching exceptions.
    """
    if not ENV_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            values[key.strip()] = val.strip()
    return values


def _require_env() -> dict[str, str]:
    """Return the parsed .env dict, or abort with a helpful message if absent.

    Used by commands that cannot run without a configured environment
    (e.g. `up`, which needs APIC_HOST and MCP_PORT). Raises click.Abort
    so Click exits cleanly without a Python traceback.
    """
    env = _read_env()
    if not env:
        console.print(
            "[red]✗[/]  .env not found — create one first "
            "([bold]python scripts/setup-env.py[/] or copy [bold].env.example[/])"
        )
        raise click.Abort()
    return env


def _schema_age_label() -> str:
    """Return a Rich-formatted string describing how fresh the schema index is.

    Compares the mtime of data/class-descriptions.json against the current
    time and returns a color-coded label:
      - green  : updated less than 1 hour ago
      - yellow : between 1 hour and 24 hours old
      - red    : older than 24 hours, or file not found at all

    This is used both in the `up` summary panel and the `status` dashboard
    so the operator always knows whether schemas need refreshing.
    """
    if not SCHEMA_FILE.exists():
        return "[red]not found — run: python scripts/lab.py collect[/]"
    age_s = time.time() - SCHEMA_FILE.stat().st_mtime
    if age_s < 3600:
        return f"[green]{int(age_s / 60)}m ago[/]"
    if age_s < 86400:
        return f"[yellow]{int(age_s / 3600)}h ago[/]"
    return f"[red]{int(age_s / 86400)}d ago[/] — consider re-collecting"


# ── commands ──────────────────────────────────────────────────────────────────


@click.group()
def cli() -> None:
    """ACI-MCP lab control — start, stop, test, collect, and manage API keys."""


@cli.command()
def up() -> None:
    """Sync deps, start the MCP server in background, wait for health check, print summary."""
    _splash()

    env = _require_env()

    # Guard: already running?
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            console.print(f"[yellow]⚠[/]  MCP server already running (pid {pid})")
            console.print("    Run [bold]python scripts/lab.py status[/] for details.")
            return
        except ProcessLookupError:
            PID_FILE.unlink()

    # Sync deps
    console.print("[bold cyan]→[/] syncing mcp dependencies …")
    result = subprocess.run(
        ["uv", "sync", "--project", str(MCP_DIR)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[red]✗[/]  uv sync failed:\n{result.stderr.strip()}")
        raise click.Abort()
    console.print("[green]✓[/] dependencies up to date")

    # Launch MCP server in background
    port = env.get("MCP_PORT", "8000")
    console.print(f"[bold cyan]→[/] starting MCP server on port {port} …")
    log_file = REPO_ROOT / ".lab-server.log"
    with log_file.open("w") as log:
        proc = subprocess.Popen(
            ["uv", "run", "--project", str(MCP_DIR), "python", "main.py"],
            cwd=MCP_DIR,
            stdout=log,
            stderr=log,
        )
    PID_FILE.write_text(str(proc.pid))

    # Health check — 401/403/405 counts as "server is up" (auth middleware active)
    url = f"http://localhost:{port}/mcp"
    ready = False
    for _ in range(20):
        time.sleep(0.7)
        if proc.poll() is not None:
            console.print(
                f"[red]✗[/]  server process exited unexpectedly — "
                f"check [bold]{log_file.relative_to(REPO_ROOT)}[/]"
            )
            PID_FILE.unlink(missing_ok=True)
            raise click.Abort()
        try:
            urllib.request.urlopen(url, timeout=2)
            ready = True
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 405):
                ready = True
                break
        except Exception:
            continue

    if not ready:
        console.print(
            f"[red]✗[/]  server did not respond after 14s — "
            f"check [bold]{log_file.relative_to(REPO_ROOT)}[/]"
        )
        raise click.Abort()

    console.print(f"[green]✓[/] MCP server is up  (pid {proc.pid})")
    console.print()

    # Summary panel
    api_keys_raw = env.get("MCP_API_KEYS", "")
    key_count = len([k for k in api_keys_raw.split(",") if k.strip()])
    auth_label = (
        f"[green]enabled[/] ({key_count} key{'s' if key_count != 1 else ''})"
        if key_count
        else "[yellow]DISABLED — server is open[/]"
    )

    table = Table.grid(padding=(0, 2))
    table.add_row("[dim]endpoint[/]", f"[bold]http://localhost:{port}/mcp[/]")
    table.add_row("[dim]auth[/]", auth_label)
    table.add_row("[dim]apic[/]", env.get("APIC_HOST", "[red]not set[/]"))
    table.add_row("[dim]schema[/]", _schema_age_label())
    table.add_row("[dim]pid[/]", str(proc.pid))
    table.add_row("[dim]logs[/]", str(log_file.relative_to(REPO_ROOT)))
    console.print(Panel(table, title="[bold yellow]lab ready[/]", border_style="yellow"))


@cli.command()
def down() -> None:
    """Send SIGTERM to the background MCP server and clean up .lab.pid."""
    if not PID_FILE.exists():
        console.print("[yellow]⚠[/]  no .lab.pid found — server not running (or started manually)")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(15):
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]✓[/] MCP server stopped (pid {pid})")
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        console.print(f"[yellow]⚠[/]  process {pid} was already gone — cleaned up .lab.pid")


@cli.command()
@click.option(
    "--live",
    is_flag=True,
    help="Include integration tests (requires a running MCP + live APIC).",
)
def test(live: bool) -> None:
    """Run pytest — unit tests only by default, add --live for full integration suite."""
    args = [
        "uv", "run", "--project", str(MCP_DIR),
        "pytest", str(MCP_DIR / "tests"),
    ]
    if live:
        args += ["-v", "--tb=short"]
    else:
        args += ["-q", "--ignore", str(MCP_DIR / "tests" / "integration")]
    result = subprocess.run(args, cwd=REPO_ROOT)
    sys.exit(result.returncode)


@cli.command()
def collect() -> None:
    """Run the schema-collector pipeline and refresh data/class-descriptions.json."""
    collect_dir = REPO_ROOT / "schema-collector"
    if not collect_dir.exists():
        console.print("[red]✗[/]  schema-collector/ not found in repo root")
        raise click.Abort()
    console.print("[bold cyan]→[/] running schema-collector pipeline …")
    result = subprocess.run(
        ["uv", "run", "python", "collect.py"],
        cwd=collect_dir,
    )
    if result.returncode == 0:
        console.print("[green]✓[/] schema collection complete — data/class-descriptions.json updated")
    sys.exit(result.returncode)


@cli.command()
def status() -> None:
    """Show server liveness, endpoint, APIC host, API key count, and schema age."""
    env = _read_env()

    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            server_label = f"[green]running[/] (pid {pid})"
        except ProcessLookupError:
            server_label = "[red]stopped[/] (stale .lab.pid — run [bold]lab down[/] to clean up)"
    else:
        server_label = "[red]stopped[/]"

    port = env.get("MCP_PORT", "8000")
    api_keys_raw = env.get("MCP_API_KEYS", "")
    key_count = len([k for k in api_keys_raw.split(",") if k.strip()])

    table = Table.grid(padding=(0, 2))
    table.add_row("[dim]server[/]", server_label)
    table.add_row("[dim]endpoint[/]", f"http://localhost:{port}/mcp")
    table.add_row("[dim]apic[/]", env.get("APIC_HOST", "[red]not set[/]"))
    table.add_row(
        "[dim]api keys[/]",
        f"{key_count} key(s) configured" if key_count else "[yellow]none — auth disabled[/]",
    )
    table.add_row("[dim]schema[/]", _schema_age_label())
    console.print(Panel(table, title="lab status", border_style="cyan"))


@cli.command()
@click.argument("count", default=1, type=click.IntRange(min=1, max=20))
def keys(count: int) -> None:
    """Generate COUNT bearer tokens (default 1, max 20) and append them to MCP_API_KEYS in .env."""
    import secrets

    new_keys = [secrets.token_urlsafe(32) for _ in range(count)]

    env = _read_env()
    existing = [k.strip() for k in env.get("MCP_API_KEYS", "").split(",") if k.strip()]
    all_keys = existing + new_keys

    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        if re.search(r"^MCP_API_KEYS=", content, flags=re.MULTILINE):
            content = re.sub(
                r"^MCP_API_KEYS=.*$",
                f"MCP_API_KEYS={','.join(all_keys)}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content += f"\nMCP_API_KEYS={','.join(all_keys)}\n"
        ENV_FILE.write_text(content, encoding="utf-8")
        console.print(f"[green]✓[/] {count} new key(s) appended to .env ({len(all_keys)} total)")
    else:
        console.print("[yellow]⚠[/]  .env not found — printing keys only (not saved)")

    for k in new_keys:
        console.print(f"  [bold yellow]{k}[/]")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
