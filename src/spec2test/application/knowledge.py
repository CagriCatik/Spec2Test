"""The knowledge base: passages of the specification corpus.

A passage is the unit the index stores and the search returns. Requirements
stay whole; other sections are split along their headings and, when long,
further split while keeping their heading and their position in the original
document.

Two texts are kept for every passage: the original, unchanged, and a
normalised form used for searching. A hit always points back to the original
position, so a quotation can be checked against the document.

No SQL, no Qt, no HTTP: the index itself is an adapter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..domain.models import ContextKind, Requirement, SourceDocument
from ..domain.validation import _UNIT, normalise_math
from .ports import ImportedDocument

#: Raising this invalidates every indexed passage: a file whose content hash
#: is unchanged still has to be read again when the parser changed.
PARSER_VERSION = "3"

MAX_PASSAGE_CHARS = 2_400
MIN_PASSAGE_CHARS = 40
#: A table is a list of independent facts, not prose. Cutting it into small
#: groups makes a single DTC or signal findable instead of drowning it in
#: fourteen neighbours, and keeps one row's worth of budget from costing the
#: price of a whole table.
MAX_TABLE_CHARS = 900
#: a row of a Markdown table: ``| a | b |``
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
#: the delimiter under a table's header: ``| :--- | ---: |``
_TABLE_DELIMITER = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

_HEADING = re.compile(r"^(?P<marks>#{1,6})\s*(?P<title>.+?)\s*$", re.MULTILINE)
#: A "**Label:** value" line. The header block of a document consists of
#: these; its content is already stored as document metadata, so indexing it
#: only adds noise and matches every query.
_LABEL_LINE = re.compile(r"^\s*(?:\*\*|__)[^*_\n]{2,60}:?(?:\*\*|__)\s*:?")
# only a horizontal rule - a table row is content, and in this corpus the
# signal and DTC tables are the most valuable context there is
_SEPARATOR = re.compile(r"^\s*[-=*_]{3,}\s*$")


def is_metadata_block(text: str) -> bool:
    """True when a passage is only a title and labelled metadata lines."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return True
    informative = [
        line
        for line in lines
        if not line.lstrip().startswith("#")
        and not _LABEL_LINE.match(line)
        and not _SEPARATOR.match(line)
    ]
    return not informative

_KIND_HINTS: tuple[tuple[ContextKind, tuple[str, ...]], ...] = (
    (
        ContextKind.SIGNAL,
        ("signal", "can ", "can-", "canfd", "can fd", "pdu", "message", "telemetry",
         "did ", "dtc", "frame", "bus matrix", "botschaft"),
    ),
    (
        ContextKind.INTERFACE,
        ("interface", "hsi", "hardware and software", "hardware/software", "api",
         "port", "pinout", "connector", "schnittstelle"),
    ),
    (
        ContextKind.REFERENCE_TEST,
        ("test", "verification", "validation", "hil", "compliance", "acceptance",
         "prüf", "nachweis"),
    ),
    (
        ContextKind.EXPLANATION,
        ("glossary", "definition", "terminology", "abbreviation", "scope",
         "overview", "state", "mode", "zustand", "begriff"),
    ),
)


def classify(heading: str | None, text: str) -> ContextKind:
    """What kind of context a passage is, judged by its heading first."""
    haystack = f"{heading or ''}\n{text[:400]}".lower()
    headline = (heading or "").lower()
    for kind, hints in _KIND_HINTS:
        if any(hint in headline for hint in hints):
            return kind
    for kind, hints in _KIND_HINTS:
        if any(hint in haystack for hint in hints):
            return kind
    return ContextKind.EXPLANATION


def normalise_for_search(text: str) -> str:
    """The form the index searches in.

    LaTeX notation becomes plain text, so a search for "50 ms" finds
    ``$50\\text{ ms}$``. Markdown decoration is dropped. The original text is
    never changed.
    """
    plain = normalise_math(text)
    plain = re.sub(r"```.*?```", " ", plain, flags=re.DOTALL)
    # the underscore stays: it is part of signal names such as BMS_Pack_U,
    # which are the most useful search terms this corpus has
    plain = re.sub(r"[|>#*`~\[\]()]+", " ", plain)
    return re.sub(r"\s+", " ", plain).strip()


@dataclass(frozen=True)
class Passage:
    """One indexed piece of a document."""

    passage_id: str
    document_id: str
    kind: ContextKind
    text: str
    char_start: int
    char_end: int
    heading: str | None = None
    external_id: str | None = None
    page: int | None = None
    #: repeated context that is NOT part of ``text``: the header rows of the
    #: table this passage was cut out of. Kept separate so ``text`` stays the
    #: exact slice ``document.text[char_start:char_end]`` - a quote that no
    #: longer matches its position is worse than a missing column name.
    prefix: str | None = None

    @property
    def search_text(self) -> str:
        """What the index matches against, column names included."""
        return normalise_for_search(self.full_text)

    @property
    def full_text(self) -> str:
        """The passage as a reader should see it: column meaning, then rows."""
        if not self.prefix:
            return self.text
        return f"{self.prefix}\n{self.text}"

    @property
    def size(self) -> int:
        """Budget cost. The prefix is sent too, so it counts."""
        return len(self.full_text)


