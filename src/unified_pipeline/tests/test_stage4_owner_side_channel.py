"""#456: `extract_cv_owner_name`'s side-channel fallback tier.

The tier consults `extract_owner_side_channel` (sdt/header/footer text the
main body walk never sees) only when the body-derived pass left BOTH
`last_name` and `full_name` empty and a real `docx_path` was given -- and
only before `fallback_from_uid`, never overriding a body-derived name.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_owner_side_channel.py -p no:cacheprovider

Self-contained: `call_llm` and `extract_owner_side_channel` are both stubbed
at the module attribute -- no Bedrock/OpenAI, no real .docx parsing.
Synthetic names only.
"""

import io
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage4.owner_name as owner_name  # noqa: E402
import unified_pipeline.stage4.extraction as extraction  # noqa: E402
import unified_pipeline.stage_4_field_extractor as stage_4_field_extractor  # noqa: E402


def _llm_result(content, total_tokens=10, cost=0.001):
    return {"content": content, "total_tokens": total_tokens, "cost": cost}


def _name_reply(**fields):
    base = {
        "first_name": "", "middle_name": "", "last_name": "",
        "suffix": "", "full_name": "", "full_name_with_credentials": "",
    }
    base.update(fields)
    return json.dumps(base)


def _touch(tmp_path, name="cv.docx"):
    """A file that exists -- content is irrelevant, `extract_owner_side_channel`
    is always stubbed in these tests, so nothing ever actually parses it."""
    p = tmp_path / name
    p.write_bytes(b"not a real docx; only Path.is_file() is checked")
    return str(p)


# ---------------------------------------------------------------------------
# Body tier succeeds -> side channel never consulted
# ---------------------------------------------------------------------------

def test_body_tier_success_never_consults_side_channel(monkeypatch, tmp_path):
    calls = {"call_llm": 0, "side_channel": 0}

    def fake_call_llm(**kwargs):
        calls["call_llm"] += 1
        return _llm_result(_name_reply(first_name="Jane", last_name="Public", full_name="Jane Public"))

    def fake_side_channel(path):
        calls["side_channel"] += 1
        return {"sdt_lines": [], "header_lines": [], "footer_lines": []}

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(owner_name, "extract_owner_side_channel", fake_side_channel)

    result = owner_name.extract_cv_owner_name(
        "2024_Test_CV", [{"text": "Jane Public, MD, Professor of Surgery"}],
        docx_path=_touch(tmp_path),
    )

    assert result["last_name"] == "Public"
    assert calls["call_llm"] == 1
    assert calls["side_channel"] == 0, "a body-derived name must never trigger the side channel"


# ---------------------------------------------------------------------------
# Body tier empty + side channel has lines -> prompt runs over them, name used
# ---------------------------------------------------------------------------

def test_side_channel_used_when_body_tier_finds_nothing(monkeypatch, tmp_path):
    prompts = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        if len(prompts) == 1:
            return _llm_result(_name_reply())  # body tier: nothing found
        return _llm_result(_name_reply(first_name="Sam", last_name="Rivera", full_name="Sam Rivera"))

    def fake_side_channel(path):
        return {"sdt_lines": ["Sam Rivera, PhD"], "header_lines": [], "footer_lines": []}

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(owner_name, "extract_owner_side_channel", fake_side_channel)

    result = owner_name.extract_cv_owner_name(
        "2024_Test_CV", [{"text": "a narrative paragraph with no name in it at all"}],
        docx_path=_touch(tmp_path),
    )

    assert result["last_name"] == "Rivera"
    assert len(prompts) == 2
    assert "Sam Rivera, PhD" in prompts[1]


# ---------------------------------------------------------------------------
# Body tier empty + side channel empty -> fallback_from_uid, unchanged
# ---------------------------------------------------------------------------

def test_side_channel_empty_falls_through_to_uid_fallback_unchanged(monkeypatch, tmp_path):
    def fake_call_llm(**kwargs):
        return _llm_result(_name_reply())

    def fake_side_channel(path):
        return {"sdt_lines": [], "header_lines": [], "footer_lines": []}

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(owner_name, "extract_owner_side_channel", fake_side_channel)

    with_docx = owner_name.extract_cv_owner_name(
        "2024_Rivera_CV", [{"text": "narrative body text"}], docx_path=_touch(tmp_path),
    )
    without_docx = owner_name.extract_cv_owner_name(
        "2024_Rivera_CV", [{"text": "narrative body text"}],
    )

    assert with_docx == without_docx == {
        "first_name": "", "middle_name": "", "last_name": "Rivera",
        "suffix": "", "full_name": "", "full_name_with_credentials": "",
    }


