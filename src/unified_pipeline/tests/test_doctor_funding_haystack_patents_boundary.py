"""Pins #492: the Title-Case "Patents & Inventions" heading must close a
funding segment in `_funding_haystacks`
(`unified_pipeline/doctor/lints/extraction.py`), the same way an ALL-CAPS or
lettered "X. " output-section header already does.

`_output_section_header` (`doctor/shared.py`) only recognises a lettered
"X. " prefix or an ALL-CAPS paragraph. Stage 6 renders M2D's heading as
Title-Case ("Patents & Inventions", written by `_fill_patents` in
`stage6/sections/patents.py`, called immediately after the M2A/B/C funding
fill in `stage_6_word_template.py`'s `generate()`), so it matched neither
form and the pending-funding (M2C) haystack kept absorbing every block of
the patents section up to the next ALL-CAPS header ("MENTORING").

The fix adds a local `_FUNDING_BOUNDARY_TITLES` frozenset checked inside the
`_funding_haystacks` loop, closing a segment on any boundary title too.

Run:
    python3 -m pytest src/unified_pipeline/tests/test_doctor_funding_haystack_patents_boundary.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.doctor.lints.extraction import _funding_haystacks  # noqa: E402

_PATENT_LINE = ("Method for Targeted Gene Delivery Using Modified Viral "
                "Vectors, US Patent 11,234,567, filed 2022, issued 2024")

_GRANT_LINE = ("Pending R01 Grant Application, National Institutes of "
              "Health, submitted 2025, status pending review")


def test_patents_heading_closes_the_m2c_haystack():
    # FAILS on unpatched dev: the patent line leaks into the M2C haystack
    # because the Title-Case "Patents & Inventions" heading closes nothing.
    blocks = [
        ("p", "Pending Funding"),
        ("table", _GRANT_LINE),
        ("p", "Patents & Inventions"),
        ("p", "Please include inventors, title of invention and patent number."),
        ("table", _PATENT_LINE),
    ]
    haystacks = _funding_haystacks(blocks)
    m2c = haystacks["M2C"]

    # `.text` is whitespace-squashed (`_squash`), so check squashed substrings.
    assert "pendingr01grant" in m2c.text.lower()
    assert "modifiedviralvectors" not in m2c.text.lower()
    assert "vectors" not in m2c.tokens


def test_non_boundary_paragraph_keeps_segment_open():
    # Guards against an over-broad fix that closes a segment on ANY
    # unrecognised "p" block rather than just the named boundary titles: a
    # plain non-heading paragraph (the kind of placeholder text stage 6
    # writes into an empty M2C, e.g. "Please summarize as for current
    # projects...") must stay part of the open funding segment, not get
    # dropped the way it would if `current` were reset on every "p" that
    # fails the `_FUNDING_SECTIONS` title match.
    placeholder_line = "Please summarize as for current projects, including sponsor."
    blocks = [
        ("p", "Pending Funding"),
        ("p", placeholder_line),
        ("table", _GRANT_LINE),
        ("p", "Patents & Inventions"),
        ("table", _PATENT_LINE),
    ]
    haystacks = _funding_haystacks(blocks)
    m2c = haystacks["M2C"]

    assert "pleasesummarizeasforcurrentprojects" in m2c.text.lower()
    assert "pendingr01grant" in m2c.text.lower()
    assert "modifiedviralvectors" not in m2c.text.lower()


def test_m2a_to_m2b_transition_still_works():
    # The existing header-title boundary (an in-set funding title) must keep
    # working unchanged -- this fix only ADDS a boundary set, it must not
    # regress the plain funding-to-funding transition.
    current_line = "Current award still open, National Science Foundation"
    past_line = "Completed award from 2019, closed out in 2021"
    blocks = [
        ("p", "Current Research Funding"),
        ("table", current_line),
        ("p", "Past (Completed) Funding"),
        ("table", past_line),
    ]
    haystacks = _funding_haystacks(blocks)

    assert "nationalsciencefoundation" in haystacks["M2A"].text.lower()
    assert "closedoutin2021" not in haystacks["M2A"].text.lower()
    assert "closedoutin2021" in haystacks["M2B"].text.lower()
