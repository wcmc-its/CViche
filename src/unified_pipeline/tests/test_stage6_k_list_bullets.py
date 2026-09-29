"""Section K entries must be Word list paragraphs, not a literal bullet character.

Across the corpus, 2,570 K bullets were a literal glyph on a paragraph carrying
no `w:numPr` at all. A literal glyph is part of the paragraph text, so Word's
outline and navigation, accessibility tooling and anything re-parsing the output
all see a bullet-shaped character rather than a list item -- and there is no
level to set, which is why #423 cannot be fixed at the render without this first.

Originally section K only, since `_insert_bulleted_entry` is shared. #483's
first pass extended it to that helper's three non-K call sites, to
`_fill_researcher_profiles`, to `_insert_bulleted_entry`'s own now-dead
`list_level is None` fallback, and to `_add_remaining_to_appendix`. #483's
second pass converts the last one, `_insert_reconsidered_segment` -- its own
tests in test_stage6_unrendered_recovery.py were rewritten alongside this to
assert `w:numPr` instead of the glyph. No literal-glyph emitter remains in
this module; `test_only_the_deferred_emitter_still_writes_a_literal_glyph`
below now expects an empty set.

Level 0 for every K bullet unless the source says otherwise. Inferring hierarchy
from `entry['text']` alone would mean splitting on newlines, and that approach
was measured to fire on 5 of 36,933 entries (0.014%) because the content is
separated by tab and pipe. Real source levels are #423's job (the last section
of this file): a table row whose source cell is a title plus bullets renders
its bullets at level 1, read back from the source docx.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_k_list_bullets.py -p no:cacheprovider
"""

import ast
import inspect
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _sources():
    """Every module contributing methods to the generator.

    The #398 split moved the section fillers out of stage_6_word_template.py and
    onto mixins under stage6/sections/, so scanning the generator's own module
    would silently find zero call sites and pass the wire tests vacuously. Walk
    the MRO instead, so a call site is covered wherever it lives.
    """
    seen = {}
    for klass in WCMTemplateGenerator.__mro__:
        try:
            path = inspect.getsourcefile(klass)
        except TypeError:  # builtins such as object have no source
            continue
        if path and "unified_pipeline" in path:
            seen[path] = Path(path)
    return list(seen.values())


K_CALLER = "_insert_teaching_entry"


def _generator():
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _num_level(para):
    """The paragraph's w:ilvl, or None when it carries no list markup."""
    pPr = para._p.pPr
    numPr = pPr.find(qn("w:numPr")) if pPr is not None else None
    if numPr is None:
        return None
    return numPr.find(qn("w:ilvl")).get(qn("w:val"))


def _bullet_calls_by_function():
    """Every _insert_bulleted_entry call site, grouped by enclosing function."""
    found = {}
    for source in _sources():
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            calls = [c for c in ast.walk(node)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                     and c.func.attr == "_insert_bulleted_entry"]
            if calls:
                found.setdefault(node.name, []).extend(calls)
    return found


# --- the helper renders both ways -------------------------------------------

def test_list_level_emits_a_word_list_paragraph_and_drops_the_glyph():
    gen = _generator()
    idx = gen._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
    para = gen._insert_bulleted_entry(idx + 1, "2018-2022 - PA Student Lectures", None,
                                      list_level=0)
    assert _num_level(para) == "0"
    assert not para.text.startswith("•")
    assert para.text == "2018-2022 - PA Student Lectures"


def test_without_list_level_still_emits_a_word_list_paragraph_at_level_0():
    # #483: the list_level-omitted fallback used to prefix a literal glyph
    # instead of calling _apply_list_bullet. No production call site has
    # omitted list_level since #485 (all three non-K sites pass 0), so this
    # was dead-but-reachable code; closed for defense in depth so a future
    # caller that forgets list_level gets a real list paragraph, not residue.
    gen = _generator()
    idx = gen._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
    para = gen._insert_bulleted_entry(idx + 1, "A non-K bullet", None)
    assert _num_level(para) == "0"
    assert not para.text.startswith("•")
    assert para.text == "A non-K bullet"


def test_teaching_entries_render_as_list_paragraphs_end_to_end():
    gen = _generator()
    gen._fill_teaching({"K1": [{
        "taxonomy_code": "K1",
        "text": "2018-2022 - PA Student Lectures, Lecturer",
        "extracted_fields": {"formatted_text": "2018-2022 - PA Student Lectures, Lecturer"},
    }]})
    rendered = [p for p in gen.doc.paragraphs if "PA Student Lectures" in p.text]
    assert len(rendered) == 1
    assert _num_level(rendered[0]) == "0"
    assert "•" not in rendered[0].text


