"""A grant identified only by its number is admitted; a bare fragment is not (#486).

`_create_grant_table`'s sparsity guard (`research_support.py:294-311`) used to
require a title, or an agency plus a role/date -- an entry identified only by
its grant number had neither, so it was rejected and rendered nowhere at all,
even though grant_number itself has rendered into Award Source since #478/#479.

Corpus measurement, re-run from this worktree against the 66-CV farm
(`python3 probe_486_sparsity.py`, farm stage-5d/5b/4 inputs via the reference
arm's `_render_index.json`):

    Total M2A/M2B/M2C entries in farm: 336
    Rejected by the sparsity guard (no usable title, no agency+role/dates): 3
    Of those, carrying a grant_number (the #486 actionable subset): 1

      ODAWYA_2002_Holtz [M2C] grant_number='R21'  text='4.  Pending-NINR R21'

The other two guard rejections (`2100_Mocco` M2A, `GLC5JG_Mocco_CV 022525` M2B)
carry no grant_number and stay rejected under this fix -- confirmed below by
construction (test_bare_grant_number_alone_still_rejected mirrors their shape:
an identifier or narrative with nothing to corroborate it).

The positive-control fixture below is ODAWYA_2002_Holtz's real M2C entry, read
from `stage_5d_citation_formatted/ODAWYA_2002_Holtz_citation_formatted.json`
(identical to `stage_5_enrichment/ODAWYA_2002_Holtz_enriched.json` for this
entry -- the citation-formatting stage passed it through unchanged):
`extracted_fields` = {"grant_number": "R21", "title": None, "pi_role": None,
"co_investigators": None, "agency": "NINR", "total_funding_requested": None,
"submission_date": None}, `text` = "4.  Pending-NINR R21". Its one
corroborating field is `agency` ("NINR") -- the fix's gate admits agency
alongside dates, role and effort as the "second field," which is what this
real entry actually carries and is required for the render-gate expectation
that ODAWYA gains a table.

This file stays about the sparsity guard alone. The rest of section M2 -- the
bucket rules, the field fallbacks, the effort rules and the eight-row rendering
contract -- is pinned in `test_stage6_research_support_contract.py`, split out
because the two together run past a thousand lines.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_grant_number_only_sparsity.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import docx

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    """A generator with just enough state to build one table or fill one section."""
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    gen.stats = {"tables_populated": 0, "entries_inserted": 0}
    return gen


def _cells(table):
    return {row.cells[0].text: row.cells[1].text for row in table.rows}


# --- #486: grant_number admitted, corroborated ----------------------------------

def test_grant_number_only_entry_is_admitted_with_a_real_farm_fixture():
    # ODAWYA_2002_Holtz's real M2C entry -- see module docstring for provenance.
    fields = {
        "grant_number": "R21",
        "title": None,
        "pi_role": None,
        "co_investigators": None,
        "agency": "NINR",
        "total_funding_requested": None,
        "submission_date": None,
    }
    entry = {"text": "4.  Pending-NINR R21", "taxonomy_code": "M2C",
              "extracted_fields": fields}
    table = _generator()._create_grant_table(fields, "M2C", entry)
    assert table is not None
    assert "R21" in _cells(table)["Award Source:"]


def test_grant_number_corroborated_by_dates_is_admitted():
    # Synthetic (constructed): dates as the corroborating field, per the
    # issue's own suggested fix, distinct from the real fixture's agency case.
    fields = {"grant_number": "K23 CA123456", "start_date": "07/2019"}
    table = _generator()._create_grant_table(fields, "M2A")
    assert table is not None
    assert "K23 CA123456" in _cells(table)["Award Source:"]


def test_grant_number_corroborated_by_role_is_admitted():
    # Synthetic: role/PI as the corroborating field.
    fields = {"grant_number": "R01 GM134307", "pi_role": "Co-Investigator"}
    table = _generator()._create_grant_table(fields, "M2B")
    assert table is not None
    assert "R01 GM134307" in _cells(table)["Award Source:"]


# --- #486: bare fragments stay rejected ------------------------------------------

def test_role_only_fragment_still_rejected():
    # The issue's own negative control: "Role: PI" alone, no grant number.
    fields = {"pi_role": "PI"}
    entry = {"text": "Role: PI", "taxonomy_code": "M2C", "extracted_fields": fields}
    assert _generator()._create_grant_table(fields, "M2C", entry) is None


def test_bare_grant_number_alone_still_rejected():
    # A grant number with no second corroborating field at all -- proves the
    # gate genuinely requires corroboration, not just any grant_number.
    fields = {"grant_number": "R21"}
    entry = {"text": "R21", "taxonomy_code": "M2C", "extracted_fields": fields}
    assert _generator()._create_grant_table(fields, "M2C", entry) is None


def test_narrative_with_no_identifier_still_rejected():
    # Mirrors the farm's other two guard rejections (2100_Mocco, GLC5JG_Mocco_CV):
    # agency and funding present, but no title, no dates/role, no grant_number.
    fields = {"agency": "Microvention", "total_funding": "$492,560"}
    entry = {"text": "Sponsored by: Microvention.\tFunding: $492,560",
             "taxonomy_code": "M2A", "extracted_fields": fields}
    assert _generator()._create_grant_table(fields, "M2A", entry) is None


# --- #659: an explicit-None extracted_fields no longer raises -------------------

def test_none_extracted_fields_does_not_raise_through_the_section_filler():
    # Reproduces on dev today as AttributeError: 'NoneType' object has no
    # attribute 'get', via research_support.py:135/168/192/237's bare
    # entry.get('extracted_fields', {}) -- {} is the *default*, only used when
    # the key is absent; an explicit None value passes straight through.
    gen = _generator()
    for _, header in (
        ("M2A", "Current Research Funding"),
        ("M2B", "Past (Completed) Funding"),
        ("M2C", "Pending Funding"),
    ):
        gen.doc.add_paragraph(header)

    entries_by_code = {
        "M2A": [{"text": "Grant with no extracted fields at all",
                  "taxonomy_code": "M2A", "extracted_fields": None}],
        "M2B": [],
        "M2C": [],
    }

    # Must not raise. The entry has no grant_number/title/agency once its
    # extracted_fields resolves to {}, so it is correctly rejected by
    # _create_grant_table and no table is added -- the point of this test is
    # the absence of an exception, not the table count.
    gen._fill_research_support(entries_by_code)


def test_verbose_reclassification_message_exercises_the_192_read(capsys):
    # research_support.py:192 -- (entry.get('extracted_fields') or {}).get('title') --
    # sits inside `if self.verbose:`, so the test above (gen.verbose = False) never
    # executes it at all. It is also structurally unreachable with extracted_fields =
    # None: an M2A entry only reaches this reclassification loop once it has a real
    # end_date, and :168's own `entry.get('extracted_fields') or {}` means end_date can
    # only be non-empty when extracted_fields was already a real dict, not None -- so no
    # input can make :192 see a None value in practice. This test instead closes the
    # "never executed at all" gap: verbose=True plus a real M2A entry with a past
    # end_date drives the line's ordinary, non-None path.
    gen = _generator()
    gen.verbose = True
    # The reclassification also attaches a comment noting the move (see
    # _add_entry_comments); the minimal _generator() fixture has no
    # `emit_comments` attribute, which _add_word_comment reads unconditionally.
    gen.emit_comments = False
    for header in ("Current Research Funding", "Past (Completed) Funding", "Pending Funding"):
        gen.doc.add_paragraph(header)

    entries_by_code = {
        "M2A": [{"text": "Old grant, already ended", "taxonomy_code": "M2A",
                  "extracted_fields": {"title": "Old Project", "end_date": "06/2020"}}],
        "M2B": [],
        "M2C": [],
    }

    gen._fill_research_support(entries_by_code)  # must not raise
    out = capsys.readouterr().out
    assert "Reclassified to M2B: 'Old Project" in out
