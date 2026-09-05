#!/usr/bin/env python3
"""
Unified Publication Classifier

Classifies publications using a two-step approach:
1. Step 1 (Rich): Use PubMed record if available (high confidence)
2. Step 2 (Lean): Fall back to text/DOI parsing (lower confidence)

Both steps feed the same classification logic for consistency.

Based on design by ChatGPT, implemented for Scholar Signals CV Pipeline.
Date: 2025-11-09
"""

import re
import hashlib
import json
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, asdict


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Features:
    """
    Normalized feature set for classification.

    Both PubMed-rich and text-only modes populate this structure,
    then the same classification logic runs on it.
    """
    # Basic metadata
    title: str = ''
    journal: str = ''
    pages: str = ''
    volume: str = ''
    issue: str = ''
    year: int | None = None
    doi: str = ''

    # Rich fields (from PubMed)
    has_abstract: bool = False
    pub_types: set[str] = None  # Set of PubMed publication types
    mesh_terms: set[str] = None
    statuses: set[str] = None

    # Derived boolean flags
    is_preprint: bool = False
    is_abstractish: bool = False
    is_published: bool = False
    is_case: bool = False
    is_reviewish: bool = False
    is_protocol: bool = False
    is_guideline: bool = False
    is_dataset_repo: bool = False
    is_software: bool = False
    is_data_paper: bool = False
    is_registry: bool = False
    is_errata: bool = False
    is_mediaish: bool = False
    has_research_cues: bool = False
    full_article_evidence: bool = False

    # Confidence adjustment
    missing_signal_penalty: int = 0

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.pub_types is None:
            self.pub_types = set()
        if self.mesh_terms is None:
            self.mesh_terms = set()
        if self.statuses is None:
            self.statuses = set()


@dataclass
class ClassificationResult:
    """
    Classification output with debugging info.
    """
    assignment: str  # S1-S9 or "Low confidence / No assignment"
    subtype: str | None = None  # S10-S30 (detailed subsection)
    confidence_pct: int = 0  # 0-100
    note: str | None = None

    # Debugging
    signals: dict | None = None
    applied_rules: list[str] | None = None

    def to_dict(self):
        """Convert to dict for JSON serialization."""
        return asdict(self)


# =============================================================================
# CONFIGURATION / CONSTANTS
# =============================================================================

# Regex patterns (compiled once for performance)
PATTERN_PREPRINT = re.compile(r'\b(10\.1101/|biorxiv|medrxiv|arxiv|osf preprint)\b', re.I)
PATTERN_ABSTRACTISH = re.compile(r'\b(suppl|supplement|proceedings?|abstracts?|poster|meeting|conference)\b|^\d+\.\d+$|^[A-Za-z]?\d{1,3}$', re.I)
PATTERN_RESEARCH_CUES = re.compile(r'\b(trial|cohort|case[- ]control|randomi[sz]ed|pilot study|validation|observational|retrospective|prospective)\b', re.I)
PATTERN_CASE_REPORT = re.compile(r'\bcase (report|study)\b(?!.*\bseries\b)(?!.*\bcontrols?\b)', re.I)
PATTERN_REVIEWISH = re.compile(r'\b(systematic review|meta[- ]analysis|scoping review|umbrella review|editorial|commentary|perspective|opinion)\b', re.I)
PATTERN_PROTOCOL = re.compile(r'\b(jove|star protocols|nature protocols|protocols\.io)\b', re.I)
PATTERN_DATA_JOURNAL = re.compile(r'\b(scientific data|gigascience|data in brief)\b', re.I)
PATTERN_REGISTRY = re.compile(r'\b(clinicaltrials\.gov|\bNCT\d+\b|\bIND\b|\bIDE\b|\bIRB\b)\b', re.I)
PATTERN_GUIDELINE = re.compile(r'\b(guideline|consensus|recommendation|statement|AHA|ACC|WHO|CDC)\b', re.I)
PATTERN_SOFTWARE = re.compile(r'\b(software|package|tool|app|v\d+\.\d+|github|pypi|cran)\b', re.I)
PATTERN_DATASET = re.compile(r'\b(zenodo|dryad|figshare|dataset|data repository)\b', re.I)
PATTERN_MEDIA = re.compile(r'\b(podcast|webinar|blog|youtube|video|interview)\b', re.I)

