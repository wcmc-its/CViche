"""Regression tests for #665 items 1-4: heuristic gaps in stage6/parsing/text.py.

Three of the four items named in scope are fixed here; item 1 was
investigated and found not safely fixable within this file -- see below.

  1. `_extract_last_name_from_uid`'s "appended initials" suffix heuristic
     (`suffix.islower() or suffix.isupper()`) fires on the tail of almost
     any Title Case surname, matching #665's own claim that no case-based
     signal distinguishes "appended initials" from "the end of an ordinary
     word". A corpus render-gate probe over all 66 CVs proved that removing
     the strip is a real regression, not a fix: uid "2003_Albrechtjs_Cv"
     belongs to a person whose real surname genuinely is "Albrecht" --
     confirmed by "Albrecht JS"/"Albrecht J" as the cited author in every
     one of that CV's own bibliography entries -- and
     `bibliography.py:176` uses this function's return value to bold the
     CV owner's name among citation authors. Removing the strip changes
     the extracted name to "Albrechtjs", which matches nothing, and
     silently drops bold-highlighting from 10 real citations, with no
     corresponding in-corpus case where the strip itself was the bug. No
     signal available inside this function distinguishes the two cases, so
     the code is left unchanged here; see the PR body for the corpus
     evidence and the recharacterisation of this item as unresolved.
  2. `_extract_name_from_uid` / `_extract_last_name_from_uid` used
     `uid.replace('CV_', '')`, which removes every occurrence of the
     substring, not just a leading prefix. Fixed with `removeprefix`.
  3. `_is_structural_label`'s all-caps check dropped any all-caps,
     4+ character, digit-free string unconditionally -- including a
     legitimate all-caps name, "USA", or an org name written in caps. Fixed
     by requiring the text to also echo one of the entry's own hierarchy
     labels before treating it as a structural header.
  4. `_is_table_header_entry` matched header keywords by substring
     (`kw.lower() in text_lower`), so "date" matched inside "candidate" and
     "organization" matched inside "Organization of Medical Education".
     Fixed with word-boundary regex matching, in both the keyword-count path
     and the tab/pipe-separated-columns path.

Items 5-7 (the duplicated date regexes and the positional-reconstruction
zips in `_parse_multi_membership_entry` and `_parse_flattened_committee_lines`)
are out of scope for this PR and untouched here.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_parsing_text.py -p no:cacheprovider

Self-contained: pure string/dict inputs, no DB, no network, no LLM, no PII.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

from unified_pipeline.stage6.parsing.text import (  # noqa: E402
    _extract_last_name_from_uid,
    _extract_name_from_uid,
    _is_structural_label,
    _is_table_header_entry,
)


# --- item 1: investigated, left unchanged (see module docstring) -----------

def test_last_name_still_strips_the_corpus_real_appended_initials_case():
    # Locks in the actual corpus behaviour this function must keep: the
    # "2003_Albrechtjs_Cv" pattern still resolves to "Albrecht", matching
    # the real cited author name in that CV's own bibliography. A change
    # that "fixes" this by no longer stripping would silently drop that
    # document's bold-highlighting again -- this is the regression the PR
    # body's corpus evidence documents.
    assert _extract_last_name_from_uid("2003_Albrechtjs_Cv") == "Albrecht"


# --- item 2: UID prefix strip must be a prefix strip, not a global replace --

def test_uid_prefix_strip_does_not_eat_mid_string_occurrences():
    # "AbCV_Doe" does not START with "CV_" -- the "CV_" sits mid-string.
    # `.replace('CV_', '')` would delete it anyway, merging "Ab" and "Doe"
    # into a single "AbDoe" token (the underscore that separated them is
    # consumed along with the deleted "CV_"), losing the real last name.
    # `removeprefix` leaves a non-leading occurrence alone.
    uid = "AbCV_Doe"
    assert _extract_last_name_from_uid(uid) == "Doe"
    assert _extract_name_from_uid(uid) == "Abcv Doe"


def test_uid_prefix_strip_still_removes_a_real_leading_prefix():
    assert _extract_last_name_from_uid("CV_2015_Wende") == "Wende"
    assert _extract_name_from_uid("CV_2015_Wende") == "Wende"


# --- item 3: all-caps structural-label check needs corroboration -----------

def test_all_caps_matching_hierarchy_is_still_a_structural_label():
    entry = {
        "text": "CLINICAL PRACTICE ACTIVITIES",
        "hierarchy": ["Clinical Practice Activities"],
    }
    assert _is_structural_label(entry) is True


def test_all_caps_name_with_no_hierarchy_echo_is_not_a_structural_label():
    # A person's name written in all caps, unrelated to the entry's own
    # section hierarchy, is real content -- not a header to drop.
    entry = {
        "text": "JOHN SMITH",
        "hierarchy": ["Clinical Practice Activities"],
    }
    assert _is_structural_label(entry) is False


def test_all_caps_org_name_with_empty_hierarchy_is_not_a_structural_label():
    entry = {"text": "MAYO CLINIC FOUNDATION", "hierarchy": []}
    assert _is_structural_label(entry) is False


def test_exact_hierarchy_match_is_still_a_structural_label():
    # Unaffected by the all-caps corroboration change -- exact match is its
    # own, separate rule.
    entry = {"text": "Direct Teaching", "hierarchy": ["Direct Teaching"]}
    assert _is_structural_label(entry) is True


# --- item 4: header-keyword matching must be token-boundary, not substring -

def test_keyword_count_path_ignores_substring_matches():
    # "did" only appears as a substring of "candidate", never as a whole
    # word -- a real word-boundary match count should not count it, so this
    # should NOT clear the default threshold of 2 even though "date" (a
    # genuine whole word here) does count.
    text = "Date: 2020, candidate review pending"
    assert _is_table_header_entry(text, ["date", "did"]) is False


def test_keyword_count_path_still_fires_on_real_whole_word_keywords():
    text = "Date of award: pending. Organization: TBD."
    assert _is_table_header_entry(text, ["date", "organization"]) is True


def test_pipe_separated_columns_ignore_substring_keyword_matches():
    # Neither "candidate" nor "update" contains the keyword "date" as a
    # whole word -- both only contain it as a substring. The old substring
    # check misclassified this two-column line as a header table row.
    text = "Candidate|Update"
    assert _is_table_header_entry(text, ["date"]) is False


def test_pipe_separated_columns_still_match_real_header_words():
    text = "Award|Date"
    assert _is_table_header_entry(text, ["award", "date"]) is True
