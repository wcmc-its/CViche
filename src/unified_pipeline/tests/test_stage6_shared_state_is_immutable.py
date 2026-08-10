"""Stage 6's module-level tables must not be mutable shared state.

The web path runs stage 6 concurrently: `run_service.py` starts each pipeline
run in a `threading.Thread`, and the orchestrator hands each stage to
`asyncio.to_thread`. Several renders therefore share one interpreter and one
copy of every module-level object in stage 6.

Per-render state is safe by construction -- `run_stage6` builds a fresh
`WCMTemplateGenerator` per call, so everything on `self` is private to one
render. These module-level lookup tables are the only objects a render could
reach that another render also holds, so they are the ones that must not be
writable. Today nothing mutates them; this pins that so it stays true rather
than merely happening to be true.

ponytail: this makes the CONTENTS unwritable, which is the part a stray
`TABLE[k] = v` could corrupt mid-render. It cannot stop a caller rebinding the
module attribute itself (`s6.TAXONOMY_TO_SECTION = {}`) -- Python has no
read-only module global. If that ever needs enforcing, the upgrade is a module
`__getattr__` returning copies, which costs a dict build per lookup.
"""
import pytest

from unified_pipeline import stage_6_word_template as s6
from unified_pipeline.stage6.formatting import dates as formatting_dates
from unified_pipeline.stage6.parsing import dates as parsing_dates

MAPPINGS = [
    (s6, "RETIRED_TAXONOMY_CODES"),
    (s6, "TAXONOMY_TO_SECTION"),
    (formatting_dates, "DATE_FORMATS"),
    (formatting_dates, "_MONTH_NAMES"),
    (parsing_dates, "_MONTH_NAME_TO_NUM"),
]

SEQUENCES = [
    (s6, "FALLBACK_TEMPLATES"),
]


@pytest.mark.parametrize("module,name", MAPPINGS)
def test_shared_mapping_rejects_item_assignment(module, name):
    table = getattr(module, name)
    key = next(iter(table))
    with pytest.raises(TypeError):
        table[key] = "mutated by another render"


@pytest.mark.parametrize("module,name", MAPPINGS)
def test_shared_mapping_rejects_update_and_clear(module, name):
    table = getattr(module, name)
    assert not hasattr(table, "update"), f"{name} still exposes update()"
    assert not hasattr(table, "clear"), f"{name} still exposes clear()"


@pytest.mark.parametrize("module,name", SEQUENCES)
def test_shared_sequence_rejects_mutation(module, name):
    seq = getattr(module, name)
    with pytest.raises(TypeError):
        seq[0] = "mutated by another render"
    assert not hasattr(seq, "append"), f"{name} still exposes append()"


def test_the_pinned_set_is_not_silently_empty():
    """Guard the guard: emptying these lists would make every case vacuous."""
    assert len(MAPPINGS) == 5
    assert len(SEQUENCES) == 1


def test_each_table_still_has_its_contents():
    """Immutability must not have been bought by dropping entries."""
    assert len(s6.TAXONOMY_TO_SECTION) == 41
    assert len(formatting_dates.DATE_FORMATS) == 43  # +1: M2D added (#564)
    assert len(s6.FALLBACK_TEMPLATES) == 2
    assert s6.TAXONOMY_TO_SECTION["A"] == "personal_data"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
