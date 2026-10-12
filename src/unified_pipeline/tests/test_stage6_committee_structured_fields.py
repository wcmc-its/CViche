"""Guard: structured Administrative Committee fields must not crash Stage 6 (#256).

Stage 4 can emit a committee field as a dict or a LIST of record dicts for a
multi-record entry (#208/#248 fusion). Writing a non-str into a Word cell
(`cell.text = <list>`) raises deep in python-docx and aborts the whole document
(a total failure — no WCM doc produced). `_committee_cell_text` coerces any such
value to plain text; the filler expands a list into one row per record.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_committee_structured_fields.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx needed for the helper.
"""

import sys
import typing
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.fields import (
    _COMMITTEE_NAME_KEYS,
    _phone_cell_text,
)
from unified_pipeline.stage_6_word_template import _committee_cell_text


def test_string_and_empty_passthrough():
    assert _committee_cell_text("Faculty Senate Committee") == "Faculty Senate Committee"
    assert _committee_cell_text("") == ""
    assert _committee_cell_text(None) == ""


def test_dict_pulls_name_like_key():
    assert _committee_cell_text({"committee_name": "Research Policy Committee", "role": "Member"}) \
        == "Research Policy Committee"
    assert _committee_cell_text({"name": "HPE Conference Committee"}) == "HPE Conference Committee"


@pytest.mark.parametrize("key", ["committee_name", "committee", "activity", "name", "journal", "title"])
def test_dict_pulls_each_name_like_key(key):
    assert _committee_cell_text({key: "Research Committee"}) == "Research Committee"


def test_committee_name_keys_constant():
    # 'journal' was added for #812 (service.py's `journal_name` field, which
    # can carry a list of {"name"/"journal": ..., "start_date": ...,
    # "end_date": ...} records) -- extending this tuple rather than
    # special-casing the caller, per the ticket.
    assert _COMMITTEE_NAME_KEYS == ("committee_name", "committee", "activity", "name", "journal", "title")


def test_dict_key_precedence():
    keys = ["committee_name", "committee", "activity", "name", "title"]
    value = {k: k.upper() for k in keys}
    assert _committee_cell_text(value) == "COMMITTEE_NAME"
    # Popping keys front-to-back: each next key wins in turn.
    value.pop("committee_name")
    assert _committee_cell_text(value) == "COMMITTEE"
    value.pop("committee")
    assert _committee_cell_text(value) == "ACTIVITY"
    value.pop("activity")
    assert _committee_cell_text(value) == "NAME"
    value.pop("name")
    assert _committee_cell_text(value) == "TITLE"
    value.pop("title")
    assert _committee_cell_text(value) == ""
    # An empty recognised value yields to the next key.
    assert _committee_cell_text({"committee_name": "", "name": "B"}) == "B"
    assert _committee_cell_text({"committee_name": None, "title": "T"}) == "T"


def test_nested_value_under_recognised_key_is_recursed():
    assert _committee_cell_text({"committee_name": {"name": "Research Committee"}}) \
        == "Research Committee"
    assert _committee_cell_text({"committee_name": ["A", "B"]}) == "A; B"
    assert _committee_cell_text({"committee_name": {"unrelated": "X"}}) == "X"


def test_dict_without_name_like_key_joins_values(): # (#555)
    # No recognised key (committee_name/committee/activity/name/title): join
    # the dict's values instead of dropping it, matching `_address_cell_text`'s
    # "never skipped" fallback. Values are recursed, not filtered to str.
    assert _committee_cell_text({"unrelated": "x"}) == "x"
    assert _committee_cell_text({"organization": "Research Committee"}) == "Research Committee"
    assert _committee_cell_text({"organization": "Z", "role": "Chair"}) == "Z; Chair"
    # non-str values are stringified into the join per the module invariant
    assert _committee_cell_text({"organization": "Z", "count": 3}) == "Z; 3"
    assert _committee_cell_text({"organization": {"name": "Z"}}) == "Z"
    assert _committee_cell_text({"count": 3}) == "3"
    assert _committee_cell_text({"a": None, "b": ""}) == ""


def test_list_of_records_joins_names():
    v = [
        {"committee_name": "Faculty Senate Research Policy Committee", "role": "Member"},
        {"committee_name": "HPE Conference Committee", "role": "Chair"},
    ]
    out = _committee_cell_text(v)
    assert "Faculty Senate Research Policy Committee" in out
    assert "HPE Conference Committee" in out
    # never returns a non-str (the thing that crashed the document)
    assert isinstance(out, str)


@pytest.mark.parametrize("v", [
    None, "", "x", 0, 1, 1.5, True, {}, [], {"a": 1}, [1, {"b": [2]}],
    {"a": {"b": {"c": "d"}}}, [None, "", "x"], object(),
])
def test_always_returns_str(v):
    assert isinstance(_committee_cell_text(v), str)


def test_scalar_fallback_stringifies():
    assert _committee_cell_text(3) == "3"
    assert _committee_cell_text(2.5) == "2.5"
    assert _committee_cell_text(True) == "True"
    assert _committee_cell_text(object()).startswith("<object object at")


def test_phone_slot_annotation_resolves():
    # Reviewer thread 1 guard: raises NameError on the pre-fix file, which
    # never imports Literal even though `_phone_cell_text`'s annotation
    # references it.
    hints = typing.get_type_hints(_phone_cell_text)
    assert hints["slot"] == typing.Literal["cell", "office", "home"]
