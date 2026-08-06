"""Value normalization lifted out of stage_6_word_template (#398).

Text in, cleaner text out. Author names in a single citation spelling, an
institution string with its trailing org suffix removed, markdown stripped for a
Word run, a repeated phrase collapsed, the reader's internal cell separators
made readable, a leaked 3b taxonomy code stripped off the front of a bullet.

They are kept together because they change for the same reason -- a new spelling
variant, separator or leaked token observed in a CV -- and apart from
`formatting`, which changes when the WCM template's appearance changes. The
sibling `fields.py` handles the case where the input is not text yet.

Names keep their leading underscore for now. Renaming and relocating in one
change would make a failure impossible to attribute to either.
"""
import re
from typing import Dict, Optional


def _normalize_author_names(authors: str) -> str:
    """
    Normalize author names to proper Vancouver format.

    Handles formats like:
    - "Kelly, R, Pirog, R" -> "Kelly R, Pirog R" (LastName, Initial pairs)
    - "Smith JA, Jones MB" -> "Smith JA, Jones MB" (already Vancouver)
    - "Smith, John A., Jones, Mary B." -> "Smith JA, Jones MB"

    Fixes common issues:
    - Double commas: "Watson, K.,," -> "Watson K"
    - Trailing punctuation
    """
    if not authors:
        return ''

    # Clean up double/triple commas
    authors = re.sub(r',{2,}', ',', authors)

    # Remove trailing punctuation
    authors = authors.rstrip('.,;')

    # Replace " & " with ", "
    authors = re.sub(r'\s*&\s*', ', ', authors)

    # Handle the "LastName, Initial, LastName, Initial" format
    # Pattern: word followed by comma and single letter(s)
    # e.g., "Kelly, R, Pirog, R" -> list of ("Kelly", "R"), ("Pirog", "R")

    # First, check if this looks like alternating "Name, Initial" pairs
    parts = [p.strip() for p in authors.split(',') if p.strip()]

    # Try to detect the pattern: alternating surnames and initials
    # Initials are 1-4 uppercase letters (possibly space-separated like "P L" or hyphenated like "R-Y")
    looks_like_pairs = True
    if len(parts) >= 2:
        for i in range(1, len(parts), 2):
            # Every odd index should be initials (1-4 uppercase letters, possibly with spaces/hyphens)
            part = parts[i].rstrip('.').replace(' ', '')
            # Match: "AB", "ABC", "A-B", "R-Y", etc.
            if not re.match(r'^[A-Z]{1,4}$', part) and not re.match(r'^[A-Z](-[A-Z])+$', part):
                looks_like_pairs = False
                break

    if looks_like_pairs and len(parts) >= 2:
        # Combine pairs: ["Kelly", "R", "Pirog", "R"] -> ["Kelly R", "Pirog R"]
        cleaned_authors = []
        i = 0
        while i < len(parts) - 1:
            surname = parts[i].strip().rstrip('.,')
            initials = parts[i + 1].strip().rstrip('.,')
            # Normalize spaced initials: "P L" -> "PL"
            initials_normalized = initials.replace(' ', '')

            # Skip if surname looks like just initials
            if len(surname) <= 2 and surname.isupper():
                i += 1
                continue

            # Handle multi-part surnames like "García Polanco"
            # Check if next "initial" is actually part of surname
            if i + 2 < len(parts):
                next_part = parts[i + 2].strip().rstrip('.,')
                if len(initials_normalized) > 4 or not initials_normalized.isupper():
                    # This might be a multi-part name
                    surname = f"{surname} {initials}"
                    initials_normalized = next_part.replace(' ', '')
                    i += 1

            cleaned_authors.append(f"{surname} {initials_normalized}")
            i += 2

        return ', '.join(cleaned_authors)

    # Fall back to simpler processing for other formats
    cleaned_authors = []
    has_et_al = False

    for author in parts:
        author_stripped = author.strip()

        # Handle "et al" specially
        if author_stripped.lower() in ('et al', 'et al.'):
            has_et_al = True
            continue

        # Skip entries that are just initials (like "MR." or "UM.")
        if re.match(r'^[A-Z]{1,3}\.?$', author_stripped):
            continue

        # Skip entries that look incomplete (just 1-2 chars)
        if len(author_stripped) <= 2:
            continue

        # Clean up individual author formatting
        author = author_stripped.rstrip('.,')

        # Remove periods from initials: "J.A." -> "JA"
        author = re.sub(r'([A-Z])\.([A-Z])', r'\1\2', author)
        author = re.sub(r'([A-Z])\.$', r'\1', author)

        if author:
            cleaned_authors.append(author)

    result = ', '.join(cleaned_authors)
    if has_et_al:
        result += ', et al.'
    return result


def _get_cleaned_institution_name(entry: Dict) -> Optional[str]:
    """Get cleaned institution name from enrichment data if available.

    When Stage 5b LLM enrichment provides a cleaned_name (institution name with
    embedded location removed), use it instead of the raw institution field.
    This prevents duplication like "Duke Medical Center, Durham, NC, Durham, NC".

    Falls back to official_name when cleaned_name is empty — the LLM sometimes
    returns empty cleaned_name even when official_name is correctly populated
    (e.g., official_name="Duke Regional Hospital" with cleaned_name="").

    Args:
        entry: Entry dict with potential institution_enrichment

    Returns:
        Cleaned institution name, or None if not available (use original)
    """
    enrichment = entry.get('institution_enrichment', {})
    cleaned = enrichment.get('cleaned_name', '')
    if cleaned:
        return cleaned
    # Fall back to official_name — always the institution without embedded location
    official = enrichment.get('official_name', '')
    return official if official else None


