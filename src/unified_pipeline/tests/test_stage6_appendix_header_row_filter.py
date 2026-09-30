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
    DROP_BARE_YEAR,
    DROP_BLANK,
    DROP_COLUMN_HEADER,
    DROP_CV_TITLE,
    DROP_DATE_STAMP,
    DROP_NEAR_TEMPLATE_INSTRUCTION,
    DROP_RENDERS_EMPTY,
    DROP_SECTION_HEADER,
    DROP_SOURCE_BOILERPLATE,
    DROP_STATUS_MARKER,
    DROP_TEMPLATE_INSTRUCTION,
    DROP_TEMPLATE_LABEL,
    DROP_TOC_LINE,
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
    ("C. Sample Appointments (include institution, title and dates)", DROP_TEMPLATE_INSTRUCTION),
    ("1. Sample Leave: N/A", DROP_TEMPLATE_INSTRUCTION),
    ("NOTE: This section includes talks, panels, etc., for which you were invited",
     DROP_TEMPLATE_INSTRUCTION),
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
    ("Signature:", DROP_TEMPLATE_LABEL),
    ("Site/Position |", DROP_TEMPLATE_LABEL),
])
def test_drop_reason_names_the_check_that_fired(text, reason):
    assert _appendix_drop_reason(text, _clean_inline_tabs(text)) == reason


@pytest.mark.parametrize("text", [
    "Reviewed manuscripts for the Journal of Clinical Oncology on an ad hoc basis.",
    "2019 | Assistant Professor | Weill Cornell Medicine",
    "Chair, Admissions Committee",
    "Sample Publications (list available upon request)",
    "3. Sample Training (describe your role) - Major advisor to five students",
    "Is your eligibility to work in the U.S. based on an employment visa?: | No",
    "Grant pending | N/A",
    "Your role*\toversight",
])
def test_genuine_content_has_no_drop_reason(text):
    assert _appendix_drop_reason(text, _clean_inline_tabs(text)) is None


# ------------------------------------- unit-level: the #885 structural checks
#
# Root cause of #885: T-validation's own `classification_reasoning` already
# says these shapes are structural ("[T-validation confirmed] Bare year
# '2021' is a structural marker"), but that string is free LLM text covering
# real T content too (a hobby, a career-gap note) -- not a safe drop
# predicate. These three checks match the entry's TEXT shape instead, and
# only when `taxonomy_code == "T"` (every other DROP_* check above applies
# regardless of code).

@pytest.mark.parametrize("text, reason", [
    ("2021", DROP_BARE_YEAR),
    ("1991", DROP_BARE_YEAR),
    ("2016.", DROP_BARE_YEAR),  # trailing period: web228/web26 shape
    ("  2021  ", DROP_BARE_YEAR),  # padded: kills a `stripped = text` mutant
    ("Completed", DROP_STATUS_MARKER),
    ("Scheduled", DROP_STATUS_MARKER),
    ("In Press", DROP_STATUS_MARKER),
    ("Published", DROP_STATUS_MARKER),
    ("Not Funded", DROP_STATUS_MARKER),
    ("ACTIVE", DROP_STATUS_MARKER),  # case-insensitive
    ("Completed.", DROP_STATUS_MARKER),  # trailing period: the `.rstrip(".")` call
    ("  Completed  ", DROP_STATUS_MARKER),  # padded: same `stripped` mutant, 2nd path
    # One case per remaining `_STATUS_MARKER_WORDS` member, so dropping a word fails.
    ("Pending", DROP_STATUS_MARKER),
    ("Funded", DROP_STATUS_MARKER),
    ("Submitted", DROP_STATUS_MARKER),
    ("Current", DROP_STATUS_MARKER),
    ("Withdrawn", DROP_STATUS_MARKER),
    ("Ongoing", DROP_STATUS_MARKER),
    ("Honors and Awards       Page 7", DROP_TOC_LINE),  # web181 shape
    ("Visiting Professorships, Seminars, and Extramural Presentations Page 17",
     DROP_TOC_LINE),
    ("Abstracts, Preliminary Communications, Panel Discussions  Page 42-54",
     DROP_TOC_LINE),  # a page RANGE, not just one number
])
def test_structural_shapes_are_dropped_when_t_coded(text, reason):
    assert _appendix_drop_reason(text, _clean_inline_tabs(text), "T") == reason


