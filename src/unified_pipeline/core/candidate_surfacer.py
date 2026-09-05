"""
LLM-Based Candidate Surfacing for Taxonomy Classification

Instead of rule-based keyword filtering, this module uses an LLM to intelligently
analyze CV structure (section/subsection headers + sample entries) and surface
the most relevant taxonomy codes.

Key advantages over keyword filtering:
- Semantic understanding of CV context
- Evidence-based decisions from sample entries
- Natural cross-category override detection
- Explainable reasoning for candidate selection
- Handles edge cases we didn't anticipate

Architecture:
1. Analyze subsection headers + 2-3 sample entries
2. LLM identifies 10-15 most relevant taxonomy codes
3. Returns primary candidates (high likelihood) + secondary (fallback)
4. Includes reasoning and automatic override detection
"""

import json
import time
from typing import List, Dict, Any, Optional, Tuple

from unified_pipeline.llm_client import call_llm


# =============================================================================
# TAXONOMY CODE DESCRIPTIONS (Condensed for LLM)
# =============================================================================

# Condensed taxonomy reference for candidate surfacing prompt
# Complete WCM CV Taxonomy - includes all parent, child, and sub-child codes
TAXONOMY_CODES_CONDENSED = {
    # Parent codes
    "A": "Personal/Contact Information",
    "C": "Honors and Awards",
    "E": "Hospital Appointments",
    "G": "Languages",
    "H": "Military Service",
    "I": "Professional Societies",
    "J": "Committees (External)",
    "O": "Leadership Positions",
    "P": "Institutional/Departmental Service",
    "R": "Media Appearances/Interviews",
    "T": "Appendix/Other",

    # B - Education
    "B1": "Academic Degrees (MD, PhD, etc.)",
    "B2": "Other Educational Experiences",

    # D - Professional Positions
    "D1": "Academic Appointments",
    "D2": "Hospital Appointments",
    "D3": "Other Professional Positions & Employment",

    # F - Licensure & Certification
    "F1": "Licensure",
    "F2": "Board Certification",

    # K - Educational Contributions
    "K1": "Group Teaching",
    "K2": "Curriculum Development",
    "K3": "Individual Teaching/Mentoring (non-degree seeking)",
    "K4": "Teaching Materials",
    "K5": "Community education and educational programs open to the public",

    # L - Clinical Practice
    "L1": "Clinical Practice",
    "L2": "Clinical Innovations",
    "L3": "Clinical Leadership",

    # M - Research
    "M1": "Research Interests/Mission Statement",
    "M2": "Research Support/Funding (parent)",
    "M2A": "Current Funding",
    "M2B": "Past (Completed) Funding",
    "M2C": "Pending Funding",
    "M2D": "Patents & Innovations",
    # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C based on status

    # N - Mentoring
    "N1": "Leadership and mentoring in programs",
    "N2": "Institutional Training Grants",
    "N3": "Mentees (parent)",
    "N3A": "Current Mentees",
    "N3B": "Past Mentees",
    "N4": "Scholarly Outputs Resulting From Mentorship",

    # Q - Professional Development
    "Q1": "Invited Lectures",
    "Q2": "Symposia Chaired/Organized",
    "Q3": "Discussant/Moderator",
    "Q4": "Editorial Activities (parent)",
    "Q4A": "Editor/Co-Editor",
    "Q4B": "Journals/Textbooks/Books (Editorial roles)",
    "Q4C": "Editorial Board Membership",
    "Q4D": "Journal Reviewing/Ad hoc Reviewing",

    # S - Scholarly Output
    "S1": "Peer-reviewed Research Articles",
    "S2": "Reviews and Editorials",
    "S3": "Books",
    "S4": "Chapters",
    "S5": "Non-peer-reviewed Research Publications",
    "S6": "Case Reports",
    "S7": "In review (manuscripts submitted or in preparation)",
    "S8": "Abstracts",
    "S9": "Other (media, podcasts, etc.)"
}


# =============================================================================
# CANDIDATE SURFACING FUNCTION
# =============================================================================

