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

from unified_pipeline.core.docx_structure_extractor import (
    extract_unified_elements,  # noqa: E402
)
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
    DROP_ON_REQUEST,
    DROP_PAGE_FURNITURE,
    DROP_RENDERS_EMPTY,
    DROP_RULE_LINE,
    DROP_SECTION_HEADER,
    DROP_SIGNATURE_BLOCK,
    DROP_SOURCE_BOILERPLATE,
    DROP_STATUS_MARKER,
    DROP_TEMPLATE_INSTRUCTION,
    DROP_TEMPLATE_LABEL,
    DROP_TOC_LINE,
    DROP_UNANSWERED_PROMPT,
    OwnerTokens,
    _appendix_drop_reason,
    _describe_dropped,
    _filter_unmapped_entries,
    _group_by_source_heading,
    _owner_signature_tokens,
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
            caplog: pytest.LogCaptureFixture, emit_comments: bool = False,
            cv_owner: dict[str, str] | None = None) -> Path:
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
    if cv_owner is not None:
        data["cv_owner"] = cv_owner
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
    # a tracked revision's wording minus one comma: near, not exact (#829)
    ("Please list activities at WCM and affiliates, NYP and previously employed "
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


@pytest.mark.parametrize("marker", ["1.", "1)", "1,", "b.", "B.", "iv.", "VIII)"])
def test_each_outline_marker_form_is_stripped_before_the_word_cap(marker):
    """Ten words behind the marker is a label; the marker itself must not
    count as the eleventh word, for every marker form."""
    assert _residual(f"{marker} {_TEN_WORDS}",
                     _confirmed("Section header.")) == DROP_SECTION_HEADER
    assert _residual(f"{marker} {_TEN_WORDS} Sample",
                     _confirmed("Section header.")) is None


_LONG_QUALIFIER = "(e.g., list each sample item or\tsample record in any sample form)"
# Stage-4 entry text keeps the source's line breaks, so a qualifier can wrap.
_WRAPPED_QUALIFIER = "(e.g., list each sample item or\nsample record in any sample form)"


@pytest.mark.parametrize("text", [
    # The e.g. qualifier would be the eleventh-plus word; it is not counted.
    f"3. Sample Record of Sample Reach {_LONG_QUALIFIER}",
    f"3. Sample Record of Sample Reach {_WRAPPED_QUALIFIER}",
    "3.  Sample Placement (sample, sample/sample role, etc.; sample sample, sample, if applicable)",
    f"b. {_TEN_WORDS} (sample sample sample)",
    f"b. {_TEN_WORDS} ()",
    # A qualifier far longer than any length bound (130 characters).
    "2. Sample Placement (" + ", ".join(["sample"] * 16) + ", if applicable)",
    # A qualifier glued to the word before it, with no space.
    # Ten label words, then the qualifier: counted, it would be the eleventh+.
    "b. one two three four five six seven eight nine ten(sample sample sample)",
    # A marker and a parenthetical alone: the parenthetical is the label, as before.
    f"1. ({_TEN_WORDS})",
])
def test_outline_label_qualifier_does_not_count_toward_the_word_cap(text):
    assert _residual(text, _confirmed("Subsection header describing a category.")) == DROP_SECTION_HEADER


@pytest.mark.parametrize("text", [
    # Without an outline marker the qualifier counts: a template marks its labels.
    f"Sample Record of Sample Reach {_LONG_QUALIFIER}",
    # The label before the qualifier is still capped at ten words.
    f"1. {_TEN_WORDS} Sample (x)",
    # A digit or "none" inside the qualifier still keeps the entry.
    "1. Sample Placement (e.g., sample one through sample 3 sample sample sample)",
    "1. Sample Patents (none so far, sample sample sample sample sample sample)",
    # Only a CLOSING parenthetical is a qualifier.
    "1. Sample (sample sample sample sample sample sample) Placement sample sample sample",
    "1. Sample (sample sample sample sample sample sample sample sample sample) Placement (x)",
    # The qualifier must close, and hold no parenthesis of its own.
    "1. Sample Record (e.g., sample sample sample sample sample sample sample sample sample",
    "1. Sample (sample sample sample sample sample sample sample sample sample sample (x)",
    "1. Sample sample sample sample sample sample sample sample sample (x) y)",
    # A marker followed by nothing but a long parenthetical has no label to cap.
    "1. (sample sample sample sample sample sample sample sample sample sample sample)",
])
def test_outline_label_qualifier_is_not_a_blanket_exemption(text):
    assert _residual(text, _confirmed("Subsection header describing a category.")) is None


def test_outline_label_qualifier_is_found_past_trailing_whitespace():
    text = f"3. Sample Record of Sample Reach {_LONG_QUALIFIER}  \n"
    assert appendix_module._is_section_label_text(text) is True


def test_outline_label_qualifier_needs_the_confirmed_section_verdict():
    text = f"3. Sample Record of Sample Reach {_LONG_QUALIFIER}"
    assert _residual(text, None) is None
    assert _residual(text, _confirmed("Personal hobby with no professional content.")) is None
    assert _residual(text, _confirmed("Subsection header."), "A") is None


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
    # More "(" than ")": the closing paren closes one it opened.
    ("sample (note (a)", "Instruction text for CV formatting."),
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
    "Belongs to the subsection.",   # not "subsection marker"
    "Structural header/category.",  # not "header/category label"
])
def test_section_wordings_need_their_full_phrase(reasoning_sentence):
    assert _residual("3) Sample Department-wide", _confirmed(reasoning_sentence)) is None


