"""Review-round-1 contract tests for `unified_pipeline/doctor/lints/extraction.py`
(PR #723, review threads 3916403496 and 3916473829).

T1 item 1 ("`lint_dedup_drops` returns strings, not findings"): the return
is one `_finding(...)` call with `suspect[:6]` as its evidence argument, so it
has always returned a one-element `list[dict]` (blamed to 5eb47a4, 2026-07-07,
before the #493 module split); `test_lint_dedup_drops_returns_structured_findings`
below pins the shape so a future edit cannot change it silently.

T1 item 2 / T2 item 1 (`_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES` is a second,
hand-maintained routing source of truth): accepted as a real but pre-existing
and already-guarded design tradeoff (extraction.py's own comment discloses
it). Neither existing test in test_taxonomy_code_render_coverage.py iterates
the set's members programmatically against their render hooks --
`test_every_render_exception_code_has_a_documented_render_hook` below does.

Run:
    python3 -m pytest src/unified_pipeline/tests/test_doctor_extraction_lint_contracts.py -q -p no:cacheprovider
"""
import itertools
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.template_boilerplate import (  # noqa: E402
    is_near_template_instruction,
    is_template_instruction,
)
from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    DEDUP_SAFE_CONTAINMENT,
    INVENTED_RECORD_LICENSURE_CODE,
    INVENTED_RECORD_MIN_VALUES,
    UNDER_EXTRACTION_MAX_PCT,
    UNDER_EXTRACTION_MIN_CHARS,
    UNDER_EXTRACTION_MIN_RECORDS,
    _alphanumeric_tokens,
    _entry_rendered,
    _entry_status,
    _FUNDING_SECTIONS,
    _is_invented_record,
    _MIN_EXACT_LEN,
    _nonempty_field_values,
    _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES,
    _rendered_row_value_sets,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dedup_drops,
    lint_invented_records,
    lint_under_extraction,
)
from unified_pipeline.doctor.shared import _haystacks  # noqa: E402
from unified_pipeline.segmentation_regression import _norm  # noqa: E402


# ==========================================================================
# D1 (T1.1) -- lint_dedup_drops returns structured findings, not strings.

def test_lint_dedup_drops_returns_structured_findings():
    poorly_covered = {"dedup_decisions": [
        {"code": "N1", "metric": "jaccard",
         "dropped_text": "alpha beta gamma delta", "kept_text": "alpha"},
    ]}
    result = lint_dedup_drops(poorly_covered)
    assert len(result) == 1
    finding = result[0]
    assert isinstance(finding, dict)
    # Shape per doctor/shared.py's _finding().
    assert set(finding.keys()) == {"lint", "severity", "message", "evidence"}
    assert finding["lint"] == "dedup_drops"
    assert finding["severity"] == "WARN"
    assert isinstance(finding["message"], str)
    assert isinstance(finding["evidence"], list)
    assert all(isinstance(item, str) for item in finding["evidence"])
    assert len(finding["evidence"]) <= 6

    # No suspects at all -> [].
    assert lint_dedup_drops({"dedup_decisions": []}) == []

    # A drop fully covered by the kept entry is not a suspect -> [].
    well_covered = {"dedup_decisions": [
        {"code": "N1", "metric": "jaccard",
         "dropped_text": "alpha beta", "kept_text": "alpha beta"},
    ]}
    assert lint_dedup_drops(well_covered) == []

    # 7 suspects: evidence caps at 6, the message still counts all 7.
    seven = {"dedup_decisions": [
        {"code": f"N{i}", "metric": "jaccard",
         "dropped_text": f"alpha{i} beta{i} gamma{i} delta{i}",
         "kept_text": f"alpha{i}"}
        for i in range(7)
    ]}
    result7 = lint_dedup_drops(seven)
    assert len(result7) == 1
    assert len(result7[0]["evidence"]) == 6
    assert "7 dedup drop(s)" in result7[0]["message"]


# ==========================================================================
# D2 (T1.2 / T2.1) -- the render-exception set is a second source of truth
# by design (extraction.py's own comment discloses it); pin every member
# against the render hook it depends on, the way the two existing tests in
# test_taxonomy_code_render_coverage.py pin the set as a whole but do not
# iterate its members individually against generate()'s source.

_EXCEPTION_HOOKS = {
    "E": "self._fill_passthrough_sections(",
    "G": "self._fill_passthrough_sections(",
    "J": "self._fill_passthrough_sections(",
    "N4": "self._fill_mentoring(",
}


