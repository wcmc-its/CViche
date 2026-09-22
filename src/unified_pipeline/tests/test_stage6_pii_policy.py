"""#820 round 2: the withhold policy table, its scopes, and the pre-render
pass that applies it (`stage6/normalization/pii.py` + `stage6/pii_pass.py`).

Three things are pinned here, all with SYNTHETIC values:

1. THE PROBE TABLE. Every row of #820's 16-shape DOB table and every
   category in the issue's comment, each run at three destinations -- an
   A-coded entry (Personal Data), an Appendix-bound entry (T: not in
   RENDER_ROUTED_CODES), and a routed content entry (S4/S1/F1) -- with the
   expected verdict per destination. The #473 negative controls ride in
   the same table as content-coded rows, so a scope flip on any policy row
   (ALL_CODES <-> PERSONAL_AND_APPENDIX) or a dropped row fails a named
   case rather than surviving silently.
2. THE PASS. `run_pii_pass` cuts fragments by offset, drops PII-keyed
   fields, records one WithheldItem per cut with the section the entry
   would have rendered in, and leaves a clean entry's dict untouched.
3. THE COMMENT TEXT. `withheld_comment_text` names categories, counts and
   sections -- never a value.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_pii_policy.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.pii import (  # noqa: E402
    CAT_AGE,
    CAT_ALIEN_REGISTRATION,
    CAT_BIRTH,
    CAT_BLOOD_TYPE,
    CAT_CHILDREN,
    CAT_DATE_OF_BIRTH,
    CAT_DEA,
    CAT_DISABILITY,
    CAT_DRIVERS_LICENSE,
    CAT_EMERGENCY_CONTACT,
    CAT_ETHNICITY,
    CAT_FAMILY,
    CAT_GENDER,
    CAT_HEALTH,
    CAT_HOME_CONTACT,
    CAT_MARITAL_STATUS,
    CAT_PASSPORT,
    CAT_PLACE_OF_BIRTH,
    CAT_RELIGION,
    CAT_SALARY,
    CAT_SPOUSE,
    CAT_SSN,
    CAT_VETERAN,
    CAT_VISA,
    DECIDED_820,
    DECIDED_821,
    DECIDED_821_PENDING,
    PRE_LLM_PLACEHOLDER,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    WithheldItem,
    _pii_fragments,
    _pii_matches,
    redact_pre_llm_values,
)
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    _resolve_licensure,
)
from unified_pipeline.stage6.pii_pass import (  # noqa: E402
    APPENDIX_SECTION_LABEL,
    WITHHELD_COMMENT_FOOTER,
    WITHHELD_COMMENT_HEADER,
    _entry_scope,
    run_pii_pass,
    withheld_comment_text,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RENDER_ROUTED_CODES,
    TAXONOMY_TO_SECTION,
)

# The three destinations a probe is run at. 'A' is the Personal Data block;
# 'T' is not in RENDER_ROUTED_CODES, so generate() sends it to the Appendix;
# 'S4' (book chapters) is a routed content code.
A, APPENDIX, CONTENT = "A", "T", "S4"

DENIED, ALLOWED = True, False


def _denied(text: str, code: str) -> bool:
    return bool(_pii_fragments(text, _entry_scope(code, RENDER_ROUTED_CODES)))


# --------------------------------------------------------------------------
# 1. The probe table
#
# (text, category, {destination: expected}). `category` is what the pass
# must report; None for a negative control.
# --------------------------------------------------------------------------

_ALL = {A: DENIED, APPENDIX: DENIED, CONTENT: DENIED}
_PERSONAL_ONLY = {A: DENIED, APPENDIX: DENIED, CONTENT: ALLOWED}
_NEVER = {A: ALLOWED, APPENDIX: ALLOWED, CONTENT: ALLOWED}

PROBE_TABLE = [
    # --- #820's 16 DOB shapes ------------------------------------------
    ("Date of Birth: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth\t01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth   01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Born: 1970", CAT_BIRTH, _PERSONAL_ONLY),
    ("Personal Information: Date of Birth: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Personal Data: Husband: Pat Example", CAT_SPOUSE, _ALL),
    ("Nationality: X; Date of Birth: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Birthday: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Year of birth: 1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth (DOB): 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("DOB/POB: 01/02/1970, Example City", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of birth 2 January 1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Born January 2, 1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Born on January 2, 1970 in Example City", CAT_DATE_OF_BIRTH, _ALL),
    ("Age: 54", CAT_AGE, _PERSONAL_ONLY),
    ("Fecha de nacimiento: 02/01/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("• Date of Birth: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("12. Date of Birth: 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("• Children: Ann, Bob", CAT_CHILDREN, _PERSONAL_ONLY),
    # --- the comment's categories --------------------------------------
    ("Social Security #: 123-45-6789", CAT_SSN, _ALL),
    ("SS#: 123-45-6789", CAT_SSN, _ALL),
    ("SSN 123-45-6789", CAT_SSN, _ALL),
    ("Social Security No. 123 45 6789", CAT_SSN, _ALL),
    ("Reference on file: 123-45-6789", CAT_SSN, _ALL),
    ("SSN (last 4): 6789", CAT_SSN, _ALL),
    ("Married to Pat Example", CAT_SPOUSE, _ALL),
    ("Personal: married to Pat Example", CAT_SPOUSE, _ALL),
    ("a method married to clinical judgement", None, _NEVER),
    ("Newly-married to the field", None, _NEVER),
    ("Wife's name: Pat Example", CAT_SPOUSE, _ALL),
    ("Marital Status: Married", CAT_MARITAL_STATUS, _ALL),
    ("Place of Birth: Example City", CAT_PLACE_OF_BIRTH, _ALL),
    ("Family: married, two children", CAT_FAMILY, _PERSONAL_ONLY),
    ("Emergency Contact: Pat Example (wife), 212-555-1234", CAT_EMERGENCY_CONTACT, _ALL),
    ("Passport No.: X1234567", CAT_PASSPORT, _ALL),
    ("Visa Status: O-1", CAT_VISA, _ALL),
    ("Immigration Status: permanent resident", CAT_VISA, _ALL),
    ("CITIZENSHIP: Example (Union), O-1 Visa.", CAT_VISA, _ALL),
    ("Alien Registration Number: A123456789", CAT_ALIEN_REGISTRATION, _ALL),
    ("Driver's License: D1234567", CAT_DRIVERS_LICENSE, _ALL),
    ("DEA #: AB1234567", CAT_DEA, _PERSONAL_ONLY),
    ("Religion: Example", CAT_RELIGION, _PERSONAL_ONLY),
    ("Ethnicity: Example", CAT_ETHNICITY, _PERSONAL_ONLY),
    ("Gender: Female", CAT_GENDER, _PERSONAL_ONLY),
    ("Veteran Status: Yes", CAT_VETERAN, _PERSONAL_ONLY),
    ("Disability: None", CAT_DISABILITY, _PERSONAL_ONLY),
    ("Health: Good", CAT_HEALTH, _PERSONAL_ONLY),
    ("Blood Type: O+", CAT_BLOOD_TYPE, _PERSONAL_ONLY),
    ("Children: Ann, Bob", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Dependents: 2", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Salary: $1", CAT_SALARY, _ALL),
    ("Honorarium: $1", CAT_SALARY, _ALL),
    # #821 (settled): home address/phone move from render to withhold,
    # Personal Data/Appendix only -- an office address/phone is unaffected.
    ("Home address: 1 Example St", CAT_HOME_CONTACT, _PERSONAL_ONLY),
    ("Home phone: 555-111-2222", CAT_HOME_CONTACT, _PERSONAL_ONLY),
    # --- #473 negative controls, as content-coded rows too -------------
    ("Children: Research, Practice and Policy. Example Press, 2001.",
     CAT_CHILDREN, _PERSONAL_ONLY),
    ("Gender: A Review of the Literature", CAT_GENDER, _PERSONAL_ONLY),
    ("Women, Gender: A review", CAT_GENDER, _PERSONAL_ONLY),
    ("Health: A Title", CAT_HEALTH, _PERSONAL_ONLY),
    ("Foreign-born", None, _NEVER),
    ("Foreign-born – 2015", None, _NEVER),
    ("Children's Oncology Group - Emeritus", None, _NEVER),
    ("Children and Fire: Research on Burn Prevention", None, _NEVER),
    ("Born in the USA: Birth Cohort Methods", None, _NEVER),
    ("Health Sciences", None, _NEVER),
    ("Public Health", None, _NEVER),
    ("Gender Medicine", None, _NEVER),
    ("Veterans Affairs Medical Center", None, _NEVER),
    ("Age-related macular degeneration", None, _NEVER),
    ("Religion and Healing in America", None, _NEVER),
    ("Office telephone: 212-555-1234", None, _NEVER),
    ("(212) 555-1234", None, _NEVER),
    ("NPI: 1234567890", None, _NEVER),
    ("Citizenship: Example", None, _NEVER),
    ("ISBN: 978-2-1234-567-1", None, _NEVER),
    ("Sex: differences in galanin expression", None, _NEVER),
    ("Race: reporting practices in clinical trials", None, _NEVER),
]


def _probe_ids():
    return [f"{text[:28]!r}@{dest}" for text, _, expected in PROBE_TABLE
            for dest in expected]


@pytest.mark.parametrize(
    "text,category,dest,expected",
    [(text, category, dest, expected[dest])
     for text, category, expected in PROBE_TABLE for dest in expected],
    ids=_probe_ids(),
)
def test_probe_table(text, category, dest, expected):
    """One case per (shape, destination): denied where the row's scope
    reaches, allowed where it does not, and the category the pass reports
    is the policy row's own."""
    assert _denied(text, dest) is expected, (
        f"{text!r} at {dest}: expected {'denied' if expected else 'allowed'}")
    if expected:
        scope = _entry_scope(dest, RENDER_ROUTED_CODES)
        assert [m.category for m in _pii_matches(text, scope)] == [category]


