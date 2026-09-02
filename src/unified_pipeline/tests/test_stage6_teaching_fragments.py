"""Section K (teaching) newline-blind fix (#476, PR1 of the accuracy wave).

`teaching.py:149` and `:172` were two of the ten `entry_lines`-based call
sites, on two DIFFERENT variables -- not compared to each other, so this
section's own decision is made per call site rather than as one migration:

`:149` -- `original_lines = entry_lines(original_text)`, inside the
`if formatted_text and original_text:` branch -- answers "did Stage 5c fuse
multiple distinct source entries into one formatted blob?" (the comment's
own words: "Stage 5c may have over-combined"). Switching it to
`entry_fragments` was tried and reverted after reading what it actually does
to the farm: 495 K entries are genuinely ONE teaching record whose raw text
happens to be tab-joined ("Title\\tDate\\tDescription" -- e.g. TDXCPW's
"Leadership in education, Duke Psychiatry Residency Program\\tJuly 2020 -
June 2024\\tSelected Initiatives:..."), which Stage 5c already reformats
correctly into one well-structured bullet with its own markdown sub-bullets
("**2020-2024** - **Course Director**...\\n  - Selected initiatives...").
`entry_fragments` makes every one of those 495 look like a multi-item entry,
which takes the OTHER branch and replaces that one good, LLM-formatted
bullet with several raw, unformatted fragment bullets (title, date and
description as three separate bullets with no attempt at prose). This is
a regression, not a fix, and `test_regression_guard_...` below pins it by
showing what the discarded change would have done.

`:172` -- `lines = entry_lines(new_text)`, inside the `elif formatted_text:`
branch (only reached when `original_text` is EMPTY) -- has nothing to
compare against, so the "did 5c over-combine" question doesn't apply; it
just decides how many `'. '`-joined pieces of Stage 5c's OWN prose to
combine. Migrated to `_fragment_parts` (entry_fragments-based). 0 farm K
entries reach this branch with either text present, so the positive control
is synthetic (§6.5 hole, disclosed in the PR body).

This branch turns out to be unreachable through `_insert_teaching_entry` as
written, for a reason that has nothing to do with #476:
`_is_structural_label(entry)` (parsing/text.py, unrelated, pre-existing)
returns True -- and the function returns immediately -- whenever
`entry.get('text', '')` is falsy, which is exactly the condition the
`elif formatted_text:` branch needs to be reached at all. The positive and
negative controls below patch `_is_structural_label` out for the one test
that needs to exercise the branch itself, which is the honest way to pin
what the changed line does without either fabricating a reachable-looking
fixture or silently declining to test :172 the way the other four sections'
positive controls are tested end to end.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_teaching_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_fragments, entry_lines  # noqa: E402
from unified_pipeline.stage6.sections import teaching  # noqa: E402
from unified_pipeline.stage6.sections.teaching import _fragment_parts  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


MULTILINE_CASES = [
    "First point\nSecond point",
    "**2020** - Course Director\n  - Detail one\n  - Detail two",
]


def _generator_with_trailing_paragraph():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("HEADING")
    gen.doc.add_paragraph("SENTINEL-END")
    return gen


# --- (a) no line the old newline split produced is lost (the :172 helper) --

def test_fragment_parts_keeps_every_entry_lines_part():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1
        assert _fragment_parts(case) == old, f"diverged on {case!r}"


# --- (b) positive control (:172): fails on dev today ------------------------

def test_positive_control_172_pipe_blind_formatted_text_gains_both_parts(monkeypatch):
    """`elif formatted_text:` branch: original_text is empty, formatted_text
    is a single-line, pipe-joined blob. entry_lines returns ONE part, so
    combined_text is that whole unsplit blob today. Fails on dev.

    `_is_structural_label` is patched out (see module docstring): it always
    returns True when `entry.get('text', '')` is falsy, which is exactly
    the condition this branch needs -- unrelated, pre-existing gate, not
    something #476 touches.
    """
    monkeypatch.setattr(teaching, "_is_structural_label", lambda entry: False)
    entry = {
        "text": "",
        "extracted_fields": {
            "formatted_text": "Course Director, Internal Medicine Clerkship | Preceptor, Ambulatory Clinic",
        },
    }
    gen = _generator_with_trailing_paragraph()
    gen._insert_teaching_entry(1, entry, is_first_visible=False)
    texts = [p.text for p in gen.doc.paragraphs]
    matches = [t for t in texts if "Course Director" in t]
    assert len(matches) == 1, texts
    assert matches[0] == "Course Director, Internal Medicine Clerkship. Preceptor, Ambulatory Clinic"


# --- (c) negative control: single-part text is unchanged --------------------

def test_negative_control_172_single_part_unchanged(monkeypatch):
    monkeypatch.setattr(teaching, "_is_structural_label", lambda entry: False)
    entry = {
        "text": "",
        "extracted_fields": {"formatted_text": "Course Director, Internal Medicine Clerkship"},
    }
    gen = _generator_with_trailing_paragraph()
    gen._insert_teaching_entry(1, entry, is_first_visible=False)
    texts = [p.text for p in gen.doc.paragraphs]
    assert "Course Director, Internal Medicine Clerkship" in texts


# --- :149 regression guard: pins the DECLINED migration ---------------------

def test_regression_guard_149_entry_fragments_would_wrongly_fragment_the_farm_shape():
    """Not a behaviour pin -- a documentation-as-code guard for why :149
    keeps `entry_lines`. TDXCPW's real farm shape: tab-joined single record,
    Stage 5c already reformats it well. entry_lines correctly sees ONE line
    (uses the good formatted_text); entry_fragments would see 3+ fragments
    (would wrongly take the "multi-item, use raw lines" branch and discard
    the LLM formatting). If this test ever fails, the farm shape changed and
    :149's discarded migration needs re-evaluating, not silently redone.
    """
    original_text = (
        "Leadership in education, Duke Psychiatry Residency Program"
        "\tJuly 2020 – June 2024"
        "\tSelected Initiatives: Co-designed the curriculum."
    )
    assert len(entry_lines(original_text)) == 1
    assert len(entry_fragments(original_text)) > 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
