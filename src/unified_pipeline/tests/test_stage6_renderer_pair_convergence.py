"""Four stage-6 renderer pairs share one implementation after #572.

Each pair implemented one rule twice, and two had already drifted apart into
live content defects:

- Section P's committee line parser was a stripped copy of section O's: no pipe
  branch, no trailing-date branch, no orphaned-date pairing. Five of seven
  common line shapes rendered with the raw line in the Activity cell and an
  empty Dates cell. Both sections now run
  `_parse_flattened_committee_lines`; O folds parsed role titles back into the
  activity text, P routes them to its Role column.
- The L2 (Clinical Innovations) bullet fallback used `_insert_bulleted_entry`,
  collapsing a multi-line entry into ONE list paragraph with soft `<w:br/>`
  breaks, where L1/L3 used `_insert_multiline_as_bullets` (one bullet per
  line). L2 now matches.
- The citation author-match + split logic existed byte-identically in the plain
  and tracked-insertion writers; it now lives once in `_citation_author_split`.
- The board-certification token classifier's four rules were written twice
  (pipe and non-pipe branch); now `_classify_cert_token` plus the module
  constants `YEAR_PATTERN` / `CERTIFICATE_NUMBER_PATTERN`.

These are wire tests: each drives the real fill method against the real WCM
template (or a real python-docx table) so a regression in any converged line
fails here, not just in a helper unit test.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_renderer_pair_convergence.py -p no:cacheprovider
"""

import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.stage6.sections.bibliography import _citation_author_split  # noqa: E402
from unified_pipeline.stage6.sections.board_certification import (  # noqa: E402
    CERTIFICATE_NUMBER_PATTERN,
    YEAR_PATTERN,
    _classify_cert_token,
)

# The seven flattened-table line shapes from #572's O-vs-P comparison. Five of
# them lost their date under P's drifted copy; the last two are the controls.
FLATTENED_COMMITTEE_LINES = [
    "Curriculum Committee (Chair 1999-2010)",
    "Curriculum Committee (Chair 1999-2010 )",  # trailing space inside parens
    "Executive Committee (Vice Chair 2006-2008) (Chair 2008-2010)",
    "Faculty Council | 1996-Present",
    "Admissions Committee    1999-2010",
    "Promotions Committee (2015-2020)",  # control: parsed the same before
    "Research Advisory Board",           # control: no date anywhere
]


def _generator(cls=WCMTemplateGenerator):
    gen = cls(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _rows_after(gen, header_text):
    """Data rows (as text tuples) of the table following the given header."""
    section_idx = gen._find_paragraph_with_text(header_text)
    assert section_idx is not None, f"template lost its '{header_text}' header"
    table = gen._find_table_after_paragraph(section_idx)
    assert table is not None
    return [tuple(cell.text for cell in row.cells) for row in table.rows[1:]]


def _multiline_entry(code):
    return {
        "text": "\n".join(FLATTENED_COMMITTEE_LINES),
        "extracted_fields": {},
        "taxonomy_code": code,
    }


class TestSectionPGainsSectionOParser:
    def test_committee_rows_gain_role_and_dates(self):
        gen = _generator()
        gen._fill_administrative_activities([_multiline_entry("P")])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Curriculum Committee", "Chair", "1999-2010"),
            ("Curriculum Committee", "Chair", "1999-2010"),
            ("Executive Committee", "Vice Chair; Chair", "2008-2010"),
            ("Faculty Council", "", "1996-Present"),
            ("Admissions Committee", "", "1999-2010"),
            ("Promotions Committee", "", "2015-2020"),
            ("Research Advisory Board", "", ""),
        ]

    def test_truthy_non_dict_extracted_fields_renders_instead_of_raising(self):
        # extracted_fields as a truthy non-Mapping used to slip past `or {}`
        # and AttributeError on the first fields.get(); the isinstance guard
        # drops it to {} so the parenthetical repair still recovers the row.
        gen = _generator()
        entry = {
            "text": "Curriculum Committee (Chair 1999-2010)",
            "extracted_fields": ["stray", "list"],
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Curriculum Committee", "Chair", "1999-2010"),
        ]

    def test_orphaned_date_lines_pair_forward_instead_of_dropping(self):
        # A flattened source table arrives as column 1's lines then column 2's:
        # the drifted copy dropped the bare date lines outright.
        gen = _generator()
        entry = {
            "text": "Committee A\nCommittee B\n1999-2010\n2005-2008",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Committee A", "", "1999-2010"),
            ("Committee B", "", "2005-2008"),
        ]

    def test_pipe_line_with_parenthetical_role(self):
        # The pipe branch's inner sub-branch (the parser docstring's own
        # example shape): role extracted from the parenthetical, pipe date wins.
        gen = _generator()
        entry = {
            "text": "Pediatric Education Committee (Chair 2002-present) | 1996-Present"
                    "\nCommittee B\nCommittee C",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Pediatric Education Committee", "Chair", "1996-Present"),
            ("Committee B", "", ""),
            ("Committee C", "", ""),
        ]

    def test_leading_pipe_bare_date_joins_the_dates_pool(self):
        # "| 1999-2010" (a one-part pipe line) unwraps to a date-only line and
        # pairs forward like any other orphaned date.
        gen = _generator()
        entry = {
            "text": "Committee A\n| 1999-2010",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Committee A", "", "1999-2010"),
        ]

    def test_paren_only_line_keeps_its_row(self):
        # The one declared behavior change beyond #572's enumerated items: a
        # role-only parenthetical renders as a row with an empty Activity cell
        # instead of being dropped.
        gen = _generator()
        entry = {
            "text": "(Program Director 2010-2013)\nCommittee B\nCommittee C",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("", "Program Director", "2010-2013"),
            ("Committee B", "", ""),
            ("Committee C", "", ""),
        ]

    def test_header_labels_are_skipped(self):
        gen = _generator()
        entry = {
            "text": "Role\nDates\nCommittee A (Chair 2001-2002)\nCommittee B\nCommittee C",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Committee A", "Chair", "2001-2002"),
            ("Committee B", "", ""),
            ("Committee C", "", ""),
        ]