def test_every_policy_row_has_a_probe_that_reaches_it():
    """A dropped or renamed policy row must fail a probe, so every category
    in the table is exercised by at least one denied row above."""
    probed = {category for _, category, _ in PROBE_TABLE if category}
    for rule in WITHHOLD_POLICY:
        assert rule.category in probed, f"policy row {rule.category!r} has no probe"


def test_every_policy_row_carries_a_scope_and_a_decision():
    for rule in WITHHOLD_POLICY:
        assert rule.scope in (SCOPE_ALL_CODES, SCOPE_PERSONAL_AND_APPENDIX), rule
        assert rule.decided_by in (DECIDED_820, DECIDED_821_PENDING, DECIDED_821), rule
        assert (rule.label is None) != (rule.shape is None), rule


def test_the_scope_split_is_generate_s_own_routing_predicate():
    """Appendix-bound means NOT in RENDER_ROUTED_CODES -- the same set
    `generate()` builds the unmapped batch from -- and nothing else."""
    assert _entry_scope("A", RENDER_ROUTED_CODES) == SCOPE_PERSONAL_AND_APPENDIX
    assert _entry_scope("T", RENDER_ROUTED_CODES) == SCOPE_PERSONAL_AND_APPENDIX
    assert _entry_scope("ZZ", RENDER_ROUTED_CODES) == SCOPE_PERSONAL_AND_APPENDIX
    for code in ("S4", "R", "F1", "B1", "M2A"):
        assert code in RENDER_ROUTED_CODES
        assert _entry_scope(code, RENDER_ROUTED_CODES) == SCOPE_ALL_CODES


