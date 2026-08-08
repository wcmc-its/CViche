"""Ordering records the way the source CV had them (#398).

One function, because the whole concern is one problem: `element_idx_start` is
not uniformly typed. Stage 2 writes an int for a paragraph, a `"table_N"` string
for a table block and a `"row.col"` string for a table row, so sorting a section
that mixes them raises `TypeError` and loses the document. Normalizing every
form to a `(major, minor)` float tuple makes the comparison total.

Kept apart from `chronological.py` deliberately: that one is wrong when a date
is missing, this one is wrong when a type is unexpected, and the fixes never
coincide.
"""

def element_idx_sort_key(value) -> tuple:
    """Document-order sort key tolerant of stage-2's mixed index types.

    ``element_idx_start`` is not uniformly typed: stage 2 writes a plain int for
    paragraph entries, a ``"table_N"`` string for table blocks, and a
    ``"row.col"`` string such as ``"22.2"`` for table rows. Sorting these raw
    raises ``TypeError: '<' not supported between instances of 'str' and 'int'``
    whenever a section mixes them. Normalize every form to a ``(major, minor)``
    float tuple so the comparison is total and preserves document order. Mirrors
    ``normalize_idx`` in stage_2_entry_extraction.py.
    """
    if value is None:
        return (float('inf'), 0.0)
    if isinstance(value, str):
        if '.' in value:
            parts = value.split('.', 1)
            try:
                return (float(parts[0]), float(parts[1]))
            except ValueError:
                return (float('inf'), 0.0)
        if value.startswith('table_'):
            try:
                return (1_000_000.0 + float(value.split('_')[1]), 0.0)
            except (ValueError, IndexError):
                return (float('inf'), 0.0)
    try:
        return (float(value), 0.0)
    except (ValueError, TypeError):
        return (float('inf'), 0.0)
