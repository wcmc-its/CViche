"""
Smart Candidate Filtering for Single-Pass Taxonomy Classification

This module replaces the constrained two-pass approach with intelligent candidate
filtering that narrows down 60+ taxonomy codes to the most likely 10-15 candidates
based on:
- Section header keywords
- Hierarchy position hints
- Entry text patterns
- Cross-category confusion triggers

The filtered candidates are then passed to the LLM with rich guidance instead of
hard ENUM constraints, allowing it to choose the correct code even for cross-category
subsections (e.g., "Teaching Awards" → H not K).

Architecture: Single-pass guided classification with escape hatch
"""

import re
from typing import List, Tuple, Dict, Optional
from .confusion_matrix import SECTION_CONFUSION_MATRIX
from .hierarchy_overrides import detect_cross_category_override


# =============================================================================
# KEYWORD-TO-CODE MAPPING
# =============================================================================

# Extracted from confusion matrix trigger keywords and common CV section patterns
KEYWORD_PATTERNS = {
    # Bibliography (S) - Most common and complex
    'S': [
        r'\bpublication', r'\bbibliography\b', r'\bpublished', r'\barticle',
        r'\bjournal', r'\bpeer.?reviewed', r'\bbook', r'\bchapter',
        r'\breview', r'\beditorial', r'\bcommentary', r'\bperspective',
        r'\babstract', r'\bposter', r'\bpresentation', r'\bproceeding',
        r'\bpreprint', r'\bsubmitted', r'\bin press', r'\bin preparation',
        r'\bmanuscript', r'\bpaper', r'\bscholarship', r'\bwriting',
        r'\bdoi:', r'\bpmid:', r'\bpubmed', r'\barxiv', r'\bbiorxiv',
        r'\bblog', r'\bpodcast', r'\bvideo', r'\bmedia', r'\bwebinar',
        r'\bsoftware', r'\bcode', r'\bgithub', r'\bdataset', r'\bdata',
        r'\bcase report', r'\bclinical report', r'\bpatient description'
    ],

    # Honors & Awards (H)
    'H': [
        r'\baward', r'\bhonor', r'\bprize', r'\brecognition',
        r'\baccolade', r'\bfellow\s+of\b', r'\belected\s+to\b',
        r'\bdistinguished', r'\boutstanding', r'\bexcellence',
        r'\bbest\s+paper', r'\bbest\s+poster', r'\bacademy\s+of',
        r'\brecipient', r'\bhonoree'
    ],

    # Teaching (K)
    'K': [
        r'\bteaching', r'\bcourse', r'\blecture', r'\bseminar',
        r'\bcurriculum', r'\bsyllabus', r'\binstructor', r'\bprofessor',
        r'\beducation', r'\bpedagogy', r'\bstudent', r'\bclass',
        r'\bworkshop', r'\btraining\s+program', r'\bprecepting',
        r'\bclerkship', r'\brotation', r'\bteach'
    ],

    # Mentoring (N)
    'N': [
        r'\bmentoring', r'\bmentor', r'\badvising', r'\badvisor',
        r'\bmentee', r'\badvisee', r'\bsupervised', r'\bsupervis',
        r'\bcommittee\s+member', r'\bthesis\s+committee', r'\bdissertation',
        r'\bphd\s+student', r'\bpostdoc', r'\bfellow\b', r'\btrainee',
        r'\bdirected\s+study', r'\bindependent\s+study', r'\bgraduate\s+student'
    ],

    # Research (M)
    'M': [
        r'\bresearch', r'\bgrant', r'\bfunding', r'\bpi\b', r'\bco-i\b',
        r'\binvestigat', r'\bstudy', r'\bproject', r'\br01\b', r'\bk23\b',
        r'\bnih\b', r'\bnsf\b', r'\bpatent', r'\binvention',
        r'\bintellectual\s+property', r'\bip\b', r'\bprovisional',
        r'\bfiled', r'\bissued', r'\blicensing'
    ],

    # Invitations to Speak (R)
    'R': [
        r'\binvited', r'\bkeynote', r'\bplenary', r'\bgrand\s+rounds',
        r'\bnamed\s+lecture', r'\bvisiting\s+professor', r'\bguest\s+lecture',
        r'\bspeaker', r'\btalk\b', r'\bpresentation\b'
    ],

    # Professional Service (Q)
    'Q': [
        r'\beditorial\s+board', r'\beditor', r'\breviewer', r'\breview\s+for',
        r'\bmanuscript\s+review', r'\bguest\s+editor', r'\bassociate\s+editor',
        r'\bstudy\s+section', r'\bgrant\s+review', r'\bfunding\s+panel',
        r'\bboard\s+member', r'\bleadership', r'\bcommittee\s+chair',
        r'\btask\s+force', r'\badvocacy', r'\bprofessional\s+service'
    ],

    # Professional Organizations (I)
    'I': [
        r'\bmember', r'\bmembership', r'\bsociety', r'\bassociation',
        r'\borganization', r'\baffiliat', r'\bfellow\s+member'
    ],

    # Institutional Leadership (O)
    'O': [
        r'\bchair', r'\bdirector', r'\bhead\s+of', r'\bchief',
        r'\bleader', r'\boversaw', r'\bmanaged', r'\bresponsible\s+for',
        r'\bbudget', r'\bpersonnel', r'\bprogram\s+direct',
        r'\bdepartment\s+head', r'\bdivision\s+chief'
    ],

    # Professional Positions (D)
    'D': [
        r'\bprofessor', r'\bassistant\s+professor', r'\bassociate\s+professor',
        r'\bfaculty', r'\bposition', r'\bappointment', r'\bemployment',
        r'\battending', r'\bclinician', r'\bphysician', r'\bresearch\s+scientist'
    ],

    # Education (B)
    'B': [
        r'\bdegree', r'\bmd\b', r'\bphd\b', r'\bms\b', r'\bmph\b', r'\bmba\b',
        r'\bbs\b', r'\bba\b', r'\buniversit', r'\bcollege', r'\bschool\s+of',
        r'\bgraduate', r'\bundergraduate', r'\bmajor', r'\bgraduat',
        r'\bthesis\s+title', r'\bdissertation\s+title'
    ],

    # Postdoctoral Training (C)
    'C': [
        r'\bpostdoc', r'\bresidency', r'\bfellowship', r'\bfellow\b',
        r'\bresiden', r'\btraining\s+program', r'\brotation'
    ],

    # Licensure (E)
    'E': [
        r'\blicense', r'\bcertificat', r'\bboard\s+certif', r'\bstate\s+license',
        r'\bmedical\s+license', r'\brn\b', r'\blpn\b', r'\blicensed'
    ],

    # Clinical Practice (P) - Note: L is reserved, using P
    'P': [
        r'\bclinic', r'\bpractice', r'\bpatient', r'\bcare', r'\btreatment',
        r'\bprocedure', r'\bconsult', r'\bhospital', r'\bmedical\s+center'
    ]
}

