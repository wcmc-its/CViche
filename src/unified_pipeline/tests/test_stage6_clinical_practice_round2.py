"""PR #625 review round-2 fixes for clinical_practice.py.

Three threads on `stage6/sections/clinical_practice.py`, all explicitly
re-demanded in-PR by the reviewer on 2026-08-25 after an earlier reply
deferred them to follow-up issues:

- thread 3850518896: positive header-cell validation for the table
  `_find_table_after_paragraph` returns, instead of only excluding funding
  tables by a negative "Award Source"/"Funding" check.
- thread 3850753654 (four asks, tested separately below): (1) a
  normalization boundary before every `.text =` write; (2) the L1 pipe
  fallback silently dropping the `location` column; (3) the L2 100-character
  truncation on the fallback path; (4) centralizing the row/font-write
  sequence duplicated across L1/L2/L3.
- thread 3850764803 (config-driven `_fill_subsection`): NOT done. See the
  review reply and the note below -- no test here, because no code changed
  for this thread.

A correction made while verifying thread 3850518896, worth recording here
because it contradicts the thread's own claim: "the three Clinical Practice
subsection tables are tables 30, 31 and 32" does not hold against the actual
.docx. Verified via python-docx: doc.tables[30:33] are the Invitations to
Speak Regional/National/International tables, not Clinical Practice/
Innovations/Leadership. The blank WCM template ships no table at all under
L1/L2/L3 -- paragraphs 96-107 (`Clinical Practice` through the blank line
before `RESEARCH`) are pure instruction prose, and the next real table in
body order is the Award Source funding table the pre-existing guard already
excluded. The header text itself ('Title', 'Institution/Location', 'Dates
(yyyy)') is still kept as the positive-match target below, because it is the
WCM template's standard 3-column convention used verbatim by those three
tables and by Section O's sibling header ('Role(s)/Position |
Institution/Location | Dates', see leadership.py) -- but it is a
same-convention guess, not a byte-verified in-place Clinical Practice
header. `_clinical_header_match` itself still classifies an unrecognized
header as ambiguous (None) rather than rejected (False) -- that tri-state
return is unchanged. What changed in #841 is what the THREE CALL SITES do
with None: they used to accept it (permissive accept, "HARD SAFETY GATE"),
which meant writing over some OTHER section's table, because the blank WCM
template has no table of its own under any clinical heading -- so a table
this function can't positively classify is never actually a
differently-worded Clinical Practice table, it is always foreign. The call
sites now treat None the same as False: a rejection that falls back to the
bullet path, which already renders the content in the right place. See
`test_l1_ambiguous_header_table_is_rejected_and_falls_back_to_bullets` below
and issue #841.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_clinical_practice_round2.py -p no:cacheprovider
"""

import sys
from collections import namedtuple
from pathlib import Path

from docx import Document

# `_clinical_header_match` reads `.text` off each cell -- real python-docx
# `_Cell` objects have that attribute; this stands in for one so the
# header-classification unit tests don't need a full Document/Table just to
# supply header strings.
_Cell = namedtuple("_Cell", ["text"])


def _cells(*texts):
    return [_Cell(t) for t in texts]

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.stage6.sections.clinical_practice import _clinical_header_match  # noqa: E402


