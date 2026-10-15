"""Regression guards for the #221 unrendered-record recovery pass.

Grounded in run KFGXBW: a fused D1 entry held five date-prefixed employment
records as text lines, extracted_fields held only the first, and the positions
table rendered exactly one row — the other four records appeared nowhere in
the output docx. The same class hits the F1/H/P/Q1/R and grant render paths.
All fixture strings here are invented; shapes mirror the affected corpus
entries (flat singular fields, tab rows with column headers, pipe-delimited
split table_rows with string element_idx values).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_unrendered_recovery.py -p no:cacheprovider

Self-contained: no DB, no LLM calls. Loads the bundled WCM template like
test_stage6_funding_appendix.py does.
"""

import ast
import copy
import inspect
import json
import sys
import textwrap
import unicodedata
from pathlib import Path

import pytest

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.stage6.fan_out import FANNED_OUT_FROM, LAST_STAGE4_RECORD  # noqa: E402
from unified_pipeline.stage6.normalization import _clean_inline_tabs  # noqa: E402
from unified_pipeline.stage6.parsing.text import (  # noqa: E402
    DATE_LED_SOURCE_TEXT,
    split_date_led_records,
)
from unified_pipeline.stage6.sections.appendix import recovery_candidates  # noqa: E402
from unified_pipeline.stage6.render_check import (  # noqa: E402
    _RENDER_TOKEN_RE,
    _norm,
    _record_rendered,
    _whole_record_rendered,
    segment_held_by_fields,
    segments_cover_source,
    tab_aware_record_lines,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    _record_lines,
    run_stage6,
    segment_already_rendered,
)


def _generator(**kwargs):
    gen = WCMTemplateGenerator(verbose=False, **kwargs)
    gen.doc = Document(gen.template_path)
    return gen


def _num_level(para):
    """The paragraph's w:ilvl, or None when it carries no list markup.

    Mirrors test_stage6_k_list_bullets.py's helper of the same name.
    """
    pPr = para._p.pPr
    numPr = pPr.find(qn("w:numPr")) if pPr is not None else None
    if numPr is None:
        return None
    return numPr.find(qn("w:ilvl")).get(qn("w:val"))


def _bulleted_texts(paragraphs):
    """Text of every real Word list paragraph among the given paragraphs.

    Both recovery emitters -- `_insert_reconsidered_segment` and
    `_add_remaining_to_appendix` -- write real list paragraphs as of #483 R2
    (previously `_insert_reconsidered_segment` prefixed a literal "•", which
    is what the old `startswith("•")` scans across this file matched on).
    """
    return [p.text for p in paragraphs if _num_level(p) is not None]


# Mirrors the KFGXBW entry-21 shape: date-range comma lines, flat singular
# extracted_fields holding only the FIRST record.
def _d1_entry():
    return {
        "element_idx_start": 21,
        "element_type": "break",
        "hierarchy": ["ACADEMIC APPOINTMENTS"],
        "taxonomy_code": "D1",
        "taxonomy_confidence": 0.85,
        "text": "\n".join([
            "Jan 2024-Present, Vice Chair of Interdimensional Affairs",
            "Aug 2019-Dec 2023, Associate Director of Quantitative "
            "Basketweaving, Institute of Speculative Metrics, Norvale "
            "University, Crab Hollow, ZQ",
            "Mar 2015-Jul 2019, Program Coordinator for Wombat Logistics, "
            "Office of Curious Ventures, Norvale University, Crab Hollow, ZQ",
        ]),
        "extraction_coverage": {"extraction_coverage_percent": 11.8},
        "extracted_fields": {
            "title": "Vice Chair of Interdimensional Affairs",
            "institution": None,
            "start_date": "Jan 2024",
            "end_date": "Present",
        },
    }


# ------------------------------------------------------- record-line heuristic

def test_record_lines_heuristics():
    text = "\n".join([
        "Aug 2019-Dec 2023, Associate Director of Quantitative Basketweaving",
        "Aug, 2026, Judge, Regional Pie Symposium",  # comma month: no heuristic
        "short",
        "Chair | Committee on Extremely Long Titles | Guild of Meandering "
        "Auditors | 2011-2014",
    ])
    assert _record_lines(text) == [
        "Aug 2019-Dec 2023, Associate Director of Quantitative Basketweaving",
        "Chair | Committee on Extremely Long Titles | Guild of Meandering "
        "Auditors | 2011-2014",
    ]


def test_tab_aware_record_lines_split_a_tab_fused_line_into_its_dated_records():
    # #1243: one line, three posts joined by tabs. `_record_lines` counts the
    # line once, so the entry never reached the recovery pass.
    fused = ("Executive Committee of the Quokka Society 2013-present\t"
             "President 5/2017-10/2020\tPresident Elect 5/2015-5/2017")
    assert _record_lines(fused) == [fused]
    assert tab_aware_record_lines(fused) == [
        "Executive Committee of the Quokka Society 2013-present",
        "President 5/2017-10/2020",
        "President Elect 5/2015-5/2017",
    ]
    # A tab row with one dated part is one record line, as before.
    row = "Chair\tCommittee on Extremely Long Titles\tGuild of Meandering Auditors\t2011-2014"
    assert tab_aware_record_lines(row) == [row] == _record_lines(row)


def test_tab_fused_entry_recovers_the_record_the_render_dropped():
    gen = _generator()
    entry = {
        "taxonomy_code": "Q1",
        "element_idx_start": 281,
        "text": ("Steering Committee of the Quokka Guild 2013-present\t"
                 "Chairperson of Marsupial Outreach 5/2017-10/2020\t"
                 "Secretary of Wallaby Affairs 5/2013-5/2015"),
        "extracted_fields": {"organization": "Quokka Guild", "role": "Steering Committee"},
    }
    gen.doc.add_paragraph("Steering Committee of the Quokka Guild 2013-present")

    gen._recover_unrendered_records({"Q1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Chairperson of Marsupial Outreach 5/2017-10/2020" in t for t in bullets)
    assert any("Secretary of Wallaby Affairs 5/2013-5/2015" in t for t in bullets)
    # The part that did render is never written a second time.
    assert not any("Steering Committee of the Quokka Guild" in t for t in bullets)


def test_tab_part_the_extracted_fields_hold_is_not_recovered():
    # #1243: a mentee's name and level, then his fellowship, each dated. The
    # row renders them cell by cell, so no output line carries the first part
    # whole, but stage 4 holds every word of it.
    gen = _generator()
    text = ("2007-2011 Quimby Farrow, MSPH, PhD (Epidemiology)\t"
            "Marsupial Heart Association Fellowship (7/2009-6/2011)")
    entry = {
        "taxonomy_code": "N3B", "text": text,
        "extracted_fields": {"mentee_name": "Quimby Farrow", "mentee_level": "MSPH, PhD (Epidemiology)",
                             "funding_source": "Marsupial Heart Association Fellowship (7/2009-6/2011)"},
    }
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"N3B": [entry]})

    assert len(gen.doc.paragraphs) == before
    assert segment_held_by_fields("2007-2011 Quimby Farrow, MSPH, PhD (Epidemiology)", entry["extracted_fields"])
    assert not segment_held_by_fields("Chairperson of Marsupial Outreach 5/2017-10/2020",
                                      entry["extracted_fields"])


def test_tab_parts_of_an_entry_stage_4_split_into_records_are_not_recovered():
    # The records render as children of their own, reformatted; the parent's
    # last child keeps the whole fused text.
    gen = _generator()
    entry = {
        "taxonomy_code": "Q1",
        "text": ("Regional Quokka Advisory Council (Appointed by Governor) 1995 - 2001\t"
                 "Committee on Quokka Facilities, Chairman, 2002 - 2004"),
        "extracted_fields": {"role": "Chairman", "organization": "Regional Quokka Advisory Council"},
        "stage4_last_record": {"key": "stage4_records", "index": 1, "count": 2, "text": None},
    }
    entry.update({LAST_STAGE4_RECORD: entry.pop("stage4_last_record")})
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"Q1": [entry]})

    assert len(gen.doc.paragraphs) == before


