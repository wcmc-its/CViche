"""Round 2 of PR #625 review on `stage6/sections/bibliography.py`.

Five fixes, five threads (two threads share one fix), plus a follow-up
comment on the print-vs-logger thread naming two prints the first pass
missed:

- 3850074822 / 3850078575: the insertion anchor for each bibliography
  subsection was re-resolved by indexing ``self.doc.paragraphs[insert_idx]``
  after every paragraph inserted -- fragile because that list is rebuilt from
  the document body on every access, and outright wrong if a bibliography
  header is the template's last paragraph (``IndexError``). The fix resolves
  one anchor paragraph per subsection and inserts everything relative to it,
  with an explicit end-of-document fallback.
- 3850107854: the tracked-insertion citation writer hardcoded Arial/11pt
  where the plain writer delegates to ``_set_font()``. Fixed with named
  module constants used by both the raw-XML build and (indirectly, via
  ``_set_font``'s own defaults) the plain path, so a change to one is visible
  at the other.
- 3850159506: ``stats['target_names_bolded']`` was incremented before the
  tracked-insertion XML build was known to have succeeded; a later failure in
  that same build fell back to the plain writer, which increments the same
  counter again -- double-counting one citation. Fixed by moving the
  increment after the insertion actually lands on the paragraph.
- 3850161322: the tracked-insertion writer's exception handler printed a
  ``self.verbose``-gated warning instead of using the project logger.
- follow-up on 3850161322 ("there are 2 more print statements on lines 199
  and 203"): ``_fill_bibliography`` itself still had two ``self.verbose``-gated
  prints -- a "could not find section header" warning and a per-section
  "N entries -> header" progress line. Neither string matches
  ``orchestrator.py``'s ``PROGRESS_PATTERNS`` or
  ``run_corpus_batch.sh``'s four literal greps, so converting them is safe.
  Both now log unconditionally: warning for the not-found case, info for
  the progress line.

#662 items 2-4 name the same three fixes above (IndexError guard, double-
count, font duplication) as still-open review gaps; re-measured against this
file's current HEAD they are already the code above and already covered by
the tests below -- confirmed, not touched, by this PR. Item 4's "silently
diverge" half was not fully closed by the round-2 fix (named constants can
still drift out of sync with ``_set_font`` by hand), so one test is added
below deriving the constants from ``_set_font``'s own defaults instead.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_bibliography_round2.py -p no:cacheprovider
"""

import logging
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
import unified_pipeline.stage6.sections.bibliography as bibliography  # noqa: E402

# The font-derivation test below calls `importlib.reload(bibliography)`.
# Reload re-executes the module in its OWN namespace, so module-level
# constants are corrected in place -- but every `class` statement binds a
# NEW class object, while `WCMTemplateGenerator.__mro__` still holds the
# `BibliographySection` that was mixed into it at first import. Left that
# way, a `patch.object(bibliography.BibliographySection, ...)` or an
# `isinstance(x, bibliography._CitationEnrichment)` in any test collected
# after this file would silently target a class no live object uses.
# Captured here, before any reload can run, and restored after one.
_PRE_RELOAD_CLASSES = {
    name: obj
    for name, obj in vars(bibliography).items()
    if isinstance(obj, type) and obj.__module__ == bibliography.__name__
}


