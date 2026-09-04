"""Section K (teaching) newline-blind fix (#476, PR1 of the accuracy wave),
and the delimiter contract review thread 3927100368 asked to have pinned.

Section K reconstructs an entry's bullet text at two call sites that read two
DIFFERENT variables, so the section's delimiter decision is made per call site
rather than as one migration:

The RAW-text side -- `original_lines = entry_lines(original_text)`, inside
`_teaching_entry_lines`' `if formatted_text and original_text:` branch --
answers "did Stage 5c fuse multiple distinct source entries into one formatted
blob?" ("Stage 5c may have over-combined"). Switching it to `entry_fragments`
was tried and reverted after reading what it actually does to the farm: 456 of
722 K entries are genuinely ONE teaching record whose raw text happens to be
tab-joined ("Title\\tDate\\tDescription"), which Stage 5c already reformats
correctly into one well-structured bullet with its own markdown sub-bullets
("**2020-2024** - **Course Director**...\\n  - Selected initiatives...").
`entry_fragments` makes every one of those look like a multi-item entry, which
takes the OTHER branch and replaces one good, LLM-formatted bullet with several
raw, unformatted fragment bullets (title, date and description as three
separate bullets with no attempt at prose). That is a regression, not a fix,
and `test_regression_guard_raw_text_...` below pins it by showing what the
discarded change would have done. The same reading is why the last fallback
(raw text, no formatted_text, no course_title) also keeps the newline-only
split: tab-splitting it turns 19 farm entries into 41 bullets, 16 of them a
bare date, when `_clean_inline_tabs` already renders the untouched row as
"Title: Date — Description".

The Stage-5c-prose side -- `_item_parts(...)` inside the `elif formatted_text:`
branch, reached only when `original_text` is EMPTY -- has nothing to compare
against, so the "did 5c over-combine" question doesn't apply; it just decides
how many `'. '`-joined pieces of Stage 5c's OWN prose to combine. It splits on
newlines and tabs, and deliberately NOT on `|`: a pipe joins the cells of one
row, and `_clean_inline_tabs` already renders it as " — " inside a single
bullet (review thread 3927100368, item 4). 0 farm K entries reach this branch
with either text present, so the positive control is synthetic (§6.5 hole,
disclosed in the PR body).

This branch turns out to be unreachable through `_insert_teaching_entry` as
written, for a reason that has nothing to do with #476:
`_is_structural_label(entry)` (parsing/text.py, unrelated, pre-existing)
returns True -- and the function returns immediately -- whenever
`entry.get('text', '')` is falsy, which is exactly the condition the
`elif formatted_text:` branch needs to be reached at all. The positive and
negative controls below patch `_is_structural_label` out for the tests that
need to exercise the branch itself, which is the honest way to pin what the
changed line does without either fabricating a reachable-looking fixture or
silently declining to test it the way the other four sections' positive
controls are tested end to end.

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
from unified_pipeline.stage6.sections.teaching import _item_parts  # noqa: E402
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


# --- (a) no line the old newline split produced is lost ---------------------

def test_item_parts_keeps_every_entry_lines_part():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1
        assert _item_parts(case) == old, f"diverged on {case!r}"


# --- (b) the delimiter contract: tab and newline split, '|' does not --------

def test_item_parts_splits_tabs_but_not_pipes():
    """Review thread 3927100368 item 4. `|` joins the cells of ONE item
    ("Role | Institution") and `_clean_inline_tabs` renders it as " — " in a
    single bullet, so making it an item separator would split one teaching
    record into two. A future swap of `_item_parts` for `entry_fragments`
    would do exactly that; this is the test that stops it.
    """
    mixed = "Role A | Institution A\tRole B | Institution B"
    assert _item_parts(mixed) == ["Role A | Institution A", "Role B | Institution B"]
    # entry_fragments is the function NOT to use here, and this is why.
    assert len(entry_fragments(mixed)) == 4


# --- (c) positive control: the tab-joined blob gains both parts -------------

def test_positive_control_pipe_blind_formatted_text_gains_both_parts(monkeypatch):
    """`elif formatted_text:` branch: original_text is empty, formatted_text
    is a single-line, tab-joined blob. entry_lines returns ONE part, so
    combined_text was that whole unsplit blob before #476.

    `_is_structural_label` is patched out (see module docstring): it always
    returns True when `entry.get('text', '')` is falsy, which is exactly
    the condition this branch needs -- unrelated, pre-existing gate, not
    something #476 touches.
    """
    monkeypatch.setattr(teaching, "_is_structural_label", lambda entry: False)
    entry = {
        "text": "",
        "extracted_fields": {
            "formatted_text": "Course Director, Internal Medicine Clerkship\tPreceptor, Ambulatory Clinic",
        },
    }
    gen = _generator_with_trailing_paragraph()
    gen._insert_teaching_entry(1, entry, is_first_visible=False)
    texts = [p.text for p in gen.doc.paragraphs]
    matches = [t for t in texts if "Course Director" in t]
    assert len(matches) == 1, texts
    assert matches[0] == "Course Director, Internal Medicine Clerkship. Preceptor, Ambulatory Clinic"


def test_pipe_joined_formatted_text_stays_one_item(monkeypatch):
    """Same branch, the other half of the contract: a pipe is not an item
    separator, so the blob stays one bullet and `_clean_inline_tabs` renders
    the pipe as " — " rather than the two ". "-joined items entry_fragments
    would have produced.
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
    matches = [p.text for p in gen.doc.paragraphs if "Course Director" in p.text]
    assert len(matches) == 1
    assert matches[0] == "Course Director, Internal Medicine Clerkship — Preceptor, Ambulatory Clinic"


# --- (d) negative control: single-part text is unchanged --------------------

def test_negative_control_single_part_unchanged(monkeypatch):
    monkeypatch.setattr(teaching, "_is_structural_label", lambda entry: False)
    entry = {
        "text": "",
        "extracted_fields": {"formatted_text": "Course Director, Internal Medicine Clerkship"},
    }
    gen = _generator_with_trailing_paragraph()
    gen._insert_teaching_entry(1, entry, is_first_visible=False)
    texts = [p.text for p in gen.doc.paragraphs]
    assert "Course Director, Internal Medicine Clerkship" in texts


# --- raw-text regression guard: pins the DECLINED migration -----------------

def test_regression_guard_raw_text_entry_fragments_would_wrongly_fragment_the_farm_shape():
    """Not a behaviour pin -- a documentation-as-code guard for why the raw
    text paths keep `entry_lines`. The real farm shape: tab-joined single
    record, Stage 5c already reformats it well. entry_lines correctly sees ONE
    line (uses the good formatted_text); entry_fragments would see 3+ fragments
    (would wrongly take the "multi-item, use raw lines" branch and discard
    the LLM formatting). If this test ever fails, the farm shape changed and
    the discarded migration needs re-evaluating, not silently redone.
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
