"""Guard for #812: every `.text =` site in `service.py` must survive a
non-str stage-4 field value.

web204 (2026-09-11 corpus batch, the largest CV in it at 2,938 entries) lost
its entire WCM output because one Q4C entry ("Editorial Board Member for:
<journal>, <year> - <year>; <journal>, <year> - Present; ..." -- several editorial
boards fused into one entry) came back from stage 4 with `journal_name` as a
list of `{"name": ..., "start_date": ..., "end_date": ...}` dicts instead of a
string. `_fill_other_service` assigned that straight to `row.cells[1].text`;
python-docx's `text` setter iterates the value and a dict element reaches
`add_char`, which raises `TypeError`. The CLI driver caught the exception,
printed `Warning: Stage 6 failed`, and produced no docx; the web orchestrator
raises and fails the run outright.

`stage6/normalization/fields.py` already had a recursing list/dict -> text
coercer (`_committee_cell_text`, re-exported here as `_cell_text` since
"committee" misleads at these call sites -- `role`, `organization`, `dates`,
`journal` are not committees) for exactly this failure mode, but the service
renderer's four writers (`_fill_service_boards`, `_add_extramural_row`,
`_fill_journal_reviewing`, `_fill_other_service`) never routed their field
values through it. This is the CLASS fix, not a one-field patch: every value
reaching a `.text =` assignment across all four writers now goes through
`_cell_text`, and `journal_name` specifically -- the one field observed
carrying per-record dates alongside its per-record name -- goes through the
new `_journal_name_cell_text` instead, which renders "<name> (<start>-<end>)"
per record when every record supplies both, and falls through to `_cell_text`
(dropping the dates, never the content) for any other shape.

`_journal_name_cell_text` and `_cell_text` are module-level functions, not
mixin methods, per `test_stage6_mixin_name_collisions.py`'s standing rule: a
name added to a section mixin can silently shadow -- or be shadowed by --
another mixin's method of the same name through Python's MRO, which is
exactly how the `_add_table_row` collision took down 52/66 corpus CVs with
the full suite green (see that test's docstring). Neither name is defined by
any of the other 22 section mixins (confirmed by grep below).

Round 2 (verify-D-812, findings 1-3) closed three gaps this file's first
pass left open:

- Finding 1 (BLOCKER): the `dates` cell was never coerced -- each of
  `_fill_service_boards`, `_fill_extramural_leadership`, `_fill_journal_
  reviewing` and `_fill_other_service` calls `format_date_range()` directly
  on raw `fields.get('start_date'/'end_date')`. `format_date_for_section`
  only `str()`s its input rather than raising, so a structured date rendered
  its Python repr into the cell instead of crashing -- invisible to every
  test in Part 3, which only varied non-date fields.
- Finding 2 (BLOCKER, test coverage): three of the coercions this ticket's
  first pass ALREADY added had no test that would fail if their `_cell_text`
  wrapper were deleted -- `role` in `_fill_other_service`, `panel_name`, and
  `role` in `_add_extramural_row` (mutants M1/M9/M12 in verify-D-812, all
  survived with the rest of the suite green).
- Finding 3 (MAJOR): `_route_q2_entries` (service.py, the Q2 -> Q4D reroute
  every Q2 entry passes through in `_fill_service` BEFORE
  `_fill_service_boards` ever runs) called `.lower()` directly on raw
  `committee_name`/`role`/`organization`, so a structured value in any of
  those three fields on a Q2 entry crashed stage 6 before it ever reached
  the (already-fixed) `.text =` sites -- a real gap in "fix the class", not
  a `.text =` site technicality. Now coerced there too.

Part 4 below covers all three. `test_q2_structured_committee_name_is_
coerced_when_called_directly` still calls `_fill_service_boards` directly,
bypassing the router, to isolate that function's own coercion from the
router's (now also fixed, and separately tested by
`test_q2_list_committee_name_survives_router_end_to_end`).

    python3 -m pytest src/unified_pipeline/tests/test_stage6_service_field_coercion.py -p no:cacheprovider

Self-contained: no DB, no network, no LLM, no PII. Every fixture value is
fictional.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest
from docx import Document

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.fields import _cell_text, _committee_cell_text  # noqa: E402
from unified_pipeline.stage6.sections.service import (  # noqa: E402
    _join_names,
    _journal_names_contain,
    _journal_name_cell_text,
    _organization_left_in_text,
    _other_service_organization_text,
    _reviewing_org_and_committee_text,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


# ---------------------------------------------------------------------------
# Part 1: `_journal_name_cell_text` unit tests (§6.9 case list).
#
#   str                              -> test_str_passes_through_unchanged
#   None                             -> test_none_becomes_empty_string
#   list of str                      -> test_list_of_str_generic_joins
#   list of dict, name + dates       -> test_list_of_dict_with_dates_joins_ranges
#   list of dict, no dates           -> test_list_of_dict_without_dates_falls_back
#   dict (single, not list)          -> test_single_dict_extracts_name_like_key
#   nested list                      -> test_nested_list_recurses
#   int                              -> test_int_is_stringified
#
# Two more beyond the required eight, both real shapes web204-class CVs can
# produce and both worth pinning explicitly:
#   one record has only a start_date -> test_start_date_only_still_ranges
#   one of several records lacks a date -> test_one_record_missing_date_falls_back_whole
# ---------------------------------------------------------------------------

Q4C = 'Q4C'


def test_str_passes_through_unchanged():
    assert _journal_name_cell_text("Fictional Journal of Widget Science", Q4C) == \
        "Fictional Journal of Widget Science"


def test_none_becomes_empty_string():
    assert _journal_name_cell_text(None, Q4C) == ""


def test_list_of_str_generic_joins():
    assert _journal_name_cell_text(
        ["Fictional Journal A", "Fictional Journal B"], Q4C
    ) == "Fictional Journal A; Fictional Journal B"


def test_list_of_dict_with_dates_joins_ranges():
    """The exact #812 shape: web204's Q4C multi-journal entry."""
    value = [
        {"name": "Fictional Journal of Widget Science", "start_date": "1901", "end_date": "1902"},
        {"name": "Fictional Advances in Gadget Technology", "start_date": "1950", "end_date": "present"},
    ]
    assert _journal_name_cell_text(value, Q4C) == (
        "Fictional Journal of Widget Science (1901-1902); "
        "Fictional Advances in Gadget Technology (1950-Present)"
    )


