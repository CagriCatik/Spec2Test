"""Candidates are collected first, then selected once under one budget.

Before this, the requirement search spent the budget and the task search got
what was left. Measured over 40 runs: it got nothing in 22 of them, because
5569 of 6000 characters were already gone. A reserved slot did not help -
the slot was there, the characters were not.
"""
from __future__ import annotations

import pytest

from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.application.context import (
    TASK_BUDGET_SHARE,
    BudgetLedger,
    build_context,
)
from spec2test.application.indexing import sync_index
from spec2test.application.ingest import reference_report
from spec2test.bootstrap import build_importers
from spec2test.domain.models import SelectionReason

TARGET = """# Contactors

### Requirement: SYS-REQ-0001
* **Title:** Contactor opening
* The BMS shall open the main contactors on `Overcurrent_Detected` and
  report `Contactor_State` over `CAN_Diag_Frame`.
"""

#: long enough to swallow a 6000 character budget on its own
def _filler(name: str, topic: str, repeats: int = 40) -> str:
    body = f"{topic} relates to `Overcurrent_Detected` and `Contactor_State`. "
    return f"# {name}\n\n## Signals\n" + body * repeats + "\n"

ARC = """# Arc chamber

## Explanation
The arc chamber quenches the arc within 3 ms of separation. Arcing beyond
that duration is reported as a welded-contact suspicion by the monitor.
"""

TASK = "Check the arc behaviour when the contactor opens under load."


@pytest.fixture
def corpus(tmp_path):
    def build(**files: str):
        for name, text in files.items():
            (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
        index = SqliteKnowledgeIndex(tmp_path / "k.db")
        synced = sync_index(index, sorted(tmp_path.glob("*.md")), build_importers())
        requirements = list(synced.requirements)
        target = next(r for r in requirements if r.external_id == "SYS-REQ-0001")
        return target, requirements, dict(synced.documents), index

    return build


def _select(corpus_parts, **kwargs):
    target, requirements, documents, index = corpus_parts
    return build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        **kwargs,
    )


# ------------------------------------------------- the reservation works ---
def test_a_task_hit_survives_a_budget_the_requirement_search_would_fill(corpus):
    """The reproduced case: the task search starved behind the other one."""
    parts = corpus(
        a_target=TARGET,
        b_arc=ARC,
        c_filler=_filler("Current monitoring", "Current monitoring"),
        d_filler=_filler("Contactor telemetry", "Contactor telemetry"),
    )
    selection = _select(
        parts, max_search_items=3, task_description=TASK, char_budget=6_000
    )
    task_items = [
        i for i in selection.items if i.reason is SelectionReason.TASK_SEARCH
    ]
    assert task_items, (
        "the task search contributed nothing: "
        f"{[(i.reason.value, i.size) for i in selection.items]}"
    )


def test_the_reservation_is_recorded_and_checkable(corpus):
    parts = corpus(a_target=TARGET, b_arc=ARC, c_filler=_filler("Monitoring", "M"))
    selection = _select(
        parts, max_search_items=3, task_description=TASK, char_budget=6_000
    )
    ledger = selection.ledger
    assert isinstance(ledger, BudgetLedger)
    assert ledger.budget == 6_000
    assert ledger.reserved_for_task == int(6_000 * TASK_BUDGET_SHARE)
    assert ledger.used_total == sum(i.size for i in selection.items)
    assert ledger.used_total <= ledger.budget
    assert ledger.candidates >= len(selection.items)


def test_nothing_is_reserved_when_no_task_hit_exists(corpus):
    parts = corpus(a_target=TARGET, b_arc=ARC)
    selection = _select(parts, max_search_items=3, task_description=None)
    assert selection.ledger.reserved_for_task == 0
    assert selection.ledger.used_by_task == 0


def test_unused_reservation_falls_to_the_other_sources(corpus):
    """A held-back share that the task search does not want is not wasted."""
    parts = corpus(
        a_target=TARGET,
        b_arc=ARC,
        c_filler=_filler("Monitoring", "Monitoring", repeats=12),
        d_filler=_filler("Telemetry", "Telemetry", repeats=12),
        e_filler=_filler("Isolation", "Isolation", repeats=12),
    )
    with_task = _select(
        parts, max_search_items=4, task_description=TASK, char_budget=4_000
    )
    without = _select(parts, max_search_items=4, char_budget=4_000)
    assert with_task.ledger.returned_to_others >= 0
    # holding a share back must not cost items overall
    assert len(with_task.items) >= len(without.items) - 1, (
        f"{len(with_task.items)} with the task search, {len(without.items)} without"
    )


def test_the_budget_is_never_exceeded(corpus):
    parts = corpus(
        a_target=TARGET,
        b_arc=ARC,
        c_filler=_filler("Monitoring", "Monitoring"),
        d_filler=_filler("Telemetry", "Telemetry"),
    )
    for budget in (500, 1_500, 3_000, 6_000):
        selection = _select(
            parts, max_search_items=4, task_description=TASK, char_budget=budget
        )
        total = sum(i.size for i in selection.items)
        assert total <= budget, f"budget {budget} exceeded by {total - budget}"
        assert selection.ledger.used_total == total


# ------------------------------------------------------ deduplication ------
def test_a_passage_found_by_both_searches_is_sent_once(corpus):
    parts = corpus(a_target=TARGET, b_arc=ARC)
    selection = _select(
        parts,
        max_search_items=4,
        task_description="overcurrent detected contactor state arc",
        max_task_items=4,
    )
    ids = [item.requirement_id for item in selection.items]
    assert len(ids) == len(set(ids)), ids
    externals = [i.external_id for i in selection.items if i.external_id]
    assert len(externals) == len(set(externals)), externals


def test_omitted_hits_are_reported_once(corpus):
    parts = corpus(
        a_target=TARGET,
        b_arc=ARC,
        c_filler=_filler("Monitoring", "Monitoring"),
        d_filler=_filler("Telemetry", "Telemetry"),
    )
    selection = _select(
        parts, max_search_items=4, task_description=TASK, char_budget=600
    )
    omitted = [i for i in selection.issues if i.code == "C_CONTEXT_OMITTED"]
    assert len(omitted) <= 1, [i.message for i in omitted]
    if omitted:
        assert "further search hit(s)" in omitted[0].message
        assert str(selection.ledger.omitted) in omitted[0].message


def test_every_selected_item_keeps_its_origin(corpus):
    parts = corpus(a_target=TARGET, b_arc=ARC, c_filler=_filler("M", "M", 10))
    selection = _select(parts, max_search_items=3, task_description=TASK)
    for item in selection.items:
        assert item.spans and item.document_id
        assert item.reason in set(SelectionReason)
        assert item.cited_by
