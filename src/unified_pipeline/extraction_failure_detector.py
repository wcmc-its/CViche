"""
Extraction Failure Detection and Smart Model Routing

This module identifies when Stage 4 field extraction has failed or produced
low-quality results, and routes those entries to a more capable model (gpt-5.1)
for re-extraction.

Architecture:
1. Run Stage 4 with fast/cheap model (gpt-4o-mini)
2. Detect extraction failures using quality metrics
3. Re-extract failed entries with gpt-5.1
4. Merge results back into final output

Cost optimization: Only pay for expensive model when needed.
"""

from typing import Dict, List, Any, Optional
import re


# =============================================================================
# FAILURE DETECTION CRITERIA
# =============================================================================

def detect_extraction_failure(
    entry: Dict[str, Any],
    extracted_fields: Dict[str, Any],
    coverage_percentage: float,
    taxonomy_code: str
) -> Dict[str, Any]:
    """
    Detect if field extraction failed or produced low-quality output.

    Args:
        entry: Original CV entry with entry_text
        extracted_fields: Extracted structured fields
        coverage_percentage: LLM-reported coverage (0-100)
        taxonomy_code: Entry taxonomy classification

    Returns:
        {
            'failed': bool,
            'failure_reasons': [str],  # List of specific issues
            'severity': 'low'|'medium'|'high',
            'should_retry_with_gpt51': bool
        }
    """
    failures = []
    severity = 'low'

    entry_text = entry.get('entry_text', '')
    text_length = len(entry_text)

    # -------------------------------------------------------------------------
    # CHECK 1: Low Coverage
    # -------------------------------------------------------------------------
    if coverage_percentage < 60 and text_length > 50:
        failures.append(f"Low coverage: {coverage_percentage:.1f}% (threshold: 60%)")
        severity = max_severity(severity, 'medium')

    # -------------------------------------------------------------------------
    # CHECK 2: Zero Coverage (Empty Extraction)
    # -------------------------------------------------------------------------
    if coverage_percentage == 0 and text_length > 50:
        failures.append(f"Zero coverage despite {text_length} chars of text")
        severity = max_severity(severity, 'high')

    # -------------------------------------------------------------------------
    # CHECK 3: Missing Critical Fields (by taxonomy)
    # -------------------------------------------------------------------------
    missing_critical = check_missing_critical_fields(
        taxonomy_code, extracted_fields, entry_text
    )
    if missing_critical:
        failures.extend(missing_critical)
        severity = max_severity(severity, 'medium')

    # -------------------------------------------------------------------------
    # CHECK 4: Malformed Field Values
    # -------------------------------------------------------------------------
    malformed = check_malformed_fields(extracted_fields)
    if malformed:
        failures.extend(malformed)
        severity = max_severity(severity, 'medium')

    # -------------------------------------------------------------------------
    # CHECK 5: Table Row Merging
    # -------------------------------------------------------------------------
    if has_merged_table_rows(extracted_fields):
        failures.append("Table rows merged (tabs/pipes in field values)")
        severity = max_severity(severity, 'medium')

    # -------------------------------------------------------------------------
    # Decision: Should we retry with gpt-5.1?
    # -------------------------------------------------------------------------
    should_retry = (
        severity in ['medium', 'high']  # Only retry non-trivial issues
        and text_length > 30  # Don't waste on minimal entries
    )

    return {
        'failed': len(failures) > 0,
        'failure_reasons': failures,
        'severity': severity,
        'should_retry_with_gpt51': should_retry,
        'entry_index': entry.get('entry_index'),
        'entry_text_preview': entry_text[:100] + '...' if len(entry_text) > 100 else entry_text
    }


# =============================================================================
# SPECIFIC FAILURE CHECKS
# =============================================================================

