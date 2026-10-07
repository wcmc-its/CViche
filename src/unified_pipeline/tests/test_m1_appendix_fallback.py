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

from unified_pipeline.stage6.normalization import _squash  # noqa: E402
from unified_pipeline.stage6.render_check import (  # noqa: E402
    _RENDER_TOKEN_RE,
    _norm,
    m1_record_rendered,
)
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


# AUTOPSY-EBYSBC-batch-2026-10-02 class E27: a dated M1 record (a position, a
# project) handed to a generated summary that never reproduces it rendered
# nowhere. "Quorvex", "Zentrix", "Plimsoll" and "Vantorel" are invented words
# that occur nowhere else in the fixtures or the bundled template.
RECORD_TOKEN = "Quorvex"
SHORT_RECORD_TOKEN = "Zentrix"
PROSE_FIELDS_TOKEN = "Plimsoll"
EMPTY_FIELDS_TOKEN = "Vantorel"


def _dated_record() -> dict:
    return {"text": f"2004-2007: Research Associate, {RECORD_TOKEN} Widget Institute, "
                    "Springfield; studied gear lubrication kinetics",
            "taxonomy_code": "M1",
            "extracted_fields": {"position": "Research Associate",
                                 "institution": f"{RECORD_TOKEN} Widget Institute",
                                 "start_date": "2004", "end_date": "2007", "narrative": None},
            "element_idx_start": 12}


def _record_entries(*records: dict) -> list[dict]:
    """The owner's own prose M1 statement plus *records*."""
    return _recoded_and_own_m1_entries()[:2] + list(records)


def _render_with_sidecar(tmp_path, entries, research_summary=None) -> tuple[str, list[dict]]:
    """The output text and the sidecar's M1 Appendix-diversion warnings."""
    text = _render(tmp_path, entries, research_summary=research_summary)
    sidecar = json.loads((tmp_path / "TESTAA_render_warnings.json").read_text())
    return text, [w for w in sidecar["warnings"]
                  if w.get("check") == "appendix_diversion" and w.get("code") == "M1"]


def _prose_m1_entries() -> list[dict]:
    """Two M1 entries that are prose, not records: only the prose fields are
    populated, or a record field is present but empty."""
    return [
        {"text": f"Our {PROSE_FIELDS_TOKEN} group asks how widget surfaces wear under repeated load",
         "taxonomy_code": "M1", "element_idx_start": 16,
         "extracted_fields": {"narrative": "how widget surfaces wear", "description": "surface wear",
                              "research_area": "tribology"}},
        {"text": f"A {EMPTY_FIELDS_TOKEN} theme across the program concerns lubricant chemistry broadly",
         "taxonomy_code": "M1", "element_idx_start": 18,
         "extracted_fields": {"narrative": "lubricant chemistry", "start_date": None, "title": ""}},
    ]


def test_dated_m1_record_a_generated_summary_leaves_out_reaches_the_appendix(tmp_path):
    """E27: the record reaches the Appendix with its own warning reason; the
    owner's prose statements still feed the summary only."""
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on widget dynamics and their control.")
    entries = _record_entries(_dated_record(), *_prose_m1_entries())
    text, diversions = _render_with_sidecar(tmp_path, entries, summary)
    assert SUMMARY_TOKEN in text
    assert text.count(RECORD_TOKEN) == 1, "the dated M1 record rendered nowhere, or twice"
    for token in (OWN_M1_TOKEN, PROSE_FIELDS_TOKEN, EMPTY_FIELDS_TOKEN):
        assert token not in text, f"a prose M1 entry ({token}) leaked to the Appendix"
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("M1", "m1_record_not_in_summary", 1)]


def test_dated_m1_record_the_summary_reproduces_is_not_repeated(tmp_path):
    """The summary paragraph is a tracked insertion; it still counts as the
    page the reader sees, so a record it carries stays out of the Appendix."""
    summary = _summary("llm_generated",
                       f"From 2004 to 2007 Dr. Public was a Research Associate at the {RECORD_TOKEN} "
                       "Widget Institute in Springfield, studying gear lubrication kinetics.")
    text = _render(tmp_path, _record_entries(_dated_record()), research_summary=summary)
    assert text.count(RECORD_TOKEN) == 1


def test_dated_m1_record_too_short_to_verify_stays_out(tmp_path):
    """A record with too few distinctive words cannot be proven absent, so it
    is not added: the Appendix only takes a verified miss."""
    short = {"text": f"2019-2020: {SHORT_RECORD_TOKEN} lab", "taxonomy_code": "M1",
             "extracted_fields": {"start_date": "2019", "end_date": "2020"}, "element_idx_start": 14}
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on widget dynamics and their control.")
    text = _render(tmp_path, _record_entries(short), research_summary=summary)
    assert SHORT_RECORD_TOKEN not in text


def test_dated_m1_record_is_not_repeated_when_the_summary_is_the_m1_text(tmp_path):
    summary = _summary("existing_content",
                       f"The {SUMMARY_TOKEN} summary is the CV's own M1 text, joined verbatim here.")
    text = _render(tmp_path, _record_entries(_dated_record()), research_summary=summary)
    assert RECORD_TOKEN not in text


def test_dated_m1_record_is_written_once_with_the_old_reason_when_no_summary_renders(tmp_path):
    """No summary: every M1 entry reaches the Appendix through the unmapped-code
    path (#317) and is reported as such, not as a record the summary left out."""
    text, diversions = _render_with_sidecar(tmp_path, _record_entries(_dated_record()))
    assert text.count(RECORD_TOKEN) == 1
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("M1", "renderer_declined", 2)]