def test_list_of_dict_without_dates_falls_back():
    """No record supplies a date: fall through to the generic coercer, which
    joins names only -- it never invents a date, and it never drops the
    record for lacking one."""
    value = [{"name": "Fictional Journal A"}, {"name": "Fictional Journal B"}]
    assert _journal_name_cell_text(value, Q4C) == "Fictional Journal A; Fictional Journal B"


def test_single_dict_extracts_name_like_key():
    assert _journal_name_cell_text({"journal": "Fictional Journal Q"}, Q4C) == "Fictional Journal Q"
    assert _journal_name_cell_text({"name": "Fictional Journal R"}, Q4C) == "Fictional Journal R"


def test_nested_list_recurses():
    value = [["Fictional Journal A", "Fictional Journal B"], "Fictional Journal C"]
    assert _journal_name_cell_text(value, Q4C) == \
        "Fictional Journal A; Fictional Journal B; Fictional Journal C"


def test_int_is_stringified():
    assert _journal_name_cell_text(3, Q4C) == "3"


def test_start_date_only_still_ranges():
    """One record supplies only start_date -- `format_date_range` reads that
    as ongoing, matching every other date cell in this file."""
    value = [{"name": "Fictional Journal A", "start_date": "2010", "end_date": ""}]
    assert _journal_name_cell_text(value, Q4C) == "Fictional Journal A (2010-Present)"


def test_one_record_missing_date_falls_back_whole():
    """Ticket spec: "when the dicts carry name + start_date/end_date;
    otherwise the coercer's generic join" -- one record. One record without
    either date fails the all-records check, so ALL records fall back to the
    plain name join, including the one that did have a date: the ticket
    describes one fallback for the whole value, not a per-record mix."""
    value = [
        {"name": "Fictional Journal A", "start_date": "1901", "end_date": "1902"},
        {"name": "Fictional Journal B"},
    ]
    assert _journal_name_cell_text(value, Q4C) == "Fictional Journal A; Fictional Journal B"


def test_record_with_no_name_like_key_falls_back_whole():
    """An empty dict has no recognised key and no dict-values to join, so
    `_cell_text` returns "" for it -- that fails the "must have a name"
    check and the whole value falls back to the generic coercer, which drops
    the contentless record and keeps the real one."""
    value = [
        {"name": "Fictional Journal A", "start_date": "1901", "end_date": "1902"},
        {},
    ]
    assert _journal_name_cell_text(value, Q4C) == "Fictional Journal A"


def test_empty_list_and_empty_string_are_falsy_not_crashes():
    assert _journal_name_cell_text([], Q4C) == ""
    assert _journal_name_cell_text("", Q4C) == ""


# ---------------------------------------------------------------------------
# Part 2: no other section mixin defines either new module-level name as a
# method (the #625 `_add_table_row` collision class this ticket must not
# repeat -- see module docstring and test_stage6_mixin_name_collisions.py).
# ---------------------------------------------------------------------------

def test_new_helper_names_are_not_defined_by_any_mixin():
    for base in WCMTemplateGenerator.__bases__:
        assert '_journal_name_cell_text' not in vars(base), (
            f"{base.__name__} defines _journal_name_cell_text -- it must stay "
            f"a module-level function in service.py, not a mixin method"
        )
        assert '_cell_text' not in vars(base), (
            f"{base.__name__} defines _cell_text -- it must stay the "
            f"module-level re-export in normalization/fields.py"
        )


def test_cell_text_is_the_committee_coercer_alias() -> None:
    """`_cell_text` is `_committee_cell_text` itself, not a second implementation
    (#812 review, r4025163438): the str-on-every-branch guarantee that lets
    `_route_q2_entries` call `.lower()` on it is proven by
    test_stage6_committee_structured_fields.py::test_always_returns_str, and
    this identity is what makes that proof apply here."""
    assert _cell_text is _committee_cell_text


# ---------------------------------------------------------------------------
# Part 3: end-to-end regression guards through the REAL composed
# `WCMTemplateGenerator`, per the ticket's requirement #1 -- exact cell text,
# not merely "did not raise".
# ---------------------------------------------------------------------------

def _entry(code, **fields):
    return {"text": " ".join(str(v) for v in fields.values()),
            "taxonomy_code": code, "element_idx_start": 0,
            "extracted_fields": fields}


