"""input_format: WCM-template vs other detection on synthetic text."""
from app.services.input_format import (
    INPUT_FORMAT_OTHER,
    INPUT_FORMAT_WCM,
    WCM_MIN_SIGNALS,
    detect_input_format,
    detect_input_format_or_none,
)

# The template's own distinctive headings (public template text, not a real CV).
_HEADINGS = [
    "EMPLOYMENT STATUS", "INSTITUTIONAL/HOSPITAL AFFILIATION", "LICENSURE, BOARD CERTIFICATION",
    "EDUCATIONAL CONTRIBUTIONS", "INSTITUTIONAL LEADERSHIP ACTIVITIES",
    "EXTRAMURAL PROFESSIONAL RESPONSIBILITIES", "INVITATIONS TO SPEAK/PRESENT",
]


def _cv(*headings):
    return "\n".join(["Synthetic Person, MD", *headings, "Some synthetic entry text."])


def test_template_headings_make_a_wcm_cv():
    fmt, score = detect_input_format(_cv(*_HEADINGS))
    assert (fmt, score) == (INPUT_FORMAT_WCM, len(_HEADINGS))


def test_headings_with_numbering_and_colons_still_count():
    assert detect_input_format(_cv(*(f"{i}. {h.title()}:" for i, h in enumerate(_HEADINGS, 1))))[0] == INPUT_FORMAT_WCM


def test_a_free_form_cv_is_other():
    fmt, score = detect_input_format(_cv("Education", "Research", "Bibliography", "Honors and Awards"))
    assert (fmt, score) == (INPUT_FORMAT_OTHER, 0)


def test_the_threshold_is_the_boundary():
    assert detect_input_format(_cv(*_HEADINGS[:WCM_MIN_SIGNALS - 1]))[0] == INPUT_FORMAT_OTHER
    assert detect_input_format(_cv(*_HEADINGS[:WCM_MIN_SIGNALS]))[0] == INPUT_FORMAT_WCM


def test_a_repeated_heading_counts_once():
    assert detect_input_format(_cv(*(["EMPLOYMENT STATUS"] * 20))) == (INPUT_FORMAT_OTHER, 1)


def test_left_in_template_instructions_count():
    line = "Duplicate table below as needed. For each funding vehicle, please include the following:"
    fmt, score = detect_input_format(_cv(*([line] * WCM_MIN_SIGNALS)))
    assert (fmt, score) == (INPUT_FORMAT_WCM, WCM_MIN_SIGNALS)


def test_no_text_is_undetermined():
    for text in (None, "", "  \n "):
        assert detect_input_format(text) == (None, None)


def test_or_none_logs_and_returns_undetermined_on_failure(monkeypatch, caplog):
    def boom(_text):
        raise RuntimeError("boom")
    monkeypatch.setattr("app.services.input_format.detect_input_format", boom)
    with caplog.at_level("WARNING"):
        assert detect_input_format_or_none("text") == (None, None)
    assert "Input-format detection failed" in caplog.text
