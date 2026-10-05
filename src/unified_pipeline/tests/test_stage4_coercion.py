"""Regression tests for `unified_pipeline/stage4/coercion.py` (#498 review sweep).

Covers the review comments on the moved-verbatim coercion module, each pinned
to a specific bug fix:

  - #3819054910: FTE regex only captured the digits after the decimal point
    and used them directly as the percent, so ".8 FTE" became "8%" instead of
    "80%" (it only read correctly by coincidence for exactly-two-digit
    fractions like ".08"). Fixed by parsing the whole decimal value and
    multiplying by 100.
  - #3819066916: `coerce_field_value_types` silently returned a non-dict
    input unchanged, violating its declared `Dict[str, Any]` return type.
    Fixed by raising `TypeError`. Also pins the pre-existing (intentional,
    unchanged) empty-list -> "" coercion.
  - #3819068608: `normalize_dates` unconditionally overwrote an
    already-populated canonical start_date/end_date with a value derived
    from a generic "date"/"year"/... field. Fixed with fill-only-if-empty
    precedence.
  - #3819073817: the "LastName, FirstName" surname-parsing regex inside
    `normalize_authors_vancouver` was ASCII-only, so legitimate Unicode
    surnames were mis-parsed. Note: most single-author "Last, First" input
    never reaches that regex at all -- the earlier multi-author splitter
    (`,\\s*(?=[A-Z])`) already breaks on any comma followed by an ASCII
    capital letter, splitting "Smith, John" into two tokens before the
    surname regex ever sees a comma. That splitter's `[A-Z]` is *also*
    ASCII-only, so it does NOT break on a comma followed by an accented
    capital (e.g. "Émile") -- that is the real, reachable path that
    exercises the surname regex, and the one used below.
  - #3819086998: negative-path coverage proving malformed identifiers/dates/
    FTE/author strings are left alone rather than silently misnormalized.
  - #556 review round 4: `ExtractedFields` / `ReformattedField` /
    `ReformattedFields` replaced `dict[str, Any]` across the regex
    post-processing pass. `ExtractedFields`' key set is hand-kept (this
    module may not import stage4.schemas), so it needs the same drift
    guard `DATE_RANGE_TAXONOMY_CODES` has -- see the last two tests.
  - Two-digit-year century: the LLM read "10/08" as 1908. The repair moves a
    19xx year the text holds only as a two-digit token to the century the
    shared pivot (core/two_digit_year.py) gives it -- see the last section.
  - Class 2 of the 2026-10-02 s7ab autopsy: a "1999-02" source range stored
    whole as a year-month date, and an "01/09" MM/YY date read as 2001-09.
    See the two sections after the century repair.
  - #1205 slice (a), EBYSBC class E12: a grant's own goal statement reached
    `notes` for some grants and not for identical siblings. See the last
    section.

Self-contained: no LLM calls, no I/O, no PII.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4.coercion import (  # noqa: E402
    DATE_FIELD_NAMES,
    ExtractedFields,
    ReformattedField,
    ReformattedFields,
    apply_regex_post_processing,
    coerce_field_value_types,
    normalize_authors_vancouver,
    normalize_dates,
)


# --- #3819054910: FTE decimal -> percent conversion ------------------------

@pytest.mark.parametrize(
    "text,expected_percent",
    [
        ("Effort: .08 FTE on this award", "8%"),
        ("Effort: .5 FTE on this award", "50%"),
        ("Effort: .8 FTE on this award", "80%"),
        ("Effort: 1.0 FTE on this award", "100%"),
        ("Effort: 0.1 FTE on this award", "10%"),  # schemas.py's own documented example
    ],
)
def test_fte_decimal_converts_to_correct_percent(text, expected_percent):
    updated, reformatted = apply_regex_post_processing(text, {}, "M2A")
    assert updated["percent_effort"] == expected_percent
    assert reformatted["percent_effort"]["reformatted"] == expected_percent


def test_fte_percent_and_word_forms_still_work():
    # Non-decimal forms were never buggy -- pin them so the split/fix didn't
    # regress the sibling patterns.
    assert apply_regex_post_processing("8% effort on this grant", {}, "M2A")[0]["percent_effort"] == "8%"
    assert apply_regex_post_processing("8 percent effort", {}, "M2A")[0]["percent_effort"] == "8%"


def test_fte_not_applied_outside_grant_taxonomy():
    # Gate is taxonomy_code.startswith('M2') -- an S1 entry with FTE-looking
    # text must not get percent_effort populated.
    updated, _ = apply_regex_post_processing(".8 FTE", {}, "S1")
    assert "percent_effort" not in updated


@pytest.mark.parametrize(
    "text,expected_original",
    [
        ("Award effort: .8 FTE annually", ".8 FTE"),        # decimal-FTE path
        ("Award effort: 25 % effort annually", "25 % effort"),  # percent path
    ],
)
def test_percent_effort_record_reports_the_matched_text(text, expected_original):
    # The reformatted record's `original` is the literal substring the winning
    # pattern matched -- the one value `_find_percent_effort` now returns
    # alongside the percent, and previously read off a match object the caller
    # held itself. Both of the helper's matching return paths are pinned;
    # nothing asserted this field on either shape before.
    _, reformatted = apply_regex_post_processing(text, {}, "M2")
    assert reformatted["percent_effort"]["original"] == expected_original


# --- #3819066916: coerce_field_value_types contract -------------------------

def test_non_dict_input_raises_type_error():
    with pytest.raises(TypeError):
        coerce_field_value_types(None)
    with pytest.raises(TypeError):
        coerce_field_value_types(["not", "a", "dict"])
    with pytest.raises(TypeError):
        coerce_field_value_types("not a dict")


def test_empty_list_coerces_to_empty_string():
    # Pins the existing, intentional behavior: an empty list is a scalar-list
    # (vacuously "all scalars") so it joins to "" like any other scalar list,
    # rather than being left as []. Any future change here is a deliberate,
    # test-visible decision, not an accident.
    assert coerce_field_value_types({"specialty": []})["specialty"] == ""


# --- #3819068608: canonical date fields take precedence --------------------

def test_existing_start_date_not_overwritten_by_raw_date_field():
    out = normalize_dates({"date": "2015-2016", "start_date": "2016"})
    assert out["start_date"] == "2016"  # unchanged, not clobbered with "2015"
    assert out["end_date"] == "2016"    # end_date was empty, so it does get filled
    assert "date" not in out            # raw field is still consumed/removed


def test_existing_end_date_not_overwritten_by_raw_date_field():
    out = normalize_dates({"date": "2009-present", "start_date": "2010", "end_date": "already set"})
    assert out["start_date"] == "2010"
    assert out["end_date"] == "already set"


def test_raw_date_field_fills_in_when_no_canonical_fields_set():
    # No precedence conflict -- both canonical fields are populated as before.
    out = normalize_dates({"date": "2015-2016"})
    assert out["start_date"] == "2015"
    assert out["end_date"] == "2016"


# --- #3819073817: Unicode-aware surname parsing -----------------------------

def test_accented_surname_parsed_correctly():
    # The multi-author splitter's own `[A-Z]` lookahead is ASCII-only, so it
    # does not split on a comma followed by an accented capital -- this is
    # what actually exercises the surname regex being fixed here. Before the
    # fix the ASCII-only surname regex failed to match "García" at all and
    # fell through to the (wrong) "FirstName LastName" branch, silently
    # swapping the names to "Émile G".
    assert normalize_authors_vancouver("García, Émile") == "García É"


def test_compound_surname_with_unicode_first_name_parsed_correctly():
    # Same mechanism as above, with a compound (multi-word) surname. Before
    # the fix this mis-split into "Émile DLC".
    assert normalize_authors_vancouver("de la Cruz, Émile") == "de la Cruz É"


def test_apostrophe_and_hyphen_surnames_still_work():
    # Regression guard: hyphen/apostrophe support (already present in the old
    # ASCII class) must survive the broadened Unicode class.
    assert normalize_authors_vancouver("O'Brien, amy") == "O'Brien A"
    assert normalize_authors_vancouver("Smith-Jones, amy") == "Smith-Jones A"


# --- #3819086998: negative-path coverage ------------------------------------

def test_malformed_pmid_not_extracted():
    # Pattern requires 7-8 digits; 3 digits must not be picked up as a PMID.
    updated, reformatted = apply_regex_post_processing("See PMID: 123 for details", {}, "S1")
    assert "pmid" not in updated
    assert "pmid" not in reformatted


def test_invalid_doi_like_string_not_extracted():
    # Pattern requires >=4 digits after "10."; "10.1/..." must not match.
    updated, reformatted = apply_regex_post_processing("doi: 10.1/x incomplete", {}, "S1")
    assert "doi" not in updated
    assert "doi" not in reformatted


def test_invalid_orcid_not_extracted():
    # Pattern requires the full NNNN-NNNN-NNNN-NNN[X] shape; a truncated
    # ORCID-looking string must not match.
    updated, reformatted = apply_regex_post_processing("ORCID: 0000-0001-123", {}, "A")
    assert "orcid" not in updated
    assert "orcid" not in reformatted


def test_malformed_date_range_left_untouched():
    # No recognized pattern -- the raw field must survive unchanged rather
    # than being partially/incorrectly split.
    out = normalize_dates({"date": "circa 1990s"})
    assert out == {"date": "circa 1990s"}
    assert "start_date" not in out
    assert "end_date" not in out


def test_unparseable_fte_not_extracted():
    # "FTE" present but with no adjacent number at all -- must not produce a
    # bogus percent_effort.
    updated, reformatted = apply_regex_post_processing("full FTE commitment to this grant", {}, "M2A")
    assert "percent_effort" not in updated
    assert "percent_effort" not in reformatted


def test_unsupported_author_format_passes_through_unchanged():
    # A single bare token ("Anonymous", a corporate byline, etc.) has no
    # comma and no multi-word structure to parse -- it must pass through
    # rather than raising or being corrupted.
    assert normalize_authors_vancouver("Anonymous") == "Anonymous"


# --- #556 round 4: drift guards for the hand-kept typed records ------------

def test_extracted_fields_typeddict_matches_active_schemas():
    # ExtractedFields' key set is the union of every field name the active
    # schemas declare, hand-kept because coercion.py may not import
    # stage4.schemas (module boundary, stage4/__init__.py) -- and because a
    # TypedDict's keys must be literals, so no module arrangement could
    # derive them at runtime either. Nothing else notices when a schema
    # gains, loses or renames a field, which would silently leave a
    # post-processor writing into a key the schema no longer declares. This
    # test may import both modules even though coercion.py itself cannot,
    # exactly as test_date_range_taxonomy_codes_matches_schema_derived_codes
    # does for DATE_RANGE_TAXONOMY_CODES.
    from unified_pipeline.stage4 import schemas

    active = schemas.get_active_schemas()
    schema_derived = {
        field
        for schema in active.values()
        for field in schema.get("fields", [])
    }

    assert set(ExtractedFields.__annotations__) == schema_derived


def test_reformatted_fields_keys_are_all_declared_extracted_fields():
    # Every key ReformattedFields reports on is a field some schema declares,
    # so a schema-side rename that the test above catches also tells you which
    # post-processor's key went stale. Kept separate from that assertion so a
    # failure names which of the two invariants broke.
    assert set(ReformattedFields.__annotations__) <= set(ExtractedFields.__annotations__)


def test_reformatted_field_record_shape_is_pinned():
    # The three-key shape is written identically at every site in coercion.py;
    # a fourth key added at one site only would be a silent partial record.
    assert set(ReformattedField.__annotations__) == {"original", "reformatted", "reason"}
    assert ReformattedField.__total__ is True
    assert ReformattedFields.__total__ is False
    assert ExtractedFields.__total__ is False


def test_percent_effort_search_order_is_decimal_fte_before_percent_patterns():
    # `_find_percent_effort`'s docstring states the order as a contract
    # ("the decimal-FTE pattern first, then each of _FTE_PERCENT_PATTERNS;
    # first match wins"), but until this test nothing pinned it: swapping the
    # two search blocks passed the whole suite. Both forms appear in this one
    # text, so only the order decides the answer.
    updated, _ = apply_regex_post_processing(
        "0.8 fte and 50% effort", {}, "M2A"
    )

    assert updated["percent_effort"] == "80%"


def test_percent_effort_already_extracted_is_not_overwritten():
    # `_normalize_grant_effort` promises to fill in only what "the LLM didn't
    # already fill in", and 30 corpus entries reach it with a truthy value --
    # but deleting the early return passed the whole suite. The regex would
    # find "50%" here; the LLM's own "25%" must survive, with no reformatted
    # record claiming a repair that did not happen.
    updated, reformatted = apply_regex_post_processing(
        "50% effort on this grant", {"percent_effort": "25%"}, "M2A"
    )

    assert updated["percent_effort"] == "25%"
    assert "percent_effort" not in reformatted


# --- Two-digit source year read into the wrong century ----------------------

_CENTURY_REASON = "Re-derived the century of a two-digit source year"


@pytest.mark.parametrize(
    "text,fields,expected",
    [
        # m/yy, the bare month-year shape
        ("Invited talk, Example Society 10/08", {"date": "1908-10"}, {"date": "2008-10"}),
        # m/d/yy
        ("Grand rounds, Example Hospital 3/22/05", {"date": "1905-03-22"}, {"date": "2005-03-22"}),
        # m/yy-yyyy: only the two-digit start is wrong; the 4-digit end is in the text
        (
            "Example Committee, member 4/07-2013",
            {"start_date": "1907", "end_date": "2013"},
            {"start_date": "2007", "end_date": "2013"},
        ),
        # m/d/yy-m/d/yy: both ends of the range move
        (
            "Example Award 8/1/08- 7/31/12",
            {"start_date": "1908-08-01", "end_date": "1912-07-31"},
            {"start_date": "2008-08-01", "end_date": "2012-07-31"},
        ),
        # apostrophe forms: straight, left and right curly quotes
        ("Example Prize '04", {"year": "1904"}, {"year": "2004"}),
        ("Example Prize \u201804", {"year": "1904"}, {"year": "2004"}),
        ("Example Prize \u201904", {"year": "1904"}, {"year": "2004"}),
        # an int year keeps its type
        ("Example Prize '04", {"year": 1904}, {"year": 2004}),
        # a 5-digit number in the text is not the 4-digit year written out
        ("Example grant 19031, awarded 5/03", {"date": "1903-05"}, {"date": "2003-05"}),
        # the text writes 20yy in full; the value has it as 19yy (#1248)
        (
            "Example Award, dates 7/1/10 - 6/30/2014",
            {"start_date": "2010-07-01", "end_date": "1914-06-30"},
            {"start_date": "2010-07-01", "end_date": "2014-06-30"},
        ),
        ("Example lecture, April 2006", {"date": "1906-04"}, {"date": "2006-04"}),
        # ... and a bare year, which has no month to re-derive it from
        ("Example committee, 2010 - 2014", {"end_date": "1914"}, {"end_date": "2014"}),
        # a patent's filing date, extracted for M2D since #1205 slice (b)
        ("Example patent, filed 3/09", {"filing_date": "1909-03"}, {"filing_date": "2009-03"}),
    ],
)
def test_two_digit_source_year_moves_to_the_pivot_century(text, fields, expected):
    updated, _ = apply_regex_post_processing(text, fields, "R")

    assert {key: updated[key] for key in expected} == expected


def test_two_digit_century_repair_records_what_it_changed():
    updated, reformatted = apply_regex_post_processing(
        "Invited talk, Example Society 10/08", {"date": "1908-10", "title": "Example"}, "R"
    )

    assert updated == {"date": "2008-10", "title": "Example"}
    assert reformatted == {
        "date": {"original": "1908-10", "reformatted": "2008-10", "reason": _CENTURY_REASON},
    }


@pytest.mark.parametrize(
    "text,fields",
    [
        # a 1960s two-digit date: the pivot reads "65" as 1965, so it stays
        ("Example Society member 6/65", {"date": "1965-06"}),
        # a 20yy year above the pivot is never pulled back to 19yy: a grant
        # ending "8/31/32" really does end in 2032
        ("Example grant 9/1/27-8/31/32", {"start_date": "2027-09-01", "end_date": "2032-08-31"}),
        # the 19xx year is written out in the text, so the text supports it
        ("Example Society, founded 1903; member 5/03", {"date": "1903"}),
        # ... even when the text also writes the 20yy year
        ("Example Society, founded 1903, renamed 2003", {"date": "1903"}),
        # 20yy inside a longer number is not the year written out
        ("Example Society, ref 120031", {"date": "1903"}),
        # no two-digit token at all
        ("Example Society, early member", {"date": "1903"}),
        # the two digits are part of a longer number, not a year token
        ("Example Society, ref 112/03", {"date": "1903"}),
        ("Example Society, ref 5/031", {"date": "1903"}),
        # a 5-digit number in a value is not a year
        ("Example Society 5/03", {"date": "19035"}),
        ("Example Society 5/03", {"date": "21903"}),
        # non-string, non-int values pass through
        ("Example Society 5/03", {"dates_attended": {"start": "1903"}}),
        ("Example Society 5/03", {"year": True}),
    ],
)
def test_two_digit_century_repair_leaves_supported_years_alone(text, fields):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), "R")

    assert updated == fields
    assert reformatted == {}


def test_two_digit_century_repair_runs_before_the_date_range_repair():
    # The range repair restores a dropped end_date only when start_date agrees
    # with the text's closed range. Run after the century repair, it sees the
    # corrected 2003 and fills the end; run before, it sees 1903 and skips.
    updated, _ = apply_regex_post_processing(
        "Example Committee member 9/03, term 2003-2005",
        {"start_date": "1903", "end_date": None},
        "P",
    )

    assert (updated["start_date"], updated["end_date"]) == ("2003", "2005")


def test_date_field_names_match_declared_date_fields():
    # DATE_FIELD_NAMES is hand-kept (coercion.py may not import
    # stage4.schemas); ExtractedFields is itself pinned to the active schemas
    # above, so this pins the date subset to it.
    declared_date_fields = {
        name for name in ExtractedFields.__annotations__ if "date" in name or "year" in name
    }

    assert set(DATE_FIELD_NAMES) == declared_date_fields
    # The century and year-not-in-source repairs record each change under the
    # date field's own key, so every date field needs a ReformattedFields key.
    assert set(DATE_FIELD_NAMES) <= set(ReformattedFields.__annotations__)


# --- YYYY-YY source range stored whole in one field (class 2) ---------------

_SHORT_RANGE_REASON = "Read a YYYY-YY source range written into one date field"


@pytest.mark.parametrize(
    "text,fields,expected",
    [
        ("Example mentee, MS 1999-02", {"start_date": None, "end_date": "1999-02"},
         ("1999", "2002")),
        ("Example mentee, MS 1999-00", {"start_date": "", "end_date": "1999-00"},
         ("1999", "2000")),
        ("Example mentee, MS 2001\u201307", {"start_date": "2001-07", "end_date": None},
         ("2001", "2007")),
        ("Example mentee, MS 2016 - 19", {"end_date": "2016-19"}, ("2016", "2019")),
    ],
)
def test_short_source_range_splits_into_start_and_end(text, fields, expected):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), "N3B")

    assert (updated["start_date"], updated["end_date"]) == expected
    assert reformatted["end_date"]["reason"] == _SHORT_RANGE_REASON


@pytest.mark.parametrize(
    "text,fields,code",
    [
        # the source has only the year; stage 4 added the month itself
        ("Example mentee, MPH 2002", {"end_date": "2002-12"}, "N3B"),
        # the other range field is already set
        ("Example mentee 1999-02", {"start_date": "1998", "end_date": "1999-02"}, "N3B"),
        # a full date in the source, not a range
        ("Example mentee 1999-02-15", {"end_date": "1999-02"}, "N3B"),
        ("Example mentee 1999-02/03", {"end_date": "1999-02"}, "N3B"),
        # read as a range it would end in 2103: a month after all
        ("Example meeting 2005-03", {"end_date": "2005-03"}, "N3B"),
        # a code whose schema has no date range
        ("Example award 1999-02", {"end_date": "1999-02"}, "H"),
    ],
)
def test_short_source_range_split_leaves_other_shapes_alone(text, fields, code):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), code)

    assert {key: updated.get(key) for key in fields} == fields
    assert "start_date" not in reformatted and "end_date" not in reformatted


# --- MM/YY source date read as YY/MM (class 2) ------------------------------

_MONTH_SLASH_YEAR_REASON = "Re-read an MM/YY source date stage 4 read as YY/MM"


def test_month_slash_year_read_backwards_is_re_read():
    updated, reformatted = apply_regex_post_processing(
        "Example mentee, PhD 01/09- 08/14",
        {"start_date": "2001-09", "end_date": "2014-08"}, "N3B")

    assert (updated["start_date"], updated["end_date"]) == ("2009-01", "2014-08")
    assert reformatted == {"start_date": {
        "original": "2001-09", "reformatted": "2009-01", "reason": _MONTH_SLASH_YEAR_REASON}}


@pytest.mark.parametrize(
    "text,fields",
    [
        # read the right way round already
        ("Example mentee 09/16- 2021", {"start_date": "2016-09", "end_date": "2021"}),
        ("Example mentee 01/09- 08/14", {"start_date": "2009-01", "end_date": "2014-08"}),
        # the four-digit year is in the text, so it supports the reading
        ("Example mentee 01/09, joined 2001", {"start_date": "2001-09"}),
        # both orders in the text: ambiguous, left alone
        ("Example mentee 01/09 and 09/01", {"start_date": "2001-09"}),
        # the first number cannot be a month
        ("Example mentee 20/09", {"start_date": "2020-09"}),
        # no slash token for the value at all
        ("Example mentee, joined in December", {"start_date": "2005-12"}),
        # part of a longer date, not an MM/YY token
        ("Example mentee 3/01/09", {"start_date": "2001-09"}),
    ],
)
def test_month_slash_year_reading_left_alone_when_text_does_not_show_it(text, fields):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), "N3B")

    assert {key: updated.get(key) for key in fields} == fields
    assert reformatted == {}


# --- Year the source text does not contain (class E26, #1248) --------------

_YEAR_NOT_IN_SOURCE_REASON = "Re-derived a year the source text does not contain"
_YEAR_NOT_IN_SOURCE_NULLED_REASON = (
    "Cleared a year the source text does not contain and gives no single year for")


@pytest.mark.parametrize(
    "text,fields,expected",
    [
        # m/d/yy: the month and day match, the year does not
        (
            "Example Foundation grant 03/01/17 - 02/28/24",
            {"start_date": "1997-03-01", "end_date": "2024-02-28"},
            {"start_date": "2017-03-01", "end_date": "2024-02-28"},
        ),
        # month name and 4-digit year: another entry's year in the value
        (
            "Example Professor (current) June 2011 -",
            {"start_date": "1968-06", "end_date": "current"},
            {"start_date": "2011-06", "end_date": "current"},
        ),
        # month name, day and year; abbreviated month with a period
        ("Example lecture, November 4, 2015", {"date": "1900-11-04"}, {"date": "2015-11-04"}),
        ("Example lecture, Dec. 2016", {"date": "1995-12"}, {"date": "2016-12"}),
        ("Example lecture, Sept 15 2004", {"date": "1990-09-15"}, {"date": "2004-09-15"}),
        # m/yyyy
        ("Example lecture 3/2011", {"date": "1987-03"}, {"date": "2011-03"}),
        # a source date without a day still gives the year of a dated value
        ("Example lecture, May 2014", {"date": "1990-05-02"}, {"date": "2014-05-02"}),
        # the text's two dates in that month give two years: cleared
        (
            "Example lecture, May 2014 and May 2015",
            {"date": "1990-05"},
            {"date": None},
        ),
        # the day of an "MM-DD" token does not vouch for a year ending in it
        # (class E26, NDMRSO GCFEBE): the text's own date in that month does
        (
            "04-17: Example lecture, April 2019",
            {"start_date": "2017-04"},
            {"start_date": "2019-04"},
        ),
        (
            "4-17: Example lecture, Apr 2019",
            {"start_date": "2017-04"},
            {"start_date": "2019-04"},
        ),
        # a bare year below 1900 the text does not write (class E26, NDMRSO
        # VXSDRD): re-derived from the text's one year, or cleared
        ("7/18 Example keynote address", {"date": "1807"}, {"date": "2018"}),
        ("Example keynote, March 2013", {"date": "1303"}, {"date": "2013"}),
        ("Example keynote 8/2019", {"date": "1808"}, {"date": "2019"}),
        ("7/18 Example keynote", {"date": "1807 "}, {"date": "2018"}),
        ("Example keynote 7/18, March 2013", {"date": "1807"}, {"date": None}),
    ],
)
def test_year_not_in_source_is_re_derived_from_the_text(text, fields, expected):
    updated, _ = apply_regex_post_processing(text, dict(fields), "R")

    assert {key: updated[key] for key in expected} == expected


def test_year_not_in_source_repair_records_what_it_changed():
    _, reformatted = apply_regex_post_processing(
        "Example Foundation grant 03/01/17 - 02/28/24",
        {"start_date": "1997-03-01", "end_date": "2024-02-28"}, "M2A")

    assert reformatted == {"start_date": {
        "original": "1997-03-01", "reformatted": "2017-03-01",
        "reason": _YEAR_NOT_IN_SOURCE_REASON}}


def test_year_not_in_source_clearing_records_an_empty_string():
    _, reformatted = apply_regex_post_processing(
        "Example lecture, May 2014 and May 2015", {"date": "1990-05"}, "R")

    assert reformatted == {"date": {
        "original": "1990-05", "reformatted": "",
        "reason": _YEAR_NOT_IN_SOURCE_NULLED_REASON}}


@pytest.mark.parametrize(
    "text,fields",
    [
        # the year is written out in the text
        ("Example lecture, June 2011; founded 1968", {"start_date": "1968-06"}),
        # its two digits are in the text as a number of their own
        ("Example lecture 6/68, renewed June 2011", {"start_date": "1968-06"}),
        ("Example committee 1967-68, June 2011", {"start_date": "1968-06"}),
        # a bare year carries no month to re-derive it from
        ("Example lecture, June 2011", {"start_date": "1968"}),
        # the text gives no date in the value's month: the year may sit in a
        # neighbouring element, so it is left alone
        ("Example lecture, June 2011", {"date": "1968-08"}),
        ("Example lecture, Symposium for residents", {"date": "2008-08"}),
        # a word that starts like a month name is not one
        ("Example Institute of Marine 2016", {"date": "1995-03"}),
        ("Example Award, Junior 2015", {"date": "1995-06"}),
        # two-digit years outside a short dashed date token still vouch
        ("Example lecture '98, June 2011", {"start_date": "1998-06"}),
        ("Example committee 98-02, June 2011", {"start_date": "1998-06"}),
        ("Example committee 98-02, June 2011", {"start_date": "2002-06"}),
        ("Example committee 2010-12, June 2011", {"start_date": "2012-06"}),
        ("Example grant 9/1/25-8/31/33", {"end_date": "2033-08-31"}),
        ("Example meeting 5/12-14/87", {"start_date": "1987-05-12"}),
        # an "MM-DD" token with no other date in that month: the year may
        # sit in a neighbouring element, so it is left alone
        ("03-22: Example lecture", {"date": "2022-03-22"}),
        ("11-15: Example lecture", {"start_date": "2015-11"}),
        # a dashed token inside a longer dashed run
        ("Example post 3-21-11-24", {"start_date": "2021-03"}),
        # a bare year: plausible, written in the text, or with no source date
        ("Example fellowship 1971-1874, renewed 7/18", {"end_date": "1874"}),
        ("Example lecture 7/18", {"date": "n.d."}),
        ("Example lecture 7/18", {"date": "2017"}),
        ("Example keynote address", {"date": "1807"}),
        ("", {"date": "1807"}),
        # a date in another month is not this date
        ("Example grant 04/01/17", {"start_date": "1997-03-01"}),
        # a date on another day of that month is not this date
        ("Example grant 03/02/17", {"start_date": "1997-03-01"}),
        ("Example lecture, November 5, 2015", {"date": "1900-11-04"}),
        # a date inside a longer slash run, or after another digit, is not one
        ("Example ref 9/03/01/17", {"start_date": "1997-03-01"}),
        ("Example ref 112/1/17", {"start_date": "1997-12-01"}),
        # a matching date with a cut-off year: the value may be its reading
        ("Example grant 05/01/2021-05/01/202", {"end_date": "2026-05-01"}),
        # not a date value
        ("Example lecture, June 2011", {"date": "1968-06-01-02"}),
        ("Example lecture, June 2011", {"year": 1968}),
        ("Example lecture 7/18", {"year": 1807}),
        # no text to check against (an earlier record of a multi-record entry)
        ("", {"start_date": "1968-06"}),
    ],
)
def test_year_not_in_source_repair_leaves_these_alone(text, fields):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), "R")

    assert {key: updated.get(key) for key in fields} == fields
    assert reformatted == {}


# --- M-YY-MM-YY source range read as an M-D-YY date (class E26) -------------

_MONTH_YEAR_DASH_RANGE_REASON = "Re-read an M-YY-MM-YY source range stage 4 read as M-D-YY"


def test_month_year_dash_range_read_as_a_date_is_re_read():
    updated, reformatted = apply_regex_post_processing(
        "Example contract 3-21-11-24 $10,000",
        {"start_date": "2011-03-21", "end_date": "2024"}, "M2B")

    assert (updated["start_date"], updated["end_date"]) == ("2021-03", "2024-11")
    assert reformatted == {
        "start_date": {"original": "2011-03-21", "reformatted": "2021-03",
                       "reason": _MONTH_YEAR_DASH_RANGE_REASON},
        "end_date": {"original": "2024", "reformatted": "2024-11",
                     "reason": _MONTH_YEAR_DASH_RANGE_REASON},
    }


@pytest.mark.parametrize(
    "text,fields,code",
    [
        # the start was read right already
        ("Example contract 3-21-11-24", {"start_date": "2021-03", "end_date": "2024-11"}, "M2B"),
        # the start is neither the M-D-YY misreading nor the range's start
        ("Example contract 3-21-11-24", {"start_date": "2020-01", "end_date": "2024"}, "M2B"),
        # an M-D-YY date, three numbers: not the four-number range
        ("Example contract 3-21-11", {"start_date": "2011-03-21"}, "M2B"),
        # the misread start year is written out in the text
        ("Example contract 3-21-11-24, renewed 2011", {"start_date": "2011-03-21"}, "M2B"),
        # two such ranges: ambiguous
        ("Example 3-21-11-24 and 1-20-3-21", {"start_date": "2011-03-21"}, "M2B"),
        # the third number cannot be a month
        ("Example contract 3-21-14-24", {"start_date": "2014-03-21"}, "M2B"),
        # the range would run backwards
        ("Example contract 3-24-11-21", {"start_date": "2011-03-24"}, "M2B"),
        # part of a longer dash or slash run
        ("Example ref 7-3-21-11-24", {"start_date": "2011-03-21"}, "M2B"),
        # no start date stored at all
        ("Example contract 3-21-11-24", {"start_date": None, "end_date": "2024"}, "M2B"),
        # not a date-range code
        ("Example contract 3-21-11-24", {"date": "2011-03-21"}, "R"),
    ],
)
def test_month_year_dash_range_left_alone_when_text_does_not_show_it(text, fields, code):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), code)

    assert {key: updated.get(key) for key in fields} == fields
    assert reformatted == {}


def test_month_year_dash_range_records_only_the_fields_it_changed():
    _, reformatted = apply_regex_post_processing(
        "Example contract 3-21-11-24",
        {"start_date": "2011-03-21", "end_date": "2024-11"}, "M2B")

    assert reformatted == {"start_date": {
        "original": "2011-03-21", "reformatted": "2021-03",
        "reason": _MONTH_YEAR_DASH_RANGE_REASON}}


# --- #1205 (a): a grant's own goal statement reaches notes ------------------

_GOAL_NOTES_REASON = "Copied the grant's own goal statement into notes"


@pytest.mark.parametrize(
    "text,expected_notes",
    [
        # a label on its own segment
        ("Example Foundation\tWidget Study\tGoal: To test widgets in the field.\tRole: PI",
         "Goal: To test widgets in the field."),
        # a label after the title on the same line
        ("Example Foundation\tWidget Study Goals: To test widgets in the field.",
         "Goals: To test widgets in the field."),
        # a sentence opening after a title with no period between them
        ("Example Foundation\tWidget Study The goal of this fund is to test widgets.",
         "The goal of this fund is to test widgets."),
        # a sentence after a period, with an adjective, past tense
        ("Example Foundation 2001-2004. Widget Study. The main goal of this project was to test widgets.",
         "The main goal of this project was to test widgets."),
        # "This goal of this ..." and odd capitals still open a sentence
        ("Example Foundation\tThis Goal Of The study is to test widgets.",
         "This Goal Of The study is to test widgets."),
        # a label holding a goal sentence starts at the label
        ("Example Foundation\tGoals: The goal of this project is to test widgets.",
         "Goals: The goal of this project is to test widgets."),
        # a mid-sentence goal keeps its whole segment
        ("Example Foundation\tWidget Study\tMy role is analysis, towards the goal of testing widgets.",
         "My role is analysis, towards the goal of testing widgets."),
        # the statement stops at the end of its line
        ("Example Foundation Goal: To test widgets in the field.\nFunded through 2030",
         "Goal: To test widgets in the field."),
        # the statement stops at a role label on its line
        ("Example Foundation\tThe goal of this study is to test widgets. Role: Co-Investigator",
         "The goal of this study is to test widgets."),
    ],
)
def test_grant_goal_statement_fills_empty_notes(text, expected_notes):
    updated, reformatted = apply_regex_post_processing(text, {"title": "Widget Study", "notes": None}, "M2B")

    assert updated["notes"] == expected_notes
    assert reformatted["notes"] == {
        "original": None, "reformatted": expected_notes, "reason": _GOAL_NOTES_REASON}


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_goal_statement_applies_to_every_code_with_notes(code):
    updated, _ = apply_regex_post_processing(
        "Example Foundation\tGoal: To test widgets in the field.", {}, code)

    assert updated["notes"] == "Goal: To test widgets in the field."


@pytest.mark.parametrize("code", ["M2D", "M2", "H", "S1"])
def test_grant_goal_statement_left_alone_for_codes_without_notes(code):
    updated, reformatted = apply_regex_post_processing(
        "Example Foundation\tGoal: To test widgets in the field.", {}, code)

    assert "notes" not in updated
    assert "notes" not in reformatted


@pytest.mark.parametrize(
    "notes,expected",
    [
        ("Extended to 2030", "Extended to 2030. Goal: To test widgets in the field."),
        ("Extended to 2030.", "Extended to 2030. Goal: To test widgets in the field."),
    ],
)
def test_grant_goal_statement_appended_to_other_notes(notes, expected):
    updated, reformatted = apply_regex_post_processing(
        "Example Foundation\tGoal: To test widgets in the field.", {"notes": notes}, "M2B")

    assert updated["notes"] == expected
    assert reformatted["notes"]["original"] == notes


@pytest.mark.parametrize(
    "notes",
    [
        "Goal: To test widgets in the field.",
        # same words, different punctuation, case and spacing
        "Example Foundation;  goal - to test widgets in the field",
    ],
)
def test_grant_goal_statement_already_in_notes_is_not_repeated(notes):
    updated, reformatted = apply_regex_post_processing(
        "Example Foundation\tGoal: To test widgets in the field.", {"notes": notes}, "M2B")

    assert updated["notes"] == notes
    assert "notes" not in reformatted


@pytest.mark.parametrize(
    "text,fields",
    [
        # "major goal(s)" is stage 6's major_goals row
        ("Example Foundation\tThe major goal of this project is to test widgets.", {}),
        ("Example Foundation\tMajor Goals: To test widgets in the field.", {}),
        # two goal segments: which record owns which is unknown
        ("Widget Study\tGoal: To test widgets.\nGadget Study\tGoal: To test gadgets.", {}),
        ("Widget Study Goal: To test widgets.\nGadget Study Goal: To test gadgets.", {}),
        # no goal statement: a title phrase, and a lower-case "the goal of this"
        ("Example Foundation\tImproving Goals of Care Conversations", {}),
        ("Example Foundation\tAimed at the goal of this consortium", {}),
        # a stray label too short to be a statement
        ("Example Foundation\tGoal: TBD", {}),
        # the statement only repeats the title
        ("Example Foundation\tThe goal of this study is to test widgets",
         {"title": "The Goal of this Study is to Test Widgets"}),
        ("", {}),
    ],
)
def test_grant_goal_statement_left_alone_when_absent_or_ambiguous(text, fields):
    updated, reformatted = apply_regex_post_processing(text, dict(fields), "M2B")

    assert updated.get("notes") is None
    assert "notes" not in reformatted