def _render(tmp_path, entries, uid="TEST12"):
    gen = WCMTemplateGenerator(verbose=False)
    gen._reconsider_appendix_entries = lambda: None
    ip, op = tmp_path / "in.json", tmp_path / "out.docx"
    ip.write_text(json.dumps({"document_uid": uid, "entries": entries}))
    gen.generate(str(ip), str(op), research_summary_path=None)
    return Document(str(op))


def _rows_containing(doc, needle):
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text for c in row.cells]
            if any(needle in c for c in cells):
                yield cells


def test_q4c_list_of_dict_journal_name_renders_joined_text_end_to_end(tmp_path):
    """The bug as filed: before the fix this raised `TypeError: 'in <string>'
    requires string as left operand, not dict` (a slightly different crash
    site than #812's traceback -- `role.lower() ... in organization.lower()`
    at this template's 2-column Editorial Board table -- but the same
    uncoerced-list-of-dict root cause) and produced no docx at all."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4C", role="Editorial Board Member", journal_name=[
            {"name": "Fictional Journal of Widget Science", "start_date": "1901", "end_date": "1902"},
            {"name": "Fictional Advances in Gadget Technology", "start_date": "1950", "end_date": "present"},
        ]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal of Widget Science"))
    assert len(rows) == 1, f"expected exactly one matching row, found {rows}"
    assert rows[0][0] == (
        "Editorial Board Member, Fictional Journal of Widget Science (1901-1902); "
        "Fictional Advances in Gadget Technology (1950-Present)"
    )


def test_q4c_plain_string_journal_name_is_byte_identical(tmp_path):
    """The identity guarantee: a CV that never had this shape must render
    exactly as it did before the coercion was added."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4C", role="Editorial Board Member",
               journal_name="Fictional Journal of Widget Science",
               start_date="2005", end_date="2009"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal of Widget Science"))
    assert len(rows) == 1
    assert rows[0][0] == "Editorial Board Member, Fictional Journal of Widget Science"
    assert rows[0][1] == "2005-2009"


def test_q4d_list_of_dict_journal_name_with_dates_end_to_end(tmp_path):
    """`_fill_journal_reviewing` (Q4D) reads the same `journal_name` field as
    `_fill_other_service` (Q4C) -- the date-aware coercion applies there too,
    not only at the field #812's traceback happened to hit."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4D", journal_name=[
            {"name": "Fictional Journal C", "start_date": "2010", "end_date": "2011"},
            {"name": "Fictional Journal D", "start_date": "2015", "end_date": "present"},
        ]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal C"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Journal C (2010-2011); Fictional Journal D (2015-Present)"


def test_q4d_list_of_dict_journal_name_without_dates_end_to_end(tmp_path):
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4D", journal_name=[{"name": "Fictional Journal C"}, {"name": "Fictional Journal D"}]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal C"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Journal C; Fictional Journal D"


def test_q1_list_organization_end_to_end(tmp_path):
    """`_fill_extramural_leadership` -> `_add_extramural_row`: organization as
    a list of str, the shape #812's issue text names as already observed on
    the corpus for other Q-coded fields (`roles`, `committee_memberships`)."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization=["Fictional Org One", "Fictional Org Two"],
               role="Treasurer", start_date="1992", end_date="1997"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Org One"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Org One; Fictional Org Two"
    assert rows[0][1] == "Treasurer"
    assert rows[0][2] == "1992-1997"


