"""PR #625 review round 2: board_certification.py, 7 threads (2026-08-25).

The reviewer overruled our earlier "pre-existing, deferred to #663" replies
and asked for each of these fixed in this PR, kept local to
`board_certification.py`. One test class per thread; thread ids are in each
class docstring so a reader can match a test back to the review comment.

`TestBlankRowGuardIsUniformAcrossColumnBranches` (2026-09-01) is a later
addition, not one of the 7 threads: it closes #663 item 8, the one item from
that issue this PR takes -- see that class's docstring.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_board_certification_round2.py -p no:cacheprovider
"""
import logging
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
from unified_pipeline.stage6.sections.board_certification import (  # noqa: E402
    CERTIFICATE_NUMBER_PATTERN,
    _classify_cert_token,
    _is_certification_header_line,
    _is_fused_certification,
    _reconstruct_certification_rows,
    _split_multi,
)


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _rows_after_board(gen):
    """Data rows (as text tuples) of the table under the Board Certification header."""
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


def _board_table(gen):
    """The table under the Board Certification header, or None."""
    section_idx = None
    for i, para in enumerate(gen.doc.paragraphs):
        text = para.text.strip()
        if text == "Board Certification" or text.startswith("Board Certification:"):
            section_idx = i
            break
    assert section_idx is not None, "template lost its Board Certification header"
    return gen._find_table_after_paragraph(section_idx)


def _cell_wt_texts(row):
    """Each cell's text in `row`, read from the raw `w:t` runs directly
    rather than python-docx's computed `cell.text` (#708: the assertion this
    feeds must hold on the actual XML, not on whatever python-docx happens
    to reconstruct). Paragraphs within a cell are joined with '\\n', same as
    `cell.text` does, since the real template's own header cells carry more
    than one `w:p` (e.g. "Certificate # " / "(indicate if board eligible)")."""
    return [
        '\n'.join(
            ''.join(t.text or '' for t in p.iter(qn('w:t')))
            for p in tc.findall(qn('w:p'))
        )
        for tc in row._tr.findall(qn('w:tc'))
    ]


class TestCertificateNumberPatternRejectsMalformed:
    """Thread 3850174961: '-', '--', '123-', '-123' were accepted as certificate numbers."""

    def test_named_malformed_values_are_rejected(self):
        for bad in ["-", "--", "123-", "-123"]:
            assert not CERTIFICATE_NUMBER_PATTERN.match(bad), bad
            # Not just "not a cert number" -- not misclassified as a
            # specialty either (see _HAS_LETTER); a punctuation-only token
            # carries no real data of any kind.
            assert _classify_cert_token(bad) is None, bad

    def test_realistic_certificate_numbers_still_classify(self):
        # Still-renders proof for the tightened grammar: real WCM certificate
        # numbers (a plain number, or hyphen-joined groups) are unaffected.
        assert CERTIFICATE_NUMBER_PATTERN.match("123456")
        assert CERTIFICATE_NUMBER_PATTERN.match("123-456")
        assert _classify_cert_token("123-456") == "cert_number"


class TestMocTokenIsAnchoredAndCaseInsensitive:
    """Thread 3850184512: unanchored, case-sensitive 'MOC' in token substring check."""

    def test_named_misclassification_examples_are_not_years(self):
        assert _classify_cert_token("NONMOC") != "year"
        assert _classify_cert_token("Board MOC Status") != "year"

    def test_real_moc_tokens_still_classify_as_year(self):
        # Still-renders proof: the documented/tested MOC shape keeps working,
        # including lowercase (the case-sensitivity half of the same defect).
        assert _classify_cert_token("MOC 2015") == "year"
        assert _classify_cert_token("moc 2015") == "year"
        assert _classify_cert_token("MOC") == "year"


class TestHeaderLineFilteringIsExactNotSubstring:
    """Thread 3850468238: substring header-keyword filtering could drop real data."""

    def test_real_header_row_is_still_dropped(self):
        assert _is_certification_header_line(
            "Name of Specialty | Board Certificate # | Date of Certification"
        )

    def test_data_line_merely_mentioning_a_header_phrase_is_kept(self):
        # Old substring check: 'date of certification' in line.lower() was
        # True for this line even though it is real specialty content, not a
        # header -- it would have been silently dropped.
        line = "Sports Medicine, awaiting Date of Certification confirmation | 456-789 | 2021"
        assert not _is_certification_header_line(line)

    def test_still_renders_end_to_end(self):
        gen = _generator()
        entry = {
            "text": "Name of Specialty | Board Certificate # | Date of Certification\n"
                    "Sports Medicine, awaiting Date of Certification confirmation | 456-789 | 2021",
            "extracted_fields": {},
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("Sports Medicine, awaiting Date of Certification confirmation", "456-789", "2021"),
        ]


