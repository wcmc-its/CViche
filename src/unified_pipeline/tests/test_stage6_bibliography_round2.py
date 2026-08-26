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

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_bibliography_round2.py -p no:cacheprovider
"""

import logging
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402
import unified_pipeline.stage6.sections.bibliography as bibliography  # noqa: E402


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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