def test_q3_dict_agency_and_panel_name_end_to_end(tmp_path):
    """`_fill_other_service`'s Q3 branch: `agency` as a dict, `panel_name` a
    plain string appended onto it."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q3", agency={"name": "Fictional Agency"}, panel_name="Fictional Panel"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Agency"))
    assert len(rows) == 1
    assert rows[0][1] == "Fictional Agency - Fictional Panel"


def test_q2_structured_committee_name_is_coerced_when_called_directly(tmp_path):
    """`_fill_service_boards` itself coerces correctly; called directly to
    isolate that from the round-1 `_route_q2_entries` defect this ticket's
    round 2 fixes below (`test_q2_list_committee_name_survives_router_end_to_end`).
    Every Q2 entry goes through that router first in the real pipeline."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    entries = [_entry(
        "Q2",
        committee_name=["Fictional Board Alpha", "Fictional Board Beta"],
        role="Member", start_date="2010", end_date="2012",
    )]
    gen._fill_service_boards(entries)
    rows = list(_rows_containing(gen.doc, "Fictional Board Alpha"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Board Alpha; Fictional Board Beta"
    assert rows[0][1] == "Member"
    assert rows[0][3] == "2010-2012"


# ---------------------------------------------------------------------------
# Part 4 (round 2, verify-D-812 findings 1-3): the dates parts, the three
# uncovered role/panel_name coercion sites (M1/M9/M12), and the Q2 router
# (`_route_q2_entries`) coercion.
# ---------------------------------------------------------------------------

def test_q2_list_start_date_is_coerced_when_called_directly():
    """Finding 1 (BLOCKER): `_fill_service_boards` computed
    `format_date_range(fields.get('start_date') or '', ...)` with no
    coercion -- `format_date_for_section` only `str()`s its input, so a
    list/dict start_date rendered its Python repr into the dates cell
    instead of raising or joining. Both parts are structured (list) so this
    kills a mutant dropping the `_cell_text` wrapper at EITHER read."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    entries = [_entry(
        "Q2",
        committee_name="Fictional Board Gamma",
        role="Member", start_date=["1930"], end_date=["1931"],
    )]
    gen._fill_service_boards(entries)
    rows = list(_rows_containing(gen.doc, "Fictional Board Gamma"))
    assert len(rows) == 1
    assert rows[0][3] == "1930-1931"


def test_q1_list_start_date_is_coerced_end_to_end(tmp_path):
    """Finding 1 (BLOCKER): `_fill_extramural_leadership` calls
    `format_date_range()` directly on raw `fields.get('start_date'/'end_date')`
    BEFORE `_add_extramural_row` ever runs -- that function's own coercion
    (killing M3/M12) never sees the damage already done upstream. Both
    parts are structured (list) so this kills a mutant dropping the
    `_cell_text` wrapper at EITHER read."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization="Fictional Org Three", role="Fictional Trustee",
               start_date=["1940"], end_date=["1941"]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Org Three"))
    assert len(rows) == 1
    assert rows[0][2] == "1940-1941"


def test_q4d_dict_start_date_is_coerced_end_to_end(tmp_path):
    """Finding 1 (BLOCKER), the ticket's own dict-date example:
    `start_date={"year": "1992"}` has no key `_COMMITTEE_NAME_KEYS`
    recognises, so `_cell_text` falls back to joining the dict's own values
    -- yielding the bare year, not a repr. `end_date` is a list, so together
    this kills a mutant dropping the `_cell_text` wrapper at EITHER read.
    Exercises `_fill_journal_reviewing`'s date coercion (a plain
    `organization`, not `journal_name`, so the journal cell itself is
    unaffected by this ticket's other fix)."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4D", organization="Fictional Journal E",
               start_date={"year": "1992"}, end_date=["1997"]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal E"))
    assert len(rows) == 1
    assert rows[0][1] == "1992-1997"


def test_q3_list_start_date_is_coerced_end_to_end(tmp_path):
    """Finding 1 (BLOCKER): `_fill_other_service`'s own
    `format_date_range()` call reads raw `fields.get('start_date'/'end_date')`.
    Both parts are structured (list) so this kills a mutant dropping the
    `_cell_text` wrapper at EITHER read."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q3", agency="Fictional Agency Two",
               start_date=["1961"], end_date=["1962"]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Agency Two"))
    assert len(rows) == 1
    assert rows[0][2] == "1961-1962"


def test_q4c_list_of_dict_journal_name_per_item_list_date_is_coerced():
    """Finding 1's own class one level deeper: a per-item `start_date`/
    `end_date` inside a `journal_name` list-of-dicts record can itself be
    structured (not just the top-level `fields.get('start_date')` reads
    above). Both parts are structured (list) so this kills a mutant dropping
    the `_cell_text` wrapper at EITHER read. Added defensively alongside the
    other dates sites -- not named by the ticket's three call sites, but the
    same defect class in code this ticket already touches."""
    value = [{"name": "Fictional Journal F", "start_date": ["1970"], "end_date": ["1971"]}]
    assert _journal_name_cell_text(value, Q4C) == "Fictional Journal F (1970-1971)"


def test_q3_list_role_is_coerced_end_to_end(tmp_path):
    """Finding 2 (BLOCKER, M1): `_fill_other_service`'s `role = fields.get('role', '')`
    read had no test that would fail if its `_cell_text` wrapper were dropped
    -- mutant M1 survived verify-D-812 with 49 tests passing. Exercises the
    3-column branch's `row.cells[0].text = role`."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q3", agency="Fictional Agency Three",
               role=["Fictional Role Chair", "Fictional Role Member"]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Agency Three"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Role Chair; Fictional Role Member"


def test_q3_list_panel_name_is_coerced_end_to_end(tmp_path):
    """Finding 2 (BLOCKER, M9): `panel_name` had no test that would fail if
    its `_cell_text` wrapper were dropped -- mutant M9 survived verify-D-812.
    `organization`/`committee_name`/`agency`/`journal_name` are all left
    empty so `panel_name` alone becomes the organization cell
    (`_fill_other_service`'s `if not organization: organization = panel_name`
    branch), isolating this coercion from the OR-chain's own."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q3", panel_name=["Fictional Panel A", "Fictional Panel B"]),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Panel A"))
    assert len(rows) == 1
    assert rows[0][1] == "Fictional Panel A; Fictional Panel B"


def test_q1_list_role_is_coerced_end_to_end(tmp_path):
    """Finding 2 (BLOCKER, M12): `_add_extramural_row`'s `role` coercion had
    no test that would fail if its `_cell_text` wrapper were dropped --
    mutant M12 survived verify-D-812 (the existing Q1 test only varied
    `organization`)."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization="Fictional Org Four",
               role=["Fictional Role A", "Fictional Role B"],
               start_date="1950", end_date="1951"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Org Four"))
    assert len(rows) == 1
    assert rows[0][1] == "Fictional Role A; Fictional Role B"


def test_q2_list_committee_name_survives_router_end_to_end(tmp_path):
    """Finding 3 (MAJOR): `_route_q2_entries` ran `.lower()` directly on raw
    `committee_name`/`role`/`organization` -- a structured `committee_name`
    raised `AttributeError: 'list' object has no attribute 'lower'` there,
    before `_fill_service_boards` (or its own, already-fixed, coercion) ever
    ran. Drives the REAL `_fill_service` entrypoint end to end through
    `generate()` -- unlike `test_q2_structured_committee_name_is_coerced_when_
    called_directly` above, which calls `_fill_service_boards` directly and
    so cannot exercise the router at all."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q2", committee_name=["Fictional Board Delta", "Fictional Board Epsilon"],
               role="Member", start_date="1980", end_date="1981"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Board Delta"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Board Delta; Fictional Board Epsilon"


def test_q2_list_role_and_organization_survive_router_and_render_end_to_end(tmp_path):
    """verify-D-812-R2 finding 1 (re-rated BLOCKER): four coercions the PR
    adds had no test that would fail if dropped -- `role` (M14) and
    `organization` (M15) in `_fill_service_boards` (service.py:622-623,
    round-1 code), and `role` (R2) and `organization` (R3) in
    `_route_q2_entries` (service.py:342,344, round-2 code). One entry with
    BOTH fields as lists, driven through the REAL `_fill_service` entrypoint
    via `generate()`, kills all four: if a router coercion is dropped,
    `.lower()` on the raw list raises `AttributeError` before
    `_fill_service_boards` ever runs (the #812 crash class); if a
    `_fill_service_boards` coercion is dropped, the raw list reaches
    `row.cells[n].text =` unjoined (the #660 silent-concatenation class)."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q2", committee_name="Fictional Board Zeta",
               role=["Fictional Role A", "Fictional Role B"],
               organization=["Fictional Org P", "Fictional Org Q"],
               start_date="1960", end_date="1962"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Board Zeta"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Board Zeta"
    assert rows[0][1] == "Fictional Role A; Fictional Role B"
    assert rows[0][2] == "Fictional Org P; Fictional Org Q"
    assert rows[0][3] == "1960-1962"


def _rerouted_q2(**fields):
    """A Q2 entry the router sends to `_fill_journal_reviewing`: no
    `journal_name`, and a text that matches `REVIEWER_PATTERNS`."""
    return {"text": "Ad hoc reviewer, " + " ".join(str(v) for v in fields.values()),
            "taxonomy_code": "Q2", "element_idx_start": 0,
            "extracted_fields": fields}


def test_rerouted_q2_committee_name_is_not_shadowed_by_organization(tmp_path):
    """#471: a rerouted Q2 entry has no `journal_name`, and the old
    `organization or committee_name` chain rendered only the generic
    organization -- the panel name never reached the docx."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _rerouted_q2(committee_name="Fictional Program Review Panel",
                     organization="Fictional Heart Society",
                     start_date="2011", end_date="2012"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Program Review Panel"))
    assert len(rows) == 1
    assert rows[0][0] == "Fictional Program Review Panel, Fictional Heart Society"


@pytest.mark.parametrize("fields, expected", [
    # one contains the other as whole words -> the longer one alone
    ({"organization": "NIH", "committee_name": "NIH Study Section"}, "NIH Study Section"),
    ({"organization": "NIH Study Section", "committee_name": "nih"}, "NIH Study Section"),
    ({"organization": "Fictional Society", "committee_name": "fictional society"}, "Fictional Society"),
    # substring collisions are NOT containment -> both rendered
    ({"organization": "NIH", "committee_name": "Nihilism Panel"}, "Nihilism Panel, NIH"),
    ({"organization": "NCI", "committee_name": "Clinical Review"}, "Clinical Review, NCI"),
    ({"organization": "AHA", "committee_name": "Ahab Award Panel"}, "Ahab Award Panel, AHA"),
    ({"organization": "APA", "committee_name": "Papal Studies"}, "Papal Studies, APA"),
    ({"organization": "NIH", "committee_name": "NIH-funded Panel"}, "NIH-funded Panel"),
    ({"organization": "NSF.", "committee_name": "CAREER awards"}, "CAREER awards, NSF."),
    ({"organization": "U.S. Army", "committee_name": "Army Research Board"}, "Army Research Board, U.S. Army"),
    ({"organization": "R+D", "committee_name": "R+D Panel"}, "R+D Panel"),
    ({"organization": "NIH\u2013NCI", "committee_name": "Site Visit"}, "Site Visit, NIH\u2013NCI"),
    # one side absent -> unchanged from the old `organization or committee_name`
    ({"organization": "Fictional Society"}, "Fictional Society"),
    ({"committee_name": "Fictional Panel"}, "Fictional Panel"),
    ({"organization": "", "committee_name": "Fictional Panel"}, "Fictional Panel"),
    ({}, ""),
])
def test_reviewing_org_and_committee_text(fields, expected):
    assert _reviewing_org_and_committee_text(fields) == expected


@pytest.mark.parametrize("code", ["Q4B", "Q4C"])
def test_editorial_journal_name_is_not_shadowed_by_organization(tmp_path, code):
    """#471 (web083 shape): on a Q4B/Q4C row a populated `organization` hid
    `journal_name`, the field naming the journal."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry(code, role="Associate Editor",
               organization="Fictional Endosurgery Group",
               journal_name="Fictional Guidelines for Fictional Disease",
               start_date="2015", end_date="2016"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Guidelines for Fictional Disease"))
    assert len(rows) == 1
    assert rows[0][0] == ("Associate Editor, Fictional Guidelines for Fictional Disease, "
                          "Fictional Endosurgery Group")
    assert rows[0][1] == "2015-2016"


@pytest.mark.parametrize("code, fields, expected", [
    # equal after case/punctuation/whitespace folding -> the organization, once
    ("Q4B", {"organization": "Fictional Press", "journal_name": "fictional  press."},
     "Fictional Press"),
    # containment must NOT collapse a journal into its society/publisher (#471 N1)
    ("Q4B", {"organization": "Fictional Press", "journal_name": "Fictional Press Journal"},
     "Fictional Press Journal"),
    ("Q4B", {"journal_name": "Neurology", "organization": "American Academy of Neurology"},
     "Neurology, American Academy of Neurology"),
    ("Q4C", {"journal_name": "Cell", "organization": "Cell Press"}, "Cell, Cell Press"),
    ("Q4C", {"journal_name": "Pediatrics", "organization": "American Academy of Pediatrics"},
     "Pediatrics, American Academy of Pediatrics"),
    ("Q4B", {"journal_name": "Science",
             "organization": "American Association for the Advancement of Science"},
     "Science, American Association for the Advancement of Science"),
    # journal contains the org -> journal alone, never the reverse (re-verify)
    ("Q4B", {"organization": "Nature",
             "journal_name": [{"name": "Nature", "start_date": "2003", "end_date": "2004"}]},
     "Nature (2003-2004)"),
    ("Q4B", {"organization": "Nature",
             "journal_name": [{"name": "Science", "start_date": "2001", "end_date": "2002"},
                              {"name": "Nature", "start_date": "2003", "end_date": "2004"}]},
     "Science (2001-2002); Nature (2003-2004)"),
    ("Q4C", {"journal_name": "Journal of the American Heart Association",
             "organization": "American Heart Association"},
     "Journal of the American Heart Association"),
    ("Q4C", {"journal_name": "JAMA Network Open", "organization": "JAMA"}, "JAMA Network Open"),
    ("Q4C", {"journal_name": "The Lancet", "organization": "Lancet"}, "The Lancet"),
    ("Q4C", {"journal_name": "Lancet", "organization": "The Lancet"}, "Lancet"),
    ("Q4C", {"journal_name": "Nature", "organization": "NATURE"}, "NATURE"),
    # a date suffix on a list-of-records journal is not part of its name
    ("Q4B", {"organization": "2004",
             "journal_name": [{"name": "Nature", "start_date": "2003", "end_date": "2004"}]},
     "Nature (2003-2004), 2004"),
    # near-misses: org merely a prefix/substring of a journal word, or "The" alone
    ("Q4B", {"journal_name": "JAMA", "organization": "JAM"}, "JAMA, JAM"),
    ("Q4B", {"journal_name": "Natural History", "organization": "Nature"}, "Natural History, Nature"),
    ("Q4B", {"journal_name": "Annals of Thermodynamics", "organization": "The"},
     "Annals of Thermodynamics, The"),
    ("Q4B", {"journal_name": [{"name": "Nature", "start_date": "2003", "end_date": "2004"}],
             "organization": "Nat"},
     "Nature (2003-2004), Nat"),
    # whitespace-only / falsy sides count as absent (N2, N3)
    ("Q4B", {"organization": "   ", "journal_name": "Fictional Annals"}, "Fictional Annals"),
    ("Q4B", {"organization": "Fictional Society", "journal_name": 0}, "Fictional Society"),
    ("Q4B", {"organization": "Fictional Society", "journal_name": []}, "Fictional Society"),
    ("Q4C", {"organization": "NIH", "journal_name": "Nihilism Review"}, "Nihilism Review, NIH"),
    ("Q4C", {"organization": "AAP", "journal_name": "Pediatric Papers"}, "Pediatric Papers, AAP"),
    ("Q4B", {"organization": "BMJ.", "journal_name": "BMJ Open"}, "BMJ Open, BMJ."),
    ("Q4B", {"organization": "Wiley\u2013Blackwell", "journal_name": "Fictional Annals"},
     "Fictional Annals, Wiley\u2013Blackwell"),
    ("Q4B", {"organization": "Fictional Society"}, "Fictional Society"),
    ("Q4B", {"journal_name": "Fictional Annals"}, "Fictional Annals"),
    ("Q4B", {"committee_name": "Fictional Panel", "journal_name": "Fictional Annals"},
     "Fictional Annals, Fictional Panel"),
    # non-editorial codes keep the old organization-wins chain
    ("Q4", {"organization": "Fictional Society", "journal_name": "Fictional Annals"},
     "Fictional Society"),
    ("Q3", {"agency": "Fictional Agency", "journal_name": "Fictional Annals"},
     "Fictional Agency"),
    ("Q4A", {"organization": "Fictional Society", "journal_name": "Fictional Annals"},
     "Fictional Society"),
])
def test_other_service_organization_text_editorial_join(code, fields, expected):
    assert _other_service_organization_text(fields, code) == expected


def test_journal_names_contain_empty_org_never_matches():
    assert _journal_names_contain("Fictional Annals", "") is False
    # an empty needle matches between two non-word chars -- must be guarded
    assert _journal_names_contain("Fictional - Annals", "") is False
    assert _journal_names_contain("Fictional - Annals", "   ") is False
    assert _journal_names_contain("Fictional Annals", "  ") is False
    assert _journal_names_contain("Fictional Annals", "The ") is False


def test_join_names_is_word_bounded_and_strips():
    assert _join_names("Ahab Panel", "AHA", collapse_contained=True) == "Ahab Panel, AHA"
    assert _join_names("Fictional Panel", "", collapse_contained=True) == "Fictional Panel"
    assert _join_names("", "", collapse_contained=True) == ""
    assert _join_names("Council", "   ", collapse_contained=True) == "Council"
    assert _join_names("  ", "Council", collapse_contained=False) == "Council"


@pytest.mark.parametrize("fields, expected", [
    # N2: whitespace-only side is absent, no trailing ", "
    ({"committee_name": "Council", "organization": "   "}, "Council"),
    ({"committee_name": "\t", "organization": "Fictional Society"}, "Fictional Society"),
    # N3: falsy non-string counts as absent, matching the old `or` chain
    ({"organization": True, "committee_name": 0}, "True"),
    ({"organization": 0, "committee_name": "Fictional Panel"}, "Fictional Panel"),
    ({"organization": [], "committee_name": "Fictional Panel"}, "Fictional Panel"),
    ({"organization": "Fictional Society", "committee_name": {}}, "Fictional Society"),
])
def test_reviewing_org_and_committee_text_blank_and_falsy(fields, expected):
    assert _reviewing_org_and_committee_text(fields) == expected


def test_rerouted_q2_with_only_blank_org_and_committee_falls_back_to_raw_text(tmp_path):
    """Whitespace-only organization AND committee_name are absent, exactly
    like missing fields: the row is rebuilt from the entry's raw text. On dev
    the blank organization won the `or` chain and the row was silently
    skipped, so this is a deliberate improvement (a row with real text is
    rendered, not dropped), pinned here."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        {"text": "Ad hoc reviewer, Fictional Raw Text Journal",
         "taxonomy_code": "Q2", "element_idx_start": 0,
         "extracted_fields": {"organization": "   ", "committee_name": " "}},
    ]
    doc = _render(tmp_path, entries)
    assert len(list(_rows_containing(doc, "Fictional Raw Text Journal"))) == 1