class TestIsFusedCertificationConsidersFullStructure:
    """Threads 3850165177 / 3850459808: fusion decided by cert-number count alone."""

    def test_predicate_true_from_certifying_board_alone(self):
        # Two boards, but only one certificate number extracted -- the old
        # `len(cert_numbers) > 1` check alone would say "not fused".
        fields = {"certifying_board": "Board A, Board B", "certificate_number": "111"}
        assert _is_fused_certification(fields, ["111"]) is True

    def test_predicate_false_for_a_genuine_single_certification(self):
        fields = {"certifying_board": "American Board of Internal Medicine", "certificate_number": "111"}
        assert _is_fused_certification(fields, ["111"]) is False

    def test_multi_board_single_cert_number_renders_as_two_rows_not_one(self):
        # End-to-end: this is exactly the entry shape the reviewer named --
        # multiple specialties, only one extracted certificate number.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Board A, Board B",
                "certificate_number": "111",
                "year_certified": "2010",
            },
            "text": "Board A | 111 | 2010\nBoard B | 222 | 2012",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("Board A", "111", "2010"),
            ("Board B", "222", "2012"),
        ]

    def test_break_predicate_back_to_cert_number_count_only_goes_red(self):
        # Rule 6.2 ablation for the fix above: reverting the dispatch to the
        # old `len(cert_numbers) > 1` behaviour must produce ONE flattened
        # row instead of two.
        import unified_pipeline.stage6.sections.board_certification as bc

        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Board A, Board B",
                "certificate_number": "111",
                "year_certified": "2010",
            },
            "text": "Board A | 111 | 2010\nBoard B | 222 | 2012",
        }

        original = bc._is_fused_certification
        try:
            bc._is_fused_certification = lambda fields, cert_numbers: len(cert_numbers) > 1
            gen._fill_board_certification([entry])
            rows = _rows_after_board(gen)
            assert rows != [("Board A", "111", "2010"), ("Board B", "222", "2012")], (
                "expected the old cert-number-only dispatch to mis-render this "
                f"as one flattened row, got {rows!r}"
            )
            assert len(rows) == 1
        finally:
            bc._is_fused_certification = original


class TestReconstructionFallsBackWithoutDroppingRows:
    """Thread 3850165177: positional specialties[i]/cert_numbers[i]/years[i] pairing.

    HARD SAFETY GATE: unequal token counts must not drop a row. These pin
    the reconstruction directly (unit level) and through the real fill
    method (wire level), then verify the wire test actually depends on the
    reconstruction still rendering every row.
    """

    def test_unequal_lengths_pair_positionally_without_dropping(self):
        rows = _reconstruct_certification_rows(
            ["Internal Medicine", "Cardiology", "Dermatology"], ["111"], ["2010"],
        )
        assert len(rows) == 3
        assert rows == [
            ("Internal Medicine", "111", "2010"),
            ("Cardiology", "", "2010"),
            ("Dermatology", "", "2010"),
        ]

    def test_orphaned_date_does_not_get_dropped_or_misassigned(self):
        # Two specialties/cert numbers, three years -- one year is orphaned.
        rows = _reconstruct_certification_rows(
            ["Internal Medicine", "Cardiology"], ["111", "222"], ["1999", "2005", "2010"],
        )
        assert len(rows) == 2
        assert rows[0] == ("Internal Medicine", "111", "1999")
        assert rows[1] == ("Cardiology", "222", "2005")

    def test_warns_when_counts_disagree(self, caplog):
        with caplog.at_level(logging.WARNING):
            _reconstruct_certification_rows(["A", "B"], ["1"], [])
        assert any("low confidence" in r.message for r in caplog.records)

    def test_does_not_warn_when_counts_agree(self, caplog):
        with caplog.at_level(logging.WARNING):
            _reconstruct_certification_rows(["A"], ["1"], ["1999"])
        assert not any("low confidence" in r.message for r in caplog.records)

    def test_realistic_fused_certification_with_a_malformed_cert_number_still_renders_every_row(self):
        # Still-renders proof combining threads 3850174961 and 3850165177:
        # a realistic two-row fused certification where the second row's
        # certificate number is one of the now-rejected malformed values.
        # Both specialties must still render a row.
        gen = _generator()
        entry = {
            "extracted_fields": {},
            "text": "Internal Medicine | 123-456 | 1999\nCardiology | - | 2005",
        }
        gen._fill_board_certification([entry])
        rows = _rows_after_board(gen)
        assert len(rows) == 2
        assert rows[0] == ("Internal Medicine", "123-456", "1999")
        # Cardiology's malformed "-" certificate number is dropped, not
        # rendered as data -- but the Cardiology row itself is not dropped.
        assert rows[1] == ("Cardiology", "", "2005")

    def test_break_reconstruction_to_drop_mismatched_rows_goes_red(self):
        # Rule 6.2 ablation: a reconstruction that refuses to render on a
        # count mismatch (instead of falling back) must fail this test.
        import unified_pipeline.stage6.sections.board_certification as bc

        def _dropping_reconstruction(specialties, cert_numbers, years):
            if len(specialties) != len(cert_numbers):
                return []
            return list(zip(specialties, cert_numbers, years or [''] * len(specialties)))

        original = bc._reconstruct_certification_rows
        try:
            bc._reconstruct_certification_rows = _dropping_reconstruction
            gen = _generator()
            entry = {
                "extracted_fields": {},
                "text": "Internal Medicine | 123-456 | 1999\nCardiology | - | 2005",
            }
            gen._fill_board_certification([entry])
            rows = _rows_after_board(gen)
            assert rows == [], f"expected the dropping reconstruction to lose both rows, got {rows!r}"
        finally:
            bc._reconstruct_certification_rows = original


