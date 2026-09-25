"""Regression guards for #424 and for the appendix's own contract (review on
#736): table column-header rows leaking into the Appendix (Section T) as
spurious numbered lines, plus the filter reasons, grouping, numbering and
truncation the section promises.

Root cause of #424: `data[0]` of every source table is emitted by the reader
with the same shape as a real data row, so a header row ("Title |
Institution/Location | Dates", "NAME:") can be classified T like real unmapped
content and reach `AppendixSection._fill_appendix`. That method's filter chain
(blank / template-instruction / source-boilerplate) had no header-row check, so
the raw row rendered as "1. Title — Institution/Location — Dates", pushing the
numbering of genuine entries that followed it.

The fix adds `_is_column_header_row` (already used by the #221 unrendered-
record recovery pass, `stage_6_word_template.py:2309`) to the filter chain.
Since the #736 review the chain is `_appendix_drop_reason`, a pure function
that names the check that fired, so the unit-level tests below drive the rules
without a Word document and the render-level tests prove the page.

Self-contained: no DB, no network, no PII. Uses the bundled WCM template and
python-docx, with the LLM-driven appendix-reconsider pass neutralized (as
test_m1_appendix_fallback.py does) so the render is deterministic and
credential-free. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_appendix_header_row_filter.py -p no:cacheprovider
"""

import json
import logging
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from docx import Document
from lxml import etree

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.docx_structure_extractor import extract_unified_elements  # noqa: E402
from unified_pipeline.stage6.normalization import _clean_inline_tabs  # noqa: E402
from unified_pipeline.stage6.render_check import _is_column_header_row  # noqa: E402
from unified_pipeline.stage6.sections import appendix as appendix_module  # noqa: E402
from unified_pipeline.stage6.sections.appendix import (  # noqa: E402
    APPENDIX_MAX_CHARS,
    DROP_BLANK,
    DROP_COLUMN_HEADER,
    DROP_NEAR_TEMPLATE_INSTRUCTION,
    DROP_RENDERS_EMPTY,
    DROP_SOURCE_BOILERPLATE,
    DROP_TEMPLATE_INSTRUCTION,
    DROP_UNANSWERED_PROMPT,
    _appendix_drop_reason,
    _describe_dropped,
    _filter_unmapped_entries,
    _group_by_source_heading,
    _truncate_appendix_text,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

REAL_SENTENCE_TOKEN = "DISTINCTIVE_REAL_SENTENCE_TOKEN"
GENUINE_ONE = "GENUINE_ONE reviewed manuscripts for the Journal of Clinical Oncology"
GENUINE_TWO = "GENUINE_TWO volunteer physician at the community clinic"
HEADER_ROW = "Title | Institution/Location | Dates"
# The reader emits a real table row's cells joined by " | " and stage 6 renders
# them joined by " — " (_clean_inline_tabs), so the em-dash form is what a
# leaked header would look like on the page.
HEADER_ROW_RENDERED = "Title — Institution/Location — Dates"

_NAME_ENTRY = {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
               "extracted_fields": {}, "element_idx_start": 0}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _t_entry(text: str, hierarchy: list[str], idx: int) -> dict[str, object]:
    return {"text": text, "taxonomy_code": "T", "extracted_fields": {},
            "hierarchy": hierarchy, "element_idx_start": idx}


def _output_text(docx_path: Path) -> str:
    doc = Document(str(docx_path))
    parts = [p.text for p in doc.paragraphs]
    parts += [c.text for tb in doc.tables for row in tb.rows for c in row.cells]
    return "\n".join(parts)


def _appendix_paragraphs(docx_path: Path) -> list[str]:
    """Body paragraph texts from the first `From "...":` label to the end --
    the appendix is the last thing `generate()` writes."""
    paragraphs = [p.text for p in Document(str(docx_path)).paragraphs]
    starts = [i for i, t in enumerate(paragraphs) if t.startswith('From "')]
    return paragraphs[starts[0]:] if starts else []


def _comment_texts(docx_path: Path) -> list[str]:
    """Every Word comment's text, read from word/comments.xml directly -- the
    generator writes that part itself, so python-docx's own comments API is
    not the thing under test."""
    with zipfile.ZipFile(str(docx_path)) as z:
        if "word/comments.xml" not in z.namelist():
            return []
        root = etree.fromstring(z.read("word/comments.xml"))
    return ["".join(t.text or "" for t in c.iter(f"{_W}t"))
            for c in root.iter(f"{_W}comment")]


def _appendix_log(caplog: pytest.LogCaptureFixture) -> str:
    """Only the appendix module's own log lines, so a record from any other
    logger can neither satisfy nor spoil an assertion."""
    return "\n".join(r.getMessage() for r in caplog.records
                     if r.name == appendix_module.logger.name)


def _render(tmp_path: Path, entries: list[dict[str, object]],
            caplog: pytest.LogCaptureFixture, emit_comments: bool = False) -> Path:
    # recover_unrendered_records=False: isolates _fill_appendix's own filter
    # chain from the separate #221 post-render recovery pass, which can pull
    # unconsumed personal-data entries (our 'A' name entry, used only to give
    # the doc a non-appendix section) into a *second*, independently-created
    # "T. APPENDIX" section via _add_remaining_to_appendix.
    # emit_comments is False by default on the generator (#153); the tests
    # that assert the summary Word comment opt in.
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False,
                               emit_comments=emit_comments)
    # Neutralize the LLM-driven appendix-reconsider pass: isolates the filter
    # chain under test and keeps the render deterministic + credential-free.
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "TEST424", "entries": entries}
    ip = tmp_path / "in.json"
    op = tmp_path / "out.docx"
    ip.write_text(json.dumps(data))
    caplog.set_level(logging.INFO, logger=appendix_module.logger.name)
    gen.generate(str(ip), str(op), research_summary_path=None)
    return op