class TestAdministrativeActivitiesReviewFixes:
    """PR #625 review-round fixes to this file, one test per thread. See
    docs/analysis for the full brief; thread ids are the GitHub review
    comment ids."""

    def test_parenthetical_present_date_renders_capital_p(self):
        # review thread 3849996402: the parenthetical-derived dates now route
        # through format_date_range, like every extracted date, instead of a
        # second hand-built date string that never capitalized "present".
        gen = _generator()
        entry = {
            "text": "University Senate (2010-present)",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("University Senate", "", "2010-Present"),
        ]

    def test_parenthetical_cleaned_even_when_activity_already_extracted(self):
        # review thread 3850003492: his exact example -- when `activity` is
        # pre-populated WITH the parenthetical still inside it, the
        # parenthetical used to survive in the Activity column while Role and
        # Dates rendered it a second time. The unrelated "(Emeritus)"
        # parenthetical is left alone -- only the one carrying a year is
        # stripped, so this isn't a blanket paren-strip (HARD SAFETY GATE:
        # realistic content still renders).
        gen = _generator()
        entry = {
            "text": "Committee A (Emeritus) (Chair 2005-2007)",
            "extracted_fields": {"activity": "Committee A (Emeritus) (Chair 2005-2007)"},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Committee A (Emeritus)", "Chair", "2005-2007"),
        ]

    def test_committee_alias_precedence_is_explicit_and_wins_over_conflicts(self):
        # review threads 3850009796 and 3850014030, same underlying fix: the
        # committee_name/committee/activity alias chain now resolves through
        # one named precedence (_COMMITTEE_ALIAS_KEYS) instead of a silent
        # `or` chain, and this is the conflicting-populated-fields test he
        # asked for -- committee_name wins whether the conflict is scalar
        # values or, per the pre-existing list-detection order, competing
        # lists.
        gen = _generator()
        scalar_entry = {
            "text": "irrelevant",
            "extracted_fields": {
                "committee_name": "IRB",
                "committee": "Wrong Committee",
                "activity": "Even More Wrong",
            },
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([scalar_entry])
        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("IRB", "", ""),
        ]

        gen2 = _generator()
        list_entry = {
            "text": "irrelevant",
            "extracted_fields": {
                "committee_name": [{"committee_name": "Right List Committee"}],
                "activity": [{"activity": "Wrong List Committee"}],
            },
            "taxonomy_code": "P",
        }
        gen2._fill_administrative_activities([list_entry])
        assert _rows_after(gen2, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Right List Committee", "", ""),
        ]

    def test_committee_record_boundary_coerces_dict_activity_before_paren_cleanup(self):
        # review thread 3850017930: `_CommitteeRecord.from_raw` coerces a
        # domain value to plain text at the point it's read out of `fields`,
        # not only inside `_add_committee_row`'s final safeguard. This is
        # also what keeps the thread-3850003492 fix above safe: without the
        # early coercion, `activity or original_text` could hand a raw dict
        # to `_PARENTHETICAL_WITH_YEAR_RE.sub`, which raises on a non-string.
        gen = _generator()
        entry = {
            "text": "(Chair 2011-2013)",
            "extracted_fields": {"activity": {"committee_name": "Nested Committee"}},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Nested Committee", "Chair", "2011-2013"),
        ]

    def test_parse_failure_mid_entries_leaves_existing_table_content_untouched(self):
        # review thread 3850029915: entries are parsed into a complete row
        # list BEFORE the table is cleared. A malformed later entry raising
        # during parsing (here: a non-dict entry, to force the failure
        # deterministically) must not leave the table cleared with only a
        # partial render in its place, and the exception must still
        # propagate -- not be swallowed (coding standards 5.4).
        gen = _generator()
        section_idx = gen._find_paragraph_with_text("INSTITUTIONAL ADMINISTRATIVE")
        table = gen._find_table_after_paragraph(section_idx)
        table.add_row().cells[0].text = "Pre-existing Row"
        rows_before = _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE")
        assert ("Pre-existing Row", "", "") in rows_before

        entries = [
            {"text": "Committee A", "extracted_fields": {}, "taxonomy_code": "P"},
            None,
        ]
        raised = False
        try:
            gen._fill_administrative_activities(entries)
        except AttributeError:
            raised = True
        assert raised, "a malformed entry must still raise, not be swallowed"

        # Never cleared: the table is byte-for-byte what it was before the
        # call, sentinel row included -- not cleared with a partial render.
        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == rows_before


