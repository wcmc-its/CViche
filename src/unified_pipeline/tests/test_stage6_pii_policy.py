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
import time
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
    CAT_INSTITUTIONAL_ID,
    CAT_MARITAL_STATUS,
    CAT_PASSPORT,
    CAT_PLACE_OF_BIRTH,
    CAT_RELIGION,
    CAT_SALARY,
    CAT_SPOUSE,
    CAT_SSN,
    CAT_TAX_ID,
    CAT_THIRD_PARTY_CONTACT,
    CAT_VETERAN,
    CAT_VISA,
    DECIDED_820,
    DECIDED_821,
    DECIDED_821_PENDING,
    DECIDED_ID_NUMBERS,
    PRE_LLM_PLACEHOLDER,
    SCOPE_ALL_CODES,
    SCOPE_PERSONAL_AND_APPENDIX,
    WITHHOLD_POLICY,
    WithheldItem,
    _pii_fragments,
    _pii_matches,
    pre_llm_bare_label_category,
    redact_pre_llm_value_of_category,
    redact_pre_llm_values,
)
from unified_pipeline.stage6.pii_pass import (  # noqa: E402
    _THIRD_PARTY_PHONE_RE,
    APPENDIX_SECTION_LABEL,
    WITHHELD_COMMENT_FOOTER,
    WITHHELD_COMMENT_HEADER,
    _entry_scope,
    _owner_name_tokens,
    relocate_withheld,
    run_pii_pass,
    withheld_comment_text,
)
from unified_pipeline.stage6.sections.licensure import (  # noqa: E402
    _resolve_licensure,
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


def _probe_matches(text: str, code: str):
    """The matches `run_pii_pass` finds in an entry of `code`: the same scope,
    and the Personal Data block's own dash-label rule for an A-coded entry."""
    return _pii_matches(text, _entry_scope(code, RENDER_ROUTED_CODES), personal_data=code == A)


def _denied(text: str, code: str) -> bool:
    return bool(_probe_matches(text, code))


# --------------------------------------------------------------------------
# 1. The probe table
#
# (text, category, {destination: expected}). `category` is what the pass
# must report; None for a negative control.
# --------------------------------------------------------------------------

_ALL = {A: DENIED, APPENDIX: DENIED, CONTENT: DENIED}
_PERSONAL_ONLY = {A: DENIED, APPENDIX: DENIED, CONTENT: ALLOWED}
_NEVER = {A: ALLOWED, APPENDIX: ALLOWED, CONTENT: ALLOWED}
#: A dash family label with no `Family` label ahead of it (#1223): withheld in
#: the Personal Data block only.
_DASH_PERSONAL_DATA_ONLY = {A: DENIED, APPENDIX: ALLOWED, CONTENT: ALLOWED}

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
    # #847 residual round 3: "Date and Place of Birth" combines both labels
    # in a word order neither existing alternative (which only combines
    # "Birth Date and Birth Place"/"Birthdate and Birthplace") matched.
    ("Date and Place of Birth: 01/02/1970, Example City", CAT_DATE_OF_BIRTH, _ALL),
    # #1071: a parenthetical between the DOB stem and its colon -- the
    # value itself (nothing after the colon) or a format hint.
    ("Birth Date (01/02/1970):", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth (01/02/1970):", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth (mm/dd/yyyy): 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Birth-date (January 2, 1970):", CAT_DATE_OF_BIRTH, _ALL),
    # ...bounded at 40 characters (`_DOB_PAREN_MAX`): a longer
    # parenthetical, or one with no colon after it, opens no label.
    ("Date of Birth (" + "x" * 40 + "): 01/02/1970", CAT_DATE_OF_BIRTH, _ALL),
    ("Date of Birth (" + "x" * 41 + "): 01/02/1970", None, _NEVER),
    ("Date of birth (an example study) and outcomes", None, _NEVER),
    # ...and one parenthesis pair on one line: it never nests, never closes
    # on a later unrelated ")", never spans a newline.
    ("Date of birth (cohort A (n=40): outcomes", None, _NEVER),
    ("Date of birth (cohort A) by site B): outcomes", None, _NEVER),
    ("Date of birth (see\nnote): outcomes", None, _NEVER),
    # The combined label keeps its own alternative (no parenthetical).
    ("Birth Date and Birth Place: 01/02/1970, Example City", CAT_DATE_OF_BIRTH, _ALL),
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
    # #1071: the combined race-and-ethnicity label (a bare "Race:" stays a
    # negative control below).
    ("Race/Ethnicity: Example", CAT_ETHNICITY, _PERSONAL_ONLY),
    ("Race / Ethnicity: Example", CAT_ETHNICITY, _PERSONAL_ONLY),
    ("Race and Ethnicity: Example", CAT_ETHNICITY, _PERSONAL_ONLY),
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
    # #1103: a label that opens with "Name of" or joins spouse and children.
    ("Name of Spouse & Children:  Pat Example, Kim (1990), Lee (1992)", CAT_SPOUSE, _ALL),
    ("Name of Spouse: Pat Example", CAT_SPOUSE, _ALL),
    ("Spouse and Children: Pat Example; Kim, Lee", CAT_SPOUSE, _ALL),
    ("Wife/Children: Pat Example, Kim", CAT_SPOUSE, _ALL),
    ("Names of Children: Kim (1990), Lee (1992)", CAT_CHILDREN, _PERSONAL_ONLY),
    # #1223: an A-coded entry is the Personal Data block, which holds no
    # titles, so this #473 control is withheld there only
    # (`_DASH_PERSONAL_DATA_ONLY`).
    ("Name of Spouse \u2013 A Documentary Film Review", CAT_SPOUSE, _DASH_PERSONAL_DATA_ONLY),
    # #1223: family prose with no label, and the wider family label.
    ("Married (Pat), two children (Kim and Lee)", CAT_SPOUSE, _PERSONAL_ONLY),
    ("2 children (Kim and Lee)", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Kim born [withheld]", CAT_BIRTH, _PERSONAL_ONLY),
    ("Family Information: Pat Example", CAT_FAMILY, _PERSONAL_ONLY),
    ("Family Data: Pat Example", CAT_FAMILY, _PERSONAL_ONLY),
    # ...whose negative controls: the same words inside a sentence or a title.
    ("Married couples (n=40) in clinical trials", None, _NEVER),
    ("A method married (loosely) to practice", None, _NEVER),
    ("Three children's hospitals (a survey)", None, _NEVER),
    ("the clinic, born 1970, was founded", None, _NEVER),
    ("Family Medicine: A Review", None, _NEVER),
    # The dash form is accepted under a `Family` label at A and T, and with no
    # label at all in an A-coded entry (the Personal Data block holds no
    # titles). So these title-shaped controls are withheld at A, and stay
    # clean at T and in a routed entry (see the #1223 section below).
    ("Children - A Review of the Literature", CAT_CHILDREN, _DASH_PERSONAL_DATA_ONLY),
    ("Spouse- A Documentary Film Review", CAT_SPOUSE, _DASH_PERSONAL_DATA_ONLY),
    ("Family- wise error rate in gene association studies", CAT_FAMILY, _DASH_PERSONAL_DATA_ONLY),
    # A spelled-out child count, with no spouse span ahead of it to cover it.
    ("two children (Kim and Lee)", CAT_CHILDREN, _PERSONAL_ONLY),
    ("three sons (Bob, Cy and Dee)", CAT_CHILDREN, _PERSONAL_ONLY),
    # #1223 (EBYSBC): the "Married:" and "Grandchildren" labels, colon form in
    # Personal Data and the Appendix, dash form in the Personal Data block.
    ("Married: Pat Example, Ed.D.", CAT_SPOUSE, _PERSONAL_ONLY),
    ("Grandchildren: Kim, Lee", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Great-grandchildren: Kim", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Great Grandchildren: Kim", CAT_CHILDREN, _PERSONAL_ONLY),
    ("Grandchildren – Kim Example, Lee, Bob, Cy", CAT_CHILDREN, _DASH_PERSONAL_DATA_ONLY),
    ("Married - Pat Example", CAT_SPOUSE, _DASH_PERSONAL_DATA_ONLY),
    # ...whose negative controls: the words inside another word or a sentence.
    ("Unmarried: a cohort study", None, _NEVER),
    ("Newly married: outcomes of a survey", None, _NEVER),
    ("Grandchildren raising grandparents", None, _NEVER),
    ("Married couples: a review", None, _NEVER),
    # NDMRSO ND1: an institutional or tax ID number in every code, like a
    # passport number (the full set is `_ND1_ID_LINES` below).
    ("UFID #: 1234-5678", CAT_INSTITUTIONAL_ID, _ALL),
    ("EIN Number\t12-345-6789", CAT_TAX_ID, _ALL),
    ("NPI: 1234567890", None, _NEVER),
    ("ORCID: 0000-0002-1234-5678", None, _NEVER),
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
        assert [m.category for m in _probe_matches(text, dest)] == [category]


def test_every_policy_row_has_a_probe_that_reaches_it():
    """A dropped or renamed policy row must fail a probe, so every category
    in the table is exercised by at least one denied row above."""
    probed = {category for _, category, _ in PROBE_TABLE if category}
    for rule in WITHHOLD_POLICY:
        assert rule.category in probed, f"policy row {rule.category!r} has no probe"


def test_every_policy_row_carries_a_scope_and_a_decision():
    for rule in WITHHOLD_POLICY:
        assert rule.scope in (SCOPE_ALL_CODES, SCOPE_PERSONAL_AND_APPENDIX), rule
        assert rule.decided_by in (DECIDED_820, DECIDED_821_PENDING, DECIDED_821,
                                    DECIDED_ID_NUMBERS), rule
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


def test_dash_preceded_dob_label_is_now_caught():
    """#847 residual: a name-then-label form ("Jane Doe - DOB: ...", a
    per-child line in a Family/Children block) was refused by the `,:(`
    boundary rule. An explicit DOB label with a whole date after it is now
    accepted after any prefix (`_explicit_dob_label`)."""
    assert _denied("Jane Doe - DOB: 01/02/2010", A)
    assert _denied("Jane Doe – Date of Birth: 01/02/2010", A)


def test_dash_preceded_boundary_stays_refused_for_other_categories():
    """Not a blanket boundary change: an unrelated category (marital status
    here) still refuses a dash-preceded label exactly as before."""
    assert not _denied("Research interests - Marital Status: Single", A)


# #1041: one label per dash-terminated category. A value that is not
# SSN/date shaped, so only the label row (never a colonless shape row)
# can be what matches.
_DASH_LABELS = [
    ("Date of Birth", CAT_DATE_OF_BIRTH),
    ("Birthplace", CAT_PLACE_OF_BIRTH),
    ("Place of Birth", CAT_PLACE_OF_BIRTH),
    ("SSN", CAT_SSN),
    ("Passport Number", CAT_PASSPORT),
    ("Alien Registration Number", CAT_ALIEN_REGISTRATION),
    ("Driver's License", CAT_DRIVERS_LICENSE),
    ("Marital Status", CAT_MARITAL_STATUS),
    ("Emergency Contact", CAT_EMERGENCY_CONTACT),
    ("Visa Status", CAT_VISA),
    ("Immigration Status", CAT_VISA),
]
_DASHES = ["-", " -", "\u2013", " \u2013", "\u2014", " \u2014"]


@pytest.mark.parametrize("dash", _DASHES)
@pytest.mark.parametrize("label, category", _DASH_LABELS)
def test_1041_dash_terminated_label_is_withheld_at_every_destination(dash, label, category):
    """#1041: a label closed by a hyphen / en dash / em dash, then
    whitespace, is withheld at every destination, like its colon form."""
    text = f"{label}{dash} Synthetic Value"
    for code in (A, APPENDIX, CONTENT):
        assert _denied(text, code), code
    assert [m.category for m in _pii_matches(text, SCOPE_ALL_CODES)] == [category]


# The whitespace after the dash, as the corpus has it: XY66RT's lines
# carry 5-11 spaces, a hard `_PII_FRAGMENT_SPLIT_RE` delimiter, so the
# match is the label alone and the VALUE is cut only by the pass's
# bare-label extension. A tab is the same shape.
_DASH_GAPS = ["-      ", " -     ", "\u2013\t", "\u2014\t", "-\t"]


@pytest.mark.parametrize("gap", _DASH_GAPS)
@pytest.mark.parametrize("label, category", _DASH_LABELS)
def test_1041_pass_cuts_the_value_after_a_gapped_dash_label(gap, label, category):
    entry = {"text": f"{label}{gap}Synthetic Value", "taxonomy_code": "T",
             "extracted_fields": {}}
    result = _run({"T": [entry]})
    assert "Synthetic" not in entry["text"], entry["text"]
    assert [i.category for i in result.withheld] == [category]
    assert entry["_pii_orphaned_value"] is False


@pytest.mark.parametrize("terminator", [":", " -", "\u2013", " \u2014"])
def test_1041_dash_label_before_a_newline_orphans_its_value_like_a_colon(terminator):
    """F2: the value on the next line is not reached by the extension (a
    newline is the source's own field separator), so the entry is flagged
    `_pii_orphaned_value` -- the dash form exactly as the colon form."""
    entry = {"text": f"Marital Status{terminator}\nSynthetic", "taxonomy_code": "T",
             "extracted_fields": {}}
    _run({"T": [entry]})
    assert entry["_pii_orphaned_value"] is True


@pytest.mark.parametrize("terminator", [":", " -", "-", "\u2013", " \u2014"])
def test_1041_dash_label_alone_at_the_end_of_a_cell_matches_like_a_colon(terminator):
    text = f"Marital Status{terminator}"
    assert [m.category for m in _pii_matches(text, SCOPE_ALL_CODES)] == [CAT_MARITAL_STATUS]
    assert pre_llm_bare_label_category(f"Date of Birth{terminator}") == CAT_DATE_OF_BIRTH


@pytest.mark.parametrize("gap", _DASH_GAPS)
def test_1041_pre_llm_scrub_reaches_a_date_after_a_gapped_dash_label(gap):
    text = f"Date of Birth{gap}01/02/1970"
    assert "01/02/1970" not in redact_pre_llm_values(text)


@pytest.mark.parametrize("text, value", [
    # No colonless shape row reaches these: only the next-run extension
    # (`_pre_llm_value_span_in_next_run`) behind a dash-terminated label.
    ("Date of Birth - | 01/02/1970", "01/02/1970"),
    ("Year of Birth -\t1970", "1970"),
    ("Birthday \u2013\t01/02/1970", "01/02/1970"),
])
def test_1041_pre_llm_scrub_takes_the_next_run_after_a_bare_dash_label(text, value):
    assert value not in redact_pre_llm_values(text)


def test_1041_bare_child_count_behind_a_marital_cut_is_withheld():
    """XY66RT's line: the `;` hard split left "<n> Children" behind the
    marital-status cut and it rendered in the Appendix."""
    entry = {"text": "Marital Status-     Synthetic; 2 Children", "taxonomy_code": "T",
             "extracted_fields": {}}
    result = _run({"T": [entry]})
    assert "Synthetic" not in entry["text"] and "Children" not in entry["text"]
    assert [i.category for i in result.withheld] == [CAT_MARITAL_STATUS, CAT_CHILDREN]


def test_1041_content_after_a_dash_label_with_its_value_survives():
    """A dash label that CARRIES its value is not bare: the cut stops at the
    label's own fragment, and a later unrelated fragment survives."""
    entry = {"text": "Marital Status - Zqv; Board Certified Internal Medicine",
             "taxonomy_code": "T", "extracted_fields": {}}
    _run({"T": [entry]})
    assert entry["text"] == "; Board Certified Internal Medicine"


@pytest.mark.parametrize("text", [
    "Status; 2 sons",
    "Status; 1 daughter",
    "Status; 3 kids",
    "Status; 2 children.",
    "Status\n1 child",
    "Status   2 children",
    "2 children   Board Certified",
    "Status | 2 children | Board Certified",
])
def test_1041_child_count_fragment_is_withheld_at_personal_and_appendix(text):
    for code in (A, APPENDIX):
        assert _denied(text, code), code


def test_1041_child_count_row_does_not_reach_a_content_code():
    """The row is PERSONAL_AND_APPENDIX only: a count in a research or
    teaching entry is study data, not the owner's family."""
    text = "Enrollment\t40 children\tNIH R01"
    assert not _denied(text, CONTENT)
    entry = {"text": text, "taxonomy_code": CONTENT, "extracted_fields": {}}
    _run({CONTENT: [entry]})
    assert entry["text"] == text


@pytest.mark.parametrize("text", [
    "Enrolled 20 children and 20 adults",
    "Studied 20 subjects, 20 children",
    "Outcomes in 4 children",
    "Single (2 children) is not a fragment of its own",
    "Cohort A; 20 children with asthma",
    "Board Certified   20 children treated",
    "Board Certified, 2 children",
    "Board Certified 2 children",
])
def test_1041_child_count_inside_prose_is_not_withheld(text):
    assert not [m for m in _pii_matches(text) if m.category == CAT_CHILDREN]


@pytest.mark.parametrize("text", [
    # The corpus false withholds a dash terminator on the ambiguous
    # title-word rows produced (#1041 A/B), synthetic stand-ins.
    "Health- Example Institute, Springfield",
    "Age-related differences in memory",
    "Age- and sex-specific norms",
    "Gender- and Race-Based Disparities",
    "Family-centered health promotion",
    "Sexuality and Health – Volume 3",
    # An unambiguous label glued to a compound word: no whitespace after
    # the hyphen, so not a terminator.
    "Salary-based compensation study",
    "Visa-free travel policy review",
    "Visa\u2014free travel policy review",
    "Visa\u2013sponsored scholars program",
    # Rows left colon-only: their labels open real titles.
    "Honorarium - Grand Rounds lecture",
])
def test_1041_dash_joined_non_pii_line_is_not_newly_withheld(text):
    assert not _denied(text, A)
    assert not _denied(text, APPENDIX)


def test_1041_the_spouse_title_control_is_withheld_only_in_the_personal_data_block():
    """#1223 took the dash form of a children / spouse / family label in an
    A-coded entry (the Personal Data block, which holds no titles), so this
    #1041 control is withheld there. In an Appendix-bound entry, which can
    hold a title, it still needs a `Family` label ahead of it."""
    text = "Spouse \u2013 A Documentary Film Review"
    assert _denied(text, A)
    assert not _denied(text, APPENDIX)
    assert not _denied(text, CONTENT)


@pytest.mark.parametrize("label", [
    "Religion", "Home Address", "Home Phone", "DEA", "Spouse", "Salary", "Honorarium",
    "Gender", "Age", "Health", "Family", "Children", "Ethnicity",
])
@pytest.mark.parametrize("dash", [" - ", " \u2013 ", " \u2014 "])
def test_1041_colon_only_category_does_not_close_on_a_dash(label, dash):
    """The dash set is opt-in: a row outside `_DASH_TERMINATED_CATEGORIES`
    must keep needing its colon, so the set cannot silently widen."""
    assert _pii_matches(f"{label}{dash}Synthetic Value") == []


@pytest.mark.parametrize("text, code", [
    ("Marital Status: Single", CONTENT),
    ("Birthplace: Synthetic City", CONTENT),
    ("Visa Status: Synthetic", CONTENT),
    ("Health: good", A),
    ("Age: 45", A),
    ("Gender: X", A),
])
def test_1041_colon_terminated_labels_unchanged(text, code):
    assert _denied(text, code)


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


_O1_HONOR = {
    # web26 (#892): the stage-4 record a field-first renderer prints from.
    "award_name": "Extraordinary Ability in Sciences, O-1 Visa",
    "granting_body": "U.S. Citizen & Immigration Service (USCIS)",
    "date": "2019",
}


def test_892_pass_drops_a_pii_value_under_a_non_pii_key_and_counts_it_once():
    """The text carries the same visa phrase, so the notice is recorded
    once (from text), not a second time for the field."""
    entry = {"text": "2019 Extraordinary Ability in Sciences, O-1 Visa | USCIS",
             "taxonomy_code": "H", "extracted_fields": dict(_O1_HONOR)}
    result = _run({"H": [entry]})
    assert "award_name" not in entry["extracted_fields"]
    assert "O-1" not in str(entry["extracted_fields"])
    assert "O-1" not in entry["text"]
    assert entry["_pii_withheld"] is True
    assert result.withheld == [WithheldItem(CAT_VISA, "Honors", 0)]


def test_892_field_value_alone_triggers_the_pass_and_is_recorded():
    """No match in `text`: the field value is the only place the visa is."""
    entry = {"text": "Award", "taxonomy_code": "H",
             "extracted_fields": dict(_O1_HONOR)}
    result = _run({"H": [entry]})
    assert "award_name" not in entry["extracted_fields"]
    assert entry["_pii_withheld"] is True
    assert entry["_pii_dropped_fields"] == ["award_name"]
    assert result.withheld == [WithheldItem(CAT_VISA, "Honors", 0)]


def test_892_non_pii_field_values_are_untouched():
    """Negative control: same shape, nothing protected -- the entry is left
    completely alone (same dict, no bookkeeping keys)."""
    fields = {"award_name": "Distinguished Teaching Award",
              "granting_body": "Example University", "date": "2019"}
    entry = {"text": "2019 Distinguished Teaching Award | Example University",
             "taxonomy_code": "H", "extracted_fields": fields}
    before = {**entry, "extracted_fields": dict(fields)}
    result = _run({"H": [entry]})
    assert entry == before
    assert entry["extracted_fields"] is fields
    assert result.withheld == []


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
    assert lines[1] == " • date of birth (1 item, Personal Data)"
    assert lines[2] == " • visa / immigration status (2 items, Appendix, Licensure)"
    assert lines[3] == WITHHELD_COMMENT_FOOTER
    assert len(lines) == 4


def test_relocate_withheld_renames_only_the_listed_entries():
    """#848: items of an entry whose residual rendered in the Appendix are
    renamed; other entries and docx-recovered items (index None) are not."""
    items = [
        WithheldItem(CAT_DATE_OF_BIRTH, "Personal Data", 0),
        WithheldItem(CAT_VISA, "Personal Data", 1),
        WithheldItem(CAT_VISA, "Personal Data", None),
    ]
    assert relocate_withheld(items, {1}) == [
        items[0], WithheldItem(CAT_VISA, "Appendix", 1), items[2]]
    assert relocate_withheld(items, set()) == items


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
    assert f" • {CAT_THIRD_PARTY_CONTACT} (1 item, {APPENDIX_SECTION_LABEL})" in text


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
    "A Gmail account is also on file. Phone Number: 212-555-0100. Mailing "
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


@pytest.mark.parametrize("text, expected", [
    # #1071: the value sits INSIDE the label's parentheses.
    ("Birth Date (01/02/1970):", f"Birth Date ({PRE_LLM_PLACEHOLDER}):"),
    ("Date of Birth (01/02/1970):", f"Date of Birth ({PRE_LLM_PLACEHOLDER}):"),
    # A format hint in the parentheses is not a value; the date after the
    # colon is.
    ("Date of Birth (mm/dd/yyyy): 01/02/1970",
     f"Date of Birth (mm/dd/yyyy): {PRE_LLM_PLACEHOLDER}"),
    ("Date of Birth (mm/dd/yyyy):\t01/02/1970",
     f"Date of Birth (mm/dd/yyyy):\t{PRE_LLM_PLACEHOLDER}"),
    # A race/ethnicity value is render-time only (#847 scrubs DOB/SSN).
    ("Race/Ethnicity: Example", "Race/Ethnicity: Example"),
])
def test_1071_pre_llm_scrub_reaches_a_dob_label_parenthetical(text, expected):
    assert redact_pre_llm_values(text) == expected


def test_1071_bare_dob_label_with_a_format_hint_names_its_category():
    """A label cell alone ("Date of Birth (mm/dd/yyyy):") hands its category
    to the next cell's scrub; one whose parentheses hold the value does not
    -- its own text is scrubbed instead."""
    assert pre_llm_bare_label_category("Date of Birth (mm/dd/yyyy):") == CAT_DATE_OF_BIRTH
    assert pre_llm_bare_label_category("Birth Date (01/02/1970):") is None


@pytest.mark.parametrize("text", [
    "Jane Example Date of Birth (mm/dd/yyyy): 01/02/1970",
    "Jane Example Birth Date (MM/DD/YYYY): 01/02/1970",
    "Jane Example Birth Date and Birth Place: 01/02/1970",
])
def test_1071_explicit_dob_label_with_a_parenthetical_skips_the_boundary_rule(text):
    """#847 residual: an explicit DOB label then a whole date is a DOB
    whatever precedes it -- with a parenthetical in the label too, and
    for the combined birth date and birth place label."""
    assert [m.category for m in _pii_matches(text, SCOPE_ALL_CODES)] == [CAT_DATE_OF_BIRTH]
    assert redact_pre_llm_values(text).endswith(f": {PRE_LLM_PLACEHOLDER}")


@pytest.mark.parametrize("text", [
    "Date of Birth (" * 20_000,
    "Birth Date (" + "a" * 200_000,
    "Birth Date " + "(" * 200_000,
    "Race / " * 20_000 + "Ethnicity",
])
def test_1071_dob_parenthetical_scan_stays_linear_on_adversarial_input(text):
    """The parenthetical is one bounded class, no nested quantifier: each
    of these took well under a second when written (~0.1s); a
    backtracking blow-up would not finish at all."""
    started = time.perf_counter()
    _pii_matches(text)
    redact_pre_llm_values(text)
    assert time.perf_counter() - started < 5.0


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


# --------------------------------------------------------------------------
# round 2 (#847): value after a hard delimiter, whole-date shapes, and
# category-not-scope selection ("Born: ..." is SCOPE_PERSONAL_AND_APPENDIX,
# not SCOPE_ALL_CODES -- scope is a render-time routing concept and there
# is no taxonomy code yet at the point this scrub runs).
# --------------------------------------------------------------------------

def test_redact_pre_llm_values_value_after_a_tab_is_scrubbed():
    out = redact_pre_llm_values("Date of Birth:\t01/02/1970")
    assert out == f"Date of Birth:\t{PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_value_after_three_plus_spaces_is_scrubbed():
    out = redact_pre_llm_values("Date of Birth:    01/02/1970")
    assert out == f"Date of Birth:    {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_value_after_a_pipe_is_scrubbed():
    out = redact_pre_llm_values("Date of Birth: | 01/02/1970")
    assert out == f"Date of Birth: | {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_never_extends_across_a_newline():
    # The label's own line has nothing after it -- a value on the NEXT
    # line is a different field and must not be pulled across.
    out = redact_pre_llm_values("Date of Birth:\nSSN: 123-45-6789")
    assert out == f"Date of Birth:\nSSN: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_iso_date_takes_the_whole_value():
    out = redact_pre_llm_values("Date of Birth: 1970-01-02")
    assert out == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_dotted_date_takes_the_whole_value():
    out = redact_pre_llm_values("Date of Birth: 12.03.1970")
    assert out == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"


# #847: an ordinal day ("1st", "22nd") matched no full-date shape, so only the
# year was withheld and the month and day reached the LLM (run 6TJNBQ).
@pytest.mark.parametrize("value", [
    "April 1st, 1990", "April 22nd, 1990", "Apr 3rd 1990", "April 4th, 1990",
    "1st April 1990", "21st of April, 1990",
])
def test_redact_pre_llm_values_ordinal_day_takes_the_whole_value(value):
    out = redact_pre_llm_values(f"Date of Birth: {value}")
    assert out == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_value_of_category_ordinal_day_in_a_value_cell():
    # The "Birth date:" | "May 2nd, 1985" table-row shape from 6TJNBQ.
    for cross_boundary in (False, True):
        out = redact_pre_llm_value_of_category(
            "April 1st, 1990", CAT_DATE_OF_BIRTH, cross_boundary=cross_boundary)
        assert out == PRE_LLM_PLACEHOLDER


def test_redact_pre_llm_values_untouched_ordinal_date_with_no_dob_label():
    text = "Presented at the 1st Annual Meeting, April 2nd, 2019."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_untouched_iso_date_publication_no_dob_label():
    text = "Published 2020-05-01 in Journal X."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_born_colon_is_scrubbed_regardless_of_scope():
    # "Born:" is CAT_BIRTH, SCOPE_PERSONAL_AND_APPENDIX -- excluded by the
    # round-1 SCOPE_ALL_CODES filter. Pre-LLM there is no taxonomy code to
    # route by, so category alone decides.
    out = redact_pre_llm_values("Born: 01/02/1970")
    assert out == f"Born: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_untouched_born_with_no_colon_or_date():
    text = "Born in New York, he later trained as a surgeon."
    assert redact_pre_llm_values(text) == text


# --------------------------------------------------------------------------
# pre_llm_bare_label_category / redact_pre_llm_value_of_category -- the
# label-cell / value-cell table fix (round 2, #847).
# --------------------------------------------------------------------------

def test_pre_llm_bare_label_category_detects_a_whole_cell_dob_label():
    assert pre_llm_bare_label_category("Date of Birth:") == CAT_DATE_OF_BIRTH


def test_pre_llm_bare_label_category_detects_a_whole_cell_ssn_label():
    assert pre_llm_bare_label_category("SSN:") == CAT_SSN


def test_pre_llm_bare_label_category_none_for_an_ordinary_cell():
    assert pre_llm_bare_label_category("Notes") is None


def test_pre_llm_bare_label_category_none_when_the_cell_already_has_a_value():
    # Whole match already covers the value -- nothing "bare" about it.
    assert pre_llm_bare_label_category("Date of Birth: 01/02/1970") is None


def test_redact_pre_llm_value_of_category_replaces_the_value_cell():
    out = redact_pre_llm_value_of_category("01/02/1970", CAT_DATE_OF_BIRTH)
    assert out == PRE_LLM_PLACEHOLDER


def test_redact_pre_llm_value_of_category_untouched_when_no_shape_matches():
    text = "Notes"
    assert redact_pre_llm_value_of_category(text, CAT_DATE_OF_BIRTH) == text


# --------------------------------------------------------------------------
# #847 residual: a child's date under a "Children:"/"Dependents:" label with
# no DOB sub-label of its own. Matches CAT_CHILDREN, not CAT_DATE_OF_BIRTH/
# CAT_BIRTH, so `_child_list_date_spans` handles it -- the render-time
# policy row is untouched (still SCOPE_PERSONAL_AND_APPENDIX, still denies
# the whole fragment as before).
# --------------------------------------------------------------------------

def test_redact_pre_llm_values_dob_under_a_children_label_with_no_dob_sub_label():
    out = redact_pre_llm_values("Children: Jane (01/02/2010)")
    assert out == f"Children: Jane ({PRE_LLM_PLACEHOLDER})"


def test_redact_pre_llm_values_dob_under_a_dependents_label():
    out = redact_pre_llm_values("Dependents: Ann, born 01/02/2010")
    assert out == f"Dependents: Ann, born {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_children_label_with_no_date_is_untouched():
    text = "Children: Ann, Bob"
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_children_label_bare_year_is_untouched():
    # Corpus blast-radius finding: a "Children:" PREFIX on a book/article
    # title ("Children: Research, Practice and Policy...", the module's own
    # #473 negative control) can end in a bare year that is a publication
    # year, not a birth year -- the corpus run caught this live, twice, in
    # a real CV. A bare year alone is not distinctive enough to AND against
    # the false positive; only a full date in a child item is (`_CHILD_ITEM_RE`).
    text = "Children: A Study of Early Intervention, City Press, 2015."
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_children_render_policy_row_unchanged():
    # This round-3 fix only widens the PRE-LLM value scrub -- the render-time
    # policy row for CAT_CHILDREN keeps its own scope and still denies the
    # whole fragment at render time exactly as before #847.
    assert _denied("Children: Ann, Bob", A)
    assert not _denied("Children: Ann, Bob", "S4")


# --------------------------------------------------------------------------
# #847 residual: a Children label naming more than one child's date takes
# every child item's date, not just the first.
# --------------------------------------------------------------------------

def test_redact_pre_llm_values_children_label_withholds_every_full_date():
    text = "Children: Ann (01/02/2010), Bob (03/04/2012), Cy (05/06/2014)"
    out = redact_pre_llm_values(text)
    assert out == (
        f"Children: Ann ({PRE_LLM_PLACEHOLDER}), "
        f"Bob ({PRE_LLM_PLACEHOLDER}), Cy ({PRE_LLM_PLACEHOLDER})"
    )
    assert not any(c.isdigit() for c in out)


def test_redact_pre_llm_values_dob_label_still_takes_only_one_value():
    # A DOB/SSN label span carries exactly one value -- the child-list
    # rule above must not touch this existing single-value shape.
    out = redact_pre_llm_values("Date of Birth: 01/02/1970")
    assert out == f"Date of Birth: {PRE_LLM_PLACEHOLDER}"


def test_redact_pre_llm_values_children_label_withholds_every_date_across_tabs():
    # A tab-separated child list: each child's date sits in its own
    # tab-delimited run, past the label's own fragment.
    text = "Children:\tAnn (01/02/2010)\tBob (03/04/2012)\tCy (05/06/2014)"
    out = redact_pre_llm_values(text)
    assert out == (
        f"Children:\tAnn ({PRE_LLM_PLACEHOLDER})\t"
        f"Bob ({PRE_LLM_PLACEHOLDER})\tCy ({PRE_LLM_PLACEHOLDER})"
    )
    assert not any(c.isdigit() for c in out)


def test_redact_pre_llm_values_children_label_withholds_every_date_after_a_column_gap():
    # A 3+-space column gap after the label, then a comma-separated list
    # of children on one line.
    text = "Children:       Ann (01/02/2010), Bob (03/04/2012), Cy (05/06/2014)"
    out = redact_pre_llm_values(text)
    assert out == (
        f"Children:       Ann ({PRE_LLM_PLACEHOLDER}), "
        f"Bob ({PRE_LLM_PLACEHOLDER}), Cy ({PRE_LLM_PLACEHOLDER})"
    )
    assert not any(c.isdigit() for c in out)


def test_redact_pre_llm_values_next_run_scrub_stops_at_a_newline():
    # Negative control: a value on the NEXT LINE (not a same-line hard
    # delimiter) is a different field and must not be pulled in, same
    # guarantee as the existing never_extends_across_a_newline test above.
    out = redact_pre_llm_values("Date of Birth:\nSSN: 123-45-6789")
    assert out == f"Date of Birth:\nSSN: {PRE_LLM_PLACEHOLDER}"


# --------------------------------------------------------------------------
# #847 residual: redact_pre_llm_value_of_category(cross_boundary=True) -- for
# a value at a lower-confidence position (a cell below a label, the next
# paragraph in the stream) the value must OPEN the text and, for a DOB, be
# a whole date. A bare year, or a date after other text, is left alone.
# --------------------------------------------------------------------------

def test_redact_pre_llm_value_of_category_cross_boundary_still_replaces_a_full_date():
    out = redact_pre_llm_value_of_category("01/02/1970", CAT_DATE_OF_BIRTH, cross_boundary=True)
    assert out == PRE_LLM_PLACEHOLDER


def test_redact_pre_llm_value_of_category_cross_boundary_takes_a_date_after_leading_whitespace():
    out = redact_pre_llm_value_of_category("  01/02/1970", CAT_DATE_OF_BIRTH, cross_boundary=True)
    assert out == f"  {PRE_LLM_PLACEHOLDER}"


@pytest.mark.parametrize("text", [
    "2001",                                  # bare year: not a whole date
    "1990-1994 BA, Example College",         # bare year opening a range
    "Appointed 07/01/2005",                  # whole date, but not opening
    "Date of Appointment: 07/01/2005",       # its own label opens the text
])
def test_redact_pre_llm_value_of_category_cross_boundary_leaves_it_alone(text):
    assert redact_pre_llm_value_of_category(text, CAT_DATE_OF_BIRTH, cross_boundary=True) == text


def test_redact_pre_llm_value_of_category_default_still_takes_a_bare_year():
    # The default (same-row/same-cell) path is unchanged -- only the
    # cross-boundary callers get the narrower match.
    out = redact_pre_llm_value_of_category("2001", CAT_DATE_OF_BIRTH)
    assert out == PRE_LLM_PLACEHOLDER


# --------------------------------------------------------------------------
# #847 residual, blind-verifier round 2: precision. A DOB/SSN label takes
# ONE value; a Children label takes only dates inside a child-shaped list;
# the next-run fallback never reaches into a run that carries its own label.
# Each expected string is what origin/dev's scrub produces for the same text.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, dev_output", [
    ("Date of Birth:\t01/02/1970\tDate of Appointment:\t07/01/2005",
     f"Date of Birth:\t{PRE_LLM_PLACEHOLDER}\tDate of Appointment:\t07/01/2005"),
    ("Date of Birth: 01/02/1970, Appointed Assistant Professor 2005",
     f"Date of Birth: {PRE_LLM_PLACEHOLDER}, Appointed Assistant Professor 2005"),
    ("Born:\t1970\tMD\t1996", f"Born:\t{PRE_LLM_PLACEHOLDER}\tMD\t1996"),
    ("Date of birth: January 1, 1970, US citizen since 1990, married 1995",
     f"Date of birth: {PRE_LLM_PLACEHOLDER}, US citizen since 1990, married 1995"),
    ("Children: Research, Practice and Policy. Oxford Press, 03/15/2019",
     "Children: Research, Practice and Policy. Oxford Press, 03/15/2019"),
])
def test_redact_pre_llm_values_keeps_non_dob_dates_as_dev_does(text, dev_output):
    assert redact_pre_llm_values(text) == dev_output


