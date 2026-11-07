"""An MCP server over the existing use cases. Read-only, on purpose.

Four tools, all of which already exist as application functions; nothing
new is decided here:

``search_requirements``  find sources, each with its ID, document version
                         and position
``get_requirement``      fetch one requirement unambiguously
``check_grounds``        the deterministic evidence check
``review_grounds``       the technical review, asked for explicitly, with
                         the model calls and any unchecked claim visible in
                         the answer

Three properties this adapter holds to:

**Nothing is written.** No tool opens a project for writing, saves, edits
or approves anything. The project file is read and closed. A caller that
wants to change something uses the application or the interface, where the
review rules apply.

**No model call happens by accident.** Only ``review_grounds`` talks to a
model, only when it is called, and it reports how many calls it made. The
other three are offline.

**The validation and the approval rules are the product's own.** This
module formats; it does not re-implement a rule. A finding keeps its code,
its severity and its path, so an agent on the other end sees exactly what
the interface shows a reviewer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_NAME = "spec2test"
#: bumped when a tool's answer shape changes
API_VERSION = "1"

#: where the server reads from, set before it starts
ENV_PROJECT = "SPEC2TEST_PROJECT"
ENV_CORPUS = "SPEC2TEST_CORPUS"
ENV_INDEX = "SPEC2TEST_INDEX"
ENV_MODEL = "SPEC2TEST_REVIEW_MODEL"
ENV_MODE = "SPEC2TEST_REVIEW_MODE"


class ConfigurationError(RuntimeError):
    """The server was started without what a tool needs."""


@dataclass(frozen=True)
class Workspace:
    """What the server reads. Resolved once, never written to."""

    project: Path | None
    corpus: Path | None
    index: Path
    review_model: str | None
    review_mode: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Workspace":
        env = dict(os.environ if env is None else env)
        project = env.get(ENV_PROJECT)
        corpus = env.get(ENV_CORPUS)
        index = env.get(ENV_INDEX) or str(Path.cwd() / ".spec2test" / "mcp.db")
        return cls(
            project=Path(project) if project else None,
            corpus=Path(corpus) if corpus else None,
            index=Path(index),
            review_model=env.get(ENV_MODEL),
            review_mode=env.get(ENV_MODE, "ollama_local"),
        )

    def require_corpus(self) -> Path:
        if self.corpus is None or not self.corpus.exists():
            raise ConfigurationError(
                f"no source directory: set {ENV_CORPUS} to a folder of "
                "requirement documents"
            )
        return self.corpus

    def require_project(self) -> Path:
        if self.project is None or not self.project.exists():
            raise ConfigurationError(
                f"no project: set {ENV_PROJECT} to a .s2t.json file"
            )
        return self.project


# --------------------------------------------------------------- loading ---
def load_corpus(workspace: Workspace):
    """Index and requirements of the configured source directory."""
    from ...adapters.documents import DEFAULT_IMPORTERS
    from ...adapters.knowledge import SqliteKnowledgeIndex
    from ...application.indexing import collect_documents, sync_index

    folder = workspace.require_corpus()
    workspace.index.parent.mkdir(parents=True, exist_ok=True)
    index = SqliteKnowledgeIndex(workspace.index)
    synced = sync_index(index, collect_documents(folder), DEFAULT_IMPORTERS)
    return index, list(synced.requirements), dict(synced.documents)


def load_project(workspace: Workspace):
    """The stored project, read-only."""
    from ...adapters.persistence.project_store import ProjectStore

    return ProjectStore(workspace.require_project()).load()


def _issue(issue) -> dict:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "path": issue.path,
        "message": issue.message,
    }


def _passage_span(passage, document=None) -> dict:
    """Where a retrieved passage sits, in the same shape as a requirement's."""
    return {
        "document_id": passage.document_id,
        "document_filename": getattr(document, "filename", None),
        "document_version": getattr(document, "version", None),
        "declared_document_id": getattr(document, "declared_id", None),
        "page": passage.page,
        "section": passage.heading,
        "char_start": passage.char_start,
        "char_end": passage.char_end,
    }


def _span(span, document=None) -> dict:
    return {
        "document_id": span.document_id,
        "document_filename": getattr(document, "filename", None),
        "document_version": getattr(document, "version", None),
        "declared_document_id": getattr(document, "declared_id", None),
        "page": span.page,
        "section": span.section,
        "char_start": span.char_start,
        "char_end": span.char_end,
    }


