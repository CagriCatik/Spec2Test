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

from dataclasses import dataclass, field, replace
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
#: other requirements of the knowledge base. Two thirds of a typical index are
#: requirement passages; leaving them out made most of it unreachable unless a
#: document happened to cite them by ID.
REQUIREMENT_KINDS = (ContextKind.REQUIREMENT,)
#: searched in this order, each tier judged against its own best hit
SEARCH_TIERS = (PREFERRED_KINDS, FALLBACK_KINDS, REQUIREMENT_KINDS)
#: share of the character budget held back for a related requirement while one
#: matched. A reserved slot alone does nothing: signal sections run to ~2300
#: characters each and two of them exhaust the whole budget.
REQUIREMENT_BUDGET_SHARE = 0.35
#: Share held back for hits the tester's own task description found.
#:
#: **Off by default.** It does what it was built for - the task text reaches
#: the prompt in 12 of 16 holdout questions instead of 5 - but 16 generation
#: runs showed no benefit to the drafts that can be told apart from the
#: model's own variance, and at a 6000 character budget the reserved 1500
#: characters rarely hold a whole passage anyway. An unproven default is not
#: worth keeping; ``generation.task_budget_share`` turns it on.
TASK_BUDGET_SHARE = 0.0


@dataclass
class BudgetLedger:
    """What was reserved, what was used, what the reservation gave back.

    Recorded so the reservation can be checked instead of believed.
    """

    budget: int = 0
    reserved_for_task: int = 0
    used_by_task: int = 0
    used_total: int = 0
    candidates: int = 0
    omitted: int = 0

    @property
    def returned_to_others(self) -> int:
        return max(0, self.reserved_for_task - self.used_by_task)

from ..domain.validation import _numeric_claims as _claims_of


@dataclass
class ContextSelection:
    """What was selected, and what was left out and why."""

    items: tuple[ContextItem, ...] = ()
    issues: list[ValidationIssue] = field(default_factory=list)
    #: filled by build_context; empty for a pure reference selection
    ledger: BudgetLedger | None = None

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
    task_description: str | None = None,
    max_task_items: int = 2,
    task_budget_share: float | None = None,
) -> ContextSelection:
    """Explicit references first, then full-text hits, within one budget.

    References are preferred because they are stated by the document itself.
    Search results fill what is left, signal and interface sections first:
    those carry the signal names and value ranges whose absence causes most
    open questions. Duplicates are dropped and the budget is shared.

    ``task_description`` is the tester's own words for this run. It is
    searched once per unit with its own small allowance, because it asks a
    different question from the requirement's identifiers: measured on this
    corpus, BM25 finds nothing at all for two thirds of prose questions while
    an embedding retriever finds most of them. Items it brings in carry
    ``SelectionReason.TASK_SEARCH`` so their contribution can be told apart
    from the rest.
    """
    selection = build_reference_context(
        under_test,
        report,
        documents=documents,
        char_budget=char_budget,
        max_items=max_items,
    )
    documents = documents or {}
    ledger = BudgetLedger(budget=char_budget)
    selection.ledger = ledger

    wants_task_search = bool(
        task_description and task_description.strip() and max_task_items > 0
    )
    if knowledge is None or (max_search_items <= 0 and not wants_task_search):
        ledger.used_total = sum(item.size for item in selection.items)
        return selection

    taken_passages = {item.requirement_id for item in selection.items}
    taken_external = {item.external_id for item in selection.items if item.external_id}
    own_passages = {
        f"{span.document_id}:req:{span.char_start}"
        for r in under_test
        for span in r.spans
    }
    # a requirement under test must never come back as its own context, not
    # even from a second document that repeats its ID
    own_external = {r.external_id for r in under_test if r.external_id}

    # --- 1. collect candidates from both searches, select nothing yet -------
    search_candidates = _search_candidates(
        under_test,
        knowledge,
        documents,
        excluded=own_passages | taken_passages,
        max_search_items=max_search_items,
        score_ratio=score_ratio,
    )
    task_candidates = (
        _task_candidates(
            under_test,
            task_description or "",
            knowledge,
            documents,
            excluded=own_passages | taken_passages,
            limit=max_task_items * 3,
        )
        if wants_task_search
        else []
    )

    # --- 2. deduplicate across both, keeping the first claim on a passage ---
    ordered: list[tuple[ContextItem, bool]] = [
        *((item, False) for item in search_candidates),
        *((item, True) for item in task_candidates),
    ]
    unique: list[tuple[ContextItem, bool]] = []
    seen_passages = set(taken_passages)
    seen_external = set(taken_external) | set(own_external)
    for item, is_task in ordered:
        if item.requirement_id in seen_passages:
            continue
        if item.external_id and item.external_id in seen_external:
            continue
        seen_passages.add(item.requirement_id)
        if item.external_id:
            seen_external.add(item.external_id)
        unique.append((item, is_task))
    ledger.candidates = len(unique)

    # --- 3. select once, under one budget, with a share held for the task ---
    items = list(selection.items)
    used = sum(item.size for item in items)
    has_task = any(is_task for _item, is_task in unique)
    share = TASK_BUDGET_SHARE if task_budget_share is None else task_budget_share
    ledger.reserved_for_task = int(char_budget * share) if has_task else 0
    slots = max_search_items + (max_task_items if has_task else 0)
    omitted = 0

    def room_for(item, is_task: bool) -> bool:
        """Non-task items may not eat into what the task search is holding."""
        ceiling = char_budget if is_task else char_budget - reserved_left
        return used + item.size <= ceiling

    reserved_left = ledger.reserved_for_task
    task_taken = 0
    for item, is_task in unique:
        if len(items) >= max_items or slots <= 0:
            omitted += 1
            continue
        if is_task and task_taken >= max_task_items:
            omitted += 1
            continue
        if not room_for(item, is_task):
            omitted += 1
            continue
        items.append(item)
        used += item.size
        slots -= 1
        if is_task:
            task_taken += 1
            ledger.used_by_task += item.size
            # only what it actually used stays reserved; the rest is released
            reserved_left = max(0, reserved_left - item.size)

    # whatever the task search did not take falls to the others
    if reserved_left:
        reserved_left = 0
        for item, is_task in unique:
            if item in items or len(items) >= max_items or slots <= 0:
                continue
            if used + item.size > char_budget:
                continue
            items.append(item)
            used += item.size
            slots -= 1
            omitted -= 1

    ledger.used_total = used
    ledger.omitted = max(0, omitted)
    if ledger.omitted:
        # one line, not one per hit: these are search results nobody asked
        # for, and a finding each buried the findings that matter
        selection.issues.append(
            _clarification(
                "C_CONTEXT_OMITTED",
                under_test[0].external_id or under_test[0].requirement_id
                if under_test
                else "unit",
                f"{ledger.omitted} further search hit(s) did not fit the context "
                f"budget of {char_budget} characters or the limit of {max_items} "
                "item(s) and were left out whole. Raise "
                "generation.context_char_budget to send more supporting context.",
            )
        )

    selection.items = tuple(items)
    return selection