# ------------------------------------------- unit-level: the header heuristic

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


@pytest.mark.parametrize("row", [
    "Title | Institution/Location | Dates",
    "State/Country | License Number | Status | Date of Issue",
    "Name of Committee | Role | Dates",
    "Year | Degree | Discipline | Institution/Location",
    "Year\t\tDegree\t\tDiscipline\t\t\tInstitution/Location",
])
def test_real_table_header_rows_are_flagged(row):
    """Real column-header rows in the shapes source CVs use (pipe-joined by
    the reader, tab-joined by the template) are flagged."""
    assert _is_column_header_row(row)


@pytest.mark.parametrize("row", [
    "2019 | Assistant Professor | Weill Cornell Medicine",
    "2015-2018 | Member | Admissions Committee | Weill Cornell Medicine",
    "2010 | MD | Medicine | Weill Cornell Medicine, New York, NY",
])
def test_numeric_token_data_rows_are_not_flagged(row):
    """A data row carrying a year or date range is genuine content: its digits
    and proper nouns outnumber the one or two label words it may contain."""
    assert not _is_column_header_row(row)


@pytest.mark.parametrize("entry", [
    "Chair, Admissions Committee",
    "Attending Physician",
    "Associate Editor, Journal of Neurology",
])
def test_short_genuine_entries_are_not_flagged(entry):
    """Short entries survive as long as fewer than half their words are
    column-label vocabulary -- 'Committee' in a three-word entry is 1/3."""
    assert not _is_column_header_row(entry)


@pytest.mark.parametrize("entry", ["Committee Chair", "Date: 2019"])
def test_documented_false_positive_class_is_pinned(entry):
    """The module docstring's known false-positive class: a short entry at
    least half of whose words (digits count as words) are column-label
    vocabulary IS flagged today. Pinned so that a change to the heuristic --
    in either direction -- shows up here rather than in a corpus batch. If
    the heuristic is tightened on purpose, drop the case and update the
    docstring in the same commit."""
    assert _is_column_header_row(entry)


# ---------------------------------------------- unit-level: the drop reasons

@pytest.mark.parametrize("text, reason", [
    ("", DROP_BLANK),
    ("   ", DROP_BLANK),
    ("• Didactic teaching (lectures, seminars, tutorials,)", DROP_TEMPLATE_INSTRUCTION),
    ("Curriculum Vitae", DROP_SOURCE_BOILERPLATE),
    ("Updated: January 2024", DROP_SOURCE_BOILERPLATE),
    ("|  |  |", DROP_RENDERS_EMPTY),
    (HEADER_ROW, DROP_COLUMN_HEADER),
    ("NAME:", DROP_COLUMN_HEADER),
    ("Please list activities at WCM and affiliates, NYP, and previously employed "
     "institutions, including division or department positions, directorships, "
     "deanships, chairmanships on major institutional committees.",
     DROP_NEAR_TEMPLATE_INSTRUCTION),
    ("N/A", DROP_UNANSWERED_PROMPT),
    ("Not Applicable |  |  |", DROP_UNANSWERED_PROMPT),
])
def test_drop_reason_names_the_check_that_fired(text, reason):
    assert _appendix_drop_reason(text, _clean_inline_tabs(text)) == reason


