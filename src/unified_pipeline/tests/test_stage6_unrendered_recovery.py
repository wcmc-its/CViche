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

from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY  # noqa: E402
from unified_pipeline.stage6.fan_out import (  # noqa: E402
    FANNED_OUT_FROM,
    LAST_STAGE4_RECORD,
)
from unified_pipeline.stage6.formatting import plain_entry_left_twips  # noqa: E402
from unified_pipeline.stage6.normalization import (  # noqa: E402
    _clean_inline_tabs,
    _squash,
)
from unified_pipeline.stage6.parsing.text import (  # noqa: E402
    DATE_LED_SOURCE_TEXT,
    split_date_led_records,
)
from unified_pipeline.stage6.render_check import (  # noqa: E402
    _RENDER_TOKEN_RE,
    _entry_pieces,
    _norm,
    _record_rendered,
    _record_tokens,
    _whole_record_rendered,
    record_chunks,
    record_pieces,
    record_siblings,
    record_twins,
    segment_held_by_fields,
    segments_cover_source,
    short_tab_part_rendered,
    short_tab_part_words,
    sibling_record_rendered,
    tab_aware_record_lines,
)
from unified_pipeline.stage6.sections.appendix import recovery_candidates  # noqa: E402
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


def test_short_officer_tab_parts_the_render_dropped_are_recovered():
    # #1243: an officer post under its society's line has one distinctive word
    # and its years, too few for the overlap test, so the pass skipped it. Its
    # word and its years identify it: a line naming the same office with other
    # years does not vouch for it.
    gen = _generator()
    entry = {
        "taxonomy_code": "Q1",
        "text": ("American Board of Quokka Surgery, term: 2007-2015\t"
                 "• Chairman, Credentials Committee, 2009-2011\t"
                 "• Secretary, 2009-2011\t• Vice President, 2011-2012"),
        "extracted_fields": {"role": "Member", "organization": "American Board of Quokka Surgery",
                             "additional_roles": [{"role": "Secretary"}, {"role": "Vice President"}]},
    }
    gen.doc.add_paragraph("Member, American Board of Quokka Surgery 2007-2015")
    gen.doc.add_paragraph("Chairman, Credentials Committee 2009-2011")
    gen.doc.add_paragraph("Secretary of the Wombat Panel, 2005-2007")

    gen._recover_unrendered_records({"Q1": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Secretary, 2009-2011" in t for t in bullets)
    assert any("Vice President, 2011-2012" in t for t in bullets)
    assert not any("Chairman" in t for t in bullets)
    assert gen.stats["unrendered_records_recovered"] == 2


def test_short_tab_part_rendered_elsewhere_or_held_by_fields_is_not_recovered():
    # #1243: the short part that did render, and a mentee line whose name a
    # scalar field holds (its "Dates:" label and years render in rows of their
    # own), are never written a second time.
    gen = _generator()
    officers = {
        "taxonomy_code": "Q1",
        "text": ("Member of the Quokka Society 1989-present\t"
                 "Treasurer, 2004-2005\tSecond Vice President 2006-2007"),
        "extracted_fields": {"organization": "Quokka Society", "role": "Member"},
    }
    mentee = {
        "taxonomy_code": "N3B",
        "text": ("Dr. Lisa Quokkason, Ph.D.    Dates: 1989-1990\t"
                 "Assistant Professor of Wombat Surgery (1991-1996)\tNorvale University"),
        "extracted_fields": {"mentee_name": "Lisa Quokkason", "mentee_level": "Ph.D.",
                             "current_position": "Assistant Professor of Wombat Surgery (1991-1996)"},
    }
    gen.doc.add_paragraph("Member of the Quokka Society 1989-present")
    gen.doc.add_paragraph("Treasurer 2004-2005")
    gen.doc.add_paragraph("Second Vice President 2006-2007")
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"Q1": [officers], "N3B": [mentee]})

    assert len(gen.doc.paragraphs) == before
    assert gen.stats["unrendered_records_recovered"] == 0


def test_short_record_line_outside_tabs_stays_unverifiable():
    # The words-and-years check reads tab parts only: a short line of a
    # newline entry is still too short to verify, and is never written.
    gen = _generator()
    entry = {"taxonomy_code": "Q1", "extracted_fields": {},
             "text": "2009-2011 Secretary of it\n2012-2014 Treasurer of it"}
    before = len(gen.doc.paragraphs)

    gen._recover_unrendered_records({"Q1": [entry]})

    assert len(gen.doc.paragraphs) == before


@pytest.mark.parametrize("part, words", [
    ("• Secretary, 2009-2011", {"secretary"}),
    ("Secretary, July, 1993 - October 1994", {"secretary"}),
    ("President Elect 5/2015-5/2017", {"president", "elect"}),
    ("1996-97 Vice-President, Acting President", {"president", "acting"}),
    ("2008-present Africa Liaison", {"africa", "liaison"}),
    ("Dr. Lisa Quokkason, Ph.D.    Dates: 1989-1990", {"quokkason"}),
    # Nothing to check it by: a heading with a year, a head count, a bare date.
    ("COMMITTEES 1993-1994", None),
    ("students 2013-2014: 12", None),
    ("July 1992 - October 1994", None),
])
def test_short_tab_part_words(part, words):
    assert short_tab_part_words(part) == words


def test_short_tab_part_rendered_needs_its_years_on_the_line():
    sets = [{"secretary", "wombat", "panel", "2005", "2007"}]
    assert short_tab_part_rendered("Secretary, 2009-2011", {}, sets) is False
    assert short_tab_part_rendered("Secretary, 2005-2007", {}, sets) is True
    assert short_tab_part_rendered("Secretary, 2009-2011", {"role": "Secretary"}, []) is True
    assert short_tab_part_rendered("COMMITTEES 1993-1994", {}, []) is None
    # #1243: a part dated by one year cannot be told from the row whose range
    # cell holds that year ("Fellow, Quokka Society" beside "2015-Present").
    assert short_tab_part_rendered("Elected fellow 2015.", {}, []) is None
    assert short_tab_part_rendered("Elected fellow 2015-2016.", {}, []) is False


def test_tab_parts_dated_by_one_year_the_render_dropped_are_recovered():
    # #1243 (BFSUMA 98's shape): a society line whose posts mostly carry one
    # year. The posts no line names are recovered; the one the render wrote
    # into another line is not, and neither is a short one-year part.
    gen = _generator()
    entry = {
        "taxonomy_code": "I",
        "text": ("Quokka College of Pharmacy, 1979-present\tFounding Member, 1979\t"
                 "Elected fellow 2015.\tResearch Affairs Committee, 1985\t"
                 "Chairperson, Awards Committee, 1987-1988"),
        "extracted_fields": {"organization": "Quokka College of Pharmacy",
                             "membership_type": "Founding Member, 1979"},
    }
    gen.doc.add_paragraph("Founding Member, 1979; Fellow, 1985, Quokka College of Pharmacy 1979-present")
    gen.doc.add_paragraph("Fellow, Quokka College of Pharmacy")

    gen._recover_unrendered_records({"I": [entry]})

    bullets = _bulleted_texts(gen.doc.paragraphs)
    assert any("Research Affairs Committee, 1985" in t for t in bullets)
    assert any("Chairperson, Awards Committee, 1987-1988" in t for t in bullets)
    assert not any("Founding Member" in t or "Elected fellow" in t for t in bullets)
    assert gen.stats["unrendered_records_recovered"] == 2


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


def _plain_left(para):
    """The left indent, in twips, of a paragraph with no list markup."""
    assert _num_level(para) is None, "expected no bullet (#1943)"
    return para.paragraph_format.left_indent.twips


@pytest.mark.parametrize(("code", "bulleted"), [("K1", False), ("L1", False), ("D1", True)])
def test_a_reconsidered_k_or_l_segment_carries_no_bullet(code, bulleted):
    # #1943: K and L entries render without bullets; a segment recovered into
    # those sections matches them. Other sections keep the list bullet.
    gen = _generator()
    segment = f"Synthetic {code} recovered segment, Example University, 2015"

    assert gen._insert_reconsidered_segment(segment, code) is True

    para = next(p for p in gen.doc.paragraphs if p.text == segment)
    if bulleted:
        assert _num_level(para) == "0"
    else:
        assert _plain_left(para) == plain_entry_left_twips(0)


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
    # #1943: the section's entries carry no bullet, and neither does its overflow.
    assert _plain_left(gen.doc.paragraphs[texts.index(text)]) == plain_entry_left_twips(0)


@pytest.mark.parametrize(("code", "bulleted"), [("K1", False), ("L1", False), ("D1", True)])
def test_body_paragraph_overflow_is_unbulleted_for_k_and_l(code, bulleted):
    """The other overflow route: the entry's own line is a body paragraph,
    replaced by the full text at the end of its section (#1943)."""
    gen = _generator()
    idx = gen._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
    short = gen._insert_bulleted_entry(idx + 1, "Synthetic short line", None, list_level=0)
    text = f"Synthetic {code} overflow prose, Example University, 2015"
    gen._overflow_entries = [(
        {"taxonomy_code": code, "text": text,
         "extraction_coverage": {"extraction_coverage_percent": 12}},
        short, code)]

    gen._route_overflow_entries()

    assert gen.stats["overflow_bullets_added"] == 1
    para = next(p for p in gen.doc.paragraphs if p.text == text)
    if bulleted:
        assert _num_level(para) == "0"
    else:
        assert _plain_left(para) == plain_entry_left_twips(0)


@pytest.mark.parametrize("bulleted", [True, False])
def test_overflow_levels_survive_with_or_without_bullets(bulleted):
    """A title, a context segment and a label's content: levels 0, 1, 2, as
    ilvl when bulleted and as indent steps when not (#1943)."""
    gen = _generator()
    idx = gen._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
    gen._insert_overflow_bullet(idx + 1, "Synthetic title\tScope:\tSynthetic scope prose",
                                bulleted=bulleted)
    paras = {p.text: p for p in gen.doc.paragraphs}
    expected = {"Synthetic title": 0, "Scope:": 1, "Synthetic scope prose": 2}
    for text, level in expected.items():
        if bulleted:
            assert _num_level(paras[text]) == str(level)
        else:
            assert _plain_left(paras[text]) == plain_entry_left_twips(level)


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
    from unified_pipeline.stage6.sections.appendix import (
        APPENDIX_NOTE_WITHHELD_ONLY_TEXT,
    )

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


# #829 (YDSVDB): the revised template's mentee rows are "<label> | <answer>".
# One stray "Goals/expected Outcomes — ..." line on the page vouched, through
# its label, for every mentee's goal line. Labels are the template's own text;
# the answers are invented.
_GOALS_LABEL = "Goals/expected Outcomes"
_SUPERVISION_LABEL = "Type of Supervision (Research, clinical, teaching, leadership)"
_STRAY_GOAL = f"{_GOALS_LABEL} — Catalogued the marigold quartzite herbarium"


def _output_line_tokens(*lines):
    return [set(_RENDER_TOKEN_RE.findall(_norm(line))) for line in lines]


def test_a_template_label_cell_is_no_verbatim_piece():
    line = f"{_GOALS_LABEL} | Shorter zeolite clinic waits"
    assert record_pieces(line) == [_squash_text(" Shorter zeolite clinic waits")]
    assert len(_entry_pieces(line)) == 2  # whole-entry checks keep the label piece (#744)


def test_a_template_label_cell_does_not_vouch_for_an_unrendered_record():
    line = f"{_GOALS_LABEL} | Shorter zeolite clinic waits for nasturtium patients"
    assert _record_rendered(line, _squash_text(_STRAY_GOAL),
                            _output_line_tokens(_STRAY_GOAL)) is False
    rendered = "Goals: Shorter zeolite clinic waits for nasturtium patients"
    assert _record_rendered(line, _squash_text(rendered),
                            _output_line_tokens(rendered)) is True


def test_a_short_answer_under_a_template_label_is_unverifiable():
    """The label's 5 tokens no longer outvote the answer's 1, so the line
    matches no stray copy of the label and is None, not rendered."""
    line = f"{_SUPERVISION_LABEL} | Leadership"
    stray = f"{_SUPERVISION_LABEL} — Lead"
    assert _record_rendered(line, _squash_text(stray), _output_line_tokens(stray)) is None


def test_record_chunks_drop_only_template_label_cells():
    plain = "Basalt Obsidian Feldspar | Hyacinth Delphinium"
    assert record_chunks(plain) == [plain, "Basalt Obsidian Feldspar ", " Hyacinth Delphinium"]
    labelled = f"{_GOALS_LABEL} | Hyacinth Delphinium"
    assert record_chunks(labelled) == [" Hyacinth Delphinium"]
    assert record_chunks(f"{_GOALS_LABEL} | {_SUPERVISION_LABEL}") == []


def test_answers_to_different_labels_need_not_share_one_output_line():
    """A5IZ6Q shape: a grant form row carries the PI name and the role, each
    under its own label; stage 6 renders them as two table rows. Once the
    labels stop vouching, the answers are judged one by one (each too thin to
    verify), not as one joined line that no single output row holds."""
    line = ("Name of Principal Investigator: | Quillon Marbury\tYour role*\toversight"
            "\tYour percent (%) effort:\t1%")
    page = ["Name of Principal Investigator: | Quillon Marbury", "Your role: | oversight",
            "Your percent (%) effort: | 1%"]
    haystack = _squash_text("\n".join(page))
    assert _record_rendered(line, haystack, _output_line_tokens(*page)) is None


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


def _two_by_two_grid():
    """Two committees, each served in the same two terms: every token of each
    row also sits in another row (#1230). Each row has 9 record tokens, as a
    real row does; the two terms of one committee share 7 of them, under
    `RECLASSIFY_MIN_TOKEN_COVERAGE`."""
    return [f"Member | {name} Committee | Example State Medical College | {term}"
            for name in ("Admissions", "Curriculum") for term in ("2010-2012", "2014-2016")]


def test_segments_cover_source_rejects_a_dropped_row_of_a_two_by_two_grid():
    """Dropping any one row of the grid leaves each of its tokens in the
    rows the reply kept, so the all-tokens rule accepted it. Each sibling
    row needs its own copy of the tokens it shares (#1230)."""
    rows = _two_by_two_grid()
    source = "\n".join(rows)
    assert segments_cover_source(rows, source) is True
    for dropped in rows:
        assert segments_cover_source([row for row in rows if row != dropped], source) is False


def test_segments_cover_source_counts_split_and_merged_grid_rows():
    """Rows split at their separators, or two rows written whole into one
    segment, keep every row's tokens and are accepted. A segment that writes
    two terms as one row carries the shared words once and is rejected,
    which keeps the entry whole (#1230)."""
    rows = _two_by_two_grid()
    source = "\n".join(rows)
    split = [cell for row in rows for cell in row.split(" | ")]
    assert segments_cover_source(split, source) is True
    assert segments_cover_source(["; ".join(rows[:2])] + rows[2:], source) is True
    merged = ("Member | Admissions Committee | Example State Medical College | "
              "2010-2012 and 2014-2016")
    assert segments_cover_source([merged] + rows[2:], source) is False


def test_segments_cover_source_unrelated_lines_do_not_pay_for_a_dropped_grid_row():
    """Unrelated lines that between them hold every word of a dropped grid row
    do not count for it: only segments that are a piece of a sibling row, or
    hold one whole, are counted (#1230)."""
    rows = _two_by_two_grid()
    unrelated = ["Member of the Faculty Senate, Riverbend Example University, 2014",
                 "Admissions Prize Committee juror, 2016",
                 "State Medical College seminar organiser"]
    source = "\n".join(rows + unrelated)
    assert segments_cover_source(rows + unrelated, source) is True
    assert segments_cover_source([row for row in rows if row != rows[1]] + unrelated,
                                 source) is False


def test_segments_cover_source_a_partner_line_may_lose_a_word_no_sibling_row_holds():
    """A line that holds a sibling row whole (its partner) is counted only for
    the sibling row's words: trimming a word of its own keeps the pooled
    share that any line without a sibling gets (#1230)."""
    rows = _two_by_two_grid()
    partner = rows[0] + " | organised the annual workshop retreat"
    source = "\n".join(rows + [partner])
    trimmed = partner.replace(" retreat", "")
    assert segments_cover_source(rows + [trimmed], source) is True
    assert segments_cover_source(rows[1:] + [partner], source) is False


def test_record_siblings_skips_a_sibling_that_holds_the_whole_line():
    """A line every token of which sits in a sibling is part of that sibling,
    so the sibling's render carries it and it needs no token of its own
    (#1230). The grid rows keep each other as siblings."""
    rows = _two_by_two_grid()
    short = rows[0].replace("Example State Medical College | ", "")
    siblings = record_siblings([short] + rows)
    assert _record_tokens(short) <= _record_tokens(rows[0])
    assert _record_tokens(rows[0]) not in siblings[short]
    assert siblings[short] == [_record_tokens(rows[2])]
    assert set(rows) <= set(siblings)


def test_non_t_grid_row_rendered_only_through_its_siblings_is_recovered():
    """Three rows of the grid on the page vouched for the fourth: every one
    of its words sits in a sibling's row. A sibling row is rendered only by
    an output line that holds a token each sibling lacks (#1230)."""
    rows = _two_by_two_grid()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    for row in rows[:3]:
        gen.doc.add_paragraph(row.replace(" | ", ", "))
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert bullets == [rows[3].replace(" | ", " \u2014 ")]


def test_non_t_grid_rows_recovered_one_after_another_each_land_once():
    """One grid row on the page: the other three are recovered in turn, and
    each recovered row then counts as on the page for the rows after it. The
    lines it may vouch through grow as rows are recovered (web207 crashed on
    a list that did not grow with them, #1230)."""
    rows = _two_by_two_grid()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    gen.doc.add_paragraph(rows[0].replace(" | ", ", "))
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert sorted(bullets) == sorted(row.replace(" | ", " \u2014 ") for row in rows[1:])


def test_non_t_grid_rows_all_rendered_are_not_recovered():
    """Every row on the page, reformatted: nothing is re-inserted (#1230)."""
    rows = _two_by_two_grid()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    for row in rows:
        gen.doc.add_paragraph(row.replace(" | ", ", ").replace("-", " - "))
    gen._recover_unrendered_records({"P": [entry]})
    assert not [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]


def test_record_siblings_holds_the_same_row_in_another_term():
    """The two terms of one committee share 7 of 9 tokens, under the sibling
    share, yet differ only in their numbers: they are siblings (#1230). Rows
    that differ only in a month name are not: a render may write the month as
    a number."""
    rows = _two_by_two_grid()
    siblings = record_siblings(rows)
    assert _record_tokens(rows[1]) in siblings[rows[0]]
    assert _record_tokens(rows[0]) in siblings[rows[1]]
    months = [f"Visiting Lecturer | Example State Medical College | {month} 2015"
              for month in ("January", "March")]
    assert record_siblings(months) == {}


def test_non_t_one_committee_in_two_terms_recovers_the_missing_term():
    """One committee in two terms, one term on the page: its row vouched for
    the other term, which shares every word. A row whose own numbers are on
    no rendered line with its words is recovered (#1230)."""
    rows = _two_by_two_grid()[:2]
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    gen.doc.add_paragraph(rows[0].replace(" | ", ", "))
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert bullets == [rows[1].replace(" | ", " \u2014 ")]


def _twin_rows():
    """Two rows told apart only by a word under five letters: `_record_tokens`
    gives them one token set (#1956)."""
    return [f"Member | Graduate Medical Education Committee | {site} | 2015-2018"
            for site in ("NYP", "WCM")]


