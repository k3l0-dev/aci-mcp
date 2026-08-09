# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Class validation now has one source of truth.

Until 2.0, ``query()`` and ``count()`` validated a class name in two tiers: the
descriptions index first, then a fallback to the schema files, because the two
collections disagreed by 213 classes and a class missing from the first could
still be perfectly queryable. The fallback emitted a warning on every one of
those 213 valid classes.

Both collections now come from the same catalogue, so the fallback is gone. What
replaces it is a single lookup — and that is worth pinning, because the failure
this guard prevents is the one the whole server is designed around: the APIC
answers a query for a misspelt class with ``[]`` and no error, which an agent
reads as "there are none".

The tests below cover the three cases that matter and one that used to be a
latent bug.
"""

from __future__ import annotations

import pytest

from niwashi_mcp.exceptions import UnknownClassError
from niwashi_mcp.registry import catalog
from tests.conftest import MINIMAL_DESCRIPTIONS, StubBackend, make_ctx


def _ctx(imdata):
    return make_ctx(
        {"descriptions": dict(MINIMAL_DESCRIPTIONS), "backend": StubBackend(imdata)}
    )

pytestmark = pytest.mark.catalog


@pytest.fixture(scope="module")
def index() -> dict:
    return catalog.descriptions_index()


@pytest.fixture(scope="module")
def gap_classes(index) -> list[str]:
    """Classes that exist but are not searchable.

    213 of them: no label, no comment, no useful property label, so the index
    builder drops them. They are still real, still queryable, and used to be
    the reason for the two-tier fallback.
    """
    names = [r[0] for r in catalog._connect().execute("SELECT class_name FROM mo")]
    return [n for n in names if n not in index]


class TestSingleSourceOfTruth:
    def test_searchable_classes_are_valid(self, index):
        for cls in ("fvBD", "fvTenant", "faultInst"):
            assert cls in index
            assert catalog.class_exists(cls) is True

    def test_gap_classes_are_valid_without_being_searchable(self, index, gap_classes):
        """The 213 exist for validation but not for search — by design.

        Before 2.0 this asymmetry needed a fallback branch and produced a
        warning on every one of them. Now it is simply two questions with two
        answers, from one source.
        """
        assert len(gap_classes) == 213, f"gap moved to {len(gap_classes)} classes"
        for cls in gap_classes[:20]:
            assert catalog.class_exists(cls) is True, f"{cls} should validate"
            assert cls not in index, f"{cls} should not be searchable"

    def test_unknown_class_is_rejected(self):
        for cls in ("fvNotAClass", "totallyMadeUp", ""):
            assert catalog.class_exists(cls) is False

    def test_typo_is_rejected_rather_than_silently_accepted(self):
        """The whole point of validating before hitting the backend.

        ``fvBDD`` would return ``[]`` from the APIC with HTTP 200, which reads
        as "no bridge domains exist". Catching it here turns a wrong answer
        into an error carrying closest-match suggestions.
        """
        assert catalog.class_exists("fvBDD") is False
        assert catalog.class_exists("fvBD") is True

    def test_case_variants_are_rejected(self):
        """A case-insensitive lookup would resurrect an old, subtle bug.

        On a case-insensitive filesystem the previous reader resolved
        ``fvBd.json`` to ``fvBD.json``, so a typo validated and then queried the
        wrong class name against the APIC. SQLite's BINARY collation removes
        the hazard; this asserts it stays removed.
        """
        for variant in ("fvBd", "FVBD", "fvbd"):
            assert catalog.class_exists(variant) is False, f"{variant} must not validate"


class TestValidationIsWiredIntoTheTools:
    """The guard has to be *called*, not merely available."""

    @staticmethod
    def _main_source() -> str:
        from pathlib import Path

        from niwashi_mcp import main

        return Path(main.__file__).read_text()

    @pytest.mark.asyncio
    async def test_query_and_count_both_validate(self, sample_imdata):
        """Both tools must consult the catalogue, and neither may reach the
        backend for a class it does not know.

        This used to `assert source.count("catalog.class_exists(class_name)") == 2`.
        Counting a substring in the source passes on a comment, passes on a call
        in dead code, and fails on a benign refactor — factoring the shared guard
        into one `_validate()` helper would have broken it while improving the
        code. Assert the behaviour instead.
        """
        from unittest.mock import patch

        from niwashi_mcp.main import count, query

        for tool in (query, count):
            ctx = _ctx(sample_imdata)
            with patch(
                "niwashi_mcp.main.catalog.class_exists", return_value=False
            ) as guard, pytest.raises(UnknownClassError):
                await tool("fvBD", ctx)
            guard.assert_called_once_with("fvBD")
            assert not ctx.lifespan_context["backend"].calls, (
                f"{tool.__name__} reached the backend for a class the catalogue "
                f"rejected — the APIC answers that with an empty result, not an error"
            )

    def test_the_two_tier_fallback_is_gone(self):
        """No warning path left that admits a class the index does not know."""
        source = self._main_source()
        assert "schema file resolved for this class" not in source
        assert "schemas_dir" not in source