@pytest.mark.parametrize("text", [
    "Reviewed manuscripts for the Journal of Clinical Oncology on an ad hoc basis.",
    "2019 | Assistant Professor | Weill Cornell Medicine",
    "Chair, Admissions Committee",
    "Is your eligibility to work in the U.S. based on an employment visa?: | No",
    "Grant pending | N/A",
])
def test_genuine_content_has_no_drop_reason(text):
    assert _appendix_drop_reason(text, _clean_inline_tabs(text)) is None


def test_blank_table_row_is_counted_as_renders_empty_not_as_a_header():
    """'|  |  |' has no words, which the header heuristic reads as 'all
    column-label vocabulary'; the renders-empty check runs first so the drop
    is reported for what it is."""
    assert _is_column_header_row("|  |  |")  # the heuristic's own verdict
    assert _appendix_drop_reason("|  |  |", _clean_inline_tabs("|  |  |")) == DROP_RENDERS_EMPTY


def test_filter_counts_each_drop_under_its_own_reason():
    entries = [
        {"text": "NAME:"},
        {"text": HEADER_ROW},
        {"text": "|  |  |"},
        {"text": "Curriculum Vitae"},
        {"text": GENUINE_ONE},
        {"text": None},
    ]
    kept, dropped = _filter_unmapped_entries(entries)
    assert [text for _, text in kept] == [GENUINE_ONE]
    assert dropped == Counter({
        DROP_COLUMN_HEADER: 2,
        DROP_RENDERS_EMPTY: 1,
        DROP_SOURCE_BOILERPLATE: 1,
        DROP_BLANK: 1,
    })


def test_filter_keeps_the_entry_object_and_its_rendered_text():
    """The writer needs the original entry (for its per-entry comments) next
    to the collapsed text it will print."""
    entry = {"text": "2010 | MD | Medicine | Weill Cornell Medicine", "hierarchy": ["Education"]}
    kept, dropped = _filter_unmapped_entries([entry])
    assert kept == [(entry, "2010 — MD — Medicine — Weill Cornell Medicine")]
    assert not dropped


def test_describe_dropped_reports_reasons_not_boilerplate():
    assert _describe_dropped(Counter({DROP_COLUMN_HEADER: 2})) == \
        "2 non-content blocks removed (column-header 2)"
    assert _describe_dropped(Counter({DROP_BLANK: 1})) == \
        "1 non-content block removed (blank 1)"
    # Chain order, not insertion order, so the text is stable across inputs.
    text = _describe_dropped(Counter({DROP_COLUMN_HEADER: 1, DROP_BLANK: 1}))
    assert text == "2 non-content blocks removed (blank 1, column-header 1)"
    assert "boilerplate/empty" not in text


# ------------------------------------------- unit-level: grouping, truncation

def test_group_by_source_heading_keys_on_top_level_heading_in_first_seen_order():
    lines = [
        ({"hierarchy": ["Service", "Journals"]}, "a"),
        ({"hierarchy": ["Education"]}, "b"),
        ({"hierarchy": ["Service", "Committees"]}, "c"),
        ({}, "d"),
        ({"hierarchy": None}, "e"),
    ]
    groups = _group_by_source_heading(lines)
    assert list(groups) == ["Service", "Education", "Unknown Section"]
    assert [text for _, text in groups["Service"]] == ["a", "c"]
    assert [text for _, text in groups["Unknown Section"]] == ["d", "e"]


@pytest.mark.parametrize("length", [0, 1, 199, 200, 201, 260, 1000])
def test_truncate_never_exceeds_the_limit(length):
    text = "".join(chr(ord("a") + i % 26) for i in range(length))
    out = _truncate_appendix_text(text)
    assert len(out) <= APPENDIX_MAX_CHARS
    if length <= APPENDIX_MAX_CHARS:
        assert out == text
    else:
        assert len(out) == APPENDIX_MAX_CHARS
        assert out.endswith("...")
        assert out[:-3] == text[:APPENDIX_MAX_CHARS - 3]


def test_appendix_max_chars_is_the_documented_200():
    assert APPENDIX_MAX_CHARS == 200


# ------------------------------------------------------------ render-level