def test_segments_cover_source_rejects_a_reply_that_drops_a_twin_row():
    """Twin rows share one token set, so neither was a sibling of the other
    and a reply that kept one carried every token of both. Each twin needs
    its own copy of the tokens (#1956)."""
    rows = _twin_rows()
    source = "\n".join(rows)
    assert _record_tokens(rows[0]) == _record_tokens(rows[1])
    assert segments_cover_source(rows, source) is True
    assert segments_cover_source(rows[:1], source) is False
    assert segments_cover_source(rows[1:], source) is False


def test_record_twins_maps_each_twin_row_to_the_other_squashed():
    """`record_siblings` cannot hold twins: no token tells them apart. Their
    squashed text does (#1956). A row with other words is no twin, and a row
    repeated with other spacing is the same record, not a twin."""
    rows = _twin_rows()
    other = "Member | Graduate Medical Education Committee | Example Hospital | 2015-2018"
    assert record_siblings(rows) == {}
    assert record_twins(rows + [other]) == {rows[0]: [_squash(rows[1])],
                                            rows[1]: [_squash(rows[0])]}
    assert record_twins([rows[0], rows[0].replace(" | ", " |  ")]) == {}


def test_non_t_twin_row_vouched_for_only_by_its_twin_is_recovered():
    """One twin on the page vouched for the other through their shared words.
    A row whose only vouching line is its twin's row verbatim is recovered
    (#1956)."""
    rows = _twin_rows()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    gen.doc.add_paragraph(rows[1])
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert bullets == [rows[0].replace(" | ", " \u2014 ")]


def test_non_t_twin_rows_neither_on_the_page_are_both_recovered():
    """Neither twin on the page: the first is recovered, and then must not
    vouch for the second (#1956)."""
    rows = _twin_rows()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert sorted(bullets) == [row.replace(" | ", " \u2014 ") for row in rows]


def test_non_t_twin_rendered_reformatted_beside_its_twin_verbatim_is_not_recovered():
    """One twin on the page reformatted past every verbatim piece, the other
    verbatim: the reformatted line vouches by its words and holds no twin, so
    it may be the row itself and the verdict stands (#1956). Both rows on the
    page verbatim re-insert nothing either."""
    rows = _twin_rows()
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    reformatted = "Graduate Medical Education (GME) Committee, NYP: Member, 2015 - 2018"
    assert not any(piece in _squash(reformatted) for piece in record_pieces(rows[0]))
    for page in ([reformatted, rows[1]], rows):
        gen = _generator()
        for line in page:
            gen.doc.add_paragraph(line)
        gen._recover_unrendered_records({"P": [entry]})
        assert not [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]


def test_sibling_record_rendered_keeps_a_twin_verdict_no_twin_line_overrules():
    """A twin's verdict turns False only when output lines vouch for it and
    every one is a twin's row verbatim (#1956). A line that holds one of its
    verbatim pieces without most of its words still vouches; with no
    haystack line vouching (a recovered tab part did), the verdict stands."""
    head = "Member, Graduate Medical Education Committee"
    rows = [f"{head} and Residency Curriculum Oversight Subcommittee, {site}, 2015-2018"
            for site in ("NYP", "WCM")]
    twins = record_twins(rows)[rows[0]]
    for page in ([rows[1]], [head, rows[1]]):
        haystack = "\x00".join(_squash(line) for line in page)
        token_sets = [set(_RENDER_TOKEN_RE.findall(_norm(line))) for line in page]
        expected = len(page) > 1
        assert sibling_record_rendered(rows[0], None, haystack, token_sets, [],
                                       twins) is expected
    token_sets = [set(_RENDER_TOKEN_RE.findall(_norm(rows[0])))]
    assert sibling_record_rendered(rows[0], None, "", token_sets, [], twins) is True


def test_non_t_date_led_grid_row_rendered_only_through_its_siblings_is_recovered():
    """The grid written date first, three rows on the page: the fourth is
    recovered (#1230)."""
    rows = [f"{term} Member, {name} Committee, Example State Medical College"
            for name in ("Admissions", "Curriculum") for term in ("2010-2012", "2014-2016")]
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    for row in rows[:3]:
        gen.doc.add_paragraph(row.replace("-", " - "))
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if "Member" in t]
    assert bullets == [rows[3]]


def test_non_t_rows_differing_only_in_a_month_name_rendered_as_numbers_are_kept():
    """Two rows told apart only by a month name, both on the page with the
    month as a number: neither is re-inserted (#1230)."""
    rows = [f"Visiting Lecturer | Example State Medical College | Boston | {month} 2015"
            for month in ("January", "March")]
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    for number in ("01", "03"):
        gen.doc.add_paragraph(
            f"{number}/2015 Visiting Lecturer, Example State Medical College, Boston")
    gen._recover_unrendered_records({"P": [entry]})
    assert not [t for t in _bulleted_texts(gen.doc.paragraphs) if "Lecturer" in t]


def test_non_t_sibling_rows_sharing_a_month_name_are_still_told_apart():
    """Two committees in the same month: the month is no row's own token, so
    the rendered row, which spells it, cannot vouch for the other (#1230)."""
    rows = [f"Member | {name} Committee | Example State Medical College | January 2015"
            for name in ("Admissions", "Curriculum")]
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    gen.doc.add_paragraph(rows[0].replace(" | ", ", "))
    gen._recover_unrendered_records({"P": [entry]})
    bullets = [t for t in _bulleted_texts(gen.doc.paragraphs) if t.startswith("Member")]
    assert bullets == [rows[1].replace(" | ", " \u2014 ")]


def _recovered_terms(rows, out):
    """The bullets the recovery pass re-inserts for P `rows` with `out` on the
    page, one paragraph per item."""
    entry = {"element_idx_start": 88, "taxonomy_code": "P",
             "text": "\n".join(rows), "extracted_fields": {}}
    gen = _generator()
    for text in out:
        gen.doc.add_paragraph(text)
    gen._recover_unrendered_records({"P": [entry]})
    return [t for t in _bulleted_texts(gen.doc.paragraphs) if "Committee" in t]


def _one_committee_two_terms(place="Weill Cornell Medicine"):
    return [f"Member | Admissions Committee | {place} | {term}"
            for term in ("2010-2012", "2014-2016")]


def test_non_t_two_terms_rendered_as_table_rows_without_the_place_are_kept():
    """Both terms on the page in the committee table's form, the place left
    out: each line holds half the row's words, so no line held the share of
    the whole row, and both rows were re-inserted. The line holding the
    row's committee cell and its own years vouches for it (#1230)."""
    rows = _one_committee_two_terms()
    assert not _recovered_terms(rows, [f"Admissions Committee | Member | {term}"
                                       for term in ("2010-2012", "2014-2016")])
    assert _recovered_terms(rows, ["Admissions Committee | Member | 2010-2012"]) == [
        rows[1].replace(" | ", " \u2014 ")]


def test_non_t_two_terms_under_a_place_heading_are_kept():
    """Both terms on the page under the place written once as a heading:
    neither row is re-inserted (#1230)."""
    rows = _one_committee_two_terms("Weill Cornell Medical College")
    assert not _recovered_terms(rows, ["Weill Cornell Medical College",
                                       "Member, Admissions Committee, 2010-2012",
                                       "Member, Admissions Committee, 2014-2016"])


def test_non_t_two_terms_with_the_years_dropped_are_kept():
    """Both terms on the page without their years: no line is either term's
    row, so nothing tells them apart and the verdict stands (#1230)."""
    rows = _one_committee_two_terms()
    assert not _recovered_terms(rows, ["Member, Admissions Committee, Weill Cornell Medicine"] * 2)


@pytest.mark.parametrize("years, recovered", [
    (("2010-12", "2014-16"), True),
    (("'10-'12", "'14-'16"), False),
])
def test_non_t_two_terms_with_shortened_years_are_kept(years, recovered):
    """Both terms on the page with each range's end written in two digits: a
    year counts by its last two digits standing alone, so neither row is
    re-inserted. With one term on the page, the other is recovered only when
    the line spells a year of its sibling whole, as "2010-12" does; "'10-'12"
    is no line positively the sibling's, and keeps the verdict (#1230)."""
    rows = _one_committee_two_terms()
    line = "Member, Admissions Committee, Weill Cornell Medicine, {}"
    assert not _recovered_terms(rows, [line.format(y) for y in years])
    assert _recovered_terms(rows, [line.format(years[0])]) == (
        [rows[1].replace(" | ", " \u2014 ")] if recovered else [])


@pytest.mark.parametrize("open_term", [
    "2014-Present", "2014-current", "2014-ongoing", "2014-now", "2014 to date"])
def test_non_t_a_term_and_an_open_term_are_told_apart(open_term):
    """One committee 2010-2012 and again in an open term ("2014-Present"):
    the open-term word counts like a year, so with either row on the page the
    other is recovered, and with both on the page neither is (#1230)."""
    rows = [f"Member | Admissions Committee | Weill Cornell Medical College | {term}"
            for term in ("2010-2012", open_term)]
    line = "Member, Admissions Committee, Weill Cornell Medical College, {}"
    terms = ("2010-2012", open_term)
    assert not _recovered_terms(rows, [line.format(term) for term in terms])
    assert _recovered_terms(rows, [line.format(terms[0])]) == [rows[1].replace(" | ", " \u2014 ")]
    assert _recovered_terms(rows, [line.format(terms[1])]) == [rows[0].replace(" | ", " \u2014 ")]


@pytest.mark.parametrize("line", [
    "Member, Admissions Committee, Example State Medical College, {}",
    "Committee on Admissions, Member, Example State Med. College, {}",
])
def test_non_t_consecutive_terms_with_shortened_years_are_kept(line):
    """Terms 2010-2012 and 2012-2014 on the page as "2010-12" and "2012-14",
    with a verbatim cell or reworded: the second term's own year is on its
    line only as "14", and the first term's line spells its sibling's 2010
    whole, so without the two-digit form it was re-inserted (#1230)."""
    rows = [f"Member | Admissions Committee | Example State Medical College | {term}"
            for term in ("2010-2012", "2012-2014")]
    assert not _recovered_terms(rows, [line.format(y) for y in ("2010-12", "2012-14")])


def test_non_t_a_year_inside_another_number_does_not_tell_terms_apart():
    """A missing term's year as two digits inside a longer number ("10014")
    or its sibling's year inside one ("20101") is not that year (#1230)."""
    rows = _one_committee_two_terms()
    place = "Member, Admissions Committee, Weill Cornell Medicine, New York, NY 10014"
    assert _recovered_terms(rows, [f"{place}, 2010-2012"]) == [rows[1].replace(" | ", " \u2014 ")]
    room = "Member, Admissions Committee, Weill Cornell Medicine, room 20101"
    assert not _recovered_terms(rows, [room, room])


def test_non_t_a_line_sharing_one_cell_word_does_not_vouch_for_a_term():
    """An unrelated line holding the missing term's years and one word of its
    row ("Member") does not vouch for it: a chunk too short for the overlap
    test is skipped, as `_record_rendered` skips it (#1230)."""
    rows = _one_committee_two_terms()
    assert _recovered_terms(rows, ["Member, Admissions Committee, Weill Cornell Medicine, 2010-2012",
                                   "Member, Faculty Council, 2014-2016"]) == [
        rows[1].replace(" | ", " \u2014 ")]


def test_non_t_row_whose_years_hold_its_siblings_is_kept_with_a_short_range():
    """'2010' and '2010-2012' rows, both on the page, the range written
    '2010-12': the range's row is not re-inserted (#1230)."""
    rows = [f"Member | Admissions Committee | Weill Cornell Medicine | {term}"
            for term in ("2010", "2010-2012")]
    assert not _recovered_terms(rows, [f"Member, Admissions Committee, Weill Cornell Medicine, {y}"
                                       for y in ("2010", "2010-12")])


def test_sibling_record_rendered_without_siblings_is_record_rendered():
    """A line with no sibling rows keeps `_record_rendered`'s verdict, and a
    sibling row on the page verbatim is rendered (#1230)."""
    rows = _two_by_two_grid()
    out = [rows[0].replace("2010-2012", "2014-2016")]
    letters = [set(_RENDER_TOKEN_RE.findall(_norm(line))) for line in out]
    records = [_record_tokens(line) for line in out]
    haystack = "\x00".join(_squash(line) for line in out)
    siblings = record_siblings(rows)
    assert sibling_record_rendered(rows[0], None, haystack, letters, records) is True
    assert sibling_record_rendered(rows[0], siblings[rows[0]], haystack, letters,
                                   records) is False
    assert sibling_record_rendered(rows[1], siblings[rows[1]], haystack, letters,
                                   records) is True


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
    withheld["_pii_withheld"] = True  # the pass sets both
    gen = _render_honors([other, withheld], supplementary_subpoints=True)

    assert "Young Investigator Award" not in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    assert _HONOR_PROSE not in "".join(
        t.text or "" for t in gen.doc.element.body.iter(qn("w:delText")))


# --- #1205: N3A/N3B mentees, the next code family ----------------------------
# The mentoring section writes each named mentee as a six-row "Label:" table of
# its own, so the prose is a deleted row at the end of that mentee's table.

_MENTEE_PROSE = ("Received the departmental travel award and presented the thesis findings "
                 "at the regional pharmacology meeting")


def _mentee(code="N3B", idx=1477, name="Ana Li", end="2017", prose=_MENTEE_PROSE):
    """A mentee whose source line goes on past the name, level and years into
    what the mentee won and presented, as EBYSBC MUHLLD 226's did. The name
    has no five-letter word, so only the level's words can anchor it: another
    postdoc's Site/Position row holds all of them (YUYVIG XNWSZN 188)."""
    return {
        "element_idx_start": idx,
        "taxonomy_code": code,
        "text": f"{name} (Postdoctoral Associate, 2014-{end})" + (f"\t{prose}" if prose else ""),
        "extracted_fields": {"mentee_name": name, "mentee_level": "Postdoctoral Associate",
                             "start_date": "2014", "end_date": end},
    }


def _other_postdoc(code="N3B", end="2017"):
    """A postdoc rendered before `_mentee()`'s, with no prose of its own."""
    return _mentee(code, idx=1470, name="Casey Sample", end=end, prose="")


