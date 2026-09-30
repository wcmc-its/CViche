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

import logging
import functools
import os
import sys
import json
import traceback
from types import MappingProxyType
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Dict, List, Any, Literal, Optional, Tuple
from collections.abc import Callable
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# stats key counting _classify_geographic_scope LLM failures (#547).
GEO_SCOPE_FAILURE_STAT = 'geographic_classification_failures'

# stats key counting _reclassify_entry_segments LLM failures (#652).
RECLASSIFY_FAILURE_STAT = 'segment_reclassification_failures'

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
    logger.error("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from unified_pipeline.llm_client import call_llm
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.core.render_check import entry_fragments, entry_lines
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
    _CELL_PHONE_KEYS,
    _OFFICE_PHONE_KEYS,
    _HOME_PHONE_KEYS,
    _ALL_PHONE_SLOT_KEYS,
    _labels_its_own_phone_slots,
    _phone_cell_text,
    _PII_FRAGMENT_SPLIT_RE,
    _squash,
    _pii_fragments,
    _from_pii_fragment,
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
from unified_pipeline.stage6.dedup import (  # noqa: F401
    DEDUP_FULL_CONTAINMENT_MIN_TOKENS,
    DEDUP_FUSED_BLOB_RECORD_LINES,
    _STOP_WORDS,
    _drop_is_safe,
    _entry_signature_words,
    _entry_title_words,
    _significant_words,
    deduplicate_entries,
)
from unified_pipeline.stage6.render_check import (  # noqa: F401
    RECORD_DATE_LINE_MIN_CHARS,
    RENDER_PIECE_MIN_CHARS,
    RENDER_PIECE_WINDOW,
    RENDER_TOKEN_MIN_COUNT,
    RENDER_TOKEN_OVERLAP,
    RETIRED_TAXONOMY_CODES,
    UNRENDERED_MIN_RECORD_LINES,
    _COLUMN_HEADER_WORDS,
    _IDENTIFYING_FIELDS,
    _MONTH_WORDS,
    _RECORD_DATE_PREFIX_RE,
    _RENDER_TOKEN_RE,
    _entry_pieces,
    _is_column_header_row,
    _looks_like_record,
    _norm,
    _record_lines,
    _record_rendered,
    _value_is_datelike,
    normalize_retired_code,
    segment_already_rendered,
)
from unified_pipeline.stage4.schemas import FIELD_SCHEMAS
from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries
from unified_pipeline.stage6.pii_pass import (  # noqa: F401
    PII_REDACTED_NOTICE,
    WITHHELD_COMMENT_AUTHOR,
    PiiPassResult,
    relocate_withheld,
    run_pii_pass,
    withheld_comment_text,
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
from unified_pipeline.stage6.sections.appendix import (
    UnmappedEntry,
    build_appendix_diversion_warnings,
)
from unified_pipeline.stage6.sections.passthrough import PASSTHROUGH_CODES

from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)





# Keys observed in structured stage-4 `phone` values: cell, office, fax. Stage 4
# extracts with no schema, so the vocabulary is unbounded -- match on the key
# name, same as the address handling.








# Keys observed in structured stage-4 `address` values on the 2026-07-25 corpus:
# home_address, office_address, business_address. There is no convention — the
# LLM picks one — so match on all of them.






# A leading 3b taxonomy code (M2B, D1, S6, N3A …) that leaked into a rendered
# bullet — code letter + 1-2 digits + optional trailing letter, bracketed at the
# very start and followed by whitespace. Seen verbatim in output on the WCM-
# template CVs (issue #251): "• [M2B] Project title: …", "• [D1] Visiting Prof…".




# XML namespaces for Word documents
WORD_NAMESPACE = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14_NAMESPACE = 'http://schemas.microsoft.com/office/word/2010/wordml'
NSMAP = {
    'w': WORD_NAMESPACE,
    'w14': W14_NAMESPACE,
}


# Date format specifications per WCM template section
# Format codes: 'mm/yyyy', 'mm/yy', 'yyyy', 'mm/dd/yyyy'


# Month name -> month number, for the date parser below. Includes the common
# 3-4 letter abbreviations CVs use ("Aug", "Sept"). Distinct from _MONTH_NAMES
# further down, which is the reverse (number -> name) for range formatting.


def _is_bullet_paragraph(para: Paragraph) -> bool:
    """True for a bullet in either representation the renderer emits.

    Section K moved to real Word list paragraphs in #474, so a validator that
    tests for a literal "•" prefix stops seeing K at all -- and check 3
    below then reports no_visible_teaching_content on every CV. #483 (both
    passes) moved every emitter this module writes to the same real-list
    form, but a literal glyph can still arrive verbatim from the SOURCE docx
    (e.g. a table cell copied through unchanged -- see web240 in #483 R1's
    render-gate residue attribution), so both forms still have to count.
    """
    if para.text.strip().startswith('•'):
        return True
    pPr = para._p.pPr
    return pPr is not None and pPr.find(qn('w:numPr')) is not None


#: Punctuation a PII cut can leave dangling on the kept residual (#821 R2
#: F3 / #834): `pii_pass.py`'s `_cut_spans` removes exactly the withheld
#: SPAN `pii.py`'s `_pii_matches` found, which stops short of whichever
#: hard delimiter (`\n`, `\t`, `|`, `;`, or a run of whitespace --
#: `normalization/pii.py`'s own `_PII_FRAGMENT_SPLIT_RE`) used to separate
#: it from a kept neighbour: "Home Phone: 555-1234; Citizenship: US" ->
#: "; Citizenship: US" after the cut. `,` and `:` are deliberately absent
#: from this set -- they are ALLOWED PRECEDING punctuation for a label
#: match (`pii.py`'s `_boundary_ok`), not hard splits, so they never sit
#: alone at a residual's edge the way the four above do; stripping them
#: too would eat real content ("U.S." or a trailing "expires 2028:" left
#: as the last kept fragment).
_DANGLING_SEPARATOR_RE = re.compile(r'^[|;\s]+|[|;\s]+$')


def _strip_dangling_separators(text: str) -> str:
    """A PII cut's residual, its own leftover fragment-delimiter
    punctuation trimmed off both ends (#821 R2 F3 / #834; see
    `_DANGLING_SEPARATOR_RE`)."""
    return _DANGLING_SEPARATOR_RE.sub('', text)


def _pii_cut_left_a_bare_label(entry: Mapping[str, Any]) -> bool:
    """True when `run_pii_pass` cut a BARE LABEL off this entry and the
    label's own value is still sitting in the residual text, uncut and now
    unlabelled (#821 R2 F3 safety check; the corpus shape is a label and
    its value joined by a literal pipe).

    Every `WITHHOLD_POLICY` label row's span runs "from the opener's own
    start to the next hard delimiter" (`normalization/pii.py`'s
    `_label_spans`): a hard delimiter (`|`, a tab, 3+ spaces --
    `_PII_FRAGMENT_SPLIT_RE`) sitting directly after the label's colon --
    a "Home telephone: | <phone>" shape several A-coded orphan entries in
    this corpus use, one field per entry, label and value joined by " | "
    the way a two-cell table row is elsewhere in this pipeline -- stops the
    span AT the colon, so the phone number itself is never cut and survives
    as what looks like a safe residual; `_clean_inline_tabs` then drops the
    now-empty label cell entirely, leaving a bare, unlabelled protected
    value. Rendering the residual is refused whenever that happened -- the
    whole entry stays denied instead, the pre-#821-R2-F3 behavior.

    The verdict is the pass's own (`_pii_orphaned_value`, written by
    `_extend_bare_label_span`) rather than a re-read of the fragment
    strings, because by this point the strings cannot answer the question:
    "the cut fragment ends in a colon" is equally true of a label whose
    value was left behind and of a label that had NOTHING after it at all
    ("Citizenship: US\\nHome Address:" -- a template leftover, nothing
    protected, and refusing it cost the citizenship line, #821 R3 F-D).
    Only the pass still holds the text on both sides of the cut."""
    return bool(entry.get('_pii_orphaned_value'))













# Paths - Use the official WCM template
TEMPLATE_PATH = Path(__file__).parent.parent.parent / "key_files" / "wcm_cv_template_faculty_october_2022_final.docx"
OUTPUT_DIR = Path(__file__).parent / "outputs" / "stage_6_wcm_documents"
# Where sample source CVs live, for the generate() fallback that locates an
# original docx when the caller didn't pass one. Both drivers now pass
# original_doc_path (#550), as do scripts/render_gate.py --source-dir and
# stage 6's own tests, so this guess is only for a direct generate() call
# without one. (The web driver's _copy_to_pipeline_input drops each upload
# into this same directory under the run's uid, which is why the fallback
# fired on the web path even before it was wired explicitly.)
SAMPLE_CV_DIR = Path(__file__).parent.parent.parent / "data" / "sample_cvs" / "word"

# Fallback template paths
FALLBACK_TEMPLATES = (
    Path(__file__).parent / "cv_parser" / "cv_template_wcm.docx",
    Path(__file__).parent.parent.parent / "business" / "examples" / "template" / "wcm_cv_template_faculty_october_2022_final.docx",
)




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
    # NOTE: M4 clinical trial codes removed - clinical trials file as M2A (no end date) or M2B (ended) (#291)

    # Mentoring
    'N1': 'mentoring_leadership',
    'N2': 'training_grants',
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


# Taxonomy codes `generate()` treats as routed to a specific section, as
# opposed to falling through to the T. Appendix catch-all. Distinct from
# TAXONOMY_TO_SECTION above (which nominally covers 'T' too, for display/
# lookup purposes elsewhere) -- this is specifically the dispatch decision
# "did this code's entries already get placed by one of the _fill_* calls
# above". Hoisted from a local inside generate() so run_doctor's coverage
# lint (#529) can check real classification output against the exact same
# set generate() uses, instead of hand-maintaining a second copy that WILL
# drift from it.
RENDER_ROUTED_CODES = frozenset({
    'A',   # Personal Data (email, phone - but unused A entries go to appendix)
    'S0',  # Researcher Profiles section
    'B1',  # Education - Academic Degrees
    'B2',  # Education - Other Educational Experiences
    'C', 'C1', 'C2', 'C3',  # Postdoctoral Training (C generic; C1 postdoc
                            # research, C2 residency, C3 fellowship -- #573)
    'D1', 'D2', 'D3',  # Professional Positions
    'F1', 'F2',  # Licensure and Board Certification
    'H',   # Honors and Awards
    'I',   # Professional Memberships
    'K1', 'K2', 'K3', 'K4', 'K5',  # Teaching Activities
    'L1', 'L2', 'L3',  # Clinical Practice, Innovation, Leadership
    'M1',  # Research Summary (from Stage 4.5) -- see the conditional discard
           # in generate(): only routed when the summary actually rendered.
    'M2A', 'M2B', 'M2C',  # Research Support (grants and clinical trials)
    'M2D',  # Patents & Innovations
    'N1',  # Mentoring - Leadership and mentoring in programs (#529)
    'N2',  # Mentoring - Institutional Training Grants and Mentored Trainee
           # Grants (#529)
    'N3A', 'N3B',  # Mentoring (current/past mentees)
    'N4',  # Mentoring - outcome narrative lines; `_fill_mentoring` renders
           # them under the MENTORING header, so they must not also reach
           # the Appendix (#587)
    'O',   # Institutional Leadership
    'P',   # Administrative Committees
    'Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D',  # Service Activities
    'R',   # Invited Presentations
    'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9',  # Bibliography
    # NOTE: T is intentionally NOT here - T entries go to Appendix
})

# Position/training codes represent career-progression stages that share most
# words but differ in rank -- `_group_and_dedup_entries` uses date-aware dedup
# for them, merging only entries whose date ranges overlap or match. A
# taxonomy fact, not a setting (§7.2: no new configuration mechanism).
_DATE_AWARE_DEDUP_CODES = frozenset({'D1', 'D2', 'D3', 'C', 'B1'})

# Codes routed by the entry's own publication status, not by the heading it
# sits under: a submitted / in-review / in-preparation manuscript (S7) belongs
# in "In review" wherever the author listed it. A hierarchy mismatch never
# reroutes one of these -- status beats heading (#946: two submitted chapters
# under "Book Chapters" rendered under "Books").
_STATUS_ROUTED_CODES = frozenset({'S7'})

# (assigned, target) same-family pairs a hierarchy mismatch never reroutes:
# the target code's own `common_confusions` in core/taxonomy_v7.json names
# exactly this mistake ("CME ... misclassified as K1 instead of K4" on K1), so
# the heading is the weaker signal there (#946 item 3). A taxonomy fact; the
# test file pins each pair to its taxonomy_v7 text.
_TAXONOMY_WARNED_CONFUSIONS = frozenset({
    ('D3', 'D1'), ('K1', 'K4'), ('K4', 'K1'), ('K5', 'K4'),
    ('Q1', 'Q2'), ('Q2', 'Q3'), ('S1', 'S8'), ('S2', 'S1'),
})


_KEEP_SENTINEL = 'KEEP'
_TAXONOMY_PATH = Path(__file__).parent / "core" / "taxonomy_v7.json"


# Leading markdown list / quote / heading / emphasis markers on a reply line.
_MD_LINE_PREFIX = re.compile(r'^(?:[\s>#*_\-\u2022]+|\d+[.)]\s+)+')


@functools.cache
def _taxonomy_codes() -> frozenset[str]:
    """Every code in core/taxonomy_v7.json. A reclassification reply may only
    name one of these (or KEEP); a shape regex let 'ALL' and 'NOTE' through
    (#264)."""
    with open(_TAXONOMY_PATH, encoding="utf-8") as f:
        return frozenset(entry['code'] for entry in json.load(f)['codes'])


def parse_reclassified_segments(
        result_text: str, original_code: str) -> list[tuple[str, str | None]] | None:
    """Parse `_reclassify_entry_segments`' "CODE: text" lines.

    Returns [(segment_text, code)], or None when no line is usable. Only a
    colon-bearing line that opens with a real taxonomy code or KEEP becomes a
    segment; any other line is model commentary (a "Here is the analysis:"
    preamble, "**Rationale:**" bullets, "> **Note:** ..."), and folding it in
    as a segment renders it as a faculty-visible bullet (#264). Such lines are
    dropped one by one, not the whole reply: live replies routinely carry a
    preamble around valid code lines, and refusing them sent the entry back
    to the appendix. A reply with no valid line returns None, so the caller
    keeps the original entry text (fail closed).
    """
    segments: list[tuple[str, str | None]] = []
    for line in result_text.strip().split('\n'):
        line = line.strip()
        if not line or ':' not in line:
            continue
        code, segment_text = (part.strip() for part in line.split(':', 1))
        # A markdown list/emphasis wrapper around a real code ('- K2: ...',
        # '**K2:** ...', '1. K2: ...') is still a code line, not commentary.
        code = _MD_LINE_PREFIX.sub('', code).rstrip('*_ ').upper()
        segment_text = segment_text.lstrip('*_ ')
        if code != _KEEP_SENTINEL and code not in _taxonomy_codes():
            logger.warning(
                "Stage 6 reclassification reply: dropped non-code line "
                "with prefix %r", code[:40])
            continue
        if segment_text and len(segment_text) > 10:
            # KEEP means "correct as originally coded" -- resolve to the
            # original code so the caller can route it home instead of
            # dumping it in the appendix (#209).
            resolved = (original_code if original_code != '?' else None) if code == _KEEP_SENTINEL else code
            segments.append((segment_text, resolved))
    return segments or None


def _pick_mismatch_target(expected_codes: list[str]) -> str | None:
    """The one code a hierarchy mismatch should reroute to, or None to skip.

    The most specific (longest) expected code, provided every code tied at
    that length lands in the same WCM section; otherwise the heading does not
    say which section it means, and the classifier's own code stands. Before
    #946 `max(key=len)` took whichever tied code stage 3b's set happened to
    list first ("Book Chapters" -> ['S3', 'S4'] -> Books). `min()` of the
    tied codes, so the result never depends on `expected_codes` order."""
    longest = max(len(code) for code in expected_codes)
    candidates = [code for code in expected_codes if len(code) == longest]
    if len({TAXONOMY_TO_SECTION.get(code) for code in candidates}) > 1:
        return None
    return min(candidates)


def _merge_appendix_diversion_warnings(
    issues: list[dict], written: list[UnmappedEntry], recovered: list[str],
) -> list[dict]:
    """Append #531/#531-R2 per-(code, reason) Appendix-diversion warnings
    (from what `_fill_appendix`/`_add_remaining_to_appendix` report they
    wrote, never re-derived from the document) to *issues*; unchanged when
    there is nothing to add. Passes `PASSTHROUGH_CODES` down rather than
    letting `appendix.py` import it from `passthrough.py` directly -- both
    are `stage6/sections/*` peers (CODING_STANDARDS.md 1.3, `[gate]`); this
    module is not a peer of either and is free to import both (#531-R3
    task 4)."""
    if not written and not recovered:
        return issues
    return issues + build_appendix_diversion_warnings(
        written, recovered, RENDER_ROUTED_CODES, PASSTHROUGH_CODES)


def _log_validation_warnings(all_warnings: list[dict]) -> None:
    """Bannered `logger.warning` echo of `generate()`'s merged section
    failures + self-check findings, pulled out of `generate()` as a pure
    move (#839 -- keeps the ratchet-tracked §9 oversized-function row from
    rising) so it has its own name rather than growing that function."""
    if not all_warnings:
        return
    logger.warning("!" * 60)
    logger.warning("VALIDATION WARNINGS")
    logger.warning("!" * 60)
    for issue in all_warnings:
        logger.warning(f"  ⚠ {issue['message']}")
    logger.warning("!" * 60)


# Personal data that must not be carried onto a WCM CV. Source CVs routinely
# carry date/place of birth, marital status and family members' names in their
# contact block; a WCM CV must not.
#
# Matched as a LABEL PREFIX terminated by a colon, against fragments split out
# of the entry text. The colon terminator is load-bearing and must not be
# relaxed: allowing '-'/en-dash as a terminator, or slop before the colon,
# produces false positives on real CV content elsewhere in the corpus
# ("Children's Oncology Group - Emeritus", "Children and Fire: Research ...",
# "Children's Hospital Colorado - Pillar Award").
#
# Deliberately EXCLUDES gender/sex/race/ethnicity/religion. Those are ordinary
# research vocabulary -- a publication titled "Gender: A Review" would be one
# colon away from deletion -- and none occurs as a personal-data label anywhere
# in the corpus. Residual gap, accepted: a label written without a colon
# ("Date of Birth<tab>12/13/1947") evades this. Colon-less labels do occur in
# the corpus, just not yet on a PII label.

# Anchored whole-key match, so 'institutional_email' and friends can never hit.
# Needed alongside the label pattern: stage 4 leaves extracted_fields empty for
# most PII entries (caught by the label), but names some of them explicitly
# (marital_status_spouse, birthplace) where the source label is unusual.
# Every person stem takes the same optional suffix, so 'wife_name' is caught
# wherever 'spouse_name' is; the anchoring, not the suffix, is what keeps
# ordinary keys out.


# PII_REDACTED_NOTICE moved to `stage6/pii_pass.py` (#820 round 2) so the
# doctor lint can skip the notice paragraph without importing this module;
# re-exported above for the existing importers.








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

        # What the #820 pre-render pass withheld this run -- set by generate();
        # empty means no notice and no comment. Per-instance, never shared.
        self._pii_result = PiiPassResult()
        # Entry indexes whose non-withheld residual rendered in the Appendix
        # (#848); the withheld comment names "Appendix" for their items.
        self._appendix_withheld_entry_indexes: set[int] = set()

        # Content overflow tracking: entries where extraction lost significant content
        self._overflow_entries = []  # List of (entry, para, taxonomy_code) tuples

        # Appendix entries pending reconsideration
        self._appendix_pending = []  # List of (entry, coverage_pct) tuples

        # Per-section render failures caught by _render_section (#565): one
        # dict per isolated section that raised, merged into the
        # render-warnings sidecar ahead of the self-check findings. Reset at
        # the top of generate(), declared here for typing/reuse across renders.
        self._section_failures: list[dict[str, Any]] = []

        # Grant entries `_create_grant_table` declined as too sparse (#839) --
        # appended to `unmapped_entries` at `generate()`'s Appendix fill so
        # they still reach the Appendix and the `renderer_declined` warning
        # instead of vanishing. Reset at the top of generate(), declared here
        # for typing/reuse across renders.
        self._declined_grant_entries: list[dict] = []

        # Taxonomy codes owned by a section that raised (#842): removed from
        # mapped_codes before the unmapped sweep so a failed section's
        # entries fall to the Appendix instead of vanishing. Reset alongside
        # _section_failures.
        self._failed_section_codes: set[str] = set()

        # Memoizes _classify_geographic_scope's LLM calls for the life of one
        # render, keyed on (activity location, owner institutions).
        self._geographic_scope_cache = {}

        # Statistics
        # Tables already cleared this render, by element id. Guards against one
        # filler wiping another's rows when both resolve to the same table (#454).
        self._cleared_tables = set()
        # Body paragraphs `_insert_bulleted_entry` wrote this render. A header
        # anchor must never resolve to one of them (#548): an ALL-CAPS bullet
        # (the writer does not bold, but the shape test accepts bold too) would pass `_find_header_paragraph`'s shape test.
        self._bullet_paras = set()
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
            GEO_SCOPE_FAILURE_STAT: 0,
            RECLASSIFY_FAILURE_STAT: 0,
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
        - Same-family reroutes (e.g., K3→K1): applied only when every expected
          code renders in one WCM section, and the pair is not one the
          taxonomy warns about (`_TAXONOMY_WARNED_CONFUSIONS`). A heading that
          names codes in several sections ("Committees" -> P, Q2, O) does not
          say which one it means, and the longest-code pick only favoured the
          two-character code (#946 item 3: 200 corpus reroutes, most wrong).
        - Cross-family reroutes (e.g., C→K1): only applied when the LLM's confidence
          was low (< 0.7), since the content analysis may have been uncertain.
        - Never: a status-routed code (`_STATUS_ROUTED_CODES`, S7), or a
          heading whose expected codes tie across WCM sections
          (`_pick_mismatch_target`) (#946).

        Returns:
            The (possibly corrected) taxonomy code to use for routing.
        """
        if not entry.get('hierarchy_mismatch_flag') or assigned_code in _STATUS_ROUTED_CODES:
            return assigned_code

        detail = entry.get('hierarchy_mismatch_detail', {})
        expected_codes = detail.get('expected_codes', [])
        if not expected_codes:
            return assigned_code

        best_expected = _pick_mismatch_target(expected_codes)

        # Check if correction would change the WCM section (None: no target)
        assigned_section = TAXONOMY_TO_SECTION.get(assigned_code)
        expected_section = TAXONOMY_TO_SECTION.get(best_expected)
        if not expected_section or assigned_section == expected_section:
            return assigned_code

        # Same family: LLM got the broad category right, hierarchy knows the sub-type
        assigned_family = assigned_code[0] if assigned_code else ''
        expected_family = best_expected[0] if best_expected else ''
        confidence = entry.get('taxonomy_confidence', 1.0)

        if assigned_family == expected_family:
            if ((assigned_code, best_expected) in _TAXONOMY_WARNED_CONFUSIONS
                    or len({TAXONOMY_TO_SECTION.get(code) for code in expected_codes}) > 1):
                return assigned_code
            if self.verbose:
                logger.info(f"    Mismatch correction: {assigned_code}→{best_expected} "
                      f"(same family, hierarchy-guided) [{entry.get('text', '')[:60]}...]")
            entry['taxonomy_code_original'] = assigned_code
            entry['taxonomy_code'] = best_expected
            return best_expected

        # Cross-family: only if LLM confidence was low
        if confidence < 0.7:
            if self.verbose:
                logger.info(f"    Mismatch correction: {assigned_code}→{best_expected} "
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
            logger.info(f"  Replaced {replaced_count} K-code entries with Stage 5c formatted versions")

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
            logger.info(f"Removed {removed} WCM-template instruction box(es)")

    def _load_cv_owner_location_from_stage4(self, document_uid: str, cv_owner_location: dict[str, Any]) -> dict[str, Any]:
        stage4_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
        stage4_candidates = list(stage4_dir.glob(f"*{document_uid}*_fields.json"))
        if stage4_candidates:
            try:
                with open(stage4_candidates[0], 'r') as f:
                    stage4_data = json.load(f)
                cv_owner_location = stage4_data.get('cv_owner_location', {})
                if cv_owner_location and cv_owner_location.get('inference_success') and self.verbose:
                    logger.info("Loaded cv_owner_location from Stage 4 output")
            except (OSError, ValueError) as e:
                # Non-fatal: geographic-scope classification just falls back to its
                # default. Still say so -- a permission error or a truncated stage-4
                # JSON should not vanish without a trace. Only file and JSON errors
                # are expected here; a code bug must surface, not read as a missing
                # file (#531 review).
                if self.verbose:
                    logger.warning(f"Could not load cv_owner_location from Stage 4: {e}")
        return cv_owner_location

    def _resolve_original_doc_path(
            self, document_uid: str, original_doc_path: str | None,
            discover_original_doc: bool = True,
    ) -> str | None:
        """The original document path if not already given, or None.

        Both drivers pass one since #550, so this guess is reached only by a
        direct generate() call without it. Anchored on the module-relative
        `SAMPLE_CV_DIR` constant plus the process CWD, instead of a stack of
        brittle '..'/.parent chains that broke silently on any restructure.

        `discover_original_doc=False` skips the guess entirely (#732): the
        result is then exactly what the caller passed, so the render cannot
        depend on the launch directory or checkout. Default True keeps every
        other caller unchanged.

        Split out of `generate()` as a PURE move (#820 R3, §3.2); the
        `discover_original_doc` early return is the only addition.
        """
        if original_doc_path or not discover_original_doc:
            return original_doc_path
        possible_paths = [
            SAMPLE_CV_DIR / f"{document_uid}.docx",
            SAMPLE_CV_DIR / f"{document_uid}.doc",
            Path('data/sample_cvs/word') / f"{document_uid}.docx",  # relative to CWD
        ]
        for path in possible_paths:
            if path.exists():
                original_doc_path = str(path.resolve())
                if self.verbose:
                    logger.info(f"Found original document: {original_doc_path}")
                break
        else:
            if self.verbose:
                logger.info(f"No original document found for {document_uid} in "
                            f"{SAMPLE_CV_DIR} or ./data/sample_cvs/word")
        return original_doc_path

    def _load_research_summary_data(
            self, research_summary_path: str | None, input_path: str,
            document_uid: str) -> dict | None:
        """Stage 4.5 research-summary JSON, given explicitly or auto-found
        next to `input_path`; None when neither exists.

        Split out of `generate()` as a PURE move (#820 R3, §3.2): identical
        body, no behaviour change.
        """
        if research_summary_path and os.path.exists(research_summary_path):
            with open(research_summary_path, 'r') as f:
                research_summary_data = json.load(f)
            if self.verbose:
                logger.info(f"Loaded research summary from Stage 4.5: {research_summary_path}")
            return research_summary_data

        # Try to find it automatically
        input_dir = Path(input_path).parent.parent
        auto_summary_path = input_dir / "stage_4_5_research_summary" / f"{document_uid}_research_summary.json"
        if auto_summary_path.exists():
            with open(auto_summary_path, 'r') as f:
                research_summary_data = json.load(f)
            if self.verbose:
                logger.info(f"Auto-loaded research summary from: {auto_summary_path}")
            return research_summary_data
        return None

    def _group_entries_by_code(
        self, entries: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Group entries by taxonomy code, applying mismatch corrections.

        Split out of what #843 landed as `_group_and_dedup_entries` (itself
        a pure move out of `generate()`, #565 §3.2a) so the #820 piece-2 PII
        deny pass can run on the freshly-grouped `entries_by_code`, between
        this and `_dedup_grouped_entries` below -- a near-duplicate dedup is
        about to drop is still re-scanned by `_recover_unrendered_records`
        (`stage6/pii_pass.py`), so it needs its own strip too, before dedup
        ever removes it.

        #983: an entry whose stage-4 records sit under a key the schema does
        not define is first fanned out into one entry per record
        (`stage6/fan_out.py`), so grouping, the PII pass and dedup all see the
        records individually.
        """
        entries_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
        mismatch_corrections = 0
        entries = fan_out_multi_record_entries(entries, FIELD_SCHEMAS)
        for entry in entries:
            code = normalize_retired_code(entry)
            code = self._correct_mismatch_if_needed(entry, code)
            if code != entry.get('taxonomy_code', 'T'):
                mismatch_corrections += 1
            entries_by_code[code].append(entry)

        if self.verbose:
            logger.info(f"Taxonomy codes found: {sorted(entries_by_code.keys())}")
            if mismatch_corrections > 0:
                logger.info(f"  Hierarchy mismatch corrections applied: {mismatch_corrections}")
        return entries_by_code

    def _dedup_grouped_entries(
        self, entries_by_code: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        """Snapshot `entries_by_code` pre-dedup, then deduplicate within each
        taxonomy-code group. Second half of what #843 landed as
        `_group_and_dedup_entries` (see `_group_entries_by_code` for the
        first half and why the two were split). Behaviour unchanged.

        Returns (entries_by_code, pre_dedup_entries_by_code, dedup_decisions).
        ``pre_dedup_entries_by_code`` is the snapshot the #221 recovery pass
        scans: dedup keeps the longer near-duplicate, which can eat a unique
        record line fused into the dropped entry, so recovery needs the
        pre-dedup groups to re-verify against.
        """
        pre_dedup_entries_by_code = {code: list(group)
                                     for code, group in entries_by_code.items()}
        total_deduped = 0
        dedup_decisions: list[dict[str, Any]] = []
        for code in list(entries_by_code.keys()):
            before = len(entries_by_code[code])
            date_aware = code in _DATE_AWARE_DEDUP_CODES
            group_decisions: list[dict[str, Any]] = []
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
            logger.info(f"  Deduplicated: {total_deduped} near-duplicate entries removed")

        return entries_by_code, pre_dedup_entries_by_code, dedup_decisions

    def _run_pii_deny_pass(
        self, entries_by_code: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Run the pre-render PII deny pass over every entry, every code,
        and store its result onto `self._pii_result`.

        #820 piece 2: called from `generate()` on the freshly-grouped
        `entries_by_code`, BEFORE `_dedup_grouped_entries`'s pre-dedup
        snapshot -- a near-duplicate dedup is about to discard is still
        re-scanned by `_recover_unrendered_records` (`stage6/pii_pass.py`),
        so it needs this pass's strip too. Scope comes from the SAME
        routing set the appendix batch is built from. Read by the notice,
        the comment on it, and `_fill_personal_data`'s docx recovery (which
        appends).
        """
        self._pii_result = run_pii_pass(
            entries_by_code, routed_codes=RENDER_ROUTED_CODES,
            section_names=TAXONOMY_TO_SECTION)
        self._appendix_withheld_entry_indexes = set()

    def generate(self, input_path: str, output_path: str = None, research_summary_path: str = None,
                 original_doc_path: str = None, discover_original_doc: bool = True) -> str:
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
        # Reset per-render section-failure tracking (#565) -- a generator
        # instance can render more than once, and a failure from a prior
        # render must never leak into this one's sidecar.
        self._section_failures, self._failed_section_codes, self._declined_grant_entries = [], set(), []

        # Load input data - each stage output is self-contained
        with open(input_path, 'r') as f:
            data = json.load(f)

        document_uid = data.get('document_uid', 'unknown')
        entries = data.get('entries', [])
        cv_owner = data.get('cv_owner', {})
        cv_owner_location = data.get('cv_owner_location', {})

        # If cv_owner_location not in input file, try to load from Stage 4 output
        if not cv_owner_location or not cv_owner_location.get('inference_success'):
            cv_owner_location = self._load_cv_owner_location_from_stage4(document_uid, cv_owner_location)

        # Store location context for geographic scope classification
        self.cv_owner_location = cv_owner_location if cv_owner_location and cv_owner_location.get('inference_success') else None
        if self.cv_owner_location and self.verbose:
            metro = self.cv_owner_location.get('metro_area', '')
            primary = self.cv_owner_location.get('primary_location', {})
            if primary:
                logger.info(f"CV Owner Location: {primary.get('city', '')}, {primary.get('state', '')} (metro: {metro})")

        # Original-document discovery and the Stage 4.5 research-summary load
        # are lifted out to their own helpers (#820 R3, pure moves -- §3.2):
        # identical bodies, no behaviour change.
        original_doc_path = self._resolve_original_doc_path(
            document_uid, original_doc_path, discover_original_doc)
        research_summary_data = self._load_research_summary_data(
            research_summary_path, input_path, document_uid)

        if self.verbose:
            logger.info(f"\n{'='*60}")
            logger.info(f"Stage 6: WCM Template Generation - {document_uid}")
            logger.info(f"{'='*60}")
            logger.info(f"Total entries: {len(entries)}")

        # Group entries by taxonomy code (#843, split into
        # _group_entries_by_code / _dedup_grouped_entries so the #820
        # piece-2 PII deny pass can run between them -- see
        # _run_pii_deny_pass's docstring for why the ordering matters).
        entries_by_code = self._group_entries_by_code(entries)
        self._run_pii_deny_pass(entries_by_code)
        entries_by_code, pre_dedup_entries_by_code, dedup_decisions = \
            self._dedup_grouped_entries(entries_by_code)

        # Load template
        self.doc = Document(self.template_path)

        # Flatten all entries for fallback searches
        all_entries = [entry for entries in entries_by_code.values() for entry in entries]

        # Fill each section. _fill_personal_data is FATAL and stays outside
        # the boundary below by deliberate judgement call (#565): a document
        # with no owner on it is worse than a failed run, so its raise still
        # propagates out of generate() and no docx is written. Every other
        # section -- including passthrough and the appendix below -- runs
        # through _render_section, so one section raising costs that section
        # only; the rest still render and the run still produces a document.
        self._fill_personal_data(entries_by_code.get('A', []), cv_owner, document_uid, all_entries, original_doc_path)

        # (label, codes, callable), SAME order as the flat dispatch this
        # replaced (order pinned by test_stage6_section_boundary.py); codes are dropped from mapped_codes on failure (#842).
        section_dispatch: list[tuple[str, frozenset[str], Callable[[], Any]]] = [
            ('researcher_profiles', frozenset({'S0'}), lambda: self._fill_researcher_profiles(entries_by_code.get('S0', []))),  # S0 section for ORCID, etc.
            ('education', frozenset({'B1'}), lambda: self._fill_education(entries_by_code.get('B1', []))),  # B1 = Academic Degrees only
            ('other_education', frozenset({'B2'}), lambda: self._fill_other_education(entries_by_code.get('B2', []))),  # B2 = Other Educational Experiences
            ('postdoc_training', frozenset({'C', 'C1', 'C2', 'C3'}), lambda: self._fill_postdoc_training(entries_by_code, all_entries)),
            ('positions', frozenset({'D1', 'D2', 'D3'}), lambda: self._fill_positions(entries_by_code)),
            ('licensure', frozenset({'F1'}), lambda: self._fill_licensure(entries_by_code.get('F1', []))),  # F1 = Licensure
            ('board_certification', frozenset({'F2'}), lambda: self._fill_board_certification(entries_by_code.get('F2', []))),  # F2 = Board Certification
            ('honors', frozenset({'H'}), lambda: self._fill_honors(entries_by_code.get('H', []))),  # H = Honors and Awards
            ('memberships', frozenset({'I'}), lambda: self._fill_memberships(entries_by_code.get('I', []))),  # I = Professional Memberships
            ('teaching', frozenset({'K1', 'K2', 'K3', 'K4', 'K5'}), lambda: self._fill_teaching(entries_by_code, original_doc_path)),  # K1-K5 = Teaching Activities
            ('research_summary', frozenset({'M1'}), lambda: self._fill_research_summary(research_summary_data)),  # Stage 4.5 output
            ('research_support', frozenset({'M2A', 'M2B', 'M2C'}), lambda: self._fill_research_support(entries_by_code, cv_owner, document_uid)),
            # NOTE: Clinical trials now handled by _fill_research_support via M2A/M2B/M2C codes
            ('patents', frozenset({'M2D'}), lambda: self._fill_patents(entries_by_code.get('M2D', []))),
            ('mentoring', frozenset({'N1', 'N2', 'N3A', 'N3B', 'N4'}), lambda: self._fill_mentoring(entries_by_code)),
            ('clinical_practice', frozenset({'L1', 'L2', 'L3'}), lambda: self._fill_clinical_practice(entries_by_code)),  # L1, L2, L3 = Clinical Practice, Innovation, Leadership
            ('leadership', frozenset({'O'}), lambda: self._fill_leadership(entries_by_code.get('O', []))),  # O = Institutional Leadership
            ('administrative_activities', frozenset({'P'}), lambda: self._fill_administrative_activities(entries_by_code.get('P', []))),  # P = Administrative Committees
            ('service', frozenset({'Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D'}), lambda: self._fill_service(entries_by_code)),  # Q1-Q4D = Service Activities
            ('presentations', frozenset({'R'}), lambda: self._fill_presentations(entries_by_code.get('R', []))),  # R = Invited Presentations
            ('bibliography', frozenset({'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'}), lambda: self._fill_bibliography(entries_by_code, cv_owner, document_uid)),
        ]
        # In dispatch order; research_support returns the T goals rows it placed in a grant table (#958).
        section_results = {label: self._render_section(label, fn, codes) for label, codes, fn in section_dispatch}
        research_summary_rendered = bool(section_results['research_summary'])

        # Fill passthrough sections (Employment Status, Institutional Affiliation,
        # Percent Effort) -- copied from source CV when it matches WCM (#294, #260).
        passthrough_result = self._render_section(
            'passthrough_sections', lambda: self._fill_passthrough_sections(all_entries))
        consumed_ids = {id(e) for e in (passthrough_result or []) + (section_results['research_support'] or [])}

        # Add appendix for ALL unmapped content -- declined M2A/M2B/M2C
        # entries (#839) are appended at the fill below, not seeded here
        # (their code IS mapped). Local mutable copy of RENDER_ROUTED_CODES:
        # the M1 discard just below mutates it per-call, and a frozenset
        # shared across calls/runs would make that stick around (#580/#581).
        # Also drops any code a failed section owns (#842), so its entries fall to the Appendix.
        mapped_codes = set(RENDER_ROUTED_CODES) - self._failed_section_codes

        # M1 (Research Activities) entries are consumed by the Stage 4.5 research
        # summary. When that summary did NOT render (no Stage 4.5 output, empty
        # summary, or the template lacks a RESEARCH ACTIVITIES header), the M1
        # entries would otherwise render nowhere AND be excluded from the appendix
        # by being 'mapped' — a silent content loss (#317, C0ZGFW). Route them to
        # the appendix safety net instead. No-op when the summary rendered.
        if not research_summary_rendered:
            mapped_codes.discard('M1')

        unmapped_entries: list[dict] = []

        # Collect ALL entries not in mapped codes, excluding passthrough-consumed ones (#294, #260) and claimed goals rows (#958).
        for code, entries in entries_by_code.items():
            if code not in mapped_codes:
                unmapped_entries.extend(e for e in entries if id(e) not in consumed_ids)

        # A stays in mapped_codes, but NOT because its entries are all consumed
        # -- that was the old assumption here and the corpus refutes it (145 of
        # 306 A entries reach no Personal Data slot, across 80 of 100 CVs).
        # Routing them here is wrong regardless: at this point the document is
        # only part-rendered, so "was this content already placed?" cannot be
        # answered yet, and the CV owner's own name banner would be appended a
        # second time. They are recovered after every section has rendered, by
        # _unconsumed_personal_data_batch.

        written_appendix_entries: list[UnmappedEntry] = []
        if unmapped_entries or self._declined_grant_entries:
            written_appendix_entries = self._render_section(
                'appendix', lambda: self._fill_appendix(unmapped_entries + self._declined_grant_entries)) or []

        # Route content-overflow entries as tracked-change bullets
        self._route_overflow_entries()

        # Reconsider appendix entries (reclassify segments to other sections)
        # then recover unrendered records (#221, after reconsider so its
        # inserts count as rendered) -- both bullet leftover content into the
        # Appendix and report back each bullet's code (#531-R2 finding F1).
        recovered_appendix_codes = list(self._reconsider_appendix_entries() or [])
        recovered_appendix_codes += self._recover_unrendered_records(pre_dedup_entries_by_code) or []

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

        # Run post-generation validation to catch common issues. Section
        # failures caught by _render_section are merged in ahead of the
        # self-check findings (#565) -- one list feeds both the banner below
        # and the sidecar, so a missing section is never quieter than a
        # cosmetic self-check finding.
        validation_issues = self._validate_output()

        # Appendix-diversion warnings (#531, #531-R2) -- see the helper's
        # own docstring for what it merges and why.
        validation_issues = _merge_appendix_diversion_warnings(
            validation_issues, written_appendix_entries, recovered_appendix_codes)

        all_warnings = self._section_failures + validation_issues + self._llm_fallback_warnings()
        _log_validation_warnings(all_warnings)

        self._write_render_warnings_sidecar(output_path, document_uid, all_warnings, dedup_decisions)

        if self.verbose:
            logger.info(f"\n{'='*60}")
            logger.info("Generation Summary")
            logger.info(f"{'='*60}")
            logger.info(f"  Entries inserted: {self.stats['entries_inserted']}")
            logger.info(f"  Tables populated: {self.stats['tables_populated']}")
            logger.info(f"  Target names bolded: {self.stats['target_names_bolded']}")
            logger.info(f"  Track changes added: {self.stats['track_changes_added']}")
            logger.info(f"  Comments added: {self.stats['comments_added']}")
            if self.stats.get('overflow_bullets_added', 0) > 0:
                logger.info(f"  Overflow bullets added: {self.stats['overflow_bullets_added']}")
            if self.stats.get('overflow_to_appendix', 0) > 0:
                logger.info(f"  Overflow to appendix: {self.stats['overflow_to_appendix']}")
            if self.stats.get('appendix_segments_reconsidered', 0) > 0:
                logger.info(f"  Appendix segments reconsidered: {self.stats['appendix_segments_reconsidered']}")
            logger.info(f"\nSaved to: {output_path}")

        return output_path

    def _geo_scope_failure_warnings(self) -> list[dict[str, Any]]:
        """One sidecar WARN when any geographic-scope classification failed
        (#547), so the run doctor sees it. Empty when none failed."""
        failures = self.stats.get(GEO_SCOPE_FAILURE_STAT, 0)
        if not failures:
            return []
        return [{
            "check": GEO_SCOPE_FAILURE_STAT,
            "code": None,
            "section": "presentations/service",
            "message": (f"{failures} geographic scope classification(s) failed "
                        "and defaulted to National; the Regional/National/"
                        "International split may be wrong"),
            "evidence": [f"{GEO_SCOPE_FAILURE_STAT}={failures}"],
            "severity": "WARN",
        }]

    def _llm_fallback_warnings(self) -> list[dict[str, Any]]:
        """Sidecar WARNs for every counted LLM-fallback stat (#547, #652)."""
        return self._geo_scope_failure_warnings() + self._reclassify_failure_warnings()

    def _reclassify_failure_warnings(self) -> list[dict[str, Any]]:
        """One sidecar WARN when any appendix-entry segment reclassification
        failed (#652), so the run doctor sees it. Empty when none failed."""
        failures = self.stats.get(RECLASSIFY_FAILURE_STAT, 0)
        if not failures:
            return []
        return [{
            "check": RECLASSIFY_FAILURE_STAT,
            "code": None,
            "section": "appendix",
            "message": (f"{failures} appendix entry reclassification(s) failed; "
                        "those entries stayed in the appendix whole instead of "
                        "being split and routed to their sections"),
            "evidence": [f"{RECLASSIFY_FAILURE_STAT}={failures}"],
            "severity": "WARN",
        }]

    def _render_section(self, label: str, fn: Callable[[], Any],
                         codes: frozenset[str] = frozenset()) -> Any:  # noqa: ANN401
        """Call one section-dispatch entry, isolating a raise to this section
        only (#565). Returns fn()'s result on success; on any Exception it
        logs the traceback, records a severity-carrying failure onto
        ``self._section_failures`` (merged into the render-warnings sidecar
        by generate()), returns None so the caller can fall back, and
        records *codes* -- the taxonomy codes this section owns -- onto
        ``self._failed_section_codes`` so generate() discards them from
        ``mapped_codes`` and routes them to the Appendix instead of dropping
        them (#842). Passthrough and appendix callers pass no codes:
        passthrough already falls through via its return-value fallback,
        and the appendix has none to discard.

        Never swallows: every caught exception gets both the log line and
        the record (§5.4) -- an isolated section must fail loudly, or the
        isolation trades a whole-document crash for a silent partial render,
        which is worse.
        """
        try:
            return fn()
        except Exception as exc:
            logger.exception(
                "Stage 6: section %s failed; rendering the remaining sections", label)
            evidence = [line[:200] for line in traceback.format_exc().splitlines()[-3:]]
            self._section_failures.append({
                "check": "section_render_failed",
                "code": None,
                "section": label,
                "message": f"section {label} failed: {type(exc).__name__}: {exc}",
                "evidence": evidence,
                "severity": "ERROR",
            })
            self._failed_section_codes |= codes
            return None

    def _write_render_warnings_sidecar(self, output_path: str, document_uid: str,
                                        warnings: list[dict[str, Any]], dedup_decisions: list[dict[str, Any]]) -> None:
        """Persist the self-check + section-failure warnings and dedup
        decision trail next to the docx so the run doctor can re-emit them
        (#227/#228) — until now they only ever reached the pod log. Written
        even when empty, so the doctor can tell a clean run from a
        pre-sidecar build. Fail-soft: a sidecar failure must never fail the
        render.
        """
        try:
            report_path = Path(output_path).with_name(
                f"{document_uid}_render_warnings.json")
            report_path.write_text(json.dumps({
                "document_uid": document_uid,
                "warnings": warnings,
                "dedup_decisions": dedup_decisions,
            }, indent=2))
        except Exception:
            logger.exception("could not write render-warnings sidecar")






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

            # Apply header row background color (first row). The PERSONAL DATA
            # table is label|value rows with no header, so its first row
            # ("Office address:") stays unshaded (faculty feedback 2026-09-15);
            # recognised by its "Work email:" cell, the same anchor
            # _write_personal_data_table_cells uses to find it.
            if not any("work email:" in c.text.lower() for r in table.rows for c in r.cells):
                for cell in table.rows[0].cells:
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

        except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
            raise
        except Exception:
            # Never gated on verbose (#547): production runs are not verbose,
            # and an LLM outage would otherwise refile every presentation as
            # National with no record. The default itself is kept.
            logger.warning("Geographic scope classification failed; "
                           "defaulting to National", exc_info=True)
            self.stats[GEO_SCOPE_FAILURE_STAT] += 1
            return 'National'  # Default on error




    def _insert_bulleted_entry(self, insert_idx: int, text: str, entry: dict | None = None,
                                add_blank_before: bool = False,
                                list_level: int | None = None,
                                entry_sibling_paras: list[Paragraph] | None = None) -> Paragraph | None:
        """Insert a SINGLE bulleted entry paragraph as a real Word list item.

        NOTE: For multi-line content, use _insert_multiline_as_bullets() instead.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content for the entry (should be single line)
            entry: Optional entry dict for adding comments
            add_blank_before: If True, add a blank line before this entry
            list_level: The ilvl passed to _apply_list_bullet (#474). Every
                call site is expected to pass this explicitly; omitting it
                defaults to level 0 rather than falling back to a literal
                "• " glyph prefix (#483 -- the last caller-omittable glyph
                path, closed alongside _add_remaining_to_appendix).
            entry_sibling_paras: The OTHER paragraphs this same entry already
                rendered into, when a caller is splitting one entry across
                several bullets and attaching the entry to this one. Passed on
                to `_add_entry_comments`, whose low-coverage overflow check has
                to weigh everything the entry rendered rather than this single
                paragraph (#476 review). A caller that renders an entry as one
                paragraph leaves it None and the check is unchanged.

        Returns:
            The created paragraph, or None if insertion failed
        """
        if insert_idx >= len(self.doc.paragraphs):
            return None

        # Create the bulleted entry paragraph first
        entry_para = self.doc.paragraphs[insert_idx].insert_paragraph_before("")
        self._bullet_paras.add(entry_para._p)

        # Add blank line before if requested (insert before the entry we just created)
        if add_blank_before:
            # Insert blank before entry_para (which pushes entry down, so blank is above entry)
            entry_para.insert_paragraph_before("")

        body = _clean_inline_tabs(_strip_taxonomy_code(text))
        run = entry_para.add_run(body)
        _set_font(run)
        self._apply_list_bullet(entry_para, level=list_level if list_level is not None else 0)

        if entry:
            self._add_entry_comments(entry_para, entry,
                                     entry_paras=[entry_para, *(entry_sibling_paras or [])])

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
            'N1': 'Leadership and mentoring in programs (Describe activity; include dates)',
            'N2': 'Institutional Training Grants and Mentored Trainee Grants',
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

        Nor may it match a bullet an earlier section inserted (#548): those are
        skipped by identity, since a bullet can be ALL-CAPS and so pass the
        shape test below (which also accepts a bold run; no bullet writer bolds).
        """
        search = search_text.lower()
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            if len(text) < 3 or search not in text.lower():
                continue
            if para._p in self._bullet_paras:
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
                        logger.info(f"    Removing template instruction: '{para_text[:60]}...'")

        # Remove identified instruction paragraphs
        for para_elem in paragraphs_to_remove:
            try:
                body.remove(para_elem)
            except ValueError:
                pass  # Already removed or not in body

    def _clear_table_data(self, table: Table, keep_header: bool = True):
        """Record, then clear all data rows from `table`.

        The row removal itself is self-free and lives in
        `stage6.formatting.docx._clear_table_data` after the #398 split. What
        needs `self` is the bookkeeping: two fillers that resolve to the SAME
        table make the second one silently destroy the first one's rows -- see
        `_fill_other_service`, where a fuzzy anchor search sent Q4A onto the
        table Q1 had just filled and wiped 39 entries across 6 corpus CVs
        (#454). This wrapper is the thin delegating method
        `test_stage6_import_surface.py` asks a split to leave behind; it changes
        no rendering behaviour. The one caller that resolves its table by fuzzy
        search consults `self._cleared_tables` before clearing.
        """
        if not table:
            return
        self._cleared_tables.add(id(table._element))
        # Resolves to the module-level import at the top of this file: a class
        # attribute of the same name does not shadow a global inside a method.
        _clear_table_data(table, keep_header=keep_header)





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








    # Title strings that field extraction sometimes emits when the source CV had
    # a column header instead of a real role (mirrors the filter in
    # ``_add_position_row``). Treated as "no title" for grouping purposes.
    _PLACEHOLDER_TITLES = frozenset({'title', 'position', 'role', 'name',
                                     'description', 'activity'})





















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
            logger.info(f"\nRouting {len(self._overflow_entries)} content-overflow entries...")

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
                                    logger.info(f"  Added overflow bullets in section {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")
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
                        logger.info(f"  Added overflow bullet in section {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")
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
                logger.warning(f"  Warning: Could not insert overflow bullet: {e}")
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
                logger.warning(f"  Warning: Could not remove abbreviated entry: {e}")

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
            logger.info(f"  Queued for reconsideration: {taxonomy_code} ({coverage_pct:.0f}% coverage, {len(original_text)} chars)")

    def _reconsider_appendix_entries(self) -> list[str]:
        """Analyze appendix-pending entries and reclassify segments to appropriate sections.

        For each entry queued for appendix, this method:
        1. Segments the content into logical blocks (by sentence/paragraph)
        2. Uses LLM to classify each segment to a taxonomy code
        3. Routes segments to appropriate WCM sections as bullets
        4. Only truly unmappable content remains for the appendix

        Returns the taxonomy code of each bullet `_add_remaining_to_appendix`
        actually wrote for the entries that stayed unmappable (#531-R2
        finding F1) -- `[]` when nothing was pending or everything was
        reclassified elsewhere.
        """
        if not self._appendix_pending:
            return []

        if self.verbose:
            logger.info(f"\nReconsidering {len(self._appendix_pending)} appendix entries...")

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
        recovered_codes: list[str] = []
        if remaining_for_appendix:
            recovered_codes = self._add_remaining_to_appendix(remaining_for_appendix)

        if self.verbose and segments_to_route:
            logger.info(f"  Reclassified {len(segments_to_route)} segments to other sections")

        return recovered_codes

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
                temperature=0.3
                # No call-site cap: a truncated segment list silently loses the
                # trailing records (#209); the 16K DEFAULT_MAX_TOKENS floor still
                # bounds a runaway. Never add a tighter cap back.
            )

            return parse_reclassified_segments(
                llm_result["content"], original_code)

        except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
            raise
        except Exception:
            self._record_reclassify_failure()
            return None

    def _record_reclassify_failure(self) -> None:
        """Log and count one reclassification failure. Never gated on
        verbose (#652): production runs are not verbose, and an LLM outage
        would otherwise leave every appendix entry unsplit with no record."""
        logger.warning("LLM reclassification failed; entry stays in "
                       "the appendix unsplit", exc_info=True)
        self.stats[RECLASSIFY_FAILURE_STAT] += 1

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

        # Insert as a real Word list paragraph (#483 R2 -- the last of the
        # four glyph emitters #474/#483 tracked; its own tests in
        # test_stage6_unrendered_recovery.py were rewritten alongside this).
        try:
            insert_para = self.doc.paragraphs[insert_idx]
            new_para = insert_para.insert_paragraph_before()

            run = new_para.add_run(_clean_inline_tabs(_strip_taxonomy_code(text)))
            _set_font(run)
            self._apply_list_bullet(new_para, level=0)

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
                logger.info(f"    Inserted [{taxonomy_code}]: {text[:60]}...")
            return True

        except Exception as e:
            if self.verbose:
                logger.warning(f"  Warning: Could not insert reconsidered segment: {e}")
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

    def _add_remaining_to_appendix(self, remaining: List[Tuple[str, str, float]]) -> list[str]:
        """Add remaining unmappable segments to the appendix as bullet lines.

        Returns the taxonomy code of each segment actually written -- one
        entry per "• text" bullet, in write order -- the same "report back
        what was actually consumed" contract `_fill_appendix` and the
        passthrough writers use (#531-R2 finding F1). A segment this method
        drops (blank, template-instruction, source-boilerplate) is NOT in
        the returned list.
        """
        # Filter BEFORE creating the section header so an all-noise batch
        # doesn't leave an empty T. APPENDIX behind (#213).
        remaining = [
            (text, code, cov) for text, code, cov in remaining
            if text and text.strip()
            and not is_template_instruction(text)
            and not is_source_boilerplate(text)
        ]
        if not remaining:
            return []

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
            run = entry_para.add_run(segment_text)
            _set_font(run)
            self._apply_list_bullet(entry_para, level=0)
            if segment_text == PII_REDACTED_NOTICE:
                # The A-820 addendum: ONE sidebar comment on the notice
                # saying what the policy removed -- categories, counts and
                # sections, never a value. Not a classification comment, so
                # it is emitted whatever `emit_comments` says (#153).
                self._add_word_comment(
                    entry_para, withheld_comment_text(relocate_withheld(
                        self._pii_result.withheld,
                        self._appendix_withheld_entry_indexes)),
                    author=WITHHELD_COMMENT_AUTHOR, always=True)
                continue
            self._add_word_comment(
                entry_para,
                f"Originally classified {original_code}; could not be mapped "
                f"to a template section.",
                author="Classification",
            )

        return [code for _, code, _ in remaining]

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

    def _recover_unrendered_records(self, entries_by_code: Dict[str, List[Dict]]) -> list[str]:
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

        `self.recover_unrendered_records=False` disables ONLY the record-line
        recovery above -- never the #820 withheld notice and its Word comment
        below. Those must reach the reader whenever `_pii_result.withheld` is
        non-empty regardless of this flag (#820 R3 finding 3): the pass has
        already stripped the PII either way, and gating the reader's only
        indication of that behind an unrelated recovery toggle was a second,
        silent loss on top of the first.

        Returns the taxonomy code of each bullet `_add_remaining_to_appendix`
        actually wrote for the lines that landed in the appendix fallback
        (#531-R2 finding F1) -- `[]` when nothing fell through to the
        appendix. The #820 withheld notice is written by its own call and is
        never in the returned list: it is not a recovered entry.
        """
        out_lines = self._rendered_output_lines()
        haystack = "\x00".join(_squash(line) for line in out_lines)

        appendix_batch = []   # (text, code, coverage) for _add_remaining_to_appendix
        n_recovered = 0

        if self.recover_unrendered_records:
            line_token_sets = [set(_RENDER_TOKEN_RE.findall(_norm(line)))
                               for line in out_lines]

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

            # Unconsumed A-coded orphans (#316) are part of the SAME record-
            # recovery safety net the flag above governs -- gated with it,
            # unlike the notice below.
            appendix_batch.extend(self._unconsumed_personal_data_batch(haystack))

        recovered_codes: list[str] = []
        if appendix_batch:
            recovered_codes = self._add_remaining_to_appendix(appendix_batch)

        if self._pii_result.withheld:
            # The withheld notice is NOT part of the recovery safety net: the
            # #820 pass has already stripped the PII from `entry['text']`
            # regardless of this flag, so suppressing the reader's only
            # indication of that behind an unrelated toggle was a second,
            # silent loss on top of the first (#820 R3 finding 3). One
            # notice per document, not one per entry. Written by its own
            # call, after the recovered lines, so its 'A' never reaches the
            # appendix_diversion report as a recovered entry (#531).
            self._add_remaining_to_appendix([(PII_REDACTED_NOTICE, 'A', 0)])

        if self.verbose and n_recovered:
            logger.info(f"  Recovered {n_recovered} unrendered record line(s) "
                        f"({len(appendix_batch)} routed to appendix)")

        return recovered_codes

    def _unconsumed_personal_data_batch(self, haystack: str
                                        ) -> List[Tuple[str, str, float]]:
        """A-coded entries that reached no Personal Data slot and no page.

        'A' is listed in `mapped_codes`, whose comment says "unused A entries
        go to appendix" -- they did not. The exclusion covered the whole code,
        on the stated assumption that "A entries are all used in Personal Data
        section". The corpus refutes it: `_fill_personal_data` reads only
        phone/address/email, so an entry carrying "Citizenship: US", "Fax: ..."
        or "Foreign Languages: ..." is consumed by nothing and then excluded
        from the appendix as well. 145 such orphans exist across 80 of the 100
        corpus CVs.

        Two filters, at deliberately different granularities:

        - Already-rendered entries are skipped. Most orphans are the faculty
          member's own name/title banner, which renders from `cv_owner` rather
          than from the A entry -- 75 of 76 are already on the page, and
          appending them would be pure duplication.
        - A PII-withheld entry renders its RESIDUAL text -- `entry['text']`
          after `run_pii_pass` (#820 piece 2, `pii_pass.py`) has already cut
          only the withheld fragment(s) out of it (`_cut_spans`), not the
          whole entry (#821 R2 F3 / #834: a fused orphan carrying BOTH a
          withheld fragment and unrelated content -- e.g. "Home Phone: ..."
          fused with "Citizenship: US" -- used to lose the citizenship line
          too, because the whole entry was denied on the fragment-level
          pass's OWN `_pii_withheld` flag, a category mismatch: that flag
          means "this entry was TOUCHED", not "this entry is entirely PII").
          Rendering the residual only applies when the pass actually cut a
          RAW-TEXT fragment (`entry['_pii_fragments']` non-empty): a
          field-key-only withhold (the PII lived in `extracted_fields`,
          e.g. `marital_status_spouse`, and never appeared in `entry['text']`
          at all) leaves the raw text completely untouched -- usually the
          entry's own uninformative label ("Additional information") with
          nothing left to say once its one associated value is gone, so
          that whole entry stays denied, same as before this fix. Likewise
          a raw-text cut whose residual comes out empty (the whole text WAS
          the withheld value, e.g. a bare "Date of Birth: ...") still
          contributes nothing. Either way the single document-wide notice
          already tells the reader something was withheld.
        """
        batch: List[Tuple[str, str, float]] = []
        redacted = 0
        for entry in getattr(self, '_unconsumed_personal_data', []):
            # #820 piece 2 / #821 R2 F3: read the pre-render pass's own
            # verdict (`entry['_pii_withheld']`) and its already-cut
            # `entry['text']`, not a second PII scan. By this point
            # `run_pii_pass` has already stripped every in-scope fragment
            # and PII-keyed `extracted_fields` entry it found, so
            # re-running `_pii_fragments`/`_PII_FIELD_KEY_RE` here would
            # find nothing left to redact on -- the two call sites must not
            # diverge from the pass. What remains in `entry['text']` is, by
            # construction, content the policy did NOT withhold.
            raw_text = entry.get('text', '') or ''
            text = _clean_inline_tabs(raw_text).strip()
            if entry.get('_pii_withheld'):
                # A raw-text cut (`_pii_fragments` non-empty) can leave a
                # real residual; a field-key-only withhold cannot (the raw
                # text was never touched) -- see the method docstring. A
                # bare-label cut whose value the pass could not reach
                # (`_pii_cut_left_a_bare_label`) means the VALUE never got
                # cut at all and is hiding in what looks like a safe
                # residual -- refuse it too (#821 R2 F3 safety check).
                # Either way this entry had protected data removed from it,
                # so it counts as redacted (#821 R3 F-H). Before the #834
                # residual fix every withheld entry was dropped whole, so
                # "redacted" and "dropped" were one population; leaving the
                # counter on the drop-only branch silently redefined it as
                # "entries dropped" and under-reported the redaction on
                # exactly the entries the residual fix changed.
                # `personal_data_recovered` right below is the count of what
                # RENDERED, so the two together still separate the cases.
                redacted += 1
                fragments = entry.get('_pii_fragments') or []
                if fragments and not _pii_cut_left_a_bare_label(entry):
                    text = _strip_dangling_separators(text).strip()
                    if text and _squash(text) not in haystack:
                        batch.append((text, 'A', 0))
                        index = entry.get('_pii_entry_index')
                        if index is not None:
                            self._appendix_withheld_entry_indexes.add(index)
                        continue
                continue
            if not text:
                continue
            if _squash(text) in haystack:
                continue
            batch.append((text, 'A', 0))

        # The withheld notice itself is NOT added here: it must reach the
        # reader whenever `self._pii_result.withheld` is non-empty regardless
        # of `self.recover_unrendered_records`, and this method only runs
        # when that flag is on (#820 R3 finding 3) -- `_recover_unrendered_
        # records` appends the notice itself, unconditionally.
        self.stats['personal_data_recovered'] = len(batch)
        self.stats['personal_data_redacted'] = redacted
        return batch


    def _add_entry_comments(self, para: Paragraph, entry: dict,
                            entry_paras: list[Paragraph] | None = None) -> None:
        """Add all relevant comments from an entry to the paragraph.

        Collects comments from various upstream pipeline stages:
        - Stage 2/3: Classification reasoning, taxonomy assignment notes
        - Stage 4: Extraction notes, coverage warnings
        - Stage 5: Enrichment status, validation warnings

        `entry_paras` is every paragraph the entry rendered into, for the
        callers that split one entry across several bullets and attach the
        comments to the first of them. Only the low-coverage overflow check
        below reads it; comments themselves still go on `para`. Left None by
        every caller that renders an entry as a single paragraph, and the
        check then measures `para` exactly as it always did.
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
        fields = entry.get('extracted_fields') or {}
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
                    # Measure what the ENTRY rendered, not just `para`. The
                    # bullet fallbacks split one entry over N paragraphs and
                    # attach the comments to the FIRST of them, so reading
                    # `para.text` alone scored a fully-rendered entry at 1/N
                    # covered and handed it to `_route_overflow_entries`,
                    # which re-emitted the whole entry underneath the bullets
                    # that already carried every word of it (#476 review).
                    rendered_paras = entry_paras if entry_paras is not None else ([para] if para else [])
                    para_text_len = len(' '.join(p.text.strip() for p in rendered_paras).strip())
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
        fields = entry.get('extracted_fields') or {}
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

    def _add_word_comment(self, para: Paragraph, comment_text: str, author: str = "CV Pipeline",
                          always: bool = False):
        """Add a Word comment to a paragraph that appears in the sidebar.

        Creates proper Word comment structure with:
        - commentRangeStart/End markers in document
        - commentReference in the text
        - comment content stored for comments.xml

        A multi-line `comment_text` renders one comment paragraph per line
        (`_create_comments_xml`).
        """
        # Issue #153: when classification comments are disabled, emit nothing.
        # Skipping here means no commentReference is added and _comments stays
        # empty, so _finalize_comments never creates a comments.xml part.
        # `always=True` is for the one comment that is NOT a classification
        # comment -- the withheld-data summary (A-820 addendum) -- which the
        # reader must see whatever that toggle says.
        if not self.emit_comments and not always:
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
                logger.warning(f"  Warning: Could not add comment: {e}")

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

            # Add the text. #552: lxml's raw `.text` setter raises on the
            # same control-code range python-docx's own Run.text rejects --
            # sanitize before assignment so an LLM-written field with a
            # stray control character doesn't kill the render. \t\n\r are
            # valid XML and are preserved (test_cell_separators.py:34-37
            # pins the tab contract downstream of this text).
            t = OxmlElement('w:t')
            # Bind once and test the SANITIZED string in the xml:space guard
            # below: a control character sitting in front of a leading space
            # (`\x0b Smith`) is stripped, so the raw text no longer says
            # whether the rendered run starts or ends with whitespace. Testing
            # `text` there let Word collapse that space and glue the run to
            # its neighbour.
            clean_text = self._sanitize_run_text(text)
            t.text = clean_text
            if clean_text.startswith(' ') or clean_text.endswith(' '):
                t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            run_elem.append(t)

            ins.append(run_elem)

            # Append to paragraph
            para._p.append(ins)

            self.stats['track_changes_added'] += 1
            return ins
        except Exception as e:
            if self.verbose:
                logger.warning(f"  Warning: Could not add track change: {e}")
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

            # Add the deleted text element (w:delText instead of w:t). #552:
            # same lxml `.text` control-character raise as the insertion
            # path -- sanitize before assignment, preserving \t\n\r.
            delText = OxmlElement('w:delText')
            # Same ordering as the insertion path above: the xml:space guard
            # has to read the sanitized string, not the raw one.
            clean_text = self._sanitize_run_text(text)
            delText.text = clean_text
            if clean_text.startswith(' ') or clean_text.endswith(' '):
                delText.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            run_elem.append(delText)

            del_elem.append(run_elem)

            # Append to paragraph
            para._p.append(del_elem)

            self.stats['track_changes_added'] += 1
            return del_elem
        except Exception as e:
            if self.verbose:
                logger.warning(f"  Warning: Could not add track change deletion: {e}")
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
                    logger.info(f"  Created comments.xml with {len(self._comments)} comment(s)")
            else:
                # Append to existing comments
                # Parse existing and merge
                if self.verbose:
                    logger.info(f"  Added {len(self._comments)} comment(s) to existing comments.xml")

        except Exception as e:
            if self.verbose:
                logger.warning(f"  Warning: Could not create comments.xml: {e}")
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
            # One comment paragraph per text line: a newline inside a
            # single w:t collapses to a space in Word, which would run the
            # withheld-data summary's bullets together.
            for text_line in escaped_text.split("\n"):
                lines.append('<w:p>')
                lines.append('<w:r>')
                lines.append(f'<w:t xml:space="preserve">{text_line}</w:t>')
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
                para = self.doc.paragraphs[i]
                para_text = para.text.strip()
                if not para_text:
                    continue
                # Check if this looks like a combined entry (semicolon-separated list)
                if _is_bullet_paragraph(para) and para_text.count(';') > 3:
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
                para = self.doc.paragraphs[i]
                para_text = para.text.strip()
                if 'CLINICAL PRACTICE' in para_text.upper():
                    break
                if _is_bullet_paragraph(para) and len(para_text) > 5:
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


def run_stage6(input_path: str, output_path: str | None = None, verbose: bool = True,
               emit_track_changes: bool = True, emit_comments: bool = False,
               strip_template_instructions: bool = True,
               recover_unrendered_records: bool = True,
               original_doc_path: str | None = None,
               discover_original_doc: bool = True) -> str:
    r"""
    Run Stage 6 on a Stage 5 (or Stage 4) output file.

    Takes a PATH, not parsed data, and that is load-bearing for concurrency.
    Stage 6 rewrites entries in place as it renders: 12 sites, in four
    functions -- ``_correct_mismatch_if_needed`` here (4);
    ``stage6.render_check.normalize_retired_code`` (2);
    ``stage6.sections.positions._propagate_institution_to_subentries`` (3); and
    ``_copy_dates`` inside
    ``stage6.sections.positions._merge_grouped_appointments`` (3) -- touching
    six keys between them, not the two an earlier wording named:

    * ``entry['taxonomy_code']`` (3 sites), the rerouted code;
    * ``entry['taxonomy_code_original']`` (3), the code it was rerouted from,
      written beside each of those;
    * ``entry['extracted_fields']`` (2), installing a fresh ``{}`` on an entry
      that carried none, so the field writes below have somewhere to land;
    * ``fields['institution']`` (1), a parent's institution propagated into a
      sub-entry's ``extracted_fields``;
    * ``entry['institution_enrichment']`` (1), that parent's enrichment record
      copied across with it;
    * ``start_date`` and ``end_date`` inside ``extracted_fields`` (2), a
      grouped appointment's dates copied onto a member that has neither.

    Counted by::

        grep -rnE "[A-Za-z_][A-Za-z0-9_]*\['[a-z_]+'\] *=[^=]|\.setdefault\(" \
            src/unified_pipeline/stage_6_word_template.py src/unified_pipeline/stage6/

    which returns 21 lines today; the other 9 write to ``self.stats`` (2) or to
    a copy the function made itself (7: four in ``sections/research_support.py``,
    three in ``normalization/records.py``), so they reach no caller. Section M2 used to
    be among them -- it annotated ``entry['reclassification_note']`` and filled
    ``fields['percent_effort']`` -- but it now classifies on
    ``copy_entries_for_render`` clones, so those three writes stop at the
    section (``stage6/sections/research_support.py``). Re-run the grep rather
    than trusting the count: it is a snapshot, not an invariant.

    Because this function is handed a path and parses the JSON itself, every
    render owns the dicts it mutates, and the web path runs renders
    concurrently (``run_service.py`` starts each run in a thread and the
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
        original_doc_path: Optional path to the original Word document, for the
            personal-data fallback that recovers contact fields from it (#550).
            Both drivers pass the resolved source path; scripts/render_gate.py
            --source-dir supplies it the same way, through here rather than
            by constructing its own generator, so the gate measures the
            production entry point. Without it generate() falls back to the
            SAMPLE_CV_DIR guess.
        discover_original_doc: False renders with no source document when
            original_doc_path is None, skipping that guess (#732). Only
            scripts/render_gate.py passes it; default True.

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
    return generator.generate(input_path, output_path, original_doc_path=original_doc_path,
                              discover_original_doc=discover_original_doc)


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
            logger.error(f"Error: Could not find input file: {args.input}")
            sys.exit(1)

    # Note: The research summary from Stage 4.5 is auto-loaded by generate() based on document_uid
    output_path = run_stage6(input_path, args.output, verbose=not args.quiet)
    logger.info(f"\nGenerated: {output_path}")


if __name__ == '__main__':
    main()