class TestSectionOBehaviorPinned:
    def test_leadership_rows_unchanged_by_the_shared_parser(self):
        # O's outputs for the same seven shapes, exactly as before #572: role
        # titles folded into the activity text, institution column empty.
        gen = _generator()
        gen._fill_leadership([_multiline_entry("O")])

        assert _rows_after(gen, "INSTITUTIONAL LEADERSHIP") == [
            ("Curriculum Committee (Chair)", "", "1999-2010"),
            ("Curriculum Committee (Chair)", "", "1999-2010"),
            ("Executive Committee (Vice Chair; Chair)", "", "2008-2010"),
            ("Faculty Council", "", "1996-Present"),
            ("Admissions Committee", "", "1999-2010"),
            ("Promotions Committee", "", "2015-2020"),
            ("Research Advisory Board", "", ""),
        ]

    def test_bare_parenthetical_line_adds_no_blank_row(self):
        # A line that is only a parenthetical with no role text, e.g.
        # "(2010-2013)", parses to activity='' and roles=(). P already skips
        # this (added by #572); O gained the same guard on review (#625) --
        # it shares the same parser output and would otherwise add a row with
        # nothing in it but a date.
        gen = _generator()
        entry = {
            "text": "Real Committee (Chair 1999-2010)\n"
                    "(2010-2013)\n"
                    "Another Committee (Member 2015-2020)",
            "extracted_fields": {},
            "taxonomy_code": "O",
        }
        gen._fill_leadership([entry])

        assert _rows_after(gen, "INSTITUTIONAL LEADERSHIP") == [
            ("Real Committee (Chair)", "", "1999-2010"),
            ("Another Committee (Member)", "", "2015-2020"),
        ]


class _NoClinicalTableGenerator(WCMTemplateGenerator):
    """Force every clinical subsection onto its bullet fallback path."""

    def _find_table_after_paragraph(self, para_idx):
        return None


def _list_paragraphs_after(gen, header_text, count):
    idx = gen._find_paragraph_with_text(header_text)
    assert idx is not None
    return gen.doc.paragraphs[idx + 1: idx + 1 + count]


def _has_numbering(para):
    pPr = para._p.pPr
    return pPr is not None and pPr.find(qn("w:numPr")) is not None


def _soft_breaks(para):
    return len(para._p.findall(f".//{qn('w:br')}"))


class _FundingTableGenerator(WCMTemplateGenerator):
    """Force the L2 table lookup to return a funding table -- the shape the
    Award Source/Funding header check exists to reject (#625 review)."""

    def _find_table_after_paragraph(self, para_idx):
        if not hasattr(self, '_funding_table'):
            self._funding_table = self.doc.add_table(rows=1, cols=2)
            self._funding_table.rows[0].cells[0].text = "Award Source"
        return self._funding_table