def _render_mentoring(entries_by_code, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_mentoring(entries_by_code)
    gen._add_supplementary_subpoints(entries_by_code)
    return gen


def _mentee_named_by(row):
    """The Name: value of the mentee table `row` is a row of."""
    first_row_cells = row.getparent().find(qn("w:tr")).findall(qn("w:tc"))
    return "".join(t.text or "" for t in first_row_cells[1].iter(qn("w:t")))


def _mentee_heading_above(row):
    """The mentee heading the table holding `row` sits under."""
    for element in row.getparent().itersiblings(preceding=True):
        text = "".join(t.text or "" for t in element.iter(qn("w:t"))).strip()
        if text in ("Current Mentees:", "Past Mentees:"):
            return text
    return None


def _mentee_names(gen):
    """The Name: value of every mentee table, in document order."""
    return [_mentee_named_by(tbl.find(qn("w:tr"))) for tbl in gen.doc.element.body.iter(qn("w:tbl"))
            if "".join(t.text or "" for t in tbl.find(qn("w:tr")).find(qn("w:tc")).iter(qn("w:t")))
            == "Name:"]


def _row_texts(body):
    return [["".join(t.text or "" for t in tc.iter(qn("w:t"))) for tc in tr.findall(qn("w:tc"))]
            for tr in body.iter(qn("w:tr"))]


def _deleted_texts(gen):
    return "".join(t.text or "" for t in gen.doc.element.body.iter(qn("w:delText")))


@pytest.mark.parametrize("code, end, heading", [("N3B", "2017", "Past Mentees:"),
                                                ("N3A", "present", "Current Mentees:")])
def test_mentee_prose_is_a_deleted_row_at_the_end_of_its_table(code, end, heading):
    gen = _render_mentoring({code: [_mentee(code, end=end)]}, supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert row.getnext() is None
    assert _mentee_named_by(row) == "Ana Li"
    assert _mentee_heading_above(row) == heading
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _MENTEE_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "2"
    assert gen.stats["supplementary_subpoints"] == 1


def test_mentee_prose_goes_under_its_own_table_among_several():
    """The postdoc before it shares its Site/Position row word for word; the
    prose still goes to the end of the table whose Name: row is its mentee."""
    gen = _render_mentoring({"N3B": [_other_postdoc(), _mentee()]}, supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _mentee_named_by(row) == "Ana Li" and row.getnext() is None
    assert _mentee_names(gen) == ["Casey Sample", "Ana Li"]


@pytest.mark.parametrize("code, end, other_end, heading", [
    ("N3B", "present", "2017", "Current Mentees:"),   # the open range makes it current
    ("N3A", "2017", "present", "Past Mentees:"),      # an ended period makes it past
])
def test_a_mentee_the_section_files_under_the_other_heading_keeps_its_sub_point(
        code, end, other_end, heading):
    gen = _render_mentoring({code: [_other_postdoc(code, other_end), _mentee(code, end=end)]},
                            supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _mentee_named_by(row) == "Ana Li"
    assert _mentee_heading_above(row) == heading
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _MENTEE_PROSE


def test_mentee_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_mentoring({"N3B": [_other_postdoc(), _mentee()]}, supplementary_subpoints=False)
    on = _render_mentoring({"N3B": [_other_postdoc(), _mentee()]}, supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _MENTEE_PROSE in rejected
    assert _MENTEE_PROSE not in "".join(_texts_of(off.doc.element.body))
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    # Empty paragraphs and every table row, cell by cell, included.
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


def test_mentee_prose_already_in_its_table_is_not_offered_again():
    """Stage 4 put the prose in `awards`, which the Project/Accomplishments row
    prints, though no N3B rendered field (`_RENDERED_FIELDS`) names it: the
    document holds it, so nothing is left to offer."""
    from unified_pipeline.stage6 import supplementary

    entry = _mentee()
    entry["extracted_fields"]["awards"] = _MENTEE_PROSE
    gen = _render_mentoring({"N3B": [entry]}, supplementary_subpoints=True)
    assert _MENTEE_PROSE in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry, "N3B", haystack) == []


def test_a_mentee_naming_no_one_renders_its_whole_line_and_gets_no_sub_point():
    """An aggregate N3B line ("<n> postdocs ...") is a line, not a table."""
    entry = _mentee()
    del entry["extracted_fields"]["mentee_name"]
    gen = _render_mentoring({"N3B": [_other_postdoc(), entry]}, supplementary_subpoints=True)

    assert any(_MENTEE_PROSE in text for text in _texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == [] and _MENTEE_PROSE not in _deleted_texts(gen)


def _render_grouped_mentoring(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_mentoring(grouped)
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def test_a_mentee_rerouted_to_n1_follows_its_destination_and_gets_no_sub_point():
    """N1 is excluded: the entry renders as an N1 line of its text, and no
    mentee table carries its prose, not even the postdoc's that shares its
    level."""
    moved = _rerouted(_mentee(), "N1", "Leadership and mentoring in programs")
    gen, grouped = _render_grouped_mentoring([_other_postdoc(), moved],
                                             supplementary_subpoints=True)

    assert grouped["N1"] == [moved] and [e["element_idx_start"] for e in grouped["N3B"]] == [1470]
    assert any(_MENTEE_PROSE in text for text in _texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == [] and _MENTEE_PROSE not in _deleted_texts(gen)


def test_a_record_rerouted_into_n3b_follows_the_mentee_rule():
    """A trainee stage 3b coded K2 under a past-mentees heading renders as a
    mentee table: its prose is a deleted row at that table's end."""
    trainee = dict(_mentee(), taxonomy_code="K2")
    gen, grouped = _render_grouped_mentoring([_rerouted(trainee, "N3B", "PAST MENTEES")],
                                             supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["N3B"]] == [1477]
    (row,) = _subpoint_rows(gen)
    assert _mentee_named_by(row) == "Ana Li" and row.getnext() is None
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _MENTEE_PROSE


def test_a_rerouted_entrys_placement_records_the_code_it_rendered_as():
    """The sidecar's record of the sub-point names the mentee code the K2
    trainee rendered as, and the entry's own index (the doctor's key)."""
    from unified_pipeline.stage6.supplementary import SubpointPlacement

    trainee = dict(_mentee(), taxonomy_code="K2")
    gen, _ = _render_grouped_mentoring([_rerouted(trainee, "N3B", "PAST MENTEES")],
                                       supplementary_subpoints=True)

    assert gen._subpoint_placements == [SubpointPlacement(1477, "N3B", (_MENTEE_PROSE,))]


def test_the_render_sidecar_records_each_sub_point_with_its_entry_and_code(tmp_path, monkeypatch):
    """Through `run_stage6`: one placement per sub-point the docx holds, under
    the entry it was written for -- a mentee's planned row and the duties the
    positions writer split off a title -- so the doctor reads each as its own
    entry's only (EBYSBC VGHNZD: one mentee's sub-point silenced another
    mentee's WARN). The postdoc with no prose has none."""
    from unified_pipeline.doctor.shared import docx_subpoint_lines
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "T1205P", "cv_owner": {"full_name": "Quenby Marblegate"},
               "entries": [_other_postdoc(), _mentee(), _title_duty_appointment()]}
    input_path = tmp_path / "T1205P_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "out.docx"), verbose=False)

    placements = json.loads((tmp_path / "T1205P_render_warnings.json").read_text())[
        SUBPOINT_PLACEMENTS_KEY]
    assert sorted((p["element_idx_start"], p["code"]) for p in placements) == [
        (661, "D3"), (1477, "N3B")]
    by_entry = {p["element_idx_start"]: p["paragraphs"] for p in placements}
    assert by_entry == {661: [_TITLE_DUTIES], 1477: [_MENTEE_PROSE]}
    assert sorted(text for p in placements for text in p["paragraphs"]) == sorted(
        docx_subpoint_lines(Document(out)))


def test_a_generator_rendering_twice_records_only_this_renders_placements(tmp_path, monkeypatch):
    """The placements reset per render, as the section failures do: a second
    CV with no sub-point has none in its sidecar."""
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    gen = WCMTemplateGenerator(verbose=False, supplementary_subpoints=True)
    placements = []
    for uid, entries in (("T1205R1", [_mentee()]), ("T1205R2", [_other_postdoc()])):
        input_path = tmp_path / f"{uid}_fields.json"
        input_path.write_text(json.dumps({"document_uid": uid, "entries": entries}))
        gen.generate(str(input_path), str(tmp_path / f"{uid}.docx"), discover_original_doc=False)
        sidecar = json.loads((tmp_path / f"{uid}_render_warnings.json").read_text())
        placements.append([p["element_idx_start"] for p in sidecar[SUBPOINT_PLACEMENTS_KEY]])
    assert placements == [[1477], []]


def test_a_mentee_whose_field_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass dropped one of the mentee's fields. What is left
    still describes what was withheld, so none of it is offered, under its
    own table or the postdoc's."""
    withheld = _mentee()
    withheld["_pii_dropped_fields"] = ["site_position"]
    withheld["_pii_withheld"] = True  # the pass sets both
    gen = _render_mentoring({"N3B": [_other_postdoc(), withheld]}, supplementary_subpoints=True)

    assert "Ana Li" in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == [] and _MENTEE_PROSE not in _deleted_texts(gen)


def test_a_mentee_named_only_inside_an_award_title_gets_no_sub_point():
    """#1702: the section refuses a mentee whose name is an award's and lists
    the entry in the Appendix. No table names that person, so its prose goes
    under no table, not even the postdoc's that shares its level."""
    refused = _mentee(idx=1480, name="Jordan Example")
    refused["text"] = f"Recipient of the Jordan Example Award, Postdoctoral Associate\t{_MENTEE_PROSE}"
    gen = _render_mentoring({"N3B": [_other_postdoc(), refused]}, supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in gen._declined_grant_entries] == [1480]
    assert "Jordan Example" not in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == [] and _MENTEE_PROSE not in _deleted_texts(gen)


def test_a_one_segment_mentee_line_is_offered_whole_its_identity_included():
    """A fragment is a whole tab segment, so a line giving the mentee's name,
    level and years before its prose is offered as the CV wrote it: Reject
    All prints those three a second time, beside the table's own rows."""
    line = f"Ana Li, Postdoctoral Associate, 2014-2017, {_MENTEE_PROSE}"
    gen = _render_mentoring({"N3B": [dict(_mentee(), text=line)]}, supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _mentee_named_by(row) == "Ana Li"
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == line


def _mentee_line(text, *periods):
    """One source line stage 4 returned as a record per (name, start, end),
    the entry's own fields being the last record's. Fan-out gives every
    earlier record a line of its own and the last one the whole line."""
    records = [{"mentee_name": name, "mentee_level": "Postdoctoral Associate",
                "start_date": start, "end_date": end} for name, start, end in periods]
    return {"element_idx_start": 1490, "taxonomy_code": "N3B", "text": text,
            "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}


_LISTED_MENTEES = (("Ana Li", "2014", "2017"), ("Casey Sample", "2012", "2015"),
                   ("Jordan Example", "2010", "2013"))


def test_a_line_listing_several_mentees_is_offered_under_none_of_their_tables():
    """YUYVIG XNWSZN 188: twelve postdocs on one line. Each renders as a table
    of its own, so the line is no one mentee's prose: under the last one's
    table, Reject All would print every other name and period again."""
    line = "Postdoctoral associates mentored in the laboratory: " + ", ".join(
        f"{name} ({start}-{end})" for name, start, end in _LISTED_MENTEES)
    gen, grouped = _render_grouped_mentoring([_mentee_line(line, *_LISTED_MENTEES)],
                                             supplementary_subpoints=True)

    assert LAST_STAGE4_RECORD in grouped["N3B"][-1] and grouped["N3B"][-1]["text"] == line
    assert sorted(_mentee_names(gen)) == sorted(name for name, _, _ in _LISTED_MENTEES)
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


_ANA = "Ana Li, Postdoctoral Associate, 2014-2017"
_CASEY = "Casey Sample, Postdoctoral Associate, 2012-2015"


@pytest.mark.parametrize("segments, owner", [
    # The segments pair with the records: each prose goes under its own mentee.
    ([f"{_ANA}, {_MENTEE_PROSE}", _CASEY], "Ana Li"),
    ([_ANA, f"{_CASEY}, {_MENTEE_PROSE}"], "Casey Sample"),
    # They do not: whose prose it is cannot be told, so it goes under neither.
    ([_ANA, _MENTEE_PROSE, _CASEY], None),
])
def test_prose_on_a_line_of_several_mentees_goes_under_its_own_or_none(segments, owner):
    entry = _mentee_line("\t".join(segments), ("Ana Li", "2014", "2017"),
                         ("Casey Sample", "2012", "2015"))
    gen, _ = _render_grouped_mentoring([entry], supplementary_subpoints=True)

    assert sorted(_mentee_names(gen)) == ["Ana Li", "Casey Sample"]
    assert [_mentee_named_by(row) for row in _subpoint_rows(gen)] == ([owner] if owner else [])
    assert _deleted_texts(gen).count(_MENTEE_PROSE) == (1 if owner else 0)


def test_one_mentees_two_periods_on_one_line_keep_its_prose():
    """YUYVIG ECXGAT 1479: stage 4 made one record per period of the same
    mentee, so the whole line is that mentee's, and its prose goes once,
    under the first of the two tables. Another line's mentee is no record
    of this one."""
    line = f"Ana Li, Postdoctoral Associate, 2008-2010 and 2012-2014, {_MENTEE_PROSE}"
    entry = _mentee_line(line, ("Ana Li", "2008", "2010"), ("Ana Li", "2012", "2014"))
    gen, _ = _render_grouped_mentoring([_other_postdoc(), entry], supplementary_subpoints=True)

    assert sorted(_mentee_names(gen)) == ["Ana Li", "Ana Li", "Casey Sample"]
    (row,) = _subpoint_rows(gen)
    assert _mentee_named_by(row) == "Ana Li" and _deleted_texts(gen) == line


# --- #1205: B1 degrees, the next code family ----------------------------------
# The education section writes every B1 entry as a row of its four-column
# table, so the prose is a deleted row under the degree's own row: the row the
# education writer recorded for that entry (the row-receipt ledger).

_THESIS = ("Thesis: Seasonal variation of nitrogen fixation in coastal wetland soils "
           "under experimental warming")
_DEGREE_FIELD = "Example Marine Studies"


def _degree(idx=19, degree="M.S.", year="2001", prose=_THESIS, code="B1", *,
            discipline=_DEGREE_FIELD, institution="Norvale University", **fields):
    """A degree whose source line goes on past the degree, field, institution
    and year into its thesis, as YUYVIG FMIGLR 19's did. "M.S." and "Ph.D."
    hold no five-letter word, so another degree of the same field and
    institution shares every distinctive word of its row (`_doctorate`). A
    value passed as None is in neither the line nor the fields."""
    named = {"degree": degree, "discipline": discipline, "institution": institution, "year": year}
    line = ", ".join(value for value in named.values() if value)
    return {
        "element_idx_start": idx,
        "taxonomy_code": code,
        "text": line + (f"\t{prose}" if prose else ""),
        "extracted_fields": {key: value for key, value in {**named, **fields}.items()
                             if value is not None},
    }


def _doctorate():
    """A later degree of the same field and institution, with no prose: it
    renders first, reverse chronologically."""
    return _degree(idx=17, degree="Ph.D.", year="2005", prose="")


def _render_education(entries, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_education(entries)
    gen._add_supplementary_subpoints({"B1": entries})
    return gen


def _row_text(tr):
    return "".join(t.text or "" for t in tr.iter(qn("w:t")))


def _degree_table_row():
    """The same degree read from a source table: stage 2 joins the row's
    cells with pipes, and its position names the row ("96.3")."""
    entry = _degree()
    entry["element_idx_start"] = "96.3"
    entry["text"] = entry["text"].replace(", ", " | ").replace("\t", " | ")
    return entry


@pytest.mark.parametrize("entry", [_degree, _degree_table_row])
def test_degree_prose_is_a_deleted_row_under_the_degree_row(entry):
    gen = _render_education([entry()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == f"M.S., {_DEGREE_FIELD}Norvale University2001"
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _THESIS
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "4"
    assert gen.stats["supplementary_subpoints"] == 1


def test_degree_prose_goes_under_its_own_row_among_degrees_of_one_institution():
    """The Ph.D. row renders first and holds every distinctive word of the
    M.S. row, so words alone put the M.S. thesis under the Ph.D. (YUYVIG
    FMIGLR 19). Only the row the writer recorded for the M.S. entry takes it."""
    gen = _render_education([_doctorate(), _degree()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = row.getprevious()
    assert _row_text(above).startswith("M.S.,")
    assert _row_text(above.getprevious()).startswith("Ph.D.,")
    assert row.getnext() is None


def test_degree_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_education([_doctorate(), _degree()], supplementary_subpoints=False)
    on = _render_education([_doctorate(), _degree()], supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _THESIS in rejected
    assert _THESIS not in "".join(_texts_of(off.doc.element.body))
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    # Empty paragraphs and every table row, cell by cell, included.
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


@pytest.mark.parametrize("field, value, line", [
    # The degree's honors print in parentheses in the degree cell.
    ("honors", "summa cum laude, with highest departmental honors",
     "Graduated summa cum laude with highest departmental honors"),
    # Stage 4 put the thesis title in `major`, which the degree cell prints.
    ("major", _THESIS.removeprefix("Thesis: "), _THESIS),
])
def test_degree_prose_already_in_its_row_is_not_offered_again(field, value, line):
    """The degree cell prints these, and the document holds most of each
    line's words (`_record_rendered`), so neither prints twice. A line with
    more words of its own than that is judged by its share of the printed
    field values (`test_a_degree_line_mostly_restating_its_cell_is_not_offered`),
    and one with more new words still is offered whole
    (`test_degree_prose_restating_part_of_its_cell_is_offered_as_written`)."""
    from unified_pipeline.stage6 import supplementary

    entry = _degree(prose=line, **{field: value})
    gen = _render_education([entry], supplementary_subpoints=True)
    assert value in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == []
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry, "B1", haystack) == []


def test_every_degree_cell_field_the_pass_counts_is_printed_in_the_degree_cell():
    """`supplementary._DEGREE_CELL_FIELDS` restates the education writer's
    degree cell as literals (`_RENDERED_FIELDS` marks schema fields only):
    each one, alone on an entry, reaches that cell."""
    from unified_pipeline.stage6 import supplementary

    for field in supplementary._DEGREE_CELL_FIELDS:
        marker = f"Zq{field.replace('_', 'x')}Z"
        gen = _render_education([_degree(prose="", **{field: marker})], supplementary_subpoints=True)
        (row,) = [tr for tr in gen.doc.element.body.iter(qn("w:tr"))
                  if _row_text(tr).startswith("M.S.,")]
        assert marker in _row_text(row.find(qn("w:tc"))), field


_ADVISOR = "Orrin Halvard Vellacott"


@pytest.mark.parametrize("field, value, line", [
    ("major", "Estuarine Sediment Geochemistry",
     "Concentration: Estuarine Sediment Geochemistry and Coastal Processes"),
    ("field_of_study", "Estuarine Sediment Geochemistry",
     "Concentration: Estuarine Sediment Geochemistry and Coastal Processes"),
    ("honors", "summa cum laude with distinction",
     "Awarded summa cum laude with distinction in research"),
    ("major_professor", _ADVISOR, f"Research supervised by {_ADVISOR}"),
    ("mentor", _ADVISOR, f"Research supervised by {_ADVISOR}"),
    # The minor is new, but half the line's words are the printed major: the
    # share rule every family uses (`SUBPOINT_FIELD_TOKEN_MAX_SHARE`) reads it
    # as the row, so it is not offered.
    ("major", "Estuarine Sediment Geochemistry",
     "Major in Estuarine Sediment Geochemistry with minor in Chemistry"),
])
def test_a_degree_line_mostly_restating_its_cell_is_not_offered(field, value, line):
    """The degree cell prints `major` or `field_of_study`, the honors and an
    advisor under any of its names, though no B1 schema field is any of them
    (`_DEGREE_CELL_FIELDS`). A line at least half of whose words are theirs
    restates the row; the document holds too few of its words for
    `_record_rendered` to see that."""
    from unified_pipeline.stage6 import supplementary

    entry = _degree(prose=line, **{field: value})
    gen = _render_education([entry], supplementary_subpoints=True)
    assert value in _row_text(next(tr for tr in gen.doc.element.body.iter(qn("w:tr"))
                                   if _row_text(tr).startswith("M.S.,")))
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary._record_rendered(line, haystack.text, haystack.line_tokens) is False
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


@pytest.mark.parametrize("field, value, line", [
    ("advisor", _ADVISOR, f"Dissertation advisor: Professor {_ADVISOR}, Department of Oceanography"),
    ("honors", "magna cum laude", "Graduated magna cum laude with departmental distinction honors"),
])
def test_degree_prose_restating_part_of_its_cell_is_offered_as_written(field, value, line):
    """More of the line's words are its own than the cell's, so it is
    offered, and as the CV wrote it: Reject All prints the advisor or the
    honors again, under the row that holds them."""
    gen = _render_education([_degree(prose=line, **{field: value})], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert value in _row_text(row.getprevious())
    assert _deleted_texts(gen) == line


def _short_degree(kind):
    """A degree whose row holds too few distinctive (5+ letter) words for the
    word anchor: "PhD", "MD", a year and a four-letter name hold none, and a
    stage-5b name the row prints may share none with stage 4's field."""
    if kind == "one word":
        return _degree(degree="PhD", discipline=None, institution="Kell University", year="2010")
    if kind == "no word":
        return _degree(degree="MD", discipline=None, institution="Vaux", year=None)
    entry = _degree(degree="Ph.D.", discipline=None,
                    institution="Norvale Graduate School of Biomedical Sciences", year="2008")
    entry["institution_enrichment"] = {"cleaned_name": "Halbrook Medical College"}
    return entry


@pytest.mark.parametrize("kind", ["one word", "no word", "renamed"])
def test_a_short_degrees_prose_goes_under_its_own_row(kind):
    """The writer's record says which row is the entry's, so the row is
    taken as it is (`_pick_anchor`), however few words it shares with the
    fields. Before, the word test found no anchor, and the prose reached
    the document in neither arm. Accept All is still the flag-off render."""
    off = _render_education([_short_degree(kind)], supplementary_subpoints=False)
    on = _render_education([_short_degree(kind)], supplementary_subpoints=True)

    (row,) = _subpoint_rows(on)
    assert _row_text(row.getprevious()).startswith(_short_degree(kind)["extracted_fields"]["degree"])
    assert _deleted_texts(on) == _THESIS
    assert _THESIS not in "".join(_texts_of(off.doc.element.body))
    row.getparent().remove(row)
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


def test_a_thesis_citation_on_a_row_naming_no_degree_gets_no_sub_point():
    """Stage 4 coded a thesis citation B1 with a field and an institution but
    no degree (EBYSBC VGHNZD 1861). The section writes it a row of its own,
    the field alone in the degree cell, beside the master's degree the
    thesis was for; the prose is of no degree on that row, so it gets none."""
    citation = _degree(idx=23, degree=None, year="2001", prose="",
                       author="Example AB", title=_THESIS.removeprefix("Thesis: "))
    citation["text"] = f"Example AB. {_THESIS.removeprefix('Thesis: ')}. Norvale University, 2001."
    gen = _render_education([_degree(prose=""), citation], supplementary_subpoints=True)

    assert any(_row_text(tr).startswith(f"{_DEGREE_FIELD}Norvale University")
               for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_degree_the_section_skips_gets_no_sub_point_under_another_row():
    """An entry naming no degree and no institution is not written (`_fill_
    education`), so it has no row of its own; its field's words would put
    its prose under the other degree's row."""
    skipped = _degree(idx=21, degree=None, institution=None,
                      prose="Completed coursework in estuarine ecology and coastal sediment chemistry")
    gen = _render_education([_degree(prose=""), skipped], supplementary_subpoints=True)

    assert len([tr for tr in gen.doc.element.body.iter(qn("w:tr"))
                if _row_text(tr).startswith("M.S.,")]) == 1
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _render_grouped_education(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_education(grouped.get("B1", []))
    gen._fill_other_education(grouped.get("B2", []))
    gen._fill_postdoc_training(grouped, [e for group in grouped.values() for e in group])
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def test_a_degree_rerouted_out_of_b1_follows_its_destination_and_gets_no_sub_point():
    """B2 is excluded: a school with no degree renders under Other
    Educational Experiences (`_route_precollegiate_education`). C is not
    offered either, and its heading lookup finds the title block, not its
    section: a training row stage 3b coded B1 under a postdoctoral heading
    renders as C. Neither the degree row of the same institution nor any
    other carries their prose."""
    school = _degree(idx=32, degree=None, discipline=None, institution="Example Preparatory School",
                     prose="Completed the advanced laboratory sequence in marine field methods")
    training = _rerouted(_degree(idx=31, degree=None, year=None, discipline=None,
                                 prose="Trained in quantitative methods for estuarine monitoring "
                                       "programmes", dates_attended="2002-2004"),
                         "C", "POSTDOCTORAL TRAINING")
    gen, grouped = _render_grouped_education([_degree(prose=""), school, training],
                                             supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["B2"]] == [32]
    assert [e["element_idx_start"] for e in grouped["C"]] == [31]
    assert "Example Preparatory School" in "".join(_texts_of(gen.doc.element.body))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_record_rerouted_into_b1_follows_the_degree_rule():
    """A degree programme stage 3b coded C under an education heading renders
    as a degree row: its thesis is a deleted row under that row."""
    gen, grouped = _render_grouped_education(
        [_rerouted(_degree(idx=30, code="C"), "B1", "EDUCATION")], supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["B1"]] == [30]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()).startswith("M.S.,")
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _THESIS


def test_a_degree_whose_field_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass dropped one of the degree's fields. What is left
    still describes what was withheld, so none of it is offered."""
    withheld = _degree()
    withheld["_pii_dropped_fields"] = ["advisor"]
    withheld["_pii_withheld"] = True  # the pass sets both
    gen = _render_education([_doctorate(), withheld], supplementary_subpoints=True)

    assert any(_row_text(tr).startswith("M.S.,") for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _degree_line(text, *degrees):
    """One source line stage 4 returned as a record per (degree, year), the
    entry's own fields being the last record's. Fan-out gives every earlier
    record a line of its own and the last one the whole line."""
    records = [{"degree": degree, "discipline": _DEGREE_FIELD, "institution": "Norvale University",
                "year": year} for degree, year in degrees]
    return {"element_idx_start": 25, "taxonomy_code": "B1", "text": text,
            "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}


_MS_LINE = f"M.S., {_DEGREE_FIELD}, Norvale University, 2001"
_PHD_LINE = f"Ph.D., {_DEGREE_FIELD}, Norvale University, 2005"


@pytest.mark.parametrize("segments, offered", [
    # The segments pair with the records: the prose goes under its own degree.
    ([f"{_MS_LINE}, {_THESIS}", _PHD_LINE], [("M.S.", f"{_MS_LINE}, {_THESIS}")]),
    ([_MS_LINE, f"{_PHD_LINE}, {_THESIS}"], [("Ph.D.", f"{_PHD_LINE}, {_THESIS}")]),
    # They do not: whose thesis it is cannot be told, so it goes under neither.
    ([_MS_LINE, _THESIS, _PHD_LINE], []),
])
def test_prose_on_a_line_of_several_degrees_goes_under_its_own_or_none(segments, offered):
    """A sub-point is the whole tab segment, as the CV wrote it: one that
    holds the degree's line as well as its thesis brings the degree, field,
    institution and year along, and Reject All prints them a second time,
    under the row that holds them (`test_a_thesis_on_its_degrees_own_line_
    brings_the_line_with_it`)."""
    entry = _degree_line("\t".join(segments), ("M.S.", "2001"), ("Ph.D.", "2005"))
    gen, grouped = _render_grouped_education([entry], supplementary_subpoints=True)

    assert LAST_STAGE4_RECORD in grouped["B1"][-1] and len(grouped["B1"]) == 2
    assert [(_row_text(row.getprevious()).split(",")[0],
             "".join(t.text for t in row.iter(qn("w:delText")))) for row in _subpoint_rows(gen)] \
        == offered


def test_a_thesis_on_its_degrees_own_line_brings_the_line_with_it():
    """A fragment splits only on tabs, line breaks and pipes, so a thesis in
    the same sentence run as its degree is offered with the degree's line,
    the shape every family has (#1673's honors, #1935's mentees): Reject All
    prints the degree, field, institution and year again, under the row
    that holds them. The CV's line as written is the shape the owner
    approved (#1205, 2026-10-09)."""
    line = f"{_PHD_LINE}. {_THESIS}"
    entry = _degree(idx=17, degree="Ph.D.", year="2005", prose="")
    entry["text"] = line
    gen = _render_education([entry], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == f"Ph.D., {_DEGREE_FIELD}Norvale University2005"
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == line


def test_the_doctor_matches_a_degree_sub_point_to_its_own_entry_only(tmp_path, monkeypatch):
    """Through `run_stage6`: the sidecar records the M.S. entry's sub-point
    under its own index and B1, and the doctor's per-entry match counts it for
    that entry, not for the Ph.D. that shares its words."""
    from unified_pipeline.doctor.shared import (
        docx_subpoint_lines,
        offered_as_subpoint,
        subpoint_placements,
        subpoint_text,
    )
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "T1205B", "cv_owner": {"full_name": "Quenby Marblegate"},
               "entries": [_doctorate(), _degree()]}
    input_path = tmp_path / "T1205B_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "out.docx"), verbose=False)

    sidecar = json.loads((tmp_path / "T1205B_render_warnings.json").read_text())
    assert [(p["element_idx_start"], p["code"], p["paragraphs"])
            for p in sidecar[SUBPOINT_PLACEMENTS_KEY]] == [(19, "B1", [_THESIS])]
    subpoints = subpoint_text(docx_subpoint_lines(Document(out)), subpoint_placements(sidecar))
    assert offered_as_subpoint(_THESIS, subpoints, element_idx=19)
    assert not offered_as_subpoint(_THESIS, subpoints, element_idx=17)


# --- #1205: O institutional leadership, the next code family -----------------
# The leadership section writes every O entry as a row of its three-column
# table, so the prose is a deleted row under the role's own row: the row the
# leadership writer recorded for that entry (the row-receipt ledger).

_ROLE_PROSE = ("Oversaw an annual operating budget and twelve clinical staff, and launched "
               "the regional outreach programme for community clinics")
_ROLE_TITLE = "Director, Example Outreach Programme"
_ROLE_INSTITUTION = "Norvale University"


def _role(idx=57, title=_ROLE_TITLE, start="2008", end="2012", prose=_ROLE_PROSE, code="O", *,
          institution=_ROLE_INSTITUTION, **fields):
    """A leadership role whose source line goes on past its dates, title and
    institution into what it oversaw, as EBYSBC SEKQUI 75's did. Another
    role of the same title and institution shares every distinctive word of
    its row, and so does a later title holding its words ("Assistant
    Director, ..."): only the dates, which are digits, tell them apart. A
    value passed as None is in neither the line nor the fields."""
    named = {"leadership_role": title, "institution": institution, "start_date": start,
             "end_date": end}
    dates = "-".join(value for value in (start, end) if value)
    line = "\t".join(part for part in (dates, ", ".join(v for v in (title, institution) if v)) if part)
    return {
        "element_idx_start": idx,
        "taxonomy_code": code,
        "text": line + (f"\t{prose}" if prose else ""),
        "extracted_fields": {key: value for key, value in {**named, **fields}.items()
                             if value is not None},
    }


def _later_role(title=_ROLE_TITLE):
    """A later role of the same institution, with no prose: it renders first,
    reverse chronologically."""
    return _role(idx=55, title=title, start="2016", end="2020", prose="")


def _render_leadership(entries, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_leadership(entries)
    gen._add_supplementary_subpoints({"O": entries})
    return gen


def _role_table_row():
    """The same role read from a source table: stage 2 joins the row's cells
    with pipes, and its position names the row ("41.2")."""
    entry = _role()
    entry["element_idx_start"] = "41.2"
    entry["text"] = entry["text"].replace("\t", " | ")
    return entry


_ROLE_ROW = f"{_ROLE_TITLE}{_ROLE_INSTITUTION}2008-2012"


@pytest.mark.parametrize("entry", [_role, _role_table_row])
def test_leadership_prose_is_a_deleted_row_under_the_role_row(entry):
    gen = _render_leadership([entry()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _ROLE_ROW
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _ROLE_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "3"
    assert gen.stats["supplementary_subpoints"] == 1


def test_a_roles_several_prose_lines_are_one_row_of_paragraphs_in_source_order():
    lines = (_ROLE_PROSE, "Chaired the programme steering group and its annual quality review")
    gen = _render_leadership([_role(prose="\t".join(lines))], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _ROLE_ROW
    assert ["".join(t.text for t in p.iter(qn("w:delText"))) for p in row.iter(qn("w:p"))] \
        == list(lines)


@pytest.mark.parametrize("later", [
    # One title held twice at one institution: the rows differ in their dates only.
    _ROLE_TITLE,
    # A later title at the same institution holding every word of this one.
    f"Assistant {_ROLE_TITLE}",
])
def test_leadership_prose_goes_under_its_own_row_among_roles_of_one_institution(later):
    """The later role renders first and holds every distinctive word of the
    earlier one's row, so words alone put the earlier role's prose under the
    later row. Only the row the writer recorded for the earlier entry takes it."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_leadership([_later_role(later), _role()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = row.getprevious()
    assert _row_text(above) == _ROLE_ROW
    assert _row_text(above.getprevious()) == f"{later}{_ROLE_INSTITUTION}2016-2020"
    assert row.getnext() is None
    # Words alone would have taken the later role's row.
    lines = list(supplementary.anchor_candidates(_render_leadership(
        [_later_role(later), _role()], supplementary_subpoints=False).doc))
    rows = [line for line in lines if _ROLE_INSTITUTION in line.text]
    assert supplementary.best_anchor(supplementary._value_tokens(_role(), "O"), rows) is rows[0]
    assert rows[0].text.startswith(later)


def test_leadership_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_leadership([_later_role(), _role()], supplementary_subpoints=False)
    on = _render_leadership([_later_role(), _role()], supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _ROLE_PROSE in rejected
    assert _ROLE_PROSE not in "".join(_texts_of(off.doc.element.body))
    assert len(_subpoint_rows(on)) == 1
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    # Empty paragraphs and every table row, cell by cell, included.
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


def _role_in_division():
    """Stage 4 put the programme's description in `division_department`,
    which the institution cell prints."""
    return _role(division_department=_ROLE_PROSE)


def _role_with_no_role_or_institution():
    """No role and no institution: the role cell prints the entry's text."""
    return _role(title=None, institution=None)


def _wrapped_role_row():
    """One source table row whose cells wrap (#987): the rejoined text is the row."""
    entry = _role(title=None, institution=None, start=None, end=None, prose="")
    entry.update(element_type="table_row", element_idx_start=41, element_idx_end=41,
                 text=f"{_ROLE_TITLE} | {_ROLE_INSTITUTION}\n{_ROLE_PROSE} | 2008 -\n2012")
    return entry


@pytest.mark.parametrize("entry", [_role_in_division, _role_with_no_role_or_institution,
                                   _wrapped_role_row])
def test_leadership_prose_already_in_its_row_is_not_offered_again(entry):
    """The row prints it (`_record_rendered`), so it does not print twice."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_leadership([entry()], supplementary_subpoints=True)
    assert _ROLE_PROSE in "".join(_row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry(), "O", haystack) == []


def test_every_leadership_row_field_the_pass_counts_is_printed_in_the_role_row():
    """`supplementary._LEADERSHIP_ROW_FIELDS`, the off-schema names among the
    fields the leadership writer reads its cells from (`_RENDERED_FIELDS`
    marks schema fields only): each one, alone on an entry with the schema's
    field empty, reaches the row."""
    from unified_pipeline.stage6 import supplementary

    for field in supplementary._LEADERSHIP_ROW_FIELDS:
        marker = f"Zq{field}Z"
        entry = _role(title=None, institution=None, prose="", **{field: marker})
        gen = _render_leadership([entry], supplementary_subpoints=True)
        assert any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr"))), field


def test_every_field_the_role_row_prints_is_one_the_pass_counts():
    """The other direction: a field the row prints that `_value_tokens` does
    not count would offer a fragment restating the row. The names are the
    role and organization fields of the codes an entry is rerouted into O
    from (D1-D3, L3, P, Q1, Q2), each alone on an entry."""
    from unified_pipeline.stage6 import supplementary
    from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS

    counted = _RENDERED_FIELDS["O"] | set(supplementary._LEADERSHIP_ROW_FIELDS)
    names = set().union(*(_RENDERED_FIELDS[code] for code in ("D1", "D2", "D3", "L3", "P", "Q1", "Q2")))
    for field in sorted(names - {"start_date", "end_date"}):
        marker = f"Zq{field}Z"
        entry = {"element_idx_start": 57, "taxonomy_code": "O", "text": "2008-2012",
                 "extracted_fields": {field: marker, "start_date": "2008", "end_date": "2012"}}
        gen = _render_leadership([entry], supplementary_subpoints=True)
        printed = any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
        assert not printed or field in counted, field


@pytest.mark.parametrize("field", ["role", "position", "organization"])
def test_a_leadership_line_mostly_restating_its_row_is_not_offered(field):
    """Stage 4 named the role or the organization under an off-schema key,
    which the row prints when the schema's is empty. A line at least half
    of whose words restate it is the row (`SUBPOINT_FIELD_TOKEN_MAX_SHARE`);
    the document holds too few of its words for `_record_rendered` to see that."""
    from unified_pipeline.stage6 import supplementary

    value = "Regional Outreach Clinics Director"
    line = f"{value}: expanded statewide pediatric services"
    emptied = {"institution": None} if field == "organization" else {"title": None}
    entry = _role(prose=line, **emptied, **{field: value})
    gen = _render_leadership([entry], supplementary_subpoints=True)
    assert any(value in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary._record_rendered(line, haystack.text, haystack.line_tokens) is False
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_flattened_leadership_table_prints_every_line_as_a_row_and_offers_nothing():
    """Three or more lines are a source table flattened into one entry: the
    writer prints each line as a row of its own (`_add_multiline_leadership_
    rows`), a prose line included, so nothing is left to offer. Every row is
    the entry's own (the ledger), and none of them gets a sub-point."""
    block = (f"Chair, Example Admissions Committee | {_ROLE_INSTITUTION} | 2001-2004\n"
             f"Director, Example Outreach Programme | {_ROLE_INSTITUTION} | 2005-2010\n"
             f"{_ROLE_PROSE}")
    entry = {"element_idx_start": 60, "taxonomy_code": "O", "text": block,
             "extracted_fields": {"leadership_role": "Chair, Example Admissions Committee",
                                  "institution": _ROLE_INSTITUTION,
                                  "start_date": "2001", "end_date": "2004"}}
    gen = _render_leadership([entry], supplementary_subpoints=True)

    rows = gen._row_receipts.elements_for(entry)
    assert len(rows) == 3 and _ROLE_PROSE in _row_text(rows[2])
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_leadership_entry_the_section_cannot_place_gets_no_sub_point():
    """The leadership writer binds only to its own table (#625, #664). With
    none, it writes no row, so no row is the entry's own: its prose goes
    under no other line of the document, however many of its words that
    line holds."""
    gen = _generator(supplementary_subpoints=True)
    table = gen._find_table_after_paragraph(gen._find_paragraph_exact(
        "INSTITUTIONAL LEADERSHIP ACTIVITIES"))
    # No longer the leadership table's header, and a line of the section
    # holding every distinctive word of the role's fields.
    table.rows[0].cells[0].text = f"{_ROLE_TITLE}, {_ROLE_INSTITUTION}"
    role = _role()
    gen._fill_leadership([role])
    gen._add_supplementary_subpoints({"O": [role]})

    assert gen._row_receipts.elements_for(role) == []
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _render_grouped_leadership(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_leadership(grouped.get("O", []))
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def _role_line(text, *roles):
    """One source line stage 4 returned as a record per (role, start, end),
    the entry's own fields being the last record's. Fan-out gives every
    earlier record a line of its own and the last one the whole line."""
    records = [{"leadership_role": role, "institution": _ROLE_INSTITUTION, "start_date": start,
                "end_date": end} for role, start, end in roles]
    return {"element_idx_start": 63, "taxonomy_code": "O", "text": text,
            "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}


_CHAIR = ("Chair, Example Admissions Committee", "2001", "2004")
_CHAIR_LINE = f"Chair, Example Admissions Committee, {_ROLE_INSTITUTION}, 2001-2004"
_DIRECTOR = (_ROLE_TITLE, "2005", "2010")
_DIRECTOR_LINE = f"{_ROLE_TITLE}, {_ROLE_INSTITUTION}, 2005-2010"


@pytest.mark.parametrize("segments, offered", [
    # The segments pair with the records: the prose goes under its own role.
    ([f"{_CHAIR_LINE}. {_ROLE_PROSE}", _DIRECTOR_LINE],
     [("Chair", f"{_CHAIR_LINE}. {_ROLE_PROSE}")]),
    ([_CHAIR_LINE, f"{_DIRECTOR_LINE}. {_ROLE_PROSE}"],
     [("Director", f"{_DIRECTOR_LINE}. {_ROLE_PROSE}")]),
    # They do not: which role the prose is about cannot be told, so neither takes it.
    ([_CHAIR_LINE, _ROLE_PROSE, _DIRECTOR_LINE], []),
])
def test_prose_on_a_line_of_several_roles_goes_under_its_own_or_none(segments, offered):
    """A sub-point is the whole tab segment, as the CV wrote it: one that
    holds the role's line as well as its prose brings the role, institution
    and dates along, and Reject All prints them a second time, under the
    row that holds them (the shape every family has; #1205, 2026-10-09)."""
    entry = _role_line("\t".join(segments), _CHAIR, _DIRECTOR)
    gen, grouped = _render_grouped_leadership([entry], supplementary_subpoints=True)

    assert LAST_STAGE4_RECORD in grouped["O"][-1] and len(grouped["O"]) == 2
    assert [(_row_text(row.getprevious()).split(",")[0],
             "".join(t.text for t in row.iter(qn("w:delText")))) for row in _subpoint_rows(gen)] \
        == offered


def test_generic_list_fan_out_children_offer_nothing_their_fields_do_not_hold():
    """Fan-out splits a record list stage 4 filed under a key the schema does
    not define only when the fields hold every word of the text, and each
    child's text is then the line built from its fields: nothing is prose.
    With prose in the text the list is not split, the entry prints its whole
    text in the role cell, and nothing is left to offer either."""
    records = [{"leadership_role": "Chair, Example Admissions Committee",
                "institution": _ROLE_INSTITUTION, "start_date": "2001", "end_date": "2004"},
               {"leadership_role": _ROLE_TITLE, "institution": _ROLE_INSTITUTION,
                "start_date": "2005", "end_date": "2010"}]
    for text, children in ((f"{_CHAIR_LINE}\t{_DIRECTOR_LINE}", 2),
                           (f"{_CHAIR_LINE}\t{_DIRECTOR_LINE}\t{_ROLE_PROSE}", 1)):
        entry = {"element_idx_start": 64, "taxonomy_code": "O", "text": text,
                 "extracted_fields": {"leadership_roles": copy.deepcopy(records)}}
        gen, grouped = _render_grouped_leadership([entry], supplementary_subpoints=True)
        assert len(grouped["O"]) == children
        assert all(gen._row_receipts.elements_for(e) for e in grouped["O"])
        assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_leadership_role_rerouted_out_of_o_follows_its_destination_and_gets_no_sub_point():
    """N1 is excluded: a role under a mentoring-programmes heading renders as
    N1. C is not offered, and its heading lookup finds the title block, not
    its section: a record holding only an institution and a start date under
    a training heading renders as C. Neither the role row of the same
    institution nor any other carries their prose."""
    programme = _rerouted(_role(idx=70), "N1", "MENTORING PROGRAMS")
    training = _rerouted(_role(idx=71, title=None, end=None,
                               prose="Trained in quantitative methods for community outreach "
                                     "programme evaluation"), "C", "POSTDOCTORAL TRAINING")
    gen, grouped = _render_grouped_leadership([_later_role(), programme, training],
                                              supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["N1"]] == [70]
    assert [e["element_idx_start"] for e in grouped["C"]] == [71]
    assert [e["element_idx_start"] for e in grouped["O"]] == [55]
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_refused_reroute_keeps_the_leadership_rule():
    """A role rerouted to I keeps its O code: I's renderer writes none of its
    fields (`_cross_family_refusal`). It renders as an O row and its prose is
    a deleted row under that row."""
    gen, grouped = _render_grouped_leadership([_rerouted(_role(), "I", "MEMBERSHIPS")],
                                              supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["O"]] == [57]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _ROLE_ROW


def test_a_record_rerouted_into_o_follows_the_leadership_rule():
    """A clinical leadership role stage 3b coded L3 under the institutional
    leadership heading renders as an O row: its prose is a deleted row under
    that row."""
    gen, grouped = _render_grouped_leadership(
        [_rerouted(_role(code="L3"), "O", "INSTITUTIONAL LEADERSHIP")], supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["O"]] == [57]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _ROLE_ROW
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _ROLE_PROSE


def test_a_leadership_role_whose_field_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass dropped one of the role's fields. What is left
    still describes what was withheld, so none of it is offered."""
    withheld = _role()
    withheld["_pii_dropped_fields"] = ["division_department"]
    withheld["_pii_withheld"] = True  # the pass sets both
    gen = _render_leadership([_later_role(), withheld], supplementary_subpoints=True)

    assert any(_row_text(tr) == _ROLE_ROW for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_leadership_role_dedup_drops_offers_its_prose_once_under_the_kept_row():
    """Stage 6 dedups before the sections render and hands the sub-point
    pass the entries from before it: the dropped copy wrote no row, so its
    prose goes only under the row of the copy dedup kept, once."""
    gen = _generator(supplementary_subpoints=True)
    grouped = gen._group_entries_by_code([_role(idx=57), _role(idx=58)])
    kept, before, _decisions = gen._dedup_grouped_entries(grouped)
    assert len(before["O"]) == 2 and len(kept["O"]) == 1
    gen._fill_leadership(kept["O"])
    gen._add_supplementary_subpoints(before)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _ROLE_ROW
    assert _deleted_texts(gen) == _ROLE_PROSE
    (placement,) = gen._subpoint_placements
    assert placement.element_idx_start == kept["O"][0]["element_idx_start"]


def test_leadership_and_appointment_prose_each_stay_under_their_own_section_row():
    """An appointment and a leadership role can share a title and an
    institution: each one's prose goes under its own section's row, never
    under the other's."""
    appointment_prose = ("Supervised the example clinic's residents and its weekly "
                         "interdisciplinary case conference")
    appointment = {"element_idx_start": 29, "taxonomy_code": "D1",
                   "text": f"2008-2012\t{_ROLE_TITLE}, {_ROLE_INSTITUTION}\t{appointment_prose}",
                   "extracted_fields": {"title": _ROLE_TITLE, "institution": _ROLE_INSTITUTION,
                                        "start_date": "2008", "end_date": "2012"}}
    gen = _generator(supplementary_subpoints=True)
    role = _role()
    gen._fill_positions({"D1": [appointment]})
    gen._fill_leadership([role])
    gen._add_supplementary_subpoints({"D1": [appointment], "O": [role]})

    by_text = {"".join(t.text for t in row.iter(qn("w:delText"))): row for row in _subpoint_rows(gen)}
    assert set(by_text) == {appointment_prose, _ROLE_PROSE}
    leadership_header = "Role(s)/Position"
    assert _row_text(by_text[_ROLE_PROSE].getparent().find(qn("w:tr"))).startswith(leadership_header)
    assert not _row_text(by_text[appointment_prose].getparent().find(qn("w:tr"))).startswith(
        leadership_header)
    assert by_text[_ROLE_PROSE].getparent() is not by_text[appointment_prose].getparent()


def test_the_doctor_matches_a_leadership_sub_point_to_its_own_entry_only(tmp_path, monkeypatch):
    """Through `run_stage6`: the sidecar records the earlier role's sub-point
    under its own index and O, and the doctor's per-entry match counts it for
    that entry, not for the later role that shares its words."""
    from unified_pipeline.doctor.shared import (
        docx_subpoint_lines,
        offered_as_subpoint,
        subpoint_placements,
        subpoint_text,
    )
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "T1205O", "cv_owner": {"full_name": "Quenby Marblegate"},
               "entries": [_later_role(), _role()]}
    input_path = tmp_path / "T1205O_fields.json"
    input_path.write_text(json.dumps(payload))

    out = run_stage6(str(input_path), str(tmp_path / "out.docx"), verbose=False)

    sidecar = json.loads((tmp_path / "T1205O_render_warnings.json").read_text())
    assert [(p["element_idx_start"], p["code"], p["paragraphs"])
            for p in sidecar[SUBPOINT_PLACEMENTS_KEY]] == [(57, "O", [_ROLE_PROSE])]
    subpoints = subpoint_text(docx_subpoint_lines(Document(out)), subpoint_placements(sidecar))
    assert offered_as_subpoint(_ROLE_PROSE, subpoints, element_idx=57)
    assert not offered_as_subpoint(_ROLE_PROSE, subpoints, element_idx=55)


def _stage6_render(tmp_path, monkeypatch, entries, uid="T1205P"):
    """Through `run_stage6`, so the PII pass and the date-led split (#983)
    run as in a real render: the document and its sidecar's placements."""
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    monkeypatch.setattr("unified_pipeline.stage_6_word_template.call_llm",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / f"{uid}_fields.json"
    input_path.write_text(json.dumps({"document_uid": uid,
                                      "cv_owner": {"full_name": "Quenby Marblegate"},
                                      "entries": entries}))
    out = run_stage6(str(input_path), str(tmp_path / "out.docx"), verbose=False)
    sidecar = json.loads((tmp_path / f"{uid}_render_warnings.json").read_text())
    return Document(out), [p["paragraphs"] for p in sidecar[SUBPOINT_PLACEMENTS_KEY]]


_SPOUSE = "Zelphine Qorvath"
_HOME_PHONE = "212-555-0147"


def _naming_the_spouse(entry, prose):
    """The spouse under a field named for it, and in the prose with no label."""
    entry["extracted_fields"]["spouse_name"] = _SPOUSE
    entry["text"] = entry["text"].replace(prose, f"{prose}, with my spouse {_SPOUSE}")
    return entry


def _with_a_home_phone(entry, prose):
    """A home phone number in the middle of the prose's line."""
    entry["text"] = entry["text"].replace(prose, f"{prose}. Home phone: {_HOME_PHONE}")
    return entry


@pytest.mark.parametrize("make, prose", [(_role, _ROLE_PROSE), (_honor, _HONOR_PROSE)],
                         ids=["O", "H"])
@pytest.mark.parametrize("protect, value", [(_naming_the_spouse, _SPOUSE),
                                            (_with_a_home_phone, _HOME_PHONE)],
                         ids=["spouse", "home_phone"])
def test_prose_holding_protected_data_is_not_offered_through_the_pii_pass(
        tmp_path, monkeypatch, make, prose, protect, value):
    """#1205 review, probes P7 and P9. The PII pass drops a field named for
    protected data and marks the entry withheld, but lists no
    `_pii_dropped_fields`; and no scope withholds a phone number in the
    middle of a line. Both printed in the entry's sub-point on Reject All,
    while the Appendix said the spouse was withheld. The same entry without
    them still has its sub-point."""
    _doc, placements = _stage6_render(tmp_path / "plain", monkeypatch, [make()])
    assert placements == [[prose]]

    doc, placements = _stage6_render(tmp_path / "protected", monkeypatch, [protect(make(), prose)])
    assert placements == []
    assert value not in "".join(t.text or "" for t in doc.element.body.iter(qn("w:t"), qn("w:delText")))


_CHAIR_PROSE = ("Established the departmental mentoring scheme and doubled faculty retention "
                "across affiliated hospitals")


def test_a_line_of_two_dated_roles_offers_neither_ones_prose(tmp_path, monkeypatch):
    """#1205 review, probe P12. One source line holds two roles, each opening
    with its dates and followed by its prose, and stage 4 returned the second.
    Both prose lines went under the second role's row on Reject All, the
    first role's achievement with them. Which role a line is about cannot be
    told, so neither is offered; the second role alone on its line still is."""
    first = f"2001-2004 Chair, Example Admissions Committee, {_ROLE_INSTITUTION}"
    second = f"2005-2010 {_ROLE_TITLE}, {_ROLE_INSTITUTION}"

    def role(text):
        return {"element_idx_start": 90, "taxonomy_code": "O", "text": text,
                "extracted_fields": {"leadership_role": _ROLE_TITLE, "institution": _ROLE_INSTITUTION,
                                     "start_date": "2005", "end_date": "2010"}}

    _doc, placements = _stage6_render(tmp_path / "one", monkeypatch, [role(f"{second}\t{_ROLE_PROSE}")])
    assert placements == [[_ROLE_PROSE]]

    doc, placements = _stage6_render(
        tmp_path / "two", monkeypatch, [role(f"{first}\t{_CHAIR_PROSE}\t{second}\t{_ROLE_PROSE}")])
    assert placements == []
    deleted = "".join(t.text or "" for t in doc.element.body.iter(qn("w:delText")))
    assert _CHAIR_PROSE not in deleted and _ROLE_PROSE not in deleted


# --- #1205: P institutional committees, the next code family -----------------
# The administrative-activities section writes every P entry as a row of its
# three-column table, so the prose is a deleted row under the committee's own
# row: the one row the writer recorded for that entry (the row-receipt
# ledger). An entry holding several records offers none.

_COMMITTEE_PROSE = ("Reviewed every proposed change to the core clerkship curriculum and drafted "
                    "the annual assessment report for the faculty council")
_COMMITTEE_NAME = "Example Curriculum Steering Committee"
_COMMITTEE_INSTITUTION = "Norvale University"


def _committee(idx=81, name=_COMMITTEE_NAME, role="Member", start="2008", end="2012",
               prose=_COMMITTEE_PROSE, code="P", *, institution=_COMMITTEE_INSTITUTION, **fields):
    """A committee whose source line goes on past its name, institution, role
    and years into what it did, as EBYSBC MQSUIC 615's did. Another term of
    the same committee shares every distinctive word of its row, and so does
    a later committee holding its words ("Executive ..."): only the years,
    which are digits, tell them apart. A value passed as None is in neither
    the line nor the fields."""
    named = {"committee_name": name, "role": role, "institution": institution,
             "start_date": start, "end_date": end}
    dates = "-".join(value for value in (start, end) if value)
    line = ", ".join(value for value in (name, institution, role, dates) if value)
    return {
        "element_idx_start": idx,
        "taxonomy_code": code,
        "text": line + (f"\t{prose}" if prose else ""),
        "extracted_fields": {key: value for key, value in {**named, **fields}.items()
                             if value is not None},
    }


def _later_committee(name=_COMMITTEE_NAME):
    """A later term at the same institution, with no prose: it renders first,
    reverse chronologically."""
    return _committee(idx=79, name=name, start="2016", end="2020", prose="")


def _render_committees(entries, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_administrative_activities(entries)
    gen._add_supplementary_subpoints({"P": entries})
    return gen


def _committee_table_row():
    """The same committee read from a source table: stage 2 joins the row's
    cells with pipes, and its position names the row ("41.2")."""
    entry = _committee()
    entry["element_idx_start"] = "41.2"
    entry["text"] = entry["text"].replace("\t", " | ")
    return entry


_COMMITTEE_ROW = f"{_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}Member2008-2012"
_COMMITTEE_HEADER = "Name of Committee"


def _table_header(row):
    return _row_text(row.getparent().find(qn("w:tr")))


@pytest.mark.parametrize("entry", [_committee, _committee_table_row])
def test_committee_prose_is_a_deleted_row_under_the_committee_row(entry):
    gen = _render_committees([entry()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _COMMITTEE_ROW
    assert _table_header(row).startswith(_COMMITTEE_HEADER)
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _COMMITTEE_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "3"
    assert gen.stats["supplementary_subpoints"] == 1


def test_a_committees_several_prose_lines_are_one_row_of_paragraphs_in_source_order():
    lines = (_COMMITTEE_PROSE, "Chaired its assessment sub-committee and the annual review of electives")
    gen = _render_committees([_committee(prose="\t".join(lines))], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _COMMITTEE_ROW
    assert ["".join(t.text for t in p.iter(qn("w:delText"))) for p in row.iter(qn("w:p"))] \
        == list(lines)


@pytest.mark.parametrize("later", [
    # One committee in two terms at one institution: the rows differ in their years only.
    _COMMITTEE_NAME,
    # A later committee at the same institution holding every word of this one.
    f"Executive {_COMMITTEE_NAME}",
])
def test_committee_prose_goes_under_its_own_row_among_committees_of_one_institution(later):
    """The later committee renders first and holds every distinctive word of
    the earlier one's row, so words alone put the earlier one's prose under
    the later row. Only the row the writer recorded for the earlier entry
    takes it."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_committees([_later_committee(later), _committee()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = row.getprevious()
    assert _row_text(above) == _COMMITTEE_ROW
    assert _row_text(above.getprevious()) == f"{later}, {_COMMITTEE_INSTITUTION}Member2016-2020"
    assert row.getnext() is None
    # Words alone would have taken the later committee's row.
    lines = list(supplementary.anchor_candidates(_render_committees(
        [_later_committee(later), _committee()], supplementary_subpoints=False).doc))
    rows = [line for line in lines if _COMMITTEE_INSTITUTION in line.text]
    assert supplementary.best_anchor(supplementary._value_tokens(_committee(), "P"), rows) is rows[0]
    assert rows[0].text.startswith(later)


def test_committee_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_committees([_later_committee(), _committee()], supplementary_subpoints=False)
    on = _render_committees([_later_committee(), _committee()], supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _COMMITTEE_PROSE in rejected
    assert _COMMITTEE_PROSE not in "".join(_texts_of(off.doc.element.body))
    assert len(_subpoint_rows(on)) == 1
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    # Empty paragraphs and every table row, cell by cell, included.
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


def test_a_short_committees_prose_goes_under_its_own_row():
    """An acronym, an acronym's institution and a short role hold one
    distinctive word, too few for the word test: the writer's record says
    the one row is the entry's own (`_pick_anchor`)."""
    gen = _render_committees([_committee(name="IRB", institution="NVU", role="Chair")],
                             supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == "IRB, NVUChair2008-2012"
    assert _deleted_texts(gen) == _COMMITTEE_PROSE


def _committee_with_no_name():
    """No committee name: the name cell prints the entry's line, its role
    and years taken out (#1346)."""
    return _committee(name=None, institution=None)


def _committee_with_parenthetical_years():
    """Nothing extracted: the writer reads the role and years out of the
    line's parenthesis and prints the rest of the line (#660)."""
    entry = _committee(name=None, role=None, start=None, end=None, institution=None)
    entry["text"] = f"{_COMMITTEE_NAME} (Chair 2008-2012). {_COMMITTEE_PROSE}"
    return entry


def _committee_on_two_lines_with_nothing_extracted():
    """Two lines, nothing extracted: the writer re-parses them, one row each."""
    return {"element_idx_start": 81, "taxonomy_code": "P", "extracted_fields": {},
            "text": f"{_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}, 2008-2012\n{_COMMITTEE_PROSE}"}


@pytest.mark.parametrize("entry", [_committee_with_no_name, _committee_with_parenthetical_years,
                                   _committee_on_two_lines_with_nothing_extracted])
def test_committee_prose_already_in_its_row_is_not_offered_again(entry):
    """The row prints it (`_record_rendered`), so it does not print twice."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_committees([entry()], supplementary_subpoints=True)
    assert _COMMITTEE_PROSE in "".join(_row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry(), "P", haystack) == []


def test_every_committee_row_field_the_pass_counts_is_printed_in_the_committee_row():
    """`supplementary._ADMINISTRATIVE_ROW_FIELDS`, the off-schema names among
    the fields the writer reads its name cell from (`_RENDERED_FIELDS` marks
    schema fields only): each one, alone on an entry with the schema's field
    empty, reaches the row."""
    from unified_pipeline.stage6 import supplementary

    assert supplementary._ADMINISTRATIVE_ROW_FIELDS
    for field in supplementary._ADMINISTRATIVE_ROW_FIELDS:
        marker = f"Zq{field}Z"
        entry = _committee(name=None, prose="", **{field: marker})
        gen = _render_committees([entry], supplementary_subpoints=True)
        assert any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr"))), field


def test_every_field_the_committee_row_prints_is_one_the_pass_counts():
    """The other direction: a field the row prints that `_value_tokens` does
    not count would offer a fragment restating the row. The names are the
    fields of the codes an entry is rerouted into P from (K3, O, Q1, Q2, and
    the positions D1-D3), each alone on an entry."""
    from unified_pipeline.stage6 import supplementary
    from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS

    counted = _RENDERED_FIELDS["P"] | set(supplementary._ADMINISTRATIVE_ROW_FIELDS)
    names = set().union(*(_RENDERED_FIELDS[code] for code in ("D1", "D2", "D3", "O", "Q1", "Q2")),
                        supplementary.ADMINISTRATIVE_ACTIVITY_FIELDS, {"name", "title", "position"})
    for field in sorted(names - {"start_date", "end_date"}):
        marker = f"Zq{field}Z"
        entry = {"element_idx_start": 81, "taxonomy_code": "P", "text": "2008-2012",
                 "extracted_fields": {field: marker, "start_date": "2008", "end_date": "2012"}}
        gen = _render_committees([entry], supplementary_subpoints=True)
        printed = any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
        assert not printed or field in counted, field


@pytest.mark.parametrize("field", ["committee", "activity"])
def test_a_committee_line_mostly_restating_its_row_is_not_offered(field):
    """Stage 4 named the committee under an off-schema key, which the row
    prints when the schema's is empty. A line at least half of whose words
    restate it is the row (`SUBPOINT_FIELD_TOKEN_MAX_SHARE`); the document
    holds too few of its words for `_record_rendered` to see that."""
    from unified_pipeline.stage6 import supplementary

    value = "Regional Pharmacy Formulary Review Panel"
    line = f"{value}: approved statewide pediatric dosing"
    entry = _committee(name=None, prose=line, **{field: value})
    gen = _render_committees([entry], supplementary_subpoints=True)
    assert any(value in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary._record_rendered(line, haystack.text, haystack.line_tokens) is False
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


# The role first, as many CVs write it. A line opening with the name cell's
# own text is read as that row, prose and all (`_record_rendered`'s verbatim
# piece), and is not offered.
_ADMISSIONS_LINE = f"Chair, Example Admissions Committee, {_COMMITTEE_INSTITUTION}, 2001-2004"
_ADMISSIONS_PROSE = ("Interviewed every applicant shortlisted for the rural medicine track "
                     "and chaired the appeals hearings")


def test_a_list_of_committee_records_prints_a_row_each_and_offers_nothing():
    """Stage 4 packed two committees as a list under the committee name
    (#208/#248): the writer prints a row for each, both the entry's own (the
    ledger), and which of them a prose line is about cannot be shown. The
    line names no years, so the writer's rows alone say it holds two."""
    entry = {"element_idx_start": 60, "taxonomy_code": "P",
             "text": f"Chair, Example Admissions Committee; Member, {_COMMITTEE_NAME}"
                     f"\t{_ADMISSIONS_PROSE}",
             "extracted_fields": {"committee_name": [
                 {"committee_name": "Example Admissions Committee", "role": "Chair",
                  "start_date": "2001", "end_date": "2004"},
                 {"committee_name": _COMMITTEE_NAME, "role": "Member",
                  "start_date": "2008", "end_date": "2012"}]}}
    gen = _render_committees([entry], supplementary_subpoints=True)

    assert len(gen._row_receipts.elements_for(entry)) == 2
    assert _ADMISSIONS_PROSE not in "".join(_row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_block_of_dated_committees_prints_a_row_each_and_offers_nothing():
    """Two or more dated lines are a source table flattened into one entry
    (#660): the writer prints each line as a row of its own
    (`_multiline_committee_rows`), a prose line included, so nothing is left
    to offer. Every row is the entry's own."""
    block = (f"Example Admissions Committee | Chair | 2001-2004\n"
             f"{_COMMITTEE_NAME} | Member | 2008-2012\n"
             f"{_COMMITTEE_PROSE}")
    entry = {"element_idx_start": 60, "taxonomy_code": "P", "text": block,
             "extracted_fields": {"committee_name": "Example Admissions Committee",
                                  "role": "Chair", "start_date": "2001", "end_date": "2004"}}
    gen = _render_committees([entry], supplementary_subpoints=True)

    rows = gen._row_receipts.elements_for(entry)
    assert len(rows) == 3 and _COMMITTEE_PROSE in _row_text(rows[2])
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _fused_committees():
    """Three committees each followed by its years, which stage 2 joined with
    tabs (EBYSBC YOXXOH 74's shape): one line, so the writer prints one row,
    of the record stage 4 extracted, the last; the other two are its records,
    not its prose."""
    parts = ["Alpha Questionnaire Working Group, Kestrel Medical Society, 1979-1980",
             "Bravo Strategic Planning Group, Osprey Regional Council, 1980-1982",
             f"{_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}, Steering Group, 1986"]
    return {"element_idx_start": 74, "taxonomy_code": "P", "text": "\t".join(parts),
            "extracted_fields": {"committee_name": _COMMITTEE_NAME, "role": "Steering Group",
                                 "institution": _COMMITTEE_INSTITUTION, "start_date": "1986"}}


@pytest.mark.parametrize("flag", [True, False])
def test_a_line_of_several_dated_committees_offers_none_and_its_records_are_recovered(flag):
    """#1205 comment 6275741663: the #221 recovery reads every sub-point
    line as rendered, so a committee record offered as another's prose would
    be in the accepted document nowhere and still not be recovered. None is
    offered, and the recovery re-emits the same records with sub-points on
    as off."""
    entry = _fused_committees()
    gen = _render_committees([entry], supplementary_subpoints=flag)
    assert len(gen._row_receipts.elements_for(entry)) == 1
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""

    gen._recover_unrendered_records({"P": [entry]})

    assert gen.stats["unrendered_records_recovered"] == 2
    page = "".join(_texts_of(gen.doc.element.body))
    assert "Alpha Questionnaire Working Group" in page and "Bravo Strategic Planning Group" in page


def test_a_committee_the_section_writes_no_row_for_gets_no_sub_point():
    """A record naming no committee is skipped (#256), so no row is the
    entry's own: its prose goes under no other line of the document, though
    the other committee's row holds every distinctive word of its line."""
    nameless = _committee(name=None)
    nameless["extracted_fields"] = {"committee_name": [{"role": "Member", "start_date": "2008"}]}
    later = _later_committee()
    gen = _render_committees([later, nameless], supplementary_subpoints=True)

    assert gen._row_receipts.elements_for(nameless) == []
    assert gen._row_receipts.elements_for(later)
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


_KESTREL_LINE = "Member, Example Admissions Appeals Committee, Kestrel Medical Society"


def _committee_after_an_undated_committee():
    """Another committee, undated, then the extracted one with its years: one
    dated part, so the writer's #660 test reads one record."""
    entry = _committee()
    entry["text"] = f"{_KESTREL_LINE}\t{entry['text']}"
    return entry


def _committee_list_with_a_nameless_record():
    """Stage 4 packed a record naming no committee and the committee in a
    list (#208/#248): the writer prints no row for the nameless one (#256)."""
    entry = _committee(prose=None)
    entry["text"] = (f"Member, 2001-2004, {_ADMISSIONS_PROSE}\t"
                     f"{_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}, Member")
    entry["extracted_fields"] = {"committee_name": [
        {"role": "Member", "start_date": "2001", "end_date": "2004"},
        {"committee_name": _COMMITTEE_NAME, "institution": _COMMITTEE_INSTITUTION,
         "role": "Member"}]}
    return entry


@pytest.mark.parametrize("entry", [_committee_after_an_undated_committee,
                                   _committee_list_with_a_nameless_record])
def test_a_committee_row_holding_another_record_gets_no_sub_point(entry):
    """The writer prints one row, the entry's own, though the line holds
    another record: that record's text would be offered as the committee's
    prose, under its row."""
    entry = entry()
    gen = _render_committees([entry], supplementary_subpoints=True)

    assert len(gen._row_receipts.elements_for(entry)) == 1
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _render_grouped_committees(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_administrative_activities(grouped.get("P", []))
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def _committee_line(text, *committees):
    """One source line stage 4 returned as a record per (name, role, start,
    end), the entry's own fields being the last record's. Fan-out gives every
    earlier record a line of its own and the last one the whole line."""
    records = [{"committee_name": name, "role": role, "institution": _COMMITTEE_INSTITUTION,
                "start_date": start, "end_date": end} for name, role, start, end in committees]
    return {"element_idx_start": 63, "taxonomy_code": "P", "text": text,
            "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}


_ADMISSIONS = ("Example Admissions Committee", "Chair", "2001", "2004")
_STEERING = (_COMMITTEE_NAME, "Member", "2005", "2010")
_STEERING_LINE = f"Member, {_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}, 2005-2010"


@pytest.mark.parametrize("segments, offered", [
    # The segments pair with the records: the prose goes under its own committee.
    ([f"{_ADMISSIONS_LINE}. {_ADMISSIONS_PROSE}", _STEERING_LINE],
     [("Example Admissions Committee", f"{_ADMISSIONS_LINE}. {_ADMISSIONS_PROSE}")]),
    ([_ADMISSIONS_LINE, f"{_STEERING_LINE}. {_COMMITTEE_PROSE}"],
     [(_COMMITTEE_NAME, f"{_STEERING_LINE}. {_COMMITTEE_PROSE}")]),
    # They do not: which committee the prose is about cannot be told, so neither takes it.
    ([_ADMISSIONS_LINE, _COMMITTEE_PROSE, _STEERING_LINE], []),
])
def test_prose_on_a_line_of_several_committees_goes_under_its_own_or_none(segments, offered):
    """A sub-point is the whole tab segment, as the CV wrote it: one that
    holds the committee's line as well as its prose brings the name,
    institution, role and years along, and Reject All prints them a second
    time, under the row that holds them (the shape every family has; #1205,
    2026-10-09)."""
    entry = _committee_line("\t".join(segments), _ADMISSIONS, _STEERING)
    gen, grouped = _render_grouped_committees([entry], supplementary_subpoints=True)

    assert LAST_STAGE4_RECORD in grouped["P"][-1] and len(grouped["P"]) == 2
    assert [(_row_text(row.getprevious()).split(",")[0],
             "".join(t.text for t in row.iter(qn("w:delText")))) for row in _subpoint_rows(gen)] \
        == offered


def test_a_committee_line_opening_with_its_name_cell_is_read_as_its_row():
    """The name cell folds the institution in ("Committee, Institution"), so
    a line that opens with that text holds the row's own text verbatim, and
    `_record_rendered` reads the whole line as rendered: none of it is
    offered, its prose included. A line opening with the role is offered
    whole, as the CV wrote it."""
    name_first = f"{_COMMITTEE_NAME}, {_COMMITTEE_INSTITUTION}, Member, 2005-2010. {_COMMITTEE_PROSE}"
    role_first = f"{_STEERING_LINE}. {_COMMITTEE_PROSE}"
    for line, offered in ((name_first, ""), (role_first, role_first)):
        entry = _committee(start="2005", end="2010", prose=None)
        entry["text"] = line
        gen = _render_committees([entry], supplementary_subpoints=True)
        assert _deleted_texts(gen) == offered


def test_generic_list_fan_out_children_of_committees_offer_nothing_their_fields_do_not_hold():
    """Fan-out splits a record list stage 4 filed under a key the schema does
    not define only when the fields hold every word of the text, and each
    child's text is then the line built from its fields: nothing is prose.
    With prose in the text the list is not split, the entry prints its whole
    line in the name cell, and nothing is left to offer either."""
    records = [{"committee_name": "Example Admissions Committee", "role": "Chair",
                "institution": _COMMITTEE_INSTITUTION, "start_date": "2001", "end_date": "2004"},
               {"committee_name": _COMMITTEE_NAME, "role": "Member",
                "institution": _COMMITTEE_INSTITUTION, "start_date": "2005", "end_date": "2010"}]
    for text, children in ((f"{_ADMISSIONS_LINE}\t{_STEERING_LINE}", 2),
                           (f"{_ADMISSIONS_LINE}\t{_STEERING_LINE}\t{_COMMITTEE_PROSE}", 1)):
        entry = {"element_idx_start": 64, "taxonomy_code": "P", "text": text,
                 "extracted_fields": {"committees": copy.deepcopy(records)}}
        gen, grouped = _render_grouped_committees([entry], supplementary_subpoints=True)
        assert len(grouped["P"]) == children
        assert all(gen._row_receipts.elements_for(e) for e in grouped["P"])
        assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_committee_rerouted_out_of_p_follows_its_destination_and_gets_no_sub_point():
    """N1 is excluded: a committee under a mentoring-programmes heading
    renders as N1. C is not offered, and its heading lookup finds the title
    block, not its section: a record holding only an institution and a start
    year under a training heading renders as C. Neither the committee row of
    the same institution nor any other carries their prose."""
    programme = _rerouted(_committee(idx=70), "N1", "MENTORING PROGRAMS")
    training = _rerouted(_committee(idx=71, name=None, role=None, end=None,
                                    prose="Trained in quantitative methods for clerkship "
                                          "curriculum evaluation"), "C", "POSTDOCTORAL TRAINING")
    gen, grouped = _render_grouped_committees([_later_committee(), programme, training],
                                              supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["N1"]] == [70]
    assert [e["element_idx_start"] for e in grouped["C"]] == [71]
    assert [e["element_idx_start"] for e in grouped["P"]] == [79]
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_refused_reroute_keeps_the_committee_rule():
    """A committee rerouted to I keeps its P code: I's renderer would drop
    its committee name (`_fields_lost_in_reroute`). It renders as a P row and
    its prose is a deleted row under that row."""
    gen, grouped = _render_grouped_committees([_rerouted(_committee(), "I", "MEMBERSHIPS")],
                                              supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["P"]] == [81]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _COMMITTEE_ROW


@pytest.mark.parametrize("code", ["Q2", "K3"])
def test_a_record_rerouted_into_p_follows_the_committee_rule(code):
    """A board seat stage 3b coded Q2, or an administrative teaching role it
    coded K3, under the institutional administrative heading renders as a P
    row: its prose is a deleted row under that row."""
    gen, grouped = _render_grouped_committees(
        [_rerouted(_committee(code=code), "P", "INSTITUTIONAL ADMINISTRATIVE ACTIVITIES")],
        supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["P"]] == [81]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _COMMITTEE_ROW
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _COMMITTEE_PROSE
    (placement,) = gen._subpoint_placements
    assert placement.code == "P"


def test_a_committee_whose_entry_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass touched the entry. What is left can still describe
    what was withheld, so none of it is offered."""
    withheld = _committee()
    withheld["_pii_withheld"] = True
    gen = _render_committees([_later_committee(), withheld], supplementary_subpoints=True)

    assert any(_row_text(tr) == _COMMITTEE_ROW for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_committee_dedup_drops_offers_its_prose_once_under_the_kept_row():
    """Stage 6 dedups before the sections render and hands the sub-point
    pass the entries from before it: the dropped copy wrote no row, so its
    prose goes only under the row of the copy dedup kept, once."""
    gen = _generator(supplementary_subpoints=True)
    grouped = gen._group_entries_by_code([_committee(idx=81), _committee(idx=82)])
    kept, before, _decisions = gen._dedup_grouped_entries(grouped)
    assert len(before["P"]) == 2 and len(kept["P"]) == 1
    gen._fill_administrative_activities(kept["P"])
    gen._add_supplementary_subpoints(before)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _COMMITTEE_ROW
    assert _deleted_texts(gen) == _COMMITTEE_PROSE
    (placement,) = gen._subpoint_placements
    assert placement.element_idx_start == kept["P"][0]["element_idx_start"]


def test_committee_leadership_and_appointment_prose_each_stay_under_their_own_section_row():
    """A committee, the leadership role of chairing it and an appointment can
    share a title and an institution: each one's prose goes under its own
    section's row, never under another's."""
    title = f"Chair, {_COMMITTEE_NAME}"
    appointment_prose = ("Supervised the example clinic's residents and its weekly "
                         "interdisciplinary case conference")
    role_prose = ("Oversaw an annual operating budget and twelve clinical staff, and launched "
                  "the regional outreach programme for community clinics")
    appointment = {"element_idx_start": 29, "taxonomy_code": "D1",
                   "text": f"2008-2012\t{title}, {_COMMITTEE_INSTITUTION}\t{appointment_prose}",
                   "extracted_fields": {"title": title, "institution": _COMMITTEE_INSTITUTION,
                                        "start_date": "2008", "end_date": "2012"}}
    role = {"element_idx_start": 57, "taxonomy_code": "O",
            "text": f"2008-2012\t{title}, {_COMMITTEE_INSTITUTION}\t{role_prose}",
            "extracted_fields": {"leadership_role": title, "institution": _COMMITTEE_INSTITUTION,
                                 "start_date": "2008", "end_date": "2012"}}
    committee = _committee(role="Chair")
    gen = _generator(supplementary_subpoints=True)
    gen._fill_positions({"D1": [appointment]})
    gen._fill_leadership([role])
    gen._fill_administrative_activities([committee])
    gen._add_supplementary_subpoints({"D1": [appointment], "O": [role], "P": [committee]})

    by_text = {"".join(t.text for t in row.iter(qn("w:delText"))): row for row in _subpoint_rows(gen)}
    assert set(by_text) == {appointment_prose, role_prose, _COMMITTEE_PROSE}
    assert _table_header(by_text[_COMMITTEE_PROSE]).startswith(_COMMITTEE_HEADER)
    assert _table_header(by_text[role_prose]).startswith("Role(s)/Position")
    assert not _table_header(by_text[appointment_prose]).startswith(
        (_COMMITTEE_HEADER, "Role(s)/Position"))
    assert len({id(row.getparent()) for row in by_text.values()}) == 3


def test_the_doctor_matches_a_committee_sub_point_to_its_own_entry_only(tmp_path, monkeypatch):
    """Through `run_stage6`: the sidecar records the earlier term's sub-point
    under its own index and P, and the doctor's per-entry match counts it for
    that entry, not for the later term that shares its words."""
    from unified_pipeline.doctor.shared import (
        docx_subpoint_lines,
        offered_as_subpoint,
        subpoint_placements,
        subpoint_text,
    )

    doc, placements = _stage6_render(tmp_path, monkeypatch, [_later_committee(), _committee()],
                                     uid="T1205C")
    assert placements == [[_COMMITTEE_PROSE]]
    sidecar = json.loads((tmp_path / "T1205C_render_warnings.json").read_text())
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    assert [(p["element_idx_start"], p["code"]) for p in sidecar[SUBPOINT_PLACEMENTS_KEY]] == [(81, "P")]
    subpoints = subpoint_text(docx_subpoint_lines(doc), subpoint_placements(sidecar))
    assert offered_as_subpoint(_COMMITTEE_PROSE, subpoints, element_idx=81)
    assert not offered_as_subpoint(_COMMITTEE_PROSE, subpoints, element_idx=79)


@pytest.mark.parametrize("protect, value", [(_naming_the_spouse, _SPOUSE),
                                            (_with_a_home_phone, _HOME_PHONE)],
                         ids=["spouse", "home_phone"])
def test_committee_prose_holding_protected_data_is_not_offered_through_the_pii_pass(
        tmp_path, monkeypatch, protect, value):
    """The PII pass drops a field named for protected data and marks the
    entry withheld; no scope withholds a phone number in the middle of a
    line (#1205 review of O). The same committee without them still has its
    sub-point."""
    _doc, placements = _stage6_render(tmp_path / "plain", monkeypatch, [_committee()])
    assert placements == [[_COMMITTEE_PROSE]]

    doc, placements = _stage6_render(tmp_path / "protected", monkeypatch,
                                     [protect(_committee(), _COMMITTEE_PROSE)])
    assert placements == []
    assert value not in "".join(t.text or "" for t in doc.element.body.iter(qn("w:t"), qn("w:delText")))


# --- #1205: R invited presentations, the next code family --------------------
# The presentations section writes every R entry as one row of its Regional,
# National or International table, so the prose is a deleted row under the
# talk's own row: the one row the writer recorded for that entry (the
# row-receipt ledger). An entry holding several talks offers none.

_TALK_PROSE = ("Delivered to an audience of about two hundred residents and nursing staff, "
               "followed by a moderated question session")
_TALK_TITLE = "Example Advances in Coastal Wetland Hydrology"
_TALK_EVENT = "Example Grand Rounds"
_TALK_VENUE = "Norvale University, Crab Hollow, ZQ"


def _talk(idx=601, title=_TALK_TITLE, event=_TALK_EVENT, venue=_TALK_VENUE, date="2016",
          prose=_TALK_PROSE, code="R", **fields):
    """A talk whose source line goes on past its title, meeting, venue and
    year into who it was for, as YUYVIG SIJYJZ 621's did. Another talk of the
    same title at the same venue shares every distinctive word of its row:
    only the year, a digit, tells them apart. A value passed as None is in
    neither the line nor the fields."""
    named = {"title": title, "event_name": event, "location": venue, "date": date}
    line = ", ".join(value for value in (title, event, venue, date) if value)
    return {
        "element_idx_start": idx,
        "taxonomy_code": code,
        "text": line + (f"\t{prose}" if prose else ""),
        "extracted_fields": {key: value for key, value in {**named, **fields}.items()
                             if value is not None},
    }


def _later_talk(title=_TALK_TITLE):
    """The same talk given again, at the same venue, with no prose: it renders
    first, reverse chronologically."""
    return _talk(idx=599, title=title, date="2019", prose="")


def _render_talks(entries, **kwargs):
    gen = _generator(**kwargs)
    gen._fill_presentations(entries)
    gen._add_supplementary_subpoints({"R": entries})
    return gen


def _talk_table_row():
    """The same talk read from a source table: stage 2 joins the row's cells
    with pipes, and its position names the row ("41.2")."""
    entry = _talk()
    entry["element_idx_start"] = "41.2"
    entry["text"] = entry["text"].replace("\t", " | ")
    return entry


_TALK_ROW = f"{_TALK_TITLE}{_TALK_EVENT}, {_TALK_VENUE}2016"
_TALK_HEADER = "TitleInstitution/LocationDates (yyyy)"


@pytest.mark.parametrize("entry", [_talk, _talk_table_row])
def test_talk_prose_is_a_deleted_row_under_the_talk_row(entry):
    gen = _render_talks([entry()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _TALK_ROW
    assert _table_header(row) == _TALK_HEADER
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _TALK_PROSE
    span = row.find(f"{qn('w:tc')}/{qn('w:tcPr')}/{qn('w:gridSpan')}")
    assert span.get(qn("w:val")) == "3"
    assert gen.stats["supplementary_subpoints"] == 1


def test_a_talks_several_prose_lines_are_one_row_of_paragraphs_in_source_order():
    lines = (_TALK_PROSE, "Co-presented with two hospital pharmacists from the regional formulary service")
    gen = _render_talks([_talk(prose="\t".join(lines))], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _TALK_ROW
    assert ["".join(t.text for t in p.iter(qn("w:delText"))) for p in row.iter(qn("w:p"))] \
        == list(lines)


@pytest.mark.parametrize("later", [
    # One talk given twice at one venue: the rows differ in their years only.
    _TALK_TITLE,
    # A later talk at the same venue holding every word of this one's title.
    f"Revisiting {_TALK_TITLE}",
])
def test_talk_prose_goes_under_its_own_row_among_talks_of_one_venue(later):
    """The later talk renders first and holds every distinctive word of the
    earlier one's row, so words alone put the earlier one's prose under the
    later row. Only the row the writer recorded for the earlier entry takes
    it."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_talks([_later_talk(later), _talk()], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    above = row.getprevious()
    assert _row_text(above) == _TALK_ROW
    assert _row_text(above.getprevious()) == f"{later}{_TALK_EVENT}, {_TALK_VENUE}2019"
    assert row.getnext() is None
    # Words alone would have taken the later talk's row.
    lines = list(supplementary.anchor_candidates(_render_talks(
        [_later_talk(later), _talk()], supplementary_subpoints=False).doc))
    rows = [line for line in lines if _TALK_VENUE in line.text]
    assert supplementary.best_anchor(supplementary._value_tokens(_talk(), "R"), rows) is rows[0]
    assert rows[0].text.startswith(later)


def test_talks_filed_under_two_scope_tables_each_take_their_own_prose():
    """A talk the CV files under its International heading renders in that
    table, the other in National: each prose goes under its own talk's row,
    in its own table."""
    abroad_prose = "Keynote for the closing plenary, followed by a workshop for early career faculty"
    abroad = _talk(idx=603, venue="Kestrel Institute, Osprey Bay", prose=abroad_prose)
    abroad["hierarchy"] = ["INVITATIONS TO SPEAK/PRESENT", "International"]
    gen = _render_talks([abroad, _talk()], supplementary_subpoints=True)

    by_text = {"".join(t.text for t in row.iter(qn("w:delText"))): row for row in _subpoint_rows(gen)}
    assert set(by_text) == {abroad_prose, _TALK_PROSE}
    assert _row_text(by_text[_TALK_PROSE].getprevious()) == _TALK_ROW
    assert _row_text(by_text[abroad_prose].getprevious()).startswith(_TALK_TITLE)
    assert "Kestrel Institute" in _row_text(by_text[abroad_prose].getprevious())
    assert by_text[_TALK_PROSE].getparent() is not by_text[abroad_prose].getparent()


def test_talk_sub_point_accept_all_is_the_flag_off_render_and_reject_restores_it():
    off = _render_talks([_later_talk(), _talk()], supplementary_subpoints=False)
    on = _render_talks([_later_talk(), _talk()], supplementary_subpoints=True)

    rejected = "".join(t.text or "" for t in on.doc.element.body.iter(qn("w:t"), qn("w:delText")))
    assert _TALK_PROSE in rejected
    assert _TALK_PROSE not in "".join(_texts_of(off.doc.element.body))
    assert len(_subpoint_rows(on)) == 1
    for row in _subpoint_rows(on):
        row.getparent().remove(row)
    # Empty paragraphs and every table row, cell by cell, included.
    assert _texts_of(on.doc.element.body) == _texts_of(off.doc.element.body)
    assert _row_texts(on.doc.element.body) == _row_texts(off.doc.element.body)


def test_a_short_talks_prose_goes_under_its_own_row():
    """An acronym title at an acronym's venue holds no distinctive word, too
    few for the word test: the writer's record says the one row is the
    entry's own (`_pick_anchor`)."""
    gen = _render_talks([_talk(title="IBD", event=None, venue="NVU")], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == "IBDNVU2016"
    assert _deleted_texts(gen) == _TALK_PROSE


def _untitled_talk():
    """No title: the Title cell is the entry's line without the meeting,
    venue and year the other cells show (`_untitled_talk_title`), so it
    prints the prose too."""
    return _talk(title=None)


def _talk_titled_by_its_prose():
    """The prose is the title stage 4 extracted."""
    entry = _talk(prose=None)
    entry["extracted_fields"]["title"] = _TALK_PROSE
    entry["text"] = f"{_TALK_PROSE}, {_TALK_EVENT}, {_TALK_VENUE}, 2016"
    return entry


@pytest.mark.parametrize("entry", [_untitled_talk, _talk_titled_by_its_prose])
def test_talk_prose_already_in_its_row_is_not_offered_again(entry):
    """The row prints it (`_record_rendered`), so it does not print twice: a
    title derived from the line counts as rendered."""
    from unified_pipeline.stage6 import supplementary

    gen = _render_talks([entry()], supplementary_subpoints=True)
    assert _TALK_PROSE in "".join(_row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary.unrendered_prose(entry(), "R", haystack) == []


# A date-shaped marker for the date fields, which the Dates cell prints
# only as a date; an end date only after a start.
_DATE_MARKERS = {"year": "1987", "start_date": "1987", "end_date": "1989"}


def _talk_with_only(field, marker):
    entry = _talk(title=None, event=None, venue=None, date=None, prose="")
    entry["extracted_fields"] = {field: marker}
    if field == "end_date":
        entry["extracted_fields"]["start_date"] = "1985"
    entry["text"] = "Example invited lecture"
    return entry


def test_every_talk_row_field_the_pass_counts_is_printed_in_the_talk_row():
    """`supplementary._PRESENTATION_ROW_FIELDS`, the off-schema names among
    the fields the writer reads its cells from (`_RENDERED_FIELDS` marks
    schema fields only): each one, alone on an entry, reaches the row."""
    from unified_pipeline.stage6 import supplementary

    assert supplementary._PRESENTATION_ROW_FIELDS
    for field in supplementary._PRESENTATION_ROW_FIELDS:
        marker = _DATE_MARKERS.get(field, f"Zq{field}Z")
        gen = _render_talks([_talk_with_only(field, marker)], supplementary_subpoints=True)
        assert any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr"))), field


def test_every_field_the_talk_row_prints_is_one_the_pass_counts():
    """The other direction: a field the row prints that `_value_tokens` does
    not count would offer a fragment restating the row. The names are the
    fields of the codes an entry is rerouted into R from (S8, S9, K1-K3,
    Q2, H, D1), each alone on an entry."""
    from unified_pipeline.stage6 import supplementary
    from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS

    counted = _RENDERED_FIELDS["R"] | set(supplementary._PRESENTATION_ROW_FIELDS)
    names = set().union(*(_RENDERED_FIELDS[code] for code in
                          ("S8", "S9", "K1", "K2", "K3", "Q2", "H", "D1") if code in _RENDERED_FIELDS),
                        supplementary.PRESENTATION_TITLE_FIELDS, supplementary.PRESENTATION_VENUE_FIELDS,
                        supplementary.PRESENTATION_EVENT_FIELDS, {"name", "organization", "position"})
    for field in sorted(names):
        marker = f"Zq{field}Z"
        gen = _render_talks([_talk_with_only(field, marker)], supplementary_subpoints=True)
        printed = any(marker in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
        assert not printed or field in counted, field


@pytest.mark.parametrize("field", ["presentation_title", "institution", "venue", "conference_name"])
def test_a_talk_line_mostly_restating_its_row_is_not_offered(field):
    """Stage 4 named the talk's title, venue or meeting under an off-schema
    key, which the row prints when the schema's is empty. A line at least
    half of whose words restate it is the row
    (`SUBPOINT_FIELD_TOKEN_MAX_SHARE`); the document holds too few of its
    words for `_record_rendered` to see that."""
    from unified_pipeline.stage6 import supplementary

    value = "Regional Pharmacy Formulary Review Symposium"
    line = "Formulary Review Symposium for Regional Pharmacy: approved statewide pediatric dosing"
    schema_name = {"presentation_title": "title", "institution": "location",
                   "venue": "location", "conference_name": "event_name"}[field]
    entry = _talk(prose=line)
    del entry["extracted_fields"][schema_name]
    entry["extracted_fields"][field] = value
    gen = _render_talks([entry], supplementary_subpoints=True)
    assert any(value in _row_text(tr) for tr in gen.doc.element.body.iter(qn("w:tr")))
    haystack = supplementary.rendered_haystack(gen._rendered_output_lines(), gen.doc)
    assert supplementary._record_rendered(line, haystack.text, haystack.line_tokens) is False
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


_OTHER_TALK = ("Kestrel Lecture on Estuary Sediment Transport and Marsh Restoration Outcomes, "
               "Osprey Regional Council, Bramble Harbour")


def _talks_in_one_paragraph():
    """Two talks each opening with its year, which the CV wrote in one
    paragraph (EBYSBC BMAMWE 1440's shape): one line, so the writer prints
    one row, of the talk stage 4 extracted; the other is its record, not its
    prose."""
    entry = _talk(prose=None)
    entry["text"] = (f"2016 {_TALK_VENUE}: {_TALK_TITLE}, {_TALK_EVENT} "
                     f"2016 {_OTHER_TALK}, Crab Hollow, ZQ")
    return entry


def _talks_each_ending_in_its_year():
    """Two talks each ending in its year, which stage 2 joined with a tab:
    the writer-side #660 test reads two dated parts."""
    entry = _talk(prose=None)
    entry["text"] = f"{_TALK_TITLE}, {_TALK_EVENT}, {_TALK_VENUE}, 2016\t{_OTHER_TALK}, 2014"
    return entry


def _talks_each_ending_in_its_bracketed_year():
    """The same, each year in brackets, which ends no part for the #660 test."""
    entry = _talk(prose=None)
    entry["text"] = (f"{_TALK_TITLE}, {_TALK_EVENT}, {_TALK_VENUE} (2016)\t{_OTHER_TALK} (2014)"
                     f"\t{_TALK_PROSE}")
    return entry


@pytest.mark.parametrize("make", [_talks_in_one_paragraph, _talks_each_ending_in_its_year,
                                  _talks_each_ending_in_its_bracketed_year])
@pytest.mark.parametrize("flag", [True, False])
def test_a_line_of_several_dated_talks_offers_none_and_its_records_are_recovered(make, flag):
    """#1205 comment 6275741663: the #221 recovery reads every sub-point
    line as rendered, so a talk offered as another's prose would be in the
    accepted document nowhere and still not be recovered. None is offered,
    and the recovery re-emits the same lines with sub-points on as off."""
    entry = make()
    gen = _render_talks([entry], supplementary_subpoints=flag)
    assert len(gen._row_receipts.elements_for(entry)) == 1
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""

    gen._recover_unrendered_records({"R": [entry]})
    off = _render_talks([make()], supplementary_subpoints=False)
    off._recover_unrendered_records({"R": [make()]})

    assert gen.stats["unrendered_records_recovered"] == off.stats["unrendered_records_recovered"]
    assert _texts_of(gen.doc.element.body) == _texts_of(off.doc.element.body)


@pytest.mark.parametrize("dates", [
    # Stage 4 kept both dates under the talk's own key ...
    {"date": "2014; 2016"},
    # ... or one under it and the other as a further span (#1245).
    {"date": "2016", "additional_dates": ["2014"]},
])
def test_one_talk_given_on_two_dates_is_one_record_and_offers_its_prose(dates):
    """The same title at two venues, each opening with its year: the Dates
    cell prints both years, so the two dated parts are this talk's own dates,
    not two talks (`_talk_dates`). Its prose goes under its row."""
    entry = _talk(prose=None, **dates)
    entry["text"] = (f"2014 {_TALK_TITLE}, {_TALK_EVENT}, Kestrel Institute\t"
                     f"2016 {_TALK_TITLE}, {_TALK_EVENT}, {_TALK_VENUE}\t{_TALK_PROSE}")
    gen = _render_talks([entry], supplementary_subpoints=True)

    (row,) = _subpoint_rows(gen)
    assert set(_row_texts_of(row.getprevious())[-1].split(", ")) == {"2014", "2016"}
    assert _deleted_texts(gen) == _TALK_PROSE


@pytest.mark.parametrize("dates", [
    {},
    # A further span repeating the talk's own date, which the cell drops.
    {"additional_dates": ["2016"]},
    # Its one date only under a further span, the string "None" its own.
    {"date": None, "year": "None", "additional_dates": ["2016"]},
], ids=["one_date", "span_repeats_its_date", "none_string"])
def test_the_same_line_with_one_date_kept_is_two_talks_and_offers_none(dates):
    """With one date kept, the row prints one occasion: the other dated part
    is a talk the row does not print."""
    entry = _talk(prose=None, **dates)
    entry["text"] = (f"2014 {_TALK_TITLE}, {_TALK_EVENT}, Kestrel Institute\t"
                     f"2016 {_TALK_TITLE}, {_TALK_EVENT}, {_TALK_VENUE}\t{_TALK_PROSE}")
    gen = _render_talks([entry], supplementary_subpoints=True)
    (row,) = gen._row_receipts.elements_for(entry)
    assert _row_texts_of(row)[-1] == "2016"
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _talk_quoting_another_title():
    """Two quoted titles on one undated line: the other talk's title shares
    no word with this one's."""
    entry = _talk(prose=None)
    entry["text"] = (f"“{_TALK_TITLE}”, {_TALK_EVENT}, {_TALK_VENUE}. “Estuary Sediment "
                     f"Transport in Tidal Marshes”, Osprey Regional Council. {_TALK_PROSE}")
    return entry


def _talk_list_kept_whole():
    """Stage 4 listed a second talk under an off-schema key, and fan-out kept
    the list whole because the text holds prose no record does (#983): the
    writer prints the entry's own fields only."""
    entry = _talk()
    entry["extracted_fields"]["additional_presentations"] = [
        {"title": "Estuary Sediment Transport in Tidal Marshes", "location": "Osprey Bay",
         "date": "2014"}]
    return entry


@pytest.mark.parametrize("entry", [_talk_quoting_another_title, _talk_list_kept_whole])
def test_a_talk_row_holding_another_talk_gets_no_sub_point(entry):
    """The writer prints one row, the entry's own, though the line holds
    another talk: that talk's text would be offered as this one's prose,
    under its row."""
    entry = entry()
    gen = _render_talks([entry], supplementary_subpoints=True)

    assert len(gen._row_receipts.elements_for(entry)) == 1
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_talk_quoting_its_own_title_differently_punctuated_offers_its_prose():
    entry = _talk(prose=None)
    entry["text"] = f"“{_TALK_TITLE.lower()}.” {_TALK_EVENT}, {_TALK_VENUE}, 2016\t{_TALK_PROSE}"
    gen = _render_talks([entry], supplementary_subpoints=True)
    assert _deleted_texts(gen) == _TALK_PROSE


def test_a_talk_the_section_writes_no_row_for_gets_no_sub_point():
    """An entry no row was written for -- one the section skipped -- has no
    row of its own: its prose goes under no other line of the document,
    though the other talk's row holds every distinctive word of its line."""
    skipped, later = _talk(), _later_talk()
    gen = _generator(supplementary_subpoints=True)
    gen._fill_presentations([later])
    gen._add_supplementary_subpoints({"R": [later, skipped]})

    assert gen._row_receipts.elements_for(skipped) == []
    assert gen._row_receipts.elements_for(later)
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def _render_grouped_talks(entries, **kwargs):
    gen = _generator(**kwargs)
    grouped = gen._group_entries_by_code(entries)
    gen._fill_presentations(grouped.get("R", []))
    gen._add_supplementary_subpoints(grouped)
    return gen, grouped


def _talk_line(text, *talks):
    """One source line stage 4 returned as a record per (title, venue, date),
    the entry's own fields being the last record's. Fan-out gives every
    earlier record a line of its own and the last one the whole line."""
    records = [{"title": title, "location": venue, "date": date} for title, venue, date in talks]
    return {"element_idx_start": 663, "taxonomy_code": "R", "text": text,
            "extracted_fields": {**records[-1], STAGE4_RECORDS_KEY: records}}


_ESTUARY = ("Estuary Sediment Transport in Tidal Marshes", "Kestrel Institute", "2014")
_WETLAND = (_TALK_TITLE, _TALK_VENUE, "2016")
_ESTUARY_LINE = "Estuary Sediment Transport in Tidal Marshes, Kestrel Institute, 2014"
_WETLAND_LINE = f"{_TALK_TITLE}, {_TALK_VENUE}, 2016"
_ESTUARY_PROSE = ("Opened the morning session on sediment budgets and led a discussion with "
                  "three regional water managers")


@pytest.mark.parametrize("segments, offered", [
    # The segments pair with the records: the prose goes under its own talk.
    ([f"{_ESTUARY_LINE}. {_ESTUARY_PROSE}", _WETLAND_LINE],
     [("Estuary Sediment Transport in Tidal Marshes", f"{_ESTUARY_LINE}. {_ESTUARY_PROSE}")]),
    ([_ESTUARY_LINE, f"Invited speaker, {_WETLAND_LINE}. {_TALK_PROSE}"],
     [(_TALK_TITLE, f"Invited speaker, {_WETLAND_LINE}. {_TALK_PROSE}")]),
    # They do not: which talk the prose is about cannot be told, so neither takes it.
    ([_ESTUARY_LINE, _TALK_PROSE, _WETLAND_LINE], []),
])
def test_prose_on_a_line_of_several_talks_goes_under_its_own_or_none(segments, offered):
    """A sub-point is the whole tab segment, as the CV wrote it: one that
    holds the talk's line as well as its prose brings the title, venue and
    year along, and Reject All prints them a second time, under the row that
    holds them (the shape every family has; #1205, 2026-10-09)."""
    entry = _talk_line("\t".join(segments), _ESTUARY, _WETLAND)
    gen, grouped = _render_grouped_talks([entry], supplementary_subpoints=True)

    assert LAST_STAGE4_RECORD in grouped["R"][-1] and len(grouped["R"]) == 2
    assert [(_row_texts_of(row.getprevious())[0],
             "".join(t.text for t in row.iter(qn("w:delText")))) for row in _subpoint_rows(gen)] \
        == offered


def _row_texts_of(tr):
    return ["".join(t.text or "" for t in tc.iter(qn("w:t"))) for tc in tr.findall(qn("w:tc"))]


def test_a_talk_line_opening_with_its_title_cell_is_read_as_its_row():
    """A line that opens with the Title cell's whole text holds the row's own
    text verbatim (`_record_rendered`'s first `RENDER_PIECE_WINDOW`
    characters), and the whole line reads as rendered: none of it is offered,
    its prose included. A line opening with the role is offered whole, as the
    CV wrote it."""
    title_first = f"{_TALK_TITLE}, {_TALK_EVENT}, {_TALK_VENUE}, 2016. {_TALK_PROSE}"
    role_first = f"Invited speaker, {title_first}"
    for line, offered in ((title_first, ""), (role_first, role_first)):
        entry = _talk(prose=None)
        entry["text"] = line
        gen = _render_talks([entry], supplementary_subpoints=True)
        assert _deleted_texts(gen) == offered


def test_generic_list_fan_out_children_of_talks_offer_nothing_their_fields_do_not_hold():
    """Fan-out splits a record list stage 4 filed under a key the schema does
    not define only when the fields hold every word of the text, and each
    child's text is then the line built from its fields: nothing is prose.
    With prose in the text the list is not split, the entry renders its own
    fields, and the list is talks the row does not print (`_lists_talks`)."""
    records = [{"title": _ESTUARY[0], "location": _ESTUARY[1], "date": _ESTUARY[2]},
               {"title": _WETLAND[0], "location": _WETLAND[1], "date": _WETLAND[2]}]
    for text, children in ((f"{_ESTUARY_LINE}\t{_WETLAND_LINE}", 2),
                           (f"{_ESTUARY_LINE}\t{_WETLAND_LINE}\t{_TALK_PROSE}", 1)):
        entry = {"element_idx_start": 664, "taxonomy_code": "R", "text": text,
                 "extracted_fields": {"presentations": copy.deepcopy(records)}}
        gen, grouped = _render_grouped_talks([entry], supplementary_subpoints=True)
        assert len(grouped["R"]) == children
        assert all(gen._row_receipts.elements_for(e) for e in grouped["R"])
        assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


@pytest.mark.parametrize("code, heading", [("N1", "MENTORING PROGRAMS"), ("S0", "BIBLIOGRAPHY")])
def test_a_talk_rerouted_out_of_r_to_an_excluded_code_gets_no_sub_point(code, heading):
    """N1 and S0 are excluded: a talk rendered as either is that code's
    record, so neither the talk row of the same title nor any other carries
    its prose."""
    gen, grouped = _render_grouped_talks([_later_talk(), _rerouted(_talk(), code, heading)],
                                         supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped[code]] == [601]
    assert [e["element_idx_start"] for e in grouped["R"]] == [599]
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


@pytest.mark.parametrize("code, heading", [("C", "POSTDOCTORAL TRAINING"), ("I", "MEMBERSHIPS")])
def test_a_refused_reroute_keeps_the_talk_rule(code, heading):
    """A talk rerouted to C or I keeps its R code: their renderers would drop
    its title (`_fields_lost_in_reroute`). It renders as an R row and its
    prose is a deleted row under that row."""
    gen, grouped = _render_grouped_talks([_rerouted(_talk(), code, heading)],
                                         supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["R"]] == [601]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _TALK_ROW


def _abstract_under_invited_talks():
    """An abstract stage 3b coded S8, under the invited-talks heading: its
    meeting is S8's `conference_name`, which the row reads (#1570)."""
    return _talk(code="S8", event=None, conference_name=_TALK_EVENT, authors="Marblegate Q")


@pytest.mark.parametrize("make", [_abstract_under_invited_talks, lambda: _talk(code="K3")])
def test_a_record_rerouted_into_r_follows_the_talk_rule(make):
    """An abstract coded S8, or a lecture coded K3, under the invited-talks
    heading renders as an R row: its prose is a deleted row under that row."""
    gen, grouped = _render_grouped_talks(
        [_rerouted(make(), "R", "INVITATIONS TO SPEAK/PRESENT")], supplementary_subpoints=True)

    assert [e["element_idx_start"] for e in grouped["R"]] == [601]
    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _TALK_ROW
    assert "".join(t.text for t in row.iter(qn("w:delText"))) == _TALK_PROSE
    (placement,) = gen._subpoint_placements
    assert placement.code == "R"


def test_a_talk_whose_entry_the_pii_pass_withheld_gets_no_sub_point():
    """#892: the PII pass touched the entry. What is left can still describe
    what was withheld, so none of it is offered."""
    withheld = _talk()
    withheld["_pii_withheld"] = True
    gen = _render_talks([_later_talk(), withheld], supplementary_subpoints=True)

    assert any(_row_text(tr) == _TALK_ROW for tr in gen.doc.element.body.iter(qn("w:tr")))
    assert _subpoint_rows(gen) == [] and _deleted_texts(gen) == ""


def test_a_talk_dedup_drops_offers_its_prose_once_under_the_kept_row():
    """Stage 6 dedups before the sections render and hands the sub-point
    pass the entries from before it: the dropped copy wrote no row, so its
    prose goes only under the row of the copy dedup kept, once."""
    gen = _generator(supplementary_subpoints=True)
    grouped = gen._group_entries_by_code([_talk(idx=601), _talk(idx=602)])
    kept, before, _decisions = gen._dedup_grouped_entries(grouped)
    assert len(before["R"]) == 2 and len(kept["R"]) == 1
    gen._fill_presentations(kept["R"])
    gen._add_supplementary_subpoints(before)

    (row,) = _subpoint_rows(gen)
    assert _row_text(row.getprevious()) == _TALK_ROW
    assert _deleted_texts(gen) == _TALK_PROSE
    (placement,) = gen._subpoint_placements
    assert placement.element_idx_start == kept["R"][0]["element_idx_start"]


def test_talk_and_honor_prose_each_stay_under_their_own_section_row():
    """An award for a talk can share its title and venue: each one's prose
    goes under its own section's row, never under the other's."""
    honor_prose = ("Awarded each year to the best lecture given by an early career speaker "
                   "at the regional meeting")
    honor = {"element_idx_start": 39, "taxonomy_code": "H",
             "text": f"2016\tBest Lecture Award for {_TALK_TITLE}, {_TALK_EVENT}\t{honor_prose}",
             "extracted_fields": {"award_name": f"Best Lecture Award for {_TALK_TITLE}",
                                  "granting_body": _TALK_EVENT, "date": "2016"}}
    talk = _talk()
    gen = _generator(supplementary_subpoints=True)
    gen._fill_honors([honor])
    gen._fill_presentations([talk])
    gen._add_supplementary_subpoints({"H": [honor], "R": [talk]})

    by_text = {"".join(t.text for t in row.iter(qn("w:delText"))): row for row in _subpoint_rows(gen)}
    assert set(by_text) == {honor_prose, _TALK_PROSE}
    assert _table_header(by_text[_TALK_PROSE]) == _TALK_HEADER
    assert _table_header(by_text[honor_prose]) != _TALK_HEADER
    assert by_text[_TALK_PROSE].getparent() is not by_text[honor_prose].getparent()


def test_the_doctor_matches_a_talk_sub_point_to_its_own_entry_only(tmp_path, monkeypatch):
    """Through `run_stage6`: the sidecar records the earlier talk's sub-point
    under its own index and R, and the doctor's per-entry match counts it for
    that entry, not for the later talk that shares its words."""
    from unified_pipeline.doctor.shared import (
        docx_subpoint_lines,
        offered_as_subpoint,
        subpoint_placements,
        subpoint_text,
    )

    doc, placements = _stage6_render(tmp_path, monkeypatch, [_later_talk(), _talk()], uid="T1205R")
    assert placements == [[_TALK_PROSE]]
    sidecar = json.loads((tmp_path / "T1205R_render_warnings.json").read_text())
    from unified_pipeline.stage6.supplementary import SUBPOINT_PLACEMENTS_KEY

    assert [(p["element_idx_start"], p["code"]) for p in sidecar[SUBPOINT_PLACEMENTS_KEY]] == [(601, "R")]
    subpoints = subpoint_text(docx_subpoint_lines(doc), subpoint_placements(sidecar))
    assert offered_as_subpoint(_TALK_PROSE, subpoints, element_idx=601)
    assert not offered_as_subpoint(_TALK_PROSE, subpoints, element_idx=599)


@pytest.mark.parametrize("protect, value", [(_naming_the_spouse, _SPOUSE),
                                            (_with_a_home_phone, _HOME_PHONE)],
                         ids=["spouse", "home_phone"])
def test_talk_prose_holding_protected_data_is_not_offered_through_the_pii_pass(
        tmp_path, monkeypatch, protect, value):
    """The PII pass drops a field named for protected data and marks the
    entry withheld; no scope withholds a phone number in the middle of a
    line (#1205 review of O). The same talk without them still has its
    sub-point."""
    _doc, placements = _stage6_render(tmp_path / "plain", monkeypatch, [_talk()])
    assert placements == [[_TALK_PROSE]]

    doc, placements = _stage6_render(tmp_path / "protected", monkeypatch,
                                     [protect(_talk(), _TALK_PROSE)])
    assert placements == []
    assert value not in "".join(t.text or "" for t in doc.element.body.iter(qn("w:t"), qn("w:delText")))


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


def _fieldless_society_entry(code="I"):
    # Stage 4 extracted nothing, so dev's renderer shows the text.
    entry = _society_entry(code=code)
    entry["extracted_fields"] = {}
    return entry


def _all_paragraph_texts(path):
    """Every paragraph's w:t text, table cells and tracked insertions included."""
    body = Document(path).element.body
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p"))]


def test_date_led_sibling_records_render_one_line_each(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "unified_pipeline.stage_6_word_template.call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "TESTUID983", "entries": [_fieldless_society_entry()],
               "cv_owner": {"full_name": "Quenby Marblegate"}}
    input_path = tmp_path / "TESTUID983_fields.json"
    input_path.write_text(json.dumps(payload))

    texts = _all_paragraph_texts(run_stage6(str(input_path), str(tmp_path / "out.docx"),
                                            verbose=False))

    # Each record is its own paragraph, once, where dev rendered the three
    # fused into one line; none is recovered a second time beside it.
    for record in _SOCIETY_RECORDS:
        assert texts.count(record) == 1, record
    assert not [t for t in texts if "Treasurer" in t and "President" in t]


@pytest.mark.parametrize("code", ["I", "O", "P"])
def test_date_led_siblings_render_one_line_each_when_one_records_fields_render(
        tmp_path, monkeypatch, code):
    # The web228/web26 (I) and web181 (O) shape: stage 4 extracted the last
    # record only. On dev the I renderer wrote that record's fields and lost
    # the 2001 membership; O and P wrote the records out of order or fused
    # into one paragraph. Now each source record is its own paragraph, once.
    monkeypatch.setattr(
        "unified_pipeline.stage_6_word_template.call_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))
    payload = {"document_uid": "TESTUID983F", "entries": [_society_entry(code=code)],
               "cv_owner": {"full_name": "Quenby Marblegate"}}
    input_path = tmp_path / "TESTUID983F_fields.json"
    input_path.write_text(json.dumps(payload))

    texts = _all_paragraph_texts(run_stage6(str(input_path), str(tmp_path / "out.docx"),
                                            verbose=False))

    assert [t for t in texts if t in _SOCIETY_RECORDS] == _SOCIETY_RECORDS


@pytest.mark.parametrize("tail", [
    "Liaison, Quimby Farrow, (212) 555-0147",
    "Liaison, Quimby Farrow, qfarrow@example.org",
    "Member No. X1234567",
], ids=["phone", "email", "ID number"])
def test_an_entry_holding_contact_details_or_an_id_keeps_its_tabs(tail):
    # #983 review round 3: the O/P line parser read the last four digits of a
    # phone or an ID as the date column. Such an entry renders as on dev.
    entry = _society_entry()
    entry["text"] = "\t".join(_SOCIETY_RECORDS + [f"2010-2012 {tail}"])
    entry = _grouped_text(entry)
    assert "\n" not in entry["text"]
    assert DATE_LED_SOURCE_TEXT not in entry


def _grouped_text(entry):
    gen = _generator()
    grouped = gen._group_and_cut_entries([entry])
    (only,) = [e for entries in grouped.values() for e in entries]
    return only


def test_grouping_puts_date_led_siblings_on_lines_and_keeps_stage2_text():
    entry = _grouped_text(_fieldless_society_entry())
    assert entry["text"] == "\n".join(_SOCIETY_RECORDS)
    assert entry[DATE_LED_SOURCE_TEXT] == "\t".join(_SOCIETY_RECORDS)


@pytest.mark.parametrize("fields, splits", [
    ({}, True),
    (None, True),
    ({"organization": "", "start_date": None, "offices": []}, True),  # nothing extracted
    # One record's fields: the renderer writes the lines instead, so the
    # sibling records those fields leave out reach the page (#983, after #1859).
    ({"organization": "Guild of Meandering Auditors"}, True),
    ({"end_date": "2009"}, True),
    # Records stage 4 separated: their renderer owns them, not the lines.
    ({"offices": [{"office": "Treasurer"}, {"office": "President"}]}, False),
])
def test_an_entry_is_split_unless_stage4_separated_its_records(fields, splits):
    entry = _society_entry()
    entry["extracted_fields"] = fields
    entry = _grouped_text(entry)
    assert ("\n" in entry["text"]) is splits
    assert (DATE_LED_SOURCE_TEXT in entry) is splits


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C", "M2D"])
def test_a_grant_keeps_its_tabs(code):
    entry = _grouped_text(_fieldless_society_entry(code=code))
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)
    assert DATE_LED_SOURCE_TEXT not in entry


@pytest.mark.parametrize("code", ["K1", "K2", "K4"])
def test_a_teaching_entry_keeps_its_tabs(code):
    # The teaching renderer splits the text on its tabs itself.
    entry = _grouped_text(_fieldless_society_entry(code=code))
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
    entry = _fieldless_society_entry()
    entry[marker] = {"key": "records", "index": 0, "count": 2}
    entry = _grouped_text(entry)
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)
    assert DATE_LED_SOURCE_TEXT not in entry


# The web181 Q1 shape: a long lead-in, then dated offices, most of them
# single years. #1243's tab-part rules read it as one record (fewer than half
# its parts give a date range, and the lead-in's colon makes the single years
# details); its date-led lines would read as two.
_FORUM_PARTS = [
    "Example Rehabilitation Forum. A not-for-profit consortium of academic departments:",
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
        "extracted_fields": {},
    })
    assert "\n" in entry["text"]  # the lines render ...
    # ... but the recovery pass reads records as it did before the split.
    assert recovery_candidates("Q1", entry) == []
    lines_only = {**entry}
    del lines_only[DATE_LED_SOURCE_TEXT]
    assert recovery_candidates("Q1", lines_only) != []


def test_a_split_entry_whose_fields_render_recovers_nothing():
    # The mentee shape of `test_tab_part_the_extracted_fields_hold_is_not_recovered`,
    # with both parts date-led. Grouping splits it, but recovery reads stage
    # 2's text, so it adds nothing beside the mentee table, as on dev.
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
    assert DATE_LED_SOURCE_TEXT in grouped["N3B"][0]
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
    entry = _fieldless_society_entry(code=code)
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
#
# Round 3 adds the render side. For every case, each of stage 2's tab parts
# that reaches the branch's docx reached dev's too, and no part reaches it cut
# across two paragraphs unless dev's cut it too (a trailing ID or phone number
# read as the date column). Every shape here is protected data, contact
# details or an ID, so an entry whose fields render is not split and renders
# exactly as on dev.

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
    # A protected label after a leading date: withheld since #1859, so the
    # entry is not split, whether or not its fields render.
    "spouse after a date": (["2005 Spouse: Jane Quillfeather"], ["Jane Quillfeather"], False),
    "salary after a date": (["2015 Salary: $250,000"], ["$250,000"], False),
    "passport number after a date": (["2015 Passport No. X1234567"], ["X1234567"], False),
}
_PII_CODES = ["T", "A", "I", "D1", "Q1", "O", "P"]
# Codes whose entries get the full Personal Data / Appendix cut; a routed code
# withholds only the shapes in `_PII_ROUTED_SHAPES`.
_PII_FULL_SCOPE_CODES = {"T", "A"}
_PII_ROUTED_SHAPES = {"bare label, value in the next cell", "date of birth, birthplace cell",
                      "in-line SSN", "in-line DOB", "spouse after a date",
                      "salary after a date", "passport number after a date"}
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


def _pii_case_entry(code, lead, shape, *, fields=False):
    entry = _society_entry(code=code) if fields else _fieldless_society_entry(code=code)
    entry["text"] = "\t".join(lead + _PII_SHAPES[shape][0])
    return entry


def _squashed(text):
    return "".join(text.split())


def _parts_on_page(texts, parts):
    """The `parts` whose text is in the docx: in one paragraph, or run on
    across paragraphs (a number cut into two cells still reached the page)."""
    page = _squashed("".join(texts))
    return {part for part in parts if _squashed(part) in page}


def _parts_cut_across_paragraphs(texts, parts):
    """The `parts` on the page that no single paragraph holds whole."""
    return {part for part in _parts_on_page(texts, parts)
            if not any(_squashed(part) in _squashed(text) for text in texts)}


# (whether stage 4 extracted fields, the lead). An entry whose fields render
# is the round-3 case; its undated lead adds nothing the dated one does not.
_PII_VARIANTS = [(False, "dated"), (False, "undated"), (True, "dated")]


@pytest.mark.parametrize("code", _PII_CODES)
@pytest.mark.parametrize("fields,lead", _PII_VARIANTS)
@pytest.mark.parametrize("shape", list(_PII_SHAPES))
def test_the_pii_pass_cuts_what_dev_cuts_with_or_without_the_split(tmp_path, monkeypatch,
                                                                   code, fields, lead, shape):
    _parts, protected, parts_split_alone = _PII_SHAPES[shape]
    entry = _pii_case_entry(code, _PII_DATED_LEAD if lead == "dated" else _PII_UNDATED_LEAD,
                            shape, fields=fields)
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
    # Round 3: the split puts no source line on the page that dev left off it,
    # and cuts none across two paragraphs.
    source_parts = entry["text"].split("\t")
    assert _parts_on_page(texts, source_parts) <= _parts_on_page(dev_texts, source_parts)
    assert (_parts_cut_across_paragraphs(texts, source_parts)
            <= _parts_cut_across_paragraphs(dev_texts, source_parts))
    for value in protected:
        assert (sum(t.count(value) for t in texts)
                == sum(t.count(value) for t in dev_texts)), value
        if withholds and code in _PII_FULL_SCOPE_CODES:
            assert not [t for t in texts if value in t], value
    if fields:
        assert texts == dev_texts  # its fields render: not split, so dev's docx


def test_an_entry_the_pii_pass_cut_is_not_split():
    # Its text and the recovery pass's input stay dev's cut text, tabs and all.
    # Field-less, so only the pass's mark keeps its dated lead unsplit.
    entry = _grouped_text(_pii_case_entry("T", _PII_DATED_LEAD, "children, year-led continuation cells"))
    assert entry["_pii_withheld"] is True
    assert entry["text"] == "\t".join(_PII_DATED_LEAD)
    assert DATE_LED_SOURCE_TEXT not in entry


@pytest.mark.parametrize("fields", [{"date_of_birth": "01/02/1960"}, {"notes": "SSN 123-45-6789"}])
def test_an_entry_the_pii_pass_touched_through_its_fields_is_not_split(fields):
    # No fragment in the text: the pass's own mark, set before the split runs,
    # and the fields it touched each keep the entry as dev renders it.
    entry = _society_entry(code="Q1", organization="Guild of Meandering Auditors", **fields)
    entry = _grouped_text(entry)
    assert entry["_pii_withheld"] is True
    assert entry["text"] == "\t".join(_SOCIETY_RECORDS)


def test_a_routed_entry_holding_protected_data_is_not_split():
    # An I entry gets the routed scope, which does not withhold a children
    # list, so the pass leaves it alone. Field-less, so only the full-scope
    # fragment test keeps its lines as dev renders them.
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
