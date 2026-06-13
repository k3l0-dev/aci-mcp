"""ACI MCP Labs — synthwave stream TUI dashboard.

Single-file layout:
  Header  — title + clock
  Body    — Sidebar (28 cols) | StreamPanel (rest)
  Footer  — key bindings

Sidebar refreshes status indicators (MCP, APIC, Schema, OpenCode) every 3 s
and accumulates live metrics from the log stream.
StreamPanel tails the MCP server log file and renders each line with
service/level colour coding.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, RichLog, Static

from scripts.tui.log_parser import LogLine, MetricsAccumulator, tail_log_file

REPO_ROOT = Path(__file__).parent.parent.parent
PID_FILE = REPO_ROOT / ".lab.pid"
OPENCODE_PID_FILE = REPO_ROOT / ".lab-opencode.pid"
LOG_FILE = REPO_ROOT / ".lab-server.log"
ENV_FILE = REPO_ROOT / ".env"
SCHEMA_FILE = REPO_ROOT / "data" / "class-descriptions.json"
METRICS_FILE = REPO_ROOT / ".lab-metrics.json"

# Synthwave palette
_MG = "#e040fb"  # magenta
_CY = "#00e5ff"  # cyan
_GN = "#69ff47"  # neon green
_YL = "#ffd700"  # yellow
_RD = "#ff1744"  # red
_DM = "#4a4a6a"  # dim
_TX = "#c0b8f8"  # text

_SVC_COLORS: dict[str, str] = {
    "auth": _MG,
    "registry": _CY,
    "query": _GN,
    "collector": _YL,
    "server": "#7986cb",
    "fastmcp": _DM,
}

_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": _DM,
    "INFO": _TX,
    "WARN": _YL,
    "WARNING": _YL,
    "ERROR": _RD,
    "CRITICAL": _RD,
}


def _parse_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE .env file, ignoring comments and blank lines."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("\"'")
    return env


def _pid_alive(pid_file: Path) -> tuple[bool, int]:
    """Return (alive, pid) from a PID file, (False, 0) if absent or dead."""
    if not pid_file.exists():
        return False, 0
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except (ValueError, OSError, ProcessLookupError):
        return False, 0


def _read_metrics() -> dict:
    """Read the latest server metrics snapshot from .lab-metrics.json."""
    try:
        return json.loads(METRICS_FILE.read_text())
    except Exception:
        return {}


def _schema_age() -> tuple[str, str]:
    """Return (label, colour) describing how fresh the schema index is."""
    if not SCHEMA_FILE.exists():
        return "not found", _RD
    age_s = time.time() - SCHEMA_FILE.stat().st_mtime
    if age_s < 3600:
        return f"{int(age_s / 60)}m ago", _GN
    if age_s < 86400:
        return f"{int(age_s / 3600)}h ago", _YL
    return f"{int(age_s / 86400)}d ago", _RD


def _fmt_time(ts: str) -> str:
    """Extract HH:MM:SS from a log timestamp string."""
    if " " in ts:
        return ts.split(" ", 1)[1].split(",")[0]
    if "T" in ts:
        return ts.split("T", 1)[1].split(".")[0]
    return ts[:8] if len(ts) >= 8 else ts


def _svc_name(name: str) -> str:
    """Extract a short service name from a dotted logger name."""
    parts = name.replace("_", ".").split(".")
    return parts[-1] if parts else name


class Sidebar(VerticalScroll):
    """Left sidebar: status indicators and live metrics, refreshed every 3 s."""

    def __init__(self) -> None:
        super().__init__(id="sidebar")
        self._accumulator = MetricsAccumulator()
        self._apic_label = "checking…"
        self._apic_color = _DM
        self._env: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        """Yield a single Static that the refresh loop rewrites in place."""
        yield Static("", id="sidebar-content")

    def on_mount(self) -> None:
        """Start the APIC poller and the 3-second refresh timer."""
        self._env = _parse_env(ENV_FILE)
        self.set_interval(3.0, self._refresh)
        self.run_worker(self._poll_apic(), exclusive=True, name="apic-poll")
        self._refresh()

    async def _poll_apic(self) -> None:
        """Poll APIC reachability every 30 s using an async TCP connect."""
        while True:
            self._env = _parse_env(ENV_FILE)
            raw = self._env.get("APIC_HOST", "").strip()
            if not raw:
                self._apic_label = "no host"
                self._apic_color = _YL
            else:
                parsed = urllib.parse.urlparse(raw)
                host = parsed.hostname or raw
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port), timeout=5.0
                    )
                    writer.close()
                    await writer.wait_closed()
                    self._apic_label = "reachable"
                    self._apic_color = _GN
                except Exception:
                    self._apic_label = "unreachable"
                    self._apic_color = _RD
            self._refresh()
            await asyncio.sleep(30)

    def record(self, line: LogLine) -> None:
        """Feed a parsed log line into the metrics accumulator."""
        self._accumulator.record(line)

    def _refresh(self) -> None:
        """Rebuild and write the sidebar content."""
        env = self._env or _parse_env(ENV_FILE)
        port = env.get("MCP_PORT", "8000")

        mcp_alive, mcp_pid = _pid_alive(PID_FILE)
        mcp_dot = f"[{_GN}]●[/]" if mcp_alive else f"[{_RD}]●[/]"
        mcp_info = f"[{_DM}]pid {mcp_pid}  :{port}[/]" if mcp_alive else f"[{_DM}]stopped[/]"

        apic_dot = f"[{self._apic_color}]●[/]"

        schema_label, schema_color = _schema_age()
        schema_dot = f"[{schema_color}]●[/]"

        oc_alive, oc_pid = _pid_alive(OPENCODE_PID_FILE)
        oc_dot = f"[{_GN}]●[/]" if oc_alive else f"[{_RD}]●[/]"
        oc_info = f"[{_DM}]pid {oc_pid}  :4096[/]" if oc_alive else f"[{_DM}]stopped[/]"

        acc = self._accumulator
        req_s = acc.req_per_second()
        uptime = acc.uptime()
        stats = acc.latency_stats()
        avg = stats["avg"]
        p95 = stats["p95"]
        total = acc.total_calls()
        errs = acc.error_count()
        err_rate = acc.error_rate()
        by_tool = acc.calls_by_tool()

        if isinstance(avg, str):
            avg_lbl, p95_lbl, lat_col = "N/A", "N/A", _DM
        else:
            avg_v = int(round(cast(float, avg)))
            p95_v = int(round(cast(float, p95)))
            avg_lbl = f"{avg_v}ms"
            p95_lbl = f"{p95_v}ms"
            lat_col = _GN if avg_v < 50 else (_YL if avg_v <= 200 else _RD)

        err_col = _GN if err_rate < 0.01 else (_YL if err_rate <= 0.05 else _RD)
        sep = f"[{_DM}]{'─' * 22}[/]"

        # ── APIC token health from .lab-metrics.json ──────────────────────────
        server_metrics = _read_metrics()
        apic_auth = server_metrics.get("apic", {})
        remaining = apic_auth.get("seconds_until_expiry")
        ttl = apic_auth.get("ttl_seconds", 600)
        auth_n = apic_auth.get("auth_count", "—")
        refresh_n = apic_auth.get("refresh_count", "—")
        last_refresh = apic_auth.get("last_refresh_at") or apic_auth.get("last_auth_at") or "—"
        task_alive = apic_auth.get("refresh_task_alive", False)

        if remaining is None:
            token_lbl = "—"
            token_col = _DM
        elif remaining < 60:
            token_lbl = f"{remaining}s  ⚠"
            token_col = _RD
        elif remaining < ttl * 0.3:
            token_lbl = f"{remaining}s"
            token_col = _YL
        else:
            token_lbl = f"{remaining}s"
            token_col = _GN

        task_dot = f"[{_GN}]●[/]" if task_alive else f"[{_RD}]●[/]"

        content = "\n".join([
            f"[{_MG} bold]◈  ACI MCP LABS[/]",
            "",
            f"[{_MG}]SERVER[/]",
            f"  {mcp_dot} MCP server",
            f"  {mcp_info}",
            "",
            f"[{_MG}]APIC[/]",
            f"  {apic_dot} {self._apic_label}",
            "",
            f"[{_MG}]SCHEMA[/]",
            f"  {schema_dot} {schema_label}",
            "",
            f"[{_MG}]OPENCODE[/]",
            f"  {oc_dot} OpenCode web",
            f"  {oc_info}",
            "",
            sep,
            "",
            f"[{_CY}]TOKEN[/]",
            "",
            f"  [{_DM}]expires[/]  [{token_col}]{token_lbl}[/]",
            f"  [{_DM}]refresh[/]  {task_dot}",
            f"  [{_DM}]logins[/]   [{_DM}]{auth_n}[/]",
            f"  [{_DM}]refreshes[/] [{_DM}]{refresh_n}[/]",
            f"  [{_DM}]last[/]     [{_DM}]{last_refresh}[/]",
            "",
            sep,
            "",
            f"[{_CY}]METRICS[/]",
            "",
            f"  [{_DM}]req/s[/]   [{_CY}]{req_s:.1f}[/]",
            f"  [{_DM}]uptime[/]  [{_CY}]{uptime or '—'}[/]",
            f"  [{_DM}]avg[/]     [{lat_col}]{avg_lbl}[/]",
            f"  [{_DM}]p95[/]     [{lat_col}]{p95_lbl}[/]",
            f"  [{_DM}]calls[/]   [{_CY}]{total}[/]",
            f"  [{_DM}]errors[/]  [{err_col}]{errs}[/]",
            "",
            f"  [{_DM}]search[/]  {by_tool['search_classes']}",
            f"  [{_DM}]schema[/]  {by_tool['get_schema']}",
            f"  [{_DM}]query[/]   {by_tool['query']}",
        ])

        try:
            self.query_one("#sidebar-content", Static).update(content)
        except Exception:
            pass


class StreamPanel(RichLog):
    """Full-height live log stream with synthwave colour coding."""

    def on_mount(self) -> None:
        """Start the async log tailer as a Textual background worker."""
        self.run_worker(
            tail_log_file(LOG_FILE, self._on_line),
            exclusive=True,
            name="log-tail",
        )

    def _on_line(self, line: LogLine) -> None:
        """Render a parsed log line and feed it to the sidebar accumulator."""
        try:
            self.app.query_one(Sidebar).record(line)
        except Exception:
            pass

        svc = _svc_name(line.name)
        svc_col = _SVC_COLORS.get(svc, _TX)
        level = line.level.upper()
        level_col = _LEVEL_COLORS.get(level, _TX)
        ts = _fmt_time(line.timestamp)

        self.write(
            f"[{_DM}]{ts}[/]  "
            f"[{svc_col}][{svc:<8}][/]  "
            f"[{level_col}]{level:<8}[/]  "
            f"[{_TX}]{line.message}[/]"
        )


class ACILabControl(App):
    """ACI MCP Labs — synthwave stream TUI."""

    CSS_PATH = str(Path(__file__).parent / "theme.tcss")
    TITLE = "ACI MCP LABS"
    SUB_TITLE = ""

    BINDINGS = [
        Binding("q", "quit", "Quit & stop", priority=True),
        Binding("s", "start_server", "Start"),
        Binding("S", "stop_server", "Stop"),
        Binding("r", "refresh_status", "Refresh"),
        Binding("t", "run_tests", "Tests"),
        Binding("b", "open_browser", "Browser"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Sidebar()
            yield StreamPanel(
                highlight=False,
                markup=True,
                auto_scroll=True,
                id="stream",
            )
        yield Footer()

    def action_quit(self) -> None:
        """Exit the TUI — lab.py finally block handles server shutdown."""
        self.exit()

    def action_start_server(self) -> None:
        """Start the lab pipeline in a background thread."""
        self.run_worker(
            self._run_cmd(["uv", "run", str(REPO_ROOT / "scripts" / "lab.py"), "up"]),
            name="lab-cmd",
            exclusive=True,
        )
        self.notify("Starting lab …", severity="information", timeout=2)

    def action_stop_server(self) -> None:
        """Stop MCP + OpenCode in a background thread."""
        self.run_worker(
            self._run_cmd(["uv", "run", str(REPO_ROOT / "scripts" / "lab.py"), "down"]),
            name="lab-cmd",
            exclusive=True,
        )
        self.notify("Stopping lab …", severity="warning", timeout=2)

    def action_refresh_status(self) -> None:
        """Force-refresh the sidebar."""
        try:
            self.query_one(Sidebar)._refresh()
        except Exception:
            pass
        self.notify("Status refreshed", severity="information", timeout=2)

    def action_run_tests(self) -> None:
        """Run the test suite in a background thread."""
        self.run_worker(
            self._run_cmd(["uv", "run", str(REPO_ROOT / "scripts" / "lab.py"), "test"]),
            name="lab-tests",
            exclusive=True,
        )
        self.notify("Running tests …", severity="information", timeout=2)

    def action_open_browser(self) -> None:
        """Open OpenCode web UI in the default browser."""
        webbrowser.open("http://localhost:4096")
        self.notify("Opening OpenCode in browser …", severity="information", timeout=2)

    async def _run_cmd(self, cmd: list[str]) -> None:
        """Run a lab CLI sub-command in a thread without blocking the TUI."""
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
