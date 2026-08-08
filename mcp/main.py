# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Backwards-compatible launcher — deprecated.

Until 1.x the server was started with ``python main.py`` from ``mcp/``. The
2.0 layout moves the code to ``src/niwashi_mcp/`` so it can be installed as a
package, which is what makes ``uvx niwashi-mcp`` possible.

This shim keeps the old command working so an existing deployment does not
break on upgrade. It will be removed in 3.0.

Prefer:
    uvx niwashi-mcp          # no checkout needed
    niwashi-mcp              # once installed
    python -m niwashi_mcp.main
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from niwashi_mcp.main import main

if __name__ == "__main__":
    warnings.warn(
        "`python main.py` is deprecated and will be removed in 3.0. "
        "Use `niwashi-mcp` (installed) or `python -m niwashi_mcp.main`.",
        DeprecationWarning,
        stacklevel=2,
    )
    main()
