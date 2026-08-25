"""PR #625 review round 2: board_certification.py, 7 threads (2026-08-25).

The reviewer overruled our earlier "pre-existing, deferred to #663" replies
and asked for each of these fixed in this PR, kept local to
`board_certification.py`. One test class per thread; thread ids are in each
class docstring so a reader can match a test back to the review comment.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_board_certification_round2.py -p no:cacheprovider
"""
import logging
import sys
from pathlib import Path

from docx import Document

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
