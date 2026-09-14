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

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage4.owner_name as owner_name  # noqa: E402


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