# Specific child code patterns for high-confidence disambiguation
CHILD_CODE_PATTERNS = {
    # Bibliography S1-S12
    'S1': [r'\bjournal', r'\barticle', r'\bpeer.?review', r'\boriginal\s+research',
           r'\bmethods\s+and\s+results', r'\bdata\s+analysis'],
    'S2': [r'\breview', r'\beditorial', r'\bcommentary', r'\bperspective',
           r'\bmeta-analysis', r'\bsystematic\s+review', r'\bguideline'],
    'S3': [r'\bbook\b', r'\bmonograph', r'\bvolume', r'\bpublisher:',
           r'\bisbn', r'\bedition'],
    'S4': [r'\bchapter', r'\bin:\s+', r'\bedited\s+by', r'\beditor:',
           r'\bbook\s+title:', r'\bpp\.\s+\d'],
    'S5': [r'\bcase\s+report', r'\bclinical\s+case', r'\bpatient\s+presentation'],
    'S6': [r'\bletter', r'\bcorresponden', r'\bcomment\s+on', r'\bresponse\s+to'],
    'S7': [r'\bsubmitted', r'\bin\s+review', r'\bin\s+preparation',
           r'\bin\s+press', r'\baccepted', r'\brevise\s+and\s+resubmit'],
    'S8': [r'\babstract', r'\bposter', r'\bproceeding', r'\bconference',
           r'\bmeeting', r'\bsupplement', r'\bsuppl\b'],
    'S9': [r'\bblog', r'\bpodcast', r'\bvideo', r'\bwebinar', r'\bmedia\s+interview',
           r'\byoutube', r'\bvimeo', r'\bnews\s+article'],
    'S11': [r'\bsoftware', r'\bcode', r'\bgithub', r'\bpackage', r'\brepository',
            r'\bopen.?source', r'\brelease', r'\bversion', r'\bpypi', r'\bcran'],
    'S12': [r'\bdataset', r'\bdata\s+reposit', r'\bzenodo', r'\bdryad',
            r'\bfigshare', r'\bdata\s+availab'],

    # Teaching K1-K4
    'K1': [r'\bcourse', r'\blecture', r'\bclass', r'\bteach', r'\binstructor'],
    'K2': [r'\bcurriculum', r'\bsyllabus', r'\bprogram\s+develop', r'\bcourse\s+design'],
    'K3': [r'\bclerkship', r'\brotation', r'\bprecepting', r'\bclinical\s+teach'],
    'K4': [r'\bworkshop', r'\bseminar', r'\btraining', r'\beducational'],

    # Mentoring N1-N4
    'N1': [r'\bcurrent', r'\bongoing', r'\bpresent\b', r'\b\d{4}\s*-\s*$'],
    'N3': [r'\bpast', r'\bformer', r'\bcompleted', r'\bgraduated', r'\bdefended'],
    'N4': [r'\bstatement', r'\bphilosoph', r'\bapproach', r'\bbelief'],

    # Research M1-M2D
    'M1': [r'\bproject', r'\bstudy', r'\binvestigat', r'\bresearch\s+focus'],
    'M2': [r'\bgrant', r'\bfunding', r'\bpi\b', r'\bco-i', r'\br01', r'\bnih', r'\bnsf'],
    'M2D': [r'\bpatent', r'\binvention', r'\bip\b', r'\bfiled', r'\bissued', r'\bus\d{7}'],

    # Professional Service Q1-Q4
    'Q1': [r'\bpresident', r'\bchair', r'\bofficer', r'\bleader', r'\bboard\s+member'],
    'Q2': [r'\bgrant\s+review', r'\bstudy\s+section', r'\bfunding\s+panel'],
    'Q3': [r'\beditorial', r'\beditor', r'\breview', r'\bmanuscript'],
    'Q4': [r'\bcommittee', r'\btask\s+force', r'\badvocacy', r'\bcommunity']
}


