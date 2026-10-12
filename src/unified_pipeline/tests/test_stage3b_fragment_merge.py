"""Tests for `stage3b/fragment_merge.py` (#1256): a tagged fragment's text
joins the entry it belongs to, except a label, an organisation sub-heading
over coded records, a numbered item, a sibling's repeated field, or a parent
the model attached from both sides.

Fixtures are synthetic: shapes from the corpus runs the module docstring
names, with invented text.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage3b.fragment_merge import (  # noqa: E402
    MERGED_FLAG,
    SKIP_BOTH_SIDES,
    SKIP_LABEL,
    SKIP_LIST_ITEM,
    SKIP_NO_PARENT,
    SKIP_ORGANIZATION,
    SKIP_REPEATED_FIELD,
    SKIPPED_FIELD,
    fragment_text_in_parent,
    merge_fragment_text,
    merge_skip_reason,
    resolve_parent,
)
from unified_pipeline.stage4.context_headings import (  # noqa: E402
    stamp_context_headings,
)


def _entry(text, code="K4", **extra):
    return {"text": text, "taxonomy_code": code, "hierarchy": ["TALKS"], **extra}


def _fragment(text, parent, code="K4", reasoning="completes the neighbouring entry"):
    return _entry(
        text, code, is_fragment=True, fragment_of=parent, fragment_reasoning=reasoning
    )


def _texts(entries):
    return [e["text"] for e in entries]


def test_previous_fragment_is_appended_with_a_space():
    entries = [_entry("Grand Rounds: Rare Tumours of the"), _fragment("Lower Limb", 0)]
    _, stats = merge_fragment_text(entries)
    assert entries[0]["text"] == "Grand Rounds: Rare Tumours of the Lower Limb"
    assert entries[1]["is_fragment"] is True and entries[1][MERGED_FLAG] is True
    assert stats["fragments_merged"] == 1


def test_previous_fragment_after_sentence_punctuation_takes_a_new_line():
    entries = [_entry("Visiting lecture, 2015."), _fragment("Springfield, IL", 0)]
    merge_fragment_text(entries)
    assert entries[0]["text"] == "Visiting lecture, 2015.\nSpringfield, IL"


def test_next_fragment_is_prepended():
    entries = [
        _fragment("June 1-3, 2010 Springfield, IL", 1, code="S8"),
        _entry("Poster titled: An Example", "S8"),
    ]
    merge_fragment_text(entries)
    assert (
        entries[1]["text"] == "June 1-3, 2010 Springfield, IL Poster titled: An Example"
    )


def test_a_chain_of_previous_fragments_lands_in_document_order():
    """MQJAVH's shape: each session line points at the one before it."""
    entries = [
        _entry("Examiner for a specialty board", "Q2"),
        _fragment("Springfield, 1985", 0, "Q2"),
        _fragment("Shelbyville, 1988", 1, "Q2"),
        _fragment("Ogdenville, 1989", 2, "Q2"),
    ]
    _, stats = merge_fragment_text(entries)
    assert (
        entries[0]["text"]
        == "Examiner for a specialty board Springfield, 1985 Shelbyville, 1988 Ogdenville, 1989"
    )
    assert stats["fragments_merged"] == 3


def test_a_chain_of_next_fragments_lands_in_document_order():
    entries = [_fragment("Alpha", 1), _fragment("Beta", 2), _entry("Gamma record")]
    merge_fragment_text(entries)
    assert entries[2]["text"] == "Alpha Beta Gamma record"


def test_a_label_the_reasoning_names_is_not_merged():
    entries = [
        _fragment(
            "2007", 1, "R", reasoning="This is a year header introducing a new section"
        ),
        _entry("1. Invited talk", "R"),
    ]
    _, stats = merge_fragment_text(entries)
    assert entries[1]["text"] == "1. Invited talk"
    assert entries[0][SKIPPED_FIELD] == SKIP_LABEL
    assert stats["fragments_not_merged"] == {SKIP_LABEL: 1}


def test_header_like_is_a_hedge_not_a_label():
    """BFSUMA: 'a header-like component of the next entry' is still content."""
    entries = [
        _fragment(
            "Example Pharma Ltd",
            1,
            "T",
            reasoning="an assignee field, a header-like component of the next entry",
        ),
        _entry("Patent Application No XY00/00001", "T"),
    ]
    merge_fragment_text(entries)
    assert entries[1]["text"] == "Example Pharma Ltd Patent Application No XY00/00001"


def test_an_organisation_line_stays_out_of_a_coded_record():
    """TXTATQ: a university line heads the courses after it, not one of them."""
    entries = [
        _entry("Pharmacotherapy course, 2018", "K1"),
        _fragment("Example State University", 0, "K1"),
    ]
    merge_fragment_text(entries)
    assert entries[0]["text"] == "Pharmacotherapy course, 2018"
    assert entries[1][SKIPPED_FIELD] == SKIP_ORGANIZATION


def test_an_organisation_line_joins_an_unclassified_parent():
    entries = [
        _fragment("Example Biomed Ltd/Example University", 1, "T"),
        _entry("Patent No XY01/0002", "T"),
    ]
    merge_fragment_text(entries)
    assert entries[1]["text"].startswith("Example Biomed Ltd/Example University ")


