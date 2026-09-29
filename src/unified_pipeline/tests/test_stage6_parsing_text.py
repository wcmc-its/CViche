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
    _looks_like_multiple_records,
    _parse_flattened_committee_lines,
    _parse_multi_membership_entry,
    ParsedActivityLine,
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
    text = "Dates|Journal Title"
    assert _is_table_header_entry(text, ["date"]) is True


def test_pipe_separated_columns_still_ignore_substring_inside_a_longer_word():
    # The trailing "s?" must not resurrect the original substring bug: "date"
    # still must not match inside "candidates" (a plural of the same false
    # positive the word-boundary fix targeted), since there is no boundary
    # immediately before "date" within that word either way.
    text = "Candidates|Update"
    assert _is_table_header_entry(text, ["date"]) is False


# --- #756: a pipe-joined entry is a header only if EVERY cell is header vocabulary

_HONORS_KW = ["award", "honor", "organization", "date", "year", "granting"]


@pytest.mark.parametrize("text", [
    "Best Teaching Award | 2020",
    "Best Teaching Award | Purdue University",
    "Award A | 2024 | Award B | 2023 | Award C | 2022",
    "  Award A   |   2024   |   Award B   |   2023",
    "2020\tAward A | Award B | 2019",
    "Award | Purdue University",
])
def test_pipe_joined_content_is_not_a_header_row(text):
    assert _is_table_header_entry(text, _HONORS_KW) is False


@pytest.mark.parametrize("text", [
    "Name of award | Organization | Date awarded (yyyy)",
    "Year | Title",
    "Dates | Journal Title",
    "Year (YYYY) | Person Months (##.##)",
    "Dates of Role(s) | Title of Role(s)",
])
def test_pipe_joined_header_rows_are_still_headers(text):
    assert _is_table_header_entry(text, _HONORS_KW) is True


def test_a_year_in_any_cell_makes_the_entry_data():
    assert _is_table_header_entry("Year | 2020", _HONORS_KW) is False


def test_membership_row_with_member_cell_is_not_a_header():
    # Corpus shape (web240): the "Member" cell is a header keyword, the
    # society cell is content.
    kw = ["organization", "membership", "society", "date", "member"]
    assert _is_table_header_entry("Society for Neuroscience\tMember", kw) is False


def test_a_year_beside_header_words_makes_the_cell_data():
    # "Awarded 2020" is made of header words plus a year; a header row names
    # a date column, it never carries a date value.
    assert _is_table_header_entry("Awarded 2020 | Organization", _HONORS_KW) is False


def test_an_entry_of_only_delimiters_is_not_a_header():
    assert _is_table_header_entry(" | | ", _HONORS_KW) is False


# --- #758: short acronym organizations on the pipe-free path ----------------

def test_short_acronym_organizations_are_kept_on_the_pipe_free_path():
    # `_entry_parts` hands this parser parts with the pipes already removed, so
    # every part takes the no-pipe branch. AMA/NIH/ASCO/IEEE are real
    # organizations of five characters or fewer and used to be dropped by a
    # `len(line) > 5` cutoff.
    parts = ["Member", "AMA", "2010-present",
             "Fellow", "ASCO", "2015-present",
             "Member", "IEEE", "2018-present"]
    assert _parse_multi_membership_entry(parts) == [
        ("Member", "AMA", "2010-present"),
        ("Fellow", "ASCO", "2015-present"),
        ("Member", "IEEE", "2018-present"),
    ]


@pytest.mark.parametrize("junk", [
    "2005", "(2005)", "May 2005", "3/2010", "-", "--", "7",
    "Dates", "Role", "Title", "Present", "N/A", "Chair", "Board", "Yes",
])
def test_non_organization_parts_are_still_rejected_on_the_pipe_free_path(junk):
    # What the length cutoff was (accidentally) guarding against, now rejected
    # by shape: bare years / month-years, punctuation, column-header and filler
    # words. None of them may become an organization.
    assert _parse_multi_membership_entry(["Member", junk, "2010-present"]) == []


# --- #664: the institution column of a flattened Section O table -----------

def test_pipe_row_institution_column_lands_in_the_institution_field():
    lines = ["Chair, Zorblax Board | Quuxville General Hospital | 2001-2005"]
    assert _parse_flattened_committee_lines(lines, institution_column=True) == [
        ParsedActivityLine("Chair, Zorblax Board", (), "2001-2005",
                           "Quuxville General Hospital")]


def test_pipe_row_default_keeps_every_cell_in_the_activity():
    # Section P's middle column is Role, not institution: the default must
    # read the row exactly as it did before the field existed.
    lines = ["Zorblax Board | Quuxville General Hospital | 2001-2005"]
    assert _parse_flattened_committee_lines(lines) == [
        ParsedActivityLine("Zorblax Board | Quuxville General Hospital", (), "2001-2005")]


def test_pipe_row_institution_column_still_lifts_the_role_parenthetical():
    lines = ["Zorblax Board (Chair 2001-2005) | Quuxville General Hospital | 2001-2005"]
    assert _parse_flattened_committee_lines(lines, institution_column=True) == [
        ParsedActivityLine("Zorblax Board", ("Chair",), "2001-2005",
                           "Quuxville General Hospital")]


def test_pipe_row_with_two_cells_has_no_institution_even_when_asked():
    # "Committee | 1999-2010": nothing sits between the activity and the date.
    assert _parse_flattened_committee_lines(
        ["Zorblax Board | 1999-2010"], institution_column=True) == [
        ParsedActivityLine("Zorblax Board", (), "1999-2010")]


def test_pipe_row_with_several_middle_cells_joins_them_as_the_institution():
    lines = ["Chair | Quuxville General Hospital | Ohio | 2001-2005"]
    (item,) = _parse_flattened_committee_lines(lines, institution_column=True)
    assert (item.activity, item.institution) == ("Chair", "Quuxville General Hospital, Ohio")


# --- #660: one dated record versus several -----------------------------------

@pytest.mark.parametrize("lines", [
    ["Zorblax Board (Chair 2001-2005)", "Quux Council (Member 2006-2008)"],   # two parentheticals
    ["Zorblax Board | 2001-2005", "Quux Council | 2006-2008"],                 # two pipe dates
    ["Zorblax Board  2001-2005", "Quux Council  2006"],                        # two trailing dates
    ["2001-2005    Zorblax Board", "2006-2008    Quux Council"],              # date-prefixed records
    ["Zorblax Board", "Quux Council", "Frob Panel", "2001", "2002", "2003"],   # orphaned date column
    ["Zorblax Board (Chair 2001-2005)", "Meets monthly", "Reviews budgets",
     "Quux Council (Member 2006-2008)"],                                       # records split by prose
])
def test_two_dated_lines_are_multiple_records(lines):
    assert _looks_like_multiple_records(lines) is True


@pytest.mark.parametrize("lines", [
    ["Zorblax Board"],
    ["Zorblax Board (Chair 2001-2005)"],
    ["Zorblax Board (Chair 2001-2005)", "Meets monthly", "Reviews budgets",
     "Advises the dean on space", "Reports to the senate"],                    # wrapped description
    ["Zorblax Board", "Dates", "2001-2005"],                                   # header label + one date
    ["Zorblax Board | Quuxville General Hospital", "Quuxville, Ohio | 2001-"],
])
def test_at_most_one_dated_line_is_a_single_record(lines):
    assert _looks_like_multiple_records(lines) is False