def test_t_validation_recoded_m1_with_only_prose_fields_still_reaches_the_appendix(tmp_path):
    """#1328 routes every recode, whether or not stage 4 found a record field
    in it. A recode carrying only `narrative` is not an E27 record, so the
    recode test alone must bring it to the Appendix, with its own reason."""
    entries = _recoded_and_own_m1_entries()
    entries[2]["extracted_fields"] = {"narrative": "website for the widget lab"}
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on widget dynamics and their control.")
    text, diversions = _render_with_sidecar(tmp_path, entries, summary)
    assert text.count(RECODED_TOKEN) == 1, "a prose-only T-validation recode rendered nowhere"
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("M1", "t_validation_recoded", 1)]


# Invented token for the instruction-box case below.
BOX_RECORD_TOKEN = "Mardella"


def test_dated_m1_record_is_not_vouched_for_by_the_template_instruction_box(tmp_path):
    """The template's instruction box is still in the document when the M1
    check runs and is deleted before save. A record whose second line shares
    its words with the box's "'Local' refers to the home institution;
    'regional' refers to city, state and nearby states" prompt must not count
    as rendered on the strength of that box text, or it is lost with the box."""
    record = {"text": f"2011-2015: Outreach Liaison, {BOX_RECORD_TOKEN} Widget Office\n"
                      "Linked the home institution with local, regional and nearby states",
              "taxonomy_code": "M1",
              "extracted_fields": {"position": "Outreach Liaison", "start_date": "2011",
                                   "end_date": "2015"},
              "element_idx_start": 20}
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on widget dynamics and their control.")
    text = _render(tmp_path, _record_entries(record), research_summary=summary)
    assert "delete this instruction box" not in text.lower()
    assert text.count(BOX_RECORD_TOKEN) == 1, "the instruction box vouched for an M1 record"


# #1429 (AUTOPSY-NDMRSO-batch-2026-10-04): one fragment of a dated M1 record
# vouched for the whole record -- its "<years> Department of <dept>, <school>"
# fragment matched an appointment row, or its project title matched a summary
# paragraph that only shares its topic words. "Gearwick", "Tarnholm" and
# "Pellucid" are invented words.
PROJECT_TOKEN = "Pellucid"


def _project_record() -> dict:
    return {"text": "2015 to 2017 Department of Gearwork, Tarnholm Widget University\t"
                    "Principal Investigator: Robin Gearwick\t"
                    f"Description: {PROJECT_TOKEN} lubricant fatigue modelling in planetary gearboxes",
            "taxonomy_code": "M1",
            "extracted_fields": {"start_date": "2015", "end_date": "2017",
                                 "department": "Department of Gearwork",
                                 "narrative": "lubricant fatigue modelling in planetary gearboxes"},
            "element_idx_start": 22}


def _letter_token_sets(*lines: str) -> list[set]:
    return [set(_RENDER_TOKEN_RE.findall(_norm(line))) for line in lines]


def _haystack(*lines: str) -> str:
    return "\x00".join(_squash(line) for line in lines)


def test_m1_record_is_not_vouched_for_by_its_date_and_department_fragment():
    """The record's leading fragment matches an appointment row word for word;
    the record as a whole is not on the page."""
    text = _project_record()["text"]
    appointment = "2015-2017 Assistant Professor, Department of Gearwork, Tarnholm Widget University"
    assert m1_record_rendered(text, _haystack(appointment), _letter_token_sets(appointment)) is False


def test_m1_record_is_not_vouched_for_by_its_title_inside_a_summary_paragraph():
    summary = ("Dr. Public's program studies lubricant fatigue modelling in planetary gearboxes, "
               "with ongoing work on bearing wear, surface coatings and industrial reliability.")
    text = _project_record()["text"]
    assert m1_record_rendered(text, _haystack(summary), _letter_token_sets(summary)) is False


def test_m1_record_rendered_whole_on_one_line_counts_as_rendered():
    """A row that carries the record's own words, reformatted, still vouches."""
    row = ("Principal Investigator Robin Gearwick | Department of Gearwork, Tarnholm Widget University | "
           f"{PROJECT_TOKEN} lubricant fatigue modelling in planetary gearboxes | 2015-2017")
    text = _project_record()["text"]
    assert m1_record_rendered(text, _haystack(row), _letter_token_sets(row)) is True


def test_m1_record_rendered_verbatim_counts_as_rendered():
    text = "2015 to 2017 Tarnholm gear study"
    assert m1_record_rendered(text, _haystack("x", text), _letter_token_sets("x")) is True


def test_m1_record_with_too_few_tokens_is_not_verifiable():
    assert m1_record_rendered("2019-2020: Tarnholm lab", _haystack("x"), _letter_token_sets("x")) is None


def test_dated_m1_record_whose_title_a_summary_shares_reaches_the_appendix(tmp_path):
    """End to end: under #1360 the record's title fragment matched the summary
    paragraph at the overlap share and the record rendered nowhere."""
    summary = _summary("llm_generated",
                       f"Dr. Public leads a {SUMMARY_TOKEN} program on lubricant fatigue modelling in "
                       "planetary gearboxes and widget dynamics.")
    text, diversions = _render_with_sidecar(tmp_path, _record_entries(_project_record()), summary)
    assert text.count(PROJECT_TOKEN) == 1, "the M1 project record rendered nowhere"
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("M1", "m1_record_not_in_summary", 1)]