def test_dea_pii_pass_scope_unchanged_licensure_withholds_it_at_render():
    """#821 (settled): the template's DEA slot IS now withheld -- but not
    by widening this row's scope (`pii.py`'s own comment on the row
    explains why: an entry that classifies as DEA purely by number SHAPE,
    with no "DEA" text at all, could never be caught by a text row either
    way, and cutting the label out of a state-bearing entry's text would
    make `_classify_licensure_entry` fall through to KIND_LICENSE and
    render the real number as an ordinary licence row -- worse than doing
    nothing). So the pass still never touches an F1 entry's text for DEA,
    exactly as before #821, and `_resolve_licensure`
    (stage6/sections/licensure.py) is what withholds it, unconditionally,
    whether classified by label or by shape. A stray `DEA #:` line in the
    Appendix is still withheld by this row.

    See test_stage6_licensure_pii_policy.py for the render-level proof
    (the DEA slot cell empty, the Word comment present)."""
    assert not _denied("DEA number: AB1234567", "F1")
    assert _denied("DEA number: AB1234567", "T")

    result = _resolve_licensure([
        {"text": "DEA AB1234567",
         "extracted_fields": {"license_number": "AB1234567", "date": "2019"}},
    ])
    assert result.dea_withheld is True
    assert result.identifiers.dea is None


def test_unanchored_matching_is_a_label_after_a_separator_not_a_bare_word():
    """The label must follow `;`, `,`, `:`, `(` or start-of-fragment. A
    label preceded by an ordinary word is NOT a label (M06: re-anchoring
    to fragment start alone must fail the first three; a bare word in
    prose must stay allowed)."""
    assert _denied("Personal Information: Date of Birth: 01/02/1970", A)
    assert _denied("Nationality: X, Date of Birth: 01/02/1970", A)
    assert _denied("(Date of Birth: 01/02/1970)", A)
    assert not _denied("The date of birth: a study of registries", CONTENT)