def test_redact_pre_llm_values_next_run_with_its_own_label_is_left_alone():
    # The DOB label's own value is blank; the next run is a different field.
    text = "Date of Birth:\tDate of Appointment: 07/01/2005"
    assert redact_pre_llm_values(text) == text


def test_redact_pre_llm_values_child_list_stops_at_a_non_child_item():
    text = "Children: Ann (01/02/2010), see Annual Report 03/04/2012"
    assert redact_pre_llm_values(text) == f"Children: Ann ({PRE_LLM_PLACEHOLDER}), see Annual Report 03/04/2012"


def test_redact_pre_llm_values_child_list_takes_a_nested_dob_keyword():
    out = redact_pre_llm_values("Children:  Ann DOB: 01/02/2010")
    assert out == f"Children:  Ann DOB: {PRE_LLM_PLACEHOLDER}"


# A child item is one given-name token plus a parenthesised date or a
# born/DOB keyword. Each expected string is origin/dev's output for the same
# text, except that the children's own dates in the first two are withheld.
@pytest.mark.parametrize("text, expected", [
    ("Children: Ann (01/02/2010), Bob (03/04/2012), Appointed 07/01/2015",
     f"Children: Ann ({PRE_LLM_PLACEHOLDER}), Bob ({PRE_LLM_PLACEHOLDER}), Appointed 07/01/2015"),
    ("Children: Ann (01/02/2010), Grant R01, 07/01/2015",
     f"Children: Ann ({PRE_LLM_PLACEHOLDER}), Grant R01, 07/01/2015"),
    ("Children: A Randomized Trial (03/15/2019)", "Children: A Randomized Trial (03/15/2019)"),
    ("Children: Healthy Eating Trial, 07/01/2005", "Children: Healthy Eating Trial, 07/01/2005"),
    ("Dependents: Health Plan, 01/01/2020", "Dependents: Health Plan, 01/01/2020"),
    ("Children: Jane, 01/02/2010", "Children: Jane, 01/02/2010"),
    ("Children: Healthy Eating Trial (07/01/2005)", "Children: Healthy Eating Trial (07/01/2005)"),
    ("Children: Trial (07/01/2015 - 06/30/2020)", "Children: Trial (07/01/2015 - 06/30/2020)"),
    ("Children: pilot (07/01/2015)", "Children: pilot (07/01/2015)"),
])
def test_redact_pre_llm_values_child_list_takes_only_child_shaped_items(text, expected):
    assert redact_pre_llm_values(text) == expected