@pytest.mark.parametrize("reasoning_sentence", [
    "Structural instruction.",      # not "instruction text/line"
    "Structural header.",           # not "broken header"
    "Structural text fragment.",    # "text" without "instruction"
    "Wrapped line.",                # "line" without "instruction"
    "Broken fragment.",             # "broken" without "header"
])
def test_instruction_wordings_need_their_full_phrase(reasoning_sentence):
    assert _residual("presenter, etc.)", _confirmed(reasoning_sentence)) is None


@pytest.mark.parametrize("text", ["", "   "])
def test_blank_text_is_not_a_section_label(text):
    """Zero words would pass the word cap, so blank must be refused first."""
    assert appendix_module._is_section_label_text(text) is False


def test_category_label_etc_is_case_insensitive():
    assert _residual("1. Sample Roles, Etc.", _confirmed("Section header.")
                     ) == DROP_SECTION_HEADER


def test_author_directive_window_allows_a_comma():
    """Only ";" and "." end the clause; a comma inside it does not."""
    assert _residual("use bold, for your name", _confirmed(_INSTRUCTION)
                     ) == DROP_TEMPLATE_INSTRUCTION


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


# ---------------------------------------------------- #1221: signature, date, rule

_FAKE_OWNER = {
    "first_name": "Jane", "middle_name": "Q.", "last_name": "Doe",
    "full_name_with_credentials": "Jane Q. Doe, MD, F.A.C.P.",
}
_OWNER_TOKENS = _owner_signature_tokens(_FAKE_OWNER)


def _sig(text, reasoning, code="T", tokens=_OWNER_TOKENS):
    return _appendix_drop_reason(text, _clean_inline_tabs(text), code, reasoning, tokens)


@pytest.mark.parametrize("text", [
    "_" * 5, "_" * 70, "=====", "-----", "–––––––", "_-_=_-",
])
def test_rule_line_is_dropped_text_only_when_t_coded(text):
    assert _sig(text, None) == DROP_RULE_LINE
    assert _sig(text, None, code="K1") is None


@pytest.mark.parametrize("text", ["____", "----", "_ _ _ _ _", "Example ______", "-----x"])
def test_short_or_mixed_rule_shapes_survive(text):
    assert _sig(text, None) != DROP_RULE_LINE


# #530 (RCBKFG GKAQHB 41): a separator of other symbols is a rule line too.
@pytest.mark.parametrize("text", ["*" * 89, "#####", "~~~~~", "*-*-*", "•••••"])
def test_symbol_separator_is_dropped_text_only_when_t_coded(text):
    assert _sig(text, None) == DROP_RULE_LINE
    assert _sig(text, None, code="K1") is None


