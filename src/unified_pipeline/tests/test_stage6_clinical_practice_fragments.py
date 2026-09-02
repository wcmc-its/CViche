"""Section L (clinical practice) newline-blind fix (#476, PR1 of the
accuracy wave).

`clinical_practice.py:480` -- `_insert_multiline_as_bullets`, the bullet
fallback shared by all three L1/L2/L3 subsections -- was one of the ten
`entry_lines`-based call sites. Its docstring's own framing ("if the source
had multiple lines, each becomes its own bullet") extends naturally to a
tab-joined blind entry, but this file also uses '|' throughout as a COLUMN
separator within one entry (`_fill_clinical_practice_l1/l2/l3`'s own
`.split('|')` calls at lines 236, 322, 405 pull Title/Institution/Dates
apart as fields of ONE row, never as separate items) -- so unlike positions,
honors and memberships, '|' must stay OUT of this call site's split, or a
single "Role | Institution | Dates" bullet would be torn into three wrong
ones.

`_bullet_parts` (module-level) replaces `entry_lines` here: '\\n' and '\\t'
are bullet boundaries, '|' is not.

Reachability, read from the actual call graph rather than assumed: none of
the three fallback callers can currently hand this function a raw,
un-welded tab.
 - L1 and L2 (`:264`, `:347`) weld every tab in `bullet_text` away before
   calling it (`.replace('\\t', ' - ', 1).replace('\\t', ' ')`).
 - L3 (`_fill_clinical_practice_l3`) looked like the exception -- its
   fallback keeps raw `original_text` when `role` can't be derived -- but
   `original_text = entry.get('text', '').strip()` runs first, which strips
   a leading tab away before `role = original_text.split('\\t')[0].strip()`
   ever sees it; `role` is therefore empty only when the WHOLE text is
   empty, at which point `bullet_text` is empty too and
   `_insert_multiline_as_bullets` is never called. A text with a tab
   in the *middle* still reaches this function un-welded, but only through
   the `elif role: bullet_text = role` branch, which discards everything
   after the first tab-segment before `_insert_multiline_as_bullets` ever
   sees it -- a separate, pre-existing data-loss bug in that branch, not an
   entry_lines/entry_fragments question and out of this PR's scope.

So this call site's fix is real but currently dormant through all three of
its own callers -- the render gate confirms it (CHANGED 0 on the farm). The
positive control below calls `_insert_multiline_as_bullets` directly, which
is legitimate: it is the function this commit changes, reachable through
`_fill_clinical_practice` in principle, and would fire the moment any future
caller (or a fix to the L3 branch above) hands it raw tab-joined text
instead of pre-processing it away first.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_clinical_practice_fragments.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.clinical_practice import _bullet_parts  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


MULTILINE_CASES = [
    "First activity\nSecond activity",
    "Chair, Ethics Committee\nMember, IRB\nMember, P&T Committee",
]

# A raw pipe-column entry (Title | Institution | Dates): must stay ONE
# part, matching every table-fill branch's own treatment of '|' as a
# column separator, not an item separator.
PIPE_COLUMN_CASE = "Volunteer Clinic | NYP Weill Cornell | 2020-2023"


def _generator_with_trailing_paragraph():
    """A real WCMTemplateGenerator with a heading and a trailing sentinel
    paragraph -- `_insert_bulleted_entry` inserts BEFORE a paragraph at
    insert_idx and bails out if insert_idx is out of range, so something
    must follow the insertion point."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("HEADING")
    gen.doc.add_paragraph("SENTINEL-END")
    return gen


# --- (a) no line the old newline split produced is lost ---------------------

def test_multiline_cases_unchanged():
    for case in MULTILINE_CASES:
        old = entry_lines(case)
        assert len(old) > 1
        assert _bullet_parts(case) == old, f"diverged on {case!r}"


def test_pipe_stays_one_part():
    """'|' is a column separator throughout this file, not a bullet
    boundary -- unlike positions/honors/memberships, it must NOT split."""
    assert _bullet_parts(PIPE_COLUMN_CASE) == [PIPE_COLUMN_CASE]
    assert entry_lines(PIPE_COLUMN_CASE) == [PIPE_COLUMN_CASE]


# --- (b) positive control: fails on dev today --------------------------------

def test_positive_control_tab_joined_text_splits_into_two_bullets():
    """`_insert_multiline_as_bullets` called directly with a raw tab-joined
    text -- the shape none of this file's own 3 callers can currently
    produce (see module docstring), but the exact shape #476 is about.
    entry_lines returns ONE part (no '\\n'), so today this renders as a
    single bullet with a literal tab character embedded in it. Fails on
    dev.
    """
    gen = _generator_with_trailing_paragraph()
    inserted = gen._insert_multiline_as_bullets(
        1, "Chair, Ethics Committee\tMember, IRB", entry=None, add_blank_before=False)
    assert inserted == 2
    texts = [p.text for p in gen.doc.paragraphs]
    assert "Chair, Ethics Committee" in texts
    assert "Member, IRB" in texts
    assert not any("\t" in t for t in texts)


# --- (c) negative control: a single-part entry is unchanged ------------------

def test_negative_control_single_part_text_unchanged():
    gen = _generator_with_trailing_paragraph()
    inserted = gen._insert_multiline_as_bullets(
        1, "Volunteer Clinic | NYP Weill Cornell | 2020-2023", entry=None, add_blank_before=False)
    assert inserted == 1
    # _insert_bulleted_entry runs _clean_inline_tabs on the way in, which
    # welds '|'/'\t' into ' - ' for display -- pre-existing, unrelated to
    # this fix. The point pinned here is the PART count: one part in, one
    # paragraph out, not three.
    texts = [p.text for p in gen.doc.paragraphs]
    assert "Volunteer Clinic — NYP Weill Cornell — 2020-2023" in texts


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
