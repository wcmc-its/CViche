"""Tests for lint_duplicate_records (#446).

`duplicate_passages` (#439) only sees runs of >=2 CONSECUTIVE rendered blocks,
so a record occupying exactly ONE block -- the common shape for a duplicated
numbered citation -- is invisible to it by construction. This is a SEPARATE
detector, not a tuning of duplicate_passages: it keys single enumerated
paragraph blocks by their normalized body and flags an exact repeat at a
different list position within a bounded window, scoped to one output
section so a publication legitimately listed under two different section
headings never fires.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_doctor_duplicate_records_lint.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml import parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls  # noqa: E402

from unified_pipeline.doctor.lints.render import (  # noqa: E402
    DUPLICATE_RECORD_MIN_CHARS,
    DUPLICATE_RECORD_WINDOW,
    lint_duplicate_records,
)
from unified_pipeline.run_doctor import _docx_text, read_docx_blocks, run_doctor  # noqa: E402


_CITATION_A = ("Smith J, Doe K. A study of duplication in rendered CV "
               "documents. Journal of Testing. 2020;12(3):45-50.")
_CITATION_B = ("Jones A, Lee M. An unrelated finding about something else "
               "entirely. Journal of Other Things. 2021;5(1):1-9.")


def test_thresholds_are_pinned():
    """Both are measured, disclosed choices (PR body), not arbitrary knobs:
    6 is the window #446's own detection methodology used, and 20 is the
    corpus probe's floor on the normalized body."""
    assert DUPLICATE_RECORD_WINDOW == 6
    assert DUPLICATE_RECORD_MIN_CHARS == 20