def test_two_column_dedupe_never_drops_the_journal_or_role(tmp_path):
    """The two-column other-service layout skips `organization` when
    `role[:20]` occurs in it. The joined `<journal>, <org>` text now feeds
    that check, so a role phrase sitting inside the journal title must not
    swallow the org, and the journal must stay visible. Renders end to end;
    `role` is always kept, so the assertion is that journal and org survive."""
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q4B", role="Associate Editor",
               organization="Fictional Society",
               journal_name="Fictional Journal of Associate Editor Studies",
               start_date="2015", end_date="2016"),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Journal of Associate Editor Studies"))
    assert len(rows) == 1
    assert "Fictional Society" in rows[0][0]
    assert rows[0][0].startswith("Associate Editor")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# #946: a start-only Q2/Q3 row is one year unless the source leaves it open.
# The entry's own text reaches `format_date_range` from both fillers; Q1 and
# Q4D are not point-in-time codes and keep reading a start-only row as
# ongoing.
# ---------------------------------------------------------------------------

def _with_text(entry, text):
    entry["text"] = text
    return entry


@pytest.mark.parametrize("text, expected", [
    ("2019\tMember, Fictional Board Delta", "2019"),
    ("2019-\tMember, Fictional Board Delta", "2019-Present"),
])
def test_q2_start_only_row_reads_its_own_source_text(text, expected):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    gen._fill_service_boards([_with_text(_entry(
        "Q2", committee_name="Fictional Board Delta", role="Member",
        start_date="2019", end_date=""), text)])
    rows = list(_rows_containing(gen.doc, "Fictional Board Delta"))
    assert len(rows) == 1
    assert rows[0][3] == expected


