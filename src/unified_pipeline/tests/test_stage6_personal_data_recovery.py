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
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    PII_REDACTED_NOTICE,
    WCMTemplateGenerator,
    _pii_fragments,
)

W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def _all_text(docx_path) -> str:
    doc = Document(str(docx_path))
    return "\n".join(n.text or "" for n in doc.element.body.iter(W_T))


def _render(tmp_path, entries, cv_owner=None) -> str:
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    payload = {"document_uid": "TESTPD", "entries": entries}
    if cv_owner:
        payload["cv_owner"] = cv_owner
    ip.write_text(json.dumps(payload))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return _all_text(op)


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


def test_research_vocabulary_is_not_denied():
    """gender/sex/race/ethnicity/religion are deliberately NOT on the list:
    they are ordinary research vocabulary and occur as publication titles."""
    for keeper in [
        "Gender: A Review of the Literature",
        "Sex: differences in galanin expression",
        "Race: reporting practices in clinical trials",
    ]:
        assert not _pii_fragments(keeper), f"false positive on {keeper!r}"