def test_a_numbered_item_is_not_glued_to_the_next():
    """RWBQKF 94: item 1 is a record of its own, not a piece of item 2."""
    entries = [
        _fragment("1. Collaboration with colleagues", 1, "K2"),
        _entry("2. Teaching activities", "K2"),
    ]
    merge_fragment_text(entries)
    assert entries[1]["text"] == "2. Teaching activities"
    assert entries[0][SKIPPED_FIELD] == SKIP_LIST_ITEM


def test_a_field_label_the_parent_already_has_is_a_sibling_record():
    """RBHRFR 468: a second 'Current position:' is the next mentee's."""
    entries = [
        _entry("Current position: Instructor, Example College 2018", "N3B"),
        _fragment("Current position: Fellow, Example Hospital", 0, "N3B"),
    ]
    merge_fragment_text(entries)
    assert entries[0]["text"] == "Current position: Instructor, Example College 2018"
    assert entries[1][SKIPPED_FIELD] == SKIP_REPEATED_FIELD


def test_a_parent_claimed_from_both_sides_takes_neither():
    """WWSEWY 539/543: posters follow their date lines, so one side is wrong."""
    entries = [
        _fragment("May 1-3, 2003, Springfield", 1, "S8"),
        _entry("Poster titled: An Example", "S8"),
        _fragment("May 9-12, 2003 Shelbyville", 1, "S8"),
    ]
    _, stats = merge_fragment_text(entries)
    assert entries[1]["text"] == "Poster titled: An Example"
    assert stats["fragments_not_merged"] == {SKIP_BOTH_SIDES: 2}


def test_a_skipped_label_on_one_side_does_not_block_the_other():
    """JIJRSN: a page stamp after the record leaves the drug name before it free."""
    entries = [
        _fragment("Examplozumab", 1, "M2B"),
        _entry("1990-1991: Co-Investigator in a trial", "M2B"),
        _fragment("7/16/18", 1, "M2B", reasoning="a date stamp/page marker"),
    ]
    merge_fragment_text(entries)
    assert entries[1]["text"] == "Examplozumab 1990-1991: Co-Investigator in a trial"
    assert entries[2][SKIPPED_FIELD] == SKIP_LABEL


def test_fragments_pointing_at_each_other_have_no_parent():
    """ZGLAAD 137/138."""
    entries = [
        _fragment("Example University / Hospital", 1, "T"),
        _fragment("University", 0, "T"),
    ]
    assert resolve_parent(entries, 0) is None
    merge_fragment_text(entries)
    assert _texts(entries) == ["Example University / Hospital", "University"]
    assert entries[0][SKIPPED_FIELD] == SKIP_NO_PARENT


def test_a_chain_that_turns_back_has_no_parent():
    """0 points forward to 2, which points back to 1: not one direction."""
    entries = [_fragment("tail", 2), _entry("Record"), _fragment("middle", 1)]
    assert resolve_parent(entries, 2) == 1
    assert resolve_parent(entries, 0) is None


def test_an_unusable_fragment_of_has_no_parent():
    for target in (5, -1, True, "0", None):
        entries = [_entry("Record"), _fragment("tail", target)]
        assert merge_skip_reason(entries, 1) == SKIP_NO_PARENT, target


def test_text_the_parent_already_holds_is_not_copied_twice():
    entries = [
        _fragment("Examplozumab", 1, "M2B"),
        _entry("Trial of Examplozumab in adults", "M2B"),
    ]
    _, stats = merge_fragment_text(entries)
    assert entries[1]["text"] == "Trial of Examplozumab in adults"
    assert entries[0][MERGED_FLAG] is True
    assert stats["fragments_already_in_parent"] == 1 and stats["fragments_merged"] == 0


def test_a_replay_over_merged_output_changes_nothing():
    entries = [_entry("Grand Rounds: Rare Tumours of the"), _fragment("Lower Limb", 0)]
    merge_fragment_text(entries)
    _, stats = merge_fragment_text(entries)
    assert entries[0]["text"] == "Grand Rounds: Rare Tumours of the Lower Limb"
    assert stats["fragments_merged"] == 0 and stats["fragments_already_in_parent"] == 1


def test_fragment_text_in_parent_ignores_space_and_case():
    entries = [
        _entry("Lecture on SPINDLE  cell tumours"),
        _fragment("spindle cell", 0),
        _fragment("Lost", 0),
    ]
    assert fragment_text_in_parent(entries, 1) is True
    assert fragment_text_in_parent(entries, 2) is False


def test_stage4_input_holds_the_fragment_once_and_no_stamp_moves():
    """The #1365 proof shape: stamp, then stage 4's fragment skip. The
    merged text reaches the one record stage 4 extracts, and the context
    stamp over the children of a T heading is the same before and after."""
    entries = [
        _entry("Example Medical Society:", "T", hierarchy=["SERVICE"]),
        _entry("Chair, Programme Committee 2010", "Q1", hierarchy=["SERVICE"]),
        _fragment("Springfield, IL", 1, "Q1"),
        _entry("Member, Awards Committee 2012", "Q1", hierarchy=["SERVICE"]),
    ]
    for entry in entries:
        entry["hierarchy"] = ["SERVICE"]
    before = stamp_context_headings(entries)
    merge_fragment_text(entries)
    after = stamp_context_headings(entries)
    kept = [e for e in after if not e.get("is_fragment")]
    assert [e["text"] for e in kept][
        1
    ] == "Chair, Programme Committee 2010 Springfield, IL"
    assert [e.get("context_heading") for e in after] == [
        e.get("context_heading") for e in before
    ]
    assert sum("Springfield" in e["text"] for e in kept) == 1