# "Birthday" also names an event, so it never gets the explicit-DOB
# exemption from the boundary rule: both the pre-LLM scrub and the render
# pass (A, T, S4) leave these exactly as origin/dev does.
@pytest.mark.parametrize("text", [
    "Symposium for Dr. Smith's 70th Birthday: 06/15/2019, Boston, MA",
    "Grand Rounds, Hospital Birthday: 06/15/2019",
])
def test_birthday_after_a_prefix_is_not_an_explicit_dob_label(text):
    assert redact_pre_llm_values(text) == text
    entries = {code: [{"text": text, "taxonomy_code": code, "extracted_fields": {}}]
               for code in ("A", "T", "S4")}
    result = _run(entries)
    assert [entries[code][0]["text"] for code in ("A", "T", "S4")] == [text] * 3
    assert result.withheld == []


@pytest.mark.parametrize("text, expected", [
    ("Jane Doe DOB: 1/12/45", f"Jane Doe DOB: {PRE_LLM_PLACEHOLDER}"),
    ("Name: Jane Doe, MD Birth Date:  01/12/1945",
     f"Name: Jane Doe, MD Birth Date:  {PRE_LLM_PLACEHOLDER}"),
])
def test_explicit_dob_label_with_a_whole_date_is_caught_after_any_prefix(text, expected):
    assert redact_pre_llm_values(text) == expected
    # One shared policy: the render deny catches it at every destination too.
    assert _denied(text, A) and _denied(text, APPENDIX) and _denied(text, CONTENT)