def _search_candidates(
    under_test: Sequence[Requirement],
    knowledge,
    documents: Mapping[str, SourceDocument],
    *,
    excluded: set,
    max_search_items: int,
    score_ratio: float,
) -> list[ContextItem]:
    """Ranked hits for the requirements themselves, tier by tier.

    No budget decision here: that is made once, over all candidates together.
    Signal and interface sections stay first, then explanations and reference
    tests, then other requirements - each tier judged against its own best
    hit, so a strong requirement cannot push a signal section out.
    """
    from .knowledge import search_terms

    if max_search_items <= 0:
        return []
    candidates: list[ContextItem] = []
    for requirement in under_test:
        terms = search_terms(requirement)
        if not terms:
            continue
        for kinds in SEARCH_TIERS:
            hits = list(
                knowledge.search(
                    terms,
                    limit=max_search_items,
                    kinds=kinds,
                    exclude_passages=excluded,
                )
            )
            if not hits:
                continue
            # bm25 scores are negative; smaller is better
            best = min(hit.score for hit in hits)
            for hit in hits:
                if best < 0 and hit.score > best * score_ratio:
                    continue
                candidates.append(_item_from_hit(hit, requirement, documents))
    return candidates


def _task_candidates(
    under_test: Sequence[Requirement],
    task_description: str,
    knowledge,
    documents: Mapping[str, SourceDocument],
    *,
    excluded: set,
    limit: int,
) -> list[ContextItem]:
    """Ranked hits for the tester's own description, over every kind at once.

    No tier order: the description is prose, and which kind answers it is
    exactly what is unknown. The retriever decides.
    """
    from .knowledge import normalise_for_search

    terms = [t for t in normalise_for_search(task_description).split() if len(t) > 2]
    if not terms or not under_test:
        return []
    cited_by = under_test[0]
    return [
        replace(
            _item_from_hit(hit, cited_by, documents),
            reason=SelectionReason.TASK_SEARCH,
        )
        for hit in knowledge.search(terms, limit=limit, exclude_passages=excluded)
    ]


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
        prefix=passage.prefix,
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
