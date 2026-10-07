"""Per-(code, reason) Appendix-diversion warnings (#531, #531-R2): stage 6
knows exactly which taxonomy codes it diverted to `T. APPENDIX`, how many
entries each carried, and why -- and until now threw all of it away.

Two writers into `T. APPENDIX`, both made to report back what they wrote:
- `_fill_appendix` reports the entries it wrote as NUMBERED lines (post
  `_appendix_drop_reason` filtering) -- reason is `no_render_route` (no
  section routed for the code), or `renderer_declined` (a section IS routed
  -- E/G/J's passthrough writer or the M1 research-summary path -- but
  declined this run's entries).
- `_add_remaining_to_appendix` reports the taxonomy code of each "bullet"
  line it writes on behalf of `_reconsider_appendix_entries` /
  `_recover_unrendered_records` -- reason is always `recovered_unrendered`
  (#531-R2 finding F1): these exist because a specific record did not
  render, independent of whether its code is routed at all.

`generate()` merges both into one `appendix_diversion` warning per
(code, reason), appended to the same `validation_issues` list
`_validate_output()` returns, before the `<uid>_render_warnings.json`
sidecar is written.

Two layers:
- pure-function tests against `build_appendix_diversion_warnings` /
  `_appendix_diversion_reason` -- no document, no pipeline.
- wire tests that drive the real `generate()` path against the bundled WCM
  template (as test_stage6_appendix_header_row_filter.py and
  test_stage6_passthrough_appendix_exclusion.py do) and read the actual
  sidecar JSON written to disk -- the ticket's self-consistency contract
  only means anything if the count comes from the real write path, not a
  hand-built dict. `_reconsider_appendix_entries` is neutralized (LLM-driven,
  irrelevant to this feature, and not reachable without credentials) so
  every render here is deterministic and credential-free --
  `test_recovered_unrendered_*` drives the real `_add_remaining_to_appendix`
  directly through that same override, with a synthetic (line, code,
  coverage) batch, rather than the unreachable LLM segmentation path.
  `recover_unrendered_records=False` isolates most tests here from the
  separate #221 post-render recovery pass, which can also append to
  `T. APPENDIX` via `_add_remaining_to_appendix` (see the module docstring
  in appendix.py and B-531's report for why that pass cannot MOVE what
  `_fill_appendix` already wrote, only add more) -- the F1 tests turn it
  back on deliberately to exercise that second writer.

Self-contained: no DB, no network, no PII. Synthetic entries only.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_appendix_diversion.py -p no:cacheprovider
"""

import json
import re
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY  # noqa: E402
from unified_pipeline.stage6.sections.appendix import (  # noqa: E402
    CODE_ORIGIN_RECONSIDER,
    REASON_NO_RENDER_ROUTE,
    REASON_RECOVERED_UNRENDERED,
    REASON_RENDERER_DECLINED,
    SEVERITY_INFO,
    SEVERITY_WARN,
    RecoveredLine,
    _appendix_diversion_reason,
    _diversion_message,
    _owner_signature_tokens,
    build_appendix_diversion_warnings,
    is_routine_recovered_line,
)
from unified_pipeline.stage6.sections.passthrough import (  # noqa: E402
    PASSTHROUGH_CODES,
    PassthroughSection,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RENDER_ROUTED_CODES,
    WCMTemplateGenerator,
    rendered_extraction_coverage,
)

_APPENDIX_HEADER = "T. APPENDIX"


# ------------------------------------------------------------- pure-function

# "ZZ" stands in for "some taxonomy code with no renderer at all" everywhere
# below. This suite originally used N2 for that role; #840 gave N2 its own
# training-grants renderer (added it to RENDER_ROUTED_CODES), which flipped
# every one of these fixtures from no_render_route to renderer_declined
# under a real code's dispatch. "ZZ" (same placeholder test_stage4_schemas.py
# uses for "not a real taxonomy code") can never be assigned a renderer, so
# it will not go stale the way N2 did.

def test_reason_no_render_route_for_a_code_never_in_render_routed_codes():
    assert _appendix_diversion_reason("ZZ", RENDER_ROUTED_CODES, PASSTHROUGH_CODES) == REASON_NO_RENDER_ROUTE


def test_reason_renderer_declined_for_a_code_in_render_routed_codes():
    # M1 IS in RENDER_ROUTED_CODES; it only reaches _fill_appendix at all
    # when generate()'s per-call mapped_codes copy discarded it (the M1
    # no-research-summary case) -- so any entry `build_appendix_diversion_
    # warnings` sees under a RENDER_ROUTED_CODES member is exactly that case.
    assert _appendix_diversion_reason("M1", RENDER_ROUTED_CODES, PASSTHROUGH_CODES) == REASON_RENDERER_DECLINED


def test_reason_renderer_declined_for_a_passthrough_code_not_in_render_routed_codes():
    # F2: E/G/J are NOT in RENDER_ROUTED_CODES (no taxonomy-code dispatch of
    # their own) but must still classify as renderer_declined, not
    # no_render_route -- _fill_passthrough_sections IS their renderer.
    for code in ("E", "G", "J"):
        assert _appendix_diversion_reason(code, RENDER_ROUTED_CODES, PASSTHROUGH_CODES) == REASON_RENDERER_DECLINED
        assert code not in RENDER_ROUTED_CODES  # the premise F2 fixes


def _entry(code: str) -> dict:
    return {"taxonomy_code": code}


def test_build_warnings_one_unrouted_code_three_entries():
    written = [_entry("ZZ"), _entry("ZZ"), _entry("ZZ")]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["check"] == "appendix_diversion"
    assert w["code"] == "ZZ"
    assert w["section"] == "T. APPENDIX"
    assert w["count"] == 3
    assert w["reason"] == REASON_NO_RENDER_ROUTE
    assert w["message"] == (
        "ZZ: 3 entries diverted to the Appendix — "
        "no stage 6 section is routed to render this taxonomy code")
    assert w["evidence"] == []