def test_every_render_exception_code_has_a_documented_render_hook():
    assert set(_EXCEPTION_HOOKS) == _RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES, (
        "a code was added to or removed from "
        "_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES without updating this "
        "test's hook map -- add its render hook here")
    src = (_SRC / "unified_pipeline" / "stage_6_word_template.py").read_text()
    for code, hook in _EXCEPTION_HOOKS.items():
        assert hook in src, (
            f"{code}'s render hook {hook!r} is gone from generate() -- if "
            f"{code} no longer renders, remove it from "
            "_RENDERED_BUT_NOT_IN_RENDER_ROUTED_CODES so "
            "lint_taxonomy_code_coverage catches the gap")


# ==========================================================================
# D3 (T2.2) / D6 (T2.6, bucket_status half) -- lint_bucket_status verdicts.

_FUNDING_TITLES = dict(_FUNDING_SECTIONS)


def _blocks_under(code, text):
    """One rendered funding subsection: its real header title from
    _FUNDING_SECTIONS, followed by one content paragraph."""
    return [("p", _FUNDING_TITLES[code].title()), ("p", text)]


def test_lint_bucket_status_skips_when_unlocatable_under_any_bucket():
    # Status implies a rebucket (M2A -> M2B), but the entry text is too
    # short to verify under any bucket (all three verdicts None) -> no
    # finding, output is empty so every bucket's haystack is empty too.
    stage4 = {"entries": [
        {"taxonomy_code": "M2A", "element_idx_start": 1,
         "text": "Pending review.",
         "extracted_fields": {"status": "Completed"}},
    ]}
    assert lint_bucket_status(stage4, []) == []


_LOCATABLE_GRANT_TEXT = ("Alpha Grant Foundation Study of Longevity "
                          "Research Program")


def test_lint_bucket_status_warns_when_rendered_under_the_wrong_bucket():
    # Same entry, now locatable: status implies M2B but it actually
    # rendered under M2A -> one WARN naming M2A.
    stage4 = {"entries": [
        {"taxonomy_code": "M2A", "element_idx_start": 5,
         "text": _LOCATABLE_GRANT_TEXT,
         "extracted_fields": {"status": "Completed"}},
    ]}
    findings = lint_bucket_status(stage4, _blocks_under("M2A", _LOCATABLE_GRANT_TEXT))
    assert len(findings) == 1
    message = findings[0]["message"]
    assert "M2B" in message and "M2A" in message
    assert "current research funding" in message


def test_lint_bucket_status_silent_when_rebucketed_correctly():
    # Rendered under the bucket the status implies (M2B) -> stage 6 already
    # rebucketed it correctly, no finding.
    stage4 = {"entries": [
        {"taxonomy_code": "M2A", "element_idx_start": 5,
         "text": _LOCATABLE_GRANT_TEXT,
         "extracted_fields": {"status": "Completed"}},
    ]}
    findings = lint_bucket_status(stage4, _blocks_under("M2B", _LOCATABLE_GRANT_TEXT))
    assert findings == []


def test_lint_bucket_status_warns_when_target_unlocatable_but_another_bucket_hits():
    # D6 mixed verdict case: the target bucket (M2B) is unlocatable (None,
    # not False), but the entry IS locatable under a different bucket
    # (M2A) -- must still warn, and must name the bucket it actually found
    # it under, not the unlocatable target.
    text = "Award 12345 total 67890 sum"  # only 2 long-word tokens: unverifiable via the token path
    stage4 = {"entries": [
        {"taxonomy_code": "M2C", "element_idx_start": 9, "text": text,
         "extracted_fields": {"status": "Completed"}},
    ]}
    findings = lint_bucket_status(stage4, _blocks_under("M2A", text))
    assert len(findings) == 1
    assert "M2A" in findings[0]["message"]


# ==========================================================================
# D4 (T2.3) -- _entry_status: what the status label regex actually captures.

@pytest.mark.parametrize("entry,expected", [
    ({"text": "Status: Funded"}, "Funded"),
    ({"text": "status - Not funded"}, "Not funded"),
    # The capture runs to the next '|' or newline, not to the first hyphen.
    ({"text": "Status: Funded - 2020"}, "Funded - 2020"),
    ({"text": "Status: Funded | PI: Smith"}, "Funded"),
    ({"text": "Some header line\nStatus: Pending review\nmore text"},
     "Pending review"),
    # extracted_fields.status wins over any text label.
    ({"text": "Status: Funded", "extracted_fields": {"status": "Awarded"}},
     "Awarded"),
    ({"text": "No label here at all"}, None),
])
def test_entry_status_label_extraction(entry, expected):
    assert _entry_status(entry) == expected


