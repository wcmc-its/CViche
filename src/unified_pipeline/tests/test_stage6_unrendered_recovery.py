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

import json
import sys
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
    "豈更車賈滑串",                  # CJK compatibility ideographs
    "ힰힱힲힳힴힵ",                  # Hangul Jamo extended-B
])
def test_every_cjk_block_is_excluded_from_the_token_regex(run):
    """#722: each range in `_CJK_CLASS` must keep a 5+ run out of the token
    set on its own (the sentence-level test above hits only Han/Hangul)."""
    assert _RENDER_TOKEN_RE.findall(run) == []


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