# =============================================================================
# CROSS-CATEGORY OVERRIDE SIGNALS
# =============================================================================

def get_override_signals(
    section_header: str,
    subsection_header: str,
    hierarchy: List[str]
) -> Optional[Tuple[str, str]]:
    """
    Check if this subsection should map to a different category than its parent.

    Uses the same logic as hierarchy_overrides.py but returns candidate hints
    instead of enforcing hard overrides.

    Returns:
        Tuple of (override_parent_code, reason) or None
    """
    subsection_lower = subsection_header.lower().strip()

    # Awards subsections → H
    award_patterns = [
        r'\bawards?\b', r'\bhonors?\b', r'\bprizes?\b',
        r'\brecognition\b', r'\baccolades?\b'
    ]
    if any(re.search(pat, subsection_lower) for pat in award_patterns):
        funding_keywords = ['grant', 'funding', 'pi', 'co-i', 'r01', 'k23', 'award amount']
        if not any(kw in subsection_lower for kw in funding_keywords):
            return ('H', f"Award subsection: '{subsection_header}'")

    # Committee subsections → N
    committee_patterns = [
        r'\bcommittees?\b', r'\bph\.?d\.?\s+committee', r'\bm\.?a\.?\s+committee',
        r'\bdissertation\s+committee', r'\bthesis\s+committee'
    ]
    if any(re.search(pat, subsection_lower) for pat in committee_patterns):
        return ('N', f"Committee subsection: '{subsection_header}'")

    # Mentoring subsections → N
    mentoring_patterns = [
        r'\bmentoring\b', r'\bmentor(ship)?\b', r'\badvis(ing|ory|or)\b',
        r'\bsupervis(ing|ion|or)\b'
    ]
    if any(re.search(pat, subsection_lower) for pat in mentoring_patterns):
        formal_keywords = ['student', 'postdoc', 'trainee', 'fellow', 'mentee']
        if any(kw in subsection_lower for kw in formal_keywords):
            return ('N', f"Mentoring subsection: '{subsection_header}'")

    # Publication subsections → S
    publication_patterns = [
        r'\bpublications?\b', r'\bpublished\b', r'\bscholarship\b',
        r'\bwriting\b', r'\barticles?\b'
    ]
    if any(re.search(pat, subsection_lower) for pat in publication_patterns):
        if 'publication' in subsection_lower or 'published' in subsection_lower:
            return ('S', f"Publication subsection: '{subsection_header}'")

    return None


