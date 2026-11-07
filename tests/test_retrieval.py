"""Lexical, semantic and fused retrieval over the same passages.

No network and no model: the embedder is a deterministic stand-in, so these
tests state the contract (same passages, same IDs, comparable scores) rather
than the quality of any particular model. The quality numbers live in
docs/benchmarks and are produced by a script, not by the test suite.
"""
from __future__ import annotations

import math
import re

import pytest

from spec2test.adapters.knowledge import (
    HybridKnowledgeIndex,
    SqliteKnowledgeIndex,
    VectorKnowledgeIndex,
)
from spec2test.adapters.knowledge.vector_index import (
    EmbeddingUnavailable,
    OllamaEmbedder,
    _all_passages,
    _cosine,
)
from spec2test.application.indexing import sync_index
from spec2test.application.retrieval_eval import (
    IDENTIFIER,
    RetrievalQuery,
    RetrievalScore,
    QueryOutcome,
    build_query_set,
    comparison_table,
    evaluate,
)
from spec2test.bootstrap import build_importers
from spec2test.domain.models import ContextKind

WORD = re.compile(r"[a-z0-9]+")


class BagOfWordsEmbedder:
    """A deterministic stand-in: one dimension per hashed word.

    It is not a good retriever and is not meant to be. It gives the vector
    index real vectors to rank with, so the adapter can be tested without a
    model, a daemon or a network.
    """

    name = "fake/bag-of-words"
    dimensions = 64

    def __init__(self) -> None:
        self.calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in WORD.findall(text.lower()):
            vector[hash(word) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed(self, texts):
        self.calls += 1
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


CORPUS = {
    "a_current": (
        "# Current validation\n\n"
        "### Requirement: SYS-REQ-0001\n"
        "* **Title:** Pack current validation\n"
        "* The BMS shall validate the pack current from `Shunt_Current_Raw`.\n"
    ),
    "b_thermal": (
        "# Thermal\n\n"
        "### Requirement: SYS-REQ-0002\n"
        "* **Title:** Coolant request\n"
        "* The BMS shall request coolant above `Pack_T_Max` of 45 degrees.\n"
    ),
    "c_signals": (
        "# Signal definitions\n\n"
        "## Signals\n"
        "`Shunt_Current_Raw` is sampled with a period of 10 ms.\n"
    ),
}


@pytest.fixture
def indexes(tmp_path):
    for name, text in CORPUS.items():
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
    lexical = SqliteKnowledgeIndex(tmp_path / "k.db")
    sync_index(lexical, sorted(tmp_path.glob("*.md")), build_importers())
    embedder = BagOfWordsEmbedder()
    vector = VectorKnowledgeIndex(
        lexical, embedder, cache_path=tmp_path / "vectors.json"
    )
    return lexical, vector, embedder, tmp_path


# ======================================================= the vector adapter ==
def test_the_vector_index_ranks_the_passages_of_the_lexical_index(indexes):
    """Same passages, same ids: only the ranking may differ."""
    lexical, vector, _embedder, _tmp = indexes
    vector.build()
    lexical_ids = {p.passage_id for p in _all_passages(lexical)}
    hits = vector.search(["current"], limit=10)
    assert hits, "the vector index returned nothing at all"
    assert {h.passage.passage_id for h in hits} <= lexical_ids
    assert vector.passage_count() == lexical.passage_count()


def test_scores_follow_the_lexical_convention(indexes):
    """Smaller is better, so the application's cut-off works on both."""
    _lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hits = vector.search(["shunt current raw"], limit=5)
    scores = [h.score for h in hits]
    assert scores == sorted(scores), "hits are not ordered best first"
    assert all(s <= 0 for s in scores), f"a similarity was not negated: {scores}"


def test_the_kind_filter_is_honoured(indexes):
    _lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hits = vector.search(["current"], limit=10, kinds=[ContextKind.SIGNAL])
    assert hits, "no signal passage was returned"
    assert all(h.passage.kind is ContextKind.SIGNAL for h in hits)


def test_exclusions_are_honoured(indexes):
    _lexical, vector, _embedder, _tmp = indexes
    vector.build()
    everything = vector.search(["current"], limit=10)
    victim = everything[0].passage
    assert victim.passage_id not in {
        h.passage.passage_id
        for h in vector.search(["current"], limit=10, exclude_passages=[victim.passage_id])
    }
    assert victim.document_id not in {
        h.passage.document_id
        for h in vector.search(
            ["current"], limit=10, exclude_documents=[victim.document_id]
        )
    }


def test_every_hit_keeps_its_provenance(indexes):
    _lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hit = vector.search(["shunt current raw"], limit=1)[0]
    assert hit.document is not None and hit.document.filename
    assert hit.passage.char_start < hit.passage.char_end
    assert hit.passage.document_id


def test_vectors_are_cached_between_runs(indexes):
    _lexical, vector, embedder, tmp_path = indexes
    first = vector.build()
    assert first > 0 and embedder.calls > 0
    calls_after_first = embedder.calls

    again = VectorKnowledgeIndex(
        _lexical, embedder, cache_path=tmp_path / "vectors.json"
    )
    assert again.build() == 0, "the second build embedded again"
    assert embedder.calls == calls_after_first


def test_changed_text_is_embedded_again(indexes):
    lexical, vector, embedder, tmp_path = indexes
    vector.build()
    (tmp_path / "c_signals.md").write_text(
        CORPUS["c_signals"].replace("10 ms", "20 ms"), encoding="utf-8"
    )
    sync_index(lexical, sorted(tmp_path.glob("*.md")), build_importers())
    fresh = VectorKnowledgeIndex(
        lexical, embedder, cache_path=tmp_path / "vectors.json"
    )
    assert fresh.build() >= 1, "a changed passage was served from the cache"


def test_exact_id_resolution_stays_with_the_lexical_index(indexes):
    """Embeddings are poor at IDs; the exact path must not go through them."""
    lexical, vector, _embedder, _tmp = indexes
    vector.build()
    assert [p.passage_id for p in vector.by_external_id("SYS-REQ-0001")] == [
        p.passage_id for p in lexical.by_external_id("SYS-REQ-0001")
    ]


def test_a_missing_embedding_model_is_reported_not_swallowed():
    embedder = OllamaEmbedder(model="definitely-not-a-model", base_url="http://127.0.0.1:1")
    with pytest.raises(EmbeddingUnavailable) as caught:
        embedder.embed(["anything"])
    assert "definitely-not-a-model" in str(caught.value)


def test_cosine_handles_degenerate_vectors():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


# ============================================================== the hybrid ==
def test_the_hybrid_returns_what_both_retrievers_know(indexes):
    lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hybrid = HybridKnowledgeIndex(lexical, vector)
    hits = hybrid.search(["shunt current raw"], limit=5)
    assert hits
    scores = [h.score for h in hits]
    assert scores == sorted(scores) and all(s <= 0 for s in scores)


def test_the_hybrid_keeps_a_passage_only_one_side_found(indexes):
    lexical, vector, _embedder, _tmp = indexes
    vector.build()

    class Blind:
        def search(self, *_a, **_k):
            return []

    only_lexical = HybridKnowledgeIndex(lexical, Blind())
    assert only_lexical.search(["Shunt_Current_Raw"], limit=5), (
        "a dead semantic side must not empty the result"
    )
    only_vector = HybridKnowledgeIndex(Blind(), vector)
    assert only_vector.search(["shunt current raw"], limit=5)


def test_the_hybrid_honours_the_kind_filter_and_exclusions(indexes):
    lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hybrid = HybridKnowledgeIndex(lexical, vector)
    hits = hybrid.search(["current"], limit=10, kinds=[ContextKind.REQUIREMENT])
    assert hits and all(h.passage.kind is ContextKind.REQUIREMENT for h in hits)
    first = hits[0].passage.passage_id
    assert first not in {
        h.passage.passage_id
        for h in hybrid.search(
            ["current"],
            limit=10,
            kinds=[ContextKind.REQUIREMENT],
            exclude_passages=[first],
        )
    }


def test_the_hybrid_reaches_the_exact_id_as_well_as_the_lexical_index(indexes):
    """The measured reason for equal weights: upweighting semantics broke this."""
    lexical, vector, _embedder, _tmp = indexes
    vector.build()
    hybrid = HybridKnowledgeIndex(lexical, vector)
    expected = {p.passage_id for p in lexical.by_external_id("SYS-REQ-0002")}
    ranked = [h.passage.passage_id for h in hybrid.search(["SYS-REQ-0002"], limit=5)]
    assert ranked and ranked[0] in expected, ranked


def test_the_hybrid_can_stand_in_for_the_index_in_context_building(indexes):
    """It must satisfy what build_context expects of a retriever."""
    from spec2test.application.context import build_context
    from spec2test.application.ingest import reference_report

    lexical, vector, _embedder, tmp_path = indexes
    vector.build()
    synced = sync_index(lexical, sorted(tmp_path.glob("*.md")), build_importers())
    requirements = list(synced.requirements)
    target = next(r for r in requirements if r.external_id == "SYS-REQ-0001")
    selection = build_context(
        [target],
        reference_report(requirements),
        documents=dict(synced.documents),
        knowledge=HybridKnowledgeIndex(lexical, vector),
        max_search_items=3,
    )
    assert selection.items, "the hybrid produced no usable context"
    assert all(item.spans for item in selection.items)


# ======================================================== the query set =====
def test_identifiers_are_recognised_and_prose_is_not():
    assert IDENTIFIER.findall("`Pack_Current_Validated` and KL15_Ignition_State") == [
        "Pack_Current_Validated",
        "KL15_Ignition_State",
    ]
    assert IDENTIFIER.findall("The pack current shall be validated.") == []


def test_the_query_set_is_derived_from_the_corpus(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    passages = _all_passages(lexical)
    queries = build_query_set(
        passages,
        max_per_family=10,
        paraphrases=[("SYS-REQ-0002", "When is cooling asked for?")],
    )
    families = {q.family for q in queries}
    assert families == {"exact_id", "signal_name", "paraphrase"}
    for query in queries:
        assert query.expected_passages
        assert set(query.expected_passages) <= {p.passage_id for p in passages}
        assert query.derivation


def test_an_id_query_expects_the_passage_with_that_id(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    passages = _all_passages(lexical)
    query = next(
        q
        for q in build_query_set(passages, max_per_family=10)
        if q.query_id == "id-SYS-REQ-0001"
    )
    expected = {p.passage_id for p in lexical.by_external_id("SYS-REQ-0001")}
    assert set(query.expected_passages) == expected


def test_a_signal_query_expects_every_passage_holding_the_name(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    passages = _all_passages(lexical)
    query = next(
        q
        for q in build_query_set(passages, max_per_family=10)
        if q.query_id == "signal-Shunt_Current_Raw"
    )
    holding = {p.passage_id for p in passages if "Shunt_Current_Raw" in p.text}
    assert set(query.expected_passages) == holding
    assert len(holding) >= 2, "the fixture must have the name in two places"


def test_a_paraphrase_naming_an_unknown_requirement_fails_loudly(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    with pytest.raises(ValueError, match="corpus does not hold"):
        build_query_set(
            _all_passages(lexical),
            max_per_family=1,
            paraphrases=[("SYS-REQ-9999", "does this exist?")],
        )


def test_a_query_without_an_expected_passage_is_refused():
    with pytest.raises(ValueError, match="no expected passage"):
        RetrievalQuery(
            query_id="q", family="exact_id", text="x",
            expected_passages=(), derivation="none",
        )


# ========================================================= the measurement ==
def _outcome(rank, family="exact_id"):
    query = RetrievalQuery(
        query_id=f"q{rank}", family=family, text="x",
        expected_passages=("p",), derivation="test",
    )
    return QueryOutcome(query=query, ranked=(), first_hit_rank=rank)


def test_recall_and_mrr_are_computed_per_family():
    score = RetrievalScore(retriever="r")
    score.outcomes = [
        _outcome(1), _outcome(3), _outcome(None),
        _outcome(2, family="paraphrase"),
    ]
    assert score.recall_at(1, "exact_id") == pytest.approx(1 / 3)
    assert score.recall_at(3, "exact_id") == pytest.approx(2 / 3)
    assert score.mrr("exact_id") == pytest.approx((1 + 1 / 3 + 0) / 3)
    assert score.mrr("paraphrase") == pytest.approx(0.5)
    assert len(score.misses()) == 1
    assert score.families == ["exact_id", "paraphrase"]


def test_a_miss_contributes_nothing_rather_than_crashing():
    score = RetrievalScore(retriever="r", outcomes=[_outcome(None)])
    assert score.mrr() == 0.0 and score.recall_at(10) == 0.0


def test_evaluate_records_where_the_expected_passage_landed(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    passages = _all_passages(lexical)
    queries = build_query_set(passages, max_per_family=5)
    score = evaluate(lexical, queries, name="fts5", limit=10)
    assert len(score.outcomes) == len(queries)
    ids = [o for o in score.outcomes if o.query.family == "exact_id"]
    assert all(o.found for o in ids), "bm25 must find an exact ID"
    assert all(o.first_hit_rank == 1 for o in ids)


def test_the_table_names_every_retriever_and_family(indexes):
    lexical, _vector, _embedder, _tmp = indexes
    queries = build_query_set(_all_passages(lexical), max_per_family=3)
    table = comparison_table([evaluate(lexical, queries, name="fts5", limit=5)])
    assert "fts5" in table
    assert "exact_id" in table and "signal_name" in table
    assert "all queries" in table


# ====================================================== the chosen retriever ==
def test_lexical_is_the_default(tmp_path):
    from spec2test.adapters.knowledge import SqliteKnowledgeIndex
    from spec2test.application.generate import GenerationSettings
    from spec2test.bootstrap import build_knowledge

    assert GenerationSettings().retrieval == "lexical"
    knowledge = build_knowledge(tmp_path / "k.db", GenerationSettings())
    assert isinstance(knowledge, SqliteKnowledgeIndex)


def test_hybrid_is_built_when_it_is_asked_for(tmp_path, monkeypatch):
    from spec2test.application.generate import GenerationSettings
    from spec2test import bootstrap

    import spec2test.adapters.knowledge.vector_index as vi

    monkeypatch.setattr(vi, "OllamaEmbedder", lambda **_k: BagOfWordsEmbedder())
    monkeypatch.setattr(
        "spec2test.adapters.knowledge.OllamaEmbedder",
        lambda **_k: BagOfWordsEmbedder(),
    )
    for name, text in CORPUS.items():
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
    index_path = tmp_path / "k.db"
    sync_index(
        SqliteKnowledgeIndex(index_path),
        sorted(tmp_path.glob("*.md")),
        build_importers(),
    )
    lines: list[str] = []
    knowledge = bootstrap.build_knowledge(
        index_path, GenerationSettings(retrieval="hybrid"), log=lines.append
    )
    assert isinstance(knowledge, HybridKnowledgeIndex)
    assert any("hybrid retrieval" in line for line in lines), lines
    assert knowledge.search(["Shunt_Current_Raw"], limit=3)


def test_a_missing_model_falls_back_to_lexical_and_says_so(tmp_path, monkeypatch):
    """A run must not stop over this, and it must not pass unnoticed."""
    from spec2test.application.generate import GenerationSettings
    from spec2test import bootstrap

    class Dead:
        name = "dead"

        def embed(self, _texts):
            raise EmbeddingUnavailable("no daemon at http://localhost:11434")

        def embed_query(self, _text):  # pragma: no cover - never reached
            raise EmbeddingUnavailable("no daemon")

    monkeypatch.setattr(
        "spec2test.adapters.knowledge.OllamaEmbedder", lambda **_k: Dead()
    )
    for name, text in CORPUS.items():
        (tmp_path / f"{name}.md").write_text(text, encoding="utf-8")
    index_path = tmp_path / "k.db"
    sync_index(
        SqliteKnowledgeIndex(index_path),
        sorted(tmp_path.glob("*.md")),
        build_importers(),
    )
    lines: list[str] = []
    knowledge = bootstrap.build_knowledge(
        index_path, GenerationSettings(retrieval="hybrid"), log=lines.append
    )
    assert isinstance(knowledge, SqliteKnowledgeIndex)
    warnings = [line for line in lines if line.startswith("warning:")]
    assert warnings and "Falling back to lexical" in warnings[0], lines


def test_the_setting_is_read_from_the_configuration():
    from spec2test.bootstrap import generation_settings_from_config

    settings = generation_settings_from_config(
        {"generation": {"retrieval": " Hybrid ", "embedding_model": "other:1b"}}
    )
    assert settings.retrieval == "hybrid"
    assert settings.embedding_model == "other:1b"