class TestSortingMatchesSiblingSections:
    """Thread 3850478580: board certification did not sort entries before rendering.

    Risk: this changes rendered row order for any entry that carries a
    start_date/end_date (most real F2 entries carry only year_certified /
    recertification_date, which the shared sorter does not read -- see the
    comment at the call site -- so those keep their original relative order;
    this test uses start_date/end_date so the reorder is actually exercised).
    """

    def test_entries_with_dates_render_most_recent_first(self):
        gen = _generator()
        entries = [
            {
                "extracted_fields": {
                    "certifying_board": "Older Board", "certificate_number": "111",
                    "start_date": "2005", "end_date": "2005",
                },
                "text": "",
            },
            {
                "extracted_fields": {
                    "certifying_board": "Newer Board", "certificate_number": "222",
                    "start_date": "2020", "end_date": "2020",
                },
                "text": "",
            },
        ]
        gen._fill_board_certification(entries)
        assert _rows_after_board(gen) == [
            ("Newer Board", "222", "2020"),
            ("Older Board", "111", "2005"),
        ]


class TestSortingReadsF2DateFields:
    """Follow-up to thread 3850478580: the shared sorter didn't know F2's
    actual date fields (`year_certified` / `recertification_date`), so real
    board-certification entries -- which carry neither `start_date` nor
    `end_date` -- all keyed to (0, 0, 0) and the sort above was decorative on
    real data. See the comment at chronological.py's date_candidates list.
    """

    def test_year_certified_alone_sorts_most_recent_first(self):
        # (a) Two F2 entries with only `year_certified` (no start_date/end_date
        # at all, matching the real F2 schema) must now sort by it.
        gen = _generator()
        entries = [
            {
                "extracted_fields": {
                    "certifying_board": "Older Board", "certificate_number": "111",
                    "year_certified": "2005",
                },
                "text": "",
            },
            {
                "extracted_fields": {
                    "certifying_board": "Newer Board", "certificate_number": "222",
                    "year_certified": "2020",
                },
                "text": "",
            },
        ]
        gen._fill_board_certification(entries)
        assert _rows_after_board(gen) == [
            ("Newer Board", "222", "2020"),
            ("Older Board", "111", "2005"),
        ]

    def test_year_certified_ablation_goes_red_without_the_field(self):
        # Rule 6.2 ablation for (a): with `year_certified` removed from the
        # candidate list, both entries above key to (0, 0, 0) and Python's
        # stable sort preserves input order -- "Older Board" first, not
        # "Newer Board" -- so this must fail without the fix.
        import unified_pipeline.stage6.sorting.chronological as chrono

        original = chrono.extract_sort_date

        def _without_year_certified(entry):
            fields = entry.get('extracted_fields') or {}
            fields = dict(fields)
            fields.pop('year_certified', None)
            stripped = dict(entry)
            stripped['extracted_fields'] = fields
            return original(stripped)

        chrono.extract_sort_date = _without_year_certified
        try:
            gen = _generator()
            entries = [
                {
                    "extracted_fields": {
                        "certifying_board": "Older Board", "certificate_number": "111",
                        "year_certified": "2005",
                    },
                    "text": "",
                },
                {
                    "extracted_fields": {
                        "certifying_board": "Newer Board", "certificate_number": "222",
                        "year_certified": "2020",
                    },
                    "text": "",
                },
            ]
            gen._fill_board_certification(entries)
            rows = _rows_after_board(gen)
            assert rows != [
                ("Newer Board", "222", "2020"),
                ("Older Board", "111", "2005"),
            ], f"expected stripped year_certified to keep input order, got {rows!r}"
        finally:
            chrono.extract_sort_date = original

    def test_recertification_date_dominates_year_certified(self):
        # (b) An entry with both fields present sorts by whichever we decided
        # dominates: `recertification_date` (the more recent event -- a
        # certification issued in 2005 and recertified in 2020 should sort as
        # a 2020 entry, not a 2005 one) beats a same-entry-shape competitor
        # whose only date is a later `year_certified`.
        gen = _generator()
        entries = [
            {
                # Issued 2005, recertified 2020 -- should sort as "2020".
                "extracted_fields": {
                    "certifying_board": "Recertified Board", "certificate_number": "111",
                    "year_certified": "2005", "recertification_date": "2020",
                },
                "text": "",
            },
            {
                # A plain, never-recertified entry from 2012 -- should sort
                # between the two: after the recertified board's effective
                # 2020, before nothing here, so we only need it to land
                # *after* the recertified board to prove recertification_date
                # (2020) beat year_certified (2005) rather than the reverse.
                "extracted_fields": {
                    "certifying_board": "Plain Board", "certificate_number": "222",
                    "year_certified": "2012",
                },
                "text": "",
            },
        ]
        gen._fill_board_certification(entries)
        rows = _rows_after_board(gen)
        board_names = [row[0] for row in rows]
        assert board_names == ["Recertified Board", "Plain Board"], (
            "recertification_date (2020) should dominate year_certified (2005) "
            f"and outrank the plain 2012 entry, got {board_names!r}"
        )


class TestSortingRegressionGuardOtherSections:
    """(c) Regression guard: an entry shaped like a DIFFERENT section (using
    start_date/end_date, the fields those 15 sibling writers actually rely
    on) must sort exactly as it did before this change -- the two new F2
    candidates must not shift the existing candidates' precedence, since
    every non-F2 section's entries never carry `year_certified` or
    `recertification_date` (F2-only ownership, verified against
    field_schemas_v1.json and field_schemas_v1.1.json: neither field is
    declared under any other taxonomy code).
    """

    def test_non_f2_entries_sort_unchanged_by_start_end_date(self):
        from unified_pipeline.stage6.sorting.chronological import (
            sort_entries_reverse_chronological,
        )

        entries = [
            {"extracted_fields": {"label": "old", "start_date": "2001", "end_date": "2005"}},
            {"extracted_fields": {"label": "new", "start_date": "2015", "end_date": "2020"}},
            {"extracted_fields": {"label": "current", "start_date": "2021", "end_date": "Present"}},
        ]
        ordered = [e["extracted_fields"]["label"] for e in sort_entries_reverse_chronological(entries)]
        assert ordered == ["current", "new", "old"]


