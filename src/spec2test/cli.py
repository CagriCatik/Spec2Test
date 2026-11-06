"""Example end-to-end run: import a specification, generate drafts, show them.

    python -m spec2test.cli run --spec tests/fixtures/specs/spec_a.md --mode fake
    python -m spec2test.cli run --spec my_spec.pdf --mode ollama_cloud --model gpt-oss:120b
    python -m spec2test.cli models --mode ollama_cloud

Exit codes: 0 completed, 2 partial, 3 cancelled, 1 failed or aborted.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from .adapters.ollama.client import API_KEY_ENV, MODE_CLOUD, MODE_LOCAL, OllamaError
from .adapters.ollama.models import capabilities_for, check_model_choice, list_models
from .adapters.export import save_json, save_markdown, save_specification_xlsx
from .adapters.persistence import JsonRunStore
from .adapters.persistence.project_store import PROJECT_SUFFIX, ProjectStore
from .application.review import project_from_run
from .application.specification import build_specification
from .application.generate import ProgressUpdate, generate_drafts
from .application.ingest import content_hash, import_documents, reference_report
from .bootstrap import (
    ALL_MODES,
    MODE_FAKE,
    build_importers,
    build_model,
    generation_settings_from_config,
    load_config,
    load_env_file,
    ollama_settings_from_config,
)
from .domain.models import RunStatus

EXIT_CODES = {
    RunStatus.COMPLETED: 0,
    RunStatus.PARTIAL: 2,
    RunStatus.CANCELLED: 3,
    RunStatus.FAILED: 1,
    RunStatus.RUNNING: 1,
}


def _out(text: str = "") -> None:
    print(text, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec2test", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="import a specification and generate drafts")
    run.add_argument("--spec", action="append", required=True, type=Path,
                     help="TXT, Markdown or PDF file (repeatable)")
    run.add_argument(
        "--mode",
        choices=ALL_MODES,
        default=None,
        help="default: the mode from config.yml, else fake",
    )
    run.add_argument("--model", default=None)
    run.add_argument("--host", default=None, help="override the API host")
    run.add_argument("--max-cases", type=int, default=None)
    run.add_argument(
        "--context",
        dest="context",
        action="store_true",
        default=None,
        help="send requirements cited by the ones under test (default: on)",
    )
    run.add_argument(
        "--no-context", dest="context", action="store_false",
        help="generate without supporting context, for a comparison",
    )
    run.add_argument(
        "--retrieval", dest="retrieval", action="store_true", default=None,
        help="also search the knowledge index for related sections",
    )
    run.add_argument(
        "--no-retrieval", dest="retrieval", action="store_false",
        help="use explicit references only",
    )
    run.add_argument(
        "--index", type=Path, default=None,
        help="knowledge index file (default: .spec2test/knowledge.db)",
    )
    run.add_argument(
        "--knowledge", type=Path, default=None,
        help="folder of documents to index before generating",
    )
    run.add_argument("--max-repairs", type=int, default=None)
    run.add_argument("--group-size", type=int, default=None)
    run.add_argument("--job-timeout", type=float, default=None)
    run.add_argument(
        "--out", type=Path, default=None,
        help="directory for the run file and the specification",
    )
    run.add_argument(
        "--title", default=None, help="title of the test specification"
    )
    run.add_argument(
        "--spec-id", default=None, help="document ID of the test specification"
    )
    run.add_argument(
        "--no-spec", dest="write_spec", action="store_false", default=True,
        help="write only the run file, no specification",
    )
    run.add_argument("--config", type=Path, default=None)

    models = sub.add_parser("models", help="list the models of the configured host")
    models.add_argument(
        "--mode",
        choices=[MODE_CLOUD, MODE_LOCAL],
        default=None,
        help="default: the mode from config.yml, else ollama_local",
    )
    models.add_argument("--model", default=None, help="check this name against the list")
    models.add_argument("--host", default=None)
    models.add_argument("--config", type=Path, default=None)
    return parser


# --------------------------------------------------------------- commands ---
def _resolve_mode(config: dict, requested: str | None, fallback: str) -> str:
    """An explicit --mode wins; otherwise config.yml decides."""
    return requested or (config.get("cloud") or {}).get("mode") or fallback


def _cmd_models(args) -> int:
    config = load_config(args.config)
    args.mode = _resolve_mode(config, args.mode, MODE_LOCAL)
    if args.mode not in (MODE_CLOUD, MODE_LOCAL):
        _out(f"mode {args.mode!r} has no model list; use ollama_cloud or ollama_local")
        return 1
    settings = ollama_settings_from_config(config, mode=args.mode, model=args.model or "unused")
    if args.host:
        settings = dataclasses.replace(settings, host=args.host)
    client = build_model(args.mode, settings, log=_out)
    try:
        names = list_models(client)
    except OllamaError as exc:
        _out(f"model list failed: {exc}")
        return 1
    _out(f"{len(names)} model(s) at {settings.resolved_host}:")
    for name in names:
        _out(f"  {name}")
    if args.model:
        caps = capabilities_for(args.model, args.mode)
        for warning in check_model_choice(args.model, names, args.mode):
            _out(f"warning: {warning}")
        for note in caps.notes:
            _out(f"note: {note}")
    return 0


def _cmd_run(args) -> int:
    config = load_config(args.config)
    args.mode = _resolve_mode(config, args.mode, MODE_FAKE)
    _out(f"mode: {args.mode}")
    gen_settings = generation_settings_from_config(config)
    if args.max_cases is not None:
        gen_settings.max_cases_per_unit = args.max_cases
    if args.max_repairs is not None:
        gen_settings.max_repair_attempts = args.max_repairs
    if args.group_size is not None:
        gen_settings.group_size = args.group_size
    if args.job_timeout is not None:
        gen_settings.job_timeout_s = args.job_timeout
    if args.context is not None:
        gen_settings.use_reference_context = args.context
    if args.retrieval is not None:
        gen_settings.use_retrieval = args.retrieval

    ollama_settings = None
    if args.mode in (MODE_CLOUD, MODE_LOCAL):
        ollama_settings = ollama_settings_from_config(
            config, mode=args.mode, model=args.model
        )
        if args.host:
            ollama_settings = dataclasses.replace(ollama_settings, host=args.host)
        caps = capabilities_for(ollama_settings.model, args.mode)
        for note in caps.notes:
            _out(f"note: {note}")
        if args.mode == MODE_CLOUD:
            _out(f"cloud mode: the API key is read from ${API_KEY_ENV} and never stored")

    try:
        model = build_model(args.mode, ollama_settings, log=_out)
    except ValueError as exc:
        _out(f"configuration error: {exc}")
        return 1

    # --- import ----------------------------------------------------------
    _out("== import ==")
    try:
        results = import_documents(args.spec, build_importers())
    except (OSError, ValueError) as exc:
        _out(f"import failed: {exc}")
        return 1

    requirements = []
    documents = {}
    for result in results:
        doc = result.document
        documents[doc.document_id] = doc
        _out(
            f"{doc.filename}: format={doc.format.value} pages={doc.page_count} "
            f"hash={doc.content_hash[:12]} requirements={len(result.requirements)}"
        )
        for warning in result.warnings:
            _out(f"  warning: {warning}")
        for issue in result.issues:
            _out(f"  {issue}")
        for req in result.requirements:
            where = "; ".join(span.label() for span in req.spans)
            _out(f"  {req.requirement_id}  ext={req.external_id or '-'}  [{where}]")
            _out(f"      status={req.clarification_status.value}  "
                 f"{req.text.splitlines()[0][:88]}")
        requirements.extend(result.requirements)

    if not requirements:
        _out("no requirement captured; nothing to generate")
        return 1

    # --- generate --------------------------------------------------------
    _out()
    _out("== generation ==")

    def progress(update: ProgressUpdate) -> None:
        _out(
            f"[{update.percent:3d}%] {update.done}/{update.total} "
            f"{update.unit_id}: {update.note}"
            + ("   (100% means finished, not successful)" if update.percent == 100 else "")
        )

    source = content_hash("\n".join(r.text for r in requirements))
    catalogue = list(requirements)
    knowledge = None
    if gen_settings.use_retrieval:
        from .adapters.knowledge import SqliteKnowledgeIndex
        from .application.indexing import collect_documents, sync_index

        index_path = args.index or (Path.cwd() / ".spec2test" / "knowledge.db")
        knowledge = SqliteKnowledgeIndex(index_path)
        folder = args.knowledge
        if folder is not None:
            report_sync = sync_index(
                knowledge, collect_documents(folder), build_importers()
            )
            _out(f"knowledge index {index_path}: {report_sync.summary()}")
            for document in report_sync.documents.values():
                documents.setdefault(document.document_id, document)
            # The knowledge base supplies context, it does not widen the test
            # scope: only --spec decides what gets test cases.
            known = {r.requirement_id for r in catalogue}
            catalogue.extend(
                r for r in report_sync.requirements if r.requirement_id not in known
            )
            _out(
                f"knowledge base: {len(catalogue) - len(requirements)} further "
                "requirement(s) available as context"
            )
        else:
            _out(f"knowledge index {index_path}: reused as it is")

    if gen_settings.use_reference_context:
        report = reference_report(catalogue)
        cited = sum(len(v) for v in report.citations.values())
        _out(
            f"reference context: {cited} citation(s), "
            f"{len(report.unresolved)} without a target"
        )
    try:
        run = generate_drafts(
            requirements,
            model,
            settings=gen_settings,
            source_hash=source,
            progress=progress,
            log=_out,
            documents=documents,
            catalogue=catalogue,
            knowledge=knowledge,
        )
    except OllamaError as exc:
        _out(f"generation aborted: {exc}")
        return 1

    # --- review view -----------------------------------------------------
    _out()
    _out("== drafts for review ==")
    for case in run.test_cases:
        refs = ", ".join(case.requirement_ids)
        _out(f"{case.case_id}  [{case.review_status.value} rev{case.revision}]  {case.title}")
        _out(f"  requirements: {refs}")
        for span in case.source_spans:
            _out(f"  source: {span.label()}")
        for step in case.steps:
            _out(f"  step {step.order}: {step.action}")
            for expectation in step.expectations:
                _out(f"      expect: {expectation}")
        for question in case.open_questions:
            _out(f"  open question: {question.question}")
    if not run.test_cases:
        _out("(no accepted draft)")

    questions = run.open_questions
    if questions:
        _out()
        _out("== open questions ==")
        for question in questions:
            _out(f"{question.question_id} ({question.origin.value}): {question.question}")

    if run.context_items:
        _out()
        _out("== supporting context sent ==")
        for unit in run.units:
            if not unit.context:
                continue
            _out(f"-- {unit.unit_id} [{', '.join(unit.requirement_ids)}]")
            for item in unit.context:
                _out(f"   {item.external_id or item.heading or item.requirement_id}")
                _out(
                    f"     kind={item.kind.value} reason={item.reason.value}"
                    + (f" matched={', '.join(item.matched_terms[:4])}"
                       if item.matched_terms else "")
                )
                _out(f"     source: {item.label}")
                for span in item.spans:
                    _out(f"     position: {span.label()}")

    _out()
    _out("== report ==")
    for line in run.report_lines():
        _out(line)

    out_dir = args.out or (Path.cwd() / "runs")
    path = JsonRunStore(out_dir).save_run(run)
    _out(f"run saved: {path}")

    if args.write_spec and run.test_cases:
        _out()
        _out("== test specification ==")
        project = project_from_run(
            run,
            catalogue,
            list(documents.values()),
            name=args.spec[0].stem if args.spec else "project",
        )
        project.specification.title = args.title or (
            f"Test specification: {args.spec[0].stem}" if args.spec else
            "Test specification"
        )
        project.specification.document_id = args.spec_id or f"TS-{run.run_id[-8:]}"
        spec = build_specification(project)
        base = Path(out_dir) / project.specification.document_id
        written = [
            save_markdown(spec, base.with_suffix(".md")),
            save_json(spec, base.with_suffix(".json")),
        ]
        try:
            written.append(save_specification_xlsx(spec, base.with_suffix(".xlsx")))
        except ImportError:  # pragma: no cover - pandas/openpyxl are optional
            _out("Excel export skipped: pandas and openpyxl are not installed")
        # the project file keeps the drafts reviewable later
        project_path = Path(out_dir) / f"{project.specification.document_id}{PROJECT_SUFFIX}"
        try:
            ProjectStore(project_path).save(project)
            written.append(project_path)
        except OSError as exc:
            _out(f"project could not be saved: {exc}")

        counts = spec.counts()
        _out(
            f"{counts['test_cases']} draft(s) for "
            f"{counts['requirements_in_scope'] - counts['uncovered_requirements']} of "
            f"{counts['requirements_in_scope']} requirement(s) in scope, "
            f"{counts['open_questions']} unanswered question(s), "
            f"{counts['findings']} validation finding(s)"
        )
        _out(f"note: {spec.coverage_note()}")
        for item in written:
            _out(f"written: {item}")
        _out(
            "Open the project file in the window to review, approve and export "
            "the approved subset."
        )

    return EXIT_CODES.get(run.status, 1)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # a .env in the working directory may provide OLLAMA_API_KEY; only the
    # variable names are reported, never their values
    for name in load_env_file():
        _out(f"loaded {name} from .env")
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "models":
        return _cmd_models(args)
    return 1  # pragma: no cover - argparse enforces a command


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
