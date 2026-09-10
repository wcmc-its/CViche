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
    _entry_rendered,
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