class TestNarrowTableSkipsRowInsteadOfBlankOrRaise:
    """Thread 3850492866: missing 1-column/0-column branch in _add_board_cert_row."""

    def test_one_column_table_skips_row_with_warning_not_blank_row(self, caplog):
        gen = _generator()
        narrow_doc = Document()
        table = narrow_doc.add_table(rows=1, cols=1)
        rows_before = len(table.rows)
        entries_before = gen.stats['entries_inserted']

        with caplog.at_level(logging.WARNING):
            gen._add_board_cert_row(table, "Internal Medicine", "123-456", "2020")

        assert len(table.rows) == rows_before, "the just-added blank row must be removed, not left behind"
        assert gen.stats['entries_inserted'] == entries_before
        assert any("too narrow" in r.message for r in caplog.records)

    def test_does_not_raise(self):
        gen = _generator()
        narrow_doc = Document()
        table = narrow_doc.add_table(rows=1, cols=0)
        # Must not raise -- a raise here would abort the rest of the document.
        gen._add_board_cert_row(table, "Internal Medicine", "123-456", "2020")


class TestFusedCertificationPredicateIgnoresBoardNameCommas:
    """DATA-LOSS GATE (2026-08-25): `_is_fused_certification` used to split
    `certifying_board` on commas to detect fusion. Real ABMS board names
    routinely contain a comma of their own ("American Board of Psychiatry
    and Neurology, Inc.", "American Board of Internal Medicine,
    Cardiovascular Disease"), so a genuine single-board entry with a comma
    in its name was misread as fusion and routed to the destructive
    text-reparse path, discarding its structured certificate_number and
    year_certified.
    """

    def test_comma_and_inc_suffix_board_name_keeps_cert_number_and_year(self):
        # The exact regressed case the gate reproduced against HEAD~1 vs
        # HEAD: HEAD~1 rendered ('American Board of Psychiatry and
        # Neurology, Inc.', '54321', '2010'); HEAD rendered ('Diplomate,
        # American Board of Psychiatry and Neurology, Inc.', '', '') -- the
        # certificate number and year were gone.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "American Board of Psychiatry and Neurology, Inc.",
                "certificate_number": "54321",
                "year_certified": "2010",
            },
            "text": "Diplomate, American Board of Psychiatry and Neurology, Inc.",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("American Board of Psychiatry and Neurology, Inc.", "54321", "2010"),
        ]

    def test_same_entry_with_empty_text_still_renders_its_row(self):
        # Same entry, `text` empty. HEAD silently omitted the entire row --
        # `_parse_and_add_multiple_certifications` returned early on empty
        # text with no log at all.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "American Board of Psychiatry and Neurology, Inc.",
                "certificate_number": "54321",
                "year_certified": "2010",
            },
            "text": "",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("American Board of Psychiatry and Neurology, Inc.", "54321", "2010"),
        ]

    def test_subspecialty_comma_renders_as_one_row_not_two(self):
        # A comma that separates a board name from its subspecialty, not a
        # second board, must not be misread as fusion either.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "American Board of Internal Medicine, Cardiovascular Disease",
                "certificate_number": "98765",
                "year_certified": "2015",
            },
            "text": "American Board of Internal Medicine, Cardiovascular Disease | 98765 | 2015",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("American Board of Internal Medicine, Cardiovascular Disease", "98765", "2015"),
        ]

    def test_genuinely_fused_entry_still_takes_the_multi_cert_path(self):
        # Proof the fix did not just disable the fused-certification
        # feature: more than one certificate number/year is still real
        # fusion evidence and still renders every row it did before the fix.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "American Board of Psychiatry and Neurology, Inc.",
                "certificate_number": "111, 222",
                "year_certified": "2010, 2012",
            },
            "text": "American Board of Psychiatry and Neurology, Inc. | 111 | 2010\n"
                    "American Board of Pediatrics | 222 | 2012",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("American Board of Psychiatry and Neurology, Inc.", "111", "2010"),
            ("American Board of Pediatrics", "222", "2012"),
        ]


class TestRealTemplateHeaderCellsAreFilteredWithoutRejectingData:
    """Defect 3 (accuracy gate, 2026-08-25): `_CERTIFICATION_HEADER_CELLS`
    matched none of the WCM template's own F2 header cells (verified via
    python-docx against
    key_files/wcm_cv_template_faculty_october_2022_final.docx). Widening the
    set must filter the real header row while still keeping a genuine data
    line -- including one that legitimately contains "board", the same word
    the real header cells contain.
    """

    def test_real_template_header_row_is_filtered(self):
        from unified_pipeline.stage6.sections.board_certification import (
            _is_certification_header_line,
        )
        assert _is_certification_header_line(
            "Full Name of Board | Certificate # \n(indicate if board eligible) "
            "| Dates of Certification \n(yyyy–yyyy)"
        )

    def test_genuine_data_line_is_still_kept(self):
        # `entry_lines` (used upstream of this filter) splits strictly on
        # "\n", so a header cell's OWN embedded line break (present in the
        # template's raw cells) already separates it into more than one
        # physical line before this function ever sees a whole row -- that
        # is a pre-existing, out-of-scope limitation of entry_lines, not
        # something this defect touches. This models the realistic case a
        # per-cell filter CAN act on: the header row flattened to one
        # physical line per cell (its own internal whitespace collapsed),
        # which `_normalize_header_cell` folds and matches.
        gen = _generator()
        entry = {
            "text": "Full Name of Board | Certificate # (indicate if board eligible) "
                    "| Dates of Certification (yyyy–yyyy)\n"
                    "American Board of Surgery | 13579 | 2018",
            "extracted_fields": {},
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("American Board of Surgery", "13579", "2018"),
        ]