@pytest.mark.parametrize("text", ["****", "* * * * *", "*****a", "*** 2019 ***"])
def test_short_spaced_or_alphanumeric_symbol_runs_survive(text):
    assert _sig(text, None) != DROP_RULE_LINE


# #530 (RCBKFG UYFRTL 17): a bare label with a closing colon, alone in its
# Appendix group, is dropped on its shape alone, whatever 3b's reasoning says.
def _t_line(text, heading="Sample Heading", reasoning=None, code="T"):
    return {"text": text, "taxonomy_code": code, "hierarchy": [heading],
            "classification_reasoning": reasoning}


def _kept_texts(entries):
    kept, dropped = _filter_unmapped_entries(entries)
    return [t for _, t in kept], dropped


@pytest.mark.parametrize("text", [
    "Sample Background:", "1. Sample Awards:", "b. National:", "Sample background:",
    "Honors, Awards, etc.:",
])
@pytest.mark.parametrize("reasoning", [
    "[T-validation confirmed] Header label only, no substantive content; correctly T",
    "[T-validation confirmed] Empty structural header with no content to classify",
    None,
])
def test_lone_bare_label_is_dropped_on_shape_alone(text, reasoning):
    kept, dropped = _kept_texts([_t_line(text, reasoning=reasoning),
                                 _t_line("Sample hobby", heading="Other Heading")])
    assert kept == ["Sample hobby"]
    assert dropped == Counter({DROP_SECTION_HEADER: 1})


def test_bare_label_with_a_line_beside_it_is_a_lead_in_and_kept():
    entries = [_t_line("Sample reviewer for:"), _t_line("Sample Journal of Testing")]
    assert _kept_texts(entries) == (["Sample reviewer for:", "Sample Journal of Testing"],
                                    Counter())


def test_lone_bare_label_is_kept_when_not_t_coded():
    assert _kept_texts([_t_line("Sample Background:", code="K1")])[0] == ["Sample Background:"]


def test_an_entry_with_no_hierarchy_groups_under_the_unknown_section():
    entries = [{"text": "Sample Background:", "taxonomy_code": "T"},
               {"text": "Sample hobby", "taxonomy_code": "T", "hierarchy": []}]
    assert _kept_texts(entries)[0] == ["Sample Background:", "Sample hobby"]


@pytest.mark.parametrize("text", [
    "Sample Background",           # no closing colon: a hobby or skill shape
    "Languages: Example",          # content after the colon
    "Note: see below:",            # two colons
    "Sample\tBackground:",         # two cells
    "Sample\nBackground:",         # two lines
    "Sample Awards 2019:",         # a digit
    "Patents (none):",             # says something about the content
    "the sample background:",      # does not open with a capital
    "Sample background was here.:",  # a sentence
    "Prof. Sample Person Email:",  # a period: a title before a person's name
    "One Two Three Four Five Six Seven Eight Nine Ten Eleven:",  # over the cap
])
def test_non_bare_label_shapes_survive_alone(text):
    assert _kept_texts([_t_line(text)])[0] == [_clean_inline_tabs(text)]


@pytest.mark.parametrize("text, sentence", [
    ("(Date)\t(Signature of Candidate)", "This is a structural artifact (signature line)."),
    ("01/02/2020\n(Date)\t(Signature of Candidate)", "Date line with signature placeholder."),
    ("1/2/2020 \nDate:\t(Signature of Candidate)", "Signature block with date."),
    ("Date: Aug 5, 2018    Signature:", "A document footer or signature block."),
    ("Date:  ____________   Signature ______________", "Signature line template."),
    ("__________\n(Date)\t____________\n   (Signature of Candidate)", "Signature line with blanks."),
    ("Signed:   Jane Q. Doe, MD", "Signature line ('Signed: ...')."),
    ("01/02/2020\n(Date)\tJANE DOE, MD\n(Signature of Candidate)", "A signature block with date."),
    ("March 3, 2021         Jane Q. Doe\tDate        Jane Q. Doe, M.D.", "A structural artifact."),
    ("March 3, 2021   Jane Doe\tDate   J. Doe, MD", "A structural artifact."),
])
def test_signature_blocks_are_dropped_with_both_signals(text, sentence):
    assert _sig(text, _confirmed(sentence)) == DROP_SIGNATURE_BLOCK