def test_flags_same_body_at_different_list_numbers_same_section():
    """Positive control -- mirrors the real shape (e.g. 2068_Yount_Cv: the
    same citation at list numbers 32 and 38). Fails on dev because
    lint_duplicate_records does not exist there at all."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"3. {_CITATION_A}"),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "duplicate_records"
    assert f["severity"] == "WARN"
    assert "1 duplicated record(s)" in f["message"]
    assert f["evidence"][0].startswith("block 1 repeats at 3")
    assert _CITATION_A[:40] in f["evidence"][0]


def test_quiet_when_the_repeat_is_under_a_different_section_heading():
    """Acceptance criterion: a work legitimately listed under two different
    section headings (e.g. 'Peer-reviewed' and 'Selected') must never fire."""
    blocks = [
        ("p", "D. PEER-REVIEWED PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", "E. SELECTED PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
    ]
    assert lint_duplicate_records(blocks) == []


def test_quiet_on_genuinely_different_bodies():
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
    ]
    assert lint_duplicate_records(blocks) == []


def test_quiet_when_the_repeat_is_the_same_prose_with_different_dates():
    """T1.2 (#446 review): normalized-text equality alone is not proof of
    duplication -- a legitimately repeated activity described identically
    on two different occasions differs in exactly its embedded date, which
    IS part of the normalized body _passage_key compares. Two records
    differing only in year therefore key differently and never fire, with
    no change to lint_duplicate_records' thresholds or semantics (the
    review accepted them as-is; only this negative control was requested).
    No corpus counter-example was found on the 65-doc farm -- all 4 real
    firings there are genuine duplicated citations."""
    body_2019 = "Visiting lecture on curriculum design, awarded 2019."
    body_2022 = "Visiting lecture on curriculum design, awarded 2022."
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {body_2019}"),
        ("p", f"2. {body_2022}"),
    ]
    assert lint_duplicate_records(blocks) == []


def test_duplicate_passages_merges_a_stretch_repeated_three_times():
    """T1.1 (#446 review): a block sequence repeated 3+ times previously
    produced one pairwise (first, second) match per adjacent occurrence
    pair, so 3 occurrences reported 2 separate findings that shared the
    middle occurrence's block range across two evidence lines. Merging
    matches by real block-index range collapses this to ONE finding
    covering the whole duplicated stretch, with one evidence line naming
    every repeat rather than one line per pair.

    This is a regression test for lint_duplicate_passages (#439), a
    DIFFERENT lint from lint_duplicate_records (#446) this file otherwise
    tests -- it lives here because this review round's write set has no
    dedicated test file for duplicate_passages, and test_run_doctor.py,
    which owns the existing duplicate_passages tests, is out of scope for
    this round."""
    from unified_pipeline.doctor.lints.render import lint_duplicate_passages
    record = [("p", "Grand Rounds Lecture"),
              ("p", "Weill Cornell Medicine"),
              ("p", "2019")]
    spacer_a = ("p", "Journal Club, unrelated topic content here")
    spacer_b = ("p", "Case Conference, another unrelated topic here")
    blocks = ([("p", "K. TEACHING")] + record
              + [spacer_a] + record
              + [spacer_b] + record)
    findings = lint_duplicate_passages(blocks)
    assert len(findings) == 1
    assert "1 passage(s)" in findings[0]["message"]
    evidence = findings[0]["evidence"]
    # ONE evidence line, not one per pair -- block range 5-7 (the middle
    # occurrence) does not appear in two separate lines.
    assert len(evidence) == 1
    assert evidence[0].count("repeat at") == 1
    assert evidence[0].startswith("blocks 1-3 repeat at 5-7, 9-11:")


def test_fires_at_exactly_the_window_distance():
    """Boundary control for the `<=` in the window prune (render.py:629-630).
    The corpus's own headline firing (2068_Yount_Cv, list numbers 32 and 38)
    sits at distance exactly DUPLICATE_RECORD_WINDOW, so an off-by-one to `<`
    would silently stop detecting it. `test_quiet_when_the_repeat_is_beyond_
    the_window` pins the other side of the same edge at distance 7."""
    filler = [("p", f"{n}. filler citation entry number {n}, long enough to "
                     f"pass the minimum body length on its own.")
              for n in range(2, DUPLICATE_RECORD_WINDOW + 1)]
    last = DUPLICATE_RECORD_WINDOW + 1
    blocks = ([("p", "D. PUBLICATIONS"), ("p", f"1. {_CITATION_A}")]
              + filler
              + [("p", f"{last}. {_CITATION_A}")])
    # the two occurrences really are DUPLICATE_RECORD_WINDOW enumerated
    # blocks apart, not fewer
    assert last - 1 == DUPLICATE_RECORD_WINDOW
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1
    assert findings[0]["evidence"][0].startswith("block 1 repeats at ")


def test_quiet_when_the_repeat_is_beyond_the_window():
    """A repeat further than DUPLICATE_RECORD_WINDOW enumerated blocks from
    its first occurrence is out of scope -- the shipped window is bounded,
    not global-within-section."""
    filler = [("p", f"{n}. filler citation entry number {n}, long enough to "
                     f"pass the minimum body length on its own.")
              for n in range(2, 2 + DUPLICATE_RECORD_WINDOW)]
    blocks = ([("p", "D. PUBLICATIONS"), ("p", f"1. {_CITATION_A}")]
              + filler
              + [("p", f"{2 + DUPLICATE_RECORD_WINDOW}. {_CITATION_A}")])
    assert lint_duplicate_records(blocks) == []


def test_blank_spacer_paragraphs_are_transparent_to_the_window():
    """A blank spacer block does not match the enumerator prefix, so it is
    dropped from the sequence entirely -- it can neither consume a window
    slot nor block a match, same as lint_duplicate_passages."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", ""),
        ("p", "   "),
        ("p", f"2. {_CITATION_A}"),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1
    assert findings[0]["evidence"][0].startswith("block 1 repeats at 4")


def test_docx_text_and_read_docx_blocks_smoke():
    """Sanity check on the primitives the tracked-changes test below relies
    on, mirroring test_run_doctor_trackchanges.py's own pattern."""
    p_ins = parse_xml(
        f'<w:p {nsdecls("w")}><w:ins w:id="1" w:author="a" '
        f'w:date="2026-01-01T00:00:00Z"><w:r><w:t>1. {_CITATION_A}</w:t>'
        '</w:r></w:ins></w:p>')
    assert _docx_text(p_ins) == f"1. {_CITATION_A}"


def test_tracked_deletion_of_a_stale_duplicate_does_not_fire(tmp_path):
    """The tracked-changes false-positive case (#446's acceptance criterion):
    an editor removed a duplicated citation via Word's track-changes, so its
    old copy survives in the docx XML only inside a <w:del>/<w:delText> run.
    `_docx_text` (the reader `read_docx_blocks` uses) excludes <w:delText> --
    the accepted-changes view a reader sees -- so this must resolve to ONE
    real occurrence, not two, and the lint must not fire.

    A naive reader that included delText (the shape of an earlier, pre-#249
    detector) would see the same body twice within the window and report a
    spurious duplicate here.
    """
    doc = Document()
    doc.add_paragraph(f"1. {_CITATION_A}")
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:del {nsdecls("w")} w:id="2" w:author="editor" '
        f'w:date="2026-01-01T00:00:00Z"><w:r><w:delText>2. {_CITATION_A}'
        '</w:delText></w:r></w:del>'))
    doc.add_paragraph(f"3. {_CITATION_B}")
    docx_path = tmp_path / "tracked_DUPTST_wcm.docx"
    doc.save(docx_path)

    blocks = read_docx_blocks(str(docx_path))
    # the deleted paragraph really does carry no accepted text
    assert blocks[1] == ("p", "")
    assert lint_duplicate_records(blocks) == []