# ==========================================================================
# D5 (T2.5) -- adversarial _entry_rendered cases.

def test_entry_rendered_whitespace_and_punctuation_differences_still_match():
    entry_text = ("A Longitudinal Study of Diabetes Outcomes in Rural "
                  "Populations")
    rendered_blocks = [
        ("p", "A   Longitudinal Study of Diabetes Outcomes, in Rural "
              "Populations!!!"),
    ]
    h = _haystacks(rendered_blocks)
    assert _entry_rendered(entry_text, h.text, h.tokens) is True


def test_entry_rendered_low_overlap_long_entry_is_definitively_false():
    entry_text = ("Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India "
                  "Juliet")
    unrelated_blocks = [
        ("p", "Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra "
              "Tango"),
    ]
    h = _haystacks(unrelated_blocks)
    assert _entry_rendered(entry_text, h.text, h.tokens) is False


def test_entry_rendered_single_long_word_is_unverifiable():
    entry_text = "Pneumonoultramicroscopicsilicovolcanoconiosis"
    h = _haystacks([("p", "unrelated text here")])
    assert _entry_rendered(entry_text, h.text, h.tokens) is None


@pytest.mark.xfail(strict=True, reason=(
    "Known false positive (D5a): a short entry whose only piece is generic "
    "institutional/section boilerplate ('Department of Medicine', 21 "
    "squashed chars, only 2 long-word tokens) matches verbatim against an "
    "UNRELATED haystack that happens to mention the same institution for a "
    "different entry, so _entry_rendered reports True for text that never "
    "actually rendered from THIS entry. The obvious fix -- require a piece "
    "to carry >= RENDER_TOKEN_MIN_COUNT long-word tokens before containment "
    "counts, in _entry_pieces -- directly reverses the #537 fix pinned by "
    "test_short_entry_present_verbatim_is_still_rendered in "
    "test_doctor_classified_unrendered_short_entries.py: that entry's only "
    "piece ('Email: mary.mckenna@bcm.edu') has exactly 2 long-word tokens "
    "('email', 'mckenna') and is recognized as rendered ONLY via "
    "containment, never via the token-overlap fallback. Telling apart "
    "short-generic-boilerplate from short-label-value text needs a "
    "different signal than a token-count floor; filed as #744, which names "
    "this test as its acceptance criterion."
))
def test_entry_rendered_false_positive_on_shared_institutional_boilerplate():
    entry_text = "Department of Medicine"
    unrelated_blocks = [
        ("p", "Jane Smith, MD is affiliated with the Department of "
              "Medicine at a different institution entirely, working on "
              "unrelated topics."),
    ]
    h = _haystacks(unrelated_blocks)
    assert _entry_rendered(entry_text, h.text, h.tokens) is False


# ==========================================================================
# D6 (T2.6) -- None/False/True semantics at lint_classified_unrendered.

_UNVERIFIABLE_TEXT = "Pending review."
_UNRENDERED_TEXT = ("Distinguished Career Achievement Award for Excellence "
                    "in Translational Science")
_RENDERED_TEXT = ("Groundbreaking Multicenter Trial of Novel Cardiac "
                  "Regeneration Therapy")
_OUTPUT_BLOCKS = [("p", "SOME HEADER"), ("p", _RENDERED_TEXT)]


def _entries(*texts, code="X"):
    return {"entries": [
        {"text": t, "taxonomy_code": code, "element_type": "paragraph"}
        for t in texts
    ]}


def test_lint_classified_unrendered_silent_when_all_entries_unverifiable():
    findings = lint_classified_unrendered(
        _entries(_UNVERIFIABLE_TEXT), _OUTPUT_BLOCKS)
    assert findings == []


def test_lint_classified_unrendered_warns_on_false_plus_none():
    findings = lint_classified_unrendered(
        _entries(_UNVERIFIABLE_TEXT, _UNRENDERED_TEXT), _OUTPUT_BLOCKS)
    assert len(findings) == 1
    assert "2 classified" in findings[0]["message"]


def test_lint_classified_unrendered_silent_when_any_entry_verified_rendered():
    findings = lint_classified_unrendered(
        _entries(_RENDERED_TEXT, _UNRENDERED_TEXT), _OUTPUT_BLOCKS)
    assert findings == []


# ==========================================================================
# D7 (T2.4) -- exact thresholds.