# ----------------------------------------------------------------- tools ---
def tool_search_requirements(
    workspace: Workspace, query: str, limit: int = 5, kind: str | None = None
) -> dict:
    """Find sources for a query. Offline; no model is called."""
    from ...application.knowledge import normalise_for_search
    from ...domain.models import ContextKind

    index, _requirements, documents = load_corpus(workspace)
    terms = [t for t in normalise_for_search(query).split() if len(t) > 2]
    kinds = [ContextKind(kind)] if kind else None
    hits = index.search(terms, limit=max(1, min(limit, 25)), kinds=kinds)
    return {
        "api_version": API_VERSION,
        "query": query,
        "terms": terms,
        "model_calls": 0,
        "hits": [
            {
                "passage_id": hit.passage.passage_id,
                "external_id": hit.passage.external_id,
                "kind": hit.passage.kind.value,
                "heading": hit.passage.heading,
                "score": hit.score,
                "matched_terms": list(hit.terms),
                "text": hit.passage.full_text,
                "source": _passage_span(
                    hit.passage, documents.get(hit.passage.document_id)
                ),
            }
            for hit in hits
        ],
    }


def tool_get_requirement(workspace: Workspace, requirement_id: str) -> dict:
    """One requirement, by external or internal ID. Ambiguity is reported."""
    _index, requirements, documents = load_corpus(workspace)
    matches = [
        r
        for r in requirements
        if requirement_id in (r.external_id, r.requirement_id)
    ]
    if not matches:
        return {
            "api_version": API_VERSION,
            "found": False,
            "model_calls": 0,
            "message": f"{requirement_id!r} is not in the configured sources",
        }
    if len(matches) > 1:
        return {
            "api_version": API_VERSION,
            "found": False,
            "model_calls": 0,
            "message": (
                f"{requirement_id!r} is defined {len(matches)} times; use the "
                "internal requirement_id to be unambiguous"
            ),
            "candidates": [
                {
                    "requirement_id": r.requirement_id,
                    "document_id": r.spans[0].document_id if r.spans else None,
                }
                for r in matches
            ],
        }
    requirement = matches[0]
    return {
        "api_version": API_VERSION,
        "found": True,
        "model_calls": 0,
        "requirement_id": requirement.requirement_id,
        "external_id": requirement.external_id,
        "text": requirement.text,
        "references": list(requirement.references),
        "clarification_status": requirement.clarification_status.value,
        "spans": [
            _span(span, documents.get(span.document_id))
            for span in requirement.spans
        ],
    }


def tool_check_grounds(workspace: Workspace, case_id: str | None = None) -> dict:
    """The deterministic evidence check over stored drafts. Offline."""
    from ...application.review import context_for_case
    from ...domain.grounding import check_grounds

    project = load_project(workspace)
    cases = [c for c in project.test_cases if case_id in (None, c.case_id)]
    if case_id and not cases:
        return {
            "api_version": API_VERSION,
            "model_calls": 0,
            "message": f"{case_id!r} is not a draft of this project",
            "cases": [],
        }
    out = []
    for case in cases:
        context = context_for_case(project, case.case_id)
        grounds = [
            {
                "claim": g.claim,
                "target": g.target,
                "kind": g.kind.value,
                "source": g.source,
            }
            for step in (*case.preconditions, *case.steps, *case.postconditions)
            for g in step.grounds
        ]
        issues = check_grounds(case, project.requirements, context=context)
        out.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "review_status": case.review_status.value,
                "revision": case.revision,
                "grounding_rules": case.grounding_rules,
                "legacy": case.is_legacy_grounding,
                "grounds": grounds,
                "findings": [_issue(i) for i in issues],
                "blocks_approval": any(
                    i.severity.value in ("error", "clarification") for i in issues
                ),
            }
        )
    return {"api_version": API_VERSION, "model_calls": 0, "cases": out}


