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

import json
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
    DUPLICATE_RECORD_FUZZY_MIN_CHARS,
    DUPLICATE_RECORD_ID_TITLE_OVERLAP,
    DUPLICATE_RECORD_MIN_CHARS,
    DUPLICATE_RECORD_TITLE_MIN_CHARS,
    DUPLICATE_RECORD_WINDOW,
    RENDER_UBIQUITOUS_MIN_LINES,
    _record_lines,
    _record_rendered,
    _rendered_lines,
    lint_duplicate_passages,
    lint_duplicate_records,
    lint_unrendered_records,
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


@pytest.mark.parametrize("second, fires", [
    (_CITATION_A.replace("documents", "document"), True),   # one letter dropped
    (_CITATION_A.replace("study", "studs"), True),          # one letter replaced
    (_CITATION_A.replace("documents", "documentss"), True),  # one letter added
    (_CITATION_A.replace("documents", "documen"), False),   # two letters
    (_CITATION_A.replace("2020", "2021"), False),           # a digit is another record
    (_CITATION_A.replace("12(3)", "12(4)"), False),
])
def test_flags_a_body_one_letter_apart(second, fires):
    """RCBKFG UYFRTL N6: stage 5d formats each copy of a record the CV lists
    twice on its own and can fold them one letter apart."""
    blocks = [("p", "D. PUBLICATIONS"), ("p", f"1. {_CITATION_A}"), ("p", f"2. {second}")]
    assert len(lint_duplicate_records(blocks)) == (1 if fires else 0)


def test_one_letter_tolerance_needs_a_long_body():
    assert DUPLICATE_RECORD_FUZZY_MIN_CHARS == 60
    short = "Example Widget Society meeting talk on rotors"  # 46 characters
    blocks = [("p", "D. TALKS"), ("p", f"1. {short}"), ("p", f"2. {short}s")]
    assert lint_duplicate_records(blocks) == []
    long_body = short + ", Example City, Example State, 12:34"
    assert len(long_body) >= DUPLICATE_RECORD_FUZZY_MIN_CHARS
    blocks = [("p", "D. TALKS"), ("p", f"1. {long_body}"), ("p", f"2. {long_body}s")]
    assert len(lint_duplicate_records(blocks)) == 1


