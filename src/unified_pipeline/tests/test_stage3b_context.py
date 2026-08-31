"""Tests for hierarchy -> taxonomy lookup in stage3b/context.py (PR #644 review).

`build_mapping_index()` stores taxonomy tree nodes under both a short title
key (last node wins when a title recurs under different parents) and a
full-path tuple key (unambiguous, since two nodes cannot share a path).
`get_taxonomy_context()` must prefer the full-path key so a duplicate short
title under an unrelated parent branch cannot silently misroute a lookup to
the wrong node -- see PR #644 review comment 3821298149.

`TaxonomyContext.get_all_suggested_codes()` must preserve subsection-first
insertion order while deduplicating, because classify.py's
`_classify_one_batch` uses ``all_suggested_codes[0]`` as the fallback
classification for an all-empty batch -- see review comment 3821313539.

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage3b.context import (
    TaxonomyContext,
    build_mapping_index,
    get_taxonomy_context,
)

import pytest


def _option(code, confidence=0.8):
    return {"code": code, "confidence": confidence}


def test_full_path_preferred_over_duplicate_short_title():
    """"Books" appears under two unrelated parents. The hierarchy path that
    names one parent must resolve to that parent's "Books" node, not
    whichever "Books" node build_mapping_index indexed last under the
    ambiguous short-title key.
    """
    mappings = [
        {
            "title": "RESEARCH AND SCHOLARSHIP",
            "children": [
                {
                    "title": "Publications",
                    "children": [
                        {"title": "Books", "taxonomy_options": [_option("R3")]},
                    ],
                },
            ],
        },
        {
            "title": "TEACHING",
            "children": [
                {
                    "title": "Materials",
                    "children": [
                        {"title": "Books", "taxonomy_options": [_option("T3")]},
                    ],
                },
            ],
        },
    ]
    index = build_mapping_index(mappings)

    research_context = get_taxonomy_context(
        ["RESEARCH AND SCHOLARSHIP", "Publications", "Books"], index)
    teaching_context = get_taxonomy_context(
        ["TEACHING", "Materials", "Books"], index)

    assert research_context.subsection["taxonomy_options"][0]["code"] == "R3"
    assert teaching_context.subsection["taxonomy_options"][0]["code"] == "T3"


def test_short_title_fallback_used_when_no_full_path_match():
    """If the caller's hierarchy path doesn't exactly match the taxonomy
    tree's path (e.g. stage 3a phrased a header slightly differently),
    tuple(hierarchy[:3]) is absent from the index and get_taxonomy_context
    must still fall back to the bare-title key rather than returning None.
    """
    mappings = [
        {
            "title": "TEACHING",
            "children": [
                {
                    "title": "Materials",
                    "children": [
                        {"title": "Books", "taxonomy_options": [_option("T3")]},
                    ],
                },
            ],
        },
    ]
    index = build_mapping_index(mappings)

    context = get_taxonomy_context(["TEACHING", "Course Materials", "Books"], index)
    assert context.subsection["taxonomy_options"][0]["code"] == "T3"


def test_get_all_suggested_codes_preserves_order_and_dedupes():
    """Subsection codes must sort first (most specific), then section, then
    meta_section, and a code repeated across levels must keep its first
    position rather than whatever order `set()` iteration would produce.
    """
    context = TaxonomyContext(
        subsection={"taxonomy_options": [_option("R3"), _option("R1")]},
        section={"taxonomy_options": [_option("R1"), _option("R2")]},
        meta_section={"taxonomy_options": [_option("R4")]},
    )
    assert context.get_all_suggested_codes() == ["R3", "R1", "R2", "R4"]


def test_get_all_suggested_codes_empty_context_returns_empty_list():
    assert TaxonomyContext().get_all_suggested_codes() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
