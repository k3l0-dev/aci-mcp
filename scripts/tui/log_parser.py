"""Log parsing and metrics extraction for the TUI stream panel and sidebar.

Handles two log formats produced by the MCP server:
  1. Python logging:   ``YYYY-MM-DD HH:MM:SS,mmm  LEVEL  name  message``
  2. FastMCP/Rich:     ``[MM/DD/YY HH:MM:SS] LEVEL  message  file.py:N``

Also provides an async log tailer that polls the file without spawning a
subprocess and handles the common cases of file-not-yet-existing and rotation.
"""

from __future__ import annotations

import asyncio
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


# ── Log line parsing ──────────────────────────────────────────────────────────

# Python logging module: YYYY-MM-DD HH:MM:SS,mmm  LEVEL  name  message
_PY_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"
    r"(\w+)\s+"
    r"(\S+)\s+"
    r"(.*)$",
)

# FastMCP RichHandler: [MM/DD/YY HH:MM:SS] LEVEL  message  file.py:N
_RICH_LOG_RE = re.compile(
    r"^\[(\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\]\s+(\w+)\s+(.+)$"
)
# Trailing "  filename.py:NNN" suffix added by Rich
_RICH_SOURCE_RE = re.compile(r"\s{2,}\S+\.py:\d+\s*$")

# FastMCP plain: YYYY-MM-DD HH:MM:SS,mmm  LEVEL  message (no name field)
_FASTMCP_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(\w+)\s+(.*)$"
)

# Latency annotation: "(42ms)" anywhere in a message
_LATENCY_RE = re.compile(r"\((\d+)ms\)")

# Tool call signatures in log messages
_TOOL_CALL_RE = re.compile(r"\b(search_classes|get_schema|query)\(")

# Names that look like dotted module paths vs bare tool/svc names
_HAS_MODULE_CHARS = re.compile(r"[.\-_]")


@dataclass
class LogLine:
    """A single parsed log entry."""

    timestamp: str
    level: str
    name: str
    message: str
    raw: str


def parse_line(line: str) -> LogLine | None:
    """Parse one raw log line into a :class:`LogLine`.

    Tries three formats in order. Returns ``None`` for banners, continuation
    lines, or anything that does not match any format.
    """
    line = line.rstrip("\n\r")
    if not line:
        return None

    # ── Format 1: Python logging (YYYY-MM-DD, has name field) ────────────────
    m = _PY_LOG_RE.match(line)
    if m:
        ts, level, name, message = m.group(1), m.group(2), m.group(3), m.group(4)
        # Heuristic: treat as FastMCP if the "name" field is a tool call or has
        # no module-path separators (not a real Python logger name).
        if _TOOL_CALL_RE.match(name) or "(" in name or not _HAS_MODULE_CHARS.search(name):
            rest = line[len(ts) + len(level) + 2:].lstrip()
            return LogLine(timestamp=ts, level=level, name="fastmcp", message=rest, raw=line)
        return LogLine(timestamp=ts, level=level, name=name, message=message, raw=line)

    # ── Format 2: Rich/FastMCP handler ([MM/DD/YY HH:MM:SS]) ─────────────────
    m = _RICH_LOG_RE.match(line)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%m/%d/%y %H:%M:%S")
            ts = dt.strftime("%Y-%m-%d %H:%M:%S,000")
        except ValueError:
            ts = m.group(1)
        msg = _RICH_SOURCE_RE.sub("", m.group(3)).strip()
        return LogLine(timestamp=ts, level=m.group(2), name="fastmcp", message=msg, raw=line)

    # ── Format 3: FastMCP plain (YYYY-MM-DD, no name field) ──────────────────
    m = _FASTMCP_LOG_RE.match(line)
    if m:
        return LogLine(
            timestamp=m.group(1), level=m.group(2),
            name="fastmcp", message=m.group(3), raw=line,
        )

    return None


# ── Metrics accumulation ──────────────────────────────────────────────────────