@pytest.mark.parametrize("text", [
    "2021", "Completed", "Honors and Awards       Page 7",
])
def test_structural_shapes_survive_when_not_t_coded(text):
    """The taxonomy_code gate: the exact same shapes that #885 confirmed only
    ever occur T-coded (measured over 37,704 entries of every code in the
    #885 corpus) are defense-in-depth gated so a non-T code can never lose
    one, even if some future entry did carry this shape."""
    for code in (None, "H", "A", "M1"):
        assert _appendix_drop_reason(text, _clean_inline_tabs(text), code) is None


@pytest.mark.parametrize("text", [
    "2021 Assistant Professor, Weill Cornell Medicine",  # a year NEXT TO text
    "Class of 2021",
    "Cooking",  # a genuine single-word T record (a real hobby, #885's own example)
    "Tabletop role-playing games",
    "Funded by the NIH",  # contains "Funded" but is not a bare status word
    "Completed the fellowship in 2019",
    "See page 7 for details",  # lowercase "page", not the ToC shape
    "Honors and Awards Page 7 Best Teacher Award 2019",  # real title AFTER
    # "Page N" -- kills a mutant that drops _TOC_LINE_RE's trailing `\s*$`:
    # without that end anchor, `.match()` would accept this as a PREFIX match
    # and drop a real, later-in-the-line award title.
    "Honors and Awards       page 7",  # lowercase "page": same shape as the
    # dropped web181 positive above, differing ONLY in case. Pins that the
    # match is case-sensitive -- unlike "See page 7 for details" above, which
    # is rejected by its trailing text and would still survive even if the
    # regex grew `re.I`.
    #
    # Real-shaped lines the corpus never exercises, so a widened regex
    # would otherwise go unnoticed.
    #
    # A citation-shaped line over 90 chars ending " Page 12" -- kills a mutant
    # that widens `_TOC_LINE_RE`'s `.{1,90}?` heading-length cap to `.*?`. The
    # cap exists because a real, long line (a bibliography citation, here) can
    # end in "Page N" by coincidence; only a SHORT heading before it is the
    # source CV's own table of contents.
    "Anderson RJ, Kim SY, Patel N, Gomez L, Chen W. Long-term outcomes "
    "following minimally invasive cardiac surgery Page 12",
    # No space before "Page" -- kills a mutant that drops the `[ \t]` run-in
    # requirement from `_TOC_LINE_RE`, which would then match "Page" fused
    # onto the end of any word.
    "HomePage 7",
    # A 4-digit number outside 19xx/20xx -- kills a mutant that widens
    # `_BARE_YEAR_RE` from `(?:19|20)\d{2}` to bare `\d{4}`.
    "1066",
    "3021",
    # A year run into other characters -- kills an unescaped `.?` in
    # `_BARE_YEAR_RE` (only a literal trailing period is allowed).
    "2019-",
    "2019)",
    "2019a",
])
def test_real_content_survives_even_when_t_coded(text):
    """Never drop anything carrying real content (#885's own gate): a year
    next to text, a single-word real record, and a sentence that merely
    contains a status or page word are not the bare shapes above."""
    assert _appendix_drop_reason(text, _clean_inline_tabs(text), "T") is None


