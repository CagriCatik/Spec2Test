"""Block building: no page may be lost, no block may be redundant."""
from __future__ import annotations

import itertools

import pytest

from spec2test.application.chunking import CoverageError, build_blocks, verify_coverage


@pytest.mark.parametrize(
    "page_count,combine,overlap",
    [
        (count, combine, overlap)
        for count, combine in itertools.product(range(1, 13), range(1, 6))
        for overlap in range(0, combine)
    ],
)
def test_every_page_is_covered_for_every_configuration(page_count, combine, overlap):
    pages = [f"page-{i}" for i in range(1, page_count + 1)]
    blocks = build_blocks(pages, combine, overlap=overlap)
    covered = {page for block in blocks for page in block.pages}
    assert covered == set(range(1, page_count + 1))


@pytest.mark.parametrize(
    "page_count,combine,overlap",
    [(5, 3, 0), (7, 4, 2), (10, 3, 0), (9, 4, 1), (12, 5, 2)],
)
def test_no_block_is_fully_covered_by_its_predecessors(page_count, combine, overlap):
    pages = [f"page-{i}" for i in range(1, page_count + 1)]
    blocks = build_blocks(pages, combine, overlap=overlap)
    seen: set[int] = set()
    for block in blocks:
        assert set(block.pages) - seen, f"block {block.index} adds no new page"
        seen.update(block.pages)


def test_the_trailing_block_is_kept_even_when_it_is_short():
    blocks = build_blocks([f"p{i}" for i in range(1, 6)], 3)
    assert [block.pages for block in blocks] == [(1, 2, 3), (4, 5)]
    assert blocks[-1].label() == "pages 4-5"


def test_block_text_keeps_the_page_content():
    blocks = build_blocks(["alpha", "beta", "gamma"], 2)
    assert blocks[0].text == "alpha\n\nbeta"
    assert blocks[1].text == "gamma"


def test_empty_input_produces_no_block():
    assert build_blocks([], 3) == []


@pytest.mark.parametrize("combine,overlap", [(0, 0), (-1, 0), (2, 2), (2, 3), (3, -1)])
def test_invalid_parameters_are_rejected(combine, overlap):
    with pytest.raises(ValueError):
        build_blocks(["a", "b"], combine, overlap=overlap)


def test_coverage_check_detects_a_gap():
    blocks = build_blocks(["a", "b", "c"], 1)
    with pytest.raises(CoverageError):
        verify_coverage(blocks[:-1], 3)


def test_one_page_fits_a_larger_block_size():
    blocks = build_blocks(["only"], 5)
    assert len(blocks) == 1 and blocks[0].pages == (1,)
