"""
Stage 3b: Entry Classification

Classifies individual CV entries to taxonomy codes using:
1. Entry content (primary signal)
2. Hierarchy context from Stage 3a (guidance/constraints)
3. Taxonomy confusion matrix (edge case handling)

Input:
  - Stage 2 entries (JSON)
  - Stage 3a header taxonomy mappings (JSON)
Output:
  - Classified entries with taxonomy codes (JSON)

The entry content drives classification, but hierarchy context helps
resolve ambiguity and provides constraints.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from dataclasses import dataclass, field
# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm

# Post-classification auto-correction validators
from core.validators.structural_header import apply_structural_corrections
from core.validators.committee_position_corrector import apply_committee_corrections
from core.validators.reasoning_consistency_checker import apply_reasoning_corrections
from core.validators.grant_status_corrector import apply_grant_status_corrections
from core.validators.hierarchy_mismatch_flagger import flag_hierarchy_mismatches, get_mismatch_summary
from core.validators.teaching_leadership_corrector import apply_teaching_leadership_corrections
from core.validators.leadership_level_corrector import apply_leadership_level_corrections
from core.validators.adjunct_position_corrector import apply_adjunct_position_corrections
from core.validators.position_subcode_reconciler import apply_position_subcode_reconciliation
from core.validators.training_compliance_corrector import apply_training_compliance_corrections
from core.validators.invited_talk_corrector import apply_invited_talk_corrections
from core.validators.grant_position_corrector import apply_grant_position_corrections
from core.validators.wcm_table_corrector import apply_wcm_table_corrections
from core.validators.prose_mentee_corrector import apply_prose_mentee_corrections
from core.validators.template_scaffold import apply_template_scaffold_corrections
from core.validators.block_coherence_corrector import apply_block_coherence_corrections

logger = logging.getLogger(__name__)


@dataclass
class TaxonomyContext:
    """Taxonomy suggestions for a hierarchy path."""
    subsection: Optional[Dict] = None  # Most specific (H3)
    section: Optional[Dict] = None      # Parent (H2)
    meta_section: Optional[Dict] = None # Top-level (H1)

    def get_primary_codes(self) -> List[str]:
        """Get primary suggested codes from most specific level."""
        if self.subsection and self.subsection.get("taxonomy_options"):
            return [o["code"] for o in self.subsection["taxonomy_options"]]
        if self.section and self.section.get("taxonomy_options"):
            return [o["code"] for o in self.section["taxonomy_options"]]
        if self.meta_section and self.meta_section.get("taxonomy_options"):
            return [o["code"] for o in self.meta_section["taxonomy_options"]]
        return []

    def get_all_suggested_codes(self) -> List[str]:
        """Get ALL suggested codes from ALL hierarchy levels (for taxonomy filtering)."""
        codes = set()
        if self.subsection and self.subsection.get("taxonomy_options"):
            codes.update(o["code"] for o in self.subsection["taxonomy_options"])
        if self.section and self.section.get("taxonomy_options"):
            codes.update(o["code"] for o in self.section["taxonomy_options"])
        if self.meta_section and self.meta_section.get("taxonomy_options"):
            codes.update(o["code"] for o in self.meta_section["taxonomy_options"])
        return list(codes)

    def format_context_string(self) -> str:
        """Format hierarchy context for LLM prompt with clear framing."""
        lines = []

        # Build hierarchy lines from top to bottom
        if self.meta_section:
            opts = self.meta_section.get("taxonomy_options", [])
            title = self.meta_section.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts[:3]])
                lines.append(f"  Top-level: {title}")
                lines.append(f"      → Suggested: {codes}")

        if self.section:
            opts = self.section.get("taxonomy_options", [])
            title = self.section.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts[:3]])
                lines.append(f"")
                lines.append(f"    Section: {title}")
                lines.append(f"      → Suggested: {codes}")

        if self.subsection:
            opts = self.subsection.get("taxonomy_options", [])
            title = self.subsection.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts])
                lines.append(f"")
                lines.append(f"      Subsection: {title}")
                lines.append(f"        → Suggested: {codes}")

        return "\n".join(lines) if lines else "  (No hierarchy context available)"


def _safe_float(value, default: float) -> float:
    """Coerce an LLM-provided numeric (e.g. a confidence) to float.

    The classifier LLM call uses ``response_format={"type": "json_object"}`` with
    no schema, so a confidence field can come back as a stringified number like
    ``"0.65"``. Downstream code compares it with ``< 0.7`` (in reconnect_fragments
    here and in stage_6_word_template.py), which raises
    ``TypeError: '<' not supported between instances of 'str' and 'float'`` and
    fails the entire run. Coerce defensively; fall back to ``default`` on anything
    non-numeric.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def load_taxonomy() -> Dict:
    """Load the taxonomy reference JSON."""
    taxonomy_path = Path(__file__).parent / "core" / "taxonomy_v7.json"
    with open(taxonomy_path, 'r') as f:
        return json.load(f)


def load_stage_2_entries(path: Path) -> Tuple[List[Dict], List[Dict]]:
    """
    Load Stage 2 entries, filtering to content entries only.

    Returns:
        Tuple of (content_entries, all_entries)
        - content_entries: Entries to classify (excludes headers and empty breaks)
        - all_entries: All entries for reference
    """
    with open(path, 'r') as f:
        data = json.load(f)

    all_entries = data.get("entries", [])

    # Filter to content entries only
    # - Always skip headers (section structure, not content)
    # - Skip "break" entries ONLY if they have no meaningful text content
    #   (Many "break" entries are actually content the LLM missed extracting)
    content_entries = []
    for e in all_entries:
        element_type = e.get("element_type", "")
        text = e.get("text", "").strip()

        # Always skip headers
        if element_type == "header":
            continue

        # For "break" entries, only skip if they're actually empty/whitespace
        if element_type == "break":
            if not text or len(text) < 3:  # Skip empty or trivially short breaks
                continue
            # Otherwise, this "break" has real content - include it for classification

        content_entries.append(e)

    return content_entries, all_entries


def load_stage_3a_mappings(path: Path) -> Dict:
    """Load Stage 3a header taxonomy mappings.

    ``mappings`` is unvalidated LLM output -- stage 3a parses it with
    ``json.loads()`` from a ``json_object``-mode call with no schema (#558).
    Nine call sites downstream subscript ``o["code"]``/``o["confidence"]``
    with no guard; a malformed option previously raised uncaught (KeyError
    on a missing "code", ValueError on a stringified confidence, TypeError
    on a bare-string option) and lost the entire run. Normalise once, here,
    before any of those sites ever see it.
    """
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"stage 3a artifact at {path} is not a JSON object (got "
            f"{type(data).__name__}) -- cannot contain a 'mappings' key"
        )
    mappings = data.get("mappings", [])
    if not isinstance(mappings, list):
        logger.warning(
            "stage 3a artifact %s: 'mappings' is not a list (%s) -- treating "
            "as empty", path, type(mappings).__name__)
        mappings = []
        data["mappings"] = mappings
    _normalize_taxonomy_mappings(mappings)
    return data


def _normalize_taxonomy_mappings(nodes: List[Dict]) -> None:
    """Recursively drop malformed nodes/taxonomy_options entries and coerce
    confidence to float, in place. See load_stage_3a_mappings (#558)."""
    nodes[:] = [n for n in nodes if isinstance(n, dict)]
    for node in nodes:
        title = node.get("title", "?")

        options = node.get("taxonomy_options")
        if isinstance(options, list):
            kept = []
            for option in options:
                if not isinstance(option, dict) or not option.get("code"):
                    logger.warning(
                        "stage 3a mapping %r: dropping malformed taxonomy_options "
                        "entry (%r) -- not an object or missing/empty 'code'", title, option)
                    continue
                option["code"] = str(option["code"])
                confidence = _safe_float(option.get("confidence"), 0.5)
                # _safe_float only guarantees convertibility, not domain -- a
                # confidence outside [0, 1] (including NaN/inf, which compare
                # false against any bound) falls back to the same 0.5 default
                # as an unparseable value.
                option["confidence"] = confidence if 0.0 <= confidence <= 1.0 else 0.5
                kept.append(option)
            if len(kept) != len(options):
                logger.warning(
                    "stage 3a mapping %r: taxonomy_options dropped from %d to %d "
                    "entries after normalisation", title, len(options), len(kept))
            node["taxonomy_options"] = kept
        elif options is not None:
            logger.warning(
                "stage 3a mapping %r: 'taxonomy_options' is not a list (%s) -- "
                "treating as empty", title, type(options).__name__)
            node["taxonomy_options"] = []

        children = node.get("children")
        if isinstance(children, list):
            _normalize_taxonomy_mappings(children)
        elif children is not None:
            logger.warning(
                "stage 3a mapping %r: 'children' is not a list (%s) -- treating "
                "as empty", title, type(children).__name__)
            node["children"] = []


def build_mapping_index(mappings: List[Dict], index: Dict = None, path: List[str] = None) -> Dict:
    """
    Build an index from header title to taxonomy mapping.

    Returns dict mapping title -> mapping node
    """
    if index is None:
        index = {}
    if path is None:
        path = []

    for node in mappings:
        title = node.get("title", "")
        # Store with full path for disambiguation
        full_path = tuple(path + [title])
        index[title] = node
        index[full_path] = node

        # Recurse into children
        children = node.get("children", [])
        if children:
            build_mapping_index(children, index, path + [title])

    return index


def get_taxonomy_context(hierarchy: List[str], mapping_index: Dict) -> TaxonomyContext:
    """
    Get taxonomy context for an entry's hierarchy path.

    Args:
        hierarchy: Entry's hierarchy path, e.g., ["RESEARCH AND SCHOLARSHIP", "Publications", "Books"]
        mapping_index: Index from build_mapping_index

    Returns:
        TaxonomyContext with mappings at each level
    """
    context = TaxonomyContext()

    if not hierarchy:
        return context

    # Try to match each level
    if len(hierarchy) >= 1:
        context.meta_section = mapping_index.get(hierarchy[0])

    if len(hierarchy) >= 2:
        context.section = mapping_index.get(hierarchy[1])
        # Try full path if simple lookup fails
        if not context.section:
            context.section = mapping_index.get(tuple(hierarchy[:2]))

    if len(hierarchy) >= 3:
        context.subsection = mapping_index.get(hierarchy[2])
        if not context.subsection:
            context.subsection = mapping_index.get(tuple(hierarchy[:3]))

    return context


def build_taxonomy_codes_for_prompt(
    taxonomy: Dict,
    relevant_families: List[str] = None,
    context_codes: List[str] = None
) -> str:
    """
    Build taxonomy reference for classification prompt.

    Includes full confusion/disambiguation info for context_codes (the codes
    suggested by the hierarchy), and basic info for other relevant families.

    Args:
        taxonomy: Full taxonomy dict
        relevant_families: Code families to include (e.g., ['S', 'H', 'T'])
        context_codes: Specific codes from hierarchy context - these get full
                      disambiguation notes (common_confusions, key_rules)

    Returns:
        Formatted taxonomy reference string
    """
    codes_list = taxonomy.get("codes", [])
    context_codes_set = set(context_codes) if context_codes else set()

    lines = []
    for code_def in codes_list:
        code = code_def["code"]

        # Filter to relevant families if specified
        if relevant_families:
            family = code[0]  # First letter is family
            if family not in relevant_families and code not in relevant_families:
                continue

        label = code_def["label"]
        purpose = code_def.get("purpose", "")

        lines.append(f"{code}: {label}")
        if purpose:
            lines.append(f"    {purpose}")

        # Include full disambiguation info for context codes
        if code in context_codes_set:
            common_confusions = code_def.get("common_confusions", [])
            key_rules = code_def.get("key_rules", [])

            if common_confusions:
                lines.append("    WATCH OUT:")
                for confusion in common_confusions:
                    lines.append(f"      - {confusion}")

            if key_rules:
                lines.append("    KEY RULES:")
                for rule in key_rules:
                    lines.append(f"      - {rule}")

    return "\n".join(lines)


_CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE = """You are an expert at classifying academic CV entries into a standardized taxonomy.

HIERARCHY CONTEXT:
  The following labels (Top-level, Section, Subsection) come directly from the original CV's
  internal structure. These are *not* authoritative taxonomy codes.

  SUGGESTED CODES: The codes in parentheses (e.g., "P 40%, Q1 30%") are automated
  first-pass suggestions that may be WRONG. They indicate what a heuristic system
  guessed based on section headers alone. Use them only as weak hints.

  CV authors typically organize their content with intention, so where an entry is placed
  in the CV is meaningful context about how the author views each entry. However, CVs vary
  widely in organization quality, and authors sometimes place entries in imperfect sections.

  GUIDELINE: Hierarchy should INFORM your classification but not be DETERMINATIVE.
  - When content clearly fits a single code (e.g., a journal article is S1 regardless of
    where it appears), that code takes precedence.
  - When content is ambiguous or could fit multiple codes (e.g., a lecture could be K1
    teaching, K5 community education, or R presentation), use the hierarchy to infer the
    CV author's likely intent and weight your classification accordingly.
  - Each entry below includes its specific CV section path. Use this per-entry context
    alongside the batch-level hierarchy above.

{context_str}

AVAILABLE TAXONOMY CODES (you may use ANY of these):
{taxonomy_ref}

════════════════════════════════════════════════════════════════════════════════
CV TAXONOMY CLASSIFICATION RULES v2.6
════════════════════════════════════════════════════════════════════════════════
Version: 2.6.0
Last Updated: 2025-01-28
Changes from v2.5:
  - Rule 16: Institutional affiliations (G) vs Professional societies (I) distinction.
  - Rule 17: Training Program Director/Co-Director → O (NOT K3). Faculty → K3.
  - Rule 3: Expanded structural header patterns (service headers, course headers).
Changes from v2.4:
  - Rule 15: Refined consulting distinction - Advisory (Q2) vs Professional/Statistical (D3).
  - Rule 17: Training Faculty on T32/grants → K3 (NOT D1).
  - Rule 26: Undergraduate honor societies (Phi Beta Kappa, etc.) → H (NOT I).
  - Renumbered rules 18-41.
Changes from v2.3:
  - Rule 35: Symposium chairing/organization → Q2 (NOT S8). Chairing ≠ Presenting.
  - Renumbered rules 36-40.
Changes from v2.2:
  - Rule 21: Poster/Abstract Reviewing → P or Q2 (NOT S8). Reviewing ≠ Presenting.
  - Rule 7: Hierarchy signals (Funded/Completed/Pending) override date-based M2A/B/C inference.
  - Renumbered rules 22-39.
Changes from v2.1:
  - Rule 15: Consultantships → Q2 (NOT D3). Consulting is external service.
  - Rule 16: Program membership ("Regular Member") → I (NOT D1).
  - Rule 7: Strengthened M2A/B/C deterministic rules (TBA→M2C, future dates→M2C).
Changes from v2.0:
  - Fixed Q3 overloading: Q3 is ONLY for grant reviewing/study sections.
  - Added Rule 25: Honors section mixed content handling.
  - Promotions in Honors sections → H (not D codes).
════════════════════════════════════════════════════════════════════════════════

These rules apply to every entry. Section headers on the CV are hints,
but CONTENT ALWAYS OVERRIDES HIERARCHY.

════════════════════════════════════════════════════════════════════════════════
A. GLOBAL PRINCIPLES AND "T" (APPENDIX/OTHER)
════════════════════════════════════════════════════════════════════════════════

1. CONTENT OVERRIDES HIERARCHY
   - Never trust section headers alone (e.g., "Other Publications",
     "Grants and Contracts", "Service", "Appendix").
   - Always classify based on the actual content of the entry:
     * If it clearly describes a grant funding the CV owner's research,
       classify as M2 (or as service/fragment per below), even if it
       appears under "Other Publications" or "Appendix".
     * If it clearly describes a publication, classify using S1–S9,
       even if it appears under a service or "Other" heading.
     * If it clearly describes a position, leadership role, teaching,
       mentoring, or service, prioritize those categories accordingly.

2. "T" IS A TRUE LAST RESORT
   - T is for genuinely miscellaneous or structural content after you
     have ruled out:
       * Grants (M2, or service P/Q2; see section C)
       * Research interests/themes (M1)
       * Researcher identifiers and bibliometrics (S0)
       * Publications/outputs (S1–S9)
       * Positions and appointments (C, D1–D3)
       * Leadership roles (O, Q1)
       * Service and outreach (P, Q2, Q3)
       * Editorial roles (Q4A–D)
       * Teaching (K1–K5)
       * Mentoring and mentees (N1–N4, especially N3A/N3B)
       * Clinical activity (L1–L3)
       * Honors/awards (H)
       * Memberships/fellow status (I)
   - If an entry plausibly fits any of these categories, prefer the
     specific code over T.

3. STRUCTURAL / NOISE ENTRIES (T OR SKIP)
   Treat these as structural artifacts or noise, not substantive content:
   a) Pure year/date lines:
      - "2013", "2019–2020", "2014–2016", "October 2021" etc. are markers.
      - Assign confidence 0.0 (effectively skip) or classify as T with
        very low confidence.
   b) Section/subsection headers → T (NOT the category they describe):
      RECOGNITION PATTERN - A line is likely a HEADER if it has:
      * Short length (typically 1-5 words)
      * No specific dates, date ranges, or years of activity
      * No specific person names, committee names, or organization names
      * No role/title the CV owner held (Member, Chair, PI, etc.)
      * No verbs describing actions taken
      * Often in Title Case or ALL CAPS
      * Describes a CATEGORY of content, not a specific entry

      EXAMPLES OF HEADERS (all → T):
      * Geographic scope: "International", "National", "Regional", "Local"
      * Activity types: "Invited", "Contributed", "Poster", "Oral"
      * Publication types: "Book Chapters", "Peer-Reviewed Publications"
      * Service categories: "Service to the University", "Committee Work",
        "Professional Service", "External Service", "Departmental Service"
      * Teaching categories: "Courses Taught", "Short Courses", "Teaching"
      * Any phrase like "[Category] Activities" or "Additional [Category]"

      KEY TEST: Does this text describe WHAT KIND of entries follow, or
      does it describe a SPECIFIC activity/achievement? If it's describing
      a category → T. If it's a specific entry → use appropriate code.

      CONTRAST:
      * "Service to the University" → T (header, category label)
      * "Member, Faculty Senate, 2018-2022" → P (specific service entry)
      * "SHORT COURSES" → T (header)
      * "Causal Inference Workshop, 2019" → K4 (specific course)
   c) Stray location/institution fragments:
      - Short lines like "Boston, MA", "Harvard Medical School",
        "University, Columbus, OH"
      - If there is no role, date, or verb, treat as T with low
        confidence (likely a broken-off piece of another entry).
   d) Orphaned budget/funding-only lines:
      - "$1,326,480 ($208,004), Ohio Department of Medicaid"
      - "$500,000, NIH" or "Government Resource Center, $2.1M"
      - If the line contains only dollar amounts and a sponsor,
        with no project title, role, or dates, treat it as a
        continuation fragment of a grant → T (low confidence).

════════════════════════════════════════════════════════════════════════════════
B. RESEARCH INTERESTS, IDENTIFIERS, AND GRAY LITERATURE
════════════════════════════════════════════════════════════════════════════════

4. RESEARCH INTERESTS / THEMES → ALWAYS M1 (NOT T)
   - If an entry describes research topics, themes, or areas of interest
     without being a specific publication, grant, or project, classify
     as M1. Examples:
     * "Occupational health and safety in healthcare settings"
     * "Exposure assessment for airborne hazards"
     * "Epidemiology of work-related respiratory disease"
   - These are research activity descriptions, not "other/appendix".

5. RESEARCHER IDENTIFIERS & BIBLIOMETRICS → ALWAYS S0
   - Entries that primarily describe researcher profile IDs or
     bibliometric metrics are S0 (never T, never A), regardless of
     section heading.
   - S0 signals:
     * ORCID (pattern: 0000-000X-XXXX-XXXX)
     * Google Scholar profile/URL
     * ResearchGate, Scopus Author ID, Web of Science ResearcherID,
       Publons, Semantic Scholar, Dimensions, Loop, Academia.edu
     * Bibliometric metrics: h-index, g-index, i10-index
     * "total citations", "times cited", "citation impact"
     * Field-Weighted Citation Impact (FWCI)
     * "over X publications", "authored X books"
   - Explicit NOT S0:
     * Course numbers (HIST 201, BIO 412)
     * Grant numbers (R01, U54, T32) → see M2 rules
     * Patent numbers (US 10,234,567) → M2D
     * Clinical trial numbers (NCT-XXXX) → M2A/M2B/M2C (based on status)
     * DOIs (10.XXXX/XXXX) → part of S1–S9 citations

6. GRAY LITERATURE, TECHNICAL REPORTS, AND "OTHER PUBLICATIONS"
   - Carefully distinguish S5, S7, and Q2; "Other Publications" is
     often a mix and should NOT all become S5 by default.
   - S5 = Technical reports & standards:
     * Official standards and specifications (ISO, HL7, W3C, etc.)
     * Implementation guides, clinical practice guidelines from
       professional societies
     * Government or institutional technical reports with formal
       publication/report numbers.
   - S7 = Working papers & other gray literature:
     * White papers, policy briefs, issue briefs, fact sheets
     * Consortium working documents, discussion papers
     * Preprints (arXiv, bioRxiv, medRxiv)
     * Manuscripts explicitly marked "in preparation", "submitted",
       "under review", "in revision"
   - Q2 = Service-related products:
     * Regulatory comments, advisory board statements, committee
       recommendations, task force reports when the primary context
       is committee/advisory service rather than a standalone
       scholarly output by the CV owner.
   - T = Internal or trivial memos/notes that clearly don't belong in
     S, M, Q, etc.

════════════════════════════════════════════════════════════════════════════════
C. GRANTS, FUNDING, CLINICAL TRIALS, AND CLINICAL ACTIVITY
════════════════════════════════════════════════════════════════════════════════

7. GRANTS AS RESEARCH FUNDING → M2 (NOT T, NOT GENERAL "OTHER")
   - When an entry describes funded research (or scholarship) where
     the CV owner is PI/Co-PI/Co-I/Site PI, classify as M2, regardless
     of the section header.
   - Strong M2 signals:
     * Grant numbers: R01, R21, R03, K08, K23, T32, U54, P30, P01,
       F31, F32, etc.
     * Major funders: NIH, NSF, CDC, NIOSH, DOD, VA, AHRQ, HRSA,
       foundations, etc.
     * Role indicators: PI, Co-PI, Co-I, Site PI, Mentor
     * Funding period: dates like "2019–2024"
     * Dollar amounts linked to projects: "$X,XXX,XXX"
   - Subtypes (DETERMINISTIC RULES - HIERARCHY OVERRIDES DATES):
     * HIERARCHY SIGNALS TAKE PRECEDENCE over date-based inference:
       - Section labeled "Funded", "Active", "Current" → M2A or M2B (not M2C)
       - Section labeled "Completed", "Past" → M2B (not M2A)
       - Section labeled "Pending", "Submitted", "Not Funded" → M2C
     * M2C (Pending) - use if ANY of these are true:
       - Listed under "Pending", "Submitted", or "Not Funded" hierarchy
       - Grant number contains "TBA", "TBD", "Pending", or is blank
       - Start date is in the future (after current year)
       - Status explicitly says "pending", "submitted", "under review"
     * M2B (Completed) - use if:
       - Listed under "Completed", "Past Funding", or similar hierarchy, OR
       - ALL dates are in the past AND none of the M2C signals are present
     * M2A (Active/Current) - use if:
       - Listed under "Active", "Current", or "Funded" hierarchy AND dates
         are not entirely in the past, OR
       - Grant period includes the current year (start ≤ now ≤ end) AND funded
     * When in doubt between M2A and M2B, prefer M2B (completed)

8. GRANT-WRITING AS SERVICE → P OR Q2 (NOT M2)
   - If the CV owner is providing grant-writing support *on behalf of*
     an institution or community organization and is NOT the PI/Co-I
     receiving research funding, treat this as service, not research
     funding:
     * Internal (home institution) → P
     * External organization (community org, NGO, society) → Q2
   - Example: "Wrote grants for [Community Organization]" → service, not M2.

9. ORPHANED FUNDING LINES → T
   - As noted in A3(d), lines that contain only dollar amounts and
     sponsors, without project titles, roles, or dates, are fragments
     of grants; classify as T (low confidence), not M2.

10. CLINICAL TRIALS → M2A/M2B/M2C (BASED ON STATUS)
    - Clinical trials are NOW classified as research funding (M2A/M2B/M2C):
      * Presence of NCT-XXXX identifiers.
      * Descriptions of interventional/observational/diagnostic trials.
    - Classification by STATUS (same rules as grants):
      * M2A = Active/ongoing clinical trials (currently recruiting/enrolling)
      * M2B = Completed clinical trials (enrollment closed, results published)
      * M2C = Planned/pending clinical trials (not yet started)
    - Apply the same hierarchy/date logic as grants (see Rule 7 above):
      * Section labeled "Active", "Current", "Ongoing" → M2A
      * Section labeled "Completed", "Past" → M2B
      * Section labeled "Pending", "Planned" → M2C

11. CLINICAL ACTIVITY CODES (L1–L3)
    - L1 = Direct patient care activities (clinical service).
    - L2 = Quality improvement or clinical innovation projects
      (improving clinical processes, workflows, or outcomes without
      a formal research protocol or publication plan).
    - L3 = Clinical leadership roles focused on clinical operations
      (e.g., Medical Director, Unit/Program Director, Clinical Chief,
      Director of Clinical Operations).
    - CROSS-REFERENCE: Clinical teaching (K2) occurs in patient care
      settings but is teaching, not clinical activity. See section H.

════════════════════════════════════════════════════════════════════════════════
D. POSITIONS, APPOINTMENTS, AND LEADERSHIP (C, D, O, Q1)
════════════════════════════════════════════════════════════════════════════════

12. TRAINING ROLES (C) VS POSITIONS (D1–D3) VS LEADERSHIP (O, Q1)
    - C = Training roles (postdoctoral fellow, research fellow, clinical
      fellow, resident, trainee), usually pre-faculty and often described
      as being "under supervision of" or "under the mentorship of" a
      specific person.
    - D1–D3 = Positions (who the person IS in terms of job rank).
      * D1: Academic appointments
        - Professor, Associate Professor, Assistant Professor,
          Instructor, Adjunct/Visiting/Affiliated faculty.
      * D2: Hospital/clinical appointments
        - Attending physician, Staff Physician, clinical appointments
          without explicit academic title.
      * D3: Other professional/industry positions
        - Research Scientist, Staff Scientist, Analyst, Biostatistician,
          industry roles, government staff roles lacking explicit
          faculty rank.
    - O = Internal leadership roles (what they GOVERN at the home
      institution):
      * Department Chair, Division Chief, Section Head, Unit Head
      * Director, Co-Director, Assistant/Associate Director
      * Program Director, Center Director, Manager
      * Associate/Assistant/Deputy/Vice Dean
      * Chief [X] (e.g., Chief Medical Officer) when this is an
        internal leadership post.
      * Coordinator, Program Leader, Program Manager, Team Lead
    - Q1 = Leadership roles in external organizations:
      * Officer/board roles: President, Vice President, Chair, Co-Chair,
        Board Chair, Treasurer, Secretary, "Program Lead", "Center Lead"
        for external bodies (societies, consortia, foundations).

13. VISITING TITLES
    - Visiting Professor/Scholar/Scientist at an academic institution
      (with "University", "College", "Medical School", or a university-
      affiliated institute) → D1.
    - Visiting Scientist at a non-academic research institution or
      government lab (e.g., some NIH/CDC/FDA intramural positions) →
      D2 or D3, depending on context (clinical vs research).
    - Always distinguish the academic rank (D1) from leadership
      responsibilities (O/Q1) if both appear in one entry.

14. KEY DISTINCTION: POSITION VS LEADERSHIP
    - D1 = "Who I am" (faculty rank/title)
    - O = "What I do" (administrative/leadership function)
    - Examples:
      * "Professor of Social Work" → D1 (academic rank)
      * "Director of Research, Age-Friendly Innovation Center" → O
      * "Associate Dean for Research" → O (administrative role)
      * "John Smith Endowed Chair in Gerontology" → D1 (prestigious appointment)

15. CONSULTANTSHIPS: ADVISORY (Q2) VS PROFESSIONAL (D3)
    - ADVISORY CONSULTING → Q2 (external service):
      * Advisory roles to organizations, agencies, or institutions:
        - "Consultant to NIH on [policy/program]" → Q2
        - "Advisory Consultant, [Foundation/Agency]" → Q2
        - "Expert Consultant, WHO" → Q2
      * Short-term advisory engagements without employment relationship
      * Consulting that is service-oriented (advising, reviewing, guiding)
    - PROFESSIONAL/STATISTICAL CONSULTING → D3 (employment):
      * Ongoing contracted work as a professional service provider:
        - "Statistical Consultant, [Company], [City]" → D3
        - "Biostatistical Consultant, [Firm]" → D3
        - "Data Analysis Consultant, [Company]" → D3
      * Key D3 signals: company name, city/location, date range suggesting
        ongoing professional relationship, "Statistical", "Biostatistical",
        "Data" in title
    - DISTINGUISHING RULE:
      * If consulting is SERVICE to an organization (advisory) → Q2
      * If consulting is WORK FOR an organization (contracted professional
        services, especially statistical/analytical) → D3
    - Summer internships at companies:
      * If framed as training/learning → C (training)
      * If framed as professional work → D3
    - HIERARCHY HINT: If the section is labeled "Professional Experience"
      or "Employment", prefer D3. If labeled "Service" or "Consulting
      (Advisory)", prefer Q2.

16. INSTITUTIONAL AFFILIATIONS (G) VS PROFESSIONAL SOCIETIES (I)
    - G = Institutional & Hospital Affiliations (INTERNAL to home institution):
      * Research centers, institutes, and programs at home institution:
        - "Berkeley Institute for Data Science (BIDS)" → G
        - "Center for Effective Global Action (CEGA)" → G
        - "Graduate Group in Biostatistics, UC Berkeley" → G
        - "Cardiovascular Research Institute" → G
      * Hospital privileges and clinical affiliations → G
      * Key signal: Institution name (University, College, Medical Center)
        appears in the affiliation
    - I = Professional Organizations & Society Memberships (EXTERNAL):
      * National/international professional societies:
        - "Member, American Psychological Association" → I
        - "Fellow, American Statistical Association" → I
        - "Member, Society for Epidemiologic Research" → I
      * Key signal: "Association", "Society", "College of [Specialty]",
        "Academy of [Field]" without institutional affiliation
    - DISTINGUISHING RULE:
      * If it's at your home institution (center, institute, program) → G
      * If it's an external professional society/organization → I
    - D1 is reserved for actual faculty titles: Professor, Associate
      Professor, Assistant Professor, Instructor, Lecturer, Adjunct.
    - Exception: "Faculty Member" with explicit faculty designation → D1.

17. TRAINING PROGRAM ROLES: FACULTY (K3) VS DIRECTOR (O)
    - TRAINING FACULTY → K3 (educational/administrative teaching):
      * "Training Faculty, T32 Training Grant" → K3
      * "Faculty, NIDA T32 Pre-doctoral Training Program" → K3
      * "Training Program Faculty, [Grant Name]" → K3
      * These roles involve mentoring/teaching trainees, which is an
        educational function (K3).
    - DIRECTOR/CO-DIRECTOR OF TRAINING PROGRAMS → O (leadership):
      * "Director, T32 Training Program" → O
      * "Co-Director, NIH T32 Computational Social Science Training Program" → O
      * "Program Director, K12 Career Development Program" → O
      * Directors have authority over program operations, budgets, and
        personnel, which is leadership (O), not just teaching (K3).
    - KEY DISTINCTION:
      * "Training Faculty" (participant in training) → K3
      * "Director/Co-Director" (leads the training program) → O
    - D1 is for formal academic appointments (Professor, etc.), not
      grant-specific training roles.
    - The grant itself may also be coded M2 separately.

════════════════════════════════════════════════════════════════════════════════
E. SERVICE, MEMBERSHIP, ADVOCACY, AND POLICY (P, Q1–Q4, H, I)
════════════════════════════════════════════════════════════════════════════════

18. INTERNAL VS EXTERNAL SERVICE (P VS Q1/Q2)
    - P = Internal service at the CV owner's home institution:
      * Departmental committees, faculty senate, IRB at home institution,
        curriculum committees, internal task forces.
    - Q1/Q2 = Service in external organizations:
      * External consortia, societies, foundations, standards bodies,
        multi-institutional initiatives, government advisory councils.
      * NEVER classify external service as P.

19. EXTERNAL LEADERSHIP VS PARTICIPATION (Q1 VS Q2)
    - Q1 = Leadership in external organizations:
      * Titles like Chair, Co-Chair, President, Vice President,
        Board Chair, Steering Committee Chair, Director, Program Lead.
    - Q2 = Non-leadership external roles:
      * Member, Board Member (with no officer title), Committee Member,
        Working Group Member, "Executive member" (membership, not
        officer), Task Force Member.

20. GRANT REVIEWING & STUDY SECTIONS (Q3)
    - Service as a grant reviewer or member of grant review panels/study
      sections is Q3:
      * NIH Study Section, NSF review panel, similar roles for other
        funders.
    - Q2 vs Q3:
      * Q3: activities focused on reviewing grants/papers.
      * Q2: committee/board/advisory roles where review is not the
        primary function.

21. EDITORIAL ROLES (Q4A–Q4D)
    - Q4A = Editor-in-Chief, Senior Editor, Co-Editor.
    - Q4B = Associate Editor, Section Editor, Guest Editor, Textbook Editor.
    - Q4C = Editorial Board Member.
    - Q4D = Peer reviewer or ad hoc reviewer for journals, books, or
      conferences.

22. POSTER/ABSTRACT REVIEWING → P OR Q2 (NOT S8)
    - Reviewing posters or abstracts at conferences is SERVICE, not presenting:
      * "Poster Reviewer, [Conference/Event]" → P (if internal) or Q2 (if external)
      * "Abstract Reviewer, [Conference]" → Q2 (external conference service)
      * "Judge, [Poster Competition]" → P or Q2
    - CRITICAL DISTINCTION:
      * PRESENTING a poster = S8 (the CV owner's own scholarly output)
      * REVIEWING/JUDGING posters = P or Q2 (service evaluating others' work)
    - Internal events (home institution, departmental):
      * "Poster Reviewer, D.K. Stanley Day at University of Florida" → P
      * "Judge, Undergraduate Research Symposium" → P
    - External events (national conferences, other institutions):
      * "Abstract Reviewer, Society for X Annual Meeting" → Q2
      * "Poster Judge, National Conference on Y" → Q2

23. MEDIA APPEARANCES & PUBLIC OUTREACH (R OR S9)
    - NOTE: Q3 is ONLY for grant reviewing/study sections. Do NOT use Q3
      for public outreach or media appearances.
    - Media interviews and press activities → S9 (Other Media):
      * TV/radio interviews, podcasts, newspaper/magazine interviews,
        press releases, media statements.
    - Public talks to lay audiences → R (Invited Talks) if invited, or
      S8 if part of a conference/meeting:
      * Schools, churches, community groups, rotary clubs, local
        governments, patient advocacy groups.
      * Public forums, town halls, community health fairs.
    - Op-eds: Op-eds in newspapers/mass media → S9 or S7 (gray lit).
    - Patient education materials (brochures, handouts) → S5.

24. POLICY TESTIMONY & REGULATORY ENGAGEMENT (Q2)
    - Contributions to government and regulatory processes are external
      service (Q2), not T:
      * Testimony before legislatures or committees.
      * Public or regulatory comments to agencies (OSHA, EPA, FDA, etc.).
      * Advisory contributions to government bodies.
      * Expert witness testimony in regulatory/policy contexts.

25. COMMUNITY SERVICE & ADVOCACY (P OR Q2)
    - Community/volunteer service is P (internal) or Q2 (external):
      * Volunteer roles, community committee service, pro bono
        consulting, community advisory boards, coalitions.
      * Examples: "Volunteer, Medical Reserve Corps"; "Member, Trails
        Committee"; "Community advocate on [issue]".
      * Grants written on behalf of community organizations (not the
        CV owner's own research funding) are P or Q2 service, NOT M2.

26. HONORS & MEMBERSHIPS (H VS I)
    - H = Honors and awards:
      * One-time recognitions (e.g., "Best Paper Award", "Young
        Investigator Award", "Elected to National Academy" if framed
        as an honor).
      * PROMOTIONS listed under Honors/Awards sections → H (the honor
        is the recognition of advancement, not the position itself).
        Example: "Promotion to Career Scientist" under Honors → H.
      * Do NOT use D1/D2/D3 for promotions in Honors sections.
      * UNDERGRADUATE HONOR SOCIETIES → H (NOT I):
        - Phi Beta Kappa, Psi Chi, Phi Sigma Tau, Sigma Xi, etc.
        - These are one-time academic recognitions, not ongoing
          professional society memberships.
        - Key signal: "Honors Society" in name + listed under HONORS section
        - Even though they have "society" in the name, they are
          recognition-based elections, not professional memberships.
    - I = Memberships and fellow status:
      * Ongoing memberships and professional society fellowships
        (with or without years), e.g., "Member, American College of X",
        "Fellow, [Society]", post-nominal letters (FACP, FAHA, etc.).
      * Professional societies where membership requires dues, active
        participation, or represents ongoing affiliation (APA, AMA, etc.).

27. HONORS SECTION MIXED CONTENT (SPECIAL HANDLING)
    - CV authors often list diverse achievements under "Honors/Awards":
      * True awards → H
      * Society fellowships (FACSM, FACP) → I (membership, not award)
      * Advisory committee appointments → Q2 (service, not honor)
      * Leadership appointments (Named Director of X) → O (admin role)
      * Editorial appointments → Q4A/Q4B/Q4C
    - CONTENT DETERMINES CODE, even in Honors sections. The section
      label "Honors" is a hint but the taxonomy code reflects what
      the item actually IS, not where the CV author placed it.
    - Exception: Promotions/advancements → H when framed as recognition.

════════════════════════════════════════════════════════════════════════════════
F. PUBLICATIONS AND SCHOLARLY OUTPUT (S0–S9, M2D)
════════════════════════════════════════════════════════════════════════════════

28. ORIGINAL RESEARCH VS REVIEWS/EDITORIALS (S1 VS S2)
    - S1 = Original peer-reviewed research:
      * Reports NEW data, experiments, or empirical findings.
      * Clearly has methods, results, and data analysis.
    - S2 = Reviews, editorials, commentaries, and perspectives:
      * Systematic reviews, scoping reviews, narrative reviews, meta-
        analyses, "Review of…", "A review of…".
      * Editorials, commentaries, perspectives, letters to the editor,
        responses to other articles.
      * Conceptual frameworks or position papers that synthesize
        existing work without new data.
    - Heuristic: If the title says "systematic review", "meta-analysis",
      "review", "perspective", "commentary", "editorial", "letter to
      the editor" and there's no clear description of new data →
      S2, not S1.

29. BOOKS, CHAPTERS, AND EDITED VOLUMES (S3 VS S4)
    - S3 = Authored books and book chapters:
      * The CV owner wrote the content:
        - Books/monographs they authored.
        - Chapters in books edited by someone else.
    - S4 = Edited books, edited volumes, and edited special issues:
      * The CV owner is listed as editor (not the main author):
        - "Smith, J. (Ed.)" or "(Eds.)"
        - "Edited by [Name]"
      * Special issues of journals where they are issue editors.

30. TECHNICAL REPORTS, STANDARDS, AND GRAY LITERATURE (S5 VS S7)
    - S5 = Technical reports and standards:
      * Official guidelines and standards documents (ISO, HL7, W3C,
        ACMG clinical guidelines, etc.).
      * Institutional or governmental technical reports with clear
        report/status identifiers.
    - S7 = Working papers and informal scholarly outputs:
      * White papers, policy briefs, issue briefs, fact sheets.
      * Consortium working documents and position papers.
      * Preprints and manuscripts "submitted", "under review",
        "in preparation", "in revision" when cited as such.

31. CASE REPORTS (S6)
    - S6 = Formal case reports or case series:
      * Clinical case reports published in journals.
      * Small series of patient cases when the format is clearly
        "case report" type.

32. ABSTRACTS, CONFERENCE PAPERS, AND PROCEEDINGS (S8)
    - S8 = All conference-related publications and presentations
      (unless they are keynotes/plenaries → see R below):
      * Abstracts, poster presentations, oral presentations at
        scientific or professional meetings.
      * Conference proceedings papers, even if peer-reviewed and
        archival.
      * Signals:
        - "Proceedings of", "In Proceedings of", "Proc."
        - "Conference", "Symposium", "Workshop", "Congress"
        - Named societies' annual meetings and standard conference
          acronyms, including but not limited to:
          CHI, CSCW, UIST, IDC, IUI, DIS, TEI, UbiComp, ISWC,
          NeurIPS, ICML, ICLR, AAAI, IJCAI, CVPR, ICCV, ECCV,
          ACL, EMNLP, NAACL, KDD, SIGIR, SIGMOD, SIGCOMM,
          ICIS, SRCD, CogSci, APHA, ICASSP, Interspeech.
      * Examples:
        - "In Proceedings of CogSci 2020" → S8
        - "Proceedings of the ACM CHI Conference" → S8
        - "ICIS 2019, pp. 234–241" → S8
        - "IDC '18: Proceedings of…" → S8
    - IMPORTANT: If the venue is a conference proceedings (not a
      journal), classify as S8, not S1, even if peer-reviewed.
    - When in doubt between S1 and S8 for something conference-like,
      choose S8.

33. MEDIA-FORMAT OUTPUTS AND PUBLIC SCHOLARSHIP (S9)
    - S9 = Media-format scholarly outputs:
      * Podcast episodes, blog posts, videos, and other media where
        the CV owner is the primary author/creator and it is presented
        as a publication-like artifact.
      * Media interviews where the CV owner is quoted/interviewed
        (TV, radio, podcasts, newspapers).
    - Heuristic:
      * If the entry is listed like a publication created by them → S9.
      * If it is a media appearance/interview → S9.
    - NOTE: Do NOT use Q3 for media appearances. Q3 is ONLY for grant
      reviewing/study sections.

34. PATENTS (M2D)
    - Entries with patent numbers (e.g., "US 10,234,567") are M2D.
    - Do NOT treat patents as S5/S7; patents are M2D even if they have
      technical descriptions.

════════════════════════════════════════════════════════════════════════════════
G. PRESENTATIONS, CONFERENCES, WORKSHOPS, AND INVITED TALKS
════════════════════════════════════════════════════════════════════════════════

35. CONFERENCE PRESENTATIONS → S8 (NOT R)
    - Talks, posters, and symposia presentations at scientific or
      professional meetings are S8:
      * "Annual Meeting", "Scientific Meeting", "Symposium",
        "Conference", "Congress".
      * "Poster presentation at…", "Oral presentation at…".
      * Society/association meetings.
    - Even if the presentation is "invited" at a conference, default
      to S8 unless it is a clearly designated keynote/plenary (R).

36. SYMPOSIUM CHAIRING/ORGANIZATION → Q2 (NOT S8)
    - Organizing or chairing a symposium is SERVICE, not a presentation:
      * "Chair of paper symposium…" → Q2 (external service)
      * "Symposium organized for [Society]" → Q2
      * "Session chair", "Panel moderator" → Q2
      * "Organized invited symposium at [Conference]" → Q2
    - CRITICAL DISTINCTION:
      * PRESENTING at a symposium = S8 (your scholarly output)
      * CHAIRING/ORGANIZING a symposium = Q2 (service facilitating others' work)
    - If the entry describes BOTH presenting AND chairing:
      * If the primary emphasis is on presenting research → S8
      * If the primary emphasis is on organizing/chairing → Q2
    - "Discussant" roles at symposia are typically service → Q2

37. INVITED PRESENTATIONS AT INSTITUTIONS → R
    - R = invited talks given at:
      * Universities and academic departments (e.g., "Invited lecture,
        Department of Medicine, Duke University").
      * Grand Rounds, departmental colloquia, named lectureships,
        institutional seminar series.
      * Keynote/plenary talks at conferences or major events.
    - Keynote/plenary exception:
      * "Keynote lecture", "Plenary lecture", "Featured speaker"
        at a conference → R (these are prestige invitations).

38. WORKSHOPS → S8 VS R
    - If a workshop is part of a conference, consortium, or meeting:
      * "Workshop at [Conference]", "RDA Plenary workshop", "GA4GH
         workshop" with city/virtual only → S8.
    - If a workshop is hosted by a specific university, department,
      or research center:
      * "Workshop, Temple University", "Workshop, MIT Department of X",
        "Workshop at [University]-affiliated Center" → R.
    - Community/public workshops for lay audiences:
      * If clearly community-facing, treat as R (invited talk) or K5
        (community education) depending on context.

════════════════════════════════════════════════════════════════════════════════
H. TEACHING, MENTORING, AND STUDENTS (K, N)
════════════════════════════════════════════════════════════════════════════════

39. TEACHING CODES (K1–K5) - CRITICAL DISTINCTIONS
    ════════════════════════════════════════════════════════════════════
    DECISION TREE:
    1. Is this about RUNNING a program (director, coordinator)? → K3
    2. Is the audience practicing professionals or for CME credit? → K4
    3. Is this research mentoring, thesis work, or lab supervision? → K2
    4. Is this clinical teaching (precepting, rounds, bedside)? → K2
    5. Is this formal classroom/didactic teaching? → K1
    6. Is this community/patient education? → K5
    ════════════════════════════════════════════════════════════════════

    - K1 = DIDACTIC TEACHING (classroom instruction):
      * Formal classroom-based instruction: lectures, courses, seminars.
      * Teaching that occurs in classrooms, lecture halls, seminar rooms.
      * ALL learner levels in didactic settings: undergrad, grad, medical
        students, residents IN CLASSROOM.
      * NOT K1: Research mentoring, CME, clinical bedside teaching.

    - K2 = RESEARCH MENTORING & CLINICAL TEACHING (hands-on supervision):
      * RESEARCH MENTORING: lab rotations, thesis supervision, research
        project advising, faculty advisor for student research.
      * CLINICAL TEACHING: precepting, bedside teaching, attending rounds,
        clinical supervision of trainees.
      * The key distinction: K2 = supervision in research OR clinical
        contexts. Classroom lectures are K1.
      * CROSS-REFERENCE: K2 differs from clinical activity (L1–L3).
        L codes are for patient care itself; K2 is for teaching during
        patient care. See section C, rule 11.

    - K3 = EDUCATIONAL PROGRAM LEADERSHIP (administration):
      * Program Director, Clerkship Director, Fellowship Director roles.
      * Curriculum development, course coordination, educational committees.
      * The key distinction: K3 = RUNNING programs. Direct teaching is K1/K2.

    - K4 = CME & PROFESSIONAL EDUCATION (practicing professionals):
      * CME courses, grand rounds, professional development workshops.
      * Lectures to practicing clinicians, residents, fellows outside
        formal curriculum (noon conferences, journal clubs).
      * The key distinction: K4 = teaching PRACTICING professionals.
        Undergraduate/medical student didactic teaching is K1.

    - K5 = Community/patient education:
      * Education aimed at non-academic audiences (patients, community
        groups) as a formal teaching activity (not just outreach talk).

40. MENTORING & STUDENT PROJECTS (N-CODES)
    - Student research projects, theses, dissertations, capstones,
      and mentee listings should NOT default to T.
    - Strong N3A/N3B pattern:
      * Format: [Year] [Student Name], [Degree]: [Title]
      * Mentions of "dissertation", "thesis", "capstone project",
        "research project", "independent study".
      * Explicit "advisor", "chair", "mentor" roles.
    - Classification:
      * N3A = current mentees (ongoing, "expected [year]").
      * N3B = past mentees (completed degree/project).
    - N1 = leadership of mentoring programs (e.g., "Director,
      T32 Program").
    - N2 = training grants focused on education/mentoring
      (these may also appear as M2 for funding classification).
    - N4 = mentee outputs:
      * Publications, awards, or recognitions specifically attributed
        to mentees in the context of mentoring.

════════════════════════════════════════════════════════════════════════════════
I. FINAL CHECKLIST: T ONLY WHEN NOTHING ELSE FITS
════════════════════════════════════════════════════════════════════════════════

41. REMINDER: T IS LAST RESORT
    - Before assigning T, systematically verify that the entry is not:
      * A grant (M2 or service P/Q2)
      * A research interest/theme (M1)
      * A researcher identifier/bibliometric summary (S0)
      * An editorial role (Q4A–D)
      * A position or appointment (C, D1–D3)
      * A leadership role (O, Q1)
      * Committee/service (P or Q2)
      * Media or outreach (Q3)
      * A presentation (R, S8)
      * A publication or scholarly output (S1–S9)
      * Teaching (K1–K5)
      * Mentoring/mentees (N1–N4, especially N3A/N3B)
      * Clinical activity (L1–L3)
      * An honor or award (H)
      * A membership/fellow status (I)
    - Only if NONE of these apply and the line is clearly a structural
      artifact or leftover do you classify it as T.

════════════════════════════════════════════════════════════════════════════════

CLASSIFICATION PROCEDURE:
1. Read each entry carefully - what IS this content?
2. Apply the rules in sections A-I above - content drives classification
3. Note the entry's CV section path - it reflects the author's intent
4. When content clearly fits one code, use that code (an award under "Teaching" is still H)
5. When content is ambiguous between codes, use the CV section path to choose
6. Assign the most specific applicable code
7. **AVOID T AT ALL COSTS**: T (miscellaneous) is a last resort

OUTPUT FORMAT:
Return a JSON object with a "classifications" array. Each element must have:
- "index": The entry index (0-based, matching input order)
- "code": The taxonomy code (e.g., "S1", "H", "M2A")
- "confidence": Your confidence (0.0-1.0)
- "reasoning": Brief 1-sentence explanation (optional, only if non-obvious)

Example:
{{"classifications": [
  {{"index": 0, "code": "S1", "confidence": 0.95}},
  {{"index": 1, "code": "H", "confidence": 0.85, "reasoning": "Content is clearly an award (H), despite Teaching section placement"}}
]}}"""


