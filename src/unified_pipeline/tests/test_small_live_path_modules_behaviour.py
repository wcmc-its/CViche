"""Behaviour tests for four small live-path modules (#704, packet 6):

- `stage6.sorting.document_order.element_idx_sort_key` -- the mixed-type sort
  key that makes a section with paragraphs, table blocks and table rows
  comparable without raising.
- `stage6.sections.researcher_profiles.ResearcherProfilesSection._fill_researcher_profiles`
  and `stage6.sections.research_summary.ResearchSummarySection._fill_research_summary`
  -- stage-6 section writers mixed into `WCMTemplateGenerator`. Instantiated
  the same way `test_stage6_k_list_bullets.py` / `test_m1_appendix_fallback.py`
  do (the real generator with `.doc` set directly), rather than a hand-rolled
  stub, so the helper methods these mixins call (`_apply_list_bullet`,
  `_add_track_change_insertion`, ...) are exercised as they really behave.
- `stage_4_field_extractor.process_cv` / `run_validation` -- the CLI
  orchestration wrappers. `process_cv` calls the real (module-level,
  already-imported) `extract_fields_from_mapped_entries` name, so every test
  that reaches it stubs that name directly on the
  `unified_pipeline.stage_4_field_extractor` module object -- not on
  `unified_pipeline.stage4.extraction`, which owns the implementation but
  whose globals `process_cv` never reads (the #496 lesson documented in
  `test_owner_affiliation_fallback.py`). Both functions derive their file
  paths from their own module's `__file__`, so each test rebinds that to a
  directory under `tmp_path` before calling in -- no output ever lands
  outside tmp_path, and no real corpus/`_batch_runs` file is read.

No network, no LLM: `call_llm` is never invoked (only `subprocess.run` is
stubbed, for the validator subprocess `run_validation` shells out to). No PII:
every fixture is synthetic text built in this file or under `tmp_path`.

Not covered on purpose: `stage_4_field_extractor.py`'s `if __name__ ==
"__main__":` block (its last ~30 lines, the argparse CLI entry) cannot run
from an imported module, and `research_summary.py`'s `except ValueError`
arm needs the heading paragraph to not be a direct body child, which
python-docx does not produce.

    python3 -m pytest src/unified_pipeline/tests/test_small_live_path_modules_behaviour.py -p no:cacheprovider
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_4_field_extractor as stage4_facade  # noqa: E402
from unified_pipeline.stage6.sorting.document_order import element_idx_sort_key  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# =============================================================================
# document_order.element_idx_sort_key
# =============================================================================

def test_int_and_equal_numeric_string_sort_key_match():
    assert element_idx_sort_key(5) == (5.0, 0.0)
    assert element_idx_sort_key("5") == (5.0, 0.0)


def test_row_col_dotted_string_splits_into_major_minor():
    assert element_idx_sort_key("22.2") == (22.0, 2.0)


def test_table_n_string_sorts_after_every_plain_index():
    assert element_idx_sort_key("table_3") == (1_000_003.0, 0.0)


def test_none_sorts_last():
    assert element_idx_sort_key(None) == (float("inf"), 0.0)


def test_non_numeric_string_sorts_last():
    assert element_idx_sort_key("abc") == (float("inf"), 0.0)


def test_malformed_dotted_string_sorts_last():
    # A '.' is present but neither side parses as a float -- the inner
    # ValueError branch inside the dotted-string arm, distinct from the
    # plain non-numeric-string branch above (which never enters that arm).
    assert element_idx_sort_key("a.b") == (float("inf"), 0.0)


def test_malformed_table_string_sorts_last():
    # startswith('table_') but nothing numeric follows -- the
    # (ValueError, IndexError) branch inside the table_ arm.
    assert element_idx_sort_key("table_") == (float("inf"), 0.0)
    assert element_idx_sort_key("table_x") == (float("inf"), 0.0)


def test_list_and_tuple_sort_last():
    # Unsupported types: float(value) raises TypeError, caught by the same
    # except clause as the ValueError paths above.
    assert element_idx_sort_key([1, 2]) == (float("inf"), 0.0)
    assert element_idx_sort_key((1, 2)) == (float("inf"), 0.0)


def test_mixed_list_sorts_in_a_single_total_order():
    """The whole point of the function (per the module docstring): a section
    mixing every element_idx_start shape stage 2 can write must sort without
    raising TypeError and land in one order, ties broken by original
    (stable-sort) position."""
    values = [5, "3", "table_2", None, "10.5", "abc", (1, 2)]
    ordered = sorted(values, key=element_idx_sort_key)
    assert ordered == ["3", 5, "10.5", "table_2", None, "abc", (1, 2)]


# =============================================================================
# shared stage-6 helpers
# =============================================================================

def _num_level(para) -> str | None:
    """The paragraph's w:ilvl, or None when it carries no list markup."""
    pPr = para._p.pPr
    numPr = pPr.find(qn("w:numPr")) if pPr is not None else None
    if numPr is None:
        return None
    return numPr.find(qn("w:ilvl")).get(qn("w:val"))


