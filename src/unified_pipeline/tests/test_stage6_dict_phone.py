"""Regression guard for issue #450: a structured phone lost every number.

Same defect family as the dict address (#442). Stage 4 stores raw LLM JSON and
`coerce_field_value_types` leaves dicts intact, so a two-column contact block can
arrive at stage 6 as ``{"cell": ..., "office": ..., "fax": ...}``.

Phone never crashed the way address did, because `_set_cell_text` stringifies --
so instead of losing the document it failed two quieter ways:

1. On web147 the entry text says "Home", so the raw-text routing sent the whole
   dict to `home_phone`. The WCM template has exactly two phone rows, Office
   telephone and Cell phone -- there is no home row -- so all three numbers were
   dropped.
2. Had the text said "cell", the dict's Python repr would have been written into
   a Word cell verbatim.

There is also a latent trap: ``';' in extracted_phone`` on a dict is a KEY
lookup, not a substring test. It returned False, which is the only reason the
``.split(';')`` below it had not already raised the #442 crash for phones.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_dict_phone.py -p no:cacheprovider

Self-contained: no DB, no network, no PII (numbers below are invented).
"""

import json
import sys
from pathlib import Path

from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    WCMTemplateGenerator,
    _labels_its_own_phone_slots,
    _phone_cell_text,
)


def test_string_phone_is_returned_untouched():
    """The 75 string phones in the corpus must render byte-identically."""
    assert _phone_cell_text("(212) 555-0100", 'office') == "(212) 555-0100"
    assert _phone_cell_text(None, 'cell') == ""
    assert _phone_cell_text("", 'office') == ""


def test_dict_phone_splits_into_the_matching_slot():
    value = {"cell": "(617) 555-0142", "office": "(919) 555-0161",
             "fax": "(919) 555-0177"}
    assert _phone_cell_text(value, 'cell') == "(617) 555-0142"
    assert _phone_cell_text(value, 'office') == "(919) 555-0161"
    assert _labels_its_own_phone_slots(value) is True


def test_alternate_key_names_are_matched():
    """Stage 4 extracts with no schema, so the vocabulary is unbounded."""
    assert _phone_cell_text({"mobile_phone_primary": "(617) 555-0142"}, 'cell') \
        == "(617) 555-0142"
    assert _phone_cell_text({"work_phone": "(919) 555-0161"}, 'office') \
        == "(919) 555-0161"


def test_unlabelled_dict_is_joined_rather_than_dropped():
    value = {"primary": "(212) 555-0100", "secondary": "(212) 555-0101"}
    assert _labels_its_own_phone_slots(value) is False
    assert _phone_cell_text(value, 'office') == "(212) 555-0100; (212) 555-0101"


def test_every_return_is_a_string_so_no_repr_can_reach_a_cell():
    shapes = [None, "", "  ", "(212) 555-0100", 5551234, [], {},
              {"cell": None}, {"office": {"x": ["y"]}}, [{"cell": "1"}]]
    for value in shapes:
        for slot in ('cell', 'office', 'home'):
            out = _phone_cell_text(value, slot)
            assert isinstance(out, str), f"{value!r} -> {out!r}"
            assert "{" not in out and "[" not in out, f"repr leaked: {out!r}"


def _render(tmp_path, entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": "TESTAA", "entries": entries}))
    gen.generate(str(ip), str(op), research_summary_path=None)
    doc = Document(str(op))
    contact = {}
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if len(cells) >= 2 and cells[0].strip().endswith(':'):
                contact.setdefault(cells[0].strip(), cells[1].strip())
    return contact


def test_web147_shape_renders_its_numbers_instead_of_dropping_them(tmp_path):
    """The real corpus shape: text says Home, the dict says cell/office/fax."""
    contact = _render(tmp_path, [
        {"text": "Business Address and Phone   Home Address and Phone",
         "taxonomy_code": "A", "element_idx_start": 0,
         "extracted_fields": {
             "name": "Jane Q. Public, MD",
             "phone": {"cell": "(617) 555-0142", "office": "(919) 555-0161",
                       "fax": "(919) 555-0177"}}},
    ])
    assert contact.get("Office telephone:") == "(919) 555-0161", \
        "the office number was dropped (#450)"
    assert contact.get("Cell phone:") == "(617) 555-0142", \
        "the cell number was dropped (#450)"


def test_no_dict_repr_reaches_the_document(tmp_path):
    contact = _render(tmp_path, [
        {"text": "Cell phone and office", "taxonomy_code": "A",
         "element_idx_start": 0,
         "extracted_fields": {"name": "Jane Q. Public, MD",
                              "phone": {"cell": "(617) 555-0142"}}},
    ])
    for value in contact.values():
        assert "{" not in value and "'" not in value.replace("'s", ""), \
            f"python repr leaked into the document: {value!r}"


def test_home_labelled_string_phone_is_unchanged(tmp_path):
    """web113 regression guard: a home number must NOT take the office row, or
    it pre-empts the real business number from a later entry."""
    contact = _render(tmp_path, [
        {"text": "HOME PHONE: (412) 555-0171", "taxonomy_code": "A",
         "element_idx_start": 0,
         "extracted_fields": {"name": "Jane Q. Public, MD",
                              "phone": "(412) 555-0171"}},
        {"text": "BUSINESS PHONE: (412) 555-0188", "taxonomy_code": "A",
         "element_idx_start": 1,
         "extracted_fields": {"phone": "(412) 555-0188"}},
    ])
    assert contact.get("Office telephone:") == "(412) 555-0188", \
        "the home number pre-empted the business number"


def test_a_mixed_dict_routes_its_slots_and_drops_the_rest(tmp_path):
    """Review question on #463: what happens to keys that name no slot.

    The answer has to be "the slots still route correctly and the rest is
    dropped", because the WCM template has exactly two phone rows. Requiring
    every key to be recognized before taking the slot path would send this dict
    down the join branch and concatenate the fax and the note into whichever
    row asked first."""
    contact = _render(tmp_path, [
        {"text": "Contact", "taxonomy_code": "A", "element_idx_start": 0,
         "extracted_fields": {
             "name": "Jane Q. Public, MD",
             "phone": {"cell": "(617) 555-0142", "note": "call after 5",
                       "fax": "(617) 555-0199", "office": "(617) 555-0177"}}},
    ])
    assert contact.get("Cell phone:") == "(617) 555-0142"
    assert contact.get("Office telephone:") == "(617) 555-0177"
    for label, value in contact.items():
        assert "555-0199" not in value, f"the fax reached {label}"
        assert "call after 5" not in value, f"the note reached {label}"


if __name__ == "__main__":
    import tempfile
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            if _fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    _fn(Path(d))
            else:
                _fn()
    print("OK")