def test_filter_counts_the_885_reasons_separately():
    entries = [
        {"text": "2021", "taxonomy_code": "T"},
        {"text": "Completed", "taxonomy_code": "T"},
        {"text": "Honors and Awards       Page 7", "taxonomy_code": "T"},
        {"text": "2021 Assistant Professor, Weill Cornell Medicine", "taxonomy_code": "T"},
        {"text": "2021"},  # no taxonomy_code at all -- must not be dropped
    ]
    kept, dropped = _filter_unmapped_entries(entries)
    assert [text for _, text in kept] == [
        "2021 Assistant Professor, Weill Cornell Medicine", "2021",
    ]
    assert dropped == Counter({
        DROP_BARE_YEAR: 1,
        DROP_STATUS_MARKER: 1,
        DROP_TOC_LINE: 1,
    })


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


# --------------------------------------------- render-level: #885's own wire

def test_web210_shape_bare_years_and_status_markers_dropped(tmp_path, caplog):
    """web210 shape (#885): a presentations list is mostly bare years and
    lone status words, with the genuine titles between them surviving under
    their original numbers-minus-drops -- the years and statuses are gone,
    the quoted title is not."""
    entries = [_NAME_ENTRY,
               _t_entry("Scheduled", ["PRESENTATIONS"], 1),
               _t_entry("Completed", ["PRESENTATIONS"], 2),
               _t_entry("2021", ["PRESENTATIONS"], 3),
               _t_entry(GENUINE_ONE, ["PRESENTATIONS"], 4),
               _t_entry("2019", ["PRESENTATIONS"], 5)]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    # Scoped to the appendix section itself, not the whole document: the WCM
    # template's own boilerplate legitimately contains "Completed" ("Past
    # (Completed) Funding"), so a whole-document substring check would be
    # vacuous here.
    assert _appendix_paragraphs(out) == [
        'From "PRESENTATIONS":',
        f"1. {GENUINE_ONE}",
    ]
    comment = _appendix_log(caplog)
    assert "bare-year 2" in comment
    assert "status-marker 2" in comment


def test_web181_shape_toc_lines_dropped(tmp_path, caplog):
    """web181 shape (#885): the source CV's own table of contents reached the
    appendix as numbered lines; dropped, with a genuine section title (no
    'Page N' suffix) surviving."""
    entries = [_NAME_ENTRY,
               _t_entry("Honors and Awards       Page 7", ["APPOINTMENTS"], 1),
               _t_entry("Grants         Page 4", ["APPOINTMENTS"], 2),
               _t_entry("EDUCATION AND TRAINING", ["APPOINTMENTS"], 3)]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    assert _appendix_paragraphs(out) == [
        'From "APPOINTMENTS":',
        "1. EDUCATION AND TRAINING",
    ]
    assert "2 non-content blocks removed (toc-line 2)" in _comment_texts(out)


def test_a_year_next_to_real_content_is_not_dropped(tmp_path, caplog):
    """Negative control: #885's own line -- a bare year is structural, a year
    NEXT TO text is a real (if fragmentary) record and must reach the page."""
    entries = [_NAME_ENTRY,
               _t_entry("2021 Assistant Professor, Weill Cornell Medicine",
                        ["Positions"], 1)]
    out = _render(tmp_path, entries, caplog, emit_comments=True)
    assert _appendix_paragraphs(out) == [
        'From "Positions":',
        "1. 2021 Assistant Professor, Weill Cornell Medicine",
    ]
    assert "removed" not in _appendix_log(caplog)


# --- #885 residual: T-validation-confirmed structural shapes ---------------
# Two signals must agree: the reasoning names the kind, AND the text has the
# kind's positive shape. Invented text only.

def _confirmed(kind_sentence):
    return f"[T-validation confirmed] {kind_sentence} It is a structural artifact."


def _residual(text, reasoning, code="T"):
    return _appendix_drop_reason(text, _clean_inline_tabs(text), code, reasoning)