def test_dedup_safe_containment_exact_boundary():
    assert DEDUP_SAFE_CONTAINMENT == 0.9
    dropped = " ".join(f"tok{i}" for i in range(10))
    kept_90pct = " ".join(f"tok{i}" for i in range(9))   # 9/10 = 0.9
    kept_80pct = " ".join(f"tok{i}" for i in range(8))   # 8/10 = 0.8
    report_90 = {"dedup_decisions": [
        {"code": "A", "metric": "j", "dropped_text": dropped,
         "kept_text": kept_90pct}]}
    report_80 = {"dedup_decisions": [
        {"code": "A", "metric": "j", "dropped_text": dropped,
         "kept_text": kept_80pct}]}
    assert lint_dedup_drops(report_90) == []          # exactly at the safe line
    assert len(lint_dedup_drops(report_80)) == 1       # just below -> suspect


def _distinctive_missing_entry(i):
    return {"text": (f"Distinguished Career Achievement Award Number {i} "
                     "for Excellence in Translational Science Research"),
            "taxonomy_code": "X", "element_type": "paragraph"}


@pytest.mark.parametrize("lost,expected_severity", [
    (CLASSIFIED_UNRENDERED_WARN_ENTRIES - 1, "INFO"),
    (CLASSIFIED_UNRENDERED_WARN_ENTRIES, "WARN"),
    (CLASSIFIED_UNRENDERED_WARN_ENTRIES + 1, "WARN"),
])
def test_classified_unrendered_warn_entries_boundary(lost, expected_severity):
    assert CLASSIFIED_UNRENDERED_WARN_ENTRIES == 2
    stage3b = {"entries": [_distinctive_missing_entry(i) for i in range(lost)]}
    findings = lint_classified_unrendered(stage3b, _OUTPUT_BLOCKS)
    assert len(findings) == 1
    assert findings[0]["severity"] == expected_severity


_UNDER_EXTRACTION_RECORD_LINE = ("{year} Distinguished award for excellence "
                                 "in service to community broadly and "
                                 "repeatedly.")


def _padded_under_extraction_text(n_records, target_len):
    lines = [_UNDER_EXTRACTION_RECORD_LINE.format(year=2000 + i)
             for i in range(n_records)]
    text = "\n".join(lines)
    assert len(text) <= target_len, "record lines alone exceed target_len"
    pad = target_len - len(text)
    if pad > 0:
        text = text + "\n" + ("z" * (pad - 1))
    assert len(text) == target_len
    return text


def _under_extraction_entry(pct, total_len, n_records):
    return {"element_type": "paragraph", "element_idx_start": 1,
            "extraction_coverage": {"extraction_coverage_percent": pct},
            "text": _padded_under_extraction_text(n_records, total_len)}


def test_under_extraction_max_pct_boundary():
    assert UNDER_EXTRACTION_MAX_PCT == 40.0
    below = _under_extraction_entry(UNDER_EXTRACTION_MAX_PCT - 0.01,
                                    UNDER_EXTRACTION_MIN_CHARS + 50, 3)
    at = _under_extraction_entry(UNDER_EXTRACTION_MAX_PCT,
                                 UNDER_EXTRACTION_MIN_CHARS + 50, 3)
    assert len(lint_under_extraction({"entries": [below]})) == 1
    assert lint_under_extraction({"entries": [at]}) == []  # >= threshold is excluded


def test_under_extraction_min_chars_boundary():
    assert UNDER_EXTRACTION_MIN_CHARS == 800
    at = _under_extraction_entry(30.0, UNDER_EXTRACTION_MIN_CHARS, 3)
    above = _under_extraction_entry(30.0, UNDER_EXTRACTION_MIN_CHARS + 1, 3)
    assert lint_under_extraction({"entries": [at]}) == []  # <= threshold is excluded
    assert len(lint_under_extraction({"entries": [above]})) == 1


def test_under_extraction_min_records_boundary():
    assert UNDER_EXTRACTION_MIN_RECORDS == 2
    below = _under_extraction_entry(30.0, UNDER_EXTRACTION_MIN_CHARS + 50,
                                    UNDER_EXTRACTION_MIN_RECORDS - 1)
    at = _under_extraction_entry(30.0, UNDER_EXTRACTION_MIN_CHARS + 50,
                                 UNDER_EXTRACTION_MIN_RECORDS)
    assert lint_under_extraction({"entries": [below]}) == []  # < threshold is excluded
    assert len(lint_under_extraction({"entries": [at]})) == 1