# =============================================================================
# CANDIDATE SCORING AND FILTERING
# =============================================================================

def calculate_candidate_likelihood(
    code: str,
    entry_text: str,
    section_header: str,
    subsection_header: str,
    hierarchy: List[str],
    override_parent: Optional[str] = None
) -> float:
    """
    Calculate likelihood score (0.0 to 1.0) for a candidate taxonomy code.

    Scoring factors:
    - Override signal: +0.5 (strong hint)
    - Section header match: +0.3
    - Entry text pattern match: +0.2
    - Child code specific pattern: +0.1
    - Hierarchy depth hint: +0.05

    Args:
        code: Taxonomy code to score (e.g., 'S1', 'H', 'K1')
        entry_text: Entry content to analyze
        section_header: Top-level section header
        subsection_header: Immediate subsection header
        hierarchy: Full hierarchy path
        override_parent: Parent code from override detection (if any)

    Returns:
        Likelihood score 0.0-1.0
    """
    score = 0.0
    text_lower = entry_text.lower()
    section_lower = section_header.lower()
    subsection_lower = subsection_header.lower()

    parent_code = code[0] if len(code) > 0 else ''

    # Factor 1: Override signal (strongest hint)
    if override_parent and parent_code == override_parent:
        score += 0.5

    # Factor 2: Section header keyword match
    if parent_code in KEYWORD_PATTERNS:
        header_text = f"{section_lower} {subsection_lower}"
        patterns = KEYWORD_PATTERNS[parent_code]
        matches = sum(1 for pat in patterns if re.search(pat, header_text))
        score += min(0.3, matches * 0.1)  # Cap at 0.3

    # Factor 3: Entry text pattern match
    if parent_code in KEYWORD_PATTERNS:
        patterns = KEYWORD_PATTERNS[parent_code]
        matches = sum(1 for pat in patterns if re.search(pat, text_lower))
        score += min(0.2, matches * 0.05)  # Cap at 0.2

    # Factor 4: Child code specific pattern
    if code in CHILD_CODE_PATTERNS:
        patterns = CHILD_CODE_PATTERNS[code]
        matches = sum(1 for pat in patterns if re.search(pat, text_lower))
        score += min(0.1, matches * 0.03)  # Cap at 0.1

    # Factor 5: Hierarchy depth hint (deeper = more specific)
    hierarchy_depth = len(hierarchy)
    if hierarchy_depth >= 3:
        score += 0.05

    return min(1.0, score)  # Cap at 1.0