class TestL2FallbackMatchesL1AndL3:
    THREE_LINES = (
        "Developed a new triage protocol\n"
        "Piloted it at NYP Weill Cornell\n"
        "Published outcomes 2023"
    )

    def test_multiline_l2_entry_renders_one_bullet_per_line(self):
        gen = _generator(_NoClinicalTableGenerator)
        entry = {"text": self.THREE_LINES, "extracted_fields": {}, "taxonomy_code": "L2"}
        gen._fill_clinical_practice({"L2": [entry]})

        # add_blank_before inserts one spacer, then three list paragraphs
        paras = _list_paragraphs_after(gen, "Clinical Innovations", 4)
        bullets = [p for p in paras if _has_numbering(p)]
        assert [p.text for p in bullets] == [
            "Developed a new triage protocol",
            "Piloted it at NYP Weill Cornell",
            "Published outcomes 2023",
        ]
        # No line hides inside a soft break: each is its own list item
        assert all(_soft_breaks(p) == 0 for p in bullets)

    def test_l2_rejects_a_funding_table_and_falls_back_to_bullets(self):
        gen = _generator(_FundingTableGenerator)
        entry = {"text": "Piloted a new triage protocol", "extracted_fields": {}, "taxonomy_code": "L2"}
        gen._fill_clinical_practice({"L2": [entry]})

        # Rejected, not populated: the forced funding table keeps its one row
        assert len(gen._funding_table.rows) == 1
        paras = _list_paragraphs_after(gen, "Clinical Innovations", 2)
        bullets = [p for p in paras if _has_numbering(p)]
        assert [p.text for p in bullets] == ["Piloted a new triage protocol"]

    def test_l2_fallback_renders_exactly_like_l1(self):
        # The convergence claim itself: identical entries through the L1 and
        # the L2 fallback produce the same paragraph sequence (text, list
        # markup, soft breaks). Under the old L2 path the first entry was one
        # paragraph with embedded <w:br/> and the second entry's insertion
        # index was off by two.
        first = {"text": self.THREE_LINES, "extracted_fields": {}}
        second = {"text": "Second entry", "extracted_fields": {}}

        gen_l1 = _generator(_NoClinicalTableGenerator)
        gen_l1._fill_clinical_practice({"L1": [dict(first), dict(second)]})
        gen_l2 = _generator(_NoClinicalTableGenerator)
        gen_l2._fill_clinical_practice({"L2": [dict(first), dict(second)]})

        def shape(gen, section_idx):
            assert section_idx is not None
            paras = gen.doc.paragraphs[section_idx + 1: section_idx + 6]
            return [(p.text, _has_numbering(p), _soft_breaks(p)) for p in paras]

        # Locate each subsection the way its own filler does
        l1_idx = gen_l1._find_paragraph_exact("Clinical Practice")
        l2_idx = gen_l2._find_paragraph_with_text("Clinical Innovations")
        assert shape(gen_l2, l2_idx) == shape(gen_l1, l1_idx)


