"""Regression tests for the code-'A' gap: unconsumed Personal Data entries were
silently deleted, and one class of them was silently rendered.

`mapped_codes` lists 'A' with the comment "unused A entries go to appendix".
They did not: the exclusion covered the whole code, on the stated assumption
that "A entries are all used in Personal Data section". `_fill_personal_data`
reads only phone/address/email, so an entry carrying "Citizenship: US" or
"Fax: ..." is consumed by nothing and was then excluded from the appendix too.
146 such orphans exist across 80 of the 100 corpus CVs.

The opposite failure also exists and is worse: web07's Office address row
renders "Cincinnati, Ohio" today, lifted straight out of
"PLACE OF BIRTH: Cincinnati, Ohio" by the address catch-all.

The two paths need DIFFERENT granularities, which is the whole point of these
tests:

- consumption path -> deny by value PROVENANCE. Dropping the whole entry breaks
  three corpus CVs whose contact block also carries a birth date, costing them
  real office addresses, an office phone and a work email.
- appendix path -> deny the whole ENTRY. It renders nothing, so discarding it
  is free, and fragment-level filtering would keep the birth date and drop only
  its label.

Text is read by walking w:t nodes, NOT Paragraph.text, which is blind to runs
inside <w:ins> tracked changes and under-reports by 11-19% (#461).

    python3 -m pytest src/unified_pipeline/tests/test_stage6_personal_data_recovery.py -p no:cacheprovider
"""

import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.pii import SCOPE_ALL_CODES  # noqa: E402
from unified_pipeline.stage6.pii_pass import (  # noqa: E402
    WITHHELD_COMMENT_AUTHOR,
    WITHHELD_COMMENT_HEADER,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    PII_REDACTED_NOTICE,
    WCMTemplateGenerator,
    _PII_FIELD_KEY_RE,
    _pii_fragments,
)

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def _all_text(docx_path) -> str:
    doc = Document(str(docx_path))
    return "\n".join(n.text or "" for n in doc.element.body.iter(W_T))


def _render(tmp_path, entries, cv_owner=None, **generator_kwargs) -> str:
    gen = WCMTemplateGenerator(verbose=False, **generator_kwargs)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    payload = {"document_uid": "TESTPD", "entries": entries}
    if cv_owner:
        payload["cv_owner"] = cv_owner
    ip.write_text(json.dumps(payload))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return _all_text(op)


def _entry(text, code, fields=None, idx=0):
    return {"text": text, "taxonomy_code": code,
            "extracted_fields": fields or {}, "element_idx_start": idx}