def _generator():
    """A real generator instance, matching the pattern already used for
    #572's renderer-pair convergence tests -- these are wire tests against
    the real WCM template, not a synthetic double."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _citation_entry(citation_body, target_name, year):
    """A minimal stage-5d-shaped bibliography entry: pre-formatted, so
    `_format_citation` passes it through unchanged (`formatting/values.py`)."""
    return {
        "extracted_fields": {
            "formatted_citation": citation_body,
            "formatting_source": "stage_5d_llm",
            "target_name": target_name,
            "year": year,
        },
    }


class _RaisingInsProxy:
    """Wraps a real ``w:ins`` element and raises on its Nth ``.append()``
    call, simulating a late failure inside the tracked-insertion XML build --
    the same failure mode the method's own ``except Exception`` already
    handles (and its docstring names: "falls back to the plain path if the
    XML build raises"). Everything except ``append`` is forwarded untouched.
    """

    def __init__(self, real_element, raise_on_append_call):
        object.__setattr__(self, "_real", real_element)
        object.__setattr__(self, "_raise_on_append_call", raise_on_append_call)
        object.__setattr__(self, "_append_calls", 0)

    def append(self, child):
        calls = self._append_calls + 1
        object.__setattr__(self, "_append_calls", calls)
        if calls == self._raise_on_append_call:
            raise RuntimeError("simulated XML build failure (test)")
        self._real.append(child)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_ins_to_fail_on_third_append(monkeypatch, raise_on_append_call=3):
    """Makes the ``w:ins`` element raise on its Nth append. For a citation
    with a non-empty author-name prefix, bolded name, and suffix, the three
    ``ins.append()`` calls are (in order): the "before" run, the bolded name
    run, the "after" run -- so raising on the 3rd call happens strictly after
    the bolded-name run has already been built and appended, which is the
    exact point the double-count bug (thread 3850159506) lived at.
    """
    real_oxml_element = bibliography.OxmlElement

    def fake_oxml_element(tag):
        elem = real_oxml_element(tag)
        if tag == "w:ins":
            return _RaisingInsProxy(elem, raise_on_append_call)
        return elem

    monkeypatch.setattr(bibliography, "OxmlElement", fake_oxml_element)


# --- 3850074822 / 3850078575: stable anchor, explicit end-of-document case ---


def test_header_as_last_paragraph_appends_at_end_without_indexerror():
    # The reviewer's stated failure case: "If a valid template ends at a
    # bibliography header, this raises IndexError." Build a document where
    # the header truly has no following paragraph.
    gen = _generator()
    doc = Document()
    doc.add_paragraph("Peer-reviewed Research Articles:")
    gen.doc = doc

    entries = {"S1": [_citation_entry("Doe J. A great study. Journal X. 2024;1(1):1-2.", "Doe J", 2024)]}

    gen._fill_bibliography(entries, cv_owner={}, document_uid="")  # must not raise

    texts = [p.text for p in gen.doc.paragraphs]
    assert texts == [
        "Peer-reviewed Research Articles:",
        "",
        "1. Doe J. A great study. Journal X. 2024;1(1):1-2.",
    ]
    assert gen.stats["entries_inserted"] == 1


def test_multiple_citations_land_in_order_and_next_section_header_survives():
    # Realistic valid input against the real template (HARD SAFETY GATE):
    # several S1 entries must still render, in reverse-chronological order,
    # immediately after the header and before whatever the template already
    # had there -- proving the resolve-once anchor didn't misplace or drop
    # anything relative to the original insert_idx re-indexing behaviour.
    gen = _generator()
    entries = {
        "S1": [
            _citation_entry("Alpha A. Oldest study. J1. 2020;1:1-2.", "Alpha A", 2020),
            _citation_entry("Beta B. Middle study. J2. 2022;2:2-3.", "Beta B", 2022),
            _citation_entry("Gamma C. Newest study. J3. 2024;3:3-4.", "Gamma C", 2024),
        ]
    }

    gen._fill_bibliography(entries, cv_owner={}, document_uid="")

    header_idx = gen._find_paragraph_with_text("Peer-reviewed Research Articles:")
    assert header_idx is not None
    following = [p.text for p in gen.doc.paragraphs[header_idx:header_idx + 6]]
    assert following[0] == "Peer-reviewed Research Articles:"
    assert following[1] == ""  # blank separator line
    assert following[2] == "1. Gamma C. Newest study. J3. 2024;3:3-4."
    assert following[3] == "2. Beta B. Middle study. J2. 2022;2:2-3."
    assert following[4] == "3. Alpha A. Oldest study. J1. 2020;1:1-2."

    # The next section header is still findable and still comes after these.
    next_header_idx = gen._find_paragraph_with_text("Reviews and Editorials:")
    assert next_header_idx is not None
    assert next_header_idx > header_idx + 4
    assert gen.stats["entries_inserted"] == 3


# --- 3850107854: no duplicated font policy ---


def test_tracked_and_plain_paths_render_identical_typography():
    gen = _generator()
    doc = Document()
    para_plain = doc.add_paragraph()
    para_tracked = doc.add_paragraph()
    citation = "1. Doe J, Smith A, Lee K. Great study. Journal Name. 2024;10(2):100-110."

    gen._add_citation_with_bold_author(para_plain, citation, "Smith A", "")
    gen._add_citation_with_bold_author_as_insertion(para_tracked, citation, "Smith A", "")

    plain_bold_run = next(r for r in para_plain.runs if r.bold)

    ins_elem = para_tracked._p.find(qn("w:ins"))
    assert ins_elem is not None
    tracked_bold_run = next(
        r for r in ins_elem.findall(qn("w:r"))
        if r.find(qn("w:rPr")) is not None and r.find(qn("w:rPr")).find(qn("w:b")) is not None
    )
    tracked_rpr = tracked_bold_run.find(qn("w:rPr"))
    tracked_font_name = tracked_rpr.find(qn("w:rFonts")).get(qn("w:ascii"))
    tracked_font_half_points = tracked_rpr.find(qn("w:sz")).get(qn("w:val"))

    # The two paths must render the same typography...
    assert tracked_font_name == plain_bold_run.font.name
    assert tracked_font_half_points == str(int(plain_bold_run.font.size.pt * 2))
    # ...and that typography must actually come from the named constants,
    # not a second pair of literals that happens to currently agree.
    assert tracked_font_name == bibliography.TRACKED_INSERTION_FONT_NAME
    assert tracked_font_half_points == bibliography.TRACKED_INSERTION_FONT_SIZE_HALF_POINTS


# --- #662 item 4: the constants above are DERIVED from _set_font, not a
# second pair of literals that happen to agree with it today ---


def test_tracked_insertion_font_constants_track_set_font_default_changes(monkeypatch):
    # The round-2 fix above (named constants) only proves the two paths
    # currently agree -- it says nothing about what happens if _set_font's
    # own default policy changes, which is the actual "silently diverge"
    # risk #662 item 4 names. Mutate _set_font's defaults and reload
    # bibliography.py: if the module constants are read live from
    # inspect.signature(_set_font), as they are now, they follow; if they
    # were hand-copied literals, as on the pre-#662-item-4 code, they would
    # not move and this assertion would fail.
    import importlib

    monkeypatch.setattr(
        bibliography._set_font, "__defaults__", ("Times New Roman", 14, False, False)
    )
    try:
        reloaded = importlib.reload(bibliography)
        assert reloaded.TRACKED_INSERTION_FONT_NAME == "Times New Roman"
        assert reloaded.TRACKED_INSERTION_FONT_SIZE_HALF_POINTS == "28"
    finally:
        # `_set_font.__defaults__` is still the mutated tuple here --
        # pytest's monkeypatch teardown only undoes it after this test
        # function returns, and reload derives the constants from whatever
        # `_set_font.__defaults__` holds *right now*. Reloading first would
        # re-derive from the still-mutated defaults and leave the module
        # holding the wrong constants for every test that runs after this
        # one in the same process. Undo the patch first, then reload so
        # the derivation reads the real defaults.
        monkeypatch.undo()
        importlib.reload(bibliography)
        # Reload rebound every class in the module to a fresh object; put the
        # originals back, so `bibliography.BibliographySection` is once again
        # the class actually sitting in `WCMTemplateGenerator.__mro__` (see
        # `_PRE_RELOAD_CLASSES` at the top of this file). The constants
        # asserted above stay the freshly re-derived ones -- the reloaded
        # code and the restored classes share one module namespace, so the
        # methods read the corrected values either way.
        for _name, _cls in _PRE_RELOAD_CLASSES.items():
            setattr(bibliography, _name, _cls)

    # T2.5 (PR #711 review, thread T2 item 5): this used to be a second,
    # standalone test (`test_reload_left_module_classes_identical_to_the_live_ones`)
    # that only passed because pytest happened to run it immediately after this
    # one in file-definition order -- it depended on the `finally` block above
    # having already run. Folded in here so the restore it checks is verified
    # in the same test that performs the restore, with no cross-test ordering
    # assumption. `_PRE_RELOAD_CLASSES` stays module-level (not moved into this
    # test) because the reload above executes at import time relative to any
    # other test in this file that might also touch `bibliography`.
    assert bibliography.BibliographySection in WCMTemplateGenerator.__mro__
    for name, cls in _PRE_RELOAD_CLASSES.items():
        assert getattr(bibliography, name) is cls, name
    # ...and the constants are the real ones again, not the mutated defaults.
    assert bibliography.TRACKED_INSERTION_FONT_NAME == "Arial"
    assert bibliography.TRACKED_INSERTION_FONT_SIZE_HALF_POINTS == "22"


# --- 3850159506: no double-counted stat on a late tracked-insertion failure ---


def test_target_names_bolded_not_double_counted_on_late_insertion_failure(monkeypatch):
    _patch_ins_to_fail_on_third_append(monkeypatch)

    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    para = doc.add_paragraph()
    citation = "1. Doe J, Smith A, Lee K. Great study. Journal Name. 2024;10(2):100-110."

    gen._add_citation_with_bold_author_as_insertion(para, citation, "Smith A", "", author="PubMed Enrichment")

    # The tracked build failed after the bold run was built but before the
    # insertion landed, so it must not have counted -- only the plain-path
    # fallback's own increment should be reflected.
    assert gen.stats["target_names_bolded"] == 1
    assert gen.stats["track_changes_added"] == 0
    # And the citation still rendered (fallback actually ran).
    assert para.text == citation
    assert [r.text for r in para.runs if r.bold] == ["Smith A"]


# --- 3850161322: project logger instead of a verbose-gated print() ---


def test_insertion_exception_logs_via_project_logger_not_print(monkeypatch, caplog, capsys):
    real_oxml_element = bibliography.OxmlElement

    def always_fail(tag):
        if tag == "w:ins":
            raise RuntimeError("simulated XML build failure (test)")
        return real_oxml_element(tag)

    monkeypatch.setattr(bibliography, "OxmlElement", always_fail)

    gen = WCMTemplateGenerator(verbose=True)  # verbose=True: old code printed
    doc = Document()
    para = doc.add_paragraph()
    citation = "1. Doe J. Great study. Journal Name. 2024;10(2):100-110."

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage6.sections.bibliography"):
        gen._add_citation_with_bold_author_as_insertion(para, citation, "Doe J", "")

    assert any(
        "Could not add citation as insertion" in record.message
        and "simulated XML build failure (test)" in record.message
        for record in caplog.records
    )
    # Nothing went to stdout -- the print() is gone, not just quieter.
    assert capsys.readouterr().out == ""
    # The fallback still rendered the citation.
    assert para.text == citation


# --- 3850155382: typed record at the enrichment boundary ---
#
# The reviewer asked for a typed domain model covering `enrichment_status`,
# `enrichment_source`, and `text`, in place of three separate `pub.get(...)`
# calls in `_fill_bibliography`. `_CitationEnrichment.from_raw` is that
# boundary conversion.


def test_citation_enrichment_from_raw_converts_realistic_dict():
    pub = {
        "extracted_fields": {"formatted_citation": "Doe J. A study. J. 2024;1:1."},
        "enrichment_status": "enriched",
        "enrichment_source": "pubmed",
        "text": "Doe J. Original unenriched text.",
    }

    record = bibliography._CitationEnrichment.from_raw(pub)

    assert record.enrichment_status == "enriched"
    assert record.enrichment_source == "pubmed"
    assert record.text == "Doe J. Original unenriched text."


def test_bibliography_renders_when_enrichment_fields_missing_from_raw_dict():
    # None of the three fields the record covers is present at all -- not
    # even as an explicit empty string. The old `pub.get(key, '')` calls
    # already tolerated a missing key; the typed boundary must too.
    gen = _generator()
    entry = _citation_entry("Doe J. A study without enrichment fields. J. 2024;1:1-2.", "Doe J", 2024)
    assert "enrichment_status" not in entry
    assert "enrichment_source" not in entry
    assert "text" not in entry

    gen._fill_bibliography({"S1": [entry]}, cv_owner={}, document_uid="")  # must not raise

    header_idx = gen._find_paragraph_with_text("Peer-reviewed Research Articles:")
    citation_texts = [p.text for p in gen.doc.paragraphs[header_idx:header_idx + 3]]
    assert "1. Doe J. A study without enrichment fields. J. 2024;1:1-2." in citation_texts
    assert gen.stats["entries_inserted"] == 1


def test_bibliography_renders_when_enrichment_fields_are_non_string_or_none():
    # enrichment_status and text are explicitly None (present, not missing --
    # `dict.get(key, '')` does not default an explicit None), and
    # enrichment_source is a non-string value that would raise
    # AttributeError on the old code's bare `enrichment_source.upper()` call
    # once `enriched_fields` is non-empty.
    gen = _generator()
    entry = _citation_entry("Doe J. A study with odd enrichment fields. J. 2024;1:1-2.", "Doe J", 2024)
    entry["enrichment_status"] = None
    entry["enrichment_source"] = 42
    entry["text"] = None
    entry["enriched_fields"] = ["title"]

    gen._fill_bibliography({"S1": [entry]}, cv_owner={}, document_uid="")  # must not raise

    header_idx = gen._find_paragraph_with_text("Peer-reviewed Research Articles:")
    citation_texts = [p.text for p in gen.doc.paragraphs[header_idx:header_idx + 3]]
    assert "1. Doe J. A study with odd enrichment fields. J. 2024;1:1-2." in citation_texts
    assert gen.stats["entries_inserted"] == 1


# --- review thread: 2 more prints on `_fill_bibliography` itself (#625) ---
#
# `_add_citation_with_bold_author_as_insertion`'s print was fixed above
# (3850161322); the reviewer's follow-up named two more, in
# `_fill_bibliography` -- the "could not find section header" warning and
# the per-section "N entries -> header" progress line. Both were gated on
# `self.verbose`; the conversion (matching aeea7a7's earlier one in this
# same file) drops the gate so the log always fires, at a level chosen by
# content: warning for the not-found case, info for plain progress.
#
# The file's last print -- `_fill_bibliography`'s own verbose-gated
# "Filling Bibliography (N publications)..." line -- is folded into the
# INFO test below rather than given its own test, since it already drives
# `_fill_bibliography` at INFO level.


def test_missing_section_header_logs_warning_via_project_logger_not_print(caplog, capsys):
    gen = _generator()
    doc = Document()  # no bibliography headers at all
    gen.doc = doc

    entries = {"S1": [_citation_entry("Doe J. A study. J. 2024;1:1-2.", "Doe J", 2024)]}

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.stage6.sections.bibliography"):
        gen._fill_bibliography(entries, cv_owner={}, document_uid="")  # must not raise

    assert any(
        "Could not find section header" in record.message
        and "Peer-reviewed Research Articles:" in record.message
        for record in caplog.records
    )
    # Nothing went to stdout -- the print() is gone, not just quieter.
    assert capsys.readouterr().out == ""
    # The header was never found, so nothing was inserted.
    assert gen.stats["entries_inserted"] == 0


def test_section_entry_count_logs_info_via_project_logger_not_print(caplog, capsys):
    gen = _generator()

    entries = {
        "S1": [
            _citation_entry("Alpha A. Study one. J1. 2020;1:1-2.", "Alpha A", 2020),
            _citation_entry("Beta B. Study two. J2. 2022;2:2-3.", "Beta B", 2022),
        ]
    }

    with caplog.at_level(logging.INFO, logger="unified_pipeline.stage6.sections.bibliography"):
        gen._fill_bibliography(entries, cv_owner={}, document_uid="")

    assert any(
        record.levelno == logging.INFO
        and "S1" in record.message
        and "2 entries" in record.message
        and "Peer-reviewed Research Article" in record.message  # header_text[:30]
        for record in caplog.records
    )
    # The verbose-gated "Filling Bibliography (N publications)..." print is
    # gone too -- it now always fires as an INFO log record.
    assert any(
        record.levelno == logging.INFO
        and "Filling Bibliography" in record.message
        and "2 publications" in record.message
        for record in caplog.records
    )
    # Nothing went to stdout -- the print() is gone, not just quieter.
    assert capsys.readouterr().out == ""
    assert gen.stats["entries_inserted"] == 2


# --- PR #711 review, thread T1 item 1: emit_track_changes=False is untested ---


def test_plain_mode_renders_citation_without_ins_and_keeps_author_bold():
    # T1.1: the non-tracked branch at bibliography.py:367-369 falls through to
    # _add_citation_with_bold_author -- proven here directly, on the real
    # WCM template, rather than only inferred from the tracked path's tests.
    gen = WCMTemplateGenerator(verbose=False, emit_track_changes=False)
    gen.doc = Document(gen.template_path)
    para = gen.doc.paragraphs[0].insert_paragraph_before("")
    citation = "1. Doe J, Smith A, Lee K. Great study. Journal Name. 2024;10(2):100-110."

    gen._add_citation_with_bold_author_as_insertion(para, citation, "Smith A", "")

    assert para._p.find(qn("w:ins")) is None
    assert para.text == citation
    for run in para.runs:
        if run.text == "Smith A":
            assert run.bold is True
        else:
            assert run.bold is not True
    assert any(run.text == "Smith A" and run.bold is True for run in para.runs)
    assert gen.stats["track_changes_added"] == 0


# --- T1 item 2: _citation_author_split direct coverage + writer parity ---
#
# test_stage6_renderer_pair_convergence.py:460-486 already covers target-name
# precedence, owner fallback + punctuation strip, no-match, substring-not-word,
# and hyphenated surname. These add the dimensions that file does not: which
# initials shapes the regex actually matches, case-insensitive owner matching,
# owner-fallback-only-when-target-absent, no-owner/no-target -> no match, and
# the "Wende, M" comma form the docstring claims matches but the regex (per
# the lead's finding, common ticket item) cannot -- pinned here, not changed.

_SPLIT_CITATION = "Wende ME, Smith J. A study of things. J Things. 2023;1:1-9."


@pytest.mark.parametrize(
    "citation, target_name, owner, expected",
    [
        # Initials variants the regex does match.
        (
            "Wende ME, Smith J. A study of things. J Things. 2023;1:1-9.",
            None, "wende",
            ("", "Wende ME", ", Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
        (
            "Wende M., Smith J. A study of things. J Things. 2023;1:1-9.",
            None, "wende",
            ("", "Wende M", "., Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
        (
            "Wende M, Smith J. A study of things. J Things. 2023;1:1-9.",
            None, "wende",
            ("", "Wende M", ", Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
        # Case-insensitive owner match: mixed-case citation, upper-case owner.
        (
            "WENDE ME, Smith J. A study of things. J Things. 2023;1:1-9.",
            None, "wende",
            ("", "WENDE ME", ", Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
        # Owner fallback used only because target_name is absent from the
        # citation -- present as an argument, but not a substring of it.
        (
            _SPLIT_CITATION, "Nobody Q", "wende",
            ("", "Wende ME", ", Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
        # Empty owner + no target -> no match at all.
        (
            "No matching author here at all.", None, "",
            ("No matching author here at all.", "", ""),
        ),
        # The "Wende, M" comma form: trailing initials join the surname only
        # across whitespace (`_trailing_initials_end`), not a comma, so only
        # the bare surname "Wende" is bolded.
        (
            "Wende, M, Smith J. A study of things. J Things. 2023;1:1-9.",
            None, "wende",
            ("", "Wende", ", M, Smith J. A study of things. J Things. 2023;1:1-9."),
        ),
    ],
)
def test_citation_author_split_additional_dimensions(citation, target_name, owner, expected):
    assert bibliography._citation_author_split(citation, target_name, owner) == expected


# --- #662 item 6: one whole-token, case-insensitive, Unicode-aware matcher ---
#
# Names are invented. Each row is (citation, target_name, owner, bolded text);
# `bolded` is '' when nothing should match. The rest of the citation must come
# back unchanged around the bolded slice, which the test asserts too.
_TAIL = ". A study of things. J Things. 2023;1:1-9."


@pytest.mark.parametrize(
    "citation, target_name, owner, bolded",
    [
        # Target path is case-insensitive and yields the citation's own text.
        ("SMITH J, Doe A" + _TAIL, "Smith J", "Nobody", "SMITH J"),
        # A target that ends inside a longer initials token falls through to
        # the owner path, which takes the whole initials run.
        ("Smith JA, Doe B" + _TAIL, "Smith J", "Smith", "Smith JA"),
        # A surname is never matched inside a longer or hyphenated one.
        ("Wuertz K, Doe A" + _TAIL, None, "Wu", ""),
        ("Alvarez-Diaz M, Doe A" + _TAIL, None, "Diaz", ""),
        ("Doe A, Diaz-Alvarez M" + _TAIL, None, "Diaz", ""),
        ("O'Neil P, Doe A" + _TAIL, None, "Neil", ""),
        ("Doe A, Neil P" + _TAIL, None, "Neil", "Neil P"),
        # Hyphen and space are the same separator; apostrophes match across
        # straight and typographic forms.
        ("Alvarez Diaz M, Doe A" + _TAIL, None, "Alvarez-Diaz", "Alvarez Diaz M"),
        ("O\u2019Neil P, Doe A" + _TAIL, None, "O'Neil", "O\u2019Neil P"),
        ("Alvarez\u2010Diaz M, Doe A" + _TAIL, None, "Alvarez-Diaz", "Alvarez\u2010Diaz M"),
        # Only stand-alone capital initials (at most three) join the surname.
        ("Wende and colleagues" + _TAIL, None, "Wende", "Wende"),
        ("Wende Michael, Doe A" + _TAIL, None, "Wende", "Wende"),
        ("Wende MEJ, Doe A" + _TAIL, None, "Wende", "Wende MEJ"),
        ("Wende MEJK, Doe A" + _TAIL, None, "Wende", "Wende"),
        ("Wende" + _TAIL, None, "Wende", "Wende"),
        # Particles that open the author widen the bold run leftward.
        ("Doe A, de la Cruz M" + _TAIL, None, "Cruz", "de la Cruz M"),
        ("Van Der Berg K, Doe A" + _TAIL, None, "Berg", "Van Der Berg K"),
        # A particle-like word that is not at an author boundary is left alone.
        ("Doe A, Kim Al Cruz M" + _TAIL, None, "Cruz", "Cruz M"),
        # Non-ASCII: case-insensitive, and composed/decomposed accents agree.
        ("Doe A, Mu\u00f1oz \u00c1" + _TAIL, None, "Mu\u00f1oz", "Mu\u00f1oz \u00c1"),
        ("Doe A, MU\u00d1OZ A" + _TAIL, None, "Mu\u00f1oz", "MU\u00d1OZ A"),
        ("Doe A, Mun\u0303oz A" + _TAIL, None, "Mu\u00f1oz", "Mun\u0303oz A"),
        ("Doe A, Mu\u00f1oz A" + _TAIL, None, "Mun\u0303oz", "Mu\u00f1oz A"),
        ("Doe A, Mu\u00f1ozz A" + _TAIL, None, "Mu\u00f1oz", ""),
        ("Doe A, Mun\u0303ozz A" + _TAIL, None, "Mu\u00f1oz", ""),
        # A surname followed by non-name text does not swallow the whitespace.
        ("Doe A, Wende (with others)" + _TAIL, None, "Wende", "Wende"),
        # A target whose initials differ from the citation's still finds the
        # author through its surname, whichever side carries more initials.
        ("Quill JD, Doe A" + _TAIL, "Quill J", "Nobody", "Quill JD"),
        ("Pell-Rowan F, Doe A" + _TAIL, "Pell-Rowan FM", "Pell", "Pell-Rowan F"),
        ("Doe A, Pell-Rowan FM" + _TAIL, "Pell-Rowan F", "Pell", "Pell-Rowan FM"),
        ("Doe A, Tarn-Ellery K" + _TAIL, "Tarn-Ellery, K.", "Ellery", "Tarn-Ellery K"),
        # An exact target match is taken as written, without extra initials.
        ("Doe A, Wu J" + _TAIL, "Wu", "Nobody", "Wu"),
        # A target that is all initials-shaped keeps its only token.
        ("Doe A, MJ Smith" + _TAIL, "MJ", "Nobody", "MJ"),
        # A possessive still ends the name; a hyphen or other letter does not.
        ("News: Marrow's research" + _TAIL, "Lina Marrow", "Marrow", "Marrow"),
        ("News: Lina Marrow's research" + _TAIL, "Lina Marrow", "Marrow", "Lina Marrow"),
        ("News: Lina Marrow’s research" + _TAIL, "Lina Marrow", "Nobody", "Lina Marrow"),
        ("News: Marrow-Li's research" + _TAIL, None, "Marrow", ""),
        ("News: Marrows research" + _TAIL, None, "Marrow", ""),
        ("News: Marrow'Sun M" + _TAIL, None, "Marrow", ""),
        # Only capital initials (at most three) are trimmed off a target.
        ("Wende ME, Doe A" + _TAIL, "Wende Jr", "Nobody", ""),
        ("Wende ME, Doe A" + _TAIL, "Wende MEJK", "Nobody", ""),
        # The target wins over the owner when both are present.
        ("Wende ME, Smith J" + _TAIL, "Smith J", "Wende", "Smith J"),
        # Blank names never match, and a blank target still falls to the owner.
        ("Doe A" + _TAIL, "  ", "", ""),
        ("Doe A, Wende M" + _TAIL, "  ", "Wende", "Wende M"),
        # A title-shaped target (a colon, or over four words) is skipped for
        # the owner surname, so the title inside the citation is never bolded.
        ("Wende M. Fictional Things Of Note: Essays" + _TAIL,
         "Fictional Things Of Note: Essays", "Wende", "Wende M"),
        ("Wende M. The Long Fictional Book Title" + _TAIL,
         "The Long Fictional Book Title", "Wende", "Wende M"),
        # A four-word name is still a name.
        ("Doe A, de la Cruz M" + _TAIL, "de la Cruz M", "Nobody", "de la Cruz M"),
    ],
)
def test_citation_author_split_whole_token_matching(citation, target_name, owner, bolded):
    before, bold, after = bibliography._citation_author_split(citation, target_name, owner)
    assert bold == bolded
    assert before + bold + after == citation
    if bolded:
        assert citation.index(bolded) == len(before)


def test_plain_and_tracked_writers_bold_the_same_substring_through_the_shared_split():
    # Parity test (T1.2): the same citation through both writers must bold
    # the identical substring -- proving the "one home for the rule" claim
    # in bibliography.py's module docstring, not just each writer separately.
    gen = _generator()
    citation = _SPLIT_CITATION

    para_plain = gen.doc.paragraphs[0].insert_paragraph_before("")
    gen._add_citation_with_bold_author(para_plain, citation, None, "Wende")
    plain_bold_text = "".join(r.text for r in para_plain.runs if r.bold)

    gen2 = WCMTemplateGenerator(verbose=False, emit_track_changes=True)
    gen2.doc = Document(gen2.template_path)
    para_tracked = gen2.doc.paragraphs[0].insert_paragraph_before("")
    gen2._add_citation_with_bold_author_as_insertion(para_tracked, citation, None, "Wende")
    ins_elem = para_tracked._p.find(qn("w:ins"))
    assert ins_elem is not None
    tracked_bold_text = "".join(
        (r.find(qn("w:t")).text or "")
        for r in ins_elem.findall(qn("w:r"))
        if r.find(qn("w:rPr")) is not None and r.find(qn("w:rPr")).find(qn("w:b")) is not None
    )

    assert plain_bold_text == tracked_bold_text == "Wende ME"


# --- T1 item 3: _CitationEnrichment.from_raw non-mapping fallback ---


@pytest.mark.parametrize(
    "raw",
    [None, [], ["x"], "text", 0, 1.5, True, ("a",), {"a"}],
)
def test_citation_enrichment_from_raw_non_mapping_returns_empty_record(raw):
    assert bibliography._CitationEnrichment.from_raw(raw) == bibliography._CitationEnrichment()


def test_citation_enrichment_from_raw_coerces_non_string_field_values():
    # A mapping with all three keys present, but none of them a string --
    # from_raw must route each through _enrichment_field_text rather than
    # assume the mapping already holds strings.
    raw = {"enrichment_status": 1, "enrichment_source": None, "text": ["a"]}
    record = bibliography._CitationEnrichment.from_raw(raw)
    assert record.enrichment_status == "1"
    assert record.enrichment_source == ""
    assert record.text == "['a']"


# --- T1 item 4: _enrichment_field_text coercion contract ---


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, ""),
        ("abc", "abc"),
        ("", ""),
        (7, "7"),
        (2.5, "2.5"),
        ({"a": 1}, "{'a': 1}"),
        ([1, 2], "[1, 2]"),
        (True, "True"),
    ],
)
def test_enrichment_field_text_coercion(value, expected):
    assert bibliography._enrichment_field_text(value) == expected


# --- T1 item 5: all nine S1-S9 header contracts ---

# Copied verbatim from bibliography.py:199-208 as the contract under test --
# a change to either copy without the other is exactly the drift this test
# exists to catch.
_SECTION_HEADERS = {
    'S1': 'Peer-reviewed Research Articles:',
    'S2': 'Reviews and Editorials:',
    'S3': 'Books:',
    'S4': 'Chapters:',
    'S5': 'Non-peer-reviewed Research Publications:',
    'S6': 'Case Reports',
    'S7': 'In review',
    'S8': 'Abstracts',
    'S9': 'Other (media, podcasts, etc.):',
}


@pytest.mark.parametrize("code, header_text", sorted(_SECTION_HEADERS.items()))
def test_each_section_code_inserts_under_its_official_header(code, header_text):
    # All nine headers are present in the real WCM template (checked while
    # writing this test by walking gen.doc.paragraphs for each header_text
    # in _SECTION_HEADERS) -- so every code below takes the "found" branch.
    gen = _generator()
    entry = _citation_entry(f"Author {code}. A study. J. 2024;1:1-2.", f"Author {code}", 2024)

    gen._fill_bibliography({code: [entry]}, cv_owner={}, document_uid="")

    header_idx = gen._find_paragraph_with_text(header_text)
    assert header_idx is not None

    citation_para = gen.doc.paragraphs[header_idx + 2]
    assert citation_para.text.startswith("1. ")
    assert citation_para.text == f"1. Author {code}. A study. J. 2024;1:1-2."


# --- T1 item 6: numbering restarts across multiple subsections ---


def test_numbering_restarts_at_one_in_every_subsection():
    gen = _generator()
    entries = {
        "S1": [
            _citation_entry("A. Study one.", "A", 2020),
            _citation_entry("B. Study two.", "B", 2021),
            _citation_entry("C. Study three.", "C", 2022),
        ],
        "S2": [
            _citation_entry("D. Study four.", "D", 2020),
            _citation_entry("E. Study five.", "E", 2021),
        ],
        "S3": [
            _citation_entry("F. Study six.", "F", 2020),
        ],
    }

    gen._fill_bibliography(entries, cv_owner={}, document_uid="")

    s1_idx = gen._find_paragraph_with_text(_SECTION_HEADERS["S1"])
    s2_idx = gen._find_paragraph_with_text(_SECTION_HEADERS["S2"])
    s3_idx = gen._find_paragraph_with_text(_SECTION_HEADERS["S3"])
    assert None not in (s1_idx, s2_idx, s3_idx)

    # S1: three entries, reverse-chronological -> numbered 1., 2., 3.
    s1_texts = [gen.doc.paragraphs[s1_idx + i].text for i in (2, 3, 4)]
    assert [t.split(".", 1)[0] + "." for t in s1_texts] == ["1.", "2.", "3."]
    assert s1_texts[0].startswith("1. C.")
    assert s1_texts[1].startswith("2. B.")
    assert s1_texts[2].startswith("3. A.")

    # S2: two entries -> restarts at 1., 2. (not 4., 5.)
    s2_texts = [gen.doc.paragraphs[s2_idx + i].text for i in (2, 3)]
    assert [t.split(".", 1)[0] + "." for t in s2_texts] == ["1.", "2."]
    assert s2_texts[0].startswith("1. E.")
    assert s2_texts[1].startswith("2. D.")

    # S3: one entry -> restarts at 1. (not 6.)
    s3_text = gen.doc.paragraphs[s3_idx + 2].text
    assert s3_text.startswith("1. F.")


# --- T1 item 7: fused stage-5d entry integration through _fill_bibliography ---


def test_fused_entry_renders_as_separate_numbered_citations():
    # Shape as in test_stage6_fused_citations.py:33-47: one stage-5d entry
    # whose formatted_citation joins three citations with "\n\n".
    fused = {
        "extracted_fields": {
            "formatted_citation": (
                "Alpha B. First study. Journal One. 2025;1:1-2.\n"
                "\n"
                "Delta E. Second study. Journal Two. 2024;2:3-4.\n"
                "\n"
                "Eta G. Third study. Journal Three. 2023;3:5-6."
            ),
            "formatting_source": "stage_5d_llm",
            "target_name": None,
            "year": 2025,
        },
    }
    gen = _generator()

    gen._fill_bibliography({"S1": [fused]}, cv_owner={}, document_uid="")

    header_idx = gen._find_paragraph_with_text(_SECTION_HEADERS["S1"])
    following = gen.doc.paragraphs[header_idx + 1: header_idx + 5]
    texts = [p.text for p in following]

    assert texts[1] == "1. Alpha B. First study. Journal One. 2025;1:1-2."
    assert texts[2] == "2. Delta E. Second study. Journal Two. 2024;2:3-4."
    assert texts[3] == "3. Eta G. Third study. Journal Three. 2023;3:5-6."
    for p in following:
        assert "\n" not in p.text
        assert p._p.find(qn("w:br")) is None
    assert gen.stats["entries_inserted"] == 3


# --- T1 item 8: enrichment integration renders both w:del and w:ins ---


def test_enrichment_integration_renders_deletion_and_bold_insertion():
    entry = {
        "extracted_fields": {
            "formatted_citation": "Doe J, Smith A. An enriched study. Journal X. 2024;1(1):1-2.",
            "formatting_source": "stage_5d_llm",
            "target_name": "Smith A",
            "year": 2024,
        },
        "enrichment_status": "enriched",
        "text": "Doe J, Smith A. Original unenriched citation.",
        "enrichment_source": "pubmed",
    }
    gen = WCMTemplateGenerator(verbose=False, emit_track_changes=True)
    gen.doc = Document(gen.template_path)

    gen._fill_bibliography({"S1": [entry]}, cv_owner={}, document_uid="")

    header_idx = gen._find_paragraph_with_text(_SECTION_HEADERS["S1"])
    para = gen.doc.paragraphs[header_idx + 2]

    del_elem = para._p.find(qn("w:del"))
    ins_elem = para._p.find(qn("w:ins"))
    assert del_elem is not None
    assert ins_elem is not None

    del_text = "".join(t.text or "" for t in del_elem.findall(".//" + qn("w:delText")))
    assert del_text == "Doe J, Smith A. Original unenriched citation."

    ins_text = "".join(t.text or "" for t in ins_elem.findall(".//" + qn("w:t")))
    assert ins_text == "1. Doe J, Smith A. An enriched study. Journal X. 2024;1(1):1-2."

    bold_run = next(
        r for r in ins_elem.findall(qn("w:r"))
        if r.find(qn("w:rPr")) is not None and r.find(qn("w:rPr")).find(qn("w:b")) is not None
    )
    assert bold_run.find(qn("w:t")).text == "Smith A"


def test_citation_author_split_target_matches_its_own_token_not_a_longer_initials_run():
    # Two authors share a surname; the target "Harlan J" is the last one, not
    # the head of "Harlan JG" (a substring match bolded the wrong author).
    citation = "Doe A, Harlan JG, Roe B, Harlan J" + _TAIL
    before, bold, after = bibliography._citation_author_split(citation, "Harlan J", "Harlan")
    assert (before, bold, after) == ("Doe A, Harlan JG, Roe B, ", "Harlan J", _TAIL)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
