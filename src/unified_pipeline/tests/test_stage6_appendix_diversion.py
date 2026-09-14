"""Per-code Appendix-diversion warnings (#531): stage 6 knows exactly which
taxonomy codes it diverted to `T. APPENDIX`, how many entries each carried,
and why -- and until now threw all of it away. `_fill_appendix` reports back
the entries it actually wrote (post `_appendix_drop_reason` filtering), and
`generate()` turns that into one `appendix_diversion` warning per code,
appended to the same `validation_issues` list `_validate_output()` returns,
before the `<uid>_render_warnings.json` sidecar is written.

Two layers:
- pure-function tests against `build_appendix_diversion_warnings` /
  `_appendix_diversion_reason` -- no document, no pipeline.
- wire tests that drive the real `generate()` path against the bundled WCM
  template (as test_stage6_appendix_header_row_filter.py and
  test_stage6_passthrough_appendix_exclusion.py do) and read the actual
  sidecar JSON written to disk -- the ticket's self-consistency contract
  only means anything if the count comes from the real write path, not a
  hand-built dict. `_reconsider_appendix_entries` is neutralized (LLM-driven,
  irrelevant to this feature) so every render here is deterministic and
  credential-free; `recover_unrendered_records=False` isolates this feature's
  own appendix additions from the separate #221 post-render recovery pass,
  which can also append to `T. APPENDIX` via `_add_remaining_to_appendix`
  (see the module docstring in appendix.py and B-531's report for why that
  pass cannot MOVE what `_fill_appendix` already wrote, only add more).

Self-contained: no DB, no network, no PII. Synthetic entries only.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_appendix_diversion.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage6.sections.appendix import (  # noqa: E402
    REASON_NO_RENDER_ROUTE,
    REASON_RENDERER_DECLINED,
    _appendix_diversion_reason,
    build_appendix_diversion_warnings,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RENDER_ROUTED_CODES,
    WCMTemplateGenerator,
)

_APPENDIX_HEADER = "T. APPENDIX"


# ------------------------------------------------------------- pure-function

def test_reason_no_render_route_for_a_code_never_in_render_routed_codes():
    assert _appendix_diversion_reason("N2", RENDER_ROUTED_CODES) == REASON_NO_RENDER_ROUTE


def test_reason_renderer_declined_for_a_code_in_render_routed_codes():
    # M1 IS in RENDER_ROUTED_CODES; it only reaches _fill_appendix at all
    # when generate()'s per-call mapped_codes copy discarded it (the M1
    # no-research-summary case) -- so any entry `build_appendix_diversion_
    # warnings` sees under a RENDER_ROUTED_CODES member is exactly that case.
    assert _appendix_diversion_reason("M1", RENDER_ROUTED_CODES) == REASON_RENDERER_DECLINED


def _entry(code: str) -> dict:
    return {"taxonomy_code": code}


def test_build_warnings_one_unrouted_code_three_entries():
    written = [_entry("N2"), _entry("N2"), _entry("N2")]
    warnings = build_appendix_diversion_warnings(written, RENDER_ROUTED_CODES)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["check"] == "appendix_diversion"
    assert w["code"] == "N2"
    assert w["section"] == "T. APPENDIX"
    assert w["count"] == 3
    assert w["reason"] == REASON_NO_RENDER_ROUTE
    assert w["message"] == (
        "N2: 3 entries diverted to the Appendix — "
        "no stage 6 section is routed to render this taxonomy code")
    assert w["evidence"] == []


def test_build_warnings_two_unrouted_codes_sorted_by_code():
    written = [_entry("N2"), _entry("M4A"), _entry("M4A")]
    warnings = build_appendix_diversion_warnings(written, RENDER_ROUTED_CODES)
    assert [w["code"] for w in warnings] == ["M4A", "N2"]
    assert [w["count"] for w in warnings] == [2, 1]
    assert all(w["reason"] == REASON_NO_RENDER_ROUTE for w in warnings)


def test_build_warnings_empty_written_list_returns_no_warnings():
    assert build_appendix_diversion_warnings([], RENDER_ROUTED_CODES) == []


def test_build_warnings_evidence_always_empty_never_entry_text():
    written = [{"taxonomy_code": "N2", "text": "some real CV sentence"}]
    warnings = build_appendix_diversion_warnings(written, RENDER_ROUTED_CODES)
    assert warnings[0]["evidence"] == []
    assert "some real CV sentence" not in json.dumps(warnings)


# ------------------------------------------------------------------ wire tests

_OWNER_ENTRY = {"text": "Name: Jane Q. Public, MD", "taxonomy_code": "A",
                "extracted_fields": {}, "element_idx_start": 0}


def _t_entry(text: str, code: str, hierarchy: list[str], idx: int) -> dict:
    return {"text": text, "taxonomy_code": code, "extracted_fields": {},
            "hierarchy": hierarchy, "element_idx_start": idx}


def _render(tmp_path: Path, uid: str, entries: list[dict],
            research_summary_path: str | None = None) -> tuple[Document, dict]:
    """Real generate() against the bundled WCM template; returns the
    rendered Document and the sidecar dict actually written to disk."""
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": uid, "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path),
                 research_summary_path=research_summary_path)
    sidecar_path = tmp_path / f"{uid}_render_warnings.json"
    sidecar = json.loads(sidecar_path.read_text())
    return Document(str(output_path)), sidecar


def _diversion_warnings(sidecar: dict) -> list[dict]:
    return [w for w in sidecar["warnings"] if w.get("check") == "appendix_diversion"]


def _appendix_numbered_lines(doc: Document) -> list[str]:
    """Just the `N. text` lines `_fill_appendix` itself wrote -- distinct
    from `_add_remaining_to_appendix`'s `• text` bullet lines, which
    `recover_unrendered_records=False` + the neutralized reconsider pass
    keep out of these renders anyway (belt and suspenders for the count)."""
    paragraphs = [p.text for p in doc.paragraphs]
    starts = [i for i, t in enumerate(paragraphs) if t.strip() == _APPENDIX_HEADER]
    if not starts:
        return []
    tail = paragraphs[starts[0]:]
    return [t for t in tail if t[:1].isdigit() and ". " in t[:5]]


def test_positive_one_unrouted_code_three_entries(tmp_path):
    entries = [_OWNER_ENTRY,
               _t_entry("N2_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "N2", ["Peer Review"], 1),
               _t_entry("N2_TWO served as ad hoc reviewer for the Journal of "
                        "Perioperative Medicine manuscripts", "N2", ["Peer Review"], 2),
               _t_entry("N2_THREE participated in the National Institutes of "
                        "Health study section panel review", "N2", ["Peer Review"], 3)]
    doc, sidecar = _render(tmp_path, "T531A", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "N2"
    assert diversions[0]["count"] == 3
    assert diversions[0]["reason"] == REASON_NO_RENDER_ROUTE
    assert len(_appendix_numbered_lines(doc)) == 3


def test_positive_two_unrouted_codes_sorted_by_code(tmp_path):
    entries = [_OWNER_ENTRY,
               _t_entry("N2_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "N2", ["Peer Review"], 1),
               _t_entry("M4A_ONE Phase II interventional trial of a novel "
                        "analgesic in postoperative pain", "M4A", ["Clinical Trials"], 2),
               _t_entry("M4A_TWO Phase III device trial evaluating a wearable "
                        "cardiac monitor", "M4A", ["Clinical Trials"], 3)]
    _doc, sidecar = _render(tmp_path, "T531B", entries)
    diversions = _diversion_warnings(sidecar)
    assert [w["code"] for w in diversions] == ["M4A", "N2"]
    assert [w["count"] for w in diversions] == [2, 1]


def test_positive_m1_discard_path_reason_renderer_declined(tmp_path):
    # No research_summary_path and no auto-discoverable stage_4_5 dir under
    # tmp_path: _fill_research_summary(None) returns False, so generate()
    # discards M1 from its mapped_codes copy for this run (#317) and the M1
    # entry below reaches _fill_appendix -- the only case that produces
    # REASON_RENDERER_DECLINED today.
    entries = [_OWNER_ENTRY,
               _t_entry("M1_ONE Our laboratory investigates mitochondrial "
                        "dysfunction in neurodegenerative disease broadly",
                        "M1", ["Research"], 1)]
    _doc, sidecar = _render(tmp_path, "T531C", entries, research_summary_path=None)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "M1"
    assert diversions[0]["count"] == 1
    assert diversions[0]["reason"] == REASON_RENDERER_DECLINED
    assert diversions[0]["message"] == (
        "M1: 1 entries diverted to the Appendix — no research summary rendered")


def test_dropped_entries_blank_boilerplate_header_are_not_counted(tmp_path):
    # One genuine N2 entry plus three that _appendix_drop_reason removes
    # before anything is written: blank, source-boilerplate, column-header.
    entries = [_OWNER_ENTRY,
               _t_entry("N2_GENUINE reviewed grant applications for the "
                        "Foundation for Anesthesia Research", "N2", ["Peer Review"], 1),
               _t_entry("Curriculum Vitae", "N2", ["Peer Review"], 2),
               _t_entry("Title | Institution/Location | Dates", "N2", ["Peer Review"], 3),
               _t_entry("", "N2", ["Peer Review"], 4)]
    doc, sidecar = _render(tmp_path, "T531D", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "N2"
    assert diversions[0]["count"] == 1
    assert len(_appendix_numbered_lines(doc)) == 1


def test_passthrough_consumed_entries_are_not_counted(tmp_path):
    # Mirrors test_stage6_passthrough_appendix_exclusion.py's own fixture:
    # an E-coded entry the passthrough writer actually wrote must not also
    # count toward the T appendix_diversion warning (#294/#260's exclusion,
    # extended here to the new bookkeeping). Both entries carry taxonomy_code
    # 'T' (the writer selects by hierarchy label, not code, same as the
    # existing test) -- only the refused one should count.
    entries = [_OWNER_ENTRY,
               _t_entry("Name of Current Employer(s): DISTINCTIVE_E_ACCEPTED_EMPLOYER",
                        "T", ["E. EMPLOYMENT STATUS"], 1),
               _t_entry("Favorite Color: DISTINCTIVE_E_REFUSED_LABEL",
                        "T", ["E. EMPLOYMENT STATUS"], 2)]
    _doc, sidecar = _render(tmp_path, "T531E", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "T"
    assert diversions[0]["count"] == 1, (
        "the passthrough-accepted entry duplicated into the appendix_diversion count")


def test_end_to_end_generate_writes_warning_into_sidecar_json(tmp_path):
    """The sidecar file on disk, read back exactly as run_doctor reads it --
    not just the in-memory validation_issues list."""
    entries = [_OWNER_ENTRY,
               _t_entry("N2_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "N2", ["Peer Review"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "T531F", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)

    sidecar_path = tmp_path / "T531F_render_warnings.json"
    assert sidecar_path.is_file()
    on_disk = json.loads(sidecar_path.read_text())
    assert on_disk["document_uid"] == "T531F"
    diversions = [w for w in on_disk["warnings"] if w["check"] == "appendix_diversion"]
    assert diversions == [{
        "check": "appendix_diversion", "code": "N2", "section": "T. APPENDIX",
        "count": 1, "reason": REASON_NO_RENDER_ROUTE,
        "message": ("N2: 1 entries diverted to the Appendix — no stage 6 "
                    "section is routed to render this taxonomy code"),
        "evidence": [],
    }]
    assert "dedup_decisions" in on_disk  # sidecar shape otherwise unchanged


# --------------------------------------------------------------------- negative

def test_negative_routed_code_only_no_appendix_diversion_warning(tmp_path):
    entries = [_OWNER_ENTRY,
               _t_entry("Distinguished Teaching Award 2020 from the medical school",
                        "H", ["Honors"], 1)]
    doc, sidecar = _render(tmp_path, "T531G", entries)
    assert _diversion_warnings(sidecar) == []
    assert _APPENDIX_HEADER not in [p.text.strip() for p in doc.paragraphs]


def test_negative_no_appendix_at_all_warnings_unchanged(tmp_path):
    """No unrouted/discarded code in the input at all: the sidecar's
    `warnings` list is exactly what _validate_output() alone would have
    produced (here: the always-fires-on-a-K-less-render teaching check) --
    proof the merge is purely additive, never present when there is nothing
    to add."""
    entries = [_OWNER_ENTRY,
               _t_entry("Distinguished Teaching Award 2020 from the medical school",
                        "H", ["Honors"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "T531H", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    doc = Document(str(output_path))
    direct = gen._validate_output()
    sidecar = json.loads((tmp_path / "T531H_render_warnings.json").read_text())
    assert sidecar["warnings"] == direct
    assert _diversion_warnings(sidecar) == []


def test_negative_existing_checks_output_unchanged_when_diversion_also_fires(tmp_path):
    """The bundled template's own EDUCATIONAL CONTRIBUTIONS header with no K
    content triggers _validate_output's real no_visible_teaching_content
    check on every render here; confirm it survives byte-for-byte alongside
    a new appendix_diversion warning, in a fixed dict order (existing checks
    first, since _validate_output runs before the appendix-diversion merge)."""
    entries = [_OWNER_ENTRY,
               _t_entry("N2_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "N2", ["Peer Review"], 1)]
    _doc, sidecar = _render(tmp_path, "T531I", entries)
    assert sidecar["warnings"] == [
        {"check": "no_visible_teaching_content", "code": "K",
         "section": "EDUCATIONAL CONTRIBUTIONS",
         "message": ("K (Teaching): No visible bulleted content found - may "
                     "be using track changes only"),
         "evidence": []},
        {"check": "appendix_diversion", "code": "N2", "section": "T. APPENDIX",
         "count": 1, "reason": REASON_NO_RENDER_ROUTE,
         "message": ("N2: 1 entries diverted to the Appendix — no stage "
                     "6 section is routed to render this taxonomy code"),
         "evidence": []},
    ]