def test_semicolon_is_a_hard_fragment_boundary():
    """M07: without `;` in the split set the label after it is not
    fragment-initial and the fragment before it would swallow it."""
    fragments = _pii_fragments("Nationality: X; Date of Birth: 01/02/1970")
    assert fragments == ["Date of Birth: 01/02/1970"]


def test_day_month_year_and_single_space_shapes():
    """M11 / M12: day-first dates and a single-space separator ahead of a
    full date are caught; a single space ahead of a bare year is not."""
    assert _denied("Date of birth 2 January 1970", CONTENT)
    assert _denied("Born 2 January 1970", CONTENT)
    assert not _denied("the clinic, born 1970, moved", CONTENT)


def test_bare_ssn_value_is_denied_on_the_value_alone_but_not_inside_an_isbn():
    """M09: the value shape is its own row. Guarded on both sides so an
    ISBN-13's 3-2-4 run does not match."""
    assert _pii_fragments("file 123-45-6789 end") == ["123-45-6789"]
    assert _pii_fragments("ISBN 978-2-1234-567-1") == []
    assert _pii_fragments("212-555-1234") == []


# --------------------------------------------------------------------------
# 2. The pass
# --------------------------------------------------------------------------

def _run(entries_by_code):
    return run_pii_pass(entries_by_code, routed_codes=RENDER_ROUTED_CODES,
                        section_names=TAXONOMY_TO_SECTION)


def test_pass_cuts_the_fragment_out_of_text_and_records_the_section():
    """M01: the text strip is the pass's primary effect."""
    entry = {"text": "Citizenship: Example; Date of Birth: 01/02/1970",
             "taxonomy_code": "A", "extracted_fields": {}}
    result = _run({"A": [entry]})
    assert entry["text"] == "Citizenship: Example;"
    assert entry["_pii_fragments"] == ["Date of Birth: 01/02/1970"]
    assert entry["_pii_withheld"] is True
    assert result.withheld == [WithheldItem(CAT_DATE_OF_BIRTH, "Personal Data", 0)]


def test_pass_drops_a_pii_keyed_field_and_records_it():
    """M03: a field key is a removal in its own right."""
    entry = {"text": "Additional information", "taxonomy_code": "A",
             "extracted_fields": {"spouse_name": "Pat Example",
                                  "phone": "212-555-1234"}}
    result = _run({"A": [entry]})
    assert entry["extracted_fields"] == {"phone": "212-555-1234"}
    assert entry["text"] == "Additional information"
    assert result.withheld == [WithheldItem(CAT_SPOUSE, "Personal Data", 0)]


def test_pass_cuts_by_offset_so_a_repeated_fragment_cuts_the_right_one():
    """The span is pii.py's own offset, not a content search: the label
    occurrence after the separator is cut, the bare-word mention before
    it is not."""
    entry = {"text": "Born - Digital: a title\nBorn: 1970",
             "taxonomy_code": "A", "extracted_fields": {}}
    _run({"A": [entry]})
    assert entry["text"] == "Born - Digital: a title"


def test_pass_leaves_a_clean_entry_completely_untouched():
    """Determinism: same dict object, same keys, no bookkeeping keys."""
    fields = {"address": "1 Example St"}
    entry = {"text": "Office: 1 Example St", "taxonomy_code": "A",
             "extracted_fields": fields}
    before = dict(entry)
    entries = [entry]
    result = _run({"A": entries})
    assert entries[0] is entry
    assert entry == before
    assert entry["extracted_fields"] is fields
    assert result.withheld == []


def test_pass_scope_follows_the_entry_s_destination():
    """The same ambiguous line: withheld on A and on an Appendix-bound
    code, rendered on a routed content code."""
    a = {"text": "Children: Ann, Bob", "taxonomy_code": "A", "extracted_fields": {}}
    t = {"text": "Children: Ann, Bob", "taxonomy_code": "T", "extracted_fields": {}}
    s4 = {"text": "Children: Research, Practice and Policy. Example Press, 2001.",
          "taxonomy_code": "S4", "extracted_fields": {}}
    result = _run({"A": [a], "S4": [s4], "T": [t]})
    assert a["text"] == "" and t["text"] == ""
    assert s4["text"].startswith("Children: Research")
    assert "_pii_withheld" not in s4
    assert result.withheld == [
        WithheldItem(CAT_CHILDREN, "Personal Data", 0),
        WithheldItem(CAT_CHILDREN, APPENDIX_SECTION_LABEL, 2),
    ]


