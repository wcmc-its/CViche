"""Regression guard for issue #457: the document uid was written in as the CV
owner's surname.

When name extraction yielded nothing, `fallback_from_uid()` derived a surname
from the document uid. That is correct for filename-style uids
('2097_Upton_Cv' -> 'Upton') and is kept. It was wrong for opaque ones: all five
hard-fail CVs in the 2026-07-25 corpus batch carried

    "cv_owner": {"first_name": "", "full_name": "", "last_name": "web151"}

A manufactured surname is worse than an empty one. It looks plausible, defeats
emptiness checks in spirit, and feeds `add_target_names` and the bibliography
author bolding a token that matches nothing in the document.

Also pinned here: the uid suffix strip was case-sensitive (`_[Cc]v$`), so a CV
named '2026_OBrien_CV' produced the literal 'CV' as the owner's surname.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_uid_surname_fallback.py -p no:cacheprovider

Self-contained: no DB, no network, no LLM, no PII.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_4_field_extractor import extract_cv_owner_name  # noqa: E402


def _owner(uid, entries=None):
    """Run owner extraction with no usable entries, forcing the uid fallback."""
    return extract_cv_owner_name(uid, entries or [])


def test_an_opaque_uid_never_becomes_a_surname():
    """The #457 case: every corpus uid is webNNN and all five hard-fails got it."""
    for uid in ("web151", "web094", "web07", "I5NKUG", "C0ZGFW", "2Q1ZQ"):
        owner = _owner(uid)
        assert owner.get("last_name", "") == "", \
            f"{uid!r} was written in as a surname"


def test_a_filename_style_uid_still_yields_its_name():
    """This behaviour is useful and is deliberately kept."""
    assert _owner("2097_Upton_Cv").get("last_name") == "Upton"
    assert _owner("WSP0KQ_Smith_Cv").get("last_name") == "Smith"


def test_the_cv_suffix_strip_is_case_insensitive():
    """'_CV' was not stripped, so the surname came out as the literal 'CV'."""
    owner = _owner("2026_OBrien_CV")
    assert owner.get("last_name") != "CV", "the file extension became the surname"
    assert owner.get("last_name") == "OBrien"


def test_a_missing_name_stays_empty_rather_than_plausible():
    """Downstream reads this; a fabricated token is worse than nothing."""
    owner = _owner("web151")
    assert owner.get("full_name", "") == ""
    assert owner.get("first_name", "") == ""
    assert owner.get("last_name", "") == ""


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("OK")
