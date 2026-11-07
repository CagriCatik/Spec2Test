"""Lexical and semantic retrieval, fused by rank.

Measured on this corpus, neither retriever wins outright: BM25 is perfect on
exact IDs and strong on signal names, embeddings are more than twice as good
on prose questions that share no word with the passage. Fusing the two ranked
lists keeps the strengths of both without deciding per query which one to
trust.

Reciprocal rank fusion is used because it needs no score calibration: BM25
scores and cosine similarities are not comparable numbers, but their ranks
are. Each retriever contributes ``1 / (k + rank)``; ``k`` damps the influence
of a single top hit, so a passage both retrievers like beats one that only
one of them ranked first.
"""
from __future__ import annotations

from typing import Iterable, Sequence

from ...domain.models import ContextKind
from .sqlite_index import Hit

#: the usual damping constant of reciprocal rank fusion
DEFAULT_RRF_K = 60


class HybridKnowledgeIndex:
    """Fuses the ranked lists of a lexical and a semantic retriever."""

    def __init__(
        self,
        lexical,
        semantic,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        lexical_weight: float = 1.0,
        semantic_weight: float = 1.0,
        over_fetch: int = 3,
    ) -> None:
        self._lexical = lexical
        self._semantic = semantic
        self._k = rrf_k
        self._weights = (lexical_weight, semantic_weight)
        self._over_fetch = max(1, over_fetch)

    def search(
        self,
        terms: Sequence[str],
        *,
        limit: int = 5,
        kinds: Iterable[ContextKind] | None = None,
        exclude_documents: Iterable[str] = (),
        exclude_passages: Iterable[str] = (),
    ) -> list[Hit]:
        kinds = list(kinds) if kinds is not None else None
        deep = limit * self._over_fetch
        lists = [
            retriever.search(
                terms,
                limit=deep,
                kinds=kinds,
                exclude_documents=exclude_documents,
                exclude_passages=exclude_passages,
            )
            for retriever in (self._lexical, self._semantic)
        ]

        fused: dict[str, float] = {}
        best_hit: dict[str, Hit] = {}
        for weight, hits in zip(self._weights, lists):
            for rank, hit in enumerate(hits, 1):
                passage_id = hit.passage.passage_id
                fused[passage_id] = fused.get(passage_id, 0.0) + weight / (
                    self._k + rank
                )
                # keep the richer hit: the one that recorded matched terms
                previous = best_hit.get(passage_id)
                if previous is None or (not previous.terms and hit.terms):
                    best_hit[passage_id] = hit

        ordered = sorted(fused.items(), key=lambda pair: pair[1], reverse=True)
        results: list[Hit] = []
        seen_sections: set[tuple[str, str | None]] = set()
        for passage_id, score in ordered:
            hit = best_hit[passage_id]
            section = (hit.passage.document_id, hit.passage.heading)
            if section in seen_sections:
                continue
            seen_sections.add(section)
            # smaller is better, as both inputs report it
            results.append(
                Hit(
                    passage=hit.passage,
                    score=-score,
                    terms=hit.terms,
                    document=hit.document,
                )
            )
            if len(results) >= limit:
                break
        return results

    # the exact paths stay lexical: that is where BM25 is perfect
    def by_external_id(self, external_id: str):
        return self._lexical.by_external_id(external_id)

    def document(self, document_id: str):
        return self._lexical.document(document_id)

    def passage_count(self) -> int:
        return self._lexical.passage_count()


__all__ = ["DEFAULT_RRF_K", "HybridKnowledgeIndex"]
