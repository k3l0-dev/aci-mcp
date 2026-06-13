#!/usr/bin/env python3
# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click>=8.1,<9.0",
#   "rich>=14.2,<15.0",
#   "pyfiglet>=1.0,<2.0",
#   "textual>=8.0,<9.0",
# ]
# ///
"""
scripts/lab.py — ACI-MCP lab control.

Running with no arguments (or `tui`) starts the full pipeline — dependency sync,
Cisco ACI sandbox reachability check, MCP server, OpenCode web UI — then opens
the synthwave TUI dashboard.

Sub-commands are available for scripting and CI:

    tui      Default: pipeline (if needed) + TUI dashboard
    up       Start the pipeline without the TUI
    down     Stop MCP server and OpenCode
    logs     Stream the MCP server log in real time (Ctrl-C to stop)
    test     Run pytest  (add --live for integration tests against a live APIC)
    collect  Refresh data/class-descriptions.json from a live APIC
    keys     Generate bearer tokens and append them to MCP_API_KEYS in .env

Usage:
    uv run scripts/lab.py              # default: tui
    uv run scripts/lab.py up           # pipeline only (no TUI)
    uv run scripts/lab.py down
    uv run scripts/lab.py test --live
"""

import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import click
import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REPO_ROOT = Path(__file__).parent.parent
# Make scripts/ importable so `from scripts.tui.app import ACILabControl` works.
sys.path.insert(0, str(REPO_ROOT))

ENV_FILE = REPO_ROOT / ".env"
PID_FILE = REPO_ROOT / ".lab.pid"
LOG_FILE = REPO_ROOT / ".lab-server.log"
OPENCODE_PID_FILE = REPO_ROOT / ".lab-opencode.pid"
OPENCODE_LOG_FILE = REPO_ROOT / ".lab-opencode.log"
MCP_DIR = REPO_ROOT / "mcp"
SCHEMA_FILE = REPO_ROOT / "data" / "class-descriptions.json"

console = Console()


# ── helpers ───────────────────────────────────────────────────────────────────


def _splash() -> None:
    """Render the MCP-ACI ASCII banner and copyright line."""
    art = pyfiglet.figlet_format("MCP-ACI", font="doom")
    console.print(Text(art.rstrip(), style="bold yellow"))
    console.print(
        "Khalid El-Ouiali · Monark AIOPS srl  © 2026",
        style="dim white",
        justify="right",
    )
    console.print()