def test_build_warnings_renderer_declined_for_non_m1_routed_code_names_no_writer():
    # #842 r2: a routed, non-passthrough, non-M1 code (Q2 -- Service, not
    # the research-summary path) that reaches REASON_RENDERER_DECLINED must
    # NOT get M1's "no research summary rendered" text -- that would
    # misdescribe a Service-section failure as a research-summary one.
    written = [_entry("Q2"), _entry("Q2")]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "Q2"
    assert w["count"] == 2
    assert w["reason"] == REASON_RENDERER_DECLINED
    assert w["message"] == (
        "Q2: 2 entries diverted to the Appendix — not placed by the section "
        "routed for Q2 (see any section_render_failed record for that section)")


def test_build_warnings_two_unrouted_codes_sorted_by_code():
    written = [_entry("ZZ"), _entry("N3"), _entry("N3")]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [w["code"] for w in warnings] == ["N3", "ZZ"]
    assert [w["count"] for w in warnings] == [2, 1]
    assert all(w["reason"] == REASON_NO_RENDER_ROUTE for w in warnings)


def test_build_warnings_empty_written_list_returns_no_warnings():
    assert build_appendix_diversion_warnings([], [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES) == []


def test_build_warnings_evidence_always_empty_never_entry_text():
    written = [{"taxonomy_code": "ZZ", "text": "some real CV sentence"}]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert warnings[0]["evidence"] == []
    assert "some real CV sentence" not in json.dumps(warnings)


def test_build_warnings_entry_with_no_taxonomy_code_groups_under_question_mark():
    # F5: the "?" bucket -- entry.get("taxonomy_code") is falsy, not KeyError.
    # "?" is in neither PASSTHROUGH_CODES nor RENDER_ROUTED_CODES, so it
    # reads as no_render_route, same as any other never-routed code.
    warnings = build_appendix_diversion_warnings([{"text": "no code here"}], [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert len(warnings) == 1
    assert warnings[0]["code"] == "?"
    assert warnings[0]["reason"] == REASON_NO_RENDER_ROUTE


def _recovered(*codes: str) -> list[RecoveredLine]:
    return [RecoveredLine(code, f"Sample recovered line {i}") for i, code in enumerate(codes)]


def test_build_warnings_recovered_codes_produce_recovered_unrendered_reason():
    warnings = build_appendix_diversion_warnings([], _recovered("D1", "D1"), RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "D1"
    assert w["count"] == 2
    assert w["reason"] == REASON_RECOVERED_UNRENDERED
    assert w["message"] == (
        "D1: 2 entries classified D1 were not found in the rendered "
        "document and were recovered into the Appendix")


def test_build_warnings_recovered_singular_count_is_grammatical():
    # F5 singular case, for the recovered_unrendered message's verb agreement.
    w = build_appendix_diversion_warnings([], _recovered("D1"), RENDER_ROUTED_CODES, PASSTHROUGH_CODES)[0]
    assert w["message"] == (
        "D1: 1 entry classified D1 was not found in the rendered document "
        "and was recovered into the Appendix")


def test_build_warnings_same_code_both_streams_two_warnings_sorted():
    # A code with BOTH a numbered warning (no_render_route) and a recovered
    # one (recovered_unrendered) yields two distinct (code, reason) rows,
    # sorted with reason as the tiebreak -- "no_render_route" < "recovered_
    # unrendered" alphabetically.
    warnings = build_appendix_diversion_warnings([_entry("T"), _entry("T")], _recovered("T"),
                                                 RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [(w["code"], w["reason"], w["count"]) for w in warnings] == [
        ("T", REASON_NO_RENDER_ROUTE, 2),
        ("T", REASON_RECOVERED_UNRENDERED, 1),
    ]


def test_build_warnings_reconsider_coded_lines_do_not_read_as_stage_3b():
    """#1225: a segment the stage 6 reconsider pass split off an overflow
    entry and coded K4 is not an entry stage 3b classified K4. Its warning
    names the pass, and a K4 line whose code IS the entry's own keeps the
    original wording in a warning of its own."""
    recovered = [RecoveredLine("K4", "Course director duties", CODE_ORIGIN_RECONSIDER),
                 RecoveredLine("K4", "Textbook chapter work", CODE_ORIGIN_RECONSIDER),
                 RecoveredLine("K4", "CME course, 2019")]
    warnings = build_appendix_diversion_warnings([], recovered, RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [(w["code"], w["reason"], w["count"], w["message"]) for w in warnings] == [
        ("K4", REASON_RECOVERED_UNRENDERED, 1,
         "K4: 1 entry classified K4 was not found in the rendered document "
         "and was recovered into the Appendix"),
        ("K4", REASON_RECOVERED_UNRENDERED, 2,
         "K4: 2 segments of overflow content that the stage 6 reconsider pass "
         "coded K4 were not placed in a section and were recovered into the Appendix"),
    ]


def test_reconsider_message_singular_is_grammatical():
    assert _diversion_message("K5", 1, REASON_RECOVERED_UNRENDERED, PASSTHROUGH_CODES,
                              CODE_ORIGIN_RECONSIDER) == (
        "K5: 1 segment of overflow content that the stage 6 reconsider pass "
        "coded K5 was not placed in a section and was recovered into the Appendix")


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
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1),
               _t_entry("ZZ_TWO served as ad hoc reviewer for the Journal of "
                        "Perioperative Medicine manuscripts", "ZZ", ["Peer Review"], 2),
               _t_entry("ZZ_THREE participated in the National Institutes of "
                        "Health study section panel review", "ZZ", ["Peer Review"], 3)]
    doc, sidecar = _render(tmp_path, "T531A", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "ZZ"
    assert diversions[0]["count"] == 3
    assert diversions[0]["reason"] == REASON_NO_RENDER_ROUTE
    assert len(_appendix_numbered_lines(doc)) == 3


def test_positive_two_unrouted_codes_sorted_by_code(tmp_path):
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1),
               _t_entry("N3_ONE mentored an invented graduate student on a "
                        "thesis about postoperative pain", "N3", ["Mentees"], 2),
               _t_entry("N3_TWO mentored an invented postdoctoral fellow on "
                        "wearable cardiac monitors", "N3", ["Mentees"], 3)]
    _doc, sidecar = _render(tmp_path, "T531B", entries)
    diversions = _diversion_warnings(sidecar)
    assert [w["code"] for w in diversions] == ["N3", "ZZ"]
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
        "M1: 1 entry diverted to the Appendix — no research summary rendered")


def test_dropped_entries_blank_boilerplate_header_are_not_counted(tmp_path):
    # One genuine ZZ entry plus three that _appendix_drop_reason removes
    # before anything is written: blank, source-boilerplate, column-header.
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_GENUINE reviewed grant applications for the "
                        "Foundation for Anesthesia Research", "ZZ", ["Peer Review"], 1),
               _t_entry("Curriculum Vitae", "ZZ", ["Peer Review"], 2),
               _t_entry("Title | Institution/Location | Dates", "ZZ", ["Peer Review"], 3),
               _t_entry("", "ZZ", ["Peer Review"], 4)]
    doc, sidecar = _render(tmp_path, "T531D", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "ZZ"
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


# ------------------------------------------------ F1: recovered_unrendered

def test_recovered_unrendered_one_routed_code_bullet(tmp_path):
    """A bullet `_add_remaining_to_appendix` writes on behalf of the
    overflow-reconsider pass reports back as one `recovered_unrendered`
    warning (#531-R2 finding F1) -- driven directly (real LLM segmentation
    needs credentials this suite doesn't have); the code (D1) IS in
    RENDER_ROUTED_CODES, proving the reason is independent of routing."""
    entries = [_OWNER_ENTRY,
               _t_entry("Distinguished Teaching Award 2020 from the medical school",
                        "H", ["Honors"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: gen._add_remaining_to_appendix(
        [("SYNTHETIC_RECOVERED_D1 grant renewal record", "D1", 0.0)])
    data = {"document_uid": "T531K", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531K_render_warnings.json").read_text())
    diversions = _diversion_warnings(sidecar)
    assert diversions == [{
        "check": "appendix_diversion", "code": "D1", "section": "T. APPENDIX",
        "count": 1, "reason": REASON_RECOVERED_UNRENDERED,
        "message": ("D1: 1 entry classified D1 was not found in the rendered "
                    "document and was recovered into the Appendix"),
        "evidence": [], "severity": SEVERITY_WARN,
    }]


def test_recovered_unrendered_mixed_with_numbered_same_code_two_warnings_sorted(tmp_path):
    """One ZZ entry reaches `_fill_appendix` as a numbered line
    (`no_render_route`) while a synthetic ZZ bullet is separately recovered
    (`recovered_unrendered`) -- two distinct warnings for the SAME code,
    sorted by (code, reason) so they are adjacent (#531-R2 finding F1)."""
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: gen._add_remaining_to_appendix(
        [("SYNTHETIC_RECOVERED_ZZ record line", "ZZ", 0.0)])
    data = {"document_uid": "T531L", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531L_render_warnings.json").read_text())
    diversions = _diversion_warnings(sidecar)
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("ZZ", REASON_NO_RENDER_ROUTE, 1),
        ("ZZ", REASON_RECOVERED_UNRENDERED, 1),
    ]


def test_recovered_unrendered_no_bullets_no_such_warning(tmp_path):
    """`_reconsider_appendix_entries` bulleting nothing (an empty
    `_add_remaining_to_appendix` batch) produces no `recovered_unrendered`
    warning at all -- the merge is purely additive per stream, same as the
    existing no-appendix-at-all negative case."""
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: gen._add_remaining_to_appendix([])
    data = {"document_uid": "T531M", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531M_render_warnings.json").read_text())
    diversions = _diversion_warnings(sidecar)
    assert [(w["code"], w["reason"]) for w in diversions] == [("ZZ", REASON_NO_RENDER_ROUTE)]
    assert all(w["reason"] != REASON_RECOVERED_UNRENDERED for w in diversions)


def test_end_to_end_generate_writes_warning_into_sidecar_json(tmp_path):
    """The sidecar file on disk, read back exactly as run_doctor reads it --
    not just the in-memory validation_issues list."""
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1)]
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
        "check": "appendix_diversion", "code": "ZZ", "section": "T. APPENDIX",
        "count": 1, "reason": REASON_NO_RENDER_ROUTE,
        "message": ("ZZ: 1 entry diverted to the Appendix — no stage 6 "
                    "section is routed to render this taxonomy code"),
        "evidence": [], "severity": SEVERITY_WARN,
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
    produced for the same entries (here: nothing, since a render with no K
    entry no longer gets the teaching check, #1221) -- proof the merge is
    purely additive, never present when there is nothing to add."""
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
    direct = gen._validate_output({entry["taxonomy_code"]: [entry] for entry in entries})
    sidecar = json.loads((tmp_path / "T531H_render_warnings.json").read_text())
    assert sidecar["warnings"] == direct
    assert _diversion_warnings(sidecar) == []


def test_negative_existing_checks_output_unchanged_when_diversion_also_fires(tmp_path):
    """A K entry the teaching section shows nothing for (its writer stubbed
    out) triggers _validate_output's real no_visible_teaching_content check;
    confirm it survives byte-for-byte alongside a new appendix_diversion
    warning, in a fixed dict order (existing checks first, since
    _validate_output runs before the appendix-diversion merge). Without the K
    entry the teaching check stays quiet (#1221)."""
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1),
               _t_entry("SAMPLE_K1 lecture series", "K1", ["Teaching"], 2)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: None
    gen._fill_teaching = lambda *_a, **_k: None
    input_path = tmp_path / "in.json"
    input_path.write_text(json.dumps({"document_uid": "T531I", "entries": entries}))
    gen.generate(str(input_path), str(tmp_path / "out.docx"), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531I_render_warnings.json").read_text())
    assert sidecar["warnings"] == [
        {"check": "no_visible_teaching_content", "code": "K",
         "section": "EDUCATIONAL CONTRIBUTIONS",
         "message": ("K (Teaching): No visible bulleted content found - may "
                     "be using track changes only"),
         "evidence": []},
        {"check": "appendix_diversion", "code": "ZZ", "section": "T. APPENDIX",
         "count": 1, "reason": REASON_NO_RENDER_ROUTE,
         "message": ("ZZ: 1 entry diverted to the Appendix — no stage "
                     "6 section is routed to render this taxonomy code"),
         "evidence": [], "severity": SEVERITY_WARN},
    ]
    _doc, k_less = _render(tmp_path, "T531J", entries[:2])
    assert [w["check"] for w in k_less["warnings"]] == ["appendix_diversion"]


# ---------------------------------------------------- R2 verifier r7 / r10

def test_recover_unrendered_records_wire_a_coded_orphan_becomes_bullet(tmp_path):
    """r7 (#531-R3, verifier finding F-R2-1): `generate()`'s
    `recovered_appendix_codes += self._recover_unrendered_records(...)` wire
    (`stage_6_word_template.py:895`) carries 204 of the corpus's 210
    `recovered_unrendered` bullets, all A-coded, via
    `_unconsumed_personal_data_batch` (personal_data.py:402's `unconsumed`
    list). Every other test in this file drives the F1 recovery path through
    `_reconsider_appendix_entries` with `recover_unrendered_records=False`;
    this one turns the REAL post-render pass on instead, with a synthetic
    A-coded entry that fills none of `_fill_personal_data`'s six contact
    slots (`work_email`/`personal_email`/`office_phone`/`cell_phone`/
    `office_address`/`home_address`) so it lands in `unconsumed` and is
    recovered with no LLM involved. `_reconsider_appendix_entries` (the
    OTHER writer into the same wire, r10's target below) is neutralized so
    this test isolates the `_recover_unrendered_records` half exactly.

    Mutant r7 (`self._recover_unrendered_records(...)` called bare, its
    return dropped) makes `recovered_appendix_codes` empty -- `diversions`
    below would be `[]` -- FAILING this test's `assert diversions == [...]`.
    """
    entries = [_t_entry("Foreign Languages: Spanish, French", "A",
                         ["Personal Data"], 0)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=True)
    gen._reconsider_appendix_entries = lambda: None
    data = {"document_uid": "T531N", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531N_render_warnings.json").read_text())
    diversions = _diversion_warnings(sidecar)
    assert diversions == [{
        "check": "appendix_diversion", "code": "A", "section": "T. APPENDIX",
        "count": 1, "reason": REASON_RECOVERED_UNRENDERED,
        "message": ("A: 1 entry classified A was not found in the rendered "
                    "document and was recovered into the Appendix"),
        "evidence": [], "severity": SEVERITY_WARN,
    }]


def test_reconsider_appendix_entries_real_tail_wires_recovered_codes(tmp_path):
    """r10 (#531-R3, verifier finding F-R2-2): the REAL
    `_reconsider_appendix_entries` tail (`stage_6_word_template.py:1968-1976`
    -- `remaining_for_appendix` -> `_add_remaining_to_appendix` -> `return
    recovered_codes`) is exercised, not replaced by a lambda as every other
    test in this file does. Only `_reclassify_entry_segments` (the LLM step)
    is stubbed, to return falsy so the deterministic post-LLM tail runs for
    real: `self._appendix_pending` is seeded directly (bypassing
    `_route_overflow_entries`, whose own low-coverage/long-text gate is a
    separate concern from this wire), so `generate()`'s real call to
    `_reconsider_appendix_entries()` drives the whole tail.

    Mutant r10 (`self._add_remaining_to_appendix(remaining_for_appendix)`
    called bare, `_reconsider_appendix_entries` returning `[]`) drops the D2
    warning below entirely -- FAILING this test.
    """
    entries = [_OWNER_ENTRY,
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1)]
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    pending_entry = {"text": "SYNTHETIC_PENDING_D2 record awaiting reconsideration",
                      "taxonomy_code": "D2", "extracted_fields": {}}
    gen._appendix_pending = [(pending_entry, 40.0)]
    gen._reclassify_entry_segments = lambda text, code: []
    data = {"document_uid": "T531O", "entries": entries}
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps(data))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T531O_render_warnings.json").read_text())
    diversions = _diversion_warnings(sidecar)
    assert [(w["code"], w["reason"], w["count"]) for w in diversions] == [
        ("D2", REASON_RECOVERED_UNRENDERED, 1),
        ("ZZ", REASON_NO_RENDER_ROUTE, 1),
    ]


def test_reconsider_coded_segments_warn_as_the_reconsider_pass(tmp_path):
    """#1225 (batch IPXFBA, HGBSCI): the reconsider pass split a D1 entry's
    duties prose into K4/K5 segments that found no anchor, and the warning
    read "K4: 7 entries classified K4", as if stage 3b had coded them. The
    real `_reconsider_appendix_entries` runs with only the LLM step stubbed
    and every anchor refused. A second pending entry whose reclassification
    fails reaches the Appendix whole under its own K4, so it keeps the
    stage 3b wording in a warning of its own."""
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    position = {"text": "SYNTHETIC_PENDING_D1 position with its duties prose",
                "taxonomy_code": "D1", "extracted_fields": {}}
    unsplit = {"text": "SYNTHETIC_PENDING_K4 continuing education course",
               "taxonomy_code": "K4", "extracted_fields": {}}
    gen._appendix_pending = [(position, 5.0), (unsplit, 40.0)]
    segments = {"D1": [("SYNTHETIC course director duties for the program", "K4"),
                       ("SYNTHETIC textbook chapters and case studies", "K4"),
                       ("SYNTHETIC outreach talks for patient groups", "K5")]}
    gen._reclassify_entry_segments = lambda text, code: segments.get(code)
    gen._insert_reconsidered_segment = lambda text, code, comment=None: False
    input_path = tmp_path / "in.json"
    input_path.write_text(json.dumps({"document_uid": "T1225", "entries": [_OWNER_ENTRY]}))
    gen.generate(str(input_path), str(tmp_path / "out.docx"), research_summary_path=None)
    sidecar = json.loads((tmp_path / "T1225_render_warnings.json").read_text())
    assert [(w["code"], w["count"], w["message"]) for w in _diversion_warnings(sidecar)] == [
        ("K4", 1, "K4: 1 entry classified K4 was not found in the rendered document "
                  "and was recovered into the Appendix"),
        ("K4", 2, "K4: 2 segments of overflow content that the stage 6 reconsider pass "
                  "coded K4 were not placed in a section and were recovered into the Appendix"),
        ("K5", 1, "K5: 1 segment of overflow content that the stage 6 reconsider pass "
                  "coded K5 was not placed in a section and was recovered into the Appendix"),
    ]



def _overflow_queued(tmp_path: Path, entry: dict) -> list[dict]:
    """The entries the real generate() queued for the low-coverage overflow."""
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    queued: list[dict] = []
    gen._reconsider_appendix_entries = lambda: queued.extend(e for e, _ in gen._appendix_pending)
    input_path = tmp_path / "in.json"
    input_path.write_text(json.dumps({"document_uid": "T1299", "entries": [_OWNER_ENTRY, entry]}))
    gen.generate(str(input_path), str(tmp_path / "out.docx"), research_summary_path=None)
    return queued


def _unrendered_records_entry(**overrides) -> dict:
    """A T entry whose two stage-4 records carry its whole text, but no
    fan-out renderer exists for T, so only its own one-word field renders."""
    words = [f"zeta{chr(97 + i % 26)}{chr(97 + i // 26)}word" for i in range(120)]
    text = " ".join(words)
    assert len(text) > 1000  # the non-K/L overflow length gate
    records = [{"title": " ".join(words[:60])}, {"title": " ".join(words[60:])}]
    entry = {"text": text, "taxonomy_code": "T", "hierarchy": ["Miscellaneous"],
             "element_idx_start": 1,
             "extracted_fields": {"title": words[0], STAGE4_RECORDS_KEY: records},
             "extraction_coverage": {"extraction_coverage_percent": 100.0,
                                     "unextracted_words": []}}
    return {**entry, **overrides}


def test_unrendered_stage4_records_do_not_lift_coverage_over_the_overflow(tmp_path):
    """#1299: stage 4's coverage counts every record it kept, but T has no
    fan-out renderer, so those records never render. Stage 6 must measure
    the entry's own fields and send it to the overflow, as before #1265."""
    queued = _overflow_queued(tmp_path, _unrendered_records_entry())
    assert [e["text"][:9] for e in queued] == ["zetaaawor"]


def test_an_entry_without_stage4_records_keeps_its_stored_coverage(tmp_path):
    entry = _unrendered_records_entry(extracted_fields={"title": "zetaaaword"})
    assert _overflow_queued(tmp_path, entry) == []


def test_rendered_extraction_coverage_recounts_only_the_entrys_own_fields():
    entry = _unrendered_records_entry()
    assert entry["extraction_coverage"]["extraction_coverage_percent"] == 100.0
    assert rendered_extraction_coverage(entry)["extraction_coverage_percent"] < 1.0
    assert rendered_extraction_coverage({"text": "x"}) is None


# ------------------------------------------------------------------ task 4

def test_passthrough_codes_matches_pinned_value_and_its_own_docstring_source():
    """Task 4 (#531-R3): `PASSTHROUGH_CODES` lives in `passthrough.py` as a
    plain literal -- the writer selects by hierarchy label, so there is no
    runtime structure to derive it from -- and is pinned here against
    `_fill_passthrough_sections`'s own "Sections handled:" docstring bullets
    (passthrough.py:339-341 -- "- E. EMPLOYMENT STATUS", "- G. ...",
    "- J. ..."). appendix.py's prior-round `_PASSTHROUGH_CODES` literal is
    deleted, so the set exists once. `appendix.py` does NOT import
    this constant -- both modules are `stage6/sections/*` peers and
    CODING_STANDARDS.md 1.3 forbids that edge -- it is threaded in as a
    parameter by `generate()` (`stage_6_word_template.py`, not a `sections/*`
    peer) instead, the same way `RENDER_ROUTED_CODES` already is; see
    `appendix.py`'s module-level comment above `_REASON_TEXT`.

    Pinned to the known triple AND cross-checked against the docstring's
    section bullets so the literal and the sections the writer documents
    cannot silently drift apart -- a bullet added, removed or reworded there
    without a matching `PASSTHROUGH_CODES` change fails this test.
    """
    assert PASSTHROUGH_CODES == frozenset({'E', 'G', 'J'})
    doc = PassthroughSection._fill_passthrough_sections.__doc__ or ""
    bullet_codes = frozenset(re.findall(r'^\s*-\s+([A-Z])\.\s', doc, re.MULTILINE))
    assert PASSTHROUGH_CODES == bullet_codes


# --------------------------------------------------------- #839: declined grants

@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_diversion_message_declined_grant_text_for_all_three_codes(code):
    """Round-3 fix for m10 (verify_r2 finding 1): the M2A/M2B/M2C decline
    message must hold for EVERY code in `_DECLINED_GRANT_CODES`, not just
    M2A -- round 2 only pinned M2A's wire test, so shrinking the frozenset
    to `{'M2A'}` survived the full suite (M2B accounts for 14 of the 20
    corpus decline hits, the majority)."""
    assert _diversion_message(code, 2, REASON_RENDERER_DECLINED, PASSTHROUGH_CODES) == (
        f"{code}: 2 entries diverted to the Appendix — declined by the "
        f"research-support renderer as too sparse to table")


_WELLFORMED_M2B_ENTRY = {
    "text": "WELLFORMED_M2B_GRANT_TOKEN", "taxonomy_code": "M2B",
    "extracted_fields": {"title": "Longitudinal Study of a Distinctive Grant Title",
                          "agency": "NIH"},
    "hierarchy": ["Research Support"], "element_idx_start": 1,
}


def test_positive_declined_sparse_m2a_entry_reaches_appendix(tmp_path):
    """#839: an M2A entry `_create_grant_table` declines as too sparse (no
    title, no substantive info, no grant number) now reaches T. APPENDIX as
    a numbered line AND fires a `renderer_declined` appendix_diversion
    warning for M2A -- previously it rendered nowhere and warned nothing."""
    entries = [_OWNER_ENTRY,
               _t_entry("SPARSE_M2A_TOKEN", "M2A", ["Research Support"], 1)]
    doc, sidecar = _render(tmp_path, "T839A", entries)
    diversions = _diversion_warnings(sidecar)
    assert len(diversions) == 1
    assert diversions[0]["code"] == "M2A"
    assert diversions[0]["count"] == 1
    assert diversions[0]["reason"] == REASON_RENDERER_DECLINED
    assert diversions[0]["message"] == (
        "M2A: 1 entry diverted to the Appendix — declined by the "
        "research-support renderer as too sparse to table")
    lines = _appendix_numbered_lines(doc)
    assert len(lines) == 1
    assert "SPARSE_M2A_TOKEN" in lines[0]


def test_wellformed_m2b_entry_does_not_seed_the_appendix(tmp_path):
    """Wire/mutant-killer: a `_create_grant_table` call that returns a real
    table (well-formed M2B entry) must leave `_declined_grant_entries` empty
    -- no Appendix line, no M2B appendix_diversion warning."""
    entries = [_OWNER_ENTRY, _WELLFORMED_M2B_ENTRY]
    doc, sidecar = _render(tmp_path, "T839B", entries)
    assert not any(w["code"] == "M2B" for w in _diversion_warnings(sidecar))
    assert _appendix_numbered_lines(doc) == []


def test_declined_grant_entries_render_after_existing_appendix_content(tmp_path):
    """Round-3 design change (lead directive, verify_r2 finding 4): a
    declined M2A/M2B/M2C entry must be APPENDED after everything else that
    reaches the Appendix by the ordinary unmapped-entries sweep, not seeded
    ahead of it -- seeding first was renumbering and reordering every
    pre-existing Appendix group (all 11 corpus uids the round-2 render gate
    touched). A T-coded (unrouted) entry and a declined M2A entry both
    exist: the T line's group must render FIRST, the M2A line's group LAST,
    and the T group keeps numbering from 1 (unaffected by the M2A group)."""
    entries = [_OWNER_ENTRY,
               _t_entry("T_UNROUTED_TOKEN", "T", ["Miscellaneous"], 1),
               _t_entry("SPARSE_M2A_TOKEN", "M2A", ["Research Support"], 2)]
    doc, sidecar = _render(tmp_path, "T839D", entries)
    lines = _appendix_numbered_lines(doc)
    assert len(lines) == 2
    assert "T_UNROUTED_TOKEN" in lines[0]
    assert lines[0].startswith("1. ")
    assert "SPARSE_M2A_TOKEN" in lines[1]
    assert lines[1].startswith("1. ")  # its own group, renumbered from 1
    diversions = _diversion_warnings(sidecar)
    assert {(w["code"], w["reason"]) for w in diversions} == {
        ("T", REASON_NO_RENDER_ROUTE), ("M2A", REASON_RENDERER_DECLINED)}


def test_declined_grant_entries_reset_between_renders(tmp_path):
    """Per-call reset (#580/#581's class of bug): a generator instance that
    renders a sparse M2A entry, then renders a second, well-formed CV, must
    not carry the first render's decline into the second render's sidecar
    or Appendix."""
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: None

    sparse_entries = [_OWNER_ENTRY,
                       _t_entry("SPARSE_M2A_TOKEN", "M2A", ["Research Support"], 1)]
    data1 = {"document_uid": "T839C1", "entries": sparse_entries}
    input1, output1 = tmp_path / "in1.json", tmp_path / "out1.docx"
    input1.write_text(json.dumps(data1))
    gen.generate(str(input1), str(output1), research_summary_path=None)

    data2 = {"document_uid": "T839C2", "entries": [_OWNER_ENTRY, _WELLFORMED_M2B_ENTRY]}
    input2, output2 = tmp_path / "in2.json", tmp_path / "out2.docx"
    input2.write_text(json.dumps(data2))
    gen.generate(str(input2), str(output2), research_summary_path=None)

    sidecar2 = json.loads((tmp_path / "T839C2_render_warnings.json").read_text())
    doc2 = Document(str(output2))
    assert not any(w["code"] == "M2A" for w in _diversion_warnings(sidecar2))
    assert all("SPARSE_M2A_TOKEN" not in p.text for p in doc2.paragraphs)
    assert _appendix_numbered_lines(doc2) == []


def test_quarantined_invalid_code_is_reported_as_invalid_code_not_no_render_route():
    # #651: stage 4 re-codes an unrecognized code to T with a marker. Stage 6
    # must say "invalid code", and keep it apart from an ordinary unrouted T.
    from unified_pipeline.stage6.sections.appendix import REASON_INVALID_CODE
    written = [
        {"taxonomy_code": "T", "taxonomy_code_quarantine_reason": "invalid_taxonomy_code",
         "original_taxonomy_code": "ZZ9"},
        {"taxonomy_code": "T", "taxonomy_code_quarantine_reason": "invalid_taxonomy_code"},
        {"taxonomy_code": "T"},
    ]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    got = {(w["code"], w["reason"]): w for w in warnings}
    assert set(got) == {("T", REASON_INVALID_CODE), ("T", REASON_NO_RENDER_ROUTE)}
    invalid = got[("T", REASON_INVALID_CODE)]
    assert invalid["count"] == 2
    assert "not a valid taxonomy code" in invalid["message"]
    assert invalid["evidence"] == []
    assert got[("T", REASON_NO_RENDER_ROUTE)]["count"] == 1


def test_t_validation_recoded_m1_is_reported_as_its_own_reason():
    # AUTOPSY-s7ab-batch-2026-10-02 class 11: an M1 entry stage 3b's
    # T-validation recoded from T reaches the Appendix for that reason, not
    # the "no research summary rendered" one an ordinary M1 entry gets.
    from unified_pipeline.stage6.sections.appendix import REASON_T_VALIDATION_RECODED
    written = [
        {"taxonomy_code": "M1", "t_validation_applied": True},
        {"taxonomy_code": "M1", "t_validation_applied": True},
        {"taxonomy_code": "M1"},
        # Recoded to a code with its own renderer: not this reason.
        {"taxonomy_code": "H", "t_validation_applied": True},
    ]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    got = {(w["code"], w["reason"]): w for w in warnings}
    assert set(got) == {("M1", REASON_T_VALIDATION_RECODED), ("M1", REASON_RENDERER_DECLINED),
                        ("H", REASON_RENDERER_DECLINED)}
    recoded = got[("M1", REASON_T_VALIDATION_RECODED)]
    assert recoded["count"] == 2
    assert recoded["message"] == (
        "M1: 2 entries diverted to the Appendix — stage 3b T-validation "
        "recoded them from T to M1, which only the research summary renders")
    assert recoded["evidence"] == []


def test_m1_entry_is_a_record_the_summary_left_out_only_when_the_summary_rendered():
    # AUTOPSY-EBYSBC-batch-2026-10-02 E27: while the research summary renders,
    # the only M1 entries in the Appendix are T-validation recodes and dated
    # records the generated summary left out. Without a summary, every M1
    # entry is there because none rendered.
    from unified_pipeline.stage6.sections.appendix import (
        REASON_M1_RECORD_NOT_IN_SUMMARY,
        REASON_T_VALIDATION_RECODED,
    )
    written = [{"taxonomy_code": "M1"}, {"taxonomy_code": "M1"},
               {"taxonomy_code": "M1", "t_validation_applied": True},
               {"taxonomy_code": "H"}]
    got = {(w["code"], w["reason"]): w for w in build_appendix_diversion_warnings(
        written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES, summary_rendered=True)}
    assert set(got) == {("M1", REASON_M1_RECORD_NOT_IN_SUMMARY), ("M1", REASON_T_VALIDATION_RECODED),
                        ("H", REASON_RENDERER_DECLINED)}
    left_out = got[("M1", REASON_M1_RECORD_NOT_IN_SUMMARY)]
    assert left_out["count"] == 2
    assert left_out["message"] == (
        "M1: 2 dated entries diverted to the Appendix — the generated research "
        "summary does not reproduce them and no other section renders them")
    assert left_out["evidence"] == []
    # #1221 lowers only routine recovered A lines to INFO; a left-out record stays WARN.
    assert left_out["severity"] == SEVERITY_WARN

    without =build_appendix_diversion_warnings(written[:2], [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [(w["code"], w["reason"]) for w in without] == [("M1", REASON_RENDERER_DECLINED)]


def test_m1_record_not_in_summary_singular_count_is_grammatical():
    # Both pronouns in the E27 message agree with a count of one.
    from unified_pipeline.stage6.sections.appendix import (
        REASON_M1_RECORD_NOT_IN_SUMMARY,
    )
    [w] = build_appendix_diversion_warnings([{"taxonomy_code": "M1"}], [], RENDER_ROUTED_CODES,
                                            PASSTHROUGH_CODES, summary_rendered=True)
    assert (w["reason"], w["count"]) == (REASON_M1_RECORD_NOT_IN_SUMMARY, 1)
    assert w["message"] == (
        "M1: 1 dated entry diverted to the Appendix — the generated research "
        "summary does not reproduce it and no other section renders it")


# ------------------------------------- #1221: the recovered A warning's severity

_SAMPLE_OWNER = {"first_name": "Jane", "middle_name": "Q.", "last_name": "Public",
                 "full_name_with_credentials": "Jane Q. Public, MD"}
_SAMPLE_OWNER_TOKENS = _owner_signature_tokens(_SAMPLE_OWNER)


@pytest.mark.parametrize("text", [
    "School: Example University School of Medicine",
    "Current affiliation: Example University",
    "URL: https://example.edu/faculty/sample",
    "www.example.org/sample-profile",
    "Contact Details:",  # a label whose value the PII pass withheld
    "Curriculum Vitae: Jane Q. Public, MD",
    "Name Jane Q. Public, MD",
    "Jane Q. Public, MD 4",
])
def test_routine_recovered_lines(text):
    assert is_routine_recovered_line(text, _SAMPLE_OWNER_TOKENS)


@pytest.mark.parametrize("text", [
    "Jane Q. Public, MD: Professor of Medicine, Example University",  # the current rank
    "School: Example University, Associate Professor of Surgery",
    "Married: Pat Sample",
    "Grandchildren: Sam, Alex",
    "Email: sample@example.org",
    "Citizenship: Exampleland",
    "January 1986",
    "Department: Sample Medicine, 2019-2021",  # a dated appointment
])
def test_recovered_lines_that_keep_the_warning(text):
    assert not is_routine_recovered_line(text, _SAMPLE_OWNER_TOKENS)


def test_owner_banner_is_routine_only_with_the_owner_tokens():
    assert not is_routine_recovered_line("Name Jane Q. Public, MD")


def test_build_warnings_recovered_a_is_info_only_when_every_line_is_routine():
    routine = [RecoveredLine("A", "School: Example University School of Medicine"),
               RecoveredLine("A", "Name Jane Q. Public, MD")]
    mixed = routine + [RecoveredLine("A", "Married: Pat Sample")]
    [info] = build_appendix_diversion_warnings(
        [], routine, RENDER_ROUTED_CODES, PASSTHROUGH_CODES, _SAMPLE_OWNER_TOKENS)
    [warn] = build_appendix_diversion_warnings(
        [], mixed, RENDER_ROUTED_CODES, PASSTHROUGH_CODES, _SAMPLE_OWNER_TOKENS)
    assert (info["count"], info["severity"]) == (2, SEVERITY_INFO)
    assert (warn["count"], warn["severity"]) == (3, SEVERITY_WARN)


def test_build_warnings_only_code_a_is_split_by_its_lines():
    # A routine-looking line under another code, or a numbered T line, stays WARN.
    warnings = build_appendix_diversion_warnings(
        [_entry("T")], [RecoveredLine("D1", "School: Example University")],
        RENDER_ROUTED_CODES, PASSTHROUGH_CODES, _SAMPLE_OWNER_TOKENS)
    assert [(w["code"], w["severity"]) for w in warnings] == [
        ("D1", SEVERITY_WARN), ("T", SEVERITY_WARN)]


def test_build_warnings_numbered_a_line_stays_warn_beside_routine_recovered_ones():
    # Only the recovered stream is split by its text: a numbered A line keeps
    # its WARN even when it counts as many lines as the routine recovered ones.
    warnings = build_appendix_diversion_warnings(
        [_entry("A")], [RecoveredLine("A", "School: Example University")],
        RENDER_ROUTED_CODES, PASSTHROUGH_CODES, _SAMPLE_OWNER_TOKENS)
    assert [(w["reason"], w["severity"]) for w in warnings] == [
        (REASON_RECOVERED_UNRENDERED, SEVERITY_INFO), (REASON_RENDERER_DECLINED, SEVERITY_WARN)]


def test_recovered_school_line_reaches_the_sidecar_as_info(tmp_path):
    """The real post-render recovery pass, cv_owner threaded from the input."""
    sidecars = {}
    for uid, extra in (("T1221A", []), ("T1221B", ["Citizenship: Exampleland"])):
        entries = [_t_entry(text, "A", ["Personal Data"], i) for i, text in enumerate(
            ["School: Example University School of Medicine", *extra])]
        gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=True)
        gen._reconsider_appendix_entries = lambda: None
        input_path = tmp_path / f"{uid}.json"
        input_path.write_text(json.dumps(
            {"document_uid": uid, "entries": entries, "cv_owner": _SAMPLE_OWNER}))
        gen.generate(str(input_path), str(tmp_path / f"{uid}.docx"), research_summary_path=None)
        sidecars[uid] = json.loads((tmp_path / f"{uid}_render_warnings.json").read_text())
    assert [(w["code"], w["count"], w["severity"]) for w in _diversion_warnings(sidecars["T1221A"])] == [
        ("A", 1, SEVERITY_INFO)]
    assert [(w["code"], w["count"], w["severity"]) for w in _diversion_warnings(sidecars["T1221B"])] == [
        ("A", 2, SEVERITY_WARN)]


def test_recovered_owner_banner_is_info_with_the_renders_cv_owner(tmp_path):
    """A banner reaching the Appendix by the reconsider pass is routine only
    because generate() hands its cv_owner to the warning builder."""
    severities = {}
    for uid, owner in (("T1221C", _SAMPLE_OWNER), ("T1221D", None)):
        gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
        gen._reconsider_appendix_entries = lambda gen=gen: gen._add_remaining_to_appendix(
            [("Name Jane Q. Public, MD", "A", 0.0)])
        payload = {"document_uid": uid, "entries": [_OWNER_ENTRY]}
        if owner:
            payload["cv_owner"] = owner
        input_path = tmp_path / f"{uid}.json"
        input_path.write_text(json.dumps(payload))
        gen.generate(str(input_path), str(tmp_path / f"{uid}.docx"), research_summary_path=None)
        sidecar = json.loads((tmp_path / f"{uid}_render_warnings.json").read_text())
        severities[uid] = [w["severity"] for w in _diversion_warnings(sidecar)]
    assert severities == {"T1221C": [SEVERITY_INFO], "T1221D": [SEVERITY_WARN]}


def test_untitled_undated_citation_goes_to_the_appendix_not_the_bibliography(tmp_path):
    """#446 (EBYSBC HFAJCC-05, NDXXAD-02): an S1 entry with no title and no
    year -- an author-only split head, or a 5d placeholder title -- is not a
    citation. It reaches the Appendix as a renderer decline, and the dated,
    titled citation beside it still renders numbered."""
    head = _t_entry("Quill A, Brandt B, HEAD_ONLY_TOKEN C,", "S1", ["Publications"], 1)
    head["extracted_fields"] = {"authors": "Quill A, Brandt B, Head C",
                                "title": "[Title not provided]",
                                "formatted_citation": "Quill A, Brandt B, Head C. [Title not provided].",
                                "formatting_source": "stage_5d_llm"}
    whole = _t_entry("Quill A. WHOLE_TOKEN study. J Imag Stud. 2019.", "S1", ["Publications"], 2)
    whole["extracted_fields"] = {"formatted_citation": "Quill A. WHOLE_TOKEN study. J Imag Stud. 2019.",
                                 "formatting_source": "stage_5d_llm", "year": "2019",
                                 "title": "WHOLE_TOKEN study"}
    doc, sidecar = _render(tmp_path, "T446A", [_OWNER_ENTRY, head, whole])
    lines = _appendix_numbered_lines(doc)
    assert len(lines) == 1 and "HEAD_ONLY_TOKEN" in lines[0]
    texts = [p.text for p in doc.paragraphs]
    assert "1. Quill A. WHOLE_TOKEN study. J Imag Stud. 2019." in texts
    assert not any("[Title not provided]" in t for t in texts)
    assert {(w["code"], w["reason"]) for w in _diversion_warnings(sidecar)} == {
        ("S1", REASON_RENDERER_DECLINED)}
