"""Project guidance is optional, and it belongs to a project.

The shipped files are named Beginner / Advanced / Expert, which reads like
a skill level. It is not: ``Prompt_Expert_Test_Architect.txt`` opens with

    # Project guidance: Rear-Axle-Steering (RAS), expert level

and goes on about counter-steering and rear-wheel angles. Loaded into a
battery-management run — which the shipped ``config.yml`` did by default —
it tells the model to name observation channels that the requirements
under test have never heard of.

This module does not forbid that. A tester may deliberately reuse guidance
across projects, and guessing wrong would be worse than saying nothing. It
reports: when the guidance names a system the sources never mention, that
is said out loud, once, with both names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

#: "# Project guidance: Rear-Axle-Steering (RAS), expert level"
_HEADING = re.compile(r"^#+\s*(?:project\s+guidance:\s*)?(.+?)\s*$", re.IGNORECASE)
#: a domain name worth comparing: two or more letters, not a common word
_TERM = re.compile(r"\b[A-Z][A-Za-z]*(?:[- ][A-Z][A-Za-z]*)+\b|\b[A-Z]{2,6}\b")
_IGNORE = {
    "HIL", "ECU", "CAN", "JSON", "ID", "IDS", "API", "PDF", "UTC", "ISO",
    "TODO", "NOTE", "OK", "AND", "OR", "THE", "A", "I", "DO", "IT",
}


@dataclass(frozen=True)
class GuidanceCheck:
    """What the guidance claims to be about, and whether that matches."""

    subject: str | None
    unknown_terms: tuple[str, ...]

    @property
    def mismatched(self) -> bool:
        return bool(self.unknown_terms)

    def message(self) -> str | None:
        if not self.mismatched:
            return None
        named = ", ".join(self.unknown_terms[:3])
        about = f" The file calls itself {self.subject!r}." if self.subject else ""
        return (
            f"The project guidance talks about {named}, which does not "
            f"appear anywhere in the loaded sources.{about} It will still be "
            "sent; check that it belongs to this project."
        )


def _terms(text: str) -> list[str]:
    found: list[str] = []
    for match in _TERM.finditer(text):
        term = match.group(0).strip()
        bare = term.replace("-", " ").upper()
        if bare in _IGNORE or len(term) < 3:
            continue
        found.append(term)
    return found


def subject_of(guidance: str) -> str | None:
    """What the first heading says the guidance is about."""
    for line in guidance.splitlines():
        if line.strip().startswith("#"):
            match = _HEADING.match(line.strip())
            if match:
                return match.group(1)
    return None


def check_guidance(guidance: str, sources: Sequence[str]) -> GuidanceCheck:
    """Does the guidance name a system the sources never mention?

    Only the opening of the file is read: the heading and the role section
    are where a project names itself. Scanning the whole text would flag
    every passing example.
    """
    if not guidance or not guidance.strip():
        return GuidanceCheck(subject=None, unknown_terms=())
    head = "\n".join(guidance.splitlines()[:12])
    corpus = "\n".join(sources).lower()
    unknown: list[str] = []
    for term in _terms(head):
        needle = term.lower()
        if needle in corpus:
            continue
        # a hyphenated name may be spelled with spaces in the sources
        if needle.replace("-", " ") in corpus:
            continue
        if needle not in unknown:
            unknown.append(term)
    return GuidanceCheck(subject=subject_of(guidance), unknown_terms=tuple(unknown))


__all__ = ["GuidanceCheck", "check_guidance", "subject_of"]