def _read_env() -> dict[str, str]:
    """Parse the repo-root .env file and return its key=value pairs.

    Ignores comments and blank lines. Returns an empty dict if the file is absent.
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
    """Return the parsed .env or abort with a helpful message if absent."""
    env = _read_env()
    if not env:
        console.print(
            "[red]✗[/]  .env not found — copy [bold].env.example[/] and "
            "fill in APIC_HOST, APIC_USER, APIC_PASSWORD"
        )
        raise click.Abort()
    return env


def _pid_alive(pid_file: Path) -> tuple[bool, int]:
    """Return (alive, pid) from a PID file; (False, 0) if absent or dead."""
    if not pid_file.exists():
        return False, 0
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (ValueError, OSError, ProcessLookupError):
        return False, 0


def _schema_age_label() -> str:
    """Return a Rich-formatted freshness label for data/class-descriptions.json."""
    if not SCHEMA_FILE.exists():
        return "[red]not found — run: lab collect[/]"
    age_s = time.time() - SCHEMA_FILE.stat().st_mtime
    if age_s < 3600:
        return f"[green]{int(age_s / 60)}m ago[/]"
    if age_s < 86400:
        return f"[yellow]{int(age_s / 3600)}h ago[/]"
    return f"[red]{int(age_s / 86400)}d ago[/] — consider re-collecting"


def _check_apic(env: dict[str, str]) -> None:
    """TCP-probe APIC_HOST and print a status line.

    Does not abort on failure — the MCP server can still start (useful for
    unit-test runs that do not need a live APIC).
    """
    import socket

    raw = env.get("APIC_HOST", "").strip()
    if not raw:
        console.print("[yellow]⚠[/]  APIC_HOST not set — queries will fail at runtime")
        return

    parsed = urllib.parse.urlparse(raw)
    hostname = parsed.hostname or raw
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    console.print(f"[bold cyan]→[/] checking APIC sandbox ({hostname}:{port}) …")
    try:
        with socket.create_connection((hostname, port), timeout=5):
            pass
        console.print(f"[green]✓[/] APIC sandbox reachable  ({hostname}:{port})")
    except OSError as exc:
        console.print(f"[yellow]⚠[/]  APIC sandbox unreachable — {exc}")
        console.print("    MCP will start but queries will fail until the APIC is accessible.")


def _stop_process(pid_file: Path, label: str) -> None:
    """Send SIGTERM to the process in pid_file, wait up to 3 s, remove the file."""
    if not pid_file.exists():
        console.print(f"[yellow]⚠[/]  no {pid_file.name} — {label} not running")
        return
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(15):
            time.sleep(0.2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        pid_file.unlink(missing_ok=True)
        console.print(f"[green]✓[/] {label} stopped  (pid {pid})")
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        console.print(f"[yellow]⚠[/]  pid {pid} already gone — cleaned up {pid_file.name}")


def _start_pipeline(env: dict[str, str]) -> None:
    """Sync deps, check APIC, start MCP server and OpenCode.

    Prints Rich-formatted progress to the console. Raises click.Abort on
    any critical failure (uv sync error, server crash before health check).
    Is a no-op for already-running components (prints a warning instead).
    """
    # ── MCP server ────────────────────────────────────────────────────────────
    mcp_alive, mcp_pid = _pid_alive(PID_FILE)
    if mcp_alive:
        console.print(f"[yellow]⚠[/]  MCP already running (pid {mcp_pid}) — skipping")
    else:
        # Sync dependencies
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

        # APIC sandbox reachability
        _check_apic(env)

        # Launch MCP in background
        port = env.get("MCP_PORT", "8000")
        console.print(f"[bold cyan]→[/] starting MCP server on :{port} …")
        with LOG_FILE.open("w") as log:
            proc = subprocess.Popen(
                ["uv", "run", "--project", str(MCP_DIR), "python", "main.py"],
                cwd=MCP_DIR,
                stdout=log,
                stderr=log,
            )
        PID_FILE.write_text(str(proc.pid))

        # Health check — 401/403/405 counts as "server up" (auth middleware active)
        url = f"http://localhost:{port}/mcp"
        ready = False
        for _ in range(20):
            time.sleep(0.7)
            if proc.poll() is not None:
                console.print(
                    f"[red]✗[/]  MCP server exited unexpectedly — "
                    f"check [bold]{LOG_FILE.relative_to(REPO_ROOT)}[/]"
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
                f"[red]✗[/]  MCP server did not respond after 14 s — "
                f"check [bold]{LOG_FILE.relative_to(REPO_ROOT)}[/]"
            )
            raise click.Abort()
        console.print(f"[green]✓[/] MCP server up  (pid {proc.pid})")

    # ── OpenCode web UI ───────────────────────────────────────────────────────
    oc_alive, oc_pid = _pid_alive(OPENCODE_PID_FILE)
    if oc_alive:
        console.print(f"[yellow]⚠[/]  OpenCode already running (pid {oc_pid}) — skipping")
    else:
        console.print("[bold cyan]→[/] starting OpenCode web UI on :4096 …")
        with OPENCODE_LOG_FILE.open("w") as oc_log:
            oc_proc = subprocess.Popen(
                ["opencode", "web", "--port", "4096", "--log-level", "DEBUG", "--print-logs"],
                cwd=REPO_ROOT,
                stdout=oc_log,
                stderr=oc_log,
            )
        OPENCODE_PID_FILE.write_text(str(oc_proc.pid))
        time.sleep(1.5)
        if oc_proc.poll() is not None:
            console.print(
                f"[yellow]⚠[/]  OpenCode exited immediately — "
                f"check [bold]{OPENCODE_LOG_FILE.name}[/]"
            )
        else:
            console.print(f"[green]✓[/] OpenCode web UI ready  (pid {oc_proc.pid})")
            import webbrowser
            webbrowser.open("http://localhost:4096")


# ── commands ──────────────────────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """ACI-MCP lab — no arguments starts the pipeline and opens the TUI."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(tui)


