"""Section H (honors) newline-blind fix (#476, PR1 of the accuracy wave).

`honors.py:122` was one of the ten `entry_lines`-based call sites. Its own
per-part loop already disambiguates a tab ("does the last tab part look like
a year?" -- Award\\tYear splits, Award\\tDate\\tDescription rejoins into one
award) and a pipe ("Award | Year") *within a single already-newline-split
line*; the bug entry_lines caused was narrower than the other four sections':
a fully blind entry (no newline AT ALL) never reached that per-part logic as
more than one opaque blob, so a genuinely fused multi-award entry joined only
by '|' silently kept just its first award and dropped the rest (the OLD
`elif '|' in line:` branch reads only `parts[0]`/`parts[1]`, ignoring
anything past the first pipe).

`_entry_parts` fixes exactly that: when `entry_lines` already returns more
than one part (a genuine newline-separated entry), it is returned unchanged
-- migrating those to `entry_fragments` wholesale was tried and reverted
after reading the actual corpus diff it produced (see the function's own
docstring): 2054_Opresko_Cv's "1994 | American Chemical Society Award,\\n..."
already has 2 lines and collided with the pre-existing (separate) "DATE |
AWARD" mislabeling bug in the loop below, adding a spurious duplicate row;
2068_Yount_Cv's single blind entry mixes a tab AND a '|', and splitting the
'|' first defeats the tab-rejoin disambiguation, again producing a spurious
row ("Research.com" on its own). Both are why the fix is scoped to a
single-line, tab-free entry only -- disclosed here, not silently dropped.
There is no farm H entry of that exact shape (the farm's 9 tab-free blind '|'
entries are all "YEAR | Award, Org", already rendered correctly today via the
extracted fields' own single-award path -- this fix changes nothing for
them, hence CHANGED 0 in the render gate); the positive control below is a
synthetic fixture pinning the behaviour for when the farm does grow one.

The same emptiness of the farm hid a regression in the first cut of this
fix, which is why the last three tests exist: the pipeline's own table-cell
join writes ONE award as "Award | Organization | Year", and splitting every
'|' re-emitted the organization as a second, empty award row that origin/dev
never produced. `_entry_parts` now filters out the parts stage 4 already
extracted as column cells and requires the bare years to pair one-to-one
with the awards, so a column tuple is returned whole. The third test is the
counterweight: the two-award, two-year entry the fix exists for must still
split.

Review round 1 (#733) added a second half to this file: regression cover for
the parsing rules that review changed -- the two- and three-column '|' forms,
trailing year ranges, strong-before-generic organization keywords, the
case-sensitive proper-noun check, the narrower-table fallback, and the parser
and record the rest of it now goes through. Its own header comment says what
it deliberately leaves to the wider behaviour matrix.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_honors_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.honors import (  # noqa: E402
    _FALLBACK_SCHEMA_STAT,
    HonorRecord,
    _entry_parts,
    _extract_organization_from_award,
    _honor_columns,
    _split_award_year,
    parse_honor_entry,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# Genuinely multi-line, farm-derived shapes: entry_lines already returns >1
# part for each of these, so _entry_parts must return them byte-identical.
MULTILINE_CASES = [
    # 2054_Opresko_Cv H entry shape: a pipe INSIDE the first of two newline
    # lines. Splitting further on '|' here is what produced the spurious row
    # -- _entry_parts must not touch it.
    "1994 | American Chemical Society Award,\nLehigh Valley Chapter of ACS",
    "1998-1999 | Mentored Investigator Award,\nThe Four Diamonds Fund of the Milton S. Hershey",
    "Award One\nAward Two",
    "2020 AICT Outstanding Article Award\n2018 Prior Award",
]

# Single-line (blind) shapes that must ALSO pass through untouched: a tab
# anywhere in the line (038WKA's 3-tab-field award, 2082_Dr_Scot's
# mid-sentence wrap artifact, 2068_Yount_Cv's tab+pipe combination).
BLIND_TAB_CASES = [
    "Award Name\tOctober 2024\tSelected from a national pool of applicants.",
    "1997 – National Institute for Staff and Organizational Development (NISOD) Award for\tTeaching Excellence. Valencia Community College, Orlando, Florida.",
    "2022\tWorld's Best Social Scientists, Ranking | Research.com",
]


_TEMPLATE_HEADERS = ["Name of award", "Organization", "Date awarded (yyyy)"]


def _fill_honors_into(entries, cols=3):
    """(rows, generator) after filling a `cols`-column H table."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("H. HONORS AND AWARDS")
    table = gen.doc.add_table(rows=1, cols=cols)
    for i, header in enumerate(_TEMPLATE_HEADERS[:cols]):
        table.rows[0].cells[i].text = header
    gen._fill_honors(entries)
    return [[c.text for c in r.cells] for r in table.rows[1:]], gen


