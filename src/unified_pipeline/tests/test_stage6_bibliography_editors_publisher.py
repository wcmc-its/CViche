"""#659: `_format_citation` (`stage6/formatting/values.py:24`) crashes on an
explicit-None `extracted_fields`.

This is one of ten `entry.get('extracted_fields', {})` call sites named by
#659 across `stage6/`; the other nine (`mentoring.py`, `other_education.py`,
`patents.py`, `research_support.py`) are sibling PRs, not this one.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_bibliography_editors_publisher.py -p no:cacheprovider
"""

from unified_pipeline.stage6.formatting.values import _format_citation


def test_format_citation_none_extracted_fields_does_not_raise():
    """Positive control (#659): raises AttributeError on dev (values.py:24
    was `entry.get('extracted_fields', {})`, which returns None -- not the
    default -- when the key is present and explicitly None)."""
    entry = {'extracted_fields': None, 'enrichment_data': {}, 'enriched_fields': []}

    citation, target_name, enriched_fields = _format_citation(entry, 1)

    assert citation.startswith('1. ')
    assert target_name is None
    assert enriched_fields == []


def test_format_citation_missing_extracted_fields_key_still_works():
    """The pre-existing default-args case (no key at all) keeps working
    after the guard changes from a dict default to `or {}`."""
    entry = {'enrichment_data': {}, 'enriched_fields': []}

    citation, target_name, enriched_fields = _format_citation(entry, 3)

    assert citation == '3. '