def surface_candidates_for_subsection(
    section_header: str,
    subsection_header: str,
    sample_entries: list[str],
    model: str = "gpt-5.1",
    max_candidates: int = 12
) -> dict[str, Any]:
    """
    Use LLM to analyze CV subsection structure and surface relevant taxonomy codes.

    This replaces rule-based keyword filtering with intelligent semantic analysis.
    The LLM examines:
    - Section header (e.g., "Teaching")
    - Subsection header (e.g., "Teaching Publications:")
    - Sample entries (2-3 examples showing actual content)

    And returns the most likely taxonomy codes with reasoning.

    Args:
        section_header: Top-level CV section (e.g., "Teaching", "Research")
        subsection_header: Subsection within that section (e.g., "Teaching Publications:")
        sample_entries: 2-3 representative entries from this subsection
        model: LLM model to use (default: gpt-4o-mini for cost efficiency)
        max_candidates: Maximum primary candidates to return (default: 12)

    Returns:
        {
            'primary_candidates': [
                {'code': 'S1', 'likelihood': 0.30, 'reasoning': '...'},
                {'code': 'S2', 'likelihood': 0.25, 'reasoning': '...'},
                ...
            ],
            'secondary_candidates': [
                {'code': 'K1', 'likelihood': 0.05, 'reasoning': '...'},
                ...
            ],
            'override_detected': {
                'from_parent': 'K',
                'to_parent': 'S',
                'reason': 'Sample entries are publications, not teaching activities'
            } or None,
            'confidence': 0.85,
            'token_usage': {...},
            'elapsed_time': 1.23
        }

    Example:
        >>> surface_candidates_for_subsection(
        ...     section_header="Teaching",
        ...     subsection_header="Teaching Publications:",
        ...     sample_entries=[
        ...         "Instructor's Manual for By the People, 2012",
        ...         "Article: 'Benefits of Teaching Government', 2006"
        ...     ]
        ... )
        {
            'primary_candidates': [
                {'code': 'S3', 'likelihood': 0.25, 'reasoning': 'Books/manuals'},
                {'code': 'S4', 'likelihood': 0.25, 'reasoning': 'Book chapters'},
                {'code': 'S1', 'likelihood': 0.20, 'reasoning': 'Journal articles'},
                ...
            ],
            'override_detected': {
                'from_parent': 'K',
                'to_parent': 'S',
                'reason': 'Samples show scholarly publications, not teaching activities'
            }
        }
    """
    start_time = time.time()

    # Build system prompt
    system_prompt = """You are a CV taxonomy expert analyzing CV structure to identify relevant taxonomy codes.

Your task: Given a CV subsection (with headers and sample entries), identify the 10-15 most relevant taxonomy codes.

Analysis approach:
1. **Evidence-based**: Look at SAMPLE ENTRIES to see what content actually appears
2. **Semantic understanding**: Don't just match keywords - understand intent
3. **Cross-category awareness**: Detect when subsection contradicts parent section
4. **Granular specificity**: Include both broad and specific codes when relevant

KEYWORD-BASED CLASSIFICATION GUIDANCE:

Academic Positions (keywords: professor, instructor, lecturer, faculty, chair):
- "Assistant Professor", "Associate Professor", "Full Professor" → D1 (Current) or D2 (Previous)
- "Lecturer", "Instructor", "Adjunct Faculty" → D1 or D2
- "Department Chair", "Director", "Dean" → O (Leadership)

Conference Presentations (keywords: conference, symposium, poster, abstract, presentation):
- Conference talks, posters, abstracts → S8 (Abstracts)
- Examples: "Poster at APSA", "Abstract at Annual Meeting", "Presentation at Conference"

Committee Service (keywords: committee, dissertation, thesis):
- "PhD committee member", "Dissertation committee", "Thesis advisor" → P (Institutional Admin)
- Primary advisor relationships → N1/N3/N4 (Mentoring)

Publications (keywords: journal, article, book, chapter, review, editor):
- Peer-reviewed journal articles → S1
- Books and monographs → S3
- Book chapters → S4
- Reviews and editorials → S2

Grants & Funding (keywords: grant, funding, award, NIH, NSF):
- Active research support → M2 (Research Support)
- Completed grants → M2

Teaching Activities (keywords: course, seminar, lecture, curriculum):
- Courses and seminars → K1 (Group Teaching)
- Curriculum development → K2

Example cross-category case:
- Section: "Teaching"
- Subsection: "Teaching Publications:"
- Samples: "Instructor Manual...", "Article: 'Benefits of Teaching...'"
- Analysis: These are PUBLICATIONS (S codes), not teaching activities (K codes)
- Override: from K → to S"""

    # Build user prompt
    user_prompt = f"""Analyze this CV subsection and identify relevant taxonomy codes:

SECTION: {section_header}
SUBSECTION: {subsection_header}

IMPORTANT - SECTION-HEADER-BASED PRIORITIES:
Check if section/subsection headers contain these keywords and prioritize accordingly:

1. **Position keywords** ("Position", "Employment", "Appointment", "Academic", "Faculty"):
   → ALWAYS include D1/D2 as PRIMARY candidates with high likelihood (0.30+)
   → These codes describe academic/clinical positions regardless of sample content
   → Example: "Previous Positions" → D2 must be primary candidate

2. **Publication keywords** ("Publication", "Bibliography", "Scholarly Output"):
   → ALWAYS include S-family codes (S1, S2, S3, S4, S8) as primary candidates
   → Example: "Publications" → S1, S3, S4 must be primary

3. **Research keywords** ("Research", "Grants", "Funding"):
   → ALWAYS include M1/M2 as primary candidates
   → Example: "Research Support" → M2 must be primary

4. **Teaching keywords** ("Teaching", "Education", "Instruction"):
   → ALWAYS include K-family codes as primary candidates
   → Example: "Teaching Experience" → K1, K3 must be primary

SAMPLE ENTRIES (showing actual content):
"""

    for i, entry in enumerate(sample_entries[:3], 1):
        # Truncate long entries
        entry_preview = entry[:200] + "..." if len(entry) > 200 else entry
        user_prompt += f"{i}. {entry_preview}\n"

    user_prompt += f"""

AVAILABLE TAXONOMY CODES:
{json.dumps(TAXONOMY_CODES_CONDENSED, indent=2)}

Your analysis:
1. What do the SAMPLE ENTRIES actually describe?
2. Does the SUBSECTION HEADER indicate a specific type?
3. Should the SECTION HEADER override clear evidence from samples?
4. Are there cross-category signals? (e.g., "Publications" under "Teaching")

Return JSON with:
{{
  "primary_candidates": [
    {{"code": "S1", "likelihood": 0.30, "reasoning": "Sample shows journal articles"}},
    {{"code": "S3", "likelihood": 0.25, "reasoning": "Sample shows books/manuals"}},
    ...  // Top {max_candidates} most likely codes
  ],
  "secondary_candidates": [
    {{"code": "K1", "likelihood": 0.05, "reasoning": "Edge case: teaching materials"}},
    ...  // Lower likelihood alternatives (max 5)
  ],
  "override_detected": {{
    "from_parent": "K",
    "to_parent": "S",
    "reason": "Sample entries are publications (scholarly outputs), not teaching activities"
  }} or null,
  "confidence": 0.85  // How confident are you in this analysis?
}}

IMPORTANT:
- Likelihood scores should sum to ~1.0 across all candidates
- Include reasoning for EACH candidate
- Detect override ONLY if samples clearly contradict section header
- Primary candidates: high likelihood (>0.05)
- Secondary candidates: fallback options (<0.05)"""

    # Prepare API call
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # JSON schema for response
    response_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "candidate_analysis",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "primary_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "likelihood": {"type": "number"},
                                "reasoning": {"type": "string"}
                            },
                            "required": ["code", "likelihood", "reasoning"],
                            "additionalProperties": False
                        }
                    },
                    "secondary_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "likelihood": {"type": "number"},
                                "reasoning": {"type": "string"}
                            },
                            "required": ["code", "likelihood", "reasoning"],
                            "additionalProperties": False
                        }
                    },
                    "override_detected": {
                        "type": ["object", "null"],
                        "properties": {
                            "from_parent": {"type": "string"},
                            "to_parent": {"type": "string"},
                            "reason": {"type": "string"}
                        },
                        "required": ["from_parent", "to_parent", "reason"],
                        "additionalProperties": False
                    },
                    "confidence": {"type": "number"}
                },
                "required": ["primary_candidates", "secondary_candidates", "override_detected", "confidence"],
                "additionalProperties": False
            }
        }
    }

    # Call LLM
    try:
        result_llm = call_llm(
            stage="core_candidate_surfacer",
            messages=messages,
            response_format=response_schema,
            max_tokens=1500,
        )

        # Parse response
        result = json.loads(result_llm["content"])

        # Add metadata
        result['token_usage'] = {
            'prompt_tokens': result_llm["prompt_tokens"],
            'completion_tokens': result_llm["completion_tokens"],
            'total_tokens': result_llm["total_tokens"]
        }
        result['elapsed_time'] = time.time() - start_time
        result['model'] = result_llm["model"]
        result['success'] = True

        return result

    except Exception as e:
        # Return error result
        return {
            'success': False,
            'error': str(e),
            'primary_candidates': [],
            'secondary_candidates': [],
            'override_detected': None,
            'confidence': 0.0,
            'token_usage': {},
            'elapsed_time': time.time() - start_time
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_candidates_for_prompt(
    primary_candidates: list[dict],
    secondary_candidates: list[dict],
    include_full_descriptions: bool = True
) -> str:
    """
    Format surfaced candidates for use in entry classification prompt.

    Args:
        primary_candidates: List of primary candidate dicts from surface_candidates_for_subsection()
        secondary_candidates: List of secondary candidate dicts
        include_full_descriptions: Whether to include detailed code descriptions

    Returns:
        Formatted string for LLM prompt with PRIMARY and ESCAPE HATCH sections
    """
    lines = []

    # Primary candidates (detailed)
    lines.append("=" * 70)
    lines.append("PRIMARY CANDIDATE CODES (pre-filtered as most likely):")
    lines.append("=" * 70)
    lines.append("")

    for i, cand in enumerate(primary_candidates, 1):
        code = cand['code']
        likelihood = cand['likelihood']
        reasoning = cand['reasoning']

        # Get full description from TAXONOMY_CODES_CONDENSED
        description = TAXONOMY_CODES_CONDENSED.get(code, "Unknown code")

        lines.append(f"{i}. {code}: {description}")
        lines.append(f"   Likelihood: {likelihood:.2f} - {reasoning}")

        if include_full_descriptions:
            # Could add examples here if we had them
            pass

        lines.append("")

    # Escape hatch (condensed)
    if secondary_candidates:
        lines.append("")
        lines.append("=" * 70)
        lines.append("ESCAPE HATCH (lower likelihood alternatives):")
        lines.append("=" * 70)
        lines.append("")
        lines.append("If none of the PRIMARY candidates fit, you MAY select from:")
        lines.append("")

        for cand in secondary_candidates:
            code = cand['code']
            description = TAXONOMY_CODES_CONDENSED.get(code, "Unknown")
            lines.append(f"  • {code}: {description}")

        lines.append("")
        lines.append("IMPORTANT: If using ESCAPE HATCH:")
        lines.append("  - Explain why PRIMARY candidates don't fit")
        lines.append("  - Provide detailed reasoning for alternative choice")
        lines.append("  - Set used_escape_hatch: true in response")
        lines.append("")

    return "\n".join(lines)


def extract_parent_code(code: str) -> str:
    """Extract parent code from child code (e.g., 'S1' → 'S', 'K3' → 'K')."""
    if code and len(code) > 0:
        return code[0]
    return code


def has_subsections(parent_code: str) -> bool:
    """Check if a parent code has child subsections."""
    parents_with_children = ['A', 'B', 'C', 'D', 'K', 'M', 'N', 'Q', 'R', 'S']
    return parent_code in parents_with_children