class TestTheSourceTablesOwnHeaderRowIsNotACertification:
    """#829 (A5IZ6Q): an older template revision's header row "Full Name of
    Board | Certificate # | Dates of Certification" reached stage 4, which
    returned certifying_board="Full Name of Board", and the
    single-certification path rendered it as the faculty member's
    certification."""

    HEADER = "Full Name of Board | Certificate # | Dates of Certification"

    def test_a_header_row_extracted_as_fields_renders_no_row(self):
        gen = _generator()
        gen._fill_board_certification([{
            "text": self.HEADER,
            "extracted_fields": {"certifying_board": "Full Name of Board",
                                 "certificate_number": "Certificate #"},
        }])
        assert _rows_after_board(gen) == []

    def test_a_real_certification_beside_it_still_renders(self):
        gen = _generator()
        gen._fill_board_certification([
            {"text": self.HEADER,
             "extracted_fields": {"certifying_board": "Full Name of Board",
                                  "certificate_number": "Certificate #"}},
            {"text": "American Board of Fictional Medicine | 24680 | 2015",
             "extracted_fields": {"certifying_board": "American Board of Fictional Medicine",
                                  "certificate_number": "24680", "year_certified": "2015"}},
        ])
        assert [row[:2] for row in _rows_after_board(gen)] == [
            ("American Board of Fictional Medicine", "24680")]

    def test_header_fields_with_a_real_line_in_the_text_are_kept(self):
        """Header-only fields are not enough: a text line that is real data
        means stage 4 read the wrong line, not that the entry is a header."""
        from unified_pipeline.stage6.sections.board_certification import _is_header_record
        fields = {"certifying_board": "Full Name of Board", "certificate_number": "Certificate #"}
        assert not _is_header_record(
            fields, self.HEADER + "\nAmerican Board of Fictional Medicine | 24680 | 2015")
        assert _is_header_record(fields, self.HEADER)
        # stage 4 can return certificate_number as a list
        assert _is_header_record(
            {"certifying_board": "Full Name of Board", "certificate_number": ["Certificate #"]},
            self.HEADER)

    def test_older_revision_header_cells_are_header_cells(self):
        assert _is_certification_header_line(self.HEADER)
        assert not _is_certification_header_line("Certificate # 24680")


class TestRejectedTokenIsLogged:
    """Defect 4 (accuracy gate, 2026-08-25): a token like '123-' or '-'
    classifies to None with no log anywhere (§5.3/§5.10)."""

    def test_rejected_token_is_named_in_a_debug_log(self, caplog):
        with caplog.at_level(logging.DEBUG):
            assert _classify_cert_token("123-") is None
        assert any("123-" in r.message for r in caplog.records)

    def test_empty_token_is_not_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            assert _classify_cert_token("") is None
        assert not caplog.records


