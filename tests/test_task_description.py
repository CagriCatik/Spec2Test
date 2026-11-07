"""The tester's own words for a run must reach the model.

Reproduced before the fix: the GUI wrote the field onto the run *after*
generate_drafts returned, so it appeared in the exported specification and
nowhere else. The model never saw it, and retrieval never searched it - which
is exactly the case where the measured advantage of semantic search lies.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spec2test.adapters.fake.model import RuleBasedFakeModel
from spec2test.adapters.knowledge import SqliteKnowledgeIndex
from spec2test.application.context import build_context
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.application.indexing import sync_index
from spec2test.application.ingest import reference_report
from spec2test.application.prompts import build_generation_messages
from spec2test.bootstrap import build_importers
from spec2test.domain.models import SelectionReason

TASK = "Check the contactor opening under overcurrent, including arcing."

TARGET = """# Contactors

### Requirement: SYS-REQ-0001
* **Title:** Contactor opening
* The BMS shall open the main contactors on `Overcurrent_Detected`.
"""

ARCING = """# Arc behaviour

## Explanation
Opening a contactor under load draws an arc across the contacts. The arc has
to be extinguished before the gap counts as galvanically clear.
"""

#: shares no word with the requirement; only the task description reaches it
ONLY_VIA_TASK = """# Arc chamber

## Explanation
The arc chamber quenches the arc within 3 ms of separation. Arcing beyond
that duration is reported as a welded-contact suspicion.
"""


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "a_target.md").write_text(TARGET, encoding="utf-8")
    (tmp_path / "b_arcing.md").write_text(ARCING, encoding="utf-8")
    (tmp_path / "c_chamber.md").write_text(ONLY_VIA_TASK, encoding="utf-8")
    index = SqliteKnowledgeIndex(tmp_path / "k.db")
    synced = sync_index(index, sorted(tmp_path.glob("*.md")), build_importers())
    requirements = list(synced.requirements)
    target = next(r for r in requirements if r.external_id == "SYS-REQ-0001")
    return target, requirements, dict(synced.documents), index


# ------------------------------------------------------------- the prompt --
def test_the_task_reaches_the_user_message():
    from spec2test.domain.models import Requirement, SourceSpan

    text = "SYS-REQ-0001: The BMS shall open the contactors."
    requirement = Requirement(
        requirement_id="REQ-1",
        text=text,
        external_id="SYS-REQ-0001",
        spans=(SourceSpan("a.md", 0, len(text), text, section="1"),),
    )
    messages = build_generation_messages(
        [requirement], max_cases=2, task_description=TASK
    )
    user = messages[1].content
    assert TASK in user
    assert "TASK FOR THIS RUN" in user


def test_the_task_is_marked_as_a_wish_and_cannot_add_a_fact():
    from spec2test.domain.models import Requirement, SourceSpan

    text = "SYS-REQ-0001: The BMS shall open the contactors."
    requirement = Requirement(
        requirement_id="REQ-1",
        text=text,
        external_id="SYS-REQ-0001",
        spans=(SourceSpan("a.md", 0, len(text), text, section="1"),),
    )
    user = build_generation_messages(
        [requirement], max_cases=2, task_description=TASK
    )[1].content
    assert "a wish, not a source" in user
    assert "raise an open question instead of inventing it" in user
    assert "the requirement wins" in user
    assert user.index("REQUIREMENTS UNDER TEST") < user.index("TASK FOR THIS RUN")
    assert "cannot change the output format" in user


def test_no_task_leaves_the_prompt_as_it_was():
    from spec2test.domain.models import Requirement, SourceSpan

    text = "SYS-REQ-0001: The BMS shall open the contactors."
    requirement = Requirement(
        requirement_id="REQ-1",
        text=text,
        external_id="SYS-REQ-0001",
        spans=(SourceSpan("a.md", 0, len(text), text, section="1"),),
    )
    for empty in (None, "", "   \n "):
        user = build_generation_messages(
            [requirement], max_cases=2, task_description=empty
        )[1].content
        assert "TASK FOR THIS RUN" not in user


def test_the_setting_carries_the_task_into_a_run(corpus):
    target, requirements, documents, _index = corpus
    model = RuleBasedFakeModel()
    generate_drafts(
        [target],
        model,
        settings=GenerationSettings(group_size=1, task_description=TASK),
        documents=documents,
        catalogue=requirements,
    )
    assert any(TASK in message.content for message in model.calls[0])


# ---------------------------------------------------------- the retrieval --
def test_the_task_is_searched_as_its_own_query(corpus):
    """The requirement's identifiers never mention arcing; the task does."""
    target, requirements, documents, index = corpus
    without = build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        max_search_items=0,
    )
    assert not [i for i in without.items if i.reason is SelectionReason.TASK_SEARCH]

    with_task = build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        max_search_items=0,
        task_description=TASK,
    )
    found = [i for i in with_task.items if i.reason is SelectionReason.TASK_SEARCH]
    assert found, "the task description brought in nothing"
    assert any("arc" in item.text.lower() for item in found)