def _render_honors(entries, cols=3):
    rows, _ = _fill_honors_into(entries, cols)
    return rows


def _raw(text):
    """An entry stage 4 extracted nothing from -- the fallback parse path."""
    return {"text": text, "extracted_fields": {}}


# --- (a) no line the old newline split produced is lost ---------------------

def test_multiline_entries_are_returned_unchanged():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1, f"fixture {case!r} is not actually multi-line"
        assert _entry_parts(case) == old, f"diverged on already-multiline {case!r}"


def test_blind_lines_with_a_tab_are_also_returned_whole():
    for case in BLIND_TAB_CASES:
        old = entry_lines(case)
        assert old == [case]
        assert _entry_parts(case) == old, f"tab-bearing blind line split: {case!r}"


# --- (b) positive control: fails on dev today --------------------------------

def test_positive_control_pipe_blind_multi_award_entry_gains_both_rows():
    """Today's `elif '|' in line:` branch reads only the first two pipe
    segments of a single-line entry and silently drops the rest -- this is a
    single line (no '\\n'), so `entry_lines` returns exactly one opaque part
    and the multi-award branch (which needs >1 award_lines) never triggers;
    the entry falls to the single-award branch, which renders the whole raw
    text truncated at 150 chars as one garbled row. Fails on dev.
    """
    entry = {
        "text": "Best Teaching Award | 2020 | Excellence in Mentorship Award | 2018",
        "extracted_fields": {},
    }
    rows = _render_honors([entry])
    assert len(rows) == 2, f"expected 2 awards, got {rows}"
    assert rows[0][0] == "Best Teaching Award"
    assert rows[0][2] == "2020"
    assert rows[1][0] == "Excellence in Mentorship Award"
    assert rows[1][2] == "2018"


# --- (c) negative control: a single-part entry is unchanged ------------------

def test_negative_control_single_award_unchanged():
    entry = {
        "text": "2020 AECT Distinguished Service Award, Purdue University",
        "extracted_fields": {
            "award_name": "AECT Distinguished Service Award",
            "granting_body": "Purdue University",
            "date": "2020",
        },
    }
    rows = _render_honors([entry])
    assert len(rows) == 1
    assert rows[0][0] == "AECT Distinguished Service Award"
    assert rows[0][1] == "Purdue University"
    assert rows[0][2] == "2020"


def test_negative_control_farm_pipe_blind_year_first_shape_unchanged():
    """2054_Opresko_Cv's actual shape: a single-line 'YEAR | Award, Org'
    entry. One bare year and one award-looking part survive `_entry_parts`'
    column filter, which is one award -- below the two a multi-award list
    needs -- so the entry is returned whole and renders from the clean
    extracted fields exactly as it does on dev."""
    entry = {
        "text": "1991 | Freshman Chemistry Achievement Award, CRC Press",
        "extracted_fields": {
            "award_name": "Freshman Chemistry Achievement Award",
            "granting_body": "CRC Press",
            "date": "1991",
        },
    }
    rows = _render_honors([entry])
    assert len(rows) == 1
    assert rows[0][0] == "Freshman Chemistry Achievement Award"
    assert rows[0][1] == "CRC Press"
    assert rows[0][2] == "1991"


# --- regression: the pipeline's own three-column cell join ------------------

# "Award | Organization | Year" is how the pipeline's own table-cell join
# writes ONE award. Splitting it re-emitted the organization as a second,
# empty award row. Both variants below render exactly one row on origin/dev.
COLUMN_JOIN_TEXT = "Best Teaching Award | Purdue University | 2020"


