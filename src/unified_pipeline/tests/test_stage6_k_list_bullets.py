"""Section K entries must be Word list paragraphs, not a literal bullet character.

Across the corpus, 2,570 K bullets were a literal glyph on a paragraph carrying
no `w:numPr` at all. A literal glyph is part of the paragraph text, so Word's
outline and navigation, accessibility tooling and anything re-parsing the output
all see a bullet-shaped character rather than a list item -- and there is no
level to set, which is why #423 cannot be fixed at the render without this first.

Originally section K only, since `_insert_bulleted_entry` is shared. #483 then
extended it to that helper's three non-K call sites and to
`_fill_researcher_profiles`. The two emitters in the appendix/recovery region
still write a literal glyph -- their own tests assert it -- and converting them
is the remaining half of #483.

Level 0 for every K bullet, not a title/child split. Inferring hierarchy here
would mean splitting `entry['text']` on newlines, and that approach was measured
to fire on 5 of 36,933 entries (0.014%) because the content is separated by tab
and pipe. Real source levels are #423's job; this issue only supplies the list
paragraph for them to sit on.

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


def test_without_list_level_the_literal_glyph_is_unchanged():
    gen = _generator()
    idx = gen._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
    para = gen._insert_bulleted_entry(idx + 1, "A non-K bullet", None)
    assert _num_level(para) is None
    assert para.text == "• A non-K bullet"


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


# --- and the six K call sites actually ask for it ----------------------------

def test_every_teaching_call_site_requests_a_list_level():
    # Guards the wire, not the helper: deleting list_level from any one of the
    # six call sites silently restores the glyph for part of section K.
    calls = _bullet_calls_by_function()[K_CALLER]
    assert len(calls) == 6, f"expected 6 K call sites, found {len(calls)}"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "list_level" in kwargs, f"{K_CALLER} line {call.lineno} lost list_level"


def test_every_bulleted_entry_call_site_requests_a_list_level():
    # #483 extended this to the three non-K sites, so the helper now has no
    # caller left on the literal-glyph branch. The branch itself stays, because
    # the two emitters in the appendix/recovery region still take it.
    for name, calls in _bullet_calls_by_function().items():
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "list_level" in kwargs, f"{name} line {call.lineno} lost list_level"


def test_only_the_deferred_emitters_still_write_a_literal_glyph():
    """Pins the residue. Any new glyph emitter fails here rather than in a render.

    #474 handled the six K call sites, #483 the other three plus
    `_fill_researcher_profiles`. What is left is the appendix/recovery pair,
    whose own tests assert the glyph -- converting them means updating 16 of
    those tests, which is the remaining half of #483.
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
    assert emitters == {"_insert_reconsidered_segment", "_add_remaining_to_appendix"}, \
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
