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

    text.py     a value in, a cleaner value out
    fields.py   a stage-4 field of unpredictable *shape* in, plain text out
    records.py  a record filed wrongly in, the correction out

`fields.py` is separate from `text.py` because its input is not text yet. Stage 4
stores raw LLM JSON against no schema, so a field the renderer expects to be a
string can arrive as a dict or a list -- and `cell.text = <dict>` aborts the whole
document (#442, #450). Until a schema layer exists between stage 4 and stage 6,
that absence is absorbed there and nowhere else.
"""

from .fields import (  # noqa: F401
    _HOME_ADDRESS_KEYS,
    _OFFICE_ADDRESS_KEYS,
    _address_cell_text,
    _committee_cell_text,
    _labels_its_own_address_slots,
)
from .records import (  # noqa: F401
    grant_status_rebucket_target,
    split_fused_citation_entries,
)
from .text import (  # noqa: F401
    _TAXONOMY_CODE_PREFIX,
    _clean_inline_tabs,
    _deduplicate_repeated_content,
    _get_cleaned_institution_name,
    _normalize_author_names,
    _strip_markdown_for_word,
    _strip_org_tail,
    _strip_taxonomy_code,
)
