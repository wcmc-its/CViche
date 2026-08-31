"""Tests for stage3b's taxonomy renderer (#522).

`build_taxonomy_codes_for_prompt` (src/unified_pipeline/stage3b/prompt.py)
renders `core/taxonomy_v7.json` into the classification prompt's
`{taxonomy_ref}` slot. That JSON is a hand-maintained, repo-committed file --
not LLM output -- but it sits on the classification path (called once per
batch), so a single malformed entry (a typo dropping "code" or "label", an
empty code, a non-string code) must degrade to "skip that entry" rather than
raise KeyError/IndexError and abort the whole classification batch. These
tests pin that behaviour, plus the family-vs-exact-code filtering contract
(`relevant_codes_or_families` matches on either).

Purely cosmetic permutations (case-only differences, ordering) are not
covered here -- they don't exercise a correctness gap.
"""
import logging
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from unified_pipeline.stage3b.prompt import build_taxonomy_codes_for_prompt


def test_missing_code_key_is_skipped_not_a_crash(caplog):
    taxonomy = {"codes": [
        {"label": "Original Research", "purpose": "no code field"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    with caplog.at_level(logging.WARNING):
        rendered = build_taxonomy_codes_for_prompt(taxonomy)
    assert "H: Honors and Awards" in rendered
    assert "no code field" not in rendered
    assert "malformed taxonomy entry" in caplog.text


def test_missing_label_key_is_skipped_not_a_crash(caplog):
    taxonomy = {"codes": [
        {"code": "S1", "purpose": "no label field"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    with caplog.at_level(logging.WARNING):
        rendered = build_taxonomy_codes_for_prompt(taxonomy)
    assert "H: Honors and Awards" in rendered
    assert "S1" not in rendered
    assert "malformed taxonomy entry" in caplog.text


def test_empty_string_code_is_skipped_not_an_indexerror():
    taxonomy = {"codes": [
        {"code": "", "label": "Blank code"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    rendered = build_taxonomy_codes_for_prompt(taxonomy)
    assert "H: Honors and Awards" in rendered
    assert "Blank code" not in rendered


def test_non_string_code_is_skipped_not_a_typeerror():
    taxonomy = {"codes": [
        {"code": 123, "label": "Numeric code"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    rendered = build_taxonomy_codes_for_prompt(taxonomy)
    assert "H: Honors and Awards" in rendered
    assert "Numeric code" not in rendered


def test_relevant_codes_or_families_matches_family_letter():
    taxonomy = {"codes": [
        {"code": "S1", "label": "Original Research"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    rendered = build_taxonomy_codes_for_prompt(taxonomy, relevant_codes_or_families=["S"])
    assert "S1: Original Research" in rendered
    assert "H: Honors and Awards" not in rendered


def test_relevant_codes_or_families_matches_exact_code_outside_its_family():
    # A full code (e.g. "M2A") should be included even when its family
    # letter ("M") is not itself in the filter set -- the renderer's
    # documented dual contract: family letters AND exact codes both match.
    taxonomy = {"codes": [
        {"code": "M2A", "label": "Active Grant"},
        {"code": "M1", "label": "Research Interests"},
        {"code": "H", "label": "Honors and Awards"},
    ]}
    rendered = build_taxonomy_codes_for_prompt(taxonomy, relevant_codes_or_families=["M2A"])
    assert "M2A: Active Grant" in rendered
    assert "M1: Research Interests" not in rendered
    assert "H: Honors and Awards" not in rendered


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
