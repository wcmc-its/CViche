"""Section L (clinical practice) fragment recovery (#476) and the PR review
round-1 fixes to it.

`_insert_multiline_as_bullets` -- the bullet fallback shared by all three
L1/L2/L3 subsections -- was one of the ten `entry_lines`-based call sites.
It now segments through `_bullet_parts`: '\\n' and '\\t' are bullet
boundaries, '|' is NOT, because '|' is this file's own column separator at
every table-fill branch (Title/Location/Dates are fields of ONE entry).

The review round found the first cut of that change did not actually reach
the rendered document, and these tests are written end to end through the
real WCM template so the same thing cannot happen again. Driving
`_fill_clinical_practice_l1/l2/l3` over the shipped blank template with one
tab-joined entry produced, before the fix:

    L1, L2   ONE bullet, "Role — Institution Dates". The callers welded the
             tabs first -- first tab to " — ", every later tab to a bare
             space -- so the split never saw a tab and parts 2 and 3 ran
             together with no separator at all.
    L3       ONE bullet, "Role". `original_text.split('\\t')[0]` kept the
             first fragment as the role and dropped the rest, so Institution
             and Dates were deleted from the document outright.

and after it, one bullet per fragment on all three. A third defect surfaced
only once those end-to-end tests existed: the first entry of a subsection
also inserts a blank spacer paragraph, which the return value did not count,
so from the second entry onward each insertion index was one paragraph too
high and the second entry's bullets were interleaved into the middle of the
first entry's.

Every test below drives a real `WCMTemplateGenerator`. The three that must
prove the *production* path load the shipped template and assert
`tables_populated == 0` first, so if the template ever grows a real clinical
table the test says so instead of quietly measuring nothing.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_clinical_practice_fragments.py -p no:cacheprovider
"""

import difflib
import sys
from collections import namedtuple
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.core.render_check import entry_lines  # noqa: E402
from unified_pipeline.stage6.sections.clinical_practice import _bullet_parts  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# --- fixtures ---------------------------------------------------------------

# The scratch document's trailing paragraph. `_insert_bulleted_entry` inserts
# BEFORE the paragraph at insert_idx and returns None when insert_idx is past
# the end, so something has to follow the insertion point.
SENTINEL_TEXT = "SENTINEL-END"
HEADING_TEXT = "HEADING"

# gen        -- a real WCMTemplateGenerator over the scratch document
# insert_idx -- the index bullets are inserted before, derived from the
#               sentinel's own position rather than hard-coded
# baseline   -- the paragraph texts before anything was inserted
_Scratch = namedtuple("_Scratch", ["gen", "insert_idx", "baseline"])


def _scratch_document():
    """A two-paragraph scratch document plus its named insertion point.

    The insertion index is read back off the sentinel paragraph instead of
    being written as a literal at every call site, so adding a paragraph to
    this fixture cannot silently move where a test thinks it is inserting
    (PR #733 review, test item 8).
    """
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph(HEADING_TEXT)
    gen.doc.add_paragraph(SENTINEL_TEXT)
    baseline = [p.text for p in gen.doc.paragraphs]
    return _Scratch(gen, baseline.index(SENTINEL_TEXT), baseline)