@pytest.mark.parametrize("text, kind_sentence, reason", [
    ("CURRICULUM VITA", "Document title 'CURRICULUM VITA' with no content.", DROP_CV_TITLE),
    ("Curriculum Vitae & Bibliography", "Document title/header with no content.", DROP_CV_TITLE),
    ("Sample-legal Curriculum Vitae", "This is a document title/header.", DROP_CV_TITLE),
    ("Curriculum Vitae\nJune 2014", "Document header with date - pure structural.", DROP_CV_TITLE),
    ("As of August 10, 2022", "Date stamp 'As of August 10, 2022' is metadata.", DROP_DATE_STAMP),
    ("Date: July 24, 2022", "This is a date stamp indicating CV version.", DROP_DATE_STAMP),
    ("DATE Mar 14th, 2023", "Date stamp only - temporal marker.", DROP_DATE_STAMP),
    ("*Revised June 29, 2018", "This is a revision date stamp.", DROP_DATE_STAMP),
    ("Date April, 6, 2024", "This is a document date stamp.", DROP_DATE_STAMP),
    ("Professional Experience", "This is a section header ('Professional Experience').", DROP_SECTION_HEADER),
    ("Courses Taught:", "Section header 'Courses Taught:' is a structural marker.", DROP_SECTION_HEADER),
    ("III. SAMPLE EXPERIENCE", "Section header label appearing under another section.", DROP_SECTION_HEADER),
    ("Graduate", "This is a section subheader categorizing degree type.", DROP_SECTION_HEADER),
    ("Advisory Boards or Committees", "Section category label that organizes content.", DROP_SECTION_HEADER),
    ("Years     School     Degree", "This is a column header row (Years, School, Degree).", DROP_COLUMN_HEADER),
    ("Trainee | Topic | Program | Outcome", "Column header row for table.", DROP_COLUMN_HEADER),
])
def test_confirmed_structural_shapes_are_dropped(text, kind_sentence, reason):
    assert _residual(text, _confirmed(kind_sentence)) == reason


@pytest.mark.parametrize("text, kind_sentence", [
    # Reasoning says header but the text is a sentence / carries data.
    ("Professional Experience includes two positions.", "This is a section header."),
    ("Section 4 overview", "This is a section header."),  # digit
    ("Professional Experience 2019", "This is a section header."),
    ("One two three four five six seven eight nine ten eleven", "Section header only."),  # > cap
    ("Current position: Assistant Professor (as of 2021), Sample University",
     "This is a date stamp."),
    ("Date of birth June 3, 1980, Springfield", "This is a date stamp."),
    ("Curriculum Vitae of Jane Q Sample, Sample University", "Document title/header."),
    ("Year | Degree | Major\n2001 | BS | Biology", "Column header row with data following."),
])
def test_confirmed_kind_without_the_positive_shape_survives(text, kind_sentence):
    assert _residual(text, _confirmed(kind_sentence)) is None


@pytest.mark.parametrize("text, reasoning", [
    # Right shape, but stage 3b did not confirm it structural.
    ("Professional Experience", "This is a section header."),
    ("Professional Experience", "[T-validation overridden] Section header."),
    ("Professional Experience", ""),
    ("Professional Experience", None),
    # Right shape, confirmed, but the reasoning names a different kind.
    ("Professional Experience", _confirmed("Personal hobby with no professional content.")),
    ("Cooking", _confirmed("Personal interest listed under hobbies.")),
    ("Leverhulme Trust", _confirmed("Funding organization name with no grant details.")),
    ("Sample Journal of Testing", _confirmed("Orphaned journal title with no article detail.")),
    # Each shape needs ITS OWN kind named: a date, a title and a label row
    # under a different confirmed verdict are kept.
    ("As of August 10, 2022", _confirmed("This is a section header.")),
    ("Curriculum Vitae & Bibliography", _confirmed("Date stamp only.")),
    ("Years     School     Degree", _confirmed("Personal hobby with no professional content.")),
    ("Trainee | Topic | Program", _confirmed("This is a document title/header.")),
    # A single cell is not a header ROW.
    ("Assorted", _confirmed("Column header row for table.")),
    # A cell over the word cap is prose, not a column label.
    ("Trainee | one two three four five six seven eight nine ten", _confirmed("Column header row.")),
    # A date followed by more text is not a bare stamp.
    ("Date May 30, 2020 Springfield General Hospital", _confirmed("This is a date stamp.")),
    # More than two words before the title is a different sentence.
    ("One Two Three Curriculum Vitae", _confirmed("This is a document title/header.")),
    # A looser "structural header" verdict is not a SECTION header verdict:
    # the corpus used it for a name line and an institution name.
    ("Sam Q Example MD MPH", _confirmed("This is a name/credentials line and a structural header.")),
    ("Example Institute of Testing", _confirmed("Standalone institution name; structural header only.")),
    # "none" says something about the content, so it is not a bare label.
    ("Patents (none)", _confirmed("Section header 'Patents (none)' is structural notation.")),
    # Kind word only deep in the prose, past the lead window.
    ("Professional Experience",
     "[T-validation confirmed] " + "x" * 120 + " section header"),
])
def test_shape_without_confirmed_kind_survives(text, reasoning):
    assert _residual(text, reasoning) is None