def test_column_join_with_fields_is_not_split_into_two_awards():
    entry = {
        "text": COLUMN_JOIN_TEXT,
        "extracted_fields": {
            "award_name": "Best Teaching Award",
            "granting_body": "Purdue University",
            "date": "2020",
        },
    }
    assert _entry_parts(COLUMN_JOIN_TEXT, ("Purdue University", "2020")) == [
        COLUMN_JOIN_TEXT]
    rows = _render_honors([entry])
    assert rows == [["Best Teaching Award", "Purdue University", "2020"]], rows


def test_column_join_without_fields_is_not_split_into_two_awards():
    """The year-balance half of the guard, with nothing extracted to filter
    with: two award-looking parts and a single bare year is a column tuple,
    not two awards that each carry a year.

    The row it renders is asserted in full since the three-column fix
    (#733 review item 1): before that fix this text reached the table as one
    row whose award cell was the whole raw string, pipes included, with the
    organization column left empty.
    """
    assert _entry_parts(COLUMN_JOIN_TEXT) == [COLUMN_JOIN_TEXT]
    rows = _render_honors([_raw(COLUMN_JOIN_TEXT)])
    assert rows == [["Best Teaching Award", "Purdue University", "2020"]], rows


def test_pipe_blind_multi_award_split_survives_the_guard():
    """The guard must not swallow the case the fix exists for: two awards
    that each carry their own year still split."""
    text = "Best Teaching Award | 2020 | Excellence in Mentorship Award | 2018"
    assert _entry_parts(text, ("", "")) == [
        "Best Teaching Award", "2020",
        "Excellence in Mentorship Award", "2018"]


# =========================================================================
# Review round 1 (#733): regression cover for the parsing rules the review
# changed. The reviewer's wider behaviour matrix -- sorting, verbose mode,
# duplicate awards, production-template integration and the rest -- is a
# separate pass; what follows is only the cover the source items themselves
# asked for.
#
# A note that explains the parser-level tests below: the shared entry filter
# `_is_table_header_entry` (stage6/parsing/text.py) classifies a SHORT
# pipe-joined entry as a source table's header row when half its cells carry
# a header word, and "award" is one of them. "Best Teaching Award | 2020" is
# therefore dropped by the renderer before the parser ever sees it. That is a
# pre-existing filter outside this section, so the two-column shapes it eats
# are pinned on `parse_honor_entry`/`_honor_columns` directly and the
# rendering assertions use shapes the filter passes.
# =========================================================================


# --- item 1: two- and three-column '|' forms --------------------------------

def test_three_column_pipe_form_fills_all_three_cells():
    """`Award | Organization | Year` put the ORGANIZATION in the date column
    and lost the date: the old branch read cell 2 as the year unconditionally.
    """
    rows = _render_honors([_raw("Best Teaching Award | Purdue University | 2020")])
    assert rows == [["Best Teaching Award", "Purdue University", "2020"]]


def test_three_column_pipe_form_accepts_a_month_and_year_date_cell():
    """A date cell is not only a bare year. Reading it as one split this text
    into three awards, because none of its three cells was a bare year."""
    rows = _render_honors(
        [_raw("Best Teaching Award | Purdue University | August 2020")])
    assert rows == [["Best Teaching Award", "Purdue University", "2020"]]


def test_two_column_pipe_form_reads_the_second_cell_as_the_date():
    """`Award | Year`, the two-column form, still means award and date."""
    assert _honor_columns("Best Teaching Award | 2020") == HonorRecord(
        "Best Teaching Award", "", "2020")
    assert parse_honor_entry(_raw("Best Teaching Award | 2020")) == [
        HonorRecord("Best Teaching Award", "", "2020")]


def test_two_column_pipe_form_does_not_call_an_organization_a_date():
    """The other two-column form. `parts[1]` was previously assumed to be a
    year whatever it said, which put the granting body in the date column."""
    assert _honor_columns("Best Teaching Award | Purdue University") == \
        HonorRecord("Best Teaching Award", "Purdue University", "")