@pytest.mark.parametrize("text", [
    "Jane Doe DOB: 1945",                    # bare year: not a whole date
    "Jane Doe DOB: pending",                 # no date at all
    "Rebirth Date: 01/02/2019 conference",   # label glued inside a word
    "Jane Doe Birthplace: 01/02/1945",       # not the DOB row's label
])
def test_explicit_dob_label_narrowing_stays_refused_without_all_three_conditions(text):
    assert redact_pre_llm_values(text) == text
    assert not _denied(text, CONTENT)


# --------------------------------------------------------------------------
# #849: a policy label one plain space after another field's value
#
# The stop is the KNOWN-label vocabulary (`_KNOWN_FIELD_LABEL_RE`), never a
# guess at where a value ends (#821 R4). All values synthetic.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, kept, gone", [
    ("Citizenship: US Date of Birth: March 1971", "Citizenship: US", "March 1971"),
    ("Citizenship: US Social Security Number: 900 12 3456", "Citizenship: US", "900 12 3456"),
    ("Citizenship: US Born: 03/04/1971", "Citizenship: US", "03/04/1971"),
    ("Citizenship: US Place of Birth: Springfield", "Citizenship: US", "Springfield"),
    ("Nationality: Dual Citizen Marital Status: Married", "Nationality: Dual Citizen", "Married"),
    ("Date of Birth: 03/04/1971 Marital Status: Married", "", "Married"),
])
def test_849_policy_label_after_a_known_field_value_is_withheld(text, kept, gone):
    assert _pii_fragments(text)
    entries = {"A": [{"text": text, "taxonomy_code": "A", "extracted_fields": {}}]}
    result = _run(entries)
    residual = entries["A"][0]["text"]
    assert gone not in residual
    assert kept in residual
    assert result.withheld