def filter_candidate_codes(
    entry_text: str,
    section_header: str,
    subsection_header: str,
    hierarchy: List[str],
    target_count: int = 12,
    min_score: float = 0.1
) -> List[Tuple[str, float, str]]:
    """
    Filter taxonomy codes to most likely candidates based on context.

    Returns top N candidates with likelihood scores, designed to replace the
    constrained two-pass approach with guidance-based single-pass classification.

    Args:
        entry_text: Entry content to classify
        section_header: Top-level section header
        subsection_header: Immediate subsection header
        hierarchy: Full hierarchy path
        target_count: Target number of candidates (default 12)
        min_score: Minimum likelihood score threshold (default 0.1)

    Returns:
        List of (code, likelihood_score, reason) tuples, sorted by score descending.
        Typically 10-15 candidates for primary list, rest go to escape hatch.

    Example:
        >>> filter_candidate_codes(
        ...     "Best Paper Award, AMIA 2023",
        ...     "Teaching",
        ...     "Teaching Awards:",
        ...     ["Teaching", "Teaching Awards:"],
        ...     target_count=12
        ... )
        [
            ('H', 0.85, 'Award subsection override + award keywords'),
            ('K1', 0.25, 'Parent section Teaching'),
            ('K4', 0.20, 'Teaching keywords in text'),
            ...
        ]
    """
    # Check for cross-category override
    override_result = get_override_signals(section_header, subsection_header, hierarchy)
    override_parent = override_result[0] if override_result else None
    override_reason = override_result[1] if override_result else ""

    # Get all valid codes from valid_taxonomy_codes module
    # For now, use a simplified list (will import properly later)
    all_codes = [
        'A1', 'A2', 'A3',
        'B1', 'B2',
        'C1', 'C2', 'C3',
        'D1', 'D2',
        'E', 'F', 'G', 'H', 'I', 'J',
        'K1', 'K2', 'K3', 'K4',
        'L',
        'M1', 'M2', 'M2A', 'M2B', 'M2C', 'M2D',
        'N1', 'N3', 'N4',
        'O', 'P',
        'Q1', 'Q2', 'Q3', 'Q4',
        'R1', 'R2',
        'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'S11', 'S12',
        'T'
    ]

    # Score all candidates
    scored_candidates = []
    for code in all_codes:
        score = calculate_candidate_likelihood(
            code=code,
            entry_text=entry_text,
            section_header=section_header,
            subsection_header=subsection_header,
            hierarchy=hierarchy,
            override_parent=override_parent
        )

        # Build reason string
        reason_parts = []
        if override_parent and code[0] == override_parent:
            reason_parts.append(override_reason)
        if score >= 0.3:
            reason_parts.append("strong keyword match")
        elif score >= 0.15:
            reason_parts.append("moderate keyword match")
        elif score >= min_score:
            reason_parts.append("weak keyword match")

        reason = "; ".join(reason_parts) if reason_parts else "background candidate"

        if score >= min_score:
            scored_candidates.append((code, score, reason))

    # Sort by score descending
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    # Return top N candidates (typically 10-15)
    # If we have fewer than target_count above threshold, include more
    if len(scored_candidates) < target_count:
        # Add some background candidates to reach target
        remaining_codes = [c for c in all_codes if c not in [sc[0] for sc in scored_candidates]]
        for code in remaining_codes[:target_count - len(scored_candidates)]:
            scored_candidates.append((code, 0.05, "background candidate"))

    return scored_candidates[:target_count]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_confusion_guidance(primary_code: str) -> str:
    """
    Get confusion disambiguation guidance for a primary code.

    Extracts from confusion_matrix to help LLM distinguish between
    commonly confused categories.

    Args:
        primary_code: Primary taxonomy code (e.g., 'S', 'H', 'K')

    Returns:
        Formatted guidance string for LLM prompt
    """
    # Map code to confusion matrix key
    code_to_key = {
        'S': 'bibliography',
        'H': 'honors',
        'K': 'educational_contributions',
        'N': 'mentoring',
        'M': 'research_overview',
        'R': 'invitations_to_speak',
        'Q': 'extramural_professional_activities',
        'O': 'institutional_leadership',
        'D': 'professional_positions',
        'B': 'education_and_training',
        'C': 'postdoctoral_training',
        'I': 'professional_organizations',
        'P': 'clinical_practice'
    }

    key = code_to_key.get(primary_code)
    if not key or key not in SECTION_CONFUSION_MATRIX:
        return ""

    section_info = SECTION_CONFUSION_MATRIX[key]

    guidance_parts = []

    # Add core principles
    if 'core_principles' in section_info:
        guidance_parts.append("Core Principles:")
        for principle in section_info['core_principles'][:3]:  # Top 3
            guidance_parts.append(f"  - {principle}")

    # Add decision order
    if 'decision_order' in section_info:
        guidance_parts.append("\nDecision Order:")
        for step in section_info['decision_order'][:4]:  # Top 4 steps
            guidance_parts.append(f"  {step}")

    return "\n".join(guidance_parts)