# Publication type sets (for PubMed classification)
ERRATA_TYPES = {'Published Erratum', 'Retraction of Publication', 'Retracted Publication', 'Expression of Concern'}
REVIEW_TYPES = {'Review', 'Systematic Review', 'Meta-Analysis', 'Scoping Review'}
EDITORIAL_TYPES = {'Editorial', 'Comment', 'Letter'}


# =============================================================================
# ENTRYPOINT: UNIFIED CLASSIFIER
# =============================================================================

def classify_publication_any(input_payload: dict) -> ClassificationResult:
    """
    Classify a publication using available information.

    Args:
        input_payload: Dict with keys:
            - pmid: str (optional)
            - pmcid: str (optional)
            - doi: str (optional)
            - pubmed_record: Dict (optional, from BulkPubMedFetcher)
            - citation_text: str (optional, raw citation string)
            - url_hint: str (optional)

    Returns:
        ClassificationResult
    """
    # Step 1: Try PubMed-rich mode if we have a record
    r1 = ClassificationResult(assignment="Low confidence / No assignment", confidence_pct=0)

    if input_payload.get('pubmed_record'):
        feat = build_features_from_pubmed(input_payload['pubmed_record'])
        r1 = classify_with_shared_logic(feat)

    # Step 2: If Step 1 absent or low confidence, try text/DOI mode
    if r1.confidence_pct < 50:
        feat2 = build_features_from_textish(
            citation_text=input_payload.get('citation_text', ''),
            doi=input_payload.get('doi', ''),
            url_hint=input_payload.get('url_hint', '')
        )
        r2 = classify_with_shared_logic(feat2)

        # Apply missing-signal penalty
        r2.confidence_pct = max(0, r2.confidence_pct - feat2.missing_signal_penalty)

        # Use whichever is better
        if r2.confidence_pct > r1.confidence_pct:
            return r2

    return r1


# =============================================================================
# FEATURE EXTRACTION: PUBMED MODE
# =============================================================================

def build_features_from_pubmed(pubmed_record: dict) -> Features:
    """
    Build features from a rich PubMed record.

    Args:
        pubmed_record: Dict from BulkPubMedFetcher

    Returns:
        Features object
    """
    feat = Features()

    # Basic metadata
    feat.title = pubmed_record.get('title', '')
    feat.journal = pubmed_record.get('journal_title', '')
    feat.pages = pubmed_record.get('pagination', '')
    feat.volume = pubmed_record.get('volume', '')
    feat.issue = pubmed_record.get('issue', '')
    feat.year = pubmed_record.get('pub_year')
    feat.doi = pubmed_record.get('doi', '')

    # Rich fields
    feat.has_abstract = bool(pubmed_record.get('abstract_text', '').strip())
    feat.pub_types = set(pubmed_record.get('publication_type_list', []))
    feat.mesh_terms = set(pubmed_record.get('mesh_terms', []))
    feat.statuses = set(pubmed_record.get('publication_statuses', []))

    # No missing signal penalty (we have everything)
    feat.missing_signal_penalty = 0

    # Derive flags
    derive_flags(feat)

    return feat


# =============================================================================
# FEATURE EXTRACTION: TEXT MODE
# =============================================================================

def build_features_from_textish(citation_text: str, doi: str, url_hint: str) -> Features:
    """
    Build features from text/DOI only (no PubMed record).

    Args:
        citation_text: Raw citation string
        doi: DOI string
        url_hint: URL (may contain hints)

    Returns:
        Features object
    """
    feat = Features()

    # Extract what we can from text
    feat.title = extract_title(citation_text)
    feat.journal = extract_journal(citation_text)
    feat.pages = regex_find_pages(citation_text)
    feat.volume = regex_find_volume(citation_text)
    feat.issue = regex_find_issue(citation_text)
    feat.year = regex_find_year(citation_text)
    feat.doi = doi or regex_find_doi(citation_text) or regex_find_doi(url_hint)

    # Unknown in text mode
    feat.has_abstract = False
    feat.pub_types = infer_pub_types_from_text(citation_text, feat.title)
    feat.mesh_terms = set()
    feat.statuses = set()

    # Calculate missing signal penalty
    missing_count = sum([
        not bool(feat.journal),
        not bool(feat.volume or feat.issue or feat.pages),
        not bool(feat.year),
        not bool(feat.doi)
    ])
    feat.missing_signal_penalty = {0: 0, 1: 5, 2: 10, 3: 15, 4: 20}.get(missing_count, 20)

    # Derive flags
    derive_flags(feat)

    return feat