def check_missing_critical_fields(
    taxonomy_code: str,
    extracted_fields: Dict[str, Any],
    entry_text: str
) -> List[str]:
    """
    Check if critical fields are missing based on taxonomy code.

    Returns list of failure reasons.
    """
    failures = []

    # Publication codes (S family)
    if taxonomy_code.startswith('S') and taxonomy_code not in ['S7', 'S9']:
        # Critical: title, date
        if not extracted_fields.get('title'):
            failures.append("Missing critical field: title (publication)")

        if not extracted_fields.get('publication_date'):
            # Check if date appears in text
            if has_year_in_text(entry_text):
                failures.append("Missing critical field: publication_date (year present in text)")

        # Authors expected but not required (sometimes missing in CVs)
        if not extracted_fields.get('authors') and len(entry_text) > 100:
            # Only flag if entry is substantial
            failures.append("Missing expected field: authors (long publication entry)")

    # Position codes (D family, C family)
    elif taxonomy_code.startswith('D') or taxonomy_code.startswith('C'):
        if not extracted_fields.get('institution'):
            failures.append("Missing critical field: institution (position entry)")

        if not extracted_fields.get('role_or_title'):
            failures.append("Missing critical field: role_or_title (position entry)")

        if not extracted_fields.get('start_date'):
            if has_year_in_text(entry_text):
                failures.append("Missing critical field: start_date (year present in text)")

    # Award codes (H family)
    elif taxonomy_code.startswith('H'):
        if not extracted_fields.get('honor_or_award_name'):
            failures.append("Missing critical field: honor_or_award_name")

        if not extracted_fields.get('award_date'):
            if has_year_in_text(entry_text):
                failures.append("Missing critical field: award_date (year present in text)")

    # Mentoring codes (N family)
    elif taxonomy_code.startswith('N'):
        if not extracted_fields.get('mentee_name'):
            failures.append("Missing critical field: mentee_name")

    return failures


def check_malformed_fields(extracted_fields: Dict[str, Any]) -> List[str]:
    """
    Check for malformed field values.

    Returns list of failure reasons.
    """
    failures = []

    # Check authors field
    authors = extracted_fields.get('authors', '')
    if authors:
        # Trailing comma
        if authors.strip().endswith(','):
            failures.append("Malformed authors: trailing comma")

        # Leading "and"
        if authors.strip().lower().startswith('and '):
            failures.append("Malformed authors: leading 'and'")

        # Multiple trailing commas
        if authors.count(',,') > 0:
            failures.append("Malformed authors: double commas")

    # Check date ranges not split
    start_date = extracted_fields.get('start_date', '')
    end_date = extracted_fields.get('end_date', '')

    if isinstance(start_date, str) and '-' in start_date and len(start_date) > 4:
        # Looks like a range: "2010-2015"
        if not end_date:
            failures.append("Date range not split: start_date contains range but end_date is null")

    return failures


def has_merged_table_rows(extracted_fields: Dict[str, Any]) -> bool:
    """
    Check if any field values contain tab or pipe characters.

    This indicates table rows were not properly split.
    """
    for field_name, field_value in extracted_fields.items():
        if isinstance(field_value, str):
            if '\t' in field_value or '|' in field_value:
                # Exception: URLs can have pipes
                if field_name not in ['url', 'doi', 'link']:
                    return True
    return False


def has_year_in_text(text: str) -> bool:
    """Check if text contains a 4-digit year (1900-2099)."""
    year_pattern = r'\b(19|20)\d{2}\b'
    return bool(re.search(year_pattern, text))


def max_severity(current: str, new: str) -> str:
    """Return the higher severity level."""
    severity_order = ['low', 'medium', 'high']
    current_idx = severity_order.index(current)
    new_idx = severity_order.index(new)
    return severity_order[max(current_idx, new_idx)]


# =============================================================================
# BATCH ANALYSIS
# =============================================================================

