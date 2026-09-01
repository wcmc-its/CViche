"""Guard against a repeat of the `_add_table_row` mixin collision.

`WCMTemplateGenerator` (`stage_6_word_template.py`) is composed from 23
section mixins, one per file under `stage6/sections/`. Python resolves an
attribute reached through `self.<name>` by walking the MRO left to right and
returning the FIRST match -- it does not care whether two mixins independently
defined the same name for unrelated reasons. If two of them do, the later
mixin's own calls to `self.<name>` silently run the earlier mixin's version
instead of their own.

That is exactly what broke: `ClinicalPracticeSection` (added by #625, commit
aeea7a7) and `MembershipsSection` both defined `_add_table_row`, with
different signatures. `ClinicalPracticeSection` precedes `MembershipsSection`
in the `WCMTemplateGenerator` base list, so every `self._add_table_row(...)`
call inside `memberships.py` resolved to the clinical version and raised
`TypeError: ... got an unexpected keyword argument 'entry'` -- on 52 of 66
corpus CVs, with the full 1287-test pipeline suite green throughout, because
no test in that suite ever calls a memberships-rendering method through the
COMPOSED class; the round-2 fixture tests build a generator but exercise
`ClinicalPracticeSection` methods only.

Each mixin owns its methods; nothing here is a documented shared hook (see
`memberships.py`'s own module docstring: "`_add_table_row` ... lives here
because this is the only section that calls it. Everything else either
writes cells directly or uses one of the shared `_add_table_row_with_*`
variants, which stay on `WCMTemplateGenerator`" -- i.e. sharing goes through
`WCMTemplateGenerator` itself or an explicit `_with_*` suffix, never through
two mixins defining the same bare name). So the rule admits no allowlist: any
name owned by more than one mixin is a bug, full stop, until a real shared
convention exists and is documented as one.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_mixin_name_collisions.py -p no:cacheprovider
"""

import sys
from collections import defaultdict
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _mixin_callable_owners():
    """Map each callable name defined directly on a mixin to every mixin
    (by class name) that defines it.

    Walks `WCMTemplateGenerator.__bases__` rather than the full MRO -- the
    bases ARE the 23 section mixins, each inheriting only from `object`, so
    walking `vars()` per base (not per MRO entry) looks only at names each
    mixin defines itself, not names it merely inherits. Dunder names
    (`__init__`, `__repr__`, ...) are excluded: Python's own machinery, not a
    section author's naming choice.
    """
    owners = defaultdict(list)
    for base in WCMTemplateGenerator.__bases__:
        # The docstring's premise, pinned: a mixin that grows a parent would
        # carry inherited names this per-base walk cannot see.
        assert base.__bases__ == (object,), f"{base.__name__} inherits from {base.__bases__}; extend the walk to its MRO"
        for name, value in vars(base).items():
            if name.startswith("__"):
                continue
            if callable(value):
                owners[name].append(base.__name__)
    return owners


def test_no_two_section_mixins_define_the_same_callable_name():
    """The regression test for the `_add_table_row` collision itself.

    Fails the moment a second mixin defines a name another mixin already
    owns -- exactly the shape of the #625 regression, generalized to every
    name instead of pinning `_add_table_row` alone, so the next collision
    (any name, any pair of mixins) is caught here instead of by a 52/66
    corpus render failure.
    """
    owners = _mixin_callable_owners()
    collisions = {name: sorted(defs) for name, defs in owners.items() if len(defs) > 1}
    assert collisions == {}, (
        "two or more section mixins composed into WCMTemplateGenerator define "
        f"the same callable name -- the later mixin in the base list silently "
        f"loses its own method to the earlier one via MRO resolution: "
        f"{collisions}"
    )


def test_the_mixin_surface_is_not_silently_empty():
    """Guard the guard: an import failure or an empty base list would make
    the collision check above vacuously pass.
    """
    owners = _mixin_callable_owners()
    assert len(WCMTemplateGenerator.__bases__) == 23, (
        "WCMTemplateGenerator's mixin count changed -- if intentional, "
        "update this pin"
    )
    assert len(owners) > 0, "no callables found on any section mixin"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
