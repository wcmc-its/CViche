"""Parsing: reading structure out of raw text and extracted records.

One responsibility: input in, an answer about its shape out. Nothing here builds
output -- no python-docx object is touched, no WCM section is chosen, no value is
reformatted for display. That read-only contract is what makes these safe to call
from any section writer and testable with a string literal.

    text.py     raw text in -- which year, is this a header, is this a label
    records.py  a stage 4 record in -- what kind of thing is this
    dates.py    both, for the one value they have to agree about

The split between the first two is by input, not by topic: `text.py` parses
strings, `records.py` classifies dicts. `dates.py` is the exception and is a
file precisely because it is one -- the string parser and the record reader had
already drifted apart once when they lived on opposite sides of that line (#266).
"""

from .dates import (  # noqa: F401
    _MONTH_NAME_TO_NUM,
    _dates_overlap_or_match,
    _get_entry_date_range,
    _parse_date_components,
)
from .records import (  # noqa: F401
    _is_mentee_record,
    _is_mentoring_outcome,
    _is_orphan_fragment,
)
from .text import (  # noqa: F401
    ParsedActivityLine,
    _extract_last_name_from_uid,
    _extract_name_from_uid,
    _extract_year_from_text,
    _is_structural_label,
    _is_table_header_entry,
    _parse_flattened_committee_lines,
    _parse_multi_membership_entry,
)
