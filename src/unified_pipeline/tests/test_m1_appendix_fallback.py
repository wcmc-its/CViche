"""Regression test for issue #317: M1 (Research Activities) entries silently
dropped when the Stage 4.5 research summary is absent.

`_fill_research_summary` renders only the Stage 4.5 synthesized summary; the raw
M1 entries are consumed upstream by Stage 4.5. When Stage 4.5 produced nothing,
the summary did not render AND M1 was in `mapped_codes` (excluded from the
appendix), so M1 content vanished (C0ZGFW). The fix makes `_fill_research_summary`
report whether it rendered, and routes M1 entries to the appendix when it did not.

Self-contained: no DB, no network, no PII. Uses the bundled WCM template and
python-docx. The LLM-driven appendix-reconsider pass is neutralized so the test
is deterministic and needs no credentials. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_m1_appendix_fallback.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

M1_TOKEN = "DISTINCTIVE_M1_TOKEN"


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _output_text(docx_path) -> str:
    """Every paragraph's w:t text, tracked insertions included -- python-docx's
    `paragraph.text` skips runs inside w:ins, which is how stage 6 writes the
    research summary and Appendix lines."""
    body = Document(str(docx_path)).element.body
    return "\n".join("".join(t.text or "" for t in p.iter(f"{_W_NS}t"))
                     for p in body.iter(f"{_W_NS}p"))


def _render(tmp_path, entries, research_summary=None) -> str:
    gen = WCMTemplateGenerator(verbose=False)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the routing
    # under test and keeps the render deterministic + credential-free.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TESTAA", "entries": entries}
    ip = tmp_path / "in.json"
    op = tmp_path / "out.docx"
    ip.write_text(json.dumps(data))
    summary_path = None
    if research_summary is not None:
        summary_path = tmp_path / "summary.json"
        summary_path.write_text(json.dumps({"research_summary": research_summary}))
    gen.generate(str(ip), str(op), research_summary_path=str(summary_path) if summary_path else None)
    return _output_text(op)


def test_m1_entry_rendered_when_no_research_summary(tmp_path):
    """The bug: with no Stage 4.5 summary, the M1 entry rendered nowhere. After
    the fix it falls through to the appendix and its content survives."""
    entries = [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": f"The {M1_TOKEN} research program on widget dynamics ran 2010 to 2014",
         "taxonomy_code": "M1", "extracted_fields": {"narrative": "ran 2010 to 2014"},
         "element_idx_start": 5},
    ]
    text = _render(tmp_path, entries)
    assert M1_TOKEN in text, "M1 entry was dropped when no research summary rendered"


def test_fill_research_summary_reports_whether_it_rendered():
    """The signal the fix depends on: False when nothing renders."""
    gen = WCMTemplateGenerator(verbose=False)
    assert gen._fill_research_summary(None) is False
    assert gen._fill_research_summary({"research_summary": {"text": ""}}) is False
    assert gen._fill_research_summary({"research_summary": {"text": "too short"}}) is False


RECODED_TOKEN = "DISTINCTIVE_RECODED_TOKEN"
OWN_M1_TOKEN = "DISTINCTIVE_OWN_M1_TOKEN"
SUMMARY_TOKEN = "DISTINCTIVE_SUMMARY_TOKEN"


def _summary(method: str, text: str) -> dict:
    return {"text": text, "generation_method": method, "word_count": len(text.split())}


def _recoded_and_own_m1_entries() -> list[dict]:
    """An owner's own M1 research statement, plus a website line stage 3b's
    T-validation recoded from T to M1 (AUTOPSY-s7ab-batch-2026-10-02 class 11)."""
    return [
        {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
         "extracted_fields": {}, "element_idx_start": 0},
        {"text": f"My {OWN_M1_TOKEN} research program studies widget dynamics in depth",
         "taxonomy_code": "M1", "extracted_fields": {"narrative": "widget dynamics"},
         "element_idx_start": 5},
        {"text": f"https://lab.example.org/ - {RECODED_TOKEN} website for the widget lab",
         "taxonomy_code": "M1", "t_validation_applied": True,
         "classification_reasoning": "[T-validation reclassified from T] lab site",
         "extracted_fields": {"narrative": "website for the widget lab", "url": "https://lab.example.org/"},
         "element_idx_start": 9},
    ]


def test_t_validation_recoded_m1_reaches_the_appendix_when_a_generated_summary_renders(tmp_path):
    """Class 11: the generated summary paraphrases M1, so the recoded entry was
    lost. It now reaches the Appendix; the owner's own M1 statement still feeds
    the summary only and is not repeated there."""
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on widget dynamics and their control.")
    text = _render(tmp_path, _recoded_and_own_m1_entries(), research_summary=summary)
    assert SUMMARY_TOKEN in text, "the research summary did not render"
    assert RECODED_TOKEN in text, "T-validation-recoded M1 entry rendered nowhere"
    assert OWN_M1_TOKEN not in text, "the owner's own M1 entry leaked to the Appendix"


def test_t_validation_recoded_m1_is_not_repeated_when_the_summary_is_the_m1_text(tmp_path):
    """A verbatim summary (generation_method existing_content) already carries
    every M1 entry's text, so the Appendix must not repeat the recoded one."""
    summary = _summary("existing_content",
                       f"The {SUMMARY_TOKEN} summary is the CV's own M1 text, joined verbatim here.")
    text = _render(tmp_path, _recoded_and_own_m1_entries(), research_summary=summary)
    assert SUMMARY_TOKEN in text
    assert RECODED_TOKEN not in text


def test_t_validation_recoded_m1_is_written_once_when_no_summary_renders(tmp_path):
    """With no summary every M1 entry already reaches the Appendix through the
    unmapped-code path (#317); the recoded one must not be added a second time."""
    text = _render(tmp_path, _recoded_and_own_m1_entries())
    assert text.count(RECODED_TOKEN) == 1
    assert text.count(OWN_M1_TOKEN) == 1