def _all_run_text(para) -> str:
    """Every w:t descendant's text, including runs nested inside a w:ins
    track-change wrapper -- python-docx's own Paragraph.text property skips
    text inside revision marks entirely, which would make a tracked
    insertion invisible to a plain `.text` assertion."""
    return "".join(t.text or "" for t in para._p.iter(qn("w:t")))


def _s6_generator(**kwargs) -> WCMTemplateGenerator:
    kwargs.setdefault("verbose", False)
    gen = WCMTemplateGenerator(**kwargs)
    gen.doc = Document(gen.template_path)
    return gen


# =============================================================================
# researcher_profiles.ResearcherProfilesSection._fill_researcher_profiles
# =============================================================================

def test_empty_entries_is_a_no_op():
    gen = _s6_generator()
    before = [p.text for p in gen.doc.paragraphs]
    ret = gen._fill_researcher_profiles([])
    assert ret is None
    assert [p.text for p in gen.doc.paragraphs] == before
    assert gen.stats["entries_inserted"] == 0


def test_single_entry_strips_bracketed_taxonomy_code_and_cell_separator_and_bullets():
    gen = _s6_generator()
    gen._fill_researcher_profiles([{"text": "[S0] ORCID\t0000-0001-2345-6789"}])

    paras = gen.doc.paragraphs
    entry_idx = next(
        i for i, p in enumerate(paras) if p.text == "ORCID: 0000-0001-2345-6789"
    )
    peer_idx = next(
        i for i, p in enumerate(paras) if "Peer-reviewed Research Articles" in p.text
    )
    # The bracketed "[S0] " taxonomy code is gone and the internal "\t"
    # separator was rendered as ": " by _clean_inline_tabs -- proves both
    # cleanup calls ran, not just one of them.
    assert entry_idx < peer_idx
    assert _num_level(paras[entry_idx]) == "0"
    assert gen.stats["entries_inserted"] == 1


def test_several_entries_render_in_source_order_despite_reverse_insertion():
    """Guards the `reversed(s0_entries)` loop: insert_paragraph_before always
    inserts at the same anchor and pushes the previous entry down, so walking
    entries forward (dropping the reversed()) would render them backwards."""
    gen = _s6_generator()
    gen._fill_researcher_profiles([
        {"text": "MARKER_A first"},
        {"text": "MARKER_B second"},
        {"text": "MARKER_C third"},
    ])
    order = [p.text for p in gen.doc.paragraphs if p.text.startswith("MARKER_")]
    assert order == ["MARKER_A first", "MARKER_B second", "MARKER_C third"]


