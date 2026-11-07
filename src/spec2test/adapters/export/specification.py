"""Export of the test specification: Markdown, JSON and Excel.

All three are rendered from the same structured specification, so they cannot
disagree. Actions and their expectations stay together, unresolved points stay
visible, and a draft export says plainly that it is one.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ...application.specification import TestSpecification, steps_of


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


# ------------------------------------------------------------- markdown ----
def to_markdown(spec: TestSpecification) -> str:
    counts = spec.counts()
    out: list[str] = []
    add = out.append

    add(f"# {spec.title}")
    add("")
    add(f"**Document ID:** {spec.document_id}  ")
    add(f"**Revision:** {spec.revision}  ")
    add(f"**Created:** {spec.created_at}  ")
    add(f"**Project:** {spec.project_name}  ")
    add(
        "**Content:** "
        + ("approved test cases only" if spec.approved_only else "drafts included")
    )
    add("")
    if spec.objective:
        add("## Objective")
        add("")
        add(spec.objective)
        add("")
    if spec.scope:
        add("## Scope")
        add("")
        add(spec.scope)
        add("")

    add("## Status")
    add("")
    add("| Item | Count |")
    add("| - | - |")
    for key in (
        "requirements",
        "test_cases",
        "draft",
        "needs_clarification",
        "approved",
        "rejected",
        "requirements_in_scope",
        "uncovered_requirements",
        "out_of_scope_requirements",
        "open_questions",
        "findings",
        "cases_with_findings",
    ):
        add(f"| {key.replace('_', ' ')} | {counts[key]} |")
    add("")
    add(f"> {spec.coverage_note()}")
    add("")

    add("## Requirements under test")
    add("")
    add("| Requirement | Document | Version | Drafts | Approved |")
    add("| - | - | - | - | - |")
    for state in spec.requirement_states:
        if not state.in_scope:
            continue
        add(
            f"| {state.label} | {state.document_filename or '-'} | "
            f"{state.document_version or '-'} | {len(state.cases)} | "
            f"{len(state.approved)} |"
        )
    add("")

    add("## Test cases")
    add("")
    for entry in spec.entries:
        case = entry.case
        add(f"### {case.case_id} - {case.title}")
        add("")
        add(
            f"**Status:** {case.review_status.value}, revision {case.revision}"
            + ("  (review again: the requirements changed)" if case.needs_recheck else "")
        )
        add("")
        add(f"**Objective:** {case.objective}")
        add("")
        linked = ", ".join(
            r.external_id or r.display_id() for r in entry.requirements
        ) or ", ".join(case.requirement_ids)
        add(f"**Requirements:** {linked}")
        add("")
        rows = steps_of(case)
        if rows:
            add("| # | Kind | Action | Expected result |")
            add("| - | - | - | - |")
            for kind, order, action, expectations in rows:
                expected = "<br>".join(expectations) or "-"
                add(f"| {order} | {kind} | {_cell(action)} | {_cell(expected)} |")
            add("")
        if case.open_questions:
            add("**Open questions**")
            add("")
            for question in case.open_questions:
                mark = "resolved" if question.resolved else "open"
                add(f"- [{mark}] {question.question}")
                if question.answer:
                    add(f"  - answer: {question.answer} ({question.resolved_by})")
                if question.suggestion:
                    add(
                        f"  - proposal, not a specification: {question.suggestion}"
                    )
            add("")
        if entry.findings:
            add("**Validation findings**")
            add("")
            for issue in entry.findings:
                add(f"- {issue.code} at {issue.path}: {issue.message}")
            add("")
        if case.accepted_findings:
            add("**Accepted findings**")
            add("")
            for finding in case.accepted_findings:
                add(
                    f"- {finding.code}: {finding.justification} "
                    f"({finding.by}, {finding.at})"
                )
            add("")
        if entry.context:
            add("**Supporting context used**")
            add("")
            for item in entry.context:
                position = ", ".join(span.label() for span in item.spans)
                add(
                    f"- {item.external_id or item.heading or item.requirement_id} "
                    f"({item.kind.value}, {item.reason.value}) - {item.label}"
                )
                if position:
                    add(f"  - position: {position}")
            add("")

    if spec.out_of_scope:
        add("")
        add(
            f"{len(spec.out_of_scope)} further requirement(s) of the knowledge "
            "base were not in scope of this specification."
        )
        add("")

    if spec.uncovered:
        add("## Requirements in scope without a draft")
        add("")
        for state in spec.uncovered:
            add(f"- {state.label} ({state.document_filename or 'unknown document'})")
        add("")

    unresolved = [q for q in spec.project_questions if not q.resolved]
    if unresolved:
        add("## Open questions of the project")
        add("")
        for question in unresolved:
            add(f"- {question.question}")
        add("")

    if spec.runs:
        add("## Generation runs")
        add("")
        add("| Run | Model | Mode | Status | Prompt | Context |")
        add("| - | - | - | - | - | - |")
        for run in spec.runs:
            add(
                f"| {run['run_id']} | {run['model']} | {run['mode']} | "
                f"{run['status']} | {run['prompt_version']} | {run['context_items']} |"
            )
        add("")
        for run in spec.runs:
            if run.get("task_description"):
                add(f"**Task for {run['run_id']}:** {run['task_description']}")
                add("")
    return "\n".join(out).rstrip() + "\n"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", "<br>")


# ----------------------------------------------------------------- json ----
def to_dict(spec: TestSpecification) -> dict:
    return {
        "specification_format": "1",
        "title": spec.title,
        "document_id": spec.document_id,
        "revision": spec.revision,
        "created_at": spec.created_at,
        "project": spec.project_name,
        "objective": spec.objective,
        "scope": spec.scope,
        "approved_only": spec.approved_only,
        "counts": spec.counts(),
        "coverage_note": spec.coverage_note(),
        "requirements": [
            {
                "requirement_id": state.requirement.requirement_id,
                "external_id": state.requirement.external_id,
                "document": state.document_filename,
                "version": state.document_version,
                "content_hash": state.content_hash,
                "clarification_status": state.requirement.clarification_status.value,
                "references": list(state.requirement.references),
                "text": state.requirement.text,
                "in_scope": state.in_scope,
            "case_ids": [case.case_id for case in state.cases],
                "approved_case_ids": [case.case_id for case in state.approved],
            }
            for state in spec.requirement_states
        ],
        "test_cases": [
            {
                **_plain(entry.case),
                "findings": _plain(entry.findings),
                "context": _plain(entry.context),
            }
            for entry in spec.entries
        ],
        "project_questions": _plain(spec.project_questions),
        "runs": spec.runs,
    }


def to_json(spec: TestSpecification) -> str:
    return json.dumps(to_dict(spec), ensure_ascii=False, indent=2)


def save_markdown(spec: TestSpecification, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_markdown(spec), encoding="utf-8")
    return path


def save_json(spec: TestSpecification, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(spec), encoding="utf-8")
    return path


# ---------------------------------------------------------------- excel ----
SHEETS = ("Test cases", "Test steps", "Requirement links", "Open questions",
          "Context sources")


def save_specification_xlsx(spec: TestSpecification, path: Path | str) -> Path:
    """Separate sheets; a step keeps its action and expectation in one row."""
    import pandas as pd
    from openpyxl.utils import get_column_letter

    cases = [
        {
            "case_id": entry.case.case_id,
            "title": entry.case.title,
            "objective": entry.case.objective,
            "review_status": entry.case.review_status.value,
            "revision": entry.case.revision,
            "needs_recheck": entry.case.needs_recheck,
            "requirements": ", ".join(
                r.external_id or r.display_id() for r in entry.requirements
            ),
            "open_questions": len(entry.unresolved_questions),
            "findings": len(entry.findings),
        }
        for entry in spec.entries
    ]
    steps = [
        {
            "case_id": entry.case.case_id,
            "kind": kind,
            "number": order,
            "action": action,
            "expected_result": "\n".join(expectations),
        }
        for entry in spec.entries
        for kind, order, action, expectations in steps_of(entry.case)
    ]
    links = [
        {
            "case_id": entry.case.case_id,
            "requirement": requirement.external_id or requirement.display_id(),
            "internal_id": requirement.requirement_id,
            "document": next(
                (
                    state.document_filename
                    for state in spec.requirement_states
                    if state.requirement.requirement_id == requirement.requirement_id
                ),
                None,
            ),
        }
        for entry in spec.entries
        for requirement in entry.requirements
    ]
    questions = [
        {
            "case_id": entry.case.case_id,
            "question_id": question.question_id,
            "question": question.question,
            "origin": question.origin.value,
            "resolved": question.resolved,
            "answer": question.answer,
            "resolved_by": question.resolved_by,
        }
        for entry in spec.entries
        for question in entry.case.open_questions
    ] + [
        {
            "case_id": "",
            "question_id": question.question_id,
            "question": question.question,
            "origin": question.origin.value,
            "resolved": question.resolved,
            "answer": question.answer,
            "resolved_by": question.resolved_by,
        }
        for question in spec.project_questions
    ]
    sources = [
        {
            "case_id": entry.case.case_id,
            "source": item.external_id or item.heading or item.requirement_id,
            "kind": item.kind.value,
            "reason": item.reason.value,
            "document": item.document_filename,
            "version": item.document_version,
            "content_hash": item.content_hash,
            "position": ", ".join(span.label() for span in item.spans),
        }
        for entry in spec.entries
        for item in entry.context
    ]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = dict(
        zip(SHEETS, (cases, steps, links, questions, sources))
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, rows in frames.items():
            frame = pd.DataFrame(rows)
            if frame.empty:
                frame = pd.DataFrame(columns=["(no entries)"])
            frame.to_excel(writer, index=False, sheet_name=name)
            sheet = writer.sheets[name]
            for index, column in enumerate(frame.columns, 1):
                values = [str(v) for v in frame[column] if str(v).strip()]
                width = max([len(str(column))] + [min(len(v), 60) for v in values]) + 2
                sheet.column_dimensions[get_column_letter(index)].width = width
    return path


#: deprecated alias, see the note in tabular.py
save_xlsx = save_specification_xlsx