@pytest.mark.parametrize("text", [
    "Gave a talk on Date of Birth: a history of the census",  # no known label before it
    "Citizenship: US Member of the Social Security Number society",  # label words, no colon
    "Note: gave a talk on Date of Birth: a history",           # a colon, but not a KNOWN label
    "Citizenship: US Appointed 2005",                       # no policy label at all
])
def test_849_prose_containing_a_label_word_is_not_cut(text):
    assert _pii_fragments(text) == []


@pytest.mark.parametrize("text", [
    "Citizenship: US Language: Spanish",                        # "age:" inside Language
    "Office Address: 1300 York Ave Webpage: www.example.org",   # "age:" inside Webpage
    "Citizenship: US Idea: a new clinic",                       # "dea:" inside Idea
    "Phone: 555-0100 Pre-Marital Status: survey",               # hyphen before the label
    "Phone: 555-0100 O'Visa: none",                             # apostrophe before the label
])
def test_849_policy_label_inside_a_word_after_a_known_field_is_not_cut(text):
    """The known-field path accepts a policy label only at a word start;
    it used to match "age:" inside "Language:" and render "Langu"."""
    entries = {"A": [{"text": text, "taxonomy_code": "A", "extracted_fields": {}}]}
    _run(entries)
    assert entries["A"][0]["text"] == text


# --------------------------------------------------------------------------
# #847 residual round 5: child dates the child-item rule did not reach.
# All names, dates and years synthetic.
# --------------------------------------------------------------------------

def test_redact_pre_llm_values_children_label_after_a_merged_known_field_still_takes_child_items():
    # The Children label's span merges into the Marital Status span that
    # opens the line and keeps ITS category, so the child-item rule never
    # ran on it: the child's date reached the LLM.
    text = "Marital Status: Married Children: Ann (01/02/2010)"
    assert redact_pre_llm_values(text) == f"Marital Status: Married Children: Ann ({PRE_LLM_PLACEHOLDER})"


@pytest.mark.parametrize("text, expected", [
    # A Children label after another known field on its own line is a
    # Personal Data field, not a title: every whole date it carries is a
    # child's, whatever the item looks like.
    ("Marital Status: Married (Jo) Children: (1), Jane Roe, 01/02/94",
     f"Marital Status: Married (Jo) Children: (1), Jane Roe, {PRE_LLM_PLACEHOLDER}"),
    ("Citizenship: US Children: Jane, 01/02/2010 and Bob, 03/04/2012",
     f"Citizenship: US Children: Jane, {PRE_LLM_PLACEHOLDER} and Bob, {PRE_LLM_PLACEHOLDER}"),
    # ...up to the next known field label only.
    ("Citizenship: US Children: Jane, 01/02/2010 Date of Appointment: 07/01/2015",
     f"Citizenship: US Children: Jane, {PRE_LLM_PLACEHOLDER} Date of Appointment: 07/01/2015"),
    # A bare year is still never a child's date here.
    ("Marital Status: Married Children: two, since 2010", "Marital Status: Married Children: two, since 2010"),
    # A known label mid-line is not a Personal Data line: a citation.
    ("Roe J. Threats to Health: Youth in India. Health of Children: Hazards, Bangkok, 3-7 March 2002",
     "Roe J. Threats to Health: Youth in India. Health of Children: Hazards, Bangkok, 3-7 March 2002"),
])
def test_redact_pre_llm_values_children_label_after_a_known_field_takes_every_whole_date(text, expected):
    assert redact_pre_llm_values(text) == expected


@pytest.mark.parametrize("text, expected", [
    # A whole date that OPENS a Children label's value is a child's date:
    # no title opens with one.
    ("Dependents: 01/02/2010", f"Dependents: {PRE_LLM_PLACEHOLDER}"),
    ("Children: 01/02/2010, 03/04/2012 and 05/06/2014",
     f"Children: {PRE_LLM_PLACEHOLDER}, {PRE_LLM_PLACEHOLDER} and {PRE_LLM_PLACEHOLDER}"),
    ("Children: Ann (01/02/2010), 03/04/2012",
     f"Children: Ann ({PRE_LLM_PLACEHOLDER}), {PRE_LLM_PLACEHOLDER}"),
    # A bare date followed by more text is not a list item.
    ("Children: 01/02/2010 Symposium, Boston", "Children: 01/02/2010 Symposium, Boston"),
    ("Children: Ann (01/02/2010), 07/01/2015 Appointed",
     f"Children: Ann ({PRE_LLM_PLACEHOLDER}), 07/01/2015 Appointed"),
    # A year range is not a whole date.
    ("Children: 2015-2016 NHANES data brief", "Children: 2015-2016 NHANES data brief"),
])
def test_redact_pre_llm_values_a_date_opening_a_children_value_is_a_child_item(text, expected):
    assert redact_pre_llm_values(text) == expected


# --------------------------------------------------------------------------
# #1223: the withhold cuts the whole family / birth / home-contact value, not
# only the matched label's own fragment. All names, dates and numbers are
# synthetic.
# --------------------------------------------------------------------------

def _withheld_residual(text: str, code: str = A) -> tuple[str, list[WithheldItem]]:
    entry = {"text": text, "taxonomy_code": code, "extracted_fields": {}}
    result = _run({code: [entry]})
    return entry["text"], result.withheld