def test_entry_missing_text_key_still_renders_an_empty_bulleted_paragraph():
    # entry.get('text', '').strip() silently defaults to '' rather than
    # skipping the entry or raising -- asserted as current behaviour. Not
    # fixed here: nothing upstream is known to omit 'text', but if it ever
    # does, this is what actually happens (a blank bullet, not a dropped
    # entry and not a crash).
    gen = _s6_generator()
    gen._fill_researcher_profiles([{"other_key": "no text field at all"}])

    paras = gen.doc.paragraphs
    peer_idx = next(
        i for i, p in enumerate(paras) if "Peer-reviewed Research Articles" in p.text
    )
    # The inserted (empty) entry paragraph sits directly above the trailing
    # blank spacer, which sits directly above the anchor heading.
    assert paras[peer_idx - 1].text == ""
    assert _num_level(paras[peer_idx - 2]) == "0"
    assert paras[peer_idx - 2].text == ""
    assert gen.stats["entries_inserted"] == 1


def test_verbose_prints_the_entry_count(capsys):
    gen = _s6_generator(verbose=True)
    gen._fill_researcher_profiles([{"text": "one"}, {"text": "two"}])
    assert "Filling Researcher Profiles (2 entries)..." in capsys.readouterr().out


def test_falls_back_to_bibliography_heading_when_peer_reviewed_is_absent(tmp_path):
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph("Intro line")
    doc.add_paragraph("BIBLIOGRAPHY")
    doc.add_paragraph("Trailer line")
    saved = tmp_path / "bibliography_only.docx"
    doc.save(saved)
    gen.doc = Document(saved)

    gen._fill_researcher_profiles([{"text": "MARKER_FALLBACK"}])

    texts = [p.text for p in gen.doc.paragraphs]
    intro_idx = texts.index("Intro line")
    marker_idx = texts.index("MARKER_FALLBACK")
    bib_idx = texts.index("BIBLIOGRAPHY")
    assert intro_idx < marker_idx < bib_idx
    assert gen.stats["entries_inserted"] == 1


def test_returns_without_writing_when_neither_anchor_heading_exists(tmp_path):
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph("Nothing relevant in this document")
    saved = tmp_path / "no_anchor.docx"
    doc.save(saved)
    gen.doc = Document(saved)
    before = [p.text for p in gen.doc.paragraphs]

    ret = gen._fill_researcher_profiles([{"text": "Should never appear"}])

    assert ret is None
    assert [p.text for p in gen.doc.paragraphs] == before
    assert gen.stats["entries_inserted"] == 0


# =============================================================================
# research_summary.ResearchSummarySection._fill_research_summary
# =============================================================================

_LONG_SUMMARY = "MARKER_SUMMARY " + ("substantive research narrative content word " * 3)


def test_none_data_renders_nothing():
    gen = _s6_generator()
    assert gen._fill_research_summary(None) is False
    assert gen.stats["entries_inserted"] == 0


def test_empty_text_renders_nothing():
    gen = _s6_generator()
    assert gen._fill_research_summary({"research_summary": {"text": ""}}) is False
    assert gen.stats["entries_inserted"] == 0


def test_text_under_the_50_char_floor_renders_nothing():
    short = "x" * 49
    gen = _s6_generator()
    assert gen._fill_research_summary({"research_summary": {"text": short}}) is False


def test_text_at_the_50_char_floor_clears_the_length_gate():
    exactly_50 = "y" * 50
    gen = _s6_generator()
    assert gen._fill_research_summary({"research_summary": {"text": exactly_50}}) is True


def test_missing_activities_heading_renders_nothing(tmp_path):
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph("Nothing relevant to any anchor heading in this document")
    saved = tmp_path / "no_activities.docx"
    doc.save(saved)
    gen.doc = Document(saved)

    ret = gen._fill_research_summary({"research_summary": {"text": _LONG_SUMMARY}})

    assert ret is False
    assert gen.stats["entries_inserted"] == 0