def test_tab_part_held_only_by_a_list_field_is_still_recovered():
    # #1243: `additional_roles` items are not always printed (fan-out declined,
    # or a field the section does not name), so a part whose words only a list
    # holds is not the extracted record.
    gen = _generator()
    entry = {
        "taxonomy_code": "I",
        "text": ("Member of the Quokka Society 2009-present\t"
                 "Chairman, Credentials Committee, 2009-2011\t"
                 "Secretary of the Wombat Panel 2012-2014"),
        "extracted_fields": {"organization": "Quokka Society", "role": "Member",
                             "additional_roles": ["Chairman, Credentials Committee",
                                                  "Secretary of the Wombat Panel"]},
    }
    gen.doc.add_paragraph("Member of the Quokka Society 2009-present")

    gen._recover_unrendered_records({"I": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Chairman, Credentials Committee, 2009-2011" in t for t in bullets)
    assert any("Secretary of the Wombat Panel 2012-2014" in t for t in bullets)
    assert not segment_held_by_fields("Chairman, Credentials Committee, 2009-2011",
                                      entry["extracted_fields"])


def test_recovered_tab_part_does_not_vouch_for_its_sibling():
    # #1243: siblings of one society share their vocabulary and differ in the
    # dates. The first one written must not make the next read as rendered.
    gen = _generator()
    entry = {
        "taxonomy_code": "Q1",
        "text": ("Member of the Quokka Society 1989-present\t"
                 "Chair, Program Committee of the Quokka Society 2015-2017\t"
                 "Vice Chair, Program Committee of the Quokka Society 2012-2015"),
        "extracted_fields": {"organization": "Quokka Society", "role": "Member"},
    }
    gen.doc.add_paragraph("Member of the Quokka Society 1989-present")

    gen._recover_unrendered_records({"Q1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Chair, Program Committee of the Quokka Society 2015-2017" in t for t in bullets)
    assert any("Vice Chair, Program Committee of the Quokka Society 2012-2015" in t for t in bullets)


def _quokka_entry(*dated_parts):
    return {
        "taxonomy_code": "Q1",
        "text": "\t".join(("Member of the Quokka Society 1989-present",) + dated_parts),
        "extracted_fields": {"organization": "Quokka Society", "role": "Member"},
    }


def test_a_tab_part_recovered_from_one_entry_vouches_for_its_duplicate_in_another():
    # #1243: dedup keeps near-duplicate entries apart until later, so the same
    # fused entry can reach the pass twice; each part is written once.
    gen = _generator()
    parts = ("Chair, Program Committee of the Quokka Society 2015-2017",
             "Treasurer of the Wombat Panel 2012-2014")
    gen.doc.add_paragraph("Member of the Quokka Society 1989-present")

    gen._recover_unrendered_records({"Q1": [_quokka_entry(*parts), _quokka_entry(*parts)]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    for part in parts:
        assert sum(part in t for t in bullets) == 1
    assert gen.stats["unrendered_records_recovered"] == 2


@pytest.mark.parametrize("tab_first", [True, False])
def test_a_record_recovered_as_a_tab_part_is_not_rewritten_from_a_newline_entry(tab_first):
    # #1243: the same records, fused by tabs in one entry and one per line in
    # another; each is written once whichever entry the pass reads first.
    gen = _generator()
    parts = ("2015-2017 Chair, Program Committee of the Quokka Society",
             "2012-2014 Treasurer of the Wombat Panel")
    gen.doc.add_paragraph("Member of the Quokka Society 1989-present")
    newline_entry = {"taxonomy_code": "Q1", "text": "\n".join(parts), "extracted_fields": {}}
    entries = [_quokka_entry(*parts), newline_entry]

    gen._recover_unrendered_records({"Q1": entries if tab_first else entries[::-1]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    for part in parts:
        assert sum(part in t for t in bullets) == 1
    assert gen.stats["unrendered_records_recovered"] == 2


def test_a_recovered_tab_part_does_not_vouch_for_a_later_entry_that_differs_in_its_years():
    # #1243: two entries of one society share their vocabulary; the second
    # one's part carries other years, so it is its own record.
    gen = _generator()
    first = "Chair, Program Committee of the Quokka Society 2015-2017"
    second = "Chair, Program Committee of the Quokka Society 2009-2011"
    gen.doc.add_paragraph("Member of the Quokka Society 1989-present")

    gen._recover_unrendered_records({"Q1": [
        _quokka_entry(first, "Treasurer of the Wombat Panel 2012-2014"),
        _quokka_entry(second, "Auditor of the Numbat Panel 2005-2007"),
    ]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any(first in t for t in bullets)
    assert any(second in t for t in bullets)


def test_record_line_held_by_fields_is_still_recovered_when_it_is_not_a_tab_part():
    # The held-by-fields skip is for tab parts only: an ordinary record line
    # whose words the fields hold, and that the render dropped, is lost.
    gen = _generator()
    entry = {
        "taxonomy_code": "Q1",
        "text": ("2009-2011 Member of the Quokka Society in good standing\n"
                 "2012-2014 Chair of the Wombat Panel for three years"),
        "extracted_fields": {"notes": "2009-2011 Member of the Quokka Society in good standing "
                                      "2012-2014 Chair of the Wombat Panel for three years"},
    }
    gen.doc.add_paragraph("Unrelated line about marsupial husbandry in the highlands")

    gen._recover_unrendered_records({"Q1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    assert "2012-2014 Chair of the Wombat Panel for three years" in texts
    assert gen.stats['unrendered_records_recovered'] == 2


# ------------------------------------------------ D1 positions path (KFGXBW)

def test_d1_remainder_recovered_in_positions_section():
    gen = _generator()
    entry = _d1_entry()

    # The real render path: one table row from extracted_fields, remainder
    # lines dropped (#221).
    gen._fill_positions({"D1": [entry]})
    gen._recover_unrendered_records({"D1": [entry]})

    paras = gen.doc.paragraphs
    texts = [p.text for p in paras]
    bullets = _bulleted_texts(paras)
    assert any(t.startswith("Aug 2019-Dec 2023, Associate Director of "
                            "Quantitative Basketweaving") for t in bullets)
    assert any(t.startswith("Mar 2015-Jul 2019, Program Coordinator for "
                            "Wombat Logistics") for t in bullets)
    # The extracted record rendered as a table row — never duplicated as a
    # bullet.
    assert not any("Interdimensional Affairs" in t for t in bullets)
    # Recovered into the entry's own section, not the appendix.
    header_idx = next(i for i, t in enumerate(texts)
                      if "Academic Appointments" in t)
    bullet_idx = next(i for i, (p, t) in enumerate(zip(paras, texts))
                      if _num_level(p) is not None and t.startswith("Aug 2019"))
    assert bullet_idx > header_idx
    assert not any("T. APPENDIX" in t for t in texts)


def test_reformatted_rendered_line_not_recovered():
    gen = _generator()
    # Simulate a stage-6 reformat of the first record: different date format
    # and field order, but its distinctive tokens stay on one line.
    gen.doc.add_paragraph(
        "Curator of Improbable Archives, Norvale University (2019 - 2023)")
    entry = {
        "element_idx_start": 7,
        "taxonomy_code": "D1",
        "text": "\n".join([
            "Aug 2019-Dec 2023, Curator of Improbable Archives, Norvale "
            "University",
            "Feb 2011-Jul 2015, Keeper of Ceremonial Spreadsheets, Bureau of "
            "Ornamental Data, Quexley College",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"D1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert not any("Improbable Archives" in t for t in bullets)
    assert any(t.startswith("Feb 2011-Jul 2015, Keeper of Ceremonial "
                            "Spreadsheets") for t in bullets)


def test_short_title_extracted_record_not_double_rendered():
    """#221 review (HIGH): a short extracted title ('Professor') whose source
    line carries department tokens the positions table omits pushes the
    per-line token overlap below 0.7, so the already-rendered guard — not the
    token check — must keep the record from being re-emitted as a bullet next
    to its own table row."""
    gen = _generator()
    entry = {
        "element_idx_start": 40,
        "taxonomy_code": "D1",
        "text": "\n".join([
            "Jul 2014-Present, Professor, Department of Speculative "
            "Rehabilitation Metrics, Norvale University Medical College",
            "Aug 2009-Jun 2014, Associate Professor, Bureau of Ornamental "
            "Data, Quexley College",
        ]),
        "extracted_fields": {
            "title": "Professor",
            "institution": "Norvale University Medical College",
            "start_date": "Jul 2014",
            "end_date": "Present",
        },
    }

    gen._fill_positions({"D1": [entry]})
    gen._recover_unrendered_records({"D1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    # The extracted record rendered as a table row — never also as a bullet.
    assert not any("Jul 2014-Present" in t for t in bullets)
    # The dropped sibling is still recovered.
    assert any(t.startswith("Aug 2009-Jun 2014, Associate Professor")
               for t in bullets)


def test_short_title_guard_requires_both_date_anchors():
    """The short-title conjunction must not mark a fused sibling as already
    rendered when it only shares a boundary date and a title suffix
    ('Associate Professor' contains 'Professor')."""
    fields = {
        "title": "Professor",
        "institution": "Norvale University Medical College",
        "start_date": "Jul 2014",
        "end_date": "Present",
    }
    line = ("Jul 2014-Present, Professor, Department of Speculative "
            "Rehabilitation Metrics, Norvale University Medical College")
    sibling = ("Aug 2009-Jul 2014, Associate Professor, Norvale University "
               "Medical College")
    assert segment_already_rendered(line, fields)
    assert not segment_already_rendered(sibling, fields)


def test_unverifiable_short_line_never_recovered():
    gen = _generator()
    entry = {
        "taxonomy_code": "D1",
        "text": "\n".join([
            # <3 distinctive tokens: absent verbatim, but unverifiable (None)
            "Jun 2011-Jun 2014, Dean, ZQ Camp",
            "Feb 2001-Jul 2005, Cartographer of Forgotten Stairwells, "
            "Quexley College",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"D1": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    assert not any("ZQ Camp" in t for t in texts)
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any(t.startswith("Feb 2001-Jul 2005, Cartographer of Forgotten "
                            "Stairwells") for t in bullets)


# --------------------------------------------------- F1 licensure path (2071)

def _f1_entry():
    return {
        "element_idx_start": 20,
        "element_idx_end": 21,
        "element_type": "table",
        "hierarchy": ["Certification and Licensure"],
        "taxonomy_code": "F1",
        "text": "\n".join([
            "Licensure",
            # Column-header row: tabbed and >60 chars (a record line under the
            # tab heuristic) but with no digit payload — must never become a
            # recovered bullet.
            "State/Country\t\tLicense Number\t\tStatus\t\t\tDate of Issue\t\t"
            "Date of Expiration",
            "Commonwealth of Greater Zephyria\t\t4471\t\t\tActive\t03/2011\t\t"
            "06/30/2027",
            "Board of Speculative Osteopathy\t83412\t\t\tCert 09/2015\t\t"
            "09/22/2028",
        ]),
        "extracted_fields": {
            "state_country": "Commonwealth of Greater Zephyria",
            "license_number": "4471",
            "issue_date": "2011-03",
            "expiration_date": "2027-06-30",
        },
    }


_CJK_RECORD = ("2015年4月 東京大学医学部附属病院 循環器内科 准教授として心不全の臨床研究に従事 "
               "한국어로된논문제목입니다 中华人民共和国国家自然科学基金资助项目")


def test_cjk_never_forms_a_render_token_so_cjk_lines_are_unverifiable():
    """#722: CJK has no word boundaries, so the 5-letter floor is not
    meaningful for it; CJK is excluded, not measured. A CJK-only record line
    is None (not verifiable), never False, and Latin/Cyrillic words in a
    mixed-script line still count. Invented text."""
    assert _RENDER_TOKEN_RE.findall(_norm(_CJK_RECORD)) == []
    mixed = _norm("東京大学医学部 Cardiology Иванов 心不全の臨床研究 Blorvane")
    assert set(_RENDER_TOKEN_RE.findall(mixed)) == {
        "cardiology", "иванов", "blorvane"}
    other = [set(_RENDER_TOKEN_RE.findall(_norm("Completely unrelated "
                                                "Cardiology Blorvane line")))]
    assert _record_rendered(_CJK_RECORD, "", other) is None
    latin = "Jun 2011, Quexley Cartographer Stairwells Forgotten College"
    assert _record_rendered(latin, "", other) is False


@pytest.mark.parametrize("run", [
    "ひらがなのことばです",          # Hiragana
    "カタカナノコトバデス",          # Katakana
    "ｶﾀｶﾅｶﾅｶﾅｶﾅ",                    # Halfwidth Katakana (raw, pre-NFKD)
    "한국어한ᄀ",  # Hangul Jamo (NFKD form)
    "".join(chr(0x3400 + i * 16) for i in range(12)),        # CJK extension A
    "".join(chr(0x20000 + i * 16) for i in range(12)),       # CJK extension B
    "ㄱㄲㄴㄷㄹㅁ",                  # Hangul compatibility Jamo
    "ㇰㇱㇲㇳㇴㇵ",                  # Katakana phonetic extensions
    "ꥠꥡꥢꥣꥤꥥ",                  # Hangul Jamo extended-A
    "".join(chr(0xF900 + i) for i in range(6)),              # CJK compatibility ideographs (NFC-unstable literal)
    "ힰힱힲힳힴힵ",                  # Hangul Jamo extended-B
])
def test_every_cjk_block_is_excluded_from_the_token_regex(run):
    """#722: each range in `_CJK_CLASS` must keep a 5+ run out of the token
    set on its own (the sentence-level test above hits only Han/Hangul)."""
    assert _RENDER_TOKEN_RE.findall(run) == []


_CJK_NAME_PREFIXES = ("CJK ", "HIRAGANA", "KATAKANA", "HENTAIGANA", "HANGUL",
                      "HALFWIDTH KATAKANA", "HALFWIDTH HANGUL")


def test_cjk_exclusion_matches_unicode_names_across_every_code_point():
    """#722: pins every `_CJK_CLASS` endpoint at once. A letter whose Unicode
    name is CJK/kana/Hangul never forms a token, and every other letter does
    (Bopomofo is the named, deliberate gap: its names start 'BOPOMOFO')."""
    escaped, over_excluded = [], []
    for cp in range(sys.maxunicode + 1):
        ch = chr(cp)
        if not unicodedata.category(ch).startswith("L"):
            continue
        is_cjk = unicodedata.name(ch, "").startswith(_CJK_NAME_PREFIXES)
        forms_token = _RENDER_TOKEN_RE.fullmatch(ch * 5) is not None
        if is_cjk and forms_token:
            escaped.append(hex(cp))
        elif not is_cjk and not forms_token:
            over_excluded.append(hex(cp))
    assert escaped == [], escaped[:20]
    assert over_excluded == [], over_excluded[:20]


def test_f1_dropped_license_recovered_column_header_not():
    gen = _generator()
    entry = _f1_entry()

    gen._fill_licensure([entry])
    gen._recover_unrendered_records({"F1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("83412" in b for b in bullets)          # dropped second license
    assert not any("License Number" in b for b in bullets)  # column header
    assert not any("4471" in b for b in bullets)       # rendered license


def test_two_cell_no_digit_record_recovered():
    """The 2071 D2 residual: a real dateless two-cell record (title \\t
    institution) must not be mistaken for a column-header row — only
    full-width no-digit rows (>=2 separators) are header furniture."""
    gen = _generator()
    entry = {
        "element_idx_start": 31,
        "taxonomy_code": "D2",
        "text": "\n".join([
            "Jul 2014-Present, Attending Wrangler of Nocturnal Metrics, "
            "Quexley College Medical Pavilion",
            "Visiting Lecturer of Ornamental Calculus\tNorvale Institute of "
            "Applied Whimsy",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"D2": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Ornamental Calculus" in t for t in bullets)


def test_dateless_committee_row_recovered():
    """#221 review: a real committee row with no digits and >=2 separators
    ('Member | Committee ... | Organization | description') is a record, not
    column-header furniture — only majority-label rows are skipped (see the
    F1 column-header test above)."""
    gen = _generator()
    entry = {
        "element_idx_start": 77,
        "taxonomy_code": "P",
        "text": "\n".join([
            "Member | Committee on Subterranean Balloon Safety Standards | "
            "Guild of Meandering Auditors | reviews annual certification "
            "checklists",
            "Chair | Panel of Improbable Weights and Measures 2013-2016 | "
            "Norvale Metrology Circle",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"P": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Subterranean Balloon" in t for t in bullets)


def test_near_duplicate_variant_across_entries_recovered_once():
    """#221 review: dedup drops entries precisely because they NEAR-duplicate
    a kept one, so the same unrendered record can reach recovery twice with
    punctuation variance. The first insert must count as rendered for the
    second variant."""
    gen = _generator()
    base = ("Aug 2019-Dec 2023, Associate Director of Quantitative "
            "Basketweaving, Institute of Speculative Metrics, Norvale "
            "University")
    entry_a = {
        "element_idx_start": 50,
        "taxonomy_code": "D1",
        "text": "\n".join([
            base,
            "Mar 2015-Jul 2019, Program Coordinator for Wombat Logistics, "
            "Office of Curious Ventures, Norvale University",
        ]),
        "extracted_fields": {},
    }
    entry_b = {
        "element_idx_start": 54,
        "taxonomy_code": "D1",
        "text": "\n".join([
            base.replace("Aug 2019-Dec 2023", "Aug 2019 - Dec 2023") + ".",
            "Feb 2011-Jul 2015, Keeper of Ceremonial Spreadsheets, Bureau of "
            "Ornamental Data, Quexley College",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"D1": [entry_a, entry_b]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    hits = [t for t in bullets if "Quantitative Basketweaving" in t]
    assert len(hits) == 1


# ------------------------------------------------ S bibliography anchor (#221)

def test_s_code_recovery_lands_in_bibliography_section():
    """#221 review: BIBLIOGRAPHY has no bold subsection header, and a plain
    substring fallback anchored S-code recoveries on the MENTORING section's
    '**Optional: List publications...' instruction paragraph. The recovered
    bullet must land after the real BIBLIOGRAPHY header."""
    gen = _generator()
    entry = {
        "element_idx_start": 90,
        "taxonomy_code": "S1",
        "text": "\n".join([
            "Fennwick, R., Marblegate, Q.\tOrnamental calculus for the "
            "reluctant curator\tJournal of Improbable Results\t2017",
            "Marblegate, Q., Fennwick, R.\tSubaquatic origami and its "
            "discontents\tAnnals of Speculative Metrics\t2019",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"S1": [entry]})

    paras = gen.doc.paragraphs
    texts = [p.text for p in paras]
    biblio_idx = next(i for i, t in enumerate(texts)
                      if t.strip() == "BIBLIOGRAPHY")
    optional_idx = next(i for i, t in enumerate(texts)
                        if t.strip().startswith("**Optional"))
    assert optional_idx < biblio_idx  # template invariant the bug relied on
    bullet_idxs = [i for i, (p, t) in enumerate(zip(paras, texts))
                   if _num_level(p) is not None and "Ornamental calculus" in t]
    assert bullet_idxs, "citation line was not recovered"
    assert all(i > biblio_idx for i in bullet_idxs)


# -------------------------------------------------- M2B grant table_row (2054)

def _m2b_entry():
    return {
        # Split table_rows carry STRING element_idx values plus parent keys.
        "element_idx_start": "66.16",
        "element_idx_end": "66.16",
        "parent_idx": 66,
        "table_index": 14,
        "row_index": 16,
        "element_type": "table_row",
        "hierarchy": ["PROFESSIONAL ACTIVITIES", "2. Research and Training"],
        "taxonomy_code": "M2B",
        "text": "\n".join([
            "01/01/2011-05/31/2014 | 01/01/2011-05/31/2014 | Cartography of "
            "Subterranean Cloud Formations, 5R01ZZ049917-04",
            '"Cartography of Subterranean Cloud Formations"',
            "07/01/2016-06/30/2019 | 07/01/2016-06/30/2019 | Panuvian Wetland "
            "Acoustics Survey, 3R21QX041776-02",
        ]),
        "extracted_fields": {
            "grant_number": "5R01ZZ049917-04",
            "title": "Cartography of Subterranean Cloud Formations",
            "agency": "NIOF",
            "total_funding": "$46,176",
        },
    }


def test_m2b_sibling_routed_home_extracted_grant_guarded():
    gen = _generator()
    entry = _m2b_entry()

    # Nothing rendered at all (the 2054 case: even the extracted grant's
    # tokens end up split across grant-table label/value rows). The
    # identifying-fields guard — not the token check — must keep the
    # extracted grant from being re-inserted.
    gen._recover_unrendered_records({"M2B": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Panuvian Wetland Acoustics Survey" in b for b in bullets)
    assert not any("Cartography of Subterranean" in b for b in bullets)
    # Routed to the funding subsection (the #214 precedent), not the appendix.
    header_idx = next(i for i, t in enumerate(texts)
                      if "Past (Completed) Funding" in t)
    bullet_idx = next(i for i, t in enumerate(texts) if "Panuvian" in t)
    assert bullet_idx > header_idx
    assert not any("T. APPENDIX" in t for t in texts)


def test_m2b_datelike_field_values_do_not_vouch_sibling_recovered():
    """#221 post-review (corpus CV 2054, entry 66.16): sibling records of a
    fused grant entry share their leading date ranges, so a date-like
    identifying-field value ('07/01/2016-06/30/2019', 21 chars — over the
    guard's length bar) textually present in a sibling line must not mark
    that sibling as already rendered. Same for the extracted grant's bare
    grant number, which carries no alphabetic word at all. Before the fix
    the guard vouched on these and the recovery pass skipped a genuinely
    absent grant record line."""
    gen = _generator()
    entry = {
        "element_idx_start": "66.16",
        "element_idx_end": "66.16",
        "parent_idx": 66,
        "element_type": "table_row",
        "hierarchy": ["PROFESSIONAL ACTIVITIES", "2. Research and Training"],
        "taxonomy_code": "M2B",
        "text": "\n".join([
            "07/01/2016-06/30/2019 | 07/01/2016-06/30/2019 | Cartography of "
            "Subterranean Cloud Formations, 5R01ZZ049917-04",
            "07/01/2016-06/30/2019 | 07/01/2016-06/30/2019 | Meandering "
            "Auditors Guild Junior Fellowship, 3R21QX041776-02",
        ]),
        "extracted_fields": {
            "grant_number": "5R01ZZ049917-04",
            "title": "Cartography of Subterranean Cloud Formations",
            "agency": "NIOF",
            # Extraction noise mirroring the corpus shape: a shared date
            # range landed in an identifying field.
            "award_source": "07/01/2016-06/30/2019",
        },
    }

    # Neither record rendered anywhere (the 2054 case). Only prose values may
    # block recovery: the extracted grant's own line is vouched by its title;
    # the sibling shares nothing with the fields but dates + punctuation.
    assert not segment_already_rendered(
        "07/01/2016-06/30/2019 | 07/01/2016-06/30/2019 | Meandering "
        "Auditors Guild Junior Fellowship, 3R21QX041776-02",
        entry["extracted_fields"])

    gen._recover_unrendered_records({"M2B": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    hits = [b for b in bullets if "Meandering Auditors Guild Junior" in b]
    assert len(hits) == 1  # sibling recovered, exactly once
    # The extracted grant's line still blocked by its (prose) title.
    assert not any("Cartography of Subterranean" in b for b in bullets)


def test_prose_identifying_values_still_vouch_and_block_recovery():
    """Regression for the date-like tightening: an extracted record whose
    title and organization DO appear in its record line is still treated as
    rendered — the guard must keep vouching on prose values."""
    gen = _generator()
    entry = {
        "element_idx_start": 12,
        "taxonomy_code": "P",
        "text": "\n".join([
            "Chair | Panel of Improbable Weights and Measures | Norvale "
            "Metrology Circle | 2013-2016",
            "Member | Committee on Subterranean Balloon Safety Standards | "
            "Guild of Meandering Auditors | 2017-2020",
        ]),
        "extracted_fields": {
            "title": "Panel of Improbable Weights and Measures",
            "organization": "Norvale Metrology Circle",
        },
    }

    gen._recover_unrendered_records({"P": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert not any("Improbable Weights" in b for b in bullets)
    assert any("Subterranean Balloon" in b for b in bullets)


def test_recovery_not_swallowed_by_appendix_group_head():
    """The KFGXBW re-render regression: _fill_appendix leaves bold
    'From "ACADEMIC APPOINTMENTS":' group heads at document end, and the
    subsection search must not anchor recovered D1 records there instead of
    the real positions section."""
    gen = _generator()
    entry = _d1_entry()
    gen._fill_positions({"D1": [entry]})

    # Simulate an earlier _fill_appendix run whose group head echoes the
    # source section name.
    appendix_head = gen.doc.add_paragraph()
    run = appendix_head.add_run("T. APPENDIX")
    run.bold = True
    group_head = gen.doc.add_paragraph()
    run = group_head.add_run('From "ACADEMIC APPOINTMENTS":')
    run.bold = True

    gen._recover_unrendered_records({"D1": [entry]})

    paras = gen.doc.paragraphs
    texts = [p.text for p in paras]
    header_idx = next(i for i, t in enumerate(texts)
                      if "Academic Appointments" in t)
    appendix_idx = next(i for i, t in enumerate(texts) if "T. APPENDIX" in t)
    bullet_idx = next(i for i, (p, t) in enumerate(zip(paras, texts))
                      if _num_level(p) is not None and t.startswith("Aug 2019"))
    assert header_idx < bullet_idx < appendix_idx


# ------------------------------------------------- appendix fallback + option

def test_unmapped_code_falls_back_to_appendix_untruncated():
    gen = _generator()
    entry = {
        "taxonomy_code": "F2",  # no subsection header mapping
        "text": "\n".join([
            "Jan 2012-Dec 2016, Fellowship of Ornithological Cryptography, "
            "Guild of Meandering Auditors, Crab Hollow, ZQ",
            "Feb 2017-Dec 2021, Fellowship of Subaquatic Origami, Guild of "
            "Meandering Auditors, Crab Hollow, ZQ",
        ]),
        "extracted_fields": {},
    }

    gen._recover_unrendered_records({"F2": [entry]})

    texts = [p.text for p in gen.doc.paragraphs]
    assert any("T. APPENDIX" in t for t in texts)
    # Untruncated verbatim, as a real Word list paragraph (#483) -- the
    # 200-char _fill_appendix truncation is part of what masked the KFGXBW
    # loss.
    matches = [p for p in gen.doc.paragraphs
               if p.text == "Jan 2012-Dec 2016, Fellowship of Ornithological "
                             "Cryptography, Guild of Meandering Auditors, "
                             "Crab Hollow, ZQ"]
    assert len(matches) == 1
    assert _num_level(matches[0]) == "0"
    # No other paragraph duplicates it (the pre-#483 glyph path could
    # theoretically double-insert without the exact-text match above
    # catching it, since a glyph prefix changes the string).
    assert len([t for t in texts if "Ornithological Cryptography" in t]) == 1


def test_reconsider_insert_failure_falls_back_to_appendix(monkeypatch):
    """A reconsidered segment whose code has no usable section anchor must
    land in the appendix, not vanish (#221 hardening of the #214 path)."""
    gen = _generator()
    entry = {"text": "blob", "taxonomy_code": "D9", "extracted_fields": {}}
    gen._appendix_pending = [(entry, 5.0)]
    monkeypatch.setattr(
        gen, "_reclassify_entry_segments",
        lambda text, code: [("Custodian of Improbable Ledgers, Guild of "
                             "Meandering Auditors, 2011-2014", "D9")])

    gen._reconsider_appendix_entries()

    texts = [p.text for p in gen.doc.paragraphs]
    assert any("Custodian of Improbable Ledgers" in t for t in texts)


# ------------------------------------- K3/K4/K5 teaching anchors (#1225)

# The template's own K subsection headings, written out here rather than read
# back from the map under test: `_get_wcm_section_header` once returned strings
# that were substrings of no heading ("Curriculum Development", "Other
# Teaching"), so K4/K5 segments fell to the Appendix, and K3's "Mentoring"
# matched the MENTORING section instead of "Administrative teaching".
_K_SUBSECTION_HEADINGS = {
    "K1": "Didactic teaching",
    "K2": "Clinical teaching",
    "K3": "Administrative teaching",
    "K4": "Continuing education and professional education",
    "K5": "Other education/outreach activities",
}


def _heading_above(gen, text):
    """Text of the nearest bold paragraph above the first paragraph holding
    `text` -- the subsection heading that bullet sits under."""
    paras = gen.doc.paragraphs
    idx = next(i for i, p in enumerate(paras) if text in p.text)
    for para in reversed(paras[:idx]):
        if para.text.strip() and para.runs and para.runs[0].bold:
            return para.text.strip()
    return None


def _section_header_map_codes():
    """Every code `_get_wcm_section_header` maps explicitly. The map is a local
    of that method, so read its keys from the source instead of copying them
    into the test, where the copy could drift from the map."""
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(WCMTemplateGenerator._get_wcm_section_header)))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "subsection_map"
                        for t in node.targets)):
            return list(ast.literal_eval(node.value))
    raise AssertionError("subsection_map not found in _get_wcm_section_header")


@pytest.mark.parametrize("code", sorted(_K_SUBSECTION_HEADINGS))
def test_reconsidered_k_segment_lands_under_its_own_template_subsection(code):
    gen = _generator()
    segment = f"Synthetic {code} teaching segment, Example University, 2015"

    assert gen._insert_reconsidered_segment(segment, code) is True

    assert _heading_above(gen, segment).startswith(_K_SUBSECTION_HEADINGS[code])


def test_every_section_header_map_code_anchors_in_the_blank_template():
    codes = _section_header_map_codes()
    assert {"K3", "K4", "K5"} <= set(codes), "map harvest is vacuous"
    gen = _generator()

    unanchored = [c for c in codes
                  if not gen._insert_reconsidered_segment(
                      f"Synthetic {c} segment, Example University", c)]

    assert unanchored == []


@pytest.mark.parametrize("code", ["K3", "K4", "K5"])
def test_table_cell_overflow_for_k3_k4_k5_stays_in_the_teaching_section(code):
    """The third `_get_wcm_section_header` caller: a K entry whose paragraph
    sits in a table cell is inserted at the end of the teaching section, and
    queued for the Appendix only when no header resolves."""
    gen = _generator()
    cell_para = gen.doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    text = f"Synthetic {code} overflow prose, Example University, 2015"
    gen._overflow_entries = [(
        {"taxonomy_code": code, "text": text,
         "extraction_coverage": {"extraction_coverage_percent": 12}},
        cell_para, code)]

    gen._route_overflow_entries()

    assert gen._appendix_pending == []
    assert gen.stats["overflow_bullets_added"] == 1
    texts = [p.text for p in gen.doc.paragraphs]
    start = next(i for i, t in enumerate(texts) if t.strip() == "EDUCATIONAL CONTRIBUTIONS")
    end = next(i for i, t in enumerate(texts) if t.startswith("CLINICAL PRACTICE, INNOVATION"))
    assert start < texts.index(text) < end


def test_reconsider_prompt_names_k3_k4_k5_by_their_taxonomy_labels(monkeypatch):
    """The prompt's code hint was stale in the same way as the anchors (K3
    "Mentoring/Advising", K4 "Curriculum Development"), so the model coded
    segments to meanings the taxonomy does not give those codes (#1225)."""
    prompts = []

    def fake_llm(*args, **kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return {**_PRICED_LLM_RESULT, "content": "D1: Synthetic position"}

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm", fake_llm)
    taxonomy = json.loads(
        (_SRC / "unified_pipeline" / "core" / "taxonomy_v7.json").read_text())
    labels = {c["code"]: c["label"] for c in taxonomy["codes"]}

    _generator()._reclassify_entry_segments("Synthetic position, Example University", "D1")

    hint_lines = prompts[0].splitlines()
    for code in ("K3", "K4", "K5"):
        assert any(line.startswith(f"{code}: {labels[code]}") for line in hint_lines)


def test_option_off_restores_old_behavior():
    gen = _generator(recover_unrendered_records=False)
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"D1": [_d1_entry()]})

    assert len(gen.doc.paragraphs) == before


def test_recovery_defaults_on():
    assert WCMTemplateGenerator(verbose=False).recover_unrendered_records is True


def test_single_record_entry_not_touched():
    gen = _generator()
    entry = {
        "taxonomy_code": "D1",
        "text": "Aug 2019-Dec 2023, Wrangler of Nocturnal Metrics, Quexley "
                "College",
        "extracted_fields": {},
    }
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"D1": [entry]})

    assert len(gen.doc.paragraphs) == before


# ------------------------------------------------------------ generate() wiring

def test_generate_recovers_remainder_by_default(tmp_path):
    payload = {
        "document_uid": "TESTUID221",
        "entries": [_d1_entry()],
        "cv_owner": {"full_name": "Quenby Marblegate"},
    }
    input_path = tmp_path / "TESTUID221_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "out.docx"), verbose=False)

    bullets = _bulleted_texts(Document(out).paragraphs)
    assert any("Quantitative Basketweaving" in t for t in bullets)


def test_deduped_entry_unique_record_still_recovered(tmp_path, monkeypatch):
    """The 2071 entry-168 residual: dedup keeps the longer near-duplicate and
    drops a fused sibling whose SECOND record exists nowhere else. Recovery
    must scan the pre-dedup entries so that record still surfaces."""
    monkeypatch.setattr(
        "unified_pipeline.stage_6_word_template.call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("no LLM in tests")))
    kept_entry = {
        "element_idx_start": 168,
        "taxonomy_code": "P",
        "text": ("Member | Committee on Subterranean Balloon Safety Standards"
                 " | Guild of Meandering Auditors | reviews annual protocols "
                 "and certification checklists for subterranean balloon "
                 "safety inspections across member lodges, 2013-2016"),
        "extracted_fields": {
            "role": "Member",
            "committee": "Committee on Subterranean Balloon Safety Standards",
        },
    }
    dropped_entry = {
        "element_idx_start": 171,
        "taxonomy_code": "P",
        "text": ("Member | Committee on Subterranean Balloon Safety Standards"
                 " | Guild of Meandering Auditors\n"
                 "Chair | Panel of Improbable Weights and Measures 2013-2016 "
                 "| Norvale Metrology Circle"),
        "extracted_fields": {
            "role": "Member",
            "committee": "Committee on Subterranean Balloon Safety Standards",
        },
    }
    # Preflight: the fixture must actually trigger the dedup drop (title
    # containment) so this test exercises the pre-dedup snapshot. The kept
    # text states the dropped record's 2013-2016 range: a dropped entry whose
    # date range the kept one lacks is kept, not dropped (#666).
    from unified_pipeline.stage_6_word_template import deduplicate_entries
    assert deduplicate_entries([kept_entry, dropped_entry],
                               verbose=False) == [kept_entry]

    payload = {
        "document_uid": "TESTUID221P",
        "entries": [kept_entry, dropped_entry],
        "cv_owner": {"full_name": "Quenby Marblegate"},
    }
    input_path = tmp_path / "TESTUID221P_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "dedup.docx"),
                     verbose=False)

    texts = [p.text for p in Document(out).paragraphs]
    hits = [t for t in texts if "Improbable Weights and Measures" in t]
    assert len(hits) == 1  # recovered exactly once


def test_generate_flag_off_drops_remainder(tmp_path):
    payload = {
        "document_uid": "TESTUID221",
        "entries": [_d1_entry()],
        "cv_owner": {"full_name": "Quenby Marblegate"},
    }
    input_path = tmp_path / "TESTUID221_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "off.docx"),
                     verbose=False, recover_unrendered_records=False)

    texts = [p.text for p in Document(out).paragraphs]
    # The flag being off means the record is never touched at all -- checking
    # for a plain substring, not a glyph-prefixed one, since #483 R2 removed
    # the glyph representation the recovery path could have produced.
    assert not any("Quantitative Basketweaving" in t for t in texts)


@pytest.mark.parametrize("call", ["geographic_scope", "reclassify"])
def test_stage6_llm_outage_propagates_but_other_errors_default(monkeypatch, call):
    """A provider outage past the budget fails the run (#810); any other
    call_llm error still takes the method's default."""
    from unified_pipeline.llm.retry import LLMOutageError

    gen = _generator()
    gen.cv_owner_location = {"primary_location": {"institution": "Example Medical College",
                                                  "city": "Springfield", "state": "ZZ"}}
    entry = {"text": "Visiting Lecturer", "extracted_fields": {"organization": "Example Institute"}}

    def run():
        if call == "geographic_scope":
            return gen._classify_geographic_scope(entry)
        return gen._reclassify_entry_segments("Visiting Lecturer, Example Institute, 2010", "P")

    def outage(*args, **kwargs):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm", outage)
    with pytest.raises(LLMOutageError):
        run()

    def blip(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm", blip)
    assert run() == ("National" if call == "geographic_scope" else None)


_PRICED_LLM_RESULT = {"content": '{"scope": "National"}', "cost": 0.125,
                      "prompt_tokens": 11, "completion_tokens": 5,
                      "cache_read_tokens": 2, "cache_write_tokens": 1}


@pytest.mark.parametrize("call", ["geographic_scope", "reclassify"])
def test_stage6_llm_calls_are_added_to_the_generators_usage(monkeypatch, call):
    """#1177: stage 6 reported no cost at all; every one of its call_llm sites
    now feeds the generator's LlmUsage. The scope decision makes two calls when
    the activity names no reach of its own (#1579): the named-reach step, then
    the distance step."""
    gen = _generator()
    gen.cv_owner_location = {"primary_location": {"institution": "Example Medical College",
                                                  "city": "Springfield", "state": "ZZ"}}
    entry = {"text": "Visiting Lecturer", "extracted_fields": {"organization": "Example Institute"}}
    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: dict(_PRICED_LLM_RESULT))

    if call == "geographic_scope":
        gen._classify_geographic_scope(entry)
    else:
        gen._reclassify_entry_segments("Visiting Lecturer, Example Institute, 2010", "P")

    calls = 2 if call == "geographic_scope" else 1
    assert gen.llm_usage.cost == pytest.approx(0.125 * calls)
    assert gen.llm_usage.prompt_tokens == 11 * calls
    assert gen.llm_usage.completion_tokens == 5 * calls
    assert gen.llm_usage.cache_read_tokens == 2 * calls
    assert gen.llm_usage.cache_write_tokens == 1 * calls


def test_run_stage6_hands_the_callers_usage_to_the_generator(monkeypatch):
    from unified_pipeline import stage_6_word_template as stage6
    from unified_pipeline.llm_client import LlmUsage

    seen = {}

    class _FakeGenerator:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def generate(self, *args, **kwargs):
            return "unused.docx"

    monkeypatch.setattr(stage6, "WCMTemplateGenerator", _FakeGenerator)
    usage = LlmUsage()

    stage6.run_stage6("unused.json", llm_usage=usage)

    assert seen["llm_usage"] is usage


def test_remaining_appendix_opens_with_the_shared_cviche_note_box():
    # #534/#1388: the bullet writer creates T. APPENDIX with the same CViche
    # note box as _fill_appendix -- never claiming the entries appear nowhere
    # else (stage 6 cannot prove that) -- and generate() restates its count.
    from unified_pipeline.stage6.formatting import is_cviche_box
    from unified_pipeline.stage6.sections.appendix import (
        APPENDIX_NOTE_TITLE,
        appendix_note_text,
    )

    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen._add_remaining_to_appendix([("Example Leftover Committee, 2019-2021", "T", 0.0)])
    [box] = [t for t in gen.doc.tables if is_cviche_box(t)]
    assert [p.text for p in box.cell(0, 0).paragraphs] == [APPENDIX_NOTE_TITLE, appendix_note_text(1)]
    # The box sits right after the heading, before the bullet.
    body = list(gen.doc.element.body)
    heading = next(i for i, el in enumerate(body) if el.tag.endswith("}p") and
                   "".join(t.text or "" for t in el.iter(qn("w:t"))) == "T. APPENDIX")
    assert body[heading + 1] is box._tbl

    # No blank paragraph around the box: the heading carries the space above
    # it and the first line after it the space below (#1388).
    assert body[heading].find(qn("w:pPr")).find(qn("w:spacing")).get(qn("w:after")) == "120"
    after = body[heading + 2]
    assert "".join(t.text or "" for t in after.iter(qn("w:t"))) == "Example Leftover Committee, 2019-2021"
    assert after.find(qn("w:pPr")).find(qn("w:spacing")).get(qn("w:before")) == "120"

    gen._set_appendix_note_count(7)
    assert box.cell(0, 0).paragraphs[1].text == appendix_note_text(7)


def test_the_appendix_note_counts_numbered_lines_and_every_recovered_bullet():
    """#1388: the box is written by the first writer; the reconsider pass and
    the unrendered-record recovery add bullets after it, and the box must say
    the total (the sidecar's appendix_diversion counts sum to the same)."""
    from unified_pipeline.stage6.formatting import is_cviche_box
    from unified_pipeline.stage6.sections.appendix import (
        RecoveredLine,
        appendix_note_text,
    )

    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen._write_appendix_header(1)
    gen._reconsider_appendix_entries = lambda: [RecoveredLine("T", "a"), RecoveredLine("T", "b")]
    gen._recover_unrendered_records = lambda entries_by_code, cv_owner: [RecoveredLine("D1", "c")]
    recovered = gen._recover_appendix_lines([{"text": "x", "taxonomy_code": "T"}], {}, None)
    assert len(recovered) == 3
    [box] = [t for t in gen.doc.tables if is_cviche_box(t)]
    assert box.cell(0, 0).paragraphs[1].text == appendix_note_text(4)


def _withheld_generator():
    from unified_pipeline.stage6.normalization.pii import WithheldItem

    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen._pii_result.withheld = [WithheldItem("DEA registration number", "Personal Data", 0)]
    gen._reconsider_appendix_entries = lambda: []
    return gen


def _appendix_note(gen):
    from unified_pipeline.stage6.formatting import is_cviche_box

    [box] = [t for t in gen.doc.tables if is_cviche_box(t)]
    return box.cell(0, 0).paragraphs[1].text


def test_an_appendix_holding_only_the_withheld_notice_states_no_entry_count():
    """#1582: with nothing written or recovered, the withheld notice alone
    opens the Appendix; it is not an entry, so the box restated a count of 0
    as "These 0 entries ..." above no entries. It now says what is there."""
    from unified_pipeline.stage6.pii_pass import PII_REDACTED_NOTICE
    from unified_pipeline.stage6.sections.appendix import APPENDIX_NOTE_WITHHELD_ONLY_TEXT

    gen = _withheld_generator()
    recovered = gen._recover_appendix_lines([], {}, None)

    assert recovered == []
    note = _appendix_note(gen)
    assert note == APPENDIX_NOTE_WITHHELD_ONLY_TEXT
    assert "0 entries" not in note
    assert PII_REDACTED_NOTICE in [p.text for p in gen.doc.paragraphs]


def test_the_withheld_notice_is_not_counted_beside_a_real_appendix_entry():
    """#1582: the notice beside one numbered line keeps the one-entry wording."""
    from unified_pipeline.stage6.sections.appendix import appendix_note_text

    gen = _withheld_generator()
    gen._write_appendix_header(1)
    gen._recover_appendix_lines([{"text": "x", "taxonomy_code": "T"}], {}, None)

    assert _appendix_note(gen) == appendix_note_text(1)
    assert _appendix_note(gen).startswith("This entry from your original CV")


# ------------------------------------ #530: foreign scaffolding never recovered

def test_add_remaining_to_appendix_drops_foreign_template_scaffolding():
    """The bullet writer's own filter (the recovery path, not `_fill_appendix`)
    drops another institution's instruction line and a "label: N/A" placeholder
    and still writes the genuine segment."""
    gen = _generator()
    written = gen._add_remaining_to_appendix([
        ("C. Sample Appointments (include institution, title and dates)", "T", 0.0),
        ("1. Sample Leave: N/A", "T", 0.0),
        ("Served on the sample review panel for the Example Society", "T", 0.0),
    ])
    assert [(line.code, line.text) for line in written] == [
        ("T", "Served on the sample review panel for the Example Society")]
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert "Served on the sample review panel for the Example Society" in bullets
    assert not any("Sample Appointments" in t or "Sample Leave" in t for t in bullets)


# ------------------------------------------- #1230: T-coded fused entries

_T_ROWS = [
    f"Example Grant Title {title} | Example Funder {funder} | Role {n} | {n}7% | ${n}1,500 | 19{n}1-19{n}2"
    for n, (title, funder) in enumerate((("Zeolite Catalysis", "Marigold Foundation"),
                                         ("Quartzite Weathering", "Nasturtium Institute"),
                                         ("Obsidian Fracturing", "Hyacinth Society")), start=3)
]


def _t_entry():
    return {
        "element_idx_start": 7,
        "element_type": "table_row",
        "hierarchy": ["COMPLETED GRANTS"],
        "taxonomy_code": "T",
        "text": "\n".join(_T_ROWS),
        "extraction_coverage": {"extraction_coverage_percent": 3.0},
        "extracted_fields": {},
    }


def _appendix_count(gen, text):
    """How many paragraphs of the document carry *text*."""
    return sum(text in p.text for p in gen.doc.paragraphs)


def test_t_fused_entry_rows_past_the_cap_render_once_in_the_appendix():
    """`_fill_appendix` renders a T body whole (#1230), so every row reaches
    the page and the recovery pass finds nothing to re-add."""
    gen = _generator()
    entry = _t_entry()
    assert len(entry["text"]) > 200
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    assert gen.stats["unrendered_records_recovered"] == 0
    assert all(_appendix_count(gen, row.split(" | ")[0]) == 1 for row in _T_ROWS)


def test_t_single_line_over_the_cap_is_not_printed_twice():
    """EBYSBC XELRLZ shape: a 302-character T footnote printed cut as a
    numbered item, then again whole as a recovered bullet (#1230)."""
    footnote = ("Synthetic footnote 2004: the zeolite appointment above was held "
                "jointly with the Marigold Institute, Example City, and the role "
                "carried teaching duties in quartzite petrology, field courses in "
                "obsidian sites, and a review panel seat for the Example Society.")
    assert len(footnote) > 200
    entry = {**_t_entry(), "text": footnote, "element_type": "paragraph"}
    gen = _generator()
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    assert _appendix_count(gen, footnote) == 1
    assert gen.stats["unrendered_records_recovered"] == 0


def test_t_undated_lines_and_near_duplicate_rows_past_the_cap_are_kept():
    """Undated non-record lines (a duty bullet) and sibling rows that differ
    only in a short word or a digit reach the page whole (#1230)."""
    lines = ["Synthetic duty: directed the zeolite workgroup and its quarterly reviews",
             "Member | Marigold Panel A | Example Society",
             "Member | Marigold Panel B | Example Society",
             "Member | Marigold Panel 7 | Example Society",
             "Synthetic note: obsidian outreach was shared with the nasturtium team"]
    entry = {**_t_entry(), "text": "\n".join(lines)}
    gen = _generator()
    gen._fill_appendix([entry])
    body = "\n".join(p.text for p in gen.doc.paragraphs)
    assert all(_clean_inline_tabs(line) in body for line in lines)


def test_t_row_already_on_the_page_is_not_duplicated():
    gen = _generator()
    entry = _t_entry()
    gen.doc.add_paragraph(_T_ROWS[0])  # rendered whole, e.g. by another section
    gen._recover_unrendered_records({"T": [entry]})
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert _T_ROWS[0] not in bullets
    assert _T_ROWS[1] in bullets and _T_ROWS[2] in bullets


def test_t_row_whose_title_cell_alone_renders_is_still_recovered():
    """The per-cell check treats a rendered title as the record rendered; for T
    the whole line must be accounted for, so funder and amount are not lost."""
    gen = _generator()
    entry = _t_entry()
    gen.doc.add_paragraph("Example Grant Title Quartzite Weathering")
    gen._recover_unrendered_records({"T": [entry]})
    assert _T_ROWS[1] in _bulleted_texts(gen.doc.paragraphs)


def test_whole_record_rendered_verdicts():
    row = _T_ROWS[0]
    sets = [set(_RENDER_TOKEN_RE.findall(_norm(row)))]
    assert _whole_record_rendered(row, _squash_text(row), sets) is True
    assert _whole_record_rendered(_T_ROWS[1], "", sets) is False
    assert _whole_record_rendered("A | B | 1", "", []) is None


def test_whole_record_rendered_cut_line_vouches_only_for_a_record_it_carries_whole():
    from unified_pipeline.stage6.render_check import _record_tokens
    carried = "Example Grant Title Zeolite Catalysis | Example Funder Marigold Foundation | 1987"
    cut = [_record_tokens(carried + " | extra words here ...")]
    assert _whole_record_rendered(carried, "", [], cut) is True
    longer = carried + " | with a tail the cut line lost entirely"
    assert _whole_record_rendered(longer, "", [], cut) is False


def _squash_text(text):
    from unified_pipeline.stage6.normalization import _squash
    return _squash(text)


def test_segments_cover_source_threshold():
    words = ["zeolite", "marigold", "quartzite", "nasturtium", "basalt",
             "chrysanthemum", "obsidian", "hyacinth", "feldspar", "delphinium"] * 2
    source = "\n".join(f"Synthetic Record {w}{'x' * i} Entry" for i, w in enumerate(words))
    faithful = source.split("\n")
    assert segments_cover_source(faithful, source) is True
    assert segments_cover_source(["[all 20 records follow]"], source) is False
    assert segments_cover_source(faithful[:10], source) is False
    assert segments_cover_source([], "tiny") is True  # too few tokens to judge


def test_t_header_rows_the_appendix_would_drop_are_not_recovered():
    """A T entry stage 3b confirmed as a table header row is scaffolding the
    Appendix drops; recovery must not resurrect its lines."""
    header_rows = [
        "Years Inclusive | Grant Number and Title | Source | Annual Direct Costs",
        "Years Inclusive | Years Inclusive | Grant Number and Title | Source",
    ] * 4  # past APPENDIX_MAX_CHARS, so the entry reaches the candidate scan
    entry = {
        "element_idx_start": 3, "element_type": "table_row",
        "hierarchy": ["GRANTS"], "taxonomy_code": "T",
        "text": "\n".join(header_rows),
        "classification_reasoning":
            "[T-validation confirmed] Column header row - structural artifact",
        "extraction_coverage": {"extraction_coverage_percent": 0.0},
        "extracted_fields": {},
    }
    gen = _generator()
    gen._recover_unrendered_records({"T": [entry]})
    assert not any("Years Inclusive" in t
                   for t in _bulleted_texts(gen.doc.paragraphs))


def test_t_sibling_row_differing_only_in_amount_and_years_is_recovered():
    """Two rows share title, funder and role words and differ in amount and
    years. The one on the page must not vouch for the other (#1230)."""
    base = "Example Grant Title Zeolite Catalysis | Example Funder Marigold Foundation | Site Lead | "
    on_page = base + "$1,500 | 1991-1992"
    sibling = base + "$8,200 | 1987-1988"
    entry = _t_entry()
    entry["text"] = "\n".join([on_page, sibling, _T_ROWS[1]])
    gen = _generator()
    gen.doc.add_paragraph(on_page)
    gen._recover_unrendered_records({"T": [entry]})
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert sibling in bullets
    assert on_page not in bullets


def test_t_prose_lines_past_the_cap_are_recovered():
    """Lines of a capped T entry that are not record-shaped (no pipe, tab or
    dated prefix) are recovered too: the cap cut them just the same."""
    lines = [f"Doe J. Synthetic {w} lecture series, Example University, 200{i}."
             for i, w in enumerate(("zeolite", "marigold", "quartzite", "nasturtium",
                                    "basalt", "obsidian"))]
    entry = _t_entry()
    entry["text"] = "\n".join(lines)
    gen = _generator()
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    body = "\n".join(p.text for p in gen.doc.paragraphs)
    assert all(line in body for line in lines)


def test_t_single_record_entry_cut_by_the_cap_is_recovered():
    long_line = ("Doe J. 2003. Synthetic invited lecture on zeolite catalysis at the Example "
                 "Symposium, hosted by the Marigold Institute, Example City, with a "
                 "long tail of detail that lies well past the two hundred character cap.")
    entry = _t_entry()
    entry["text"] = long_line
    gen = _generator()
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    assert long_line in "\n".join(p.text for p in gen.doc.paragraphs)


def test_t_undated_prose_and_undated_merged_cell_rows_are_not_recovered():
    """A line with no year and no row shape (template text, an objective) is
    not a record, and neither is a merged-cell row that collapses to one such
    cell: recovery leaves both to the Appendix pointer."""
    prose = ("Example instruction: list every activity in reverse order and "
             "describe each briefly, with the role held and the venue used. " * 2)
    merged = " | ".join(["Example label for a category of activities"] * 3)
    entry = _t_entry()
    entry["text"] = "\n".join([prose, merged])
    gen = _generator()
    gen._recover_unrendered_records({"T": [entry]})
    assert gen.stats["unrendered_records_recovered"] == 0
    body = "\n".join(p.text for p in gen.doc.paragraphs)
    assert "Example instruction" not in body and "Example label for" not in body


def test_t_merged_cell_description_after_a_record_is_recovered_once():
    """A description spanning six grid columns is flattened to six copies; the
    recovered line carries it once."""
    description = ("Funding for a synthetic pilot study of zeolite weathering "
                   "in Example County, used to collect first-round samples.")
    entry = _t_entry()
    entry["text"] = "\n".join([_T_ROWS[0], " | ".join([description] * 6), _T_ROWS[1]])
    gen = _generator()
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    body = "\n".join(p.text for p in gen.doc.paragraphs)
    assert description in body
    assert description + " | " + description not in body


def test_t_description_row_after_a_record_is_recovered():
    """An undated description row directly after a grant row travels with it."""
    description = ("Funding for a synthetic pilot study of zeolite weathering "
                   "in Example County, used to collect first-round samples.")
    entry = _t_entry()
    entry["text"] = "\n".join([_T_ROWS[0], description, _T_ROWS[1]])
    gen = _generator()
    gen._fill_appendix([entry])
    gen._recover_unrendered_records({"T": [entry]})
    assert description in "\n".join(p.text for p in gen.doc.paragraphs)


def test_t_entry_under_the_cap_is_left_alone():
    entry = _t_entry()
    entry["text"] = "Doe J. Short synthetic note, Example University."
    gen = _generator()
    gen._recover_unrendered_records({"T": [entry]})
    assert gen.stats["unrendered_records_recovered"] == 0


def test_segments_cover_source_rejects_a_reply_that_summarises_a_few_lines():
    """17 of 20 lines kept verbatim plus one bracketed summary: pooled coverage
    is high, but three source lines are carried by no segment (#1230)."""
    words = ["zeolite", "marigold", "quartzite", "nasturtium", "basalt",
             "chrysanthemum", "obsidian", "hyacinth", "feldspar", "delphinium",
             "granite", "snapdragon", "tourmaline", "gardenia", "pumice",
             "lavender", "dolomite", "begonia", "gypsum", "camellia"]
    lines = [f"Doe J. Synthetic {w} outcomes. Journal of Invented {w.title()} stuff {i}."
             for i, w in enumerate(words)]
    source = "\n".join(lines)
    reply = lines[:17] + ["[3 further publications follow]"]
    assert segments_cover_source(lines, source) is True
    assert segments_cover_source(reply, source) is False


def test_segments_cover_source_ignores_a_repeated_page_header_the_reply_drops():
    """A page-break header repeats in the source and a faithful reply rightly
    drops it; that must not reject the reply (#1230)."""
    pairs = (("zeolite", "geyser"), ("marigold", "estuary"), ("quartzite", "lagoon"),
             ("nasturtium", "tundra"), ("basalt", "savanna"), ("obsidian", "fjord"),
             ("hyacinth", "canyon"), ("feldspar", "glacier"))
    lines = [f"Doe J. Synthetic {a} and {b} outcomes {i}." for i, (a, b) in enumerate(pairs)]
    header = "Example Header Continued"
    source = "\n".join(lines[:4] + [header] + lines[4:] + [header])
    assert segments_cover_source(lines, source) is True
    assert segments_cover_source(lines[:-1], source) is False


def test_segments_cover_source_rejects_a_reply_that_drops_a_near_duplicate_sibling():
    """Six committee rows told apart by one word: a reply that keeps five of
    them carries 92% of the sixth row's tokens, so the pooled per-line share
    forgave the loss. A row with a sibling needs every one of its tokens in
    the reply (#1230)."""
    names = ("Zeolite", "Marigold", "Quartzite", "Nasturtium", "Obsidian", "Hyacinth")
    rows = [f"Member | {n} Committee | Example Department of Synthetic Geology | "
            f"Example College of Sciences | Example University | 1987-1992" for n in names]
    source = "\n".join(rows)
    assert segments_cover_source(rows, source) is True
    assert segments_cover_source(rows[:-1], source) is False
    assert segments_cover_source(rows[1:], source) is False
    # Two sibling rows merged into one segment still carry both whole.
    assert segments_cover_source(["; ".join(rows[:2])] + rows[2:], source) is True


def test_segments_cover_source_accepts_a_sibling_row_split_across_segments():
    """The prompt asks for one segment per responsibility, so a reply that
    keeps every token of a sibling row but splits it at its separators is a
    faithful reply and must not be rejected (#1230). Only a lost token fails."""
    names = ("Zeolite", "Marigold", "Quartzite", "Nasturtium", "Obsidian", "Hyacinth")
    rows = [f"Director, {n} Laboratory, Example Hospital, 2004-2010; supervised residents; "
            f"chaired the quality committee" for n in names]
    source = "\n".join(rows)
    split = [part for row in rows for part in row.split("; ")]
    assert segments_cover_source(split, source) is True
    # A split reply that loses the one word telling a sibling apart is rejected.
    lost = [part.replace("Hyacinth", "") for part in split]
    assert segments_cover_source(lost, source) is False


def test_segments_cover_source_short_line_counts_as_carried():
    """A line too short to verify (under RENDER_TOKEN_MIN_COUNT tokens) cannot
    be held against the reply, though the long lines are all kept (#1230)."""
    pairs = (("zeolite", "geyser"), ("marigold", "estuary"), ("quartzite", "lagoon"),
             ("nasturtium", "tundra"), ("basalt", "savanna"), ("obsidian", "fjord"))
    lines = [f"Doe J. Synthetic {a} and {b} outcomes {i}." for i, (a, b) in enumerate(pairs)]
    source = "\n".join(lines + ["Page 2"])
    assert segments_cover_source(lines, source) is True


def test_segments_cover_source_pools_tokens_for_a_line_without_a_sibling():
    """The all-tokens rule is for sibling rows only: a line with no near
    duplicate keeps the pooled share, so a reply that splits it across two
    segments, or loses one of its five tokens (0.8 carried), is still
    believed (#1230)."""
    triples = (("zeolite", "geyser", "basalt"), ("marigold", "estuary", "savanna"),
               ("quartzite", "lagoon", "granite"), ("nasturtium", "tundra", "prairie"),
               ("obsidian", "fjord", "meadow"), ("hyacinth", "glacier", "plateau"))
    lines = [f"Synthetic {a} {b} {c} outcomes." for a, b, c in triples]
    source = "\n".join(lines)
    halves = [half for line in lines for half in line.split(" ", 2)]
    assert segments_cover_source(halves, source) is True
    # One token of a non-sibling line lost: 4 of 5 still carried, accepted.
    # A strict all-tokens rule on every line would reject this reply.
    lossy = [line.replace("geyser ", "") for line in lines]
    assert segments_cover_source(lossy, source) is True
    # Two tokens lost from the same line: 3 of 5, rejected.
    assert segments_cover_source([line.replace("geyser ", "").replace("basalt ", "")
                                  for line in lines[:1]] + lines[1:], source) is False


def test_t_sibling_rows_differing_only_in_one_name_are_all_recovered():
    """Committee rows with the same years and place, told apart by one word:
    the complete row on the page must not vouch for its siblings (#1230)."""
    names = ("Zeolite", "Marigold", "Quartzite", "Nasturtium", "Obsidian", "Hyacinth")
    rows = [f"Member | {n} Committee | Example Department of Synthetic Geology | Example College of Sciences | Example University | 1987-1992"
            for n in names]
    entry = _t_entry()
    entry["text"] = "\n".join(rows)
    gen = _generator()
    gen.doc.add_paragraph(rows[0])
    gen._recover_unrendered_records({"T": [entry]})
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert all(row in bullets for row in rows[1:])
    assert rows[0] not in bullets


def test_t_row_that_is_its_own_non_t_entry_is_not_recovered():
    """A T entry that repeats a table whose rows are entries of their own: the
    row renders from its entry, reformatted, so recovering it duplicates it."""
    row = _T_ROWS[1]
    own = dict(_t_entry(), taxonomy_code="N", text=row)
    entry = _t_entry()
    entry["text"] = "\n".join(_T_ROWS)
    gen = _generator()
    gen.doc.add_paragraph("Reformatted Zeolite")  # the entry rendered, differently
    gen._recover_unrendered_records({"T": [entry], "N": [own]})
    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert row not in bullets
    assert _T_ROWS[2] in bullets


# --- #1205: supplementary prose as tracked-deleted sub-points -----------------
# The generator glue for stage6/supplementary.py: the flag, the pass's place
# before the overflow routing and this recovery, and both skipping what a
# sub-point carries. The sub-point module itself is test_stage6_supplementary.py.

_DUTY_PROSE = ("Responsible for coordinating the departmental curriculum committee "
               "and supervising graduate trainees in the example programme")


def _duty_appointment():
    """A D1 row whose source text ends in duty prose no field holds, split
    over two record-shaped lines the way SQMWHM 29's was (YUYVIG)."""
    return {
        "element_idx_start": 29,
        "taxonomy_code": "D1",
        "hierarchy": ["PROFESSIONAL EXPERIENCE"],
        "text": ("July 2010 - present\tAssociate Professor and Program Director, Department of "
                 f"Example Studies, Norvale University\nMain Annex, ZQ.\t{_DUTY_PROSE}"),
        "extraction_coverage": {"extraction_coverage_percent": 21.0},
        "extracted_fields": {
            "title": "Associate Professor and Program Director",
            "institution": "Department of Example Studies, Norvale University",
            "start_date": "2010-07",
            "end_date": "present",
        },
    }


def _subpoint_rows(gen):
    author = "CViche: source text with no template field"
    return [tr for tr in gen.doc.element.body.iter(qn("w:tr"))
            if (d := tr.find(f"{qn('w:trPr')}/{qn('w:del')}")) is not None
            and d.get(qn("w:author")) == author]


def _render_duty_appointment(**kwargs):
    gen = _generator(**kwargs)
    entry = _duty_appointment()
    gen._fill_positions({"D1": [entry]})
    gen._add_supplementary_subpoints({"D1": [entry]})
    return gen, entry


def test_supplementary_subpoints_default_off():
    assert WCMTemplateGenerator(verbose=False).supplementary_subpoints is False


def test_run_stage6_passes_the_subpoint_flag_to_the_generator(monkeypatch):
    from unified_pipeline import stage_6_word_template as stage6

    seen = {}

    class _FakeGenerator:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def generate(self, *args, **kwargs):
            return "unused.docx"

    monkeypatch.setattr(stage6, "WCMTemplateGenerator", _FakeGenerator)
    stage6.run_stage6("unused.json")
    assert seen["supplementary_subpoints"] is True  # on by default since 2026-10-09
    stage6.run_stage6("unused.json", supplementary_subpoints=False)
    assert seen["supplementary_subpoints"] is False


def test_duty_prose_is_a_deleted_row_under_its_appointment():
    gen, _entry = _render_duty_appointment(supplementary_subpoints=True)

    rows = _subpoint_rows(gen)
    assert len(rows) == 1
    above = rows[0].getprevious()
    assert "Associate Professor and Program Director" in "".join(
        t.text for t in above.iter(qn("w:t")))
    assert "".join(t.text for t in rows[0].iter(qn("w:delText"))) == _DUTY_PROSE
    assert gen.stats["supplementary_subpoints"] == 1
    assert gen._subpoint_lines and gen._subpoint_lines[0].endswith(_DUTY_PROSE)


@pytest.mark.parametrize("kwargs", [{}, {"supplementary_subpoints": True, "emit_track_changes": False}])
def test_no_sub_point_with_the_flag_off_or_track_changes_off(kwargs):
    gen, _entry = _render_duty_appointment(**kwargs)
    assert _subpoint_rows(gen) == []
    assert "supplementary_subpoints" not in gen.stats
    assert gen._subpoint_lines == [] and gen._subpoint_entry_ids == set()


@pytest.mark.parametrize("flag, queued", [(True, 0), (False, 1)])
def test_the_overflow_pass_skips_an_entry_whose_prose_is_a_sub_point(flag, queued):
    gen, entry = _render_duty_appointment(supplementary_subpoints=flag)
    cell_para = gen.doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    gen._overflow_entries = [(entry, cell_para, "D1")]

    gen._route_overflow_entries()

    assert len(gen._appendix_pending) == queued


@pytest.mark.parametrize("flag, recovered", [(True, 0), (False, 1)])
def test_the_recovery_reads_a_sub_point_as_its_entry_rendered(flag, recovered):
    gen, entry = _render_duty_appointment(supplementary_subpoints=flag)

    gen._recover_unrendered_records({"D1": [entry]})

    assert gen.stats["unrendered_records_recovered"] == recovered


_TITLE_ROLE = "Visiting Fellow in Example Science"
_TITLE_DUTIES = "to coordinate the example curriculum committee"
#: The source states the duties at more length than stage 4 kept in `title`,
#: so the text's duty paragraph is not mostly field values: only the title
#: row's sub-point in the pass's haystack keeps it from being planned again.
_TEXT_DUTIES = (f"{_TITLE_DUTIES}, supervise graduate trainees, organise regional "
                "seminars, maintain programme records and evaluate outcomes")


def _title_duty_appointment():
    """A D3 row whose `title` holds duties (#1641's split), and whose text
    holds them too, as DYLJXC 661/668's did (YUYVIG)."""
    return {
        "element_idx_start": 661,
        "taxonomy_code": "D3",
        "hierarchy": ["PROFESSIONAL EXPERIENCE"],
        "text": f"2013-2015\t{_TITLE_ROLE}, Example Policy Office\t{_TEXT_DUTIES}",
        "extracted_fields": {
            "title": f"{_TITLE_ROLE}, Example Policy Office, {_TITLE_DUTIES}",
            "organization": "Example Institute for Science",
            "start_date": "2013", "end_date": "2015",
        },
    }


def test_title_duties_are_offered_once_and_read_as_rendered():
    """#1205: the duties the positions writer made a sub-point join the pass's
    haystack, so they are not planned again from the entry's text, and the
    #221 recovery reads them as the entry rendered."""
    gen = _generator(supplementary_subpoints=True)
    entry = _title_duty_appointment()
    gen._fill_positions({"D3": [entry]})
    gen._add_supplementary_subpoints({"D3": [entry]})

    rows = _subpoint_rows(gen)
    texts = ["".join(t.text for t in row.iter(qn("w:delText"))) for row in rows]
    assert texts[0].endswith(_TITLE_DUTIES)
    # No second sub-point repeats the duties the title row already offers.
    assert [t for t in texts[1:] if _TITLE_DUTIES in t] == []
    assert gen.stats["supplementary_subpoints"] == len(rows)
    gen._recover_unrendered_records({"D3": [entry]})
    assert gen.stats["unrendered_records_recovered"] == 0


def test_sub_point_anchors_stop_at_the_appendix_heading(monkeypatch):
    """The Appendix repeats entry text in bullets: no place for a sub-point."""
    from unified_pipeline.stage6 import supplementary

    gen = _generator(supplementary_subpoints=True)
    gen._write_appendix_header(1)
    appendix = gen.doc.paragraphs[gen._find_header_paragraph("T. APPENDIX")]._p
    seen = []
    real = supplementary.anchor_candidates

    def spy(doc, stop_before=None):
        seen.append(stop_before)
        return real(doc, stop_before)

    monkeypatch.setattr(supplementary, "anchor_candidates", spy)
    gen._add_supplementary_subpoints({"D1": [_duty_appointment()]})
    assert seen == [appendix]


# --- #1205: Q1/Q2 extramural service, the next code family -------------------
# Both render as table rows in Q's tables, so the prose is a deleted row under
# the entry's own row, spanning that table's columns.

_SERVICE_PROSE = ("Advised the society on regional workforce planning and drafted "
                  "the annual training standards for member programmes")


def _board_service():
    """A Q2 row whose source line goes on past its committee, role and
    organization into the committee's remit, as YUYVIG ECXGAT 2129's did."""
    return {
        "element_idx_start": 2129,
        "taxonomy_code": "Q2",
        "text": f"2015-2018\tMember, Example Advisory Committee, Example Society\t{_SERVICE_PROSE}",
        "extracted_fields": {"committee_name": "Example Advisory Committee", "role": "Member",
                             "organization": "Example Society",
                             "start_date": "2015", "end_date": "2018"},
    }


def _extramural_leadership():
    """A Q1 row whose source holds the organization's mission, as EBYSBC
    RGUNJV 3057's did."""
    return {
        "element_idx_start": 3057,
        "taxonomy_code": "Q1",
        "text": f"2009-2014\tExample Foundation, President\t{_SERVICE_PROSE}",
        "extracted_fields": {"organization": "Example Foundation", "role": "President",
                             "start_date": "2009", "end_date": "2014"},
    }


def _texts_of(body):
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p"))]


def _render_service(code, entry, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_service({code: [entry]})
    gen._add_supplementary_subpoints({code: [entry]})
    return gen


@pytest.mark.parametrize("code, entry, anchor_text, columns", [
    ("Q2", _board_service, "Example Advisory Committee", 4),
    ("Q1", _extramural_leadership, "Example Foundation", 3),
])
def test_service_prose_is_a_deleted_row_under_its_own_row(code, entry, anchor_text, columns):
    gen = _render_service(code, entry(), supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = row.getprevious()
    assert anchor_text in "".join(t.text for t in above.iter(qn("w:t")))
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _SERVICE_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == str(columns)
    assert gen.stats["supplementary_subpoints"] == 1


@pytest.mark.parametrize("code, entry", [("Q2", _board_service), ("Q1", _extramural_leadership)])
def test_service_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it(code, entry):
    off = _render_service(code, entry(), supplementary_subpoints=False)
    on = _render_service(code, entry(), supplementary_subpoints=True)

    # Reject All: the deleted text is the reader's again.
    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _SERVICE_PROSE in rejected
    assert _SERVICE_PROSE not in "".join(_texts_of(off.doc.element.body))
    # Accept All: the deleted row goes, and the document is the flag-off one,
    # empty paragraphs included.
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)


def test_service_prose_already_in_its_cell_is_not_offered_again():
    """A Q2 row with no committee field shows the entry's whole text in its
    cell (`_fill_service_boards`' fallback): nothing is left to offer."""
    from unified_pipeline.stage6 import supplementary

    entry = _board_service()
    entry["extracted_fields"] = {"start_date": "2015", "end_date": "2018"}
    gen = _render_service("Q2", entry, supplementary_subpoints=True)
    assert _subpoint_rows(gen) == []
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry, "Q2", haystack) == []


def test_journal_reviewing_q4d_stays_excluded():
    entry = dict(_board_service(), taxonomy_code="Q4D")
    entry["extracted_fields"] = {"journal_name": "Example Journal of Training", "role": "Reviewer"}
    gen = _render_service("Q4D", entry, supplementary_subpoints=True)
    assert _subpoint_rows(gen) == []


def _journal_reviewer_coded_q2():
    """A Q2 entry `_route_q2_entries` reroutes to Journal Reviewing: its
    rendered line is a Q4D row, but its own code stays Q2."""
    return {
        "element_idx_start": 1514,
        "taxonomy_code": "Q2",
        "text": f"2015-2018\tReviewer, Example Journal of Training\t{_SERVICE_PROSE}",
        "extracted_fields": {"role": "Reviewer", "organization": "Example Journal of Training",
                             "start_date": "2015", "end_date": "2018"},
    }


def test_a_q2_entry_rendered_as_journal_reviewing_gets_no_sub_point():
    """The 2026-10-02 decision excludes journal reviewing. A Q2 entry the
    section writes into the Journal Reviewing table is that, whatever its
    code says, so it is excluded by where it rendered."""
    from unified_pipeline.stage6.sections.service import _route_q2_entries

    entry = _journal_reviewer_coded_q2()
    assert len(_route_q2_entries([entry])[0]) == 1
    gen = _render_service("Q2", entry, supplementary_subpoints=True)
    assert "Example Journal of Training" in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    assert _SERVICE_PROSE not in "".join(
        t.text or "" for t in gen.doc.element.body.iter(qn("w:delText")))


# --- #1205: H honors, the next code family -----------------------------------
# The honors section writes every H entry as a row of its three-column table
# (`_add_honors_row`), so the prose is a deleted row under the award's row.

_HONOR_PROSE = ("Awarded annually to an early career investigator whose published "
                "research advanced clinical understanding of respiratory infections")


def _honor():
    """An H row whose source line goes on past the award, its granting body
    and year into the award's criteria, as EBYSBC QITQWH 39-53's did."""
    return {
        "element_idx_start": 39,
        "taxonomy_code": "H",
        "text": f"2019\tYoung Investigator Award, Example Thoracic Society\t{_HONOR_PROSE}",
        "extracted_fields": {"award_name": "Young Investigator Award",
                             "granting_body": "Example Thoracic Society", "date": "2019"},
    }


def _render_honors(entries, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_honors(entries)
    gen._add_supplementary_subpoints({"H": entries})
    return gen


def test_honor_prose_is_a_deleted_row_under_the_award_row():
    gen = _render_honors([_honor()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = "".join(t.text for t in row.getprevious().iter(qn("w:t")))
    assert "Young Investigator Award" in above
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _HONOR_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "3"
    assert gen.stats["supplementary_subpoints"] == 1


def test_honor_prose_goes_under_its_own_award_among_several():
    other = {"element_idx_start": 41, "taxonomy_code": "H",
             "text": "2015\tOutstanding Mentor Award, Example Medical College",
             "extracted_fields": {"award_name": "Outstanding Mentor Award",
                                  "granting_body": "Example Medical College", "date": "2015"}}
    gen = _render_honors([other, _honor()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = "".join(t.text for t in row.getprevious().iter(qn("w:t")))
    assert "Young Investigator Award" in above and "Mentor" not in above
    below = row.getnext()
    assert below is not None and "Outstanding Mentor Award" in "".join(
        t.text for t in below.iter(qn("w:t")))


def test_honor_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_honors([_honor()], supplementary_subpoints=False)
    on = _render_honors([_honor()], supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _HONOR_PROSE in rejected
    assert _HONOR_PROSE not in "".join(_texts_of(off.doc.element.body))
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)


def test_honor_prose_already_in_its_award_cell_is_not_offered_again():
    """With no award field, the award cell shows the entry's whole text
    (`parse_honor_entry`'s fallback): nothing is left to offer."""
    from unified_pipeline.stage6 import supplementary

    entry = _honor()
    entry["extracted_fields"] = {"date": "2019"}
    gen = _render_honors([entry], supplementary_subpoints=True)
    assert _HONOR_PROSE in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry, "H", haystack) == []


def _rerouted(entry, expected_code, heading):
    """`entry` flagged by stage 3b's hierarchy check, at low confidence, under
    a heading that expects `expected_code`: `_correct_mismatch_if_needed`
    moves it across families."""
    return dict(entry, taxonomy_confidence=0.5, hierarchy_mismatch_flag=True,
                hierarchy_mismatch_detail={"expected_codes": [expected_code],
                                           "hierarchy": [heading]})


def _render_grouped(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_honors(grouped.get("H", []))
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def test_an_honor_rerouted_out_of_h_follows_its_destination_and_gets_no_sub_point():
    """An H entry rendered as another code is that code's record: N1 is
    excluded, so neither its own row nor another award's carries its prose."""
    other = {"element_idx_start": 41, "taxonomy_code": "H",
             "text": "2015\tYoung Investigator Award, Example Thoracic Society",
             "extracted_fields": {"award_name": "Young Investigator Award",
                                  "granting_body": "Example Thoracic Society", "date": "2015"}}
    moved = _rerouted(_honor(), "N1", "MENTORING PROGRAMS")
    gen, grouped = _render_grouped([other, moved], supplementary_subpoints=True)

    assert grouped["N1"] == [moved] and grouped["H"] == [other]
    assert _subpoint_rows(gen) == []
    assert _HONOR_PROSE not in "".join(
        t.text or "" for t in gen.doc.element.body.iter(qn("w:delText")))


def test_a_record_rerouted_into_h_follows_the_honors_rule():
    """A society fellowship stage 3b coded I under an honors heading renders
    as an award: its prose is a deleted row under that award's row."""
    fellowship = dict(_honor(), taxonomy_code="I")
    gen, grouped = _render_grouped([_rerouted(fellowship, "H", "HONORS")],
                                   supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["H"]] == [39]
    (row,) = _subpoint_rows(gen)
    assert "Young Investigator Award" in "".join(t.text for t in row.getprevious().iter(qn("w:t")))
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _HONOR_PROSE


def test_an_honor_whose_award_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass dropped the award's name, so the honors writer
    renders no row for what is left. Its prose is not offered either, under
    another award of the same granting body or anywhere else."""
    other = {"element_idx_start": 41, "taxonomy_code": "H",
             "text": "2015\tOutstanding Mentor Award, Example Thoracic Society",
             "extracted_fields": {"award_name": "Outstanding Mentor Award",
                                  "granting_body": "Example Thoracic Society", "date": "2015"}}
    withheld = _honor()
    del withheld["extracted_fields"]["award_name"]
    withheld["_pii_dropped_fields"] = ["award_name"]
    gen = _render_honors([other, withheld], supplementary_subpoints=True)

    assert "Young Investigator Award" not in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    assert _HONOR_PROSE not in "".join(
        t.text or "" for t in gen.doc.element.body.iter(qn("w:delText")))


# ------------------------------------------- date-led sibling records (#983)
# Stage 2 joins an entry's paragraphs with tabs, so three society records that
# each open with their own date reach stage 6 as one line. Stage 4 extracted
# the last one only (the web228/web26 I-entry shape, synthetic names).

_SOCIETY_RECORDS = [
    "2001-    Guild of Meandering Auditors (GMA)",
    "2004-2008  Executive Board Member, Treasurer.",
    "2007-2009 President, Organizing Committee, 44th Annual Meeting, GMA, Crab Hollow, ZQ",
]


def _society_entry(code="I", **fields):
    return {
        "element_idx_start": 60,
        "element_idx_end": 62,
        "element_type": "paragraph",
        "hierarchy": ["PROFESSIONAL MEMBERSHIPS"],
        "taxonomy_code": code,
        "taxonomy_confidence": 0.9,
        "text": "\t".join(_SOCIETY_RECORDS),
        "extracted_fields": fields or {
            "organization": "Guild of Meandering Auditors (GMA)",
            "start_date": "2007",
            "end_date": "2009",
            "membership_type": "President, Organizing Committee, 44th Annual Meeting",
        },
        "extraction_coverage": {"extraction_coverage_percent": 40.0},
    }


def _all_paragraph_texts(path):
    """Every paragraph's w:t text, table cells and tracked insertions included."""
    body = Document(path).element.body
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p"))]


def test_date_led_sibling_records_render_one_line_each(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "unified_pipeline.stage_6_word_template.call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "TESTUID983", "entries": [_society_entry()],
               "cv_owner": {"full_name": "Quenby Marblegate"}}
    input_path = tmp_path / "TESTUID983_fields.json"
    input_path.write_text(json.dumps(payload))

    texts = _all_paragraph_texts(run_stage6(str(input_path), str(tmp_path / "out.docx"),
                                            verbose=False))

    # Each record is its own paragraph, once: the 2001 membership stage 4 did
    # not extract renders, and the Treasurer record is not recovered a second
    # time beside it.
    for record in _SOCIETY_RECORDS:
        assert texts.count(record) == 1, record
    assert not [t for t in texts if "Treasurer" in t and "President" in t]


def _grouped_text(entry):
    gen = _generator()
    grouped = gen._group_and_cut_entries([entry])
    (only,) = [e for entries in grouped.values() for e in entries]
    return only


def test_grouping_puts_date_led_siblings_on_lines_and_keeps_stage2_text():
    entry = _grouped_text(_society_entry())
    assert entry["text"] == "\n".join(_SOCIETY_RECORDS)
    assert entry[DATE_LED_SOURCE_TEXT] == "\t".join(_SOCIETY_RECORDS)


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C", "M2D"])
def test_a_grant_keeps_its_tabs(code):
    entry = _grouped_text(_society_entry(code=code, title="Example Grant"))
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)
    assert DATE_LED_SOURCE_TEXT not in entry


@pytest.mark.parametrize("code", ["K1", "K2", "K4"])
def test_a_teaching_entry_keeps_its_tabs(code):
    # The teaching renderer splits the text on its tabs itself.
    entry = _grouped_text(_society_entry(code=code, course_title="Example Course"))
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)


def test_records_stage4_separated_keep_their_tabs():
    # A list of records under a key the I schema does not define: fan-out
    # declines it (no schema key), and its renderer, not the text, owns them.
    entry = _grouped_text(_society_entry(
        organization="Guild of Meandering Auditors (GMA)",
        offices=[{"office": "Treasurer", "start_date": "2004"},
                 {"office": "President", "start_date": "2007"}]))
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)


@pytest.mark.parametrize("marker", [FANNED_OUT_FROM, LAST_STAGE4_RECORD])
def test_a_record_fan_out_made_keeps_its_tabs(marker):
    # PR #1850 review: no test held the fan-out exclusion. A record fan-out
    # made is one of stage 4's records; dedup reads its marker, not its lines.
    entry = _society_entry()
    entry[marker] = {"key": "records", "index": 0, "count": 2}
    entry = _grouped_text(entry)
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)
    assert DATE_LED_SOURCE_TEXT not in entry


# The web181 Q1 shape: a long lead-in, then dated offices, most of them
# single years. #1243's tab-part rules read it as one record (fewer than half
# its parts give a date range); its date-led lines would read as two.
_FORUM_PARTS = [
    "Example Rehabilitation Forum. A not-for-profit consortium of academic departments",
    "2005-present Founding President.",
    "2009 Conference Director: First World Conference, Example City.",
    "2011 Conference Director: World Congress, Example City",
    "2012 Conference co-director, World Congress, Example City",
    "2016-present Example Fellowship Director",
]


def test_recovery_reads_stage2_text_not_the_date_led_lines():
    entry = _grouped_text({
        "taxonomy_code": "Q1",
        "text": "\t".join(_FORUM_PARTS),
        "extracted_fields": {"role": "Example Fellowship Director",
                             "organization": "Example Rehabilitation Forum",
                             "start_date": "2016", "end_date": "present"},
    })
    assert "\n" in entry["text"]  # the lines render ...
    # ... but the recovery pass reads records as it did before the split.
    assert recovery_candidates("Q1", entry) == []
    lines_only = {**entry}
    del lines_only[DATE_LED_SOURCE_TEXT]
    assert recovery_candidates("Q1", lines_only) != []


def test_a_date_led_part_the_fields_hold_is_not_recovered_after_the_split():
    # The mentee shape of `test_tab_part_the_extracted_fields_hold_is_not_recovered`,
    # with both parts date-led, so grouping puts them on two lines. Stage 4
    # holds every word of both; the pass still sees them as stage 2's tab parts.
    gen = _generator()
    entry = {
        "taxonomy_code": "N3B",
        "text": ("2007-2011 Quimby Farrow, MSPH, PhD (Epidemiology)\t"
                 "2009-2011 Marsupial Heart Association Fellowship"),
        "extracted_fields": {"mentee_name": "Quimby Farrow", "mentee_level": "MSPH, PhD (Epidemiology)",
                             "start_date": "2007", "end_date": "2011",
                             "funding_source": "Marsupial Heart Association Fellowship (2009-2011)"},
    }
    grouped = gen._group_and_cut_entries([entry])
    assert "\n" in grouped["N3B"][0]["text"]
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records(grouped)

    assert len(gen.doc.paragraphs) == before


@pytest.mark.parametrize("code", ["Q1", "D1"])
@pytest.mark.parametrize("pii", ["SSN: 123-45-6789", "Date of Birth: 01/02/1960"])
def test_a_withheld_fragment_in_a_date_led_record_is_not_recovered(tmp_path, monkeypatch,
                                                                   code, pii):
    # PR #1850 review: the PII pass cut `text`, but the recovery pass read the
    # pre-split stage-2 text and put the withheld line back verbatim.
    monkeypatch.setattr(
        "unified_pipeline.stage_6_word_template.call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    entry = _society_entry(code=code)
    entry["text"] = "\t".join([_SOCIETY_RECORDS[0], f"{_SOCIETY_RECORDS[1]} {pii}",
                               _SOCIETY_RECORDS[2]])
    payload = {"document_uid": "TESTUID983P", "entries": [entry],
               "cv_owner": {"full_name": "Quenby Marblegate"}}
    input_path = tmp_path / "TESTUID983P_fields.json"
    input_path.write_text(json.dumps(payload))

    texts = _all_paragraph_texts(run_stage6(str(input_path), str(tmp_path / "out.docx"),
                                            verbose=False))

    value = pii.split(": ", 1)[1]
    assert not [t for t in texts if value in t]


# ------------------- #983 review round 2: the PII pass sees stage 2's tabs
# The pass's bare-label extension and its continuation-cell absorption run on
# over a tab and stop at a line break. A split that ran first turned the tab
# after "Children:" or "Date of Birth:" into a line break, and the value in
# the next cell rendered. Each case renders twice: as the branch does, and with
# the split switched off, which is dev's grouping and PII pass unchanged
# (stage6/pii_pass.py is dev's file). The pass must get the same input, cut the
# same spans, and leave every protected value in the docx the same number of
# times. Synthetic names and values throughout.

_PII_DATED_LEAD = ["2001-2005 Member, Guild of Meandering Auditors",
                   "2006-2008 Treasurer, Guild of Meandering Auditors"]
_PII_UNDATED_LEAD = ["Member, Guild of Meandering Auditors",
                     "Treasurer, Guild of Meandering Auditors"]
# name -> (the parts after the lead, the protected values, whether those parts
# alone hold two date-led records and so split behind an undated lead too)
_PII_SHAPES = {
    "bare label, value in the next cell": (
        ["Date of Birth:", "01/02/1960, Quillhaven, Ohio"], ["01/02/1960"], False),
    "bare birth label, place in the next cell": (
        ["Born:", "1960 Quillhaven, Ohio"], ["1960 Quillhaven"], False),
    "children, year-led continuation cells": (
        ["Children:", "2001 Ann Marblegate", "2004 Bob Marblegate"],
        ["Ann Marblegate", "Bob Marblegate"], True),
    "children, continuation cells": (
        ["Children: Ann Marblegate (b. 2001)", "Bob Marblegate (b. 2004)"],
        ["Ann Marblegate", "Bob Marblegate"], False),
    "home address, continuation cell": (
        ["Home Address: 12 Elm Street", "2019 Apartment 4, Quillhaven"],
        ["12 Elm Street", "Apartment 4"], False),
    "date of birth, birthplace cell": (
        ["Date of Birth: 01/02/1960", "1960 Quillhaven General Hospital"],
        ["01/02/1960", "Quillhaven General Hospital"], False),
    "in-line SSN": (["President, Organizing Committee SSN: 123-45-6789"], ["123-45-6789"], False),
    "in-line DOB": (["Auditor Date of Birth: 01/02/1960"], ["01/02/1960"], False),
    # A value shape, not a label: withheld only in an Appendix-bound entry,
    # and the full-scope `_pii_fragments` does not see it, so only the order
    # keeps the split's copy of stage 2's text from holding it uncut.
    "third-party email": (["2009-2011 Liaison, Quimby Farrow, qfarrow@example.org"],
                          ["qfarrow@example.org"], False),
    "third-party phone": (["2009-2011 Liaison, Quimby Farrow, (212) 555-0147"],
                          ["555-0147"], False),
}
_PII_CODES = ["T", "A", "I", "D1", "Q1"]
# Codes whose entries get the full Personal Data / Appendix cut; a routed code
# withholds only the shapes in `_PII_ROUTED_SHAPES`.
_PII_FULL_SCOPE_CODES = {"T", "A"}
_PII_ROUTED_SHAPES = {"bare label, value in the next cell", "date of birth, birthplace cell",
                      "in-line SSN", "in-line DOB"}
# Withheld in an Appendix-bound (T) entry only: an A entry's contacts are the
# owner's, and a routed code keeps a contact.
_PII_THIRD_PARTY_SHAPES = {"third-party email", "third-party phone"}


def _pii_case_withholds(code, shape):
    if shape in _PII_THIRD_PARTY_SHAPES:
        return code == "T"
    return code in _PII_FULL_SCOPE_CODES or shape in _PII_ROUTED_SHAPES


def _docx_part_texts(path):
    """Each paragraph's w:t + w:delText text in every XML part of the docx
    (body, comments, headers): a protected value must be nowhere in it."""
    import zipfile

    from lxml import etree

    texts = []
    with zipfile.ZipFile(path) as docx:
        for name in docx.namelist():
            if not name.endswith(".xml"):
                continue
            root = etree.fromstring(docx.read(name))
            for para in root.iter(qn("w:p")):
                texts.append("".join(node.text or "" for node in para.iter(qn("w:t"), qn("w:delText"))))
    return texts


def _render_watching_pii_pass(tmp_path, monkeypatch, entries, *, dev_split_order):
    """Render `entries`; return (what the PII pass got and gave, paragraph texts).
    `dev_split_order` switches the date-led split off: dev's path."""
    import copy

    import unified_pipeline.stage_6_word_template as stage6

    seen = {}
    real_pass = stage6.run_pii_pass

    def watched_pass(entries_by_code, **kwargs):
        seen["input"] = {code: [e.get("text") for e in group]
                         for code, group in entries_by_code.items()}
        result = real_pass(entries_by_code, **kwargs)
        seen["output"] = {code: [(e.get("text"), copy.deepcopy(e.get("_pii_fragments")),
                                  e.get("_pii_orphaned_value")) for e in group]
                          for code, group in entries_by_code.items()}
        seen["withheld"] = list(result.withheld)
        return result

    arm = "dev" if dev_split_order else "branch"
    with monkeypatch.context() as patch:
        patch.setattr(stage6, "call_llm", lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("no LLM in tests")))
        patch.setattr(stage6, "run_pii_pass", watched_pass)
        if dev_split_order:
            patch.setattr(stage6, "_split_date_led_groups", lambda entries_by_code: None)
        input_path = tmp_path / f"TESTUID983{arm}_fields.json"
        input_path.write_text(json.dumps({"document_uid": f"TESTUID983{arm}", "entries": entries,
                                          "cv_owner": {"full_name": "Quenby Marblegate"}}))
        out = run_stage6(str(input_path), str(tmp_path / f"{arm}.docx"), verbose=False)
    return seen, _docx_part_texts(out)


def _pii_case_entry(code, lead, shape):
    entry = _society_entry(code=code)
    entry["text"] = "\t".join(lead + _PII_SHAPES[shape][0])
    return entry


@pytest.mark.parametrize("code", _PII_CODES)
@pytest.mark.parametrize("lead", ["dated", "undated"])
@pytest.mark.parametrize("shape", list(_PII_SHAPES))
def test_the_pii_pass_cuts_what_dev_cuts_with_or_without_the_split(tmp_path, monkeypatch,
                                                                   code, lead, shape):
    _parts, protected, parts_split_alone = _PII_SHAPES[shape]
    entry = _pii_case_entry(code, _PII_DATED_LEAD if lead == "dated" else _PII_UNDATED_LEAD, shape)
    # The case is what it says: stage 2's text splits behind a dated lead, and
    # behind an undated one only when the PII parts alone hold two records.
    assert (split_date_led_records(entry["text"]) != entry["text"]) == (
        lead == "dated" or parts_split_alone)

    dev_seen, dev_texts = _render_watching_pii_pass(
        tmp_path, monkeypatch, [copy.deepcopy(entry)], dev_split_order=True)
    seen, texts = _render_watching_pii_pass(
        tmp_path, monkeypatch, [copy.deepcopy(entry)], dev_split_order=False)

    assert seen["input"] == dev_seen["input"]  # stage 2's tabs, as on dev
    assert seen["output"] == dev_seen["output"]
    assert seen["withheld"] == dev_seen["withheld"]
    withholds = _pii_case_withholds(code, shape)
    assert bool(seen["withheld"]) == withholds
    for value in protected:
        assert (sum(t.count(value) for t in texts)
                == sum(t.count(value) for t in dev_texts)), value
        if withholds and code in _PII_FULL_SCOPE_CODES:
            assert not [t for t in texts if value in t], value


def test_an_entry_the_pii_pass_cut_is_not_split():
    # Its text and the recovery pass's input stay dev's cut text, tabs and all.
    entry = _grouped_text(_pii_case_entry("T", _PII_DATED_LEAD, "children, year-led continuation cells"))
    assert entry["_pii_withheld"] is True
    assert entry["text"] == "\t".join(_PII_DATED_LEAD)
    assert DATE_LED_SOURCE_TEXT not in entry


@pytest.mark.parametrize("fields", [{"date_of_birth": "01/02/1960"}, {"notes": "SSN 123-45-6789"}])
def test_an_entry_the_pii_pass_touched_through_its_fields_is_not_split(fields):
    # No fragment in the text, so only the pass's own mark, set before the
    # split runs, keeps the entry as dev renders it.
    entry = _society_entry(code="Q1", organization="Guild of Meandering Auditors", **fields)
    entry = _grouped_text(entry)
    assert entry["_pii_withheld"] is True
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)


def test_a_routed_entry_holding_protected_data_is_not_split():
    # An I entry gets the routed scope, which does not withhold a children
    # list, so the pass leaves it alone; dev rendered its fields, not the names.
    entry = _grouped_text(_pii_case_entry("I", _PII_DATED_LEAD, "children, year-led continuation cells"))
    assert "_pii_withheld" not in entry
    assert "\n" not in entry["text"]
    assert DATE_LED_SOURCE_TEXT not in entry


# Judgement call 6: the web205/web210 shape. Date-led mentee lines whose
# mentee table already renders them, now with a withheld fragment in one line.
# Recovery must not write the mentee lines back beside the table.
_MENTEE_PII_ENTRY = {
    "element_idx_start": 80,
    "element_idx_end": 81,
    "element_type": "paragraph",
    "hierarchy": ["MENTORING"],
    "taxonomy_code": "N3B",
    "taxonomy_confidence": 0.9,
    "text": ("2007-2011 Quimby Farrow, MSPH, PhD (Epidemiology), SSN: 123-45-6789\t"
             "2009-2011 Marsupial Heart Association Fellowship"),
    "extracted_fields": {"mentee_name": "Quimby Farrow", "mentee_level": "MSPH, PhD (Epidemiology)",
                         "start_date": "2007", "end_date": "2011",
                         "funding_source": "Marsupial Heart Association Fellowship (2009-2011)"},
}


def test_a_pii_touched_date_led_mentee_entry_recovers_no_duplicate_lines(tmp_path, monkeypatch):
    without_pii = _MENTEE_PII_ENTRY["text"].replace(", SSN: 123-45-6789", "")
    assert split_date_led_records(without_pii) != without_pii  # date-led siblings
    dev_seen, dev_texts = _render_watching_pii_pass(
        tmp_path, monkeypatch, [copy.deepcopy(_MENTEE_PII_ENTRY)], dev_split_order=True)
    seen, texts = _render_watching_pii_pass(
        tmp_path, monkeypatch, [copy.deepcopy(_MENTEE_PII_ENTRY)], dev_split_order=False)

    assert seen["withheld"] == dev_seen["withheld"] and seen["withheld"]
    assert not [t for t in texts if "123-45-6789" in t]
    for token in ("Quimby Farrow", "Marsupial Heart Association"):
        # Once, in the mentee table, on both arms: never recovered again.
        assert sum(token in t for t in texts) == sum(token in t for t in dev_texts) == 1, token
