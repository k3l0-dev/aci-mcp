# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Startup cost and resident footprint — the two budgets nothing measured.

Both sit on the critical path of `uvx niwashi-mcp`, which is the documented way
to run this server. Neither was measured anywhere, and the 2.0 migration changed
both: the recorded baseline still carries `descriptions_load_cold = 33.96` ms
from the pre-2.0 JSON path, against a measured 420 ms today — an 11.5x
regression written into a committed file that nobody read, because the file
records timings without asserting them.

Measured on this machine (Apple M3 Max, Python 3.13, niwaki 1.8.0), three cold
processes: 419 / 420 / 436 ms for import plus a full index rebuild, and a
resident footprint of ~159 MB once the tokenised index is warm.

The ceilings below are sanity bounds sized to catch an order-of-magnitude
change, not to police milliseconds. A CI runner is 2-4x slower than this
machine and the budgets allow for it — the search perf suite already learned
that a laptop-calibrated constant is a test that only passes on a laptop.
"""

from __future__ import annotations

import resource
import subprocess
import sys
import time

import pytest

from niwashi_mcp.registry import catalog
from niwashi_mcp.registry.descriptions import search

pytestmark = pytest.mark.catalog


def _rss_mb() -> float:
    """Peak resident set, in MB. ru_maxrss is bytes on macOS, KiB on Linux."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / 1e6 if sys.platform == "darwin" else raw / 1e3


class TestStartup:
    def test_a_cold_process_builds_the_index_in_seconds_not_minutes(self):
        """The whole cost of `uvx niwashi-mcp` reaching "Registry loaded".

        Measured in a fresh interpreter, because the point is what a user waits
        for, and an in-process measurement would be reading a warm SQLite page
        cache and an already-imported module tree.
        """
        code = (
            "import time;t=time.perf_counter();"
            "from niwashi_mcp.registry import catalog;"
            "i=catalog.descriptions_index();"
            "print(len(i), (time.perf_counter()-t)*1000)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.split()

        entries, ms = int(out[0]), float(out[1])
        assert entries == 15_239, f"the index built {entries} entries"
        assert ms < 4_000, (
            f"cold startup took {ms:.0f} ms. Measured at ~420 ms here and this "
            f"allows ~9x for a slower runner; at this point it is not a slow "
            f"machine, it is a changed algorithm."
        )

    def test_the_index_is_built_once_not_per_call(self):
        """`main.py` builds it in the lifespan, deliberately.

        `descriptions.search()` caches its tokenised index on the *identity* of
        the dict it is handed, so a rebuild per call would silently re-tokenise
        15,239 entries every time. This asserts the rebuild is what costs, and
        that reusing the same dict does not pay it again.
        """
        index = catalog.descriptions_index()
        search("warm", index, limit=1)

        t0 = time.perf_counter()
        for _ in range(20):
            search("bridge domain", index, limit=10)
        per_search = (time.perf_counter() - t0) / 20

        t0 = time.perf_counter()
        catalog.descriptions_index()
        rebuild = time.perf_counter() - t0

        # A ratio, not a constant: it holds on any machine, where a millisecond
        # ceiling would not. Measured here, ~17 ms per warm search against
        # ~320 ms to rebuild — roughly 18x. Ten is the floor that still means
        # "the rebuild is the expensive part, and it happens once".
        assert rebuild > per_search * 10, (
            f"a warm search costs {per_search * 1000:.1f} ms against "
            f"{rebuild * 1000:.0f} ms to rebuild the index — only "
            f"{rebuild / per_search:.1f}x. Either the tokenised cache is gone "
            f"(so every call rebuilds) or the rebuild stopped being the "
            f"expensive part, and the lifespan's build-once is no longer buying "
            f"what its comment claims."
        )


class TestFootprint:
    def test_the_resident_set_stays_within_a_container_sized_budget(self):
        """Two structures live for the process lifetime, by design.

        The catalogue connection holds the string pools; the tokenised search
        index holds ~15,239 entries. Measured here: ~45 MB for the index, ~93 MB
        more once tokenised, ~159 MB resident in total.

        The ceiling matters because a deployment with a memory limit gets a
        SIGKILL, not an exception — CPython does not read cgroup limits, so the
        process simply disappears with exit 137, no traceback and no log line.
        """
        index = catalog.descriptions_index()
        search("bridge domain", index, limit=10)

        rss = _rss_mb()
        assert rss < 500, (
            f"resident set is {rss:.0f} MB. Measured at ~159 MB; this allows 3x "
            f"for interpreter and platform differences. Past it, a 512 MB "
            f"container limit is a coin toss."
        )

    def test_the_tokenised_index_is_resident_not_rebuilt(self):
        """Repeated searches must not grow the footprint.

        If the cache were lost, each call would build and discard a full index —
        visible as steadily climbing RSS rather than as an error.
        """
        index = catalog.descriptions_index()
        search("warm", index, limit=1)
        before = _rss_mb()

        for term in ("tenant", "contract", "subnet", "fault", "interface", "vrf"):
            search(term, index, limit=10)

        assert _rss_mb() - before < 20, (
            f"six searches added {_rss_mb() - before:.0f} MB — the tokenised "
            f"index is being rebuilt per call"
        )


class TestPathologicalSchemas:
    """The classes the bounding layer exists for."""

    @pytest.mark.parametrize("cls", ["faultDelegate", "faultInst", "tagTag"])
    def test_a_universal_class_loads_without_stalling(self, cls):
        """`faultDelegate` carries 64,313 `dnFormats`.

        Unbounded, `get_schema` on it serialises to 7.8 MB of JSON — roughly 2M
        tokens for one call. The bounding happens in `main.py`, but the *load*
        underneath it still decodes every entry, and that cost was unmeasured.
        """
        t0 = time.perf_counter()
        schema = catalog.load_schema(cls, include_property_details=True)
        elapsed = time.perf_counter() - t0

        assert schema, f"{cls} did not resolve"
        assert elapsed < 2.0, f"{cls} took {elapsed * 1000:.0f} ms to project"
