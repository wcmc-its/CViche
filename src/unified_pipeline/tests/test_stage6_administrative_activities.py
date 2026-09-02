"""Section P (administrative activities) fixes: #660 item 1, #627.

Two independent, pre-existing bugs in `_parse_administrative_activity_rows`'s
single/double-line fallback, both surfaced by review on #625 (thread history
in the issue text) and confirmed against `origin/dev`. #660 items 2 and 3
(parenthetical dates bypassing `format_date_range`, and a parenthetical
already present in `activity` not getting stripped) were already fixed on
dev before this PR -- `TestParentheticalFallbackAlreadyFixed` below pins
that existing behavior rather than re-fixing it.

- #660 item 1: `if len(lines) >= 3` had no check for whether extraction
  already produced a complete activity+dates record before rerouting to the
  raw-text multiline parser, silently discarding a fully-populated
  structured record whenever the source text happened to wrap across 3+
  display lines.
- #627: a 1- or 2-line entry whose raw text carries an unresolved
  pipe-separated date column (e.g. "Committee (Chair 2002-present) |
  1996-Present") bypassed the shared #572 line parser entirely, because
  only 3+-line entries (or 2-line entries with nothing extracted) were
  routed through it. The parenthetical fallback has no pipe branch, so it
  stripped only the paren and left "| 1996-Present" stuck in Activity while
  taking the wrong paren date for Dates.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_administrative_activities.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.stage6.formatting import format_date_range  # noqa: E402


def _generator():
    return WCMTemplateGenerator(verbose=False)


def _rows(entry):
    """Run one entry through the pure parsing function (no python-docx
    needed -- `_parse_administrative_activity_rows` never touches a table,
    see its own docstring / review thread 3850029915)."""
    return _generator()._parse_administrative_activity_rows([entry])


class TestCompleteStructuredRecordSurvivesMultilineText:
    """#660 item 1: a fully-populated activity+dates record from
    `extracted_fields` must never be discarded just because the entry's raw
    source text happens to wrap across 3+ lines."""

    def test_three_line_text_with_complete_fields_keeps_the_structured_record(self):
        entry = {
            "text": (
                "Faculty Senate Research Policy Committee\n"
                "Works on curriculum and research policy matters\n"
                "Meets monthly during the academic year"
            ),
            "extracted_fields": {
                "committee_name": "Faculty Senate Research Policy Committee",
                "role": "Member",
                "start_date": "2015",
                "end_date": "2020",
            },
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("2015", "2020", "P")
        assert rows == [("Faculty Senate Research Policy Committee", "Member", expected_dates)]

    def test_three_line_text_with_incomplete_fields_still_reparses(self):
        # Regression guard on the fix above: an entry that genuinely IS a
        # merged multi-record block (extraction only captured the first
        # item) must still go through the multiline reparse -- the
        # completeness check must not swallow this case too.
        entry = {
            "text": (
                "Committee A (Chair 2011-2013)\n"
                "Committee B (Member 2014-2016)\n"
                "Committee C (Vice Chair 2017-2019)"
            ),
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        assert len(rows) == 3
        assert rows[0][0] == "Committee A"
        assert rows[1][0] == "Committee B"
        assert rows[2][0] == "Committee C"

    def test_single_line_no_pipe_complete_record_is_not_reparsed(self):
        # Review round 1 (PR #714, thread 3915848371 item 1): the existing
        # complete-record coverage above always combines completeness with a
        # 3-line text, so it primarily guards `len(lines) <= 3`, not the
        # "no pipe at all" shape. Use a raw text with its OWN different
        # parenthetical role+date, so a reparse (or the parenthetical
        # fallback) would be observable if either ran: reparsing would
        # produce role "Member" / dates from 1990-1995 instead of the
        # structured Chair/2015-2020 record.
        entry = {
            "text": "Curriculum Committee (Member 1990-1995)",
            "extracted_fields": {
                "committee_name": "Curriculum Committee",
                "role": "Chair",
                "start_date": "2015",
                "end_date": "2020",
            },
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("2015", "2020", "P")
        assert rows == [("Curriculum Committee", "Chair", expected_dates)]


class TestUnresolvedPipeRoutesThroughSharedParser:
    """#627: a 1- or 2-line pipe entry now routes through the same shared
    #572 parser the 3+-line path already uses."""

    def test_single_line_pipe_entry_uses_shared_parser(self):
        entry = {
            "text": "Pediatric Education Committee (Chair 2002-present) | 1996-Present",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        # The shared parser's resolution, not the old bug's
        # ("Pediatric Education Committee | 1996-Present", "Chair", "2002-present").
        assert rows == [("Pediatric Education Committee", "Chair", "1996-Present")]

    def test_two_line_pipe_entry_uses_shared_parser(self):
        entry = {
            "text": "Curriculum Committee (Chair 2005-2010) | 2004-2011\nSecond unrelated line",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        assert ("Curriculum Committee", "Chair", "2004-2011") in rows

    def test_complete_extraction_with_a_pipe_is_not_rerouted(self):
        # Regression guard: when extraction already gave a complete
        # activity+dates record, a `|` elsewhere in the raw text must not
        # send the entry through the reparse path -- `structured_complete`
        # (the same #660 item 1 signal) protects this case too.
        entry = {
            "text": "Faculty Senate Committee | irrelevant trailing note",
            "extracted_fields": {
                "committee_name": "Faculty Senate Committee",
                "role": "Chair",
                "start_date": "2015",
                "end_date": "2020",
            },
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("2015", "2020", "P")
        assert rows == [("Faculty Senate Committee", "Chair", expected_dates)]

    def test_non_pipe_single_line_entry_is_unaffected(self):
        # Sanity check that the pipe-routing fix is scoped to entries that
        # actually contain a pipe -- a plain parenthetical-only entry keeps
        # using the (already #660-item-2/3-fixed) parenthetical fallback,
        # not the shared parser's own unformatted date string.
        entry = {
            "text": "Committee A (2010-present)",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        # format_date_range capitalizes an open-ended date to "Present";
        # the shared parser's own raw regex groups would preserve whatever
        # case the source used ("present"). This pins that the already-fixed
        # #660 item 2 path is still the one in effect here.
        expected_dates = format_date_range("2010", "present", "P")
        assert expected_dates.endswith("Present")
        assert rows == [("Committee A", "", expected_dates)]

    def test_basic_pipe_activity_date_form_uses_shared_parser(self):
        # Review round 1 (PR #714, thread 3915848371 item 3): the existing
        # pipe coverage above always combines the pipe date with a
        # parenthetical role, so it never independently proves the plain
        # "Activity | date" shape (no parenthetical anywhere) resolves
        # correctly on its own.
        entry = {
            "text": "Quality Improvement Committee | 1996-Present",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("1996", "Present", "P")
        assert rows == [("Quality Improvement Committee", "", expected_dates)]


class TestTwoLineNonPipeEmptyExtractionBoundary:
    """Review round 1 (PR #714, thread 3915848371 item 2): a 2-line,
    non-pipe entry with incomplete extraction and a parenthetical
    role+date on line 1.

    Two shapes, distinguished by what extraction gave `activity`:

    - A2b below: extraction already populated `activity` (just not
      `dates`). `(len(lines) > 1 and not activity)` is False from the
      start (activity was never empty), so this never reaches the
      reparse route -- the parenthetical fallback (administrative_activities.py:262-300)
      fills `dates`/`role` from line 1's parenthetical instead, and the
      single-row fallback (line 330-332) writes it as one row. Matches the
      #660-item-1 FACTS note: "a 2-line entry whose extraction has
      `activity` but no `dates` is NOT reparsed".

    - A2 below: extraction is fully empty. Contrary to what the FACTS note
      and PR #714's own T1-item-2 review comment both assume ("a two-line
      entry with incomplete extraction and parenthetical activity/date
      information ... reparsed"), this does NOT reparse either. The
      parenthetical fallback runs first (`if not dates`, line 262) and,
      because `activity` is falsy, executes
      `_PARENTHETICAL_WITH_YEAR_RE.sub('', activity or original_text)`
      against the WHOLE original_text (both lines) -- stripping only the
      matched parenthetical and leaving the raw newline and line 2 stuck
      inside the single resulting `activity` string. By the time the
      routing check at line 322 runs, `activity` is no longer empty, so
      `(len(lines) > 1 and not activity)` is False and the shared #572
      reparse never fires. A second committee genuinely present on line 2
      would have its own role/dates silently dropped (see
      test_two_committees_are_collapsed_and_the_second_ones_dates_are_lost
      below) -- pinned as real, current behavior; not fixed here (test-only
      ticket). Flagged in the PR body for review round 1.
    """

    def test_activity_present_no_dates_takes_the_parenthetical_fallback(self):
        entry = {
            "text": "Curriculum Committee (Chair 2005-2010)\nsecond line",
            "extracted_fields": {"committee_name": "Curriculum Committee"},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("2005", "2010", "P")
        assert rows == [("Curriculum Committee", "Chair", expected_dates)]

    def test_empty_extraction_with_a_leading_parenthetical_is_not_reparsed(self):
        entry = {
            "text": "Curriculum Committee (Chair 2005-2010)\nsecond line",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        # Actual behavior (see class docstring): the parenthetical fallback
        # intercepts this before the reparse-routing check ever sees an
        # empty `activity`, so it is NOT reparsed -- contrary to the T1
        # item 2 review comment's stated expectation for this shape.
        expected_dates = format_date_range("2005", "2010", "P")
        assert rows == [("Curriculum Committee\nsecond line", "Chair", expected_dates)]

    def test_two_committees_are_collapsed_and_the_second_ones_dates_are_lost(self):
        # Sharper demonstration of the same gap: when line 2 is itself a
        # genuine second committee with its own parenthetical role+date,
        # the fallback still only reads the FIRST parenthetical match in
        # the whole text, so Committee B's role and dates are dropped
        # entirely rather than reparsed into a second row.
        entry = {
            "text": "Committee A (Chair 2005-2010)\nCommittee B (Member 2011-2015)",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        expected_dates = format_date_range("2005", "2010", "P")
        assert rows == [("Committee A\nCommittee B", "Chair", expected_dates)]


class TestParentheticalFallbackAlreadyFixed:
    """#660 items 2 and 3, already fixed on dev before this PR -- pinned
    here (not re-fixed) so a future change to this file can't silently
    reintroduce either bug without a test noticing."""

    def test_open_ended_parenthetical_date_is_capitalized(self):
        # Item 2: the parenthetical fallback must route through
        # format_date_range, not an ad hoc f-string -- "present" renders
        # "Present" the same way every other open-ended date on this
        # taxonomy code does.
        entry = {
            "text": "Committee A (2010-present)",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        assert rows[0][2] == format_date_range("2010", "present", "P")
        assert "Present" in rows[0][2]

    def test_parenthetical_already_in_activity_is_stripped_not_duplicated(self):
        # Item 3: when extraction already populated `activity` with the
        # parenthetical intact, it must still be stripped -- not left
        # duplicated alongside the same role/dates rendered into their own
        # columns.
        entry = {
            "text": "Committee A (Chair 2011-2013)",
            "extracted_fields": {"activity": "Committee A (Chair 2011-2013)"},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        assert rows == [("Committee A", "Chair", format_date_range("2011", "2013", "P"))]


class TestThreeLineBurstFallsBackWhenReparseYieldsNothing:
    """The `if parsed_rows: rows.extend(...) else: <single-row fallback>`
    restructuring (administrative_activities.py:327-332) also changed the
    3+-line `multiline_burst` path for the case where the shared reparse
    (`_multiline_committee_rows` -> `_parse_flattened_committee_lines`)
    finds nothing usable -- e.g. a block of bare orphaned dates with no
    activity text on any line. Before this PR that case fell straight
    through to `rows.extend(self._multiline_committee_rows(lines))` with no
    fallback, so an empty reparse silently dropped the entry
    (`_parse_administrative_activity_rows` returned `[]`). Now every route
    into `_multiline_committee_rows` -- including this one -- shares the
    same fallback, so an empty reparse folds the raw text into one row
    instead of vanishing. Benign direction (recovers text that would
    otherwise be dropped) and zero corpus incidence, but undisclosed and
    previously untested -- pinned here.
    """

    def test_date_only_three_line_block_falls_back_to_one_row_instead_of_vanishing(self):
        entry = {
            "text": "2010\n2011\n2012",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }

        rows = _rows(entry)

        assert rows == [("2010\n2011\n2012", "", "")]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
