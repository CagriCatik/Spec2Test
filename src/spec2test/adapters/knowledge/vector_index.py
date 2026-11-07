"""Semantic retrieval over the passages the lexical index already holds.

The point of this adapter is a fair comparison. It does not parse documents,
split passages, classify kinds or resolve IDs: all of that stays in
:mod:`spec2test.application.knowledge` and the SQLite index. It reads the very
same passages and only ranks them differently, by cosine similarity between
the query and the passage text.

LlamaIndex supplies the vector store and the similarity query; the embeddings
come from an Ollama model. Both are imported inside the functions that need
them, so the package still imports on a machine that has neither.

Scores follow the convention of the lexical index: smaller is better. A
cosine similarity of 0.82 is reported as -0.82, which lets the application's
relevance cut-off work on both retrievers unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ...application.knowledge import Passage
from ...domain.models import ContextKind, SourceDocument
from .sqlite_index import Hit, SqliteKnowledgeIndex

#: bumped when the stored vectors stop being comparable with fresh ones
VECTOR_FORMAT_VERSION = "1"


class EmbeddingUnavailable(RuntimeError):
    """The embedding model could not be reached or is not installed."""


# ------------------------------------------------------------- embedding ---
@dataclass
class OllamaEmbedder:
    """Embeddings from a local Ollama model, through LlamaIndex.

    The model must be present locally (``ollama pull nomic-embed-text``).
    No key is read or sent: this talks to the local daemon only.
    """

    model: str = "nomic-embed-text"
    base_url: str = "http://localhost:11434"
    batch_size: int = 32

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def _client(self):
        try:
            from llama_index.embeddings.ollama import OllamaEmbedding
        except ImportError as exc:  # pragma: no cover - depends on the machine
            raise EmbeddingUnavailable(
                "llama-index-embeddings-ollama is not installed: "
                "pip install llama-index-core llama-index-embeddings-ollama"
            ) from exc
        return OllamaEmbedding(
            model_name=self.model,
            base_url=self.base_url,
            embed_batch_size=self.batch_size,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._client()
        try:
            return [
                list(vector)
                for vector in client.get_text_embedding_batch(list(texts))
            ]
        except Exception as exc:  # noqa: BLE001 - every transport error is one case
            raise EmbeddingUnavailable(
                f"the embedding model {self.model!r} at {self.base_url} could not "
                f"be reached: {type(exc).__name__}: {exc}"
            ) from exc

    def embed_query(self, text: str) -> list[float]:
        client = self._client()
        try:
            return list(client.get_query_embedding(text))
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingUnavailable(
                f"the embedding model {self.model!r} at {self.base_url} could not "
                f"be reached: {type(exc).__name__}: {exc}"
            ) from exc


# ------------------------------------------------------------ the adapter ---
class VectorKnowledgeIndex:
    """Cosine retrieval over the passages of a :class:`SqliteKnowledgeIndex`.

    Vectors are cached next to the lexical index, keyed by passage id, the
    hash of the passage text and the embedding model. A passage whose text
    changed is embedded again; the rest is read from the cache, so a repeated
    benchmark run does not pay for the corpus twice.
    """

    def __init__(
        self,
        lexical: SqliteKnowledgeIndex,
        embedder=None,
        *,
        cache_path: Path | str | None = None,
    ) -> None:
        self._lexical = lexical
        self._embedder = embedder or OllamaEmbedder()
        self._cache_path = Path(cache_path) if cache_path else None
        self._vectors: dict[str, list[float]] = {}
        self._passages: dict[str, Passage] = {}
        self._built = False

    # ------------------------------------------------------------ build --
    def build(self, *, progress=None) -> int:
        """Embed every passage that is not in the cache. Returns how many."""
        passages = _all_passages(self._lexical)
        self._passages = {p.passage_id: p for p in passages}
        cached = self._read_cache()
        pending = [
            p for p in passages if _cache_key(p, self._embedder.name) not in cached
        ]
        if progress:
            progress(f"{len(passages)} passage(s), {len(pending)} to embed")
        for start in range(0, len(pending), 64):
            batch = pending[start : start + 64]
            vectors = self._embedder.embed([p.text for p in batch])
            for passage, vector in zip(batch, vectors):
                cached[_cache_key(passage, self._embedder.name)] = vector
            if progress:
                progress(f"embedded {min(start + 64, len(pending))}/{len(pending)}")
        self._write_cache(cached)
        self._vectors = {
            p.passage_id: cached[_cache_key(p, self._embedder.name)]
            for p in passages
            if _cache_key(p, self._embedder.name) in cached
        }
        self._built = True
        return len(pending)

    # ----------------------------------------------------------- search --
    def search(
        self,
        terms: Sequence[str],
        *,
        limit: int = 5,
        kinds: Iterable[ContextKind] | None = None,
        exclude_documents: Iterable[str] = (),
        exclude_passages: Iterable[str] = (),
    ) -> list[Hit]:
        """Rank by similarity to the terms, read as one query."""
        if not self._built:
            self.build()
        query = " ".join(str(term) for term in terms if str(term).strip())
        if not query or not self._vectors:
            return []
        wanted = {k for k in kinds} if kinds is not None else None
        skip_documents = set(exclude_documents)
        skip_passages = set(exclude_passages)

        vector = self._embedder.embed_query(query)
        scored: list[tuple[float, Passage]] = []
        for passage_id, stored in self._vectors.items():
            passage = self._passages[passage_id]
            if wanted is not None and passage.kind not in wanted:
                continue
            if passage.document_id in skip_documents:
                continue
            if passage_id in skip_passages:
                continue
            scored.append((_cosine(vector, stored), passage))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        hits: list[Hit] = []
        seen_sections: set[tuple[str, str | None]] = set()
        lowered = [str(term).lower() for term in terms]
        for similarity, passage in scored:
            section = (passage.document_id, passage.heading)
            if section in seen_sections:
                continue
            seen_sections.add(section)
            haystack = passage.search_text.lower()
            hits.append(
                Hit(
                    passage=passage,
                    # smaller is better, as the lexical index reports it
                    score=-float(similarity),
                    terms=tuple(t for t in lowered if t in haystack),
                    document=self._lexical.document(passage.document_id),
                )
            )
            if len(hits) >= limit:
                break
        return hits

    # ------------------------------------------------------------ cache --
    def _read_cache(self) -> dict[str, list[float]]:
        if self._cache_path is None or not self._cache_path.exists():
            return {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if raw.get("format") != VECTOR_FORMAT_VERSION:
            return {}
        return {key: list(value) for key, value in raw.get("vectors", {}).items()}

    def _write_cache(self, vectors: dict[str, list[float]]) -> None:
        if self._cache_path is None:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(
                {
                    "format": VECTOR_FORMAT_VERSION,
                    "model": self._embedder.name,
                    "vectors": vectors,
                }
            ),
            encoding="utf-8",
        )

    @property
    def model_name(self) -> str:
        return self._embedder.name

    def document(self, document_id: str) -> SourceDocument | None:
        return self._lexical.document(document_id)

    def passage_count(self) -> int:
        return len(self._passages) if self._built else self._lexical.passage_count()

    def by_external_id(self, external_id: str) -> list[Passage]:
        """Exact ID resolution stays with the lexical index, as it should."""
        return self._lexical.by_external_id(external_id)


# ----------------------------------------------------------------- helpers --
def _all_passages(index: SqliteKnowledgeIndex) -> list[Passage]:
    from .sqlite_index import _passage

    connection = index._connection  # noqa: SLF001 - same package, one storage
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM passages ORDER BY passage_id").fetchall()
    return [_passage(row) for row in rows]


def _cache_key(passage: Passage, model: str) -> str:
    digest = hashlib.sha256(passage.text.encode("utf-8")).hexdigest()[:16]
    return f"{model}|{passage.passage_id}|{digest}"


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


__all__ = [
    "EmbeddingUnavailable",
    "OllamaEmbedder",
    "VectorKnowledgeIndex",
    "VECTOR_FORMAT_VERSION",
]
