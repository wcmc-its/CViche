"""Regression guards for #424: table column-header rows leaking into the
Appendix (Section T) as spurious numbered lines.

Root cause: `data[0]` of every source table is emitted by the reader with the
same shape as a real data row, so a header row ("Title | Institution/Location
| Dates", "NAME:") can be classified T like real unmapped content and reach
`AppendixSection._fill_appendix`. That method's filter chain (blank /
template-instruction / source-boilerplate) had no header-row check, so the raw
row rendered as "1. Title — Institution/Location — Dates", pushing the
numbering of genuine entries that followed it.

The fix adds `_is_column_header_row` (already used by the #221 unrendered-
record recovery pass, `stage_6_word_template.py:2309`) to the filter chain,
now `_appendix_drop_reason`, which names the check that fired; the drop is
reported on the module logger and in the appendix's summary comment as
"column-header", not folded into an undifferentiated "boilerplate/empty"
count (review on #736).

Self-contained: no DB, no network, no PII. Uses the bundled WCM template and
python-docx, with the LLM-driven appendix-reconsider pass neutralized (as
test_m1_appendix_fallback.py does) so the render is deterministic and
credential-free. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_appendix_header_row_filter.py -p no:cacheprovider
"""

import json
import logging
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.render_check import _is_column_header_row  # noqa: E402
from unified_pipeline.stage6.sections import appendix as appendix_module  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

REAL_SENTENCE_TOKEN = "DISTINCTIVE_REAL_SENTENCE_TOKEN"


def _output_text(docx_path) -> str:
    doc = Document(str(docx_path))
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _appendix_log(caplog) -> str:
    """Only the appendix module's own log lines."""
    return "\n".join(r.getMessage() for r in caplog.records
                     if r.name == appendix_module.logger.name)


def _render(tmp_path, entries, caplog):
    # recover_unrendered_records=False: isolates _fill_appendix's own filter
    # chain from the separate #221 post-render recovery pass, which can pull
    # unconsumed personal-data entries (our 'A' name entry, used only to give
    # the doc a non-appendix section) into a *second*, independently-created
    # "T. APPENDIX" section via _add_remaining_to_appendix.
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the filter
    # chain under test and keeps the render deterministic + credential-free.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TEST424", "entries": entries}
    ip = tmp_path / "in.json"
    op = tmp_path / "out.docx"
    ip.write_text(json.dumps(data))
    caplog.set_level(logging.INFO, logger=appendix_module.logger.name)
    gen.generate(str(ip), str(op), research_summary_path=None)
    return _output_text(op), _appendix_log(caplog)


# ---------------------------------------------------------------- unit-level

def test_is_column_header_row_flags_the_424_shapes():
    """Ground truth for the gate expectation named in the ticket: both
    2071_Zuschlag_Cv's degree-table header and 2054_Opresko_Cv's bare 'NAME:'
    row are majority column-label vocabulary."""
    assert _is_column_header_row("Year: Degree | Discipline | Institution/Location")
    assert _is_column_header_row("NAME:")


def test_is_column_header_row_does_not_flag_real_content():
    """Negative control at the unit level: an ordinary CV sentence is not
    majority column-label vocabulary."""
    assert not _is_column_header_row(
        "Reviewed manuscripts for the Journal of Clinical Oncology on an ad "
        "hoc basis."
    )


# ------------------------------------------------------------ render-level

def test_degree_table_header_row_dropped_from_appendix(tmp_path, caplog):
    """Positive control 1 (2071_Zuschlag_Cv shape): a T-coded degree-table
    header row produces no appendix paragraph."""
    entries = [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": "Year: Degree | Discipline | Institution/Location",
         "taxonomy_code": "T", "extracted_fields": {},
         "hierarchy": ["Education"], "element_idx_start": 1},
    ]
    text, printed = _render(tmp_path, entries, caplog)
    # The raw pipe-joined form never reaches the document -- _clean_inline_tabs
    # collapses " | " to " — " before anything is written, so a literal-pipe
    # assertion here would be vacuous. Assert the actual rendered em-dash form
    # is absent, and that no trace of the header row's words survives at all.
    assert "1. Year: Degree — Discipline — Institution/Location" not in text
    assert "Year: Degree" not in text
    assert "T. APPENDIX" not in text, "no other unmapped entries -- appendix should be empty"
    assert "1 non-content block removed (column-header 1)" in printed


def test_bare_name_header_row_dropped_from_appendix(tmp_path, caplog):
    """Positive control 2 (2054_Opresko_Cv shape): a bare 'NAME:' row that
    would otherwise render as the appendix's '1. NAME:' line is dropped."""
    entries = [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": "NAME:", "taxonomy_code": "T", "extracted_fields": {},
         "hierarchy": ["Committee Membership"], "element_idx_start": 1},
    ]
    text, printed = _render(tmp_path, entries, caplog)
    assert "1. NAME:" not in text
    assert "T. APPENDIX" not in text, "no other unmapped entries -- appendix should be empty"
    assert "1 non-content block removed (column-header 1)" in printed


def test_real_sentence_entry_still_renders(tmp_path, caplog):
    """Negative control: a T-coded entry that is genuine (non-header) prose
    still reaches the Appendix -- the new filter must not be overbroad."""
    entries = [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": f"{REAL_SENTENCE_TOKEN}: reviewed manuscripts for the "
                  "Journal of Clinical Oncology on an ad hoc basis.",
         "taxonomy_code": "T", "extracted_fields": {},
         "hierarchy": ["Service"], "element_idx_start": 1},
    ]
    text, printed = _render(tmp_path, entries, caplog)
    assert REAL_SENTENCE_TOKEN in text
    assert "T. APPENDIX" in text
    assert "removed" not in printed