def _template_generator():
    """A generator over the real WCM template -- the document every
    production run of stage 6 starts from."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _inserted_paragraphs(before, after):
    """The paragraph texts `after` has that `before` did not, in document
    order. Used instead of a fixed slice so a test states what the renderer
    ADDED, not where the template happens to put its headers."""
    added = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(after[j1:j2])
    return added


# code, the filler method under test, the template header it writes under
SUBSECTIONS = [
    ("L1", "_fill_clinical_practice_l1", "Clinical Practice"),
    ("L2", "_fill_clinical_practice_l2", "Clinical Innovations"),
    ("L3", "_fill_clinical_practice_l3", "Clinical Leadership"),
]


def _render_through_template(filler_name, entries, route_overflow=False):
    """Drive one subsection filler over the real template and return
    (generator, paragraphs it inserted).

    `route_overflow` additionally runs `_route_overflow_entries`, the step a
    real `run_stage6` runs after every section is filled. Stopping before it
    hides a whole class of defect: the router re-emits an entry it judges
    under-rendered, so a section can render correctly and still put its
    content in the document twice."""
    gen = _template_generator()
    before = [p.text for p in gen.doc.paragraphs]
    getattr(gen, filler_name)(entries)
    if route_overflow:
        gen._route_overflow_entries()
    after = [p.text for p in gen.doc.paragraphs]
    return gen, _inserted_paragraphs(before, after)


def _entry(text, **fields):
    return {"text": text, "extracted_fields": dict(fields)}


# --- the shapes under test ---------------------------------------------------

TAB_JOINED = "Attending Physician\tNYP Weill Cornell\t2015-2020"
TAB_JOINED_PARTS = ["Attending Physician", "NYP Weill Cornell", "2015-2020"]

# A raw pipe-column entry (Title | Institution | Dates): ONE item, matching
# every table-fill branch's own treatment of '|' as a column separator.
PIPE_COLUMN_CASE = "Volunteer Clinic | NYP Weill Cornell | 2020-2023"
PIPE_COLUMN_RENDERED = "Volunteer Clinic — NYP Weill Cornell — 2020-2023"

MIXED_DELIMITER_CASE = "Alpha\nBeta\tGamma\nDelta"
MIXED_DELIMITER_PARTS = ["Alpha", "Beta", "Gamma", "Delta"]

# A tab-joined entry long enough (>300 chars) and poorly enough extracted
# (<50% coverage) to be a candidate for stage 6's content-overflow router.
# Those two thresholds are the router's own, in `_add_entry_comments`.
OVERFLOW_SCOPE = (
    "Scope: ambulatory continuity practice covering roughly two thousand patient "
    "visits per year, with supervision of residents and medical students on the "
    "inpatient service and a weekly procedure clinic dedicated to joint injections "
    "and minor office surgery")
OVERFLOW_PARTS = ["Attending Physician, Division of General Internal Medicine",
                  "NYP Weill Cornell Medical Center", OVERFLOW_SCOPE, "2015-2020"]
OVERFLOW_CASE = "\t".join(OVERFLOW_PARTS)

# L3 with the role field missing and the fragments after the role repeating
# the very fields the composed bullet is built from.
L3_DUPLICATED_REMAINDER = "Attending Physician\tNYP Weill Cornell\t2015-2020"
L3_COMPOSED_BULLET = "Attending Physician, NYP Weill Cornell, 2015-2020"


# --- (a) the production path: does the split reach the document? -------------

@pytest.mark.parametrize("code,filler,header", SUBSECTIONS)
def test_tab_joined_entry_renders_one_bullet_per_fragment_end_to_end(code, filler, header):
    """Pins the defect the review found: the fragment split never reached
    the rendered document.

    Through the real template this entry used to render as ONE bullet --
    "Attending Physician — NYP Weill Cornell 2015-2020" on L1/L2, and the
    bare "Attending Physician" on L3, with the other two fragments deleted.
    """
    gen, added = _render_through_template(filler, [_entry(TAB_JOINED)])
    # The blank template ships no clinical table, so all three subsections
    # take the bullet fallback. Assert it rather than assume it.
    assert gen.stats["tables_populated"] == 0, f"{code} took the table branch"
    assert added == [""] + TAB_JOINED_PARTS
    assert gen.stats["entries_inserted"] == len(TAB_JOINED_PARTS)


@pytest.mark.parametrize("code,filler,header", SUBSECTIONS)
def test_pipe_columns_render_as_one_item_end_to_end(code, filler, header):
    """'|' is a column separator in L1/L2/L3, so "Title | Institution |
    Dates" must reach the document as ONE item, not three bullets. Unit
    testing `_bullet_parts` alone cannot prove that -- the entry crosses
    `_clean_inline_tabs` on the way in, which welds the columns to " — "."""
    gen, added = _render_through_template(filler, [_entry(PIPE_COLUMN_CASE)])
    assert gen.stats["tables_populated"] == 0, f"{code} took the table branch"
    assert added == ["", PIPE_COLUMN_RENDERED]
    assert gen.stats["entries_inserted"] == 1


@pytest.mark.parametrize("code,filler,header", SUBSECTIONS)
def test_mixed_newline_and_tab_renders_four_bullets_end_to_end(code, filler, header):
    """Flattened CV content mixes both delimiters in one entry. Newline and
    tab were only ever tested apart; "A\\nB\\tC\\nD" is the shape that needs
    both rules at once."""
    gen, added = _render_through_template(filler, [_entry(MIXED_DELIMITER_CASE)])
    assert gen.stats["tables_populated"] == 0, f"{code} took the table branch"
    assert added == [""] + MIXED_DELIMITER_PARTS


def test_l3_keeps_every_fragment_when_no_role_field_was_extracted():
    """The L3-specific data-loss branch the review named.

    With no extracted role, `_fill_clinical_practice_l3` derived one from
    `original_text.split('\\t')[0]` and rendered only that, so the complete
    input did NOT survive: "Attending Physician" reached the document and
    "NYP Weill Cornell" and "2015-2020" were dropped with no warning. Every
    fragment must now be present exactly once.
    """
    gen, added = _render_through_template("_fill_clinical_practice_l3", [_entry(TAB_JOINED)])
    for fragment in TAB_JOINED_PARTS:
        assert added.count(fragment) == 1, f"{fragment!r} not rendered exactly once"
    assert added == [""] + TAB_JOINED_PARTS


def test_l3_composed_bullet_keeps_the_fragments_after_the_role():
    """The same branch when the entry DOES carry institution/dates fields:
    the first fragment still composes the "Role, Institution, Dates" bullet,
    and the fragments after it follow as their own bullets instead of being
    discarded.

    The fixture deliberately mixes both kinds of fragment. It used to carry
    only genuinely-new text, which made it a test that could not fail on the
    shape its own name describes: an entry flattened out of one source row
    repeats the institution and the dates in the fragments AND in the
    extracted fields, so the interesting question is which of the fragments
    after the role survive, not whether any of them do.
    """
    gen, added = _render_through_template(
        "_fill_clinical_practice_l3",
        [_entry("Program Director\tNYP Weill Cornell\tSurgical residency rotation\t2015-2020",
                institution="NYP Weill Cornell", start_date="2015", end_date="2020")],
    )
    assert added == [
        "",
        "Program Director, NYP Weill Cornell, 2015-2020",
        "Surgical residency rotation",
    ]


def test_l3_does_not_repeat_the_institution_and_dates_it_just_composed():
    """Pins the duplication the blind verifier found on the corpus.

    With no extracted role, L3 takes the role from the text before the first
    tab and composes "Role, Institution, Dates" from the extracted fields --
    then appended EVERY remaining fragment, and those fragments are the same
    institution and dates the composer had just written. One corpus CV grew a
    third paragraph whose entire text was already inside the first. Rendered
    before the fix as ['', 'Attending Physician, NYP Weill Cornell,
    2015-2020', 'NYP Weill Cornell', '2015-2020'].

    A loss-only census cannot see this, so assert the whole paragraph
    sequence and that each value appears exactly once.
    """
    gen, added = _render_through_template(
        "_fill_clinical_practice_l3",
        [_entry(L3_DUPLICATED_REMAINDER,
                institution="NYP Weill Cornell", start_date="2015", end_date="2020")],
    )
    assert gen.stats["tables_populated"] == 0, "L3 took the table branch"
    assert added == ["", L3_COMPOSED_BULLET]
    for value in ("Attending Physician", "NYP Weill Cornell", "2015-2020"):
        assert " ".join(added).count(value) == 1, f"{value!r} rendered more than once"


def test_l3_drops_a_repeated_fragment_but_not_a_word_inside_a_longer_one():
    """The duplication filter compares content WORDS in contiguous order, and
    both halves of that matter.

    "Division of Bioethics" is the institution the composed bullet already
    names, so it must go -- and it has to be recognised across the ", " the
    composer itself inserted, which a fragment-vs-fragment comparison would
    miss. "Ethics" is a different fragment that only LOOKS present: it is a
    substring of "Bioethics" and nothing more, so a substring test drops real
    content here while the word comparison keeps it.
    """
    gen, added = _render_through_template(
        "_fill_clinical_practice_l3",
        [_entry("Chair\tDivision of Bioethics\tEthics",
                institution="Division of Bioethics", start_date="2015", end_date="2020")],
    )
    assert added == ["", "Chair, Division of Bioethics, 2015-2020", "Ethics"]


@pytest.mark.parametrize("code,filler,header", SUBSECTIONS)
def test_second_entry_renders_after_the_first_not_inside_it(code, filler, header):
    """The spacer the first entry inserts was not counted in the return
    value the fillers use to advance their insertion index, so the second
    entry's bullets landed one paragraph too high -- inside the first
    entry's. Before the fix this rendered as
    ['', 'First entry part a', 'Second entry part a', 'Second entry part b',
    'First entry part b'].
    """
    gen, added = _render_through_template(filler, [
        _entry("First entry part a\tFirst entry part b"),
        _entry("Second entry part a\tSecond entry part b"),
    ])
    assert added == [
        "",
        "First entry part a",
        "First entry part b",
        "Second entry part a",
        "Second entry part b",
    ]


# --- (a2) the overflow router: does the split make stage 6 render it twice? --

def _low_coverage_entry(text, code):
    """An entry the content-overflow router is entitled to consider: a K/L
    code, under 50% extraction coverage, over 300 characters."""
    return {"text": text, "taxonomy_code": code, "extracted_fields": {},
            "extraction_coverage": {"extraction_coverage_percent": 20.0,
                                    "unextracted_words": ["scope", "clinic"]}}


@pytest.mark.parametrize("code,filler,header", SUBSECTIONS)
def test_fragment_split_does_not_re_emit_the_entry_through_the_overflow_router(code, filler, header):
    """Pins the duplication the blind verifier found on the corpus.

    `_add_entry_comments` queues an entry for the content-overflow router
    when the paragraph it just wrote holds less than 80% of the entry's
    source text. The entry's comments ride the FIRST bullet, so once these
    subsections stopped welding the tabs away, that first bullet held one
    fragment of four -- the entry looked 1/4 rendered, and
    `_route_overflow_entries` re-emitted the whole thing at the end of the
    section, on top of the bullets that already carried every word of it.
    On one corpus CV that was +36 duplicated word tokens.

    Nothing here is under-rendered: all four fragments are on the page, so
    the queue must be empty and every fragment must appear exactly once.
    """
    assert len(OVERFLOW_CASE) > 300, "fixture no longer reaches the router's length threshold"
    gen, added = _render_through_template(
        filler, [_low_coverage_entry(OVERFLOW_CASE, code)], route_overflow=True)

    assert gen.stats["tables_populated"] == 0, f"{code} took the table branch"
    assert gen._overflow_entries == [], "a fully rendered entry was queued for overflow"
    assert gen.stats["overflow_bullets_added"] == 0
    assert added == [""] + OVERFLOW_PARTS
    for fragment in OVERFLOW_PARTS:
        assert added.count(fragment) == 1, f"{fragment[:30]!r} rendered more than once"


def test_a_genuinely_under_rendered_single_paragraph_entry_is_still_queued():
    """The blast radius of the fix above, stated as a test.

    `_add_entry_comments` serves every section, and the fix only changes what
    it measures when a caller says an entry rendered into several paragraphs.
    A caller that renders an entry as ONE paragraph passes nothing, and an
    entry whose paragraph really does hold a fraction of its source text must
    still reach the overflow router exactly as before.
    """
    scratch = _scratch_document()
    entry = _low_coverage_entry(OVERFLOW_CASE, "L1")
    scratch.gen._insert_bulleted_entry(scratch.insert_idx, "Attending Physician", entry)

    assert len(scratch.gen._overflow_entries) == 1
    queued_entry, queued_para, queued_code = scratch.gen._overflow_entries[0]
    assert queued_entry is entry
    # No list_level, so this call site prefixes the literal "• " glyph (#483).
    assert queued_para.text == "• Attending Physician"
    assert queued_code == "L1"


def test_a_fully_rendered_single_paragraph_entry_is_still_not_queued():
    """The other half of the same blast radius: a single-paragraph caller
    whose paragraph does hold the whole source text was never queued, and
    still is not. Together with the test above this pins both sides of the
    80% check for the callers the fix does not touch."""
    scratch = _scratch_document()
    entry = _low_coverage_entry(OVERFLOW_CASE, "L1")
    scratch.gen._insert_bulleted_entry(scratch.insert_idx, OVERFLOW_CASE, entry)

    assert scratch.gen._overflow_entries == []


# --- (b) `_bullet_parts` vs `entry_lines`: where the contracts agree ---------

# Inputs where the two splitters MUST agree. Checked against the real
# `entry_lines`, never a restatement of its rules, so a change to either one
# fails here. Mirrors the table in clinical_practice.py's module docstring.
ENTRY_LINES_AGREE = [
    ("two lines", "First activity\nSecond activity", ["First activity", "Second activity"]),
    ("three lines",
     "Chair, Ethics Committee\nMember, IRB\nMember, P&T Committee",
     ["Chair, Ethics Committee", "Member, IRB", "Member, P&T Committee"]),
    ("repeated empty lines", "First activity\n\n\n\nSecond activity",
     ["First activity", "Second activity"]),
    ("leading and trailing whitespace", "   First activity   \n   Second activity   ",
     ["First activity", "Second activity"]),
    ("whitespace-only lines between content", "First activity\n   \n\t\nSecond activity",
     ["First activity", "Second activity"]),
    ("empty string", "", []),
    ("whitespace only", "   \n\t\n  ", []),
    ("none", None, []),
    ("pipe columns", PIPE_COLUMN_CASE, [PIPE_COLUMN_CASE]),
    ("single item", "Attending Physician", ["Attending Physician"]),
]


@pytest.mark.parametrize("label,text,expected",
                         ENTRY_LINES_AGREE,
                         ids=[c[0] for c in ENTRY_LINES_AGREE])
def test_bullet_parts_matches_entry_lines_off_the_tab_path(label, text, expected):
    """Compatibility: on every input without a tab, `_bullet_parts` returns
    exactly what `entry_lines` returned, so #476 cannot regress a shape the
    newline-only splitter already handled."""
    assert entry_lines(text) == expected
    assert _bullet_parts(text) == expected


# The divergence, stated as (input, entry_lines result, _bullet_parts result).
# Exactly one input class separates the two contracts: text containing a tab.
ENTRY_LINES_DIVERGE = [
    ("single tab", "Attending Physician\tNYP Weill Cornell",
     ["Attending Physician\tNYP Weill Cornell"],
     ["Attending Physician", "NYP Weill Cornell"]),
    ("three tab columns", TAB_JOINED, [TAB_JOINED], TAB_JOINED_PARTS),
    ("mixed newline and tab", MIXED_DELIMITER_CASE,
     ["Alpha", "Beta\tGamma", "Delta"], MIXED_DELIMITER_PARTS),
    ("repeated tabs", "Alpha\t\t\tBeta", ["Alpha\t\t\tBeta"], ["Alpha", "Beta"]),
    ("tab with padding", "Alpha \t Beta", ["Alpha \t Beta"], ["Alpha", "Beta"]),
]


@pytest.mark.parametrize("label,text,old,new",
                         ENTRY_LINES_DIVERGE,
                         ids=[c[0] for c in ENTRY_LINES_DIVERGE])
def test_bullet_parts_diverges_from_entry_lines_only_on_tabs(label, text, old, new):
    """The deliberate divergence, pinned so it stays deliberate: a tab is a
    bullet boundary here and is not one in `entry_lines`. Pinning both sides
    means a later "fix" to either splitter shows up as a failure rather than
    a silent rendering change."""
    assert entry_lines(text) == old
    assert _bullet_parts(text) == new
    assert old != new


def test_pipe_stays_one_part():
    """'|' is a column separator throughout this file, not a bullet
    boundary -- unlike positions/honors/memberships, it must NOT split."""
    assert _bullet_parts(PIPE_COLUMN_CASE) == [PIPE_COLUMN_CASE]
    assert entry_lines(PIPE_COLUMN_CASE) == [PIPE_COLUMN_CASE]


# --- (c) the bullet writer itself -------------------------------------------

def test_positive_control_tab_joined_text_splits_into_two_bullets():
    """`_insert_multiline_as_bullets` with a raw tab-joined text: two parts
    in, exactly two bullet paragraphs out and nothing else.

    Asserting the whole paragraph sequence, not just membership, is the
    point -- a stray extra paragraph or a duplicated bullet used to pass an
    `in`-only check.
    """
    scratch = _scratch_document()
    inserted = scratch.gen._insert_multiline_as_bullets(
        scratch.insert_idx, "Chair, Ethics Committee\tMember, IRB",
        entry=None, add_blank_before=False)

    assert inserted == 2
    texts = [p.text for p in scratch.gen.doc.paragraphs]
    assert len(texts) == len(scratch.baseline) + 2
    assert texts == [HEADING_TEXT, "Chair, Ethics Committee", "Member, IRB", SENTINEL_TEXT]
    for bullet in ("Chair, Ethics Committee", "Member, IRB"):
        assert texts.count(bullet) == 1
    assert not any("\t" in t for t in texts)


def test_add_blank_before_puts_one_spacer_above_the_first_bullet():
    """`add_blank_before=True` was never exercised. The spacer goes above
    the FIRST bullet, not between the bullets or after the last one, and it
    counts toward the return value -- the fillers advance their insertion
    index by that number, so a spacer it does not count slides every later
    entry one paragraph too high.
    """
    scratch = _scratch_document()
    inserted = scratch.gen._insert_multiline_as_bullets(
        scratch.insert_idx, "Chair, Ethics Committee\tMember, IRB",
        entry=None, add_blank_before=True)

    texts = [p.text for p in scratch.gen.doc.paragraphs]
    assert texts == [HEADING_TEXT, "", "Chair, Ethics Committee", "Member, IRB", SENTINEL_TEXT]
    assert inserted == 3
    assert inserted == len(texts) - len(scratch.baseline)


def test_no_spacer_and_no_paragraphs_when_the_text_has_no_parts():
    """A text with nothing in it inserts nothing at all -- not even the
    spacer -- so a skipped entry cannot leave a stray blank paragraph
    behind or advance the caller's insertion index."""
    scratch = _scratch_document()
    inserted = scratch.gen._insert_multiline_as_bullets(
        scratch.insert_idx, "  \n\t\n ", entry=None, add_blank_before=True)

    assert inserted == 0
    assert [p.text for p in scratch.gen.doc.paragraphs] == scratch.baseline


def test_mixed_delimiter_text_becomes_four_bullets():
    """Tabs and newlines coexist in flattened CV content; "A\\nB\\tC\\nD"
    needs both rules applied to one text."""
    scratch = _scratch_document()
    inserted = scratch.gen._insert_multiline_as_bullets(
        scratch.insert_idx, MIXED_DELIMITER_CASE, entry=None, add_blank_before=False)

    assert inserted == 4
    texts = [p.text for p in scratch.gen.doc.paragraphs]
    assert texts == [HEADING_TEXT] + MIXED_DELIMITER_PARTS + [SENTINEL_TEXT]


def test_negative_control_single_part_text_unchanged():
    scratch = _scratch_document()
    inserted = scratch.gen._insert_multiline_as_bullets(
        scratch.insert_idx, PIPE_COLUMN_CASE, entry=None, add_blank_before=False)

    assert inserted == 1
    # _insert_bulleted_entry runs _clean_inline_tabs on the way in, which
    # welds '|' into ' — ' for display -- pre-existing, unrelated to this
    # fix. The point pinned here is the PART count: one part in, one
    # paragraph out, not three.
    texts = [p.text for p in scratch.gen.doc.paragraphs]
    assert texts == [HEADING_TEXT, PIPE_COLUMN_RENDERED, SENTINEL_TEXT]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
