"""Regression tests for the four findings of the L1 review of b5be55b.

P1  the GUI did not carry the cloud mode into the worker
P1  an incomplete run could be reported as ``completed``
P1  run IDs used whole seconds, so results could overwrite each other
P2  the documented CLI call did not work after a normal installation
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from spec2test.adapters.fake.model import ScriptedModel
from spec2test.adapters.persistence import JsonRunStore, RunAlreadyStoredError
from spec2test.application.contracts import SCHEMA_VERSION
from spec2test.application.generate import (
    GenerationSettings,
    generate_drafts,
    new_run_id,
)
from spec2test.domain.models import (
    GenerationRun,
    GenerationUnitResult,
    Requirement,
    RunStatus,
    SourceSpan,
    UnitStatus,
)

ROOT = Path(__file__).resolve().parents[1]


def requirement(rid: str, ext: str) -> Requirement:
    text = f"{ext}: The system shall react within 100 ms."
    return Requirement(
        rid, text, (SourceSpan("doc-1", 0, len(text), text, page=1),), ext
    )


def answer(ref: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "test_cases": [
                {
                    "title": f"draft for {ref}",
                    "objective": "o",
                    "requirement_refs": [ref],
                    "steps": [
                        {
                            "order": 1,
                            "action": "trigger",
                            "expectations": ["reacts within 100 ms"],
                        }
                    ],
                }
            ],
        }
    )


# =====================================================================
# P1: an incomplete run must not be reported as completed
# =====================================================================
def test_an_exhausted_budget_makes_the_run_partial_not_completed():
    """The reproduction from the review: unit 1 succeeds, the budget then ends."""
    import time

    class SlowModel(ScriptedModel):
        def chat(self, messages, *, deadline=None):
            time.sleep(0.2)
            return super().chat(messages, deadline=deadline)

    requirements = [requirement("REQ-1", "SYS-REQ-0001"), requirement("REQ-2", "SYS-REQ-0002")]
    model = SlowModel([answer("SYS-REQ-0001"), answer("SYS-REQ-0002")])

    run = generate_drafts(
        requirements, model, settings=GenerationSettings(job_timeout_s=0.15)
    )

    assert run.status is RunStatus.PARTIAL, "a half-processed run is not completed"
    assert len(run.test_cases) == 1, "the finished draft is kept"
    assert model.call_count == 1, "no request after the budget ended"


def test_a_requirement_that_was_never_started_is_recorded_explicitly():
    import time

    class SlowModel(ScriptedModel):
        def chat(self, messages, *, deadline=None):
            time.sleep(0.2)
            return super().chat(messages, deadline=deadline)

    requirements = [requirement("REQ-1", "SYS-REQ-0001"), requirement("REQ-2", "SYS-REQ-0002")]
    run = generate_drafts(
        requirements,
        SlowModel([answer("SYS-REQ-0001"), answer("SYS-REQ-0002")]),
        settings=GenerationSettings(job_timeout_s=0.15),
    )

    not_started = run.not_started_units
    assert len(not_started) == 1
    unit = not_started[0]
    assert unit.requirement_ids == ("REQ-2",)
    assert unit.source_spans[0].page == 1, "the source reference is kept"
    assert {issue.code for issue in unit.issues} == {"E_UNIT_NOT_STARTED"}

    report = "\n".join(run.report_lines())
    assert "NOT STARTED" in report
    assert "REQ-2" in report
    assert "not started=1" in report


def test_planned_and_processed_units_are_compared():
    requirements = [requirement("REQ-1", "SYS-REQ-0001"), requirement("REQ-2", "SYS-REQ-0002")]
    run = generate_drafts(
        requirements,
        ScriptedModel([answer("SYS-REQ-0001"), answer("SYS-REQ-0002")]),
    )
    assert run.planned_unit_count == 2
    assert len(run.processed_units) == 2
    assert run.is_incomplete is False
    assert run.status is RunStatus.COMPLETED


def test_a_run_whose_units_vanished_is_never_completed():
    """Defence in depth: the counter alone already prevents a false success."""
    run = GenerationRun(
        run_id="r",
        model="m",
        endpoint="e",
        mode="fake",
        prompt_version="p",
        schema_version="1.0",
        source_hash="h",
        planned_unit_count=3,
    )
    accepted = GenerationUnitResult("U1", ("REQ-1",), UnitStatus.ACCEPTED)
    accepted.test_cases = [object()]  # type: ignore[list-item]
    run.units = [accepted]
    assert run.is_incomplete is True
    assert run.resolve_status() is RunStatus.PARTIAL


def test_cancellation_still_wins_over_partial_and_records_the_rest():
    requirements = [requirement("REQ-1", "SYS-REQ-0001"), requirement("REQ-2", "SYS-REQ-0002")]
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    run = generate_drafts(
        requirements,
        ScriptedModel([answer("SYS-REQ-0001"), answer("SYS-REQ-0002")]),
        should_cancel=should_cancel,
    )
    assert run.status is RunStatus.CANCELLED
    assert [u.requirement_ids for u in run.not_started_units] == [("REQ-2",)]


# =====================================================================
# P1: run IDs must not collide, results must not be overwritten
# =====================================================================
def test_run_ids_generated_in_the_same_second_differ():
    ids = {new_run_id() for _ in range(200)}
    assert len(ids) == 200, "run IDs collide"


def test_a_run_id_is_sortable_and_carries_a_unique_suffix():
    run_id = new_run_id()
    assert re.fullmatch(r"run-\d{8}T\d{6}-[0-9a-f]{12}", run_id), run_id


def test_two_runs_in_the_same_second_keep_both_result_files(tmp_path):
    store = JsonRunStore(tmp_path / "runs")
    paths = []
    for ref, rid in (("SYS-REQ-0001", "REQ-1"), ("SYS-REQ-0002", "REQ-2")):
        run = generate_drafts([requirement(rid, ref)], ScriptedModel([answer(ref)]))
        paths.append(store.save_run(run))

    assert paths[0] != paths[1], "the second run overwrote the first"
    assert len({p.name for p in paths}) == 2
    titles = {
        json.loads(path.read_text(encoding="utf-8"))["run"]["units"][0]["test_cases"][0][
            "title"
        ]
        for path in paths
    }
    assert titles == {"draft for SYS-REQ-0001", "draft for SYS-REQ-0002"}


def test_storing_over_an_existing_run_is_refused(tmp_path):
    store = JsonRunStore(tmp_path / "runs")
    run = generate_drafts([requirement("REQ-1", "SYS-REQ-0001")], ScriptedModel([answer("SYS-REQ-0001")]))
    store.save_run(run)
    with pytest.raises(RunAlreadyStoredError):
        store.save_run(run)
    assert store.save_run(run, overwrite=True).exists(), "deliberate replace stays possible"


def test_a_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    store = JsonRunStore(tmp_path / "runs")
    run = generate_drafts([requirement("REQ-1", "SYS-REQ-0001")], ScriptedModel([answer("SYS-REQ-0001")]))

    import os

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save_run(run)

    directory = tmp_path / "runs"
    assert list(directory.glob("*.json")) == []
    assert list(directory.iterdir()) == [], "a temporary file was left behind"


def test_the_legacy_bridge_also_uses_unique_run_ids():
    from spec2test.application.legacy import generate_legacy_cases

    class Agent:
        def generate(self, content, *, system_prompt=None):
            return json.dumps(
                [
                    {
                        "testcase_name": "t",
                        "objective": "o",
                        "actions": ["a"],
                        "expected_results": ["e"],
                        "postconditions": ["p"],
                    }
                ]
            )

    ids = set()
    for _ in range(5):
        _cases, run = generate_legacy_cases(
            ["page one"], Agent(), document_id="d.pdf", combine=1
        )
        ids.add(run.run_id)
    assert len(ids) == 5


# =====================================================================
# P2: the package must be installable with a working CLI entry point
# =====================================================================
def test_pyproject_declares_an_installable_package_and_cli_entry_point():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "spec2test"
    assert data["project"]["scripts"]["spec2test"] == "spec2test.cli:main"
    assert data["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_the_declared_version_matches_the_package():
    import spec2test

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == spec2test.__version__


def test_the_runtime_dependencies_cover_what_the_package_imports():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = " ".join(data["project"]["dependencies"]).lower()
    for package in ("requests", "pydantic", "pyyaml", "pymupdf"):
        assert package in declared, f"{package} is imported but not declared"
    # Qt and the exporters belong to the optional GUI extra, not to the core
    assert "pyside6" not in declared
    gui_extra = " ".join(data["project"]["optional-dependencies"]["gui"]).lower()
    assert "pyside6" in gui_extra and "openpyxl" in gui_extra


def test_the_cli_entry_point_is_callable():
    from spec2test.cli import main

    assert callable(main)
    with pytest.raises(SystemExit):
        main(["--help"])


def test_the_configuration_is_looked_up_in_the_working_directory(tmp_path, monkeypatch):
    """An installed command must use the config of the project it runs in."""
    from spec2test import bootstrap

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yml").write_text(
        "cloud:\n  mode: ollama_local\nmodel:\n  name: local-model\n", encoding="utf-8"
    )
    assert bootstrap.default_config_path() == tmp_path / "config.yml"
    config = bootstrap.load_config()
    assert config["model"]["name"] == "local-model"


def test_a_missing_configuration_is_not_an_error(tmp_path, monkeypatch):
    from spec2test import bootstrap

    monkeypatch.setattr(bootstrap, "DEFAULT_CONFIG_PATH", tmp_path / "nope.yml")
    monkeypatch.chdir(tmp_path)
    assert bootstrap.default_config_path() is None
    assert bootstrap.load_config() == {}
