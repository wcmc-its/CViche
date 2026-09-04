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

Self-contained: no LLM calls, no I/O, no PII.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4.coercion import (  # noqa: E402
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
