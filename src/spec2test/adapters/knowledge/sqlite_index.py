"""A durable knowledge index: SQLite with FTS5.

One file, no server. Exact lookups by requirement ID and full-text search over
a normalised copy of every passage, while the original text and its position
stay untouched next to it.

Synchronisation compares the content hash *and* the parser version: a file
that has not changed still has to be read again once the importer changed.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ...application.knowledge import (
    PARSER_VERSION,
    Passage,
    normalise_for_search,
)
from ...domain.models import ContextKind, SourceDocument

SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    document_id    TEXT PRIMARY KEY,
    filename       TEXT NOT NULL,
    path           TEXT,
    declared_id    TEXT,
    version        TEXT,
    content_hash   TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    indexed_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS passages (
    passage_id  TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    external_id TEXT,
    heading     TEXT,
    page        INTEGER,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL,
    text        TEXT NOT NULL,
    -- repeated context that is not part of text, such as a table's header
    -- rows. Stored apart so text stays the exact document slice.
    prefix      TEXT,
    search_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS passages_external
    ON passages(external_id);
CREATE INDEX IF NOT EXISTS passages_document
    ON passages(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    search_text,
    passage_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


class IndexError_(RuntimeError):
    """The index cannot be used; the message says why."""


@dataclass(frozen=True)
class Hit:
    """A search result, with everything needed to trace it back."""

    passage: Passage
    score: float
    terms: tuple[str, ...]
    document: SourceDocument | None = None


@dataclass
class SyncReport:
    added: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, {len(self.updated)} updated, "
            f"{len(self.removed)} removed, {len(self.unchanged)} unchanged"
        )


def _fts_query(terms: Sequence[str]) -> str:
    """A safe OR query: every term is quoted, so FTS syntax cannot leak in."""
    cleaned = []
    for term in terms:
        token = re.sub(r'["\']', " ", str(term)).strip()
        if len(token) < 2:
            continue
        cleaned.append(f'"{token}"')
    return " OR ".join(cleaned)


class SqliteKnowledgeIndex:
    """Implements the knowledge port on SQLite + FTS5."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        with self._connection:
            self._connection.executescript(_SCHEMA)
            self._connection.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)",
                (SCHEMA_VERSION,),
            )
        stored = self._connection.execute(
            "SELECT value FROM meta WHERE key = 'schema'"
        ).fetchone()
        if stored and stored["value"] != SCHEMA_VERSION:
            raise IndexError_(
                f"{self.path} uses index schema {stored['value']}, this version "
                f"reads {SCHEMA_VERSION}. Delete the file to rebuild it."
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "SqliteKnowledgeIndex":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ---------------------------------------------------------- writing --
    def needs_reindex(self, document: SourceDocument) -> bool:
        row = self._connection.execute(
            "SELECT content_hash, parser_version FROM documents WHERE document_id = ?",
            (document.document_id,),
        ).fetchone()
        if row is None:
            return True
        return (
            row["content_hash"] != document.content_hash
            or row["parser_version"] != PARSER_VERSION
        )

    def put_document(
        self,
        document: SourceDocument,
        passages: Sequence[Passage],
        *,
        path: str | None = None,
        indexed_at: str = "",
    ) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM passages_fts WHERE passage_id IN "
                "(SELECT passage_id FROM passages WHERE document_id = ?)",
                (document.document_id,),
            )
            self._connection.execute(
                "DELETE FROM passages WHERE document_id = ?", (document.document_id,)
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO documents(document_id, filename, path, "
                "declared_id, version, content_hash, parser_version, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document.document_id,
                    document.filename,
                    path,
                    document.declared_id,
                    document.version,
                    document.content_hash,
                    PARSER_VERSION,
                    indexed_at,
                ),
            )
            for passage in passages:
                # column names are searchable: a query for "debounce time"
                # must reach the rows of the table that has that column
                search_text = passage.search_text
                self._connection.execute(
                    "INSERT INTO passages(passage_id, document_id, kind, "
                    "external_id, heading, page, char_start, char_end, text, "
                    "prefix, search_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        passage.passage_id,
                        passage.document_id,
                        passage.kind.value,
                        passage.external_id,
                        passage.heading,
                        passage.page,
                        passage.char_start,
                        passage.char_end,
                        passage.text,
                        passage.prefix,
                        search_text,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO passages_fts(search_text, passage_id) VALUES (?, ?)",
                    (search_text, passage.passage_id),
                )

    def remove_document(self, document_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM passages_fts WHERE passage_id IN "
                "(SELECT passage_id FROM passages WHERE document_id = ?)",
                (document_id,),
            )
            self._connection.execute(
                "DELETE FROM passages WHERE document_id = ?", (document_id,)
            )
            self._connection.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )

    def known_document_ids(self) -> set[str]:
        return {
            row["document_id"]
            for row in self._connection.execute("SELECT document_id FROM documents")
        }

    # ---------------------------------------------------------- reading --
    def document(self, document_id: str) -> SourceDocument | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            return None
        from ...domain.models import DocumentFormat

        return SourceDocument(
            document_id=row["document_id"],
            filename=row["filename"],
            format=DocumentFormat.MARKDOWN,
            content_hash=row["content_hash"],
            text="",
            declared_id=row["declared_id"],
            version=row["version"],
        )

    def passage_count(self) -> int:
        return self._connection.execute("SELECT COUNT(*) AS n FROM passages").fetchone()[
            "n"
        ]

    def by_external_id(self, external_id: str) -> list[Passage]:
        rows = self._connection.execute(
            "SELECT * FROM passages WHERE external_id = ? ORDER BY passage_id",
            (external_id,),
        ).fetchall()
        return [_passage(row) for row in rows]

    def search(
        self,
        terms: Sequence[str],
        *,
        limit: int = 5,
        kinds: Iterable[ContextKind] | None = None,
        exclude_documents: Iterable[str] = (),
        exclude_passages: Iterable[str] = (),
    ) -> list[Hit]:
        query = _fts_query(terms)
        if not query:
            return []
        sql = [
            "SELECT p.*, bm25(passages_fts) AS score",
            "FROM passages_fts f JOIN passages p ON p.passage_id = f.passage_id",
            "WHERE passages_fts MATCH ?",
        ]
        params: list[object] = [query]
        if kinds is not None:
            wanted = [k.value for k in kinds]
            sql.append(f"AND p.kind IN ({', '.join('?' * len(wanted))})")
            params.extend(wanted)
        excluded_documents = list(exclude_documents)
        if excluded_documents:
            sql.append(
                f"AND p.document_id NOT IN ({', '.join('?' * len(excluded_documents))})"
            )
            params.extend(excluded_documents)
        excluded_passages = list(exclude_passages)
        if excluded_passages:
            sql.append(
                f"AND p.passage_id NOT IN ({', '.join('?' * len(excluded_passages))})"
            )
            params.extend(excluded_passages)
        # ask for more than needed: near-duplicates of one section are merged
        sql.append("ORDER BY score LIMIT ?")
        params.append(max(limit * 4, limit))

        try:
            rows = self._connection.execute(" ".join(sql), params).fetchall()
        except sqlite3.OperationalError as exc:  # pragma: no cover - defensive
            raise IndexError_(f"search failed: {exc}") from exc

        hits: list[Hit] = []
        seen_sections: set[tuple[str, str | None]] = set()
        for row in rows:
            passage = _passage(row)
            # one section counts once, however it was split
            section = (passage.document_id, passage.heading)
            if section in seen_sections:
                continue
            seen_sections.add(section)
            matched = tuple(
                term
                for term in terms
                if str(term).lower() in row["search_text"].lower()
            )
            hits.append(
                Hit(
                    passage=passage,
                    score=float(row["score"]),
                    terms=matched,
                    document=self.document(passage.document_id),
                )
            )
            if len(hits) >= limit:
                break
        return hits


def _passage(row: sqlite3.Row) -> Passage:
    return Passage(
        passage_id=row["passage_id"],
        document_id=row["document_id"],
        kind=ContextKind(row["kind"]),
        text=row["text"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        heading=row["heading"],
        external_id=row["external_id"],
        page=row["page"],
        prefix=row["prefix"] if "prefix" in row.keys() else None,
    )