_OVERLAP_WORDS = [''.join(c) for c in itertools.islice(
    itertools.product('abcdefghijklmnopqrstuvwxyz', repeat=5), 100)]
_OVERLAP_ENTRY_TEXT = " ".join(_OVERLAP_WORDS)


def _overlap_haystack(matched_n):
    # Reversed order so the entry's own leading squashed piece (its first
    # ~8 words) never appears as a literal substring here -- this isolates
    # the token-overlap path from verbatim containment.
    matched = list(reversed(_OVERLAP_WORDS[:matched_n]))
    return _haystacks([("p", "SOME HEADER"),
                       ("p", " ".join(matched) + " filler filler filler")])


def test_entry_rendered_token_overlap_exact_boundary():
    from unified_pipeline.doctor.shared import RENDER_TOKEN_OVERLAP
    assert RENDER_TOKEN_OVERLAP == 0.7
    at_boundary = _overlap_haystack(70)     # 70/100 = 0.70
    just_below = _overlap_haystack(69)      # 69/100 = 0.69
    assert _entry_rendered(_OVERLAP_ENTRY_TEXT, at_boundary.text,
                           at_boundary.tokens) is True
    assert _entry_rendered(_OVERLAP_ENTRY_TEXT, just_below.text,
                           just_below.tokens) is False


# ==========================================================================
# D8 (T2.7) -- dedup tokenization: repetition, Unicode, digits, punctuation.

def test_alphanumeric_tokens_repeated_tokens_collapse():
    # A dropped entry that repeats one token is fully covered by a kept
    # entry with a single occurrence of it -- the SET collapses repeats.
    dropped = {"code": "A", "metric": "j",
              "dropped_text": "grant grant grant", "kept_text": "grant"}
    assert lint_dedup_drops({"dedup_decisions": [dropped]}) == []
    assert _alphanumeric_tokens("grant grant grant") == {"grant"}
    assert _alphanumeric_tokens("grant") == {"grant"}


def test_alphanumeric_tokens_unicode_diaeresis_splits_on_the_non_ascii_char():
    # _norm lowercases and joins whitespace but does not strip diacritics;
    # _DEDUP_TOKEN_RE is ASCII-only ([a-z0-9]+), so it breaks at the 'ü'
    # rather than treating "müller" as one token or normalizing it away.
    assert _alphanumeric_tokens("Müller") == {"m", "ller"}


def test_alphanumeric_tokens_digits_are_tokens_punctuation_is_not():
    assert _alphanumeric_tokens("Grant 2020") == {"grant", "2020"}
    assert _alphanumeric_tokens("grant, funded!") == {"grant", "funded"}


# ==========================================================================
# D9 -- lint_invented_records (#829): a record built from the WCM template's
# own labels rather than real content, reaching the delivered document.
#
# "Full Name of Board" / "Certificate #" / "Dates of Certification" are the
# WCM board-certification template's own column labels, already committed in
# `template_boilerplate_phrases.json` -- template scaffolding text, not a
# verbatim line from any real CV. Same for the licensure paragraph below,
# which is the tracked template's own instruction wording with one word
# swapped ("ABC" for the real template's own hospital name) so the near-match
# path is exercised without reproducing any real CV's text.

_BOARD_LABEL = "Full Name of Board"
_CERT_NUMBER_LABEL = "Certificate #"
_CERT_DATE_LABEL = "Dates of Certification"

_HEADER_ROW = [_BOARD_LABEL, f"{_CERT_NUMBER_LABEL} \n(indicate if board eligible)",
              f"{_CERT_DATE_LABEL} \n(yyyy–yyyy)"]
_INVENTED_CERT_ROW = [_BOARD_LABEL, _CERT_NUMBER_LABEL, ""]
_REAL_CERT_ROW = ["American Board of Internal Medicine", "123456", "2015"]

_NEAR_MATCH_LICENSURE_TEXT = (
    "(Every doctor appointed to the ABC Hospital staff, except interns and "
    "aliens in the US via non-immigrant visas, must have a New York State "
    "license or a temporary certificate in lieu of the license.)")


def _invented_cert_entry(element_idx_start=1):
    return {"taxonomy_code": "F2", "element_type": "table_row",
            "element_idx_start": element_idx_start,
            "text": f"{_BOARD_LABEL} | {_CERT_NUMBER_LABEL} | {_CERT_DATE_LABEL}",
            "extracted_fields": {
                "certifying_board": _BOARD_LABEL,
                "certificate_number": _CERT_NUMBER_LABEL,
                "year_certified": None, "recertification_date": None}}