class TestPerRowBackfillFromStructuredFields:
    """Adversarial re-gate follow-up (2026-08-25): `_is_fused_certification`'s
    "more than one occurrence of 'board'" signal fires on real single
    certifications ("Sub-board of the American Board of Psychiatry and
    Neurology"; "American Board of X, Board Eligible" -- the template's own
    certificate column literally reads "Certificate # (indicate if board
    eligible)"). Routed to the free-text reparse, when it recovers a year
    but no certificate number the old whole-entry safety net
    (`not any(cert_num or year for _, cert_num, year in rows)`) does not
    fire, because a year WAS recovered -- so the row rendered with its
    certificate number blanked even though the entry's own structured
    fields had it.

    The fix is per-FIELD, per-ROW backfill, not a smarter predicate: a
    single reparsed row missing a certificate number or date takes it from
    the entry's structured fields when those fields supply exactly one
    unambiguous candidate. It deliberately does NOT apply across multiple
    reparsed rows -- a single structured `certificate_number` cannot be
    attributed to one row out of several without inventing which row it
    belongs to, so that case is left blank and logged instead (§5.3/§5.10).
    """

    def test_sub_board_phrase_backfills_certificate_number_from_structured_fields(self):
        # (a) Real ABPN subspecialty phrasing containing "board" twice.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Sub-board of the American Board of Psychiatry and Neurology",
                "certificate_number": "67890",
                "year_certified": "2015",
            },
            "text": "Child and Adolescent Psychiatry, Sub-board of the American Board "
                    "of Psychiatry and Neurology\n2015",
        }
        gen._fill_board_certification([entry])
        rows = _rows_after_board(gen)
        assert len(rows) == 1
        assert rows[0][1] == "67890", f"certificate number was blanked, got {rows[0]!r}"
        assert rows[0][2] == "2015"

    def test_board_eligible_phrase_backfills_certificate_number_from_structured_fields(self):
        # (b) The template's own certificate column reads "Certificate #
        # (indicate if board eligible)" -- extraction plausibly emits this.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "American Board of Surgery, Board Eligible",
                "certificate_number": "11223",
                "year_certified": "2018",
            },
            "text": "American Board of Surgery, Board Eligible\n2018",
        }
        gen._fill_board_certification([entry])
        rows = _rows_after_board(gen)
        assert len(rows) == 1
        assert rows[0][1] == "11223", f"certificate number was blanked, got {rows[0]!r}"
        assert rows[0][2] == "2018"

    def test_genuinely_fused_multi_row_entry_does_not_duplicate_certificate_number(self):
        # (c) A real fusion with its OWN multiple structured certificate
        # numbers/years reparses into two full rows, unchanged from before
        # this fix. The structured `certificate_number` is itself a
        # comma-separated pair here, so it is already ambiguous -- backfill
        # must not pick one of the two and copy it into the row that is
        # missing a token, nor drop that row.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Board A, Board B",
                "certificate_number": "111, 222",
                "year_certified": "2010, 2012",
            },
            "text": "Board A | 111 | 2010\nBoard B | 2012",
        }
        gen._fill_board_certification([entry])
        rows = _rows_after_board(gen)
        assert rows == [
            ("Board A", "111", "2010"),
            ("Board B", "", "2012"),
        ]
        assert rows[0][1] != rows[1][1], "the two rows must not share one certificate number"

    def test_single_structured_cert_number_not_attributable_across_multiple_rows_is_logged(self, caplog):
        # (d) The structured `certificate_number` here IS a single,
        # unambiguous candidate ("111") -- but the reparse produced two
        # rows, and it cannot be pinned to "Board A" (which already has its
        # own "111") vs "Board B" (which has none) without guessing. Backfill
        # must decline, leave Board B's certificate number blank, and log
        # that it declined rather than doing nothing visibly.
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Board A, Board B",
                "certificate_number": "111",
                "year_certified": "2010",
            },
            "text": "Board A | 111 | 2010\nBoard B | | 2012",
        }
        with caplog.at_level(logging.WARNING):
            gen._fill_board_certification([entry])
        rows = _rows_after_board(gen)
        assert rows == [
            ("Board A", "111", "2010"),
            ("Board B", "", "2012"),
        ]
        assert any(
            "cannot be attributed to a single row" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]


class TestSingleRowBackfillIsLogged:
    """#696 item 2: the single-row branch of the backfill logs a substitution
    and a skip, with field name and counts but never the CV's values."""

    _SECRET_NUM = "ZQ-90417"

    def _run(self, caplog, fields, text):
        gen = _generator()
        with caplog.at_level(logging.INFO):
            gen._fill_board_certification(
                [{"extracted_fields": fields, "text": text}]
            )
        return [r for r in caplog.records if "single reparsed row" in r.message]

    def test_substitution_is_logged_without_the_value(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": self._SECRET_NUM,
                "year_certified": "2015",
            },
            "Zed Medicine, Sub-board of the American Board of Zed\n2015",
        )
        assert [r.levelno for r in records] == [logging.INFO]
        assert "certificate_number" in records[0].getMessage()
        assert "backfilled" in records[0].getMessage()
        assert self._SECRET_NUM not in records[0].getMessage()

    def test_skip_of_multi_value_structured_field_is_logged(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": "ZQ-1, ZQ-2",
                "year_certified": "2015",
            },
            "Zed Medicine, Sub-board of the American Board of Zed\n2015",
        )
        assert [r.levelno for r in records] == [logging.WARNING]
        assert "certificate_number" in records[0].getMessage()
        assert "2 values" in records[0].getMessage()
        assert "ZQ-1" not in records[0].getMessage()

    def test_year_substitution_is_logged(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": "555",
                "year_certified": "2015",
            },
            "Zed Medicine, Sub-board of the American Board of Zed | 555",
        )
        assert [r.levelno for r in records] == [logging.INFO]
        assert "year_certified" in records[0].getMessage()

    def test_year_skip_of_multi_value_structured_field_is_logged(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": "555",
                "year_certified": "2015, 2016",
            },
            "Zed Medicine, Sub-board of the American Board of Zed | 555",
        )
        assert [r.levelno for r in records] == [logging.WARNING]
        assert "year_certified" in records[0].getMessage()
        assert "2 values" in records[0].getMessage()

    def test_nothing_logged_when_structured_field_is_absent(self, caplog):
        # No candidate at all is not a skip: there is nothing to attribute.
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "year_certified": "2015",
            },
            "Zed Medicine, Sub-board of the American Board of Zed\n2015",
        )
        assert records == []

    def test_nothing_logged_when_structured_year_is_absent(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": "555",
            },
            "Zed Medicine, Sub-board of the American Board of Zed | 555",
        )
        assert records == []

    def test_nothing_logged_when_row_needs_no_backfill(self, caplog):
        records = self._run(
            caplog,
            {
                "certifying_board": "Sub-board of the American Board of Zed",
                "certificate_number": "555",
                "year_certified": "2015",
            },
            "Zed Medicine | 555 | 2015",
        )
        assert records == []


