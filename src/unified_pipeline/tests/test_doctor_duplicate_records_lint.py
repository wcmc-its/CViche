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


if __name__ == "__main__":
    test_thresholds_are_pinned()
    test_flags_same_body_at_different_list_numbers_same_section()
    test_quiet_when_the_repeat_is_under_a_different_section_heading()
    test_quiet_on_genuinely_different_bodies()
    test_quiet_when_the_repeat_is_beyond_the_window()
    test_blank_spacer_paragraphs_are_transparent_to_the_window()
    test_docx_text_and_read_docx_blocks_smoke()
    print("OK")