@pytest.mark.parametrize("text", [
    # a children list whose members each sit in a cell of their own
    "Children:   Kim Example - 5/6/71\tBob Example - 8/21/73\tCy Example - 11/2/75",
    # ...or are joined by a semicolon
    "Children: Kim (1971); Bob (1973); Cy (1975)",
    # a wider family label, its member in the next gap-separated cell
    "Family Information:   Wife:   Pat Example - Married 1969",
    # the place of birth in the cell after a date of birth the scrub already replaced
    f"Date of Birth:    {PRE_LLM_PLACEHOLDER}\tExample City, Examplestan",
    # the second address line and the phone in the cells after a home address
    "Home Address:   1 Example St\tSpringfield, XX 00000\t555-010-2030",
])
def test_1223_a_withheld_label_takes_the_unlabelled_cells_that_continue_it(text):
    residual, withheld = _withheld_residual(text)
    assert residual == ""
    assert withheld


def test_1223_unlabelled_marital_prose_and_a_phone_cell_do_not_survive_a_personal_information_block():
    text = ("Personal Information:\tMarried (Pat), two children (Kim and Lee)"
            "\tHome Address: 1 Example St, Springfield, XX 00000\t555-010-2030")
    residual, withheld = _withheld_residual(text)
    assert residual == "Personal Information:"
    assert {item.category for item in withheld} == {CAT_SPOUSE, CAT_HOME_CONTACT}


@pytest.mark.parametrize("text", [
    f"Kim born {PRE_LLM_PLACEHOLDER}",
    "Kim Example, born 01/02/1972",
])
def test_1223_a_birth_statement_takes_the_name_before_it(text):
    residual, withheld = _withheld_residual(text)
    assert residual == ""
    assert [item.category for item in withheld] == [CAT_BIRTH]


@pytest.mark.parametrize("text, kept", [
    # the next labelled cell is the next field, never this label's value
    ("Date of Birth: 01/02/1970\tCitizenship: US", "Citizenship: US"),
    ("Home Address: 1 Example St\tOffice Phone: 212-555-0100", "Office Phone: 212-555-0100"),
    # a known label part-way through a cell ends the cut there
    ("Children: Kim (1971)\tBob (1973) Citizenship: US", "Citizenship: US"),
    # a line break is the one boundary the source drew
    ("Children: Kim (1971)\nOffice phone 212-555-0100", "Office phone 212-555-0100"),
    # a category outside the continuation set keeps its single-fragment cut
    ("Marital Status: Married\tUS citizen", "US citizen"),
])
def test_1223_the_continuation_stops_where_the_next_field_begins(text, kept):
    residual, withheld = _withheld_residual(text)
    assert residual == kept
    assert withheld


def test_1223_a_routed_content_entry_keeps_its_single_fragment_cut():
    """The continuation is a Personal Data / Appendix rule: a routed entry
    (S4 here) only ever loses the label's own fragment."""
    residual, _ = _withheld_residual("Date of Birth: 01/02/1970\tExample Study, Springfield", code=CONTENT)
    assert residual == "Example Study, Springfield"


def test_1223_a_children_line_under_a_family_label_in_an_appendix_entry_is_withheld():
    text = ("Publications\n\nFamily:\n     Married to Pat Example, 1968\n"
            "     Children- Kim (2/1971), Lee (9/1973)\n\nTravel:\n     Example Country, 1966")
    residual, withheld = _withheld_residual(text, code=APPENDIX)
    assert "Kim" not in residual and "Lee" not in residual and "Pat" not in residual
    assert "Publications" in residual and "Example Country, 1966" in residual
    assert [item.category for item in withheld] == [CAT_FAMILY, CAT_SPOUSE, CAT_CHILDREN]


@pytest.mark.parametrize("text, categories", [
    ("Family:\n     Children- Kim (2/1971), Bob (9/1973)", [CAT_FAMILY, CAT_CHILDREN]),
    ("Family:\n     Wife - Pat Example\n     Children – Kim (1971)",
     [CAT_FAMILY, CAT_SPOUSE, CAT_CHILDREN]),
    ("Family Information:\tChildren- Kim (1971)", [CAT_FAMILY, CAT_CHILDREN]),
])
def test_1223_the_dash_form_of_a_family_label_is_withheld_under_a_family_label(text, categories):
    assert [m.category for m in _pii_matches(text)] == categories


@pytest.mark.parametrize("text", [
    "Children- Kim (1971), Bob (1973)",             # no Family label at all
    "Spouse- Pat Example",
    "Family:\n\nChildren- Kim (1971)",              # a blank line closes the block
    "Children - A Review of the Literature",
    "Family Medicine: A Review\nChildren- an essay",  # not a Family label
])
def test_1223_the_dash_form_of_a_family_label_outside_a_family_block_is_not_withheld(text):
    assert [m.category for m in _pii_matches(text) if m.category != CAT_FAMILY] == []


def test_1223_the_dash_form_is_a_personal_data_and_appendix_rule():
    assert _pii_matches("Family:\n     Children- Kim (1971)", SCOPE_ALL_CODES) == []


@pytest.mark.parametrize("text, expected", [
    # A DOB or SSN label after labelless family prose keeps its value scrub:
    # the new render-time rows are not part of the pre-LLM value scrub, so
    # their spans cannot swallow the label that follows them.
    ("Married (Pat), Born: 01/02/1970", f"Married (Pat), Born: {PRE_LLM_PLACEHOLDER}"),
    ("Family:\n     Wife- Pat, Born: 01/02/1970",
     f"Family:\n     Wife- Pat, Born: {PRE_LLM_PLACEHOLDER}"),
    ("Married (Pat), Date of Birth: 01/02/1970",
     f"Married (Pat), Date of Birth: {PRE_LLM_PLACEHOLDER}"),
    ("2 children (Kim and Lee) DOB: 01/02/1970",
     f"2 children (Kim and Lee) DOB: {PRE_LLM_PLACEHOLDER}"),
    ("two children (Kim and Lee), SSN: 123-45-6789",
     f"two children (Kim and Lee), SSN: {PRE_LLM_PLACEHOLDER}"),
    # "Family Information:" / "Family Data:" widen the render-time family label
    # only. As a pre-LLM row they would absorb the DOB or SSN in the same
    # fragment into a `family` span (a merged span keeps the leftmost category),
    # and the scrub would stop replacing it.
    ("Family Information: 123-45-6789", f"Family Information: {PRE_LLM_PLACEHOLDER}"),
    ("Family Data: SSN: 123-45-6789", f"Family Data: SSN: {PRE_LLM_PLACEHOLDER}"),
    ("Family Information: Date of Birth: 01/02/1970",
     f"Family Information: Date of Birth: {PRE_LLM_PLACEHOLDER}"),
    ("Family Information: Date of Birth 01/02/1970",
     f"Family Information: Date of Birth {PRE_LLM_PLACEHOLDER}"),
    ("Family Information: Pat Example (DOB 01/02/1970)",
     f"Family Information: Pat Example (DOB {PRE_LLM_PLACEHOLDER})"),
    ("Family Information: born 01/02/1970", f"Family Information: born {PRE_LLM_PLACEHOLDER}"),
    # ...and the dash children/spouse labels are not part of it either.
    ("Spouse- Pat, DOB: 01/02/1970", f"Spouse- Pat, DOB: {PRE_LLM_PLACEHOLDER}"),
    # The scrub stays idempotent: a second pass finds no date left to take, and
    # the year after a scrubbed birth statement is not a birth year.
    (f"Kim born {PRE_LLM_PLACEHOLDER}, 1981 graduate", f"Kim born {PRE_LLM_PLACEHOLDER}, 1981 graduate"),
    ("Kim Example, born 01/02/1972", f"Kim Example, born {PRE_LLM_PLACEHOLDER}"),
])
def test_1223_the_pre_llm_value_scrub_is_unchanged_by_the_render_time_family_rows(text, expected):
    assert redact_pre_llm_values(text) == expected
    assert redact_pre_llm_values(expected) == expected


def test_1223_a_birth_statement_with_no_name_before_it_matches_nothing_new():
    """The row exists for the name that precedes "born": a bare "born
    [withheld]" has no protected value left, and "Born on <date>" is the
    colon-less date-of-birth row's, with its own category."""
    assert _pii_matches(f"born {PRE_LLM_PLACEHOLDER}") == []
    assert [m.category for m in _pii_matches("Born on January 2, 1970")] == [CAT_DATE_OF_BIRTH]


def test_1223_a_labelless_family_shape_does_not_take_the_cell_after_it():
    """Only a label has a value that continues in the next cell. The cell after
    "Married (<name>)" is another field, here a citizenship (a #821 "render" item)."""
    residual, withheld = _withheld_residual("Married (Pat)\tUS citizen")
    assert residual == "US citizen"
    assert [item.category for item in withheld] == [CAT_SPOUSE]


@pytest.mark.parametrize("text, categories", [
    ("Children- Kim (1971), Bob (1973)", [CAT_CHILDREN]),
    ("Children - Kim (1971)", [CAT_CHILDREN]),
    ("Spouse- Pat Example", [CAT_SPOUSE]),
    ("Wife- Pat", [CAT_SPOUSE]),
    ("Husband - Pat", [CAT_SPOUSE]),
    ("Family- Married to Pat Example", [CAT_FAMILY]),
    # ...so the title-shaped controls are withheld there too (the issue's own,
    # and #473's spouse-title control), though not in an Appendix entry below.
    ("Children - A Review of the Literature", [CAT_CHILDREN]),
    ("Spouse- A Documentary Film Review", [CAT_SPOUSE]),
    ("Family- wise error rate in gene association studies", [CAT_FAMILY]),
    ("Name of Spouse \u2013 A Documentary Film Review", [CAT_SPOUSE]),
])
def test_1223_the_dash_form_of_a_family_label_in_the_personal_data_block_is_withheld(text, categories):
    """An A-coded entry is the Personal Data block: it holds no titles, so the
    dash form needs no `Family` label ahead of it there."""
    residual, withheld = _withheld_residual(text, code=A)
    assert residual == ""
    assert [item.category for item in withheld] == categories


