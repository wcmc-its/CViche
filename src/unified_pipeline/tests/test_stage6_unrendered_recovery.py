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

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    _record_lines,
    run_stage6,
    segment_already_rendered,
)
from unified_pipeline.stage6.render_check import (  # noqa: E402
    _RENDER_TOKEN_RE,
    _norm,
    _record_rendered,
    _whole_record_rendered,
    segments_cover_source,
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
    """#1177: stage 6 reported no cost at all; both of its call_llm sites now
    feed the generator's LlmUsage."""
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

    assert gen.llm_usage.cost == pytest.approx(0.125)
    assert gen.llm_usage.prompt_tokens == 11
    assert gen.llm_usage.completion_tokens == 5
    assert gen.llm_usage.cache_read_tokens == 2
    assert gen.llm_usage.cache_write_tokens == 1


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


def test_remaining_appendix_intro_is_the_shared_accurate_banner():
    # #534: the bullet writer creates T. APPENDIX with the same one-line intro
    # as _fill_appendix -- italic, and never claiming the entries appear
    # nowhere else (stage 6 cannot prove that).
    from unified_pipeline.stage6.sections.appendix import APPENDIX_INTRO_TEXT

    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen._add_remaining_to_appendix([("Example Leftover Committee, 2019-2021", "T", 0.0)])

    paragraphs = gen.doc.paragraphs
    header = next(i for i, p in enumerate(paragraphs) if p.text == "T. APPENDIX")
    intro = paragraphs[header + 1]
    assert intro.text == APPENDIX_INTRO_TEXT
    assert all(run.italic for run in intro.runs)


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
    assert written == ["T"]
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


def test_t_fused_entry_rows_cut_by_the_appendix_cap_are_recovered():
    """`_fill_appendix` caps a body at APPENDIX_MAX_CHARS, so the rows after the
    cap reach no page; the recovery pass, no longer skipping T, writes them."""
    gen = _generator()
    entry = _t_entry()
    gen._fill_appendix([entry])
    capped = "\n".join(p.text for p in gen.doc.paragraphs)
    assert _T_ROWS[2] not in capped  # the cap really cut it

    gen._recover_unrendered_records({"T": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert _T_ROWS[2] in bullets
    assert gen.stats["unrendered_records_recovered"] >= 1


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