class TestCitationWritersShareOneSplit:
    CITATION = "Wende ME, Smith J. A study of things. J Things. 2023;1:1-9."

    def test_split_prefers_target_name(self):
        before, bold, after = _citation_author_split(self.CITATION, "Smith J", "Nobody")
        assert (before, bold, after) == ("Wende ME, ", "Smith J", ". A study of things. J Things. 2023;1:1-9.")

    def test_split_falls_back_to_owner_last_name_and_strips_punctuation(self):
        before, bold, after = _citation_author_split(self.CITATION, None, "wende")
        assert bold == "Wende ME"
        assert before == ""
        assert after == ", Smith J. A study of things. J Things. 2023;1:1-9."

    def test_split_returns_whole_citation_when_nothing_matches(self):
        before, bold, after = _citation_author_split(self.CITATION, "Nobody Q", "Jones")
        assert (before, bold, after) == (self.CITATION, "", "")

    def test_split_matches_target_name_as_a_substring_not_a_word(self):
        # Pinned, not endorsed (#625 review): target_name is matched via `in`,
        # so a short target_name can match inside a longer surname. Both
        # writers shared this behavior before #572; the shared function keeps
        # it byte-identical rather than tightening it here.
        citation = "Wuertz K, Smith J. A study of things. J Things. 2023;1:1-9."
        before, bold, after = _citation_author_split(citation, "Wu", "Nobody")
        assert (before, bold, after) == (
            "", "Wu", "ertz K, Smith J. A study of things. J Things. 2023;1:1-9.")

    def test_split_falls_back_to_owner_last_name_for_a_hyphenated_surname(self):
        citation = "Alvarez-Diaz M, Smith J. A study of things. J Things. 2023;1:1-9."
        before, bold, after = _citation_author_split(citation, None, "Alvarez-Diaz")
        assert (before, bold, after) == (
            "", "Alvarez-Diaz M", ", Smith J. A study of things. J Things. 2023;1:1-9.")

    def test_split_falls_back_case_insensitively_to_owner_last_name(self):
        citation = "WENDE ME, Smith J. A study of things. J Things. 2023;1:1-9."
        before, bold, after = _citation_author_split(citation, None, "wende")
        assert (before, bold, after) == (
            "", "WENDE ME", ", Smith J. A study of things. J Things. 2023;1:1-9.")

    def test_plain_writer_bolds_through_the_shared_split(self):
        gen = _generator()
        para = gen.doc.paragraphs[0].insert_paragraph_before("")
        gen._add_citation_with_bold_author(para, self.CITATION, None, "Wende")

        runs = [(r.text, bool(r.bold)) for r in para.runs]
        assert runs == [
            ("Wende ME", True),
            (", Smith J. A study of things. J Things. 2023;1:1-9.", False),
        ]

    def test_plain_writer_no_match_emits_single_unbolded_run(self):
        # No-match on the wire: the whole citation stays one unbolded run and
        # the bolded-name counter does not move.
        gen = _generator()
        bolded_before = gen.stats['target_names_bolded']
        para = gen.doc.paragraphs[0].insert_paragraph_before("")
        gen._add_citation_with_bold_author(para, self.CITATION, "Nobody Q", "Jones")

        assert [(r.text, bool(r.bold)) for r in para.runs] == [(self.CITATION, False)]
        assert gen.stats['target_names_bolded'] == bolded_before

    def test_insertion_writer_produces_the_same_segmentation(self):
        gen = _generator()
        assert gen.emit_track_changes
        para = gen.doc.paragraphs[0].insert_paragraph_before("")
        gen._add_citation_with_bold_author_as_insertion(para, self.CITATION, None, "Wende")

        ins = para._p.find(qn("w:ins"))
        assert ins is not None, "tracked insertion was not emitted"
        segments = []
        for run in ins.findall(qn("w:r")):
            text = "".join(t.text or "" for t in run.findall(qn("w:t")))
            bold = run.find(f"{qn('w:rPr')}/{qn('w:b')}") is not None
            segments.append((text, bold))
        assert segments == [
            ("Wende ME", True),
            (", Smith J. A study of things. J Things. 2023;1:1-9.", False),
        ]


class TestBoardCertClassifierConvergence:
    def test_classifier_rules(self):
        assert _classify_cert_token("1999") == "year"
        assert _classify_cert_token("123-456") == "cert_number"
        assert _classify_cert_token("MOC 2015") == "year"
        assert _classify_cert_token("Internal Medicine") == "specialty"
        # digit-led but neither year- nor number-shaped: dropped
        assert _classify_cert_token("19xx") is None
        assert _classify_cert_token("") is None

    def test_module_constants_are_the_two_shared_regexes(self):
        assert YEAR_PATTERN.match("2004") and not YEAR_PATTERN.match("204")
        assert CERTIFICATE_NUMBER_PATTERN.match("12-34") and not CERTIFICATE_NUMBER_PATTERN.match("12a")

    def test_pipe_and_bare_line_board_cert_formats_render_identical_rows(self):
        gen = _generator()
        # Same tokens once pipe-separated, once one per line; the parser must
        # rebuild the same two rows either way.
        pipe_entry = {
            "text": "Name of Specialty | Board Certificate # | Date of Certification\n"
                    "Internal Medicine | 123-456 | 1999\n"
                    "Cardiology | 789-012 | 2005",
            "extracted_fields": {},
        }
        gen._fill_board_certification([pipe_entry])
        rows_pipe = _rows_after_board(gen)

        gen2 = _generator()
        bare_entry = {
            "text": "Internal Medicine\nCardiology\n123-456\n789-012\n1999\n2005",
            "extracted_fields": {},
        }
        gen2._fill_board_certification([bare_entry])
        rows_bare = _rows_after_board(gen2)

        assert rows_pipe == rows_bare == [
            ("Internal Medicine", "123-456", "1999"),
            ("Cardiology", "789-012", "2005"),
        ]