def _comments(docx_path) -> list[tuple[str, str]]:
    """(author, text) of every comment in the docx's comments part, text
    lines joined with newlines; [] when the part does not exist."""
    with zipfile.ZipFile(docx_path) as z:
        if "word/comments.xml" not in z.namelist():
            return []
        root = ET.fromstring(z.read("word/comments.xml"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    out = []
    for c in root.iter(f"{W}comment"):
        lines = ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in c.iter(f"{W}p")]
        out.append((c.get(f"{W}author"), "\n".join(lines)))
    return out


def _a(text, fields=None, idx=0):
    return {"text": text, "taxonomy_code": "A",
            "extracted_fields": fields or {}, "element_idx_start": idx}


# --------------------------------------------------------------------------
# consumption path
# --------------------------------------------------------------------------

def test_birthplace_is_not_rendered_as_an_address(tmp_path):
    """web07's shape: stage 4 extracted the birthplace into `address`, and the
    address catch-all put it in the Office address row."""
    text = _render(tmp_path, [
        _a("PLACE OF BIRTH: Cincinnati, Ohio", {"address": "Cincinnati, Ohio"}),
    ])
    assert "Cincinnati, Ohio" not in text, "birthplace rendered as an address"


def test_real_contact_data_survives_an_entry_that_also_carries_pii(tmp_path):
    """web086's shape, and the reason this denies by value rather than by entry.

    Dropping the whole entry -- the obvious implementation -- costs this CV its
    office address and office phone. This test fails if someone makes that
    change.
    """
    text = _render(tmp_path, [
        _a("Born: December 13, 1965\nOffice: 8200 East Belleview Ave, Greenwood Village, CO\n"
           "Phone: (303) 694-3333",
           {"address": "8200 East Belleview Ave, Greenwood Village, CO",
            "phone": "(303) 694-3333"}),
    ])
    assert "8200 East Belleview Ave" in text, "real office address was dropped with the PII"
    assert "(303) 694-3333" in text, "real office phone was dropped with the PII"
    assert "December 13, 1965" not in text, "birth date reached the document"


def test_email_regex_fallback_does_not_harvest_from_a_pii_fragment(tmp_path):
    text = _render(tmp_path, [
        _a("Spouse's name: Pat Doe (pat.doe@example.com)"),
    ])
    assert "pat.doe@example.com" not in text


def test_personal_data_path_keeps_phone_and_email_beside_a_birth_date(tmp_path: Path) -> None:
    """#735 review item 3: the same entry carries a birth date AND a real
    office phone AND a work email; the consumption path denies by value, so
    both live contact values survive while only the birth date is denied.
    Extends `test_real_contact_data_survives_an_entry_that_also_carries_pii`
    (address + phone) with the one field it does not cover, email.

    The email is deliberately absent from the raw text (unlike address and
    phone, which the entry's raw text also carries): `_fill_personal_data`
    has a second, regex-based email fallback that scans raw text
    independently of `extracted_fields['email']`, and it would mask a
    regression in the field-based classification this test targets if the
    email string were also sitting in the text for it to find."""
    text = _render(tmp_path, [
        _a("Born: April 1, 1958\n"
           "Office: 1300 York Avenue\n"
           "Phone: (212) 555-0100",
           {"address": "1300 York Avenue",
            "phone": "(212) 555-0100",
            "email": "jane.roe@med.example.edu"}),
    ])
    assert "(212) 555-0100" in text, "real office phone was dropped with the PII"
    assert "jane.roe@med.example.edu" in text, "real work email was dropped with the PII"
    assert "April 1, 1958" not in text, "birth date reached the document"


# --------------------------------------------------------------------------
# appendix recovery path
# --------------------------------------------------------------------------

def test_unconsumed_entry_reaches_the_appendix(tmp_path):
    """The bug: an A entry supplying no phone/address/email rendered nowhere."""
    text = _render(tmp_path, [
        _a("Citizenship: United States of America"),
        _a("Foreign Languages: Spanish, Portuguese"),
    ])
    assert "United States of America" in text, "citizenship was silently deleted"
    assert "Spanish, Portuguese" in text, "languages were silently deleted"


def test_consumed_entry_is_not_duplicated_into_the_appendix(tmp_path):
    """An entry that DID fill a slot must not also be appended."""
    text = _render(tmp_path, [
        _a("Office: 1300 York Avenue, New York, NY",
           {"address": "1300 York Avenue, New York, NY"}),
    ])
    assert text.count("1300 York Avenue") == 1, "consumed address duplicated"


def test_name_banner_already_on_the_page_is_not_appended_again(tmp_path):
    """The redundancy filter, and the reason recovery runs post-render.

    The commonest orphan by far is the faculty member's own name/title banner:
    76 of the corpus's 146, of which 75 already render -- from `cv_owner`, not
    from the A entry, so the entry itself looks unconsumed. Without this filter
    every one of them is appended a second time.
    """
    owner = "Jane Q. Public, M.D., Ph.D."
    text = _render(tmp_path, [_a(owner)], cv_owner={"full_name_with_credentials": owner})
    assert text.count(owner) == 1, "name banner duplicated into the appendix"


def test_pii_orphan_is_redacted_with_a_visible_notice(tmp_path):
    """Withheld, but not silently: the reader is told something was removed."""
    text = _render(tmp_path, [
        _a("Date of Birth: 12/13/1947"),
        _a("Marital Status: Married, spouse Jane Roe"),
        _a("Citizenship: Canada"),
    ])
    assert "12/13/1947" not in text, "date of birth reached the document"
    assert "Jane Roe" not in text, "spouse name reached the document"
    assert PII_REDACTED_NOTICE in text, "content was withheld with no indication"
    assert "Canada" in text, "non-PII entry withheld along with the PII"


def test_one_redaction_notice_per_document(tmp_path):
    text = _render(tmp_path, [
        _a("Date of Birth: 12/13/1947"),
        _a("Place of Birth: Rochester, NY"),
        _a("Marital Status: Married"),
    ])
    assert text.count(PII_REDACTED_NOTICE) == 1


def test_pii_named_only_by_field_key_is_caught(tmp_path):
    """6 of the corpus PII entries carry no recognisable text label; stage 4
    names the field instead."""
    text = _render(tmp_path, [
        _a("Additional information", {"marital_status_children": "Ann, Bob"}),
    ])
    assert "Ann, Bob" not in text
    assert PII_REDACTED_NOTICE in text


def test_appendix_removal_catches_raw_text_and_field_key_pii_alike(tmp_path: Path) -> None:
    """#735 review item 1, end-to-end through the real appendix removal site
    (`_unconsumed_personal_data_batch`, `stage_6_word_template.py:2390`): an
    A-coded entry whose RAW TEXT carries a PII label, and a sibling entry
    whose text carries no label but whose `extracted_fields` alone carries a
    PII key, are both absent from the generated document with one shared
    withheld notice -- and a third, non-PII sibling in the same appendix
    batch still renders. `test_pii_orphan_is_redacted_with_a_visible_notice`
    and `test_pii_named_only_by_field_key_is_caught` pin the two halves
    separately; this pins them together in one batch, which is what item 1
    actually asks for."""
    text = _render(tmp_path, [
        _a("Date of Birth: 04/01/1958"),
        _a("Additional information", {"marital_status_spouse": "Pat Roe"}),
        _a("Foreign Languages: French"),
    ])
    assert "04/01/1958" not in text, "raw-text-labelled PII reached the document"
    assert "Additional information" not in text, (
        "field-key-only PII entry reached the document"
    )
    assert "Pat Roe" not in text, "field-key-only PII value reached the document"
    assert PII_REDACTED_NOTICE in text, "content was withheld with no indication"
    assert "French" in text, "a non-PII sibling in the same batch was dropped too"


def test_pii_in_a_tab_separated_cell_is_still_caught(tmp_path):
    """The scan must read the RAW entry text. _clean_inline_tabs rewrites the
    first '\\t' to ': ' and ' | ' to ' — ', destroying the fragment boundaries
    the scan splits on -- scan the cleaned text instead and the PII cell merges
    into its neighbour, no longer starts a fragment, and renders."""
    text = _render(tmp_path, [
        _a("Office: 1300 York Ave\tDate of Birth: 12/13/1947"),
        _a("Home | Marital Status: Married"),
    ])
    assert "12/13/1947" not in text
    assert "Married" not in text
    assert PII_REDACTED_NOTICE in text


def test_web057_shape_is_withheld_from_a_t_coded_appendix_line(tmp_path):
    """#820's local instance, end to end: a T-coded line with the spouse
    label buried after another label (`Personal Information:: Husband:`)
    -- the unanchored-matching case, through the real Appendix path."""
    text = _render(tmp_path, [
        _entry("Personal Information:: Husband: Pat Example, MD", "T"),
        _entry("Foreign Languages: French", "T"),
    ])
    assert "Pat Example" not in text, "spouse name reached the Appendix"
    assert PII_REDACTED_NOTICE in text
    assert "French" in text


def test_notice_is_keyed_on_something_withheld_not_on_appendix_redaction(tmp_path):
    """M20: the notice used to be keyed on the A-orphan redaction count.
    PII withheld from a RENDERED section (an F1 entry's text) with no
    A-coded orphan at all must still produce the notice."""
    text = _render(tmp_path, [
        _entry("Medical license, Example State; SSN: 123-45-6789", "F1",
               {"license_number": "X1", "state": "Example State"}),
    ])
    assert "123-45-6789" not in text
    assert PII_REDACTED_NOTICE in text


def test_no_notice_when_the_only_label_is_out_of_scope(tmp_path):
    """The round-1 false notice: an ambiguous label on a routed content
    code is not withheld, so nothing is recorded and the document carries
    neither the notice nor a comment (the render gate's CHANGED 0)."""
    text = _render(tmp_path, [
        _entry("Children: Research, Practice and Policy. Example Press, 2001.", "S4",
               {"title": "Children: Research, Practice and Policy",
                "authors": "Roe J", "year": "2001", "publisher": "Example Press"}),
        _entry("DEA number: AB1234567", "F1", {"license_number": "AB1234567"}),
    ])
    assert "Children: Research" in text, "a content-code title was withheld"
    assert PII_REDACTED_NOTICE not in text
    assert _comments(tmp_path / "out.docx") == []


def test_email_field_fallback_does_not_harvest_from_a_pii_fragment_on_any_code(tmp_path):
    """M23: the all-entries email fallback (`personal_data.py`, the
    `_pii_fragments` read at its second site) must consult the pass's
    stored fragments -- a T-coded emergency-contact entry whose
    `extracted_fields['email']` was lifted from the fragment must not
    supply the Work email slot."""
    text = _render(tmp_path, [
        _entry("Emergency Contact: Pat Example, pat.example@example.com", "T",
               {"email": "pat.example@example.com"}),
    ])
    assert "pat.example@example.com" not in text


def test_pass_runs_before_dedup_so_a_dropped_near_duplicate_is_still_scanned(tmp_path):
    """`_recover_unrendered_records` re-scans the PRE-dedup entries, so an
    entry dedup drops can still put a record line on the page. The pass
    therefore runs before the pre-dedup snapshot: the dropped
    near-duplicate's SSN is recorded (notice emitted) rather than skipped.
    With the pass after dedup this document carried no notice."""
    kept = ("2001 - 2002\tExample Award for Distinguished Example Work, Example Society of Examples\n"
            "2003 - 2004\tExample Prize for Outstanding Example Research, Example Foundation of Examples")
    dropped = ("2001 - 2002\tExample Award for Distinguished Example Work, Example Society\n"
               "2003 - 2004\tExample Prize for Outstanding Example Research; SSN: 123-45-6789")
    text = _render(tmp_path, [_entry(kept, "H"), _entry(dropped, "H", idx=1)])
    assert "123-45-6789" not in text
    assert PII_REDACTED_NOTICE in text
    assert "Example Award for Distinguished Example Work" in text


# --------------------------------------------------------------------------
# the Word comment on the notice (A-820 addendum)
# --------------------------------------------------------------------------

def test_withheld_notice_carries_one_word_comment_listing_categories(tmp_path):
    """Round trip: save, reopen the package, read the comment part back.
    Exactly one comment, on the notice, author "CViche", listing both
    categories with counts and sections and NOT the values -- and present
    with `emit_comments=False` (it is not a classification comment, #153)."""
    text = _render(tmp_path, [
        _entry("Date of Birth: 01/02/1970", "A"),
        _entry("Visa Status: O-1", "T"),
    ], emit_comments=False)
    assert "01/02/1970" not in text and "O-1" not in text
    assert PII_REDACTED_NOTICE in text
    comments = _comments(tmp_path / "out.docx")
    assert len(comments) == 1, comments
    author, body = comments[0]
    assert author == WITHHELD_COMMENT_AUTHOR
    lines = body.split("\n")
    assert lines[0] == WITHHELD_COMMENT_HEADER
    assert " • date of birth — 1 item, Personal Data" in lines
    assert " • visa / immigration status — 1 item, Appendix" in lines
    assert "01/02/1970" not in body and "O-1" not in body, "a withheld value re-leaked into the comment"


def test_withheld_comment_is_anchored_on_the_notice_paragraph(tmp_path):
    _render(tmp_path, [_entry("Date of Birth: 01/02/1970", "A")], emit_comments=False)
    doc = Document(str(tmp_path / "out.docx"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    anchored = [p for p in doc.paragraphs
                if p._p.find(f"{W}commentRangeStart") is not None]
    assert len(anchored) == 1
    assert PII_REDACTED_NOTICE in anchored[0].text


def test_withheld_comment_is_the_only_comment_even_with_classification_comments_on(tmp_path):
    """With emit_comments=True the Appendix's other bullets get their
    classification comments; the notice paragraph gets ONLY the withheld
    summary, not a second "Originally classified A" comment."""
    _render(tmp_path, [
        _entry("Date of Birth: 01/02/1970", "A"),
        _entry("Foreign Languages: French", "A"),
    ], emit_comments=True)
    comments = _comments(tmp_path / "out.docx")
    assert len([c for c in comments if c[0] == WITHHELD_COMMENT_AUTHOR]) == 1
    assert any("Originally classified" in body for _, body in comments), (
        "the classification comments were not emitted with emit_comments=True")
    doc = Document(str(tmp_path / "out.docx"))
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    notice = [p for p in doc.paragraphs if PII_REDACTED_NOTICE in p.text]
    assert len(notice) == 1
    assert len(notice[0]._p.findall(f"{W}commentRangeStart")) == 1


# --------------------------------------------------------------------------
# the deny predicate itself
# --------------------------------------------------------------------------

def test_deny_predicate_requires_a_colon_terminator():
    """The colon terminator is load-bearing in two separate ways, and each
    relaxation deletes real CV content.

    Allowing '-' or an en-dash as a terminator swallows publication and award
    titles that merely begin with a listed word. Allowing slop before the colon
    ("children" + anything + ':') swallows any title containing one. All of
    these strings, or their shapes, occur in the corpus.
    """
    for keeper in [
        # would match if '-' / en-dash were accepted as a terminator
        "Born - Digital: A Study of Youth Media Practices",
        "Children - Oncology Group Consortium, Emeritus",
        "Spouse – A Documentary Film Review",
        # would match if slop were allowed before the colon
        "Children and Fire: Research on Burn Prevention",
        "Children of the Pandemic: A Longitudinal Cohort",
        "Born in the USA: Birth Cohort Methods",
        # ordinary corpus content that must never match
        "Children's Oncology Group - Emeritus",
        "Children's Hospital Colorado - Pillar Award",
        "Religion and Healing in America",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"


def test_deny_predicate_catches_the_real_labels():
    for pii in [
        "Date of Birth: 12/13/1947",
        "PLACE OF BIRTH: Cincinnati, Ohio",
        "Born: December 13, 1965",
        "Marital Status: Married",
        "Spouse's name: Jane Roe",
        "Children: Ann, Bob",
        "D.O.B.: 1/1/1970",
    ]:
        assert _pii_fragments(pii), f"missed {pii!r}"


def test_person_field_keys_accept_a_suffix():
    """Stage 4 names these keys freely, so every person stem takes a suffix --
    'wife_name' must be caught wherever 'spouse_name' is. Anchoring is what
    keeps ordinary keys out, so the suffix costs no precision."""
    for key in ["spouse_name", "wife_name", "husband_name", "children_names",
                "dependents_names", "marital_status_children",
                "personal_wife", "birthplace", "dob", "ssn"]:
        assert _PII_FIELD_KEY_RE.match(key), f"missed {key!r}"
    for keeper in ["institutional_email", "office_address", "housewife"]:
        assert not _PII_FIELD_KEY_RE.match(keeper), f"false positive {keeper!r}"


def test_research_vocabulary_still_off_the_list_is_not_denied():
    """sex/race are still deliberately NOT on the list -- ordinary research
    vocabulary that occurs as publication titles, unchanged by #820.

    `gender`/`ethnicity`/`religion` ARE on the list since #820, but only at
    the Personal Data / Appendix scope (`SCOPE_PERSONAL_AND_APPENDIX`): the
    #473 control "Gender: A Review of the Literature" is restored for every
    routed content code, where the ambiguous rows never apply -- see
    `test_protected_class_labels_deny_only_the_colon_form` for the two
    scopes side by side."""
    for keeper in [
        "Sex: differences in galanin expression",
        "Race: reporting practices in clinical trials",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"
    for content_title in [
        "Gender: A Review of the Literature",
        "Children: Research, Practice and Policy",
        "Health: A Journal Title",
    ]:
        assert not _pii_fragments(content_title, SCOPE_ALL_CODES), (
            f"content-code false positive on {content_title!r}")


def test_protected_class_labels_deny_only_the_colon_form():
    """#820 comment: religion/ethnicity/gender/veteran status/disability/
    health/blood type join the vocabulary as colon-terminated labels --
    every one of these leaked on a real CV before this ticket.

    The colon terminator is the same guard #473 built for
    marital-status/spouse/children, applied to this class for the first
    time: a bare occurrence of the word, with no colon directly after it,
    must still pass through untouched -- these are the ticket's own named
    negative controls (`docs` A-820, MUST NOT list). A *colon-terminated*
    title starting with one of these words ("Gender: A Review of the
    Literature") is denied ONLY where the entry is A-coded or Appendix-bound
    (#820 round 2's scope split); on a routed content code it renders.
    """
    for pii in [
        "Religion: Catholic",
        "Ethnicity: Hispanic",
        "Gender: Female",
        "Veteran Status: Yes",
        "Disability: None",
        "Health: Good",
        "Blood Type: O+",
    ]:
        assert _pii_fragments(pii), f"missed {pii!r}"
    for keeper in [
        "Health Sciences",
        "Public Health",
        "Gender Medicine",
        "Veterans Affairs Medical Center",
        "Age-related macular degeneration",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"
