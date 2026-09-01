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
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import _committee_cell_text


def test_string_and_empty_passthrough():
    assert _committee_cell_text("Faculty Senate Committee") == "Faculty Senate Committee"
    assert _committee_cell_text("") == ""
    assert _committee_cell_text(None) == ""


def test_dict_pulls_name_like_key():
    assert _committee_cell_text({"committee_name": "Research Policy Committee", "role": "Member"}) \
        == "Research Policy Committee"
    assert _committee_cell_text({"name": "HPE Conference Committee"}) == "HPE Conference Committee"


def test_dict_without_name_like_key_joins_string_values(): # (#555)
    # No recognised key (committee_name/committee/activity/name/title): join
    # the dict's string values instead of dropping it, matching
    # `_address_cell_text`'s "never skipped" fallback.
    assert _committee_cell_text({"unrelated": "x"}) == "x"
    assert _committee_cell_text({"organization": "Research Committee"}) == "Research Committee"
    assert _committee_cell_text({"organization": "Z", "role": "Chair"}) == "Z; Chair"
    # non-string values are not stringified into the join
    assert _committee_cell_text({"organization": "Z", "count": 3}) == "Z"
    # nothing recognisable at all still returns ""
    assert _committee_cell_text({"count": 3}) == ""


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


if __name__ == "__main__":
    test_string_and_empty_passthrough()
    test_dict_pulls_name_like_key()
    test_list_of_records_joins_names()
    print("OK")