def test_summary_renders_as_a_tracked_insertion_under_research_activities():
    gen = _s6_generator()  # emit_track_changes=True is the default
    ret = gen._fill_research_summary({"research_summary": {"text": _LONG_SUMMARY}})
    assert ret is True

    paras = gen.doc.paragraphs
    hits = [p for p in paras if "MARKER_SUMMARY" in _all_run_text(p)]
    assert len(hits) == 1
    summary_para = hits[0]

    # The load-bearing detail from the module docstring: LLM-generated text
    # renders as a tracked w:ins insertion (not a plain run) attributed by
    # name, so it is never indistinguishable from the author's own words.
    ins = summary_para._p.find(qn("w:ins"))
    assert ins is not None
    assert ins.get(qn("w:author")) == "LLM Research Summary"

    activities_idx = next(
        i for i, p in enumerate(paras) if "RESEARCH ACTIVITIES" in p.text.upper()
    )
    summary_idx = paras.index(summary_para)
    # Placement is the docstring's stated purpose: heading, one blank spacer,
    # then the summary -- NOT appended at the end of the document (which
    # `activities_idx < summary_idx` alone would also accept).
    assert summary_idx == activities_idx + 2
    assert paras[activities_idx + 1].text == ""
    assert summary_idx < len(paras) - 1

    assert gen.stats["entries_inserted"] == 1
    assert gen.stats["track_changes_added"] == 1


def test_verbose_prints_a_status_line_for_each_early_exit_and_the_success_path(capsys):
    """verbose=True is the CLI-visible progress-reporting path (`run_full_pipeline.py`
    prints Warning: Stage N failed around a driver that reads none of this --
    but the section writers' own progress lines are only reachable this way).
    One test per branch's message, each paired with the return value that
    branch actually produces, so a message alone (with the wrong return)
    would not satisfy this."""
    verbose_gen = _s6_generator(verbose=True)
    assert verbose_gen._fill_research_summary(None) is False
    assert "Skipping Research Summary section (no Stage 4.5 output)" in capsys.readouterr().out

    assert verbose_gen._fill_research_summary({"research_summary": {"text": "short"}}) is False
    assert "Skipping Research Summary section (no substantive content)" in capsys.readouterr().out

    assert verbose_gen._fill_research_summary(
        {"research_summary": {"text": _LONG_SUMMARY, "word_count": 7, "generation_method": "llm_generated"}}
    ) is True
    assert "Filling Research Summary (7 words, llm_generated)..." in capsys.readouterr().out


def test_verbose_prints_a_warning_when_the_activities_heading_is_missing(tmp_path, capsys):
    gen = WCMTemplateGenerator(verbose=True)
    doc = Document()
    doc.add_paragraph("Nothing relevant to any anchor heading in this document")
    saved = tmp_path / "no_activities_verbose.docx"
    doc.save(saved)
    gen.doc = Document(saved)

    ret = gen._fill_research_summary({"research_summary": {"text": _LONG_SUMMARY}})

    assert ret is False
    assert "Could not find 'RESEARCH ACTIVITIES'" in capsys.readouterr().out


def test_track_changes_disabled_renders_summary_as_a_plain_run():
    """#153: emit_track_changes=False renders the final text directly (no
    w:ins), the fallback branch _add_track_change_insertion takes before it
    ever reaches the try/except that increments track_changes_added."""
    gen = _s6_generator(emit_track_changes=False)
    ret = gen._fill_research_summary({"research_summary": {"text": _LONG_SUMMARY}})
    assert ret is True

    hits = [p for p in gen.doc.paragraphs if "MARKER_SUMMARY" in p.text]
    assert len(hits) == 1
    assert hits[0]._p.find(qn("w:ins")) is None
    assert gen.stats["track_changes_added"] == 0
    assert gen.stats["entries_inserted"] == 1


# =============================================================================
# stage_4_field_extractor.process_cv / run_validation
# =============================================================================

def _rebind_module_file(monkeypatch, base: Path, depth: int) -> None:
    """Point stage_4_field_extractor's own `__file__` at a scratch location
    under `base` so every Path(__file__)-derived path process_cv/run_validation
    compute -- including the outputs/ directories they create -- lands inside
    `base` instead of the real repo tree.

    `depth` is the number of intermediate directories between `base` and the
    fake file: process_cv only needs one `.parent` (depth=1 is enough to keep
    everything under `base`); run_validation needs `.parent.parent.parent` to
    land exactly on `base` (its "project root"), which takes depth=2.
    """
    fake = base
    for i in range(depth):
        fake = fake / f"d{i}"
    fake = fake / "stage_4_field_extractor.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(stage4_facade, "__file__", str(fake))


