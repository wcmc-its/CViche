"""Regression guard for the contact half of issue #427: the scorer reported
"no contact found" on CVs that plainly carry contact details.

`score_cv_owner` tested exactly three literal field names -- "email", "phone",
"address" -- but stage 4 stores raw LLM JSON with no schema, so it files contact
under whatever key the model picked. Across the 100-CV corpus of the 2026-07-25
batch it emitted institutional_email (18 entries), personal_email (9), fax (7),
primary_email (4), home_address, office_address, work_phone, home_phone, cell,
mobile_phone_primary and secondary_phone -- none of which the tuple matched.
Two CVs (web136, web15) took the 0.3 `not any_contact` penalty with a perfectly
good institutional email sitting in the fields JSON.

    python3 -m pytest src/unified_pipeline/tests/test_quality_score_contact_keys.py -p no:cacheprovider

Self-contained: no DB, no network, no PII.
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.quality_score import score_cv_owner  # noqa: E402


def _fields(tmp_path, extracted_fields):
    """Write a minimal stage-4 fields JSON with a valid owner name."""
    payload = {
        "document_uid": "TESTAA",
        "cv_owner": {"first_name": "Jane", "last_name": "Public",
                     "full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True,
                              "primary_location": "New York, NY"},
        "entries": [{"taxonomy_code": "A", "extracted_fields": extracted_fields}],
    }
    (tmp_path / "TESTAA_fields.json").write_text(json.dumps(payload))
    return tmp_path


def _any_contact(tmp_path, extracted_fields) -> bool:
    _, detail, _ = score_cv_owner(_fields(tmp_path, extracted_fields))
    assert "any_contact=" in detail
    return "any_contact=True" in detail


def test_plain_key_names_still_count(tmp_path):
    """The three names the scorer always handled must keep working."""
    for key in ("email", "phone", "address"):
        assert _any_contact(tmp_path, {key: "something"}), key


def test_the_key_names_stage_4_actually_emits_count(tmp_path):
    """Every variant observed in the 100-CV corpus. This is the #427 bug."""
    for key in ("institutional_email", "personal_email", "primary_email",
                "work_email", "home_address", "office_address", "business_address",
                "work_phone", "home_phone", "phone_office", "secondary_phone",
                "mobile_phone_primary", "cell", "fax", "mobile", "telephone"):
        assert _any_contact(tmp_path, {key: "something"}), \
            f"{key} is contact information and was scored as absent"


def test_a_blank_value_is_not_contact(tmp_path):
    """None means the field was extracted and found empty."""
    assert not _any_contact(tmp_path, {"institutional_email": None})
    assert not _any_contact(tmp_path, {})


def test_unrelated_fields_are_not_mistaken_for_contact(tmp_path):
    """The match is on the key name, so it must not over-fire."""
    for key in ("title", "institution", "degrees", "narrative", "year",
                "department", "organization", "employer", "role", "journal"):
        assert not _any_contact(tmp_path, {key: "something"}), \
            f"{key} is not contact information"


def test_no_contact_still_takes_the_penalty(tmp_path):
    """The dimension must keep firing when the CV really has no contact."""
    fraction, detail, cap = score_cv_owner(_fields(tmp_path, {"title": "Professor"}))
    assert "any_contact=False" in detail
    assert fraction >= 0.3, "the missing-contact penalty must survive this change"
    assert cap is None, "this is not the hard-fail path"



# --- source-blank contact is an input gap, not an extraction miss (#427) ---

def _entries(tmp_path, *texts):
    """Write a minimal stage-2 entries JSON (synthetic text only)."""
    payload = {"document_uid": "TESTAA",
               "entries": [{"text": t} for t in texts]}
    (tmp_path / "TESTAA_entries.json").write_text(json.dumps(payload))


def _no_contact_score(tmp_path, *source_texts):
    _entries(tmp_path, *source_texts)
    return score_cv_owner(_fields(tmp_path, {"title": "Professor"}))


def test_a_source_with_no_email_or_phone_is_not_penalized(tmp_path):
    """C0ZGFW's shape: the PERSONAL DATA block is empty labels."""
    fraction, detail, _ = _no_contact_score(
        tmp_path, "PERSONAL DATA", "Office Address:", "Email:", "Phone:",
        "2010-2015 Assistant Professor, Example University")
    assert "source_contact=False" in detail
    assert fraction == 0.0


def test_a_missed_email_in_the_source_keeps_the_penalty(tmp_path):
    fraction, detail, _ = _no_contact_score(tmp_path, "Email: jane@example.org")
    assert "source_contact=True" in detail
    assert fraction >= 0.3


def test_a_missed_phone_in_the_source_keeps_the_penalty(tmp_path):
    for phone in ("(212) 555-0100", "212-555-0100", "212.555.0100",
                  "+1 212 555 0100", "212-555-0100x12"):
        fraction, detail, _ = _no_contact_score(tmp_path, f"Tel: {phone}")
        assert "source_contact=True" in detail, phone
        assert fraction >= 0.3, phone


def test_doi_pmid_and_grant_digit_runs_are_not_phone_numbers(tmp_path):
    """3-3-4 digit runs inside identifiers were the census's false hits."""
    fraction, detail, _ = _no_contact_score(
        tmp_path,
        "Smith J. Title. J Med. 2019. doi:10.1016/j.123-456-7890",
        "https://example.org/abs/212-555-0100/full",
        "Grant R01-212-555-0100 (PI)",
        "Accession 212 555 01001234")
    assert "source_contact=False" in detail
    assert fraction == 0.0


def test_a_certificate_number_then_a_year_is_not_a_phone_number(tmp_path):
    """#822: a six-digit certificate number followed by a year read as a
    phone number, so a CV with no contact details lost 4.5 points."""
    fraction, detail, _ = _no_contact_score(
        tmp_path,
        "Example Board of Medicine, Certificate #123456 2005, Example State",
        "License 654321 2012, Example Agency")
    assert "source_contact=False" in detail
    assert fraction == 0.0


def test_a_phone_without_a_separator_after_the_parenthesised_area_code_still_counts(tmp_path):
    for phone in ("(212)555-0100", "212 555 0100"):
        _, detail, _ = _no_contact_score(tmp_path, f"Tel: {phone}")
        assert "source_contact=True" in detail, phone


def test_extracted_contact_does_not_consult_the_source(tmp_path):
    _entries(tmp_path, "no contact here")
    _, detail, _ = score_cv_owner(_fields(tmp_path, {"email": "x"}))
    assert "source_contact=None" in detail


def test_unreadable_entries_json_keeps_the_penalty(tmp_path):
    (tmp_path / "TESTAA_entries.json").write_text("{not json")
    fraction, detail, _ = score_cv_owner(_fields(tmp_path, {"title": "Professor"}))
    assert "source_contact=None" in detail
    assert fraction >= 0.3

if __name__ == "__main__":
    import tempfile
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            with tempfile.TemporaryDirectory() as d:
                _fn(Path(d))
    print("OK")