def analyze_extraction_batch(extraction_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze a batch of extraction results and identify failures.

    Args:
        extraction_results: List of extraction result dicts from Stage 4

    Returns:
        {
            'total_entries': int,
            'failed_entries': int,
            'should_retry_count': int,
            'failures_by_severity': {'low': 5, 'medium': 3, 'high': 1},
            'retry_candidates': [entry_index, ...],
            'failure_details': [failure_dict, ...]
        }
    """
    total = len(extraction_results)
    failures = []
    retry_candidates = []
    severity_counts = {'low': 0, 'medium': 0, 'high': 0}

    for result in extraction_results:
        entry = {
            'entry_text': result.get('entry_text', ''),
            'entry_index': result.get('entry_index')
        }
        extracted = result.get('extracted_fields', {})
        coverage = result.get('coverage_percentage', 0)
        taxonomy = result.get('taxonomy_code', '')

        failure = detect_extraction_failure(entry, extracted, coverage, taxonomy)

        if failure['failed']:
            failures.append(failure)
            severity_counts[failure['severity']] += 1

            if failure['should_retry_with_gpt51']:
                retry_candidates.append(result.get('entry_index'))

    return {
        'total_entries': total,
        'failed_entries': len(failures),
        'should_retry_count': len(retry_candidates),
        'failures_by_severity': severity_counts,
        'retry_candidates': retry_candidates,
        'failure_details': failures,
        'failure_rate': len(failures) / total if total > 0 else 0,
        'retry_rate': len(retry_candidates) / total if total > 0 else 0
    }


# =============================================================================
# REPORTING
# =============================================================================

def format_failure_report(analysis: Dict[str, Any]) -> str:
    """Format failure analysis as human-readable report."""
    lines = []

    lines.append("=" * 80)
    lines.append("EXTRACTION FAILURE ANALYSIS")
    lines.append("=" * 80)
    lines.append("")

    lines.append(f"Total entries: {analysis['total_entries']}")
    lines.append(f"Failed entries: {analysis['failed_entries']} ({analysis['failure_rate']:.1%})")
    lines.append(f"Should retry with gpt-5.1: {analysis['should_retry_count']} ({analysis['retry_rate']:.1%})")
    lines.append("")

    lines.append("Failures by severity:")
    for severity in ['high', 'medium', 'low']:
        count = analysis['failures_by_severity'][severity]
        if count > 0:
            lines.append(f"  {severity.upper()}: {count}")
    lines.append("")

    if analysis['should_retry_count'] > 0:
        lines.append(f"Retry candidates (entry indices): {analysis['retry_candidates'][:20]}")
        if len(analysis['retry_candidates']) > 20:
            lines.append(f"  ... and {len(analysis['retry_candidates']) - 20} more")
        lines.append("")

    # Sample failures
    if analysis['failure_details']:
        lines.append("Sample failures:")
        for i, failure in enumerate(analysis['failure_details'][:5], 1):
            lines.append(f"\n{i}. Entry {failure['entry_index']} ({failure['severity']} severity)")
            lines.append(f"   Text: {failure['entry_text_preview']}")
            lines.append(f"   Issues:")
            for reason in failure['failure_reasons']:
                lines.append(f"     • {reason}")

        if len(analysis['failure_details']) > 5:
            lines.append(f"\n... and {len(analysis['failure_details']) - 5} more failures")

    lines.append("")
    lines.append("=" * 80)

    return "\n".join(lines)


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    # Example: Analyze extraction results
    sample_results = [
        {
            'entry_index': 42,
            'entry_text': 'Smith, J. (2020). "Important Research". Journal of Science, 45(2), 123-145.',
            'taxonomy_code': 'S1',
            'extracted_fields': {
                'title': 'Important Research',
                'publication_date': None,  # MISSING!
                'authors': 'Smith, J.,'  # MALFORMED!
            },
            'coverage_percentage': 45  # LOW!
        }
    ]

    analysis = analyze_extraction_batch(sample_results)
    print(format_failure_report(analysis))