def _write_stage3b(base: Path, uid: str, entries: list[dict]) -> None:
    d = base / "outputs" / "stage_3b_classified_entries"
    d.mkdir(parents=True)
    (d / f"{uid}_classified.json").write_text(json.dumps({"entries": entries}))


def _write_legacy_stage3(base: Path, uid: str, entries: list[dict]) -> None:
    d = base / "outputs" / "stage_3_taxonomy_mapping"
    d.mkdir(parents=True)
    (d / f"{uid}_mapped.json").write_text(json.dumps({"mapped_entries": entries}))


def _stub_extract_fields(monkeypatch, captured: dict | None = None) -> None:
    """Stub the name process_cv actually calls: its own module's global
    binding, not unified_pipeline.stage4.extraction (the #496 lesson)."""

    def fake(valid_entries, batch_size=10, document_uid="", cancel_check=None):
        if captured is not None:
            captured["batch_size"] = batch_size
            captured["document_uid"] = document_uid
            captured["cancel_check"] = cancel_check
            captured["valid_entries"] = valid_entries
        return {
            "entries": [
                {**e, "extracted_fields": {"STUB_MARKER": True}} for e in valid_entries
            ],
            "total_cost": 0.25,
            "total_tokens": 1234,
            "cache_read_tokens": 10,
            "cache_write_tokens": 5,
            "cv_owner": {"last_name": "Stubowner"},
            "cv_owner_location": {"city": "New York"},
            "stats": {"extracted": len(valid_entries), "skipped": 0},
        }

    monkeypatch.setattr(stage4_facade, "extract_fields_from_mapped_entries", fake)


def test_process_cv_reads_stage3b_and_filters_fragments_and_duplicates(tmp_path, monkeypatch):
    _rebind_module_file(monkeypatch, tmp_path, depth=1)
    base = Path(stage4_facade.__file__).parent
    uid = "UIDONE"
    _write_stage3b(base, uid, [
        {"text": "Keep me", "taxonomy_code": "S1"},
        {"text": "Frag", "taxonomy_code": "S1", "is_fragment": True},
        {"text": "Dup", "taxonomy_code": "S1", "is_duplicate": True},
    ])
    captured = {}
    _stub_extract_fields(monkeypatch, captured)

    result = stage4_facade.process_cv(f"{uid}.docx")

    # Fragment/duplicate filtering happened before the stub ever saw the data.
    assert captured["document_uid"] == uid
    assert captured["batch_size"] == 10
    assert [e["text"] for e in captured["valid_entries"]] == ["Keep me"]

    output = result["output"]
    assert output["document_uid"] == uid
    assert output["total_entries"] == 1
    assert output["stats"]["fragments_skipped"] == 1
    assert output["stats"]["duplicates_skipped"] == 1
    assert output["total_cost"] == 0.25
    assert output["cv_owner"] == {"last_name": "Stubowner"}
    # Only the stub's own return value stamps this marker -- proves the
    # written JSON came from extract_fields_from_mapped_entries's result, not
    # a pass-through of the raw stage-3b entries (#643 lesson: don't let a
    # fallback/pass-through path satisfy an assertion meant for the real one).
    assert output["entries"][0]["extracted_fields"]["STUB_MARKER"] is True

    on_disk = json.loads(Path(result["output_path"]).read_text())
    assert on_disk == output


def test_process_cv_raises_when_only_legacy_stage3_output_exists(tmp_path, monkeypatch):
    """#852: process_cv used to fall back to the legacy stage_3_taxonomy_mapping/
    output when stage 3b was absent, but no live driver (run_full_pipeline.py,
    orchestrator.py, scripts/) writes that directory any more, so the fallback
    was dead code that could only be reached by a hand-crafted fixture -- and
    on that unreachable path it mislabeled its own provenance, always
    stamping `source_stage: "3b"` even when the legacy branch loaded the data.
    The fallback branch is removed rather than fixed; this pins that a
    legacy-only artifact (no stage3b) now raises, same as no artifact at all."""
    _rebind_module_file(monkeypatch, tmp_path, depth=1)
    base = Path(stage4_facade.__file__).parent
    uid = "LEGACYUID"
    _write_legacy_stage3(base, uid, [{"text": "Legacy entry", "taxonomy_code": "D1"}])
    _stub_extract_fields(monkeypatch)

    with pytest.raises(FileNotFoundError):
        stage4_facade.process_cv(f"{uid}.docx")