def _strip_org_tail(name: str, org: str) -> str:
    """Remove a trailing organization segment (plus one short comma-led
    city tail, "..., Indiana University, Bloomington") from an award name
    so the org isn't duplicated across the name and Organization cells
    (#229). Conservative: only strips at end-of-string."""
    if not org:
        return name
    stripped = re.sub(
        r'[\s,]*' + re.escape(org) +
        r'(?:,\s*[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)?[\s,.]*$',
        '', name).strip()
    return stripped or name


def _deduplicate_repeated_content(text: str, separator: str = '|') -> str:
    """Remove repeated content from pipe-separated text.

    Handles cases where table extraction causes the same content to repeat:
    "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"

    Args:
        text: Raw text that may contain repeated segments
        separator: The separator between repeated segments (default: '|')

    Returns:
        Deduplicated text with only the first unique segment
    """
    if not text or separator not in text:
        return text

    parts = [p.strip() for p in text.split(separator) if p.strip()]
    if len(parts) <= 1:
        return text

    # Check if all parts are similar (using first part as reference)
    first_part = parts[0]

    # Normalize for comparison (lowercase, remove extra whitespace)
    def normalize(s):
        return ' '.join(s.lower().split())

    first_normalized = normalize(first_part)

    # Count how many parts match the first
    matching_count = sum(1 for p in parts if normalize(p) == first_normalized)

    # If most parts are identical, return just the first one
    if matching_count >= len(parts) * 0.5:
        return first_part

    # Otherwise return original (parts are meaningfully different)
    return text


def _strip_markdown_for_word(text: str, preserve_newlines: bool = False) -> str:
    """
    Convert markdown-formatted text to plain text suitable for Word document.

    Handles:
    - Bold: **text** -> text
    - Sub-bullets: - (item) -> (item)
    - Headers: # Header -> Header
    - Preserves quotes and other content

    Args:
        preserve_newlines: If True, join lines with newlines instead of
            semicolons. Use for teaching entries where each line becomes
            a separate bullet (main entry + notes).
    """
    if not text:
        return ''

    # Remove bold markers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)

    # Handle sub-bullets - strip the dash prefix
    lines = text.split('\n')
    result_parts = []
    for line in lines:
        line = line.strip()
        if line.startswith('- '):
            # Sub-bullet, strip the prefix and any "Notes: " structural marker from Stage 5c
            line = line[2:].strip()
            if line.startswith('Notes: '):
                line = line[7:]
            elif line.startswith('Notes:'):
                line = line[6:].strip()
            result_parts.append(line)
        elif line:
            result_parts.append(line)

    # Join with appropriate separator
    if preserve_newlines:
        return '\n'.join(result_parts)
    elif len(result_parts) > 1:
        # Multiple lines - join with semicolon for compactness
        result = '; '.join(result_parts)
    else:
        result = result_parts[0] if result_parts else ''

    return result


def _clean_inline_tabs(text: str) -> str:
    """Render the pipeline's internal cell separators readably.

    Two separators are artifacts of how the readers flatten a source CV, and
    neither belongs in a rendered Word document:

    - " | " joins the cells of a table row (``docx_structure_extractor``). Those
      cells are columns, not a label/value pair, so they rejoin with " — ".
      A row whose cells are all empty is a blank template row carrying no
      information; it collapses to "" so callers can drop it.
    - "\\t" joins the "Label\\tValue" pairs of the WCM template's tables. A raw
      tab renders ragged against Word's default tab stops, so the first becomes
      ": " (label: value) and any further tabs become " — ".

    Properly structured content (mentee/board tables) is routed to real Word
    tables upstream via classification; this is the fallback for residual text.

    ponytail: the name says "tabs" but it now handles both separators. Kept as-is
    so this change does not collide with the three bullet call sites that #254
    also edits; rename to _clean_cell_separators once that has landed.
    """
    if not text:
        return text
    if "|" in text:
        text = " — ".join(c.strip() for c in text.split("|") if c.strip())
    if "\t" not in text:
        return text
    parts = [p.strip() for p in text.split("\t") if p.strip()]
    if len(parts) <= 1:
        return text.replace("\t", " ").strip()
    return parts[0] + ": " + " — ".join(parts[1:])


# A leading 3b taxonomy code (M2B, D1, S6, N3A …) that leaked into a rendered
# bullet — code letter + 1-2 digits + optional trailing letter, bracketed at the
# very start and followed by whitespace. Seen verbatim in output on the WCM-
# template CVs (issue #251): "• [M2B] Project title: …", "• [D1] Visiting Prof…".
_TAXONOMY_CODE_PREFIX = re.compile(r"^\s*\[[A-Z]\d{1,2}[A-Z]?\]\s+")


def _strip_taxonomy_code(text: str) -> str:
    """Drop a leading bracketed taxonomy code from bullet text before render.
    # ponytail: shape-match, not a code allowlist — could also strip a leading
    # grant-mechanism token like "[R01] " (rare as a bullet's first token); switch
    # to the TAXONOMY_TO_SECTION key set if that ever shows up in output."""
    return _TAXONOMY_CODE_PREFIX.sub("", text) if text else text
