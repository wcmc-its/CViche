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
    PRESENTATION_DATE_FIELDS,
    PRESENTATION_END_DATE_FIELD,
    _source_leaves_year_open,
    envelope_date_spans,
    extra_date_spans,
    format_date_for_section,
    format_date_list,
    format_date_range,
    further_date_spans,
    normalize_iso_dates_in_text,
    presentation_date_spans,
    with_extra_date_spans,
)
from .docx import (  # noqa: F401
    CARD_ORG_SEPARATOR,
    CVICHE_BOX_BORDER,
    CVICHE_BOX_BORDER_SIZE,
    CVICHE_BOX_FILL,
    CVICHE_BOX_FULL_WIDTH,
    CVICHE_BOX_OUTSIDE_PT,
    CVICHE_BOX_PADDING,
    CVICHE_BOX_PREFIX,
    CVICHE_BOX_TEXT_PT,
    CVICHE_BOX_TITLE_PT,
    ENTRY_GAP_AFTER_PT,
    PLAIN_ENTRY_LEFT_TWIPS,
    PLAIN_ENTRY_LEVEL_STEP_TWIPS,
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
    clear_entry_end,
    cviche_box_group,
    cviche_box_item,
    cviche_box_pair,
    cviche_box_text,
    is_cviche_box,
    is_entry_end,
    mark_entry_end,
    plain_entry_left_twips,
    plain_entry_level,
    space_around_cviche_box,
    text_width_twips,
    write_card_line,
)
from .values import (  # noqa: F401
    _format_citation,
    _format_currency,
    _format_mentee_duration,
    event_status_note,
    with_event_status,
)
