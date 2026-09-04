"""Repeated content, collapsed back to one copy.

Neither a domain concern nor a rendering one: it repairs an artifact of
table extraction, where a merged cell arrives with its own content repeated
across every segment. It changes when a new extraction pathology is
observed in a CV, whatever the value happens to mean.
"""
import logging

logger = logging.getLogger(__name__)


def _deduplicate_repeated_content(text: str, separator: str = '|') -> str:
    """Remove repeated content from pipe-separated text.

    Handles cases where table extraction causes the same content to repeat:
    "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"

    Args:
        text: Raw text that may contain repeated segments
        separator: The separator between repeated segments (default: '|')

    Returns:
        The first segment, but ONLY when every segment is an exact repeat of
        it (a merged cell repeating itself). Anything less -- including two
        segments that simply don't match -- is meaningfully different
        content and is returned unchanged, untruncated (#561).
    """
    if not text or separator not in text:
        return text

    parts = [p.strip() for p in text.split(separator) if p.strip()]
    if len(parts) <= 1:
        return text

    # Check if all parts are identical (using first part as reference)
    first_part = parts[0]

    # Normalize for comparison (lowercase, remove extra whitespace)
    def normalize(s):
        return ' '.join(s.lower().split())

    first_normalized = normalize(first_part)

    # Count how many parts match the first
    matching_count = sum(1 for p in parts if normalize(p) == first_normalized)

    # Collapse only when EVERY segment is identical -- the merged-cell
    # pathology this function exists for. `matching_count >= len(parts) *
    # 0.5` was a tautology at len(parts) == 2 (matching_count always counts
    # the reference segment against itself, so 1 >= 1.0 unconditionally),
    # truncating any two-segment title to its first half regardless of
    # whether the segments matched at all (#561).
    if matching_count == len(parts):
        # Structural metadata only: the segment itself is CV-derived text
        # (a grant or appointment title), so its length is logged and its
        # content is not.
        logger.debug(
            "_deduplicate_repeated_content: collapsed %d identical segments "
            "to one of %d characters", len(parts), len(first_part),
        )
        return first_part

    # Otherwise return original (parts are meaningfully different)
    return text
