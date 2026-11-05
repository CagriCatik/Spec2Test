"""End to end, offline: import a synthetic specification, generate with a fake
model, validate, review, store and reload."""
from __future__ import annotations

import json
from pathlib import Path

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


def test_the_store_writes_atomically(spec_a, tmp_path):
    requirements, _ = ingest_all([spec_a])
    run = generate_drafts(requirements[:1], RuleBasedFakeModel())
    store = JsonRunStore(tmp_path / "runs")
    first = store.save_run(run)
    run.run_id = run.run_id  # same id, overwrite
    second = store.save_run(run)
    assert first == second
    assert list((tmp_path / "runs").glob("*.tmp")) == [], "no temporary file is left"


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