@pytest.mark.parametrize("text, expected", [
    ("2019 Fictional Agency Three study section", "2019"),
    ("Fictional Agency Three study section (2019-", "2019-Present"),
])
def test_q3_start_only_row_reads_its_own_source_text_end_to_end(tmp_path, text, expected):
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _with_text(_entry("Q3", agency="Fictional Agency Three",
                          start_date="2019", end_date=""), text),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Agency Three"))
    assert len(rows) == 1
    assert rows[0][2] == expected


def test_q1_start_only_row_keeps_present_end_to_end(tmp_path):
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        _entry("Q1", organization="Fictional Society Four", role="President",
               start_date="2019", end_date=""),
    ]
    doc = _render(tmp_path, entries)
    rows = list(_rows_containing(doc, "Fictional Society Four"))
    assert len(rows) == 1
    assert rows[0][-1] == "2019-Present"


# ---------------------------------------------------------------------------
# Part 5 (#983): the raw-text fallback cells are not cut at 100/150 characters.
# One row per fallback site of `service.py`: `_fill_service_boards` (Q2),
# `_fill_extramural_leadership` (Q1, with and without a role),
# `_fill_journal_reviewing` (Q4D) and `_fill_other_service` (Q3, Q4A, Q4C).
# ---------------------------------------------------------------------------

