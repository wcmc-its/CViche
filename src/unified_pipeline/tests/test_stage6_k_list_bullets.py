"""Section K entries must be Word list paragraphs, not a literal bullet character.

Across the corpus, 2,570 K bullets were a literal glyph on a paragraph carrying
no `w:numPr` at all. A literal glyph is part of the paragraph text, so Word's
outline and navigation, accessibility tooling and anything re-parsing the output
all see a bullet-shaped character rather than a list item -- and there is no
level to set, which is why #423 cannot be fixed at the render without this first.

Scope is section K only, and deliberately: `_insert_bulleted_entry` is shared,
and its other three call sites keep the glyph. The 241 remaining non-K glyphs
come from four separate emitters and are #483.

Level 0 for every K bullet, not a title/child split. Inferring hierarchy here
would mean splitting `entry['text']` on newlines, and that approach was measured
to fire on 5 of 36,933 entries (0.014%) because the content is separated by tab
and pipe. Real source levels are #423's job; this issue only supplies the list
paragraph for them to sit on.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_k_list_bullets.py -p no:cacheprovider
"""

import ast
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

_MODULE = Path(WCMTemplateGenerator.__module__.replace(".", "/"))
_SOURCE = (_SRC / _MODULE).with_suffix(".py")

K_CALLER = "_insert_teaching_entry"
NON_K_CALLERS = ("_insert_multiline_as_bullets", "_fill_clinical_practice",
                 "_fill_hospital_affiliation")


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
    tree = ast.parse(_SOURCE.read_text())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls = [c for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "_insert_bulleted_entry"]
        if calls:
            found[node.name] = calls
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


def test_non_k_call_sites_stay_on_the_literal_glyph():
    # #483, not this issue. Passing list_level here would rewrite bullets in
    # clinical practice and hospital affiliation with no gate covering them.
    by_fn = _bullet_calls_by_function()
    for name in NON_K_CALLERS:
        for call in by_fn.get(name, []):
            kwargs = {kw.arg for kw in call.keywords}
            assert "list_level" not in kwargs, f"{name} line {call.lineno} opted into #474"