def test_tracked_insertion_of_a_citation_does_not_manufacture_a_duplicate(tmp_path):
    """The tracked-changes acceptance case #446 itself names: the doctor's
    reader already sees accepted text inside <w:ins> runs (#249), so a
    citation delivered as a tracked INSERTION -- not just as plain text --
    must read correctly and must not manufacture a spurious duplicate
    against an unrelated neighbouring record. Built with a real docx
    (python-docx + raw w:ins XML), and it calls the lint -- unlike
    test_docx_text_and_read_docx_blocks_smoke above, which only pins the
    reader primitive.
    """
    doc = Document()
    doc.add_paragraph("D. PUBLICATIONS")
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:ins {nsdecls("w")} w:id="3" w:author="editor" '
        f'w:date="2026-01-01T00:00:00Z"><w:r><w:t>1. {_CITATION_A}</w:t>'
        '</w:r></w:ins>'))
    doc.add_paragraph(f"2. {_CITATION_B}")
    docx_path = tmp_path / "tracked_ins_DUPTST_wcm.docx"
    doc.save(docx_path)

    blocks = read_docx_blocks(str(docx_path))
    # not vacuous: the inserted citation really is readable as accepted text
    assert blocks[1] == ("p", f"1. {_CITATION_A}")
    assert lint_duplicate_records(blocks) == []


def test_quiet_on_short_duplicated_body_under_the_floor():
    """Negative control for DUPLICATE_RECORD_MIN_CHARS: a short repeated
    fragment ('See above.'-style) below the 20-char floor must not fire even
    though the enumerator and section match, so a two-word aside standing
    between two unrelated records cannot read as a duplicated record.
    Mutation check: with DUPLICATE_RECORD_MIN_CHARS monkeypatched to 0 this
    same fixture DOES fire -- this is what pins the floor behaviourally."""
    short_body = "See above."  # normalized key ("see above") is 9 chars
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {short_body}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"3. {short_body}"),
    ]
    assert lint_duplicate_records(blocks) == []


def test_quiet_on_duplicated_non_enumerated_paragraph():
    """Negative control for the 'enumerated blocks only' scope: two
    identical PLAIN paragraphs (no list enumerator) must not fire even
    though their bodies match exactly and clear the floor -- the lint only
    tracks paragraphs that open with a list enumerator
    (_PASSAGE_ENUMERATOR_RE), the same restriction lint_duplicate_passages
    applies to its own block keys."""
    plain_body = ("This is a plain narrative paragraph repeated verbatim, "
                  "long enough to clear the 20-character floor on its own.")
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", plain_body),
        ("p", f"2. {_CITATION_B}"),
        ("p", plain_body),
    ]
    assert lint_duplicate_records(blocks) == []


def test_quiet_on_duplicated_enumerated_table_blocks():
    """Negative control for the `kind != "p"` restriction (render.py:623).
    `read_docx_blocks` emits ("table", <joined cell lines>) blocks whose first
    line can itself open with a list enumerator; a grant or teaching table
    legitimately repeated in two rendered rows is not a duplicated citation,
    and the lint tracks paragraph blocks only. Without the kind guard these
    same two blocks key alike inside the window and DO fire -- that is what
    makes this a behavioural pin rather than a restatement."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("table", f"1. {_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
        ("table", f"3. {_CITATION_A}"),
    ]
    assert lint_duplicate_records(blocks) == []


def test_table_block_that_looks_like_a_header_does_not_reset_the_window():
    """Pin for the `if kind == "p" else None` gate on the section-header check
    (render.py:617), mirroring the sibling at render.py:186. A short all-caps
    table block between two copies of the same citation must NOT read as a
    section header and reset the match window. Without the gate this fixture
    yields 0 findings; with it, 1 -- the discriminating case from the
    scoped re-verification of the gate. A real paragraph header (control)
    still resets, and the same two blocks with nothing between them still
    fire."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("table", "B. FAKE"),
        ("p", f"2. {_CITATION_A}"),
    ]
    assert len(lint_duplicate_records(blocks)) == 1
    real_header = [blocks[0], blocks[1], ("p", "E. REAL"), blocks[3]]
    assert lint_duplicate_records(real_header) == []
    assert len(lint_duplicate_records([blocks[0], blocks[1], blocks[3]])) == 1


def test_run_doctor_dispatches_duplicate_records_and_skips_without_docx(tmp_path):
    """Dispatch wiring, both directions: fires as a real finding when the
    stage-6 docx carries a duplicate, and degrades to the standard INFO
    'skipped: missing stage_6_docx' finding -- never a crash or silence --
    when there is no docx to read."""
    root = tmp_path / "outputs"
    doc = Document()
    doc.add_paragraph("D. PUBLICATIONS")
    doc.add_paragraph(f"1. {_CITATION_A}")
    doc.add_paragraph(f"2. {_CITATION_B}")
    doc.add_paragraph(f"3. {_CITATION_A}")
    out_dir = root / "stage_6_wcm_documents"
    out_dir.mkdir(parents=True)
    doc.save(out_dir / "DUPTST_cv_wcm.docx")

    payload = run_doctor(root, "DUPTST")
    fired = [f for f in payload["findings"] if f["lint"] == "duplicate_records"]
    assert len(fired) == 1
    assert fired[0]["severity"] == "WARN"

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_payload = run_doctor(empty_root, "NOPE")
    skipped = [f for f in empty_payload["findings"]
               if f["lint"] == "duplicate_records"]
    assert len(skipped) == 1
    assert skipped[0]["severity"] == "INFO"
    assert "skipped" in skipped[0]["message"]
