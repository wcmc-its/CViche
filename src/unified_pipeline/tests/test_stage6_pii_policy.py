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
    CAT_THIRD_PARTY_CONTACT,
    CAT_VETERAN,
    CAT_VISA,
    DECIDED_820,
    DECIDED_821,
    DECIDED_821_PENDING,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    WithheldItem,
    _pii_fragments,
    _pii_matches,
)
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    _resolve_licensure,
)
from unified_pipeline.stage6.pii_pass import (  # noqa: E402
    APPENDIX_SECTION_LABEL,
    WITHHELD_COMMENT_FOOTER,
    WITHHELD_COMMENT_HEADER,
    _entry_scope,
    _owner_name_tokens,
    _THIRD_PARTY_PHONE_RE,
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
# 4. Third-party contact in an unlabelled References block (#833)
#
# `WITHHOLD_POLICY` is label-driven; a free-form References block ("Name,
# Title, Institution" / phone / email, no label) carries nothing it keys
# on, so it needs a VALUE-SHAPE rule of its own -- applied only to entries
# `run_pii_pass` already scopes as Appendix-bound (code not in
# `routed_codes` and not 'A'), and only to a phone/email that is not the
# CV owner's own (known from the 'A' entries) and not a generic mailbox.
# --------------------------------------------------------------------------

def _owner_a_entry(name, *contacts):
    text = name + "\t" + "\t".join(contacts)
    return {"text": text, "taxonomy_code": "A",
            "extracted_fields": {"name": name}}


def test_references_block_with_name_title_institution_phone_and_email_is_withheld():
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu", "212-555-0100")
    t = {"text": "Dr. Jordan Reviewer, Chair, Example State University\t"
                 "555-234-8899\tjreviewer@example-state.edu",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert t["_pii_withheld"] is True
    assert t["text"] == "Dr. Jordan Reviewer, Chair, Example State University"
    assert [i.category for i in result.withheld if i.entry_index == 1] == [
        CAT_THIRD_PARTY_CONTACT, CAT_THIRD_PARTY_CONTACT]


def test_references_block_with_only_an_email_is_withheld():
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "Dr. Jordan Reviewer, Example State University, "
                 "jreviewer@example-state.edu",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "jreviewer@example-state.edu" not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


@pytest.mark.parametrize("phone", [
    "555-234-8899", "(555) 234-8899", "555.234.8899", "555 234 8899",
    "+1 555-234-8899",
])
def test_references_block_with_only_a_phone_is_withheld(phone):
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": f"Dr. Jordan Reviewer, Example State University, {phone}",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert phone not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


def test_two_references_in_one_entry_each_get_their_own_withheld_item():
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "Dr. Jordan Reviewer, jreviewer@example-state.edu\n"
                 "Dr. Alex Second, asecond@example-college.edu",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "jreviewer@example-state.edu" not in t["text"]
    assert "asecond@example-college.edu" not in t["text"]
    assert [i.category for i in result.withheld] == [
        CAT_THIRD_PARTY_CONTACT, CAT_THIRD_PARTY_CONTACT]


def test_comment_names_third_party_contact():
    text = withheld_comment_text([
        WithheldItem(CAT_THIRD_PARTY_CONTACT, APPENDIX_SECTION_LABEL, 1)])
    assert f" • {CAT_THIRD_PARTY_CONTACT} — 1 item, {APPENDIX_SECTION_LABEL}" in text


# --- negative controls, each a test -----------------------------------

def test_lab_website_with_no_email_or_phone_is_untouched():
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "Lab website: https://example-lab.example.edu/research",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == "Lab website: https://example-lab.example.edu/research"
    assert result.withheld == []


def test_owners_own_email_and_phone_from_a_are_untouched_in_an_appendix_entry():
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu", "212-555-0100")
    t = {"text": "Reprint requests to Dana Example, dana.example@wcm.example.edu, "
                 "212-555-0100",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_owners_own_contact_with_no_owner_name_in_the_entry_is_still_untouched():
    """Exercises the owner-contact exemption on its own terms (#833 round
    2, still true post-#920's per-value rewrite): this entry carries NO
    owner-name token anywhere -- not in its prose, not in the email's own
    local part -- so only the owner-contact set comparison can spare it.
    The owner's email is deliberately NOT `dana.example@...` -- that local
    part literally spells out the owner's own name tokens, so reusing it
    here would let the local-part check pass instead of the owner-contact
    check, making the owner-contact comparison itself untested (the
    round-1 verifier's exact finding, reproduced against a name-free
    address to confirm the check does the work alone)."""
    a = _owner_a_entry("Dana Example", "office-contact-9142@wcm.test", "212-555-0100")
    t = {"text": "Contact for reprints: office-contact-9142@wcm.test, 212-555-0100",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_owners_phone_in_a_different_format_is_still_recognised_as_the_owners_own():
    """`_phone_digits` normalises before comparing (#833 round 2): the same
    phone, formatted differently in the Appendix entry than in the 'A'
    entry, must still be spared -- no owner-name token here either, so the
    normalisation itself is what has to do the work."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu", "212-555-0100")
    t = {"text": "Contact for reprints: (212) 555-0100",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_owners_phone_with_a_plus_one_country_code_matches_the_bare_10_digit_form():
    """#920 review: `_phone_digits` used to strip only non-digits, so
    "+1 212-555-0100" (11 digits: "12125550100") and "212-555-0100" (10
    digits: "2125550100") normalised to two DIFFERENT strings and never
    matched each other. Now an 11-digit result starting with "1" has that
    leading digit stripped first, so the owner's own number is recognised
    regardless of which format carries the country code."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu", "+1 212-555-0100")
    t = {"text": "Contact for reprints: 212-555-0100",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_owners_phone_only_in_extracted_fields_is_still_recognised():
    """The owner set is gathered from `extracted_fields` as well as `text`
    (#833 round 2): a phone present only in the 'A' entry's structured
    field, never in its raw text, still spares the same phone elsewhere."""
    a = {"text": "Dana Example", "taxonomy_code": "A",
         "extracted_fields": {"name": "Dana Example", "phone": "212-555-0100"}}
    t = {"text": "Contact for reprints: 212-555-0100",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_owner_name_tokens_fall_back_to_the_first_a_entry_s_whole_text():
    """When no 'A' entry has `extracted_fields['name']`, the owner name
    tokens fall back to the first 'A' entry's raw text (#833 round 2): a
    References-block-style Personal Data entry ("Name, Title,
    Institution", no separate name field) still spares the owner's own
    second contact -- via the #920 per-value rule, because the second
    email's OWN LOCAL PART ("dana.example.alt") carries the fallback
    tokens, not because the surrounding entry prose happens to."""
    a = {"text": "Dana Example, Professor, Example State University",
         "taxonomy_code": "A", "extracted_fields": {}}
    t = {"text": "Dana Example is also reachable at "
                 "dana.example.alt@gmail.com for editorial correspondence",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_plus_one_country_code_is_fully_cut_not_left_dangling():
    """The phone shape's optional `+1` prefix must be cut along with the
    digits it introduces (#833 round 2) -- assert the exact residual, not
    just that the digits are gone, so a mutant that cuts only the 3-3-4
    digit run and leaves '+1' behind is caught."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "Dr. Jordan Reviewer, Example State University, "
                 "+1 555-234-8899",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == "Dr. Jordan Reviewer, Example State University,"
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


def test_owners_second_email_without_a_name_token_in_its_local_part_is_now_withheld():
    """#920 review, the headline fix: the exemption used to be PER ENTRY --
    2+ of the owner's own name tokens ANYWHERE in the entry's text spared
    every value in it, so this exact shape (the owner's own name in the
    prose, right beside a second email of theirs) was untouched under the
    old rule for the wrong reason -- the entry-wide name check, not
    anything about the email itself. It is now PER VALUE
    (`_email_spared_by_owner_name`): a local part with no owner-name
    token in it gets no exemption from the name-sharing path, only from
    `owner` (the owner's OWN harvested contacts) -- and this second
    address was never harvested, because it never appeared in an 'A'
    entry. The correct, safer new behaviour is to withhold it: an
    over-redacted second email of the owner's own costs almost nothing; a
    real reference's contact info beside the owner's name used to leak
    completely (see the entry-wide leak test below).

    Synthetic value only: `dqe.alt77@example.org`, not the real-looking
    `@gmail.com` domain this test carried before the #920 blocker fix
    (verifier minor note)."""
    a = _owner_a_entry("Dana Q Example", "dana.example@wcm.example.edu")
    t = {"text": "Dana Q Example is also reachable at "
                 "dqe.alt77@example.org for editorial correspondence",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "dqe.alt77@example.org" not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


def test_owners_second_email_whose_own_local_part_carries_a_name_token_is_untouched():
    """The positive of the test above: the owner-own-second-email case the
    #920 fix must keep sparing. Nothing OUTSIDE the address itself names
    the owner (no "Dana", no "Example" in the surrounding prose) -- the
    entry-level gate (`_shares_owner_name`) is satisfied here only because
    it scans the WHOLE entry text and the address's own local part
    ("dana.q.example") tokenises to "dana" and "example" too, and the
    SAME local part also carries those tokens as whole segments, so the
    #920 fix's second conjunct holds as well. Both conjuncts true, by the
    address's own shape alone -- proving the exemption still reaches this
    case without a separate name-bearing heading or footer."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "Reprint requests: dana.q.example@gmail.com",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_a_references_entry_sharing_the_owner_s_name_still_withholds_a_third_party_s_contact():
    """#920 review -- THE LEAK this ticket closes. On the baseline
    (`_shares_owner_name`, per-entry): a References entry that carries the
    CV owner's own name anywhere in it -- a "References for <owner>"
    heading, a letterhead line, a footer -- was spared WHOLE the moment
    the owner's name tokens matched, so every referee's phone and email in
    that same block rendered verbatim in the Appendix. Neither the
    referee's email's local part ("jreviewer") nor the phone shares any
    owner name token, and neither is one of the owner's own harvested
    contacts, so under the #920 fix's two-conjunct rule (`_shares_owner_name`
    AND a whole-segment local-part match) both are withheld even though the
    entry as a whole satisfies the first conjunct on its own. This test
    FAILS on baseline commit 82f3744 (proven by running it, unmodified,
    against a `git archive` of that commit -- see the PR reply) -- and
    would ALSO fail against the #920-round-1 fix (`b0c5d9e`) with the
    email's local part changed to a name-token substring, since that
    fix dropped the entry-level conjunct entirely rather than adding a
    second one."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": "References for Dana Example\n"
                 "Dr. Jordan Reviewer, Example State University\n"
                 "jreviewer@example-state.edu, 212-555-0199",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "jreviewer@example-state.edu" not in t["text"]
    assert "212-555-0199" not in t["text"]
    assert [i.category for i in result.withheld] == [
        CAT_THIRD_PARTY_CONTACT, CAT_THIRD_PARTY_CONTACT]


@pytest.mark.xfail(
    reason=(
        "#920 round 2 narrows the ceiling to two conjuncts but does not "
        "eliminate it: an email is wrongly spared whenever (a) its own "
        "entry already meets `_shares_owner_name`'s baseline two-token "
        "gate AND (b) its local part has a whole segment equal to an "
        "owner name token -- here 'email', harvested by "
        "`_owner_name_tokens`'s no-name-field fallback from the WHOLE "
        "first 'A' entry's raw text, which has no concept of 'label' or "
        "'domain fragment'. Real fallback token sets measured on the "
        "local corpus include `and`, `edu`, `com`, `gmail`, `email`, "
        "`phone`, `number`, `address`, `name`, `this`, `some`, `text`, "
        "`room`, `floor` -- any one of these landing as a THIRD PARTY's "
        "own local-part segment, in an entry that also shares two of the "
        "owner's fallback tokens, is spared by the same mechanism tested "
        "here. A same-surname relative is the WITH-a-name-field analogue "
        "(`_owner_name_tokens` need not fall back for the ceiling to "
        "bite). Phones are unaffected (no per-value name signal at all). "
        "Upgrade path unchanged from the #833/#920-round-1 docstrings: "
        "restrict the fallback in `_owner_name_tokens` to a leading "
        "name-shaped run."
    ),
    strict=True,
)
def test_email_local_part_sharing_a_fallback_word_as_its_own_segment_is_still_spared():
    a = {"text": "Personal Data: Name field not provided on the source "
                 "form. Email: dana.example@state.edu. Phone: 212-555-0100.",
         "taxonomy_code": "A", "extracted_fields": {}}
    t = {"text": "Personal Data forwarded here: email.desk@example-state.edu",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "email.desk@example-state.edu" not in t["text"]
    assert result.withheld != []


_FALLBACK_A_TEXT = (
    "Dana Example. Name field intentionally left blank on the source "
    "form. Email: dana@state.edu. Alternate email: dana.alt@example.com. "
    "A Gmail account is also on file. Phone Number: 212-555-0100. Home "
    "Address: 1 Example St, Room 204, Floor 3. This profile does not "
    "list some fields and the text is limited. Previously affiliated "
    "with MIT. Research area: leg biomechanics. New Haven, CT."
)


def _fallback_a_entry() -> dict:
    """An 'A' entry with NO `extracted_fields['name']`, so
    `_owner_name_tokens` falls back to tokenising this WHOLE raw text
    (#833 round 2 / #920 blocker). Deliberately carries, as ordinary
    prose or label/domain fragments and never as anyone's actual name,
    every fallback token this ticket's evidence measured on the local
    corpus (`and`, `edu`, `com`, `gmail`, `email`, `phone`, `number`,
    `address`, `name`, `this`, `some`, `text`, `room`, `floor`) plus
    `new`, `mit` and `leg`, each used below in a real-corpus-shaped
    substring-vs-whole-segment counter-example. Returns a FRESH dict each
    call -- `run_pii_pass` mutates entries in place, and this is shared
    across parametrize cases."""
    return {"text": _FALLBACK_A_TEXT, "taxonomy_code": "A",
            "extracted_fields": {}}


#: (owner 'A' entry factory, the owner's own name for a References
#: heading, the counter-example local part, a case id). Each local part
#: is a SUBSTRING of an owner token without being a whole
#: `.`/`_`/`-`/digit-delimited SEGMENT of itself -- exactly the shape
#: `_local_part_shares_owner_name`'s bare substring test wrongly spared
#: (the #920 blocker this fix closes): 'edu' inside 'eduardo', 'new'
#: inside 'newman', 'mit' inside the 'smith' half of 'york.smith', 'leg'
#: inside the 'college' half of 'college.admin2', 'lee' inside
#: 'kathleen', 'doe' inside 'doeringer', 'kim' inside the 'kimberly' half
#: of 'kimberly.jones'.
_SEGMENT_GATE_COUNTER_EXAMPLES = [
    (_fallback_a_entry, "Dana Example", "eduardo", "fallback-edu"),
    (_fallback_a_entry, "Dana Example", "newman", "fallback-new"),
    (_fallback_a_entry, "Dana Example", "york.smith", "fallback-mit"),
    (_fallback_a_entry, "Dana Example", "college.admin2", "fallback-leg"),
    (lambda: _owner_a_entry("Ann Lee", "ann.lee@example.com"), "Ann Lee",
     "kathleen", "name-lee"),
    (lambda: _owner_a_entry("Jane Doe", "jane.doe@example.com"), "Jane Doe",
     "doeringer", "name-doe"),
    (lambda: _owner_a_entry("Bo Kim", "bo.kim@example.com"), "Bo Kim",
     "kimberly.jones", "name-kim"),
]


@pytest.mark.parametrize(
    "owner_a_entry, owner_name, local_part, case_id",
    _SEGMENT_GATE_COUNTER_EXAMPLES,
    ids=[c[3] for c in _SEGMENT_GATE_COUNTER_EXAMPLES],
)
@pytest.mark.parametrize(
    "shares_name", [True, False],
    ids=["shares-name-heading", "no-owner-name-heading"])
def test_920_blocker_every_substring_counter_example_is_withheld(
    owner_a_entry, owner_name, local_part, case_id, shares_name,
):
    """#920 blocker fix, the required regression test: every one of these
    real-corpus-shaped local parts was WRONGLY SPARED by `b0c5d9e`'s bare
    substring check (`_local_part_shares_owner_name`) -- a PII regression
    against the #833 baseline (82f3744), which withheld all seven in the
    no-owner-name-heading variant (its own entry-level gate already
    refuses when the heading does not name the owner) but SPARED them
    per-entry in the shares-name-heading variant (the baseline's gate is
    entry-wide, so a name-sharing heading spares every value in that
    entry -- the #920 entry-wide leak this fix also closes). The
    two-conjunct fix (`_email_spared_by_owner_name`) withholds every one
    of them in BOTH variants: when the heading does not name the owner,
    the baseline entry-level gate alone already refuses to spare it
    (barring an incidental single-token overlap from the synthetic
    `@example.org`/`@example.com` domains sharing "example" with the
    fallback owner's own name -- never enough on its own to reach the
    two-token threshold); when it does, the whole-segment check on the
    local part is what refuses -- the SAME substring-vs-segment
    distinction that fixes the #920 blocker."""
    heading = (f"References for {owner_name}" if shares_name
               else "Please see attached documentation for details")
    t = {"text": f"{heading}\n{local_part}@example.org",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [owner_a_entry()], "T": [t]})
    assert f"{local_part}@example.org" not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


def test_920_blocker_segment_match_alone_does_not_spare_without_the_entry_gate():
    """Isolates conjunct (a) (`_shares_owner_name`) from conjunct (b): the
    referee entry below shares only ONE of Ann Lee's two name tokens by
    construction (no "Ann" anywhere, and the domain contributes neither),
    so `_shares_owner_name` refuses it -- the threshold is 2 of 2 -- even
    though the email's own local part ("lee") is an EXACT whole-segment
    match for the other token. A mutant that drops conjunct (a) and
    spares on the segment match alone wrongly spares this email."""
    a = _owner_a_entry("Ann Lee", "ann.lee@example.com")
    t = {"text": "External submission, nothing else related: lee@example.org",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "lee@example.org" not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


def test_920_blocker_segment_split_treats_a_digit_as_a_separator():
    """Isolates the segment-split regex itself (`_LOCAL_PART_SEGMENT_RE`,
    `re.split(r"[^a-z]+", ...)`, never a `.`-only split): a digit suffix
    must split off exactly like `.`/`_`/`-` do, so "kim2" reduces to the
    same segment "kim" a bare "kim" would. Bo Kim's own second address in
    this shape is spared -- unlike the `kimberly.jones` counter-example
    above, whose segments never reduce to "kim" at all -- pinning that
    digit-splitting is not itself the leak the #920 blocker closed."""
    a = _owner_a_entry("Bo Kim", "bo.kim@example.com")
    t = {"text": "References for Bo Kim\nAlternate contact: kim2@example.org",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


@pytest.mark.parametrize("local_part", [
    "editor", "office", "info", "journal", "admin", "submissions",
])
def test_generic_editorial_mailbox_is_untouched(local_part):
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    t = {"text": f"Journal of Example Studies, Editorial Board, "
                 f"{local_part}@example-journal.org",
         "taxonomy_code": "T", "extracted_fields": {}}
    before = t["text"]
    result = _run({"A": [a], "T": [t]})
    assert t["text"] == before
    assert result.withheld == []


def test_an_a_coded_entrys_own_values_are_never_withheld_by_this_rule():
    """#920 review: the explicit `code != PERSONAL_DATA_CODE` guard that
    used to keep the third-party check off code 'A' entirely is gone --
    the rule now runs against 'A' entries too. It is still always a
    no-op there, but for a different, more robust reason than the old
    per-entry name check: `_owner_contacts` harvests every email/phone
    SHAPE out of the very same 'A' entries this rule then scans, so
    whatever this entry carries is already a member of `owner` by
    construction, by the time the check runs -- including a value that
    is not really the CV owner's, as here."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    a["text"] += "\tAlso listed: Jordan Reviewer, jreviewer@example-state.edu"
    before = a["text"]
    result = _run({"A": [a]})
    assert a["text"] == before
    assert result.withheld == []


def test_a_routed_entry_with_a_third_party_email_is_untouched():
    """Scope is Appendix-only: a routed content code (here F1, Licensure)
    never runs the third-party check even when it carries someone else's
    contact."""
    a = _owner_a_entry("Dana Example", "dana.example@wcm.example.edu")
    f1 = {"text": "Reference: Dr. Jordan Reviewer, jreviewer@example-state.edu",
          "taxonomy_code": "F1", "extracted_fields": {}}
    before = f1["text"]
    result = _run({"A": [a], "F1": [f1]})
    assert f1["text"] == before
    assert result.withheld == []


def test_bare_ssn_in_an_appendix_entry_classifies_as_ssn_not_phone():
    """#833 constraint: the phone value shape must not swallow an SSN's
    3-2-4 run. No 'A' entries at all here -- the point is the shape
    distinction, not owner provenance."""
    t = {"text": "SSN: 123-45-6789", "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"T": [t]})
    assert [i.category for i in result.withheld] == [CAT_SSN]
    assert t["text"] == ""


def test_third_party_phone_shape_rejects_an_ssn_shape_outright():
    """r2 m08: the previous SSN-vs-phone test only proved `_merge_matches`
    picks the SSN row first when BOTH policy rows match -- it never
    checked that `_THIRD_PARTY_PHONE_RE`'s own shape rejects an SSN's
    3-2-4 run. Direct regex assertions, not routed through the pass, so a
    mutant that widens the phone shape's middle group (`\\d{3}` ->
    `\\d{2,3}`) is caught even if merge precedence would otherwise hide it."""
    assert _THIRD_PARTY_PHONE_RE.search("123-45-6789") is None
    m = _THIRD_PARTY_PHONE_RE.search("212-555-0100")
    assert m is not None
    assert m.group() == "212-555-0100"


def test_owner_name_tokens_drop_initials_and_honorifics():
    """r2 m14: `_MIN_OWNER_NAME_TOKEN_LEN` must actually filter out short
    tokens (initials, "Jr") before they can cheaply satisfy the
    owner-name-sharing check. Direct assertion on `_owner_name_tokens`
    pins the set itself; the entry-level assertion pins the consequence --
    an Appendix entry sharing only "Jr" and an initial (never in the
    filtered token set) with the owner is still a third party, so its
    email is cut."""
    a = {"text": "", "taxonomy_code": "A",
         "extracted_fields": {"name": "J. Q. Sampleton Jr"}}
    assert _owner_name_tokens([a]) == frozenset({"sampleton"})

    t = {"text": "J. Reviewer Jr, Example State University, "
                 "jreviewer@example-state.edu",
         "taxonomy_code": "T", "extracted_fields": {}}
    result = _run({"A": [a], "T": [t]})
    assert "jreviewer@example-state.edu" not in t["text"]
    assert [i.category for i in result.withheld] == [CAT_THIRD_PARTY_CONTACT]


# --------------------------------------------------------------------------
# 5. Bare-label span extension (#821 R3 F-B)
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
