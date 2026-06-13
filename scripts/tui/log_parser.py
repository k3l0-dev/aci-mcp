"""Log parsing and metrics extraction for the TUI metrics panel.

Parses two log formats produced by the MCP server:
  1. Python logging module:  `%(asctime)s  %(levelname)-8s  %(name)s  %(message)s`
  2. FastMCP RichHandler:    `%(asctime)s  %(levelname)s  %(message)s`

Also provides an async log tailer that polls the file (no subprocess).
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

# Python logging module format (with name field)
_PY_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"  # timestamp
    r"(\w+)\s+"                                            # level
    r"(\S+)\s+"                                            # name
    r"(.*)$",
)

# FastMCP RichHandler format (no name field)
_FASTMCP_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"  # timestamp
    r"(\w+)\s+"                                            # level
    r"(.*)$",
)

# Rich/Textual handler format: [MM/DD/YY HH:MM:SS] LEVEL   message   file.py:N
_RICH_LOG_RE = re.compile(
    r"^\[(\d{2}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\]\s+"
    r"(\w+)\s+"
    r"(.+)$",
)
# Strip trailing "  filename.py:NNN" alignment suffix from Rich messages
_RICH_SOURCE_RE = re.compile(r"\s{2,}\S+\.py:\d+\s*$")

# Latency pattern: "(12ms)" anywhere in the message
_LATENCY_RE = re.compile(r"\((\d+)ms\)")

# Tool call patterns in messages — no ^ so search() finds it anywhere in the line
_TOOL_CALL_RE = re.compile(
    r"\b(search_classes|get_schema|query)\("
)


@dataclass
class LogLine:
    """A parsed log line."""

    timestamp: str
    level: str
    name: str
    message: str
    raw: str


def parse_line(line: str) -> LogLine | None:
    """Parse a single log line into a :class:`LogLine`.

    Tries three formats in order:
      1. Python logging module (``YYYY-MM-DD HH:MM:SS,mmm  LEVEL  name  msg``)
      2. Rich/FastMCP handler (``[MM/DD/YY HH:MM:SS] LEVEL  msg  file.py:N``)
      3. FastMCP plain (``YYYY-MM-DD HH:MM:SS,mmm  LEVEL  msg``)
    Returns ``None`` for unparseable lines (banners, continuation lines, etc.).
    """
    line = line.rstrip("\n\r")
    if not line:
        return None

    # ── Format 1: Python logging (YYYY-MM-DD timestamp + name field) ─────────
    m = _PY_LOG_RE.match(line)
    if m:
        name = m.group(3)
        message = m.group(4)
        # Treat as FastMCP if the "name" field looks like a tool call or has no
        # module-path separators (dots, hyphens, underscores).
        is_tool_like = (
            _TOOL_CALL_RE.match(name)
            or "(" in name
            or name in ("search_classes", "get_schema", "query")
        )
        looks_like_module = any(c in name for c in (".", "-", "_"))
        if is_tool_like or not looks_like_module:
            rest = line[len(m.group(1)) + len(m.group(2)) + 2:].lstrip()
            return LogLine(
                timestamp=m.group(1),
                level=m.group(2),
                name="fastmcp",
                message=rest,
                raw=line,
            )
        return LogLine(
            timestamp=m.group(1),
            level=m.group(2),
            name=name,
            message=message,
            raw=line,
        )

    # ── Format 2: Rich/FastMCP handler ([MM/DD/YY HH:MM:SS] LEVEL  msg) ──────
    m = _RICH_LOG_RE.match(line)
    if m:
        try:
            rich_dt = datetime.strptime(m.group(1), "%m/%d/%y %H:%M:%S")
            ts = rich_dt.strftime("%Y-%m-%d %H:%M:%S,000")
        except ValueError:
            ts = m.group(1)
        msg = _RICH_SOURCE_RE.sub("", m.group(3)).strip()
        return LogLine(
            timestamp=ts,
            level=m.group(2),
            name="fastmcp",
            message=msg,
            raw=line,
        )

    # ── Format 3: FastMCP plain (YYYY-MM-DD, no name field) ──────────────────
    m = _FASTMCP_LOG_RE.match(line)
    if m:
        return LogLine(
            timestamp=m.group(1),
            level=m.group(2),
            name="fastmcp",
            message=m.group(3),
            raw=line,
        )

    return None


# ── Metrics accumulation ──────────────────────────────────────────────────────

class MetricsAccumulator:
    """Accumulates metrics from parsed log lines.

    All time-based metrics use a 60-second sliding window.
    """

    def __init__(self) -> None:
        self._timestamps: list[str] = []
        self._latencies: list[int] = []
        self._tool_calls: dict[str, int] = defaultdict(int)
        self._error_count = 0
        self._total_lines = 0

    def record(self, line: LogLine) -> None:
        """Record a parsed log line and update all metrics."""
        self._total_lines += 1
        self._timestamps.append(line.timestamp)

        # Count errors
        if line.level in ("WARNING", "ERROR"):
            self._error_count += 1

        # Extract latency from "(Nms)" patterns in the message
        latency_match = _LATENCY_RE.search(line.message)
        if latency_match:
            self._latencies.append(int(latency_match.group(1)))

        # Track tool calls
        tool_match = _TOOL_CALL_RE.search(line.message)
        if tool_match:
            self._tool_calls[tool_match.group(1)] += 1

    def req_per_second(self) -> float:
        """Requests per second over the last 60 seconds."""
        if not self._timestamps:
            return 0.0
        # Use the last timestamp as reference point
        last_ts = self._timestamps[-1]
        # Parse the last timestamp to get a reference time
        try:
            last_dt = datetime.strptime(last_ts, "%Y-%m-%d %H:%M:%S,%f")
        except (ValueError, TypeError):
            return 0.0

        # Count timestamps within the last 60 seconds
        window_start = last_dt.timestamp() - 60
        count = 0
        for ts in self._timestamps:
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f")
                if dt.timestamp() >= window_start:
                    count += 1
            except (ValueError, TypeError):
                continue

        return count / 60.0

    def uptime(self) -> str:
        """Time since the first log line, formatted as '2h 14m'."""
        if not self._timestamps:
            return "0s"

        try:
            first_dt = datetime.strptime(self._timestamps[0], "%Y-%m-%d %H:%M:%S,%f")
            last_dt = datetime.strptime(self._timestamps[-1], "%Y-%m-%d %H:%M:%S,%f")
            delta = last_dt - first_dt
            total_seconds = int(delta.total_seconds())
        except (ValueError, TypeError):
            return "0s"

        if total_seconds < 60:
            return f"{total_seconds}s"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    def latency_stats(self) -> dict[str, float | str]:
        """Return avg, median, p95 latency in milliseconds.

        Returns ``"N/A"`` for any stat when no latency data is available.
        """
        if not self._latencies:
            return {"avg": "N/A", "median": "N/A", "p95": "N/A"}

        avg = statistics.mean(self._latencies)
        median = statistics.median(self._latencies)
        sorted_latencies = sorted(self._latencies)
        p95_index = min(int(math.ceil(0.95 * len(sorted_latencies))) - 1, len(sorted_latencies) - 1)
        p95 = sorted_latencies[p95_index]

        return {
            "avg": round(avg, 1),
            "median": round(median, 1),
            "p95": round(p95, 1),
        }

    def total_calls(self) -> int:
        """Total number of tool calls recorded."""
        return sum(self._tool_calls.values())

    def calls_by_tool(self) -> dict[str, int]:
        """Tool call counts, always including the three known tools."""
        result = {"search_classes": 0, "get_schema": 0, "query": 0}
        for tool, count in self._tool_calls.items():
            result[tool] = count
        return result

    def error_count(self) -> int:
        """Number of WARNING/ERROR level lines."""
        return self._error_count

    def error_rate(self) -> float:
        """Error rate as a fraction (0.0–1.0)."""
        if self._total_lines == 0:
            return 0.0
        return self._error_count / self._total_lines


# ── Async log tailer ──────────────────────────────────────────────────────────

async def tail_log_file(
    log_path: Path,
    callback: Callable[[LogLine], None],
    poll_interval: float = 1.0,
) -> None:
    """Tail a log file asynchronously using polling.

    Reads the last 200 lines on initial load, then follows new lines.
    Handles file not existing (returns gracefully) and file rotation
    (detects when file is truncated/renamed).

    Args:
        log_path: Path to the log file.
        callback: Called for each new parsed :class:`LogLine`.
        poll_interval: Seconds between polls (default 1.0).
    """
    # Read last 200 lines on initial load
    if not log_path.exists():
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, IOError):
        return

    # Initial load: last 200 lines
    initial_lines = lines[-200:] if len(lines) > 200 else lines
    for line in initial_lines:
        parsed = parse_line(line)
        if parsed:
            callback(parsed)

    # Track file size for rotation detection
    try:
        current_size = log_path.stat().st_size
    except OSError:
        return

    # Position after the last read
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(current_size)

        while True:
            await asyncio.sleep(poll_interval)

            # Check if file still exists
            if not log_path.exists():
                return

            try:
                new_size = log_path.stat().st_size
            except OSError:
                continue

            # File was truncated or rotated (size decreased)
            if new_size < current_size:
                # Re-read from beginning
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                except (OSError, IOError):
                    current_size = new_size
                    continue

                initial_lines = lines[-200:] if len(lines) > 200 else lines
                for line in initial_lines:
                    parsed = parse_line(line)
                    if parsed:
                        callback(parsed)

                current_size = new_size
                continue

            # File grew — read new content
            if new_size > current_size:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(current_size)
                        new_content = f.read()
                except (OSError, IOError):
                    current_size = new_size
                    continue

                current_size = new_size

                # Process line by line
                for line in new_content.splitlines(keepends=True):
                    parsed = parse_line(line)
                    if parsed:
                        callback(parsed)