class TestBlankRowGuardIsUniformAcrossColumnBranches:
    """#663 item 8: the too-narrow (<2 column) branch already dropped a row

    left with nothing written into it (see
    TestNarrowTableSkipsRowInsteadOfBlankOrRaise above, from #625), but that
    guard lived only in the `else` branch. The `>= 3` and `>= 2` branches had
    no equivalent check: given a specialty, certificate number, and dates
    that are all empty or falsy, they wrote every cell as `''` and still
    incremented `entries_inserted` for a row with literally nothing on it.
    The fix moves the check ahead of the column-count branching so it
    applies once, to every branch, instead of needing a copy per branch.
    """

    def test_three_column_table_skips_a_fully_blank_row(self, caplog):
        gen = _generator()
        wide_doc = Document()
        table = wide_doc.add_table(rows=1, cols=3)
        rows_before = len(table.rows)
        entries_before = gen.stats['entries_inserted']

        with caplog.at_level(logging.WARNING):
            gen._add_board_cert_row(table, "", "", "")

        assert len(table.rows) == rows_before, "the blank row must be removed, not left behind"
        assert gen.stats['entries_inserted'] == entries_before
        assert any("skipping blank row" in r.message for r in caplog.records)

    def test_two_column_table_skips_a_fully_blank_row(self, caplog):
        gen = _generator()
        two_col_doc = Document()
        table = two_col_doc.add_table(rows=1, cols=2)
        rows_before = len(table.rows)
        entries_before = gen.stats['entries_inserted']

        with caplog.at_level(logging.WARNING):
            gen._add_board_cert_row(table, "", "", "")

        assert len(table.rows) == rows_before, "the blank row must be removed, not left behind"
        assert gen.stats['entries_inserted'] == entries_before
        assert any("skipping blank row" in r.message for r in caplog.records)

    def test_falsy_but_non_blank_inputs_are_not_treated_as_blank(self):
        # None and "0" style falsy-but-meaningful-if-present inputs aside,
        # this pins that a row with just ONE real value (dates only, as in
        # a certification with no recovered specialty or cert number) still
        # renders -- the guard is "all three empty", not "any one empty".
        gen = _generator()
        doc = Document()
        table = doc.add_table(rows=1, cols=3)
        entries_before = gen.stats['entries_inserted']

        gen._add_board_cert_row(table, "", "", "2020")

        assert len(table.rows) == 2, "a row with a real date must still be written"
        assert table.rows[1].cells[2].text == "2020"
        assert gen.stats['entries_inserted'] == entries_before + 1

    def test_three_and_two_column_tables_still_render_real_content(self):
        # Still-renders proof: the new guard must not touch the ordinary,
        # non-blank path either branch already handled correctly.
        gen = _generator()

        wide_doc = Document()
        wide_table = wide_doc.add_table(rows=1, cols=3)
        gen._add_board_cert_row(wide_table, "Internal Medicine", "123-456", "2020")
        assert tuple(c.text for c in wide_table.rows[1].cells) == (
            "Internal Medicine", "123-456", "2020",
        )

        two_col_doc = Document()
        two_col_table = two_col_doc.add_table(rows=1, cols=2)
        gen._add_board_cert_row(two_col_table, "Internal Medicine", "123-456", "2020")
        assert tuple(c.text for c in two_col_table.rows[1].cells) == (
            "Internal Medicine", "123-456 (2020)",
        )

    def test_whitespace_only_inputs_are_treated_as_blank(self):
        # #663 item 8 follow-up: the date fields cannot deliver this case
        # (format_date_for_section strips first and returns '' for
        # whitespace), but `certifying_board` is read unstripped in
        # _fill_board_certification, so a whitespace-only board makes
        # has_structured_data true and reaches this function as
        # specialty='  '. Truthiness alone ("if not (specialty or
        # cert_number or dates)") does not catch that -- '  ' is truthy --
        # so the guard must strip before testing.
        gen = _generator()
        doc = Document()
        table = doc.add_table(rows=1, cols=3)
        rows_before = len(table.rows)
        entries_before = gen.stats['entries_inserted']

        gen._add_board_cert_row(table, "  ", "\t", " ")

        assert len(table.rows) == rows_before, "a whitespace-only row must be removed, not left behind"
        assert gen.stats['entries_inserted'] == entries_before


class TestEmptyEntriesClearsTemplatePlaceholderRow:
    """#708: `_fill_board_certification([])` used to return before
    `_clear_table_data` ever ran, so the WCM template's own shipped blank
    placeholder data row (real template table 10, row 1) survived into the
    delivered document on every CV with zero F2 entries -- 23 of 66 corpus
    CVs. The clear now always runs once the section header and table are
    located; the row-writing loop is simply a no-op on an empty `entries`
    (round 2: the separate early return after the clear was dead code and
    has been removed). `tables_populated` is gated on `entries` -- a
    cleared placeholder is not a populated table.
    """

    def test_real_template_no_entries_leaves_no_blank_data_row(self):
        gen = _generator()
        table = _board_table(gen)
        assert table is not None
        # Fixture guard: fail loudly, not silently-vacuous, if the shipped
        # template ever stops carrying the placeholder row this test targets.
        assert len(table.rows) == 2, (
            "template fixture drifted: expected header + one placeholder "
            "data row before the fill call"
        )

        gen._fill_board_certification([])

        assert table.rows[1:] == [], (
            "no data row of any kind -- blank or otherwise -- should remain "
            "with zero F2 entries"
        )
        # Header row is untouched -- read via raw w:t so this cannot be
        # satisfied by a header cell python-docx merely reports as non-empty.
        assert _cell_wt_texts(table.rows[0]) == [
            "Full Name of Board",
            "Certificate # \n(indicate if board eligible)",
            "Dates of Certification \n(yyyy–yyyy)",
        ]
        # round 2: a cleared placeholder is not a "populated" table.
        assert gen.stats['tables_populated'] == 0

    def test_one_synthetic_f2_entry_renders_exactly_one_row_no_blank_row(self):
        # Regression guard for the non-empty path: the clear now always
        # runs, so this also proves it doesn't leave a SECOND (blank) row
        # behind alongside the real one.
        gen = _generator()
        entry = {
            "text": "",
            "extracted_fields": {
                "certifying_board": "American Board of Synthetic Medicine",
                "certificate_number": "999999",
                "year_certified": "2019",
            },
        }

        gen._fill_board_certification([entry])

        table = _board_table(gen)
        assert len(table.rows) == 2, "exactly one data row, no blank row alongside it"
        assert _cell_wt_texts(table.rows[1]) == [
            "American Board of Synthetic Medicine", "999999", "2019",
        ]
        assert gen.stats['tables_populated'] == 1


