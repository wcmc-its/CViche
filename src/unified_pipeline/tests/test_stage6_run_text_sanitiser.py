"""#552: a control character in LLM-written text kills the render, and the
documented citation fallback does not rescue it.

lxml's own ``Element.text`` setter raises ``ValueError: All strings must be
XML compatible...`` on the C0/C1 control range ``[\\x00-\\x08\\x0b\\x0c\\x0e-
\\x1f]`` -- the same range python-docx's ``Run.text`` setter rejects. Three
places in stage 6 build revision XML by hand and assign straight into that
setter, bypassing python-docx entirely:

- ``WCMTemplateGenerator._add_track_change_insertion``
  (stage_6_word_template.py, ``t.text = ...``)
- ``WCMTemplateGenerator._add_track_change_deletion``
  (stage_6_word_template.py, ``delText.text = ...``)
- ``BibliographySection._add_citation_with_bold_author_as_insertion``'s
  nested ``create_run_element`` (bibliography.py, ``t.text = ...``)

All three now route through the shared ``BibliographySection.
_sanitize_run_text`` (reached via the mixin as ``self._sanitize_run_text``),
which strips that range and explicitly preserves ``\\t``, ``\\n``, ``\\r`` --
both valid XML and required by ``_clean_inline_tabs``'s label/value contract
(test_cell_separators.py:34-37).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_run_text_sanitiser.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections.bibliography import (  # noqa: E402
    BibliographySection,
    _CONTROL_CHAR_PATTERN,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

# One representative codepoint from each stripped sub-range, plus the three
# preserved whitespace controls that must survive untouched.
CONTROL_CHARS = ['\x00', '\x07', '\x0b', '\x0c', '\x0e', '\x1f']
PRESERVED_WHITESPACE = ['\t', '\n', '\r']


def _generator(emit_track_changes=True):
    gen = WCMTemplateGenerator(verbose=False, emit_track_changes=emit_track_changes)
    gen.doc = Document()
    return gen


def _blank_paragraph(gen):
    return gen.doc.add_paragraph()


# --- the shared helper itself ---


@pytest.mark.parametrize("char", CONTROL_CHARS)
def test_sanitize_run_text_strips_every_stripped_subrange_member(char):
    assert BibliographySection._sanitize_run_text(f"before{char}after") == "beforeafter"


@pytest.mark.parametrize("char", PRESERVED_WHITESPACE)
def test_sanitize_run_text_preserves_tab_newline_cr(char):
    assert BibliographySection._sanitize_run_text(f"before{char}after") == f"before{char}after"


def test_sanitize_run_text_leaves_ordinary_text_untouched():
    text = "Doe J, Smith A. A study of things. Journal. 2024;10(2):100-110."
    assert BibliographySection._sanitize_run_text(text) == text


def test_control_char_pattern_never_matches_preserved_whitespace():
    # Regression guard on the regex itself, independent of the function that
    # wraps it: \x09/\x0a/\x0d must never be added to the stripped range.
    for char in PRESERVED_WHITESPACE:
        assert not _CONTROL_CHAR_PATTERN.search(char)
    for char in CONTROL_CHARS:
        assert _CONTROL_CHAR_PATTERN.search(char)


# --- site 1: stage_6_word_template.py:_add_track_change_insertion ---


def test_track_change_insertion_survives_control_character():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_insertion(para, "Enrolled 12\x0b patients")  # must not raise

    ins_elem = para._p.find(qn("w:ins"))
    assert ins_elem is not None
    t_elem = ins_elem.find(qn("w:r")).find(qn("w:t"))
    assert t_elem.text == "Enrolled 12 patients"


def test_track_change_insertion_preserves_tab_in_text():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_insertion(para, "Label\tValue")

    ins_elem = para._p.find(qn("w:ins"))
    t_elem = ins_elem.find(qn("w:r")).find(qn("w:t"))
    assert t_elem.text == "Label\tValue"


# --- site 2: stage_6_word_template.py:_add_track_change_deletion ---


def test_track_change_deletion_survives_control_character():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_deletion(para, "Enrolled 12\x0c patients")  # must not raise

    del_elem = para._p.find(qn("w:del"))
    assert del_elem is not None
    delText_elem = del_elem.find(qn("w:r")).find(qn("w:delText"))
    assert delText_elem.text == "Enrolled 12 patients"


def test_track_change_deletion_preserves_newline_in_text():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_deletion(para, "Line one\nLine two")

    del_elem = para._p.find(qn("w:del"))
    delText_elem = del_elem.find(qn("w:r")).find(qn("w:delText"))
    assert delText_elem.text == "Line one\nLine two"


# --- site 3: bibliography.py:_add_citation_with_bold_author_as_insertion ---


def test_citation_insertion_survives_control_character_without_falling_back():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)
    citation = "1. Doe J, Smith\x0b A. A study. Journal. 2024;10(2):100-110."

    gen._add_citation_with_bold_author_as_insertion(
        para, citation, "Smith A", "", author="PubMed Enrichment"
    )  # must not raise

    # The tracked-insertion path itself must have succeeded -- not the
    # except-block fallback to the plain (non-tracked) writer, which is the
    # documented "safety net" #552 found does not actually work for this
    # input class (it re-raises the identical error on the same string).
    assert gen.stats["track_changes_added"] == 1
    ins_elem = para._p.find(qn("w:ins"))
    assert ins_elem is not None
    rendered = "".join(
        (t.text or "") for r in ins_elem.findall(qn("w:r")) for t in r.findall(qn("w:t"))
    )
    assert "\x0b" not in rendered
    assert rendered == citation.replace("\x0b", "")


def test_citation_insertion_preserves_tab_in_bolded_author_split():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)
    citation = "1. Doe J, Smith A.\tA study with a tab. Journal. 2024;10(2):100-110."

    gen._add_citation_with_bold_author_as_insertion(
        para, citation, "Smith A", "", author="PubMed Enrichment"
    )

    ins_elem = para._p.find(qn("w:ins"))
    rendered = "".join(
        (t.text or "") for r in ins_elem.findall(qn("w:r")) for t in r.findall(qn("w:t"))
    )
    assert "\t" in rendered


# --- the xml:space="preserve" guard reads the SANITIZED string ---
#
# Round-2 finding. All three sites assign the sanitized text but originally
# tested the RAW text in their `startswith(' ') or endswith(' ')` guard. A
# control character in front of a leading space (`"\x0b Smith"`) is stripped,
# so the rendered run does begin with a space while the raw string does not --
# the guard skipped xml:space="preserve" and Word collapsed that space,
# gluing the run to its neighbour. Whitespace-only damage, but it is silent,
# which is exactly what the sanitiser was added to avoid.

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def test_insertion_preserves_space_exposed_by_stripping_a_control_char():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_insertion(para, "\x0b Smith")

    t_elem = para._p.find(qn("w:ins")).find(qn("w:r")).find(qn("w:t"))
    assert t_elem.text == " Smith"
    assert t_elem.get(XML_SPACE) == "preserve"


def test_deletion_preserves_space_exposed_by_stripping_a_control_char():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_deletion(para, "Smith \x0b")

    delText_elem = para._p.find(qn("w:del")).find(qn("w:r")).find(qn("w:delText"))
    assert delText_elem.text == "Smith "
    assert delText_elem.get(XML_SPACE) == "preserve"


def test_citation_insertion_preserves_space_exposed_by_stripping_a_control_char():
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)
    # No target name matches, so the whole citation is one run -- the raw
    # string starts and ends with a control character, the sanitized one with
    # a space.
    citation = "\x0b Doe J. A study. Journal. 2024;10(2):100-110. \x0b"

    gen._add_citation_with_bold_author_as_insertion(
        para, citation, "Nobody Q", "", author="PubMed Enrichment"
    )

    ins_elem = para._p.find(qn("w:ins"))
    t_elems = [t for r in ins_elem.findall(qn("w:r")) for t in r.findall(qn("w:t"))]
    assert len(t_elems) == 1
    assert t_elems[0].text == " Doe J. A study. Journal. 2024;10(2):100-110. "
    assert t_elems[0].get(XML_SPACE) == "preserve"


def test_ordinary_leading_space_still_preserved_at_all_three_sites():
    # Control arm: the guard's pre-existing behaviour for text that needs no
    # sanitising at all must be unchanged by the reordering.
    gen = _generator(emit_track_changes=True)

    para_ins = _blank_paragraph(gen)
    gen._add_track_change_insertion(para_ins, " Smith ")
    t_elem = para_ins._p.find(qn("w:ins")).find(qn("w:r")).find(qn("w:t"))
    assert t_elem.get(XML_SPACE) == "preserve"

    para_del = _blank_paragraph(gen)
    gen._add_track_change_deletion(para_del, " Smith ")
    delText_elem = para_del._p.find(qn("w:del")).find(qn("w:r")).find(qn("w:delText"))
    assert delText_elem.get(XML_SPACE) == "preserve"

    para_cit = _blank_paragraph(gen)
    gen._add_citation_with_bold_author_as_insertion(
        para_cit, " Doe J. A study. Journal. 2024;1:1-2. ", "Nobody Q", ""
    )
    t_elems = [
        t for r in para_cit._p.find(qn("w:ins")).findall(qn("w:r")) for t in r.findall(qn("w:t"))
    ]
    assert t_elems[0].get(XML_SPACE) == "preserve"


def test_text_without_boundary_spaces_still_gets_no_xml_space_attribute():
    # The other control arm: reordering must not start setting the attribute
    # on runs that never needed it.
    gen = _generator(emit_track_changes=True)
    para = _blank_paragraph(gen)

    gen._add_track_change_insertion(para, "Enrolled 12\x0b patients")

    t_elem = para._p.find(qn("w:ins")).find(qn("w:r")).find(qn("w:t"))
    assert t_elem.text == "Enrolled 12 patients"
    assert t_elem.get(XML_SPACE) is None


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-v"]))
