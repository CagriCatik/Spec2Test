"""Full-text search as context: which passages the selection may reach.

Two thirds of a typical index are requirement passages. They were indexed
but never searched, so a requirement that defines a signal could only become
context when some document happened to cite it by ID.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.application.context import (
    REQUIREMENT_BUDGET_SHARE,
    SEARCH_TIERS,
    build_context,
)
from spec2test.application.indexing import sync_index
from spec2test.application.ingest import reference_report
from spec2test.bootstrap import build_importers
from spec2test.domain.models import ContextKind, SelectionReason

TARGET = """# Current validation

### Requirement: SYS-REQ-0001
* **Title:** Pack current validation
* The BMS shall validate the pack current from `Shunt_Current_Raw` and
  `Hall_Current_Raw` and raise `Current_Sensor_Plausibility_Error`.
"""

SIBLING = """# Current sensing

### Requirement: SYS-REQ-0002
* **Title:** Shunt sampling period
* `Shunt_Current_Raw` shall be sampled with a period of 10 ms and a
  tolerance of 2 A across the full range of `Hall_Current_Raw`.
"""

SIGNALS = """# Signal definitions

## Signals
`Current_Sensor_Plausibility_Error` is a boolean status signal raised by the
current validation of `Shunt_Current_Raw`.
"""


@pytest.fixture
def corpus(tmp_path):
    """Build an index and return (target requirement, all, documents, index)."""

    def build(**files: str):
        for name, text in files.items():
            (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
        paths = sorted(tmp_path.glob("*.md"))
        index = SqliteKnowledgeIndex(tmp_path / "k.db")
        synced = sync_index(index, paths, build_importers())
        requirements = list(synced.requirements)
        target = next(r for r in requirements if r.external_id == "SYS-REQ-0001")
        return target, requirements, dict(synced.documents), index

    return build


def _select(target, requirements, documents, index, **kwargs):
    return build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        **kwargs,
    )


# ---------------------------------------------- the requirement tier --------
def test_a_matching_requirement_is_selected(corpus):
    """Reproduced: the direct search found it, the selection returned none."""
    target, requirements, documents, index = corpus(a_target=TARGET, b_sibling=SIBLING)

    direct = index.search(["Shunt_Current_Raw"], limit=10)
    assert any(h.passage.kind is ContextKind.REQUIREMENT for h in direct), (
        "the index does not even hold a requirement passage for the signal"
    )

    selection = _select(target, requirements, documents, index, max_search_items=3)
    chosen = {item.external_id for item in selection.items}
    assert "SYS-REQ-0002" in chosen, (
        f"the related requirement was not selected: {chosen}"
    )


def test_the_requirement_under_test_is_never_its_own_context(corpus):
    target, requirements, documents, index = corpus(a_target=TARGET, b_sibling=SIBLING)
    selection = _select(target, requirements, documents, index, max_search_items=5)
    assert target.external_id not in {i.external_id for i in selection.items}
    assert target.requirement_id not in {i.requirement_id for i in selection.items}


def test_a_repeated_id_in_another_document_is_not_context_either(corpus):
    """The same requirement, indexed twice, must not come back as its own."""
    target, requirements, documents, index = corpus(
        a_target=TARGET,
        b_copy=TARGET.replace("# Current validation", "# Copy of the same"),
    )
    selection = _select(target, requirements, documents, index, max_search_items=5)
    assert "SYS-REQ-0001" not in {i.external_id for i in selection.items}


def test_signal_sections_still_come_first(corpus):
    target, requirements, documents, index = corpus(
        a_target=TARGET, b_sibling=SIBLING, c_signals=SIGNALS
    )
    selection = _select(target, requirements, documents, index, max_search_items=3)
    kinds = [item.kind for item in selection.items]
    assert ContextKind.SIGNAL in kinds, kinds
    assert kinds.index(ContextKind.SIGNAL) < len(kinds) - 1 or len(kinds) == 1
    assert kinds[0] is not ContextKind.REQUIREMENT, (
        "a requirement must not outrank a signal section"
    )


def test_a_requirement_survives_a_budget_that_signal_sections_would_fill(corpus):
    """A reserved slot alone did nothing: the budget was already spent."""
    filler = "# Signals\n\n## Signals\n" + (
        "`Shunt_Current_Raw` carries the raw shunt measurement. " * 60
    )
    target, requirements, documents, index = corpus(
        a_target=TARGET, b_sibling=SIBLING, c_signals=filler
    )
    selection = _select(
        target, requirements, documents, index, max_search_items=3, char_budget=6_000
    )
    assert "SYS-REQ-0002" in {i.external_id for i in selection.items}


def test_hits_that_do_not_fit_are_reported_once_not_once_each(corpus):
    long_sibling = SIBLING + ("\n* Further detail about the sampling. " * 200)
    target, requirements, documents, index = corpus(
        a_target=TARGET, b_sibling=long_sibling, c_signals=SIGNALS
    )
    selection = _select(
        target, requirements, documents, index, max_search_items=5, char_budget=600
    )
    omitted = [i for i in selection.issues if i.code == "C_CONTEXT_OMITTED"]
    assert len(omitted) <= 1, [i.message for i in omitted]
    if omitted:
        assert "further search hit(s)" in omitted[0].message


def test_a_selected_requirement_carries_its_origin(corpus):
    target, requirements, documents, index = corpus(a_target=TARGET, b_sibling=SIBLING)
    selection = _select(target, requirements, documents, index, max_search_items=3)
    item = next(i for i in selection.items if i.external_id == "SYS-REQ-0002")
    assert item.reason is SelectionReason.FULL_TEXT_SEARCH
    assert item.kind is ContextKind.REQUIREMENT
    assert item.spans and item.spans[0].quote
    assert item.document_filename == "b_sibling.md"
    assert item.matched_terms, "the terms that found it are not recorded"
    assert item.cited_by == ("SYS-REQ-0001",)


# ---------------------------------------------- no kind may be invisible ----
def test_every_context_kind_is_searched_by_some_tier():
    """A new ContextKind must not silently become unreachable."""
    searched = {kind for tier in SEARCH_TIERS for kind in tier}
    missing = set(ContextKind) - searched
    assert missing == set(), f"these kinds are indexed but never searched: {missing}"


def test_the_reserved_share_leaves_most_of_the_budget_to_the_preferred_kinds():
    assert 0 < REQUIREMENT_BUDGET_SHARE < 0.5