@cli.command()
def tui() -> None:
    """Start the pipeline (if not running), then open the TUI dashboard."""
    env = _require_env()
    mcp_alive, _ = _pid_alive(PID_FILE)
    if not mcp_alive:
        _splash()
        _start_pipeline(env)
        console.print()
        time.sleep(0.3)  # let terminal settle before Textual takes over the screen

    from scripts.tui.app import ACILabControl  # noqa: PLC0415
    ACILabControl().run()


@cli.command()
def up() -> None:
    """Start the pipeline (MCP + APIC check + OpenCode) without the TUI."""
    _splash()
    env = _require_env()
    _start_pipeline(env)

    port = env.get("MCP_PORT", "8000")
    api_keys_raw = env.get("MCP_API_KEYS", "")
    key_count = len([k for k in api_keys_raw.split(",") if k.strip()])
    auth_label = (
        f"[green]enabled[/] ({key_count} key{'s' if key_count != 1 else ''})"
        if key_count
        else "[yellow]DISABLED — server is open[/]"
    )
    console.print()
    table = Table.grid(padding=(0, 2))
    table.add_row("[dim]endpoint[/]", f"[bold]http://localhost:{port}/mcp[/]")
    table.add_row("[dim]auth[/]", auth_label)
    table.add_row("[dim]apic[/]", env.get("APIC_HOST", "[red]not set[/]"))
    table.add_row("[dim]schema[/]", _schema_age_label())
    table.add_row("[dim]opencode[/]", "[bold]http://localhost:4096[/]")
    console.print(Panel(table, title="[bold yellow]lab ready[/]", border_style="yellow"))


@cli.command()
def down() -> None:
    """Stop the MCP server and OpenCode."""
    _stop_process(PID_FILE, "MCP server")
    _stop_process(OPENCODE_PID_FILE, "OpenCode")


@cli.command()
@click.option("--lines", "-n", default=50, show_default=True, help="Past lines to show.")
def logs(lines: int) -> None:
    """Stream the MCP server log in real time (Ctrl-C to stop)."""
    if not LOG_FILE.exists():
        console.print(f"[red]✗[/]  {LOG_FILE.name} not found — run [bold]lab up[/] first")
        raise click.Abort()
    subprocess.run(["tail", f"-n{lines}", "-f", str(LOG_FILE)])


@cli.command()
@click.option(
    "--live",
    is_flag=True,
    help="Include integration tests (requires a running MCP + live APIC).",
)
def test(live: bool) -> None:
    """Run pytest — unit tests only by default; add --live for full suite."""
    args = [
        "uv", "run", "--project", str(MCP_DIR),
        "pytest", str(MCP_DIR / "tests"),
    ]
    if live:
        args += ["-v", "--tb=short"]
    else:
        args += ["-q", "--ignore", str(MCP_DIR / "tests" / "integration")]
    sys.exit(subprocess.run(args, cwd=REPO_ROOT).returncode)


@cli.command()
def collect() -> None:
    """Run the schema-collector pipeline and refresh data/class-descriptions.json."""
    collect_dir = REPO_ROOT / "schema-collector"
    if not collect_dir.exists():
        console.print("[red]✗[/]  schema-collector/ not found in repo root")
        raise click.Abort()
    console.print("[bold cyan]→[/] running schema-collector pipeline …")
    result = subprocess.run(["uv", "run", "python", "collect.py"], cwd=collect_dir)
    if result.returncode == 0:
        console.print("[green]✓[/] data/class-descriptions.json updated")
    sys.exit(result.returncode)


@cli.command()
@click.argument("count", default=1, type=click.IntRange(min=1, max=20))
def keys(count: int) -> None:
    """Generate COUNT bearer tokens (default 1) and append them to MCP_API_KEYS in .env."""
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
        console.print(f"[green]✓[/] {count} new key(s) appended to .env  ({len(all_keys)} total)")
    else:
        console.print("[yellow]⚠[/]  .env not found — keys not saved")

    for k in new_keys:
        console.print(f"  [bold yellow]{k}[/]")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