@pytest.mark.parametrize("text", [
    "Children- Kim (1971), Bob (1973)",
    "Spouse- Pat Example",
    "Children - A Review of the Literature",
    "Spouse- A Documentary Film Review",
])
def test_1223_the_dash_form_outside_a_family_block_in_an_appendix_entry_is_kept(text):
    """The issue's own negative control: an Appendix-bound entry can hold a
    title, so without a `Family` label ahead of it a dash label is left alone."""
    residual, withheld = _withheld_residual(text, code=APPENDIX)
    assert residual == text
    assert withheld == []


@pytest.mark.parametrize("text, kept", [
    # a list that goes on over the next lines of the entry
    ("Children: Kim Example\nBob Example", ""),
    ("Children: Kim (1971)\nBob (1973)\nCy (1975)", ""),
    ("Children:\nKim Example, 1971\nBob Example, 1973", ""),
    ("Family: Pat Example\nKim Example - 5/6/71\nBob Example - 8/21/73", ""),
    ("Children: Kim Example and Bob Example\nCy Example & Dee Example", ""),
    # ...up to the next labelled field, a blank line, or a line that is not a list
    ("Children: Kim (1971)\nBob (1973)\nCitizenship: US", "Citizenship: US"),
    ("Children: Kim Example\n\nBob Example", "Bob Example"),
    ("Children: Kim Example\nawarded the prize", "awarded the prize"),
    ("Children: Kim Example\nSee 12 Example Street", "See 12 Example Street"),
    ("Children: Kim Example\nBob", "Bob"),
    # the whole line must be the list: a line that goes on in prose is not one
    ("Children: Kim (1971)\nBob (1973) attends Example School", "Bob (1973) attends Example School"),
    # a category that has no list (a spouse, a date of birth) never takes the next line
    ("Spouse: Pat Example\nKim Example", "Kim Example"),
    ("Date of Birth: 01/02/1970\nKim Example", "Kim Example"),
])
def test_1223_a_children_or_family_list_takes_the_list_lines_that_go_on_after_it(text, kept):
    residual, withheld = _withheld_residual(text)
    assert residual == kept
    assert withheld


def test_1223_a_routed_content_entry_keeps_a_children_list_whole():
    """Children and family labels are Personal Data / Appendix rows: a routed
    entry matches neither, so no list line of it is cut."""
    text = "Children: Kim Example\nBob Example"
    assert _withheld_residual(text, code=CONTENT) == (text, [])


def test_1223_the_list_line_shape_is_linear_in_the_length_of_a_line():
    """A long run of name-like tokens that never becomes a list line must not
    be rescanned at every offset."""
    start = time.perf_counter()
    for text in ("Children: Kim\n" + "Example " * 4000 + "x",
                 "Children: Kim\n" + "Example, " * 4000 + "x",
                 "Children: Kim\n" + " " * 8000 + "Example\t" * 2000 + "1"):
        _withheld_residual(text)
    assert time.perf_counter() - start < 2.0


def test_1223_the_price_of_the_continuation_is_an_unlabelled_neighbour_after_the_label():
    """Stated cost, pinned so a reviewer sees it: a cell that follows a date of
    birth, children, spouse, family or home-address label with no label of its
    own is cut with the value ("US citizen" is a #821 "render" item), and a
    line of capitalised words after a children or family list is cut as a list
    line. Never a leak; possibly a loss."""
    assert _withheld_residual("Date of Birth: 01/02/1970\tUS citizen")[0] == ""
    assert _withheld_residual("Children: Kim Example\nUS Citizen")[0] == ""



# --------------------------------------------------------------------------
# #1223 (EBYSBC, EQADVR): the "Married:" and "Grandchildren" family labels, and
# a "Born:" label whose place of birth sits past a semicolon or a column gap.
# The spacing is the corpus's own (a semicolon, an en dash, tabs, a 5-space
# gap); every name and place is synthetic.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text, categories", [
    ("Married: Pat Example, Ed.D.", [CAT_SPOUSE]),
    ("Grandchildren – Kim Example, Lee, Bob, Cy", [CAT_CHILDREN]),
    ("Great-grandchildren - Kim, Lee", [CAT_CHILDREN]),
    ("Married – Pat Example", [CAT_SPOUSE]),
    # a family list whose members each sit in a cell of their own
    ("Grandchildren:\tKim Example\tLee Example\tBob Example", [CAT_CHILDREN]),
    ("Married:\tPat Example\tEd.D.", [CAT_SPOUSE]),
    # ...or go on over the next lines of the entry
    ("Grandchildren: Kim Example\nLee Example", [CAT_CHILDREN]),
    # the place of birth after the date the pre-LLM scrub already replaced
    (f"Born: {PRE_LLM_PLACEHOLDER}; Exampleville, Examplestan", [CAT_BIRTH]),
    (f"Born: {PRE_LLM_PLACEHOLDER}     Exampleville, Examplestan", [CAT_BIRTH]),
    (f"Born: {PRE_LLM_PLACEHOLDER}\tExampleville, Examplestan", [CAT_BIRTH]),
])
def test_1223_ebysbc_family_and_birth_labels_withhold_the_whole_value(text, categories):
    residual, withheld = _withheld_residual(text, code=A)
    assert residual == ""
    assert [item.category for item in withheld] == categories


def test_1223_ebysbc_the_birth_fragment_carries_the_place_for_the_provenance_check():
    """The Personal Data writer denies an extracted address by containment in
    the pass's own fragments (`_from_pii_fragment`): the place of birth stage 4
    lifted into `address` has to be inside the birth fragment, or the Office
    address catch-all takes it."""
    entry = {"text": f"Born: {PRE_LLM_PLACEHOLDER}; Exampleville, Examplestan",
             "taxonomy_code": A, "extracted_fields": {"address": "Exampleville, Examplestan"}}
    _run({A: [entry]})
    assert any("Exampleville, Examplestan" in fragment for fragment in entry["_pii_fragments"])


@pytest.mark.parametrize("text, kept", [
    # the next labelled cell is the next field, never the birth label's value
    (f"Born: {PRE_LLM_PLACEHOLDER};\tCitizenship: US", "Citizenship: US"),
    # a line break is the one boundary the source drew
    (f"Born: {PRE_LLM_PLACEHOLDER}\nOffice phone 212-555-0100", "Office phone 212-555-0100"),
    ("Married: Pat Example\nOffice phone 212-555-0100", "Office phone 212-555-0100"),
])
def test_1223_ebysbc_the_continuation_still_stops_where_the_next_field_begins(text, kept):
    residual, withheld = _withheld_residual(text, code=A)
    assert residual.endswith(kept) and "Exampleville" not in residual
    assert withheld


def test_1223_ebysbc_a_home_address_cut_runs_over_a_married_label_as_before():
    """Why the two labels are shape rows: as label rows they would join the
    known-field vocabulary a home-address cut stops at part-way through its
    value, and the spouse's name after "Married:" (which opens mid-fragment,
    so matches nothing itself) would render. The cut may only grow."""
    residual, withheld = _withheld_residual(
        "Home Address:     12 Example St Springfield Married: Pat Example")
    assert residual == ""
    assert [item.category for item in withheld] == [CAT_HOME_CONTACT]


@pytest.mark.parametrize("text", [
    "Grandchildren – A Review of Kinship Care",
    "Married - A Documentary Film Review",
])
def test_1223_ebysbc_the_dash_form_outside_a_family_block_in_an_appendix_entry_is_kept(text):
    """An Appendix-bound entry can hold a title: the dash form needs a
    `Family` label ahead of it there, as the children and spouse rows do."""
    assert _withheld_residual(text, code=APPENDIX) == (text, [])


def test_1223_ebysbc_the_dash_form_under_a_family_label_in_an_appendix_entry_is_withheld():
    text = "Family:\n     Married – Pat Example\n     Grandchildren – Kim, Lee"
    residual, withheld = _withheld_residual(text, code=APPENDIX)
    assert "Pat" not in residual and "Kim" not in residual
    assert [item.category for item in withheld] == [CAT_FAMILY, CAT_SPOUSE, CAT_CHILDREN]


@pytest.mark.parametrize("text", [
    "Married: Pat Example",
    "Grandchildren: Kim (2010), Lee (2012)",
    f"Born: {PRE_LLM_PLACEHOLDER}; Exampleville",
])
def test_1223_ebysbc_a_routed_content_entry_is_untouched(text):
    """All three are Personal Data / Appendix rows: a routed entry keeps its
    text (the "Born:" label is the ambiguous-title row, not the DOB row)."""
    assert _withheld_residual(text, code=CONTENT) == (text, [])


@pytest.mark.parametrize("text, expected", [
    # The new rows are render-time only: a DOB after them keeps its value scrub.
    ("Married: Pat Example, Date of Birth: 01/02/1970",
     f"Married: Pat Example, Date of Birth: {PRE_LLM_PLACEHOLDER}"),
    ("Grandchildren: Kim Example DOB: 01/02/2010",
     f"Grandchildren: Kim Example DOB: {PRE_LLM_PLACEHOLDER}"),
    ("Born: 01/02/1970; Exampleville, Examplestan",
     f"Born: {PRE_LLM_PLACEHOLDER}; Exampleville, Examplestan"),
])
def test_1223_ebysbc_the_pre_llm_value_scrub_is_unchanged(text, expected):
    assert redact_pre_llm_values(text) == expected


# --------------------------------------------------------------------------
# NDMRSO ND1: institutional and tax ID numbers
# --------------------------------------------------------------------------