class TestBoardCellCarriesTheSpecialty:
    """#897 item 4: two certifications from one board rendered as identical
    "American Board of Pediatrics" rows. The "Full Name of Board" cell is
    now board + specialty, through the structured path and through
    `_parse_and_add_multiple_certifications`'s structured fallback alike."""

    def test_two_certifications_from_one_board_stay_distinct(self):
        gen = _generator()
        gen._fill_board_certification([
            {"text": "American Board of Pediatrics, \nCertification in General Pediatrics | 109949 | 10/09/2014",
             "extracted_fields": {"certifying_board": "American Board of Pediatrics",
                                  "specialty": "General Pediatrics",
                                  "certificate_number": "109949", "year_certified": "2014-10-09"}},
            {"text": "American Board of Pediatrics, \nCertification in Pediatric Emergency Medicine | 2611 | 5/21/2019",
             "extracted_fields": {"certifying_board": "American Board of Pediatrics",
                                  "specialty": "Pediatric Emergency Medicine",
                                  "certificate_number": "2611", "year_certified": "2019-05-21"}},
        ])
        assert _rows_after_board(gen) == [
            ("American Board of Pediatrics, Pediatric Emergency Medicine", "2611", "2019"),
            ("American Board of Pediatrics, General Pediatrics", "109949", "2014"),
        ]

    def test_a_specialty_already_in_the_board_name_is_not_repeated(self):
        gen = _generator()
        gen._fill_board_certification([
            {"text": "American Board of Internal Medicine (Cardiovascular Disease) | 98765 | 2015",
             "extracted_fields": {"certifying_board": "American Board of Internal Medicine (Cardiovascular Disease)",
                                  "specialty": "Cardiovascular Disease",
                                  "certificate_number": "98765", "year_certified": "2015"}},
        ])
        assert _rows_after_board(gen) == [
            ("American Board of Internal Medicine (Cardiovascular Disease)", "98765", "2015"),
        ]

    def test_no_specialty_renders_the_board_alone(self):
        gen = _generator()
        gen._fill_board_certification([
            {"text": "American Board of Internal Medicine | 123456 | 2010",
             "extracted_fields": {"certifying_board": "American Board of Internal Medicine",
                                  "specialty": None,
                                  "certificate_number": "123456", "year_certified": "2010"}},
        ])
        assert _rows_after_board(gen) == [("American Board of Internal Medicine", "123456", "2010")]


class TestCertificateNumberSeparatorHeuristic:
    """#569: the comma/semicolon split that decides whether an entry is fused.

    `_split_multi` treats `,` and `;` as the same separator, so a mixed
    "111, 222; 333" counts three certifications, exactly like the pure-comma
    and pure-semicolon neighbours. `_is_fused_certification` then fires on
    more than one part.
    """

    def test_pure_comma_splits_into_parts(self):
        assert _split_multi("111, 222, 333") == ["111", "222", "333"]

    def test_pure_semicolon_splits_into_parts(self):
        assert _split_multi("111; 222; 333") == ["111", "222", "333"]

    def test_mixed_comma_and_semicolon_splits_into_the_same_parts(self):
        assert _split_multi("111, 222; 333") == ["111", "222", "333"]

    def test_mixed_separators_drop_empty_parts_and_whitespace(self):
        assert _split_multi(" 111 ,; 222 ;, ") == ["111", "222"]

    def test_single_number_and_blank_are_not_split(self):
        assert _split_multi("111") == ["111"]
        assert _split_multi("") == []
        assert _split_multi(None) == []

    def test_list_input_is_not_resplit_on_separators(self):
        assert _split_multi(["111", " 222 ", ""]) == ["111", "222"]

    def test_fusion_detected_for_pure_comma_pure_semicolon_and_mixed(self):
        for raw in ("111, 222", "111; 222", "111, 222; 333"):
            parts = _split_multi(raw)
            assert _is_fused_certification({"certificate_number": raw}, parts) is True, raw

    def test_single_number_is_not_fused(self):
        assert _is_fused_certification({"certificate_number": "111"}, _split_multi("111")) is False

    def test_mixed_separator_entry_takes_the_multi_cert_path_end_to_end(self):
        gen = _generator()
        entry = {
            "extracted_fields": {
                "certifying_board": "Board A",
                "certificate_number": "111, 222; 333",
                "year_certified": "2010",
            },
            "text": "Board A | 111 | 2010\nBoard B | 222 | 2012\nBoard C | 333 | 2014",
        }
        gen._fill_board_certification([entry])
        assert _rows_after_board(gen) == [
            ("Board A", "111", "2010"),
            ("Board B", "222", "2012"),
            ("Board C", "333", "2014"),
        ]