def _real_cert_entry(element_idx_start=1):
    return {"taxonomy_code": "F2", "element_type": "table_row",
            "element_idx_start": element_idx_start,
            "text": "American Board of Internal Medicine | 123456 | 2015",
            "extracted_fields": {
                "certifying_board": "American Board of Internal Medicine",
                "certificate_number": "123456",
                "year_certified": "2015", "recertification_date": None}}


def _invented_licensure_entry(element_idx_start=1, element_type="break",
                              text=_NEAR_MATCH_LICENSURE_TEXT):
    return {"taxonomy_code": "F1", "element_type": element_type,
            "element_idx_start": element_idx_start, "text": text,
            "extracted_fields": {"state_country": "New York State",
                                 "license_number": None, "issue_date": None,
                                 "expiration_date": None}}


def test_invented_record_min_values_is_2_and_licensure_code_is_f1():
    assert INVENTED_RECORD_MIN_VALUES == 2
    assert INVENTED_RECORD_LICENSURE_CODE == "F1"
    assert _MIN_EXACT_LEN == 25


# -- _nonempty_field_values ------------------------------------------------

def test_nonempty_field_values_flattens_lists_and_drops_blanks_and_dicts():
    fields = {
        "single": "Alpha",
        "blank": "",
        "whitespace_only": "   ",
        "absent": None,
        "listed": ["Beta", "", None, "Gamma"],
        "nested": {"unexpected": "should not be flattened"},
    }
    assert _nonempty_field_values(fields) == ["Alpha", "Beta", "Gamma"]
    assert _nonempty_field_values({}) == []


# -- _is_invented_record ----------------------------------------------------

def test_is_invented_record_true_on_the_board_certification_header_row():
    assert _is_invented_record(_invented_cert_entry()["extracted_fields"])


def test_is_invented_record_false_on_real_board_certification_values():
    assert not _is_invented_record(_real_cert_entry()["extracted_fields"])


def test_is_invented_record_false_below_the_min_values_floor():
    # One matched label alone, however distinctive, is not enough evidence
    # (the G/Institutional-Affiliation shape: a single unfilled prompt label
    # legitimately echoes back with a blank companion cell).
    assert not _is_invented_record({"affiliation_type": _BOARD_LABEL})
    # Isolate the count guard from the length guard: "Board / Organization
    # Name" alone is exactly 25 chars (== _MIN_EXACT_LEN), long enough on
    # its own to pass the length floor, so this fails ONLY on value count.
    long_single_label = "Board / Organization Name"
    assert len(long_single_label) == _MIN_EXACT_LEN
    assert not _is_invented_record({"granting_body": long_single_label})


def test_is_invented_record_false_below_the_combined_length_floor():
    # "Total"/"100%" are themselves registered template phrases (the blank
    # %-effort table's own worked example), and a real, fully-filled J table
    # legitimately ends in a "Total | 100%" row -- 2 matched values, but only
    # 9 combined chars, well under _MIN_EXACT_LEN (25).
    assert not _is_invented_record({"activity": "Total", "percent_effort": "100%"})


def test_is_invented_record_true_at_the_exact_combined_length_floor():
    # "Organization" (12 chars) and "Bibliography" (12 chars) are each their
    # own registered template label -- raw lengths sum to 24, and the "|"
    # the code joins them with brings the combined length to exactly 25
    # (== _MIN_EXACT_LEN), the boundary the `<` comparison must accept.
    # A `<` -> `<=` mutant on the length check, or a join separator swapped
    # from "|" to "" (which drops this to 24), each flip this to False.
    a, b = "Organization", "Bibliography"
    assert len(a) + len(b) == 24
    assert len(f"{a}|{b}") == _MIN_EXACT_LEN
    assert _is_invented_record({"field_one": a, "field_two": b})


def test_is_invented_record_false_one_char_below_the_combined_length_floor():
    # Same two-label shape, one char short of the floor: "Organization"
    # (12) and "Institution" (11) join to exactly 24 chars, isolating the
    # `<` boundary from the other side.
    a, b = "Organization", "Institution"
    assert len(f"{a}|{b}") == _MIN_EXACT_LEN - 1
    assert not _is_invented_record({"field_one": a, "field_two": b})