_LONG_RAW = ("Zorblax Trustee Council on Ferrous Metallurgy and Applied Kite Science "
             "and its Standing Subcommittee on Ceremonial Bunting Standards and Review "
             "of Historical Pennant Practice")


@pytest.mark.parametrize("code, fields, cell", [
    ("Q1", {}, 0),
    ("Q1", {"role": "Treasurer"}, 0),
    ("Q2", {}, 0),
    ("Q3", {}, 1),
    ("Q4A", {}, 0),
    ("Q4C", {}, 0),
    ("Q4D", {}, 0),
])
def test_raw_text_fallback_cell_is_the_whole_text(tmp_path, code, fields, cell):
    assert len(_LONG_RAW) > 150
    entries = [
        _entry("A", name="Jane Q. Public, MD"),
        {"text": _LONG_RAW, "taxonomy_code": code, "element_idx_start": 1,
         "extracted_fields": fields},
    ]
    rows = list(_rows_containing(_render(tmp_path, entries), "Zorblax"))
    assert len(rows) == 1
    assert rows[0][cell].strip() == _LONG_RAW


# ---------------------------------------------------------------------------
# Part 6 (#946): a Q1 row whose stage-4 fields name no organization no longer
# prints the whole raw entry line in the Organization cell.
# ---------------------------------------------------------------------------