def test_degree_table_header_row_dropped_from_appendix(tmp_path, caplog):
    """Positive control 1 (2071_Zuschlag_Cv shape): a T-coded degree-table
    header row produces no appendix paragraph."""
    entries = [_NAME_ENTRY,
               _t_entry("Year: Degree | Discipline | Institution/Location", ["Education"], 1)]
    text = _output_text(_render(tmp_path, entries, caplog))
    # The raw pipe-joined form never reaches the document -- _clean_inline_tabs
    # collapses " | " to " — " before anything is written, so a literal-pipe
    # assertion here would be vacuous. Assert the actual rendered em-dash form
    # is absent, and that no trace of the header row's words survives at all.
    assert "1. Year: Degree — Discipline — Institution/Location" not in text
    assert "Year: Degree" not in text
    assert "T. APPENDIX" not in text, "no other unmapped entries -- appendix should be empty"
    assert "1 non-content block removed (column-header 1)" in _appendix_log(caplog)


def test_bare_name_header_row_dropped_from_appendix(tmp_path, caplog):
    """Positive control 2 (2054_Opresko_Cv shape): a bare 'NAME:' row that
    would otherwise render as the appendix's '1. NAME:' line is dropped."""
    entries = [_NAME_ENTRY, _t_entry("NAME:", ["Committee Membership"], 1)]
    text = _output_text(_render(tmp_path, entries, caplog))
    assert "1. NAME:" not in text
    assert "T. APPENDIX" not in text, "no other unmapped entries -- appendix should be empty"
    assert "1 non-content block removed (column-header 1)" in _appendix_log(caplog)


def test_real_sentence_entry_still_renders(tmp_path, caplog):
    """Negative control: a T-coded entry that is genuine (non-header) prose
    still reaches the Appendix -- the new filter must not be overbroad."""
    entries = [_NAME_ENTRY,
               _t_entry(f"{REAL_SENTENCE_TOKEN}: reviewed manuscripts for the "
                        "Journal of Clinical Oncology on an ad hoc basis.", ["Service"], 1)]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    text = _output_text(out)
    assert REAL_SENTENCE_TOKEN in text
    assert "T. APPENDIX" in text
    assert "removed" not in _appendix_log(caplog)
    assert not [c for c in _comment_texts(out) if "removed" in c]


def test_blank_table_row_renders_empty(tmp_path, caplog):
    """A blank template table row ('|  |  |') is non-empty as raw text and
    empty on the page: no appendix, and the drop is reported as renders-empty,
    not as a header."""
    entries = [_NAME_ENTRY, _t_entry("|  |  |", ["Service"], 1)]
    out = _render(tmp_path, entries, caplog)
    assert "T. APPENDIX" not in _output_text(out)
    assert _appendix_paragraphs(out) == []
    assert "1 non-content block removed (renders-empty 1)" in _appendix_log(caplog)


def test_template_instruction_is_filtered(tmp_path, caplog):
    entries = [_NAME_ENTRY,
               _t_entry("• Didactic teaching (lectures, seminars, tutorials,)", ["Teaching"], 1)]
    out = _render(tmp_path, entries, caplog)
    text = _output_text(out)
    # The phrase itself is Section K's own instruction text in the WCM
    # template, so only the appendix form of it must be absent.
    assert "1. • Didactic teaching" not in text
    assert "T. APPENDIX" not in text
    assert _appendix_paragraphs(out) == []
    assert "1 non-content block removed (template-instruction 1)" in _appendix_log(caplog)


def test_source_cv_boilerplate_is_filtered(tmp_path, caplog):
    entries = [_NAME_ENTRY,
               _t_entry("Curriculum Vitae", ["Unknown"], 1),
               _t_entry("Updated: January 2024", ["Unknown"], 2)]
    text = _output_text(_render(tmp_path, entries, caplog))
    assert "1. Curriculum Vitae" not in text
    assert "Updated: January 2024" not in text
    assert "T. APPENDIX" not in text
    assert "2 non-content blocks removed (source-boilerplate 2)" in _appendix_log(caplog)


