"""Regression guard for issue #454: Q4A destroyed the Q1 Leadership table.

`_fill_other_service` mapped Q4A (Editor-in-Chief / Senior Editor / Co-Editor,
stage_3b:807) to the anchor 'EXTRAMURAL PROFESSIONAL RESPONSIBILITIES'. That
resolves to the top-level Q-section header, whose first following table is
'Leadership in Extramural Organizations' -- the table `_fill_extramural_leadership`
had filled with Q1 twenty lines earlier in the same function. The
`_clear_table_data` that follows then wiped every Q1 row, and because Q1 is in
`mapped_codes` the wiped entries were excluded from the appendix safety net too:
silent total loss, the same shape as the M1 bug fixed in #317.

Measured over the 10 corpus CVs carrying both codes: with Q4A present, 23 of 64
Q1 organizations reached the Leadership table and 29 of 64 survived anywhere in
the document. After the fix, 64/64 on both counts.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_q4a_does_not_wipe_q1.py -p no:cacheprovider

Self-contained: no DB, no network, no PII.
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

Q1_ORG = "Society For Distinctive Clinical Trials"
Q4A_JOURNAL = "Distinctive Journal Of Widget Medicine"


def _entry(code, **fields):
    return {"text": " ".join(str(v) for v in fields.values()),
            "taxonomy_code": code, "element_idx_start": 0,
            "extracted_fields": fields}


def _render(tmp_path, entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": "TESTAA", "entries": entries}))
    gen.generate(str(ip), str(op), research_summary_path=None)
    doc = Document(str(op))
    text = "\n".join([p.text for p in doc.paragraphs] +
                     [c.text for t in doc.tables for r in t.rows for c in r.cells])
    lead = []
    for t in doc.tables:
        rows = [" | ".join(c.text for c in r.cells) for r in t.rows]
        head = rows[0].lower() if rows else ""
        if "organization" in head and "role" in head and "date" in head:
            lead.append("\n".join(rows))
    return text, "\n".join(lead)


def _q1_and_q4a():
    return [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization=Q1_ORG, role="Treasurer", dates="1992-1997"),
        _entry("Q4A", journal_name=Q4A_JOURNAL, role="Editor-in-Chief",
               dates="2001-2017"),
    ]


def test_q1_survives_when_the_cv_also_has_q4a(tmp_path):
    """The bug: one Q4A entry wiped every Q1 row and they went nowhere else."""
    text, lead = _render(tmp_path, _q1_and_q4a())
    assert Q1_ORG in lead, "Q4A wiped the Q1 Leadership table (#454)"
    assert Q1_ORG in text, "Q1 content vanished from the whole document"


def test_q4a_still_renders(tmp_path):
    """Fixing the wipe must not silently drop Q4A instead."""
    text, _ = _render(tmp_path, _q1_and_q4a())
    assert Q4A_JOURNAL in text, "Q4A content was lost by the reroute"


def test_q4a_does_not_land_in_the_leadership_table(tmp_path):
    """Q4A is editorial; the Leadership table belongs to Q1."""
    _, lead = _render(tmp_path, _q1_and_q4a())
    assert Q4A_JOURNAL not in lead, \
        "Q4A is still being written into the Q1 Leadership table"


def test_q1_alone_is_unaffected(tmp_path):
    """The 50 corpus CVs with Q1 and no Q4A must render exactly as before."""
    text, lead = _render(tmp_path, [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization=Q1_ORG, role="Treasurer", dates="1992-1997"),
    ])
    assert Q1_ORG in lead and Q1_ORG in text


def test_clearing_is_recorded_so_a_second_filler_cannot_wipe_the_first():
    """The backstop: any two codes resolving to one table, not just Q4A/Q1."""
    from docx import Document as _Doc
    gen = WCMTemplateGenerator(verbose=False)
    assert gen._cleared_tables == set(), "must start empty per render"
    doc = _Doc()
    doc.add_table(rows=3, cols=2)
    gen._clear_table_data(doc.tables[0], keep_header=True)
    # A FRESH lookup, as `_fill_other_service` makes, with nothing else holding
    # the first `Table`. Keyed on `id(table._element)` this was False: lxml
    # freed the first proxy and built a new one with a new id (#454, GNONHA).
    again = doc.tables[0]
    assert len(again.rows) == 1, "the header row must survive the clear"
    assert again._element in gen._cleared_tables, \
        "clearing must be recorded by element identity, or the guard cannot fire"


Q4_DATE_ONLY = "2012-Present"
Q4_ROLE = "Distinctive Widget Advisory Panelist"


def _q1_and_q4():
    """A Q1 record plus plain Q4 entries, one of them a bare date line.

    Q4's anchors ('EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', 'Leadership in
    Extramural') resolve to the table Q1 has just filled -- the GNONHA shape.
    """
    return [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization=Q1_ORG, role="Treasurer", dates="1992-1997"),
        _entry("Q4", role=None, journal_name=None, start_date="2012",
               end_date="present"),
        _entry("Q4", role=Q4_ROLE, start_date="2015", end_date="2016"),
    ]


def test_q1_survives_a_plain_q4_entry_on_the_same_table(tmp_path):
    """The reopened #454: one bare-date Q4 entry cleared every Q1 row (GNONHA)."""
    text, lead = _render(tmp_path, _q1_and_q4())
    assert Q1_ORG in lead, "a Q4 entry wiped the Q1 Leadership table (#454)"
    assert Q4_ROLE in text and Q4_DATE_ONLY in text, \
        "keeping Q1 must not drop the Q4 entries instead"


def test_the_backstop_fires_when_q4_resolves_to_the_q1_table(tmp_path, caplog):
    """The guard is live in a full render, not only against a held proxy."""
    import logging
    with caplog.at_level(logging.WARNING):
        _render(tmp_path, _q1_and_q4())
    assert any("already filled by another code" in r.getMessage()
               for r in caplog.records), "the #454 backstop never fired"


def test_no_section_filler_clears_a_table_behind_the_guard():
    """Every stage6/sections clear goes through `self._clear_table_data`.

    The bare module-level function skips the bookkeeping, so the backstop in
    `_fill_other_service` cannot see the table was filled. That is how the
    #454 guard went dead when the fillers moved into stage6/sections/.
    """
    import ast
    sections = _SRC / "unified_pipeline" / "stage6" / "sections"
    offenders = []
    for path in sorted(sections.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            # Importing it at all (aliased or not) is the bypass; a bare-name
            # call is the same bypass from a name defined some other way.
            imported = isinstance(node, ast.ImportFrom) and any(
                a.name == "_clear_table_data" for a in node.names)
            called = (isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name)
                      and node.func.id == "_clear_table_data")
            if imported or called:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"bare _clear_table_data( bypasses the #454 guard: {offenders}"


def test_the_backstop_can_actually_log_when_it_fires():
    """The guard's own warning must not be the thing that crashes the run.

    `logger.warning(...)` in `_fill_other_service` was the module's only use of
    a name the module never defined, so firing the backstop raised NameError --
    and stage 6 raising fails the whole run in the web orchestrator, which is
    strictly worse than the wipe the backstop exists to prevent. No other test
    catches it, because with Q4A rerouted the branch is designed never to run.
    """
    import unified_pipeline.stage_6_word_template as s6
    s6.logger.warning("backstop smoke check, %r", "arg")


if __name__ == "__main__":
    import tempfile
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            if _fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    _fn(Path(d))
            else:
                _fn()
    print("OK")