@pytest.mark.parametrize("code", [None, "A", "G", "N4", "M1"])
def test_confirmed_structural_shapes_survive_when_not_t_coded(code):
    text = "Professional Experience"
    assert _residual(text, _confirmed("This is a section header."), code) is None


# --- #530 residual: bare outline labels and wrapped instruction tails ------
# Another institution's template numbers its empty category labels ("1. Sample
# Awards", "b. Sample Scope:"); the marker's own digit used to fail the
# digit-free shape. Same two-signal rule as above, invented text only.

@pytest.mark.parametrize("text, kind_sentence", [
    ("1. Sample Awards", "Section header 'Sample Awards' is a structural label."),
    ("12. Sample Elected Roles, etc.", "Section header describing a category."),
    ("b. Sample Scope:", "Structural subsection marker with no entry content."),
    ("iv. Sample Placement Options", "Subsection marker, purely organizational."),
    ("3) Sample Department-wide", "Structural header/category label, not an entry."),
    ("2. Sample Review Panels (titles, dates)", "Section header for review panels."),
])
def test_confirmed_bare_outline_label_is_dropped(text, kind_sentence):
    assert _residual(text, _confirmed(kind_sentence)) == DROP_SECTION_HEADER


@pytest.mark.parametrize("text, kind_sentence", [
    # A digit past the marker is data, marker or not.
    ("1. Sample Awards 2019", "Section header."),
    ("3. Section 4 overview", "Section header."),
    # A sentence, or an over-long label, is content.
    ("1. Sample awards were received.", "Section header."),
    ("1. Sample awards received etc.", "Section header."),  # no comma: not a category label
    ("1. One two three four five six seven eight nine ten eleven", "Section header."),
    # The marker is stripped only at the start of the line, and only one of them.
    ("Sample Awards 2. Foo", "Section header."),
    ("1. 2. Sample Awards", "Section header."),
    # A marker is at most two digits, and must be followed by whitespace.
    ("123. Sample Awards", "Section header."),
    ("1.Sample Awards", "Section header."),
    # A marker with nothing behind it is not a label.
    ("1.", "Section header."),
    # Confirmed as something else: the organisation name a CV opens with.
    ("1. Sample Institute of Testing", "Institution name only, structural header/artifact."),
    ("1. Cooking", "Personal hobby listed under interests."),
])
def test_outline_label_without_both_signals_survives(text, kind_sentence):
    assert _residual(text, _confirmed(kind_sentence)) is None


_TEN_WORDS = " ".join(["Sample"] * 10)  # the label word cap: 10 fits, 11 does not