def test_pipe_shapes_that_name_no_columns_are_left_alone():
    """Anything outside the two documented forms keeps the historical
    first-cell/second-cell reading rather than being guessed at."""
    assert _honor_columns("2020 | Best Teaching Award") is None      # leading date
    assert _honor_columns("Award A | 2024 | Award B") is None        # odd count
    assert _honor_columns("A | B | C | 2020") is None                # four cells
    assert _honor_columns("Award | 2019 | 2020") is None             # middle date


def test_a_pipe_inside_an_award_name_is_read_as_a_column_boundary():
    """The limitation the module docstring states: '|' is structural, so this
    is one award, an organization and a date -- not one award whose name
    contains a pipe, and not two awards."""
    rows = _render_honors(
        [_raw("Excellence in Research | Teaching Award | 2024")])
    assert rows == [["Excellence in Research", "Teaching Award", "2024"]]


def test_extracted_fields_beat_the_pipe_columns():
    """The column reading is a fallback: whatever stage 4 extracted wins."""
    rows = _render_honors([{
        "text": "Best Teaching Award | Purdue University | 2020",
        "extracted_fields": {"award_name": "Best Teaching Award",
                             "granting_body": "Purdue Research Foundation",
                             "date": "2019"},
    }])
    assert rows == [["Best Teaching Award", "Purdue Research Foundation",
                     "2019"]]


# --- item 3: trailing year ranges -------------------------------------------

def test_trailing_year_range_parses_with_either_dash():
    """The contract named ranges but only the LEADING half accepted one, so
    'Award 2015-2017' kept a dangling '2015 -' in the award name."""
    assert _split_award_year("Distinguished Service Award 2015-2017") == (
        "Distinguished Service Award", "2015-2017")
    assert _split_award_year("Distinguished Service Award 2015\u20132017") == (
        "Distinguished Service Award", "2015\u20132017")
    assert _split_award_year("Distinguished Service Award (2015-2017)") == (
        "Distinguished Service Award", "2015-2017")
    assert _split_award_year("Distinguished Service Award 2015 - present") == (
        "Distinguished Service Award", "2015 - present")


def test_leading_and_trailing_year_forms_agree():
    """Both ends accept the same year grammar, which is what 'consistent'
    means here: same award name out, same year out."""
    for leading, trailing in [
            ("2015-2017 Long Fellowship", "Long Fellowship 2015-2017"),
            ("2015\u20132017 Long Fellowship", "Long Fellowship 2015\u20132017"),
            ("2020 Long Fellowship", "Long Fellowship 2020")]:
        assert _split_award_year(leading) == _split_award_year(trailing), (
            leading, trailing)


def test_trailing_year_range_reaches_the_date_cell():
    rows = _render_honors([_raw("Distinguished Service Award 2015-2017")])
    assert rows == [["Distinguished Service Award", "", "2015-2017"]]


# --- item 4: strong organization keywords before the generic one ------------

def test_generic_org_keyword_inside_an_award_name_does_not_win():
    """'Center' is generic enough to sit inside an award's own name, and
    anchoring on the LAST keyword of one flat list let it beat the body that
    granted the award purely by coming later."""
    assert _extract_organization_from_award(
        "Purdue University Clinical Simulation Center Award") == \
        "Purdue University"
    assert _extract_organization_from_award(
        "Purdue University Teaching Center Award") == "Purdue University"
    assert _extract_organization_from_award(
        "Best Teaching Award, Purdue University, Cancer Center") == \
        "Purdue University"


def test_generic_org_keyword_still_wins_when_nothing_stronger_appears():
    assert _extract_organization_from_award(
        "Innovation Award, Simulation Center") == "Simulation Center"


def test_generic_org_keyword_still_extends_a_name_it_continues():
    """The ambiguity cuts both ways: here 'Center' is the tail of the
    hospital's own name, not part of the award, so the span keeps running to
    it. Award vocabulary in between is what tells the two apart."""
    assert _extract_organization_from_award(
        "Faculty Teaching Award, New York Presbyterian Hospital "
        "Weill Cornell Center") == \
        "New York Presbyterian Hospital Weill Cornell Center"