# --- and _add_remaining_to_appendix renders the same way ---------------------

def test_appendix_segment_emits_a_word_list_paragraph_and_drops_the_glyph():
    # #483: this was the second of the two remaining glyph emitters. A mutant
    # restoring `f"• {segment_text}"` fails both this direct render check
    # and the AST census in test_only_the_deferred_emitter_still_writes_a_literal_glyph.
    gen = _generator()
    gen._add_remaining_to_appendix([
        ("Recovered committee membership, synthetic case", "P", 12.0),
    ])
    rendered = [p for p in gen.doc.paragraphs
                if "Recovered committee membership" in p.text]
    assert len(rendered) == 1
    assert _num_level(rendered[0]) == "0"
    assert not rendered[0].text.startswith("•")
    assert rendered[0].text == "Recovered committee membership, synthetic case"


def test_reconsidered_segment_emits_a_word_list_paragraph_and_drops_the_glyph():
    # #483 R2: this was the fourth and last glyph emitter. A mutant restoring
    # `f"• {...}"` fails both this direct render check and the AST census in
    # test_no_literal_glyph_emitter_remains.
    gen = _generator()
    inserted = gen._insert_reconsidered_segment(
        "Recovered synthetic committee record, case ZQ-4", "D1")
    assert inserted
    rendered = [p for p in gen.doc.paragraphs
                if "Recovered synthetic committee record" in p.text]
    assert len(rendered) == 1
    assert _num_level(rendered[0]) == "0"
    assert not rendered[0].text.startswith("•")
    assert rendered[0].text == "Recovered synthetic committee record, case ZQ-4"


def test_reconsidered_segment_with_no_section_anchor_returns_false():
    # Negative path: an unmappable code returns False without inserting
    # anything (callers fall back to _add_remaining_to_appendix) -- exercised
    # here rather than only through the appendix's own tests, since it is
    # this function's own guard.
    gen = _generator()
    before = len(gen.doc.paragraphs)
    inserted = gen._insert_reconsidered_segment(
        "Unroutable synthetic content", "ZZ-NOT-A-CODE")
    assert inserted is False
    assert len(gen.doc.paragraphs) == before


def test_appendix_noise_only_batch_still_adds_no_paragraphs():
    # Negative path: the pre-existing empty/boilerplate filter must survive
    # the glyph-to-list conversion unchanged -- an all-noise batch adds
    # nothing, list paragraph or otherwise.
    gen = _generator()
    before = len(gen.doc.paragraphs)
    gen._add_remaining_to_appendix([("", "T", 0.0), ("Page 2 of 9", "T", 0.0)])
    assert len(gen.doc.paragraphs) == before


# --- and the K call site actually asks for it --------------------------------

def test_every_teaching_call_site_requests_a_list_level():
    # Guards the wire, not the helper: dropping list_level from the K call site
    # silently restores the glyph for section K. The count was 7 until #476
    # moved the text reconstruction into `_teaching_entry_lines`, leaving
    # `_insert_teaching_entry` with one insertion loop instead of seven
    # per-branch calls; the count still has to be pinned, so a new emitter
    # added beside it fails here rather than in a render.
    calls = _bullet_calls_by_function()[K_CALLER]
    assert len(calls) == 1, f"expected 1 K call site, found {len(calls)}"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "list_level" in kwargs, f"{K_CALLER} line {call.lineno} lost list_level"


def test_every_bulleted_entry_call_site_requests_a_list_level():
    # #483 extended this to the three non-K sites, so the helper has no
    # caller left that omits list_level. The parameter stays optional --
    # omitting it now defaults to level 0 rather than a literal glyph -- but
    # every known call site still names it explicitly.
    for name, calls in _bullet_calls_by_function().items():
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "list_level" in kwargs, f"{name} line {call.lineno} lost list_level"


def test_no_literal_glyph_emitter_remains():
    """Pins the residue at zero. Any new glyph emitter fails here rather than
    in a render.

    #474 handled the six K call sites; #483's first pass converted the other
    three non-K `_insert_bulleted_entry` sites, `_fill_researcher_profiles`,
    that helper's own fallback branch, and `_add_remaining_to_appendix`;
    #483's second pass converted the last one, `_insert_reconsidered_segment`
    (see test_reconsidered_segment_emits_a_word_list_paragraph_and_drops_the_glyph
    below, and the rewritten assertions in test_stage6_unrendered_recovery.py).
    """
    emitters = set()
    for source in _sources():
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for call in ast.walk(node):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "add_run" and call.args):
                    continue
                arg = call.args[0]
                parts = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
                first = parts[0] if parts else None
                if isinstance(first, ast.Constant) and str(first.value).startswith("•"):
                    emitters.add(node.name)
    assert emitters == set(), \
        f"unexpected literal-glyph emitters: {sorted(emitters)}"


