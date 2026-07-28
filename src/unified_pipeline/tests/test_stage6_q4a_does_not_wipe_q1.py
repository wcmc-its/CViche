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
    table = _Doc().add_table(rows=3, cols=2)
    gen._clear_table_data(table, keep_header=True)
    assert len(table.rows) == 1, "the header row must survive the clear"
    assert id(table._element) in gen._cleared_tables, \
        "clearing must be recorded, or the guard cannot fire"


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
