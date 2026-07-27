#!/usr/bin/env python3
"""
Stage 6: WCM Word Template Generation

Generates a WCM-formatted Word document from Stage 5 enriched output.

Features:
- Fills all WCM template sections based on taxonomy codes
- Bolds the CV owner's name (target_name) in publications
- Adds Word comments for reformatted fields showing original text
- Uses tracked changes/comments for enriched data

Input: Stage 5 enriched JSON (or Stage 4 fields JSON if no enrichment needed)
Output: WCM-formatted .docx file

Author: Scholar Signals CV Pipeline
Date: 2025-11-29
"""

import os
import sys
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from collections import defaultdict

try:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches, Twips
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml.ns import qn, nsmap
    from docx.oxml import OxmlElement
    from docx.parts.document import DocumentPart
    from lxml import etree
except ImportError:
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from unified_pipeline.llm_client import call_llm
from unified_pipeline.core.render_check import entry_fragments
from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)


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


def _committee_cell_text(value) -> str:
    """Coerce a possibly-structured committee field to plain cell text.

    Stage 4 can emit a committee field as a dict or a list of record dicts for a
    multi-record entry (#208/#248 fusion), not just a string. Writing a non-str
    into a Word cell (``cell.text = <dict>``) raises deep in python-docx and
    aborts the whole document (#256). Never let that happen: pull the name-like
    value from a dict, join a list, and stringify anything else."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("committee_name") or value.get("committee")
                   or value.get("activity") or value.get("name")
                   or value.get("title") or "")
    if isinstance(value, list):
        return "; ".join(t for t in (_committee_cell_text(v) for v in value) if t)
    return str(value)


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


# XML namespaces for Word documents
WORD_NAMESPACE = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14_NAMESPACE = 'http://schemas.microsoft.com/office/word/2010/wordml'
NSMAP = {
    'w': WORD_NAMESPACE,
    'w14': W14_NAMESPACE,
}


# Date format specifications per WCM template section
# Format codes: 'mm/yyyy', 'mm/yy', 'yyyy', 'mm/dd/yyyy'
DATE_FORMATS = {
    'B1': 'mm/yyyy',      # Education: Dates attended (mm/yyyy-mm/yyyy)
    'B2': 'mm/yy',        # Other Education: Dates attended (mm/yy – mm/yy)
    'C': 'mm/yy',         # Postdoc Training: Dates (mm/yy - mm/yy)
    'C1': 'mm/yy',
    'C2': 'mm/yy',
    'D1': 'mm/yy',        # Academic Appointments: Dates (mm/yy - mm/yy)
    'D2': 'mm/yy',        # Hospital Appointments
    'D3': 'mm/yy',        # Other Positions
    'F1': 'mm/dd/yyyy',   # Licensure: Date of issue (mm/dd/yyyy)
    'F2': 'yyyy',         # Board Certification: Dates (yyyy–yyyy)
    'H': 'yyyy',          # Honors: Date awarded (yyyy)
    'I': 'yyyy',          # Memberships: Date (yyyy-yyyy)
    'K1': 'yyyy',         # Teaching activities
    'K2': 'yyyy',
    'K3': 'yyyy',
    'K4': 'yyyy',
    'K5': 'yyyy',
    'M2A': 'mm/yy',       # Grants: various date formats
    'M2B': 'mm/yy',
    'M2C': 'mm/yy',
    'N3A': 'yyyy',        # Mentoring
    'N3B': 'yyyy',
    'O': 'yyyy',          # Leadership
    'P': 'yyyy',          # Committees: Dates (yyyy-yyyy)
    'Q1': 'yyyy',         # Service activities
    'Q2': 'yyyy',
    'Q3': 'yyyy',
    'Q4': 'yyyy',
    'Q4A': 'yyyy',
    'Q4B': 'yyyy',
    'Q4C': 'yyyy',
    'Q4D': 'yyyy',
    'R': 'yyyy',          # Invited Presentations: Dates (yyyy)
    'S1': 'yyyy',         # Publications: year only
    'S2': 'yyyy',
    'S3': 'yyyy',
    'S4': 'yyyy',
    'S5': 'yyyy',
    'S6': 'yyyy',
    'S7': 'yyyy',
    'S8': 'yyyy',
    'S9': 'yyyy',
}


# Month name -> month number, for the date parser below. Includes the common
# 3-4 letter abbreviations CVs use ("Aug", "Sept"). Distinct from _MONTH_NAMES
# further down, which is the reverse (number -> name) for range formatting.
_MONTH_NAME_TO_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11,
    'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def _parse_date_components(date_str: str):
    """Parse a date string into (year, month, day) ints; any component absent
    from the input is None. Returns (None, None, None) when nothing parses.

    Single source of truth for date parsing, shared by format_date_for_section
    (rendering) and extract_sort_date (reverse-chron sorting) so the two cannot
    drift. They previously carried near-duplicate copies that HAD drifted: the
    sort copy lacked the '\\.?' in the month-name pattern, so "Aug. 2021" /
    "Sept. 2019" failed every branch and the entry sorted to the bottom of its
    section while still rendering its date correctly (issue #266).
    """
    s = str(date_str or '').strip()
    if not s:
        return (None, None, None)
    # YYYY-MM-DD / YYYY/MM/DD
    m = re.match(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # MM/DD/YYYY / MM-DD-YYYY
    m = re.match(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
    if m:
        return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
    # YYYY-MM / YYYY/MM (disjoint from MM/YYYY below: 4-digit lead vs 4-digit tail)
    m = re.match(r'(\d{4})[-/](\d{1,2})$', s)
    if m:
        return (int(m.group(1)), int(m.group(2)), None)
    # MM/YYYY / MM-YYYY
    m = re.match(r'(\d{1,2})[-/](\d{4})', s)
    if m:
        return (int(m.group(2)), int(m.group(1)), None)
    # Just YYYY
    m = re.match(r'^(\d{4})$', s)
    if m:
        return (int(m.group(1)), None, None)
    # Month YYYY -- "August 2021", "Aug 2021", "Aug. 2021", "Sept. 2019"
    m = re.match(r'([a-zA-Z]+)\.?\s*(\d{4})', s)
    if m:
        return (int(m.group(2)), _MONTH_NAME_TO_NUM.get(m.group(1).lower()), None)
    return (None, None, None)


def format_date_for_section(date_str: str, taxonomy_code: str, is_end_date: bool = False) -> str:
    """
    Format a date string according to the WCM template requirements for a section.

    Args:
        date_str: Input date string (various formats)
        taxonomy_code: Taxonomy code to determine required format
        is_end_date: True if this is an end date (affects 'present' handling)

    Returns:
        Formatted date string according to WCM requirements
    """
    if not date_str:
        return ''

    date_str = str(date_str).strip()

    # Handle 'present', 'current', 'ongoing' - always return as 'Present'
    if date_str.lower() in ('present', 'current', 'ongoing', 'now'):
        return 'Present'

    # Get required format for this taxonomy code
    required_format = DATE_FORMATS.get(taxonomy_code, 'yyyy')

    year, month, day = _parse_date_components(date_str)

    # If we couldn't parse it, return as-is
    if not year:
        return date_str
    year = str(year)

    # Format according to required format
    if required_format == 'yyyy':
        return year
    elif required_format == 'mm/yyyy':
        if month:
            return f"{month:02d}/{year}"
        return year  # Fall back to year only if no month
    elif required_format == 'mm/yy':
        if month:
            return f"{month:02d}/{year[-2:]}"
        # If no month, use full 4-digit year (2-digit looks odd standalone)
        return year
    elif required_format == 'mm/dd/yyyy':
        if month and day:
            return f"{month:02d}/{day:02d}/{year}"
        elif month:
            return f"{month:02d}/01/{year}"  # Default to 1st of month
        return year

    return date_str


def format_date_range(start_date: str, end_date: str, taxonomy_code: str) -> str:
    """
    Format a date range according to WCM template requirements.

    Args:
        start_date: Start date string
        end_date: End date string (may be 'present', empty, or a date)
        taxonomy_code: Taxonomy code to determine required format

    Returns:
        Formatted date range string (e.g., "08/17-07/21" or "2017-Present")
    """
    formatted_start = format_date_for_section(start_date, taxonomy_code)
    formatted_end = format_date_for_section(end_date, taxonomy_code, is_end_date=True)

    if formatted_start and formatted_end:
        # Avoid redundant ranges like "2024-2024" when both resolve to the same string
        if formatted_start == formatted_end:
            return formatted_start
        return f"{formatted_start}-{formatted_end}"
    elif formatted_start:
        # Avoid "Present-Present" when start is already 'Present'
        if formatted_start == 'Present':
            return 'Present'
        return f"{formatted_start}-Present"
    elif formatted_end:
        return formatted_end
    return ''


_MONTH_NAMES = {
    '01': 'January', '02': 'February', '03': 'March', '04': 'April',
    '05': 'May', '06': 'June', '07': 'July', '08': 'August',
    '09': 'September', '10': 'October', '11': 'November', '12': 'December',
    '1': 'January', '2': 'February', '3': 'March', '4': 'April',
    '5': 'May', '6': 'June', '7': 'July', '8': 'August',
    '9': 'September',
}

def normalize_iso_dates_in_text(text: str) -> str:
    """Replace ISO-format dates in free text with human-readable equivalents.

    Handles patterns the Stage 5c LLM sometimes produces:
      2021-03-01  -> March 2021
      2019-08-01–2019-09-01  -> August 2019–September 2019
      2018-08  -> August 2018
      2012-06–2012-07  -> June 2012–July 2012
    """
    if not text:
        return text

    def _iso_to_readable(m):
        year, month = m.group(1), m.group(2)
        day = m.group(3) if m.lastindex >= 3 and m.group(3) else None
        month_name = _MONTH_NAMES.get(month, month)
        return f"{month_name} {year}"

    # YYYY-MM-DD (drop the day)
    text = re.sub(r'\b(\d{4})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b', _iso_to_readable, text)
    # YYYY-MM (no day)
    text = re.sub(r'\b(\d{4})[-/](0?[1-9]|1[0-2])\b', _iso_to_readable, text)

    return text


_STOP_WORDS = frozenset({
    'a', 'an', 'and', 'as', 'at', 'be', 'by', 'for', 'from', 'i', 'in',
    'is', 'it', 'of', 'on', 'or', 'the', 'to', 'was', 'with',
})


def _significant_words(text: str) -> set:
    """Extract significant words from text, stripping stop words and punctuation."""
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _entry_signature_words(entry: Dict) -> set:
    """Extract significant words from an entry's full text."""
    return _significant_words(entry.get('text') or '')


def _entry_title_words(entry: Dict) -> set:
    """Extract significant words from the title/activity portion of an entry.

    Tries multiple strategies to isolate the meaningful title:
    1. Text before first tab (structured entries)
    2. Quoted text (presentation titles often in quotes)
    3. extracted_fields 'title' or 'activity_title'
    4. Fallback to first 100 chars
    """
    text = (entry.get('text') or '')
    if '\t' in text:
        title = text.split('\t')[0]
    else:
        # Try to find quoted title (common for presentations)
        quoted = re.findall(r'["\u201c](.+?)["\u201d]', text)
        if quoted:
            title = ' '.join(quoted)
        else:
            # Try extracted fields
            fields = entry.get('extracted_fields', {}) or {}
            title = (fields.get('title') or fields.get('activity_title') or
                     fields.get('presentation_title') or '')
            if not title:
                title = text[:100]
    return _significant_words(title)


def _get_entry_date_range(entry: Dict) -> tuple:
    """Extract (start_date, end_date) strings from an entry for dedup comparison."""
    fields = entry.get('extracted_fields', {}) or {}
    return (fields.get('start_date', '') or '', fields.get('end_date', '') or '')


def _dates_overlap_or_match(entry_a: Dict, entry_b: Dict) -> bool:
    """Return True if two entries have the same or overlapping date ranges.

    Used to distinguish true duplicates (same thing listed twice) from career
    progressions (different roles at the same institution in different periods).
    If either entry lacks dates, we conservatively return True (assume possible dup).
    """
    start_a, end_a = _get_entry_date_range(entry_a)
    start_b, end_b = _get_entry_date_range(entry_b)
    # If either lacks dates, can't prove they're different — allow dedup
    if not start_a or not start_b:
        return True
    # Exact match (most common for true duplicates)
    if start_a == start_b and end_a == end_b:
        return True
    # Check overlap: parse to comparable strings (YYYY-MM format sorts correctly)
    # Normalize to just YYYY-MM for comparison
    sa = start_a[:7]  # "2008-07" from "2008-07-01"
    sb = start_b[:7]
    ea = (end_a[:7] if end_a and end_a.lower() != 'present' else '9999-12')
    eb = (end_b[:7] if end_b and end_b.lower() != 'present' else '9999-12')
    # Overlap test: A.start <= B.end AND B.start <= A.end
    return sa <= eb and sb <= ea


# _drop_is_safe: a reworded true duplicate ("Associate Professor, HPE, USUHS"
# inside "...Department of Health Professions Education (HPE) Uniformed
# Services University...") has EVERY significant word contained in the kept
# entry — but so does a 3-token degree line whose distinguishing token the
# tokenizer destroyed ('M.S' vs 'PhD', the 2Q1_ZQ B1 loss). Full containment
# only proves duplication when the dropped entry carries enough tokens.
DEDUP_FULL_CONTAINMENT_MIN_TOKENS = 5

# _drop_is_safe token-containment is a TRUE-DUPLICATE signal only when the kept
# entry is itself a single record. When the kept entry is a FUSED multi-record
# blob (a whole layout table captured atomically, #208), a distinct single
# record is fully token-contained in it merely because the blob swallowed it —
# dropping it is real content loss, not deduplication (C0ZGFW: 35 invited
# presentations + 3 teaching records dropped into "Title/Institution/Dates"
# table blobs of 52 and 13 record-lines). A blob this size is the fusion bug,
# not a duplicate. ponytail: gate on record-line count; the source fix is
# de-fusing the table in stage 2 (#208/#248).
DEDUP_FUSED_BLOB_RECORD_LINES = 5


def _drop_is_safe(dropped_entry: Dict, kept_entry: Dict) -> bool:
    """#227 guard: only drop an entry when the loss is provably recoverable.

    Safe when the dropped text is verbatim-contained in the kept entry, or
    every significant word of a token-rich dropped entry appears in the kept
    entry (both are true-duplicate shapes) AND the kept entry is not a fused
    multi-record blob, or the dropped entry is a fused multi-record candidate —
    those the #221/#225 recovery pass re-verifies line by line against the
    rendered document. A single-line entry that merely SCORES similar is the
    #227 loss class: distinct records sharing role/date/venue boilerplate (7 of
    8 drops on 2Q1_ZQ were real content loss, all single-line); a distinct
    record swallowed by a fused table blob is the same loss class (C0ZGFW)."""
    dropped_squashed = _squash(dropped_entry.get('text', ''))
    if dropped_squashed and dropped_squashed in _squash(kept_entry.get('text', '')):
        return True
    dropped_sig = _entry_signature_words(dropped_entry)
    if (len(dropped_sig) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS
            and dropped_sig <= _entry_signature_words(kept_entry)
            and len(_record_lines(kept_entry.get('text', ''))) < DEDUP_FUSED_BLOB_RECORD_LINES):
        return True
    return len(_record_lines(dropped_entry.get('text', ''))) >= UNRENDERED_MIN_RECORD_LINES


def deduplicate_entries(entries: List[Dict], verbose: bool = False,
                        require_date_overlap: bool = False,
                        decisions: Optional[List[Dict]] = None) -> List[Dict]:
    """Remove near-duplicate entries within a code group.

    Uses two metrics to catch duplicates:
    1. Jaccard similarity (symmetric) — catches similar-length entries
    2. Containment (asymmetric) — catches when a short entry is a subset
       of a longer one (e.g., brief mention vs. detailed description)

    When two entries are duplicates, the longer / more detailed one is kept.

    If require_date_overlap is True, text-similar entries are only deduped when
    their date ranges match or overlap.  This prevents false positives on career
    progression sequences (e.g., Intern -> Resident -> Chief Resident at same
    institution) where word overlap is high but dates differ.

    If decisions is a list, every drop is appended to it as a dict (metric
    values plus dropped/kept text) so the caller can persist the decision
    trail for the run doctor (#227: at these thresholds a drop is not always
    a true duplicate).
    """
    if len(entries) <= 1:
        return entries

    sigs = [_entry_signature_words(e) for e in entries]
    titles = [_entry_title_words(e) for e in entries]
    drop_indices = set()

    for i in range(len(entries)):
        if i in drop_indices:
            continue
        for j in range(i + 1, len(entries)):
            if j in drop_indices:
                continue
            if not sigs[i] or not sigs[j]:
                continue
            intersection = sigs[i] & sigs[j]
            union = sigs[i] | sigs[j]
            smaller = min(len(sigs[i]), len(sigs[j]))

            jaccard = len(intersection) / len(union) if union else 0
            containment = len(intersection) / smaller if smaller else 0

            # Also check title-only similarity (text before first tab).
            # This catches cases where both entries describe the same activity
            # but have very different narrative descriptions.
            # Require at least 4 significant words in the smaller title to avoid
            # false positives from short generic titles like "Emergency Medicine".
            title_containment = 0.0
            if titles[i] and titles[j]:
                title_smaller = min(len(titles[i]), len(titles[j]))
                if title_smaller >= 4:
                    title_inter = titles[i] & titles[j]
                    title_containment = len(title_inter) / title_smaller if title_smaller else 0

            is_dup = jaccard >= 0.6 or containment >= 0.75 or title_containment >= 0.8

            # Safety check: if full-text metrics trigger but titles are clearly
            # different, these are likely distinct items at the same venue (e.g.,
            # two different talks at the same grand rounds session).
            if is_dup and title_containment < 0.8 and titles[i] and titles[j]:
                title_union = titles[i] | titles[j]
                title_jaccard = (len(titles[i] & titles[j]) / len(title_union)
                                 if title_union else 0)
                if title_jaccard <= 0.25 and min(len(titles[i]), len(titles[j])) >= 3:
                    if verbose:
                        print(f"    Dedup: skipping (different titles, "
                              f"title_jaccard={title_jaccard:.2f}) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False

            # For career-progression codes, require date overlap to confirm
            if is_dup and require_date_overlap:
                if not _dates_overlap_or_match(entries[i], entries[j]):
                    if verbose:
                        print(f"    Dedup: skipping (dates differ) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False
            if is_dup:
                # Keep the longer (more detailed) entry
                len_i = len(entries[i].get('text', ''))
                len_j = len(entries[j].get('text', ''))
                drop = j if len_i >= len_j else i
                kept = i if drop == j else j
                if not _drop_is_safe(entries[drop], entries[kept]):
                    if verbose:
                        print(f"    Dedup: skipping (similar but not "
                              f"verbatim-contained, single record — keeping "
                              f"both, #227) "
                              f"[{entries[drop].get('text', '')[:50]}...]")
                    continue
                if jaccard >= 0.6:
                    metric = f"jaccard={jaccard:.2f}"
                elif containment >= 0.75:
                    metric = f"containment={containment:.2f}"
                else:
                    metric = f"title={title_containment:.2f}"
                if verbose:
                    print(f"    Dedup: dropping entry ({metric}), "
                          f"keeping [{entries[kept].get('text', '')[:60]}...]")
                if decisions is not None:
                    decisions.append({
                        "metric": metric,
                        "jaccard": round(jaccard, 2),
                        "containment": round(containment, 2),
                        "title_containment": round(title_containment, 2),
                        "dropped_text": entries[drop].get('text', '')[:500],
                        "kept_text": entries[kept].get('text', '')[:500],
                    })
                drop_indices.add(drop)
                if drop == i:
                    # i is gone: it must not keep vouching to drop later j's
                    # (observed over-drop vector in the 2Q1_ZQ S8 trace, #227)
                    break

    if drop_indices:
        return [e for idx, e in enumerate(entries) if idx not in drop_indices]
    return entries


def element_idx_sort_key(value) -> tuple:
    """Document-order sort key tolerant of stage-2's mixed index types.

    ``element_idx_start`` is not uniformly typed: stage 2 writes a plain int for
    paragraph entries, a ``"table_N"`` string for table blocks, and a
    ``"row.col"`` string such as ``"22.2"`` for table rows. Sorting these raw
    raises ``TypeError: '<' not supported between instances of 'str' and 'int'``
    whenever a section mixes them. Normalize every form to a ``(major, minor)``
    float tuple so the comparison is total and preserves document order. Mirrors
    ``normalize_idx`` in stage_2_entry_extraction.py.
    """
    if value is None:
        return (float('inf'), 0.0)
    if isinstance(value, str):
        if '.' in value:
            parts = value.split('.', 1)
            try:
                return (float(parts[0]), float(parts[1]))
            except ValueError:
                return (float('inf'), 0.0)
        if value.startswith('table_'):
            try:
                return (1_000_000.0 + float(value.split('_')[1]), 0.0)
            except (ValueError, IndexError):
                return (float('inf'), 0.0)
    try:
        return (float(value), 0.0)
    except (ValueError, TypeError):
        return (float('inf'), 0.0)


def extract_sort_date(entry: Dict) -> tuple:
    """
    Extract a sortable date tuple from an entry for reverse-chronological ordering.

    Returns (year, month, day) tuple where:
    - 'Present'/current entries get (9999, 12, 31) to sort first
    - Entries with dates get their actual values
    - Entries with no date get (0, 0, 0) to sort last

    Args:
        entry: Entry dictionary with extracted_fields

    Returns:
        Tuple (year, month, day) for sorting
    """
    fields = entry.get('extracted_fields', {})

    # Try various date fields in order of preference
    date_candidates = [
        fields.get('end_date', ''),
        fields.get('year', ''),
        fields.get('year_awarded', ''),
        fields.get('start_date', ''),
        fields.get('date', ''),
        fields.get('publication_date', ''),
    ]

    for date_str in date_candidates:
        if not date_str:
            continue

        date_str = str(date_str).strip().lower()

        # 'Present' or 'current' sorts first (most recent)
        if date_str in ('present', 'current', 'ongoing', 'now'):
            return (9999, 12, 31)

        # Shared parser (see format_date_for_section). Month/day absent from the
        # input default to 1 so partial dates ("2019", "Aug. 2021") still sort
        # sensibly within their year.
        year, month, day = _parse_date_components(date_str)
        if year:
            return (year, month if month is not None else 1,
                    day if day is not None else 1)

    # No date found - sort last
    return (0, 0, 0)


def sort_entries_reverse_chronological(entries: List[Dict]) -> List[Dict]:
    """
    Sort entries in reverse chronological order (most recent first).

    Entries with 'Present' or current dates sort first.
    Entries with no date sort last.

    Args:
        entries: List of entry dictionaries

    Returns:
        Sorted list of entries
    """
    return sorted(entries, key=extract_sort_date, reverse=True)


def split_fused_citation_entries(pubs: List[Dict]) -> List[Dict]:
    """Un-fuse publication entries whose stage-5d ``formatted_citation`` carries
    multiple newline-separated citations.

    The #208 fusion class reaches the bibliography too: when several source
    citations collapse into one entry, stage 5d formats the whole block into a
    single ``formatted_citation`` (newline-separated), and _fill_bibliography
    then renders ONE numbered item followed by unnumbered ``<w:br/>``
    continuation lines (the "no numbering on some pubs" symptom, HNFLBA S8).

    Splitting each non-blank line into its own entry lets the caller number them
    individually. A non-fused citation is a single line (verified: 100/101 of a
    real CV's formatted_citations have zero internal newlines), so it passes
    through untouched. Only stage-5d LLM citations with >=2 lines are split;
    other bibliography shapes (parts-built citations) never carry newlines.
    Continuation lines (i>0) are distinct records, so per-entry provenance that
    belongs to the block as a whole (classification comment, enrichment
    track-change, original text) is kept on the first line only, not replayed
    on each.
    """
    out: List[Dict] = []
    for pub in pubs:
        fields = pub.get('extracted_fields') or {}
        fc = fields.get('formatted_citation') or ''
        lines = [ln.strip() for ln in fc.splitlines() if ln.strip()]
        if fields.get('formatting_source') == 'stage_5d_llm' and len(lines) >= 2:
            for i, line in enumerate(lines):
                clone = dict(pub)
                clone['extracted_fields'] = {**fields, 'formatted_citation': line}
                if i > 0:
                    clone['enrichment_status'] = ''
                    clone['text'] = ''
                    clone.pop('classification_reasoning', None)
                out.append(clone)
        else:
            out.append(pub)
    return out


# Paths - Use the official WCM template
TEMPLATE_PATH = Path(__file__).parent.parent.parent / "key_files" / "wcm_cv_template_faculty_october_2022_final.docx"
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_6_wcm_documents"
# Local-dev only: where sample source CVs live, for the generate() fallback that
# locates an original docx when the caller didn't pass one. Absent in the
# deployed image (the server always passes original_doc_path explicitly).
SAMPLE_CV_DIR = Path(__file__).parent.parent.parent / "data" / "sample_cvs" / "word"

# Fallback template paths
FALLBACK_TEMPLATES = [
    Path(__file__).parent / "cv_parser" / "cv_template_wcm.docx",
    Path(__file__).parent.parent.parent / "business" / "examples" / "template" / "wcm_cv_template_faculty_october_2022_final.docx",
]


# Retired taxonomy codes that were pure renames of a still-live code. Stage-3b
# occasionally still emits the old code (e.g. patents tagged as the retired M3),
# which has no render route and gets silently dropped. Normalize to the live code
# at grouping time so the existing renderer picks them up.
# ponytail: pure renames only. Codes with NO live equivalent (N4, M4C) need a
# real render route instead — see #261; don't add them here.
RETIRED_TAXONOMY_CODES = {
    'M3': 'M2D',  # Patents & Innovations — former M3 renamed to M2D (taxonomy v7)
}
# ponytail: pure renames ONLY — old code and target must mean the same thing.
# Deliberately NOT here:
#   M4A/M4B/M4C (clinical trials). update_m4_to_m2.py suggests M4A->M2A/M4B->M2B,
#   but that mapping is WRONG against the live taxonomy: M4A/M4B/M4C are trial
#   TYPES (Interventional / Observational / Device), while M2A/M2B/M2C are funding
#   STATUS (Current / Past / Pending). Renaming type->status files completed trials
#   under "Current Research Funding" (verified on web059). Trials need status-aware
#   routing, not a static map — see the clinical-trials issue.
#   N4/M4C have no live equivalent and need real render routes — see #261.


def normalize_retired_code(entry: Dict) -> str:
    """Rewrite a retired taxonomy code on ``entry`` to its live equivalent.

    Preserves the pre-normalization code under ``taxonomy_code_original`` (same
    convention as the #261 mismatch path) and returns the effective code. A
    non-retired code is returned unchanged and the entry is left untouched.
    """
    code = entry.get('taxonomy_code', 'T')
    live = RETIRED_TAXONOMY_CODES.get(code)
    if live:
        entry['taxonomy_code_original'] = code
        entry['taxonomy_code'] = live
        return live
    return code


# Taxonomy code to WCM section mapping
TAXONOMY_TO_SECTION = {
    # Personal Data
    'A': 'personal_data',

    # Education
    'B1': 'education',
    'B2': 'education',
    'C': 'postdoc_training',

    # Positions
    'D1': 'academic_appointments',
    'D2': 'hospital_appointments',
    'D3': 'other_positions',

    # Licensure
    'F1': 'licensure',
    'F2': 'board_certification',

    # Honors
    'H': 'honors',

    # Memberships
    'I': 'memberships',

    # Teaching
    'K1': 'teaching',
    'K2': 'teaching',
    'K3': 'teaching_leadership',
    'K4': 'cme',
    'K5': 'community_education',

    # Research
    'M1': 'research_activities',
    'M2A': 'current_grants',
    'M2B': 'completed_grants',
    'M2C': 'pending_grants',
    'M2D': 'patents',
    # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C based on status

    # Mentoring
    'N3A': 'current_mentees',
    'N3B': 'past_mentees',

    # Service
    'O': 'institutional_leadership',
    'P': 'committees',
    'Q1': 'editorial',
    'Q2': 'reviewer',
    'Q3': 'extramural_committees',
    'Q4': 'professional_service',

    # Presentations
    'R': 'invited_presentations',

    # Bibliography
    'S0': 'researcher_profile',
    'S1': 'peer_reviewed',
    'S2': 'reviews_editorials',
    'S3': 'books',
    'S4': 'book_chapters',
    'S5': 'technical_reports',
    'S6': 'case_reports',
    'S7': 'in_review',
    'S8': 'abstracts',
    'S9': 'other_media',

    # Misc
    'T': 'miscellaneous',
}


# Fields whose values identify a specific record (vs. generic values like a
# status string shared by many records). Used by segment_already_rendered.
_IDENTIFYING_FIELDS = (
    'title', 'project_title', 'agency', 'award_source',
    'mentee_name', 'organization', 'grant_number',
)

# Month words (>=4 alphabetic chars) allowed inside a date-like field value.
_MONTH_WORDS = frozenset((
    'january', 'february', 'march', 'april', 'june', 'july', 'august',
    'september', 'sept', 'october', 'november', 'december',
))


def _value_is_datelike(v: str) -> bool:
    """True when a normalized field value carries no identifying prose —
    only date/number/punctuation content (month names allowed). Date ranges
    are shared across the sibling records of a fused entry, and bare
    alphanumeric codes ('1F30AG032861-01A1') read the same wherever they
    land, so such values must never vouch on their own that a specific
    record rendered (#221 post-review: on corpus CV 2054 entry 66.16 they
    outvoted a genuinely absent grant record line)."""
    return all(word in _MONTH_WORDS for word in re.findall(r'[a-z]{4,}', v))


def segment_already_rendered(segment_text: str, extracted_fields: Dict) -> bool:
    """True if an overflow segment duplicates content already rendered from
    this entry's extracted fields — e.g. the first grant of an under-extracted
    multi-record entry, which DID make it into a funding table (#209).

    Matches only identifying fields (title/agency/name), never generic ones
    (a status like "Submitted 2026, Under review" is shared across records
    and would wrongly mark unrendered siblings as duplicates). Within those
    fields, values with no alphabetic word beyond month names (date ranges,
    bare grant numbers) never vouch either — dates are shared across sibling
    records (#221 post-review). A title too
    short to identify a record on its own ("Professor", "Chair") counts only
    together with the record's other anchors: BOTH extracted date endpoints
    (fused career-progression siblings share a boundary date and title
    suffixes — "Associate Professor" contains "Professor" — but not both
    endpoints) plus the extracted institution/organization when there is one.

    ponytail: normalized substring match; upgrade to token-overlap scoring if
    false positives appear.
    """
    if not extracted_fields:
        return False
    seg = re.sub(r'\s+', ' ', segment_text or '').lower()

    def _norm_val(value) -> str:
        if not isinstance(value, str):
            return ''
        return re.sub(r'\s+', ' ', value).lower().strip()

    def _word_in_seg(v: str) -> bool:
        # Word-bounded so 'present' can't match inside 'presentation'.
        return bool(v) and bool(
            re.search(r'(?<!\w)' + re.escape(v) + r'(?!\w)', seg))

    for key in _IDENTIFYING_FIELDS:
        v = _norm_val(extracted_fields.get(key))
        if len(v) >= 15 and v in seg and not _value_is_datelike(v):
            return True

    # Short-title conjunction (#221 review): the extracted record's source
    # line often carries department/descriptor tokens the table render omits,
    # so the recovery token check alone can't recognize it as rendered.
    title = _norm_val(extracted_fields.get('title')
                      or extracted_fields.get('project_title'))
    if not (title and len(title) < 15 and _word_in_seg(title)):
        return False
    dates = [_norm_val(d) for d in (extracted_fields.get('start_date'),
                                    extracted_fields.get('end_date'))]
    if not all(dates) or not all(_word_in_seg(d) for d in dates):
        return False
    org = _norm_val(extracted_fields.get('institution')
                    or extracted_fields.get('organization')
                    or extracted_fields.get('agency'))
    return not org or org in seg


# ---------------------------------------------------------------------------
# Unrendered-record recovery (#221).
#
# The structured-fields-only render paths (positions, licensure, honors,
# committees, presentations, ...) render ONE row/bullet from an entry's
# extracted_fields and silently drop the unextracted remainder record lines of
# a fused multi-record entry. The constants and helpers below mirror
# run_doctor's lint 8 ("unrendered_records") so the offline doctor and this
# render-time safety net agree on what "a record line" and "rendered" mean.
# run_doctor is optional tooling and must not become a pipeline import — keep
# the two copies in sync by name.

RENDER_TOKEN_MIN_COUNT = 3
RENDER_TOKEN_OVERLAP = 0.7
_RENDER_TOKEN_RE = re.compile(r"[a-z]{5,}")
RENDER_PIECE_MIN_CHARS = 15
RENDER_PIECE_WINDOW = 40

# An entry is a fused multi-record candidate at this many record-like lines.
# _looks_like_record only sees pipe/tab rows; employment/appointment records
# are date-range-prefixed comma lines ("Jun 2020-Jun 2025, Assistant
# Professor"), caught by the prefix pattern when the line carries a payload
# beyond the bare date range.
UNRENDERED_MIN_RECORD_LINES = 2
RECORD_DATE_LINE_MIN_CHARS = 20
_RECORD_DATE_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{3,9}\.? )?\d{4}\s*[-–—]")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def _squash(text) -> str:
    """Whitespace-FREE normalization for verbatim containment checks."""
    return re.sub(r"\s+", "", str(text or "")).lower()


def _looks_like_record(line: str) -> bool:
    line = line.strip()
    return len(line) > 60 and (" | " in line or "\t" in line)


# Column-label vocabulary for the no-digit row filter below: a multi-cell row
# with no year/number payload is only header furniture when a majority of its
# words are table labels — dateless multi-cell rows can be real records
# ("Member | Committee on X | Organization Y | description").
_COLUMN_HEADER_WORDS = frozenset({
    'state', 'country', 'license', 'number', 'status', 'date', 'dates',
    'issue', 'issued', 'expiration', 'expires', 'title', 'organization',
    'role', 'committee', 'type', 'location', 'institution', 'certification',
    'name', 'year', 'years', 'description',
})


def _is_column_header_row(line: str) -> bool:
    """True when a majority of the row's words are column-label vocabulary
    ("State/Country  License Number  Status  Date of Issue ...")."""
    words = [w.strip('.,;:()') for w in re.split(r'[\s\t|/]+', _norm(line))]
    words = [w for w in words if w]
    if not words:
        return True
    hits = sum(1 for w in words if w in _COLUMN_HEADER_WORDS)
    return hits / len(words) >= 0.5


def _record_lines(text) -> List[str]:
    """Record-like lines of an entry: pipe/tab rows plus date-range-prefixed
    lines that carry a payload beyond the bare date range."""
    return [line.strip() for line in str(text or "").split("\n")
            if _looks_like_record(line)
            or (len(line.strip()) >= RECORD_DATE_LINE_MIN_CHARS
                and _RECORD_DATE_PREFIX_RE.match(line.strip()))]


def _entry_pieces(text) -> List[str]:
    """Squashed fragments of an entry long enough to be looked up verbatim in
    the rendered-output haystack."""
    pieces = []
    for frag in entry_fragments(text):
        squashed = _squash(frag)
        if len(squashed) >= RENDER_PIECE_MIN_CHARS:
            pieces.append(squashed[:RENDER_PIECE_WINDOW])
    return pieces


def _record_rendered(line: str, haystack: str,
                     line_token_sets: List[set]) -> Optional[bool]:
    """Whether one record line surfaces in the output: verbatim piece first,
    then per-output-line token overlap (per-line, not pooled, so common
    academic words scattered across unrelated sections can't vouch for a
    dropped record). Verbatim absence alone proves nothing — stage 6
    reformats dates/fields — so False requires a token-verifiable miss; a
    line without enough distinctive tokens is None, not missing."""
    if any(piece in haystack for piece in _entry_pieces(line)):
        return True
    rendered = None
    for chunk in [line] + entry_fragments(line):
        tokens = set(_RENDER_TOKEN_RE.findall(_norm(chunk)))
        if len(tokens) < RENDER_TOKEN_MIN_COUNT:
            continue
        if any(len(tokens & line_tokens) / len(tokens) >= RENDER_TOKEN_OVERLAP
               for line_tokens in line_token_sets):
            return True
        rendered = False
    return rendered


def grant_status_rebucket_target(status: str) -> Tuple[Optional[str], Optional[str]]:
    """Map a grant's extracted status string to the funding bucket it belongs
    in (#210). Returns (target_code, reclassification_note); (None, None)
    when the status doesn't force a move.

    An explicit status beats date inference: "Under review" / "Submitted" is
    Pending (M2C) no matter what dates say; "Not funded" is kept under
    Pending with a review comment rather than silently dropped.
    """
    status = (status or '').strip()
    if not status:
        return None, None
    lowered = status.lower()
    if re.search(r'not\s+funded|unfunded|declined|rejected', lowered):
        return 'M2C', (
            f"Status is '{status}' — kept under Pending Funding rather than "
            "dropped; confirm whether to keep this entry on the CV"
        )
    if 'award' not in lowered and re.search(r'under\s+review|submitted|pending', lowered):
        return 'M2C', f"Reclassified to Pending (M2C): status is '{status}'"
    if re.search(r'\bcompleted?\b|\bclosed\b|\bexpired\b', lowered):
        return 'M2B', f"Reclassified to Completed (M2B): status is '{status}'"
    return None, None


class WCMTemplateGenerator:
    """
    Generates WCM Word documents from enriched CV data.
    """

    def __init__(self, template_path: str = None, verbose: bool = True,
                 emit_track_changes: bool = True, emit_comments: bool = False,
                 strip_template_instructions: bool = True,
                 recover_unrendered_records: bool = True):
        # Find a valid template path
        self.template_path = self._find_template(template_path)
        self.verbose = verbose
        self.doc = None

        # When True, drop the WCM template's leading gray "instruction box"
        # (table[0]) from the generated document. That box is template
        # scaffolding baked into the .docx, not extracted CV content, so the
        # Stage 2 entry filter never sees it — it has to be removed here.
        self.strip_template_instructions = strip_template_instructions

        # Output-rendering options (issue #153). Defaults mirror the Run model
        # column defaults: track changes ON, classification comments OFF.
        # When emit_track_changes is False, insertions/deletions render as plain
        # runs (final text only) so the document stays valid and readable.
        # When emit_comments is False, no commentReference is emitted and no
        # comments.xml part is created.
        self.emit_track_changes = emit_track_changes
        self.emit_comments = emit_comments

        # Post-render safety net (#221): after all sections render, re-emit
        # record lines of fused multi-record entries that provably did not
        # surface anywhere in the document (the structured-fields-only render
        # paths keep the extracted record and drop the remainder).
        self.recover_unrendered_records = recover_unrendered_records

        # CV owner location context for geographic scope classification
        self.cv_owner_location = None

        # Track changes and comments
        self._comment_id = 0
        self._revision_id = 0
        self._comments = []  # Store comments to add to comments.xml

        # Content overflow tracking: entries where extraction lost significant content
        self._overflow_entries = []  # List of (entry, para, taxonomy_code) tuples

        # Appendix entries pending reconsideration
        self._appendix_pending = []  # List of (entry, coverage_pct) tuples

        # Memoizes _classify_geographic_scope's LLM calls for the life of one
        # render, keyed on (activity location, owner institutions).
        self._geographic_scope_cache = {}

        # Statistics
        self.stats = {
            'sections_filled': 0,
            'entries_inserted': 0,
            'tables_populated': 0,
            'target_names_bolded': 0,
            'comments_added': 0,
            'track_changes_added': 0,
            'overflow_bullets_added': 0,
            'overflow_to_appendix': 0,
            'appendix_segments_reconsidered': 0,
            'unrendered_records_recovered': 0,
        }

    def _find_template(self, template_path: str = None) -> str:
        """Find a valid template file."""
        if template_path and os.path.exists(template_path):
            return template_path

        # Try primary template
        if TEMPLATE_PATH.exists():
            return str(TEMPLATE_PATH)

        # Try fallbacks
        for fallback in FALLBACK_TEMPLATES:
            if fallback.exists():
                return str(fallback)

        raise FileNotFoundError(
            f"Could not find WCM template. Tried:\n"
            f"  - {TEMPLATE_PATH}\n"
            f"  - {FALLBACK_TEMPLATES}"
        )

    def _correct_mismatch_if_needed(self, entry: Dict, assigned_code: str) -> str:
        """Correct taxonomy code routing when hierarchy mismatch flag indicates a likely misclassification.

        Conservative correction rules:
        - Same-family reroutes (e.g., K5→K1): always applied since the LLM got the family
          right but the sub-type wrong, and the CV's section structure is a better judge.
        - Cross-family reroutes (e.g., C→K1): only applied when the LLM's confidence
          was low (< 0.7), since the content analysis may have been uncertain.

        Returns:
            The (possibly corrected) taxonomy code to use for routing.
        """
        if not entry.get('hierarchy_mismatch_flag'):
            return assigned_code

        detail = entry.get('hierarchy_mismatch_detail', {})
        expected_codes = detail.get('expected_codes', [])
        if not expected_codes:
            return assigned_code

        # Pick the most specific expected code (longest, e.g., "K1" over "K")
        best_expected = max(expected_codes, key=len)

        # Check if correction would change the WCM section
        assigned_section = TAXONOMY_TO_SECTION.get(assigned_code)
        expected_section = TAXONOMY_TO_SECTION.get(best_expected)
        if not expected_section or assigned_section == expected_section:
            return assigned_code

        # Same family: LLM got the broad category right, hierarchy knows the sub-type
        assigned_family = assigned_code[0] if assigned_code else ''
        expected_family = best_expected[0] if best_expected else ''
        confidence = entry.get('taxonomy_confidence', 1.0)

        if assigned_family == expected_family:
            if self.verbose:
                print(f"    Mismatch correction: {assigned_code}→{best_expected} "
                      f"(same family, hierarchy-guided) [{entry.get('text', '')[:60]}...]")
            entry['taxonomy_code_original'] = assigned_code
            entry['taxonomy_code'] = best_expected
            return best_expected

        # Cross-family: only if LLM confidence was low
        if confidence < 0.7:
            if self.verbose:
                print(f"    Mismatch correction: {assigned_code}→{best_expected} "
                      f"(cross-family, low confidence {confidence:.2f}) [{entry.get('text', '')[:60]}...]")
            entry['taxonomy_code_original'] = assigned_code
            entry['taxonomy_code'] = best_expected
            return best_expected

        return assigned_code

    def _merge_stage_5c_entries(self, base_entries: List[Dict], stage_5c_entries: List[Dict]) -> List[Dict]:
        """Merge Stage 5c K-code entries as overrides into base entries.

        Stage 5c contains only K-code entries with LLM formatting.
        We replace matching K-code entries in base with the Stage 5c versions.

        Args:
            base_entries: Full entry list from Stage 5/5b/4
            stage_5c_entries: K-code entries from Stage 5c (override layer)

        Returns:
            Merged entry list with K-codes from Stage 5c
        """
        # Build lookup for Stage 5c entries by element_idx_start (unique identifier)
        stage_5c_by_idx = {}
        for entry in stage_5c_entries:
            idx = entry.get('element_idx_start')
            if idx is not None:
                stage_5c_by_idx[str(idx)] = entry

        # Replace K-code entries in base with Stage 5c versions
        merged = []
        k_codes = {'K1', 'K2', 'K3', 'K4', 'K5'}
        replaced_count = 0

        for entry in base_entries:
            code = entry.get('taxonomy_code', '')
            idx = str(entry.get('element_idx_start', ''))

            if code in k_codes and idx in stage_5c_by_idx:
                # Replace with Stage 5c version
                merged.append(stage_5c_by_idx[idx])
                replaced_count += 1
            else:
                merged.append(entry)

        if self.verbose and replaced_count > 0:
            print(f"  Replaced {replaced_count} K-code entries with Stage 5c formatted versions")

        return merged

    # Distinctive header of the WCM template's gray instruction box. This
    # phrase never appears in real CV content, so a substring match on it
    # uniquely identifies the box and nothing else.
    _INSTRUCTION_BOX_SIGNATURE = "when preparing the wcm cv template"

    def _remove_instruction_box(self) -> None:
        """Remove the leading gray "instruction box" table(s) from self.doc.

        The box is a shaded table baked into the template that tells the author
        how to fill it in ("When preparing the WCM CV template ... delete this
        instruction box"). We match it by its distinctive header text rather
        than by index so real content tables are never touched.
        ponytail: signature-substring match on one table; upgrade to a phrase
        set only if a future template ships a differently-worded box.
        """
        removed = 0
        for tbl in list(self.doc.tables):
            text = " ".join(
                cell.text for row in tbl.rows for cell in row.cells
            ).lower()
            if self._INSTRUCTION_BOX_SIGNATURE in text:
                tbl._element.getparent().remove(tbl._element)
                removed += 1
        if removed and self.verbose:
            print(f"Removed {removed} WCM-template instruction box(es)")

    def generate(self, input_path: str, output_path: str = None, research_summary_path: str = None,
                 original_doc_path: str = None) -> str:
        """
        Main entry point: Generate WCM document from pipeline output.

        Each stage output is self-contained with complete state, so Stage 6
        simply reads from the latest stage output (5c > 5b > 5 > 4).

        Args:
            input_path: Path to pipeline JSON output (Stage 5c/5b/5/4)
            output_path: Optional output path
            research_summary_path: Optional path to Stage 4.5 research summary JSON
            original_doc_path: Optional path to original Word document (for fallback email extraction)

        Returns:
            Path to generated document
        """
        # Load input data - each stage output is self-contained
        with open(input_path, 'r') as f:
            data = json.load(f)

        document_uid = data.get('document_uid', 'unknown')
        entries = data.get('entries', [])
        cv_owner = data.get('cv_owner', {})
        cv_owner_location = data.get('cv_owner_location', {})

        # If cv_owner_location not in input file, try to load from Stage 4 output
        if not cv_owner_location or not cv_owner_location.get('inference_success'):
            stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
            stage4_candidates = list(stage4_dir.glob(f"*{document_uid}*_fields.json"))
            if stage4_candidates:
                try:
                    with open(stage4_candidates[0], 'r') as f:
                        stage4_data = json.load(f)
                    cv_owner_location = stage4_data.get('cv_owner_location', {})
                    if cv_owner_location and cv_owner_location.get('inference_success') and self.verbose:
                        print(f"Loaded cv_owner_location from Stage 4 output")
                except Exception as e:
                    # Non-fatal: geographic-scope classification just falls back
                    # to its default. Still say so -- a permission error or a
                    # truncated stage-4 JSON should not vanish without a trace.
                    if self.verbose:
                        print(f"  Warning: Could not load cv_owner_location from Stage 4: {e}")

        # Store location context for geographic scope classification
        self.cv_owner_location = cv_owner_location if cv_owner_location and cv_owner_location.get('inference_success') else None
        if self.cv_owner_location and self.verbose:
            metro = self.cv_owner_location.get('metro_area', '')
            primary = self.cv_owner_location.get('primary_location', {})
            if primary:
                print(f"CV Owner Location: {primary.get('city', '')}, {primary.get('state', '')} (metro: {metro})")

        # Try to find original document if not provided. Local-dev fallback
        # only -- the server always passes original_doc_path, and SAMPLE_CV_DIR
        # doesn't exist in the deployed image. Anchored on the module-relative
        # SAMPLE_CV_DIR constant plus the process CWD, instead of a stack of
        # brittle '..'/.parent chains that broke silently on any restructure.
        if not original_doc_path:
            possible_paths = [
                SAMPLE_CV_DIR / f"{document_uid}.docx",
                SAMPLE_CV_DIR / f"{document_uid}.doc",
                Path('data/sample_cvs/word') / f"{document_uid}.docx",  # relative to CWD
            ]
            for path in possible_paths:
                if path.exists():
                    original_doc_path = str(path.resolve())
                    if self.verbose:
                        print(f"Found original document: {original_doc_path}")
                    break
            else:
                if self.verbose:
                    print(f"No original document found for {document_uid} in "
                          f"{SAMPLE_CV_DIR} or ./data/sample_cvs/word")

        # Load Stage 4.5 research summary if available
        research_summary_data = None
        if research_summary_path and os.path.exists(research_summary_path):
            with open(research_summary_path, 'r') as f:
                research_summary_data = json.load(f)
            if self.verbose:
                print(f"Loaded research summary from Stage 4.5: {research_summary_path}")
        else:
            # Try to find it automatically
            input_dir = Path(input_path).parent.parent
            auto_summary_path = input_dir / "stage_4_5_research_summary" / f"{document_uid}_research_summary.json"
            if auto_summary_path.exists():
                with open(auto_summary_path, 'r') as f:
                    research_summary_data = json.load(f)
                if self.verbose:
                    print(f"Auto-loaded research summary from: {auto_summary_path}")

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"Stage 6: WCM Template Generation - {document_uid}")
            print(f"{'='*60}")
            print(f"Total entries: {len(entries)}")

        # Group entries by taxonomy code, applying mismatch corrections
        entries_by_code = defaultdict(list)
        mismatch_corrections = 0
        for entry in entries:
            code = normalize_retired_code(entry)
            code = self._correct_mismatch_if_needed(entry, code)
            if code != entry.get('taxonomy_code', 'T'):
                mismatch_corrections += 1
            entries_by_code[code].append(entry)

        if self.verbose:
            print(f"Taxonomy codes found: {sorted(entries_by_code.keys())}")
            if mismatch_corrections > 0:
                print(f"  Hierarchy mismatch corrections applied: {mismatch_corrections}")

        # Deduplicate within each code group.
        # Position/training codes (D1, D2, D3, C, B1) represent career progression
        # stages that share most words but differ in rank — use date-aware dedup
        # that only merges entries whose date ranges overlap or match.
        DATE_AWARE_DEDUP_CODES = {'D1', 'D2', 'D3', 'C', 'B1'}
        # Snapshot the pre-dedup groups for the #221 recovery pass: dedup keeps
        # the longer near-duplicate, which can eat a unique record line fused
        # into the dropped entry. Recovery re-verifies every line against the
        # rendered document, so scanning dropped entries is safe — content the
        # surviving duplicate rendered is seen as rendered.
        pre_dedup_entries_by_code = {code: list(group)
                                     for code, group in entries_by_code.items()}
        total_deduped = 0
        dedup_decisions: List[Dict] = []
        for code in list(entries_by_code.keys()):
            before = len(entries_by_code[code])
            date_aware = code in DATE_AWARE_DEDUP_CODES
            group_decisions: List[Dict] = []
            entries_by_code[code] = deduplicate_entries(
                entries_by_code[code], verbose=self.verbose,
                require_date_overlap=date_aware,
                decisions=group_decisions)
            for decision in group_decisions:
                decision["code"] = code
            dedup_decisions.extend(group_decisions)
            removed = before - len(entries_by_code[code])
            if removed > 0:
                total_deduped += removed
        if self.verbose and total_deduped > 0:
            print(f"  Deduplicated: {total_deduped} near-duplicate entries removed")

        # Load template
        self.doc = Document(self.template_path)

        # Flatten all entries for fallback searches
        all_entries = [entry for entries in entries_by_code.values() for entry in entries]

        # Fill each section
        self._fill_personal_data(entries_by_code.get('A', []), cv_owner, document_uid, all_entries, original_doc_path)
        self._fill_researcher_profiles(entries_by_code.get('S0', []))  # S0 section for ORCID, etc.
        self._fill_education(entries_by_code.get('B1', []))  # B1 = Academic Degrees only
        self._fill_other_education(entries_by_code.get('B2', []))  # B2 = Other Educational Experiences
        self._fill_postdoc_training(entries_by_code, all_entries)
        self._fill_positions(entries_by_code)
        self._fill_licensure(entries_by_code.get('F1', []))  # F1 = Licensure
        self._fill_board_certification(entries_by_code.get('F2', []))  # F2 = Board Certification
        self._fill_honors(entries_by_code.get('H', []))  # H = Honors and Awards
        self._fill_memberships(entries_by_code.get('I', []))  # I = Professional Memberships
        self._fill_teaching(entries_by_code)  # K1-K5 = Teaching Activities
        research_summary_rendered = self._fill_research_summary(research_summary_data)  # Stage 4.5 output
        self._fill_research_support(entries_by_code, cv_owner, document_uid)
        # NOTE: Clinical trials now handled by _fill_research_support via M2A/M2B/M2C codes
        self._fill_patents(entries_by_code.get('M2D', []))
        self._fill_mentoring(entries_by_code)
        self._fill_clinical_practice(entries_by_code)  # L1, L2, L3 = Clinical Practice, Innovation, Leadership
        self._fill_leadership(entries_by_code.get('O', []))  # O = Institutional Leadership
        self._fill_administrative_activities(entries_by_code.get('P', []))  # P = Administrative Committees
        self._fill_service(entries_by_code)  # Q1-Q4D = Service Activities
        self._fill_presentations(entries_by_code.get('R', []))  # R = Invited Presentations
        self._fill_bibliography(entries_by_code, cv_owner, document_uid)

        # Fill passthrough sections (Employment Status, Institutional Affiliation)
        # These are copied directly from source CV when the source format matches WCM
        self._fill_passthrough_sections(all_entries)

        # Add appendix for ALL unmapped content
        # Codes that are mapped to specific sections in the WCM template:
        mapped_codes = {
            'A',   # Personal Data (email, phone - but unused A entries go to appendix)
            'S0',  # Researcher Profiles section
            'B1',  # Education - Academic Degrees
            'B2',  # Education - Other Educational Experiences
            'C', 'C1', 'C2',  # Postdoctoral Training (C is generic, C1/C2 are sub-types)
            'D1', 'D2', 'D3',  # Professional Positions
            'F1', 'F2',  # Licensure and Board Certification
            'H',   # Honors and Awards
            'I',   # Professional Memberships
            'K1', 'K2', 'K3', 'K4', 'K5',  # Teaching Activities
            'L1', 'L2', 'L3',  # Clinical Practice, Innovation, Leadership
            'M1',  # Research Summary (from Stage 4.5)
            'M2A', 'M2B', 'M2C',  # Research Support (grants and clinical trials)
            'M2D',  # Patents & Innovations
            'N3A', 'N3B',  # Mentoring (current/past mentees)
            'O',   # Institutional Leadership
            'P',   # Administrative Committees
            'Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D',  # Service Activities
            'R',   # Invited Presentations
            'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9',  # Bibliography
            # NOTE: T is intentionally NOT here - T entries go to Appendix
        }

        # M1 (Research Activities) entries are consumed by the Stage 4.5 research
        # summary. When that summary did NOT render (no Stage 4.5 output, empty
        # summary, or the template lacks a RESEARCH ACTIVITIES header), the M1
        # entries would otherwise render nowhere AND be excluded from the appendix
        # by being 'mapped' — a silent content loss (#317, C0ZGFW). Route them to
        # the appendix safety net instead. No-op when the summary rendered.
        if not research_summary_rendered:
            mapped_codes.discard('M1')

        unmapped_entries = []

        # Collect ALL entries not in mapped codes
        for code, entries in entries_by_code.items():
            if code not in mapped_codes:
                unmapped_entries.extend(entries)

        # Note: A entries are all used in Personal Data section, no need to add extras to appendix
        # The Personal Data section handles name, address, email, phone, etc.

        if unmapped_entries:
            self._fill_appendix(unmapped_entries)

        # Route content-overflow entries as tracked-change bullets
        self._route_overflow_entries()

        # Reconsider appendix entries - reclassify segments to appropriate sections
        self._reconsider_appendix_entries()

        # Post-render safety net: re-emit record lines the structured render
        # dropped (#221). Runs after the overflow/reconsider passes so their
        # inserts count as rendered, and before comment finalization and
        # instruction-box removal (anchor lookups are text-based). Scans the
        # PRE-dedup entries so records fused into a deduped-away entry are
        # still checked.
        self._recover_unrendered_records(pre_dedup_entries_by_code)

        # Finalize comments (add to comments.xml)
        self._finalize_comments()

        # Apply vertical middle alignment to ALL table cells
        self._apply_vertical_alignment_to_all_tables()

        # Apply table styling (header background color, borders)
        self._apply_table_styling_to_all_tables()

        # Drop the WCM template's gray instruction box last, after all
        # content-search-based filling is done, so table removal can't shift
        # anything the fill logic relied on.
        if self.strip_template_instructions:
            self._remove_instruction_box()

        # Determine output path
        if output_path is None:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = str(OUTPUT_DIR / f"{document_uid}_wcm.docx")

        # Save
        self.doc.save(output_path)

        # Run post-generation validation to catch common issues
        validation_issues = self._validate_output()
        if validation_issues:
            print(f"\n{'!'*60}")
            print("VALIDATION WARNINGS")
            print(f"{'!'*60}")
            for issue in validation_issues:
                print(f"  ⚠ {issue['message']}")
            print(f"{'!'*60}")

        # Persist the self-check warnings and dedup decision trail next to
        # the docx so the run doctor can re-emit them (#227/#228) — until now
        # they only ever reached the pod log. Written even when empty, so the
        # doctor can tell a clean run from a pre-sidecar build. Fail-soft: a
        # sidecar failure must never fail the render.
        try:
            report_path = Path(output_path).with_name(
                f"{document_uid}_render_warnings.json")
            report_path.write_text(json.dumps({
                "document_uid": document_uid,
                "warnings": validation_issues,
                "dedup_decisions": dedup_decisions,
            }, indent=2))
        except Exception as exc:
            print(f"  ⚠ could not write render-warnings sidecar: {exc}")

        if self.verbose:
            print(f"\n{'='*60}")
            print("Generation Summary")
            print(f"{'='*60}")
            print(f"  Entries inserted: {self.stats['entries_inserted']}")
            print(f"  Tables populated: {self.stats['tables_populated']}")
            print(f"  Target names bolded: {self.stats['target_names_bolded']}")
            print(f"  Track changes added: {self.stats['track_changes_added']}")
            print(f"  Comments added: {self.stats['comments_added']}")
            if self.stats.get('overflow_bullets_added', 0) > 0:
                print(f"  Overflow bullets added: {self.stats['overflow_bullets_added']}")
            if self.stats.get('overflow_to_appendix', 0) > 0:
                print(f"  Overflow to appendix: {self.stats['overflow_to_appendix']}")
            if self.stats.get('appendix_segments_reconsidered', 0) > 0:
                print(f"  Appendix segments reconsidered: {self.stats['appendix_segments_reconsidered']}")
            print(f"\nSaved to: {output_path}")

        return output_path

    def _strip_markdown_for_word(self, text: str, preserve_newlines: bool = False) -> str:
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

    def _set_font(self, run, name='Arial', size=11, bold=False, italic=False):
        """Set font properties for a run - always 11pt Arial unless specified."""
        run.font.name = name
        run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic
        # Ensure font name applies to complex script and East Asian text as well
        r = run._element
        rPr = r.get_or_add_rPr()
        rFonts = rPr.find(qn('w:rFonts'))
        if rFonts is None:
            rFonts = OxmlElement('w:rFonts')
            rPr.insert(0, rFonts)
        rFonts.set(qn('w:ascii'), name)
        rFonts.set(qn('w:hAnsi'), name)
        rFonts.set(qn('w:cs'), name)

    def _set_cell_text(self, cell, text: str, bold: bool = False):
        """Set cell text with proper Arial 11pt formatting."""
        cell.text = ""  # Clear existing
        if cell.paragraphs:
            para = cell.paragraphs[0]
            run = para.add_run(str(text) if text else "")
            self._set_font(run, bold=bold)

    def _set_table_border(self, table: Table, color: str = '808080', size: int = 4):
        """Set table borders to 1px (4 eighths of a point), 50% gray."""
        tbl = table._tbl
        # CT_Tbl.tblPr is a OneAndOnlyOne descriptor: it returns the element or
        # raises InvalidXmlError -- it never returns None. (ECMA-376 makes
        # w:tblPr required on w:tbl, so a valid document always has it.) The
        # old `if tbl.tblPr is not None else OxmlElement(...)` ternary and its
        # trailing `if tbl.tblPr is None: tbl.insert(0, tblPr)` were therefore
        # both unreachable. Note there is no get_or_add_tblPr() to reach for --
        # OneAndOnlyOne generates no such accessor.
        tblPr = tbl.tblPr

        tblBorders = OxmlElement('w:tblBorders')
        for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
            border = OxmlElement(f'w:{border_name}')
            border.set(qn('w:val'), 'single')
            border.set(qn('w:sz'), str(size))  # 4 = 0.5pt, 8 = 1pt
            border.set(qn('w:color'), color)
            tblBorders.append(border)

        # Remove existing borders and add new ones
        existing = tblPr.find(qn('w:tblBorders'))
        if existing is not None:
            tblPr.remove(existing)
        tblPr.append(tblBorders)

    def _set_cell_vertical_alignment(self, cell, align='center'):
        """Set cell vertical alignment to center (middle)."""
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        vAlign = OxmlElement('w:vAlign')
        vAlign.set(qn('w:val'), align)
        # Remove existing vAlign
        existing = tcPr.find(qn('w:vAlign'))
        if existing is not None:
            tcPr.remove(existing)
        tcPr.append(vAlign)

    def _apply_vertical_alignment_to_all_tables(self):
        """Apply vertical middle alignment and paragraph spacing to ALL table cells.

        This ensures consistency across:
        - Pre-existing template table cells
        - Newly added table cells

        Sets:
        - Vertical alignment: center (middle)
        - Paragraph spacing: 4pt before and 4pt after
        """
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    self._set_cell_vertical_alignment(cell, 'center')
                    # Set paragraph spacing for all paragraphs in cell
                    for para in cell.paragraphs:
                        self._set_paragraph_spacing(para, before_pt=4, after_pt=4)

    def _apply_table_styling_to_all_tables(self):
        """Apply standard WCM table styling to ALL tables.

        Styling applied:
        - Header row (first row): Light gray background ("White, Background 1, Darker 25%" = D9D9D9)
        - All cells: Light gray borders (D9D9D9)
        """
        # Gray color for header background and borders (D9D9D9 = White, Background 1, Darker 25%)
        gray_color = "D9D9D9"

        for table in self.doc.tables:
            if not table.rows:
                continue

            # Apply header row background color (first row)
            header_row = table.rows[0]
            for cell in header_row.cells:
                self._set_cell_background(cell, gray_color)

            # Apply borders to all cells
            for row in table.rows:
                for cell in row.cells:
                    self._set_cell_borders(cell, gray_color)

    def _set_paragraph_spacing(self, para, before_pt: int = 4, after_pt: int = 4):
        """Set paragraph spacing before and after.

        Args:
            para: Paragraph to modify
            before_pt: Space before in points
            after_pt: Space after in points
        """
        pPr = para._p.get_or_add_pPr()

        # Remove existing spacing element if present
        existing = pPr.find(qn('w:spacing'))
        if existing is not None:
            pPr.remove(existing)

        # Create new spacing element with before and after
        spacing = OxmlElement('w:spacing')
        spacing.set(qn('w:before'), str(before_pt * 20))  # Convert pt to twips (1pt = 20 twips)
        spacing.set(qn('w:after'), str(after_pt * 20))
        pPr.append(spacing)

    def _format_currency(self, value) -> str:
        """Format a value as US currency ($).

        Args:
            value: Number, string with digits, or empty value

        Returns:
            Formatted currency string (e.g., "$14,876") or empty string
        """
        if not value:
            return ''

        # Convert to string and extract numeric portion
        value_str = str(value).strip()

        # If already formatted with $, just return it
        if value_str.startswith('$'):
            return value_str

        # Remove any existing currency symbols, commas, and whitespace
        cleaned = re.sub(r'[$,\s]', '', value_str)

        # Try to extract a number
        try:
            # Handle cases like "14876" or "14876.00"
            num = float(cleaned)
            # Format with commas and $ symbol, no decimal places for whole numbers
            if num == int(num):
                return f"${int(num):,}"
            else:
                return f"${num:,.2f}"
        except ValueError:
            # If we can't parse it, return the original value
            return value_str

    def _classify_geographic_scope(self, entry: Dict) -> str:
        """Classify an entry's geographic scope as Regional, National, or International.

        Uses LLM (gpt-5.1) to intelligently determine if an activity is in the same
        metropolitan area as the CV owner's institution(s), leveraging the model's
        geographic knowledge.

        Args:
            entry: Entry dict with text and extracted_fields

        Returns:
            'Regional', 'National', or 'International'
        """
        if not self.cv_owner_location:
            return 'National'  # Default if no location context

        # Extract activity location/organization from entry
        fields = entry.get('extracted_fields', {}) or {}
        entry_location = fields.get('location', '') or fields.get('city', '') or ''
        entry_org = fields.get('organization', '') or fields.get('institution', '') or ''
        entry_text = entry.get('text', '')

        # Build a location string for the activity
        activity_location = entry_org or entry_location or entry_text[:200]
        if not activity_location.strip():
            return 'National'  # Can't classify without location info

        # Get CV owner's institutions
        owner_institutions = []
        primary = self.cv_owner_location.get('primary_location', {})
        if primary.get('institution'):
            inst = primary.get('institution')
            city = primary.get('city', '')
            state = primary.get('state', '')
            owner_institutions.append(f"{inst}, {city}, {state}" if city else inst)

        # Add all affiliations
        for loc in self.cv_owner_location.get('locations', []):
            if loc.get('institution'):
                inst = loc.get('institution')
                city = loc.get('city', '')
                state = loc.get('state', '')
                loc_str = f"{inst}, {city}, {state}" if city else inst
                if loc_str not in owner_institutions:
                    owner_institutions.append(loc_str)

        metro_area = self.cv_owner_location.get('metro_area', '')

        if not owner_institutions:
            return 'National'

        # Create cache key to avoid repeated LLM calls for same location
        cache_key = f"{activity_location[:100]}|{','.join(owner_institutions[:2])}"
        if cache_key in self._geographic_scope_cache:
            return self._geographic_scope_cache[cache_key]

        # Use LLM to classify
        try:
            prompt = f"""Classify the geographic scope of this academic activity relative to the CV owner's institution(s).

**CV Owner's Institution(s)**: {'; '.join(owner_institutions)}
**CV Owner's Metro Area**: {metro_area or 'Unknown'}

**Activity Location/Organization**: {activity_location}

**Classification Rules**:
- **Regional**: Activity is in the SAME metropolitan area as CV owner's institution
  - Examples: If owner is at Weill Cornell (NYC), then Columbia, NYU, Mount Sinai, Montefiore are Regional
  - Same city or nearby suburbs count as Regional
- **National**: Activity is in the SAME COUNTRY but DIFFERENT metropolitan area
  - Examples: If owner is in NYC, then Johns Hopkins (Baltimore), Stanford (SF), Mayo (Minnesota) are National
- **International**: Activity is in a DIFFERENT COUNTRY
  - Examples: Oxford (UK), Karolinska (Sweden), University of Toronto (Canada) are International

Return ONLY a JSON object: {{"scope": "Regional" | "National" | "International"}}"""

            llm_result = call_llm(
                stage="stage_6",
                messages=[
                    {"role": "system", "content": "You are a geographic classification system. Use your knowledge of institution locations to classify scope. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )

            result = json.loads(llm_result["content"])
            scope = result.get('scope', 'National')

            # Validate response
            if scope not in ('Regional', 'National', 'International'):
                scope = 'National'

            # Cache the result
            self._geographic_scope_cache[cache_key] = scope

            return scope

        except Exception as e:
            if self.verbose:
                print(f"    ⚠ Geographic classification error: {e}")
            return 'National'  # Default on error

    def _get_cv_owner_name(self, cv_owner: Dict = None, document_uid: str = '') -> str:
        """Extract the CV owner's full name for auto-filling PI fields.

        Args:
            cv_owner: Dict with keys like 'last_name', 'first_name', etc.
            document_uid: Document UID like "2015_Wende" to extract name from

        Returns:
            Full name string (e.g., "Adam Wende") or last name if first not available
        """
        if cv_owner:
            first = cv_owner.get('first_name', '')
            last = cv_owner.get('last_name', '')
            if first and last:
                return f"{first} {last}"
            elif last:
                return last

        # Fall back to extracting from document_uid
        if document_uid:
            # Handle patterns like "2015_Wende" or "2003_Albrechtjs_Cv"
            parts = document_uid.split('_')
            if len(parts) >= 2:
                # Second part is usually the name
                name_part = parts[1]
                # Remove common suffixes
                name_part = re.sub(r'(js|cv|CV|Cv)$', '', name_part, flags=re.IGNORECASE)
                # Capitalize properly
                return name_part.capitalize()

        return ''

    def _add_paragraph_spacing_before(self, para, space_pt: int = 10):
        """Add spacing before a paragraph (for table margins)."""
        pPr = para._p.get_or_add_pPr()
        spacing = OxmlElement('w:spacing')
        spacing.set(qn('w:before'), str(space_pt * 20))  # Convert pt to twips
        existing = pPr.find(qn('w:spacing'))
        if existing is not None:
            pPr.remove(existing)
        pPr.append(spacing)

    def _insert_multiline_as_bullets(self, insert_idx: int, text: str, entry: Dict = None,
                                       add_blank_before: bool = False) -> int:
        """Insert multi-line text as separate bullets, one per line.

        This is the STANDARD method for inserting bulleted content. It respects
        the original document's line structure - if the source had multiple lines,
        each becomes its own bullet.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content (may contain newlines)
            entry: Optional entry dict for adding comments (attached to first bullet only)
            add_blank_before: If True, add a blank line before the first entry

        Returns:
            Number of bullets inserted
        """
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            return 0

        # Insert in reverse order since we're inserting before insert_idx
        for j, line_text in enumerate(reversed(lines)):
            is_last = (j == len(lines) - 1)  # Last in reversed = first in original
            self._insert_bulleted_entry(
                insert_idx, line_text,
                entry if is_last else None,  # Attach entry/comments to first bullet
                add_blank_before=add_blank_before and is_last, list_level=0
            )

        return len(lines)

    def _insert_bulleted_entry(self, insert_idx: int, text: str, entry: Dict = None,
                                add_blank_before: bool = False,
                                list_level: int = None) -> Optional[Paragraph]:
        """Insert a SINGLE bulleted entry paragraph with a bullet character prefix.

        NOTE: For multi-line content, use _insert_multiline_as_bullets() instead.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content for the entry (should be single line)
            entry: Optional entry dict for adding comments
            add_blank_before: If True, add a blank line before this entry
            list_level: When given, emit a real Word list paragraph at this
                ilvl via _apply_list_bullet instead of prefixing a literal "• "
                glyph (#474). Left at None by the three non-K call sites, whose
                1,765 corpus-wide glyphs are #483.

        Returns:
            The created paragraph, or None if insertion failed
        """
        if insert_idx >= len(self.doc.paragraphs):
            return None

        # Create the bulleted entry paragraph first
        entry_para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")

        # Add blank line before if requested (insert before the entry we just created)
        if add_blank_before:
            # Insert blank before entry_para (which pushes entry down, so blank is above entry)
            entry_para.insert_paragraph_before("")

        body = _clean_inline_tabs(_strip_taxonomy_code(text))
        # Without list_level, a simple bullet character prefix: it avoids Word
        # numbering system issues across different templates, at the cost of not
        # being a list item to Word's outline, to accessibility tooling, or to
        # anything re-parsing the output.
        run = entry_para.add_run(body if list_level is not None else f"• {body}")
        self._set_font(run)
        if list_level is not None:
            self._apply_list_bullet(entry_para, level=list_level)

        if entry:
            self._add_entry_comments(entry_para, entry)

        self.stats['entries_inserted'] += 1
        return entry_para

    def _apply_bullet_formatting(self, para: Paragraph) -> None:
        """Apply bullet list formatting to a paragraph using XML.

        This is used when the 'List Bullet' style is not available in the template.
        Creates a proper Word bullet list with hanging indent.
        """
        # Get or create paragraph properties
        pPr = para._p.get_or_add_pPr()

        # Create numbering properties for bullet
        numPr = OxmlElement('w:numPr')

        # Use abstract numbering ID 0 (typically bullets in Word)
        ilvl = OxmlElement('w:ilvl')
        ilvl.set(qn('w:val'), '0')
        numPr.append(ilvl)

        numId = OxmlElement('w:numId')
        numId.set(qn('w:val'), '1')  # numId 1 is typically bullet list
        numPr.append(numId)

        # Insert numbering properties at beginning of pPr
        pPr.insert(0, numPr)

        # Set hanging indent for proper bullet alignment (0.25" indent, 0.25" hanging)
        ind = OxmlElement('w:ind')
        ind.set(qn('w:left'), '720')      # 0.5 inch in twips (1440 twips = 1 inch)
        ind.set(qn('w:hanging'), '360')   # 0.25 inch hanging indent

        # Remove existing indentation if any
        existing_ind = pPr.find(qn('w:ind'))
        if existing_ind is not None:
            pPr.remove(existing_ind)
        pPr.append(ind)

    def _apply_list_bullet(self, para: Paragraph, level: int = 0) -> None:
        """Apply Word native list bullet formatting using the WCM template's numbering.

        Uses numId=1 (abstractNum=4) from the WCM template which defines:
          ilvl=0: filled circle (bullet), left=1080, hang=360
          ilvl=1: open circle (o),        left=1800, hang=360
          ilvl=2: filled square (bullet),  left=2520, hang=360

        This produces the closed circle -> open circle -> closed square hierarchy
        with proper margin indentation handled entirely by Word.
        """
        # Set the List Paragraph style
        try:
            para.style = self.doc.styles['List Paragraph']
        except KeyError:
            pass  # Style not found — numbering alone will still work

        # Add numPr to paragraph properties
        pPr = para._p.get_or_add_pPr()

        # Remove any existing numPr
        existing_numPr = pPr.find(qn('w:numPr'))
        if existing_numPr is not None:
            pPr.remove(existing_numPr)

        numPr = OxmlElement('w:numPr')
        ilvl = OxmlElement('w:ilvl')
        ilvl.set(qn('w:val'), str(level))
        numId = OxmlElement('w:numId')
        numId.set(qn('w:val'), '1')
        numPr.append(ilvl)
        numPr.append(numId)
        pPr.append(numPr)

    def _extract_year_from_text(self, text: str) -> Optional[str]:
        """Extract year from raw text as fallback when not in extracted_fields.

        Looks for patterns like:
        - "August 2021"
        - "December 2017"
        - "May 2015"
        - "(2021)"
        - "2019-2021"
        """
        if not text:
            return None

        # Pattern 1: Month Year (e.g., "August 2021", "December 2017")
        month_year = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text)
        if month_year:
            return month_year.group(2)

        # Pattern 2: Year in parentheses at end (e.g., "(2021)")
        paren_year = re.search(r'\((\d{4})\)\s*$', text)
        if paren_year:
            return paren_year.group(1)

        # Pattern 3: Year range - take the end year (e.g., "2019-2021")
        year_range = re.search(r'(\d{4})\s*[-–—]\s*(\d{4})', text)
        if year_range:
            return year_range.group(2)

        # Pattern 4: Single year in text
        single_year = re.search(r'\b(19\d{2}|20\d{2})\b', text)
        if single_year:
            return single_year.group(1)

        return None

    # Phrases a CV uses to mark a degree that has not yet been conferred.
    _IN_PROGRESS_DEGREE_MARKERS = (
        'expected', 'anticipated', 'in progress', 'in-progress', 'ongoing',
        'to be conferred', 'to be awarded', 'candidate', 'pending', 'present',
    )

    def _degree_is_in_progress(self, raw_text: str, year_awarded: str) -> bool:
        """Return True when a degree has not yet been conferred.

        Two general signals, neither tied to any specific CV:
        1. The source line carries an explicit "not yet awarded" marker
           ("expected", "anticipated", "in progress", "candidate", ...).
        2. The award year parses to a year later than the current (run) year, so
           it cannot already have been conferred.
        """
        text = (raw_text or '').lower()
        for marker in self._IN_PROGRESS_DEGREE_MARKERS:
            if marker in text:
                return True

        # Future award year => not yet conferred. year_awarded is already
        # normalized to a 4-digit year by format_date_for_section(..., 'H').
        match = re.search(r'(19|20)\d{2}', str(year_awarded))
        if match:
            try:
                if int(match.group(0)) > datetime.now().year:
                    return True
            except ValueError:
                pass
        return False

    def _is_from_enrichment(self, entry: Dict, field: str) -> bool:
        """Check if a field value came from enrichment rather than extraction."""
        enriched_fields = entry.get('enriched_fields', [])
        return field in enriched_fields

    def _deduplicate_repeated_content(self, text: str, separator: str = '|') -> str:
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

    def _is_table_header_entry(self, text: str, header_keywords: List[str], threshold: int = 2) -> bool:
        """Detect if an entry is actually a table header that was mistakenly extracted as data.

        Table headers are characterized by:
        - Multiple header-like words (e.g., "Name of award", "Date", "Organization")
        - Tab or pipe-separated columns
        - No substantive content (just column labels)

        Args:
            text: The entry text to check
            header_keywords: List of keywords that typically appear in headers for this section
            threshold: Minimum number of header keywords required to classify as header

        Returns:
            True if this appears to be a table header, False otherwise
        """
        if not text:
            return False

        # Normalize text for checking
        text_lower = text.lower().strip()

        # If text is very short, it might be header-like
        # But only if it matches header patterns
        if len(text_lower) < 100:
            # Count how many header keywords appear
            keyword_count = sum(1 for kw in header_keywords if kw.lower() in text_lower)

            # Check for common header patterns
            header_patterns = [
                r'\bname\s+of\s+',  # "Name of award", "Name of organization"
                r'\bdate\s*(awarded|received|of|issued)?\b',  # "Date awarded", "Date of issue"
                r'\b(organization|institution)\s*(name)?\b',  # "Organization", "Institution name"
                r'\btitle\b.*\b(institution|organization|dates?)\b',  # "Title | Institution | Dates"
                r'\bdates?\s*\(?[mdy/]+\)?',  # "Dates (mm/yy)"
            ]

            pattern_matches = sum(1 for p in header_patterns if re.search(p, text_lower))

            # If multiple header keywords AND pattern matches, likely a header
            if keyword_count >= threshold and pattern_matches >= 1:
                return True

            # Also check for tab/pipe-separated header-only content
            if ('\t' in text or '|' in text):
                parts = re.split(r'[\t|]', text_lower)
                # If all parts are short and most match header keywords, it's a header
                if all(len(p.strip()) < 30 for p in parts if p.strip()):
                    parts_matching = sum(1 for p in parts if any(kw in p for kw in header_keywords))
                    if parts_matching >= len(parts) * 0.5:
                        return True

        return False

    def _is_structural_label(self, entry: Dict) -> bool:
        """Check if an entry is a structural label from the source CV rather than actual content.

        Source CVs contain section headers, sub-headers, and structural labels
        (e.g., "CLINICAL PRACTICE ACTIVITIES", "Direct Teaching/Precepting/Supervision")
        that sometimes get extracted as entries. These should not appear as content
        in the WCM output — the WCM template provides its own structure.

        Checks:
        1. All-caps text longer than 3 characters (section headers)
        2. Entry text that exactly matches one of its own hierarchy labels
        """
        text = (entry.get('text', '') or '').strip()
        if not text:
            return True

        # All-caps text (section headers like "CLINICAL PRACTICE ACTIVITIES")
        if text == text.upper() and len(text) > 3 and not any(c.isdigit() for c in text):
            return True

        # Text that exactly matches one of its hierarchy labels
        hierarchy = entry.get('hierarchy', [])
        for label in hierarchy:
            if text.strip().lower() == label.strip().lower():
                return True

        return False

    def _clean_institution_field(self, institution: str) -> Tuple[str, str]:
        """Clean up institution field that may contain tab-separated values or embedded locations.

        Handles cases like:
        - "Weill Cornell Medical College\\tNew York Presbyterian Hospital\\tNew York, New York"
        - "Weill Cornell Medical College, New York, NY"

        Args:
            institution: Raw institution string from extraction

        Returns:
            Tuple of (cleaned_institution, extracted_location)
            - cleaned_institution: Institution name(s) properly formatted
            - extracted_location: City, State if found embedded in the string
        """
        if not institution:
            return '', ''

        # Common US state patterns (full names and abbreviations)
        us_states = {
            'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
            'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
            'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana',
            'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota',
            'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
            'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina',
            'North Dakota', 'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania',
            'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas',
            'Utah', 'Vermont', 'Virginia', 'Washington', 'West Virginia',
            'Wisconsin', 'Wyoming', 'District of Columbia'
        }
        state_abbrevs = {
            'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'ID',
            'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS',
            'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK',
            'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV',
            'WI', 'WY', 'DC'
        }
        # Common international locations
        international_locations = {
            'Doha, Qatar', 'Qatar', 'London, UK', 'London, England', 'Toronto, Canada',
            'Montreal, Canada', 'Paris, France', 'Berlin, Germany', 'Tokyo, Japan'
        }

        # Split on tabs first
        parts = [p.strip() for p in institution.split('\t') if p.strip()]

        institutions = []
        location = ''

        for part in parts:
            # Check if this part looks like a location (City, State pattern)
            is_location = False

            # Check for "City, State" pattern where State is a US state
            if ', ' in part:
                potential_parts = part.rsplit(', ', 1)
                if len(potential_parts) == 2:
                    potential_state = potential_parts[1].strip()
                    if potential_state in us_states or potential_state in state_abbrevs:
                        # This is a City, State - check if it's ONLY location or institution + location
                        potential_city = potential_parts[0].strip()
                        # If the "city" part contains institution keywords, it's probably "Institution, City, State"
                        inst_keywords = ['University', 'College', 'Hospital', 'Medical', 'Institute', 'Center', 'School']
                        if any(kw in potential_city for kw in inst_keywords):
                            # This is "Institution, City, State" - need to parse further
                            # Try to find where institution ends and city begins
                            # Look for comma before a city name
                            for state in us_states:
                                # Pattern: "..., CityName, StateName"
                                pattern = rf',\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*{re.escape(state)}$'
                                match = re.search(pattern, part)
                                if match:
                                    city = match.group(1)
                                    location = f"{city}, {state}"
                                    institutions.append(part[:match.start()].strip())
                                    is_location = True
                                    break
                            if not is_location:
                                # Couldn't parse, keep as institution
                                institutions.append(part)
                        else:
                            # This is just "City, State"
                            location = part
                            is_location = True

            # Check for international locations
            if not is_location:
                for intl_loc in international_locations:
                    if part == intl_loc or part.endswith(f', {intl_loc}'):
                        if part == intl_loc:
                            location = part
                            is_location = True
                        else:
                            # "Institution, Location"
                            institutions.append(part.replace(f', {intl_loc}', '').strip())
                            location = intl_loc
                            is_location = True
                        break

            if not is_location:
                institutions.append(part)

        # Deduplicate institutions (preserving order) before joining
        seen = set()
        unique_institutions = []
        for inst in institutions:
            key = inst.lower().strip()
            if key not in seen:
                seen.add(key)
                unique_institutions.append(inst)

        # Join multiple institutions with " / " separator
        cleaned_institution = ' / '.join(unique_institutions) if unique_institutions else ''

        return cleaned_institution, location

    def _get_institution_location(self, entry: Dict) -> Tuple[str, bool]:
        """Get formatted location string from institution enrichment data.

        Uses institution_enrichment from Stage 5b if available, otherwise falls back
        to extracted_fields.location.

        IMPORTANT: For known institutions (from config.yaml), we use the default location
        instead of enrichment when:
        1. The institution name contains a known institution (substring match)
        2. The original text doesn't have an explicit location different from the default

        This handles cases like "Weill Cornell Medical College, Doha, Qatar" where
        enrichment returns "Doha, Qatar" but we want "New York, NY" for the main campus.

        Args:
            entry: Entry dict with potential institution_enrichment

        Returns:
            Tuple of (location_string, is_from_enrichment)
            - location_string: Formatted location (e.g., "Columbus, OH") or empty string
            - is_from_enrichment: True if location came from enrichment (needs track change)
        """
        # Known institutions with default locations (should match config.yaml)
        known_institutions = {
            'weill cornell': 'New York, NY',
            'new york presbyterian': 'New York, NY',
            'newyork-presbyterian': 'New York, NY',
            'nyp': 'New York, NY',
            'memorial sloan': 'New York, NY',
            'hospital for special surgery': 'New York, NY',
        }

        # Check if this is a known institution that should use default location
        fields = entry.get('extracted_fields', {})
        institution_name = (fields.get('institution', '') or '').lower()
        original_text = (entry.get('text', '') or '').lower()

        # Check for known institution match (substring)
        default_location = None
        for known_inst, default_loc in known_institutions.items():
            if known_inst in institution_name or known_inst in original_text:
                default_location = default_loc
                break

        # If it's a known institution, check if original text has a different explicit location
        # (like "Doha, Qatar" or "Valhalla, NY") - if so, we should NOT override
        if default_location:
            # Check if original text contains a non-default location
            non_default_locations = ['doha', 'qatar', 'valhalla', 'ithaca', 'london', 'houston']
            has_explicit_non_default = any(loc in original_text for loc in non_default_locations)

            if not has_explicit_non_default:
                # Use default location for known institution
                return (default_location, False)  # False = not from enrichment (no track change needed)

        # Check for institution enrichment data (from Stage 5b)
        enrichment = entry.get('institution_enrichment', {})
        if enrichment:
            city = enrichment.get('city', '')
            state = enrichment.get('state', '')
            country_code = enrichment.get('country_code', '')

            if city and state:
                # For US, use state abbreviation
                if country_code == 'US':
                    state_abbrevs = {
                        'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
                        'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
                        'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
                        'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
                        'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
                        'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
                        'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
                        'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
                        'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
                        'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
                        'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
                        'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
                        'Wisconsin': 'WI', 'Wyoming': 'WY', 'District of Columbia': 'DC'
                    }
                    state_abbrev = state_abbrevs.get(state, state)
                    return (f"{city}, {state_abbrev}", True)  # True = from enrichment
                else:
                    # For non-US, include country
                    country = enrichment.get('country', '')
                    location = f"{city}, {country}" if country else f"{city}, {state}"
                    return (location, True)  # True = from enrichment
            elif city:
                return (city, True)  # True = from enrichment

        # Fall back to extracted_fields.location (not from enrichment)
        fields = entry.get('extracted_fields', {})
        return (fields.get('location', ''), False)  # False = not from enrichment

    def _get_cleaned_institution_name(self, entry: Dict) -> Optional[str]:
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

    def _recover_institution_from_nearby_entries(self, entry: Dict, all_entries: List[Dict]) -> str:
        """Recover institution name from nearby entries in the original CV.

        When a training entry (like Graduate Research Assistant) is missing institution,
        look at subsequent entries by element_idx that might contain the institution name.
        Common patterns: "University of X", "Department of X", institution names.
        """
        entry_end_idx = entry.get('element_idx_end', entry.get('element_idx_start', -1))
        # Ensure entry_end_idx is an integer (may be string from JSON)
        try:
            entry_end_idx = int(entry_end_idx)
        except (ValueError, TypeError):
            entry_end_idx = -1
        if entry_end_idx < 0:
            return ''

        # University/institution patterns
        institution_patterns = [
            'university of', 'college of', 'institute of', 'school of',
            'department of', 'center for', 'laboratory', 'hospital',
            ' – department', ' - department'
        ]

        # Look at entries within the next 5 element indices
        for other_entry in all_entries:
            other_start = other_entry.get('element_idx_start', -1)
            # Ensure other_start is an integer (may be string from JSON)
            try:
                other_start = int(other_start)
            except (ValueError, TypeError):
                continue

            # Check if this entry is immediately after our target (within 5 elements)
            if other_start > entry_end_idx and other_start <= entry_end_idx + 5:
                other_text = other_entry.get('text', '').strip()
                other_text_lower = other_text.lower()

                # Check if this looks like an institution
                for pattern in institution_patterns:
                    if pattern in other_text_lower:
                        # Return the institution text (clean it up)
                        return other_text

        return ''

    def _get_wcm_section_header(self, taxonomy_code: str) -> str:
        """Map taxonomy code to WCM subsection header text for precise routing.

        Returns the WCM subsection header where overflow content for this code
        should be inserted. Uses actual WCM template header text at the most
        specific level possible.
        """
        # Map specific taxonomy codes to WCM subsection headers
        # More specific codes first, then fall back to section-level
        subsection_map = {
            # Education / positions / licensure / honors — the exact header
            # strings the corresponding _fill_* methods search for, so a
            # recovered record lands next to the table its siblings rendered
            # into (#221).
            'B1': 'EDUCATION',
            'D1': 'Academic Appointments',
            'D2': 'Hospital Appointments',
            'D3': 'Other Professional Positions',
            'F1': 'Licensure',
            'H': 'HONORS',
            # Teaching (K codes) - map to specific teaching subsections
            'K1': 'Didactic Teaching',
            'K2': 'Clinical Teaching',
            'K3': 'Mentoring',  # or could go to MENTORING section
            'K4': 'Curriculum Development',
            'K5': 'Other Teaching',
            # Clinical (L codes) - map to clinical subsections
            'L1': 'Clinical Practice',
            'L2': 'Clinical Innovations',
            'L3': 'Clinical Leadership',
            # Research (M codes)
            'M': 'Research Activities',
            'M2A': 'Current Research Funding',
            'M2B': 'Past (Completed) Funding',
            'M2C': 'Pending Funding',
            # Mentoring (N codes)
            'N': 'Mentees',
            'N3A': 'Current Mentees:',
            'N3B': 'Past Mentees:',
            # Leadership (O codes)
            'O': 'INSTITUTIONAL LEADERSHIP',
            # Administrative (P codes)
            'P': 'INSTITUTIONAL ADMINISTRATIVE',
            # Service (Q codes)
            'Q1': 'Leadership in Extramural',
            'Q2': 'Service on Boards',
            'Q3': 'Grant Reviewing',
            'Q4': 'Editorial',
            # Presentations (R codes)
            'R': 'INVITATIONS TO SPEAK',
        }

        # Try exact code first, then prefix
        if taxonomy_code in subsection_map:
            return subsection_map[taxonomy_code]

        # Fall back to prefix (first character)
        prefix = taxonomy_code[0] if taxonomy_code else ''
        section_fallback = {
            'K': 'EDUCATIONAL CONTRIBUTIONS',
            'L': 'CLINICAL PRACTICE',
            'M': 'RESEARCH',
            'N': 'MENTORING',
            'O': 'INSTITUTIONAL LEADERSHIP',
            'P': 'INSTITUTIONAL ADMINISTRATIVE',
            'Q': 'EXTRAMURAL PROFESSIONAL',
            'R': 'INVITATIONS TO SPEAK',
            'S': 'BIBLIOGRAPHY',
        }
        return section_fallback.get(prefix, '')

    def _find_paragraph_with_text(self, search_text: str) -> Optional[int]:
        """Find paragraph index containing text."""
        search = search_text.lower()
        for i, para in enumerate(self.doc.paragraphs):
            if search in para.text.lower():
                return i
        return None

    def _find_header_paragraph(self, search_text: str) -> Optional[int]:
        """First paragraph containing search_text that is formatted like a
        section header (ALL-CAPS text or a bold run) — never plain body text.

        Content-insertion anchors must not match instruction prose: the
        MENTORING section's '**Optional: List publications...' paragraph
        contains 'bibliography' by substring and would swallow S-code
        recoveries mid-Mentoring if plain substring search were used.
        """
        search = search_text.lower()
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            if len(text) < 3 or search not in text.lower():
                continue
            if text.isupper() or (para.runs and para.runs[0].bold):
                return i
        return None

    def _find_section_end_paragraph_idx(self, section_para_idx: int) -> Optional[int]:
        """Find the paragraph index where the next major WCM section starts.

        Scans forward from section_para_idx looking for the next bold+underlined
        paragraph that looks like a major section header. Matches both letter-prefixed
        headers (e.g., "K. EDUCATIONAL") and plain uppercase headers (e.g., "RESEARCH",
        "MENTORING") used in the WCM template.

        Returns that index so callers can insert before it, or None if not found.
        """
        # Known major WCM section header keywords (uppercase, bold+underlined)
        major_section_keywords = {
            'PERSONAL DATA', 'EDUCATION', 'POSTDOCTORAL', 'PROFESSIONAL POSITIONS',
            'EMPLOYMENT STATUS', 'LICENSURE', 'INSTITUTIONAL/HOSPITAL',
            'HONORS', 'PROFESSIONAL ORGANIZATIONS', 'PERCENT EFFORT',
            'EDUCATIONAL CONTRIBUTIONS', 'CLINICAL PRACTICE', 'RESEARCH',
            'MENTORING', 'INSTITUTIONAL LEADERSHIP', 'INSTITUTIONAL ADMINISTRATIVE',
            'EXTRAMURAL PROFESSIONAL', 'INVITATIONS TO SPEAK', 'BIBLIOGRAPHY',
        }
        letter_pattern = re.compile(r'^[A-T]\.\s')
        paragraphs = self.doc.paragraphs

        for i in range(section_para_idx + 1, len(paragraphs)):
            para = paragraphs[i]
            text = para.text.strip()
            if not text:
                continue
            # Check if the first run is bold AND underlined (WCM section header style)
            runs = para.runs
            if not (runs and runs[0].bold and runs[0].underline):
                continue
            # Match letter-prefixed headers (e.g., "T. APPENDIX")
            if letter_pattern.match(text):
                return i
            # Match known major section keywords
            text_upper = text.upper()
            for keyword in major_section_keywords:
                if text_upper.startswith(keyword):
                    return i

        return None

    def _find_paragraph_exact(self, search_text: str) -> Optional[int]:
        """Find paragraph index with exact text match (stripped, case-insensitive)."""
        search = search_text.lower().strip()
        for i, para in enumerate(self.doc.paragraphs):
            para_text = para.text.strip().lower()
            if para_text == search:
                return i
        return None

    def _find_table_with_cell_text(self, search_text: str) -> Optional[Table]:
        """Find a table containing a cell with the given text."""
        search = search_text.lower()
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if search in cell.text.lower():
                        return table
        return None

    def _find_table_after_paragraph(self, para_idx: int) -> Optional[Table]:
        """Find the first table after a paragraph."""
        if para_idx >= len(self.doc.paragraphs):
            return None

        target_para = self.doc.paragraphs[para_idx]
        para_elem = target_para._element
        body_elements = list(self.doc.element.body)

        try:
            para_body_idx = body_elements.index(para_elem)
            for i in range(para_body_idx + 1, len(body_elements)):
                if body_elements[i].tag.endswith('tbl'):
                    return Table(body_elements[i], self.doc)
        except ValueError:
            pass

        return None

    def _remove_template_instruction_paragraphs(self, start_para_idx: int, max_paragraphs: int = 10):
        """Remove template instruction paragraphs after a section header.

        Template instructions are placeholder text that should be removed when
        actual content is inserted. Common patterns include:
        - "(funding agency – federal, foundation, industry; type of grant)*"
        - "Award Source" as a standalone label
        - Asterisk-prefixed instructions

        Args:
            start_para_idx: Paragraph index to start searching from
            max_paragraphs: Maximum number of paragraphs to check after the header
        """
        if start_para_idx >= len(self.doc.paragraphs):
            return

        # Template instruction patterns to detect and remove
        instruction_patterns = [
            r'^\s*\(.*?(?:funding|agency|federal|foundation|industry|type of grant).*?\)\*?\s*$',
            r'^\s*\*.*(?:instructions?|guidelines?|notes?|please|enter|specify).*$',
            r'^\s*Award\s+Source\s*:?\s*$',
            r'^\s*\[.*?\]\s*$',  # Bracketed placeholders like [Enter here]
            r'^\s*<.*?>\s*$',    # Angle bracket placeholders
        ]

        body = self.doc.element.body
        paragraphs_to_remove = []

        # Check paragraphs after the section header (but not the header itself)
        for i in range(start_para_idx + 1, min(start_para_idx + max_paragraphs, len(self.doc.paragraphs))):
            para = self.doc.paragraphs[i]
            para_text = para.text.strip()

            # Stop if we hit a new section header (usually bold or all caps)
            if para_text and (para_text.isupper() or para_text.endswith(':')):
                # Check if this might be a section header by looking at formatting
                if len(para_text) < 80 and not para_text.startswith('('):
                    break

            # Stop if we hit a table (we're past the instruction area)
            try:
                para_elem = para._element
                body_elements = list(body)
                para_body_idx = body_elements.index(para_elem)
                if para_body_idx + 1 < len(body_elements) and body_elements[para_body_idx + 1].tag.endswith('tbl'):
                    break
            except ValueError:
                pass

            # Check if paragraph is template boilerplate.
            # Primary check: shared, precision-biased detector (single source of
            # truth, generated from the WCM template). Fallback: the legacy
            # regexes below (for placeholder shapes the phrase list can't cover,
            # e.g. bracketed/angle placeholders).
            if para_text:
                matched = is_template_instruction(para_text)
                if not matched:
                    for pattern in instruction_patterns:
                        if re.match(pattern, para_text, re.IGNORECASE):
                            matched = True
                            break
                if matched:
                    paragraphs_to_remove.append(para._element)
                    if self.verbose:
                        print(f"    Removing template instruction: '{para_text[:60]}...'")

        # Remove identified instruction paragraphs
        for para_elem in paragraphs_to_remove:
            try:
                body.remove(para_elem)
            except ValueError:
                pass  # Already removed or not in body

    def _clear_table_data(self, table: Table, keep_header: bool = True):
        """Remove all data rows from table."""
        if not table:
            return
        start_row = 1 if keep_header else 0
        for i in range(len(table.rows) - 1, start_row - 1, -1):
            table._element.remove(table.rows[i]._element)

    def _style_table(self, table: Table):
        """Apply standard WCM table styling with header background and borders.

        Styling applied:
        - Header row: Light gray background ("White, Background 1, Darker 25%" = D9D9D9)
        - All cells: 1px light gray borders
        """
        if not table or not table.rows:
            return

        # Gray color for header background and borders (D9D9D9 = White, Background 1, Darker 25%)
        gray_color = "D9D9D9"

        # Style header row (first row) with background color
        if table.rows:
            header_row = table.rows[0]
            for cell in header_row.cells:
                self._set_cell_background(cell, gray_color)

        # Apply borders to all cells
        for row in table.rows:
            for cell in row.cells:
                self._set_cell_borders(cell, gray_color)

    def _set_cell_background(self, cell, color_hex: str):
        """Set cell background/shading color.

        Args:
            cell: The table cell
            color_hex: Hex color string (without #), e.g., "D9D9D9"
        """
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()

        # Remove existing shading if any
        existing_shd = tcPr.find(qn('w:shd'))
        if existing_shd is not None:
            tcPr.remove(existing_shd)

        # Add new shading element
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:color'), 'auto')
        shd.set(qn('w:fill'), color_hex)
        tcPr.append(shd)

    def _set_cell_borders(self, cell, color_hex: str, size: str = "4"):
        """Set cell borders.

        Args:
            cell: The table cell
            color_hex: Hex color string for border color
            size: Border size in eighths of a point (4 = 0.5pt, 8 = 1pt)
        """
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()

        # Remove existing borders if any
        existing_borders = tcPr.find(qn('w:tcBorders'))
        if existing_borders is not None:
            tcPr.remove(existing_borders)

        # Add new borders element
        tcBorders = OxmlElement('w:tcBorders')

        for border_name in ['top', 'left', 'bottom', 'right']:
            border = OxmlElement(f'w:{border_name}')
            border.set(qn('w:val'), 'single')
            border.set(qn('w:sz'), size)
            border.set(qn('w:space'), '0')
            border.set(qn('w:color'), color_hex)
            tcBorders.append(border)

        tcPr.append(tcBorders)

    def _add_table_row(self, table: Table, data: List[str], is_header: bool = False, entry: Dict = None):
        """Add a row to a table with proper formatting.

        Args:
            table: The table to add to
            data: List of cell values
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline
        """
        if not table:
            return
        row = table.add_row()
        first_cell_para = None
        for i, value in enumerate(data):
            if i < len(row.cells):
                cell = row.cells[i]
                cell.text = str(value) if value else ""
                # Set vertical alignment to center (middle)
                self._set_cell_vertical_alignment(cell, 'center')
                for para in cell.paragraphs:
                    if i == 0 and first_cell_para is None:
                        first_cell_para = para
                    for run in para.runs:
                        # Always 11pt Arial, bold for headers
                        self._set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1

    def _add_table_row_with_track_changes(self, table: Table, data: List[Tuple[str, bool]],
                                           is_header: bool = False, entry: Dict = None,
                                           track_change_author: str = "Institution Enrichment"):
        """Add a row to a table with track changes for enriched content.

        Args:
            table: The table to add to
            data: List of tuples (value, is_enriched) - if is_enriched=True, shows as track change
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline
            track_change_author: Author name for track change attribution
        """
        if not table:
            return
        row = table.add_row()
        first_cell_para = None

        for i, (value, is_enriched) in enumerate(data):
            if i < len(row.cells):
                cell = row.cells[i]
                # Set vertical alignment to center (middle)
                self._set_cell_vertical_alignment(cell, 'center')

                # Clear default paragraph
                if cell.paragraphs:
                    para = cell.paragraphs[0]
                    para.clear()

                    if i == 0:
                        first_cell_para = para

                    if is_enriched and value:
                        # Add as track change insertion
                        self._add_track_change_insertion(para, str(value), author=track_change_author)
                    else:
                        # Add as normal text
                        run = para.add_run(str(value) if value else "")
                        self._set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1

    def _add_table_row_with_mixed_content(self, table: Table, cell_contents: List[List[Tuple[str, bool, str]]],
                                           is_header: bool = False, entry: Dict = None):
        """Add a row to a table with mixed normal and track-change content per cell.

        Args:
            table: The table to add to
            cell_contents: List of cell content lists. Each cell content is a list of tuples:
                          [(text, is_enriched, author), ...]
                          e.g., [("University of South Carolina", False, ""), (", Columbia, SC", True, "Institution Enrichment")]
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline
        """
        if not table:
            return
        row = table.add_row()
        first_cell_para = None

        for i, content_parts in enumerate(cell_contents):
            if i < len(row.cells):
                cell = row.cells[i]
                # Set vertical alignment to center (middle)
                self._set_cell_vertical_alignment(cell, 'center')

                # Clear default paragraph
                if cell.paragraphs:
                    para = cell.paragraphs[0]
                    para.clear()

                    if i == 0:
                        first_cell_para = para

                    # Add each part of the content
                    for text, is_enriched, author in content_parts:
                        if not text:
                            continue
                        if is_enriched:
                            # Add as track change insertion
                            self._add_track_change_insertion(para, str(text), author=author or "Enrichment")
                        else:
                            # Add as normal text
                            run = para.add_run(str(text))
                            self._set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1

    def _format_table_headers(self, table: Table):
        """Make table header row bold and set proper formatting."""
        if not table or not table.rows:
            return
        header_row = table.rows[0]
        for cell in header_row.cells:
            self._set_cell_vertical_alignment(cell, 'center')
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run, size=11, bold=True)

    def _add_spacing_paragraph(self, after_element=None):
        """Add a blank paragraph for spacing between elements.

        Args:
            after_element: XML element to insert after. If None, appends to end of document.

        Returns:
            The created paragraph element, or None if failed.
        """
        # Create a new paragraph
        para = self.doc.add_paragraph()
        para.paragraph_format.space_before = Pt(6)
        para.paragraph_format.space_after = Pt(6)

        # If we need to insert after a specific element, move it
        if after_element is not None:
            body = self.doc.element.body
            body_elements = list(body)
            try:
                elem_idx = body_elements.index(after_element)
                # Remove from end and insert after the specified element
                body.remove(para._element)
                body.insert(elem_idx + 1, para._element)
            except (ValueError, IndexError):
                pass

        return para._element

    def _fill_personal_data(self, entries: List[Dict], cv_owner: Dict, document_uid: str,
                            all_entries: List[Dict] = None, original_doc_path: str = None):
        """Fill personal data section.

        Args:
            entries: A-coded entries specifically
            cv_owner: CV owner data if available
            document_uid: Document identifier
            all_entries: All entries from the CV (to search for email if not in A entries)
            original_doc_path: Path to original Word document (for fallback email extraction)
        """
        if self.verbose:
            print("\nFilling Personal Data...")

        # Get name from cv_owner if available
        # Priority: full_name_with_credentials > full_name
        # Note: last_name alone is not considered a complete name (will try fallback)
        name = None
        name_is_complete = False  # Track if we have a full name or just last name
        if cv_owner and cv_owner.get('full_name_with_credentials'):
            # Take only first line (may contain newline + date prepared)
            name = cv_owner['full_name_with_credentials'].split('\n')[0].strip()
            if name:
                name_is_complete = True
        elif cv_owner and cv_owner.get('full_name'):
            name = cv_owner['full_name']
            if name:
                name_is_complete = True

        # Try to extract from A entries (LinkedIn URL, etc.)
        if not name:
            for entry in entries:
                text = entry.get('text', '')
                # Look for LinkedIn URL pattern: linkedin.com/in/firstname-lastname
                linkedin_match = re.search(r'linkedin\.com/in/([a-z]+-[a-z]+)', text.lower())
                if linkedin_match:
                    parts = linkedin_match.group(1).split('-')
                    name = ' '.join(p.title() for p in parts)
                    name_is_complete = True
                    break

        # Collect different types of contact info from A entries
        # The original text contains labels like "Office address:", "Cell phone:", etc.
        work_email = None
        personal_email = None
        office_phone = None
        cell_phone = None
        home_phone = None
        office_address = None
        home_address = None

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            text = entry.get('text', '').lower()

            # Determine type based on original text labels
            extracted_phone = fields.get('phone')
            extracted_address = fields.get('address')
            extracted_email = (fields.get('email') or
                              fields.get('primary_email') or
                              fields.get('institutional_email') or
                              fields.get('work_email') or
                              fields.get('personal_email'))

            # Classify phone by type
            # Handle case where multiple phones are in one entry (e.g., "Mobile: X  Work: Y")
            if extracted_phone:
                # Check if text contains multiple phone type labels
                has_mobile = 'cell' in text or 'mobile' in text
                has_work = 'office' in text or 'work' in text
                has_home = 'home' in text

                if has_mobile and has_work and ';' in extracted_phone:
                    # Both types in same entry — try to split them
                    # Parse from original text to get correct assignment
                    phones = [p.strip() for p in extracted_phone.split(';')]
                    # Find phone numbers in order they appear in text
                    mobile_match = re.search(r'(?:cell|mobile)[^(]*(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})', text, re.IGNORECASE)
                    work_match = re.search(r'(?:work|office)[^(]*(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})', text, re.IGNORECASE)
                    if mobile_match and not cell_phone:
                        cell_phone = mobile_match.group(1)
                    if work_match and not office_phone:
                        office_phone = work_match.group(1)
                elif has_mobile:
                    if not cell_phone:
                        cell_phone = extracted_phone
                elif has_home:
                    if not home_phone:
                        home_phone = extracted_phone
                elif has_work or not office_phone:
                    if not office_phone:
                        office_phone = extracted_phone

            # Classify address by type
            if extracted_address:
                if 'home' in text:
                    if not home_address:
                        home_address = extracted_address
                elif 'office' in text or 'work' in text or 'business' in text or not office_address:
                    if not office_address:
                        office_address = extracted_address

            # Classify email by type
            if extracted_email:
                if 'personal' in text:
                    if not personal_email:
                        personal_email = extracted_email
                elif not work_email:
                    work_email = extracted_email

            # Also check entry text for email pattern (fallback)
            if not work_email and not personal_email:
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', entry.get('text', ''))
                if email_match:
                    work_email = email_match.group(0)

        # Legacy variable names for compatibility with rest of function
        email = work_email
        phone = office_phone
        address = office_address

        # If still no email, search all entries for email patterns
        if not email and all_entries:
            for entry in all_entries:
                text = entry.get('text', '')
                # Look for email pattern
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                if email_match:
                    email = email_match.group(0)
                    break

                # Also check extracted fields
                fields = entry.get('extracted_fields', {}) or {}
                email = fields.get('email') or fields.get('primary_email')
                if email:
                    break

        # Fallback: read personal data from original Word document
        # This handles cases where personal data is in tables (e.g., NAME: | Patricia Opresko)
        if original_doc_path and Path(original_doc_path).exists():
            try:
                original_doc = Document(original_doc_path)

                # First check tables (common format: label in col 0, value in col 1)
                for table in original_doc.tables[:3]:  # Only check first 3 tables
                    for row in table.rows:
                        if len(row.cells) >= 2:
                            label = row.cells[0].text.strip().lower()
                            value = row.cells[1].text.strip()

                            # Extract name if not yet found (or only have last name)
                            if not name_is_complete and 'name' in label and ':' in label:
                                if value and len(value) > 2:
                                    name = value
                                    name_is_complete = True
                                    if self.verbose:
                                        print(f"  Found name from table: {name}")

                            # Extract address if not yet found
                            # Note: Business address cells often contain embedded phone/fax/email
                            if not address and ('address' in label or 'business' in label) and ':' in label:
                                if value and len(value) > 5:
                                    # Parse the address block - it may contain Phone:, Fax:, E-mail: lines
                                    address_lines = []
                                    for line in value.split('\n'):
                                        line = line.strip()
                                        line_lower = line.lower()

                                        # Extract phone if embedded in address
                                        if not phone and ('phone:' in line_lower or 'phone\t' in line_lower):
                                            phone_match = re.search(r'(?:phone[:\s]+)(.+)', line, re.IGNORECASE)
                                            if phone_match:
                                                phone = phone_match.group(1).strip()
                                                if self.verbose:
                                                    print(f"  Found phone from address block: {phone}")
                                            continue

                                        # Extract email if embedded in address
                                        if not email and ('e-mail:' in line_lower or 'email:' in line_lower or 'e-mail\t' in line_lower):
                                            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
                                            if email_match:
                                                email = email_match.group(0)
                                                if self.verbose:
                                                    print(f"  Found email from address block: {email}")
                                            continue

                                        # Skip fax lines
                                        if 'fax:' in line_lower or 'fax\t' in line_lower:
                                            continue

                                        # Keep other lines as address
                                        if line:
                                            address_lines.append(line)

                                    address = '\n'.join(address_lines)
                                    if self.verbose:
                                        print(f"  Found address from table: {address[:50]}...")

                            # Extract phone if not yet found
                            if not phone and ('phone' in label or 'telephone' in label) and ':' in label:
                                if value and len(value) > 5:
                                    phone = value
                                    if self.verbose:
                                        print(f"  Found phone from table: {phone}")

                            # Extract email if not yet found
                            if not email and 'email' in label:
                                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', value)
                                if email_match:
                                    email = email_match.group(0)
                                    if self.verbose:
                                        print(f"  Found email from table: {email}")

                # Also check paragraphs for email (if not found in tables)
                if not email:
                    for para in original_doc.paragraphs[:20]:
                        text = para.text.strip()
                        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                        if email_match:
                            email = email_match.group(0)
                            if self.verbose:
                                print(f"  Found email from paragraph: {email}")
                            break
            except Exception as e:
                if self.verbose:
                    print(f"  Warning: Could not read original document for personal data: {e}")

        # Fallback to document_uid for name
        if not name:
            name = self._extract_name_from_uid(document_uid)

        # Find and fill Name field
        name_idx = self._find_paragraph_with_text("Name:")
        if name_idx is not None:
            para = self.doc.paragraphs[name_idx]
            para.clear()
            run = para.add_run(f"Name: {name}")
            self._set_font(run, bold=True)

        # Fill Date of preparation with today's date
        date_idx = self._find_paragraph_with_text("Date of preparation")
        if date_idx is not None:
            para = self.doc.paragraphs[date_idx]
            para.clear()
            today = datetime.now().strftime("%B %-d, %Y")  # e.g., "February 1, 2026"
            run = para.add_run(f"Date of preparation: {today}")
            self._set_font(run)

        # Fill email, phone, and address in the PERSONAL DATA table (Table 1)
        # Table 1 structure: Office address, Office telephone, Work email, Home address, Cell phone, Personal email
        personal_data_table = self._find_table_with_cell_text("Work email:")
        if personal_data_table is not None:
            for row in personal_data_table.rows:
                cell_text = row.cells[0].text.strip().lower()

                # Office address
                if office_address and 'office address' in cell_text:
                    formatted_address = office_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                    self._set_cell_text(row.cells[1], formatted_address)
                    self.stats['entries_inserted'] += 1

                # Office telephone
                if office_phone and 'office telephone' in cell_text:
                    self._set_cell_text(row.cells[1], office_phone)
                    self.stats['entries_inserted'] += 1

                # Work email
                if work_email and 'work email' in cell_text:
                    self._set_cell_text(row.cells[1], work_email)
                    self.stats['entries_inserted'] += 1

                # Home address
                if home_address and 'home address' in cell_text:
                    formatted_address = home_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                    self._set_cell_text(row.cells[1], formatted_address)
                    self.stats['entries_inserted'] += 1

                # Cell phone
                if cell_phone and 'cell phone' in cell_text:
                    self._set_cell_text(row.cells[1], cell_phone)
                    self.stats['entries_inserted'] += 1

                # Personal email
                if personal_email and 'personal email' in cell_text:
                    self._set_cell_text(row.cells[1], personal_email)
                    self.stats['entries_inserted'] += 1

    def _fill_researcher_profiles(self, s0_entries: List[Dict]):
        """Fill S0 researcher profile info (ORCID, Google Scholar, etc).

        Inserts right before "Peer-reviewed Research Articles" without a header.
        Content is formatted as a bulleted list.
        """
        if not s0_entries:
            return

        if self.verbose:
            print(f"Filling Researcher Profiles ({len(s0_entries)} entries)...")

        # Find "Peer-reviewed Research Articles" to insert directly above it
        peer_reviewed_idx = self._find_paragraph_with_text("Peer-reviewed Research Articles")
        if peer_reviewed_idx is None:
            # Fallback to BIBLIOGRAPHY
            peer_reviewed_idx = self._find_paragraph_with_text("BIBLIOGRAPHY")
        if peer_reviewed_idx is None:
            return

        # Insert a blank line before "Peer-reviewed" first
        self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")

        # Insert entries in REVERSE order so they appear in correct order
        # Format as bulleted list
        for entry in reversed(s0_entries):
            text = entry.get('text', '').strip()
            entry_para = self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")
            run = entry_para.add_run(_clean_inline_tabs(_strip_taxonomy_code(text)))
            self._set_font(run)
            self._apply_list_bullet(entry_para, level=0)
            self.stats['entries_inserted'] += 1

        # Add a blank line before the S0 content
        self.doc.paragraphs[peer_reviewed_idx].insert_paragraph_before("")

    def _extract_name_from_uid(self, uid: str) -> str:
        """Extract formatted name from document UID."""
        # Remove year prefix (e.g., "2015_Wende" -> "Wende")
        parts = uid.replace('CV_', '').split('_')

        # Filter out year
        parts = [p for p in parts if not p.isdigit() and len(p) > 2]

        if len(parts) >= 2:
            # Assume "First_Last" or "Last_First"
            return ' '.join(parts).title()
        elif parts:
            return parts[0].title()
        return uid

    def _extract_last_name_from_uid(self, uid: str) -> str:
        """Extract last name from document UID for author matching."""
        # Remove year prefix (e.g., "2015_Wende" -> "Wende")
        parts = uid.replace('CV_', '').split('_')

        # Filter out years and very short parts
        parts = [p for p in parts if not p.isdigit() and len(p) > 2]

        if parts:
            # Last part is typically the last name
            last_name = parts[-1]
            # Handle cases like "Albrechtjs" -> "Albrecht" (initials appended)
            if len(last_name) > 5:
                # Check if last 2-3 chars look like initials
                for suffix_len in [2, 3]:
                    suffix = last_name[-suffix_len:]
                    if suffix.islower() or suffix.isupper():
                        base = last_name[:-suffix_len]
                        if len(base) >= 3:
                            return base.title()
            return last_name.title()
        return ''

    def _fill_education(self, entries: List[Dict]):
        """Fill education table with track changes for enriched content.

        Track changes are used for:
        - City/state from institution enrichment (only the location part, not institution name)
        - Years extracted from raw text when not in extracted_fields
        """
        if self.verbose:
            print(f"Filling Education ({len(entries)} entries)...")

        edu_idx = self._find_paragraph_with_text("EDUCATION")
        if edu_idx is None:
            return

        table = self._find_table_after_paragraph(edu_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {})
            raw_text = entry.get('text', '')

            # Degree column (not enriched)
            degree = fields.get('degree', '')
            major = fields.get('major') or fields.get('field_of_study', '')
            if major and major not in degree:
                degree = f"{degree}, {major}" if degree else major

            # Institution - use cleaned_name from enrichment if available
            institution = fields.get('institution', '')
            if institution and institution.lower() == 'none':
                institution = ''
            cleaned = self._get_cleaned_institution_name(entry)
            if cleaned:
                institution = cleaned

            # Skip entries that have no degree AND no institution
            # These are likely training items that don't fit the education table format
            if not degree and not institution:
                continue

            # Location from enrichment
            location, location_is_enriched = self._get_institution_location(entry)

            # Dates - format according to B1 requirements (mm/yyyy-mm/yyyy)
            # Field extraction may use three different structures:
            #   1. Flat: 'dates_attended_start_date' / 'dates_attended_end_date'
            #   2. Nested dict: 'dates_attended': {'start_date': '...', 'end_date': '...'}
            #   3. Generic: 'start_date' / 'end_date'
            start = fields.get('dates_attended_start_date', '') or fields.get('start_date', '')
            end = fields.get('dates_attended_end_date', '') or fields.get('end_date', '')

            # Check for nested dict structure
            if not start and not end:
                dates_attended = fields.get('dates_attended', {})
                if isinstance(dates_attended, dict):
                    start = dates_attended.get('start_date', '') or ''
                    end = dates_attended.get('end_date', '') or ''
            if start or end:
                dates = format_date_range(start, end, 'B1')
            else:
                dates = ''

            # Year awarded - try to extract from raw text if missing
            year_awarded = fields.get('year_awarded') or fields.get('year') or end or ''
            year_is_enriched = False

            # If year_awarded is still empty, try to extract from raw text
            if not year_awarded and raw_text:
                extracted_year = self._extract_year_from_text(raw_text)
                if extracted_year:
                    year_awarded = extracted_year
                    year_is_enriched = True  # Mark as enriched since we extracted it

            # Format year_awarded - B1 uses mm/yyyy but year awarded column is just yyyy
            if year_awarded:
                year_awarded = format_date_for_section(year_awarded, 'H')  # H uses yyyy format

            # A degree that is still in progress ("expected May 2026", a future
            # award year, etc.) must NOT be presented as a conferred year. Mark it
            # as anticipated so the reader can tell it has not been awarded yet.
            if year_awarded and self._degree_is_in_progress(raw_text, year_awarded):
                year_awarded = f"Expected {year_awarded}"

            # Build cell contents with mixed normal/track-change content
            # Cell 0: Degree (never enriched)
            degree_content = [(degree, False, "")]

            # Check if location is already present in institution to avoid duplication
            # e.g., "University of Pittsburgh, Pittsburgh, PA" shouldn't get ", Pittsburgh, PA" appended again
            location_already_present = False
            if location and institution:
                # Check if city is already in the institution string
                location_parts = location.split(',')
                if location_parts:
                    city = location_parts[0].strip()
                    # Check for city name in institution (case-insensitive)
                    if city.lower() in institution.lower():
                        location_already_present = True

            # Cell 1: Institution + Location (location may be enriched)
            if location and location_is_enriched and not location_already_present:
                # Institution is normal text, ", City, State" is track change
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, "Institution Enrichment")
                    ]
                else:
                    institution_content = [(location, True, "Institution Enrichment")]
            elif location and not location_already_present:
                # Location exists but not from enrichment - all normal text
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                # No location or location already present
                institution_content = [(institution, False, "")]

            # Cell 2: Dates (never enriched for now)
            dates_content = [(dates, False, "")]

            # Cell 3: Year awarded (may be enriched if extracted from text)
            year_content = [(year_awarded, year_is_enriched, "Text Extraction")]

            # Add the row with mixed content
            self._add_table_row_with_mixed_content(
                table,
                [degree_content, institution_content, dates_content, year_content],
                entry=entry
            )

    def _fill_other_education(self, entries: List[Dict]):
        """Fill Other Educational Experiences section (B2 entries).

        B2 entries are training programs, certifications, workshops - not formal degrees.
        These go in a separate section from the main Education table.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Other Educational Experiences ({len(entries)} entries)...")

        # Try to find the "OTHER EDUCATIONAL" or similar section
        section_idx = self._find_paragraph_with_text("OTHER EDUCATIONAL")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("SPECIAL TRAINING")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("ADDITIONAL TRAINING")

        if section_idx is None:
            # No dedicated section found - these entries will need to go elsewhere
            # For now, skip them (they could go in appendix or we could create a section)
            if self.verbose:
                print(f"  No 'Other Educational Experiences' section found in template")
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {})
            raw_text = entry.get('text', '')

            # Program/Training name
            program_name = (fields.get('program_name', '') or
                          fields.get('program_type', '') or
                          fields.get('title', ''))

            # Institution - use cleaned_name from enrichment if available
            institution = fields.get('institution', '')
            if institution and institution.lower() == 'none':
                institution = ''
            cleaned = self._get_cleaned_institution_name(entry)
            if cleaned:
                institution = cleaned

            # Skip entries with no meaningful content
            if not program_name and not institution:
                continue

            # Location from enrichment
            location, location_is_enriched = self._get_institution_location(entry)

            # Dates - format according to B2 requirements (mm/yy – mm/yy)
            start = fields.get('start_date', '')
            end = fields.get('end_date', '')
            year = fields.get('year', '') or fields.get('year_awarded', '')
            year_is_enriched = False

            # Try to build date range, or fall back to single year
            if start or end:
                dates = format_date_range(start, end, 'B2')
            elif year:
                dates = format_date_for_section(year, 'B2')
            else:
                # Try to extract from raw text
                extracted_year = self._extract_year_from_text(raw_text) if raw_text else ''
                if extracted_year:
                    dates = format_date_for_section(extracted_year, 'B2')
                    year_is_enriched = True
                else:
                    dates = ''

            # Build cell contents
            program_content = [(program_name, False, "")]

            if location and location_is_enriched:
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, "Institution Enrichment")
                    ]
                else:
                    institution_content = [(location, True, "Institution Enrichment")]
            elif location:
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                institution_content = [(institution, False, "")]

            dates_content = [(dates, year_is_enriched, "Text Extraction")]

            self._add_table_row_with_mixed_content(
                table,
                [program_content, institution_content, dates_content],
                entry=entry
            )

    @staticmethod
    def _propagate_institution_to_subentries(entries: List[Dict], verbose: bool = False) -> List[Dict]:
        """Fill blank institutions from the nearest preceding entry that has one.

        Source CVs often list sub-positions as indented bullets under a parent
        institution.  Stage 4 field extraction treats each bullet as a separate
        entry but can't see the parent's institution.  This forward-propagates
        institution (and its enrichment data) in document order so sub-entries
        inherit their parent context.
        """
        if not entries:
            return entries
        # Sort by document order (element_idx_start) to ensure parent comes first
        ordered = sorted(entries, key=lambda e: element_idx_sort_key(e.get('element_idx_start')))
        last_institution = None
        last_enrichment = None
        propagated = 0
        for entry in ordered:
            fields = entry.get('extracted_fields', {}) or {}
            inst = fields.get('institution') or fields.get('organization') or ''
            if inst:
                last_institution = inst
                last_enrichment = entry.get('institution_enrichment')
            elif last_institution:
                # This entry has no institution — inherit from parent
                if not fields:
                    entry['extracted_fields'] = fields = {}
                fields['institution'] = last_institution
                # Also propagate enrichment if available
                if last_enrichment and not entry.get('institution_enrichment'):
                    entry['institution_enrichment'] = dict(last_enrichment)
                propagated += 1
        if verbose and propagated > 0:
            print(f"    Propagated institution to {propagated} sub-entries")
        return entries

    # Title strings that field extraction sometimes emits when the source CV had
    # a column header instead of a real role (mirrors the filter in
    # ``_add_position_row``). Treated as "no title" for grouping purposes.
    _PLACEHOLDER_TITLES = frozenset({'title', 'position', 'role', 'name',
                                     'description', 'activity'})

    @classmethod
    def _position_title(cls, entry: Dict) -> str:
        """Real title for a position entry, with column-header placeholders removed."""
        fields = entry.get('extracted_fields', {}) or {}
        title = (fields.get('title') or '').strip()
        if title.lower() in cls._PLACEHOLDER_TITLES:
            return ''
        return title

    @staticmethod
    def _position_has_dates(entry: Dict) -> bool:
        """True if the entry carries any date of its own (start or end)."""
        fields = entry.get('extracted_fields', {}) or {}
        return bool(fields.get('start_date') or fields.get('end_date'))

    @classmethod
    def _merge_grouped_appointments(cls, entries: List[Dict],
                                    verbose: bool = False) -> List[Dict]:
        """Reassemble appointments fragmented across title / employer rows.

        Source CVs commonly list one employer with a date range on its own line
        and the several roles held there on the lines beneath it (or vice-versa:
        a role line followed by the unit + date range).  Stage 4 field extraction
        treats each line as a separate entry, so the same appointment is split
        into a title-less "employer + dates" row and one or more date-less
        "title only" rows.  Rendered straight, that produces blank-TITLE rows
        (which read as active/"Present" once sorted) and blank-DATES rows.

        This pass works in document order on a single taxonomy-code list (run
        after institution propagation) and applies three general rules:

        Rule 2 (header + children): a title-less dated entry immediately followed
            by one or more title-only entries at the same employer is an employer
            header over the roles held there — copy its dates onto each child and
            drop the now-redundant bare header.
        Rule 1 (adjacent pair): a remaining title-only entry document-adjacent to
            a title-less dated entry (either order) is one appointment split in
            two — copy the dates onto the titled row and drop the bare dates row.
        Rule 3 (employer summary): a title-less dated header whose date span is
            already covered by an overlapping *titled* row at the same employer is
            redundant — drop it (but keep it if it is the only record).

        Rules 2 then 1 run as separate passes so a header is never mistaken for a
        lone adjacent dates row. Dates are only ever *copied into* a row that
        lacks them; an entry that already carries its own dates is never
        overwritten. No titles or dates are fabricated — a row stays blank if the
        group genuinely has no source.
        """
        if not entries or len(entries) < 2:
            return entries

        ordered = sorted(entries,
                         key=lambda e: element_idx_sort_key(e.get('element_idx_start')))

        def _copy_dates(src: Dict, dst: Dict) -> None:
            src_f = src.get('extracted_fields', {}) or {}
            dst_f = dst.get('extracted_fields')
            if not dst_f:
                dst['extracted_fields'] = dst_f = {}
            if not (dst_f.get('start_date') or dst_f.get('end_date')):
                dst_f['start_date'] = src_f.get('start_date', '')
                dst_f['end_date'] = src_f.get('end_date', '')

        def _employer(e: Dict) -> str:
            f = e.get('extracted_fields', {}) or {}
            return (f.get('institution') or f.get('organization') or '').strip().lower()

        dropped = set()  # id() of header entries fully absorbed by children
        merged = 0

        # Pass 1 — Rule 2: a title-less dated entry is an employer header; the
        # immediately-following title-only rows are the roles held there. Copy the
        # header's dates onto each child, then drop the redundant bare header.
        # Children must share the header's employer (institution propagation has
        # already pushed the header's institution onto its sub-rows, so a mismatch
        # means the run has reached a different employer). Done before Rule 1 so a
        # header is never mistaken for a lone adjacent dates row.
        for i, entry in enumerate(ordered):
            if id(entry) in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            header_employer = _employer(entry)
            children = []
            for nxt in ordered[i + 1:]:
                if id(nxt) in dropped:
                    continue
                nxt_employer = _employer(nxt)
                same_employer = (not nxt_employer or not header_employer
                                 or nxt_employer == header_employer)
                if (cls._position_title(nxt) and not cls._position_has_dates(nxt)
                        and same_employer):
                    children.append(nxt)
                else:
                    break
            if children:
                for child in children:
                    _copy_dates(entry, child)
                    merged += 1
                dropped.add(id(entry))

        # Pass 2 — Rule 1: a title-only row immediately adjacent (in document
        # order) to a remaining bare dates row, in either order, is one
        # appointment split across two lines (e.g. "Staff Nurse" /
        # "Medical/Surgical Unit (07/04-04/10)"). Physical adjacency is the
        # fingerprint; the bare dates row often carries a sub-unit/department in
        # its institution field rather than a distinct employer, so the employer
        # strings need not match here.
        for i, entry in enumerate(ordered):
            if id(entry) in dropped:
                continue
            if not cls._position_title(entry) or cls._position_has_dates(entry):
                continue

            def _date_neighbor(cand):
                if cand is None or id(cand) in dropped:
                    return None
                if cls._position_title(cand) or not cls._position_has_dates(cand):
                    return None
                return cand

            neighbor = _date_neighbor(ordered[i + 1] if i + 1 < len(ordered) else None)
            if neighbor is None:
                neighbor = _date_neighbor(ordered[i - 1] if i > 0 else None)
            if neighbor is not None:
                _copy_dates(neighbor, entry)
                dropped.add(id(neighbor))
                merged += 1

        # Rule 3: a title-less dated "employer summary" header whose date range is
        # already represented by titled sub-positions at the same employer is
        # redundant — its only content (institution + a date span) reappears, with
        # a title, on the rows beneath it.  Drop it so it does not render as a
        # blank-TITLE row.  Requires an overlapping *titled* sibling at the same
        # institution; a header with no such sibling is the sole record and kept.
        for entry in ordered:
            if id(entry) in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            employer = _employer(entry)
            if not employer:
                continue
            for other in ordered:
                if other is entry or id(other) in dropped:
                    continue
                if not cls._position_title(other) or not cls._position_has_dates(other):
                    continue
                if _employer(other) != employer:
                    continue
                if _dates_overlap_or_match(entry, other):
                    dropped.add(id(entry))
                    merged += 1
                    break

        if not dropped:
            if verbose and merged:
                print(f"    Merged dates into {merged} fragmented appointment rows")
            return entries

        result = [e for e in ordered if id(e) not in dropped]
        if verbose:
            print(f"    Merged {len(dropped)} fragmented appointment row(s); "
                  f"propagated dates to {merged} role row(s)")
        return result

    def _fill_positions(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill positions tables with track changes for enriched content.

        The WCM template has THREE separate position tables:
        1. Academic Appointments (D1) - faculty positions
        2. Hospital Appointments (D2) - clinical positions
        3. Other Professional Positions (D3) - non-academic positions

        Track changes are used for city/state from institution enrichment only.
        """
        d1_entries = entries_by_code.get('D1', [])
        d2_entries = entries_by_code.get('D2', [])
        d3_entries = entries_by_code.get('D3', [])

        # Propagate institution from parent entries to blank sub-entries, then
        # reassemble appointments that were fragmented into separate title /
        # employer+dates rows (see _merge_grouped_appointments).
        for code, entry_list in (('D1', d1_entries), ('D2', d2_entries), ('D3', d3_entries)):
            self._propagate_institution_to_subentries(entry_list, verbose=self.verbose)
            merged = self._merge_grouped_appointments(entry_list, verbose=self.verbose)
            if merged is not entry_list:
                entry_list[:] = merged
                entries_by_code[code] = entry_list

        total_positions = len(d1_entries) + len(d2_entries) + len(d3_entries)
        if self.verbose:
            print(f"Filling Positions ({total_positions} entries)...")
            if d1_entries:
                print(f"  D1 Academic: {len(d1_entries)} entries")
            if d2_entries:
                print(f"  D2 Hospital: {len(d2_entries)} entries")
            if d3_entries:
                print(f"  D3 Other: {len(d3_entries)} entries")

        # Fill Academic Appointments table (D1)
        acad_idx = self._find_paragraph_with_text("Academic Appointments")
        if acad_idx is not None and d1_entries:
            acad_table = self._find_table_after_paragraph(acad_idx)
            if acad_table:
                self._clear_table_data(acad_table, keep_header=True)
                self.stats['tables_populated'] += 1
                sorted_d1 = sort_entries_reverse_chronological(d1_entries)
                for entry in sorted_d1:
                    self._add_position_row(acad_table, entry)

        # Fill Hospital Appointments table (D2)
        hosp_idx = self._find_paragraph_with_text("Hospital Appointments")
        if hosp_idx is not None and d2_entries:
            hosp_table = self._find_table_after_paragraph(hosp_idx)
            if hosp_table:
                self._clear_table_data(hosp_table, keep_header=True)
                self.stats['tables_populated'] += 1
                sorted_d2 = sort_entries_reverse_chronological(d2_entries)
                for entry in sorted_d2:
                    self._add_position_row(hosp_table, entry)

        # Fill Other Professional Positions table (D3)
        other_idx = self._find_paragraph_with_text("Other Professional Positions")
        if other_idx is not None and d3_entries:
            other_table = self._find_table_after_paragraph(other_idx)
            if other_table:
                self._clear_table_data(other_table, keep_header=True)
                self.stats['tables_populated'] += 1
                sorted_d3 = sort_entries_reverse_chronological(d3_entries)
                for entry in sorted_d3:
                    self._add_position_row(other_table, entry)

        # Fallback: If no specific subsection tables found, use the generic PROFESSIONAL POSITIONS table
        if acad_idx is None and hosp_idx is None and other_idx is None:
            pos_idx = self._find_paragraph_with_text("PROFESSIONAL POSITIONS")
            if pos_idx is None:
                return

            table = self._find_table_after_paragraph(pos_idx)
            if not table:
                return

            self._clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

            # Combine all and sort
            all_entries = d1_entries + d2_entries + d3_entries
            sorted_entries = sort_entries_reverse_chronological(all_entries)
            for entry in sorted_entries:
                self._add_position_row(table, entry)

    def _add_position_row(self, table, entry: Dict):
        """Add a single position entry to a table."""
        original_text = entry.get('text', '')
        fields = entry.get('extracted_fields', {}) or {}

        # Check if we have valid extracted fields - if so, use them even if text looks like a header
        has_valid_fields = bool(
            fields.get('title') or
            fields.get('institution') or
            fields.get('organization') or
            (fields.get('start_date') and fields.get('end_date'))
        )

        # Skip table header entries that were mistakenly extracted as data
        # BUT only if we don't have valid extracted fields to work with
        if not has_valid_fields and self._is_table_header_entry(original_text, ['title', 'institution', 'organization', 'dates', 'city', 'state', 'position']):
            if self.verbose:
                print(f"  Skipping position header entry: '{original_text[:50]}...'")
            return

        title = fields.get('title') or ''
        # Detect placeholder values that are actually column headers from source CV tables
        # e.g., field extraction returning "Title" when the CV had "Title | Institution | Dates"
        title_lower = title.strip().lower()
        if title_lower in ('title', 'position', 'role', 'name', 'description', 'activity'):
            title = ''
        # D3 entries often use 'organization' instead of 'institution' in field extraction
        raw_institution = fields.get('institution', '') or fields.get('organization', '')
        department = fields.get('department', '')

        # Only try to recover institution from raw text if it's actually EMPTY
        # Don't overwrite valid extracted institutions like "Weill Cornell Medical College"
        # just because they don't include city/state (that comes from enrichment)
        if not raw_institution:
            raw_text = entry.get('text', '')
            # Try to extract institution from tab-separated or newline-separated text
            if '\t' in raw_text or '\n' in raw_text:
                parts = re.split(r'[\t\n]', raw_text)
                for part in parts:
                    part = part.strip()
                    # Skip parts that look like titles, dates, or headers
                    if not part or len(part) < 3:
                        continue
                    if re.match(r'^(title|institution|dates?|city|state|\d)', part.lower()):
                        continue
                    # Check for location patterns (City, State or Organization City, State)
                    if re.search(r',\s*[A-Z]{2}\b', part) or re.search(r'\b[A-Z][a-z]+,\s*[A-Z][A-Za-z]', part):
                        raw_institution = part
                        break

        # Use LLM-cleaned institution name (strips embedded location); fall back to raw field
        institution = self._get_cleaned_institution_name(entry) or raw_institution

        # Build base institution string with department
        institution_base = institution
        if department:
            institution_base = f"{institution}, {department}"

        # Location from Stage 5b enrichment
        location, location_is_enriched = self._get_institution_location(entry)

        # Get taxonomy code for this entry (D1, D2, or D3)
        taxonomy_code = entry.get('taxonomy_code', 'D1')

        # Dates - format according to D1/D2/D3 requirements (mm/yy - mm/yy)
        start = fields.get('start_date', '')
        end = fields.get('end_date', '')
        dates = format_date_range(start, end, taxonomy_code)

        # Build cell contents with mixed normal/track-change content
        title_content = [(title, False, "")]

        # Check if location is already present in institution_base to avoid duplication
        # e.g., "University of Pittsburgh, Pittsburgh, PA" shouldn't get ", Pittsburgh, PA" appended again
        location_already_present = False
        if location and institution_base:
            # Check if city is already in the institution string
            location_parts = location.split(',')
            if location_parts:
                city = location_parts[0].strip()
                # Check for city name in institution (case-insensitive)
                if city.lower() in institution_base.lower():
                    location_already_present = True

        if location and location_is_enriched and not location_already_present:
            # Institution/dept is normal text, ", City, State" is track change
            if institution_base:
                institution_content = [
                    (institution_base, False, ""),
                    (f", {location}", True, "Institution Enrichment")
                ]
            else:
                institution_content = [(location, True, "Institution Enrichment")]
        elif location and not location_already_present:
            institution_full = f"{institution_base}, {location}" if institution_base else location
            institution_content = [(institution_full, False, "")]
        else:
            institution_content = [(institution_base, False, "")]

        dates_content = [(dates, False, "")]

        self._add_table_row_with_mixed_content(
            table,
            [title_content, institution_content, dates_content],
            entry=entry
            )

    def _fill_postdoc_training(self, entries_by_code: Dict[str, List[Dict]], all_entries: List[Dict] = None):
        """Fill postdoctoral training table with track changes for enriched content.

        Track changes are used for city/state from institution enrichment only.
        """
        training_entries = (
            entries_by_code.get('C', []) +
            entries_by_code.get('C1', []) +
            entries_by_code.get('C2', [])
        )

        if not training_entries:
            return

        if self.verbose:
            print(f"Filling Postdoctoral Training ({len(training_entries)} entries)...")

        # Try to find the POSTDOCTORAL section
        training_idx = self._find_paragraph_with_text("POSTDOCTORAL")
        if training_idx is None:
            training_idx = self._find_paragraph_with_text("TRAINING")
        if training_idx is None:
            return

        table = self._find_table_after_paragraph(training_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(training_entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {})

            # Training type/title (clean up tabs that may have been extracted)
            training_type = fields.get('training_type', '') or fields.get('title', 'Postdoctoral')
            # Replace tabs with comma-space for cleaner display
            if '\t' in training_type:
                training_type = ', '.join(part.strip() for part in training_type.split('\t') if part.strip())
            field_of_study = fields.get('field_of_study', '') or fields.get('specialty', '')
            if field_of_study and field_of_study not in training_type:
                training_type = f"{training_type}, {field_of_study}" if training_type else field_of_study

            # Institution with location from enrichment
            raw_institution = fields.get('institution', '')

            # Use LLM-cleaned institution name (strips embedded location); fall back to raw field
            institution = self._get_cleaned_institution_name(entry) or raw_institution

            # If institution is missing, try to recover from nearby entries in original CV
            if not institution and all_entries:
                institution = self._recover_institution_from_nearby_entries(entry, all_entries)

            # Location from Stage 5b enrichment
            location, location_is_enriched = self._get_institution_location(entry)

            # Get taxonomy code for this entry (C, C1, or C2)
            taxonomy_code = entry.get('taxonomy_code', 'C')

            # Dates - format according to C/C1/C2 requirements (mm/yy - mm/yy)
            start = fields.get('start_date', '')
            end = fields.get('end_date', '')
            dates = format_date_range(start, end, taxonomy_code)

            # Build cell contents with mixed normal/track-change content
            training_content = [(training_type, False, "")]

            # Check if location is already present in institution to avoid duplication
            location_already_present = False
            if location and institution:
                # Check if city is already in the institution string
                location_parts = location.split(',')
                if location_parts:
                    city = location_parts[0].strip()
                    # Check for city name in institution (case-insensitive)
                    if city.lower() in institution.lower():
                        location_already_present = True

            if location and location_is_enriched and not location_already_present:
                # Institution is normal text, ", City, State" is track change
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, "Institution Enrichment")
                    ]
                else:
                    institution_content = [(location, True, "Institution Enrichment")]
            elif location and not location_already_present:
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                institution_content = [(institution, False, "")]

            dates_content = [(dates, False, "")]

            self._add_table_row_with_mixed_content(
                table,
                [training_content, institution_content, dates_content],
                entry=entry
            )

    def _fill_research_summary(self, research_summary_data: Optional[Dict]):
        """Fill Research Summary section from Stage 4.5 output.

        Inserts a RESEARCH SUMMARY section before RESEARCH SUPPORT with
        the biosketch-style research summary paragraph.

        Args:
            research_summary_data: Stage 4.5 standalone output containing:
                - research_summary.text: The generated summary
                - research_summary.generation_method: "llm_generated" or "existing_content"
                - research_summary.word_count: Word count of summary

        Returns:
            True if a summary paragraph was rendered, False otherwise. Callers use
            this to route M1 entries to the appendix when the summary is absent
            (#317) instead of dropping them.
        """
        if not research_summary_data:
            if self.verbose:
                print("Skipping Research Summary section (no Stage 4.5 output)")
            return False

        # Extract summary from Stage 4.5 structure
        summary_info = research_summary_data.get('research_summary', {})
        summary_text = summary_info.get('text', '')

        if not summary_text or len(summary_text.strip()) < 50:
            if self.verbose:
                print("Skipping Research Summary section (no substantive content)")
            return False

        word_count = summary_info.get('word_count', len(summary_text.split()))
        generation_method = summary_info.get('generation_method', 'unknown')

        if self.verbose:
            print(f"Filling Research Summary ({word_count} words, {generation_method})...")

        # Find RESEARCH ACTIVITIES section (M1) to insert under
        activities_idx = self._find_paragraph_with_text("RESEARCH ACTIVITIES")
        if activities_idx is None:
            # Fallback: try "Research Activities" (case variations)
            activities_idx = self._find_paragraph_with_text("Research Activities")
        if activities_idx is None:
            if self.verbose:
                print("  Warning: Could not find 'RESEARCH ACTIVITIES' section")
            return False

        # Get the paragraph element to insert after
        activities_para = self.doc.paragraphs[activities_idx]
        body = self.doc.element.body
        body_elements = list(body)

        try:
            insert_idx = body_elements.index(activities_para._element) + 1  # Insert AFTER header
        except ValueError:
            return False

        # Add blank line after RESEARCH ACTIVITIES header
        blank_para = self.doc.add_paragraph()
        blank_para.paragraph_format.space_after = Pt(6)
        body.insert(insert_idx, blank_para._element)
        insert_idx += 1

        # Create summary paragraph (no new header - goes under existing RESEARCH ACTIVITIES)
        # Use track changes since this is LLM-generated content, not from the original CV
        summary_para = self.doc.add_paragraph()
        self._add_track_change_insertion(summary_para, summary_text.strip(), author="LLM Research Summary")
        summary_para.paragraph_format.space_after = Pt(12)

        # Move to correct position (after blank line)
        body.insert(insert_idx, summary_para._element)

        self.stats['entries_inserted'] += 1
        return True

    def _fill_research_support(self, entries_by_code: Dict[str, List[Dict]], cv_owner: Dict = None, document_uid: str = ''):
        """Fill research support section with individual tables per grant.

        Creates a table for each grant with the WCM data model:
        - Award Source (funding agency)
        - Project title
        - Annual direct costs
        - Non-financial support
        - Duration of support (mm/yyyy-mm/yyyy)
        - Name of Principal Investigator
        - Your role
        - Your percent (%) effort
        - Major goals (optional)

        Grants are organized under WCM template headers:
        - Current Research Funding (M2A)
        - Past (Completed) Funding (M2B)
        - Pending Funding (M2C)

        Reclassification: Grants classified as M2A (current) but with end dates
        before the current year are moved to M2B (completed) with a comment.
        """
        # Get CV owner name for auto-filling PI when role is Principal Investigator
        owner_name = self._get_cv_owner_name(cv_owner, document_uid)
        current_year = datetime.now().year

        # Filter out role/effort header entries and extract percent effort metadata
        # These are entries like "Individual's role in project including percent effort\nProject Name 0.01"
        role_effort_pattern = re.compile(
            r"(?:Individual's role|your role|role in project|percent effort)",
            re.IGNORECASE
        )
        effort_lookup = {}  # project_name_normalized -> percent_effort

        def filter_and_extract_effort(entries):
            """Filter out role/effort headers and build effort lookup."""
            filtered = []
            for entry in entries:
                text = entry.get('text', '')
                # Check if this is a role/effort header entry
                if role_effort_pattern.search(text[:60]):
                    # Parse project names and their percent efforts from this entry
                    # Pattern: "Project Title 0.01" or "Project Title .08FTE"
                    lines = text.split('\n')
                    for line in lines[1:]:  # Skip the header line
                        line = line.strip()
                        if not line:
                            continue
                        # Match pattern: "Project Name 0.01" or "Project Name .08FTE"
                        effort_match = re.search(r'^(.+?)\s+(\d*\.?\d+)\s*(?:FTE)?$', line, re.IGNORECASE)
                        if effort_match:
                            project_name = effort_match.group(1).strip().lower()
                            effort_value = effort_match.group(2)
                            # Normalize to percentage (0.01 -> 1%, .08 -> 8%)
                            try:
                                effort_float = float(effort_value)
                                if effort_float <= 1:
                                    effort_pct = f"{int(effort_float * 100)}%"
                                else:
                                    effort_pct = f"{int(effort_float)}%"
                                effort_lookup[project_name] = effort_pct
                            except ValueError:
                                pass
                    if self.verbose:
                        print(f"  Filtered role/effort header entry, extracted {len(effort_lookup)} effort values")
                    continue  # Skip this entry, don't add to filtered
                filtered.append(entry)
            return filtered

        # Reclassify M2A grants with past end dates to M2B
        m2a_entries = filter_and_extract_effort(list(entries_by_code.get('M2A', [])))
        m2b_entries = filter_and_extract_effort(list(entries_by_code.get('M2B', [])))
        m2c_entries = filter_and_extract_effort(list(entries_by_code.get('M2C', [])))

        # Apply extracted percent effort to matching grants
        def apply_effort_to_grants(entries):
            for entry in entries:
                fields = entry.get('extracted_fields', {})
                title = (fields.get('title', '') or '').lower().strip()
                if title and not fields.get('percent_effort'):
                    # Try to find matching effort in lookup
                    for project_name, effort in effort_lookup.items():
                        if project_name in title or title in project_name:
                            fields['percent_effort'] = effort
                            if self.verbose:
                                print(f"  Matched effort {effort} to '{title[:40]}...'")
                            break

        apply_effort_to_grants(m2a_entries)
        apply_effort_to_grants(m2b_entries)
        apply_effort_to_grants(m2c_entries)

        # Rebucket by each grant's own extracted status BEFORE date inference:
        # an explicit "Under review" / "Not funded" beats everything (#210).
        bucket_lists = {'M2B': m2b_entries, 'M2C': m2c_entries}
        for source_code, source_list in (('M2A', m2a_entries), ('M2B', m2b_entries)):
            for entry in list(source_list):
                fields = entry.get('extracted_fields') or {}
                target, note = grant_status_rebucket_target(fields.get('status'))
                if target and target != source_code:
                    source_list.remove(entry)
                    entry.setdefault('reclassification_note', note)
                    bucket_lists[target].append(entry)
                    if self.verbose:
                        title = str(fields.get('title') or 'Unknown')
                        print(f"  Status rebucket {source_code}->{target}: '{title[:40]}'")

        # Check each M2A entry for past end dates
        entries_to_move = []
        for entry in m2a_entries:
            fields = entry.get('extracted_fields', {})
            end_date = fields.get('end_date', '')

            # Parse end date to check if it's in the past
            if end_date and end_date.lower() not in ('present', 'current', 'ongoing', ''):
                # Try to extract year from end date
                year_match = re.search(r'(\d{4})', str(end_date))
                if year_match:
                    end_year = int(year_match.group(1))
                    if end_year < current_year:
                        # This grant has ended - reclassify to M2B
                        entries_to_move.append((entry, end_date, end_year))

        # Move entries and add reclassification comments
        for entry, end_date, end_year in entries_to_move:
            m2a_entries.remove(entry)

            # Add reclassification note to the entry
            if 'reclassification_note' not in entry:
                entry['reclassification_note'] = f"Reclassified from Current (M2A) to Completed (M2B): end date {end_date} is before {current_year}"

            m2b_entries.append(entry)

            if self.verbose:
                title = entry.get('extracted_fields', {}).get('title') or 'Unknown'
                print(f"  Reclassified to M2B: '{title[:40]}...' (ended {end_year})")

        # Map taxonomy codes to WCM template section headers
        # These must match the exact text in the official WCM template
        categories = [
            ('M2A', 'Current Research Funding', m2a_entries),
            ('M2B', 'Past (Completed) Funding', m2b_entries),
            ('M2C', 'Pending Funding', m2c_entries),
        ]

        total_grants = sum(len(entries) for _, _, entries in categories)
        if self.verbose:
            print(f"Filling Research Support ({total_grants} grants)...")

        # Process each category - find the corresponding section in the template
        for code, section_header, entries in categories:
            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(section_header)
            if section_idx is None:
                if self.verbose:
                    print(f"  Warning: Could not find section header '{section_header}'")
                continue

            # Remove any existing template table after this section
            # (even if no entries, to avoid leaving empty template tables)
            existing_table = self._find_table_after_paragraph(section_idx)
            if existing_table:
                existing_table._element.getparent().remove(existing_table._element)

            if not entries:
                continue

            # Sort entries reverse chronologically (most recent first)
            sorted_entries = sort_entries_reverse_chronological(entries)

            if self.verbose:
                print(f"  {code}: {len(entries)} grants -> '{section_header}'")

            # Create a table for each grant with spacing between them
            # Track the last inserted element (as actual XML element reference)
            last_element = self.doc.paragraphs[section_idx]._element

            for i, entry in enumerate(sorted_entries):
                fields = entry.get('extracted_fields', {})

                # Create grant table - pass the element to insert after and owner name
                grant_table = self._create_grant_table(fields, code, entry, insert_after_element=last_element, owner_name=owner_name)
                if grant_table:
                    self.stats['tables_populated'] += 1
                    self.stats['entries_inserted'] += 1

                    # Update last_element to the newly inserted table
                    last_element = grant_table._tbl

                    # Add blank paragraph after each grant table for spacing
                    # (except after the last one in each category)
                    if i < len(sorted_entries) - 1:
                        spacing_para = self._add_spacing_paragraph(after_element=grant_table._tbl)
                        if spacing_para is not None:
                            last_element = spacing_para

    def _create_grant_table(self, fields: Dict, code: str, entry: Dict = None, insert_after_element=None, owner_name: str = '') -> Optional[Table]:
        """Create an individual grant table with the WCM data model.

        Args:
            fields: Extracted field data for the grant
            code: Taxonomy code (M2A, M2B, M2C)
            entry: Full entry dict for comments/metadata
            insert_after_element: XML element to insert after. If None, falls back to RESEARCH SUPPORT.
            owner_name: CV owner's name for auto-filling PI when role is Principal Investigator

        Returns:
            Table object, or None if entry is too sparse to create a useful table
        """
        # Validate minimum required fields - skip header-like entries
        # A valid grant should have at least a title OR (agency + role/dates)
        # Check multiple title field names since clinical trials use trial_title/study_title/text
        title = fields.get('title', '') or fields.get('trial_title', '') or fields.get('study_title', '') or fields.get('text', '')

        # Clean up title - remove repeated content from merged table cells
        # e.g., "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"
        title = self._deduplicate_repeated_content(title)

        agency = fields.get('agency') or fields.get('funding_source', '') or fields.get('sponsor', '')
        total_funding = fields.get('total_funding', '') or fields.get('annual_direct_costs', '')

        # Detect and fix cross-field duplication where the same content appears in multiple fields
        # This happens when Stage 4 incorrectly puts the same text in agency, title, AND funding
        if title and agency and title.strip().lower() == agency.strip().lower():
            # Agency and title are identical - keep as title only, clear agency
            agency = ''
        if title and total_funding and title.strip().lower() == total_funding.strip().lower():
            # Funding is same as title - clear funding
            total_funding = ''
            fields['total_funding'] = ''
            fields['annual_direct_costs'] = ''
        role = fields.get('pi_role') or fields.get('role', '') or fields.get('description', '')
        start_date = fields.get('start_date', '') or fields.get('date', '')
        end_date = fields.get('end_date', '')

        has_title = bool(title and len(title.strip()) > 10)
        # For clinical trials: if we have a title and a date, that's substantive enough
        has_substantive_info = bool((agency and (role or start_date or end_date)) or (title and start_date))

        if not has_title and not has_substantive_info:
            # This is likely a header like "Funding: National Cancer Institute" - skip it
            if self.verbose:
                text = entry.get('text', '')[:50] if entry else ''
                print(f"  Skipping sparse grant entry: '{text}...'")
            return None

        # Get role and determine PI name
        role = fields.get('pi_role') or fields.get('role', '')
        percent_effort = fields.get('percent_effort', '')

        # Fix: If role looks like a percent value (just a number, possibly with %), it was misextracted
        # The LLM sometimes puts percent effort in the pi_role field
        if role and not percent_effort:
            role_stripped = role.strip().rstrip('%').strip()
            if role_stripped.isdigit() or (role_stripped.replace('.', '', 1).isdigit()):
                # This looks like a percent value, not a role description
                percent_effort = role
                role = ''

        pi_name = fields.get('pi_name') or fields.get('principal_investigator', '') or fields.get('co_investigators', '')

        # Parse PI name from raw text if not in extracted fields
        # Common format: "Agency | Amount | Dates | PI Name"
        raw_text = entry.get('text', '') if entry else ''
        if not pi_name and raw_text and '|' in raw_text:
            parts = [p.strip() for p in raw_text.split('|')]
            # Skip if all parts are identical (repeated content from merged cells)
            unique_parts = set(p.lower() for p in parts if p)
            if len(parts) >= 4 and len(unique_parts) > 1:
                # The last part is often the PI name
                potential_name = parts[-1].strip()
                # Check if it looks like a name:
                # - Not just digits/dates
                # - Matches name pattern (First Last or Last, First)
                # - Short enough to be a name (< 50 chars)
                # - Doesn't contain project/grant keywords
                project_keywords = ['project', 'study', 'grant', 'research', 'program', 'trial',
                                    'investigation', 'promotion', 'implementation', 'development']
                if (potential_name and
                    len(potential_name) < 50 and
                    not re.match(r'^[\d\-/]+$', potential_name) and
                    not any(kw in potential_name.lower() for kw in project_keywords)):
                    # Check for name patterns (First Last or Last, First)
                    if re.match(r'^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+$', potential_name):
                        pi_name = potential_name

        # Auto-fill PI name when role indicates Principal Investigator and no PI name specified
        if not pi_name and owner_name and 'principal' in role.lower() and 'investigator' in role.lower():
            pi_name = owner_name

        # Format costs as currency
        costs = fields.get('annual_direct_costs') or fields.get('total_funding', '')
        costs_formatted = self._format_currency(costs)

        # Define the grant data model rows
        # Use the extracted title/agency variables (which check multiple field names) instead of just fields.get()
        rows = [
            ('Award Source:', agency),
            ('Project title:', title),
            ('Annual direct costs:', costs_formatted),
            ('Non-financial support:', fields.get('non_financial_support', '')),
            ('Duration of support:', self._format_grant_duration(fields, code)),
            ('Name of Principal Investigator:', pi_name),
            ('Your role:', role),
            ('Your percent (%) effort:', percent_effort),
        ]

        # Add optional major goals if present (check major_goals, description, or narrative)
        goals = fields.get('major_goals') or fields.get('description', '') or fields.get('narrative', '')
        if goals and len(goals.strip()) > 10:  # Only if substantive
            rows.append(('Major project goals:', goals))

        # Create a new table with 2 columns
        table = self.doc.add_table(rows=len(rows), cols=2)

        # Set table borders and formatting
        self._set_table_border(table, color='808080', size=4)

        # Fill in the table
        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            # Label cell (bold)
            label_cell = row.cells[0]
            label_cell.text = label
            self._set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    self._set_font(run, bold=True)

            # Value cell
            value_cell = row.cells[1]
            value_cell.text = str(value) if value else ''
            self._set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)

        # Add comments from entry
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        # Move table to correct position
        body = self.doc.element.body
        body_elements = list(body)

        # Determine insertion point
        if insert_after_element is not None:
            insert_element = insert_after_element
        else:
            # Fallback: find RESEARCH SUPPORT
            support_idx = self._find_paragraph_with_text("RESEARCH SUPPORT")
            if support_idx is None:
                return table
            insert_element = self.doc.paragraphs[support_idx]._element

        # Find where to insert and place table after the element
        try:
            elem_idx = body_elements.index(insert_element)
            body.insert(elem_idx + 1, table._tbl)
        except (ValueError, IndexError):
            pass

        return table

    def _format_grant_duration(self, fields: Dict, taxonomy_code: str = 'M2A') -> str:
        """Format grant duration according to WCM requirements.

        Grants use mm/yy format per the template.
        Clinical trials may use 'date' instead of 'start_date'.
        """
        start = fields.get('start_date', '') or fields.get('date', '')
        end = fields.get('end_date', '')

        return format_date_range(start, end, taxonomy_code)

    def _fill_patents(self, entries: List[Dict]):
        """Fill Patents & Inventions section (M2D entries).

        Creates an individual 2-column label/value table per patent, inserted
        after the "Patents & Inventions" heading in the template.
        WCM template instruction: "Please include inventors, title of invention
        and patent number."

        Fields from Stage 4: patent_number, title, inventors, filing_date,
        issue_date, status, assignee, narrative.
        """
        if not entries:
            return

        # Find the section in the template
        section_idx = self._find_paragraph_with_text("Patents & Inventions")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Patents")
        if section_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find 'Patents & Inventions' section in template")
            return

        # Remove the instruction paragraph (the one after the header) if it starts with "Please include"
        if section_idx + 1 < len(self.doc.paragraphs):
            next_para = self.doc.paragraphs[section_idx + 1]
            if next_para.text.strip().startswith("Please include"):
                next_para.text = ""

        if self.verbose:
            print(f"Filling Patents & Inventions ({len(entries)} entries)...")

        # Sort entries reverse chronologically
        sorted_entries = sort_entries_reverse_chronological(entries)

        # Insert tables after the section header
        last_element = self.doc.paragraphs[section_idx]._element

        for i, entry in enumerate(sorted_entries):
            fields = entry.get('extracted_fields', {})

            title = fields.get('title', '')
            patent_number = fields.get('patent_number', '')
            inventors = fields.get('inventors', '')
            filing_date = fields.get('filing_date', '')
            issue_date = fields.get('issue_date', '')
            status = fields.get('status', '')
            assignee = fields.get('assignee', '')
            narrative = fields.get('narrative', '')

            # Skip entries with no meaningful content
            if not title and not patent_number:
                if self.verbose:
                    text = entry.get('text', '')[:50]
                    print(f"  Skipping sparse patent entry: '{text}...'")
                continue

            # Format dates
            formatted_filing = format_date_for_section(filing_date, 'M2D') if filing_date else ''
            formatted_issue = format_date_for_section(issue_date, 'M2D') if issue_date else ''

            # Build rows — only include rows that have data
            rows = []
            if title:
                rows.append(('Title of invention:', title))
            if patent_number:
                rows.append(('Patent number:', patent_number))
            if inventors:
                rows.append(('Inventors:', inventors))
            if status:
                rows.append(('Status:', status))
            if formatted_filing:
                rows.append(('Filing date:', formatted_filing))
            if formatted_issue:
                rows.append(('Issue date:', formatted_issue))
            if assignee:
                rows.append(('Assignee:', assignee))
            if narrative and len(narrative.strip()) > 10:
                rows.append(('Description:', narrative))

            if not rows:
                continue

            # Create a 2-column table
            table = self.doc.add_table(rows=len(rows), cols=2)
            self._set_table_border(table, color='808080', size=4)

            # Fill the table
            first_cell_para = None
            for ri, (label, value) in enumerate(rows):
                row = table.rows[ri]
                # Label cell (bold)
                label_cell = row.cells[0]
                label_cell.text = label
                self._set_cell_vertical_alignment(label_cell, 'center')
                for para in label_cell.paragraphs:
                    if ri == 0 and first_cell_para is None:
                        first_cell_para = para
                    for run in para.runs:
                        self._set_font(run, bold=True)

                # Value cell
                value_cell = row.cells[1]
                value_cell.text = str(value) if value else ''
                self._set_cell_vertical_alignment(value_cell, 'center')
                for para in value_cell.paragraphs:
                    for run in para.runs:
                        self._set_font(run)

            # Add comments from entry
            if first_cell_para:
                self._add_entry_comments(first_cell_para, entry)

            # Move table to correct position (after the last inserted element)
            body = self.doc.element.body
            body_elements = list(body)
            try:
                elem_idx = body_elements.index(last_element)
                body.insert(elem_idx + 1, table._tbl)
            except (ValueError, IndexError):
                pass

            self.stats['tables_populated'] += 1
            self.stats['entries_inserted'] += 1
            last_element = table._tbl

            # Add spacing between patent tables (except after the last one)
            if i < len(sorted_entries) - 1:
                spacing_para = self._add_spacing_paragraph(after_element=table._tbl)
                if spacing_para is not None:
                    last_element = spacing_para

    @staticmethod
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

    @staticmethod
    def _is_mentoring_outcome(entry: Dict) -> bool:
        """True if the entry is N4 mentoring-outcome narrative.

        _correct_mismatch_if_needed rewrites an unmapped N4 to N3A, stashing the
        original under 'taxonomy_code_original' — so check both (#261).
        """
        return 'N4' in (entry.get('taxonomy_code'), entry.get('taxonomy_code_original'))

    @staticmethod
    def _is_mentee_record(entry: Dict) -> bool:
        """True if the entry names a person, i.e. a per-mentee table can be built.

        N3A/N3B also carry aggregate summaries ("Ph.D. Graduated: 38") that name no
        one. Those are real content but cannot fill a per-mentee table (#261).
        """
        fields = entry.get('extracted_fields', {}) or {}
        return bool((fields.get('name') or fields.get('mentee_name') or '').strip())

    def _insert_mentoring_line(self, text: str, insert_after_idx: int, entry: Dict = None):
        """Insert a plain mentoring paragraph directly after ``insert_after_idx``.

        Used for content that belongs in MENTORING but has no per-mentee table to
        live in: aggregate counts (N3A/N3B with no name) and outcome narrative (N4).
        Positioned with the same body-splice the mentee tables use.
        """
        para = self.doc.add_paragraph()
        run = para.add_run(text)
        self._set_font(run)
        if entry:
            self._add_entry_comments(para, entry)

        body = self.doc.element.body
        try:
            target = self.doc.paragraphs[insert_after_idx]._element
            body.insert(list(body).index(target) + 1, para._element)
        except (ValueError, IndexError):
            pass
        self.stats['entries_inserted'] += 1
        return para

    def _fill_mentoring(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill mentoring section with individual tables per mentee.

        Creates tables for N3A (current mentees) and N3B (past mentees).
        Overrides: If end_date contains 'present', mentee is treated as current.

        N3A/N3B entries that name no mentee are aggregate summaries and render as
        plain lines instead of tables; N4 (mentoring outcomes) has no table at all
        and renders under the section header (#261). Without this, all three were
        dropped silently: _create_mentee_table returns None with no name, and N4 has
        no entry in TAXONOMY_TO_SECTION.
        """
        n3a_entries = list(entries_by_code.get('N3A', []))
        n3b_entries = list(entries_by_code.get('N3B', []))
        n4_entries = list(entries_by_code.get('N4', []))

        # Override: Move N3B entries to current if the relationship appears ongoing
        # If end_date contains "present" OR (has start_date but no end_date), treat as current
        # Rationale: If there's no end date, the displayed duration would show "-present"
        entries_to_move = []
        for entry in n3b_entries:
            fields = entry.get('extracted_fields', {})
            end_date = str(fields.get('end_date', '') or '').strip()
            start_date = str(fields.get('start_date', '') or '').strip()

            # Check if end_date indicates ongoing
            end_lower = end_date.lower()
            is_ongoing = ('present' in end_lower or end_lower in ('ongoing', 'current', 'now'))

            # Also treat as ongoing if there's a start but no end (would display as "-present")
            if not is_ongoing and start_date and not end_date:
                is_ongoing = True

            if is_ongoing:
                entries_to_move.append(entry)

        for entry in entries_to_move:
            n3b_entries.remove(entry)
            n3a_entries.append(entry)

        # Split off aggregate summaries (no mentee named) — they get a line, not a
        # table. Done after the ongoing-reshuffle above, which keys off dates a
        # summary never has, so the partition cannot change that outcome.
        n3a_summaries = [e for e in n3a_entries if not self._is_mentee_record(e)]
        n3b_summaries = [e for e in n3b_entries if not self._is_mentee_record(e)]
        n3a_entries = [e for e in n3a_entries if self._is_mentee_record(e)]
        n3b_entries = [e for e in n3b_entries if self._is_mentee_record(e)]

        # Outcome narrative arrives disguised as a current mentee (see
        # _is_mentoring_outcome). Reclaim it and render it under the section header
        # rather than beneath "Current Mentees:", where it does not belong.
        n4_entries += [e for e in n3a_summaries + n3b_summaries
                       if self._is_mentoring_outcome(e)]
        n3a_summaries = [e for e in n3a_summaries if not self._is_mentoring_outcome(e)]
        n3b_summaries = [e for e in n3b_summaries if not self._is_mentoring_outcome(e)]

        total_mentees = len(n3a_entries) + len(n3b_entries)
        total_extra = len(n3a_summaries) + len(n3b_summaries) + len(n4_entries)
        if total_mentees + total_extra == 0:
            return

        if self.verbose:
            print(f"Filling Mentoring ({total_mentees} mentees, {total_extra} summary/outcome lines)...")
            if entries_to_move:
                print(f"  Moved {len(entries_to_move)} mentees from Past to Current (end_date=present)")

        # Find "Current Mentees:" and "Past Mentees:" insertion points
        # These are more specific than "MENTORING" which can match other content
        current_mentees_idx = self._find_paragraph_exact("Current Mentees:")
        past_mentees_idx = self._find_paragraph_exact("Past Mentees:")

        # Fallback to section header if specific markers not found
        if current_mentees_idx is None and past_mentees_idx is None:
            mentoring_idx = self._find_paragraph_exact("MENTORING")
            if mentoring_idx is None:
                mentoring_idx = self._find_paragraph_with_text("Mentees")
            if mentoring_idx is None:
                return
            # Insert both current and past after the section header
            current_mentees_idx = mentoring_idx
            past_mentees_idx = mentoring_idx

        # Fill Current Mentees (N3A)
        if (n3a_entries or n3a_summaries) and current_mentees_idx is not None:
            # Remove any existing template table after "Current Mentees:"
            existing_table = self._find_table_after_paragraph(current_mentees_idx)
            if existing_table:
                existing_table._element.getparent().remove(existing_table._element)

            # Create tables for each current mentee (in REVERSE order so final order is correct)
            # Each table is inserted right after the header, pushing earlier ones down
            for entry in reversed(n3a_entries):
                fields = entry.get('extracted_fields', {})
                self._create_mentee_table_with_spacing(fields, current_mentees_idx, entry)
                self.stats['tables_populated'] += 1
                self.stats['entries_inserted'] += 1

            # Summaries go in last so they land directly under the header, above the
            # tables (each insert pushes the previous one down).
            self._insert_mentoring_summaries(n3a_summaries, current_mentees_idx)

        # Fill Past Mentees (N3B)
        if n3b_entries or n3b_summaries:
            # Re-find Past Mentees index since it may have shifted after current mentee insertion
            past_mentees_idx = self._find_paragraph_exact("Past Mentees:")
            if past_mentees_idx is not None:
                # Remove any existing template table after "Past Mentees:"
                existing_table = self._find_table_after_paragraph(past_mentees_idx)
                if existing_table:
                    existing_table._element.getparent().remove(existing_table._element)

                # Create tables for each past mentee (in REVERSE order so final order is correct)
                for entry in reversed(n3b_entries):
                    fields = entry.get('extracted_fields', {})
                    self._create_mentee_table_with_spacing(fields, past_mentees_idx, entry)
                    self.stats['tables_populated'] += 1
                    self.stats['entries_inserted'] += 1

                self._insert_mentoring_summaries(n3b_summaries, past_mentees_idx)

        # Mentoring outcomes (N4) have no table in the WCM template. Render them
        # under the section header, re-found because the inserts above shifted it.
        if n4_entries:
            mentoring_idx = self._find_paragraph_exact("MENTORING")
            if mentoring_idx is not None:
                self._insert_mentoring_summaries(n4_entries, mentoring_idx)

    def _insert_mentoring_summaries(self, entries: List[Dict], insert_after_idx: int):
        """Render summary/outcome entries as plain lines after ``insert_after_idx``.

        Reversed so that, with each insert landing immediately after the header and
        pushing the previous one down, the final document order matches ``entries``.
        """
        for entry in reversed(entries):
            text = _clean_inline_tabs((entry.get('text') or '').strip())
            if text:
                self._insert_mentoring_line(text, insert_after_idx, entry)

    def _create_mentee_table(self, fields: Dict, insert_after_idx: int, entry: Dict = None) -> Optional[Table]:
        """Create an individual mentee table matching WCM template structure.

        WCM Template expects:
        - Name
        - Site/Position (your role/title during mentorship, or degree program)
        - Mentoring Period (mm/yyyy-mm/yyyy)
        - Project/Accomplishments (dissertation title, research focus)
        - Current Position
        - Type of Supervision (research, clinical, teaching, leadership)
        """
        # Build Site/Position from available data
        # Prefer mentee_level (degree type) + site_position if both available
        site_position = ''
        mentee_level = fields.get('mentee_level', '')  # e.g., "PhD, MBSB"
        site_pos_raw = fields.get('site_position', '')  # e.g., "Thesis" or "Ph.D., Human Genetics"

        if mentee_level and site_pos_raw:
            # Combine if they're different
            if mentee_level.lower() not in site_pos_raw.lower():
                site_position = f"{mentee_level} - {site_pos_raw}"
            else:
                site_position = mentee_level or site_pos_raw
        else:
            site_position = mentee_level or site_pos_raw

        # Build Project/Accomplishments from research_focus (dissertation title)
        project = fields.get('research_focus', '') or fields.get('dissertation_title', '')

        # Determine supervision type - default to "Research" for thesis/dissertation mentees
        supervision_type = fields.get('supervision_type', '')
        if not supervision_type:
            # Infer from site_position or mentee_level
            level_lower = (mentee_level or site_pos_raw or '').lower()
            if any(x in level_lower for x in ['phd', 'thesis', 'dissertation', 'doctoral']):
                supervision_type = 'Research'
            elif any(x in level_lower for x in ['postdoc', 'fellow']):
                supervision_type = 'Research'
            elif any(x in level_lower for x in ['resident', 'clinical']):
                supervision_type = 'Clinical'
            elif any(x in level_lower for x in ['master', 'ms', 'ma']):
                supervision_type = 'Research'

        # Build all rows - include blank values for consistency with other sections
        rows = [
            ('Name:', fields.get('name') or fields.get('mentee_name', '')),
            ('Site/Position:', site_position),
            ('Mentoring Period:', self._format_mentee_duration(fields)),
            ('Project/Accomplishments:', project),
            ('Current Position:', fields.get('current_position', '')),
            ('Type of Supervision:', supervision_type),
        ]

        # Must have at least a name
        if not rows[0][1]:
            return None

        # Create table
        table = self.doc.add_table(rows=len(rows), cols=2)
        self._set_table_border(table, color='808080', size=4)

        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            # Label cell (bold)
            label_cell = row.cells[0]
            label_cell.text = label
            self._set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    self._set_font(run, bold=True)

            # Value cell
            value_cell = row.cells[1]
            value_cell.text = str(value) if value else ''
            self._set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)

        # Add comments from entry
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        # Position table in document
        body = self.doc.element.body
        if insert_after_idx < len(self.doc.paragraphs):
            target_para = self.doc.paragraphs[insert_after_idx]._element
            body_elements = list(body)
            try:
                para_idx = body_elements.index(target_para)
                body.insert(para_idx + 1, table._tbl)
            except (ValueError, IndexError):
                pass

        return table

    def _create_mentee_table_with_spacing(self, fields: Dict, insert_after_idx: int, entry: Dict = None) -> Optional[Table]:
        """Create an individual mentee table with spacing paragraph after it.

        Inserts: [header para] -> [spacing para] -> [table]
        Since we insert in reverse order, the final document shows:
        [header para] -> [table] -> [spacing para] -> [table] -> [spacing para] ...
        """
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        # First create the table
        table = self._create_mentee_table(fields, insert_after_idx, entry)
        if not table:
            return None

        # Now insert a spacing paragraph AFTER the table (which means BEFORE in insertion order)
        # Create a blank paragraph element
        body = self.doc.element.body
        spacing_para = OxmlElement('w:p')

        # Add paragraph properties for spacing
        pPr = OxmlElement('w:pPr')
        spacing = OxmlElement('w:spacing')
        spacing.set(qn('w:before'), '120')  # 6pt before
        spacing.set(qn('w:after'), '120')   # 6pt after
        pPr.append(spacing)
        spacing_para.append(pPr)

        # Insert the spacing paragraph right after the table
        # The table was inserted at para_idx + 1, so spacing goes at para_idx + 2
        if insert_after_idx < len(self.doc.paragraphs):
            target_para = self.doc.paragraphs[insert_after_idx]._element
            body_elements = list(body)
            try:
                para_idx = body_elements.index(target_para)
                # Table is at para_idx + 1, so insert spacing at para_idx + 2
                body.insert(para_idx + 2, spacing_para)
            except (ValueError, IndexError):
                pass

        return table

    def _format_mentee_duration(self, fields: Dict) -> str:
        """Format mentee duration."""
        start = fields.get('start_date', '')
        end = fields.get('end_date', '')

        if start and end:
            return f"{start}-{end}"
        elif start:
            return f"{start}-present"
        return ''

    # "MD" (from "Bethesda, MD") and "Bloomington" are comma segments the
    # short-proper-noun org fallback happily returns (#229) — never treat a
    # bare state abbreviation as an organization.
    _US_STATE_ABBREVS = frozenset({
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI',
        'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI',
        'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC',
        'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX', 'UT',
        'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'})

    _MONTH_TAIL_RE = re.compile(
        r'[\s,]*(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)$', re.IGNORECASE)

    def _split_award_year(self, text: str) -> Tuple[str, str]:
        """Split an award line into (name-without-year, year-or-range).

        Handles the shapes the honors fallback parser actually sees (#229):
        leading years/ranges ("2020 AECT ...", "2015-2017 Featured ...") and
        trailing years with punctuation ("..., August 2025." / "... (2021)").
        Returns the original text and '' when no year is found.
        """
        m = re.match(r'^\s*((?:19|20)\d{2}(?:\s*[-–]\s*'
                     r'(?:(?:19|20)\d{2}|present))?)\b[\s,.:–-]*',
                     text, re.IGNORECASE)
        if m:
            return text[m.end():].strip(' ,.;'), m.group(1)
        m = re.search(r'(?:^|[\s,(])((?:19|20)\d{2})\s*[).]?\s*$', text)
        if m:
            cleaned = text[:m.start()].rstrip(' ,.(;')
            # "..., August 2025." leaves a dangling month — drop it too
            cleaned = self._MONTH_TAIL_RE.sub('', cleaned).rstrip(' ,.;')
            return cleaned, m.group(1)
        return text, ''

    @staticmethod
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

    def _fill_honors(self, entries: List[Dict]):
        """Fill H. HONORS, AWARDS section.

        WCM template has table with columns: Name of award | Organization | Date awarded (yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Honors ({len(entries)} entries)...")

        # Find the HONORS section
        honors_idx = self._find_paragraph_with_text("HONORS")
        if honors_idx is None:
            honors_idx = self._find_paragraph_with_text("AWARDS")
        if honors_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(honors_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries that were mistakenly extracted as data
            # Common patterns: "Name of award\tOrganization\tDate awarded" or similar
            if self._is_table_header_entry(original_text, ['award', 'honor', 'organization', 'date', 'year', 'granting']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Get fields for table columns
            award_name = fields.get('award_name', '')
            granting_body = fields.get('granting_body', '') or fields.get('organization', '')
            date = fields.get('date', '') or fields.get('year', '')

            # Check if this entry contains multiple awards (newline-separated)
            # This happens when multiple honors were merged during extraction
            lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            # Separate award lines from year lines
            # Years are typically 4-digit numbers or ranges like "2017-2020"
            year_pattern = re.compile(r'^(\d{4}(?:\s*-\s*\d{4})?|\d{4}(?:\s*-\s*present)?)$', re.IGNORECASE)

            # Header patterns to skip (tab-separated column headers from source CV tables)
            header_keywords = {'name of award', 'date awarded', 'organization', 'granting body', 'honor', 'year'}

            award_lines = []
            year_lines = []
            for line in lines:
                # Skip lines that look like table column headers
                # e.g., "Name of award\tDate awarded" or "Name of award\tOrganization\tDate awarded"
                line_lower = line.lower().replace('\t', ' ')
                if sum(1 for kw in header_keywords if kw in line_lower) >= 2:
                    continue

                # Handle tab-separated "Award Name\tYear" format
                if '\t' in line:
                    tab_parts = [p.strip() for p in line.split('\t') if p.strip()]
                    if len(tab_parts) >= 2 and year_pattern.match(tab_parts[-1]):
                        # Last tab-field is a year, everything before is the award
                        award_lines.append('\t'.join(tab_parts[:-1]))
                        year_lines.append(tab_parts[-1])
                        continue
                    elif len(tab_parts) == 1:
                        # Tab-prefixed year or award
                        line = tab_parts[0]
                    # else: treat as normal line with tabs stripped
                    else:
                        line = ' '.join(tab_parts)

                # Check if line is just a year
                if year_pattern.match(line):
                    year_lines.append(line)
                # Check for "Award Name | Year" format
                elif '|' in line:
                    parts = line.split('|')
                    award_lines.append(parts[0].strip())
                    if len(parts) > 1 and parts[1].strip():
                        year_lines.append(parts[1].strip())
                else:
                    award_lines.append(line)

            # If we have multiple awards in the text, process each separately
            if len(award_lines) > 1:
                # Determine year ordering: if years are in descending order (most recent first,
                # matching typical reverse-chronological award lists), use forward mapping.
                # If ascending, reverse them to align with descending awards.
                def _extract_first_year(y):
                    m = re.match(r'(\d{4})', y)
                    return int(m.group(1)) if m else 0

                if len(year_lines) >= 2:
                    first_y = _extract_first_year(year_lines[0])
                    last_y = _extract_first_year(year_lines[-1])
                    ordered_years = year_lines if first_y >= last_y else list(reversed(year_lines))
                else:
                    ordered_years = year_lines

                for i, award_text in enumerate(award_lines):
                    # Stage 4 extracted clean fields for (at most) one award of
                    # the fused entry — use them for the line they belong to
                    # instead of re-parsing it from raw text (#229).
                    if award_name and award_name.lower() in award_text.lower():
                        self._add_honors_row(
                            table, award_name,
                            granting_body or self._extract_organization_from_award(award_text),
                            format_date_for_section(date, 'H') if date else '')
                        continue

                    # Try to get corresponding year from ordered list
                    year_for_award = ''
                    if i < len(ordered_years):
                        year_for_award = ordered_years[i]

                    # If no year found from text, extract the inline year
                    # (leading "2020 Award ...", range, or trailing "... 2025.")
                    if not year_for_award:
                        award_text, year_for_award = self._split_award_year(award_text)

                    # Format date
                    if year_for_award:
                        year_for_award = format_date_for_section(year_for_award, 'H')

                    # Extract organization from award text
                    org = self._extract_organization_from_award(award_text)

                    # The org is usually a trailing segment of the raw line —
                    # keep it out of the name cell (#229)
                    award_text = self._strip_org_tail(award_text, org)

                    # Add row
                    self._add_honors_row(table, award_text, org, year_for_award)
            else:
                # Single award - use extracted fields
                if not award_name:
                    award_name = original_text[:150]

                # If no extracted date, parse the inline year out of the name
                # (leading "2021 Award ...", range, or trailing "... 2021.");
                # fall back to the original text for the year alone.
                if not date:
                    award_name, date = self._split_award_year(award_name)
                    if not date:
                        _, date = self._split_award_year(original_text)

                # Format date as yyyy
                if date:
                    date = format_date_for_section(date, 'H')

                # Extract organization if field extraction didn't provide one
                if not granting_body:
                    granting_body = self._extract_organization_from_award(award_name)

                # Same duplication hazard as the multi-award path (#229)
                award_name = self._strip_org_tail(award_name, granting_body)

                self._add_honors_row(table, award_name, granting_body, date)

    def _extract_organization_from_award(self, text: str) -> str:
        """Extract organization name from award/honor text using institutional keyword patterns.

        Uses a multi-strategy approach:
        1. 'from [Organization]' explicit pattern
        2. Comma-separated segments with institutional keywords
        3. 'Association/Society of X' at start of text
        4. Proper noun phrases around institutional keywords anywhere in text

        Returns the organization name, or empty string if none identified.
        """
        if not text:
            return ''

        # Words that are part of award descriptions, not organization names
        STOP = frozenset(['award', 'excellence', 'teaching', 'list', 'recognition',
                          'member', 'elected', 'senior', 'certificate', 'mentoring',
                          'director', 'subinternship', 'housestaff', 'faculty',
                          'resident', 'scholarship', 'honor', 'clinical', 'student'])

        IKW = (r'(?:University|College|Hospital|Medical\s+Center|Society|Association|'
               r'Institute|Academy|Foundation|Program\s+Directors|Center)')

        def _build_org_around_keyword(txt):
            """Find last institutional keyword in text and build org name around it."""
            keywords = list(re.finditer(IKW, txt, re.IGNORECASE))
            if not keywords:
                return ''
            km = keywords[-1]  # Use last keyword to capture full org span

            # Walk backwards from keyword
            before = txt[:km.start()]
            words = before.rstrip().split()
            pre = []
            for w in reversed(words):
                wc = w.strip('.,;\u2013\u2014-()\"\u2019')
                if not wc:
                    # Dash/punctuation-only token - preserve and keep walking
                    pre.insert(0, w.strip())
                    continue
                if wc.lower() in STOP:
                    break
                if wc[0].islower() and wc.lower() not in ('of', 'the', 'and', 'at', 'in', 'for'):
                    break
                pre.insert(0, wc)

            # Walk forward: handle dash-connected institution names
            after = txt[km.end():]
            post = ''
            dm = re.match(r'(\s*[\u2013\u2014-]\s*(?:[A-Z][\w.]+\s+)*?' + IKW + r')', after)
            if dm:
                post = dm.group(1).strip('\u2013\u2014- ').strip()

            parts = pre + [km.group(0)]
            org = ' '.join(parts)
            if post:
                org += ' \u2013 ' + post
            return org.strip('.,; ')

        # Strategy 1: "from [Organization]"
        fm = re.search(r'\bfrom\b\s+(.+)$', text, re.IGNORECASE)
        if fm:
            org = _build_org_around_keyword(fm.group(1))
            if len(org.split()) >= 2:
                return org

        # Strategy 2: comma-separated segments (check last segments first).
        # Two passes: an institutional-keyword segment anywhere beats the
        # short-proper-noun fallback — a single reversed pass used to return
        # "MD" or a bare city before ever reaching the real org (#229).
        if ',' in text:
            segs = [s.strip().rstrip('.,;') for s in text.split(',')]
            for seg in reversed(segs):
                if seg and re.search(IKW, seg, re.IGNORECASE) and len(seg.split()) <= 10:
                    return seg
            for seg in reversed(segs):
                if not seg or seg.upper() in self._US_STATE_ABBREVS \
                        or any(ch.isdigit() for ch in seg):
                    continue
                # Short proper-noun segment (e.g., "Weill Cornell")
                if re.match(r'^[A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,2}$', seg):
                    return seg

        # Strategy 3: "Association/Society of X" at start of text
        m = re.match(
            r'((?:Medical\s+)?' + IKW + r'\s+(?:of|for)\s+(?:the\s+)?(?:State\s+of\s+)?'
            r'[A-Z][\w\s.-]+?)(?:\s+(?:Mentoring|Award|Certificate|Medical\s+Student|Grant))',
            text, re.IGNORECASE
        )
        if m:
            return m.group(1).strip().rstrip('.,;')

        # Strategy 4: institutional keyword anywhere - build org around last match
        org = _build_org_around_keyword(text)
        if len(org.split()) >= 2 and len(org) < len(text) * 0.7:
            return org

        return ''

    def _add_honors_row(self, table, award_name: str, granting_body: str, date: str):
        """Add a single row to the honors table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = award_name or ''
            row.cells[1].text = granting_body or ''
            row.cells[2].text = date or ''
        elif num_cols >= 2:
            row.cells[0].text = award_name or ''
            row.cells[1].text = date or ''
        else:
            row.cells[0].text = f"{award_name} ({date})" if date else award_name

        # Apply font formatting to each cell
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)

        self.stats['entries_inserted'] += 1

    def _fill_memberships(self, entries: List[Dict]):
        """Fill I. PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS section.

        Entries have fields: organization, membership_type, start_date, end_date
        Uses table with columns: Organization, Date (yyyy-yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Memberships ({len(entries)} entries)...")

        # Find the MEMBERSHIPS section
        memberships_idx = self._find_paragraph_with_text("PROFESSIONAL ORGANIZATIONS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("SOCIETY MEMBERSHIPS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("MEMBERSHIPS")
        if memberships_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(memberships_idx)
        if not table:
            # Fall back to finding table with "Organization" header
            table = self._find_table_with_cell_text("Organization")
            if table and "Date" not in table.rows[0].cells[1].text:
                table = None  # Wrong table

        if not table:
            if self.verbose:
                print("  Warning: Could not find memberships table")
            return

        # Clear existing data rows
        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries
            if self._is_table_header_entry(original_text, ['organization', 'membership', 'society', 'date', 'member']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Check if this entry contains multiple memberships (newline-separated)
            # Pattern: "Member\nElected Member | Org1\nOrg2 | date1\ndate2"
            lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            # Detect multi-membership pattern: multiple organization names or membership types
            if len(lines) > 2:
                # Try to parse multiple memberships
                memberships = self._parse_multi_membership_entry(lines)
                if memberships:
                    for mem_type, org, dates in memberships:
                        org_text = f"{mem_type}, {org}" if mem_type and mem_type.lower() not in org.lower() else org
                        self._add_table_row(table, [org_text, dates], entry=entry)
                        self.stats['entries_inserted'] += 1
                    continue

            # Single membership - use extracted fields
            organization = fields.get('organization', '')
            membership_type = fields.get('membership_type', '')
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')

            if not organization:
                organization = original_text[:150]

            # Format: Membership Type, Organization
            if membership_type and membership_type.lower() not in organization.lower():
                org_text = f"{membership_type}, {organization}"
            else:
                org_text = organization

            # Format date range for table column
            date_str = format_date_range(start_date, end_date, 'I') if (start_date or end_date) else ''

            # Add row to table
            self._add_table_row(table, [org_text, date_str], entry=entry)
            self.stats['entries_inserted'] += 1

    def _parse_multi_membership_entry(self, lines: List[str]) -> List[Tuple[str, str, str]]:
        """Parse multiple memberships from merged entry lines.

        Handles patterns like:
        - "Member | Org1 | date1" per line
        - "Member\\nElected Member | Org1\\nOrg2 | date1\\ndate2"

        Returns:
            List of (membership_type, organization, dates) tuples
        """
        memberships = []
        membership_types = []
        organizations = []
        dates = []

        # Common membership type indicators
        membership_keywords = ['member', 'fellow', 'diplomat', 'associate', 'elected', 'honorary']
        date_pattern = re.compile(r'^(\d{1,2}/?\d{0,4}\s*-\s*(?:present|\d{1,2}/?\d{0,4}))$|^(\d{4}\s*-\s*(?:present|\d{4}))$', re.IGNORECASE)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if line has pipe separators (structured format)
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                for part in parts:
                    if not part:
                        continue
                    if any(kw in part.lower() for kw in membership_keywords) and len(part.split()) <= 3:
                        membership_types.append(part)
                    elif date_pattern.match(part) or re.match(r'^\d{1,2}/\d{4}', part):
                        dates.append(part)
                    else:
                        organizations.append(part)
            else:
                # No pipe - classify by content
                if any(kw in line.lower() for kw in membership_keywords) and len(line.split()) <= 3:
                    membership_types.append(line)
                elif date_pattern.match(line) or re.match(r'^\d{1,2}/\d{4}', line):
                    dates.append(line)
                elif len(line) > 5:  # Likely organization name
                    organizations.append(line)

        # Match up memberships - pair organizations with types and dates
        if organizations:
            for i, org in enumerate(organizations):
                mem_type = membership_types[i] if i < len(membership_types) else ''
                date = dates[i] if i < len(dates) else ''
                memberships.append((mem_type, org, date))

        return memberships

    def _fill_teaching(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill K. TEACHING ACTIVITIES section.

        Routes K-codes to their appropriate WCM subsections:
        - K1 (didactic) -> "Didactic teaching"
        - K2 (clinical) -> "Clinical teaching"
        - K3 (administrative) -> "Administrative teaching"
        - K4 (CME) -> "Continuing education and professional education"
        - K5 (community) -> "Other education/outreach activities"

        Entries are inserted as a flat chronological list under each K-code section.
        The WCM template provides the structure; Stage 5c handles per-entry formatting.
        Original CV hierarchy labels are not carried over.
        """
        # Define K-code to section header mapping
        k_section_map = {
            'K1': ('Didactic teaching', ['Didactic teaching', 'Didactic']),
            'K2': ('Clinical teaching', ['Clinical teaching', 'bedside teaching']),
            'K3': ('Administrative teaching', ['Administrative teaching', 'leadership role']),
            'K4': ('Continuing education', ['Continuing education', 'professional education']),
            'K5': ('Other education', ['outreach activities', 'Other education/outreach', 'community education or patient']),
        }

        # Count total entries
        total_entries = sum(len(entries_by_code.get(code, [])) for code in k_section_map.keys())
        if total_entries == 0:
            return

        if self.verbose:
            print(f"Filling Teaching ({total_entries} entries)...")

        # Fill each K-code section separately
        for code, (section_name, search_texts) in k_section_map.items():
            entries = entries_by_code.get(code, [])
            if not entries:
                continue

            # Find the appropriate section header
            section_idx = None
            for search_text in search_texts:
                section_idx = self._find_paragraph_with_text(search_text)
                if section_idx is not None:
                    break

            if section_idx is None:
                # Fall back to general teaching section
                section_idx = self._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
                if section_idx is None:
                    continue

            # Flat list: sort chronologically and insert without original CV sub-headers.
            # The WCM template's own K-code sections (Didactic, Clinical, Administrative,
            # CME, Community) provide the structure; Stage 5c handles per-entry formatting.
            sorted_entries = sort_entries_reverse_chronological(entries)
            reversed_entries = list(reversed(sorted_entries))

            for i, entry in enumerate(reversed_entries):
                is_first_in_section = (i == len(reversed_entries) - 1)
                self._insert_teaching_entry(section_idx + 1, entry,
                                            is_first_visible=is_first_in_section)

    def _insert_teaching_entry(self, insert_idx: int, entry: Dict, is_first_visible: bool = False):
        """Insert a single teaching entry as a bulleted item.

        Handles Stage 5c formatted text (with track changes), structured fields,
        and raw text fallback.
        """
        # Skip structural labels from source CV
        if self._is_structural_label(entry):
            return

        fields = entry.get('extracted_fields', {}) or {}
        formatted_text = fields.get('formatted_text', '')
        original_text = entry.get('text', '')

        if self._is_orphan_fragment(fields, formatted_text, original_text):
            return

        # Normalize any raw ISO dates the LLM left in formatted text
        if formatted_text:
            formatted_text = normalize_iso_dates_in_text(formatted_text)

        if formatted_text and original_text:
            # Check if original has multiple distinct items (newline-separated list)
            original_lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            if len(original_lines) > 1:
                # Multi-item entry: use original lines (Stage 5c may have over-combined)
                # Insert in reverse order since we're inserting before insert_idx
                for j, line_text in enumerate(reversed(original_lines)):
                    if not line_text:
                        continue
                    add_blank = is_first_visible and (j == len(original_lines) - 1)
                    self._insert_bulleted_entry(
                        insert_idx, line_text, entry if j == 0 else None,
                        add_blank_before=add_blank, list_level=0
                    )
            else:
                # Single item: use formatted_text
                new_text = self._strip_markdown_for_word(formatted_text, preserve_newlines=True)
                self._insert_bulleted_entry(
                    insert_idx, new_text, entry,
                    add_blank_before=is_first_visible, list_level=0
                )

        elif formatted_text:
            new_text = self._strip_markdown_for_word(formatted_text, preserve_newlines=True)
            lines = [l.strip() for l in new_text.split('\n') if l.strip()]
            combined_text = '. '.join(lines) if len(lines) > 1 else (lines[0] if lines else '')
            self._insert_bulleted_entry(
                insert_idx, combined_text, entry,
                add_blank_before=is_first_visible, list_level=0
            )

        else:
            # No Stage 5c formatting - fall back to building text from fields
            course_code = fields.get('course_code', '')
            course_title = fields.get('course_title', '')
            institution = fields.get('institution', '')
            role = fields.get('role', '')

            if isinstance(course_title, list):
                course_title = '; '.join(course_title)
            if isinstance(course_code, list):
                course_code = '; '.join(course_code)

            if course_code and course_title:
                text = f"{course_code}: {course_title}"
                if institution and institution not in text:
                    text += f", {institution}"
                if role and role not in text:
                    text += f" ({role})"
                self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=is_first_visible, list_level=0)
            elif course_title:
                text = course_title
                if institution and institution not in text:
                    text += f", {institution}"
                if role and role not in text:
                    text += f" ({role})"
                self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=is_first_visible, list_level=0)
            else:
                lines = []
                for line in original_text.split('\n'):
                    line = line.strip()
                    if not line or line.lower() in ['title', 'institution', 'dates', 'role']:
                        continue
                    if ';' in line and len(line) > 100:
                        lines.extend([item.strip() for item in line.split(';') if item.strip()])
                    else:
                        lines.append(line)

                for j, line_text in enumerate(reversed(lines)):
                    if not line_text:
                        continue
                    add_blank = is_first_visible and (j == len(lines) - 1)
                    self._insert_bulleted_entry(insert_idx, line_text, entry if j == 0 else None, add_blank_before=add_blank, list_level=0)

    def _fill_service(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill Q. EXTRAMURAL PROFESSIONAL RESPONSIBILITIES sections using tables.

        WCM template structure:
        - Leadership in Extramural Organizations: Table with Organization, Role, Dates
        - Service on Boards: Tables for Regional/National/International with Committee, Role, Org, Dates
        - Grant Reviewing/Study Sections: Table with Role, Organization, Dates
        - Editorial Activities: Multiple tables for Editor, Editorial Board, Journal Reviewing
        """
        # Collect all Q entries
        q_entries = []
        for code in ['Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D']:
            q_entries.extend(entries_by_code.get(code, []))

        if not q_entries:
            return

        if self.verbose:
            print(f"Filling Service Activities ({len(q_entries)} entries)...")

        # Reroute Q2 entries that are actually journal reviewing to Q4D
        # This handles misclassified entries where "Reviewer" role for a journal was coded as Q2
        # Also handles multi-line entries that contain mixed activities
        q2_entries = list(entries_by_code.get('Q2', []))
        q4d_entries = list(entries_by_code.get('Q4D', []))

        journal_keywords = ['journal', 'j.', 'j ', 'pediatrics', 'lancet', 'jama',
                           'perinatology', 'neonatology', 'oncology', 'cardiology', 'neurology',
                           'editorial board', 'ad hoc reviewer', 'manuscript review']
        reviewer_patterns = ['abstract reviewer', 'reviewer for', 'manuscript reviewer',
                            'peer reviewer', 'ad hoc reviewer']
        board_keywords = ['committee', 'board member', 'panel member', 'council', 'task force',
                         'working group', 'planning committee', 'advisory', 'moderator']

        rerouted_to_journal = []
        actual_board_entries = []

        for entry in q2_entries:
            text = entry.get('text', '')
            text_lower = text.lower()
            fields = entry.get('extracted_fields', {}) or {}
            role = (fields.get('role', '') or '').lower()
            committee = (fields.get('committee_name', '') or '').lower()
            org = (fields.get('organization', '') or '').lower()

            # Check if this is a multi-line entry with mixed activities
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            if len(lines) > 1:
                # Split into journal reviewing and board entries
                journal_lines = []
                board_lines = []

                for line in lines:
                    line_lower = line.lower()
                    is_reviewer_line = any(p in line_lower for p in reviewer_patterns)
                    is_board_line = any(kw in line_lower for kw in board_keywords)

                    if is_reviewer_line and not is_board_line:
                        journal_lines.append(line)
                    else:
                        board_lines.append(line)

                # Create separate entries for journal reviewing lines
                for jline in journal_lines:
                    new_entry = {
                        'text': jline,
                        'taxonomy_code': 'Q4D',
                        'rerouted_from_q2': True,
                        'extracted_fields': {'organization': jline}
                    }
                    rerouted_to_journal.append(new_entry)

                # Keep remaining lines as board entry (if any)
                if board_lines:
                    # Update the original entry to only contain board lines
                    entry['text'] = '\n'.join(board_lines)
                    actual_board_entries.append(entry)

            else:
                # Single-line entry - classify based on content
                is_journal_reviewer = (
                    (role == 'reviewer' and any(kw in text_lower for kw in journal_keywords[:10])) or
                    any(kw in text_lower for kw in ['editorial board', 'ad hoc reviewer', 'manuscript review']) or
                    any(p in text_lower for p in reviewer_patterns) or
                    (role == 'reviewer' and 'j ' in committee) or
                    (role == 'reviewer' and 'journal' in org)
                )

                # Check if this is clearly a board/committee entry
                is_board_entry = any(kw in text_lower for kw in board_keywords)

                if is_journal_reviewer and not is_board_entry:
                    # This looks like journal reviewing, reroute to Q4D
                    entry['taxonomy_code'] = 'Q4D'
                    entry['rerouted_from_q2'] = True
                    rerouted_to_journal.append(entry)
                else:
                    actual_board_entries.append(entry)

        if rerouted_to_journal and self.verbose:
            print(f"  Rerouted {len(rerouted_to_journal)} Q2 entries/lines to Journal Reviewing")

        q4d_entries.extend(rerouted_to_journal)
        q2_entries = actual_board_entries

        # Q2 entries go to "Service on Boards and/or Committees" - use National table by default
        if q2_entries:
            self._fill_service_boards(q2_entries)

        # Q4D entries go to "Journal Reviewing/Ad hoc Reviewing" table
        if q4d_entries:
            self._fill_journal_reviewing(q4d_entries)

        # Q4C and other Q4 entries (not Q4D) go to a general service table or bullet list
        other_q_entries = []
        for code in ['Q1', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C']:
            other_q_entries.extend(entries_by_code.get(code, []))

        if other_q_entries:
            self._fill_other_service(other_q_entries)

    def _fill_service_boards(self, entries: List[Dict]):
        """Fill Service on Boards and/or Committees tables.

        WCM template has tables for Regional/National/International.
        Uses cv_owner_location to classify geographic scope of each entry.
        Table structure: Name of Committee | Role | Organization | Dates
        """
        if not entries:
            return

        # Find the Service on Boards section
        service_idx = self._find_paragraph_with_text("Service on Boards")
        if service_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find section for Service on Boards")
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            scope_idx = None
            for i in range(service_idx, min(service_idx + 30, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope:
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(service_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            if self.verbose:
                print(f"  Warning: Could not find any table for Service on Boards")
            return

        # Clear tables and mark as populated
        for scope, table in tables_by_scope.items():
            self._clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

        # Classify and route entries by geographic scope
        entries_by_scope = {'Regional': [], 'National': [], 'International': []}
        for entry in entries:
            scope = self._classify_geographic_scope(entry)
            entries_by_scope[scope].append(entry)

        if self.verbose and self.cv_owner_location:
            regional_count = len(entries_by_scope['Regional'])
            national_count = len(entries_by_scope['National'])
            intl_count = len(entries_by_scope['International'])
            print(f"  Service on Boards: {regional_count} Regional, {national_count} National, {intl_count} International")

        # Fill each table with its entries
        for scope, scope_entries in entries_by_scope.items():
            if not scope_entries:
                continue

            # Find the table for this scope (fall back to National)
            table = tables_by_scope.get(scope) or tables_by_scope.get('National')
            if not table:
                continue

            sorted_entries = sort_entries_reverse_chronological(scope_entries)

            for entry in sorted_entries:
                fields = entry.get('extracted_fields', {}) or {}
                taxonomy_code = entry.get('taxonomy_code', 'Q2')

                committee = fields.get('committee_name') or ''
                role = fields.get('role') or 'Member'
                organization = fields.get('organization') or ''

                # When Stage 4 merges committee name into the role field
                # (e.g., role="Chair, Ultrasound Committee"), split them apart
                if not committee and ', ' in role:
                    role_parts = role.split(', ', 1)
                    role_word = role_parts[0].strip().lower()
                    # Only split if the first part looks like a role title
                    if role_word in ('chair', 'co-chair', 'deputy chair', 'vice chair',
                                     'member', 'secretary', 'treasurer', 'president',
                                     'vice president', 'director', 'advisor', 'liaison',
                                     'representative', 'reviewer', 'editor', 'delegate'):
                        role = role_parts[0].strip()
                        committee = role_parts[1].strip()

                # If still no committee, fall back to organization
                if not committee:
                    committee = organization
                    organization = ''
                start_date = fields.get('start_date') or ''
                end_date = fields.get('end_date') or ''
                dates = format_date_range(start_date, end_date, taxonomy_code) or ''

                # If we don't have structured fields, parse from raw text
                if not committee:
                    text = entry.get('text', '')[:150]
                    committee = text

                # Add row to table
                row = table.add_row()
                num_cols = len(row.cells)

                # Populate based on number of columns
                # WCM template has 4 columns: Committee, Role, Organization, Dates
                if num_cols >= 4:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = organization or ''
                    row.cells[3].text = dates or ''
                elif num_cols >= 3:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = dates or ''
                else:
                    row.cells[0].text = f"{committee} ({role})" if role else (committee or '')
                    if num_cols > 1:
                        row.cells[1].text = dates or ''

                # Apply font formatting to each cell
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            self._set_font(run)
                self.stats['entries_inserted'] += 1

    def _fill_extramural_leadership(self, entries: List[Dict]):
        """Fill Leadership in Extramural Organizations table (Q1 entries).

        Table structure: Organization | Role | Dates
        Handles multi-line entries that need splitting.
        """
        if not entries:
            return

        # Find Leadership in Extramural Organizations section
        section_idx = self._find_paragraph_with_text("Leadership in Extramural Organizations")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Leadership in Extramural")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1


        for entry in entries:
            original_text = entry.get('text', '')
            fields = entry.get('extracted_fields', {}) or {}

            # Check if extracted_fields has valid data - prefer using LLM extraction over raw parsing
            organization = fields.get('organization', '')
            role = fields.get('role', '')
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')

            # If we have at least organization or role from extraction, use that
            # The LLM extraction is more reliable than trying to parse garbled table text
            if organization or role:
                dates = format_date_range(start_date, end_date, 'Q1')
                if not organization:
                    organization = original_text[:100]
                self._add_extramural_row(table, organization, role, dates)
            else:
                # No useful extracted fields - try to parse from raw text
                lines = [l.strip() for l in original_text.split('\n') if l.strip()]
                if len(lines) > 3:
                    # Multiple items merged - parse and split them
                    self._parse_extramural_leadership_lines(table, lines)
                else:
                    # Single entry without extracted fields - use raw text
                    self._add_extramural_row(table, original_text[:100], '', '')

    def _parse_extramural_leadership_lines(self, table, lines: List[str]):
        """Parse multiple extramural leadership lines and add rows.

        Handles complex patterns like:
        - Organization name followed by indented roles
        - Date ranges at end of lines or in separate date block
        - Two-column table extractions where roles and dates are in separate columns
        """

        # Patterns for date detection
        year_only_pattern = re.compile(r'^(\d{4})\s*$')
        date_range_pattern = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?(?:\s*,\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)*)$', re.IGNORECASE)
        embedded_date_pattern = re.compile(r'\|\s*(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)\s*$', re.IGNORECASE)
        trailing_date_pattern = re.compile(r'(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)\s*$', re.IGNORECASE)

        # Role indicators
        role_keywords = ['member', 'chair', 'reviewer', 'liaison', 'mentor', 'committee',
                         'board', 'council', 'advisor', 'director', 'leader', 'representative']

        # Known organizations for context
        known_orgs = [
            'Association of Pediatric Program Directors', 'APPD',
            'American Academy of Pediatrics', 'AAP',
            'Academic Pediatric Association', 'APA',
            'Pediatric Academic Society', 'PAS',
            'National Board of Medical Examiners', 'NBME',
            'American Medical Association', 'AMA',
            'American Board of Pediatrics', 'ABP',
            'ACGME', 'Lenox Hill', 'American College',
            'Society', 'Association', 'Academy', 'Board', 'Institute'
        ]

        # First pass: categorize each line
        content_lines = []  # (text, embedded_date, is_org, is_role, original_idx)
        date_only_lines = []  # standalone dates

        for idx, line in enumerate(lines):
            line = line.strip()
            if not line or line.lower() in ['dates', 'organization', 'role', 'position']:
                continue

            # Check for date-only line (possibly multiple dates on one line)
            if date_range_pattern.match(line):
                # Split if multiple dates separated by newlines within the line
                date_parts = re.split(r'\s*\n\s*', line)
                for dp in date_parts:
                    dp = dp.strip()
                    if dp:
                        date_only_lines.append(dp)
                continue

            # Check for pipe-separated format: "Role | Date" or "Org | Role | Date"
            embedded_date = ''
            embedded_match = embedded_date_pattern.search(line)
            if embedded_match:
                embedded_date = embedded_match.group(1)
                line = line[:embedded_match.start()].strip().rstrip('|').strip()

            # Determine if this is an organization or a role
            line_lower = line.lower()
            is_org = any(org.lower() in line_lower for org in known_orgs)
            is_role = any(kw in line_lower for kw in role_keywords) and not is_org

            # Indented lines are usually sub-items (roles under an org)
            is_indented = lines[idx].startswith('   ') or lines[idx].startswith('\t')
            if is_indented:
                is_role = True
                is_org = False

            content_lines.append((line, embedded_date, is_org, is_role, idx))

        # Second pass: build items with org-role pairing
        items = []
        current_org = None

        for line, embedded_date, is_org, is_role, _ in content_lines:
            if is_org:
                current_org = line
                # If org has embedded date, it's a standalone membership
                if embedded_date:
                    items.append((current_org, 'Member', embedded_date))
                else:
                    # Just setting context, will get roles below
                    pass
            elif is_role and current_org:
                # Role under current organization
                items.append((current_org, line, embedded_date))
            elif is_role:
                # Standalone role (no org context)
                items.append((line, '', embedded_date))
            else:
                # Generic content - could be org or description
                if len(line) > 50:  # Long text is probably a description
                    items.append((line, '', embedded_date))
                else:
                    current_org = line
                    if embedded_date:
                        items.append((current_org, '', embedded_date))

        # Third pass: match date-only lines to items without dates
        # Strategy: try to match dates in order they appear
        items_needing_dates = [(i, item) for i, item in enumerate(items) if not item[2]]

        if date_only_lines and items_needing_dates:
            # If roughly equal counts, match 1:1 in order
            if abs(len(date_only_lines) - len(items_needing_dates)) <= 2:
                for (item_idx, _), date in zip(items_needing_dates, date_only_lines):
                    org, role, _ = items[item_idx]
                    items[item_idx] = (org, role, date)
            else:
                # More dates than items or vice versa - match from top
                for i, (item_idx, _) in enumerate(items_needing_dates):
                    if i < len(date_only_lines):
                        org, role, _ = items[item_idx]
                        items[item_idx] = (org, role, date_only_lines[i])

        # Add rows for each item
        for org, role, date in items:
            # Skip items with no meaningful content
            if not org and not role:
                continue
            self._add_extramural_row(table, org, role, date)

    def _add_extramural_row(self, table, organization: str, role: str, dates: str):
        """Add a single row to extramural leadership table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = organization or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            combined = f"{organization} - {role}" if role else organization
            row.cells[0].text = combined or ''
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{organization} ({role}) {dates}".strip()

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)
        self.stats['entries_inserted'] += 1

    def _fill_journal_reviewing(self, entries: List[Dict]):
        """Fill Journal Reviewing/Ad hoc Reviewing table.

        Table structure: Journal / Organization Name | Dates
        """
        if not entries:
            return

        # Filter out header-like entries (e.g., just "Reviewer", "Journal", etc.)
        header_patterns = ['reviewer', 'journal', 'ad hoc', 'editorial', 'organization']
        filtered_entries = []
        for entry in entries:
            text = entry.get('text', '').strip().lower()
            # Skip if it's a single word that looks like a header
            if text and len(text.split()) <= 2 and any(text == h for h in header_patterns):
                continue
            filtered_entries.append(entry)

        if not filtered_entries:
            return

        # Find the Journal Reviewing section
        section_idx = self._find_paragraph_with_text("Journal Reviewing")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Ad hoc Reviewing")

        if section_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find Journal Reviewing section")
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            if self.verbose:
                print(f"  Warning: Could not find Journal Reviewing table")
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(filtered_entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'Q4D')

            journal = fields.get('journal_name', '') or fields.get('organization', '') or fields.get('committee_name', '')
            start_date = fields.get('start_date', '') or fields.get('year', '')
            end_date = fields.get('end_date', '')
            dates = format_date_range(start_date, end_date, taxonomy_code)

            if not journal:
                # Parse from raw text, but clean up common patterns
                raw_text = entry.get('text', '')[:100]
                # Remove "Reviewer" prefix if present
                journal = re.sub(r'^(?:Reviewer|Ad hoc Reviewer)[,:\s]*', '', raw_text, flags=re.IGNORECASE).strip()

            # Skip if still empty or too short
            if not journal or len(journal.strip()) < 3:
                continue

            row = table.add_row()
            row.cells[0].text = journal
            if len(row.cells) > 1:
                row.cells[1].text = dates

            # Apply font formatting to each cell
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        self._set_font(run)
            self.stats['entries_inserted'] += 1

    def _fill_other_service(self, entries: List[Dict]):
        """Fill other Q entries that don't have specific tables.

        Uses bullet list format under appropriate section headers.
        """
        if not entries:
            return

        # Handle Q1 separately - it goes to Leadership in Extramural Organizations
        q1_entries = [e for e in entries if e.get('taxonomy_code') == 'Q1']
        if q1_entries:
            self._fill_extramural_leadership(q1_entries)

        # Filter out Q1 from remaining entries
        entries = [e for e in entries if e.get('taxonomy_code') != 'Q1']
        if not entries:
            return

        # Group by section
        # Q4B = Associate/Guest Editor roles, Q4C = Editorial Board Member
        # These should go to Editorial Activities section, not generic Professional Service
        sections = {
            'Q3': ('Grant Reviewing', ['Grant Reviewing', 'Study Sections']),
            'Q4': ('Professional Service', ['EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', 'Leadership in Extramural']),
            'Q4A': ('Professional Service', ['EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', 'Leadership in Extramural']),
            'Q4B': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
            'Q4C': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
        }

        entries_by_section = {}
        for entry in entries:
            code = entry.get('taxonomy_code', 'Q4')
            if code in sections:
                section_name = sections[code][0]
                if section_name not in entries_by_section:
                    entries_by_section[section_name] = {'entries': [], 'search_texts': sections[code][1]}
                entries_by_section[section_name]['entries'].append(entry)

        for section_name, data in entries_by_section.items():
            section_entries = data['entries']
            search_texts = data['search_texts']

            section_idx = None
            for search_text in search_texts:
                section_idx = self._find_paragraph_with_text(search_text)
                if section_idx is not None:
                    break

            if section_idx is None:
                continue

            # Try to find and use a table first
            table = self._find_table_after_paragraph(section_idx)
            if table:
                self._clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                sorted_entries = sort_entries_reverse_chronological(section_entries)
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    taxonomy_code = entry.get('taxonomy_code', 'Q4')

                    role = fields.get('role', '')
                    # Q3 uses 'agency', others use 'organization' or 'committee_name'
                    # Q4B/Q4C (editorial) use 'journal_name'
                    organization = (fields.get('organization', '') or
                                    fields.get('committee_name', '') or
                                    fields.get('agency', '') or
                                    fields.get('journal_name', ''))
                    start_date = fields.get('start_date', '')
                    end_date = fields.get('end_date', '')
                    dates = format_date_range(start_date, end_date, taxonomy_code)

                    # For Q4B/Q4C entries, try to parse role from raw text if missing
                    if not role and taxonomy_code in ['Q4B', 'Q4C']:
                        raw_text = entry.get('text', '')
                        # Pattern: "date | role | journal" or "role | journal"
                        # Match various editor/board roles
                        role_match = re.search(
                            r'\|\s*((?:Associate|Guest|Deputy|Senior|Managing|Executive|Review|Reviewing)\s*'
                            r'(?:Editor|editorial\s*board\s*member)|Editorial\s*(?:Board|board)\s*(?:Member|member)?)\s*\|',
                            raw_text, re.IGNORECASE
                        )
                        if role_match:
                            role = role_match.group(1).strip()
                        elif 'Editor' in raw_text or 'editor' in raw_text or 'Editorial' in raw_text:
                            # Try to extract role another way - check specific patterns
                            if 'Associate Editor' in raw_text:
                                role = 'Associate Editor'
                            elif 'Guest Editor' in raw_text:
                                role = 'Guest Editor'
                            elif 'Review editorial board member' in raw_text.lower():
                                role = 'Review Editorial Board Member'
                            elif 'Editorial board member' in raw_text.lower():
                                role = 'Editorial Board Member'
                            elif 'Editorial board' in raw_text.lower():
                                role = 'Editorial Board'

                    if not organization:
                        # Parse from raw text as fallback, but strip date prefix
                        raw_text = entry.get('text', '')
                        # Remove common date patterns from beginning
                        organization = re.sub(r'^\d{4}[-–]?\d{0,4}\s*\|?\s*', '', raw_text)[:100]
                        # If role already contains most of the organization text, don't duplicate
                        if role and organization and role.lower()[:30] in organization.lower():
                            organization = ''

                    row = table.add_row()
                    num_cols = len(row.cells)
                    if num_cols >= 3:
                        row.cells[0].text = role or ''
                        row.cells[1].text = organization or ''
                        row.cells[2].text = dates or ''
                    elif num_cols >= 2:
                        # Avoid duplicating content when role already contains full description
                        if role and organization and role.lower()[:20] in organization.lower():
                            row.cells[0].text = role
                        elif role and organization:
                            row.cells[0].text = f"{role}, {organization}"
                        else:
                            row.cells[0].text = role or organization
                        row.cells[1].text = dates
                    else:
                        row.cells[0].text = f"{organization} ({dates})" if dates else organization

                    # Apply font formatting to each cell
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                self._set_font(run)
                    self.stats['entries_inserted'] += 1

    def _fill_licensure(self, entries: List[Dict]):
        """Fill F1. LICENSURE section.

        WCM template has table with: State | Number | Date of issue | Date of last registration
        Also fills DEA and NPI numbers in a separate table (Table 9).
        """

        if not entries:
            return

        if self.verbose:
            print(f"Filling Licensure ({len(entries)} entries)...")

        # Find Licensure section
        section_idx = self._find_paragraph_with_text("Licensure")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("LICENSURE")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        # Track NPI and DEA numbers to fill separately
        npi_number = None
        dea_number = None
        regular_licenses = []

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            state = fields.get('state_country') or fields.get('state') or fields.get('jurisdiction') or ''
            license_number = fields.get('license_number') or fields.get('number') or ''
            issue_date = fields.get('issue_date') or fields.get('date') or ''
            expiration_date = fields.get('expiration_date') or ''

            # Detect NPI number: 10 digits, or text mentions "NPI"
            if license_number and (re.match(r'^\d{10,11}$', license_number) or
                                   'NPI' in original_text.upper()):
                npi_number = license_number
                continue

            # Detect DEA number: 2 letters + 7 alphanumeric, or text mentions "DEA"
            if license_number and (re.match(r'^[A-Za-z]{2}[A-Za-z0-9]{7}$', license_number) or
                                   'DEA' in original_text.upper()):
                dea_number = license_number
                continue

            # Regular license entry - format dates as mm/dd/yyyy per WCM template
            if state or license_number:
                regular_licenses.append({
                    'state': state,
                    'license_number': license_number,
                    'issue_date': format_date_for_section(issue_date, 'F1') if issue_date else '',
                    'expiration_date': format_date_for_section(expiration_date, 'F1') if expiration_date else ''
                })
            elif original_text and not any(kw in original_text.upper() for kw in ['NPI', 'DEA']):
                # Fallback to raw text for unstructured entries
                regular_licenses.append({
                    'state': original_text[:100],
                    'license_number': '',
                    'issue_date': '',
                    'expiration_date': ''
                })

        # Fill regular licenses table
        for lic in regular_licenses:
            row = table.add_row()
            num_cols = len(row.cells)
            if num_cols >= 4:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''
                row.cells[2].text = lic['issue_date'] or ''
                row.cells[3].text = lic['expiration_date'] or ''
            elif num_cols >= 2:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''

            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        self._set_font(run)
            self.stats['entries_inserted'] += 1

        # Fill DEA/NPI table (Table 9 in template)
        self._fill_dea_npi(dea_number, npi_number)

    def _fill_dea_npi(self, dea_number: str, npi_number: str):
        """Fill DEA and NPI numbers in their dedicated table.

        The WCM template has a 2-row table:
        Row 0: DEA number: (optional) | [value]
        Row 1: NPI number: (optional) | [value]
        """
        if not dea_number and not npi_number:
            return

        # Find the DEA/NPI table by looking for a table containing "DEA number"
        dea_npi_table = None
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if 'DEA number' in cell.text:
                        dea_npi_table = table
                        break
                if dea_npi_table:
                    break
            if dea_npi_table:
                break

        if not dea_npi_table:
            return

        # Fill in the values
        for row in dea_npi_table.rows:
            if len(row.cells) >= 2:
                label = row.cells[0].text.lower()
                if 'dea' in label and dea_number:
                    row.cells[1].text = dea_number
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            self._set_font(run)
                elif 'npi' in label and npi_number:
                    row.cells[1].text = npi_number
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            self._set_font(run)

    def _fill_board_certification(self, entries: List[Dict]):
        """Fill F2. BOARD CERTIFICATION section.

        WCM template has table with: Name of specialty | Board Certificate # | Date of Certification

        Handles cases where multiple certifications are merged into one entry.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Board Certification ({len(entries)} entries)...")

        # Find Board Certification section - need to find the subsection header,
        # not the main "LICENSURE, BOARD CERTIFICATION" section header
        section_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            # Look for exact match or starts with "Board Certification"
            if text == "Board Certification" or text.startswith("Board Certification:"):
                section_idx = i
                break
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            certifying_board = fields.get('certifying_board', '')
            certificate_number = fields.get('certificate_number', '')
            year_certified = fields.get('year_certified', '')
            recertification_date = fields.get('recertification_date', '')

            # Handle certificate_number being a list (from LLM extraction) or string
            if isinstance(certificate_number, list):
                cert_numbers = [str(n).strip() for n in certificate_number if n]
                certificate_number = ', '.join(cert_numbers)  # Also update for single cert case
            else:
                certificate_number = str(certificate_number) if certificate_number else ''

            # Check if we have structured fields
            if certifying_board or certificate_number:
                # Check if multiple certifications were merged (multiple cert numbers)
                cert_numbers = []
                if certificate_number:
                    # Split on semicolons or commas
                    cert_numbers = [n.strip() for n in certificate_number.replace(';', ',').split(',') if n.strip()]

                if len(cert_numbers) > 1:
                    # Multiple certifications merged - try to parse from original text
                    # Original text pattern: "Specialty1\n\nSpecialty2 | CertNum1\n\nCertNum2 | Year1\nYear2"
                    self._parse_and_add_multiple_certifications(table, original_text, entry)
                else:
                    # Single certification - format dates as yyyy-yyyy per WCM template
                    # Use start_date/end_date if available, fall back to year_certified/recertification_date
                    start_date = fields.get('start_date') or year_certified
                    end_date = fields.get('end_date') or recertification_date

                    start_fmt = format_date_for_section(str(start_date), 'F2') if start_date else ''
                    end_fmt = format_date_for_section(str(end_date), 'F2') if end_date else ''

                    # Build date range string
                    if start_fmt and end_fmt:
                        if end_fmt.lower() == 'present':
                            date_str = f"{start_fmt}-Present"
                        elif end_fmt != start_fmt:
                            date_str = f"{start_fmt}-{end_fmt}"
                        else:
                            date_str = start_fmt
                    elif start_fmt:
                        date_str = start_fmt
                    elif end_fmt:
                        date_str = end_fmt
                    else:
                        date_str = ''

                    self._add_board_cert_row(table, certifying_board, certificate_number, date_str)
            else:
                # No structured fields - try to parse from text
                self._parse_and_add_multiple_certifications(table, original_text, entry)

    def _parse_and_add_multiple_certifications(self, table, text: str, entry: Dict):
        """Parse multiple board certifications from raw text and add rows."""
        if not text:
            return


        # Handle pipe-separated format: "Specialty | CertNum | Year"
        # First, split on newlines and filter empty lines
        lines = [line.strip() for line in text.split('\n') if line.strip()]

        # Skip header lines
        header_keywords = ['name of specialty', 'board certificate', 'date of certification']
        lines = [l for l in lines if not any(kw in l.lower() for kw in header_keywords)]

        if not lines:
            return

        # Try to parse pipe-separated rows first
        # Format might be: "Specialty | CertNum | Year" on each line
        # Or mixed format from extraction artifacts

        specialties = []
        cert_numbers = []
        years = []

        for line in lines:
            # Check if line contains pipe separator
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                for part in parts:
                    if not part:
                        continue
                    # Classify each part
                    if re.match(r'^\d{4}$', part):
                        years.append(part)
                    elif re.match(r'^[\d\-]+$', part):
                        cert_numbers.append(part)
                    elif 'MOC' in part:
                        years.append(part)
                    elif not re.match(r'^\d', part):
                        # Doesn't start with digit - likely specialty
                        specialties.append(part)
            else:
                # No pipe - classify the whole line
                line = line.strip()
                if re.match(r'^\d{4}$', line):
                    years.append(line)
                elif re.match(r'^[\d\-]+$', line):
                    cert_numbers.append(line)
                elif 'MOC' in line:
                    years.append(line)
                elif not re.match(r'^\d', line) and line:
                    specialties.append(line)

        # Match specialties with cert numbers (assume same order)
        num_certs = max(len(specialties), len(cert_numbers), 1)
        for i in range(num_certs):
            specialty = specialties[i] if i < len(specialties) else ''
            cert_num = cert_numbers[i] if i < len(cert_numbers) else ''
            # For years, try to match or use available
            year = ''
            if i < len(years):
                year = years[i]
            elif years:
                # Use last year if we have fewer years than certs
                year = years[-1] if i >= len(years) else years[i]

            # Format year as yyyy per WCM template
            if year:
                year = format_date_for_section(year, 'F2')

            if specialty or cert_num:
                self._add_board_cert_row(table, specialty, cert_num, year)

    def _add_board_cert_row(self, table, specialty: str, cert_number: str, dates: str):
        """Add a single board certification row to the table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = specialty or ''
            row.cells[1].text = cert_number or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = specialty or ''
            row.cells[1].text = f"{cert_number} ({dates})" if dates else (cert_number or '')

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)
        self.stats['entries_inserted'] += 1

    def _fill_clinical_practice(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill L. CLINICAL PRACTICE, INNOVATION, and LEADERSHIP section.

        This section has three subsections:
        - L1: Clinical Practice (patient care activities)
        - L2: Clinical Innovations (new approaches to care)
        - L3: Clinical Leadership (director/head roles)

        Each subsection uses a simple bulleted or table format.
        """
        l1_entries = entries_by_code.get('L1', [])
        l2_entries = entries_by_code.get('L2', [])
        l3_entries = entries_by_code.get('L3', [])

        total = len(l1_entries) + len(l2_entries) + len(l3_entries)
        if total == 0:
            return

        if self.verbose:
            print(f"Filling Clinical Practice ({len(l1_entries)} L1, {len(l2_entries)} L2, {len(l3_entries)} L3)...")

        # L1: Clinical Practice
        if l1_entries:
            # Find the "Clinical Practice" subsection (not the main "CLINICAL PRACTICE, INNOVATION..." header)
            # Use exact match first, then fall back to contains match
            section_idx = self._find_paragraph_exact("Clinical Practice")
            if section_idx is None:
                # Try to find a paragraph that starts with "Clinical Practice" but not the full section header
                for i, para in enumerate(self.doc.paragraphs):
                    text = para.text.strip()
                    if text == "Clinical Practice" or (
                        text.lower().startswith("clinical practice") and
                        "innovation" not in text.lower() and
                        "leadership" not in text.lower()
                    ):
                        section_idx = i
                        break

            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate table is actually for Clinical Practice, not a different section
                # Check if first cell contains "Award Source" which indicates a grant table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    # Grant tables have "Award Source", not clinical practice tables
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical practice)")

                sorted_entries = sort_entries_reverse_chronological(l1_entries)

                if table and table_is_valid:
                    self._clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1
                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        # Extract fields - clinical practice entries typically have:
                        # activity/location, institution, dates
                        activity = fields.get('activity') or fields.get('role') or fields.get('title') or ''
                        location = fields.get('location') or fields.get('institution') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L1') or ''

                        # Fallback to parsing original text if fields are empty
                        if not activity and original_text:
                            # Try to parse "Activity | Location | Dates" format
                            parts = original_text.split('|')
                            if len(parts) >= 2:
                                activity = parts[0].strip()
                                if len(parts) >= 3:
                                    dates = parts[-1].strip() if not dates else dates

                        if activity or location:
                            row = table.add_row()
                            # Typical clinical practice table: Activity/Type | Location | Dates
                            if len(row.cells) >= 3:
                                row.cells[0].text = activity
                                row.cells[1].text = location
                                row.cells[2].text = dates
                            elif len(row.cells) >= 2:
                                row.cells[0].text = f"{activity}" if activity else location
                                row.cells[1].text = dates
                            else:
                                row.cells[0].text = f"{activity} - {location} ({dates})" if dates else f"{activity} - {location}"

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        self._set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found - insert as bullet points after section header
                    if self.verbose:
                        print(f"  No Clinical Practice table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        original_text = entry.get('text', '').strip()

                        # Skip entries that are structural labels from the source CV
                        if self._is_structural_label(entry):
                            continue

                        # L1 clinical practice entries are narrative summaries —
                        # use the full original text rather than just the extracted
                        # clinical_role label, which loses the descriptive detail.
                        # Clean up tab-delimited format from source CV.
                        bullet_text = original_text.replace('\t', ' — ', 1).replace('\t', ' ') if '\t' in original_text else original_text

                        if bullet_text:
                            # Use multiline helper to properly split entries with multiple lines
                            inserted = self._insert_multiline_as_bullets(
                                section_idx + 1 + bullet_count, bullet_text, entry,
                                add_blank_before=(bullet_count == 0)
                            )
                            bullet_count += inserted

        # L2: Clinical Innovations
        if l2_entries:
            section_idx = self._find_paragraph_with_text("Clinical Innovations")
            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate the table is actually for innovations, not a grant/funding table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical innovations)")

                sorted_entries = sort_entries_reverse_chronological(l2_entries)

                if table and table_is_valid:
                    self._clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1

                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        title = fields.get('title') or fields.get('innovation') or ''
                        role = fields.get('role') or ''
                        description = fields.get('description') or ''
                        start_date = fields.get('start_date') or fields.get('date') or ''
                        dates = format_date_for_section(start_date, 'L2') if start_date else ''

                        if not title and original_text:
                            title = original_text.split('|')[0].strip() if '|' in original_text else original_text[:100]

                        if title:
                            row = table.add_row()
                            # Typical innovation table: Date | Title/Location | Role/Description
                            if len(row.cells) >= 3:
                                row.cells[0].text = dates
                                row.cells[1].text = title
                                row.cells[2].text = f"{role}. {description}".strip('. ') if role or description else ''
                            elif len(row.cells) >= 2:
                                row.cells[0].text = dates
                                row.cells[1].text = title
                            else:
                                row.cells[0].text = f"{dates}: {title}" if dates else title

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        self._set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found — insert as bullet points
                    if self.verbose:
                        print(f"  No Clinical Innovations table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        original_text = entry.get('text', '').strip()
                        if self._is_structural_label(entry):
                            continue
                        bullet_text = original_text.replace('\t', ' — ', 1).replace('\t', ' ') if '\t' in original_text else original_text
                        if bullet_text:
                            self._insert_bulleted_entry(section_idx + 1 + bullet_count, bullet_text, entry, add_blank_before=(bullet_count == 0), list_level=0)
                            bullet_count += 1

        # L3: Clinical Leadership
        if l3_entries:
            section_idx = self._find_paragraph_with_text("Clinical Leadership")
            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate the table is actually a leadership table, not a grant/funding table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical leadership)")

                sorted_entries = sort_entries_reverse_chronological(l3_entries)

                if table and table_is_valid:
                    self._clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1

                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        role = fields.get('role') or fields.get('leadership_role') or fields.get('title') or ''
                        institution = fields.get('institution') or fields.get('organization') or ''
                        description = fields.get('description') or fields.get('program') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L3') or ''

                        if not role and original_text:
                            parts = original_text.split('|')
                            if len(parts) >= 1:
                                role = parts[0].strip()

                        if role:
                            row = table.add_row()
                            # Typical leadership table: Year(s) | Role | Description
                            if len(row.cells) >= 3:
                                row.cells[0].text = dates
                                row.cells[1].text = role
                                row.cells[2].text = f"{institution}. {description}".strip('. ') if institution or description else ''
                            elif len(row.cells) >= 2:
                                row.cells[0].text = dates
                                row.cells[1].text = f"{role} - {institution}" if institution else role
                            else:
                                row.cells[0].text = f"{dates}: {role}" if dates else role

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        self._set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found — insert as bullet points
                    if self.verbose:
                        print(f"  No Clinical Leadership table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '').strip()

                        if self._is_structural_label(entry):
                            continue

                        role = fields.get('role') or fields.get('leadership_role') or fields.get('title') or ''
                        institution = fields.get('institution') or fields.get('organization') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L3') or ''

                        if not role and original_text:
                            role = original_text.split('\t')[0].strip()

                        if role and institution and dates:
                            bullet_text = f"{role}, {institution}, {dates}"
                        elif role and dates:
                            bullet_text = f"{role}, {dates}"
                        elif role:
                            bullet_text = role
                        else:
                            bullet_text = original_text

                        if bullet_text:
                            # Use multiline helper to properly split entries with multiple lines
                            inserted = self._insert_multiline_as_bullets(
                                section_idx + 1 + bullet_count, bullet_text, entry,
                                add_blank_before=(bullet_count == 0)
                            )
                            bullet_count += inserted

    def _fill_leadership(self, entries: List[Dict]):
        """Fill O. INSTITUTIONAL LEADERSHIP ACTIVITIES section.

        WCM template has table with: Role(s)/Position | Institution/Location | Dates
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Institutional Leadership ({len(entries)} entries)...")

        # Find Leadership section
        section_idx = self._find_paragraph_with_text("INSTITUTIONAL LEADERSHIP")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Leadership")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'O')
            original_text = entry.get('text', '')

            # Check for leadership_role field (O code schema) as well as generic role/position
            role = fields.get('leadership_role') or fields.get('role') or fields.get('position') or ''
            institution = fields.get('institution') or fields.get('organization') or ''
            # Also check division_department for O codes
            if not institution:
                institution = fields.get('division_department') or ''
            start_date = fields.get('start_date') or ''
            end_date = fields.get('end_date') or ''
            dates = format_date_range(start_date, end_date, taxonomy_code) or ''

            # Check if this entry contains multiple items (newline-separated)
            lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            # For single/double-line entries, use extracted fields normally.
            if len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                self._add_multiline_leadership_rows(table, lines)
            elif len(lines) > 1 and not role:
                # Two lines, no extracted role - still try multi-line parsing
                self._add_multiline_leadership_rows(table, lines)
            else:
                if not role and not institution:
                    role = original_text[:100]

                self._add_leadership_row(table, role, institution, dates)

    def _add_leadership_row(self, table, role: str, institution: str, dates: str):
        """Add a single row to leadership table."""
        row = table.add_row()
        num_cols = len(row.cells)
        if num_cols >= 3:
            row.cells[0].text = role or ''
            row.cells[1].text = institution or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{role}, {institution}" if institution else (role or '')
            row.cells[1].text = dates or ''

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)
        self.stats['entries_inserted'] += 1

    def _add_multiline_leadership_rows(self, table, lines: List[str]):
        """Parse multiple leadership/committee lines and add separate rows.

        Handles patterns like:
        - "Committee Name (Chair 1999-2010)" - parenthetical role+date
        - "Committee Name | 1999-2010" - pipe-separated date column
        - "Committee Name    1999-2010" - trailing date
        - Lines followed by date-only lines (from table column extraction)
        """

        # Date patterns
        year_pattern = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)$', re.IGNORECASE)
        trailing_date = re.compile(r'(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)\s*$', re.IGNORECASE)
        # Parenthetical with role+date: "(Chair 1999-2010)" or "(Vice Chair 2006-2008)"
        paren_role_date = re.compile(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\s*\)', re.IGNORECASE)

        items = []  # (activity_text, institution, date)
        dates_pool = []

        for line in lines:
            # Skip empty or header-like lines
            if not line or line.lower() in ['dates', 'role', 'committee', 'institution']:
                continue

            # Handle pipe separator from table column extraction
            # e.g., "Committee (Chair 2002-present) | 1996-Present"
            if '|' in line:
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 2 and trailing_date.match(parts[-1]):
                    # Last part is a date, rest is the activity
                    activity = ' | '.join(parts[:-1])
                    pipe_date = parts[-1]
                    # Also extract any parenthetical role+date from the activity
                    paren_match = paren_role_date.search(activity)
                    if paren_match:
                        role_text = paren_match.group(1).strip().rstrip(',')
                        clean_activity = paren_role_date.sub('', activity).strip()
                        if role_text:
                            clean_activity = f"{clean_activity} ({role_text})"
                    else:
                        clean_activity = activity
                    items.append((clean_activity, '', pipe_date))
                    continue
                elif len(parts) == 1:
                    line = parts[0]
                # else fall through to normal processing

            # Check if this is a date-only line
            if year_pattern.match(line):
                dates_pool.append(line)
                continue

            # Check for parenthetical role+date: "Committee (Chair 1999-2010)"
            paren_match = paren_role_date.search(line)
            if paren_match:
                role_text = paren_match.group(1).strip().rstrip(',')
                start_year = paren_match.group(2)
                end_year = paren_match.group(3)
                item_date = f"{start_year}-{end_year}"
                # Clean the activity text: remove the parenthetical
                clean_activity = paren_role_date.sub('', line).strip()
                if role_text:
                    clean_activity = f"{clean_activity} ({role_text})"
                # Check for multiple parentheticals on same line
                # e.g., "(Vice Chair 2006-2008) (Chair 2008-2010)"
                all_parens = list(paren_role_date.finditer(line))
                if len(all_parens) > 1:
                    # Take the latest date range
                    last = all_parens[-1]
                    item_date = f"{last.group(2)}-{last.group(3)}"
                    # Reconstruct clean activity with all roles
                    clean_activity = paren_role_date.sub('', line).strip()
                    roles = [m.group(1).strip().rstrip(',') for m in all_parens if m.group(1).strip()]
                    if roles:
                        clean_activity = f"{clean_activity} ({'; '.join(roles)})"
                items.append((clean_activity, '', item_date))
                continue

            # Check if line has embedded date at the end (not in parentheses)
            date_match = trailing_date.search(line)
            if date_match:
                item_text = line[:date_match.start()].strip()
                item_date = date_match.group(1)
                if item_text:
                    items.append((item_text, '', item_date))
                else:
                    # Just a date with no text - add to pool
                    dates_pool.append(item_date)
                continue

            # Plain text line - no date found
            items.append((line, '', ''))

        # Match dates_pool to items without dates using forward mapping.
        # Both items and dates come from the same source table (column 1 → items,
        # column 2 → dates), so they're always in the same order.
        date_idx = 0
        for i in range(len(items)):
            if not items[i][2] and date_idx < len(dates_pool):
                items[i] = (items[i][0], items[i][1], dates_pool[date_idx])
                date_idx += 1

        # Add rows for each item
        for role, institution, item_date in items:
            self._add_leadership_row(table, role, institution, item_date)

    def _fill_administrative_activities(self, entries: List[Dict]):
        """Fill P. INSTITUTIONAL ADMINISTRATIVE ACTIVITIES section.

        WCM template has table with: Activity/Committee | Role | Dates
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Administrative Activities ({len(entries)} entries)...")

        # Find Administrative section
        section_idx = self._find_paragraph_with_text("INSTITUTIONAL ADMINISTRATIVE")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("ADMINISTRATIVE ACTIVITIES")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'P')
            original_text = entry.get('text', '')

            # Stage 4 packs a multi-committee entry as a LIST of record dicts under
            # committee_name/committee/activity (#208/#248 fusion). Expand each into
            # its own row rather than dumping a list into one cell (#256 crash).
            record_list = next(
                (v for v in (fields.get('committee_name'), fields.get('committee'),
                             fields.get('activity')) if isinstance(v, list)), None)
            if record_list:
                for rec in record_list:
                    if isinstance(rec, dict):
                        a = _committee_cell_text(rec.get('committee_name') or rec.get('committee')
                                                 or rec.get('activity') or rec.get('name'))
                        r = _committee_cell_text(rec.get('role'))
                        d = format_date_range(rec.get('start_date') or '',
                                              rec.get('end_date') or '', taxonomy_code) or ''
                    else:
                        a, r, d = _committee_cell_text(rec), '', ''
                    if a:
                        self._add_committee_row(table, a, r, d)
                continue

            activity = fields.get('activity') or fields.get('committee') or fields.get('committee_name') or ''
            role = fields.get('role') or ''
            start_date = fields.get('start_date') or ''
            end_date = fields.get('end_date') or ''
            dates = format_date_range(start_date, end_date, taxonomy_code) or ''

            # If dates not extracted, try to parse from parenthetical patterns in original text
            # Common patterns: "(Chair 2011-2013)", "(2010-present)", "(Member 1999-2012)"
            if not dates and original_text:
                # Pattern 1: (Role YYYY-YYYY) or (Role YYYY-present)
                paren_match = re.search(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\)', original_text, re.IGNORECASE)
                if paren_match:
                    potential_role = paren_match.group(1).strip()
                    start_year = paren_match.group(2)
                    end_year = paren_match.group(3)
                    dates = f"{start_year}-{end_year}"
                    # Extract role if present (e.g., "Chair", "Member")
                    if potential_role and not role:
                        role = potential_role.rstrip(',').strip()
                    # Clean activity by removing the parenthetical
                    if not activity:
                        activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', original_text).strip()
                else:
                    # Pattern 2: Just (YYYY-YYYY) without role
                    paren_match = re.search(r'\((\d{4})\s*[-–]\s*(\d{4}|present)\)', original_text, re.IGNORECASE)
                    if paren_match:
                        dates = f"{paren_match.group(1)}-{paren_match.group(2)}"
                        if not activity:
                            activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', original_text).strip()

            # Check if this entry contains multiple items (newline-separated)
            lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            if len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                self._add_multiline_committee_rows(table, lines)
            elif len(lines) > 1 and not activity:
                # Two lines, no extracted activity - still try multi-line parsing
                self._add_multiline_committee_rows(table, lines)
            else:
                if not activity:
                    activity = original_text[:150]

                self._add_committee_row(table, activity, role, dates)

    def _add_committee_row(self, table, activity: str, role: str, dates: str):
        """Add a single committee row with proper column handling."""
        # Defensive: never write a non-str (dict/list) into a Word cell — it
        # raises deep in python-docx and aborts the whole document (#256).
        activity = _committee_cell_text(activity)
        role = _committee_cell_text(role)
        dates = _committee_cell_text(dates)
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = activity or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{activity} ({role})" if role else activity
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{activity} - {dates}" if dates else activity

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    self._set_font(run)
        self.stats['entries_inserted'] += 1

    def _add_multiline_committee_rows(self, table, lines: List[str]):
        """Add multiple committee rows from multiline content, parsing dates from each line."""

        # Pattern for bare date lines (orphaned from table extraction)
        bare_date_pattern = re.compile(r'^\s*\|?\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?\s*$', re.IGNORECASE)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip bare date lines - they're orphaned from table extraction
            # and we can't associate them with any committee
            if bare_date_pattern.match(line):
                continue

            activity = line
            role = ''
            dates = ''

            # Try to parse "(Role YYYY-YYYY)" or "(YYYY-YYYY)" pattern
            paren_match = re.search(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\)', line, re.IGNORECASE)
            if paren_match:
                potential_role = paren_match.group(1).strip()
                start_year = paren_match.group(2)
                end_year = paren_match.group(3)
                dates = f"{start_year}-{end_year}"
                if potential_role:
                    role = potential_role.rstrip(',').strip()
                activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', line).strip()
            else:
                # Try simpler pattern: just (YYYY-YYYY)
                paren_match = re.search(r'\((\d{4})\s*[-–]\s*(\d{4}|present)\)', line, re.IGNORECASE)
                if paren_match:
                    dates = f"{paren_match.group(1)}-{paren_match.group(2)}"
                    activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', line).strip()

            # Skip if activity is empty after date extraction
            if not activity:
                continue

            self._add_committee_row(table, activity, role, dates)

    def _fill_presentations(self, entries: List[Dict]):
        """Fill R. INVITATIONS TO SPEAK/PRESENT section.

        WCM template has tables for Regional/National/International:
        Table structure: Title | Institution/Location | Dates (yyyy)

        Uses cv_owner_location to classify geographic scope of each entry.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Invited Presentations ({len(entries)} entries)...")

        # Find Presentations section
        section_idx = self._find_paragraph_with_text("INVITATIONS TO SPEAK")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Invited Presentations")
        if section_idx is None:
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            # Look for scope header (may have * suffix like "National*")
            scope_idx = None
            for i in range(section_idx, min(section_idx + 25, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope or para_text == f"{scope}*":
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(section_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            return

        # Clear tables and mark as populated
        for scope, table in tables_by_scope.items():
            self._clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

        # Classify and route entries by geographic scope
        entries_by_scope = {'Regional': [], 'National': [], 'International': []}
        for entry in entries:
            scope = self._classify_geographic_scope(entry)
            entries_by_scope[scope].append(entry)

        if self.verbose and self.cv_owner_location:
            regional_count = len(entries_by_scope['Regional'])
            national_count = len(entries_by_scope['National'])
            intl_count = len(entries_by_scope['International'])
            print(f"  Presentations: {regional_count} Regional, {national_count} National, {intl_count} International")

        # Fill each table with its entries
        for scope, scope_entries in entries_by_scope.items():
            if not scope_entries:
                continue

            # Find the table for this scope (fall back to National)
            table = tables_by_scope.get(scope) or tables_by_scope.get('National')
            if not table:
                continue

            sorted_entries = sort_entries_reverse_chronological(scope_entries)

            for entry in sorted_entries:
                fields = entry.get('extracted_fields', {}) or {}
                taxonomy_code = entry.get('taxonomy_code', 'R')

                title = fields.get('title') or fields.get('presentation_title') or ''
                institution = fields.get('institution') or fields.get('location') or fields.get('venue') or ''

                # Get date, falling back to start_date/end_date for multi-date entries
                raw_date = fields.get('year') or fields.get('date') or ''
                # Handle the string "None" from field extraction
                if str(raw_date).strip().lower() == 'none':
                    raw_date = ''
                if not raw_date:
                    # Fall back to start_date for entries with date ranges
                    raw_date = fields.get('start_date') or ''
                    if str(raw_date).strip().lower() == 'none':
                        raw_date = ''
                # Format date as yyyy per WCM template requirements
                formatted_date = format_date_for_section(raw_date, 'R') if raw_date else ''

                if not title:
                    title = entry.get('text', '')[:150]

                row = table.add_row()
                num_cols = len(row.cells)
                if num_cols >= 3:
                    row.cells[0].text = title or ''
                    row.cells[1].text = institution or ''
                    row.cells[2].text = formatted_date
                elif num_cols >= 2:
                    row.cells[0].text = title or ''
                    row.cells[1].text = formatted_date

                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            self._set_font(run)
                self.stats['entries_inserted'] += 1

    def _fill_passthrough_sections(self, all_entries: List[Dict]):
        """Fill sections that can be copied directly from source CV when format matches.

        These are short, structured sections in the WCM template that may already exist
        in the source CV in the same format. If found, we copy them directly.

        Sections handled:
        - E. EMPLOYMENT STATUS
        - G. INSTITUTIONAL/HOSPITAL AFFILIATION

        Note: PERCENT EFFORT is complex and typically filled manually.

        Args:
            all_entries: All entries from the pipeline (to search by hierarchy)
        """
        self._fill_employment_status(all_entries)
        self._fill_hospital_affiliation(all_entries)

    def _fill_employment_status(self, all_entries: List[Dict]):
        """Fill E. EMPLOYMENT STATUS section.

        Looks for entries with hierarchy containing 'EMPLOYMENT STATUS' and
        text in 'Label: Value' format (e.g., 'Name of Employer(s): Weill Cornell').
        """
        # Find entries from Employment Status section
        matching_entries = []
        for entry in all_entries:
            hierarchy = entry.get('hierarchy', [])
            hierarchy_str = ' '.join(hierarchy).upper()

            # Match entries specifically from Employment Status section
            if 'EMPLOYMENT STATUS' in hierarchy_str or (
                'EMPLOYMENT' in hierarchy_str and 'H.' in hierarchy_str
            ):
                text = entry.get('text', '').strip()
                # Accept "Label: Value" format entries
                if text and ':' in text:
                    parts = text.split(':', 1)
                    value = parts[1].strip() if len(parts) > 1 else ''
                    # Must have meaningful value after colon
                    if len(value) > 2:
                        matching_entries.append(entry)

        if not matching_entries:
            return

        # Find "Name of Current Employer" paragraph in template (more specific than section header)
        target_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.lower()
            if 'name of current employer' in text or 'name of employer' in text:
                target_idx = i
                break

        if target_idx is None:
            # Fall back to EMPLOYMENT STATUS section header
            target_idx = self._find_paragraph_with_text('EMPLOYMENT STATUS')

        if target_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Employment Status section")
            return

        if self.verbose:
            print(f"  Passthrough: Filling Employment Status ({len(matching_entries)} entries)")

        # Update the target paragraph with the employer info
        for entry in matching_entries:
            text = entry.get('text', '').strip()
            if ':' in text:
                # Parse "Label: Value" and update the corresponding template paragraph
                parts = text.split(':', 1)
                label = parts[0].strip()
                value = parts[1].strip()

                # Find and update the matching label in template
                for i, para in enumerate(self.doc.paragraphs):
                    para_text = para.text.strip()
                    # Match paragraphs with similar labels (e.g., "Name of Current Employer(s):")
                    if 'employer' in para_text.lower() and ':' in para_text:
                        # Update the paragraph: keep the label, add the value
                        label_end = para_text.find(':')
                        existing_label = para_text[:label_end + 1]
                        # Clear and rewrite
                        para.clear()
                        run = para.add_run(f"{existing_label}\t{value}")
                        self._set_font(run)
                        self.stats['entries_inserted'] += 1
                        break

    def _fill_hospital_affiliation(self, all_entries: List[Dict]):
        """Fill G. INSTITUTIONAL/HOSPITAL AFFILIATION section.

        Looks for dedicated hospital affiliation entries or extracts from D2 positions.
        """
        # Find entries from Affiliation sections
        matching_entries = []
        for entry in all_entries:
            hierarchy = entry.get('hierarchy', [])
            hierarchy_str = ' '.join(hierarchy).upper()

            if ('AFFILIATION' in hierarchy_str and 'HOSPITAL' in hierarchy_str) or \
               ('INSTITUTIONAL' in hierarchy_str and 'AFFILIATION' in hierarchy_str):
                text = entry.get('text', '').strip()
                if text and len(text) > 5:
                    matching_entries.append(entry)

        if not matching_entries:
            # No dedicated affiliation entries - skip (D2 positions are handled elsewhere)
            return

        # Find the affiliation table
        section_idx = self._find_paragraph_with_text('INSTITUTIONAL/HOSPITAL AFFILIATION')
        if section_idx is None:
            section_idx = self._find_paragraph_with_text('HOSPITAL AFFILIATION')

        if section_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Hospital Affiliation section")
            return

        # Find table after the section
        table = self._find_table_after_paragraph(section_idx)

        # Validate this is the affiliation table (should have "Primary Hospital" or similar)
        if table and table.rows:
            first_cell = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
            if 'hospital' not in first_cell and 'affiliation' not in first_cell and 'primary' not in first_cell:
                # Wrong table - don't modify
                if self.verbose:
                    print(f"  Passthrough: Skipping non-affiliation table (first cell: '{first_cell[:30]}')")
                table = None

            if table:
                # Clear existing table data and fill with matched entries
                self._clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in matching_entries:
                    text = entry.get('text', '').strip()
                    fields = entry.get('extracted_fields', {}) or {}

                    # Try to extract structured content
                    if ':' in text:
                        # Parse "Label: Value" format
                        parts = text.split(':', 1)
                        label = parts[0].strip()
                        value = parts[1].strip() if len(parts) > 1 else ''

                        # Add as row: [Label, Value]
                        row = table.add_row()
                        row.cells[0].text = label
                        if len(row.cells) > 1:
                            row.cells[1].text = value
                    else:
                        # Add as single-cell row
                        row = table.add_row()
                        row.cells[0].text = text

                    # Apply font formatting
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                self._set_font(run)

                    self.stats['entries_inserted'] += 1
            else:
                # No table - insert as bullet points after the section header
                for i, entry in enumerate(matching_entries):
                    text = entry.get('text', '').strip()
                    if text:
                        insert_idx = section_idx + 1 + i
                        self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=(i == 0), list_level=0)

    def _route_overflow_entries(self):
        """Route content-overflow entries as tracked-change bullets in their WCM sections.

        For entries where extraction coverage was very low (<50%) and the original text
        is substantial (>300 chars), inserts the full original text as a tracked-change
        bullet at the end of the entry's WCM section. Falls back to Section T (Appendix)
        if the section end can't be determined.
        """
        if not self._overflow_entries:
            return

        if self.verbose:
            print(f"\nRouting {len(self._overflow_entries)} content-overflow entries...")

        for entry, para, taxonomy_code in self._overflow_entries:
            original_text = entry.get('text', '').strip()
            if not original_text:
                continue

            # Get coverage for the comment
            extraction_coverage = entry.get('extraction_coverage', {})
            coverage_pct = extraction_coverage.get('extraction_coverage_percent', 0) if isinstance(extraction_coverage, dict) else 0

            # Find this paragraph's position in the document
            para_idx = None
            for i, doc_para in enumerate(self.doc.paragraphs):
                if doc_para._p is para._p:
                    para_idx = i
                    break

            if para_idx is None:
                # Paragraph not in doc.paragraphs (likely in a table cell).
                # For K/L codes, find the section header and insert bullets there
                # instead of routing to appendix.
                if taxonomy_code.startswith('K') or taxonomy_code.startswith('L'):
                    section_header = self._get_wcm_section_header(taxonomy_code)
                    header_idx = self._find_paragraph_with_text(section_header)
                    if header_idx is not None:
                        section_end_idx = self._find_section_end_paragraph_idx(header_idx)
                        if section_end_idx is not None:
                            overflow_para = self._insert_overflow_bullet(section_end_idx, original_text)
                            if overflow_para:
                                self._add_word_comment(
                                    overflow_para,
                                    f"Content overflow: The table row captured only "
                                    f"{coverage_pct:.0f}% of the original text ({len(original_text)} chars). "
                                    f"Full content inserted as bullets for review.",
                                    author="CViche Overflow"
                                )
                                self.stats['overflow_bullets_added'] += 1
                                if self.verbose:
                                    print(f"  Added overflow bullets in section {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")
                                continue
                # Fall back to appendix for non-K/L codes or if section insertion failed
                self._route_overflow_to_appendix(entry, coverage_pct)
                continue

            # Find end of this WCM section
            section_end_idx = self._find_section_end_paragraph_idx(para_idx)

            if section_end_idx is not None:
                # Insert overflow bullet just before the next section header
                overflow_para = self._insert_overflow_bullet(section_end_idx, original_text)
                if overflow_para:
                    # Add explanatory comment (blue text + comment = reviewer signal)
                    self._add_word_comment(
                        overflow_para,
                        f"Content overflow (shown in blue): The table row captured only "
                        f"{coverage_pct:.0f}% of the original text ({len(original_text)} chars). "
                        f"Full content inserted below for review — edit or delete as appropriate.",
                        author="CViche Overflow"
                    )
                    # Remove the original abbreviated entry to prevent duplication
                    # (overflow bullet is in the same section, so keeping both is redundant)
                    self._remove_abbreviated_entry(para)
                    self.stats['overflow_bullets_added'] += 1
                    if self.verbose:
                        print(f"  Added overflow bullet in section {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")
                else:
                    # Insertion failed — fall back to appendix, keep original in place
                    self._route_overflow_to_appendix(entry, coverage_pct)
            else:
                # Last section or lookup failed — fall back to appendix, keep original in place
                self._route_overflow_to_appendix(entry, coverage_pct)

    def _insert_overflow_bullet(self, insert_idx: int, text: str) -> Optional[Paragraph]:
        """Insert overflow content as tracked-change bullet(s) preserving original structure.

        Each tab-separated segment from the original CV becomes its own paragraph,
        mirroring the original Word document's paragraph structure. Levels are
        assigned via a state machine:
          - First segment: ilvl=0 (title)
          - Label segments ending with ':': ilvl=1 (section labels)
          - Content after a label: ilvl=2 (detail content)
          - Everything else: ilvl=1 (dates, institution context)

        Returns the first created paragraph, or None if insertion failed.
        """
        if insert_idx >= len(self.doc.paragraphs):
            return None

        try:
            # Split on tabs — each tab was a paragraph boundary in the original CV
            segments = text.split('\t') if '\t' in text else [text]
            clean_segments = [seg.strip() for seg in segments if seg.strip()]

            if not clean_segments:
                clean_segments = [text.replace('\t', ' ')]

            # Assign levels using a state machine that mirrors the original CV structure:
            #   ilvl=0: title (first segment)
            #   ilvl=1: dates, institution context, section labels (e.g., "Scope:")
            #   ilvl=2: content that follows a label (scope description, initiatives)
            levels = []
            after_label = False
            for i, seg in enumerate(clean_segments):
                if i == 0:
                    levels.append(0)
                elif seg.rstrip().endswith(':') or seg.rstrip().endswith(':\u200b'):
                    # Section label (e.g., "Scope:", "Selected Initiatives:")
                    levels.append(1)
                    after_label = True
                elif after_label:
                    # Content under a label — stays at ilvl=2 until next label
                    levels.append(2)
                else:
                    # Context segments before any label (dates, institution)
                    levels.append(1)

            # Insert paragraphs in reverse order (insert_paragraph_before pushes down).
            # Uses normal runs (not w:ins tracked changes) because Word does not
            # render list bullets on paragraphs whose only content is inside w:ins.
            # Indentation starts at 0 for ilvl=0 (override the List Paragraph
            # style's default 720-twip / 0.5-inch left indent).
            INDENT_PER_LEVEL = 360          # 0.25 inch step per level
            HANGING = 360                   # 0.25 inch hanging indent

            first_para = None
            for idx, (seg_text, level) in enumerate(reversed(list(zip(clean_segments, levels)))):
                para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")
                self._apply_list_bullet(para, level=level)

                # Override indentation so ilvl=0 bullet sits at the left margin.
                pPr = para._p.get_or_add_pPr()
                existing_ind = pPr.find(qn('w:ind'))
                if existing_ind is not None:
                    pPr.remove(existing_ind)
                ind = OxmlElement('w:ind')
                left = INDENT_PER_LEVEL * (level + 1)
                ind.set(qn('w:left'), str(left))
                ind.set(qn('w:hanging'), str(HANGING))
                pPr.append(ind)

                run = para.add_run(seg_text)
                self._set_font(run)

                # The last iteration in reversed order is forward_idx=0 (the title)
                if idx == len(clean_segments) - 1:
                    first_para = para

            return first_para
        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not insert overflow bullet: {e}")
            return None

    def _remove_abbreviated_entry(self, para: Paragraph):
        """Remove the original abbreviated entry that overflow is replacing.

        Handles both table-cell paragraphs (removes the entire row) and
        body-level paragraphs (removes the paragraph element).
        """
        try:
            p_element = para._p
            parent = p_element.getparent()
            if parent is None:
                return

            # Check if paragraph is inside a table cell (w:tc)
            parent_tag = parent.tag
            if parent_tag.endswith('}tc'):
                # In a table cell — remove the entire row
                row = parent.getparent()  # w:tr
                if row is not None:
                    table_elem = row.getparent()  # w:tbl
                    if table_elem is not None:
                        table_elem.remove(row)
            else:
                # Body-level paragraph — remove directly
                parent.remove(p_element)
        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not remove abbreviated entry: {e}")

    def _route_overflow_to_appendix(self, entry: Dict, coverage_pct: float = 0):
        """Queue an overflow entry for reconsideration before adding to appendix.

        Instead of immediately adding to appendix, we collect entries here.
        The reconsideration step will analyze them for segments that could
        be reclassified to other sections (K, L, etc.). Only truly unmappable
        content ends up in the final appendix.
        """
        original_text = entry.get('text', '').strip()
        if not original_text:
            return

        # Queue for reconsideration
        self._appendix_pending.append((entry, coverage_pct))

        self.stats['overflow_to_appendix'] += 1
        if self.verbose:
            taxonomy_code = entry.get('taxonomy_code', '?')
            print(f"  Queued for reconsideration: {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")

    def _reconsider_appendix_entries(self):
        """Analyze appendix-pending entries and reclassify segments to appropriate sections.

        For each entry queued for appendix, this method:
        1. Segments the content into logical blocks (by sentence/paragraph)
        2. Uses LLM to classify each segment to a taxonomy code
        3. Routes segments to appropriate WCM sections as bullets
        4. Only truly unmappable content remains for the appendix
        """
        if not self._appendix_pending:
            return

        if self.verbose:
            print(f"\nReconsidering {len(self._appendix_pending)} appendix entries...")

        # Collect all segments that could be reclassified
        segments_to_route = []  # List of (segment_text, taxonomy_code, original_entry)
        remaining_for_appendix = []  # Entries/segments that couldn't be reclassified

        for entry, coverage_pct in self._appendix_pending:
            original_text = entry.get('text', '').strip()
            original_code = entry.get('taxonomy_code', '?')

            # Use LLM to segment and reclassify
            reclassified = self._reclassify_entry_segments(original_text, original_code)

            if reclassified:
                for segment_text, new_code in reclassified:
                    if segment_already_rendered(segment_text, entry.get('extracted_fields') or {}):
                        # Already visible in the document (e.g. the one grant
                        # that DID get extracted from an under-extracted
                        # multi-record entry) — don't duplicate it anywhere.
                        continue
                    if new_code and new_code != 'T':
                        # Route confirmed-code segments home too: a segment
                        # keeping its (correct) code is usually an unrendered
                        # sibling record, not unmappable content (#209).
                        segments_to_route.append((segment_text, new_code, entry))
                    else:
                        # Couldn't reclassify this segment
                        remaining_for_appendix.append((segment_text, new_code or original_code, coverage_pct))
            else:
                # LLM couldn't process - keep original in appendix
                remaining_for_appendix.append((original_text, original_code, coverage_pct))

        # Route reclassified segments to their new sections
        for segment_text, new_code, original_entry in segments_to_route:
            if self._insert_reconsidered_segment(segment_text, new_code):
                self.stats['appendix_segments_reconsidered'] += 1
            else:
                # No usable anchor for this code — keep the segment visible
                # in the appendix rather than dropping it silently (#221).
                remaining_for_appendix.append((segment_text, new_code, 0.0))

        # Add remaining unmappable content to appendix
        if remaining_for_appendix:
            self._add_remaining_to_appendix(remaining_for_appendix)

        if self.verbose and segments_to_route:
            print(f"  Reclassified {len(segments_to_route)} segments to other sections")

    def _reclassify_entry_segments(self, text: str, original_code: str) -> List[Tuple[str, str]]:
        """Use LLM to segment and reclassify content from an appendix entry.

        Returns list of (segment_text, taxonomy_code) tuples.
        """
        # Build a condensed taxonomy reference for relevant codes
        taxonomy_hint = """
K1: Didactic Teaching (courses, lectures)
K2: Clinical Teaching (bedside, rounds)
K3: Mentoring/Advising
K4: Curriculum Development
K5: Other Teaching Activities
L1: Clinical Practice activities
L2: Clinical Innovations
L3: Clinical/Administrative Leadership
M2A: Current Research Funding (active/awarded grants)
M2B: Past/Completed Research Funding
M2C: Pending Funding (submitted, under review, or not funded)
N3A: Current Mentees (trainees currently supervised)
N3B: Past Mentees (graduated/former trainees)
O: Institutional Leadership (department head, director)
P: Institutional Committee Service
Q1: Leadership in External Organizations
Q2: Service on External Boards/Committees
"""

        prompt = f"""Analyze this CV content that was originally classified as {original_code} but contains additional narrative that may belong in other sections.

ORIGINAL TEXT:
{text}

TASK:
1. Split this into logical segments (each responsibility, role, or activity)
2. For each segment, assign the most appropriate taxonomy code from:
{taxonomy_hint}

RULES:
- Keep position title/dates with the original code ({original_code})
- Teaching activities → K codes
- Administrative/leadership roles → L3 or O
- Committee service → P or Q2
- External organization leadership → Q1
- Clinical practice details → L1
- Grants/funding → M2A (active/awarded), M2B (completed), M2C (submitted/under review/not funded)
- Mentees/advisees → N3A (current) or N3B (past/graduated)
- Only reclassify segments that CLEARLY belong elsewhere
- Use "KEEP" for segments that should stay with original code

OUTPUT FORMAT (one per line):
CODE: segment text

Example:
{original_code}: Attending Pathologist, Hospital Name, 2004-Present
K2: Participate in clinical teaching conferences for residents
L3: Assistant medical director of Surgical Pathology Laboratory
O: Acting Chairman of Pathology (October-December, 2006)

Now analyze the text above:"""

        try:
            llm_result = call_llm(
                stage="stage_6",
                messages=[
                    {"role": "system", "content": "You are an expert at analyzing academic CV content and classifying it into standard CV taxonomy categories."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                # Generous cap: a truncated segment list silently loses the
                # trailing records (#209) — never tighten this back down.
                max_tokens=4000
            )

            result_text = llm_result["content"].strip()

            # Parse the response
            segments = []
            for line in result_text.split('\n'):
                line = line.strip()
                if not line or ':' not in line:
                    continue
                # Parse "CODE: text" format
                parts = line.split(':', 1)
                if len(parts) == 2:
                    code = parts[0].strip().upper()
                    segment_text = parts[1].strip()
                    if segment_text and len(segment_text) > 10:
                        # KEEP means "correct as originally coded" — resolve to
                        # the original code so the caller can route it home
                        # instead of dumping it in the appendix (#209).
                        if code == 'KEEP':
                            code = original_code if original_code != '?' else None
                        segments.append((segment_text, code))

            return segments if segments else None

        except Exception as e:
            if self.verbose:
                print(f"  Warning: LLM reclassification failed: {e}")
            return None

    def _insert_reconsidered_segment(self, text: str, taxonomy_code: str,
                                     comment: str = None) -> bool:
        """Insert a reclassified segment into the appropriate WCM subsection.

        Returns True when the bullet was actually inserted, so callers can
        fall back to the appendix instead of silently losing the segment.
        """
        # Find the subsection header for this code
        section_header = self._get_wcm_section_header(taxonomy_code)
        if not section_header:
            return False

        # Never anchor inside the appendix: its bold 'From "SECTION":' group
        # heads echo source section names and would swallow content meant for
        # the real section (the appendix always sits at document end, and
        # _fill_appendix runs before this).
        appendix_idx = self._find_paragraph_with_text("T. APPENDIX")

        # Use precise subsection search to avoid matching main section headers
        header_idx = self._find_subsection_header(section_header,
                                                  before_idx=appendix_idx)
        if header_idx is None:
            # Fall back to a header-looking match only (ALL-CAPS main section
            # headers the subsection search skips by design). A plain
            # substring fallback anchored S-code recoveries on the MENTORING
            # instruction paragraph containing 'bibliography'; failing to
            # anchor is safe — callers fall back to the appendix.
            header_idx = self._find_header_paragraph(section_header)
            if (header_idx is not None and appendix_idx is not None
                    and header_idx >= appendix_idx):
                header_idx = None
        if header_idx is None:
            return False

        # Find the right insertion point: after the subsection header and any
        # instructional text, but before the next subsection or table
        insert_idx = self._find_subsection_insert_point(header_idx)
        if insert_idx is None:
            return False

        # Insert as a bullet
        try:
            insert_para = self.doc.paragraphs[insert_idx]
            new_para = insert_para.insert_paragraph_before()

            run = new_para.add_run(f"• {_clean_inline_tabs(_strip_taxonomy_code(text))}")
            self._set_font(run)

            # Add explanatory comment
            self._add_word_comment(
                new_para,
                comment or (
                    f"Recovered from unmapped overflow content and routed to {taxonomy_code}. "
                    f"Review and edit as appropriate."
                ),
                author="CViche Reconsideration"
            )

            if self.verbose:
                print(f"    Inserted [{taxonomy_code}]: {text[:60]}...")
            return True

        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not insert reconsidered segment: {e}")
            return False

    def _find_subsection_header(self, search_text: str,
                                before_idx: int = None) -> Optional[int]:
        """Find a subsection header that exactly matches the search text.

        Unlike _find_paragraph_with_text which does substring matching,
        this looks for paragraphs where the text closely matches the search
        and it's formatted as a subsection (bold but not all-caps main section).
        When before_idx is given, only paragraphs before it are considered
        (e.g. to keep the search out of the appendix).
        """
        search_lower = search_text.lower().strip()

        for i, para in enumerate(self.doc.paragraphs):
            if before_idx is not None and i >= before_idx:
                break
            text = para.text.strip()
            text_lower = text.lower()

            # Skip empty or very short paragraphs
            if len(text) < 3:
                continue

            # Check if text matches (allowing for minor variations)
            if search_lower in text_lower:
                # Skip main section headers (ALL CAPS with letters like "L. CLINICAL")
                if text.isupper() or (len(text) > 2 and text[1] == '.' and text[0].isupper()):
                    continue

                # Check if it's bold (subsection header style)
                if para.runs and para.runs[0].bold:
                    # Make sure it's a close match (not just substring)
                    # "Clinical Practice" should match "Clinical Practice" but not
                    # "CLINICAL PRACTICE, INNOVATION, and LEADERSHIP"
                    if len(text) < len(search_text) * 2:
                        return i

        return None

    def _find_subsection_insert_point(self, header_idx: int) -> Optional[int]:
        """Find the right paragraph index to insert content after a subsection header.

        Scans forward from header_idx, skipping instructional text (paragraphs
        starting with 'Please', 'Include', etc.), and returns the index of the
        next subsection header or major section header to insert before.
        """
        # Instructional text patterns to skip
        instruction_starts = ('please', 'include', 'list', 'describe', 'use', 'note:')

        for i in range(header_idx + 1, min(header_idx + 10, len(self.doc.paragraphs))):
            para = self.doc.paragraphs[i]
            text = para.text.strip().lower()

            # Skip empty paragraphs
            if not text:
                continue

            # Skip instructional paragraphs
            if any(text.startswith(instr) for instr in instruction_starts):
                continue

            # Check if this is a bold subsection header (next subsection)
            if para.runs and para.runs[0].bold:
                return i

            # Check if this looks like a table follows (often has specific patterns)
            # If we hit regular content, insert here
            return i

        # Fallback to end of section
        return self._find_section_end_paragraph_idx(header_idx)

    def _add_remaining_to_appendix(self, remaining: List[Tuple[str, str, float]]):
        """Add remaining unmappable segments to the appendix."""
        # Filter BEFORE creating the section header so an all-noise batch
        # doesn't leave an empty T. APPENDIX behind (#213).
        remaining = [
            (text, code, cov) for text, code, cov in remaining
            if text and text.strip()
            and not is_template_instruction(text)
            and not is_source_boilerplate(text)
        ]
        if not remaining:
            return

        # Find or create the T. APPENDIX section
        appendix_idx = self._find_paragraph_with_text("T. APPENDIX")

        if appendix_idx is None:
            # Create the appendix section
            self.doc.add_paragraph()
            appendix_para = self.doc.add_paragraph()
            run = appendix_para.add_run("T. APPENDIX")
            self._set_font(run, bold=True)
            run.underline = True

            intro_para = self.doc.add_paragraph()
            run = intro_para.add_run(
                "The following content from the original CV was not successfully mapped to this CV format:"
            )
            self._set_font(run)
            self.doc.add_paragraph()

        # Add each remaining segment. The taxonomy code is an internal
        # pipeline identifier — keep it in a reviewer comment, never in the
        # faculty-facing text (#213).
        for segment_text, original_code, coverage_pct in remaining:
            entry_para = self.doc.add_paragraph()
            run = entry_para.add_run(f"• {segment_text}")
            self._set_font(run)
            self._add_word_comment(
                entry_para,
                f"Originally classified {original_code}; could not be mapped "
                f"to a template section.",
                author="Classification",
            )

    def _rendered_output_lines(self) -> List[str]:
        """Every rendered text line of the in-memory document: body paragraphs
        plus table cells. Two render-time divergences from run_doctor's
        read_docx_blocks (which walks only top-level tables, cell by cell):
        nested tables are recursed into, and each table row is ALSO emitted
        with its cells joined as one line, so a record rendered as a
        structured row (title / dates / institution cells) keeps its tokens
        together the way one source line does. Extra lines only ever ADD
        matches — fewer false "absent" verdicts, never more; the offline
        doctor may still WARN on rows this pass correctly judged rendered
        (reconciling lint 8's semantics is PR #223 scope)."""
        lines: List[str] = []

        def add(text: str):
            for ln in str(text or '').split('\n'):
                if ln.strip():
                    lines.append(ln)

        def walk_table(tbl):
            for row in tbl.rows:
                cell_texts = []
                for cell in row.cells:
                    if cell.text.strip():
                        cell_texts.append(cell.text)
                        add(cell.text)
                    for nested in cell.tables:
                        walk_table(nested)
                if len(cell_texts) > 1:
                    add(' | '.join(' '.join(t.split()) for t in cell_texts))

        for para in self.doc.paragraphs:
            add(para.text)
        for tbl in self.doc.tables:
            walk_table(tbl)
        return lines

    def _recover_unrendered_records(self, entries_by_code: Dict[str, List[Dict]]):
        """Post-render safety net (#221): re-emit record lines the structured
        render dropped.

        The structured-fields-only render paths keep the stage-4-extracted
        record and silently drop the unextracted remainder record lines of a
        fused multi-record entry (and most of those paths never reach the #214
        overflow pipeline at all). This pass runs after every section — and the
        overflow/reconsider passes — has rendered, checks each record-like line
        of every entry (pre-dedup, so records fused into a deduped-away entry
        are covered) against the in-memory document (mirroring run_doctor
        lint 8), and re-inserts the provably-absent ones as verbatim bullets in
        the entry's own section, with the appendix as the guaranteed-no-loss
        fallback. Lines that cannot be VERIFIED absent are never re-inserted:
        duplicating faculty-facing content is worse than leaving a loss for the
        offline doctor to flag.
        """
        if not self.recover_unrendered_records:
            return

        out_lines = self._rendered_output_lines()
        haystack = "\x00".join(_squash(line) for line in out_lines)
        line_token_sets = [set(_RENDER_TOKEN_RE.findall(_norm(line)))
                           for line in out_lines]

        appendix_batch = []   # (text, code, coverage) for _add_remaining_to_appendix
        n_recovered = 0

        for code, entries in entries_by_code.items():
            if code == 'T':
                # Appendix catch-all — _fill_appendix already carries these.
                continue
            for entry in entries:
                records = _record_lines(entry.get('text'))
                if len(records) < UNRENDERED_MIN_RECORD_LINES:
                    continue  # not a fused multi-record entry
                fields = entry.get('extracted_fields') or {}
                coverage = (entry.get('extraction_coverage') or {}).get(
                    'extraction_coverage_percent', 0)
                for line in records:
                    if _record_rendered(line, haystack, line_token_sets) is not False:
                        # Rendered (possibly reformatted), or too short to
                        # verify either way — never re-insert.
                        continue
                    if segment_already_rendered(line, fields):
                        # The record that DID render from extracted fields: a
                        # grant table splits its tokens across label/value
                        # rows, so the token check alone can miss it (#209).
                        continue
                    if (not re.search(r'\d', line)
                            and line.count('\t') + line.count('|') >= 2
                            and _is_column_header_row(line)):
                        # Multi-column rows with no year/number payload AND
                        # majority column-label words are tabular header rows
                        # ("State/Country  License Number  Status ...")
                        # satisfying the tab-record heuristic — not CV
                        # records. A dateless multi-cell row of real content
                        # (committee membership: "Member | Committee on X |
                        # Organization") is still recovered.
                        continue
                    if is_template_instruction(line) or is_source_boilerplate(line):
                        continue
                    inserted = self._insert_reconsidered_segment(
                        line, code,
                        comment=(
                            f"Recovered: this record from the source CV was not "
                            f"rendered by the structured {code} section. "
                            f"Review placement and formatting."
                        ))
                    if not inserted:
                        appendix_batch.append((line, code, coverage))
                    # Count the re-inserted line as rendered so a
                    # near-identical variant in another pre-dedup entry
                    # (trailing period, 'Sep' vs 'Sept') is verified rendered
                    # instead of inserted a second time — dedup drops entries
                    # precisely because they near-duplicate a kept one, so
                    # exact-squash matching is not enough.
                    haystack += "\x00" + _squash(line)
                    line_token_sets.append(
                        set(_RENDER_TOKEN_RE.findall(_norm(line))))
                    self.stats['unrendered_records_recovered'] += 1
                    n_recovered += 1

        if appendix_batch:
            self._add_remaining_to_appendix(appendix_batch)

        if self.verbose and n_recovered:
            print(f"  Recovered {n_recovered} unrendered record line(s) "
                  f"({len(appendix_batch)} routed to appendix)")

    def _fill_appendix(self, unmapped_entries: List[Dict]):
        """Add appendix section for unmapped content.

        Creates a T. Appendix section listing entries that couldn't be mapped
        to the WCM template structure. Format matches S. BIBLIOGRAPHY style.
        """
        if not unmapped_entries:
            return

        # Layer 3 backstop: drop WCM-template instruction boilerplate, source-CV
        # furniture (title lines, date stamps — #213), and empty entries that
        # slipped through to the unmapped pile so they do not pollute the
        # Appendix. Precision-biased: real CV content is never dropped.
        _pre_filter = len(unmapped_entries)
        unmapped_entries = [
            e for e in unmapped_entries
            if e.get("text", "").strip()
            and not is_template_instruction(e.get("text", ""))
            and not is_source_boilerplate(e.get("text", ""))
        ]

        # Render each surviving entry once, collapsing the readers' internal cell
        # separators. A table row whose cells are all empty (a blank WCM template
        # row, e.g. "|  |  |") is non-empty as raw text but renders to "" — it
        # carries no information, so it is dropped rather than shown as a bullet.
        rendered_entries = [
            (e, _clean_inline_tabs(e.get("text", "")))
            for e in unmapped_entries
        ]
        rendered_entries = [(e, t) for e, t in rendered_entries if t.strip()]

        _appendix_filtered = _pre_filter - len(rendered_entries)
        if _appendix_filtered:
            print(f"Filtered {_appendix_filtered} boilerplate/empty entries from Appendix")

        if not rendered_entries:
            return

        if self.verbose:
            print(f"Adding Appendix ({len(rendered_entries)} unmapped entries)...")

        # Add blank paragraph before appendix header (matching BIBLIOGRAPHY style)
        self.doc.add_paragraph()

        # Add appendix header - matching BIBLIOGRAPHY style (bold + underline)
        appendix_para = self.doc.add_paragraph()
        run = appendix_para.add_run("T. APPENDIX")
        self._set_font(run, bold=True)
        run.underline = True

        # Add explanatory text
        intro_para = self.doc.add_paragraph()
        run = intro_para.add_run(
            "The following content from the original CV was not successfully mapped to this CV format:"
        )
        self._set_font(run)

        # Emit ONE summary doc comment for the boilerplate we removed (rather
        # than a per-entry comment for each dropped block).
        if _appendix_filtered:
            self._add_word_comment(
                intro_para,
                f"{_appendix_filtered} boilerplate/empty block"
                f"{'s' if _appendix_filtered != 1 else ''} removed",
                author="Template Filter",
            )

        # Add blank paragraph after intro text
        self.doc.add_paragraph()

        # Group entries by their original CV section header
        entries_by_header = {}
        for entry, text in rendered_entries:
            hierarchy = entry.get('hierarchy', [])
            header = hierarchy[0] if hierarchy else 'Unknown Section'
            if header not in entries_by_header:
                entries_by_header[header] = []
            entries_by_header[header].append((entry, text))

        # List entries grouped by original header
        is_first_section = True
        for header, entries in entries_by_header.items():
            # Add blank paragraph before each section header (except the first one)
            if not is_first_section:
                self.doc.add_paragraph()
            is_first_section = False

            # Add subsection header showing original CV section
            header_para = self.doc.add_paragraph()
            run = header_para.add_run(f"From \"{header}\":")
            self._set_font(run, bold=True)

            for i, (entry, text) in enumerate(entries, start=1):
                element_idx = entry.get('element_idx_start', '')

                # Truncate long entries
                if len(text) > 200:
                    text = text[:200] + '...'

                entry_para = self.doc.add_paragraph()
                bullet_text = f"{i}. {text}"
                run = entry_para.add_run(bullet_text)
                self._set_font(run)

                # Add comments from entry (e.g., why it was classified as T)
                self._add_entry_comments(entry_para, entry)

                self.stats['entries_inserted'] += 1

    def _add_entry_comments(self, para: Paragraph, entry: Dict):
        """Add all relevant comments from an entry to the paragraph.

        Collects comments from various upstream pipeline stages:
        - Stage 2/3: Classification reasoning, taxonomy assignment notes
        - Stage 4: Extraction notes, coverage warnings
        - Stage 5: Enrichment status, validation warnings
        """
        comments_to_add = []

        # Classification reasoning from Stage 2/3
        classification_reasoning = entry.get('classification_reasoning')
        if classification_reasoning:
            taxonomy_code = entry.get('taxonomy_code', '?')
            confidence = entry.get('taxonomy_confidence') or entry.get('confidence', '')
            conf_str = f" (confidence: {confidence})" if confidence else ""
            comments_to_add.append({
                'text': f"Classified as {taxonomy_code}{conf_str}: {classification_reasoning}",
                'author': "Classification"
            })

        # Fragment information
        if entry.get('is_fragment'):
            fragment_info = entry.get('fragment_reasoning', 'Entry identified as fragment')
            fragment_of = entry.get('fragment_of', '')
            if fragment_of:
                fragment_info += f" (part of entry {fragment_of})"
            comments_to_add.append({
                'text': f"Fragment: {fragment_info}",
                'author': "Classification"
            })

        # T-validation notes
        if entry.get('t_validation_applied'):
            comments_to_add.append({
                'text': "T-validation was applied to this entry",
                'author': "Validation"
            })

        # Reasoning conflict detection
        if entry.get('reasoning_conflict_detected'):
            comments_to_add.append({
                'text': "Classification conflict detected - review recommended",
                'author': "Validation"
            })

        # Extraction coverage warnings
        # Skip for K-codes (teaching entries) since they have free-form content like director names
        # that aren't separate extraction fields, and skip if Stage 5c has already formatted the entry
        taxonomy_code = entry.get('taxonomy_code', '')
        fields = entry.get('extracted_fields', {})
        is_k_code = taxonomy_code.startswith('K')
        has_formatted_text = fields.get('formatted_text') or fields.get('formatting_source') == 'stage_5c_llm'

        extraction_coverage = entry.get('extraction_coverage', {})
        if isinstance(extraction_coverage, dict) and not is_k_code and not has_formatted_text:
            coverage_pct = extraction_coverage.get('extraction_coverage_percent', 100)
            if coverage_pct and coverage_pct < 70:
                unextracted = extraction_coverage.get('unextracted_words', [])
                unextracted_str = ', '.join(unextracted[:5]) if unextracted else ''
                comments_to_add.append({
                    'text': f"Low extraction coverage ({coverage_pct:.0f}%). Unextracted: {unextracted_str}",
                    'author': "Extraction"
                })

                # Collect for content overflow routing if coverage is low on a substantial entry.
                # K/L codes (Teaching/Clinical) naturally support bullet text, so use a more
                # generous threshold (50% coverage, 300 chars) for these sections.
                # Other codes use strict thresholds (15% coverage, 1000 chars) since table rows
                # typically summarize entries adequately even at 20-40% coverage.
                original_text = entry.get('text', '')
                is_kl_code = taxonomy_code.startswith('K') or taxonomy_code.startswith('L')
                if (is_kl_code and coverage_pct < 50 and len(original_text) > 300) or \
                   (not is_kl_code and coverage_pct < 15 and len(original_text) > 1000):
                    # Skip S-codes (bibliography) — they use enrichment, low coverage is expected
                    # Skip entries with formatted_text — they already have full LLM-formatted content
                    # Skip if the paragraph already contains most of the original text
                    # (e.g., bullet entries that render full original_text directly)
                    para_text_len = len(para.text.strip()) if para else 0
                    para_already_has_content = para_text_len >= len(original_text) * 0.8
                    if not taxonomy_code.startswith('S') and not has_formatted_text and not para_already_has_content:
                        self._overflow_entries.append((entry, para, taxonomy_code))

        # Direct comment fields
        for field_name in ['comment', 'pipeline_comment', 'extraction_comment',
                           'classification_comment', 'validation_comment', 'note']:
            if entry.get(field_name):
                comments_to_add.append({
                    'text': entry[field_name],
                    'author': "CV Pipeline"
                })

        # Comments in extracted_fields
        fields = entry.get('extracted_fields', {})
        if fields.get('comment'):
            comments_to_add.append({
                'text': fields['comment'],
                'author': "Extraction"
            })
        if fields.get('note'):
            comments_to_add.append({
                'text': fields['note'],
                'author': "Extraction"
            })

        # Narrative field - substantive prose that doesn't fit structured fields
        # Include as comment so reviewers can see the full context
        # EXCEPT for M2A/M2B/M2C grants where narrative is shown in "Major project goals" row
        taxonomy_code = entry.get('taxonomy_code', '')
        skip_narrative_comment = taxonomy_code in ('M2A', 'M2B', 'M2C')

        narrative = fields.get('narrative', '')
        if narrative and len(narrative.strip()) > 10 and not skip_narrative_comment:
            # Truncate very long narratives for readability
            if len(narrative) > 500:
                narrative_text = narrative[:500] + '... [truncated]'
            else:
                narrative_text = narrative
            comments_to_add.append({
                'text': f"Narrative: {narrative_text}",
                'author': "Extraction"
            })

        # Enrichment data comments
        enrichment_data = entry.get('enrichment_data', {})
        if enrichment_data.get('comment'):
            comments_to_add.append({
                'text': enrichment_data['comment'],
                'author': "Enrichment"
            })

        # Validation warnings
        if entry.get('validation_warning'):
            comments_to_add.append({
                'text': f"Warning: {entry['validation_warning']}",
                'author': "Validation"
            })
        if entry.get('extraction_warning'):
            comments_to_add.append({
                'text': f"Extraction warning: {entry['extraction_warning']}",
                'author': "Extraction"
            })

        # Reclassification notes (e.g., grants moved from Current to Completed)
        if entry.get('reclassification_note'):
            comments_to_add.append({
                'text': entry['reclassification_note'],
                'author': "Reclassification"
            })

        # Add all collected comments
        for comment_info in comments_to_add:
            self._add_word_comment(para, comment_info['text'], author=comment_info['author'])

    def _add_word_comment(self, para: Paragraph, comment_text: str, author: str = "CV Pipeline"):
        """Add a Word comment to a paragraph that appears in the sidebar.

        Creates proper Word comment structure with:
        - commentRangeStart/End markers in document
        - commentReference in the text
        - comment content stored for comments.xml
        """
        # Issue #153: when classification comments are disabled, emit nothing.
        # Skipping here means no commentReference is added and _comments stays
        # empty, so _finalize_comments never creates a comments.xml part.
        if not self.emit_comments:
            return
        try:
            comment_id = str(self._comment_id)
            self._comment_id += 1

            # Get the paragraph element
            p = para._p

            # Add comment range start right after pPr (pPr must stay first per OOXML)
            comment_start = OxmlElement('w:commentRangeStart')
            comment_start.set(qn('w:id'), comment_id)
            pPr = p.find(qn('w:pPr'))
            if pPr is not None:
                pPr.addnext(comment_start)
            else:
                p.insert(0, comment_start)

            # Add comment range end and reference at the end
            comment_end = OxmlElement('w:commentRangeEnd')
            comment_end.set(qn('w:id'), comment_id)
            p.append(comment_end)

            # Create a run for the comment reference
            comment_ref_run = OxmlElement('w:r')
            comment_ref = OxmlElement('w:commentReference')
            comment_ref.set(qn('w:id'), comment_id)
            comment_ref_run.append(comment_ref)
            p.append(comment_ref_run)

            # Store comment for later addition to comments.xml
            self._comments.append({
                'id': comment_id,
                'author': author,
                'date': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
                'text': comment_text
            })

            self.stats['comments_added'] += 1
        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not add comment: {e}")

    def _add_track_change_insertion(self, para: Paragraph, text: str, author: str = "PubMed Enrichment"):
        """Mark text as an insertion (track change) that appears in Word's review mode.

        Creates proper Word track change structure with w:ins element.
        """
        # Issue #153: when track changes are disabled, render the inserted text
        # as a plain run (no w:ins). The text is the final/accepted content, so
        # the document reads as if the change were already accepted.
        if not self.emit_track_changes:
            run = para.add_run(text)
            self._set_font(run)
            return run
        try:
            revision_id = str(self._revision_id)
            self._revision_id += 1

            # Create the insertion element
            ins = OxmlElement('w:ins')
            ins.set(qn('w:id'), revision_id)
            ins.set(qn('w:author'), author)
            ins.set(qn('w:date'), datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))

            # Create a run inside the insertion
            run_elem = OxmlElement('w:r')

            # Add run properties for font
            rPr = OxmlElement('w:rPr')
            rFonts = OxmlElement('w:rFonts')
            rFonts.set(qn('w:ascii'), 'Arial')
            rFonts.set(qn('w:hAnsi'), 'Arial')
            rPr.append(rFonts)
            sz = OxmlElement('w:sz')
            sz.set(qn('w:val'), '22')  # 11pt = 22 half-points
            rPr.append(sz)
            run_elem.append(rPr)

            # Add the text
            t = OxmlElement('w:t')
            t.text = text
            if text.startswith(' ') or text.endswith(' '):
                t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            run_elem.append(t)

            ins.append(run_elem)

            # Append to paragraph
            para._p.append(ins)

            self.stats['track_changes_added'] += 1
            return ins
        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not add track change: {e}")
            # Fall back to normal text
            run = para.add_run(text)
            self._set_font(run)
            return run

    def _add_track_change_deletion(self, para: Paragraph, text: str, author: str = "LLM Formatter"):
        """Mark text as a deletion (track change) that appears in Word's review mode.

        Creates proper Word track change structure with w:del element.
        The deleted text will appear struck-through in Word's track changes view.
        """
        # Issue #153: when track changes are disabled, omit the deletion entirely
        # (the deleted text is the superseded/original content). The paired
        # insertion still emits the final text as a plain run, so the accepted
        # version is what remains.
        if not self.emit_track_changes:
            return None
        try:
            revision_id = str(self._revision_id)
            self._revision_id += 1

            # Create the deletion element
            del_elem = OxmlElement('w:del')
            del_elem.set(qn('w:id'), revision_id)
            del_elem.set(qn('w:author'), author)
            del_elem.set(qn('w:date'), datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))

            # Create a run inside the deletion
            run_elem = OxmlElement('w:r')

            # Add run properties for font and strikethrough
            rPr = OxmlElement('w:rPr')
            rFonts = OxmlElement('w:rFonts')
            rFonts.set(qn('w:ascii'), 'Arial')
            rFonts.set(qn('w:hAnsi'), 'Arial')
            rPr.append(rFonts)
            sz = OxmlElement('w:sz')
            sz.set(qn('w:val'), '22')  # 11pt = 22 half-points
            rPr.append(sz)
            run_elem.append(rPr)

            # Add the deleted text element (w:delText instead of w:t)
            delText = OxmlElement('w:delText')
            delText.text = text
            if text.startswith(' ') or text.endswith(' '):
                delText.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            run_elem.append(delText)

            del_elem.append(run_elem)

            # Append to paragraph
            para._p.append(del_elem)

            self.stats['track_changes_added'] += 1
            return del_elem
        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not add track change deletion: {e}")
            return None

    def _add_track_change_pair(self, para: Paragraph, original_text: str, new_text: str,
                                author: str = "LLM Formatter"):
        """Add a tracked change showing original text as deleted and new text as inserted.

        This creates the Word revision pattern where reviewers can accept/reject the change.
        """
        # First add the deletion (original text struck through)
        self._add_track_change_deletion(para, original_text, author=author)
        # Then add the insertion (new text)
        self._add_track_change_insertion(para, new_text, author=author)

    def _finalize_comments(self):
        """Add comments to the document's comments.xml part.

        This creates a proper comments.xml file in the document package
        so comments appear in Word's sidebar.
        """
        if not self._comments:
            return

        try:
            from docx.opc.constants import CONTENT_TYPE as CT
            from docx.opc.part import Part
            from docx.opc.packuri import PackURI

            # Create comments XML content
            comments_xml = self._create_comments_xml()

            # Check if comments part already exists
            comments_uri = PackURI('/word/comments.xml')
            comments_part = None

            for rel in self.doc.part.rels.values():
                if 'comments' in str(rel.reltype).lower():
                    comments_part = rel.target_part
                    break

            if comments_part is None:
                # Create new comments part
                # The relationship type for comments
                comments_reltype = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments'
                comments_content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml'

                # Create the part with XML content
                from docx.opc.package import OpcPackage
                package = self.doc.part.package

                # Add the part to the package
                comments_part = Part(
                    comments_uri,
                    comments_content_type,
                    comments_xml.encode('utf-8'),
                    package
                )

                # Add relationship from document part to comments part
                self.doc.part.relate_to(comments_part, comments_reltype)

                if self.verbose:
                    print(f"  Created comments.xml with {len(self._comments)} comment(s)")
            else:
                # Append to existing comments
                # Parse existing and merge
                if self.verbose:
                    print(f"  Added {len(self._comments)} comment(s) to existing comments.xml")

        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not create comments.xml: {e}")
                import traceback
                traceback.print_exc()

    def _create_comments_xml(self) -> str:
        """Create XML content for comments.xml."""
        import html

        # Build comments XML
        lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
        lines.append('<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')

        for comment in self._comments:
            # Escape special XML characters in text content
            escaped_text = html.escape(comment["text"], quote=True)
            escaped_author = html.escape(comment["author"], quote=True)

            lines.append(f'<w:comment w:id="{comment["id"]}" w:author="{escaped_author}" w:date="{comment["date"]}">')
            lines.append('<w:p>')
            lines.append('<w:r>')
            lines.append(f'<w:t>{escaped_text}</w:t>')
            lines.append('</w:r>')
            lines.append('</w:p>')
            lines.append('</w:comment>')

        lines.append('</w:comments>')
        return '\n'.join(lines)

    def _fill_bibliography(self, entries_by_code: Dict[str, List[Dict]], cv_owner: Dict, document_uid: str = ''):
        """Fill bibliography section with formatted citations.

        Uses the official WCM template section headers and restarts numbering
        within each subsection.
        """
        # Map taxonomy codes to WCM template section header text
        # These must match the exact text in the official WCM template
        section_headers = {
            'S1': 'Peer-reviewed Research Articles:',
            'S2': 'Reviews and Editorials:',
            'S3': 'Books:',
            'S4': 'Chapters:',
            'S5': 'Non-peer-reviewed Research Publications:',
            'S6': 'Case Reports',
            'S7': 'In review',
            'S8': 'Abstracts',
            'S9': 'Other (media, podcasts, etc.):',
        }

        # Get CV owner last name for fallback bolding
        cv_owner_last_name = ''
        if cv_owner and cv_owner.get('last_name'):
            cv_owner_last_name = cv_owner['last_name']
        elif document_uid:
            # Fallback: extract from document_uid (e.g., "2015_Wende" -> "Wende")
            cv_owner_last_name = self._extract_last_name_from_uid(document_uid)

        # Count total publications
        pub_codes = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
        total_pubs = sum(len(entries_by_code.get(code, [])) for code in pub_codes)

        if self.verbose:
            print(f"Filling Bibliography ({total_pubs} publications)...")

        # Process each publication type
        for code in pub_codes:
            pubs = entries_by_code.get(code, [])
            if not pubs:
                continue

            header_text = section_headers.get(code, '')
            if not header_text:
                continue

            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(header_text)
            if section_idx is None:
                if self.verbose:
                    print(f"  Warning: Could not find section header '{header_text}'")
                continue

            if self.verbose:
                print(f"  {code}: {len(pubs)} entries -> '{header_text[:30]}...'")

            # Sort reverse chronologically (most recent first)
            pubs_sorted = sort_entries_reverse_chronological(pubs)
            # Un-fuse any entry that collapsed several citations into one
            # (#208), so each is numbered instead of rendering as unnumbered
            # <w:br/> continuation lines under one number.
            pubs_sorted = split_fused_citation_entries(pubs_sorted)

            # Insert after the section header - numbering restarts at 1 for each section
            insert_idx = section_idx + 1

            # Add blank line before first citation in each section
            if pubs_sorted:
                self.doc.paragraphs[insert_idx].insert_paragraph_before("")
                insert_idx += 1

            for citation_num, pub in enumerate(pubs_sorted, start=1):
                citation_text, target_name, enriched_fields = self._format_citation(pub, citation_num)
                original_text = pub.get('text', '')
                enrichment_status = pub.get('enrichment_status', '')

                # Insert citation paragraph before the next element
                para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")

                # Check if this entry was enriched - if so, use track changes
                if enrichment_status == 'enriched' and original_text:
                    # Show original as deleted, enriched citation as inserted
                    # First add the deletion (original text)
                    self._add_track_change_deletion(para, original_text, author="PubMed Enrichment")
                    # Then add the insertion (enriched citation) with bold author
                    self._add_citation_with_bold_author_as_insertion(
                        para, citation_text, target_name, cv_owner_last_name,
                        author="PubMed Enrichment"
                    )
                else:
                    # No enrichment - add citation normally with target name bolded
                    self._add_citation_with_bold_author(para, citation_text, target_name, cv_owner_last_name)

                # Add comment explaining enrichment (no inline text)
                if enriched_fields:
                    enrichment_source = pub.get('enrichment_source', '')
                    if enrichment_source:
                        comment = f"Data enriched from {enrichment_source.upper()}. Fields updated: {', '.join(enriched_fields)}"
                        self._add_word_comment(para, comment, author="PubMed Enrichment")

                # Add comments from upstream pipeline processes
                self._add_entry_comments(para, pub)

                insert_idx += 1
                self.stats['entries_inserted'] += 1

    def _format_citation(self, entry: Dict, num: int) -> Tuple[str, Optional[str], List[str]]:
        """
        Format a publication entry as Vancouver-style citation.

        Returns:
            (citation_text, target_name, enriched_fields) tuple
        """
        fields = entry.get('extracted_fields', {})
        enrichment = entry.get('enrichment_data', {})
        enriched_fields = entry.get('enriched_fields', [])  # Track which fields were enriched

        # Check if Stage 5d provided a pre-formatted citation (for non-enriched entries)
        formatted_citation = fields.get('formatted_citation', '')
        if formatted_citation and fields.get('formatting_source') == 'stage_5d_llm':
            # Use the LLM-formatted citation directly
            citation = f"{num}. {formatted_citation}"
            target_name = fields.get('target_name')
            return citation, target_name, enriched_fields

        parts = []

        # Authors - prefer enriched PubMed authors, fall back to extracted
        authors = enrichment.get('pubmed_authors') or fields.get('authors', '')
        if authors:
            # Clean and normalize author names
            authors = self._normalize_author_names(authors)
            parts.append(authors + ".")

        # Title - prefer enriched PubMed title, fall back to extracted
        title = enrichment.get('pubmed_title') or fields.get('title', '')
        if title:
            title = title.rstrip('.')
            parts.append(title + ".")

        # Journal or Book title - prefer enriched
        journal = enrichment.get('pubmed_journal') or fields.get('journal', '')
        book_title = fields.get('book_title', '')
        if journal:
            parts.append(journal + ".")
        elif book_title:
            # For book chapters (S4), use "In: Book Title"
            parts.append(f"In: {book_title}.")

        # Year;Volume(Issue):Pages
        year = str(fields.get('year', ''))
        volume = enrichment.get('pubmed_volume') or fields.get('volume', '')
        issue = enrichment.get('pubmed_issue') or fields.get('issue', '')
        pages = enrichment.get('pubmed_pages') or fields.get('pages', '')

        cit_parts = []
        if year:
            cit_parts.append(year)
        if volume:
            cit_parts.append(f";{volume}")
        if issue:
            cit_parts.append(f"({issue})")
        if pages:
            cit_parts.append(f":{pages}")

        if cit_parts:
            parts.append("".join(cit_parts) + ".")

        # Identifiers - separated by periods
        ids = []
        doi = fields.get('doi', '')
        pmid = fields.get('pmid', '')
        pmcid = fields.get('pmcid', '')

        if doi:
            ids.append(f"doi:{doi}.")
        if pmid:
            ids.append(f"PMID:{pmid}.")
        if pmcid:
            ids.append(f"PMCID:{pmcid}.")

        if ids:
            parts.append(" ".join(ids).rstrip('.') + ".")  # Ensure single final period

        citation = f"{num}. " + " ".join(parts)
        target_name = fields.get('target_name')

        return citation, target_name, enriched_fields

    def _normalize_author_names(self, authors: str) -> str:
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

    def _add_citation_with_bold_author(self, para: Paragraph, citation: str, target_name: Optional[str], cv_owner_last_name: str = ''):
        """
        Add citation text to paragraph, bolding the target author name.

        If target_name is not found, falls back to searching for cv_owner_last_name.
        """
        para.clear()

        # Determine what to bold
        name_to_bold = None
        if target_name and target_name in citation:
            name_to_bold = target_name
        elif cv_owner_last_name:
            # Fallback: find cv_owner's name in the citation using regex
            # Look for patterns like "Wende ME", "Wende, M", "Wende M.", etc.
            pattern = rf'\b{re.escape(cv_owner_last_name)}\s*[A-Z]{{0,3}}\.?\b'
            match = re.search(pattern, citation, re.IGNORECASE)
            if match:
                name_to_bold = match.group(0).rstrip('.,')

        if name_to_bold and name_to_bold in citation:
            # Split around target name
            idx = citation.index(name_to_bold)
            before = citation[:idx]
            after = citation[idx + len(name_to_bold):]

            # Add before (normal)
            if before:
                run1 = para.add_run(before)
                self._set_font(run1)

            # Add target name (bold)
            run2 = para.add_run(name_to_bold)
            self._set_font(run2, bold=True)
            self.stats['target_names_bolded'] += 1

            # Add after (normal)
            if after:
                run3 = para.add_run(after)
                self._set_font(run3)
        else:
            # No target name to bold
            run = para.add_run(citation)
            self._set_font(run)

    def _add_citation_with_bold_author_as_insertion(self, para: Paragraph, citation: str,
                                                     target_name: Optional[str], cv_owner_last_name: str = '',
                                                     author: str = "PubMed Enrichment"):
        """
        Add citation as a track change insertion, bolding the target author name.

        This creates proper Word track change structure with w:ins element,
        and includes bold formatting for the target author within the insertion.
        """
        # Issue #153: when track changes are disabled, render the citation as a
        # plain (non-tracked) paragraph with the target author bolded.
        if not self.emit_track_changes:
            self._add_citation_with_bold_author(para, citation, target_name, cv_owner_last_name)
            return
        try:
            revision_id = str(self._revision_id)
            self._revision_id += 1

            # Create the insertion element
            ins = OxmlElement('w:ins')
            ins.set(qn('w:id'), revision_id)
            ins.set(qn('w:author'), author)
            ins.set(qn('w:date'), datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))

            # Determine what to bold
            name_to_bold = None
            if target_name and target_name in citation:
                name_to_bold = target_name
            elif cv_owner_last_name:
                pattern = rf'\b{re.escape(cv_owner_last_name)}\s*[A-Z]{{0,3}}\.?\b'
                match = re.search(pattern, citation, re.IGNORECASE)
                if match:
                    name_to_bold = match.group(0).rstrip('.,')

            def create_run_element(text: str, bold: bool = False) -> Any:
                """Create a w:r element with text and optional bold."""
                run_elem = OxmlElement('w:r')
                rPr = OxmlElement('w:rPr')
                rFonts = OxmlElement('w:rFonts')
                rFonts.set(qn('w:ascii'), 'Arial')
                rFonts.set(qn('w:hAnsi'), 'Arial')
                rPr.append(rFonts)
                sz = OxmlElement('w:sz')
                sz.set(qn('w:val'), '22')  # 11pt
                rPr.append(sz)
                if bold:
                    b = OxmlElement('w:b')
                    rPr.append(b)
                run_elem.append(rPr)
                t = OxmlElement('w:t')
                t.text = text
                if text.startswith(' ') or text.endswith(' '):
                    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                run_elem.append(t)
                return run_elem

            if name_to_bold and name_to_bold in citation:
                # Split around target name
                idx = citation.index(name_to_bold)
                before = citation[:idx]
                after = citation[idx + len(name_to_bold):]

                # Add before (normal)
                if before:
                    ins.append(create_run_element(before, bold=False))

                # Add target name (bold)
                ins.append(create_run_element(name_to_bold, bold=True))
                self.stats['target_names_bolded'] += 1

                # Add after (normal)
                if after:
                    ins.append(create_run_element(after, bold=False))
            else:
                # No target name to bold - single run
                ins.append(create_run_element(citation, bold=False))

            # Append insertion to paragraph
            para._p.append(ins)
            self.stats['track_changes_added'] += 1

        except Exception as e:
            if self.verbose:
                print(f"  Warning: Could not add citation as insertion: {e}")
            # Fall back to normal citation
            self._add_citation_with_bold_author(para, citation, target_name, cv_owner_last_name)

    def _validate_output(self) -> List[Dict]:
        """Validate the generated document for common issues.

        Returns a list of structured warning dicts ({check, code, section,
        message, evidence}) — the message strings are what the VALIDATION
        WARNINGS banner prints, and the whole dict is persisted to the
        render-warnings sidecar for the run doctor (#228).
        This catches regressions in:
        - K sections: content should be bulleted, not combined into single entries
        - P section tables: should not have bare dates in column A
        - Other structural issues
        """
        issues = []

        # Check 1: Bulleted sections should have separate bullets, not semicolon-combined entries
        # This applies to K (Teaching), L (Clinical), and other bulleted sections
        bulleted_sections = {
            'Didactic teaching': 'K1',
            'Clinical teaching': 'K2',
            'Administrative teaching': 'K3',
            'Continuing education': 'K4',
            'Clinical Practice': 'L1',
            'Clinical Leadership': 'L3',
        }
        for section_text, code in bulleted_sections.items():
            section_idx = self._find_paragraph_with_text(section_text)
            if section_idx is None:
                continue

            # Look at the next few paragraphs after the section header
            for i in range(section_idx + 1, min(section_idx + 5, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if not para_text:
                    continue
                # Check if this looks like a combined entry (semicolon-separated list)
                if para_text.startswith('•') and para_text.count(';') > 3:
                    issues.append({
                        "check": "semicolon_fused_bullets",
                        "code": code,
                        "section": section_text,
                        "message": f"{code} ({section_text}): Content appears combined with semicolons instead of separate bullets",
                        "evidence": [para_text[:200]],
                    })
                break

        # Check 2: Committee/Administrative tables should not have bare dates in column A
        bare_date_pattern = re.compile(r'^[\|\s]*\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?[\s]*$', re.IGNORECASE)
        for table in self.doc.tables:
            if len(table.rows) < 2:
                continue
            first_cell = table.rows[0].cells[0].text.strip() if table.rows[0].cells else ''
            if 'Committee' not in first_cell and 'Activity' not in first_cell:
                continue

            bare_dates = []
            for row in table.rows[1:]:
                col_a = row.cells[0].text.strip() if row.cells else ''
                if bare_date_pattern.match(col_a):
                    bare_dates.append(col_a)

            if bare_dates:
                issues.append({
                    "check": "bare_dates_in_table",
                    "code": None,
                    "section": first_cell[:30],
                    "message": f"Table '{first_cell[:30]}': {len(bare_dates)} rows have bare dates in column A (should be filtered)",
                    "evidence": bare_dates[:3],
                })

        # Check 3: Teaching section should have visible content (not just track changes)
        teaching_idx = self._find_paragraph_with_text("EDUCATIONAL CONTRIBUTIONS")
        if teaching_idx is not None:
            has_visible_bullets = False
            for i in range(teaching_idx + 1, min(teaching_idx + 30, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if 'CLINICAL PRACTICE' in para_text.upper():
                    break
                if para_text.startswith('•') and len(para_text) > 5:
                    has_visible_bullets = True
                    break
            if not has_visible_bullets:
                issues.append({
                    "check": "no_visible_teaching_content",
                    "code": "K",
                    "section": "EDUCATIONAL CONTRIBUTIONS",
                    "message": "K (Teaching): No visible bulleted content found - may be using track changes only",
                    "evidence": [],
                })

        return issues


def run_stage6(input_path: str, output_path: str = None, verbose: bool = True,
               emit_track_changes: bool = True, emit_comments: bool = False,
               strip_template_instructions: bool = True,
               recover_unrendered_records: bool = True) -> str:
    """
    Run Stage 6 on a Stage 5 (or Stage 4) output file.

    Args:
        input_path: Path to enriched JSON file
        output_path: Optional output path
        verbose: Print progress
        emit_track_changes: Render edits as Word track changes (default True).
            When False, edits render as plain accepted text.
        emit_comments: Emit Word classification/pipeline comments (default False).
        recover_unrendered_records: Re-emit record lines of fused multi-record
            entries that the structured render provably dropped (#221;
            default True).

    Returns:
        Path to generated document
    """
    generator = WCMTemplateGenerator(
        verbose=verbose,
        emit_track_changes=emit_track_changes,
        emit_comments=emit_comments,
        strip_template_instructions=strip_template_instructions,
        recover_unrendered_records=recover_unrendered_records,
    )
    return generator.generate(input_path, output_path)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Stage 6: WCM Template Generation')
    parser.add_argument('input', help='Stage 5 enriched JSON file or document UID')
    parser.add_argument('--output', '-o', help='Output .docx path')
    parser.add_argument('--quiet', '-q', action='store_true', help='Suppress progress output')

    args = parser.parse_args()

    # Resolve input path - find the best source for entries
    # NOTE: Stage 4.5 only has research summary, not entries
    # So we need to find entries from Stage 5b/5/4, and research summary separately from Stage 4.5
    input_path = args.input
    if not os.path.exists(input_path):
        stage5b_dir = Path(__file__).parent / "outputs" / "stage_5b_institution_enrichment"
        stage5_dir = Path(__file__).parent / "outputs" / "stage_5_enrichment"
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"

        # Find entries from Stage 5b, then Stage 5, then Stage 4
        candidates = list(stage5b_dir.glob(f"*{input_path}*_institution_enriched.json"))
        if not candidates:
            candidates = list(stage5_dir.glob(f"*{input_path}*_enriched.json"))
        if not candidates:
            candidates = list(stage4_dir.glob(f"*{input_path}*_fields.json"))

        if candidates:
            input_path = str(candidates[0])
        else:
            print(f"Error: Could not find input file: {args.input}")
            sys.exit(1)

    # Note: The research summary from Stage 4.5 is auto-loaded by generate() based on document_uid
    output_path = run_stage6(input_path, args.output, verbose=not args.quiet)
    print(f"\nGenerated: {output_path}")


if __name__ == '__main__':
    main()
