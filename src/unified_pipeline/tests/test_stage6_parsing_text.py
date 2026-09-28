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
     CV owner's name among citation authors. Removing the strip changes the
     extracted name to "Albrechtjs", and a direct python-docx run-level bold
     diff (ref arm vs. a probe render with the strip disabled) confirms it
     silently drops bold-highlighting from 12 of the 98 paragraphs in that
     document's bibliography citing "Albrecht" -- the 12 formatted as bare
     "Albrecht J"/"Albrecht JS", which the stripped form no longer matches;
     the other 86 are unaffected, so "matches nothing" (this file's and this
     test's own earlier wording) overstated the mechanism. 12 is also what
     commit 6935094's body and PR_BODY.md already said; this file previously
     said 10, a round-1-review-caught inconsistency, now corrected against
     the re-run measurement rather than either prior number. No signal
     available inside this function distinguishes the two cases, so the
     code is left unchanged here; see the PR body for the corpus evidence
     and the recharacterisation of this item as unresolved.
  2. `_extract_name_from_uid` / `_extract_last_name_from_uid` used
     `uid.replace('CV_', '')`, which removes every occurrence of the
     substring, not just a leading prefix. Fixed with `removeprefix`.
  3. `_is_structural_label`'s all-caps check dropped any all-caps,
     4+ character, digit-free string unconditionally -- including a
     legitimate all-caps name, "USA", or an org name written in caps.
     Round 1 fixed this by requiring the text to echo one of the entry's
     own hierarchy labels before treating it as a structural header, but a
     corpus probe (round-1 review) showed that alone is a net regression in
     the sections that actually call this function: 13 of 66 corpus CVs
     carry a "CLINICAL PRACTICE ACTIVITIES" entry classified to L1 (clinical
     practice) whose own `hierarchy` is a *different*, mismatched section
     ("PROFESSIONAL ORGANIZATIONS AND SOCIETIES" or "ADMINISTRATIVE AND
     ACADEMIC LEADERSHIP" -- a stage 2/3 misclassification, flagged on the
     entry itself as `hierarchy_mismatch_flag`), so hierarchy-echo alone
     never catches it -- 13 stray headers un-caught, 0 legitimate all-caps
     content in those same sections rescued, in the round-1 corpus probe.
     Fixed by adding a second, independent corroborating signal: an entry
     whose `extracted_fields` were all null (stage 4 attempted extraction
     and found nothing) is also treated as structural, since a genuine
     all-caps content entry always has at least one populated field or is
     missing `extracted_fields` altogether (an untested/synthetic caller),
     and every one of the 13 corpus cases has an all-null `extracted_fields`
     dict with zero in-corpus counterexample among the same sections' other
     all-caps entries.
  4. `_is_table_header_entry` matched header keywords by substring
     (`kw.lower() in text_lower`), so "date" matched inside "candidate" and
     "organization" matched inside "Organization of Medical Education".
     Fixed with word-boundary regex matching, in both the keyword-count path
     and the tab/pipe-separated-columns path. Round 1 review caught that a
     bare `\\bkeyword\\b` also stopped matching a plural header word
     ("Dates" for keyword "date"), inconsistent with `header_patterns`'
     existing `dates?` handling a few lines below; both word-boundary sites
     now allow an optional trailing "s".

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


def test_all_caps_header_with_mismatched_hierarchy_and_no_fields_is_a_structural_label():
    # Round-1 regression guard: the corpus-real shape from all 13 flagged
    # cases (all L1, all "CLINICAL PRACTICE ACTIVITIES", all with a
    # hierarchy naming a *different* section and an all-null
    # `extracted_fields`). Hierarchy-echo alone misses this -- the second
    # corroborating signal (extraction found nothing) must catch it.
    entry = {
        "text": "CLINICAL PRACTICE ACTIVITIES",
        "hierarchy": ["PROFESSIONAL ORGANIZATIONS AND SOCIETIES"],
        "taxonomy_code": "L1",
        "extracted_fields": {
            "clinical_role": None,
            "institution": None,
            "start_date": None,
            "end_date": None,
        },
    }
    assert _is_structural_label(entry) is True


def test_all_caps_content_with_a_populated_field_is_not_a_structural_label():
    # The "no extracted fields" signal must not fire on real content just
    # because its hierarchy happens not to echo the text -- an entry stage 4
    # actually extracted something from stays protected.
    entry = {
        "text": "MAYO CLINIC FOUNDATION",
        "hierarchy": ["PROFESSIONAL ORGANIZATIONS AND SOCIETIES"],
        "extracted_fields": {
            "clinical_role": "Member",
            "institution": None,
            "start_date": None,
            "end_date": None,
        },
    }
    assert _is_structural_label(entry) is False


def test_all_caps_content_with_an_empty_extracted_fields_dict_is_not_a_structural_label():
    # An empty dict (no keys at all, distinct from "keys present but all
    # None") means extraction wasn't attempted for this shape of entry, not
    # "extraction ran and found nothing" -- `not any({}.values())` would be
    # True, so this must be excluded explicitly (the truthiness check on
    # `fields` itself before the value check).
    entry = {"text": "MAYO CLINIC FOUNDATION", "hierarchy": [], "extracted_fields": {}}
    assert _is_structural_label(entry) is False


# --- #757: blank raw text with stage-5c formatted_text is content ----------

def test_blank_text_with_formatted_text_is_not_a_structural_label():
    entry = {"text": "  \n ", "extracted_fields": {"formatted_text": "Grand rounds"}}
    assert _is_structural_label(entry) is False


def test_blank_text_without_formatted_text_is_still_a_structural_label():
    assert _is_structural_label({"text": ""}) is True
    assert _is_structural_label({"text": "  ", "extracted_fields": {}}) is True
    assert _is_structural_label(
        {"text": "", "extracted_fields": {"formatted_text": "   "}}) is True
    assert _is_structural_label(
        {"text": "", "extracted_fields": {"formatted_text": None}}) is True


def test_blank_text_with_formatted_text_and_a_blank_hierarchy_label_is_kept():
    # A blank hierarchy label must not re-trigger the "text equals its own
    # hierarchy label" check ('' == '') for an entry that has formatted_text.
    entry = {"text": "", "hierarchy": [""],
             "extracted_fields": {"formatted_text": "Grand rounds"}}
    assert _is_structural_label(entry) is False


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


def test_pipe_separated_columns_match_plural_header_word():
    # Round-1 regression guard, corpus-real shape (2054_Opresko_Cv has a
    # "Dates | Journal Title" entry): a bare "date" keyword must still match
    # the plural "Dates" column header, the same way the substring check it
    # replaced did (and the way `header_patterns` already handles it via
    # `dates?`). Only "date" is passed as a keyword and the second column
    # ("Something") cannot match it either way, so this isolates the plural
    # match itself -- with the first version of this fix (a bare `\bdate\b`,
    # no trailing "s?"), word-boundary matching stopped matching "Dates"
    # entirely (0 of 2 parts match, below the 50% threshold) rather than
    # just dropping the "candidate" false positive it was meant to fix.
    text = "Dates|Something"
    assert _is_table_header_entry(text, ["date"]) is True


def test_pipe_separated_columns_still_ignore_substring_inside_a_longer_word():
    # The trailing "s?" must not resurrect the original substring bug: "date"
    # still must not match inside "candidates" (a plural of the same false
    # positive the word-boundary fix targeted), since there is no boundary
    # immediately before "date" within that word either way.
    text = "Candidates|Update"
    assert _is_table_header_entry(text, ["date"]) is False


# --- #756: a short pipe-joined DATA entry is not a header row ---------------

_HONORS_KW = ["award", "honor", "organization", "date", "year", "granting"]


@pytest.mark.parametrize("text", [
    "Best Teaching Award | 2020",
    "Best Teaching Award | Purdue University",
    "Award A | 2024 | Award B | 2023 | Award C | 2022",
    "  Award A   |   2024   |   Award B   |   2023",
    "2020\tAward A | Award B | 2019",
])
def test_short_pipe_joined_data_entry_is_not_a_header(text):
    assert _is_table_header_entry(text, _HONORS_KW) is False


@pytest.mark.parametrize("text", [
    "Name of award | Organization | Date awarded",
    "Name of award\tOrganization\tDate awarded",
    "Honor | Granting body | Year",
    "Award | Organization | Date awarded (yyyy)",
    "Award | Organization | Date(s)",
])
def test_real_pipe_joined_header_row_is_still_a_header(text):
    assert _is_table_header_entry(text, _HONORS_KW) is True


def test_a_parenthetical_value_is_not_a_format_hint():
    assert _is_table_header_entry("Award (2020) | Purdue University", _HONORS_KW) is False


def test_other_sections_keywords_do_not_collide_on_data_either():
    assert _is_table_header_entry(
        "American Medical Society | Fellow, 2019",
        ["organization", "membership", "society", "date", "member"]) is False
    assert _is_table_header_entry(
        "Organization | Membership | Date",
        ["organization", "membership", "society", "date", "member"]) is True


_POSITION_KW = ["title", "institution", "organization", "dates", "city", "state",
                "position"]


def test_positions_keywords_collide_on_data_the_same_way_and_no_longer_do():
    assert _is_table_header_entry(
        "Chair, Organization Committee | 2020", _POSITION_KW) is False
    assert _is_table_header_entry(
        "Title | Institution | Dates | City | State", _POSITION_KW) is True


@pytest.mark.parametrize("text", [
    "The | Purdue University",        # filler-only cell carries no keyword
    "Best Teaching Award |",          # an empty cell is not a label
    "Award University | 2020",        # only the listed filler words are tolerated
])
def test_a_cell_needs_a_keyword_and_only_listed_filler_to_be_a_label(text):
    assert _is_table_header_entry(text, _HONORS_KW) is False


def test_a_format_hint_after_the_keyword_still_makes_a_label_cell():
    # The hint is stripped before the filler check, so the cell is a label; the
    # other cell is what decides the row (see the value-cell tests below).
    assert _is_table_header_entry("Year (yyyy) | Honor", _HONORS_KW) is True


_MEMBERSHIP_KW = ["organization", "membership", "society", "date", "member"]


@pytest.mark.parametrize("text", [
    "Member | American Heart Association",
    "Member\tAmerican Heart Association",
    "Member | Society for Neuroscience",
    "Member | 2019",
    "Society Member | 2019-present",
    "Member | Society | 2019",
])
def test_a_bare_role_cell_beside_a_value_is_membership_data_not_a_header(text):
    """A row whose one label cell sits beside an organization name or a date
    value is data: a header names a date column but never carries a date."""
    assert _is_table_header_entry(text, _MEMBERSHIP_KW) is False


@pytest.mark.parametrize("text", [
    "Organization | Date",
    "Society | Date",
    "Member | Society",
    "Membership | Organization | Dates",
    "Member | Since",
    "Society | Year",
])
def test_two_column_membership_header_rows_are_still_headers(text):
    assert _is_table_header_entry(text, _MEMBERSHIP_KW) is True


def test_a_single_label_cell_needs_single_word_companions_to_be_a_header():
    assert _is_table_header_entry(
        "Date (yyyy) | Purdue University", _HONORS_KW) is False


def test_two_label_cells_keep_the_row_a_header_beside_a_multi_word_cell():
    assert _is_table_header_entry(
        "Award | Year | Contact person", _HONORS_KW) is True


def test_a_digit_in_a_non_label_cell_makes_the_row_data():
    assert _is_table_header_entry("Award | Year | 2020", _HONORS_KW) is False


def test_a_filler_word_alone_makes_a_label_only_with_a_keyword():
    # Each filler word is pinned on the pipe path (the keyword-count path
    # would otherwise classify "Name of award" by itself): keyword "date"
    # has no header pattern that matches these cells.
    for filler in ["name", "of", "the", "and", "or", "by", "body", "awarded",
                   "received", "issued", "granted"]:
        assert _is_table_header_entry(
            f"Honor {filler} | Year", ["honor", "year"]) is True, filler


def test_a_keyword_is_a_whole_word_inside_a_label_cell():
    # Without the word boundary "Theaward" would strip to a keyword plus the
    # filler word "the" and read as a label cell.
    assert _is_table_header_entry("Theaward | Theaward", ["award"]) is False
    assert _is_table_header_entry("Award | Award", ["award"]) is True
