"""Block building over pages/sections.

Invariant: every input page ends up in at least one block. A trailing block is
kept even when it is shorter than ``combine``; a block that would add no new
page is not emitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

PAGE_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class Block:
    index: int
    pages: tuple[int, ...]  # 1-based page/segment numbers
    text: str

    @property
    def page_from(self) -> int:
        return self.pages[0]

    @property
    def page_to(self) -> int:
        return self.pages[-1]

    def label(self) -> str:
        if self.page_from == self.page_to:
            return f"page {self.page_from}"
        return f"pages {self.page_from}-{self.page_to}"


class CoverageError(RuntimeError):
    """Raised when block building would drop input pages."""


def build_blocks(
    pages: Sequence[str], combine: int, *, overlap: int = 0
) -> list[Block]:
    if combine < 1:
        raise ValueError("combine must be >= 1")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= combine:
        raise ValueError("overlap must be smaller than combine")

    total = len(pages)
    if total == 0:
        return []

    step = combine - overlap
    blocks: list[Block] = []
    covered_end = 0  # exclusive index of the furthest covered page
    start = 0

    while start < total:
        end = min(start + combine, total)
        if end > covered_end:
            # Emit only blocks that extend coverage; never skip a short tail.
            blocks.append(
                Block(
                    index=len(blocks) + 1,
                    pages=tuple(range(start + 1, end + 1)),
                    text=PAGE_SEPARATOR.join(pages[start:end]),
                )
            )
            covered_end = end
        if end >= total:
            break
        start += step

    verify_coverage(blocks, total)
    return blocks


def verify_coverage(blocks: Sequence[Block], page_count: int) -> None:
    covered = {page for block in blocks for page in block.pages}
    missing = sorted(set(range(1, page_count + 1)) - covered)
    if missing:
        raise CoverageError(f"pages not covered by any block: {missing}")
