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

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml import parse_xml  # noqa: E402
from docx.oxml.ns import nsdecls  # noqa: E402

from unified_pipeline.doctor.lints.render import (  # noqa: E402
    DUPLICATE_RECORD_MIN_CHARS,
    DUPLICATE_RECORD_WINDOW,
    lint_duplicate_passages,
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


def test_flags_when_enumerator_and_punctuation_differ_between_occurrences():
    """T4.6 is the parametrized enumerator-style coverage; this is T4.3
    (normalized-body coverage): every existing positive control in this
    file repeats the SAME byte-identical string. Here the two occurrences
    differ in both their list enumerator ('1. ' vs '7) ') and their
    internal punctuation (periods/semicolons vs commas/colons/exclamation),
    which _passage_key strips and folds respectively -- so they still key
    identically and still fire."""
    body_a = ("1. Smith J, Doe K. A study of duplication. Journal of "
              "Testing. 2020;12(3):45-50.")
    body_b = ("7) Smith J; Doe K: A study of duplication! Journal of "
              "Testing, 2020:12(3):45-50.")
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", body_a),
        ("p", f"2. {_CITATION_B}"),
        ("p", body_b),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1


def test_three_occurrences_count_as_two_duplicate_pairs():
    """T4.4: three occurrences of the same body within the window produce
    2 pairs, not 3 or 1 -- every new occurrence is compared against the
    earliest still-in-window match, so a chain of 3 occurrences is 2 links.
    lint_duplicate_records' own pairing semantics are untouched by this
    review (only tests were added here, per the review's own MUST NOT)."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"3. {_CITATION_A}"),
        ("p", f"4. {_CITATION_A}"),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1
    assert "2 duplicated record(s)" in findings[0]["message"]
    assert len(findings[0]["evidence"]) == 2


@pytest.mark.parametrize("enumerator", ["1. ", "12) ", "• ", "- ", "* "])
def test_recognizes_every_supported_enumerator_style(enumerator):
    """T4.6: every enumerator form list allowed by _PASSAGE_ENUMERATOR_RE
    (numeric with '.' or ')', four bullet glyphs, or a dash before
    whitespace) must be tracked -- every other test in this file uses only
    the numeric '1. ' style."""
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"{enumerator}{_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"{enumerator}{_CITATION_A}"),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1


def test_flags_a_unicode_citation_repeated():
    """T4.11: _CITATION_A/_CITATION_B are pure ASCII in every other test in
    this file; a citation with non-ASCII names/characters must be tracked
    the same way -- _passage_key's normalization is Unicode-agnostic
    (casefold + whitespace collapse + punctuation fold), not ASCII-only."""
    citation = ("Müller Ø, Nguyễn T. 中文 title on "
                "duplication in rendered CV documents. 2021;3(2):10-20.")
    blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {citation}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"3. {citation}"),
    ]
    findings = lint_duplicate_records(blocks)
    assert len(findings) == 1


def test_duplicate_records_and_duplicate_passages_do_not_double_fire():
    """T4.10: the two lints are scoped to disjoint shapes -- a >=2-block
    repeated record is duplicate_passages' shape and duplicate_records'
    single-block rule (`kind != "p"` aside, it still only tracks ONE
    enumerated paragraph's own body, not a stretch of neighbouring blocks)
    cannot see it; a single-block repeat is invisible to duplicate_passages
    by construction (DUPLICATE_PASSAGE_MIN_BLOCKS=2) and is
    duplicate_records' own reason to exist (#446)."""
    two_block_record = [
        ("p", "Grand Rounds Lecture, Weill Cornell Medicine"),
        ("p", "2019"),
    ]
    spacer = ("p", "Unrelated single filler line of narrative text here")
    two_block_blocks = ([("p", "K. TEACHING")] + two_block_record
                         + [spacer] + two_block_record)
    assert len(lint_duplicate_passages(two_block_blocks)) == 1
    assert lint_duplicate_records(two_block_blocks) == []

    one_block_blocks = [
        ("p", "D. PUBLICATIONS"),
        ("p", f"1. {_CITATION_A}"),
        ("p", f"2. {_CITATION_B}"),
        ("p", f"3. {_CITATION_A}"),
    ]
    assert lint_duplicate_passages(one_block_blocks) == []
    assert len(lint_duplicate_records(one_block_blocks)) == 1


def test_names_match_whole_word_token_containment_not_bare_substring():
    """T1.3: regression test for the _names_match fix -- see the T1.1 note
    above on why lint tests outside lint_duplicate_records/duplicate_passages
    live in this file for this review round (no dedicated per-lint test
    file exists, and test_run_doctor.py is out of scope).

    The ticket's original acceptance criterion also asked for 'Research' vs
    'Research Administration' to NOT match. Implemented literally (a
    coordinate-segment split that only matches on 'and'/'&'/','), that
    passed the synthetic test but caused a REAL regression on the doctor
    A/B gate: the 65-doc farm's 2100_Mocco lost a genuine dead_sections
    true positive ('Research Presentations' / empty output 'RESEARCH')
    because the two no longer matched. A spurious match here can only
    SUPPRESS a real finding, never fabricate one, so a same-word match is
    the safer failure direction -- whole-word TOKEN CONTAINMENT keeps
    'Research' matching 'Research Administration' (no corpus regression)
    while still closing the confirmed real defect: 'education' as a
    fragment inside the single word 'educational' no longer matches
    'educational contributions' at all (different tokens, not a shared
    substring across a word boundary)."""
    from unified_pipeline.doctor.lints.render import _names_match
    assert _names_match("research", "research administration")
    assert not _names_match("education", "educational contributions")
    assert _names_match("honors", "b. honors and awards")


def test_dead_sections_counts_a_non_blank_table_cell_as_content():
    """T1.4: the scout's exact repro -- a section rendered entirely as a
    table with short (but real) cell text must not read as dead, while a
    table with only blank cells still does."""
    from unified_pipeline.doctor.lints.render import lint_dead_sections
    stage2 = {"entries": [
        {"element_type": "entry", "hierarchy": ["Honors"],
         "text": "Received the ACS Award for outstanding service in 2019."},
        {"element_type": "entry", "hierarchy": ["Honors"],
         "text": "Received the NIH Merit Award for research excellence."},
        {"element_type": "entry", "hierarchy": ["Honors"],
         "text": "Named Teacher of the Year by the department in 2020."},
    ]}
    filled = [("p", "B. HONORS"), ("table", "ACS Award\n2019\nNIH Award\n2021")]
    assert lint_dead_sections(stage2, filled) == []

    blank_table = [("p", "B. HONORS"), ("table", "   \n  \n")]
    findings = lint_dead_sections(stage2, blank_table)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"


def test_table_shape_col_resolves_an_ambiguous_header_pair():
    """T1.7: regression test for the explicit ordered-alias-tuple col()
    rewrite, discriminating against the OLD substring rule it replaced --
    not just re-proving a case the old rule already got right. The old
    rule's name_i = col("award", "honor") matched the FIRST column
    containing 'award' as a bare substring, so a 'date awarded (yyyy)'
    column (which contains 'award' inside 'awarded') stole the name role
    away from the real 'name of award' column whenever it was listed
    first. Word-boundary matching on the multi-word alias 'name of award'
    does not fall for it: 'awarded' has no word boundary after 'award'.
    Proven by putting the blob defect in the THIRD column -- it only fires
    if name_i correctly resolved there, not to the date column."""
    from unified_pipeline.doctor.lints.render import lint_table_shape
    tables = [[
        ["date awarded (yyyy)", "organization", "name of award"],
        ["2020", "Cardiology Society of America", "X" * 200],
    ]]
    findings = lint_table_shape(tables)
    assert len(findings) == 1
    assert "name-cell blob" in findings[0]["evidence"][0]


def test_table_shape_row_index_survives_a_blank_row_above_it():
    """T1.8: regression test for enumerating original rows -- a blank row 2
    must not shift the reported row number of the defective row 3 down to
    'row 2'."""
    from unified_pipeline.doctor.lints.render import lint_table_shape
    tables = [[
        ["name of award", "organization", "date awarded (yyyy)"],
        ["Fine Row", "Society", "2020"],
        ["", "", ""],
        ["X" * 200, "Society", "2021"],
    ]]
    findings = lint_table_shape(tables)
    assert len(findings) == 1
    assert findings[0]["evidence"][0].startswith("row 3:")


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
    """Boundary control for the `<=` comparison that prunes `recent` against
    DUPLICATE_RECORD_WINDOW. The corpus's own headline firing (2068_Yount_Cv,
    list numbers 32 and 38)
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


def test_mixed_tracked_insertion_and_deletion_in_one_document(tmp_path):
    """T4.7: the two tracked-change tests above exercise insertion and
    deletion SEPARATELY, each in its own document. This combines both in
    one document -- an editor who deleted a stale duplicate and inserted
    its replacement via track-changes in the same edit session -- so the
    reader must resolve <w:ins>/<w:del> correctly when both appear
    together, not just each in isolation."""
    doc = Document()
    doc.add_paragraph("D. PUBLICATIONS")
    doc.add_paragraph(f"1. {_CITATION_A}")
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:del {nsdecls("w")} w:id="4" w:author="editor" '
        f'w:date="2026-01-01T00:00:00Z"><w:r><w:delText>2. {_CITATION_A}'
        '</w:delText></w:r></w:del>'))
    doc.add_paragraph()._p.append(parse_xml(
        f'<w:ins {nsdecls("w")} w:id="5" w:author="editor" '
        f'w:date="2026-01-01T00:00:00Z"><w:r><w:t>2. {_CITATION_B}</w:t>'
        '</w:r></w:ins>'))
    docx_path = tmp_path / "tracked_mixed_DUPTST_wcm.docx"
    doc.save(docx_path)

    blocks = read_docx_blocks(str(docx_path))
    assert blocks[1] == ("p", f"1. {_CITATION_A}")
    assert blocks[2] == ("p", "")
    assert blocks[3] == ("p", f"2. {_CITATION_B}")
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
    """Negative control for the paragraph-only guard before the enumerator
    check (`kind != "p"` short-circuits before `_PASSAGE_ENUMERATOR_RE` is
    even tried against a table block).
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
    """Pin for the `if kind == "p" else None` gate on the section-header
    check, mirroring the same non-paragraph-blocks-cannot-be-headers gate in
    lint_dead_sections. A short all-caps
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


def test_run_doctor_reports_corrupt_stage6_docx_and_skips_duplicate_records(tmp_path):
    """T4.2: a present-but-unparseable stage-6 docx must surface as ERROR
    'unreadable', mirroring test_run_doctor.py's own pattern for a corrupt
    source docx -- not the benign INFO 'skipped: missing'.
    duplicate_records reads stage_6_docx=blocks exactly like every other
    blocks-driven lint, so a broken docx degrades it the same way."""
    root = tmp_path / "outputs"
    out_dir = root / "stage_6_wcm_documents"
    out_dir.mkdir(parents=True)
    (out_dir / "CORRUPT_cv_wcm.docx").write_bytes(b"not a real docx, just bytes")

    payload = run_doctor(root, "CORRUPT")
    finding = next(f for f in payload["findings"]
                   if f["lint"] == "duplicate_records")
    assert finding["severity"] == "ERROR"
    assert "unreadable" in finding["message"]
    assert "stage_6_docx" in finding["message"]
    assert payload["worst_severity"] == "ERROR"


def test_run_doctor_dispatches_duplicate_records_and_skips_without_docx(tmp_path):
    """Dispatch wiring, both directions: fires as a real finding when the
    stage-6 docx carries a duplicate, and degrades to the standard INFO
    'skipped: missing stage_6_docx' finding -- never a crash or silence --
    when there is no docx to read. T4.5 extends the fired-path assertions
    to the report's counts/worst_severity, not just the one finding."""
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
    assert payload["counts"]["WARN"] >= 1
    assert payload["worst_severity"] in ("WARN", "ERROR")

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_payload = run_doctor(empty_root, "NOPE")
    skipped = [f for f in empty_payload["findings"]
               if f["lint"] == "duplicate_records"]
    assert len(skipped) == 1
    assert skipped[0]["severity"] == "INFO"
    assert "skipped" in skipped[0]["message"]
    assert empty_payload["counts"]["WARN"] == 0
    assert empty_payload["worst_severity"] == "INFO"
