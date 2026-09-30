"""Pins #537: a short label-prefixed entry ("Email: x@y.org", "Full Name:
...") must not be reported as definitively unrendered by `_entry_rendered`
(`unified_pipeline/doctor/lints/extraction.py`).

`_entry_rendered` tries verbatim piece containment, then falls back to
distinctive-token overlap. A short entry produces a >=15-char squashed
*piece* (so `_entry_pieces` is non-empty) but its 5+-letter token count sits
below `RENDER_TOKEN_MIN_COUNT`, so the token loop's `continue` never runs for
it. The bug: `verifiable` used to seed from `bool(pieces)`, so a short entry
whose piece isn't found verbatim (expected -- stage 6 drops the label and
renders only the value) fell through to a hard `False` ("not rendered")
instead of `None` ("too short to verify either way"), and
`lint_classified_unrendered` counts a `False` as a miss.

The fix seeds `verifiable = False` instead, matching the sibling
`_record_rendered` in `doctor/lints/render.py`, which never derives
`verifiable` from `_entry_pieces` at all.

Run:
    python3 -m pytest src/unified_pipeline/tests/test_doctor_classified_unrendered_short_entries.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    _classified_entry_rendered,
    _entry_rendered,
    _fields_rendered,
    _stage4_evidence,
    lint_classified_unrendered,
)
from unified_pipeline.doctor.shared import _haystacks  # noqa: E402


# The two entries #537 was filed against, verbatim.
_SHORT_EMAIL = "Email:  mary.mckenna@bcm.edu"
_SHORT_FULL_NAME = "1. Full Name: David Quach"

# A long entry with >=3 five-letter-plus tokens, so the token loop DOES run
# for it regardless of this fix -- it stays a genuine False when missing.
_LONG_ENTRY = ("Distinguished Career Achievement Award, National Foundation "
               "for Advanced Biomedical Research, presented annually to "
               "outstanding investigators in translational medicine")

_UNRELATED_HAYSTACK = _haystacks(
    [("p", "PERSONAL DATA"),
     ("p", "Some completely unrelated paragraph of rendered output text.")])


def test_short_labeled_entry_absent_is_unverifiable_not_unrendered():
    """Positive control: FAILS on unpatched dev, which returns False here."""
    result = _entry_rendered(_SHORT_EMAIL, _UNRELATED_HAYSTACK.text,
                             _UNRELATED_HAYSTACK.tokens)
    assert result is None


def test_short_full_name_entry_absent_is_unverifiable_not_unrendered():
    result = _entry_rendered(_SHORT_FULL_NAME, _UNRELATED_HAYSTACK.text,
                             _UNRELATED_HAYSTACK.tokens)
    assert result is None


def test_long_entry_absent_is_still_definitively_unrendered():
    # The fix must not turn every miss into None -- an entry with enough
    # distinctive tokens to run the overlap check, and that fails it, is
    # still a genuine False.
    result = _entry_rendered(_LONG_ENTRY, _UNRELATED_HAYSTACK.text,
                             _UNRELATED_HAYSTACK.tokens)
    assert result is False


def test_short_entry_present_verbatim_is_still_rendered():
    # Verbatim containment fires before `verifiable` is even seeded -- this
    # fix must not weaken the True path.
    h = _haystacks([("p", "PERSONAL DATA"), ("p", _SHORT_EMAIL)])
    assert _entry_rendered(_SHORT_EMAIL, h.text, h.tokens) is True


def test_lint_classified_unrendered_ignores_short_entries_in_the_evidence():
    # A stage-3b dict with two short labeled entries and one long missing
    # entry, all under one taxonomy code, against an unrelated haystack: the
    # code still WARNs (the long entry is a genuine miss), but the short
    # entries no longer count as verified-missing evidence.
    stage3b = {"entries": [
        {"text": _SHORT_EMAIL, "element_type": "paragraph",
         "taxonomy_code": "A"},
        {"text": _SHORT_FULL_NAME, "element_type": "paragraph",
         "taxonomy_code": "A"},
        {"text": _LONG_ENTRY, "element_type": "paragraph",
         "taxonomy_code": "A"},
    ]}
    blocks = [("p", "PERSONAL DATA"),
             ("p", "Some completely unrelated paragraph of rendered output text.")]
    findings = lint_classified_unrendered(stage3b, blocks)

    assert len(findings) == 1
    finding = findings[0]
    assert "3 classified" in finding["message"]  # len(entries), unchanged
    evidence_text = " ".join(finding["evidence"])
    assert "mckenna" not in evidence_text.lower()
    assert "quach" not in evidence_text.lower()
    assert "Distinguished Career" in evidence_text


# --- #890: stage-4 field values and policy-withheld Personal Data entries ----

_SPACER = ("p", "An unrelated paragraph that shares nothing with the entries.")


def _entry(idx, code, text, end=None):
    return {"element_idx_start": idx, "element_idx_end": end,
            "element_type": "paragraph", "taxonomy_code": code, "text": text}


def _record(idx, code, fields, success=True, end=None):
    return {**_entry(idx, code, "", end), "extracted_fields": fields,
            "extraction_success": success}


# A licence line whose rendered form is a table row: state | number | date. No
# verbatim piece and almost no raw token survives, so the token test alone
# calls it unrendered (the web209 F1 shape).
_LICENCE_TEXT = "Vermont #00000: Expires December 31, 2013 (renewal pending review)"
_LICENCE_FIELDS = {"state_country": "Vermont", "license_number": "#00000",
                   "expiration_date": "2013-12-31"}
_LICENCE_ROW = ("table", "Vermont | #00000 | 12/31/2013\nVermont | #00000 | 12/31/2013")


def _licence_inputs(blocks, fields=None, success=True):
    stage3b = {"entries": [_entry(7, "F1", _LICENCE_TEXT)]}
    stage4 = {"entries": [_record(7, "F1", fields or _LICENCE_FIELDS, success)]}
    return stage3b, blocks, stage4


def test_field_values_on_one_rendered_row_count_as_rendered():
    stage3b, blocks, stage4 = _licence_inputs([_SPACER, _LICENCE_ROW])
    assert lint_classified_unrendered(stage3b, blocks) != [], "control: token test alone fires"
    assert lint_classified_unrendered(stage3b, blocks, stage4) == []


def test_absent_stage4_keeps_the_token_test_verdict():
    stage3b, blocks, _ = _licence_inputs([_SPACER, _LICENCE_ROW])
    assert lint_classified_unrendered(stage3b, blocks, None) == \
        lint_classified_unrendered(stage3b, blocks)


def test_failed_extraction_is_not_field_evidence():
    stage3b, blocks, stage4 = _licence_inputs([_SPACER, _LICENCE_ROW], success=False)
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_one_label_value_is_not_enough_the_1092_shape():
    # A program-name label rendered alone: the detail was lost. One field value
    # found on a line must never clear the entry.
    stage3b = {"entries": [_entry(3, "B2", "Sample Clinical Program: Over 100 procedures performed")]}
    stage4 = {"entries": [_record(3, "B2", {"program_name": "Sample Clinical Program",
                                            "start_date": "2000-09"})]}
    blocks = [_SPACER, ("p", "Sample Clinical Program")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_values_scattered_over_different_lines_are_not_one_record():
    # The name on one line, the number on another: chance, not a rendered record.
    stage3b, _, stage4 = _licence_inputs([])
    blocks = [_SPACER, ("p", "Vermont Health and Science University"),
              ("p", "Reference #00000 in an unrelated grant")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_year_alone_beside_a_short_value_is_not_enough():
    # No value of RENDERED_FIELDS_MIN_VALUE_CHARS+ on the line: chance hits.
    stage3b = {"entries": [_entry(4, "D3", "Ohio 2014 committee member and chair of the panel")]}
    stage4 = {"entries": [_record(4, "D3", {"state": "Ohio", "year": "2014"})]}
    blocks = [_SPACER, ("p", "Ohio | 2014")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_value_shared_by_other_records_is_boilerplate():
    # The same long value on two records is repeated content, not evidence.
    shared = "Weill Cornell Medicine, New York"
    stage3b = {"entries": [_entry(1, "C", "Fellow, alpha unusual text one"),
                           _entry(2, "C", "Resident, beta unusual text two")]}
    stage4 = {"entries": [_record(1, "C", {"institution": shared, "start_date": "2001"}),
                          _record(2, "C", {"institution": shared, "start_date": "2002"})]}
    blocks = [_SPACER, ("p", f"{shared} | 2001")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_withheld_date_of_birth_and_marital_status_are_not_losses():
    stage3b = {"entries": [
        _entry(1, "A", "Marital Status: Married (Spouse Example) Children: (1), Child Example, 01/02/2000"),
        _entry(2, "A", "Date of Birth: January 2, 1970")]}
    assert lint_classified_unrendered(stage3b, [_SPACER]) == []


def test_home_contact_without_a_colon_is_withheld_not_lost():
    stage3b = {"entries": [_entry(
        1, "A", "Jane Example M.D.\t123 Example St Anytown, New York 00000"
                "\tHome Phone (555) 010-0100")]}
    assert lint_classified_unrendered(stage3b, [_SPACER]) == []


def test_an_unrendered_personal_data_entry_without_protected_data_still_fires():
    stage3b = {"entries": [_entry(1, "A", _LONG_ENTRY)]}
    assert len(lint_classified_unrendered(stage3b, [_SPACER])) == 1


def test_policy_withheld_exclusion_is_personal_data_only():
    # The same marital-status text under another code is judged as before.
    text = "Marital Status: Married (Spouse Example) Children: (1), Child Example, 01/02/2000"
    stage3b = {"entries": [_entry(1, "N1", text)]}
    assert len(lint_classified_unrendered(stage3b, [_SPACER])) == 1


def test_dates_shared_by_records_do_not_block_a_rendered_row():
    # Two training records with the same years: dates are not content values,
    # so the year both share neither vouches for nor blocks the row whose
    # title and institution are on it (the web196 C shape).
    stage3b = {"entries": [_entry(1, "C", "1992-1996 Postdoc Res. Assoc., Depts. Alpha, U. of Exampleton"),
                           _entry(2, "C", "1992-1996 Clin. Res. Fellow, Dept. Beta, Example U.")]}
    stage4 = {"entries": [
        _record(1, "C", {"training_type": "Postdoctoral Research Associate",
                         "institution": "Expanded Institute Name", "start_date": "1992", "end_date": "1996"}),
        _record(2, "C", {"training_type": "Clinical Research Fellow",
                         "institution": "Another Abbrev. Inst.", "start_date": "1992", "end_date": "1996"})]}
    blocks = [_SPACER, ("table", "Postdoctoral Research Associate | Expanded Institute Name | 1992-1996")]
    assert lint_classified_unrendered(stage3b, blocks, stage4) == []


def test_authors_and_year_alone_do_not_render_a_citation():
    # Only the author list and the year survive of authors/title/book/year: one
    # of three content values is not a majority.
    stage3b = {"entries": [_entry(6, "S4", "1. Doe J, Roe K, Poe L. Sample title of a chapter. In: Sample Book. 2020.")]}
    stage4 = {"entries": [_record(6, "S4", {
        "authors": "Doe J, Roe K, Poe L", "title": "Sample title of a chapter",
        "book": "Sample Book of Examples", "year": "2020"})]}
    blocks = [_SPACER, ("p", "1. Doe J, Roe K, Poe L. 2020.")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1
    # Control: the same record with the title on the line as well is a majority.
    blocks = [_SPACER, ("p", "1. Doe J, Roe K, Poe L. Sample title of a chapter. 2020.")]
    assert lint_classified_unrendered(stage3b, blocks, stage4) == []


def test_a_date_column_alone_does_not_render_a_degree():
    # Degree and institution lost; the rendered line is only the dates,
    # "1987-07" included.
    stage3b = {"entries": [_entry(8, "B1", "M.D., Example University, July 1987 to July 1994 (Exampletown, Ohio program)")]}
    stage4 = {"entries": [_record(8, "B1", {
        "degree": "M.D.", "institution": "Example University",
        "start_date": "1987-07", "end_date": "1994"})]}
    blocks = [_SPACER, ("p", "07/1987-07/1994")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_record_with_fewer_than_two_content_values_falls_through_to_the_token_test():
    # One content value (the rest are dates): never field evidence.
    stage3b = {"entries": [_entry(9, "B1", _UNSHARED_TEXT)]}
    stage4 = {"entries": [_record(9, "B1", {"degree": "Doctorate of Example",
                                            "start_date": "1987-07", "end_date": "1994"})]}
    blocks = [_SPACER, ("p", "Doctorate of Example 1987-07 1994")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_month_word_dates_are_not_content_values():
    # "July 1987" squashes to "july1987"; the date test must see it unsquashed.
    stage3b = {"entries": [_entry(10, "B1", "M.D., Example University, 1987 until 1994 (Exampletown, Ohio program)")]}
    stage4 = {"entries": [_record(10, "B1", {
        "degree": "M.D.", "institution": "Example University",
        "start_date": "July 1987", "end_date": "July 1994", "issue_date": "June 1990"})]}
    blocks = [_SPACER, ("p", "July 1987 June 1990 July 1994")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_template_scaffolding_is_not_a_content_value():
    # The template's own "Title, include area of training" prompt sits on every
    # rendered training row; it must not count toward the majority.
    stage3b = {"entries": [_entry(11, "C", _UNSHARED_TEXT)]}
    stage4 = {"entries": [_record(11, "C", {
        "training_type": "Title, include area of training",
        "institution": "Distinctive Example Institute",
        "specialty": "Second Distinctive Specialty"})]}
    blocks = [_SPACER, ("table", "Title, include area of training | Distinctive Example Institute")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_short_value_shared_by_records_still_counts():
    # Only values of RENDERED_FIELDS_MIN_VALUE_CHARS+ can be boilerplate; a
    # state both records carry is still one of each record's content values.
    stage3b = {"entries": [_entry(1, "F1", _UNSHARED_TEXT), _entry(2, "F1", _UNSHARED_TEXT + " two")]}
    stage4 = {"entries": [
        _record(1, "F1", {"state_country": "Ohio", "license_number": "#11111"}),
        _record(2, "F1", {"state_country": "Ohio", "license_number": "#22222"})]}
    blocks = [_SPACER, ("table", "Ohio | #11111\nOhio | #22222")]
    assert lint_classified_unrendered(stage3b, blocks, stage4) == []


# A text no rendered line shares a token or a piece with, so only field values
# can vouch for it.
_UNSHARED_TEXT = "Zqxjv wkplm unusual passage nobody rendered anywhere"


def test_a_value_that_is_template_scaffolding_is_not_a_distinctive_value():
    # "Postdoctoral Training" is a template heading. Beside a year on one row
    # it would be two values on a line; the template heading alone is not
    # evidence the entry rendered (#744's twin for fields).
    stage3b = {"entries": [_entry(1, "C", _UNSHARED_TEXT)]}
    stage4 = {"entries": [_record(1, "C", {"program": "Postdoctoral Training",
                                           "start_date": "2014"})]}
    blocks = [_SPACER, ("table", "Postdoctoral Training | 2014")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_short_value_matches_on_a_word_boundary_not_inside_another_word():
    # "MA" is inside "pharmacology": one real value on the row, not two.
    stage3b = {"entries": [_entry(1, "D3", _UNSHARED_TEXT)]}
    stage4 = {"entries": [_record(1, "D3", {"title": "Distinctive Example Title",
                                            "state": "MA"})]}
    inside = [_SPACER, ("table", "Distinctive Example Title | pharmacology")]
    assert len(lint_classified_unrendered(stage3b, inside, stage4)) == 1
    beside = [_SPACER, ("table", "Distinctive Example Title | MA")]
    assert lint_classified_unrendered(stage3b, beside, stage4) == []


def test_records_sharing_a_start_index_pair_by_their_end_index():
    # Both records start at 5; the entry spans 5-9. The record that rendered
    # is 5-6's, listed last: pairing by start alone would pick it.
    stage3b = {"entries": [_entry(5, "F1", _UNSHARED_TEXT, end=9)]}
    stage4 = {"entries": [
        _record(5, "F1", {"state_country": "Ohio", "license_number": "#99999"}, end=9),
        _record(5, "F1", _LICENCE_FIELDS, end=6)]}
    assert len(lint_classified_unrendered(stage3b, [_SPACER, _LICENCE_ROW], stage4)) == 1
    # And the other way round: the entry's own record rendered, a sibling
    # sharing the start index (listed last) did not.
    stage4 = {"entries": [
        _record(5, "F1", _LICENCE_FIELDS, end=9),
        _record(5, "F1", {"state_country": "Ohio", "license_number": "#99999"}, end=6)]}
    assert lint_classified_unrendered(stage3b, [_SPACER, _LICENCE_ROW], stage4) == []


def test_two_records_on_one_span_are_ambiguous_and_give_no_evidence():
    stage3b = {"entries": [_entry(5, "F1", _UNSHARED_TEXT, end=9)]}
    stage4 = {"entries": [_record(5, "F1", {"note": "something else entirely"}, end=9),
                          _record(5, "F1", _LICENCE_FIELDS, end=9)]}
    assert len(lint_classified_unrendered(stage3b, [_SPACER, _LICENCE_ROW], stage4)) == 1


def test_boilerplate_is_counted_over_every_record_including_ambiguous_ones():
    # The institution sits on two records that share a span (so neither pairs
    # with an entry) and on the entry's own record: shared by three, boilerplate.
    inst = "Weill Cornell Medicine, New York"
    stage3b = {"entries": [_entry(1, "C", _UNSHARED_TEXT, end=2)]}
    stage4 = {"entries": [
        _record(1, "C", {"title": "Distinctive Example Title", "institution": inst}, end=2),
        _record(5, "C", {"institution": inst}, end=9),
        _record(5, "C", {"institution": inst, "note": "other"}, end=9)]}
    blocks = [_SPACER, ("table", f"Distinctive Example Title | {inst}")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_withheld_entry_is_not_judged_even_when_its_field_values_rendered():
    # Withheld -> None (never judged) must come BEFORE the field check: the
    # entry may not vouch for its code by a True verdict either.
    text = "Marital Status: Married (Spouse Example)"
    entry = _entry(1, "A", text)
    stage4 = _stage4_evidence({"entries": [_record(
        1, "A", {"label": "Marital Status", "value": "Married Spouse Example"})]})
    h = _haystacks([("table", "Marital Status | Married Spouse Example")])
    assert _fields_rendered(entry, h.text.split("\x00"), stage4) is True, "control"
    assert _classified_entry_rendered(entry, h, h.text.split("\x00"),
                                      frozenset(), stage4) is None


# --- #890 residual: stage-5b institution names and unwritten category fields --

_TRAINING_TEXT = "1992-1996 Postdoc Res. Assoc., Depts. Alpha, U. of Exampleton & Sample U."
_TRAINING_FIELDS = {"training_type": "Postdoctoral Research Associate",
                    "institution": "Depts. Alpha, U. of Exampleton & Sample U.",
                    "start_date": "1992", "end_date": "1996"}
_TRAINING_ROW = ("table", "Postdoctoral Research Associate | Departments of Alpha, "
                          "University of Exampleton and Sample University | 1992-1996")
_EXPANDED = {"cleaned_name": "Departments of Alpha, University of Exampleton and "
                             "Sample University", "official_name": "Ignored Official Name"}


def _training_inputs(blocks, enrichment):
    stage3b = {"entries": [_entry(3, "C", _TRAINING_TEXT)]}
    stage4 = {"entries": [_record(3, "C", _TRAINING_FIELDS)]}
    stage5b = {"entries": [{**_record(3, "C", _TRAINING_FIELDS),
                            "institution_enrichment": enrichment}]}
    return stage3b, blocks, stage4, stage5b


def test_the_stage_5b_institution_name_stage_6_wrote_counts_as_the_institution():
    stage3b, blocks, stage4, stage5b = _training_inputs([_SPACER, _TRAINING_ROW], _EXPANDED)
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1, "control"
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, None)) == 1, "control"
    assert lint_classified_unrendered(stage3b, blocks, stage4, stage5b) == []


def test_official_name_stands_in_when_stage_5b_returns_no_cleaned_name():
    official = {"cleaned_name": "", "official_name": _EXPANDED["cleaned_name"]}
    stage3b, blocks, stage4, stage5b = _training_inputs([_SPACER, _TRAINING_ROW], official)
    assert lint_classified_unrendered(stage3b, blocks, stage4, stage5b) == []


def test_the_stage_5b_name_must_share_a_line_with_the_other_values():
    # The expanded name on its own line, the title on another: chance, not the row.
    stage3b, _, stage4, stage5b = _training_inputs([], _EXPANDED)
    blocks = [_SPACER, ("p", "Postdoctoral Research Associate"),
              ("p", _EXPANDED["cleaned_name"])]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1


def test_a_stage_5b_name_that_is_boilerplate_shared_by_records_is_not_evidence():
    # The expanded name is the institution of two other records too: repeated content.
    stage3b, blocks, stage4, stage5b = _training_inputs([_SPACER, _TRAINING_ROW], _EXPANDED)
    name = _EXPANDED["cleaned_name"]
    stage4["entries"] += [_record(8, "C", {"institution": name}),
                          _record(9, "C", {"institution": name})]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1


def test_stage_5b_records_sharing_a_span_give_no_institution_name():
    stage3b, blocks, stage4, stage5b = _training_inputs([_SPACER, _TRAINING_ROW], _EXPANDED)
    stage5b["entries"].append(dict(stage5b["entries"][0]))
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1


def test_stage_5b_alone_is_not_evidence_without_the_title_on_the_row():
    # Only the institution was written; the training title was lost. One of two
    # content values is not a majority however the institution is spelled.
    stage3b, _, stage4, stage5b = _training_inputs([], _EXPANDED)
    blocks = [_SPACER, ("table", "Departments of Alpha, University of Exampleton "
                                 "and Sample University | 1992-1996")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1


_MEDIA_TEXT = "January 12, 2015. Example Channel, Sample Researchers Study Effects of Practice"
_MEDIA_FIELDS = {"authors": None, "year": "2015-01-12", "media_type": "News Broadcast/Online News",
                 "title": "Sample Researchers Study Effects of Practice",
                 "venue": "Example Channel", "url": "https://example.org/a?fbclid=x"}
_MEDIA_LINE = ("p", "1. Sample researchers study effects of practice. Example Channel. 2015 January 12.")


def test_an_unwritten_category_field_does_not_outvote_the_values_that_rendered():
    stage3b = {"entries": [_entry(2, "S9", _MEDIA_TEXT)]}
    stage4 = {"entries": [_record(2, "S9", _MEDIA_FIELDS)]}
    assert lint_classified_unrendered(stage3b, [_SPACER, _MEDIA_LINE], stage4) == []


def test_the_category_label_alone_is_not_the_entry():
    # The category rendered nowhere and only the title did: one content value
    # is not enough, so this falls to the token test and still fires.
    stage3b = {"entries": [_entry(2, "S9", _UNSHARED_TEXT)]}
    fields = {**_MEDIA_FIELDS, "venue": None, "url": None}
    stage4 = {"entries": [_record(2, "S9", fields)]}
    blocks = [_SPACER, ("p", "Sample researchers study effects of practice.")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_a_category_label_on_the_line_is_not_counted_either():
    # Excluded both ways: the label beside one real value must not build a
    # majority (title unrendered; venue and the label on the line: two of the
    # three values if the label counted, one of two if it does not).
    stage3b = {"entries": [_entry(2, "S9", _UNSHARED_TEXT)]}
    stage4 = {"entries": [_record(2, "S9", {**_MEDIA_FIELDS, "url": None})]}
    blocks = [_SPACER, ("p", "Example Channel. News Broadcast/Online News.")]
    assert len(lint_classified_unrendered(stage3b, blocks, stage4)) == 1


def test_category_fields_are_written_by_no_section():
    from unified_pipeline.doctor.lints.extraction import UNWRITTEN_CATEGORY_FIELDS
    from unified_pipeline.stage6.fan_out import _RENDERED_FIELDS
    written = set().union(*_RENDERED_FIELDS.values())
    assert UNWRITTEN_CATEGORY_FIELDS
    assert not UNWRITTEN_CATEGORY_FIELDS & written


def test_a_stage_5b_record_without_a_name_gives_no_institution_name():
    # An empty name squashes to "", which a short-value boundary search would
    # find on every line: it must never become an alias.
    for enrichment in (None, {}, {"cleaned_name": "", "official_name": ""}):
        stage3b, blocks, stage4, stage5b = _training_inputs(
            [_SPACER, ("table", "Postdoctoral Research Associate |  | 1992-1996")], enrichment)
        assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1


def test_a_stage_5b_record_without_extracted_fields_is_skipped_not_fatal():
    stage3b, blocks, stage4, stage5b = _training_inputs([_SPACER, _TRAINING_ROW], _EXPANDED)
    stage5b["entries"][0]["extracted_fields"] = None
    assert len(lint_classified_unrendered(stage3b, blocks, stage4, stage5b)) == 1
