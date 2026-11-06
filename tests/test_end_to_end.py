"""End to end, offline: import a synthetic specification, generate with a fake
model, validate, review, store and reload."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spec2test.adapters.documents import DEFAULT_IMPORTERS
from spec2test.adapters.fake.model import RuleBasedFakeModel
from spec2test.adapters.persistence import JsonRunStore, run_to_dict
from spec2test.application.generate import GenerationSettings, generate_drafts
from spec2test.application.ingest import content_hash, import_documents
from spec2test.cli import main
from spec2test.domain.models import ReviewStatus, RunStatus
from spec2test.domain.validation import may_approve


def ingest_all(paths):
    results = import_documents(paths, DEFAULT_IMPORTERS)
    return [req for result in results for req in result.requirements], results


def test_the_whole_fixture_set_is_processed(spec_a, spec_pdf, tmp_path):
    requirements, results = ingest_all([spec_a, spec_pdf])
    assert len(requirements) >= 10

    run = generate_drafts(
        requirements,
        RuleBasedFakeModel(),
        settings=GenerationSettings(max_cases_per_unit=2),
        source_hash=content_hash("".join(r.text for r in requirements)),
    )
    assert run.status is RunStatus.COMPLETED
    assert len(run.test_cases) == len(requirements)

    for case in run.test_cases:
        assert case.review_status is ReviewStatus.DRAFT
        assert case.requirement_ids, "every draft references a requirement"
        assert case.source_spans, "every draft carries a source reference"
        assert case.steps and all(step.expectations for step in case.steps)


def test_missing_numeric_values_surface_as_open_questions(spec_a):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements, RuleBasedFakeModel())

    vague = next(r for r in requirements if r.external_id == "RAS-SW-REQ-0007")
    questions = [
        q
        for case in run.test_cases
        if vague.requirement_id in case.requirement_ids
        for q in case.open_questions
    ]
    assert questions, "the requirement without a limit must raise a question"
    assert any("measurable" in q.question or "value" in q.question for q in questions)


def test_a_draft_with_open_questions_cannot_be_approved(spec_a):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements, RuleBasedFakeModel())
    with_questions = [case for case in run.test_cases if case.open_questions]
    assert with_questions
    allowed, report = may_approve(with_questions[0], requirements)
    assert allowed is False
    assert "C_OPEN_QUESTIONS" in report.codes()


def test_a_clean_draft_can_be_approved_by_a_deliberate_decision(spec_a):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements, RuleBasedFakeModel())
    clean = [case for case in run.test_cases if not case.open_questions]
    assert clean, "at least one draft should be free of findings"
    allowed, _ = may_approve(clean[0], requirements)
    assert allowed is True
    approved = clean[0].with_status(ReviewStatus.APPROVED)
    assert approved.review_status is ReviewStatus.APPROVED
    # an edit after approval creates a new revision and removes the approval
    edited = approved.next_revision(objective="sharpened objective")
    assert edited.review_status is ReviewStatus.DRAFT and edited.revision == 2


def test_a_run_survives_storing_and_reloading(spec_a, tmp_path):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements, RuleBasedFakeModel())

    path = JsonRunStore(tmp_path / "runs").save_run(run)
    reloaded = json.loads(path.read_text(encoding="utf-8"))

    assert reloaded["summary"]["status"] == run.status.value
    assert reloaded["summary"]["test_case_drafts"] == len(run.test_cases)
    first = reloaded["run"]["units"][0]
    assert first["attempts"][0]["raw_response"], "the raw answer is preserved"
    assert first["attempts"][0]["prompt_version"]
    assert first["test_cases"][0]["review_status"] == "draft"
    assert first["test_cases"][0]["source_spans"][0]["quote"]
    assert reloaded["run"]["prompt_version"] and reloaded["run"]["schema_version"]


def test_the_store_writes_atomically_and_leaves_no_temporary_file(spec_a, tmp_path):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements[:1], RuleBasedFakeModel())
    store = JsonRunStore(tmp_path / "runs")
    path = store.save_run(run)
    assert path.exists()
    assert list((tmp_path / "runs").glob("*.tmp")) == [], "no temporary file is left"
    assert list((tmp_path / "runs").glob(".*")) == [], "no hidden partial file is left"


def test_run_to_dict_contains_the_provenance():
    from spec2test.domain.models import GenerationRun

    run = GenerationRun(
        run_id="r",
        model="m",
        endpoint="https://ollama.com/api/chat",
        mode="ollama_cloud",
        prompt_version="p",
        schema_version="1.0",
        source_hash="abc",
        parameters={"temperature": 0.2},
    )
    payload = run_to_dict(run)["run"]
    assert payload["endpoint"] == "https://ollama.com/api/chat"
    assert payload["mode"] == "ollama_cloud"
    assert payload["source_hash"] == "abc"
    assert payload["parameters"] == {"temperature": 0.2}


# -------------------------------------------------------------------- cli ----
def test_the_cli_example_run_succeeds_offline(spec_a, tmp_path, capsys):
    code = main(
        ["run", "--spec", str(spec_a), "--mode", "fake", "--out", str(tmp_path / "runs")]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "== drafts for review ==" in output
    assert "completed" in output
    assert "100% means finished, not successful" in output
    assert list((tmp_path / "runs").glob("*.json"))


def test_the_cli_reports_a_failed_run_with_a_non_zero_exit_code(spec_a, tmp_path, monkeypatch):
    from spec2test import cli

    class AlwaysBroken:
        @property
        def descriptor(self):
            from spec2test.application.ports import ModelDescriptor

            return ModelDescriptor(model="broken", endpoint="fake://", mode="fake")

        def chat(self, messages, *, deadline=None):
            from spec2test.application.ports import ModelReply

            return ModelReply(text="not json")

    monkeypatch.setattr(cli, "build_model", lambda *_a, **_k: AlwaysBroken())
    code = cli.main(
        ["run", "--spec", str(spec_a), "--mode", "fake", "--out", str(tmp_path / "r")]
    )
    assert code == 1


def test_the_cli_refuses_an_unknown_document_format(tmp_path, capsys):
    bad = tmp_path / "spec.docx"
    bad.write_bytes(b"x")
    code = main(["run", "--spec", str(bad), "--mode", "fake"])
    assert code == 1
    assert "no importer" in capsys.readouterr().out


# =====================================================================
# AP 5: the full review round trip
# =====================================================================
def test_generate_save_close_open_edit_clarify_approve_reopen(spec_a, tmp_path):
    """The acceptance test for the review workflow.

    generate -> save -> close -> open -> edit -> clarify -> approve -> reopen
    """
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.review import (
        ApprovalBlocked,
        CaseEdit,
        accept_finding,
        approval_report,
        approve_case,
        edit_case,
        project_from_run,
        resolve_question,
        revisions_of,
    )
    from spec2test.domain.models import ReviewStatus

    # --- generate ------------------------------------------------------
    results = import_documents([spec_a], DEFAULT_IMPORTERS)
    requirements = [req for result in results for req in result.requirements]
    documents = [result.document for result in results]
    run = generate_drafts(requirements, RuleBasedFakeModel())
    assert run.status is RunStatus.COMPLETED

    project = project_from_run(run, requirements, documents, name="RAS review")
    path = tmp_path / "ras.s2t.json"

    # --- save and close -------------------------------------------------
    with ProjectStore(path) as store:
        store.save(project)
    assert ProjectStore(path).locked_by_other is False, "the lock is released"
    del project

    # --- open -----------------------------------------------------------
    opened = ProjectStore(path).load()
    assert len(opened.documents) == 1 and opened.documents[0].text
    assert len(opened.requirements) == 10
    assert len(opened.test_cases) == 10
    assert all(c.review_status is ReviewStatus.DRAFT for c in opened.test_cases)

    # a draft that still carries an open question cannot be approved
    with_question = next(c for c in opened.test_cases if c.unresolved_questions)
    with pytest.raises(ApprovalBlocked):
        approve_case(opened, with_question.case_id)

    # --- edit -----------------------------------------------------------
    edited = edit_case(
        opened,
        with_question.case_id,
        CaseEdit(title="Reviewed: " + with_question.title),
        by="anna",
        note="title sharpened",
    )
    assert edited.revision == 2

    # --- clarify ---------------------------------------------------------
    question = edited.unresolved_questions[0]
    resolve_question(
        opened,
        edited.case_id,
        question.question_id,
        "Limit confirmed as 10 deg by the system owner",
        by="anna",
    )

    # --- accept the remaining findings with a reason ---------------------
    allowed, report = approval_report(opened, edited.case_id)
    assert allowed is False, "a structural finding still blocks"
    for issue in list(report.issues):
        accept_finding(
            opened,
            edited.case_id,
            issue.code,
            issue.path,
            "covered by the clarification from the system owner",
            by="anna",
        )

    # --- approve ----------------------------------------------------------
    approved = approve_case(opened, edited.case_id, by="anna", note="checked")
    assert approved.review_status is ReviewStatus.APPROVED
    assert approved.revision == 2

    with ProjectStore(path) as store:
        store.save(opened)

    # --- reopen -----------------------------------------------------------
    again = ProjectStore(path).load()
    final = again.case(approved.case_id)
    assert final.review_status is ReviewStatus.APPROVED
    assert final.revision == 2
    assert final.title.startswith("Reviewed: ")
    stored_question = final.open_questions[0]
    assert stored_question.resolved is True
    assert stored_question.answer.startswith("Limit confirmed")
    assert stored_question.resolved_by == "anna"

    # the earlier revision and the whole decision trail are still there
    assert [c.revision for c in revisions_of(again, final.case_id)] == [1, 2]
    actions = [e.action.value for e in again.history_of(final.case_id)]
    assert actions[0] == "created"
    assert actions[-1] == "approved"
    assert "edited" in actions and "question_resolved" in actions
    # the reason for every accepted finding survives
    assert final.accepted_findings
    assert all(f.justification and f.by == "anna" for f in final.accepted_findings)
    assert again.count_by_status()["approved"] == 1
    assert again.count_by_status()["draft"] == 9
    assert again.rework_statistics()["edits"] == 1


def test_a_source_change_after_approval_is_noticed(spec_a, tmp_path):
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.review import (
        apply_requirement_changes,
        approve_case,
        project_from_run,
    )
    from spec2test.domain.models import ReviewStatus
    from dataclasses import replace

    results = import_documents([spec_a], DEFAULT_IMPORTERS)
    requirements = [req for result in results for req in result.requirements]
    run = generate_drafts(requirements, RuleBasedFakeModel())
    project = project_from_run(
        run, requirements, [r.document for r in results], name="demo"
    )

    clean = next(c for c in project.test_cases if not c.unresolved_questions)
    approve_case(project, clean.case_id)

    # the specification is edited and imported again
    changed = [
        replace(req, text=req.text + " The limit is now 5 deg.")
        if req.requirement_id in clean.requirement_ids
        else req
        for req in requirements
    ]
    touched = apply_requirement_changes(project, changed)

    assert clean.case_id in [c.case_id for c in touched]
    after = project.case(clean.case_id)
    assert after.needs_recheck is True
    assert after.review_status is ReviewStatus.DRAFT, "the approval is withdrawn"

    path = tmp_path / "demo.s2t.json"
    ProjectStore(path).save(project)
    assert ProjectStore(path).load().case(clean.case_id).needs_recheck is True


def test_a_partial_run_can_still_be_saved_and_reopened(spec_a, tmp_path):
    """Partial failures must not destroy the successful results."""
    from spec2test.adapters.fake.model import ScriptedModel
    from spec2test.adapters.persistence.project_store import ProjectStore
    from spec2test.application.contracts import SCHEMA_VERSION
    from spec2test.application.review import project_from_run

    results = import_documents([spec_a], DEFAULT_IMPORTERS)
    requirements = [req for result in results for req in result.requirements][:2]
    good = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "test_cases": [
                {
                    "title": "ok",
                    "objective": "o",
                    "requirement_refs": [requirements[0].external_id],
                    "steps": [
                        {"order": 1, "action": "a", "expectations": ["0.2 deg"]}
                    ],
                }
            ],
        }
    )
    run = generate_drafts(
        requirements, ScriptedModel([good, "broken", "broken", "broken"])
    )
    assert run.status is RunStatus.PARTIAL

    project = project_from_run(
        run, requirements, [r.document for r in results], name="partial"
    )
    path = tmp_path / "partial.s2t.json"
    ProjectStore(path).save(project)
    reopened = ProjectStore(path).load()

    assert len(reopened.test_cases) == 1, "the successful draft survives"
    assert reopened.runs[0].status is RunStatus.PARTIAL
    assert reopened.runs[0].rejected_units, "the failure stays visible"


# =====================================================================
# the CLI produces the deliverable, not only a run file
# =====================================================================
def test_the_cli_writes_the_specification_next_to_the_run(spec_a, tmp_path, capsys):
    """A run on its own is not the result a reader asked for."""
    from spec2test.cli import main

    out = tmp_path / "out"
    code = main(
        [
            "run",
            "--spec",
            str(spec_a),
            "--mode",
            "fake",
            "--out",
            str(out),
            "--title",
            "Test specification: fixture A",
            "--spec-id",
            "TS-FIX-001",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "== test specification ==" in output

    markdown = out / "TS-FIX-001.md"
    payload = out / "TS-FIX-001.json"
    project = out / "TS-FIX-001.s2t.json"
    for path in (markdown, payload, project):
        assert path.exists(), f"{path.name} was not written"

    text = markdown.read_text(encoding="utf-8")
    assert "# Test specification: fixture A" in text
    assert "**Document ID:** TS-FIX-001" in text
    assert "## Test cases" in text
    assert "| # | Kind | Action | Expected result |" in text
    assert "not proof of functional coverage" in text

    data = json.loads(payload.read_text(encoding="utf-8"))
    assert data["document_id"] == "TS-FIX-001"
    assert data["counts"]["test_cases"] == len(data["test_cases"])
    assert data["approved_only"] is False

    # the project file can be reopened for review
    from spec2test.adapters.persistence.project_store import ProjectStore

    reopened = ProjectStore(project).load()
    assert reopened.test_cases
    assert reopened.specification.document_id == "TS-FIX-001"


def test_the_specification_counts_match_the_findings(spec_a, tmp_path, capsys):
    """A clean-looking count would hide the findings the run reported."""
    from spec2test.application.review import project_from_run
    from spec2test.application.specification import build_specification

    results = import_documents([spec_a], DEFAULT_IMPORTERS)
    requirements = [r for result in results for r in result.requirements]
    run = generate_drafts(requirements, RuleBasedFakeModel())
    project = project_from_run(
        run, requirements, [r.document for r in results], name="demo"
    )
    spec = build_specification(project)
    counts = spec.counts()

    reported = sum(len(entry.findings) for entry in spec.entries)
    assert counts["findings"] == reported
    assert counts["cases_with_findings"] <= counts["test_cases"]
    if reported:
        assert f"{reported} validation finding(s)" in spec.coverage_note()


def test_the_specification_can_be_suppressed(spec_a, tmp_path):
    from spec2test.cli import main

    out = tmp_path / "out"
    main(["run", "--spec", str(spec_a), "--mode", "fake", "--out", str(out),
          "--no-spec"])
    assert list(out.glob("*.md")) == []
    assert list(out.glob("run-*.json")), "the run file is still written"