def test_is_invented_record_false_on_a_mixed_real_and_label_record():
    # Isolates the `all(...)` guard itself (not just the two floors above):
    # one real value plus one template label clears both the value-count
    # floor (2) and the combined-length floor (>= 25, here 49) but must
    # stay False -- `any(...)` in place of `all(...)` would wrongly call
    # this fabricated, and nothing else in this file rebuilds a
    # certifying_board/certificate_number pair with one side real (#829).
    mixed = {"certifying_board": "American Board of Internal Medicine",
             "certificate_number": _CERT_NUMBER_LABEL}
    assert len(mixed) >= INVENTED_RECORD_MIN_VALUES
    assert len("|".join(mixed.values())) >= _MIN_EXACT_LEN
    assert not _is_invented_record(mixed)


def test_is_invented_record_false_with_no_values_at_all():
    assert not _is_invented_record({"a": None, "b": ""})


# -- _rendered_row_value_sets ------------------------------------------------

def test_rendered_row_value_sets_distinguishes_header_from_data_row():
    rows = _rendered_row_value_sets([[_HEADER_ROW, _INVENTED_CERT_ROW]])
    invented_key = frozenset({_BOARD_LABEL.lower(), _CERT_NUMBER_LABEL.lower()})
    header_key = frozenset(_norm(v) for v in _HEADER_ROW)
    assert invented_key in rows
    assert header_key in rows
    # The header row's 3rd cell carries a parenthetical the data row's
    # matching cell does not -- they must not collide into one set.
    assert invented_key != header_key


def test_rendered_row_value_sets_ignores_empty_cells_and_empty_tables():
    assert _rendered_row_value_sets([]) == set()
    assert _rendered_row_value_sets([[["", "", ""]]]) == set()
    assert _rendered_row_value_sets([[["Alpha", "", "Beta"]]]) == {
        frozenset({"alpha", "beta"})}


# -- lint_invented_records: part (a), the rendered-header-record shape -----

def test_lint_invented_records_warns_on_a_rendered_header_record():
    stage4 = {"entries": [_invented_cert_entry(element_idx_start=7)]}
    table_rows = [[_HEADER_ROW, _INVENTED_CERT_ROW]]
    findings = lint_invented_records(stage4, table_rows)
    assert len(findings) == 1
    finding = findings[0]
    assert set(finding.keys()) == {"lint", "severity", "message", "evidence"}
    assert finding["lint"] == "invented_records"
    assert finding["severity"] == "WARN"
    assert "F2" in finding["message"] and "7" in finding["message"]
    assert "#829" in finding["message"]
    # The evidence is the populated field values, not just the finding's
    # key set -- an `[]` in place of the list comprehension would still
    # pass every assertion above (#829's own contract test only checks
    # keys), silently dropping what a reviewer sees. The two None-valued
    # fields (`year_certified`, `recertification_date`) contribute nothing.
    assert finding["evidence"] == [
        "certifying_board: Full Name of Board",
        "certificate_number: Certificate #",
    ]


def test_lint_invented_records_silent_when_the_header_record_never_rendered():
    # Stage 4 still fabricates the entry, but stage 6 correctly suppressed
    # it (#959's own fix, for this one section) -- only the real header row
    # made it into the document, no second data row.
    stage4 = {"entries": [_invented_cert_entry()]}
    table_rows = [[_HEADER_ROW]]
    assert lint_invented_records(stage4, table_rows) == []


def test_lint_invented_records_silent_on_real_certification_content():
    stage4 = {"entries": [_real_cert_entry()]}
    table_rows = [[_HEADER_ROW, _REAL_CERT_ROW]]
    assert lint_invented_records(stage4, table_rows) == []


def test_lint_invented_records_skips_taxonomy_code_t():
    entry = _invented_cert_entry()
    entry["taxonomy_code"] = "T"
    assert lint_invented_records({"entries": [entry]}, [[_HEADER_ROW, _INVENTED_CERT_ROW]]) == []


def test_lint_invented_records_silent_on_single_label_affiliation_prompt():
    # The G/Institutional-Affiliation shape: one unfilled prompt label
    # rendered with a blank companion cell is the template's own intentional
    # "show the prompt, blank if unfilled" convention, not a fabricated
    # multi-field record.
    entry = {"taxonomy_code": "G", "element_type": "break",
            "element_idx_start": 3, "text": f"{_BOARD_LABEL}:",
            "extracted_fields": {"organization": None,
                                 "affiliation_type": _BOARD_LABEL}}
    table_rows = [[[_BOARD_LABEL, ""]]]
    assert lint_invented_records({"entries": [entry]}, table_rows) == []


