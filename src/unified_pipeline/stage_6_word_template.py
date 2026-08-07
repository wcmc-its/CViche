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
from types import MappingProxyType
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
# Every name below is re-exported from this module by being imported here: it is
# the public import surface (tests/test_stage6_import_surface.py pins 21 of them).
# run_full_pipeline.py and the backend orchestrator.py are parallel drivers over
# the same stage_* modules and share no code, so a name that moves without a
# re-export lands silently in whichever driver nobody ran. The blanket
# `noqa: F401` is deliberate -- several of these have no caller left in this file
# and exist purely to keep that surface intact.
from unified_pipeline.stage6.formatting import (  # noqa: F401
    DATE_FORMATS,
    _MONTH_NAMES,
    _clear_table_data,
    _format_citation,
    _format_currency,
    _format_mentee_duration,
    _set_cell_background,
    _set_cell_borders,
    _set_cell_text,
    _set_cell_vertical_alignment,
    _set_font,
    _set_paragraph_spacing,
    _set_table_border,
    format_date_for_section,
    format_date_range,
    normalize_iso_dates_in_text,
)
from unified_pipeline.stage6.parsing import (  # noqa: F401
    _MONTH_NAME_TO_NUM,
    _dates_overlap_or_match,
    _extract_last_name_from_uid,
    _extract_name_from_uid,
    _extract_year_from_text,
    _get_entry_date_range,
    _is_mentee_record,
    _is_mentoring_outcome,
    _is_orphan_fragment,
    _is_structural_label,
    _is_table_header_entry,
    _parse_date_components,
    _parse_multi_membership_entry,
)
from unified_pipeline.stage6.resolution import (  # noqa: F401
    _get_cv_owner_name,
    _get_institution_location,
    _recover_institution_from_nearby_entries,
)
from unified_pipeline.stage6.normalization import (  # noqa: F401
    _HOME_ADDRESS_KEYS,
    _OFFICE_ADDRESS_KEYS,
    _TAXONOMY_CODE_PREFIX,
    _address_cell_text,
    _clean_inline_tabs,
    _committee_cell_text,
    _deduplicate_repeated_content,
    _get_cleaned_institution_name,
    _labels_its_own_address_slots,
    _normalize_author_names,
    _strip_markdown_for_word,
    _strip_org_tail,
    _strip_taxonomy_code,
    grant_status_rebucket_target,
    split_fused_citation_entries,
)
from unified_pipeline.stage6.sorting import (  # noqa: F401
    element_idx_sort_key,
    extract_sort_date,
    sort_entries_reverse_chronological,
)
from unified_pipeline.stage6.sections import (  # noqa: F401
    AdministrativeActivitiesSection,
    AppendixSection,
    BibliographySection,
    BoardCertificationSection,
    ClinicalPracticeSection,
    EducationSection,
    HonorsSection,
    LeadershipSection,
    LicensureSection,
    MembershipsSection,
    MentoringSection,
    OtherEducationSection,
    PassthroughSection,
    PatentsSection,
    PersonalDataSection,
    PositionsSection,
    PostdocTrainingSection,
    PresentationsSection,
    ResearchSummarySection,
    ResearchSupportSection,
    ResearcherProfilesSection,
    ServiceSection,
    TeachingSection,
)
from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)


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


# Paths - Use the official WCM template
TEMPLATE_PATH = Path(__file__).parent.parent.parent / "key_files" / "wcm_cv_template_faculty_october_2022_final.docx"
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_6_wcm_documents"
# Local-dev only: where sample source CVs live, for the generate() fallback that
# locates an original docx when the caller didn't pass one. Absent in the
# deployed image (the server always passes original_doc_path explicitly).
SAMPLE_CV_DIR = Path(__file__).parent.parent.parent / "data" / "sample_cvs" / "word"

# Fallback template paths
FALLBACK_TEMPLATES = (
    Path(__file__).parent / "cv_parser" / "cv_template_wcm.docx",
    Path(__file__).parent.parent.parent / "business" / "examples" / "template" / "wcm_cv_template_faculty_october_2022_final.docx",
)


# Retired taxonomy codes that were pure renames of a still-live code. Stage-3b
# occasionally still emits the old code (e.g. patents tagged as the retired M3),
# which has no render route and gets silently dropped. Normalize to the live code
# at grouping time so the existing renderer picks them up.
# ponytail: pure renames only. Codes with NO live equivalent (N4, M4C) need a
# real render route instead — see #261; don't add them here.
RETIRED_TAXONOMY_CODES = MappingProxyType({
    'M3': 'M2D',  # Patents & Innovations — former M3 renamed to M2D (taxonomy v7)
})
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
TAXONOMY_TO_SECTION = MappingProxyType({
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
})


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