def _q1_entry(text, **fields):
    return {"text": text, "taxonomy_code": "Q1", "element_idx_start": 1,
            "extracted_fields": fields}


def _q1_row(tmp_path, entry, needle):
    entries = [_entry("A", name="Jane Q. Public, MD"), entry]
    rows = list(_rows_containing(_render(tmp_path, entries), needle))
    assert len(rows) == 1
    return rows[0]


def test_q1_missing_organization_is_the_text_left_after_role_and_dates(tmp_path):
    row = _q1_row(
        tmp_path,
        _q1_entry("2022-present Fictional Gazette WoW\tSection Editor, Widgets",
                  role="Section Editor, Widgets", start_date="2022", end_date="present"),
        "Fictional Gazette")
    assert row == ["Fictional Gazette WoW", "Section Editor, Widgets", "2022-Present"]


def test_q1_role_that_is_the_whole_line_leaves_organization_blank(tmp_path):
    row = _q1_row(
        tmp_path,
        _q1_entry("2005-present Fictional Symposium Committee Chair",
                  role="Fictional Symposium Committee Chair",
                  start_date="2005", end_date="present"),
        "Fictional Symposium")
    assert row == ["", "Fictional Symposium Committee Chair", "2005-Present"]


def test_q1_year_the_dates_cell_does_not_show_stays_in_organization(tmp_path):
    row = _q1_row(
        tmp_path,
        _q1_entry("Board of Directors, Fictional Society\t\t2011-2013\tSecretary",
                  role="Secretary"),
        "Fictional Society")
    assert row[0] == "Board of Directors, Fictional Society, 2011-2013"
    assert row[1] == "Secretary"


@pytest.mark.parametrize("alias", ["journal_name", "program_name"])
def test_q1_rerouted_entry_names_its_organization_by_the_original_schema(tmp_path, alias):
    """A Q4B/K3 entry moved to Q1 keeps `journal_name`/`program_name`."""
    # The field's value differs from what the entry text would give, so the
    # test tells a row that read the field from one that read the text.
    row = _q1_row(
        tmp_path,
        _q1_entry("2022-present Gazette\tSection Editor",
                  role="Section Editor", start_date="2022", end_date="present",
                  **{alias: "Fictional Gazette WoW"}),
        "Fictional Gazette")
    assert row == ["Fictional Gazette WoW", "Section Editor", "2022-Present"]


@pytest.mark.parametrize("empty", ["", None])
def test_q1_empty_organization_falls_through_to_a_filled_alias(tmp_path, empty):
    """An empty `organization` does not shadow a filled alias field."""
    row = _q1_row(
        tmp_path,
        _q1_entry("2022-present Gazette\tSection Editor", organization=empty,
                  journal_name="Fictional Gazette WoW", role="Section Editor",
                  start_date="2022", end_date="present"),
        "Fictional Gazette")
    assert row[0] == "Fictional Gazette WoW"


def test_q1_organization_field_wins_over_the_aliases(tmp_path):
    row = _q1_row(
        tmp_path,
        _q1_entry("x", organization="Fictional Org Five", journal_name="Fictional Gazette",
                  role="Treasurer"),
        "Fictional Org Five")
    assert row[0] == "Fictional Org Five"


@pytest.mark.parametrize("text, role, start, end, expected", [
    # A role word inside a longer word is not removed.
    ("Chairman Fictional Society", "Chair", None, None, "Chairman Fictional Society"),
    # Two roles separated by ';' are each removed, in any order.
    ("Awards Committee Chair\tFictional Society\tResearch Chair",
     "Research Chair; Awards Committee Chair", None, None, "Fictional Society"),
    # Punctuation and spacing between the role's words may differ from the field.
    ("1993-1996 Co-founder.  Charter Member", "Co-founder, Charter Member", "1993", "1996", ""),
    # Only a range whose every year the fields carry is dropped.
    ("Fictional Society 2001-2004 Treasurer", "Treasurer", "2001", None,
     "Fictional Society 2001-2004"),
    ("2001- Fictional Society Treasurer", "Treasurer", "2001", None, "Fictional Society"),
    # A range with no end ("2001-") goes whole, even inside the line.
    ("Fictional 2001- Society Treasurer", "Treasurer", "2001", None, "Fictional Society"),
    # A lone year removed mid-line leaves no double space behind.
    ("Fictional 2001 Society Treasurer", "Treasurer", "2001", None, "Fictional Society"),
    # Punctuation the removal leaves at the edges of a segment is trimmed.
    ("Fictional Society, Treasurer.", "Treasurer", None, None, "Fictional Society"),
    # The role is removed whatever its case in the entry text.
    ("Fictional Society PRESIDENT", "President", None, None, "Fictional Society"),
    # No role, no dates, no separators: the text is returned as is.
    ("Fictional Society", "", None, None, "Fictional Society"),
    (None, "Treasurer", None, None, ""),
    # A structured role is no text to subtract: the entry text stands as it is.
    ("2001- Fictional Society\tSecretary", [{"role": "Secretary"}], "2001", None,
     "2001- Fictional Society\tSecretary"),
])
def test_organization_left_in_text(text, role, start, end, expected):
    assert _organization_left_in_text(text, role, start, end) == expected