@dataclass
class _BatchStats:
    """One batch's contribution to classify_entries_batch's accumulators.

    _classify_one_batch returns one of these instead of mutating outer
    accumulator variables; classify_entries_batch sums them across batches.
    Field names mirror the loop-local variables the inline code used to
    increment (#604).
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    llm_batches: int = 0  # 1 if this batch attempted an LLM call, else 0
    observed_model: Optional[str] = None  # model id the API actually served (#459)
    failed_batches: int = 0  # 1 if this batch's LLM call raised, else 0


def _build_taxonomy_ref_for_batch(
    taxonomy_context: TaxonomyContext,
    taxonomy: Dict,
) -> Tuple[List[str], str]:
    """Resolve the suggested codes and taxonomy reference text shared by every
    batch in one classify_entries_batch call.

    Returns:
        Tuple of (all_suggested_codes, taxonomy_ref)
    """
    # Get ALL suggested codes from ALL hierarchy levels for taxonomy filtering
    # This ensures we don't miss codes suggested at parent levels
    all_suggested_codes = taxonomy_context.get_all_suggested_codes()
    relevant_families = set(c[0] for c in all_suggested_codes) if all_suggested_codes else None

    # Build taxonomy reference with disambiguation info for all suggested codes
    if relevant_families and len(relevant_families) <= 5:
        # Include suggested families plus a few common alternatives
        relevant_families.update(['H', 'T'])  # Always include honors and other
        # Pass ALL suggested codes so they get full disambiguation notes
        taxonomy_ref = build_taxonomy_codes_for_prompt(
            taxonomy,
            relevant_families=list(relevant_families),
            context_codes=all_suggested_codes
        )
    else:
        # Full taxonomy, but still include disambiguation for suggested codes
        taxonomy_ref = build_taxonomy_codes_for_prompt(
            taxonomy,
            context_codes=all_suggested_codes
        )

    return all_suggested_codes, taxonomy_ref


def _classify_one_batch(
    batch_entries: List[Dict],
    batch_start: int,
    taxonomy_context: TaxonomyContext,
    all_suggested_codes: List[str],
    taxonomy_ref: str,
    model: str,
) -> Tuple[List[Dict], _BatchStats]:
    """Classify a single batch against a taxonomy_ref built once by the caller.

    Returns this batch's own results list and its own stats contribution --
    it never appends to a shared list or mutates an outer accumulator, so
    classify_entries_batch can extend/sum the return values after the call.
    """
    stats = _BatchStats()

    # Skip empty entries
    entries_with_text = [(i, e) for i, e in enumerate(batch_entries) if e.get("text", "").strip()]

    if not entries_with_text:
        # All empty - assign parent code with low confidence
        results = []
        for entry in batch_entries:
            primary = all_suggested_codes[0] if all_suggested_codes else "T"
            results.append({
                **entry,
                "taxonomy_code": primary,
                "taxonomy_confidence": 0.0,
                "classification_source": "empty_entry"
            })
        return results, stats

    # Build prompt
    context_str = taxonomy_context.format_context_string()

    system_prompt = _CLASSIFICATION_SYSTEM_PROMPT_TEMPLATE.format(
        context_str=context_str, taxonomy_ref=taxonomy_ref
    )

    # Build entries list for user message (include per-entry hierarchy)
    entries_lines = []
    for i, e in entries_with_text:
        hierarchy_path = " > ".join(e.get("hierarchy", [])) or "unknown"
        entries_lines.append(f"[{i}] (Section: {hierarchy_path}) {e['text'][:500]}")
    entries_text = "\n".join(entries_lines)

    user_message = f"""Classify these {len(entries_with_text)} entries:

{entries_text}

Return ONLY valid JSON with the classifications array."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    # Call LLM
    stats.llm_batches = 1
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2000
        )

        # Parse response
        content = llm_result["content"]
        result = json.loads(content)
        classifications = result.get("classifications", [])

        # Track tokens/cost
        stats.input_tokens = llm_result["prompt_tokens"]
        stats.output_tokens = llm_result["completion_tokens"]
        stats.cost = llm_result["cost"]
        stats.observed_model = llm_result.get("model")

    except Exception:
        # Every entry in this batch falls back to the default code below;
        # the caller aggregates failed_batches and fails the run if NO
        # batch ever produced a real classification (#61).
        stats.failed_batches = 1
        hierarchy_path = " > ".join(batch_entries[0].get("hierarchy", [])) or "(no hierarchy)"
        logger.exception(
            "Stage 3b batch classification failed; %d entries fall back to "
            "default codes (batch at offset %d, hierarchy: %s)",
            len(entries_with_text), batch_start, hierarchy_path
        )
        classifications = []

    # Build index lookup for classifications.
    #
    # This runs OUTSIDE the try/except above, so anything raised here
    # escapes classify_entries_batch and run_stage_3b entirely: the
    # orchestrator fails the whole web run, while the CLI prints
    # "Warning: Stage N failed" and lets every later stage run on
    # unclassified entries. The response is requested as a bare
    # json_object with no schema, so an object without "index" (KeyError)
    # or a non-dict element (TypeError) is a real possibility. Skip those
    # loudly instead -- they fall back to the default code below, which is
    # what a missing classification already does (#521).
    class_by_idx = {}
    malformed = 0
    for c in classifications:
        if isinstance(c, dict) and "index" in c:
            class_by_idx[c["index"]] = c
        else:
            malformed += 1
    if malformed:
        logger.warning(
            "Stage 3b: skipped %d malformed classification object(s) in the "
            "batch at offset %d; those entries fall back to the default code",
            malformed, batch_start
        )

    # Map results back to entries
    results = []
    for orig_idx, entry in enumerate(batch_entries):
        if not entry.get("text", "").strip():
            # Empty entry
            primary = all_suggested_codes[0] if all_suggested_codes else "T"
            results.append({
                **entry,
                "taxonomy_code": primary,
                "taxonomy_confidence": 0.0,
                "classification_source": "empty_entry"
            })
        else:
            # class_by_idx is keyed by the ORIGINAL index within
            # batch_entries: the prompt labels each entry "[{i}]" using the
            # i carried in entries_with_text, which came from
            # enumerate(batch_entries), and the model echoes those labels
            # back as "index". Looking up a POSITION within entries_with_text
            # instead only agrees when nothing was filtered out -- and stage 2
            # emits empty "break" entries throughout the list on purpose
            # (filter_extraction_noise keeps them; "breaks are legitimately
            # empty"). One break in a batch shifted every later entry, so an
            # entry was persisted with its neighbour's code and confidence,
            # indistinguishable downstream from a correct classification (#520).
            c = class_by_idx.get(orig_idx)
            if c is not None:
                results.append({
                    **entry,
                    # Coalesce an explicit-null/empty LLM code to the fallback,
                    # and coerce a stringified confidence to float, so the
                    # persisted values never crash downstream .startswith / < 0.7.
                    "taxonomy_code": c.get("code") or (all_suggested_codes[0] if all_suggested_codes else "T"),
                    "taxonomy_confidence": _safe_float(c.get("confidence"), 0.5),
                    "classification_reasoning": c.get("reasoning"),
                    "classification_source": "llm"
                })
            else:
                # Fallback to primary code
                primary = all_suggested_codes[0] if all_suggested_codes else "T"
                results.append({
                    **entry,
                    "taxonomy_code": primary,
                    "taxonomy_confidence": 0.5,
                    "classification_source": "fallback"
                })

    return results, stats


