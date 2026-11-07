"""Does the retriever find the right source? Measured, not assumed.

This is deliberately separate from the generation comparison. A retriever can
only help if it surfaces the passage that holds the answer; comparing written
test drafts before that is settled measures two things at once.

Three query families, because they fail differently:

``exact_id``     the external ID of a requirement. A lexical index should be
                 perfect here; an embedding index is known to be weak at it,
                 which is why exact ID resolution stays with the lexical path
                 in the product.
``signal_name``  an identifier such as ``Pack_Current_Validated``. The term
                 appears verbatim in the passages that matter.
``paraphrase``   the same question in prose, without the identifier. This is
                 where lexical search is expected to fail and semantics to
                 pay off.

The expected passages are derived from the corpus, never written by hand:
for an ID query it is the passage carrying that ID, for a signal query every
passage whose text contains the identifier. Only the wording of a paraphrase
is authored, and its expected answer is the requirement it was written from,
which is recorded with the query so it can be checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..domain.models import ContextKind

#: ``Pack_Current_Validated``, ``KL15_Ignition_State`` - not prose words
IDENTIFIER = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")


@dataclass(frozen=True)
class RetrievalQuery:
    """One question with the passages that count as a correct answer."""

    query_id: str
    family: str
    text: str
    #: passage ids that answer it; a hit on any of them counts
    expected_passages: tuple[str, ...]
    #: how the expectation was derived, so a reader can check it
    derivation: str
    source_external_id: str | None = None

    def __post_init__(self) -> None:
        if not self.expected_passages:
            raise ValueError(f"{self.query_id} has no expected passage")


@dataclass
class QueryOutcome:
    query: RetrievalQuery
    ranked: tuple[str, ...]
    #: 1-based rank of the first expected passage, ``None`` when not found
    first_hit_rank: int | None
    seconds: float = 0.0

    @property
    def found(self) -> bool:
        return self.first_hit_rank is not None

    def hit_at(self, k: int) -> bool:
        return self.first_hit_rank is not None and self.first_hit_rank <= k

    @property
    def reciprocal_rank(self) -> float:
        return 0.0 if self.first_hit_rank is None else 1.0 / self.first_hit_rank


@dataclass
class RetrievalScore:
    """Aggregated result for one retriever over one query set."""

    retriever: str
    outcomes: list[QueryOutcome] = field(default_factory=list)

    def _subset(self, family: str | None) -> list[QueryOutcome]:
        if family is None:
            return self.outcomes
        return [o for o in self.outcomes if o.query.family == family]

    def recall_at(self, k: int, family: str | None = None) -> float:
        subset = self._subset(family)
        if not subset:
            return 0.0
        return sum(1 for o in subset if o.hit_at(k)) / len(subset)

    def mrr(self, family: str | None = None) -> float:
        subset = self._subset(family)
        if not subset:
            return 0.0
        return sum(o.reciprocal_rank for o in subset) / len(subset)

    def median_seconds(self, family: str | None = None) -> float:
        subset = sorted(o.seconds for o in self._subset(family))
        if not subset:
            return 0.0
        middle = len(subset) // 2
        if len(subset) % 2:
            return subset[middle]
        return (subset[middle - 1] + subset[middle]) / 2

    @property
    def families(self) -> list[str]:
        return sorted({o.query.family for o in self.outcomes})

    def misses(self, family: str | None = None) -> list[QueryOutcome]:
        return [o for o in self._subset(family) if not o.found]


# ------------------------------------------------------------ query sets ---
def build_query_set(
    passages: Sequence,
    *,
    max_per_family: int = 20,
    paraphrases: Sequence[tuple[str, str]] = (),
) -> list[RetrievalQuery]:
    """Derive queries from the indexed passages.

    ``paraphrases`` are ``(external_id, question)`` pairs: the question is
    authored, the expected answer is the requirement it names, resolved here
    against the corpus so a wrong ID fails loudly instead of silently
    scoring zero.
    """
    by_external: dict[str, list] = {}
    for passage in passages:
        if passage.external_id:
            by_external.setdefault(passage.external_id, []).append(passage)

    queries: list[RetrievalQuery] = []

    # --- exact ids -------------------------------------------------------
    for external_id in sorted(by_external)[:max_per_family]:
        queries.append(
            RetrievalQuery(
                query_id=f"id-{external_id}",
                family="exact_id",
                text=external_id,
                expected_passages=tuple(
                    p.passage_id for p in by_external[external_id]
                ),
                derivation="the passage carrying this external ID",
                source_external_id=external_id,
            )
        )

    # --- signal names ----------------------------------------------------
    occurrences: dict[str, list] = {}
    for passage in passages:
        for name in set(IDENTIFIER.findall(passage.text)):
            occurrences.setdefault(name, []).append(passage)
    # a name that is everywhere tests nothing, a name that is nowhere either
    candidates = sorted(
        (name for name, hits in occurrences.items() if 1 <= len(hits) <= 6),
        key=lambda name: (-len(occurrences[name]), name),
    )
    for name in candidates[:max_per_family]:
        queries.append(
            RetrievalQuery(
                query_id=f"signal-{name}",
                family="signal_name",
                text=name,
                expected_passages=tuple(
                    p.passage_id for p in occurrences[name]
                ),
                derivation=f"every passage whose text contains {name!r}",
            )
        )

    # --- paraphrases -----------------------------------------------------
    for external_id, question in paraphrases:
        targets = by_external.get(external_id)
        if not targets:
            raise ValueError(
                f"the paraphrase for {external_id!r} names a requirement the "
                "corpus does not hold"
            )
        queries.append(
            RetrievalQuery(
                query_id=f"para-{external_id}",
                family="paraphrase",
                text=question,
                expected_passages=tuple(p.passage_id for p in targets),
                derivation=(
                    f"authored question about {external_id}; the expected "
                    "passage is that requirement"
                ),
                source_external_id=external_id,
            )
        )
    return queries


# -------------------------------------------------------------- the run ----
def evaluate(
    retriever,
    queries: Sequence[RetrievalQuery],
    *,
    name: str,
    limit: int = 10,
    kinds: Iterable[ContextKind] | None = None,
    terms_of=None,
) -> RetrievalScore:
    """Run every query and record where the expected passage landed."""
    import time

    split = terms_of or (lambda text: text.split())
    score = RetrievalScore(retriever=name)
    for query in queries:
        started = time.perf_counter()
        hits = retriever.search(split(query.text), limit=limit, kinds=kinds)
        elapsed = time.perf_counter() - started
        ranked = tuple(hit.passage.passage_id for hit in hits)
        expected = set(query.expected_passages)
        rank = next(
            (i for i, pid in enumerate(ranked, 1) if pid in expected), None
        )
        score.outcomes.append(
            QueryOutcome(
                query=query, ranked=ranked, first_hit_rank=rank, seconds=elapsed
            )
        )
    return score


def comparison_table(scores: Sequence[RetrievalScore], *, ks=(1, 3, 5)) -> str:
    """A plain table, one block per query family."""
    if not scores:
        return "no results"
    families = sorted({f for s in scores for f in s.families})
    width = max(len(s.retriever) for s in scores) + 2
    lines: list[str] = []
    for family in [*families, None]:
        label = family or "all queries"
        counts = {len(s._subset(family)) for s in scores}  # noqa: SLF001
        lines.append(f"{label}  ({counts.pop() if len(counts) == 1 else '?'} queries)")
        header = f"  {'retriever':<{width}}" + "".join(
            f"recall@{k:<4}" for k in ks
        ) + f"{'MRR':<9}{'median s':<10}misses"
        lines.append(header)
        for s in scores:
            row = f"  {s.retriever:<{width}}"
            row += "".join(f"{s.recall_at(k, family):<11.2f}" for k in ks)
            row += f"{s.mrr(family):<9.3f}{s.median_seconds(family):<10.3f}"
            row += str(len(s.misses(family)))
            lines.append(row)
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "IDENTIFIER",
    "QueryOutcome",
    "RetrievalQuery",
    "RetrievalScore",
    "build_query_set",
    "comparison_table",
    "evaluate",
]