# ---------------------------------------------------------------------------
# docx_path=None -> identical to pre-#456 behaviour
# ---------------------------------------------------------------------------

def test_docx_path_none_matches_pre_456_behavior(monkeypatch):
    def fake_call_llm(**kwargs):
        return _llm_result(_name_reply())

    side_channel_calls = []
    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: side_channel_calls.append(p) or {"sdt_lines": [], "header_lines": [], "footer_lines": []},
    )

    result = owner_name.extract_cv_owner_name("2024_Doe_CV", [{"text": "narrative text"}])

    assert result["last_name"] == "Doe"
    assert side_channel_calls == []


# ---------------------------------------------------------------------------
# docx_path to a missing file -> no raise, falls through
# ---------------------------------------------------------------------------

def test_missing_docx_path_falls_through_without_raising(monkeypatch, tmp_path):
    def fake_call_llm(**kwargs):
        return _llm_result(_name_reply())

    side_channel_calls = []
    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: side_channel_calls.append(p) or {"sdt_lines": ["should never be read"], "header_lines": [], "footer_lines": []},
    )

    missing_path = str(tmp_path / "does_not_exist.docx")

    result = owner_name.extract_cv_owner_name(
        "2024_Doe_CV", [{"text": "narrative text"}], docx_path=missing_path,
    )

    assert result["last_name"] == "Doe"
    assert side_channel_calls == [], "extract_owner_side_channel must not be called for a nonexistent path"


# ---------------------------------------------------------------------------
# The `:104` no-entries path also consults the side channel
# ---------------------------------------------------------------------------

def test_no_entries_path_consults_side_channel(monkeypatch, tmp_path):
    """mapped_entries=[] hits the no-first_entries branch directly -- no
    body-tier LLM call at all -- and the side channel must still run before
    the uid fallback."""
    prompts = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _llm_result(_name_reply(first_name="Alex", last_name="Nguyen", full_name="Alex Nguyen"))

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: {"sdt_lines": ["Alex Nguyen, MD"], "header_lines": [], "footer_lines": []},
    )

    result = owner_name.extract_cv_owner_name("web999", [], docx_path=_touch(tmp_path))

    assert result["last_name"] == "Nguyen"
    assert len(prompts) == 1, "only the side-channel prompt should run -- no body-tier call with zero entries"
    assert "Alex Nguyen, MD" in prompts[0]


def test_no_entries_path_with_no_side_channel_content_uses_uid_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: {"sdt_lines": [], "header_lines": [], "footer_lines": []},
    )
    called_llm = []
    monkeypatch.setattr(owner_name, "call_llm", lambda **kwargs: called_llm.append(1))

    result = owner_name.extract_cv_owner_name("web999", [], docx_path=_touch(tmp_path))

    assert result["last_name"] == ""  # 'web999' is opaque -- #457's contract
    assert called_llm == [], "no side-channel lines means no LLM call at all"


# ---------------------------------------------------------------------------
# Line cap and char cap enforced
# ---------------------------------------------------------------------------

def test_side_channel_line_and_char_caps_are_enforced(monkeypatch, tmp_path):
    long_line = "X" * 500
    # Fixed-width, zero-padded markers so no marker is a substring of another
    # (unlike "line 1" inside "line 10") -- keeps the membership checks below exact.
    many_lines = [f"UNIQLINE{i:03d}TAG" for i in range(50)]

    prompts = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _llm_result(_name_reply())

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: {"sdt_lines": [long_line] + many_lines, "header_lines": [], "footer_lines": []},
    )

    owner_name.extract_cv_owner_name("web998", [], docx_path=_touch(tmp_path))

    assert len(prompts) == 1
    prompt_text = prompts[0]

    # The long line is truncated to OWNER_SIDE_CHANNEL_MAX_CHARS, not passed whole.
    assert "X" * owner_name.OWNER_SIDE_CHANNEL_MAX_CHARS in prompt_text
    assert "X" * (owner_name.OWNER_SIDE_CHANNEL_MAX_CHARS + 1) not in prompt_text

    # Only OWNER_SIDE_CHANNEL_MAX_LINES lines total make it into the prompt --
    # the long_line occupies one slot, leaving MAX_LINES - 1 of many_lines.
    included = sum(1 for marker in many_lines if marker in prompt_text)
    assert included == owner_name.OWNER_SIDE_CHANNEL_MAX_LINES - 1