def test_no_institutional_keyword_yields_no_organization():
    assert _extract_organization_from_award("Nothing institutional here") == ""
    assert _extract_organization_from_award("") == ""


# --- item 7: the proper-noun check is case-sensitive ------------------------

def test_named_body_needs_a_real_proper_noun():
    """[A-Z] is the signal that what follows 'of'/'for' is a proper name.
    Compiling the whole pattern with re.IGNORECASE made it match a lowercase
    letter, so the branch accepted text with no proper name in it at all."""
    assert _extract_organization_from_award(
        "Society of Fictional Medicine Mentoring Award") == \
        "Society of Fictional Medicine"
    assert _extract_organization_from_award(
        "society of fictional medicine Mentoring Award") == ""


def test_named_body_keyword_itself_stays_case_insensitive():
    """Only the proper-name half became case-sensitive: a CV that writes the
    institutional keyword in lower case still matches, as long as the name
    after it is capitalized."""
    assert _extract_organization_from_award(
        "society of Fictional Medicine Mentoring Award") == \
        "society of Fictional Medicine"


# --- item 2: a template variant with fewer columns --------------------------

_ORG_ENTRY = {
    "text": "Best Teaching Award, Purdue University, 2020",
    "extracted_fields": {"award_name": "Best Teaching Award",
                         "granting_body": "Purdue University",
                         "date": "2020"},
}


def test_two_column_table_keeps_the_granting_body():
    """A two-column variant mapped cell 2 to the date and dropped the
    granting body outright, producing a valid-looking DOCX missing it."""
    rows, gen = _fill_honors_into([_ORG_ENTRY], cols=2)
    assert rows == [["Best Teaching Award, Purdue University", "2020"]]
    assert gen.stats[_FALLBACK_SCHEMA_STAT] == 1


def test_one_column_table_keeps_the_granting_body_and_the_date():
    rows, gen = _fill_honors_into([_ORG_ENTRY], cols=1)
    assert rows == [["Best Teaching Award, Purdue University (2020)"]]
    assert gen.stats[_FALLBACK_SCHEMA_STAT] == 1


def test_the_full_template_records_no_schema_fallback():
    rows, gen = _fill_honors_into([_ORG_ENTRY])
    assert rows == [["Best Teaching Award", "Purdue University", "2020"]]
    assert gen.stats.get(_FALLBACK_SCHEMA_STAT, 0) == 0


# --- items 5 and 6: a pure parser over a typed record -----------------------

def test_parse_honor_entry_is_pure_and_typed():
    """The parser needs no document, no generator and no template: it is
    reachable on a plain dict and answers in HonorRecords."""
    records = parse_honor_entry({
        "text": "2020 AECT Distinguished Service Award, Purdue University",
        "extracted_fields": {},
    })
    assert records == [HonorRecord("AECT Distinguished Service Award",
                                   "Purdue University", "2020")]
    assert all(isinstance(r, HonorRecord) for r in records)


def test_malformed_fields_never_reach_a_table_cell():
    """`extracted_fields` is not a contract anything enforces: a key can be
    absent, None or a number. Coercing at the parser boundary is what keeps
    None out of a cell -- and out of _strip_org_tail, which raised on it."""
    records = parse_honor_entry({
        "text": "Best Poster Award, Imaginary Society of Things, 2021.",
        "extracted_fields": {"award_name": None, "granting_body": None,
                             "date": None},
    })
    assert records == [HonorRecord("Best Poster Award",
                                   "Imaginary Society of Things", "2021")]
    # a non-string field is coerced rather than written into the cell as-is
    assert parse_honor_entry({
        "text": "Best Poster Award",
        "extracted_fields": {"award_name": "Best Poster Award", "date": 2021},
    }) == [HonorRecord("Best Poster Award", "", "2021")]


def test_an_entry_with_no_text_renders_nothing_rather_than_a_blank_row():
    """A None text used to raise TypeError slicing it; an empty row is not
    the honest answer either."""
    assert parse_honor_entry({"text": None, "extracted_fields": None}) == [
        HonorRecord("", "", "")]
    assert _render_honors([{"text": None, "extracted_fields": None}]) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