def test_pass_records_the_routed_section_name_for_a_content_code():
    """An ALL_CODES category on a routed code names that code's section."""
    entry = {"text": "Licensed; SSN: 123-45-6789", "taxonomy_code": "F1",
             "extracted_fields": {"license_number": "X1"}}
    result = _run({"F1": [entry]})
    assert result.withheld == [WithheldItem(CAT_SSN, "Licensure", 0)]
    assert entry["text"] == "Licensed;"
    assert entry["extracted_fields"] == {"license_number": "X1"}


def test_pass_entry_index_counts_across_codes_in_the_order_given():
    b1 = {"text": "MD, Example University, 2001", "taxonomy_code": "B1", "extracted_fields": {}}
    t = {"text": "Visa Status: O-1", "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"B1": [b1], "T": [t]})
    assert result.withheld == [WithheldItem(CAT_VISA, APPENDIX_SECTION_LABEL, 1)]


def test_pass_records_one_item_per_fragment_and_per_key():
    entry = {"text": "Date of Birth: 01/02/1970\nPlace of Birth: Example City",
             "taxonomy_code": "A", "extracted_fields": {"dob": "01/02/1970"}}
    result = _run({"A": [entry]})
    assert [i.category for i in result.withheld] == [
        CAT_DATE_OF_BIRTH, CAT_PLACE_OF_BIRTH, CAT_DATE_OF_BIRTH]


# --------------------------------------------------------------------------
# 3. The comment text
# --------------------------------------------------------------------------

def test_comment_text_names_categories_counts_and_sections_only():
    text = withheld_comment_text([
        WithheldItem(CAT_DATE_OF_BIRTH, "Personal Data", 0),
        WithheldItem(CAT_VISA, "Appendix", 3),
        WithheldItem(CAT_VISA, "Licensure", 4),
    ])
    lines = text.split("\n")
    assert lines[0] == WITHHELD_COMMENT_HEADER
    assert lines[1] == " • date of birth — 1 item, Personal Data"
    assert lines[2] == " • visa / immigration status — 2 items, Appendix, Licensure"
    assert lines[3] == WITHHELD_COMMENT_FOOTER
    assert len(lines) == 4


def test_comment_text_carries_no_value():
    """The item carries no value at all, so the text cannot: pinned against
    a future WithheldItem field that might."""
    text = withheld_comment_text([WithheldItem(CAT_SSN, "Appendix", 0)])
    assert "123-45-6789" not in text
    assert set(WithheldItem._fields) == {"category", "section_label", "entry_index"}


# --------------------------------------------------------------------------
# 4. Bare-label span extension (#821 R3 F-B)
#
# The extension exists because a hard delimiter can sit between a label and
# its own value, leaving the value uncut. It must reach exactly that value
# and nothing else: the round-2 version ran to the next hard delimiter with
# no test for what it was swallowing, and swallowed the NEXT labelled field
# -- citizenship and a work email, both #821 "render" items.
#
# Each case is the whole entry text, what must be GONE from the residual,
# and what must SURVIVE in it. All values synthetic.
# --------------------------------------------------------------------------

_BARE_LABEL_CASES = [
    # (id, text, gone, survives)
    ("newline_sibling_label",
     "Home Address:\nCitizenship: US",
     [], ["Citizenship: US"]),
    ("newline_numbered_sibling_label",
     "2. Home Address:\n3. Work Email: someone@example.org",
     [], ["3. Work Email: someone@example.org"]),
    ("single_space_sibling_label",
     "Home Phone:        555-0100 Citizenship: US",
     ["555-0100"], ["Citizenship: US"]),
    ("pipe_then_gapped_sibling_label",
     # The sibling's own colon sits PAST a hard delimiter (a column-aligned
     # "Citizenship   : US"), so a stop that only looked inside the span up
     # to the next delimiter could not see the label at all and would cut
     # the sibling's name off its value.
     "Home telephone: | Citizenship   : US",
     [], ["Citizenship"]),
    ("pipe_orphaned_value",
     "Home telephone: | 555-0100",
     ["555-0100"], []),
    ("wide_gap_orphaned_value",
     "Home Phone:        555-0100",
     ["555-0100"], []),
]


@pytest.mark.parametrize(
    "text,gone,survives",
    [(text, gone, survives) for _, text, gone, survives in _BARE_LABEL_CASES],
    ids=[case_id for case_id, *_ in _BARE_LABEL_CASES],
)
def test_bare_label_extension_cuts_the_value_and_only_the_value(text, gone, survives):
    """The five shapes, through the real `run_pii_pass`. The first three are
    the negative cases the extension must NOT reach past (a sibling field on
    the next line, the same with list markers, a sibling field one plain
    space after the value); the last two are the corpus shapes it exists
    for (a pipe, and a column-aligned wide gap with nothing after the
    value)."""
    entry = {"text": text, "taxonomy_code": "A", "extracted_fields": {}}
    _run({"A": [entry]})
    residual = entry["text"]
    for fragment in gone:
        assert fragment not in residual, f"{fragment!r} survived the cut"
        assert any(fragment in f for f in entry["_pii_fragments"]), (
            f"{fragment!r} was not part of what the pass says it removed")
    for fragment in survives:
        assert fragment in residual, f"{fragment!r} was cut with its neighbour"
        assert not any(fragment in f for f in entry["_pii_fragments"]), (
            f"{fragment!r} was recorded as withheld")


def test_bare_label_extension_never_crosses_a_newline_into_a_value():
    """The one shape the extension deliberately does NOT rescue: the value
    on the line below its label. A newline is the source document's own
    field separator, so what follows it cannot be assumed to be this
    label's value -- the pass leaves the line alone and flags the entry
    instead (`_pii_orphaned_value`), which is what makes
    `stage_6_word_template.py::_pii_cut_left_a_bare_label` refuse the
    whole entry rather than render an unlabelled address."""
    entry = {"text": "Home Address:\n12 Example Street", "taxonomy_code": "A",
             "extracted_fields": {}}
    _run({"A": [entry]})
    assert entry["text"] == "12 Example Street"
    assert entry["_pii_orphaned_value"] is True


def test_bare_label_with_nothing_after_it_orphans_nothing():
    """The negative of the flag above, and the whole of #821 R3 F-D: a
    protected label with no value anywhere after it (a template leftover)
    is not a leak, so the entry's other fields must still render."""
    entry = {"text": "Citizenship: US\nHome Address:", "taxonomy_code": "A",
             "extracted_fields": {}}
    _run({"A": [entry]})
    assert entry["text"] == "Citizenship: US"
    assert entry["_pii_orphaned_value"] is False


def test_bare_label_followed_by_a_sibling_label_orphans_nothing():
    entry = {"text": "Home Address:\nCitizenship: US", "taxonomy_code": "A",
             "extracted_fields": {}}
    _run({"A": [entry]})
    assert entry["_pii_orphaned_value"] is False


# --------------------------------------------------------------------------
# redact_pre_llm_values (#847) -- the value-only scrub applied before any
# LLM stage reads the text, at extract_unified_elements. Reuses this same
# WITHHOLD_POLICY table (via _pii_matches), restricted to CAT_DATE_OF_BIRTH
# and CAT_SSN; every other category is untouched here regardless of scope.
# --------------------------------------------------------------------------

def test_redact_pre_llm_values_replaces_ssn_value_keeps_label():
    out = redact_pre_llm_values("SSN: 123-45-6789")
    assert out == f"SSN: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_replaces_bare_ssn_shape_with_no_label():
    out = redact_pre_llm_values("Contact ref 123-45-6789 on file.")
    assert out == f"Contact ref {PRE_LLM_PLACEHOLDER} on file."


def test_redact_pre_llm_values_replaces_dob_value_with_colon_keeps_label():
    out = redact_pre_llm_values("Date of Birth: 01/02/1970")
    assert out == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_replaces_dob_value_colonless_keeps_label():
    out = redact_pre_llm_values("Born on 01/02/1970, in Example City")
    assert out == f"Born on {PRE_LLM_PLACEHOLDER}, in Example City"


def test_redact_pre_llm_values_untouched_publication_date_no_dob_label():
    text = "Smith J. Date: 2015. A study of examples."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_untouched_non_ssn_shaped_nine_digit_number():
    text = "Reference number 123456789 on the invoice."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_untouched_grant_number_shape():
    text = "Grant number R01-CA123456 funded 1999."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_untouched_out_of_scope_category():
    # Marital status is in WITHHOLD_POLICY but not a pre-LLM category --
    # only render-time (#820/#821) withholds it.
    text = "Marital Status: Married"
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_is_idempotent():
    once = redact_pre_llm_values("Date of Birth: 01/02/1970")
    twice = redact_pre_llm_values(once)
    assert once == twice == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"