def test_mixed_header_and_genuine_entries_numbering_does_not_shift(tmp_path, caplog):
    """The #424 symptom: a header row ahead of genuine entries pushed their
    numbers to 2 and 3. With the row dropped they are 1 and 2, and the summary
    comment says a column-header row was removed, not 'boilerplate/empty'."""
    entries = [_NAME_ENTRY,
               _t_entry(HEADER_ROW, ["Service"], 1),
               _t_entry(GENUINE_ONE, ["Service"], 2),
               _t_entry(GENUINE_TWO, ["Service"], 3)]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    assert _appendix_paragraphs(out) == [
        'From "Service":',
        f"1. {GENUINE_ONE}",
        f"2. {GENUINE_TWO}",
    ]
    assert HEADER_ROW_RENDERED not in _output_text(out)
    assert "1 non-content block removed (column-header 1)" in _comment_texts(out)


def test_numbering_restarts_per_source_heading(tmp_path, caplog):
    entries = [_NAME_ENTRY,
               _t_entry("EDU_ONE bachelor of arts, some college", ["Education"], 1),
               _t_entry("EDU_TWO master of science, some university", ["Education"], 2),
               _t_entry(GENUINE_ONE, ["Service"], 3),
               _t_entry(GENUINE_TWO, ["Service"], 4)]
    out = _render(tmp_path, entries, caplog)
    assert _appendix_paragraphs(out) == [
        'From "Education":',
        "1. EDU_ONE bachelor of arts, some college",
        "2. EDU_TWO master of science, some university",
        "",
        'From "Service":',
        f"1. {GENUINE_ONE}",
        f"2. {GENUINE_TWO}",
    ]


def test_over_limit_entry_renders_at_exactly_the_limit(tmp_path, caplog):
    """A >200-character entry renders as exactly 200 characters after the
    number, marker included -- the old `text[:200] + '...'` gave 203."""
    long_text = "LONG_ENTRY " + " ".join(f"word{i}" for i in range(60))
    assert len(long_text) > APPENDIX_MAX_CHARS
    exact_text = "EXACT_ENTRY " + "y" * (APPENDIX_MAX_CHARS - len("EXACT_ENTRY "))
    assert len(exact_text) == APPENDIX_MAX_CHARS
    entries = [_NAME_ENTRY,
               _t_entry(long_text, ["Service"], 1),
               _t_entry(exact_text, ["Service"], 2)]
    paragraphs = _appendix_paragraphs(_render(tmp_path, entries, caplog))
    rendered_long = paragraphs[1]
    assert rendered_long.startswith("1. ")
    body = rendered_long[len("1. "):]
    assert len(body) == APPENDIX_MAX_CHARS
    assert body.endswith("...")
    assert body[:-3] == long_text[:APPENDIX_MAX_CHARS - 3]
    assert paragraphs[2] == f"2. {exact_text}"  # at the limit: untouched


def test_real_source_table_header_row_is_dropped_but_data_row_survives(tmp_path, caplog):
    """Integration regression through the real reader: a source .docx table
    whose first row is a column header. `extract_unified_elements` emits
    `data[0]` with the same cell shape as the data row (#424's root cause 1),
    and its `text` is the rows pipe-joined exactly as stage 2 hands them on.
    Fed to the appendix T-coded, the header row is dropped and the data row is
    numbered 1."""
    source = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("EDUCATION")
    table = doc.add_table(rows=2, cols=4)
    for cell, value in zip(table.rows[0].cells,
                           ["Year", "Degree", "Discipline", "Institution/Location"]):
        cell.text = value
    for cell, value in zip(table.rows[1].cells,
                           ["2010", "MD", "Medicine", "Weill Cornell Medicine, New York, NY"]):
        cell.text = value
    doc.save(str(source))

    tables = [e for e in extract_unified_elements(str(source))["elements"]
              if e["type"] in ("table", "table_content")]
    assert len(tables) == 1
    rows = tables[0]["data"]
    assert len(rows) == 2
    # Root cause 1: nothing distinguishes the header row from the data row.
    assert [sorted(cell) for cell in rows[0]] == [sorted(cell) for cell in rows[1]]
    row_texts = tables[0]["text"].split("\n")
    assert row_texts == [
        "Year | Degree | Discipline | Institution/Location",
        "2010 | MD | Medicine | Weill Cornell Medicine, New York, NY",
    ]

    entries = [_NAME_ENTRY] + [
        _t_entry(row, ["EDUCATION"], idx + 1) for idx, row in enumerate(row_texts)
    ]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    assert _appendix_paragraphs(out) == [
        'From "EDUCATION":',
        "1. 2010 — MD — Medicine — Weill Cornell Medicine, New York, NY",
    ]
    assert "Year — Degree" not in _output_text(out)
    assert "1 non-content block removed (column-header 1)" in _comment_texts(out)