@pytest.mark.parametrize("text, sentence", [
    # A real record that merely contains a date, or a name, or the word signature.
    ("Signed a contract with Example Press, 2019", "Signature line."),
    ("Signature Programs Committee, Example University, 2018", "Signature block."),
    ("Jane Q. Doe, Example University, 03/04/2019", "Signature block."),
    ("Date of Birth: 01/02/1970", "Signature block."),
    ("Signed: Pat Roe, MD", "Signature line."),  # a name that is not the owner's
    ("Of", "Signature line."),
    ("Candidate of Medicine, 06/15/2010", "Structural artifact."),
    # Degree candidacy lines hold a date and the owner's credential.
    ("PhD Candidate, May 2019", "Structural artifact."),
    ("MD Candidate, June 2010", "Structural artifact."),
    # A credential or an initial plus a date is not the owner's name plus a date.
    ("PhD, May 2019", "Structural artifact."),
    ("M.D., June 2010", "Structural artifact."),
    ("J. 03/04/2019", "Structural artifact."),
    ("FACP 3/4/2020", "Signature line."),
])
def test_signature_shape_never_drops_a_real_record(text, sentence):
    assert _sig(text, _confirmed(sentence)) is None


@pytest.mark.parametrize("text, lead", [
    ("(Date)\t(Signature of Candidate)", "Signature line with placeholder."),
    ("Signed:   Jane Q. Doe, MD", "Signed line."),
    ("Date: ____ Signature ____", "Document footer."),
])
def test_each_reasoning_word_alone_is_a_signature_signal(text, lead):
    # Raw reasoning without "structural artifact": each alternative must carry it.
    assert _sig(text, f"[T-validation confirmed] {lead}") == DROP_SIGNATURE_BLOCK


@pytest.mark.parametrize("lead", [
    "Assigned to the program.",  # "signed" inside another word
    "Designed as a form.",
    "This is not a structural artifact.",  # a negated phrase
    "This is not a signature line.",
])
def test_signature_reasoning_signal_is_word_bounded_and_not_negated(lead):
    reasoning = f"[T-validation confirmed] {lead}"
    assert _sig("Date: ____ Signature ____", reasoning) is None


def test_signature_shape_alone_or_reasoning_alone_keeps_the_entry():
    shape = "(Date)\t(Signature of Candidate)"
    assert _sig(shape, None) is None
    assert _sig(shape, "[T-validation confirmed] Hobbies list.") is None
    assert _sig("Jane Q. Doe, Example University", _confirmed("Signature line.")) is None


def test_signature_blocks_survive_when_not_t_coded():
    text = "(Date)\t(Signature of Candidate)"
    assert _sig(text, _confirmed("Signature line."), code="K1") is None


def test_signed_owner_needs_the_owner_signature_tokens():
    text = "Signed:   Jane Q. Doe, MD"
    assert _sig(text, _confirmed("Signature line."), tokens=OwnerTokens()) is None


@pytest.mark.parametrize("text, sentence", [
    ("(As of 02/14/2017)", "Structural timestamp/date marker."),
    ("As of 02/14/2017", "Date stamp."),
    ("Sept 9, 2016", "Date line only."),
    ("9/9/2016", "Date line only."),
    ("(Sept 9, 2016)", "Date stamp."),
])
def test_numeric_and_bare_full_date_stamps_are_dropped(text, sentence):
    assert _sig(text, _confirmed(sentence)) == DROP_DATE_STAMP


@pytest.mark.parametrize("text", [
    "May 2020",  # month-year alone is also a record's date fragment
    "Sept 9, 2016 Annual Meeting",
    "(As of 02/14/2017) Example Society, Member",
    "02/14",
    "(1.2.10)",  # a section number or two-digit-year date is not a stamp
    "12-15-20",
])
def test_date_stamp_shape_never_drops_a_record_that_holds_a_date(text):
    assert _sig(text, _confirmed("Date stamp.")) is None


