"""#821 (settled 2026-09-14): the licensure half of the withhold policy.

DEA is withheld with notice -- see the DEA row's own comment in
`stage6/normalization/pii.py` for why this is enforced here
(`_resolve_licensure`/`_fill_licensure`) rather than by widening that row's
scope. NPI stays public.

These tests drive the real `_fill_licensure`/`_resolve_licensure`/
`_recover_unrendered_records` paths, the same pattern
`test_stage6_classification_literals.py` and
`test_stage6_unrendered_recovery.py` already use. All values are synthetic.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_licensure_pii_policy.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage6.normalization.pii import CAT_DEA  # noqa: E402
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    _LICENSURE_SECTION_LABEL,
    _resolve_licensure,
    _strip_dea_lines,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _f1_dea_entry(**extra_fields):
    fields = {"license_number": "AB1234567"}
    fields.update(extra_fields)
    return {"taxonomy_code": "F1", "text": "DEA registration AB1234567",
            "extracted_fields": fields}


def _f1_npi_entry():
    return {"taxonomy_code": "F1", "text": "NPI 1234567890",
            "extracted_fields": {"license_number": "1234567890"}}


# ---------------------------------------------------------------------------
# 1. The real-template render: the DEA cell renders empty, the withheld
#    item reaches the SAME list the notice/comment mechanism reads. NPI
#    (public, #821 "render") still reaches its own cell.
# ---------------------------------------------------------------------------

def test_dea_slot_renders_empty_and_is_recorded_withheld():
    gen = _generator()
    gen._fill_licensure([_f1_dea_entry(), _f1_npi_entry()])

    dea_npi_table = None
    for table in gen.doc.tables:
        for row in table.rows:
            if 'dea number' in row.cells[0].text.lower():
                dea_npi_table = table
                break
        if dea_npi_table:
            break
    assert dea_npi_table is not None, "template's DEA/NPI table not found"

    cells = {row.cells[0].text.lower(): row.cells[1].text
             for row in dea_npi_table.rows}
    assert cells.get('dea number: (optional)', None) == ""
    assert cells.get('npi number: (optional)', None) == "1234567890"

    assert len(gen._pii_result.withheld) == 1
    item = gen._pii_result.withheld[0]
    assert item.category == CAT_DEA
    assert item.section_label == _LICENSURE_SECTION_LABEL == "Licensure"


def test_dea_slot_stays_blank_with_no_dea_entry_and_records_nothing():
    """Negative control: an ordinary state licence entry never touches the
    withheld list or the identifiers table."""
    gen = _generator()
    gen._fill_licensure([
        {"taxonomy_code": "F1", "text": "New York State Medical License",
         "extracted_fields": {"state": "New York", "license_number": "123456"}},
    ])
    assert gen._pii_result.withheld == []


# ---------------------------------------------------------------------------
# 2. `_resolve_licensure` is pure and reports the decision without a
#    document -- unit-level companion to the render test above.
# ---------------------------------------------------------------------------

def test_resolve_licensure_dea_withheld_flag_and_text_mutation():
    """`dea_withheld` is set and `identifiers.dea` stays None regardless of
    whether the raw entry names an F1 sub-kind by LABEL or by SHAPE alone
    (test_shape_fallback_dea_two_letters_seven_alphanumeric,
    test_stage6_licensure_render.py, already pins the classifier itself)."""
    labelled = _f1_dea_entry()
    result = _resolve_licensure([labelled])
    assert result.dea_withheld is True
    assert result.identifiers.dea is None
    assert result.licenses == ()
    # The mutation the #221 recovery-safety-net test below depends on.
    assert labelled["text"] == ""


# ---------------------------------------------------------------------------
# 3. The #221 post-render recovery pass must not re-insert a DEA number
#    fused into a multi-line F1 entry alongside an unrelated record line --
#    the recovery pass reads `entry['text']` directly, not this section's
#    own rendered output (see `_resolve_licensure`'s own docstring on why
#    it blanks `raw['text']` for a DEA-classified entry).
# ---------------------------------------------------------------------------

def test_221_recovery_does_not_reinsert_a_fused_dea_line():
    """#821 R2 F4: `_resolve_licensure` now strips only the DEA line(s) out
    of a DEA-classified entry's raw text (`_strip_dea_lines`), not the
    whole entry -- so a fused F1 entry's real state-licence lines stay
    available to the #221 recovery pass (`_recover_unrendered_records`,
    which reads `entry['text']` directly). Both licence lines are
    record-shaped per `_record_lines` (>60 chars, pipe-delimited),
    dated, and name a state and a licence number, so they actually clear
    `UNRENDERED_MIN_RECORD_LINES` and get scanned -- round 1's version of
    this test used two short prose lines that never did (verifier F4: the
    test passed on the mutant that removed the blank because neither line
    was ever a candidate for recovery in the first place). Asserts BOTH
    halves of the fix: the licence lines ARE recovered, and no DEA-shaped
    token reaches the document either way."""
    gen = _generator()
    entry = {
        "taxonomy_code": "F1",
        "text": "\n".join([
            "DEA registration AB1234567",
            "State of New Jersey | License 887744 | Issued 05/2016 | Expires 05/2026",
            "Commonwealth of Vermont | Medical Certificate 224466 | Granted 09/2017 | Renewal due 09/2027",
        ]),
        "extracted_fields": {"license_number": "AB1234567"},
    }

    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    joined = "\n".join(texts)
    assert "887744" in joined, "the New Jersey licence line was not recovered"
    assert "224466" in joined, "the Vermont licence line was not recovered"
    assert not any("AB1234567" in t for t in texts), \
        "the DEA number leaked into the document"


def test_221_recovery_does_not_reinsert_a_record_shaped_dea_line():
    """Negative case `_strip_dea_lines` exists for: a DEA line that is
    ITSELF record-shaped (long, pipe-delimited, dated -- exactly what
    `_record_lines` looks for) would be picked up and re-inserted verbatim
    by the #221 recovery pass if it reached `entry['text']` unstripped --
    the precise leak the round-1 verifier's g7 mutant (the blank/strip
    call removed) produced. Two non-DEA sibling licence lines keep the
    entry at 3 record-shaped lines before the strip (>= 2 after it), so the
    entry is not skipped by the `UNRENDERED_MIN_RECORD_LINES` gate either
    way -- this fixture actually exercises the strip rather than being
    saved by the entry being too short to scan at all."""
    gen = _generator()
    entry = {
        "taxonomy_code": "F1",
        "text": "\n".join([
            "DEA Number AB1234567 | Schedule II | Issued 03/2015 | Expires 03/2018",
            "State of Connecticut | License 335577 | Issued 01/2018 | Expires 01/2028",
            "Commonwealth of Massachusetts | Certificate 446688 | Granted 02/2019 | Renewal due 02/2029",
        ]),
        "extracted_fields": {"license_number": "AB1234567"},
    }

    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    joined = "\n".join(texts)
    assert not any("AB1234567" in t for t in texts), \
        "the record-shaped DEA line leaked into the document"
    assert "335577" in joined, "the Connecticut licence line was not recovered"
    assert "446688" in joined, "the Massachusetts licence line was not recovered"


# ---------------------------------------------------------------------------
# 4. `_strip_dea_lines` removes DEA lines and NOTHING that merely shares
#    their length (#821 R3 F-A).
#
#    The round-2 predicate tested every alphanumeric token against the
#    classifier's deliberately loose shape (two letters + seven
#    ALPHANUMERICS), which is also the shape of every nine-letter English
#    word -- so a fused entry lost every licence line that happened to name
#    a nine-letter state or carry a nine-letter word, silently.
# ---------------------------------------------------------------------------

#: Nine letters each: the length the loose shape confused with a DEA number.
_NINE_LETTER_WORDS = ("Wisconsin", "Certified", "Emergency", "Physician")


def test_strip_dea_lines_keeps_a_line_whose_only_nine_char_token_is_a_word():
    """The negative case, one line per nine-letter word. None of these is a
    DEA registration (which is two letters then seven DIGITS), so none of
    these lines may be removed."""
    for word in _NINE_LETTER_WORDS:
        line = f"State of Example | {word} licence 123456 | Issued 01/2015"
        assert _strip_dea_lines(line) == line, f"{word!r} was read as a DEA number"


def test_strip_dea_lines_removes_a_real_dea_number_token():
    """The positive case: two letters then seven digits, with no "DEA"
    label anywhere on the line to fall back on."""
    assert _strip_dea_lines("Registration AB1234567 | Issued 01/2015") == ""


def test_strip_dea_lines_removes_a_labelled_line_and_keeps_its_siblings():
    text = "\n".join([
        "State of Example | Licence 123456 | Issued 01/2015",
        "DEA registration AB1234567",
        "Commonwealth of Example | Certificate 654321 | Issued 02/2016",
    ])
    kept = _strip_dea_lines(text)
    assert "AB1234567" not in kept
    assert kept.split("\n") == [
        "State of Example | Licence 123456 | Issued 01/2015",
        "Commonwealth of Example | Certificate 654321 | Issued 02/2016",
    ]


def test_221_recovery_keeps_fused_licence_lines_with_nine_letter_words():
    """#821 R3 F-A, through the real render + #221 recovery path: a fused
    F1 entry whose DEA line sits alongside two record-shaped state-licence
    lines, one of them naming a nine-letter jurisdiction and one carrying a
    nine-letter word in its own text. Both licences must come back out of
    `_recover_unrendered_records`, and no DEA-shaped token may reach the
    document. Under the round-2 predicate the nine-letter line was stripped
    with the DEA line, which also dropped the entry below
    `UNRENDERED_MIN_RECORD_LINES` and cost the OTHER licence too -- two
    licences recovered before this branch, zero after it."""
    gen = _generator()
    entry = {
        "taxonomy_code": "F1",
        "text": "\n".join([
            "DEA registration AB1234567",
            "State of Wisconsin | License 887744 | Issued 05/2016 | Expires 05/2026",
            "State of New Jersey | Certified License 665533 | Issued 09/2017 "
            "| Renewal due 09/2027",
        ]),
        "extracted_fields": {"license_number": "AB1234567"},
    }

    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    joined = "\n".join(texts)
    # Neither number is a substring of the DEA token, so a leak cannot
    # satisfy these two assertions.
    assert "887744" in joined, "the nine-letter-jurisdiction licence line was lost"
    assert "665533" in joined, "the sibling licence line was lost with it"
    assert not any("AB1234567" in t for t in texts), \
        "the DEA number leaked into the document"


# ---------------------------------------------------------------------------
# 5. #1217: two shapes the label/shape classifier let a DEA number through.
#
#    (a) the row is labelled with the agency's FULL NAME, so no "DEA" token
#        exists and the stated "state" turns the shape tiebreak off;
#    (b) the reader fused a DEA number into ANOTHER credential's number cell,
#        so the entry that holds it is a perfectly good licence.
# ---------------------------------------------------------------------------

_AGENCY = "Drug Enforcement Administration"


def _agency_row_entry(number="AB1234567"):
    return {
        "taxonomy_code": "F1",
        "text": f"{_AGENCY}   {number}   2011 - Present",
        "extracted_fields": {"state_country": _AGENCY, "license_number": number,
                             "issue_date": "2011", "expiration_date": "present"},
    }


def _fused_number_entry(number="#12345A, #12345B, 1234567890, #AB1234567"):
    return {
        "taxonomy_code": "F1",
        "text": "Licensed Physician, Example State | #12345A\n #12345B\n 1234567890\n"
                " #AB1234567 | 2005-Present",
        "extracted_fields": {"state_country": "Example State",
                             "license_number": number,
                             "issue_date": "2005", "expiration_date": "present"},
    }


def _all_cell_text(gen):
    return "\n".join(c.text for t in gen.doc.tables for r in t.rows for c in r.cells)


def test_agency_named_row_is_withheld_not_rendered_as_a_licence():
    state_licence = {"taxonomy_code": "F1", "text": "State of Example 99887",
                     "extracted_fields": {"state_country": "State of Example",
                                          "license_number": "99887"}}
    result = _resolve_licensure([_agency_row_entry(), state_licence])
    assert result.dea_withheld is True
    assert result.identifiers.dea is None
    assert [r.number for r in result.licenses] == ["99887"]


def test_agency_named_row_render_has_no_number_and_records_the_notice():
    gen = _generator()
    gen._fill_licensure([_agency_row_entry()])
    assert "AB1234567" not in _all_cell_text(gen)
    assert _AGENCY not in _all_cell_text(gen)
    assert [i.category for i in gen._pii_result.withheld] == [CAT_DEA]


def test_a_dea_token_fused_into_a_licence_number_cell_is_cut_out_and_withheld():
    entry = _fused_number_entry()
    result = _resolve_licensure([entry])
    assert result.dea_withheld is True
    assert [(r.state, r.number) for r in result.licenses] == [
        ("Example State", "#12345A, #12345B, 1234567890")]
    # the #221 recovery pass re-reads this text: the DEA line is gone from it
    assert "AB1234567" not in entry["text"]


def test_fused_dea_token_is_cut_wherever_it_sits_in_the_cell():
    for cell, expected in (
        ("AB1234567, 12345A, 67890B", "12345A, 67890B"),
        ("12345A, AB1234567, 67890B", "12345A, 67890B"),
        ("12345A 67890B AB1234567", "12345A 67890B"),
        ("12345A; #AB1234567", "12345A"),
    ):
        result = _resolve_licensure([_fused_number_entry(cell)])
        assert result.dea_withheld is True, cell
        assert result.licenses[0].number == expected, cell


def test_fused_dea_token_render_has_no_number_and_records_the_notice():
    gen = _generator()
    gen._fill_licensure([_fused_number_entry()])
    text = _all_cell_text(gen)
    assert "AB1234567" not in text
    assert "#12345A, #12345B, 1234567890" in text
    assert [i.category for i in gen._pii_result.withheld] == [CAT_DEA]


def test_221_recovery_does_not_reinsert_a_dea_line_fused_into_a_licence_entry():
    """The entry classifies as a LICENCE, so nothing blanks its text unless
    the fused-token branch does: the recovery pass re-reads `entry['text']`
    and would put this record-shaped DEA line back as a verbatim bullet."""
    gen = _generator()
    entry = {
        "taxonomy_code": "F1",
        "text": "\n".join([
            "State of Example | License #12345A | Issued 05/2016 | Expires 05/2026",
            "Controlled Substance Registration | #AB1234567 | Issued 05/2016 | Expires 05/2026",
        ]),
        "extracted_fields": {"state_country": "State of Example",
                             "license_number": "#12345A, #AB1234567",
                             "issue_date": "2016", "expiration_date": "2026"},
    }
    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})
    full = "\n".join([p.text for p in gen.doc.paragraphs] + [_all_cell_text(gen)])
    assert "AB1234567" not in full, "the fused DEA number leaked into the document"


def test_a_lone_dea_shaped_number_beside_a_stated_state_is_still_a_licence():
    """The #573 guard is kept: shape alone cannot tell a licence number from
    a DEA number when the row names a jurisdiction and holds one token."""
    entry = _fused_number_entry("AB1234567")
    result = _resolve_licensure([entry])
    assert result.dea_withheld is False
    assert result.licenses[0].number == "AB1234567"


def test_a_multi_token_number_cell_without_a_dea_token_is_untouched():
    entry = _fused_number_entry("12345A, B")
    text_before = entry["text"]
    result = _resolve_licensure([entry])
    assert result.dea_withheld is False
    assert result.licenses[0].number == "12345A, B"
    assert entry["text"] == text_before


def test_a_nine_letter_word_in_a_number_cell_is_not_cut():
    result = _resolve_licensure([_fused_number_entry("Wisconsin, 12345A")])
    assert result.dea_withheld is False
    assert result.licenses[0].number == "Wisconsin, 12345A"


# ---------------------------------------------------------------------------
# 6. #1217, the NPI slot: the fused-cell cut must cover an NPI-labelled entry
#    too. If the stacked credential column lists the NPI first, the NPI entry
#    is the one that receives every number, DEA included.
# ---------------------------------------------------------------------------

def _npi_entry(number, *, labelled_by="text"):
    entry = {
        "taxonomy_code": "F1",
        "text": f"NPI | {number}",
        "extracted_fields": {"license_number": number},
    }
    if labelled_by == "type":
        entry["text"] = f"Identifier | {number}"
        entry["extracted_fields"]["license_type"] = "NPI"
    return entry


def _dea_npi_table_cells(gen):
    for table in gen.doc.tables:
        if any("dea number" in r.cells[0].text.lower() for r in table.rows):
            return {r.cells[0].text.lower(): r.cells[1].text for r in table.rows}
    raise AssertionError("template's DEA/NPI table not found")


def test_a_dea_token_fused_into_an_npi_number_cell_is_cut_out_and_withheld():
    for labelled_by in ("text", "type"):
        entry = _npi_entry("1234567890, AB1234567", labelled_by=labelled_by)
        result = _resolve_licensure([entry])
        assert result.identifiers.npi == "1234567890", labelled_by
        assert result.dea_withheld is True, labelled_by
        assert result.licenses == ()
        assert "AB1234567" not in entry["text"], labelled_by


def test_a_lone_dea_shaped_number_in_an_npi_entry_is_not_an_npi():
    """Unlike a licence cell, one token is enough here: an NPI is ten or
    eleven digits, so a DEA-shaped value is not the NPI wherever it sits."""
    entry = _npi_entry("AB1234567")
    result = _resolve_licensure([entry])
    assert not result.identifiers.npi
    assert result.dea_withheld is True
    assert "AB1234567" not in entry["text"]


def test_an_npi_entry_that_holds_only_an_npi_is_untouched():
    entry = _npi_entry("1234567890")
    text_before = entry["text"]
    result = _resolve_licensure([entry])
    assert result.identifiers.npi == "1234567890"
    assert result.dea_withheld is False
    assert entry["text"] == text_before


def test_a_fused_npi_cell_render_has_no_dea_number_and_records_the_notice():
    gen = _generator()
    gen._fill_licensure([_npi_entry("1234567890, AB1234567")])
    assert "AB1234567" not in _all_cell_text(gen)
    assert _dea_npi_table_cells(gen)["npi number: (optional)"] == "1234567890"
    assert [i.category for i in gen._pii_result.withheld] == [CAT_DEA]


def test_a_clean_npi_entry_still_fills_the_slot_in_either_order_beside_a_cut_one():
    for order in (("AB1234567", "1234567890"), ("1234567890", "AB1234567")):
        result = _resolve_licensure([_npi_entry(n) for n in order])
        assert result.identifiers.npi == "1234567890", order
        assert result.dea_withheld is True, order
