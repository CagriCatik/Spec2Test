"""Import documents and capture requirements with traceable source spans.

Requirement capture is deterministic in L1: structured requirement IDs are
detected by pattern, and text without an ID is split on paragraph boundaries.
Model-assisted segmentation (AP 4) is deliberately not part of this delivery.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from ..domain.models import (
    ClarificationStatus,
    Requirement,
    Severity,
    SourceDocument,
    SourceSpan,
    ValidationIssue,
)
from .ports import DocumentImportPort, ImportedDocument, TextSegment

# Uppercase segments around a REQ marker, ending in a number. Matches
#   RAS-SW-REQ-0010.3      prefix before REQ
#   BMS-REQ-AFE-0101       subsystem segment after REQ
#   SYS-REQ-004, REQ-12    the short forms
DEFAULT_REQUIREMENT_ID_PATTERN = (
    r"\b((?:[A-Z][A-Z0-9]*-)*REQ(?:-[A-Z][A-Z0-9]*)*-\d+(?:\.\d+)*)\b"
)

# What may stand between the start of a line and a requirement ID for the ID
# to count as a *definition*: heading markers, a list bullet, bold markers and
# a label such as "Requirement:". Anything else means the ID is a reference
# inside running text.
DEFINITION_PREFIX_PATTERN = (
    r"\s{0,3}(?:#{1,6}\s*)?(?:[-*+]\s+)?(?:\*\*|__)?\s*"
    r"(?:(?:Requirement|Anforderung|Req)\b[\s:.\-]*)?(?:\*\*|__)?\s*"
)
_DEFINITION_PREFIX = re.compile(DEFINITION_PREFIX_PATTERN, re.IGNORECASE)

_MODAL_WORDS = (
    "shall",
    "must",
    "should",
    "muss",
    "müssen",
    "soll",
    "sollen",
    "darf",
    "dürfen",
)


@dataclass
class IngestResult:
    document: SourceDocument
    requirements: list[Requirement] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    segments: tuple[TextSegment, ...] = ()

    @property
    def pages(self) -> list[str]:
        return [segment.text for segment in self.segments]

    def with_external_id(self, external_id: str) -> list[Requirement]:
        return [r for r in self.requirements if r.external_id == external_id]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def internal_requirement_id(document: SourceDocument, char_start: int) -> str:
    """Stable internal ID. Never exported as an ID of the source document."""
    return f"REQ-{document.content_hash[:8]}-{char_start:06d}"


def _segments_for_range(
    segments: Sequence[TextSegment], char_start: int, char_end: int
) -> list[TextSegment]:
    return [
        seg
        for seg in segments
        if seg.char_start < char_end and seg.char_end > char_start
    ]


def _spans_for_range(
    document: SourceDocument,
    segments: Sequence[TextSegment],
    char_start: int,
    char_end: int,
) -> tuple[SourceSpan, ...]:
    """One span per page/section the requirement touches.

    A requirement that crosses a page boundary therefore keeps both pages.
    """
    touched = _segments_for_range(segments, char_start, char_end)
    if not touched:
        return (
            SourceSpan(
                document_id=document.document_id,
                char_start=char_start,
                char_end=char_end,
                quote=document.slice(char_start, char_end),
            ),
        )
    spans: list[SourceSpan] = []
    for seg in touched:
        start = max(char_start, seg.char_start)
        end = min(char_end, seg.char_end)
        if end <= start:
            continue
        spans.append(
            SourceSpan(
                document_id=document.document_id,
                char_start=start,
                char_end=end,
                quote=document.slice(start, end),
                page=seg.page,
                section=seg.section,
            )
        )
    return tuple(spans)


def _is_definition(text: str, start: int) -> bool:
    """True when the ID at ``start`` opens a requirement rather than citing one.

    A definition stands at the beginning of its line, optionally behind
    heading markers, a bullet, bold markers or a label such as "Requirement:".
    An ID in the middle of a sentence is a reference to another requirement.
    """
    line_start = text.rfind("\n", 0, start) + 1
    return _DEFINITION_PREFIX.fullmatch(text, line_start, start) is not None


def _needs_clarification(text: str, id_pattern: str) -> bool:
    """Vague wording without any measurable criterion.

    Digits that belong to a requirement ID do not count as a criterion.
    """
    body = re.sub(id_pattern, " ", text)
    vague = re.search(
        r"\b(safe value|appropriate|reasonable|fast|quickly|sufficient|geeignet|"
        r"angemessen|schnell|ausreichend)\b",
        body,
        re.IGNORECASE,
    )
    measurable = re.search(r"\d", body)
    return bool(vague) and not bool(measurable)


def extract_requirements(
    imported: ImportedDocument,
    *,
    id_pattern: str = DEFAULT_REQUIREMENT_ID_PATTERN,
    capture_unidentified: bool = True,
) -> IngestResult:
    """Capture requirements from an imported document.

    Identical external IDs in different documents - and even inside one
    document - are never merged: each occurrence keeps its own internal ID.
    """
    document = imported.document
    segments = imported.segments
    result = IngestResult(
        document=document, segments=segments, warnings=list(imported.warnings)
    )
    text = document.text

    # Section headings bound a requirement; page breaks deliberately do not,
    # so a requirement crossing a page boundary stays one requirement.
    section_starts = sorted(
        seg.char_start
        for seg in segments
        if seg.section is not None and seg.char_start > 0
    )

    all_matches = list(re.finditer(id_pattern, text))
    matches = [m for m in all_matches if _is_definition(text, m.start())]
    if not matches and all_matches:
        result.issues.append(
            ValidationIssue(
                "C_NO_REQUIREMENT_DEFINITION",
                Severity.CLARIFICATION,
                document.filename,
                f"{len(all_matches)} requirement ID(s) occur only inside running "
                "text, so none of them defines a requirement here. Check the "
                "document structure before generating from it.",
            )
        )
    anchored: list[tuple[int, int, str]] = []
    for i, match in enumerate(matches):
        start = match.start()
        limits = [len(text)]
        if i + 1 < len(matches):
            next_start = matches[i + 1].start()
            limits.append(next_start)
            # A requirement never reaches into the page on which the next
            # requirement starts, so page headers of that page are not
            # attributed to it as a source.
            next_segment = next(
                (
                    seg
                    for seg in segments
                    if seg.char_start <= next_start < seg.char_end
                ),
                None,
            )
            if (
                next_segment is not None
                and next_segment.char_start > start
                and next_segment.page is not None
            ):
                limits.append(next_segment.char_start)
        limits.extend(pos for pos in section_starts if pos > start)
        end = min(limits)
        anchored.append((start, end, match.group(1)))

    seen_external: dict[str, int] = {}
    for start, end, external_id in anchored:
        body = text[start:end].rstrip()
        end = start + len(body)
        references = tuple(
            dict.fromkeys(
                found
                for found in re.findall(id_pattern, body)
                if found != external_id
            )
        )
        requirement = Requirement(
            requirement_id=internal_requirement_id(document, start),
            external_id=external_id,
            text=body,
            spans=_spans_for_range(document, segments, start, end),
            clarification_status=(
                ClarificationStatus.NEEDS_CLARIFICATION
                if _needs_clarification(body, id_pattern)
                else ClarificationStatus.CLEAR
            ),
            references=references,
        )
        result.requirements.append(requirement)
        seen_external[external_id] = seen_external.get(external_id, 0) + 1

    for external_id, count in seen_external.items():
        if count > 1:
            result.issues.append(
                ValidationIssue(
                    "C_DUPLICATE_EXTERNAL_ID",
                    Severity.CLARIFICATION,
                    f"{document.filename}:{external_id}",
                    f"external ID {external_id!r} occurs {count} times in this "
                    "document; the occurrences are kept separate and must be "
                    "disambiguated manually",
                )
            )

    if capture_unidentified:
        result.requirements.extend(
            _capture_unidentified(document, segments, anchored, result)
        )

    result.requirements.sort(key=lambda r: r.spans[0].char_start if r.spans else 0)

    for segment in segments:
        # Only page-based documents are worth warning about: an empty section
        # in Markdown is just a heading, while an empty PDF page may hide
        # content in an image.
        if segment.is_empty and segment.page is not None:
            result.warnings.append(
                f"{document.filename}: page {segment.page} contains no extractable "
                "text (empty or image-only); OCR is out of scope"
            )
    if not result.requirements:
        result.issues.append(
            ValidationIssue(
                "E_NO_REQUIREMENTS",
                Severity.ERROR,
                document.filename,
                "no requirement could be captured from this document",
            )
        )
    return result


def _capture_unidentified(
    document: SourceDocument,
    segments: Sequence[TextSegment],
    anchored: Sequence[tuple[int, int, str]],
    result: IngestResult,
) -> list[Requirement]:
    """Requirement-like paragraphs outside any ID-anchored range."""
    text = document.text
    covered = [(start, end) for start, end, _ in anchored]

    def is_covered(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in covered)

    found: list[Requirement] = []
    for match in re.finditer(r"[^\n][^\n]*(?:\n[^\n]+)*", text):
        start, end = match.start(), match.end()
        if is_covered(start, end):
            continue
        body = match.group(0).strip()
        if body.startswith("#") or len(body) < 25:
            continue
        lowered = body.lower()
        if not any(word in lowered for word in _MODAL_WORDS):
            continue
        offset = match.group(0).index(body.split("\n", 1)[0])
        abs_start = start + offset
        abs_end = abs_start + len(body)
        found.append(
            Requirement(
                requirement_id=internal_requirement_id(document, abs_start),
                external_id=None,
                text=body,
                spans=_spans_for_range(document, segments, abs_start, abs_end),
                clarification_status=ClarificationStatus.NEEDS_CLARIFICATION,
                notes=(
                    "no external requirement ID in the source; boundary derived "
                    "from paragraph structure and needs review",
                ),
            )
        )
    if found:
        result.issues.append(
            ValidationIssue(
                "C_REQUIREMENT_WITHOUT_ID",
                Severity.CLARIFICATION,
                document.filename,
                f"{len(found)} requirement-like paragraph(s) without an external "
                "ID received stable internal IDs and need a boundary review",
            )
        )
    return found


@dataclass
class ReferenceReport:
    """How the captured requirements refer to each other."""

    #: external ID -> the requirements that define it (more than one is a defect)
    definitions: dict[str, list[Requirement]] = field(default_factory=dict)
    #: (source requirement, referenced external ID) that nothing defines
    unresolved: list[tuple[Requirement, str]] = field(default_factory=list)
    #: referenced external ID -> requirements citing it
    citations: dict[str, list[Requirement]] = field(default_factory=dict)

    @property
    def duplicate_definitions(self) -> dict[str, list[Requirement]]:
        return {k: v for k, v in self.definitions.items() if len(v) > 1}

    def issues(self) -> list[ValidationIssue]:
        found: list[ValidationIssue] = []
        for external_id, requirements in self.duplicate_definitions.items():
            where = ", ".join(
                sorted({span.document_id for r in requirements for span in r.spans})
            )
            found.append(
                ValidationIssue(
                    "C_DUPLICATE_DEFINITION",
                    Severity.CLARIFICATION,
                    external_id,
                    f"{external_id} is defined {len(requirements)} times ({where}); "
                    "the occurrences are kept separate and must be disambiguated",
                )
            )
        for requirement, target in self.unresolved:
            found.append(
                ValidationIssue(
                    "C_UNRESOLVED_REFERENCE",
                    Severity.CLARIFICATION,
                    requirement.display_id(),
                    f"refers to {target}, which is not part of the imported set",
                )
            )
        return found

    def resolve(self, external_id: str) -> Requirement | None:
        """The single requirement defining this ID, if it is unambiguous."""
        candidates = self.definitions.get(external_id, [])
        return candidates[0] if len(candidates) == 1 else None


def reference_report(requirements: Sequence[Requirement]) -> ReferenceReport:
    """Resolve the references between the captured requirements.

    This is the deterministic first stage of retrieval: a requirement that
    says "see BMS-REQ-THM-0403" is linked to the requirement defining it,
    without any search.
    """
    report = ReferenceReport()
    for requirement in requirements:
        if requirement.external_id:
            report.definitions.setdefault(requirement.external_id, []).append(
                requirement
            )
    for requirement in requirements:
        for target in requirement.references:
            report.citations.setdefault(target, []).append(requirement)
            if target not in report.definitions:
                report.unresolved.append((requirement, target))
    return report


def import_documents(
    paths: Iterable[Path], importers: Sequence[DocumentImportPort]
) -> list[IngestResult]:
    """Import several documents; an unsupported format is an explicit error."""
    results: list[IngestResult] = []
    for path in paths:
        importer = next((imp for imp in importers if imp.supports(Path(path))), None)
        if importer is None:
            raise ValueError(f"no importer for {path} (supported: pdf, txt, markdown)")
        results.append(extract_requirements(importer.load(Path(path))))
    return results