def test_owner_signature_tokens_normalise_periods_and_tolerate_missing_owner():
    assert {"jane", "q", "doe", "md", "facp", "j", "d", "m", "f"} <= _OWNER_TOKENS.removable
    assert _owner_signature_tokens(None) == OwnerTokens()
    assert _owner_signature_tokens({"first_name": None}) == OwnerTokens()


def test_signature_and_rule_drops_are_counted_and_described():
    entries = [
        {"text": "_" * 40, "taxonomy_code": "T"},
        {"text": "Signed: Jane Doe", "taxonomy_code": "T",
         "classification_reasoning": _confirmed("Signature line.")},
        {"text": "Example Society, Member, 2019", "taxonomy_code": "T",
         "classification_reasoning": _confirmed("Signature line.")},
    ]
    kept, dropped = _filter_unmapped_entries(entries, _OWNER_TOKENS)
    assert [t for _, t in kept] == ["Example Society, Member, 2019"]
    assert dropped == Counter({DROP_RULE_LINE: 1, DROP_SIGNATURE_BLOCK: 1})
    assert _describe_dropped(dropped) == (
        "2 non-content blocks removed (rule-line 1, signature-block 1)"
    )


def test_fill_appendix_passes_the_cv_owner_to_the_signature_shape(tmp_path, caplog):
    entry = _t_entry("Signed:   Jane Q. Doe, MD", ["Footer"], 3)
    entry["classification_reasoning"] = _confirmed("Signature line.")
    keeper = _t_entry("Example Leftover Society Membership, 2015", ["Footer"], 4)
    doc = Document(str(_render(tmp_path, [_NAME_ENTRY, entry, keeper], caplog,
                               cv_owner=_FAKE_OWNER)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Example Leftover Society Membership" in text
    assert "Signed:" not in text
    doc2 = Document(str(_render(tmp_path, [_NAME_ENTRY, entry, keeper], caplog)))
    assert "Signed:" in "\n".join(p.text for p in doc2.paragraphs)


# ------------------------------------------- #1221 (EBYSBC E24): page furniture

@pytest.mark.parametrize("text", [
    # Word field-code text left in the runs by a .doc conversion.
    "PAGE \\* MERGEFORMAT 7",
    "Page PAGE 3 of NUMPAGES 12",
    "page PAGE \\* MERGEFORMAT 4/ NUMPAGES \\* MERGEFORMAT 11",
    "PAGE iv",
    "SHAPE \\* CHARFORMAT",
    "Curriculum Vitae Page PAGE 23",
    'JQDoe vita, version DATE \\@ "d MMMM yyyy" \\* MERGEFORMAT 3 November 2011',
    'DATE \\@ "yy-MM-dd" 11-03-02 JQD vitae',
    # A lone page word and the owner's running header or footer.
    "Page",
    "Jane Q. Doe P a g e | 2",
    "Jane Doe Curriculum Vitae 7",
    "Jane Q. Doe, MD",
    "March 3, 2021   Jane Q. Doe",
    "Jane Q. Doe, MD -- vita as of 3/11/2011",
    "Jane Q. Doe, M.D., F.A.C.P. (revision of 2 Nov 2011)",
    "p. 3 -- Doe, Jane, M.D. -- C.V. 2/11/03",
    "Jane Q. Doe\u2019s Curriculum Vitae",
    # Revision stamps, month-name or numeric, two- or four-digit year.
    "Vitae updated 7 November 2011",
    "Last revision date: 2 Oct 2011",
    "Revision 11/3/02",
    "(revised 02-11-03)",
    "Version of 3/11/2011",
    "Version of November 2011",
    # A section label repeated at the top of the next page.
    "SAMPLE APPOINTMENTS  (Continued):",
    "Sample Committees (cont'd)",
])
def test_page_furniture_is_dropped_text_only_when_t_coded(text):
    assert _sig(text, None) == DROP_PAGE_FURNITURE
    assert _sig(text, None, code="K1") is None


@pytest.mark.parametrize("text", [
    "1979-1983",  # no furniture evidence: a date range alone is kept as before
    "September 1983",
    "Spring 1979",
    "Pages 12-19",  # a citation's page range
    "p. 45-67",
    "Page Street Clinic",
    "Example Lab, Page 2",
    "Jane Q. Doe Lab, Example University",
    "Doe J, Roe P. Sample title. Example Journal 2019",
    "PAGE 3 Doe J. Sample title",
    "Pat Roe, MD",  # a name that is not the owner's
    "Updated the curriculum for the sample course, 2019",
    "Date of Birth: 01/02/1970",
    "Statement of Sample Goals:",
    "Sample Program 2 (Continued)",
    "Continued",
    # Record numbers: a phone number, a ZIP code, a run-together phone number
    # and a citation's volume and pages are content beside the owner's name.
    "Jane Q. Doe, MD 555-010-0199",
    "Jane Q. Doe 12345",
    "Jane Q. Doe 5550100199",
    "Doe J. 2011;7:101-109",
    # "(continued)" after a record rather than a heading.
    "Served on the sample review committee (continued)",
    "Member, Example Society (continued)",
    "Example Society Annual Meeting Program Planning Committee (Continued)",
])
def test_page_furniture_never_drops_a_line_with_other_words(text):
    assert _sig(text, None) not in {DROP_PAGE_FURNITURE, DROP_ON_REQUEST}


def test_owner_name_needs_the_owner_tokens():
    # Without the owner's tokens the name is just two words, kept.
    assert _sig("Jane Q. Doe, MD", None, tokens=OwnerTokens()) is None
    assert _sig("Page PAGE 3", None, tokens=OwnerTokens()) == DROP_PAGE_FURNITURE


def test_fused_initials_are_the_owners_only():
    # "JQDoe" is the owner's initials on her surname; "XYDoe" and "JQRoe" are not.
    assert appendix_module.is_page_furniture("JQDoe CV", _OWNER_TOKENS)
    assert not appendix_module.is_page_furniture("XYDoe CV", _OWNER_TOKENS)
    assert not appendix_module.is_page_furniture("JQRoe CV", _OWNER_TOKENS)
    assert not appendix_module.is_page_furniture("JQDZDoe CV", _OWNER_TOKENS)  # four initials


def test_owner_signature_tokens_carry_names_and_initials():
    assert _OWNER_TOKENS.names == {"jane", "doe"}
    assert _OWNER_TOKENS.initials == {"j", "q", "d"}


@pytest.mark.parametrize("text", [
    "References: Available on Request",
    "Available upon request |",
    "References available upon request.",
])
def test_references_on_request_is_dropped_when_t_coded(text):
    assert _sig(text, None) == DROP_ON_REQUEST
    assert _sig(text, None, code="K1") is None


def test_on_request_needs_the_whole_line():
    assert _sig("Sample data set, available on request from the author", None) is None


@pytest.mark.parametrize("text, sentence", [
    ("Project Sponsor Role Percent Total Period", "Table header row with column labels."),
    ("Lecture # Topic Audience Venue Hours", "Structural header row with column labels."),
    ("Prize Title Given By Year Received", "Structural header line with only column labels."),
])
def test_single_spaced_column_header_rows_are_dropped_with_both_signals(text, sentence):
    assert _sig(text, _confirmed(sentence)) == DROP_COLUMN_HEADER
    assert _sig(text, _confirmed("Software skill listing.")) is None


@pytest.mark.parametrize("text", [
    "Sample Grant Title Example Funder 2019",  # a digit means data
    "Example Society, Member",  # sentence punctuation
    "Sample Site",  # two words
    "Member of the Guild",  # fewer than three capitalised words
    "Example Lecture Series hosted Sample Speakers",  # a lowercase word that is no connector
])
def test_label_run_shape_keeps_data_and_prose(text):
    assert _sig(text, _confirmed("Table header row.")) is None


def test_labelled_directive_is_dropped_with_both_signals():
    text = "Sample Statement:  (to be completed by the applicant; please be brief.)"
    assert _sig(text, _confirmed("Template instruction placeholder text.")) == DROP_TEMPLATE_INSTRUCTION
    assert _sig(text, _confirmed("Hobby list.")) is None
    # A digit inside the parentheses is a real value, not the template's directive.
    held = "Sample Statement:  (to be completed by the applicant, 2011)"
    assert _sig(held, _confirmed("Template instruction placeholder text.")) is None


def test_furniture_drops_are_counted_and_described():
    entries = [
        {"text": "PAGE \\* MERGEFORMAT 17", "taxonomy_code": "T"},
        {"text": "Jane Q. Doe, MD", "taxonomy_code": "T"},
        {"text": "References: Available on Request", "taxonomy_code": "T"},
        {"text": "Example Hobby Club, Member, 2019", "taxonomy_code": "T"},
    ]
    kept, dropped = _filter_unmapped_entries(entries, _OWNER_TOKENS)
    assert [t for _, t in kept] == ["Example Hobby Club, Member, 2019"]
    assert dropped == Counter({DROP_PAGE_FURNITURE: 2, DROP_ON_REQUEST: 1})
    assert _describe_dropped(dropped) == (
        "3 non-content blocks removed (page-furniture 2, on-request 1)"
    )


def test_older_checks_keep_their_drop_over_page_furniture():
    # A CV title and a date stamp were dropped under their own reasons before
    # #1221; the furniture check runs after them and claims neither.
    assert _sig("Curriculum Vitae", None) == DROP_SOURCE_BOILERPLATE
    assert _sig("As of 02/14/2017", _confirmed("Date stamp.")) == DROP_DATE_STAMP


def test_fill_appendix_drops_the_owners_running_header(tmp_path, caplog):
    header = _t_entry("Jane Q. Doe Curriculum Vitae 21", ["Publications"], 3)
    keeper = _t_entry("Example Leftover Society Membership, 2015", ["Publications"], 4)
    doc = Document(str(_render(tmp_path, [_NAME_ENTRY, header, keeper], caplog,
                               cv_owner=_FAKE_OWNER)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Example Leftover Society Membership" in text
    assert "Curriculum Vitae 21" not in text


# ---------------------------------------------- #1431: furniture after #1364
# Shapes the NDMRSO batch found surviving in the Appendix. Invented text only.

def _reclassified(kind_sentence):
    return f"[T-validation reclassified from T] {kind_sentence}"


@pytest.mark.parametrize("text", [
    "· · · · · · · · · ·", "·  ·  ·  ·  ·", "•  •  •  •  •", "…  …  …  …  …",
])
def test_spaced_dot_leader_is_a_rule_line_when_t_coded(text):
    assert _sig(text, None) == DROP_RULE_LINE
    assert _sig(text, None, code="K1") is None


@pytest.mark.parametrize("text", ["· · · ·", "· · x · · ·", "Sample · · · · ·", ". . . . . 12"])
def test_short_or_mixed_dot_leaders_survive(text):
    assert _sig(text, None) != DROP_RULE_LINE


@pytest.mark.parametrize("text, sentence", [
    ("7/16/18", "Stray date fragment with no context."),
    ("As of 21 April 2023", "Date marker indicating the CV update date."),
    ("(As of 3rd March 2019)", "Revision date stamp."),
    ("(As of 7/16/18)", "Date stamp."),
])
def test_two_digit_year_and_day_first_date_stamps_are_dropped(text, sentence):
    assert _sig(text, _confirmed(sentence)) == DROP_DATE_STAMP


@pytest.mark.parametrize("text", [
    "7-16-18",  # hyphens with a two-digit year read as a range
    "7/16/18 Sample Annual Meeting",
    "21 April 2023 Example Society Lecture",
    "7/16",
])
def test_new_date_shapes_keep_a_record_or_a_range(text):
    assert _sig(text, _confirmed("Stray date fragment.")) is None


@pytest.mark.parametrize("text, sentence, code, reason", [
    ("Period Fellowship/Scholarship Project Grad Student",
     "Table header for graduate student awards under student supervision.", "N3", DROP_COLUMN_HEADER),
    ("Semester Project Undergraduate",
     "Table header for undergraduate student research activities.", "N3", DROP_COLUMN_HEADER),
    ("SAMPLE PUBLICATIONS (CONTINUED)",
     "Continuation header for the Sample Publications section.", "S1", DROP_PAGE_FURNITURE),
    ("Sample Talks (cont'd)", "Continued heading for sample talks.", "R", DROP_PAGE_FURNITURE),
    ("Professional Experience", "Section header for experience entries.", "B1", DROP_SECTION_HEADER),
])
def test_structural_line_3b_moved_out_of_t_is_dropped_with_both_signals(text, sentence, code, reason):
    assert _sig(text, _reclassified(sentence), code=code) == reason


@pytest.mark.parametrize("text, reasoning, code", [
    # The reclassified verdict names no structural kind.
    ("Semester Project Undergraduate", _reclassified("Mentee research project."), "N3"),
    ("SAMPLE PUBLICATIONS (CONTINUED)", _reclassified("Publication list entry."), "S1"),
    # The kind is named but the text is a record, not that shape.
    ("Sample Project Grant 2019", _reclassified("Table header for grants."), "M2A"),
    ("Served on the sample review committee (continued)",
     _reclassified("Continuation header for service."), "P1"),
    # The structural verdict without 3b's reclassified tag is not a signal.
    ("Semester Project Undergraduate", "Table header for undergraduate research.", "N3"),
    ("Semester Project Undergraduate", _confirmed("Table header row."), "N3"),
])
def test_moved_out_of_t_keeps_what_lacks_either_signal(text, reasoning, code):
    assert _sig(text, reasoning, code=code) is None


def test_month_year_alone_drops_only_under_a_cv_date_verdict():
    cv_date = _reclassified("Dated fragment likely the CV date/preparation date.")
    assert appendix_module.reclassified_structural_reason("April 2020", cv_date) == DROP_DATE_STAMP
    assert appendix_module.reclassified_structural_reason(
        "April 2020", _reclassified("Date stamp.")) is None
    assert appendix_module.reclassified_structural_reason(
        "April 2020 Example Award", cv_date) is None
    # The T path reads the same CV-date kind; a plain stamp verdict still keeps it.
    assert _sig("April 2020", _confirmed("CV preparation date.")) == DROP_DATE_STAMP
    assert _sig("April 2020", _confirmed("Date stamp.")) is None


@pytest.mark.parametrize("text", [
    "Date of this résumé: February 9, 2009",
    "Date of this resume: 2/9/2009",
    "Résumé updated March 2011",
])
def test_resume_date_line_is_page_furniture(text):
    assert _sig(text, None) == DROP_PAGE_FURNITURE


@pytest.mark.parametrize("text", [
    "Résumé writing workshop, Example University, 2009",
    "Date of this sample review: February 9, 2009",
])
def test_resume_word_needs_a_furniture_only_line(text):
    assert _sig(text, None) != DROP_PAGE_FURNITURE


@pytest.mark.parametrize("text", [
    "Example University School of Medicine\tStandardized Curriculum Vitae\tJane Q. Doe, MD",
    "Example University School of Medicine: Standardized Curriculum Vitae — Jane Q. Doe, MD",
    "Curriculum Vitae | Jane Q. Doe, MD",
    "Example Institute | Curriculum Vitae | Page 3",
])
def test_running_title_cells_are_page_furniture(text):
    assert appendix_module.is_page_furniture(text, _OWNER_TOKENS)


@pytest.mark.parametrize("text, tokens", [
    # Not the owner's name.
    ("Example University\tCurriculum Vitae\tPat Roe, MD", _OWNER_TOKENS),
    ("Example University\tCurriculum Vitae\tJane Q. Doe, MD", OwnerTokens()),
    # No CV-title cell, two leftover cells, a digit in the leftover, four cells.
    ("Example University\tSample Department\tJane Q. Doe, MD", _OWNER_TOKENS),
    ("Example University\tSample Lab\tCurriculum Vitae\tJane Q. Doe", _OWNER_TOKENS),
    ("Example Hall Room 12\tCurriculum Vitae\tJane Q. Doe", _OWNER_TOKENS),
    ("Sample Department\tExample University\tCurriculum Vitae", _OWNER_TOKENS),
])
def test_running_title_needs_a_title_cell_and_an_owner_or_page_cell(text, tokens):
    assert not appendix_module.is_page_furniture(text, tokens)
