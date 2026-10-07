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
from unified_pipeline.stage4.context_headings import (
    stamp_context_headings,  # noqa: E402
)

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
    ("LEGAL CONSULTING (not necessarily an exhaustive listing)", "LEGAL CONSULTING"),  # aside dropped before the caps/word tests
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
    "Northgate Center Dept of Surgery.",       # period, no comma
    "Northgate Center Dept. of Surgery",       # period, no comma
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
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _e("Curriculum Vitae", "T"), _e("o1")])
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


def _dropped(text, code="P", **flags):
    return {**_e(text, code), "is_fragment": True, **flags}


def test_a_dropped_sibling_sub_heading_ends_the_run_and_is_never_stamped():
    """B1: 3b flags the sibling 'Beta University:' is_fragment; stage 4 filters it
    out, so the run must already have ended on the FULL list."""
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _dropped("Beta University:", "O"), _e("b1")])
    assert out == [None, "Alpha University", None, None]
    out = _stamps([_e("Alpha University:", "T"), _e("a1"), _dropped("a2 tail"), _e("a3")])
    assert out == [None, "Alpha University", None, "Alpha University"]     # a plain dropped line is neutral
    assert _stamps([_dropped("Gamma University:", "T"), _e("c1")]) == [None, None]  # dropped T is no heading


@pytest.mark.parametrize("text", [
    "Stanford, California", "Connecticut", "And Date", "Between Alpha and Beta Care",
    "Alpha Beta Gamma Delta Epsilon Zeta Eta", "TLS", "JAMA",
])
def test_bare_label_false_positives_are_refused(text):
    assert _stamps([_e(text, "T"), _e("child")]) == [None, None]


def test_a_wrapped_line_tail_is_not_a_bare_heading():
    tail = {**_e("Northgate School of Medicine", "T"), "element_type": "break"}
    assert _stamps([tail, _e("child")]) == [None, None]
    para = {**tail, "element_type": "paragraph"}
    assert _stamps([para, _e("child")]) == [None, "Northgate School of Medicine"]


def test_a_bare_label_inside_a_run_of_t_entries_or_before_one_is_a_list_item():
    items = [_e(f"Journal {n} Review", "T") for n in "AB"]
    assert set(_stamps([*items, _e("Last Item Here", "T"), _e("child")])) == {None}     # tail of a T list
    assert _stamps([_e("Alpha Journal", "T"), _e("Beta Journal", "T"), _e("child")])[0] is None    # next is T
    assert _stamps([_e("Section Title", "T"), _e("Graduate Students", "T"), _e("child")])[-1] == "Graduate Students"


def _flat(text_code_pairs):
    """Entries under one leaf that spans >= FLAT_HIERARCHY_MIN_LETTERS code letters."""
    filler = [_e(f"filler {c}{i}", c) for c in "ABCD"[:ch.FLAT_HIERARCHY_MIN_LETTERS]
              for i in range(ch.FLAT_LETTER_MIN_ENTRIES)]
    return filler + [_e(t, c) for t, c in text_code_pairs]


def test_in_a_flat_hierarchy_a_run_stops_when_the_code_letter_changes():
    entries = _flat([("Alpha University:", "T"), ("n1", "N3A"), ("n2", "N3B"), ("s1", "S1"), ("n3", "N3A")])
    assert _stamps(entries)[-5:] == [None, "Alpha University", "Alpha University", None, None]


def test_in_a_coherent_hierarchy_a_run_may_mix_code_letters():
    entries = [_e("Alpha University:", "T"), _e("p1", "P"), _e("o1", "O")]
    assert _stamps(entries) == [None, "Alpha University", "Alpha University"]


def test_a_letter_held_by_too_few_entries_does_not_make_a_leaf_flat():
    # One stray Q1 and one Q2 under a P/O leaf: still coherent, so the run
    # keeps its O children (the three letters are there, but only P and O
    # are held by enough entries to count).
    few = ch.FLAT_LETTER_MIN_ENTRIES - 1
    strays = [_e(f"q{i}", "Q1") for i in range(few)]
    fillers = [_e(f"p{i}", "P") for i in range(ch.FLAT_LETTER_MIN_ENTRIES)]
    entries = strays + fillers + [_e("Alpha University:", "T"), _e("p-child", "P"), _e("o-child", "O"),
                                  _e("o-child 2", "O")]
    assert _stamps(entries)[-3:] == ["Alpha University"] * 3


def test_a_t_entry_that_is_not_a_heading_ends_the_run():
    # "2003-2016 Beta Institute ..." is T but has a digit, so it is no heading;
    # the entries after it belong to its group, not to the heading above.
    entries = [_e("Alpha University:", "T"), _e("child 1"),
               _e("2003-2016 Beta Institute, Gamma", "T"), _e("child 2")]
    assert _stamps(entries) == [None, "Alpha University", None, None]


def test_t_and_dropped_entries_do_not_count_toward_a_flat_leaf():
    # Three dropped Q1 fragments must not add a third letter to a P/O leaf.
    n = ch.FLAT_LETTER_MIN_ENTRIES
    dropped = [{**_e(f"q{i}", "Q1"), "is_fragment": True} for i in range(n)]
    entries = dropped + [_e(f"p{i}") for i in range(n)] + [_e("Alpha University:", "T"), _e("p-child")] \
        + [_e(f"o-child {i}", "O") for i in range(n)]
    assert _stamps(entries)[-(n + 1):] == ["Alpha University"] * (n + 1)


def test_a_bare_label_followed_by_a_dropped_t_entry_governs_nothing():
    entries = [_e("Alpha Beta Label", "T"), {**_e("Gamma list item", "T"), "is_fragment": True}, _e("child")]
    assert set(_stamps(entries)) == {None}


def test_a_streak_of_another_letter_ends_the_run():
    n = ch.MAX_FOREIGN_LETTER_STREAK
    entries = [_e("Course Lecturer:", "T")] + [_e(f"course {i}", "K1") for i in range(n + 1)] \
        + [_e(f"mentee {i}", "N3B") for i in range(n)]
    assert _stamps(entries)[1:] == ["Course Lecturer"] * (n + 1) + [None] * n


def test_a_short_detour_and_a_misfiled_first_child_keep_the_run():
    n = ch.MAX_FOREIGN_LETTER_STREAK
    detour = [_e("misfiled", "L1")] + [_e(f"course {i}", "K1") for i in range(n)] \
        + [_e(f"other {i}", "D3") for i in range(n - 1)] + [_e("course last", "K2")]
    entries = [_e("Alpha School of Medicine", "T")] + detour
    assert _stamps(entries)[1:] == ["Alpha School of Medicine"] * len(detour)


def test_a_bare_label_whose_next_entry_is_t_governs_nothing():
    entries = [_e("Alpha Journal", "T"), _e("A long list item, with a comma", "T"), _e("child")]
    assert set(_stamps(entries)) == {None}
    assert _stamps([_e("Alpha Journal", "T")]) == [None]
