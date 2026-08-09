# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/

The niwashi-mcp test suite is organized into six categories, each with a
distinct purpose and a distinct relationship to CI:

  unit/         Pure-logic tests with no I/O — registry/filter.py,
                registry/catalog.py, registry/descriptions.py, middleware,
                exceptions, and apic/client.py driven through a fake HTTP
                transport (FakeHTTPClient in tests/unit/test_client.py).
                Fast, deterministic, run on every CI push.

  integration/  Tool-level tests (main.py's search_classes / get_schema /
                query / get_by_dn / count) driven through StubBackend, an
                in-memory ApicClient replacement (tests/conftest.py). Proves
                tool-layer behavior — validation, clamping, error shapes —
                without a live APIC. Also includes
                test_tool_client_wiring.py, which swaps StubBackend for a
                *real* ApicClient wired to the same FakeHTTPClient pattern
                used in tests/unit/test_client.py: this is what actually
                proves a tool argument (page, rsp_subtree_include,
                time_range, config_only, ...) reaches the real APIC request
                ApicClient builds, and that an invalid filter attribute
                raises FilterError all the way out through the tool —
                neither of which StubBackend's simplified Python
                reimplementation of filtering can ever verify, since it
                never calls registry.filter.build_filter() at all. Runs on
                every CI push.

  live/         End-to-end tests against a real Cisco APIC (or the internal
                APIC simulator lab instance) via the real ApicClient — no
                stubs, no fakes. Marked @pytest.mark.live and EXCLUDED from
                the default `uv run pytest` run (see the `addopts` default
                in pyproject.toml: "-m not live"). This is deliberate: a
                public GitHub Actions runner has no network path to the
                internal lab simulator, so wiring this suite into default
                CI would only ever produce environment-dependent failures,
                never a useful signal. tests/live/ is meant for local
                development and pre-release validation instead — run it
                explicitly with `uv run pytest tests/live/ -m live` (or
                `--override-ini addopts=""` to lift the default exclusion
                entirely). The session-scoped `live_client` fixture in
                tests/live/conftest.py auto-skips (never fails) when the
                simulator is unreachable. A sibling piece of work updates
                the CI configs themselves to reflect this split — this
                docstring only documents the intent.

  perf/         Latency/throughput budget tests against synthetic
                production-scale data (15k+ classes, 1000-object responses,
                and the real niwaki catalogue) — catches performance regressions in the
                hot paths (schema loading, filter building, search scoring),
                not correctness regressions.

  baseline/     Recorded behaviour, asserted as EQUALITY rather than as a
                floor: the descriptions index by digest, get_schema() for 38
                stratified classes, and the exact top-5 of all 74 golden
                queries. Built as the anti-drift net for the 2.0 data-layer
                migration and kept because equality catches what a floor
                cannot. It replaced eval/, whose Recall@1 >= 0.60 floor sat so
                far under the delivered 0.784 that it killed two of twelve
                scoring mutants where these pins kill all twelve.

  live/         Requires a reachable APIC. Gated behind the `live` marker and
                excluded from the default run by addopts, so a plain
                `uv run pytest` never touches the network.
"""
