"""Classifying an extracted record by kind (#398).

The sibling of `text.py`: same read-only contract, but the input is a stage 4
record rather than a raw string. Each answers "what kind of thing is this?" --
a stray sub-header, a named mentee, a mentoring-outcome narrative -- so a section
writer can route it without re-deriving the taxonomy rules inline.

All three were `@staticmethod` on the generator already, which is the clearest
possible statement that they were never methods.
"""
from typing import Dict

def _is_orphan_fragment(fields: Dict, formatted_text: str, original_text: str) -> bool:
    """True if a teaching entry is a stray sub-header rather than real content.

    Such fragments carry no date, audience, location, formatted_text, or title.
    Length alone is NOT sufficient: a short entry with an extracted title is a
    real record (#262). "Biotia-HSS Next Generation Sequencing Orthopedic Assay"
    (54 chars, titled) was being discarded, while its sibling table rows
    Bactisure (180 chars) and Lamprene (120) rendered only by being longer.
    """
    has_date = bool(fields.get('date') or fields.get('start_date') or fields.get('end_date'))
    has_audience = bool(fields.get('audience') or fields.get('level'))
    has_location = bool(fields.get('location') or fields.get('institution'))
    has_formatted = bool(formatted_text)
    has_title = bool((fields.get('title') or '').strip())
    return (not has_date and not has_audience and not has_location
            and not has_formatted and not has_title and len(original_text) < 80)


def _is_mentee_record(entry: Dict) -> bool:
    """True if the entry names a person, i.e. a per-mentee table can be built.

    N3A/N3B also carry aggregate summaries ("Ph.D. Graduated: 38") that name no
    one. Those are real content but cannot fill a per-mentee table (#261).
    """
    fields = entry.get('extracted_fields', {}) or {}
    return bool((fields.get('name') or fields.get('mentee_name') or '').strip())


def _is_mentoring_outcome(entry: Dict) -> bool:
    """True if the entry is N4 mentoring-outcome narrative.

    _correct_mismatch_if_needed rewrites an unmapped N4 to N3A, stashing the
    original under 'taxonomy_code_original' — so check both (#261).
    """
    return 'N4' in (entry.get('taxonomy_code'), entry.get('taxonomy_code_original'))