#: (text, category): every label the two ID rows know, each with a number
#: after it. ATUVAL element 1's shape is the first (an institution's ID label,
#: "#:", the number); MQJAVH element 6's the "EIN Number<tab>" one (the number
#: in the next tab cell, which a label row's span stops short of).
_ND1_ID_LINES = [
    ("UFID #: 1234-5678", CAT_INSTITUTIONAL_ID),
    ("CWID abc2001", CAT_INSTITUTIONAL_ID),
    ("EMPLID: 1234567", CAT_INSTITUTIONAL_ID),
    ("Employee ID: 1234567", CAT_INSTITUTIONAL_ID),
    ("Employee ID Number: abc1234", CAT_INSTITUTIONAL_ID),
    ("Employee ID No.: 1234567", CAT_INSTITUTIONAL_ID),
    ("Employee ID: | 1234567", CAT_INSTITUTIONAL_ID),
    ("Employee ID: E-1234567", CAT_INSTITUTIONAL_ID),
    ("Employee Identification Number: 1234567", CAT_INSTITUTIONAL_ID),
    ("Staff ID: 1234567", CAT_INSTITUTIONAL_ID),
    ("Student No. 1234 5678", CAT_INSTITUTIONAL_ID),
    ("Faculty ID 1234567", CAT_INSTITUTIONAL_ID),
    ("Personnel No. 1234567", CAT_INSTITUTIONAL_ID),
    ("Payroll Number: 1234567", CAT_INSTITUTIONAL_ID),
    ("Badge ID - 1234567", CAT_INSTITUTIONAL_ID),
    ("University ID\t12345678", CAT_INSTITUTIONAL_ID),
    ("University Identification: 12345678", CAT_INSTITUTIONAL_ID),
    ("Institution ID: 12345678", CAT_INSTITUTIONAL_ID),
    ("Institutional ID: 12345678", CAT_INSTITUTIONAL_ID),
    ("Campus ID: 12345678", CAT_INSTITUTIONAL_ID),
    ("EIN Number\t12-345-6789", CAT_TAX_ID),
    ("FEIN: 12-3456789", CAT_TAX_ID),
    ("Federal Tax ID No.: 12-3456789", CAT_TAX_ID),
    ("Tax Payer ID: 12-3456789", CAT_TAX_ID),
    ("Taxpayer Identification Number: 12-3456789", CAT_TAX_ID),
    ("Employer Identification Number - 12-3456789", CAT_TAX_ID),
    ("TIN 123456789", CAT_TAX_ID),
    ("ITIN: 912-34-5678", CAT_TAX_ID),
    # An SSN given as the tax ID is one tax-ID span, label and value.
    ("Tax ID: 123-45-6789", CAT_TAX_ID),
]

#: Public identifiers (#821 renders NPI and ORCID), a board certificate's bare
#: "ID #", a grant number after an institution name (JFGZFT element 146's
#: shape), a short count, a bare label, and a label word running on into
#: another word.
_ND1_NOT_ID_LINES = [
    "NPI: 1234567890",
    "ORCID: 0000-0002-1234-5678",
    "PMID: 12345678",
    "Example Board of Internal Medicine, ID #: 123456",
    "Example Fund at Sample University #1234567",
    "Grant No. R01 CA123456",
    "Sample University No. 3 Hospital",
    "Student Number: 12",
    "Tax ID:",
    "Employee Assistance Program 2019",
    "Student Nov2019",
    "University Idaho1234",
    "UFIDA1234",
    "TINY1234 sensor",
    "Einstein 1234",
    "Latin 12345",
    "Crystal clear or tin ear: a study",
]


@pytest.mark.parametrize("text, category", _ND1_ID_LINES)
@pytest.mark.parametrize("code", [A, APPENDIX, CONTENT])
def test_nd1_an_id_number_line_is_cut_whole_in_every_code(text, category, code):
    """The whole line goes, label and value, so nothing is left for the
    Appendix recovery to render, and the notice names the category."""
    residual, withheld = _withheld_residual(text, code=code)
    assert residual == ""
    assert [item.category for item in withheld] == [category]


@pytest.mark.parametrize("text", _ND1_NOT_ID_LINES)
@pytest.mark.parametrize("code", [A, APPENDIX, CONTENT])
def test_nd1_public_identifiers_grants_and_other_words_are_not_id_numbers(text, code):
    assert not [m for m in _probe_matches(text, code)
                if m.category in (CAT_INSTITUTIONAL_ID, CAT_TAX_ID)]


def test_nd1_the_id_rows_carry_the_id_number_decision():
    rows = [r for r in WITHHOLD_POLICY if r.category in (CAT_INSTITUTIONAL_ID, CAT_TAX_ID)]
    assert [r.category for r in rows] == [CAT_INSTITUTIONAL_ID, CAT_TAX_ID]
    assert all(r.decided_by == DECIDED_ID_NUMBERS for r in rows)


def test_nd1_an_id_number_beside_other_contact_data_leaves_the_rest():
    residual, withheld = _withheld_residual(
        "Citizenship: Example\tEmployee ID: 1234567\tFax: 555-201-0123", code=A)
    assert "1234567" not in residual
    assert "Citizenship: Example" in residual and "Fax: 555-201-0123" in residual
    assert [item.category for item in withheld] == [CAT_INSTITUTIONAL_ID]


def test_nd1_the_id_rows_are_render_time_only():
    """An SSN given as a tax ID keeps its pre-LLM value scrub: the tax-ID span
    starts ahead of it and would otherwise take it over."""
    assert redact_pre_llm_values("Tax ID: 123-45-6789") == f"Tax ID: {PRE_LLM_PLACEHOLDER}"
    assert (redact_pre_llm_values("Employee ID: 123-45-6789")
            == f"Employee ID: {PRE_LLM_PLACEHOLDER}")
    assert redact_pre_llm_values("UFID #: 1234-5678") == "UFID #: 1234-5678"


# #1223 (NDMRSO, class E2): a bare "Birth" label with a whole date after it,
# and a spelled-out child count behind a marital-status cut. The spacing is
# the corpus's own (BNYLDF idx 24, HUOGDE idx 8, VXSDRD idx 26); every value
# is synthetic.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Birth: March 4, 1970 in Exampleville, Examplestan",
    "BIRTH March 4, 1970\tExampleville",
    f"BIRTH {PRE_LLM_PLACEHOLDER}\tExampleville",
    "Birth - 4 March 1970",
    "Birth: 1970-03-04",
    "BIRTH 04/03/1970",
])
def test_1223_ndmrso_a_bare_birth_label_withholds_the_date_and_the_place(text):
    for code in (A, APPENDIX, CONTENT):
        residual, withheld = _withheld_residual(text, code=code)
        assert [item.category for item in withheld] == [CAT_DATE_OF_BIRTH], code
        if code != CONTENT:
            # the place of birth in the next cell goes with it
            assert residual == "", code


@pytest.mark.parametrize("text", [
    "Birth: A History of Midwifery",
    "Birth cohort 1990",
    "Birth 2019 Symposium",
    "Birth: 2019",
    "Preterm birth: 03/15/2019 outcomes",
    "Very preterm birth 12 March 2019",
    "Birthplace of an Idea",
    "Afterbirth: 03/04/2019 review",
    "Pre-birth 03/04/2019 visit",
    "Example Children's Hospital, 03/04/2015",
    "Example Children's Oncology Group",
])
def test_1223_ndmrso_birth_titles_and_childrens_institutions_are_kept(text):
    for code in (A, APPENDIX, CONTENT):
        assert not _denied(text, code), code
    assert redact_pre_llm_values(text) == text


@pytest.mark.parametrize("text, expected", [
    ("BIRTH March 4, 1970\tExampleville", f"BIRTH {PRE_LLM_PLACEHOLDER}\tExampleville"),
    ("Birth: 03/04/1970 in Exampleville", f"Birth: {PRE_LLM_PLACEHOLDER} in Exampleville"),
])
def test_1223_ndmrso_the_pre_llm_scrub_takes_the_date_after_a_bare_birth_label(text, expected):
    assert redact_pre_llm_values(text) == expected


def test_1223_ndmrso_a_bare_birth_cut_stops_where_the_next_field_begins():
    residual, withheld = _withheld_residual("BIRTH 03/04/1970\tCitizenship: US", code=A)
    assert residual == "Citizenship: US"
    assert [item.category for item in withheld] == [CAT_DATE_OF_BIRTH]


def test_1223_ndmrso_a_spelled_out_child_count_behind_a_marital_cut_is_withheld():
    entry = {"text": "Marital Status:  Synthetic; five children", "taxonomy_code": "T",
             "extracted_fields": {}}
    result = _run({"T": [entry]})
    assert "Synthetic" not in entry["text"] and "children" not in entry["text"]
    assert [i.category for i in result.withheld] == [CAT_MARITAL_STATUS, CAT_CHILDREN]


@pytest.mark.parametrize("text", [
    "Status; two sons",
    "Status\none child",
    "Status   four children",
    "Status | ten kids | Board Certified",
])
def test_1223_ndmrso_a_spelled_out_child_count_fragment_is_withheld(text):
    for code in (A, APPENDIX):
        assert _denied(text, code), code
    assert not _denied(text, CONTENT)


@pytest.mark.parametrize("text", [
    "Cohort A; two children with asthma",
    "Outcomes in four children",
    "Board Certified, two children",
    "Status; often children are seen",
    "Status; someone child-centred",
])
def test_1223_ndmrso_a_spelled_out_child_count_inside_prose_is_not_withheld(text):
    assert not [m for m in _pii_matches(text) if m.category == CAT_CHILDREN]


def test_1223_ndmrso_the_child_count_row_does_not_backtrack_into_a_long_space_run():
    """The count row tries eleven spelled-out counts after the spaces that open
    a fragment; with a backtracking space run that cost seconds on a long gap
    (16,000 spaces: about 0.04 s possessive, several seconds without)."""
    start = time.perf_counter()
    _pii_matches("Status;" + " " * 16000 + "x")
    assert time.perf_counter() - start < 1.0