def test_process_cv_raises_when_neither_stage_output_exists(tmp_path, monkeypatch):
    _rebind_module_file(monkeypatch, tmp_path, depth=1)
    _stub_extract_fields(monkeypatch)

    with pytest.raises(FileNotFoundError):
        stage4_facade.process_cv("NOSUCHUID.docx")


def test_process_cv_threads_cancel_check_through_to_extraction(tmp_path, monkeypatch):
    _rebind_module_file(monkeypatch, tmp_path, depth=1)
    base = Path(stage4_facade.__file__).parent
    uid = "CANCELUID"
    _write_stage3b(base, uid, [{"text": "One entry here", "taxonomy_code": "S1"}])
    captured = {}
    _stub_extract_fields(monkeypatch, captured)

    def sentinel_cancel():
        raise RuntimeError("should never actually fire in this test")

    stage4_facade.process_cv(f"{uid}.docx", cancel_check=sentinel_cancel)

    assert captured["cancel_check"] is sentinel_cancel


def test_run_validation_skips_quietly_when_the_validator_script_is_absent(tmp_path, monkeypatch, capsys):
    _rebind_module_file(monkeypatch, tmp_path, depth=2)

    def fail_if_called(args, capture_output=False, text=True):
        raise AssertionError(
            "run_validation must return before ever calling subprocess.run "
            "when the validator script is missing"
        )

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    # No validate_stage4_extraction.py written under tmp_path -- must return
    # without raising and without touching subprocess at all (proved by the
    # stub above, which would raise AssertionError if reached). If the early
    # `return` in the validator-absent branch is deleted, execution falls
    # through into the try/subprocess.run block and this test fails.
    stage4_facade.run_validation(str(tmp_path / "out.json"))

    out = capsys.readouterr().out
    assert "Skipping validation" in out
    assert "Running Validation" not in out


def test_run_validation_invokes_the_validator_with_the_output_path(tmp_path, monkeypatch):
    _rebind_module_file(monkeypatch, tmp_path, depth=2)
    validator = tmp_path / "validate_stage4_extraction.py"
    validator.write_text("# stub validator, never actually executed\n")
    calls = []

    def fake_run(args, capture_output=False, text=True):
        calls.append(args)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    out_path = str(tmp_path / "out.json")

    stage4_facade.run_validation(out_path)

    assert len(calls) == 1
    assert calls[0][0] == sys.executable
    assert calls[0][1] == str(validator)
    assert calls[0][2] == out_path


def test_run_validation_survives_a_nonzero_validator_exit(tmp_path, monkeypatch, capsys):
    _rebind_module_file(monkeypatch, tmp_path, depth=2)
    (tmp_path / "validate_stage4_extraction.py").write_text("# stub\n")

    def fake_run(args, capture_output=False, text=True):
        class _Result:
            returncode = 1

        return _Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    stage4_facade.run_validation(str(tmp_path / "out.json"))  # must not raise
    out = capsys.readouterr().out
    assert "Validation completed with warnings" in out
    assert "Validation error" not in out


def test_run_validation_survives_a_subprocess_exception(tmp_path, monkeypatch, capsys):
    _rebind_module_file(monkeypatch, tmp_path, depth=2)
    (tmp_path / "validate_stage4_extraction.py").write_text("# stub\n")

    def fake_run(args, capture_output=False, text=True):
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    stage4_facade.run_validation(str(tmp_path / "out.json"))  # caught, must not raise
    out = capsys.readouterr().out
    assert "Validation error: boom" in out
    assert "Extraction still successful" in out