# ---------------------------------------------------------------------------
# #456-R2 F4: a corrupt-but-existing docx_path must not raise -- it must log
# a warning and fall through to fallback_from_uid, matching the docstring's
# own claim (which was previously false: a real non-zip file propagated
# PackageNotFoundError straight out of the tier and failed the web driver).
# ---------------------------------------------------------------------------

def test_corrupt_docx_path_falls_through_without_raising(monkeypatch, tmp_path, caplog):
    def fake_call_llm(**kwargs):
        return _llm_result(_name_reply())

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    # extract_owner_side_channel is NOT stubbed here -- this test exercises
    # the real function against a genuinely corrupt file (an ASCII file with
    # a .docx extension), so Document(docx_path) really raises
    # PackageNotFoundError and the tier's own try/except must catch it.

    corrupt_path = tmp_path / "corrupt.docx"
    corrupt_path.write_text("not a docx at all, just ascii text")

    with caplog.at_level("WARNING", logger="unified_pipeline.stage4.owner_name"):
        result = owner_name.extract_cv_owner_name(
            "2024_Rivera_CV", [{"text": "narrative body text"}], docx_path=str(corrupt_path),
        )

    assert result["last_name"] == "Rivera"  # fallback_from_uid, unchanged
    warnings = [r.message for r in caplog.records if "owner side channel unreadable" in r.message]
    assert len(warnings) == 1
    assert warnings[0].startswith("2024_Rivera_CV: owner side channel unreadable:")


# ---------------------------------------------------------------------------
# #456-R2 F3, mutant m5: sdt/header/footer lines must ALL reach the prompt,
# in that order -- not just whichever channel happened to be tested alone
# above. `combined = channel['sdt_lines']` (dropping header/footer) survived
# the full suite before this test existed.
# ---------------------------------------------------------------------------

def test_side_channel_combines_sdt_header_and_footer_lines_in_order(monkeypatch, tmp_path):
    prompts = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _llm_result(_name_reply())

    monkeypatch.setattr(owner_name, "call_llm", fake_call_llm)
    monkeypatch.setattr(
        owner_name, "extract_owner_side_channel",
        lambda p: {
            "sdt_lines": ["SDTMARK Owner"],
            "header_lines": ["HDRMARK Owner"],
            "footer_lines": ["FTRMARK Owner"],
        },
    )

    owner_name.extract_cv_owner_name("web997", [], docx_path=_touch(tmp_path))

    assert len(prompts) == 1
    prompt_text = prompts[0]
    assert "SDTMARK Owner" in prompt_text
    assert "HDRMARK Owner" in prompt_text
    assert "FTRMARK Owner" in prompt_text
    # sdt -> header -> footer is the contract order (extract_owner_side_channel's
    # docstring, `_owner_side_channel_content_lines`).
    assert (
        prompt_text.index("SDTMARK Owner")
        < prompt_text.index("HDRMARK Owner")
        < prompt_text.index("FTRMARK Owner")
    )


# ---------------------------------------------------------------------------
# #456-R2 F3, mutants m7a/m7b: `docx_path` must reach `extract_cv_owner_name`
# across BOTH hops of the wire -- process_cv -> extract_fields_from_mapped_
# entries (m7a) and extract_fields_from_mapped_entries -> extract_cv_owner_
# name (m7b). Either kwarg silently dropped left the full 3257-test suite
# green before these two tests existed.
# ---------------------------------------------------------------------------

def test_extraction_passes_docx_path_to_extract_cv_owner_name(monkeypatch):
    """m7b: extract_fields_from_mapped_entries -> extract_cv_owner_name."""
    captured = {}

    def fake_owner_name(document_uid, mapped_entries, docx_path=None, usage=None):
        captured["docx_path"] = docx_path
        return {
            "first_name": "", "middle_name": "", "last_name": "",
            "suffix": "", "full_name": "", "full_name_with_credentials": "",
        }

    monkeypatch.setattr(extraction, "extract_cv_owner_name", fake_owner_name)
    monkeypatch.setattr(extraction, "extract_fields_batch", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no entries -- extract_fields_batch must not be called")
    ))

    extraction.extract_fields_from_mapped_entries(
        [], document_uid="web996", docx_path="/synthetic/path/web996.docx",
    )

    assert captured["docx_path"] == "/synthetic/path/web996.docx"