def extract_title(citation_text: str) -> str:
    """Extract title from citation text (basic heuristic)."""
    # Title is usually in quotes or before journal name
    match = re.search(r'["""\'](.*?)["""\']', citation_text)
    if match:
        return match.group(1)
    return ''


def extract_journal(citation_text: str) -> str:
    """Extract journal name from citation text."""
    # Heuristic: look for italicized journal or pattern after title
    # This is simplistic - real implementation would be more sophisticated
    match = re.search(r'\.\s+([A-Z][A-Za-z\s&]+)\.\s+\d{4}', citation_text)
    if match:
        return match.group(1).strip()
    return ''


def regex_find_pages(text: str) -> str:
    """Find page numbers in citation."""
    match = re.search(r'\b(\d+)-(\d+)\b', text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    # Abstract pattern (single number or decimal)
    match = re.search(r'\b(\d+\.\d+|\d{3,4})\b', text)
    if match:
        return match.group(1)
    return ''


def regex_find_volume(text: str) -> str:
    """Find volume number."""
    match = re.search(r'\b(\d{1,3})\([^)]*\):', text)  # 30(4):
    if match:
        return match.group(1)
    return ''


def regex_find_issue(text: str) -> str:
    """Find issue number."""
    match = re.search(r'\((\d+)\):', text)  # (4):
    if match:
        return match.group(1)
    return ''


def regex_find_year(text: str) -> int | None:
    """Find publication year."""
    match = re.search(r'\b(19|20)\d{2}\b', text)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            pass
    return None


def regex_find_doi(text: str) -> str:
    """Find DOI in text."""
    if not text:
        return ''
    match = re.search(r'\b10\.\d{4,}/[^\s]+', text)
    if match:
        return match.group(0).rstrip('.,;')
    return ''


def infer_pub_types_from_text(citation_text: str, title: str) -> set[str]:
    """
    Infer PubMed-like publication types from text.

    Only infer types we're confident about. Empty set means uncertain.

    Args:
        citation_text: Full citation string
        title: Extracted title

    Returns:
        Set of inferred publication types
    """
    types = set()
    text_lower = (citation_text + " " + title).lower()

    # High-confidence inferences only
    if re.search(r'\b(systematic review|meta-analysis)\b', text_lower):
        types.add("Systematic Review")
        types.add("Review")
    elif re.search(r'\breview\b', text_lower) and not re.search(r'\bunder review\b', text_lower):
        types.add("Review")

    if re.search(r'\beditorial\b', text_lower):
        types.add("Editorial")

    if PATTERN_CASE_REPORT.search(text_lower):
        types.add("Case Reports")

    if re.search(r'\brandomized controlled trial\b', text_lower):
        types.add("Randomized Controlled Trial")
    elif re.search(r'\bclinical trial\b', text_lower):
        types.add("Clinical Trial")

    if re.search(r'\bletter\b', text_lower):
        types.add("Letter")

    if re.search(r'\bcomment(ary)?\b', text_lower):
        types.add("Comment")

    # Only add "Journal Article" if we have evidence of journal publication
    if re.search(r'\b\d{4};\d+', text_lower):  # e.g., "2024;30(4):123"
        types.add("Journal Article")

    return types


# =============================================================================
# FEATURE DERIVATION
# =============================================================================

def derive_flags(feat: Features):
    """
    Derive boolean flags from basic features.

    Modifies feat in-place.
    """
    combined_text = f"{feat.title} {feat.journal} {feat.pages}".lower()

    # Preprint detection
    feat.is_preprint = (
        PATTERN_PREPRINT.search(feat.doi or '') is not None
        or PATTERN_PREPRINT.search(feat.journal or '') is not None
    )

    # Abstract-only detection
    feat.is_abstractish = PATTERN_ABSTRACTISH.search(combined_text) is not None

    # Published status
    feat.is_published = bool(
        feat.volume or feat.issue or feat.pages or
        (feat.doi and not feat.is_preprint) or
        'Epub ahead of print' in feat.statuses
    )

    # Case report
    feat.is_case = (
        'Case Reports' in feat.pub_types or
        PATTERN_CASE_REPORT.search(combined_text) is not None
    )

    # Review/editorial
    feat.is_reviewish = (
        bool(feat.pub_types & REVIEW_TYPES) or
        bool(feat.pub_types & EDITORIAL_TYPES) or
        PATTERN_REVIEWISH.search(combined_text) is not None
    )

    # Research cues
    feat.has_research_cues = PATTERN_RESEARCH_CUES.search(feat.title) is not None

    # Protocol
    feat.is_protocol = (
        'Protocol' in feat.pub_types or
        PATTERN_PROTOCOL.search(feat.journal or '') is not None
    )

    # Guideline
    feat.is_guideline = (
        PATTERN_GUIDELINE.search(feat.title) is not None or
        PATTERN_GUIDELINE.search(feat.journal or '') is not None
    )

    # Dataset/software/registry
    feat.is_dataset_repo = PATTERN_DATASET.search(combined_text) is not None
    feat.is_software = PATTERN_SOFTWARE.search(combined_text) is not None
    feat.is_data_paper = PATTERN_DATA_JOURNAL.search(feat.journal or '') is not None
    feat.is_registry = PATTERN_REGISTRY.search(combined_text) is not None

    # Errata
    feat.is_errata = bool(feat.pub_types & ERRATA_TYPES)

    # Media
    feat.is_mediaish = PATTERN_MEDIA.search(combined_text) is not None

    # Full article evidence
    feat.full_article_evidence = (
        feat.has_abstract and
        '-' in (feat.pages or '')  # Has page range
    )


# =============================================================================
# CLASSIFICATION LOGIC (SHARED BY BOTH MODES)
# =============================================================================

def classify_with_shared_logic(feat: Features) -> ClassificationResult:
    """
    Core classification logic using normalized features.

    Works identically whether features came from PubMed or text parsing.

    Args:
        feat: Features object

    Returns:
        ClassificationResult
    """
    applied_rules = []

    # Priority 1: Errata/Retractions → S9
    if feat.is_errata:
        applied_rules.append('errata_detection')
        return rollup("S9", 80, "errata/retraction", applied_rules, feat)

    # Priority 2: Publication status (pre-publication overrides type)
    if not feat.is_published:
        if feat.is_preprint:
            applied_rules.append('preprint_status')
            return rollup("S10", 95, None, applied_rules, feat)
        if feat.is_registry:
            applied_rules.append('registry_unpublished')
            return rollup("S21", 90, None, applied_rules, feat)
        # Generic unpublished
        applied_rules.append('unpublished_status')
        return rollup("S7", 85, None, applied_rules, feat)

    # Priority 3: Format check (abstract-only overrides content type)
    if feat.is_abstractish and not feat.full_article_evidence:
        applied_rules.append('abstract_only')
        return rollup("S8", 90, None, applied_rules, feat)

    # Priority 4: Special document types
    if "Book" in feat.pub_types:
        applied_rules.append('pubtype_book')
        return rollup("S3", 95, None, applied_rules, feat)
    if "Book Chapter" in feat.pub_types:
        applied_rules.append('pubtype_chapter')
        return rollup("S4", 95, None, applied_rules, feat)

    # Priority 5: Non-traditional outputs
    if feat.is_software:
        applied_rules.append('software_detected')
        return rollup("S11", 85, None, applied_rules, feat)
    if feat.is_dataset_repo:
        applied_rules.append('dataset_repo')
        return rollup("S12", 90, None, applied_rules, feat)
    if feat.is_data_paper:
        applied_rules.append('data_descriptor_journal')
        return rollup("S16", 92, "S16→S1", applied_rules, feat)

    # Priority 6: Content-based classification
    if feat.is_case:
        applied_rules.append('case_report')
        return rollup("S6", 90, None, applied_rules, feat)
    if feat.is_protocol:
        applied_rules.append('protocol_detected')
        return rollup("S13", 90, "S13→S2", applied_rules, feat)
    if feat.is_guideline:
        applied_rules.append('guideline_detected')
        return rollup("S14", 90, "S14→S2", applied_rules, feat)
    if feat.is_reviewish or "Review" in feat.pub_types:
        applied_rules.append('review_detected')
        return rollup("S2", 90, None, applied_rules, feat)

    # Priority 7: Generic journal article (conservative)
    if "Journal Article" in feat.pub_types:
        if len(feat.pub_types) == 1:  # ONLY "Journal Article"
            applied_rules.append('journal_article_only')
            if feat.has_abstract and feat.has_research_cues:
                applied_rules.append('research_cues_present')
                return rollup("S1", 55, "low confidence JA-only", applied_rules, feat)
            else:
                applied_rules.append('ja_only_no_cues')
                return low_confidence(applied_rules, feat)
        else:  # Journal Article + other types
            applied_rules.append('journal_article_plus')
            return rollup("S1", 75, None, applied_rules, feat)

    # Priority 8: Fallbacks
    if feat.is_preprint:
        applied_rules.append('preprint_fallback')
        return rollup("S10", 80, None, applied_rules, feat)
    if feat.is_mediaish:
        applied_rules.append('media_fallback')
        return rollup("S9", 80, None, applied_rules, feat)

    # No match
    applied_rules.append('no_match')
    return low_confidence(applied_rules, feat)


def rollup(subtype: str, confidence: int, note: str | None,
          applied_rules: list[str], feat: Features) -> ClassificationResult:
    """
    Create classification result with rollup logic.

    Args:
        subtype: Detailed subsection (e.g., "S10", "S13→S2")
        confidence: Base confidence (0-100)
        note: Optional note
        applied_rules: List of rule IDs that matched
        feat: Features object (for debugging)

    Returns:
        ClassificationResult
    """
    # Rollup map (simplified - in production, load from database)
    ROLLUP = {
        'S1': 'S1', 'S2': 'S2', 'S3': 'S3', 'S4': 'S4', 'S5': 'S5',
        'S6': 'S6', 'S7': 'S7', 'S8': 'S8', 'S9': 'S9',
        'S10': 'S9', 'S11': 'S9', 'S12': 'S9', 'S13': 'S2',
        'S14': 'S2', 'S15': 'S7', 'S16': 'S1', 'S21': 'S5',
        'S22': 'S9', 'S23': 'S9', 'S24': 'S9', 'S25': 'S5', 'S30': 'S2'
    }

    # Parse subtype (handle "S13→S2" notation)
    primary_subtype = subtype.split('→')[0] if '→' in subtype else subtype
    assignment = ROLLUP.get(primary_subtype, primary_subtype)

    return ClassificationResult(
        assignment=assignment,
        subtype=primary_subtype,
        confidence_pct=confidence,
        note=note,
        signals=build_signals_dict(feat),
        applied_rules=applied_rules
    )


def low_confidence(applied_rules: list[str], feat: Features) -> ClassificationResult:
    """Return low confidence result."""
    return ClassificationResult(
        assignment="Low confidence / No assignment",
        subtype=None,
        confidence_pct=0,
        note="Insufficient evidence for classification",
        signals=build_signals_dict(feat),
        applied_rules=applied_rules
    )


def build_signals_dict(feat: Features) -> dict:
    """Build signals dict for debugging."""
    return {
        'is_preprint': feat.is_preprint,
        'is_abstractish': feat.is_abstractish,
        'is_published': feat.is_published,
        'is_case': feat.is_case,
        'is_reviewish': feat.is_reviewish,
        'is_protocol': feat.is_protocol,
        'is_guideline': feat.is_guideline,
        'has_research_cues': feat.has_research_cues,
        'full_article_evidence': feat.full_article_evidence,
        'pub_types': list(feat.pub_types),
        'missing_signal_penalty': feat.missing_signal_penalty
    }
