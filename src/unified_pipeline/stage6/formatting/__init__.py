"""Formatting: how content is made to *look* right in the Word document.

One responsibility: manipulating python-docx objects (runs, paragraphs, cells,
tables). Nothing here knows what a CV is, which WCM section it is writing, or
what any value means -- pass it a run and a point size and it sets a font. That
is what makes it safely shared and separately testable.

Anything that decides *what* to write belongs in the section writers; anything
that decides *how a value should read* belongs in normalization.

    docx.py    a python-docx object in, its appearance mutated
    values.py  a value in, the string that appears on the page out
    dates.py   a date in, the string the section's format calls for out
"""

from .dates import (  # noqa: F401
    _MONTH_NAMES,
    DATE_FORMATS,
    DATE_SPAN_SEPARATOR,
    EXTRA_SPAN_CODES,
    EXTRA_SPAN_KEYS,
    _source_leaves_year_open,
    envelope_date_spans,
    extra_date_spans,
    format_date_for_section,
    format_date_range,
    further_date_spans,
    normalize_iso_dates_in_text,
    with_extra_date_spans,
)
from .docx import (  # noqa: F401
    CVICHE_BOX_BORDER,
    CVICHE_BOX_BORDER_SIZE,
    CVICHE_BOX_FILL,
    CVICHE_BOX_PREFIX,
    DetachedAnchorError,
    _clear_table_data,
    _insert_after,
    _set_cell_background,
    _set_cell_borders,
    _set_cell_text,
    _set_cell_vertical_alignment,
    _set_font,
    _set_paragraph_spacing,
    _set_table_border,
    add_cviche_box,
    cviche_box_line,
    is_cviche_box,
)
from .values import (  # noqa: F401
    _format_citation,
    _format_currency,
    _format_mentee_duration,
)
