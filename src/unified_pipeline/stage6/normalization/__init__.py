"""Normalization: canonicalising an extracted value before it is rendered.

One responsibility: value in, cleaner value out. Nothing here touches a
python-docx object, decides which WCM section a record belongs to, or reads
pipeline state -- that separation is what makes these safe to reuse and trivial
to test.

The distinction from `formatting` is deliberate and worth keeping. Formatting
decides how a value *looks* in Word (font, borders, spacing); normalization
decides what the value *is* -- which author-name spelling, which institution
string, whether a repeated phrase is dropped. A change to one should not require
a change to the other.
"""

from .text import (  # noqa: F401
    _deduplicate_repeated_content,
    _get_cleaned_institution_name,
    _normalize_author_names,
    _strip_markdown_for_word,
    _strip_org_tail,
)