def _generator():
    """A generator loaded against the actual WCM template, verbose off."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _fake_doc_with_table(heading_text, header_cells):
    """A minimal hand-built doc: one heading paragraph immediately followed
    by a table with the given header row and one sentinel data row, so a
    wrongly-rejected table is distinguishable from an untouched one.

    Matches the fixture shape `test_stage6_leadership_round2.py` uses for
    the same class of fix on Section O.
    """
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph().add_run(heading_text).bold = True
    table = doc.add_table(rows=1, cols=len(header_cells))
    for cell, text in zip(table.rows[0].cells, header_cells):
        cell.text = text
    sentinel = table.add_row()
    sentinel.cells[0].text = "SENTINEL-DO-NOT-TOUCH"
    gen.doc = doc
    return gen, table


class TestPositiveHeaderValidationUnit:
    """Thread 3850518896, verified directly against the real .docx."""

    def test_real_template_convention_header_matches(self):
        # Byte-real header text, read via python-docx from the actual
        # template shipped in this repo. doc.tables[30] is the Invitations
        # to Speak - Regional table, not Clinical Practice -- see the module
        # docstring for why this is nonetheless the closest verified
        # real-header source for L1/L2/L3, which have no table of their own
        # in the blank template.
        gen = _generator()
        header_cells = gen.doc.tables[30].rows[0].cells
        assert [c.text for c in header_cells] == [
            'Title', 'Institution/Location', 'Dates (yyyy)']
        assert _clinical_header_match(header_cells) is True

    def test_real_award_source_header_is_rejected(self):
        gen = _generator()
        header_cells = gen.doc.tables[15].rows[0].cells
        assert 'Award Source' in header_cells[0].text
        assert _clinical_header_match(header_cells) is False

    def test_slightly_edited_but_plausible_header_is_still_accepted(self):
        # A real faculty member's edited copy could plausibly read this way
        # -- extra parenthetical, spaced slash, different case -- and the
        # match must be positive (True), not merely fall through to the
        # ambiguous/permissive branch (None).
        edited = _cells("Title (Talk/Role)", "Institution / Location", "Dates (YYYY)")
        assert _clinical_header_match(edited) is True

    def test_unrelated_header_is_ambiguous_not_rejected(self):
        # HARD SAFETY GATE: a header this function doesn't recognize (e.g.
        # an education or membership table) must classify as None, never
        # False -- only a confident funding-table match may reject.
        unrelated = _cells("Institution", "Degree", "Year")
        assert _clinical_header_match(unrelated) is None

    def test_empty_header_row_is_ambiguous_not_rejected(self):
        assert _clinical_header_match([]) is None


class TestPositiveHeaderValidationEndToEnd:
    """Thread 3850518896, driven through `_fill_clinical_practice` itself."""

    def test_l1_convention_header_table_accepts_and_renders(self):
        gen, table = _fake_doc_with_table(
            "Clinical Practice",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "",
            "extracted_fields": {
                "activity": "Attending, inpatient medicine",
                "location": "NYP Weill Cornell",
                "start_date": "2020",
                "end_date": "2023",
            },
        }
        gen._fill_clinical_practice({"L1": [entry]})

        assert gen.stats["tables_populated"] == 1
        assert gen.stats["entries_inserted"] == 1
        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("Attending, inpatient medicine", "NYP Weill Cornell", "2020-2023")]

    def test_l1_award_source_table_is_rejected_and_falls_back_to_bullets(self):
        gen, table = _fake_doc_with_table(
            "Clinical Practice",
            ["Award Source: (funding agency)", ""],
        )
        entry = {"text": "Attending, inpatient medicine", "extracted_fields": {}}
        gen._fill_clinical_practice({"L1": [entry]})

        # Funding table left completely untouched: header row plus the
        # original sentinel row, nothing added or cleared.
        assert [tuple(c.text for c in r.cells) for r in table.rows] == [
            ("Award Source: (funding agency)", ""),
            ("SENTINEL-DO-NOT-TOUCH", ""),
        ]
        assert gen.stats["tables_populated"] == 0
        assert gen.stats["entries_inserted"] == 0

    def test_l1_ambiguous_header_table_is_rejected_and_falls_back_to_bullets(self):
        # #841 (this pin used to assert the opposite -- "still renders" into
        # the ambiguous table -- which was pinning the defect: on a real
        # render the table `_find_table_after_paragraph` hands back after a
        # clinical heading is NEVER this section's own, because the blank
        # WCM template has no table under any clinical heading at all. So an
        # unrecognized header is always some OTHER section's table (in the
        # corpus, most often a mentee placeholder -- 21 of 105 CVs lost a
        # rendered mentee table to this accept-by-default). The bullet
        # fallback already renders the content, in the right place, so #841
        # makes ambiguous a rejection, same as a confident funding-table
        # non-match. The "HARD SAFETY GATE" comment this replaced argued the
        # opposite failure mode (dropping real clinical content); that
        # premise was backwards.
        gen, table = _fake_doc_with_table("Clinical Practice", ["Activity", "Where"])
        # `_insert_bulleted_entry` inserts a new paragraph BEFORE the
        # paragraph at its target index and returns None (no-op) when that
        # index is out of range -- `_fake_doc_with_table`'s minimal fixture
        # (heading immediately followed by the table, nothing after) has no
        # paragraph there. A trailing paragraph gives the bullet path
        # somewhere to insert before, same as a real document always has
        # (the next section's heading).
        gen.doc.add_paragraph("")
        entry = {"text": "Attending, inpatient medicine", "extracted_fields": {"activity": "Attending", "location": "NYP"}}
        gen._fill_clinical_practice({"L1": [entry]})

        # Foreign table left completely untouched: header row plus the
        # original sentinel row, nothing added or cleared.
        assert [tuple(c.text for c in r.cells) for r in table.rows] == [
            ("Activity", "Where"),
            ("SENTINEL-DO-NOT-TOUCH", ""),
        ]
        assert gen.stats["tables_populated"] == 0
        # `entries_inserted` counts BOTH the table-write and bullet paths
        # (`_insert_bulleted_entry` increments it too) -- 1 here is the
        # bullet, not a table row.
        assert gen.stats["entries_inserted"] == 1
        # Rendered as a bullet after the heading instead of into the table.
        bullet_texts = [p.text for p in gen.doc.paragraphs if "Attending" in p.text]
        assert bullet_texts == ["Attending, inpatient medicine"]


class TestL1PipeFallbackKeepsLocation:
    """Thread 3850753654, item 2: parts[1] used to be dropped on the floor."""

    def test_l1_three_part_pipe_fallback_populates_all_three_columns(self):
        gen, table = _fake_doc_with_table(
            "Clinical Practice",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "Volunteer Clinic | NYP Weill Cornell | 2020-2023",
            "extracted_fields": {},
        }
        gen._fill_clinical_practice({"L1": [entry]})

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("Volunteer Clinic", "NYP Weill Cornell", "2020-2023")]

    def test_l1_pipe_fallback_does_not_override_an_already_extracted_location(self):
        # extracted_fields supplied a location; the pipe fallback only fires
        # because `activity` is empty, and must not clobber the real value
        # with the (possibly stale) pipe-parsed one.
        gen, table = _fake_doc_with_table(
            "Clinical Practice",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "Volunteer Clinic | Some Other Place | 2020-2023",
            "extracted_fields": {"location": "NYP Weill Cornell"},
        }
        gen._fill_clinical_practice({"L1": [entry]})

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("Volunteer Clinic", "NYP Weill Cornell", "2020-2023")]


class TestL2NoTruncation:
    """Thread 3850753654, item 3: no business rule behind the 100-char cut."""

    def test_l2_fallback_preserves_full_text_over_100_chars(self):
        # #841: header must positively match to reach the table-write path
        # at all (an ambiguous header like the original "Dates |
        # Title/Location | Role/Description" is now rejected, same as a
        # confident funding non-match) -- the column COUNT this test is
        # about (3) is unaffected by the header text.
        gen, table = _fake_doc_with_table(
            "Clinical Innovations",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        long_text = "A" * 150
        entry = {"text": long_text, "extracted_fields": {}}
        gen._fill_clinical_practice({"L2": [entry]})

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert len(rows) == 1
        assert rows[0][1] == long_text
        assert len(rows[0][1]) == 150


class TestNormalizationBoundary:
    """Thread 3850753654, item 1: a single `.text =` normalization boundary.

    Reuses the existing `_committee_cell_text` (stage6.normalization.fields)
    rather than inventing a parallel one -- the same helper Section O/P
    already run their row values through for the identical hazard (#256,
    #442, #450: a non-string extracted field raises deep in python-docx and
    aborts the whole document).
    """

    def test_l1_list_and_dict_valued_fields_do_not_crash_and_are_joined(self):
        gen, table = _fake_doc_with_table(
            "Clinical Practice",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "",
            "extracted_fields": {
                "activity": ["Attending physician", "Consulting physician"],
                "location": {"name": "NYP Weill Cornell"},
                "start_date": "2019",
                "end_date": "2022",
            },
        }
        gen._fill_clinical_practice({"L1": [entry]})

        # Without the normalization boundary this doesn't raise -- python-docx
        # silently iterates the raw list/dict instead ("name" for the dict,
        # "Attending physicianConsulting physician" with no separator for the
        # list) and the wrong text reaches the delivered document unnoticed.
        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("Attending physician; Consulting physician", "NYP Weill Cornell", "2019-2022")]

    def test_l3_dict_valued_role_does_not_crash_and_is_extracted(self):
        # #841: header must positively match to reach the table-write path
        # (see test_l2_fallback_preserves_full_text_over_100_chars above);
        # the column COUNT this test is about (3) is unaffected.
        gen, table = _fake_doc_with_table(
            "Clinical Leadership",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "",
            "extracted_fields": {
                "role": {"title": "Director, Wound Care Program"},
                "institution": "NYP Weill Cornell",
            },
        }
        gen._fill_clinical_practice({"L3": [entry]})

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("", "Director, Wound Care Program", "NYP Weill Cornell")]


class TestAddTableRowCentralization:
    """Thread 3850753654, item 4: one shared row/font-write helper.

    `_add_clinical_table_row` replaces the row-creation/text-write/font-apply
    sequence that used to be written out three times. These pin that its
    narrower-table fallback branches (previously duplicated per-renderer)
    still behave the same after being centralized.
    """

    def test_two_column_table_uses_the_two_column_fallback(self):
        # #841: header must positively match to reach the table-write path;
        # a 2-cell header row can still satisfy `_clinical_header_match`
        # (it only inspects the first two cells), so "Title | Location"
        # keeps this a genuinely 2-column table.
        gen, table = _fake_doc_with_table("Clinical Practice", ["Title", "Location"])
        entry = {
            "text": "",
            "extracted_fields": {
                "activity": "Attending physician",
                "start_date": "2020",
                "end_date": "2021",
            },
        }
        gen._fill_clinical_practice({"L1": [entry]})

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("Attending physician", "2020-2021")]

    def test_one_column_table_uses_the_one_column_fallback(self):
        # A 1-column table can never satisfy `_clinical_header_match`'s
        # positive match -- it requires a second cell to check for
        # "institution"/"location" -- so after #841 it can never reach
        # `_add_clinical_table_row` through the render path at all (an
        # unrecognized header is now a rejection, same as a funding table).
        # Exercise the centralized helper directly instead, same as its own
        # docstring describes ("the narrower branches exist only so a
        # differently-shaped table still renders something instead of
        # raising").
        gen = WCMTemplateGenerator(verbose=False)
        doc = Document()
        table = doc.add_table(rows=1, cols=1)
        table.rows[0].cells[0].text = "Everything"
        gen.doc = doc

        gen._add_clinical_table_row(
            table,
            three_col=["2021", "New triage protocol", "some description"],
            two_col=["New triage protocol", "2021"],
            one_col=["2021: New triage protocol"],
        )

        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("2021: New triage protocol",)]


def _fake_doc_with_mentee_shaped_table(heading_text):
    """Heading immediately followed by a 6-row `Name`-headed table -- the
    WCM template's own mentee-placeholder shape (#841: this is the table
    the corpus cascade actually leaves as "the next table" after a clinical
    heading in 21 of 105 CVs) -- with a SENTINEL marker in a data row, plus
    a trailing paragraph so the bullet fallback has somewhere to insert
    before (see the comment on `test_l1_ambiguous_header_table_is_rejected_
    and_falls_back_to_bullets` above). Returns (gen, table, rows_before), a
    byte-copy of the table's cell text for the untouched-table assertion.
    """
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph().add_run(heading_text).bold = True
    table = doc.add_table(rows=6, cols=2)
    table.rows[0].cells[0].text = "Name"
    table.rows[0].cells[1].text = "Institution/Program"
    table.rows[1].cells[0].text = "SENTINEL-DO-NOT-TOUCH"
    doc.add_paragraph("")
    gen.doc = doc
    rows_before = [tuple(c.text for c in r.cells) for r in table.rows]
    return gen, table, rows_before


class TestSubsectionMenteeShapedTableProtection841:
    """#841, ticket D-836-R2 TASK 2: per subsection (a) a mentee-shaped
    table is untouched and the entry falls back to bullets. (b) and (c) --
    a positively-matching table still fills, a funding table is still
    rejected -- already exist for L1 (`TestPositiveHeaderValidationEndToEnd`)
    and are added here for L2/L3, which had no end-to-end coverage of
    either branch before this round.
    """

    # --- (a) mentee-shaped table untouched, L1/L2/L3 ---------------------

    def test_l1_mentee_shaped_table_is_untouched_and_falls_back_to_bullets(self):
        gen, table, rows_before = _fake_doc_with_mentee_shaped_table("Clinical Practice")
        entry = {"text": "Attending, inpatient medicine",
                  "extracted_fields": {"activity": "Attending", "location": "NYP"}}
        gen._fill_clinical_practice({"L1": [entry]})

        rows_after = [tuple(c.text for c in r.cells) for r in table.rows]
        assert rows_after == rows_before
        assert gen.stats["tables_populated"] == 0
        assert any("Attending, inpatient medicine" in p.text for p in gen.doc.paragraphs)

    def test_l2_mentee_shaped_table_is_untouched_and_falls_back_to_bullets(self):
        gen, table, rows_before = _fake_doc_with_mentee_shaped_table("Clinical Innovations")
        entry = {"text": "New triage protocol", "extracted_fields": {"title": "New triage protocol"}}
        gen._fill_clinical_practice({"L2": [entry]})

        rows_after = [tuple(c.text for c in r.cells) for r in table.rows]
        assert rows_after == rows_before
        assert gen.stats["tables_populated"] == 0
        assert any("New triage protocol" in p.text for p in gen.doc.paragraphs)

    def test_l3_mentee_shaped_table_is_untouched_and_falls_back_to_bullets(self):
        gen, table, rows_before = _fake_doc_with_mentee_shaped_table("Clinical Leadership")
        entry = {"text": "Director, Wound Care Program",
                  "extracted_fields": {"role": "Director, Wound Care Program"}}
        gen._fill_clinical_practice({"L3": [entry]})

        rows_after = [tuple(c.text for c in r.cells) for r in table.rows]
        assert rows_after == rows_before
        assert gen.stats["tables_populated"] == 0
        assert any("Director, Wound Care Program" in p.text for p in gen.doc.paragraphs)

    # --- (b) positively-matching table still fills, L2/L3 (L1 exists) ----

    def test_l2_convention_header_table_accepts_and_renders(self):
        gen, table = _fake_doc_with_table(
            "Clinical Innovations",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {"text": "", "extracted_fields": {"title": "New triage protocol", "start_date": "2021"}}
        gen._fill_clinical_practice({"L2": [entry]})

        assert gen.stats["tables_populated"] == 1
        assert gen.stats["entries_inserted"] == 1
        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("2021", "New triage protocol", "")]

    def test_l3_convention_header_table_accepts_and_renders(self):
        gen, table = _fake_doc_with_table(
            "Clinical Leadership",
            ["Title", "Institution/Location", "Dates (yyyy)"],
        )
        entry = {
            "text": "",
            "extracted_fields": {
                "role": "Director, Wound Care Program",
                "institution": "NYP Weill Cornell",
                "start_date": "2020",
                "end_date": "2023",
            },
        }
        gen._fill_clinical_practice({"L3": [entry]})

        assert gen.stats["tables_populated"] == 1
        assert gen.stats["entries_inserted"] == 1
        rows = [tuple(c.text for c in r.cells) for r in table.rows[1:]]
        assert rows == [("2020-2023", "Director, Wound Care Program", "NYP Weill Cornell")]

    # --- (c) funding table still rejected, L2/L3 (L1 exists) -------------

    def test_l2_award_source_table_is_rejected_and_falls_back_to_bullets(self):
        gen, table = _fake_doc_with_table(
            "Clinical Innovations",
            ["Award Source: (funding agency)", ""],
        )
        entry = {"text": "New triage protocol", "extracted_fields": {}}
        gen._fill_clinical_practice({"L2": [entry]})

        assert [tuple(c.text for c in r.cells) for r in table.rows] == [
            ("Award Source: (funding agency)", ""),
            ("SENTINEL-DO-NOT-TOUCH", ""),
        ]
        assert gen.stats["tables_populated"] == 0
        assert gen.stats["entries_inserted"] == 0

    def test_l3_award_source_table_is_rejected_and_falls_back_to_bullets(self):
        gen, table = _fake_doc_with_table(
            "Clinical Leadership",
            ["Award Source: (funding agency)", ""],
        )
        entry = {"text": "Director, Wound Care Program", "extracted_fields": {}}
        gen._fill_clinical_practice({"L3": [entry]})

        assert [tuple(c.text for c in r.cells) for r in table.rows] == [
            ("Award Source: (funding agency)", ""),
            ("SENTINEL-DO-NOT-TOUCH", ""),
        ]
        assert gen.stats["tables_populated"] == 0
        assert gen.stats["entries_inserted"] == 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
