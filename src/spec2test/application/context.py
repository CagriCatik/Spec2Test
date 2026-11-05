"""Resolved references as supporting context.

Only requirements that the requirement under test cites directly are added.
There is no recursive expansion: the chain stops after one step, so the input
stays predictable and a reviewer can see where every line came from.

Each item carries its origin - document, declared ID and version, content hash,
requirement ID and source span - and the selection is stored verbatim with the
run. IDs alone would not survive a later change to the documents.

No Qt, no HTTP, no file access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..domain.models import (
    ContextItem,
    ContextKind,
    Requirement,
    SelectionReason,
    SourceSpan,
    Severity,
    SourceDocument,
    SourceSpan,
    ValidationIssue,
)
from .ingest import ReferenceReport

DEFAULT_CONTEXT_CHAR_BUDGET = 6_000
DEFAULT_MAX_CONTEXT_ITEMS = 5
#: a search hit must be at least this good compared with the best one
DEFAULT_SCORE_RATIO = 0.4
#: kinds the search looks at first: they carry signal names and value ranges
PREFERRED_KINDS = (ContextKind.SIGNAL, ContextKind.INTERFACE)
FALLBACK_KINDS = (ContextKind.EXPLANATION, ContextKind.REFERENCE_TEST)

from ..domain.validation import _numeric_claims as _claims_of


@dataclass
class ContextSelection:
    """What was selected, and what was left out and why."""

    items: tuple[ContextItem, ...] = ()
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.items)

    def by_external_id(self, external_id: str) -> ContextItem | None:
        return next(
            (item for item in self.items if item.external_id == external_id), None
        )


def _clarification(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code, Severity.CLARIFICATION, path, message)


def build_reference_context(
    under_test: Sequence[Requirement],
    report: ReferenceReport,
    *,
    documents: Mapping[str, SourceDocument] | None = None,
    char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    max_items: int = DEFAULT_MAX_CONTEXT_ITEMS,
) -> ContextSelection:
    """Collect the requirements cited by the ones under test.

    A reference to nothing, an ambiguous reference and anything that does not
    fit the budget are reported. Nothing is ever shortened: an item is sent
    whole or not at all.
    """
    documents = documents or {}
    selection = ContextSelection()
    own_ids = {r.requirement_id for r in under_test}
    own_external = {r.external_id for r in under_test if r.external_id}

    wanted: dict[str, list[str]] = {}
    for requirement in under_test:
        for target in requirement.references:
            if target in own_external:
                # already part of the unit, or the requirement citing itself
                continue
            wanted.setdefault(target, []).append(
                requirement.external_id or requirement.requirement_id
            )

    items: list[ContextItem] = []
    used = 0
    for target, cited_by in wanted.items():
        definitions = report.definitions.get(target, [])
        if not definitions:
            selection.issues.append(
                _clarification(
                    "C_UNRESOLVED_REFERENCE",
                    target,
                    f"{', '.join(cited_by)} refers to {target}, which is not part "
                    "of the imported set; no context was added for it",
                )
            )
            continue
        if len(definitions) > 1:
            where = ", ".join(sorted({d.spans[0].document_id for d in definitions if d.spans}))
            selection.issues.append(
                _clarification(
                    "C_AMBIGUOUS_REFERENCE",
                    target,
                    f"{target} is defined more than once ({where}); the reference "
                    "was not resolved and no context was added",
                )
            )
            continue

        requirement = definitions[0]
        if requirement.requirement_id in own_ids:
            continue

        document_id = requirement.spans[0].document_id if requirement.spans else ""
        document = documents.get(document_id)
        item = ContextItem(
            requirement_id=requirement.requirement_id,
            external_id=requirement.external_id,
            text=requirement.text,
            document_id=document_id,
            spans=requirement.spans,
            cited_by=tuple(dict.fromkeys(cited_by)),
            kind=ContextKind.REQUIREMENT,
            reason=SelectionReason.EXPLICIT_REFERENCE,
            document_filename=document.filename if document else None,
            declared_document_id=document.declared_id if document else None,
            document_version=document.version if document else None,
            content_hash=document.content_hash if document else None,
        )

        if len(items) >= max_items:
            selection.issues.append(
                _clarification(
                    "C_CONTEXT_OMITTED",
                    target,
                    f"{target} was left out: at most {max_items} context "
                    "requirement(s) are sent. Reduce the references or raise "
                    "the limit.",
                )
            )
            continue
        if used + item.size > char_budget:
            selection.issues.append(
                _clarification(
                    "C_CONTEXT_OMITTED",
                    target,
                    f"{target} ({item.size} characters) does not fit the context "
                    f"budget of {char_budget}; it was left out whole rather than "
                    "shortened",
                )
            )
            continue

        items.append(item)
        used += item.size

    selection.items = tuple(items)
    return selection


def build_context(
    under_test: Sequence[Requirement],
    report: ReferenceReport,
    *,
    documents: Mapping[str, SourceDocument] | None = None,
    knowledge=None,
    char_budget: int = DEFAULT_CONTEXT_CHAR_BUDGET,
    max_items: int = DEFAULT_MAX_CONTEXT_ITEMS,
    max_search_items: int = 3,
    score_ratio: float = DEFAULT_SCORE_RATIO,
) -> ContextSelection:
    """Explicit references first, then full-text hits, within one budget.

    References are preferred because they are stated by the document itself.
    Search results fill what is left, signal and interface sections first:
    those carry the signal names and value ranges whose absence causes most
    open questions. Duplicates are dropped and the budget is shared.
    """
    selection = build_reference_context(
        under_test,
        report,
        documents=documents,
        char_budget=char_budget,
        max_items=max_items,
    )
    if knowledge is None or max_search_items <= 0:
        return selection

    items = list(selection.items)
    used = sum(item.size for item in items)
    taken_passages = {item.requirement_id for item in items}
    taken_external = {item.external_id for item in items if item.external_id}
    own_documents = {
        span.document_id for r in under_test for span in r.spans
    }
    own_passages = {
        f"{span.document_id}:req:{span.char_start}"
        for r in under_test
        for span in r.spans
    }
    search_slots = max_search_items

    from .knowledge import search_terms

    for requirement in under_test:
        if search_slots <= 0 or len(items) >= max_items:
            break
        terms = search_terms(requirement)
        if not terms:
            continue
        hits = list(
            knowledge.search(
                terms,
                limit=max_search_items,
                kinds=PREFERRED_KINDS,
                exclude_passages=own_passages | taken_passages,
            )
        )
        hits += list(
            knowledge.search(
                terms,
                limit=max_search_items,
                kinds=FALLBACK_KINDS,
                exclude_passages=own_passages | taken_passages,
            )
        )
        if not hits:
            continue
        best = min(hit.score for hit in hits)
        for hit in hits:
            if search_slots <= 0 or len(items) >= max_items:
                break
            passage = hit.passage
            if passage.passage_id in taken_passages:
                continue
            if passage.external_id and passage.external_id in taken_external:
                continue
            # bm25 scores are negative; smaller is better
            if best < 0 and hit.score > best * score_ratio:
                continue
            item = _item_from_hit(hit, requirement, documents or {})
            if used + item.size > char_budget:
                selection.issues.append(
                    _clarification(
                        "C_CONTEXT_OMITTED",
                        passage.passage_id,
                        f"a search hit in {item.document_filename or passage.document_id} "
                        f"({item.size} characters) does not fit the context budget "
                        f"of {char_budget}; it was left out whole",
                    )
                )
                continue
            items.append(item)
            taken_passages.add(passage.passage_id)
            if passage.external_id:
                taken_external.add(passage.external_id)
            used += item.size
            search_slots -= 1

    selection.items = tuple(items)
    return selection


def _item_from_hit(hit, cited_by: Requirement, documents: Mapping[str, SourceDocument]):
    passage = hit.passage
    document = hit.document or documents.get(passage.document_id)
    span = SourceSpan(
        document_id=passage.document_id,
        char_start=passage.char_start,
        char_end=passage.char_end,
        quote=passage.text[:400],
        page=passage.page,
        section=passage.heading,
    )
    return ContextItem(
        requirement_id=passage.passage_id,
        external_id=passage.external_id,
        text=passage.text,
        document_id=passage.document_id,
        spans=(span,),
        cited_by=(cited_by.external_id or cited_by.requirement_id,),
        document_filename=document.filename if document else None,
        declared_document_id=document.declared_id if document else None,
        document_version=document.version if document else None,
        content_hash=document.content_hash if document else None,
        kind=passage.kind,
        reason=SelectionReason.FULL_TEXT_SEARCH,
        matched_terms=tuple(hit.terms[:6]),
        heading=passage.heading,
    )


# ------------------------------------------------------------ conflicts ----
def _claims(text: str) -> set[tuple[float, str]]:
    """The same reading as the validator uses, LaTeX notation included."""
    return _claims_of(text)


def detect_contradictions(
    under_test: Sequence[Requirement], context: Sequence[ContextItem]
) -> list[ValidationIssue]:
    """Report values that differ between the requirement and its context.

    Deliberately narrow: a conflict is only reported when both sides state
    exactly **one** value for the same unit, so that value is plausibly "the"
    value of the same quantity. A requirement that mentions several durations
    says nothing about which of them a context duration would contradict, and
    reporting all pairs would bury the reviewer in false findings.

    Matching quantities by name needs the retrieval stage; until then this
    check stays conservative. The decision which source is right belongs to
    the reviewer, so this is a clarification, never a silent preference.
    """
    issues: list[ValidationIssue] = []
    for requirement in under_test:
        own = _claims(requirement.text)
        if not own:
            continue
        own_by_unit: dict[str, list[float]] = {}
        for value, unit in own:
            own_by_unit.setdefault(unit, []).append(value)
        for item in context:
            other_by_unit: dict[str, list[float]] = {}
            for value, unit in _claims(item.text):
                other_by_unit.setdefault(unit, []).append(value)
            for unit, values in sorted(other_by_unit.items()):
                own_values = own_by_unit.get(unit, [])
                if len(own_values) != 1 or len(values) != 1:
                    continue  # ambiguous: which quantity would conflict?
                value = values[0]
                if abs(value - own_values[0]) < 1e-9:
                    continue
                issues.append(
                    _clarification(
                        "C_POSSIBLE_VALUE_CONFLICT",
                        requirement.external_id or requirement.requirement_id,
                        f"{requirement.external_id or requirement.requirement_id} "
                        f"states {own_values[0]:g} {unit}, while "
                        f"{item.external_id or item.requirement_id} states "
                        f"{value:g} {unit}. This is a suspicion, not a confirmed "
                        "conflict: the two values may belong to different "
                        "quantities, conditions or operating states. A reviewer "
                        "decides; the model must not choose a source.",
                    )
                )
    return issues


def covered_by_context(
    value: float, unit: str, context: Sequence[ContextItem]
) -> ContextItem | None:
    """The context item stating this quantity, if exactly one does."""
    matches = [
        item
        for item in context
        if any(
            abs(value - other) < 1e-9 and unit == other_unit
            for other, other_unit in _claims(item.text)
        )
    ]
    return matches[0] if len(matches) == 1 else None