def test_lint_invented_records_silent_on_a_real_percent_effort_total_row():
    entry = {"taxonomy_code": "J", "element_type": "table_row",
            "element_idx_start": 4, "text": "Total | 100%",
            "extracted_fields": {"activity": "Total", "percent_effort": "100%"}}
    table_rows = [[["Activity", "% Effort"], ["Total", "100%"]]]
    assert lint_invented_records({"entries": [entry]}, table_rows) == []


# -- lint_invented_records: part (b), the F1 invented-licence shape --------

def test_lint_invented_records_warns_on_an_f1_entry_matching_a_known_instruction_exactly():
    known_instruction = ("Licensure: Every physician appointed to the "
                         "Hospital staff, except interns, and aliens in "
                         "the US via non-immigrant visas, must have a New "
                         "York State license or a temporary certificate in "
                         "lieu of the license.")
    entry = _invented_licensure_entry(text=known_instruction)
    findings = lint_invented_records({"entries": [entry]}, [])
    assert len(findings) == 1
    assert findings[0]["lint"] == "invented_records"
    assert findings[0]["severity"] == "WARN"
    assert "F1" in findings[0]["message"] and "#829" in findings[0]["message"]
    # The evidence is the source text itself (truncated), not just the
    # message -- an `[]` in place of `[text[:120]]` would still pass every
    # assertion above. `known_instruction` is 207 chars, so this also pins
    # the truncation, not just that evidence is non-empty.
    assert findings[0]["evidence"] == [known_instruction[:120]]


def test_lint_invented_records_warns_on_an_f1_entry_matching_via_the_pipe_split_path_only():
    # A table-row-shaped F1 text ("... | ") reaches `is_template_instruction`
    # via its pipe-split rule (b), not the whole-string exact match in rule
    # (a) -- the trailing "|" survives normalization, so the joined string
    # never equals the known instruction verbatim. `is_near_template_instruction`
    # refuses any text containing "|" outright, so this exercises the exact
    # branch (`is_template_instruction(...)`) with the near-match branch
    # provably False, isolating the `or` in `lint_invented_records` from the
    # near-match test above (#829).
    known_instruction = ("Licensure: Every physician appointed to the "
                         "Hospital staff, except interns, and aliens in "
                         "the US via non-immigrant visas, must have a New "
                         "York State license or a temporary certificate in "
                         "lieu of the license.")
    text = f"{known_instruction} | "
    assert is_template_instruction(text)
    assert not is_near_template_instruction(text)
    entry = _invented_licensure_entry(text=text)
    findings = lint_invented_records({"entries": [entry]}, [])
    assert len(findings) == 1
    assert findings[0]["lint"] == "invented_records"
    assert "F1" in findings[0]["message"]


def test_lint_invented_records_warns_on_an_f1_entry_near_matching_a_known_instruction():
    entry = _invented_licensure_entry()  # the "ABC Hospital" near-variant
    findings = lint_invented_records({"entries": [entry]}, [])
    assert len(findings) == 1
    assert findings[0]["lint"] == "invented_records"


def test_lint_invented_records_fires_on_f1_regardless_of_element_type():
    # A5IZ6Q's own invented licence was extracted as element_type "break" --
    # pin that this lint does not filter it out (#829).
    for element_type in ("break", "header", "paragraph", "table_row"):
        entry = _invented_licensure_entry(element_type=element_type)
        assert len(lint_invented_records({"entries": [entry]}, [])) == 1, element_type


def test_lint_invented_records_ignores_the_same_text_under_a_different_code():
    entry = _invented_licensure_entry()
    entry["taxonomy_code"] = "F2"
    assert lint_invented_records({"entries": [entry]}, []) == []


def test_lint_invented_records_silent_on_real_f1_content():
    entry = {"taxonomy_code": "F1", "element_type": "table_row",
            "element_idx_start": 2,
            "text": "New York | 123456 | 06/01/2010",
            "extracted_fields": {"state_country": "New York",
                                 "license_number": "123456",
                                 "issue_date": "06/01/2010",
                                 "expiration_date": None}}
    assert lint_invented_records({"entries": [entry]}, []) == []


def test_lint_invented_records_reports_both_shapes_in_one_run():
    stage4 = {"entries": [_invented_cert_entry(element_idx_start=1),
                          _invented_licensure_entry(element_idx_start=2)]}
    table_rows = [[_HEADER_ROW, _INVENTED_CERT_ROW]]
    findings = lint_invented_records(stage4, table_rows)
    assert len(findings) == 2
    assert any("F2" in f["message"] for f in findings)
    assert any("F1" in f["message"] for f in findings)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
