"""Sorting: the order records appear in within a section (#398).

The fifth layer named in the split plan, and the last of the original five to be
built -- deliberately, because for a long time there was nothing in it that was
not also doing something else. What belongs here is only the ordering decision:
a key function, or a sort that applies one. Choosing which records to render is
a section writer's job, and reading the date a key is built from is
`parsing/dates.py`.

    chronological.py   most recent first, the default for a dated section
    document_order.py  the order the source CV put them in

The two files are separate because they answer to different inputs and fail
differently. Chronological order reads a date off `extracted_fields` and is
wrong when the date is missing or unparseable. Document order reads
`element_idx_start`, which stage 2 writes with three different types, and is
wrong by raising `TypeError` mid-render. Neither would be clearer for being
next to the other.
"""

from .chronological import (  # noqa: F401
    extract_sort_date,
    sort_entries_reverse_chronological,
)
from .document_order import element_idx_sort_key  # noqa: F401