def classify_entries_batch(
    entries: List[Dict],
    taxonomy_context: TaxonomyContext,
    taxonomy: Dict,
    model: str = "gpt-5.1",
    batch_size: int = 15
) -> Tuple[List[Dict], Dict]:
    """
    Classify a batch of entries with the same taxonomy context.

    Args:
        entries: List of entry dicts with 'text' field
        taxonomy_context: Shared taxonomy context for these entries
        taxonomy: Full taxonomy reference
        model: OpenAI model to use
        batch_size: Max entries per LLM call

    Returns:
        Tuple of (classified_entries, stats)
    """
    all_results = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    llm_batches = 0  # batches that attempted an LLM call
    observed_model = None  # model id the API actually served (#459)
    failed_batches = 0  # batches whose LLM call raised (entries fell back)

    all_suggested_codes, taxonomy_ref = _build_taxonomy_ref_for_batch(taxonomy_context, taxonomy)

    # Process in batches
    for batch_start in range(0, len(entries), batch_size):
        batch_entries = entries[batch_start:batch_start + batch_size]

        batch_results, batch_stats = _classify_one_batch(
            batch_entries, batch_start, taxonomy_context, all_suggested_codes, taxonomy_ref, model
        )
        all_results.extend(batch_results)
        total_input_tokens += batch_stats.input_tokens
        total_output_tokens += batch_stats.output_tokens
        total_cost += batch_stats.cost
        llm_batches += batch_stats.llm_batches
        failed_batches += batch_stats.failed_batches
        observed_model = batch_stats.observed_model or observed_model

    stats = {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "cost": total_cost,
        # What actually served the calls. The `model` parameter is a default no
        # orchestrator passes, so recording it stamped 100/100 corpus artifacts
        # with a model the run never used -- and 3b is the one deliberately on
        # Haiku, which is exactly the comparison the field exists for (#459).
        "model": observed_model,
        "entries_classified": len(all_results),
        "llm_batches": llm_batches,
        "failed_batches": failed_batches,
        "llm_classified": sum(1 for r in all_results if r.get("classification_source") == "llm"),
        "fallback_entries": sum(1 for r in all_results if r.get("classification_source") == "fallback"),
        "empty_entries": sum(1 for r in all_results if r.get("classification_source") == "empty_entry"),
    }

    return all_results, stats


