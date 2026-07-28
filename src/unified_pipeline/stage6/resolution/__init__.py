"""Resolution: deciding what an incomplete or ambiguous record should say.

The sixth layer, and the one none of the original five names covered. Parsing
reads what is there; normalization canonicalises a value that is already
present; formatting decides how it looks. These functions handle the case where
the answer is *not* on the record -- the source CV omitted the location, named
the employer inconsistently, or left the institution on a sibling row -- and
something must consult a table, stage 5b enrichment, or the neighbouring entries
to decide what it should be.

    institution.py  which employer, and where
    owner.py        whose CV this is

That is a real boundary and worth keeping: it is where inference lives. Every
function here returns a *decision*, and several report their confidence
alongside it, because an inferred value is rendered as a tracked change while an
extracted one is not. Code that merely reads or reformats belongs elsewhere.
"""

from .institution import (  # noqa: F401
    _get_institution_location,
    _recover_institution_from_nearby_entries,
)
from .owner import _get_cv_owner_name  # noqa: F401
