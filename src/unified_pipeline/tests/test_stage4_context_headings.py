"""#985: `stamp_context_headings` copies a stage-3b T sub-heading onto the
entries beneath it as `context_heading`.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_context_headings.py -p no:cacheprovider

Pure and offline. Invented names only.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage4 import context_headings as ch  # noqa: E402
from unified_pipeline.stage4.context_headings import stamp_context_headings  # noqa: E402

_H = ["Service", "Institutional Service"]


def _e(text, code="P", hierarchy=None):
    return {"text": text, "taxonomy_code": code, "hierarchy": list(hierarchy or _H)}


def _stamps(entries):
    return [e.get("context_heading") for e in stamp_context_headings(entries)]


@pytest.mark.parametrize("heading, expected", [
    ("Northgate University:", "Northgate University"),            # colon trigger
    ("COURSE DIRECTOR", "COURSE DIRECTOR"),                        # all-caps trigger
    ("Graduate Students and Postdocs", "Graduate Students and Postdocs"),  # bare label
    ("IV. VISITING FACULTY", "VISITING FACULTY"),                  # enumerator stripped
    ("GOVERNMENT (not necessarily an exhaustive listing)", "GOVERNMENT"),  # aside dropped
])
def test_a_heading_shaped_t_entry_stamps_the_entry_after_it(heading, expected):
    assert _stamps([_e(heading, "T"), _e("Member, Committee X")]) == [None, expected]


@pytest.mark.parametrize("text", [
    "Northgate University 2011:",              # digit
    "Name | Role:",                            # pipe
    "Name:value",                              # interior colon
    "Course\tDirector",                        # tab
    "Line one\nLine two:",                     # multi-line
    "X" * (ch.MAX_HEADING_CHARS + 1),          # too long
    "Dr. Jane Roe, Ph.D. Professor",           # bare label with a period
    "Year  School  Degree",                    # column gap
    "Is this a question?",                     # form question
    "Course Director (unbalanced",             # unbalanced paren
])
def test_each_shape_guard_refuses_a_non_heading(text):
    assert _stamps([_e(text, "T"), _e("Member, Committee X")]) == [None, None]


def test_a_long_bare_label_is_refused_but_a_long_colon_heading_is_not():
    words = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota"
    assert len(words.split()) > ch.MAX_LABEL_WORDS
    assert _stamps([_e(words, "T"), _e("child")]) == [None, None]
    assert _stamps([_e(words + ":", "T"), _e("child")]) == [None, words]


def test_a_bare_lowercase_line_is_not_a_label():
    assert _stamps([_e("graduate students", "T"), _e("child")]) == [None, None]


def test_a_numbered_heading_keeps_its_text_and_ignores_the_number_for_the_digit_guard():
    assert _stamps([_e("2. Northgate University:", "T"), _e("child")]) == [None, "Northgate University"]
    assert _stamps([_e("B) Alpha Laboratory:", "T"), _e("child")]) == [None, "Alpha Laboratory"]


def test_a_colon_ended_parenthetical_aside_is_generic():
    assert _stamps([_e("(not necessarily an exhaustive listing):", "T"), _e("child")]) == [None, None]
    assert _stamps([_e("(pending):", "T"), _e("child")]) == [None, None]


def test_only_a_t_coded_entry_is_a_heading():
    assert _stamps([_e("Northgate University:", "O"), _e("child")]) == [None, None]


@pytest.mark.parametrize("text", [
    "Curriculum Vitae", "OTHER", "Contact Information", "Medico-legal Curriculum Vitae",
    "(not necessarily an exhaustive listing)", "Selected Book Reviews", "Institutional Service",
])
def test_generic_headings_stamp_nothing(text):
    assert _stamps([_e(text, "T"), _e("child")]) == [None, None]


def test_a_heading_equal_to_the_hierarchy_leaf_is_generic_but_a_narrower_one_is_not():
    same = [_e("Institutional Service:", "T"), _e("child")]
    assert _stamps(same) == [None, None]
    narrower = [_e("Graduate teaching:", "T", ["Undergraduate and Graduate Teaching"]),
                _e("child", "K1", ["Undergraduate and Graduate Teaching"])]
    assert _stamps(narrower) == [None, "Graduate teaching"]


def test_stamp_stops_at_the_next_heading_and_restarts_under_it():
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("a2"),
                   _e("Beta University:", "T"), _e("b1")])
    assert out == [None, "Alpha University", "Alpha University", None, "Beta University"]


def test_stamp_stops_at_a_hierarchy_change():
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("x1", "P", ["Other", "Leaf"]), _e("a2")])
    assert out == [None, "Alpha University", None, None]


def test_stamp_stops_at_a_non_t_entry_that_is_itself_a_strong_heading():
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("Beta University:", "O"), _e("b1")])
    assert out == [None, "Alpha University", None, None]
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("BETA UNIVERSITY", "O"), _e("b1")])
    assert out == [None, "Alpha University", None, None]


def test_a_bare_short_non_t_child_does_not_end_the_stamp():
    out = _stamps([_e("Graduate Students", "T"), _e("Jane Roe, a PhD student, Duke", "N3A"),
                   _e("Sam Poe, a PhD student, Duke", "N3A")])
    assert out == [None, "Graduate Students", "Graduate Students"]


def test_a_generic_heading_ends_the_previous_stamp():
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("Other", "T"), _e("o1")])
    assert out == [None, "Alpha University", None, None]


def test_entries_before_any_heading_get_no_stamp():
    assert _stamps([_e("first"), _e("second")]) == [None, None]


def test_a_heading_governing_more_than_the_run_cap_stamps_nothing():
    kids = [_e(f"child {i}") for i in range(ch.MAX_STAMPED_RUN + 1)]
    assert set(_stamps([_e("Alpha University:", "T"), *kids])) == {None}
    kids = kids[:ch.MAX_STAMPED_RUN]
    assert _stamps([_e("Alpha University:", "T"), *kids])[1:] == ["Alpha University"] * ch.MAX_STAMPED_RUN


def test_the_input_is_not_mutated_and_hierarchy_code_and_text_are_unchanged():
    entries = [_e("Alpha University:", "T"), _e("a1"), _e("a2", "Q2")]
    before = copy.deepcopy(entries)
    out = stamp_context_headings(entries)
    assert entries == before
    assert all("context_heading" not in e for e in entries)
    for old, new in zip(entries, out):
        assert new is not old
        assert {k: new[k] for k in ("text", "taxonomy_code", "hierarchy")} == \
               {k: old[k] for k in ("text", "taxonomy_code", "hierarchy")}
    assert [e["text"] for e in out] == [e["text"] for e in entries]
