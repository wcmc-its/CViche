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

    def test_pipe_and_bare_lines_classify_identically_on_the_wire(self):
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