def format_candidates_for_prompt(
    candidates: List[Tuple[str, float, str]],
    include_descriptions: bool = True
) -> str:
    """
    Format candidate codes for LLM prompt.

    Args:
        candidates: List of (code, score, reason) tuples
        include_descriptions: Whether to include full descriptions

    Returns:
        Formatted string for prompt
    """
    lines = []
    lines.append("PRIMARY CANDIDATE CODES (most likely):")
    lines.append("")

    # Get code descriptions (would import from taxonomy reference)
    # For now, use simplified descriptions
    code_descriptions = {
        'S1': 'Peer-Reviewed Original Research Articles',
        'S2': 'Reviews & Editorials',
        'S3': 'Books',
        'S4': 'Book Chapters',
        'S5': 'Published Case Reports',
        'S6': 'Published Letters, Commentaries',
        'S7': 'In Review / Submitted / In Preparation',
        'S8': 'Published Meeting Abstracts / Posters',
        'S9': 'Other Media (Podcasts, Blogs, Videos)',
        'S11': 'Software / Code Releases',
        'S12': 'Datasets',
        'H': 'Honors & Awards',
        'K1': 'Group Teaching (Courses, Lectures)',
        'K2': 'Curriculum Development',
        'K3': 'Clinical Teaching',
        'K4': 'Other Educational Contributions',
        'N1': 'Current Mentees',
        'N3': 'Past Mentees',
        'N4': 'Mentoring Philosophy/Statement',
        'M1': 'Active Research Projects',
        'M2': 'Research Support (Grants)',
        'M2A': 'Current Research Support',
        'M2B': 'Completed Research Support',
        'M2C': 'Pending Research Support',
        'M2D': 'Patents & Innovations',
        'R1': 'Invited Keynotes, Named Lectures',
        'R2': 'Grand Rounds, Visiting Professorships',
        'Q1': 'Leadership in External Organizations',
        'Q2': 'Grant Reviewing',
        'Q3': 'Editorial / Review Service',
        'Q4': 'Other Professional Service',
        'O': 'Institutional Leadership',
        'D1': 'Current Academic Appointments',
        'D2': 'Previous Academic Appointments',
        'B1': 'Undergraduate Education',
        'B2': 'Graduate Education (MD, PhD, etc.)',
        'C1': 'Postdoctoral Research Positions',
        'C2': 'Residency Training',
        'C3': 'Fellowship Training'
    }

    for i, (code, score, reason) in enumerate(candidates[:12], 1):
        desc = code_descriptions.get(code, 'Other')
        if include_descriptions:
            lines.append(f"{i}. {code}: {desc}")
            lines.append(f"   Likelihood: {score:.2f} - {reason}")
        else:
            lines.append(f"{i}. {code}: {desc} (score: {score:.2f})")

    return "\n".join(lines)