def test_the_dead_track_changes_bullet_writer_is_gone():
    # A second bullet writer that looked like the one the teaching path would
    # use when emit_track_changes is on (default True), with 0 call sites.
    names = set()
    for source in _sources():
        tree = ast.parse(source.read_text())
        names |= {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_insert_bulleted_entry_with_track_changes" not in names


# --- and the validator still recognises a bullet that has no glyph -----------

def test_validator_sees_k_content_rendered_as_list_paragraphs():
    # _validate_output detected K content by a literal "•" prefix. Dropping the
    # glyph made that predicate blind, so check 3 fired on every CV with a fully
    # populated section K. Observed on C0ZGFW before _is_bullet_paragraph.
    gen = _generator()
    gen._fill_teaching({"K1": [{
        "taxonomy_code": "K1",
        "text": "2018-2022 - PA Student Lectures, Lecturer",
        "extracted_fields": {"formatted_text": "2018-2022 - PA Student Lectures, Lecturer"},
    }]})
    rendered = [p for p in gen.doc.paragraphs if "PA Student Lectures" in p.text]
    assert "•" not in rendered[0].text, "precondition: the glyph is gone"

    checks = {issue["check"] for issue in gen._validate_output()}
    assert "no_visible_teaching_content" not in checks


def test_validator_still_catches_semicolon_fused_list_paragraphs():
    # The same blindness, quieter: check 1 would simply stop reporting fused
    # K bullets rather than misreport, so nothing would have surfaced it.
    gen = _generator()
    idx = gen._find_paragraph_with_text("Didactic teaching")
    gen._insert_bulleted_entry(idx + 1, "Lecture A; Lecture B; Lecture C; Lecture D; Lecture E",
                               None, list_level=0)

    fused = [i for i in gen._validate_output() if i["check"] == "semicolon_fused_bullets"]
    assert [i["code"] for i in fused] == ["K1"]


# --- #423: sub-bullet levels re-read from the source table cell ---------------
#
# The reader flattens a table cell to newline-joined text, so the `w:numPr`
# that marks a title's children reaches no stage artifact. Stage 6 re-reads it
# from the source docx (`original_doc_path`), matching the row by CONTENT. The
# 100-CV corpus holds 8 such cells against production's 223, so these synthetic
# fixtures, not the corpus, are the evidence the fix works.

from docx.oxml import OxmlElement  # noqa: E402

_TITLE = "Sample Outreach Program"
_CHILDREN = ["Develop a curriculum for residents", "Coordinate with the partner site"]


def _bullet(para):
    """Give a fixture paragraph real list markup without calling the code under test."""
    numPr = OxmlElement("w:numPr")
    for tag, val in (("w:ilvl", "1"), ("w:numId", "1")):
        el = OxmlElement(tag)
        el.set(qn("w:val"), val)
        numPr.append(el)
    para._p.get_or_add_pPr().append(numPr)


def _source_docx(tmp_path, cells, extra_cols=1, header_row=False):
    """A table whose cell 0 of each row holds `(text, bulleted)` paragraphs."""
    doc = Document()
    table = doc.add_table(rows=len(cells) + int(header_row), cols=1 + extra_cols)
    if header_row:
        table.rows[0].cells[0].text = "TEACHING"
    for r, paras in enumerate(cells, start=int(header_row)):
        cell = table.rows[r].cells[0]
        for i, (text, bulleted) in enumerate(paras):
            para = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
            para.add_run(text)
            if bulleted:
                _bullet(para)
    path = tmp_path / "source.docx"
    doc.save(str(path))
    return str(path)


def _row_entry(lines, tail=" | 2019 - 2024 | Course Director", row_index=0, table_index=0):
    """An entry as stage 2 builds it after #488: the trailing columns ride cell 0's first line."""
    text = "\n".join([lines[0] + tail] + lines[1:]) if tail else "\n".join(lines)
    return {"taxonomy_code": "K3", "element_type": "table_row", "table_index": table_index,
            "row_index": row_index, "text": text,
            "extracted_fields": {"formatted_text": " | ".join(lines)}}


def _render_k(source, entries):
    gen = _generator()
    gen._fill_teaching({"K3": entries}, source)
    return gen


def _levels_of(gen, lines):
    out = []
    for line in lines:
        hits = [p for p in gen.doc.paragraphs if line in p.text]
        assert len(hits) == 1, f"expected one paragraph for {line!r}, got {len(hits)}"
        out.append(_num_level(hits[0]))
    return out


_HIER = [(_TITLE, False)] + [(c, True) for c in _CHILDREN]
_LINES = [_TITLE] + _CHILDREN


def test_source_sub_bullets_render_as_level_1_children_and_stay_separate_bullets(tmp_path):
    # The row's date/role column rides the title line (#488), so the raw row is
    # also what `wrapped_row_text` (#987) would weld into ONE bullet; the source
    # cell is what says it is a hierarchy instead.
    gen = _render_k(_source_docx(tmp_path, [_HIER]), [_row_entry(_LINES)])
    assert _levels_of(gen, _LINES) == ["0", "1", "1"]


def test_the_title_is_never_demoted_below_its_children(tmp_path):
    # Indexing the reversed insertion loop with j instead of len-1-j gives
    # ["1", "1", "0"] here while every helper-level assertion still passes.
    gen = _render_k(_source_docx(tmp_path, [_HIER]), [_row_entry(_LINES, tail="")])
    levels = _levels_of(gen, _LINES)
    assert levels[0] == "0" and set(levels[1:]) == {"1"}


def test_a_header_row_does_not_shift_the_lookup(tmp_path):
    # `row_index` counts the reader's processed rows, not `table.rows`: with the
    # table's header row 0 dropped, entry row 0 is source row 1. Matching by
    # content is what makes this work; using row_index directly finds the header.
    src = _source_docx(tmp_path, [_HIER], header_row=True)
    assert _levels_of(_render_k(src, [_row_entry(_LINES, row_index=0)]), _LINES) == ["0", "1", "1"]


def test_the_right_row_is_found_among_several(tmp_path):
    other = [("Another Program", False), ("Its only child bullet", True)]
    flat = [("A flat row", False), ("stays flat", False)]
    src = _source_docx(tmp_path, [flat, other, _HIER])
    gen = _render_k(src, [_row_entry(_LINES, row_index=2)])
    assert _levels_of(gen, _LINES) == ["0", "1", "1"]


def test_without_a_source_every_line_stays_level_0(tmp_path):
    gen = _render_k(None, [_row_entry(_LINES)])
    assert _levels_of(gen, _LINES) == ["0", "0", "0"]


def test_a_flat_cell_a_cell_of_peer_bullets_and_a_bulleted_title_stay_flat(tmp_path):
    # No title/children split means no hierarchy to restore; 104 of 217 measured
    # source sub-bullet clusters were whole sections of peers, where flat is right.
    for shape in ([False, False, False], [True, True, True], [True, False, True]):
        cell = [(t, b) for t, b in zip(_LINES, shape)]
        gen = _render_k(_source_docx(tmp_path, [cell]), [_row_entry(_LINES)])
        assert _levels_of(gen, _LINES) == ["0", "0", "0"], shape


def test_a_row_that_does_not_line_up_or_disagrees_is_rejected(tmp_path):
    # Count mismatch: a neighbouring column contributed a paragraph.
    assert _levels_of(_render_k(_source_docx(tmp_path, [_HIER + [("A fourth", True)]]),
                                [_row_entry(_LINES)]), _LINES) == ["0", "0", "0"]
    # Same count, different words in ONE child: the count guard alone accepts it.
    diverged = [_HIER[0], (_CHILDREN[0] + " in a different year", True), _HIER[2]]
    assert _levels_of(_render_k(_source_docx(tmp_path, [diverged]),
                                [_row_entry(_LINES)]), _LINES) == ["0", "0", "0"]


def test_a_tail_is_licensed_on_the_first_line_only(tmp_path):
    # The trailing columns may ride cell 0's first paragraph; a tail on a child
    # line means it is not this row.
    entry = _row_entry(_LINES)
    entry["text"] = "\n".join([_LINES[0], _LINES[1] + " | 2019", _LINES[2]])
    assert _levels_of(_render_k(_source_docx(tmp_path, [_HIER]), [entry]), _LINES) == ["0", "0", "0"]


def test_whitespace_kept_before_the_separator_does_not_break_the_first_line_match(tmp_path):
    # `join_row_cells` keeps the spaces a title paragraph ends with, so the
    # entry's first line is "Title   | 2019", not "Title | 2019". 26 of 30 rows
    # measured on production-shaped CVs that failed to match were this alone.
    entry = _row_entry(_LINES, tail="   | 2019 - 2024")
    assert _levels_of(_render_k(_source_docx(tmp_path, [_HIER]), [entry]), _LINES) == ["0", "1", "1"]
    # ...but words after the title, before the separator, are a different row.
    entry = _row_entry(_LINES, tail=" and more words | 2019 - 2024")
    assert _levels_of(_render_k(_source_docx(tmp_path, [_HIER]), [entry]), _LINES) == ["0", "0", "0"]


def test_identical_rows_that_disagree_on_levels_fail_closed(tmp_path):
    flat_twin = [(t, False) for t in _LINES]
    src = _source_docx(tmp_path, [_HIER, flat_twin])
    assert _levels_of(_render_k(src, [_row_entry(_LINES)]), _LINES) == ["0", "0", "0"]


def test_an_unreadable_source_warns_once_per_run_and_renders_flat(tmp_path, caplog):
    entries = [_row_entry(_LINES), _row_entry(["Second", "Second child"]),
               _row_entry(["Third", "Third child"])]
    with caplog.at_level("WARNING"):
        gen = _render_k(str(tmp_path / "gone.docx"), entries)
    warned = [r for r in caplog.records if "Could not read source cell levels" in r.getMessage()]
    assert len(warned) == 1
    assert _levels_of(gen, _LINES) == ["0", "0", "0"]


def test_a_stale_table_index_warns_and_renders_flat(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        gen = _render_k(_source_docx(tmp_path, [_HIER]), [_row_entry(_LINES, table_index=7)])
    assert any("Could not read source cell levels" in r.getMessage() for r in caplog.records)
    assert _levels_of(gen, _LINES) == ["0", "0", "0"]


def test_a_wrapped_cell_row_without_source_levels_still_rejoins_to_one_bullet(tmp_path):
    # #987 is untouched when the source has no hierarchy: one course whose title
    # cell wraps stays ONE bullet.
    flat = [(t, False) for t in _LINES]
    entry = _row_entry(_LINES)
    entry["element_idx_start"] = entry["element_idx_end"] = "3.0"
    gen = _render_k(_source_docx(tmp_path, [flat]), [entry])
    hits = [p for p in gen.doc.paragraphs if _TITLE in p.text]
    assert len(hits) == 1 and _CHILDREN[0] in hits[0].text


def test_hierarchical_row_is_not_rejoined_even_when_it_is_a_single_element_row(tmp_path):
    entry = _row_entry(_LINES)
    entry["element_idx_start"] = entry["element_idx_end"] = "3.0"
    gen = _render_k(_source_docx(tmp_path, [_HIER]), [entry])
    assert _levels_of(gen, _LINES) == ["0", "1", "1"]


def test_the_real_reader_and_stage_2_join_shape_round_trips_to_level_1_children(tmp_path):
    # The contract with stage 2, not a hand-built entry: the reader's own
    # `join_row_cells` text (date column attached to the title line, #488) and
    # its own table_index, over a table whose row 0 is the section header and so
    # is dropped from the reader's rows (row_index 0 is source row 1).
    from unified_pipeline.core.docx_structure_extractor import (
        extract_unified_elements, join_row_cells, row_cell_texts)
    src = _source_docx(tmp_path, [_HIER, [("A flat row", False)]], header_row=True)
    content = next(e for e in extract_unified_elements(src)["elements"]
                   if e["type"] == "table_content")
    row = content["data"][0]
    row[1]["text"] = "2019 - 2024"
    entry = {"taxonomy_code": "K3", "element_type": "table_row",
             "table_index": content["table_index"], "row_index": 0,
             "text": join_row_cells(row_cell_texts(row)),
             "extracted_fields": {"formatted_text": " | ".join(_LINES)}}
    assert entry["text"].split("\n")[0] == f"{_TITLE} | 2019 - 2024"
    assert _levels_of(_render_k(src, [entry]), _LINES) == ["0", "1", "1"]


def test_generate_hands_the_original_doc_path_to_section_k():
    # The wire: the K dispatch in `generate()` must pass `original_doc_path`.
    # Dropping the argument leaves every render flat with all helper tests green.
    src = inspect.getsource(WCMTemplateGenerator.generate)
    assert "self._fill_teaching(entries_by_code, original_doc_path)" in src
