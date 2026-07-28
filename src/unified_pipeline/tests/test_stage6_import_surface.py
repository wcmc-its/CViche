"""Import-surface contract for the stage 6 module split (#398).

`stage_6_word_template.py` is 9,530 lines and is being decomposed into
submodules. The decomposition must not change what the rest of the repository
can import from it, because the callers are spread across code that does not
get touched by the split:

  - `run_full_pipeline.py` and the backend `orchestrator.py` are PARALLEL
    implementations sharing no code, so a name that moves has to be fixed in
    both, and a miss lands silently in the one nobody ran.
  - `run_doctor.py` and the corpus tooling import render helpers directly.
  - Eight open PRs carry regression tests that import these names. If a symbol
    moves without a re-export, those tests fail at IMPORT time during rework and
    look like the fix was lost, when the fix is fine and only the address
    changed.

So the rule for every split PR is: move the implementation wherever separation
of concerns says it belongs, then re-export the name from this module. A
one-line `from .stage6.text import _phone_cell_text` at the bottom of
`stage_6_word_template.py` keeps every existing caller working.

**Nine of these names are private** (leading underscore). That is deliberate,
not an oversight in this test: privacy here describes intent for new callers,
but the existing imports are real and load-bearing, so they are part of the
contract whether or not they should have been. Narrowing the surface is
worthwhile, but it is a SEPARATE change from relocating code -- doing both at
once means a failure cannot be attributed to either.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_import_surface.py -p no:cacheprovider

Self-contained: imports only, no DB, no network, no LLM, no PII.
"""

import importlib
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


#: Every name imported from `stage_6_word_template` anywhere in the repository,
#: measured against origin/dev @ 5e3d8fd on 2026-07-28 by walking the AST of all
#: 350 .py files for `ImportFrom` nodes targeting this module.
#:
#: Adding to this list is fine. REMOVING from it is a breaking change, and it is
#: the one the split is most likely to make by accident.
STAGE6_IMPORT_SURFACE = (
    "DEDUP_FUSED_BLOB_RECORD_LINES",
    "RETIRED_TAXONOMY_CODES",
    "TAXONOMY_TO_SECTION",
    "WCMTemplateGenerator",
    "_address_cell_text",
    "_clean_inline_tabs",
    "_committee_cell_text",
    "_drop_is_safe",
    "_labels_its_own_address_slots",
    "_parse_date_components",
    "_record_lines",
    "_squash",
    "_strip_taxonomy_code",
    "deduplicate_entries",
    "extract_sort_date",
    "format_date_for_section",
    "grant_status_rebucket_target",
    "normalize_retired_code",
    "run_stage6",
    "segment_already_rendered",
    "split_fused_citation_entries",
)

#: Same, for the shared render helpers. `entry_lines` is added by #477 and is
#: deliberately absent here -- this file pins what exists on dev, so it can be
#: merged before the split without waiting on any open PR.
RENDER_CHECK_IMPORT_SURFACE = (
    "entry_fragments",
)


@pytest.mark.parametrize("name", STAGE6_IMPORT_SURFACE)
def test_stage6_symbol_is_still_importable(name):
    """Each name the repo imports from stage_6_word_template must resolve.

    Fails at the point a split moves a symbol out without re-exporting it.
    """
    mod = importlib.import_module("unified_pipeline.stage_6_word_template")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from stage_6_word_template. "
        f"If the split moved it, re-export it from this module -- callers in "
        f"run_full_pipeline.py, the backend orchestrator, run_doctor.py and "
        f"eight open PRs import it by this name."
    )


@pytest.mark.parametrize("name", RENDER_CHECK_IMPORT_SURFACE)
def test_render_check_symbol_is_still_importable(name):
    mod = importlib.import_module("unified_pipeline.core.render_check")
    assert hasattr(mod, name), (
        f"'{name}' is no longer importable from core.render_check."
    )