def _rows_after_board(gen):
    section_idx = None
    for i, para in enumerate(gen.doc.paragraphs):
        text = para.text.strip()
        if text == "Board Certification" or text.startswith("Board Certification:"):
            section_idx = i
            break
    assert section_idx is not None, "template lost its Board Certification header"
    table = gen._find_table_after_paragraph(section_idx)
    assert table is not None
    return [tuple(cell.text for cell in row.cells) for row in table.rows[1:]]


# --- Appended 2026-08-25: PR #625 review, threads 3850063781 and 3851117744
# (both reopened by the reviewer after a wrong #567 attribution / a "still
# want it in this PR" pushback). `pytest` is imported here, at the tail of
# the file, instead of being promoted into the top-of-file import block, to
# avoid a line-level collision with a parallel edit to that block.
import pytest  # noqa: E402


class TestExtractedFieldsMappingGuard:
    """Thread 3850063781: the reviewer's parametrized ask, misattributed in
    our earlier reply to #567 (which is about a typed-record replacement, not
    this). The real fix already landed in 5978252 -- an isinstance(Mapping)
    guard in both _fill_administrative_activities and the shared sorter,
    replacing a bare `entry.get('extracted_fields', {}) or {}` that let a
    truthy non-dict (a string, an int) through to raise AttributeError on the
    first .get() call. That commit's own test only covers a stray list; this
    sweeps the reviewer's full parametrize list -- None, [], "", "invalid",
    123 -- against the same guard.
    """

    @pytest.mark.parametrize(
        "extracted_fields",
        [None, [], "", "invalid", 123],
    )
    def test_non_mapping_extracted_fields_render_instead_of_raising(self, extracted_fields):
        gen = _generator()
        entry = {
            "taxonomy_code": "P",
            "text": "Quality Committee",
            "extracted_fields": extracted_fields,
        }
        gen._fill_administrative_activities([entry])

        # No dates, no parenthetical, one line of raw text: the guard drops
        # extracted_fields to {} and the row falls back to the raw activity
        # text with an empty Role/Dates, same as any entry with no fields.
        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Quality Committee", "", ""),
        ]


class TestInsertionWriterNeverLosesTheCitation:
    """Thread 3851117744: the two production paths in
    _add_citation_with_bold_author_as_insertion() that fall back to the plain
    writer instead of building w:ins -- disabled track changes, and a raise
    partway through XML construction. 65ad64d added three shared-split
    author-matching tests but neither of these; the reviewer asked again
    specifically for these two."""

    CITATION = TestCitationWritersShareOneSplit.CITATION

    def test_disabled_track_changes_skips_ins_and_renders_plain(self):
        # Issue #153's documented contract: emit_track_changes == False must
        # never reach the w:ins builder -- it should render exactly like
        # _add_citation_with_bold_author.
        gen = _generator()
        gen.emit_track_changes = False
        added_before = gen.stats['track_changes_added']
        para = gen.doc.paragraphs[0].insert_paragraph_before("")
        gen._add_citation_with_bold_author_as_insertion(para, self.CITATION, None, "Wende")

        assert para._p.find(qn("w:ins")) is None
        assert [(r.text, bool(r.bold)) for r in para.runs] == [
            ("Wende ME", True),
            (", Smith J. A study of things. J Things. 2023;1:1-9.", False),
        ]
        assert gen.stats['track_changes_added'] == added_before

    def test_xml_construction_failure_falls_back_to_plain_citation(self, monkeypatch):
        # bibliography.py's own module docstring promises an enrichment can
        # never cost the citation itself. Force the w:ins build to raise
        # partway through (patching the one XML-element constructor it calls,
        # not the module under test) and assert the citation still lands as
        # plain runs instead of the paragraph coming up empty or the call
        # propagating the exception.
        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated XML construction failure")

        monkeypatch.setattr(
            "unified_pipeline.stage6.sections.bibliography.OxmlElement", _boom)

        gen = _generator()
        assert gen.emit_track_changes
        para = gen.doc.paragraphs[0].insert_paragraph_before("")
        gen._add_citation_with_bold_author_as_insertion(para, self.CITATION, None, "Wende")

        assert para._p.find(qn("w:ins")) is None
        assert [(r.text, bool(r.bold)) for r in para.runs] == [
            ("Wende ME", True),
            (", Smith J. A study of things. J Things. 2023;1:1-9.", False),
        ]