def test_process_cv_passes_its_docx_path_to_extract_fields_from_mapped_entries(monkeypatch, tmp_path):
    """m7a: process_cv -> extract_fields_from_mapped_entries.

    The stage-3b JSON lookup is stubbed (a synthetic, empty entries payload)
    so this test needs no real corpus file: `Path.exists` is patched to
    report True only for the one expected stage-3b path (delegating to the
    real implementation for every other path, tmp_path's own files
    included), and this module's bare `open` name is shadowed (module-level
    only -- never touches the `open` builtin other modules see) to hand back
    that synthetic payload for that one path.
    """
    cv_path = str(tmp_path / "synthetic_cv.docx")
    Path(cv_path).write_bytes(b"not a real docx; process_cv never opens this file itself")
    document_uid = Path(cv_path).stem

    expected_stage3b = (
        Path(stage_4_field_extractor.__file__).parent
        / "outputs" / "stage_3b_classified_entries" / f"{document_uid}_classified.json"
    )
    expected_output_dir = Path(stage_4_field_extractor.__file__).parent / "outputs" / "stage_4_field_extraction"
    expected_output_path = expected_output_dir / f"{document_uid}_fields.json"

    real_exists = Path.exists
    real_mkdir = Path.mkdir

    def fake_exists(self):
        if self == expected_stage3b:
            return True
        return real_exists(self)

    def fake_mkdir(self, *args, **kwargs):
        # process_cv unconditionally mkdir's its real output dir -- never
        # actually touch the repo's real outputs/ tree from a test.
        if self == expected_output_dir:
            return None
        return real_mkdir(self, *args, **kwargs)

    def fake_open(path, mode="r", *args, **kwargs):
        p = Path(path)
        if p == expected_stage3b:
            return io.StringIO(json.dumps({"entries": []}))
        if p == expected_output_path:
            return io.StringIO()  # process_cv's own output write, discarded
        raise AssertionError(f"unexpected open() inside process_cv test: {path}")

    captured = {}

    def fake_extract(mapped_entries, **kwargs):
        captured["docx_path"] = kwargs.get("docx_path")
        return {
            "entries": [], "cv_owner": {}, "cv_owner_location": {},
            "total_cost": 0.0, "total_tokens": 0,
            "stats": {"extracted": 0, "skipped": 0},
        }

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    monkeypatch.setattr(stage_4_field_extractor, "open", fake_open, raising=False)
    monkeypatch.setattr(stage_4_field_extractor, "extract_fields_from_mapped_entries", fake_extract)

    stage_4_field_extractor.process_cv(cv_path)

    assert captured["docx_path"] == cv_path


def test_process_cv_stamps_over_the_unfiltered_3b_list_before_dropping_fragments(monkeypatch, tmp_path):
    """#985 B1: a sub-heading 3b flagged is_fragment is filtered out before
    extraction, so the stamp must run on the FULL list, where that fragment
    still ends the previous heading's run."""
    document_uid = "synthetic_cv_985"
    stage3b = Path(stage_4_field_extractor.__file__).parent / "outputs" / "stage_3b_classified_entries" / f"{document_uid}_classified.json"
    hier = ["Service"]
    entries = [
        {"text": "Alpha University:", "taxonomy_code": "T", "hierarchy": hier},
        {"text": "Member, Committee X", "taxonomy_code": "P", "hierarchy": hier},
        {"text": "Beta University:", "taxonomy_code": "O", "hierarchy": hier, "is_fragment": True},
        {"text": "Chair, Committee Y", "taxonomy_code": "P", "hierarchy": hier},
    ]
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: True if self == stage3b else real_exists(self))
    monkeypatch.setattr(Path, "mkdir", lambda self, *a, **k: None)
    monkeypatch.setattr(stage_4_field_extractor, "open", lambda path, mode="r", *a, **k: (
        io.StringIO(json.dumps({"entries": entries})) if Path(path) == stage3b else io.StringIO()), raising=False)
    captured = {}

    def fake_extract(mapped_entries, **kwargs):
        captured["stamps"] = [(e["text"], e.get("context_heading")) for e in mapped_entries]
        return {"entries": [], "cv_owner": {}, "cv_owner_location": {}, "total_cost": 0.0, "total_tokens": 0, "stats": {"extracted": 0, "skipped": 0}}

    monkeypatch.setattr(stage_4_field_extractor, "extract_fields_from_mapped_entries", fake_extract)
    stage_4_field_extractor.process_cv(str(tmp_path / f"{document_uid}.docx"))
    assert captured["stamps"] == [("Alpha University:", None), ("Member, Committee X", "Alpha University"), ("Chair, Committee Y", None)]