@pytest.mark.parametrize("tail, fires", [
    ("2018.", False),                 # authors, title and year: one per meeting
    ("2018, 2019.", False),           # years only
    ("Example Abstr. 2018;12:345.", True),  # a volume and a page
    ("2018. p. 12.", True),           # a page
])
def test_one_letter_tolerance_needs_a_volume_or_page(tail, fires):
    """126-run farm/batch corpus (web218, web227 twice, web244): the CV lists
    one abstract once per meeting, the render leaves the meeting out, and
    the two bodies end up one letter apart. Only a body naming where it was
    published (a number besides its years) is one record listed twice."""
    body = ("Sample AB, Example CD. Rotor mechanisms shaping widget speed in "
            f"example assemblies under load. {tail}")
    assert len(body) >= DUPLICATE_RECORD_FUZZY_MIN_CHARS
    other = body.replace("Rotor mechanisms", "Rotors mechanisms")
    blocks = [("p", "D. ABSTRACTS"), ("p", f"1. {body}"), ("p", f"2. {other}")]
    assert len(lint_duplicate_records(blocks)) == (1 if fires else 0)
    # An exact repeat still counts with no volume or page.
    blocks = [("p", "D. ABSTRACTS"), ("p", f"1. {body}"), ("p", f"2. {body}")]
    assert len(lint_duplicate_records(blocks)) == 1


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
    because the two no longer matched. Keeping a same-word match here (a
    same-word match can SUPPRESS a real finding but, on its own, can also
    fabricate one -- see
    test_dead_sections_does_not_fabricate_a_warn_via_an_unrelated_partial_name_match
    below, #725 review r3923589271 pt 3) is still the right trade at the
    `_names_match` level: whole-word TOKEN CONTAINMENT keeps 'Research'
    matching 'Research Administration' (no corpus regression) while still
    closing the confirmed real defect: 'education' as a fragment inside the
    single word 'educational' no longer matches 'educational contributions'
    at all (different tokens, not a shared substring across a word
    boundary). `lint_dead_sections` is where the fabrication risk actually
    gets closed, by requiring an exact match before it will trust a
    single-word source name on containment alone."""
    from unified_pipeline.doctor.lints.render import _names_match
    assert _names_match("research", "research administration")
    assert not _names_match("education", "educational contributions")
    assert _names_match("honors", "b. honors and awards")


def test_dead_sections_does_not_fabricate_a_warn_via_an_unrelated_partial_name_match():
    """#725 review r3923589271 pt 3: render.py's own `_names_match`
    docstring used to claim a spurious match here can only SUPPRESS a real
    dead_sections finding, never fabricate one. False: lint_dead_sections
    fires whenever some name-matched output section exists and every
    matched one is empty, so a bare single-word source name ('Research')
    with NO exact output counterpart can still coincidentally token-match a
    genuinely unrelated, and genuinely empty, compound section name
    ('Research Administration') while the real content renders correctly
    under a third, differently-named section ('SCHOLARSHIP') -- the lone
    coincidental match alone used to be enough to fire a WARN even though
    nothing is actually missing. lint_dead_sections now requires an exact
    normalized-name match before it trusts a single-word source name;
    'Research Administration' no longer counts as evidence for 'Research'
    on its own."""
    from unified_pipeline.doctor.lints.render import lint_dead_sections
    stage2 = {"entries": [
        {"element_type": "entry", "hierarchy": ["Research"],
         "text": "Studies host-pathogen interactions in the lab setting."},
        {"element_type": "entry", "hierarchy": ["Research"],
         "text": "Published three papers on this topic in the last year."},
        {"element_type": "entry", "hierarchy": ["Research"],
         "text": "Presented preliminary findings at two national meetings."},
    ]}
    blocks = [
        ("p", "SCHOLARSHIP"),
        ("table", "Studies host-pathogen interactions across three papers."),
        ("p", "E. RESEARCH ADMINISTRATION"),
    ]
    assert lint_dead_sections(stage2, blocks) == []


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


@pytest.mark.parametrize("offset, expect_finding", [
    (-1, True),   # WINDOW-1: well inside the window, fires
    (0, True),    # WINDOW: the `<=` boundary itself, fires
    (1, False),   # WINDOW+1: one past the boundary, quiet
])
def test_window_boundary(offset, expect_finding):
    """Boundary control for the `<=` comparison that prunes `recent` against
    DUPLICATE_RECORD_WINDOW (T4.12, #446 review). The corpus's own headline
    firing (2068_Yount_Cv, list numbers 32 and 38) sits at distance exactly
    DUPLICATE_RECORD_WINDOW, so an off-by-one to `<` would silently stop
    detecting it; one past the window must stay quiet, since the shipped
    window is bounded, not global-within-section."""
    distance = DUPLICATE_RECORD_WINDOW + offset
    filler = [("p", f"{n}. filler citation entry number {n}, long enough to "
                     f"pass the minimum body length on its own.")
              for n in range(2, distance + 1)]
    last = distance + 1
    blocks = ([("p", "D. PUBLICATIONS"), ("p", f"1. {_CITATION_A}")]
              + filler
              + [("p", f"{last}. {_CITATION_A}")])
    # the two occurrences really are `distance` enumerated blocks apart
    assert last - 1 == distance
    findings = lint_duplicate_records(blocks)
    if expect_finding:
        assert len(findings) == 1
        assert findings[0]["evidence"][0].startswith("block 1 repeats at ")
    else:
        assert findings == []


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


# --- record rule (EBYSBC E16/E28): two entries naming one record ----------
#
# Synthetic entries and titles only. The block rule above needs a
# byte-identical body a few list numbers away; these pairs differ in wording,
# sit far apart, or are one grant under two funding codes.

_TITLE_X = "A synthetic study of repeated listings in generated documents"
_TITLE_Y = "An unrelated synthetic report on something else entirely"


def _cite_entry(idx, title, code="S1", year="2020", **fields):
    return {"element_idx_start": idx, "taxonomy_code": code, "text": f"text {idx}",
            "extracted_fields": {"title": title, "year": year, **fields}}


def _grant_entry(idx, code, title, start="2021-01", **fields):
    return {"element_idx_start": idx, "taxonomy_code": code, "text": f"text {idx}",
            "extracted_fields": {"title": title, "start_date": start, **fields}}


def _filler(n):
    return [("p", f"{i}. Filler citation number {i} about an unrelated topic, 2001.")
            for i in range(n)]


def test_record_rule_thresholds_are_pinned():
    assert DUPLICATE_RECORD_TITLE_MIN_CHARS == 30
    assert DUPLICATE_RECORD_ID_TITLE_OVERLAP == 0.8


def test_record_rule_flags_one_article_reworded_far_apart():
    """The block rule misses this pair twice over: the bodies differ (journal
    abbreviated) and they are 12 list numbers apart."""
    blocks = ([("p", f"3. Doe A. {_TITLE_X}. Journal of Examples. 2020;1:1-2.")]
              + _filler(12)
              + [("p", f"16. Doe A. {_TITLE_X}. J Ex. 2020;1:1.")])
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), _cite_entry(40, _TITLE_X + ".")]}
    assert lint_duplicate_records(blocks) == []
    findings = lint_duplicate_records(blocks, stage_5d)
    assert len(findings) == 1 and findings[0]["severity"] == "WARN"
    assert findings[0]["evidence"][0].startswith("entry 10 repeats as entry 40 (S1/S1, same title)")


@pytest.mark.parametrize("second", [
    _cite_entry(40, _TITLE_X, year="2021"),        # another year: an edition, a reprint
    _cite_entry(40, _TITLE_X, code="S8"),          # another code
])
def test_record_rule_quiet_on_another_year_or_code(second):
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_X}. 2021.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), second]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_quiet_on_talks_given_at_several_meetings():
    blocks = [("p", f"1. {_TITLE_X}. Meeting one."), ("p", f"2. {_TITLE_X}. Meeting two.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, code="S8"),
                            _cite_entry(11, _TITLE_X, code="S8")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_shared_pmid_under_two_titles_is_a_lookup_error_not_a_duplicate():
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_Y}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, pmid="12345678"),
                            _cite_entry(11, _TITLE_Y, pmid="12345678")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_pairs_a_shared_doi_when_one_copy_has_no_title():
    """A citation tail enriched by its identifier into a second full copy."""
    blocks = [("p", f"1. {_TITLE_X}. 2020. doi:10.1000/x1"),
              ("p", f"9. {_TITLE_X}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, doi="10.1000/X1"),
                            _cite_entry(13, None, year=None, doi="https://doi.org/10.1000/x1")]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["evidence"][0].startswith("entry 10 repeats as entry 13 (S1/S1, same doi:10.1000/x1)")


def test_record_rule_pairs_one_book_listed_twice():
    blocks = [("p", f"1. {_TITLE_X}. Example Press, 2020."), ("p", f"7. {_TITLE_X}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, code="S3"),
                            _cite_entry(16, _TITLE_X, code="S3")]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["evidence"][0].startswith("entry 10 repeats as entry 16 (S3/S3, same title)")


def test_record_rule_ignores_a_doi_field_that_is_not_a_doi():
    """A placeholder such as "n/a" in two entries' doi fields names no record."""
    blocks = [("p", f"1. {_TITLE_X}. 2020. Example Journal."),
              ("p", f"9. {_TITLE_X}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, doi="n/a"),
                            _cite_entry(13, None, year=None, doi="N/A")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_pairs_a_shared_pmid_with_a_typo_in_one_title():
    typo = _TITLE_X.replace("repeated", "repaeted")
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {typo}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, pmid="PMID: 12345678"),
                            _cite_entry(11, typo, pmid="12345678")]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert "same pmid:12345678" in findings[0]["evidence"][0]


def test_record_rule_quiet_when_stage6_dropped_one_copy():
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_Y}. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), _cite_entry(11, _TITLE_X)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_counts_an_abstract_of_the_article_as_its_own_rendering():
    """An S4 abstract carrying the article's title renders it once more: two
    renderings are the article once and its abstract once, not a duplicate."""
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_X}. Abstract, 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), _cite_entry(11, _TITLE_X),
                            _cite_entry(12, _TITLE_X, code="S4")]}
    assert lint_duplicate_records(blocks, stage_5d) == []
    blocks.append(("p", f"3. {_TITLE_X}. 2020."))
    assert len(lint_duplicate_records(blocks, stage_5d)) == 1