@pytest.mark.parametrize("marker", ["1.", "1)", "1,", "b.", "iv.", "VIII)"])
def test_each_outline_marker_form_is_stripped_before_the_word_cap(marker):
    """Ten words behind the marker is a label; the marker itself must not
    count as the eleventh word, for every marker form."""
    assert _residual(f"{marker} {_TEN_WORDS}",
                     _confirmed("Section header.")) == DROP_SECTION_HEADER
    assert _residual(f"{marker} {_TEN_WORDS} Sample",
                     _confirmed("Section header.")) is None


def test_bare_outline_label_survives_without_confirmation_or_t_code():
    label = "1. Sample Awards"
    assert _residual(label, None) is None
    assert _residual(label, "This is a section header.") is None
    assert _residual(label, _confirmed("This is a section header."), "A") is None


@pytest.mark.parametrize("text, kind_sentence", [
    ("each sample list, preferably in reverse order)", "Structural header/instruction line."),
    ("presenter, etc.)", "Structural fragment; appears to be a broken header."),
    ("resulted from sample work; use underline or bold font for your name; number",
     "Instruction text for CV formatting, not substantive content."),
])
def test_confirmed_wrapped_instruction_tail_is_dropped(text, kind_sentence):
    assert _residual(text, _confirmed(kind_sentence)) == DROP_TEMPLATE_INSTRUCTION


@pytest.mark.parametrize("text, kind_sentence", [
    # A wrapped real record carries a year, volume or page: kept.
    ("Sample Summit, Boston (Aug. 2023)", "Instruction text for CV formatting."),
    ("7: 10-19.)", "Instruction text for CV formatting."),
    # A stray ")" that does not end the line is not a wrapped tail.
    ("sample) tail text follows", "Instruction text for CV formatting."),
    # Balanced parenthesis and no directive: an ordinary fragment.
    ("Sample Summit, Boston (annual)", "Instruction text for CV formatting."),
    # Over the word cap: prose.
    (" ".join(["word"] * 30) + ")", "Instruction text for CV formatting."),
    # An author-addressed line the reasoning does not call an instruction.
    ("use sample font for your name", "Personal hobby listed under interests."),
    # A directive needs both a verb and "your"; neither alone is enough.
    ("sample notes about your name", "Instruction text for CV formatting."),
    ("use sample font", "Instruction text for CV formatting."),
    # The verb and "your" must sit in one clause: ";" or "." ends the window.
    ("list sample; see your name", "Instruction text for CV formatting."),
    ("use sample. see your name", "Instruction text for CV formatting."),
    # ... and within 40 characters of each other.
    ("use " + "sample " * 8 + "your name", "Instruction text for CV formatting."),
])
def test_wrapped_tail_without_both_signals_survives(text, kind_sentence):
    assert _residual(text, _confirmed(kind_sentence)) is None


_INSTRUCTION = "Instruction text for CV formatting."


@pytest.mark.parametrize("verb", ["use", "please", "list", "include", "provide"])
def test_each_author_directive_verb_is_pinned(verb):
    assert _residual(f"{verb} sample font for your name",
                     _confirmed(_INSTRUCTION)) == DROP_TEMPLATE_INSTRUCTION


def _gap_text(gap):
    """'use' + exactly `gap` characters + 'your name' (digit-free)."""
    return "use " + "a" * (gap - 2) + " your name"


@pytest.mark.parametrize("gap, dropped", [(40, True), (41, False)])
def test_author_directive_window_is_forty_characters(gap, dropped):
    assert len(_gap_text(gap).split("your")[0]) - len("use") == gap
    got = _residual(_gap_text(gap), _confirmed(_INSTRUCTION))
    assert got == (DROP_TEMPLATE_INSTRUCTION if dropped else None)


@pytest.mark.parametrize("words, dropped", [(25, True), (26, False)])
def test_wrapped_tail_word_cap_is_twenty_five(words, dropped):
    text = " ".join(["word"] * (words - 1)) + " last)"
    assert len(text.split()) == words
    got = _residual(text, _confirmed(_INSTRUCTION))
    assert got == (DROP_TEMPLATE_INSTRUCTION if dropped else None)