class MetricsAccumulator:
    """Accumulate latency, error, and tool-call metrics from parsed log lines.

    All time-based metrics (req/s, uptime) use the log timestamps rather than
    wall-clock time so they reflect server activity rather than TUI uptime.
    """

    def __init__(self) -> None:
        self._timestamps: list[str] = []
        self._latencies: list[int] = []
        self._tool_calls: dict[str, int] = defaultdict(int)
        self._error_count = 0
        self._total_lines = 0

    def record(self, line: LogLine) -> None:
        """Update all counters from a parsed log line."""
        self._total_lines += 1
        self._timestamps.append(line.timestamp)

        if line.level.upper() in ("WARNING", "ERROR", "CRITICAL"):
            self._error_count += 1

        m = _LATENCY_RE.search(line.message)
        if m:
            self._latencies.append(int(m.group(1)))

        m = _TOOL_CALL_RE.search(line.message)
        if m:
            self._tool_calls[m.group(1)] += 1

    def req_per_second(self) -> float:
        """Requests per second over the trailing 60-second window."""
        if not self._timestamps:
            return 0.0
        try:
            last_dt = datetime.strptime(self._timestamps[-1], "%Y-%m-%d %H:%M:%S,%f")
        except (ValueError, TypeError):
            return 0.0
        window_start = last_dt.timestamp() - 60
        count = sum(
            1
            for ts in self._timestamps
            if _ts_to_epoch(ts) >= window_start
        )
        return count / 60.0

    def uptime(self) -> str:
        """Elapsed time from the first to the last log line."""
        if len(self._timestamps) < 2:
            return "0s"
        try:
            first = datetime.strptime(self._timestamps[0], "%Y-%m-%d %H:%M:%S,%f")
            last = datetime.strptime(self._timestamps[-1], "%Y-%m-%d %H:%M:%S,%f")
            total = int((last - first).total_seconds())
        except (ValueError, TypeError):
            return "0s"
        if total < 60:
            return f"{total}s"
        parts = []
        if total >= 3600:
            parts.append(f"{total // 3600}h")
        if total % 3600 >= 60:
            parts.append(f"{(total % 3600) // 60}m")
        return " ".join(parts)

    def latency_stats(self) -> dict[str, float | str]:
        """Return avg, median, p95 latency in ms; ``"N/A"`` when no data."""
        if not self._latencies:
            return {"avg": "N/A", "median": "N/A", "p95": "N/A"}
        s = sorted(self._latencies)
        p95_idx = min(int(math.ceil(0.95 * len(s))) - 1, len(s) - 1)
        return {
            "avg": round(statistics.mean(s), 1),
            "median": round(statistics.median(s), 1),
            "p95": round(s[p95_idx], 1),
        }

    def total_calls(self) -> int:
        """Total tool calls observed in the log stream."""
        return sum(self._tool_calls.values())

    def calls_by_tool(self) -> dict[str, int]:
        """Per-tool call counts; always includes the three known tools."""
        base = {"search_classes": 0, "get_schema": 0, "query": 0}
        base.update(self._tool_calls)
        return base

    def error_count(self) -> int:
        """Number of WARNING/ERROR/CRITICAL lines seen."""
        return self._error_count

    def error_rate(self) -> float:
        """Error rate as a fraction (0.0–1.0)."""
        return self._error_count / self._total_lines if self._total_lines else 0.0


def _ts_to_epoch(ts: str) -> float:
    """Convert a log timestamp to a Unix epoch float; 0.0 on parse failure."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except (ValueError, TypeError):
        return 0.0


# ── Async log tailer ──────────────────────────────────────────────────────────


async def tail_log_file(
    log_path: Path,
    callback: Callable[[LogLine], None],
    poll_interval: float = 1.0,
) -> None:
    """Tail a log file asynchronously using poll-based I/O.

    Reads the last 200 lines on initial load, then follows new content.
    Waits up to 60 s for the file to appear if it does not exist yet.
    Handles file rotation / truncation by re-reading from the beginning.

    Args:
        log_path:      Path to the server log file.
        callback:      Called for each parsed :class:`LogLine`.
        poll_interval: Seconds between size checks (default 1.0).
    """
    # Wait for the log file to appear (server may still be starting up).
    for _ in range(60):
        if log_path.exists():
            break
        await asyncio.sleep(1.0)
    else:
        return  # gave up

    # Deliver the last 200 historical lines immediately.
    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
        history = raw.splitlines(keepends=True)
        for line in history[-200:]:
            parsed = parse_line(line)
            if parsed:
                callback(parsed)
        position = log_path.stat().st_size
    except OSError:
        return

    # Follow new content by tracking the byte position.
    while True:
        await asyncio.sleep(poll_interval)

        if not log_path.exists():
            return

        try:
            new_size = log_path.stat().st_size
        except OSError:
            continue

        if new_size < position:
            # File was truncated or rotated — re-read from the beginning.
            position = 0

        if new_size > position:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(position)
                    chunk = f.read()
                position = new_size
                for line in chunk.splitlines(keepends=True):
                    parsed = parse_line(line)
                    if parsed:
                        callback(parsed)
            except OSError:
                continue