def tool_review_grounds(workspace: Workspace, case_id: str) -> dict:
    """The technical review. Calls a model; says how often and what it missed."""
    from ...application.ground_review import review_grounds
    from ...application.review import context_for_case
    from ...bootstrap import MODE_FAKE, build_model, ollama_settings_from_config

    project = load_project(workspace)
    case = project.case(case_id)
    if case is None:
        return {
            "api_version": API_VERSION,
            "model_calls": 0,
            "message": f"{case_id!r} is not a draft of this project",
        }
    mode = workspace.review_mode
    if mode != MODE_FAKE and not workspace.review_model:
        raise ConfigurationError(
            f"set {ENV_MODEL} to the model the review should use, or "
            f"{ENV_MODE}=fake for the offline stand-in"
        )
    settings = (
        None
        if mode == MODE_FAKE
        else ollama_settings_from_config({}, mode=mode, model=workspace.review_model)
    )
    model = build_model(mode, settings=settings)

    review = review_grounds(
        case,
        project.requirements,
        model,
        context=context_for_case(project, case_id),
    )
    return {
        "api_version": API_VERSION,
        "case_id": case_id,
        # one call per reviewed case; zero when there was nothing to review
        "model_calls": 0 if not review.verdicts else 1,
        "model": review.model,
        "prompt_version": review.prompt_version,
        "ok": review.ok,
        "complete": review.complete,
        "error": review.error,
        "summary": review.summary(),
        "verdicts": [
            {
                "claim_id": v.claim_id,
                "claim": v.claim,
                "path": v.path,
                "outcome": v.outcome,
                "verdict": v.verdict,
                "confidence": v.confidence,
                "explanation": v.explanation,
            }
            for v in review.verdicts
        ],
        "not_checked": [v.claim_id for v in review.not_checked],
        "findings": [_issue(i) for i in review.issues],
        "note": (
            "A model's opinion. It approves nothing and changes nothing. "
            "'not_checked' is not a pass."
        ),
    }


# ----------------------------------------------------------------- server --
def build_server(workspace: Workspace | None = None):
    """The FastMCP server. Imported lazily so the package works without it."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise RuntimeError(
            'fastmcp is not installed: pip install -e ".[mcp]"'
        ) from exc

    space = workspace or Workspace.from_env()
    server = FastMCP(SERVER_NAME)

    @server.tool
    def search_requirements(query: str, limit: int = 5, kind: str | None = None) -> dict:
        """Search the configured sources. Returns id, version and position.

        Offline: no model is called. ``kind`` narrows to one passage kind
        (requirement, signal, interface, explanation, reference_test).
        """
        return tool_search_requirements(space, query, limit=limit, kind=kind)

    @server.tool
    def get_requirement(requirement_id: str) -> dict:
        """One requirement by its external or internal ID, with its spans.

        Offline: no model is called. An ID that is defined twice is
        reported as ambiguous instead of resolved to one of them.
        """
        return tool_get_requirement(space, requirement_id)

    @server.tool
    def check_grounds(case_id: str | None = None) -> dict:
        """Deterministic evidence check over the configured project.

        Reports, per draft, the grounds it states and what does not hold:
        unknown source, quote that is not in the source, arithmetic that
        does not work out, a value with no ground at all. Offline. Nothing
        is written.
        """
        return tool_check_grounds(space, case_id)

    @server.tool
    def review_grounds(case_id: str) -> dict:
        """Ask a model whether each ground fits the claim it is given for.

        This is the only tool that calls a model, and only when called. The
        answer carries ``model_calls`` and ``not_checked``: a claim that
        came back unjudged is not a pass. Nothing is approved or written.
        """
        return tool_review_grounds(space, case_id)

    return server


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - entry point
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=None)
    parser.add_argument("--corpus", default=None)
    parser.add_argument("--index", default=None)
    parser.add_argument("--review-model", default=None)
    parser.add_argument("--review-mode", default=None)
    args = parser.parse_args(argv)

    env = dict(os.environ)
    for key, value in (
        (ENV_PROJECT, args.project),
        (ENV_CORPUS, args.corpus),
        (ENV_INDEX, args.index),
        (ENV_MODEL, args.review_model),
        (ENV_MODE, args.review_mode),
    ):
        if value:
            env[key] = value
    build_server(Workspace.from_env(env)).run()
    return 0


__all__ = [
    "API_VERSION",
    "ENV_CORPUS",
    "ENV_INDEX",
    "ENV_MODE",
    "ENV_MODEL",
    "ENV_PROJECT",
    "SERVER_NAME",
    "ConfigurationError",
    "Workspace",
    "build_server",
    "tool_check_grounds",
    "tool_get_requirement",
    "tool_review_grounds",
    "tool_search_requirements",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