# --- Appended 2026-08-25: PR #625 review, thread 3843817401 (#665). `logging`
# is imported here, at the tail of the file, rather than promoted into the
# top-of-file import block, for the same reason as `pytest` above -- avoiding
# a line-level collision with a parallel edit to that block.
import logging  # noqa: E402


class TestOrphanedDateAlignmentValidatesBeforePairing:
    """Thread 3843817401 (#665): the forward pairing between `dates_pool` and
    date-less items assumes the two runs are the same length because they
    come from the same flattened table. When extraction drops or inserts a
    line -- an extra header, a split row -- that assumption breaks and
    positional pairing shifts a real date onto the wrong activity. A wrong
    date is worse than no date: it renders with the same confident look as a
    correct one, so nothing downstream can tell the two apart.

    _parse_flattened_committee_lines now only pairs positionally when the
    counts agree; on a mismatch every affected item stays dateless (a blank
    Dates cell -- a visible gap, not a silent wrong answer) and a warning
    names the mismatch. Exercised through P's real fill method, same as the
    rest of this file, so a regression here fails against the actual
    document output, not just the parser's return value.
    """

    LOGGER_NAME = "unified_pipeline.stage6.parsing.text"

    def test_matching_counts_pair_every_activity_exactly_as_before(self):
        # No regression: when the two runs are the same length, every
        # activity still gets its own date, forward, positionally.
        gen = _generator()
        entry = {
            "text": "Alpha Committee\nBeta Committee\nGamma Committee\n"
                    "2001-2002\n2003-2004\n2005-2006",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Alpha Committee", "", "2001-2002"),
            ("Beta Committee", "", "2003-2004"),
            ("Gamma Committee", "", "2005-2006"),
        ]

    def test_more_dates_than_undated_activities_assigns_none_and_logs(self, caplog):
        # 2 undated activities, 3 orphaned dates: no way to know which date
        # belongs to which committee (or which date is the extra one), so
        # none get assigned rather than guessing.
        gen = _generator()
        entry = {
            "text": "Alpha Committee\nBeta Committee\n"
                    "2001-2002\n2003-2004\n2005-2006",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Alpha Committee", "", ""),
            ("Beta Committee", "", ""),
        ]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("3" in r.getMessage() and "2" in r.getMessage() for r in warnings), (
            f"expected a warning naming both counts (3 dates, 2 activities), got: "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_fewer_dates_than_undated_activities_assigns_none_and_logs(self, caplog):
        # 3 undated activities, 2 orphaned dates: same disagreement, the
        # other direction.
        gen = _generator()
        entry = {
            "text": "Alpha Committee\nBeta Committee\nGamma Committee\n"
                    "2001-2002\n2003-2004",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            gen._fill_administrative_activities([entry])

        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Alpha Committee", "", ""),
            ("Beta Committee", "", ""),
            ("Gamma Committee", "", ""),
        ]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("2" in r.getMessage() and "3" in r.getMessage() for r in warnings), (
            f"expected a warning naming both counts (2 dates, 3 activities), got: "
            f"{[r.getMessage() for r in warnings]}"
        )

    def test_extra_line_mid_list_never_shifts_a_date_onto_the_wrong_activity(self, caplog):
        # His actual failure scenario: an extra line lands between two
        # committee names, ahead of the date run. Pre-fix, forward pairing
        # would hand Alpha's real date to the extra line and starve Beta of
        # its own date entirely -- silently, with no sign anything went
        # wrong. Post-fix, the count mismatch (3 undated activities, 2
        # dates) means nobody gets a date instead of somebody getting the
        # wrong one.
        gen = _generator()
        entry = {
            "text": "Alpha Committee\nSubcommittee Notes\nBeta Committee\n"
                    "1999-2010\n2005-2008",
            "extracted_fields": {},
            "taxonomy_code": "P",
        }
        with caplog.at_level(logging.WARNING, logger=self.LOGGER_NAME):
            gen._fill_administrative_activities([entry])

        rows = _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE")
        assert rows == [
            ("Alpha Committee", "", ""),
            ("Subcommittee Notes", "", ""),
            ("Beta Committee", "", ""),
        ]
        # The specific failure this thread named: 1999-2010 must not land on
        # "Subcommittee Notes", and Beta must not come up empty while an
        # unrelated line holds a date instead.
        dates_by_activity = {activity: dates for activity, _role, dates in rows}
        assert dates_by_activity["Subcommittee Notes"] != "1999-2010"
        assert all(dates == "" for dates in dates_by_activity.values())
        assert caplog.records, "counts disagree -- the mismatch must be logged"


# --- Appended 2026-08-26: PR #625 review, follow-up on the #256 crash guard.
# The reviewer asked for "a focused wire-level regression test with multiple
# record dictionaries, including list-valued committee fields, and an
# assertion that each record produces its own Word row" to pin the
# production rendering behavior of the record_list expansion in
# administrative_activities.py -- structured/list-valued records must be
# normalized to plain strings before they reach python-docx, not merged into
# one row or handed to `cell.text` unnormalized.


class TestRecordListBurstNormalizesBeforePythonDocx:
    """#256: a stage-4 multi-committee entry can arrive as a LIST of record
    dicts under committee_name/committee/activity (#208/#248 fusion). Before
    the fix, that list went into a single Word cell whole and python-docx
    raised deep in the XML layer, aborting the entire document -- not just
    this section. `_first_committee_alias(as_list=True)` detects the burst
    and expands it to one row per record; `_CommitteeRecord.from_raw` (and
    `_add_committee_row`'s own final safeguard) run every field through
    `_committee_cell_text`, which is the thing that must never see a
    list/dict reach `cell.text` unconverted.

    This entry deliberately exercises all three alias keys the docstring
    names (committee_name, committee, activity) as the field carrying the
    LIST-OF-RECORDS burst, and separately exercises list- and dict-valued
    fields *within* one record (a record's own committee_name or role
    arriving as a list, and activity arriving as a nested dict) -- the two
    other structured shapes `_committee_cell_text` has to cover, not just
    the top-level burst shape.
    """

    def test_multi_record_committee_burst_each_becomes_its_own_row_with_str_cells(self):
        gen = _generator()
        entry = {
            "text": "irrelevant -- a structured record burst, not flattened text",
            "extracted_fields": {
                "committee_name": [
                    {
                        # Baseline record: every field a plain scalar.
                        "committee_name": "Curriculum Committee",
                        "role": "Chair",
                        "start_date": "2010",
                        "end_date": "2012",
                    },
                    {
                        # #256's exact crash shape: a LIST reaching what used
                        # to be treated as a single string field, on both
                        # the activity alias and the role field at once.
                        "committee_name": ["Executive Committee", "Executive Board"],
                        "role": ["Vice Chair", "Secretary"],
                        "start_date": "2013",
                        "end_date": "2014",
                    },
                    {
                        # The second alias in precedence order, itself
                        # list-valued.
                        "committee": ["IRB", "IACUC"],
                        "start_date": "2015",
                        "end_date": "2016",
                    },
                    {
                        # The third alias, a DICT-valued field rather than a
                        # list -- the other structured shape stage 4 emits.
                        "activity": {"committee_name": "Nested Task Force"},
                        "start_date": "2017",
                        "end_date": "2018",
                    },
                ]
            },
            "taxonomy_code": "P",
        }

        gen._fill_administrative_activities([entry])

        section_idx = gen._find_paragraph_with_text("INSTITUTIONAL ADMINISTRATIVE")
        table = gen._find_table_after_paragraph(section_idx)
        data_rows = table.rows[1:]

        # (a) each of the four record dictionaries produced its OWN row --
        # not one row merging the whole burst, and no exception mid-render.
        # This exact-text compare is the operative pin: in #256's
        # python-docx, `cell.text = <list-or-dict>` raised deep in the XML
        # layer and aborted the whole document. The currently-installed
        # python-docx instead fails SILENTLY on the same inputs -- a list
        # assigned to `cell.text` has its elements concatenated with no
        # separator, and a dict assigned to it is reduced to just its own
        # key name, with the value lost. Removing the `_committee_cell_text`
        # normalization reproduces that silent mis-render here: row 2 becomes
        # ("Executive CommitteeExecutive Board", "Vice ChairSecretary", ...)
        # instead of the semicolon-joined values below, and row 4's first
        # cell becomes "committee_name" (the dict's key) instead of "Nested
        # Task Force" -- so this compare, not a type check on `cell.text`
        # (which python-docx's getter always returns as `str` regardless of
        # what was assigned), is what catches the reversion.
        assert len(data_rows) == 4
        assert _rows_after(gen, "INSTITUTIONAL ADMINISTRATIVE") == [
            ("Curriculum Committee", "Chair", "2010-2012"),
            ("Executive Committee; Executive Board", "Vice Chair; Secretary", "2013-2014"),
            ("IRB; IACUC", "", "2015-2016"),
            ("Nested Task Force", "", "2017-2018"),
        ]

        assert gen.stats['entries_inserted'] == 4