@dataclass
class DocumentIndexEntry:
    """What the index knows about one document."""

    document: SourceDocument
    passages: list[Passage] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


def table_header(lines: Sequence[str]) -> tuple[str, int] | None:
    """The header and delimiter row of a Markdown table, if the block is one.

    Returns the two rows joined, and how many lines they took.
    """
    rows = [i for i, line in enumerate(lines) if _TABLE_ROW.match(line)]
    if len(rows) < 3:
        return None
    first = rows[0]
    if first + 1 >= len(lines) or not _TABLE_DELIMITER.match(lines[first + 1]):
        return None
    return "\n".join(lines[first : first + 2]), first + 2


def _split_table(
    text: str, start: int, limit: int
) -> list[tuple[str, int, int, str | None]] | None:
    """Cut a Markdown table into row groups, each keeping the column meaning.

    Returns ``None`` when the block is not a table, so the caller falls back
    to splitting on blank lines. A group never splits a row.
    """
    lines = text.splitlines(keepends=True)
    stripped = [line.rstrip("\n") for line in lines]
    found = table_header(stripped)
    if found is None:
        return None
    header, body_index = found

    # everything up to and including the header rows stays as one passage:
    # it is the section's own introduction plus the column meaning
    head_chars = sum(len(line) for line in lines[:body_index])
    parts: list[tuple[str, int, int, str | None]] = []
    # the header rows alone are not a passage: they say nothing on their own
    # and every row group repeats them anyway
    head_prose = [
        line
        for line in stripped[:body_index]
        if line.strip() and not _TABLE_ROW.match(line) and not line.lstrip().startswith("#")
    ]
    if head_chars and head_prose:
        parts.append((text[:head_chars], start, start + head_chars, None))

    cursor = head_chars
    group_start = cursor
    group: list[str] = []
    budget = max(limit, len(header) + 200)

    def flush() -> None:
        if not group:
            return
        body = "".join(group)
        parts.append((body, start + group_start, start + group_start + len(body), header))

    for line in lines[body_index:]:
        if group and len(header) + sum(map(len, group)) + len(line) > budget:
            flush()
            group_start = cursor
            group = []
        group.append(line)
        cursor += len(line)
    flush()
    return parts


def _split_long(text: str, start: int, limit: int) -> list[tuple[str, int, int]]:
    """Split on blank lines, keeping each part's position in the document."""
    if len(text) <= limit:
        return [(text, start, start + len(text))]
    parts: list[tuple[str, int, int]] = []
    cursor = 0
    while cursor < len(text):
        window = text[cursor : cursor + limit]
        if cursor + limit < len(text):
            cut = window.rfind("\n\n")
            if cut <= 0:
                cut = window.rfind("\n")
            if cut <= 0:
                cut = len(window)
        else:
            cut = len(window)
        chunk = text[cursor : cursor + cut]
        parts.append((chunk, start + cursor, start + cursor + len(chunk)))
        cursor += cut
    return parts