class WCMTemplateGenerator(AdministrativeActivitiesSection, AppendixSection,
                          BibliographySection, BoardCertificationSection,
                          ClinicalPracticeSection, EducationSection,
                          HonorsSection, LeadershipSection, LicensureSection,
                          MembershipsSection, MentoringSection,
                          OtherEducationSection, PassthroughSection,
                          PatentsSection, PersonalDataSection,
                          PositionsSection, PostdocTrainingSection,
                          PresentationsSection, ResearchSummarySection,
                          ResearchSupportSection, ResearcherProfilesSection,
                          ServiceSection, TeachingSection):
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
                    _set_cell_vertical_alignment(cell, 'center')
                    # Set paragraph spacing for all paragraphs in cell
                    for para in cell.paragraphs:
                        _set_paragraph_spacing(para, before_pt=4, after_pt=4)

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
                _set_cell_background(cell, gray_color)

            # Apply borders to all cells
            for row in table.rows:
                for cell in row.cells:
                    _set_cell_borders(cell, gray_color)



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

    def _insert_bulleted_entry(self, insert_idx: int, text: str, entry: Dict = None,
                                add_blank_before: bool = False) -> Optional[Paragraph]:
        """Insert a SINGLE bulleted entry paragraph with a bullet character prefix.

        NOTE: For multi-line content, use _insert_multiline_as_bullets() instead.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content for the entry (should be single line)
            entry: Optional entry dict for adding comments
            add_blank_before: If True, add a blank line before this entry

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

        # Use a simple bullet character prefix for reliable rendering
        # This avoids Word numbering system issues across different templates
        run = entry_para.add_run(f"• {_clean_inline_tabs(_strip_taxonomy_code(text))}")
        _set_font(run)

        if entry:
            self._add_entry_comments(entry_para, entry)

        self.stats['entries_inserted'] += 1
        return entry_para

    def _insert_bulleted_entry_with_track_changes(self, insert_idx: int, original_text: str,
                                                   new_text: str, entry: Dict = None,
                                                   add_blank_before: bool = False,
                                                   author: str = "LLM Formatter") -> Optional[Paragraph]:
        """Insert a bulleted entry showing original as deleted and new as inserted (track changes).

        Args:
            insert_idx: Index of paragraph to insert before
            original_text: Original text to show as deleted
            new_text: New formatted text to show as inserted
            entry: Optional entry dict for adding comments
            add_blank_before: If True, add a blank line before this entry
            author: Author name for the track change attribution

        Returns:
            The created paragraph, or None if insertion failed
        """
        if insert_idx >= len(self.doc.paragraphs):
            return None

        # Create the bulleted entry paragraph first
        entry_para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")

        # Add blank line before if requested
        if add_blank_before:
            entry_para.insert_paragraph_before("")

        # Add bullet prefix, then track change pair
        bullet_run = entry_para.add_run("• ")
        _set_font(bullet_run)

        # Add track change pair: deletion (original) then insertion (new)
        self._add_track_change_pair(entry_para, original_text, new_text, author=author)

        if entry:
            self._add_entry_comments(entry_para, entry)

        self.stats['entries_inserted'] += 1
        return entry_para


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
                _set_cell_vertical_alignment(cell, 'center')

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
                        _set_font(run, size=11, bold=is_header)

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
                _set_cell_vertical_alignment(cell, 'center')

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
                            _set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1


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
                _set_font(run)

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
            _set_font(run)

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
            _set_font(run, bold=True)
            run.underline = True

            intro_para = self.doc.add_paragraph()
            run = intro_para.add_run(
                "The following content from the original CV was not successfully mapped to this CV format:"
            )
            _set_font(run)
            self.doc.add_paragraph()

        # Add each remaining segment. The taxonomy code is an internal
        # pipeline identifier — keep it in a reviewer comment, never in the
        # faculty-facing text (#213).
        for segment_text, original_code, coverage_pct in remaining:
            entry_para = self.doc.add_paragraph()
            run = entry_para.add_run(f"• {segment_text}")
            _set_font(run)
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
            _set_font(run)
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
            _set_font(run)
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

    Takes a PATH, not parsed data, and that is load-bearing for concurrency.
    Stage 6 rewrites entries in place as it renders -- reassigning
    ``entry['taxonomy_code']`` when it reroutes a code, writing back
    ``entry['extracted_fields']``, annotating ``entry['reclassification_note']``
    -- 17 sites in all. Because this function is handed a path and parses the
    JSON itself, every render owns the dicts it mutates, and the web path runs
    renders concurrently (``run_service.py`` starts each run in a thread and the
    orchestrator hands each stage to ``asyncio.to_thread``).

    Passing already-parsed stage 5 data in here to save a re-parse would be a
    natural-looking optimisation and would silently break that: two concurrent
    renders would then rewrite one another's entries mid-render. If the
    in-memory interface is ever wanted, deep-copy at the boundary or make the
    rewrites non-destructive first.

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
