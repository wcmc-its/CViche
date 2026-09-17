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