def _grant_table(title, extra=""):
    return ("table", f"Project title:\n{title}\n{extra}")


def test_record_rule_flags_one_grant_under_two_funding_codes():
    blocks = [_grant_table(_TITLE_X, "Current"), _grant_table(_TITLE_X, "Past")]
    stage_5d = {"entries": [_grant_entry(27, "M2A", _TITLE_X, agency="Agency A"),
                            _grant_entry(127, "M2B", _TITLE_X, start="2021",
                                         agency="Agency A", grant_number="R01 1234")]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["evidence"][0].startswith(
        "entry 27 repeats as entry 127 (M2A/M2B, same title, year)")


@pytest.mark.parametrize("field, first, second", [
    ("grant_number", "R01 1111", "P01 2222"),   # one supplement per parent grant
    ("pi_name", "Trainee One", "Trainee Two"),   # one training grant, two trainees
    ("agency", "Agency A", "Agency B"),
    ("start_date", "2021", "2018"),             # a renewal
])
def test_record_rule_quiet_on_two_grants_sharing_a_title(field, first, second):
    blocks = [_grant_table(_TITLE_X), _grant_table(_TITLE_X)]
    stage_5d = {"entries": [_grant_entry(1, "M2B", _TITLE_X, **{field: first}),
                            _grant_entry(2, "M2B", _TITLE_X, **{field: second})]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_quiet_on_two_grants_of_one_year_under_different_titles():
    """Two grants starting the same year with nothing that conflicts are two
    grants when their titles differ, each rendered once in its own table."""
    blocks = [_grant_table(_TITLE_X), _grant_table(_TITLE_Y)]
    stage_5d = {"entries": [_grant_entry(1, "M2A", _TITLE_X),
                            _grant_entry(2, "M2B", _TITLE_Y)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_pairs_a_current_and_a_completed_grant():
    """M2C belongs to the grant group too: a grant listed as pending (M2A)
    and again under another funding code (M2C) is one record."""
    blocks = [_grant_table(_TITLE_X, "Pending"), _grant_table(_TITLE_X, "Other")]
    stage_5d = {"entries": [_grant_entry(5, "M2A", _TITLE_X),
                            _grant_entry(6, "M2C", _TITLE_X)]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["evidence"][0].startswith(
        "entry 5 repeats as entry 6 (M2A/M2C, same title, year)")


def test_record_rule_grant_without_a_start_year_is_not_paired():
    blocks = [_grant_table(_TITLE_X), _grant_table(_TITLE_X)]
    stage_5d = {"entries": [_grant_entry(1, "M2B", _TITLE_X, start=None),
                            _grant_entry(2, "M2C", _TITLE_X, start=None)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_grant_needs_two_tables_not_paragraphs():
    blocks = [_grant_table(_TITLE_X), ("p", f"1. {_TITLE_X}, 2021.")]
    stage_5d = {"entries": [_grant_entry(1, "M2A", _TITLE_X),
                            _grant_entry(2, "M2B", _TITLE_X)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_and_block_rule_count_one_duplicate_once():
    body = f"Doe A. {_TITLE_X}. Journal of Examples. 2020;1:1-2."
    blocks = [("p", "D. PUBLICATIONS"), ("p", f"1. {body}"), ("p", f"2. {body}")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), _cite_entry(11, _TITLE_X)]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["message"].startswith("1 duplicated record(s)")
    assert len(findings[0]["evidence"]) == 1


def test_record_rule_quiet_on_one_title_under_two_citation_codes():
    """An S1 and an S2 sharing a title are two record kinds, not one record
    listed twice, even where the title renders once per entry and once more."""
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_X}. Review, 2020."),
              ("p", f"3. {_TITLE_X}. Reprint, 2020.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, code="S1"),
                            _cite_entry(11, _TITLE_X, code="S2")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_ignores_a_title_shorter_than_the_minimum():
    short = "Synthetic short title"
    assert len(short) < DUPLICATE_RECORD_TITLE_MIN_CHARS
    blocks = [("p", f"1. {short}. Journal one. 2020."), ("p", f"7. {short}. Journal two. 2020.")]
    stage_5d = {"entries": [_cite_entry(10, short), _cite_entry(11, short)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_evidence_is_capped_at_five_but_counts_every_pair():
    titles = [f"{_TITLE_X} variant number {n}" for n in range(6)]
    blocks = [("p", f"{i}. {t}. 2020.") for i, t in enumerate(titles + titles, 1)]
    stage_5d = {"entries": [_cite_entry(10 + n, t) for n, t in enumerate(titles)]
                + [_cite_entry(50 + n, t) for n, t in enumerate(titles)]}
    findings = lint_duplicate_records(blocks, stage_5d)
    assert findings[0]["message"].startswith("6 duplicated record(s)")
    assert len(findings[0]["evidence"]) == 5


def test_record_rule_quiet_on_a_grant_and_a_citation_sharing_a_title():
    """A grant first in the entry order must not pair with an article that
    carries its title, however often the title renders."""
    blocks = [_grant_table(_TITLE_X), _grant_table(_TITLE_X), ("p", f"1. {_TITLE_X}. 2021.")]
    stage_5d = {"entries": [_grant_entry(1, "M2A", _TITLE_X, start="2021"),
                            _cite_entry(2, _TITLE_X, year="2021")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_ignores_a_pmid_too_short_to_be_one():
    """A page number read as a PMID names no record: an untitled copy that
    shares only it is not paired."""
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"2. {_TITLE_X}. 2020;12:345.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X, pmid="345"),
                            _cite_entry(11, None, pmid="345")]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_record_rule_counts_only_enumerated_paragraphs_for_a_citation():
    """A heading or prose paragraph carrying the title is not a rendering."""
    blocks = [("p", f"1. {_TITLE_X}. 2020."), ("p", f"Selected work: {_TITLE_X}.")]
    stage_5d = {"entries": [_cite_entry(10, _TITLE_X), _cite_entry(11, _TITLE_X)]}
    assert lint_duplicate_records(blocks, stage_5d) == []


def test_run_doctor_wires_stage_5d_into_duplicate_records(tmp_path):
    """`stage_5d` is an optional view: drop the registry wiring and the
    record rule goes silently dead while the block rule still runs."""
    root = tmp_path / "outputs"
    doc = Document()
    doc.add_paragraph("D. PUBLICATIONS")
    doc.add_paragraph(f"1. Doe A. {_TITLE_X}. Journal of Examples. 2020.")
    for i in range(10):
        doc.add_paragraph(f"{i + 2}. Filler citation {i} on an unrelated topic, 2001.")
    doc.add_paragraph(f"12. Doe A. {_TITLE_X}. J Ex. 2020.")
    out_dir = root / "stage_6_wcm_documents"
    out_dir.mkdir(parents=True)
    doc.save(out_dir / "DUPTST_cv_wcm.docx")
    s5d_dir = root / "stage_5d_citation_formatted"
    s5d_dir.mkdir()
    (s5d_dir / "DUPTST_cv_citation_formatted.json").write_text(json.dumps(
        {"entries": [_cite_entry(5, _TITLE_X), _cite_entry(50, _TITLE_X)]}))
    fired = [f for f in run_doctor(root, "DUPTST")["findings"]
             if f["lint"] == "duplicate_records"]
    assert len(fired) == 1
    assert fired[0]["evidence"][0].startswith("entry 5 repeats as entry 50")


# --- #446 review T1.6 (#746): what counts as a record line in lint 8 --------

@pytest.mark.parametrize("line", [
    "Jun 2020-Jun 2025, Assistant Professor of Medicine, Weill Cornell",
    "2018 - Present: Attending Physician, NewYork-Presbyterian Hospital",
    "2015-2017\tResident, Internal Medicine, Mount Sinai Hospital",
    # stage 2 glues the payload onto the end year (farm: 2068_Yount_Cv, web08)
    "2008-15Associate Professor, Department of Behavioral Sciences",
    "1996-8\tResearch Fellowship, Andrew Mellon Foundation",
    "2008-presentAssistant Professor of Medicine, Division of Cardiology",
    # open-ended range, single year, space-separated payload (farm shapes)
    "2013-\tUniversity Course on Violence (U, G, spring)",
    "2023 – Excellence in Undergraduate Teaching Award, Northern Illinois",
    "1999-2002 Ilya Laufer, Stony Brook University Medical Student",
])
def test_record_lines_keeps_every_farm_record_shape(line):
    """T1.6 positive controls: every date-prefixed record shape the 66-CV
    farm's stage-4 text carries is still a record under the tightened rule.
    Measured on the farm: the old length-plus-prefix rule admits 226 lines,
    the new one keeps 203, and the 23 it drops are all bare date lists (next
    test) -- no worded record changes class."""
    assert _record_lines(line) == [line]


@pytest.mark.parametrize("line", [
    "2020 - the year our program expanded to three campuses and beyond",
    "2019 – a period of rapid growth in the division that continued",
    "2020 - nowadays the program runs across all three campuses",
    "February 2018 – Present",
    "2004 –2020 2004-2010",
    "2006-2010, 2012, 2013",
])
def test_record_lines_rejects_date_prefixed_prose_and_bare_dates(line):
    """T1.6 negative cases: date-prefixed running prose (lowercase after the
    dash; 'nowadays' must not read as the open-ended word 'now') and bare
    date lines with no worded payload (the farm's Bostwick date column) are
    not records. The old rule admitted every one of these: each is long
    enough for its length floor and opens with a date and a dash."""
    assert len(line) >= 20
    assert _record_lines(line) == []


def test_bare_date_lines_count_toward_the_fused_floor_but_are_never_verified():
    """T1.6: a bare date line is a split-off date column -- evidence that the
    entry fuses several records, so the entry stays a lint-8 candidate -- but
    not a record to verify, so it no longer inflates the denominator. Farm
    shape: Bostwick entry 105 read '1 of 3 records absent' with two of the
    three being date lists; it now reads '1 of 1', and the one real record
    (genuinely absent from that output) is still reported."""
    pipe_row = ("AAP Resident Grief and Loss Curriculum Development Workgroup "
                "Member | 2002-2020")
    text = f"{pipe_row}\n2004 –2020 2004-2010\n2006-2010, 2012, 2013"
    blocks = [("p", "Q. COMMITTEE SERVICE"),
              ("p", "Neonatal quality improvement board, institutional member")]
    fused = {"entries": [{"element_idx_start": 105, "taxonomy_code": "Q1",
                          "text": text}]}
    findings = lint_unrendered_records(fused, blocks)
    assert len(findings) == 1
    assert "1 of 1 records absent" in findings[0]["message"]
    assert findings[0]["evidence"] == [pipe_row]

    single = {"entries": [{"element_idx_start": 105, "taxonomy_code": "Q1",
                           "text": pipe_row}]}
    assert lint_unrendered_records(single, blocks) == []


# --- #446 review T1.5 (#746): which output line may vouch for a record -----

_RECORD_2019 = ("Smith J, Jones K. Cardiac outcomes in elderly patients. "
                "J Cardiol. 2019;12:45-50.")


def test_record_rendered_rejects_the_same_title_under_a_different_year():
    """T1.5 adversarial: shared surnames AND title tokens, different year.
    Under the old per-line overlap the 2021 line vouched for the 2019 record
    (7 of 7 tokens); a dated output line that disagrees on the year is a
    different record sharing the words, so it must not."""
    other_year = _rendered_lines([
        ("p", "Smith J, Jones K. Cardiac outcomes in elderly patients. "
              "J Cardiol. 2021;14:1-9.")])
    assert _record_rendered(_RECORD_2019, "", other_year) is False


def test_record_rendered_accepts_a_reformatted_same_year_or_undated_line():
    """T1.5 positive controls: the 5d-reformatted citation with the same year
    still vouches, and a line with no year of its own (5c puts the date on a
    separate bullet) cannot disagree and falls back to token overlap."""
    same_year = _rendered_lines([
        ("p", "Smith J, Jones K (2019). Cardiac outcomes in elderly patients. "
              "Journal of Cardiology, 12, 45-50.")])
    undated = _rendered_lines([
        ("p", "Smith J, Jones K. Cardiac outcomes in elderly patients. "
              "Journal of Cardiology.")])
    assert _record_rendered(_RECORD_2019, "", same_year) is True
    assert _record_rendered(_RECORD_2019, "", undated) is True


def test_record_rendered_cjk_line_is_unverifiable_not_absent():
    """#722: CJK is excluded from the token check (no word boundaries, floor
    never measured), so a CJK record absent from the output is None, not a
    False that lint 8 would report as a dropped record. Latin absence stays
    False. Invented text."""
    out = _rendered_lines([("p", "Completely unrelated Cardiology Blorvane line")])
    cjk = "2015年4月 東京大学医学部附属病院 循環器内科 准教授として心不全の臨床研究に従事"
    assert _record_rendered(cjk, "", out) is None
    assert _record_rendered(_RECORD_2019, "", out) is False


def test_record_rendered_farm_case_different_course_with_the_same_title_words():
    """T1.5, the farm's own instance (2054_Opresko_Cv): a 2008 course record
    was vouched for by the CV's 2006 course line, which shares 4 of its 5
    tokens; the year veto turns that into the true 'absent' the doctor A/B
    gained on that uid."""
    record = "April 2008 | 2008 Course in Scientific Management and Leadership"
    out = _rendered_lines([
        ("p", "Spring 2006 - Nominated and selected to participate, 2.5 day "
              "course on Scientific Management Leadership")])
    assert _record_rendered(record, "", out) is False


def test_record_rendered_ignores_tokens_ubiquitous_in_the_output():
    """T1.5 adversarial: the owner's surname and home institution sit on
    every citation line of their own CV, so they vouch for nothing. Twelve
    filler lines make 'bostwick'/'weill'/'cornell'/'medicine' ubiquitous;
    the old rule then let a DIFFERENT paper vouch at 8 of 10 tokens, while
    the distinctive-token overlap (4 of 6) correctly does not. A record left
    with fewer than RENDER_TOKEN_MIN_COUNT distinctive tokens is None
    (unverifiable), never a manufactured absence."""
    topics = ["asthma", "measles", "obesity", "anemia", "eczema", "scoliosis",
              "autism", "diabetes", "epilepsy", "jaundice", "colic", "croup"]
    assert len(topics) >= RENDER_UBIQUITOUS_MIN_LINES
    filler = [("p", f"Bostwick S, Weill Cornell Medicine. Pediatric {t} clinic.")
              for t in topics]
    other_paper = ("p", "Bostwick S, Weill Cornell Medicine. Sleep apnea "
                        "outcomes in adolescent athletes.")
    out = _rendered_lines(filler + [other_paper])
    assert {"bostwick", "weill", "cornell", "medicine"} <= out.ubiquitous
    assert not {"sleep", "apnea"} & out.ubiquitous

    record = ("Bostwick S, Weill Cornell Medicine. Sleep apnea screening in "
              "adolescent athletes: a pilot.")
    assert _record_rendered(record, "", out) is False
    short = "Bostwick S, Weill Cornell Medicine. Sleep apnea."
    assert _record_rendered(short, "", out) is None


def test_passage_key_keeps_non_ascii_letters_and_ascii_unchanged():
    """#541: non-ASCII letters no longer fold to whitespace; ASCII keys are
    byte-identical to the old [^a-z0-9] fold."""
    from unified_pipeline.doctor.lints.render import _passage_key
    assert _passage_key("1. Zoë Brändström, Иван") == "zoe brandstrom иван"
    assert _passage_key("• Plain_text: A-B (2019)") == "plain text a b 2019"


def test_name_tokens_are_unicode_aware_and_split_on_underscore():
    """#541: Cyrillic/Greek names produce tokens (the ASCII regex gave an
    empty set); '_' still separates, as in the old [a-z0-9]+."""
    from unified_pipeline.doctor.lints.render import _name_tokens
    assert _name_tokens("1. Иван Петров") == {"иван", "петров"}
    assert _name_tokens("Ελένη Παπαδοπούλου") == {"ελενη", "παπαδοπουλου"}
    assert _name_tokens("Zoë Brändström") == {"zoe", "brandstrom"}
    assert _name_tokens("ab_cd ef") == {"ab", "cd", "ef"}
