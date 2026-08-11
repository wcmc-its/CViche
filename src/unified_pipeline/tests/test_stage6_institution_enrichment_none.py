"""Regression guard for issue #559: institution_enrichment present as None.

`_get_cleaned_institution_name` used `entry.get('institution_enrichment', {})`,
which only supplies the `{}` default when the key is absent. A stage that
writes the key with an explicit `None` (enrichment attempted but produced
nothing) reaches `.get(...)` on `None` and raises. Switching to
`entry.get('institution_enrichment') or {}` treats an absent key and a
present-but-None value the same way, since both are falsy.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_institution_enrichment_none.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx needed.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import _get_cleaned_institution_name


def test_missing_key_returns_none():
    assert _get_cleaned_institution_name({}) is None


def test_populated_dict_returns_cleaned_or_official_name():
    assert _get_cleaned_institution_name(
        {"institution_enrichment": {"cleaned_name": "Duke Medical Center"}}
    ) == "Duke Medical Center"
    # empty cleaned_name falls back to official_name
    assert _get_cleaned_institution_name(
        {"institution_enrichment": {"cleaned_name": "",
                                     "official_name": "Duke Regional Hospital"}}
    ) == "Duke Regional Hospital"


def test_none_value_no_longer_raises():
    # key present, value None -- must fall through to the same result as an
    # absent key rather than raise AttributeError on None.get(...)
    assert _get_cleaned_institution_name({"institution_enrichment": None}) is None


if __name__ == "__main__":
    test_missing_key_returns_none()
    test_populated_dict_returns_cleaned_or_official_name()
    test_none_value_no_longer_raises()
    print("OK")