@pytest.mark.parametrize("text", [
    "reuse sample font for your name",       # verb only inside a longer word
    "used sample font for your name",
    "use sample font for yourself",          # "your" only inside a longer word
    "use sample font for yours",
])
def test_author_directive_needs_word_boundaries(text):
    assert _residual(text, _confirmed(_INSTRUCTION)) is None


@pytest.mark.parametrize("reasoning_sentence", [
    "Structural category label.",   # not "header/category label"
    "Structural marker.",           # not "subsection marker"
])
def test_section_wordings_need_their_full_phrase(reasoning_sentence):
    assert _residual("3) Sample Department-wide", _confirmed(reasoning_sentence)) is None


@pytest.mark.parametrize("reasoning_sentence", [
    "Structural instruction.",      # not "instruction text/line"
    "Structural header.",           # not "broken header"
])
def test_instruction_wordings_need_their_full_phrase(reasoning_sentence):
    assert _residual("presenter, etc.)", _confirmed(reasoning_sentence)) is None


def test_category_label_etc_is_case_insensitive():
    assert _residual("1. Sample Roles, Etc.", _confirmed("Section header.")
                     ) == DROP_SECTION_HEADER


def test_author_directive_is_case_insensitive():
    assert _residual("Use bold font for your name",
                     _confirmed("Instruction text for CV formatting.")
                     ) == DROP_TEMPLATE_INSTRUCTION


def test_wrapped_tail_needs_its_own_signals_not_just_the_shape():
    tail = "presenter, etc.)"
    assert _residual(tail, None) is None
    assert _residual(tail, "Structural fragment; broken header.") is None
    assert _residual(tail, _confirmed("Structural fragment; broken header."), "A") is None


def test_older_checks_keep_their_drop_over_the_residual_check():
    """'Education' is a template label AND a confirmed section header; the
    residual check runs last so the earlier reason still owns the count."""
    reasoning = _confirmed("This is a section header ('Education').")
    assert _residual("Education", reasoning) == DROP_TEMPLATE_LABEL


def test_filter_reads_reasoning_from_the_entry_and_counts_each_reason():
    header = _confirmed("This is a section header.")
    entries = [
        {"text": "Curriculum Vitae & Bibliography", "taxonomy_code": "T",
         "classification_reasoning": _confirmed("Document title.")},
        {"text": "As of May 1, 2020", "taxonomy_code": "T",
         "classification_reasoning": _confirmed("Date stamp.")},
        {"text": "Awards", "taxonomy_code": "T", "classification_reasoning": header},
        {"text": "Awards", "taxonomy_code": "T"},  # no reasoning: kept
        {"text": "Awards", "taxonomy_code": "A", "classification_reasoning": header},
    ]
    kept, dropped = _filter_unmapped_entries(entries)
    assert [t for _, t in kept] == ["Awards", "Awards"]
    assert dropped == Counter({DROP_CV_TITLE: 1, DROP_DATE_STAMP: 1, DROP_SECTION_HEADER: 1})
    assert _describe_dropped(dropped) == (
        "3 non-content blocks removed (cv-title 1, date-stamp 1, section-header 1)"
    )


def test_appendix_intro_is_the_shared_accurate_banner(tmp_path, caplog):
    # #534: _fill_appendix writes the shared intro line, italic, and the old
    # "not successfully mapped" claim is gone.
    entries = [_t_entry("Example Leftover Society Membership, 2015", ["OTHER"], 7)]
    doc = Document(str(_render(tmp_path, entries, caplog)))
    paragraphs = doc.paragraphs
    header = next(i for i, p in enumerate(paragraphs) if p.text == "T. APPENDIX")
    intro = paragraphs[header + 1]
    assert intro.text == appendix_module.APPENDIX_INTRO_TEXT
    assert all(run.italic for run in intro.runs)
    assert "successfully mapped" not in _output_text(tmp_path / "out.docx")