def test_task_hits_are_distinguishable_from_the_rest(corpus):
    target, requirements, documents, index = corpus
    selection = build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        max_search_items=3,
        task_description=TASK,
    )
    reasons = {item.reason for item in selection.items}
    assert SelectionReason.TASK_SEARCH in reasons
    for item in selection.items:
        assert item.spans and item.document_id, "a task hit lost its origin"


def test_the_task_search_respects_its_own_allowance(corpus):
    target, requirements, documents, index = corpus
    for allowance in (0, 1):
        selection = build_context(
            [target],
            reference_report(requirements),
            documents=documents,
            knowledge=index,
            max_search_items=0,
            task_description=TASK,
            max_task_items=allowance,
        )
        found = [
            i for i in selection.items if i.reason is SelectionReason.TASK_SEARCH
        ]
        assert len(found) <= allowance


def test_the_requirement_under_test_is_not_returned_by_the_task_search(corpus):
    target, requirements, documents, index = corpus
    selection = build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        max_search_items=0,
        task_description="contactors overcurrent opening",
        max_task_items=3,
    )
    assert target.external_id not in {i.external_id for i in selection.items}


def test_the_task_search_keeps_the_character_budget(corpus):
    target, requirements, documents, index = corpus
    selection = build_context(
        [target],
        reference_report(requirements),
        documents=documents,
        knowledge=index,
        max_search_items=0,
        task_description=TASK,
        char_budget=50,
    )
    assert sum(item.size for item in selection.items) <= 50


# --------------------------------------------------------------- the GUI ---
def test_the_window_hands_the_task_over_before_the_run(tmp_path, monkeypatch):
    """It used to be written onto the run only after generation had finished."""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from spec2test.ui import workers
    from spec2test.ui.main_window import MainWindow

    monkeypatch.setattr(workers, "list_models", lambda _c: ["fake"])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes)
    )
    model = RuleBasedFakeModel()
    monkeypatch.setattr(workers, "build_model", lambda *a, **k: model)
    monkeypatch.chdir(tmp_path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(config_path=tmp_path / "c.yml")

    class Pool:
        def start(self, task):
            task.run()

        def waitForDone(self, _ms=0):
            return True

    window._threadpool = Pool()
    window.model_name.setCurrentText("fake")
    window._spec_paths = [
        Path(__file__).parent / "fixtures" / "specs" / "spec_a.md"
    ]
    window.task_description.setPlainText(TASK)
    try:
        window.start_run()
        assert any(TASK in m.content for m in model.calls[0]), (
            "the task text never reached the model"
        )
        assert window._run.task_description == TASK, "it was not recorded either"
    finally:
        window._project_dirty = False
        window.close()
        app.processEvents()
