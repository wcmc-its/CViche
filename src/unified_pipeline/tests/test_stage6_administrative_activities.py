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


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