#: Methods the test suite calls on a WCMTemplateGenerator INSTANCE, measured the
#: same way. This is a second contract, distinct from the module surface above:
#: those names are imported, these are reached for as class attributes, and the
#: module-level pin does not cover them.
#:
#: Learned the hard way. The first split moved `_set_table_border` out of the
#: class and `test_stage6_table_border.py` broke -- it was calling
#: `WCMTemplateGenerator._set_table_border(None, table)`, passing None for self.
#: Every module-surface test above still passed, because the symbol remained
#: importable; only the class attribute had gone.
#:
#: A split that moves one of these SHOULD fail here. That is the point: the
#: failure forces a deliberate choice -- update the callers, or leave a thin
#: delegating method behind -- instead of surfacing during PR rework, where it
#: looks like a lost fix rather than a moved address.
#:
#: `_extract_year_from_text` was REMOVED from this tuple by the parsing split.
#: The measurement that built the list counted `self._extract_year_from_text(...)`
#: inside the generator itself, which is an internal call, not a test reaching
#: through the class. Re-checked by name across dev and all nine open PR branches:
#: the only reference outside stage_6_word_template.py was this list. Removing a
#: name here is otherwise a breaking change -- the bar is proving no caller exists,
#: not finding it inconvenient.
STAGE6_CLASS_SURFACE = (
    "_add_remaining_to_appendix",
    "_add_track_change_insertion",
    "_add_track_change_pair",
    "_add_word_comment",
    "_degree_is_in_progress",
    "_extract_organization_from_award",
    "_fill_honors",
    "_fill_licensure",
    "_fill_positions",
    "_fill_research_summary",
    "_finalize_comments",
    "_merge_grouped_appointments",
    "_reconsider_appendix_entries",
    "_recover_unrendered_records",
    "_remove_instruction_box",
    "_split_award_year",
    "generate",
)


@pytest.mark.parametrize("name", STAGE6_CLASS_SURFACE)
def test_generator_method_is_still_on_the_class(name):
    """Each method the tests call on an instance must remain a class attribute."""
    mod = importlib.import_module("unified_pipeline.stage_6_word_template")
    assert hasattr(mod.WCMTemplateGenerator, name), (
        f"WCMTemplateGenerator.{name} is gone. If the split moved it out, either "
        f"update the callers under src/unified_pipeline/tests/ or leave a thin "
        f"delegating method -- {name} is called on an instance by the suite."
    )


def test_the_surface_list_is_not_silently_empty():
    """Guard the guard.

    A refactor that reduced these tuples to () would make every test above
    vacuously pass by generating zero cases. Pin the counts measured on dev.
    """
    assert len(STAGE6_IMPORT_SURFACE) == 21, (
        "the pinned stage 6 import surface changed size -- if that is "
        "intentional, update the count and say why in the commit message"
    )
    assert len(RENDER_CHECK_IMPORT_SURFACE) == 1
    assert len(STAGE6_CLASS_SURFACE) == 17


def test_private_names_are_a_deliberate_part_of_the_contract():
    """Document, in executable form, that most of this surface is private.

    If this ratio shifts a lot, someone is either narrowing the surface (good,
    but should be its own PR) or widening it (worth noticing).
    """
    private = [n for n in STAGE6_IMPORT_SURFACE if n.startswith("_")]
    assert len(private) == 9, (
        f"expected 9 private names in the stage 6 import surface, found "
        f"{len(private)}: {sorted(private)}"
    )


if __name__ == "__main__":
    for _n in STAGE6_IMPORT_SURFACE:
        test_stage6_symbol_is_still_importable(_n)
    for _n in RENDER_CHECK_IMPORT_SURFACE:
        test_render_check_symbol_is_still_importable(_n)
    for _n in STAGE6_CLASS_SURFACE:
        test_generator_method_is_still_on_the_class(_n)
    test_the_surface_list_is_not_silently_empty()
    test_private_names_are_a_deliberate_part_of_the_contract()
    print("OK")