def group_entries_by_hierarchy(entries: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group entries by their hierarchy path (for batching).

    Returns dict mapping hierarchy_key -> list of entries
    """
    groups = {}

    for entry in entries:
        hierarchy = entry.get("hierarchy", [])
        key = " > ".join(hierarchy) if hierarchy else "(no hierarchy)"

        if key not in groups:
            groups[key] = []
        groups[key].append(entry)

    return groups


def validate_t_classifications(
    entries: List[Dict],
    taxonomy: Dict,
    model: str = "gpt-5.1"
) -> Tuple[List[Dict], Dict]:
    """
    T-validation gate: Re-evaluate any entries classified as T (miscellaneous).

    T should be used in <2% of cases. This gate takes all T classifications
    and asks the LLM to reconsider with the FULL taxonomy and strong guidance
    that T is an absolute last resort.

    Args:
        entries: List of classified entries (some may have taxonomy_code="T")
        taxonomy: Full taxonomy reference
        model: OpenAI model to use

    Returns:
        Tuple of (updated_entries, stats) where T entries may be reclassified
    """
    # Find entries classified as exactly "T" (miscellaneous)
    # NOTE: We only review "T", not T-family codes like T1 (Community Engagement)
    # which are valid specific classifications
    t_entries = [(i, e) for i, e in enumerate(entries) if e.get("taxonomy_code") == "T"]

    if not t_entries:
        return entries, {"t_entries_reviewed": 0, "t_entries_reclassified": 0, "cost": 0.0}

    # Build FULL taxonomy reference for maximum context
    taxonomy_ref = build_taxonomy_codes_for_prompt(taxonomy)

    # Build the validation prompt
    system_prompt = f"""You are an expert CV classifier performing a CRITICAL REVIEW of entries that were initially classified as "T" (Miscellaneous/Other).

IMPORTANT CONTEXT:
- T (Miscellaneous/Other) should be used in LESS THAN 2% of CV entries
- T is an ABSOLUTE LAST RESORT when NO other code applies
- Most entries initially classified as T are actually misclassified and belong elsewhere

YOUR TASK:
For each entry below, determine if T is truly correct, or if a more specific code applies.

FULL TAXONOMY (use this to find a better code):
{taxonomy_ref}

═══════════════════════════════════════════════════════════════════════════════
COMMON MISCLASSIFICATIONS TO T (check these first!):
═══════════════════════════════════════════════════════════════════════════════

1. COMMUNITY SERVICE / OUTREACH → Q2 (not T)
   - Advisory boards, committees, task forces → Q2
   - Expert testimony, consulting → Q2
   - Community advisory participation → Q2
   - Public health outreach → Q2 or K5
   - Pro bono professional service → Q2

2. INVITED PRESENTATIONS → R (not T)
   - Grand rounds, colloquia, seminars → R
   - Keynote lectures, named lectures → R
   - Departmental or institutional talks → R
   - "Invited" anything at an academic venue → R

3. PROFESSIONAL SERVICE → P or Q2 (not T)
   - Internal committees → P
   - External committees/boards → Q2
   - Review panels → Q3 (grants) or Q4D (manuscripts)

4. GRANTS/FUNDING → M2A/M2B/M2C (not T)
   - Any entry with grant numbers, dollar amounts, PI roles → M2

5. EDITORIAL WORK → Q4A/Q4B/Q4C/Q4D (not T)
   - Editor, associate editor → Q4A/Q4B
   - Editorial board → Q4C
   - Peer review → Q4D

6. RESEARCH ACTIVITIES → M1 (not T)
   - Research interests, themes, areas → M1
   - Fieldwork, excavations → M1
   - Lab descriptions → M1

7. POSITIONS/APPOINTMENTS → D1/D2/D3/O (not T)
   - Academic titles → D1
   - Hospital appointments → D2
   - Staff positions → D3
   - Leadership/administrative roles → O

8. PROFESSIONAL MEMBERSHIPS → I (not T)
   - Society memberships → I
   - Professional organization membership → I

9. EDUCATIONAL OUTREACH → K5 (not T)
   - Public lectures, science communication → K5
   - Media appearances about science → K5

10. HONORS/AWARDS → H (not T)
    - Recognition, prizes, competitive awards → H

DECISION CRITERIA:
- If the entry fits ANY of the above patterns → use that code, NOT T
- If the entry contains keywords like "committee", "board", "advisory", "invited", "lecture", "presentation", "grant", "review" → almost certainly NOT T
- T should ONLY be used for genuinely miscellaneous items like reference lists, appendix materials, or items that truly cannot fit anywhere else

For each entry, respond with:
{{"entry_index": N, "new_code": "XX", "confidence": 0.XX, "reasoning": "brief explanation"}}

If T is genuinely correct, keep it: {{"entry_index": N, "new_code": "T", "confidence": 0.XX, "reasoning": "why no other code fits"}}
"""

    # Format entries for review
    entries_text = []
    for idx, entry in t_entries:
        text = entry.get("text", "")[:500]  # Truncate long entries
        hierarchy = " > ".join(entry.get("hierarchy", [])) or "(no hierarchy)"
        original_reasoning = entry.get("classification_reasoning", "none provided")
        entries_text.append(f"""
Entry {idx}:
  Hierarchy: {hierarchy}
  Text: {text}
  Original reasoning for T: {original_reasoning}
""")

    user_prompt = f"""Review these {len(t_entries)} entries that were classified as T (Miscellaneous).
For each one, determine if T is correct or if a more specific code should be used.

{chr(10).join(entries_text)}

Respond with a JSON array of objects, one per entry:
[
  {{"entry_index": N, "new_code": "XX", "confidence": 0.XX, "reasoning": "..."}},
  ...
]
"""

    # Log prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1
        )

        # Parse response
        content = llm_result["content"]

        # Handle both array and object responses
        result = json.loads(content)
        if isinstance(result, dict):
            # If wrapped in an object, try to find the array
            if "results" in result:
                reclassifications = result["results"]
            elif "entries" in result:
                reclassifications = result["entries"]
            elif "classifications" in result:
                reclassifications = result["classifications"]
            else:
                # Assume the dict values are the results
                reclassifications = list(result.values())[0] if result else []
        else:
            reclassifications = result

        # Apply reclassifications
        reclassified_count = 0
        for reclass in reclassifications:
            entry_idx = reclass.get("entry_index")
            new_code = reclass.get("new_code") or "T"
            confidence = _safe_float(reclass.get("confidence"), 0.5)
            reasoning = reclass.get("reasoning", "")

            if entry_idx is not None and 0 <= entry_idx < len(entries):
                old_code = entries[entry_idx].get("taxonomy_code")
                if old_code == "T" and new_code != "T":
                    entries[entry_idx]["taxonomy_code"] = new_code
                    entries[entry_idx]["taxonomy_confidence"] = confidence
                    entries[entry_idx]["classification_reasoning"] = f"[T-validation reclassified from T] {reasoning}"
                    entries[entry_idx]["t_validation_applied"] = True
                    reclassified_count += 1
                elif old_code == "T" and new_code == "T":
                    # T was confirmed - add note
                    entries[entry_idx]["classification_reasoning"] = f"[T-validation confirmed] {reasoning}"
                    entries[entry_idx]["t_validation_applied"] = True

        # Calculate cost (call_llm already returns token counts and priced cost)
        input_tokens = llm_result["prompt_tokens"]
        output_tokens = llm_result["completion_tokens"]
        cost = llm_result["cost"]

        stats = {
            "t_entries_reviewed": len(t_entries),
            "t_entries_reclassified": reclassified_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost
        }

        return entries, stats

    except Exception as e:
        print(f"    ⚠ T-validation error: {e}")
        # Report reclassifications already applied to entries before the error,
        # so meta.stats reflects reality even on a partial failure.
        return entries, {"t_entries_reviewed": len(t_entries), "t_entries_reclassified": locals().get("reclassified_count", 0), "cost": 0.0, "error": str(e)}


def reconnect_fragments(
    entries: List[Dict],
    model: str = "gpt-5.1"
) -> Tuple[List[Dict], Dict]:
    """
    Reconnect fragment entries (classified as T) to their adjacent entries.

    Some entries are fragments of larger entries that got split during extraction:
    - Orphaned location lines: "University, Columbus, OH"
    - Orphaned budget lines: "$1,326,480, Ohio Department of Medicaid"
    - Continuation lines without context

    This function identifies likely fragments and determines if they belong
    with the previous or next entry.

    Args:
        entries: List of classified entries (sorted by element_idx_start)
        model: OpenAI model to use

    Returns:
        Tuple of (updated_entries, stats) where fragments are annotated
    """
    # Find T entries that look like fragments (short, low confidence)
    fragment_candidates = []
    for i, entry in enumerate(entries):
        if entry.get("taxonomy_code") != "T":
            continue

        text = entry.get("text", "").strip()
        confidence = entry.get("taxonomy_confidence", 1.0)

        # Fragment signals:
        # - Very short text (< 100 chars)
        # - Low confidence (< 0.7)
        # - Contains only: location, dollar amount, institution name, or partial info
        is_short = len(text) < 100
        is_low_conf = _safe_float(confidence, 1.0) < 0.7

        # Check for fragment patterns
        import re
        is_location_only = bool(re.match(r'^[A-Z][a-z]+,?\s+[A-Z]{2}$', text))  # "Columbus, OH"
        is_dollar_only = bool(re.match(r'^\$[\d,]+', text)) and 'PI' not in text and 'Co-I' not in text
        is_institution_fragment = (
            len(text.split()) <= 5 and
            any(kw in text for kw in ['University', 'College', 'Institute', 'Center', 'Hospital']) and
            not any(kw in text for kw in ['Professor', 'Director', 'Chair', 'Fellow'])
        )

        if is_short and (is_low_conf or is_location_only or is_dollar_only or is_institution_fragment):
            # Need adjacent entries to compare
            prev_entry = entries[i - 1] if i > 0 else None
            next_entry = entries[i + 1] if i < len(entries) - 1 else None

            if prev_entry or next_entry:
                fragment_candidates.append({
                    "index": i,
                    "entry": entry,
                    "prev_entry": prev_entry,
                    "next_entry": next_entry
                })

    if not fragment_candidates:
        return entries, {"fragments_reviewed": 0, "fragments_reconnected": 0, "cost": 0.0}

    # Build prompt for fragment analysis
    system_prompt = """You are analyzing CV entries to identify fragments that belong with adjacent entries.

Some CV entries get incorrectly split during extraction, creating orphaned fragments like:
- Location-only lines: "University, Columbus, OH"
- Budget-only lines: "$1,326,480, Ohio Department of Medicaid"
- Partial institution names without roles

For each fragment, determine if it belongs with the PREVIOUS entry, NEXT entry, or is STANDALONE.

DECISION CRITERIA:
1. If the fragment completes information from the previous entry (e.g., location for a position, budget for a grant) → PREVIOUS
2. If the fragment introduces the next entry (e.g., header-like content) → NEXT
3. If the fragment is genuinely standalone or unclear → STANDALONE

Respond with JSON:
{
  "fragments": [
    {"index": N, "belongs_to": "previous|next|standalone", "reasoning": "brief explanation"}
  ]
}
"""

    # Format fragments for review
    fragments_text = []
    for fc in fragment_candidates:
        idx = fc["index"]
        entry = fc["entry"]
        prev_entry = fc["prev_entry"]
        next_entry = fc["next_entry"]

        prev_text = prev_entry.get("text", "")[:200] if prev_entry else "(none)"
        prev_code = prev_entry.get("taxonomy_code", "?") if prev_entry else "?"
        next_text = next_entry.get("text", "")[:200] if next_entry else "(none)"
        next_code = next_entry.get("taxonomy_code", "?") if next_entry else "?"
        frag_text = entry.get("text", "")

        fragments_text.append(f"""
Fragment at index {idx}:
  Text: "{frag_text}"

  Previous entry [{prev_code}]: "{prev_text}"
  Next entry [{next_code}]: "{next_text}"
""")

    user_prompt = f"""Analyze these {len(fragment_candidates)} fragments and determine where they belong:

{chr(10).join(fragments_text)}
"""

    # Log prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    try:
        llm_result = call_llm(
            stage="stage_3b",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1
        )

        content = llm_result["content"]
        result = json.loads(content)
        fragment_decisions = result.get("fragments", [])

        # Apply reconnections
        reconnected_count = 0
        for decision in fragment_decisions:
            idx = decision.get("index")
            belongs_to = decision.get("belongs_to", "standalone")
            reasoning = decision.get("reasoning", "")

            if idx is not None and 0 <= idx < len(entries):
                entry = entries[idx]

                if belongs_to == "previous" and idx > 0:
                    prev_entry = entries[idx - 1]
                    entry["fragment_of"] = idx - 1
                    entry["fragment_reasoning"] = reasoning
                    entry["taxonomy_code"] = prev_entry.get("taxonomy_code", "T")
                    entry["taxonomy_confidence"] = 0.3  # Low confidence for fragments
                    entry["is_fragment"] = True
                    reconnected_count += 1

                elif belongs_to == "next" and idx < len(entries) - 1:
                    next_entry = entries[idx + 1]
                    entry["fragment_of"] = idx + 1
                    entry["fragment_reasoning"] = reasoning
                    entry["taxonomy_code"] = next_entry.get("taxonomy_code", "T")
                    entry["taxonomy_confidence"] = 0.3
                    entry["is_fragment"] = True
                    reconnected_count += 1

                elif belongs_to == "standalone":
                    entry["fragment_reasoning"] = f"[Confirmed standalone] {reasoning}"

        # Calculate cost (call_llm already returns token counts and priced cost)
        input_tokens = llm_result["prompt_tokens"]
        output_tokens = llm_result["completion_tokens"]
        cost = llm_result["cost"]

        stats = {
            "fragments_reviewed": len(fragment_candidates),
            "fragments_reconnected": reconnected_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost
        }

        return entries, stats

    except Exception as e:
        print(f"    ⚠ Fragment reconnection error: {e}")
        # Report reconnections already applied to entries before the error.
        return entries, {"fragments_reviewed": len(fragment_candidates), "fragments_reconnected": locals().get("reconnected_count", 0), "cost": 0.0, "error": str(e)}


def detect_duplicates(entries: List[Dict], similarity_threshold: float = 0.9) -> Tuple[List[Dict], List[Dict]]:
    """
    Detect and flag duplicate entries based on text similarity.

    Duplicates occur when the same content appears under multiple CV sections
    (e.g., grants listed under both "Other Publications" and "Grant Support").

    Args:
        entries: List of classified entries
        similarity_threshold: Minimum similarity ratio to consider duplicate (0-1)

    Returns:
        Tuple of (deduplicated_entries, duplicate_info)
        - deduplicated_entries: Entries with duplicates marked
        - duplicate_info: List of detected duplicate pairs
    """
    import re
    from difflib import SequenceMatcher

    def normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        if not text:
            return ""
        # Lowercase, remove extra whitespace, strip punctuation
        text = text.lower()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^\w\s]', '', text)
        return text.strip()

    def text_similarity(t1: str, t2: str) -> float:
        """Calculate similarity ratio between two texts."""
        n1, n2 = normalize_text(t1), normalize_text(t2)
        if not n1 or not n2:
            return 0.0
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, n1, n2).ratio()

    # Build text index for faster lookup
    text_index = {}  # normalized_text -> list of (idx, entry)
    for idx, entry in enumerate(entries):
        text = entry.get("text", "")
        if len(text) < 20:  # Skip very short entries
            continue
        norm = normalize_text(text)
        # Use first 100 chars as key for grouping similar entries
        key = norm[:100] if len(norm) > 100 else norm
        if key not in text_index:
            text_index[key] = []
        text_index[key].append((idx, entry, norm))

    # Find duplicates
    duplicate_pairs = []
    seen_duplicates = set()  # Track which indices have been marked as duplicates

    for key, items in text_index.items():
        if len(items) < 2:
            continue

        # Compare all pairs in this group
        for i, (idx1, entry1, norm1) in enumerate(items):
            for idx2, entry2, norm2 in items[i+1:]:
                if idx1 in seen_duplicates and idx2 in seen_duplicates:
                    continue

                sim = text_similarity(entry1.get("text", ""), entry2.get("text", ""))
                if sim >= similarity_threshold:
                    # These are duplicates
                    duplicate_pairs.append({
                        "entry1_idx": idx1,
                        "entry2_idx": idx2,
                        "similarity": sim,
                        "entry1_hierarchy": entry1.get("hierarchy", []),
                        "entry2_hierarchy": entry2.get("hierarchy", []),
                        "entry1_code": entry1.get("taxonomy_code"),
                        "entry2_code": entry2.get("taxonomy_code"),
                        "text_preview": entry1.get("text", "")[:100]
                    })

                    # Mark the second one as duplicate (keep the first)
                    # Prefer M2 classification over T
                    code1 = entry1.get("taxonomy_code") or ""
                    code2 = entry2.get("taxonomy_code") or ""

                    if code1.startswith("T") and code2.startswith("M"):
                        # Second one is better classified, mark first as duplicate
                        seen_duplicates.add(idx1)
                    elif code2.startswith("T") and code1.startswith("M"):
                        # First one is better classified, mark second as duplicate
                        seen_duplicates.add(idx2)
                    else:
                        # Default: mark second as duplicate
                        seen_duplicates.add(idx2)

    # Mark duplicates in entries
    for idx, entry in enumerate(entries):
        if idx in seen_duplicates:
            entry["is_duplicate"] = True
            entry["duplicate_note"] = "This entry appears elsewhere in the CV with the same content"

    return entries, duplicate_pairs


def run_stage_3b(
    document_uid: str,
    stage_2_path: Optional[str] = None,
    stage_3a_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    model: str = "gpt-5.1"
) -> Dict:
    """
    Run Stage 3b entry classification.

    Args:
        document_uid: Document identifier
        stage_2_path: Path to Stage 2 entries (optional, will auto-detect)
        stage_3a_path: Path to Stage 3a mappings (optional, will auto-detect)
        output_dir: Output directory (optional, will auto-detect)
        model: OpenAI model to use

    Returns:
        Result dict with classified entries, stats, and output path
    """
    print("=" * 80)
    print("STAGE 3b: ENTRY CLASSIFICATION")
    print("=" * 80)
    print()

    base_dir = Path(__file__).parent / "outputs"

    # Find Stage 2 input
    if stage_2_path is None:
        stage_2_path = base_dir / "stage_2_entry_extraction" / f"{document_uid}_entries.json"
    else:
        stage_2_path = Path(stage_2_path)

    if not stage_2_path.exists():
        raise FileNotFoundError(f"Stage 2 output not found: {stage_2_path}")

    # Find Stage 3a input
    if stage_3a_path is None:
        stage_3a_path = base_dir / "stage_3a_header_mappings" / f"{document_uid}_header_taxonomy.json"
    else:
        stage_3a_path = Path(stage_3a_path)

    if not stage_3a_path.exists():
        raise FileNotFoundError(f"Stage 3a output not found: {stage_3a_path}")

    print(f"Stage 2 entries: {stage_2_path}")
    print(f"Stage 3a mappings: {stage_3a_path}")

    # Load inputs
    content_entries, all_entries = load_stage_2_entries(stage_2_path)
    stage_3a_data = load_stage_3a_mappings(stage_3a_path)
    taxonomy = load_taxonomy()

    print(f"Loaded {len(all_entries)} total entries from Stage 2")
    print(f"  Content entries to classify: {len(content_entries)}")
    print(f"  Headers/breaks skipped: {len(all_entries) - len(content_entries)}")

    # Use content entries for classification
    entries = content_entries
    print(f"Loaded taxonomy v{taxonomy['meta']['version']}")
    print()

    # Build mapping index
    mappings = stage_3a_data.get("mappings", [])
    mapping_index = build_mapping_index(mappings)

    # Group entries by hierarchy
    groups = group_entries_by_hierarchy(entries)
    print(f"Grouped entries into {len(groups)} hierarchy groups")
    print()

    # Classify each group
    all_classified = []
    total_stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "entries_classified": 0,
        "groups_processed": 0,
        "llm_batches": 0,
        "failed_batches": 0,
        "llm_classified": 0,
        "fallback_entries": 0,
        "empty_entries": 0,
        "model": None
    }

    for group_idx, (hierarchy_key, group_entries) in enumerate(groups.items(), 1):
        # Parse hierarchy from key
        if hierarchy_key == "(no hierarchy)":
            hierarchy = []
        else:
            hierarchy = hierarchy_key.split(" > ")

        print(f"[{group_idx}/{len(groups)}] {hierarchy_key[:60]}...")
        print(f"    Entries: {len(group_entries)}")

        # Get taxonomy context
        context = get_taxonomy_context(hierarchy, mapping_index)
        primary_codes = context.get_primary_codes()

        if primary_codes:
            print(f"    Suggested codes: {', '.join(primary_codes[:3])}")

        # Classify entries
        classified, stats = classify_entries_batch(
            group_entries,
            context,
            taxonomy,
            model=model
        )

        all_classified.extend(classified)

        # Update totals
        total_stats["input_tokens"] += stats["input_tokens"]
        total_stats["output_tokens"] += stats["output_tokens"]
        total_stats["total_tokens"] += stats["total_tokens"]
        total_stats["cost"] += stats["cost"]
        total_stats["entries_classified"] += stats["entries_classified"]
        total_stats["groups_processed"] += 1
        total_stats["llm_batches"] += stats["llm_batches"]
        total_stats["failed_batches"] += stats["failed_batches"]
        total_stats["llm_classified"] += stats["llm_classified"]
        total_stats["fallback_entries"] += stats["fallback_entries"]
        total_stats["empty_entries"] += stats["empty_entries"]
        total_stats["model"] = stats.get("model") or total_stats.get("model")

        print(f"    ✓ Classified {stats['entries_classified']} entries (${stats['cost']:.4f})")

    print()
    print(f"Total: {total_stats['entries_classified']} entries classified")
    print(f"Cost: ${total_stats['cost']:.4f}")
    print(f"Tokens: {total_stats['total_tokens']:,}")

    # A run whose every LLM batch failed emits all-default codes that look
    # like real data (#61: invalid Bedrock model id classified an entire A/B
    # run to fallbacks at $0 with no error). Failing the run is strictly
    # better than completing it with meaningless classifications.
    if total_stats["llm_batches"] > 0 and total_stats["llm_classified"] == 0:
        raise RuntimeError(
            f"Stage 3b produced zero LLM classifications across "
            f"{total_stats['llm_batches']} batches ({total_stats['failed_batches']} raised "
            f"errors) for {total_stats['entries_classified']} entries ({document_uid}). "
            f"Refusing to emit all-fallback default codes; see batch errors above."
        )

    # Partial batch failures stay non-fatal, but must be visible per-run
    if total_stats["failed_batches"] > 0:
        msg = (
            f"{total_stats['failed_batches']} of {total_stats['llm_batches']} classification "
            f"batches failed; {total_stats['fallback_entries']} entries fell back to default codes"
        )
        print(f"⚠️ {msg}")
        logger.warning("Stage 3b (%s): %s", document_uid, msg)

    # T-validation gate: re-evaluate any T classifications
    t_count_before = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
    if t_count_before > 0:
        print()
        print(f"T-validation gate: reviewing {t_count_before} entries classified as T...")
        all_classified, t_validation_stats = validate_t_classifications(
            all_classified,
            taxonomy,
            model=model
        )
        t_count_after = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
        reclassified = t_validation_stats.get("t_entries_reclassified", 0)

        if reclassified > 0:
            print(f"  ✓ Reclassified {reclassified} entries from T to more specific codes")
            print(f"  T entries: {t_count_before} → {t_count_after}")
        else:
            print(f"  ✓ All {t_count_before} T classifications confirmed as correct")

        # Update stats
        total_stats["t_validation"] = t_validation_stats
        total_stats["cost"] += t_validation_stats.get("cost", 0.0)
        total_stats["input_tokens"] += t_validation_stats.get("input_tokens", 0)
        total_stats["output_tokens"] += t_validation_stats.get("output_tokens", 0)
        total_stats["total_tokens"] += t_validation_stats.get("input_tokens", 0) + t_validation_stats.get("output_tokens", 0)

    # Fragment reconnection: link orphaned T entries to adjacent entries
    t_count_remaining = sum(1 for e in all_classified if e.get("taxonomy_code") == "T")
    if t_count_remaining > 0:
        print()
        print(f"Fragment reconnection: checking {t_count_remaining} remaining T entries...")
        all_classified, fragment_stats = reconnect_fragments(
            all_classified,
            model=model
        )
        fragments_reviewed = fragment_stats.get("fragments_reviewed", 0)
        fragments_reconnected = fragment_stats.get("fragments_reconnected", 0)

        if fragments_reviewed > 0:
            if fragments_reconnected > 0:
                print(f"  ✓ Reconnected {fragments_reconnected}/{fragments_reviewed} fragments to adjacent entries")
            else:
                print(f"  ✓ Reviewed {fragments_reviewed} potential fragments, none reconnected")

            # Update stats
            total_stats["fragment_reconnection"] = fragment_stats
            total_stats["cost"] += fragment_stats.get("cost", 0.0)
            total_stats["input_tokens"] += fragment_stats.get("input_tokens", 0)
            total_stats["output_tokens"] += fragment_stats.get("output_tokens", 0)
            total_stats["total_tokens"] += fragment_stats.get("input_tokens", 0) + fragment_stats.get("output_tokens", 0)
        else:
            print(f"  ✓ No fragment candidates found")

    # Detect and flag duplicates
    print()
    print("Detecting duplicates...")
    all_classified, duplicate_pairs = detect_duplicates(all_classified)
    duplicate_count = sum(1 for e in all_classified if e.get("is_duplicate"))
    if duplicate_count > 0:
        print(f"  ⚠️ Found {duplicate_count} duplicate entries (flagged, not removed)")
        for dp in duplicate_pairs[:5]:  # Show first 5
            print(f"    - {dp['text_preview'][:50]}... ({dp['entry1_code']} vs {dp['entry2_code']})")
        if len(duplicate_pairs) > 5:
            print(f"    ... and {len(duplicate_pairs) - 5} more")
    else:
        print("  ✓ No duplicates detected")

    # ═══════════════════════════════════════════════════════════════════════════
    # POST-CLASSIFICATION AUTO-CORRECTION PASS
    # ═══════════════════════════════════════════════════════════════════════════
    # These deterministic validators catch and fix common LLM misclassifications
    # without additional API calls. They run in order of priority.
    print()
    print("=" * 60)
    print("POST-CLASSIFICATION AUTO-CORRECTIONS")
    print("=" * 60)

    post_correction_stats = {}

    # 1. Structural header corrections (CV title, page numbers, etc. → T)
    # Extract person name from document_uid for name-matching
    # Format: "2071_LastName_FirstName_CV" or similar
    name_parts = document_uid.split('_')
    if len(name_parts) >= 3:
        # Try to extract name (skip numeric prefix)
        name_parts_clean = [p for p in name_parts if not p.isdigit() and p.lower() not in ('cv', 'vita', 'resume')]
        person_name = ' '.join(name_parts_clean)
    else:
        person_name = None

    print()
    print("1. Structural header corrections...")
    all_classified, structural_stats = apply_structural_corrections(all_classified, person_name)
    post_correction_stats['structural'] = structural_stats
    if structural_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {structural_stats['corrections_made']} structural elements to T")
        for detail in structural_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → T: {detail['text_preview'][:40]}...")
        if len(structural_stats['correction_details']) > 3:
            print(f"     ... and {len(structural_stats['correction_details']) - 3} more")
    else:
        print("   ✓ No structural header corrections needed")

    # 2. Committee vs position corrections (committee service → P/Q2, not D codes)
    print()
    print("2. Committee vs position corrections...")
    all_classified, committee_stats = apply_committee_corrections(all_classified)
    post_correction_stats['committee'] = committee_stats
    if committee_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {committee_stats['corrections_made']} committee entries")
        print(f"     - To P (internal service): {committee_stats['corrected_to_P']}")
        print(f"     - To Q2 (external service): {committee_stats['corrected_to_Q2']}")
        for detail in committee_stats['correction_details'][:2]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['text_preview'][:50]}...")
    else:
        print("   ✓ No committee/position corrections needed")

    # 3. Reasoning-code consistency corrections (when LLM reasoning disagrees with code)
    print()
    print("3. Reasoning-code consistency corrections...")
    all_classified, reasoning_stats = apply_reasoning_corrections(all_classified, min_confidence=0.80)
    post_correction_stats['reasoning'] = reasoning_stats
    if reasoning_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {reasoning_stats['corrections_made']} reasoning/code conflicts")
        for detail in reasoning_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['evidence'][:60]}...")
    elif reasoning_stats['conflicts_found'] > 0:
        print(f"   ⚠ Found {reasoning_stats['conflicts_found']} conflicts, {reasoning_stats['skipped_low_confidence']} skipped (low confidence)")
    else:
        print("   ✓ No reasoning/code conflicts detected")

    # 4. Grant-to-position corrections (catch position/leadership misclassified as M2A/M2B)
    print()
    print("4. Grant-to-position corrections (M2A/M2B → D2/O/L3)...")
    all_classified, grant_pos_stats = apply_grant_position_corrections(all_classified)
    post_correction_stats['grant_position'] = grant_pos_stats
    if grant_pos_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {grant_pos_stats['corrections_applied']} position entries misclassified as grants")
        for detail in grant_pos_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['position_match'][:40]}...")
    else:
        print("   ✓ No grant-to-position corrections needed")

    # 5. Grant status corrections (date-based M2A/M2B/M2C override)
    print()
    print("5. Grant status corrections (date-based)...")
    all_classified, grant_stats = apply_grant_status_corrections(all_classified)
    post_correction_stats['grant_status'] = grant_stats
    if grant_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {grant_stats['corrections_applied']} grant status codes")
        for detail in grant_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['reason'][:60]}...")
    else:
        print("   ✓ No grant status corrections needed")

    # 6. Teaching leadership corrections (K1 → K3 for Course Directors)
    print()
    print("6. Teaching leadership corrections (K1 → K3)...")
    all_classified, teaching_stats = apply_teaching_leadership_corrections(all_classified)
    post_correction_stats['teaching_leadership'] = teaching_stats
    if teaching_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {teaching_stats['corrections_applied']} teaching leadership codes")
        for detail in teaching_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - K1 → K3: {corr['reason'][:60]}...")
    else:
        print("   ✓ No teaching leadership corrections needed")

    # 7. Leadership level corrections (O → P for non-executive roles)
    print()
    print("7. Leadership level corrections (O → P)...")
    all_classified, leadership_stats = apply_leadership_level_corrections(all_classified)
    post_correction_stats['leadership_level'] = leadership_stats
    if leadership_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {leadership_stats['corrections_applied']} leadership level codes")
        for detail in leadership_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - O → P: {corr['reason'][:60]}...")
    else:
        print("   ✓ No leadership level corrections needed")

    # 8. Adjunct position corrections (D1 → D3 for non-faculty)
    print()
    print("8. Adjunct position corrections (D1 → D3)...")
    all_classified, adjunct_stats = apply_adjunct_position_corrections(all_classified)
    post_correction_stats['adjunct_position'] = adjunct_stats
    if adjunct_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {adjunct_stats['corrections_applied']} adjunct position codes")
        for detail in adjunct_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - D1 → D3: {corr['reason'][:60]}...")
    else:
        print("   ✓ No adjunct position corrections needed")

    # 8b. Cross-code position reconciliation (stray D1/D2/D3 title fragment →
    #     the subcode of the appointment group it is embedded in). Runs AFTER
    #     the per-entry position correctors (#4 grant→position, #8 adjunct) so
    #     it reconciles against already-stabilised D-codes. Deterministic; pairs
    #     with the Stage 6 grouped-appointment merge (#156) to rebuild the row.
    print()
    print("8b. Position subcode reconciliation (stray D1/D2/D3 fragment)...")
    all_classified, position_reconcile_stats = apply_position_subcode_reconciliation(all_classified)
    post_correction_stats['position_reconcile'] = position_reconcile_stats
    if position_reconcile_stats['corrections_applied'] > 0:
        print(f"   ✓ Reconciled {position_reconcile_stats['corrections_applied']} stray position fragment(s) to their appointment group")
        for detail in position_reconcile_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - {corr['from']} → {corr['to']}: {corr['reason'][:60]}...")
    else:
        print("   ✓ No stray position fragments to reconcile")

    # 9. Training/compliance corrections (P → B2 for trainings received)
    print()
    print("9. Training/compliance corrections (P → B2)...")
    all_classified, training_stats = apply_training_compliance_corrections(all_classified)
    post_correction_stats['training_compliance'] = training_stats
    if training_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {training_stats['corrections_applied']} training/compliance codes")
        for detail in training_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - P → B2: {corr['reason'][:60]}...")
    else:
        print("   ✓ No training/compliance corrections needed")

    # 10. Invited talk corrections (S8 → R for invited conference talks)
    print()
    print("10. Invited talk corrections (S8 → R)...")
    all_classified, invited_stats = apply_invited_talk_corrections(all_classified)
    post_correction_stats['invited_talk'] = invited_stats
    if invited_stats['corrections_applied'] > 0:
        print(f"   ✓ Corrected {invited_stats['corrections_applied']} invited talk codes")
        for detail in invited_stats['correction_details'][:3]:
            corr = detail['correction']
            print(f"     - S8 → R: {corr['reason'][:60]}...")
    else:
        print("   ✓ No invited talk corrections needed")

    # 10b. WCM structured-table corrections (mentee → N3A/N3B, board cert → F2, licensure → F1)
    print()
    print("10b. WCM structured-table corrections...")
    all_classified, wcm_table_stats = apply_wcm_table_corrections(all_classified)
    post_correction_stats['wcm_table'] = wcm_table_stats
    if wcm_table_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {wcm_table_stats['corrections_made']} WCM table codes")
        for detail in wcm_table_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['reason']}")
    else:
        print("   ✓ No WCM table corrections needed")

    # 10b-2. Prose named-mentee corrections (named individual mentee under an
    #        advising/mentoring section, misclassified as a K teaching code,
    #        -> N3A/N3B). Runs AFTER 10b so structured mentee rows are already
    #        N3A/N3B, and BEFORE 10c so a real named mentee is never demoted to T.
    print()
    print("10b-2. Prose named-mentee corrections...")
    all_classified, prose_mentee_stats = apply_prose_mentee_corrections(all_classified)
    post_correction_stats['prose_mentee'] = prose_mentee_stats
    if prose_mentee_stats['corrections_made'] > 0:
        print(f"   ✓ Corrected {prose_mentee_stats['corrections_made']} prose mentee codes")
        for detail in prose_mentee_stats['correction_details'][:3]:
            print(f"     - {detail['original']} → {detail['corrected_to']}: {detail['reason']}")
    else:
        print("   ✓ No prose mentee corrections needed")

    # 10c. Template-scaffold suppression (filled-template instruction text → T)
    #      Runs AFTER 10b so pure template strings (e.g. the board-table header
    #      row) end as T rather than being promoted to a content code.
    print()
    print("10c. Template-scaffold corrections...")
    all_classified, scaffold_stats = apply_template_scaffold_corrections(all_classified)
    post_correction_stats['template_scaffold'] = scaffold_stats
    if scaffold_stats['corrections_made'] > 0:
        print(f"   ✓ Recoded {scaffold_stats['corrections_made']} template-scaffold entries to T")
    else:
        print("   ✓ No template-scaffold entries detected")

    # 10c. Block-coherence repair (#198): third-party records (mentees, lab staff,
    #      students) misrouted into the SUBJECT's own sections when a body-styled
    #      sub-header was flattened. Notices incoherent / orphaned-header people
    #      blocks and punts each to an LLM judge; applies only self-family -> N
    #      reattributions. Default OFF (one LLM call per flagged block) until the
    #      gold-set regression lands -- enable with CVICHE_BLOCK_COHERENCE_REPAIR=1.
    if os.getenv("CVICHE_BLOCK_COHERENCE_REPAIR", "0") == "1":
        print()
        print("10c. Block-coherence repair (subject vs third-party)...")

        def _block_coherence_llm(prompt: str) -> str:
            return call_llm(
                stage="stage_3b_block_coherence",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1500,
            )["content"]

        all_classified, block_coherence_stats = apply_block_coherence_corrections(
            all_classified, llm=_block_coherence_llm, apply=True, subject_name=person_name
        )
        post_correction_stats['block_coherence'] = block_coherence_stats
        if block_coherence_stats['corrections_made'] > 0:
            print(f"   ✓ Reattributed {block_coherence_stats['corrections_made']} third-party rows to N")
            for f in block_coherence_stats['flagged'][:3]:
                v = f.get('verdict') or {}
                print(f"     - block n={f['n']} {f['families']} -> {v.get('verdict')} (conf {v.get('confidence')})")
        else:
            print("   ✓ No third-party misroutes corrected")

    # 11. Hierarchy-taxonomy mismatch flagging (QA review flags)
    print()
    print("11. Hierarchy-taxonomy mismatch flagging (QA)...")
    all_classified, mismatch_stats = flag_hierarchy_mismatches(all_classified)
    post_correction_stats['hierarchy_mismatches'] = mismatch_stats
    if mismatch_stats['entries_flagged'] > 0:
        print(f"   ⚠ Flagged {mismatch_stats['entries_flagged']} entries for QA review")
        for detail in mismatch_stats['flagged_details'][:3]:
            print(f"     - {detail['assigned_code']} under '{' > '.join(detail['hierarchy'][:2])}': {detail['text_preview'][:40]}...")
        if mismatch_stats['entries_flagged'] > 3:
            print(f"     ... and {mismatch_stats['entries_flagged'] - 3} more")
    else:
        print("   ✓ No hierarchy-taxonomy mismatches detected")

    # Summary
    total_post_corrections = (
        structural_stats['corrections_made'] +
        committee_stats['corrections_made'] +
        reasoning_stats['corrections_made'] +
        grant_stats['corrections_applied'] +
        teaching_stats['corrections_applied'] +
        leadership_stats['corrections_applied'] +
        adjunct_stats['corrections_applied'] +
        position_reconcile_stats['corrections_applied'] +
        training_stats['corrections_applied'] +
        invited_stats['corrections_applied']
    )
    print()
    print(f"Post-correction summary: {total_post_corrections} total corrections applied")
    print("=" * 60)

    # Add to total stats
    total_stats['post_classification_corrections'] = post_correction_stats
    total_stats['total_post_corrections'] = total_post_corrections

    # Prepare output
    if output_dir is None:
        output_dir = base_dir / "stage_3b_classified_entries"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_uid}_classified.json"

    # Build output document
    output_doc = {
        "document_uid": document_uid,
        "stage": "3b",
        "stage_name": "Entry Classification",
        "source_files": {
            "stage_2": str(stage_2_path),
            "stage_3a": str(stage_3a_path)
        },
        "entries": all_classified,
        "meta": {
            "model": total_stats.get("model") or model,
            "taxonomy_version": taxonomy["meta"]["version"],
            "total_entries": len(all_classified),
            "duplicate_entries": duplicate_count,
            "unique_entries": len(all_classified) - duplicate_count,
            "hierarchy_groups": len(groups),
            "stats": total_stats,
            # LLM-vs-fallback provenance for the initial classification pass
            # (#61); monitoring reads fallback_rate / had_classification_errors
            "classification_stats": {
                "total_entries": total_stats["entries_classified"],
                "llm_classified": total_stats["llm_classified"],
                "fallback_entries": total_stats["fallback_entries"],
                "empty_entries": total_stats["empty_entries"],
                "llm_batches": total_stats["llm_batches"],
                "failed_batches": total_stats["failed_batches"],
                "fallback_rate": round(
                    total_stats["fallback_entries"] / total_stats["entries_classified"], 4
                ) if total_stats["entries_classified"] else 0.0,
                "had_classification_errors": total_stats["failed_batches"] > 0
            },
            "post_correction_summary": {
                "total_corrections": total_post_corrections,
                "structural_corrections": structural_stats['corrections_made'],
                "committee_corrections": committee_stats['corrections_made'],
                "reasoning_corrections": reasoning_stats['corrections_made'],
                "grant_status_corrections": grant_stats['corrections_applied'],
                "teaching_leadership_corrections": teaching_stats['corrections_applied'],
                "leadership_level_corrections": leadership_stats['corrections_applied'],
                "adjunct_position_corrections": adjunct_stats['corrections_applied'],
                "training_compliance_corrections": training_stats['corrections_applied'],
                "invited_talk_corrections": invited_stats['corrections_applied']
            },
            "qa_flags": {
                "hierarchy_mismatches": mismatch_stats['entries_flagged'],
                "mismatch_summary": get_mismatch_summary(all_classified) if mismatch_stats['entries_flagged'] > 0 else None
            },
            "generated_at": datetime.now().isoformat()
        }
    }

    # Add duplicate pairs info if any found
    if duplicate_pairs:
        output_doc["meta"]["duplicate_pairs"] = duplicate_pairs

    # Compute code distribution
    code_counts = {}
    for entry in all_classified:
        code = entry.get("taxonomy_code", "?")
        code_counts[code] = code_counts.get(code, 0) + 1

    output_doc["meta"]["code_distribution"] = dict(sorted(code_counts.items()))

    # Write output
    with open(output_path, 'w') as f:
        json.dump(output_doc, f, indent=2)

    print()
    print(f"Output: {output_path}")
    print("=" * 80)

    # Print code distribution
    print()
    print("CODE DISTRIBUTION:")
    print("-" * 40)
    for code, count in sorted(code_counts.items(), key=lambda x: -x[1]):
        print(f"  {code}: {count}")

    return {
        "document_uid": document_uid,
        "output_path": str(output_path),
        "total_entries": len(all_classified),
        "stats": total_stats,
        "code_distribution": code_counts
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage_3b_entry_classifier.py <document_uid> [model]")
        print("  document_uid: Document identifier (e.g., 2086_Jones_Webb)")
        print("  model: Optional, defaults to gpt-5.1")
        print()
        print("Prerequisites:")
        print("  - Stage 2 output: outputs/stage_2_entry_extraction/{uid}_entries.json")
        print("  - Stage 3a output: outputs/stage_3a_header_mappings/{uid}_header_taxonomy.json")
        sys.exit(1)

    document_uid = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "gpt-5.1"

    result = run_stage_3b(document_uid, model=model)
