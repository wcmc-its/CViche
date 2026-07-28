"""Formatting: how content is made to *look* right in the Word document.

One responsibility: manipulating python-docx objects (runs, paragraphs, cells,
tables). Nothing here knows what a CV is, which WCM section it is writing, or
what any value means -- pass it a run and a point size and it sets a font. That
is what makes it safely shared and separately testable.

Anything that decides *what* to write belongs in the section writers; anything
that decides *how a value should read* belongs in normalization.

    docx.py    a python-docx object in, its appearance mutated
    values.py  a value in, the string that appears on the page out
"""

from .docx import (  # noqa: F401
    _clear_table_data,
    _set_cell_background,
    _set_cell_borders,
    _set_cell_text,
    _set_cell_vertical_alignment,
    _set_font,
    _set_paragraph_spacing,
    _set_table_border,
)
from .values import (  # noqa: F401
    _format_citation,
    _format_currency,
    _format_mentee_duration,
)
