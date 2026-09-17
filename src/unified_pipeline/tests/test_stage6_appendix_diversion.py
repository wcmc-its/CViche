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

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage6.sections.appendix import (  # noqa: E402
    REASON_NO_RENDER_ROUTE,
    REASON_RECOVERED_UNRENDERED,
    REASON_RENDERER_DECLINED,
    _appendix_diversion_reason,
    build_appendix_diversion_warnings,
)
from unified_pipeline.stage6.sections.passthrough import (  # noqa: E402
    PASSTHROUGH_CODES,
    PassthroughSection,
)
from unified_pipeline.stage_6_word_template import (  # noqa: E402
    RENDER_ROUTED_CODES,
    WCMTemplateGenerator,
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


def test_build_warnings_two_unrouted_codes_sorted_by_code():
    written = [_entry("ZZ"), _entry("M4A"), _entry("M4A")]
    warnings = build_appendix_diversion_warnings(written, [], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [w["code"] for w in warnings] == ["M4A", "ZZ"]
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


def test_build_warnings_recovered_codes_produce_recovered_unrendered_reason():
    warnings = build_appendix_diversion_warnings([], ["D1", "D1"], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
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
    w = build_appendix_diversion_warnings([], ["D1"], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)[0]
    assert w["message"] == (
        "D1: 1 entry classified D1 was not found in the rendered document "
        "and was recovered into the Appendix")


def test_build_warnings_same_code_both_streams_two_warnings_sorted():
    # A code with BOTH a numbered warning (no_render_route) and a recovered
    # one (recovered_unrendered) yields two distinct (code, reason) rows,
    # sorted with reason as the tiebreak -- "no_render_route" < "recovered_
    # unrendered" alphabetically.
    warnings = build_appendix_diversion_warnings([_entry("T"), _entry("T")], ["T"], RENDER_ROUTED_CODES, PASSTHROUGH_CODES)
    assert [(w["code"], w["reason"], w["count"]) for w in warnings] == [
        ("T", REASON_NO_RENDER_ROUTE, 2),
        ("T", REASON_RECOVERED_UNRENDERED, 1),
    ]


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
               _t_entry("M4A_ONE Phase II interventional trial of a novel "
                        "analgesic in postoperative pain", "M4A", ["Clinical Trials"], 2),
               _t_entry("M4A_TWO Phase III device trial evaluating a wearable "
                        "cardiac monitor", "M4A", ["Clinical Trials"], 3)]
    _doc, sidecar = _render(tmp_path, "T531B", entries)
    diversions = _diversion_warnings(sidecar)
    assert [w["code"] for w in diversions] == ["M4A", "ZZ"]
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
        "evidence": [],
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
               _t_entry("ZZ_ONE reviewed grant applications for the Foundation "
                        "for Anesthesia Education and Research", "ZZ", ["Peer Review"], 1)]
    _doc, sidecar = _render(tmp_path, "T531I", entries)
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
         "evidence": []},
    ]


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
        "evidence": [],
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