def segment_document(
    imported: ImportedDocument,
    requirements: Sequence[Requirement] = (),
    *,
    max_chars: int = MAX_PASSAGE_CHARS,
    table_chars: int = MAX_TABLE_CHARS,
) -> list[Passage]:
    """Cut a document into passages.

    Captured requirements stay whole and keep their external ID - a
    requirement cut in half is worse than one that is too long. The rest of
    the document is split along its headings; anything the requirements
    already cover is skipped, so the same sentence is not indexed twice.

    A Markdown table is cut by rows instead, into groups of about
    ``table_chars``. Each group carries the table's header rows as ``prefix``,
    so a group of five DTC rows still says which column is the code, which
    the debounce time and which the safety reaction. Before this, a long
    table was cut at an arbitrary newline and every chunk after the first was
    a wall of values with no column meaning at all.
    """
    document = imported.document
    text = document.text
    passages: list[Passage] = []
    covered: list[tuple[int, int]] = []

    for requirement in requirements:
        if not requirement.spans:
            continue
        start = min(span.char_start for span in requirement.spans)
        end = max(span.char_end for span in requirement.spans)
        covered.append((start, end))
        passages.append(
            Passage(
                passage_id=f"{document.document_id}:req:{start}",
                document_id=document.document_id,
                kind=ContextKind.REQUIREMENT,
                text=requirement.text,
                char_start=start,
                char_end=end,
                heading=_heading_for(text, start),
                external_id=requirement.external_id,
                page=requirement.spans[0].page,
            )
        )

    headings = list(_HEADING.finditer(text))
    sections: list[tuple[str | None, int, int]] = []
    if not headings:
        sections.append((None, 0, len(text)))
    else:
        if headings[0].start() > 0:
            sections.append((None, 0, headings[0].start()))
        for index, heading in enumerate(headings):
            end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(text)
            )
            sections.append((heading.group("title"), heading.start(), end))

    for heading, start, end in sections:
        section = text[start:end]
        # a table is cut by rows, so one DTC or one signal is findable on its
        # own and every group still states what its columns mean
        chunks = _split_table(section, start, table_chars)
        if chunks is None:
            chunks = [
                (chunk, chunk_start, chunk_end, None)
                for chunk, chunk_start, chunk_end in _split_long(
                    section, start, max_chars
                )
            ]
        for chunk, chunk_start, _chunk_end, prefix in chunks:
            remaining = _subtract(chunk, chunk_start, covered)
            for part, part_start in remaining:
                # a group of table rows is informative with its columns even
                # when the rows alone are short; dropping it would lose the
                # tail of every table
                informative = len(part.strip()) + (len(prefix) if prefix else 0)
                if informative < MIN_PASSAGE_CHARS or not part.strip():
                    continue
                if prefix is None and is_metadata_block(part):
                    continue  # already stored as document metadata
                passages.append(
                    Passage(
                        passage_id=f"{document.document_id}:sec:{part_start}",
                        document_id=document.document_id,
                        kind=classify(heading, prefix or part),
                        text=part,
                        char_start=part_start,
                        char_end=part_start + len(part),
                        heading=heading,
                        prefix=prefix,
                    )
                )
    passages.sort(key=lambda p: (p.char_start, p.passage_id))
    return passages


def _subtract(
    text: str, start: int, covered: Sequence[tuple[int, int]]
) -> list[tuple[str, int]]:
    """The parts of ``text`` that no requirement already covers."""
    end = start + len(text)
    cuts = sorted(
        (max(a, start), min(b, end)) for a, b in covered if a < end and b > start
    )
    pieces: list[tuple[str, int]] = []
    cursor = start
    for cut_start, cut_end in cuts:
        if cut_start > cursor:
            pieces.append((text[cursor - start : cut_start - start], cursor))
        cursor = max(cursor, cut_end)
    if cursor < end:
        pieces.append((text[cursor - start : end - start], cursor))
    return pieces


def _heading_for(text: str, position: int) -> str | None:
    last: str | None = None
    for heading in _HEADING.finditer(text):
        if heading.start() > position:
            break
        last = heading.group("title")
    return last


#: Acronyms that stand in nearly every document of a specification set. They
#: match everything and therefore say nothing about relevance.
_BOILERPLATE = {
    "iso", "iec", "sae", "unece", "ece", "asil", "qm", "srs", "req", "spec",
    "bms", "ecu", "sw", "hw", "oem", "pdf", "doc",
}


def search_terms(requirement: Requirement, *, limit: int = 14) -> list[str]:
    """Salient terms of a requirement, for a full-text query.

    Signal-like identifiers and quantities come first: they are what makes a
    section relevant. Terms taken from the requirement's own ID and
    standards boilerplate are dropped - they match every document.
    """
    text = normalise_for_search(requirement.text)
    own = {
        part.lower()
        for identifier in (requirement.external_id, *requirement.references)
        if identifier
        for part in re.split(r"[-_.]", identifier)
    }
    skip = own | _BOILERPLATE

    identifiers = re.findall(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b", text)
    quantities = re.findall(
        rf"\b\d+(?:[.,]\d+)?\s*{_UNIT}\b", text, flags=re.IGNORECASE
    )
    acronyms = re.findall(r"\b[A-Z]{3,}\b", text)
    words = re.findall(r"\b[a-z]{5,}\b", text.lower())

    ordered: list[str] = []
    seen: set[str] = set()
    # identifiers and domain words decide relevance; a bare quantity such as
    # "100 ms" stands in half the corpus
    for group in (identifiers, words, acronyms, quantities):
        for term in group:
            cleaned = term.strip()
            key = cleaned.lower()
            if not cleaned or key in seen or key in skip or key in _STOPWORDS:
                continue
            seen.add(key)
            ordered.append(cleaned)
    return ordered[:limit]


_STOPWORDS = {
    "shall", "should", "would", "where", "which", "while", "their", "there",
    "these", "those", "within", "without", "after", "before", "every", "other",
    "using", "based", "value", "values", "system", "software", "requirement",
    "following", "between", "during", "given", "when", "then",
    # structural words of this document template: they stand in every section
    "title", "rationale", "scope", "overview", "notes", "inputs", "outputs",
    "description", "verification", "specification", "general", "level",
    "section", "chapter", "table", "figure", "above", "below",
}


def documents_of(entries: Iterable[DocumentIndexEntry]) -> dict[str, SourceDocument]:
    return {entry.document.document_id: entry.document for entry in entries}
