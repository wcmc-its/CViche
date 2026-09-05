"""
Hierarchy-based override logic for taxonomy mapping.

Handles cases where CV section headers should map to different parent codes
than the top-level section suggests (cross-category subsections).

Examples:
- "Teaching Awards" under "Teaching" → H (Honors), not K (Teaching)
- "Ph.D. Committees" under "Teaching" → N (Mentoring), not K (Teaching)
"""

import re


def detect_cross_category_override(
    section_header: str,
    subsection_header: str,
    hierarchy: list[str],
    parent_code: str
) -> tuple[str, str, str] | None:
    """
    Detect if a subsection should map to a different parent category than expected.

    Args:
        section_header: Top-level section header (e.g., "Teaching")
        subsection_header: Immediate subsection header (e.g., "Teaching Awards:")
        hierarchy: Full hierarchy list
        parent_code: Parent code from Pass 1

    Returns:
        Tuple of (override_parent_code, override_parent_label, reason) if override detected,
        None otherwise.

    Examples:
        detect_cross_category_override("Teaching", "Teaching Awards:", [...], "K")
        → ("H", "Honors & Awards", "Award subsection should map to H regardless of parent section")
    """

    # Normalize headers for matching
    subsection_lower = subsection_header.lower().strip()
    section_lower = section_header.lower().strip()
    hierarchy_str = " > ".join(hierarchy).lower()

    # RULE 1: Awards subsections → H (Honors & Awards)
    # Pattern: "Awards", "Teaching Awards", "Research Awards", "Service Awards", etc.
    award_patterns = [
        r'\bawards?\b',
        r'\bhonors?\b',
        r'\bprizes?\b',
        r'\brecognition\b',
        r'\baccolades?\b'
    ]

    if any(re.search(pattern, subsection_lower) for pattern in award_patterns):
        # Exception: Don't override if it's clearly a grant/funding award (M2)
        funding_keywords = ['grant', 'funding', 'pi', 'co-i', 'r01', 'k23', 'award amount']
        if not any(keyword in subsection_lower for keyword in funding_keywords):
            return (
                "H",
                "Honors & Awards",
                f"Award subsection detected: '{subsection_header}' → H (regardless of parent '{parent_code}')"
            )

    # RULE 2: Committee subsections with named individuals → N (Mentoring & Advising)
    # Pattern: "Ph.D. Committees", "M.A. Committees", "Dissertation Committees", etc.
    committee_patterns = [
        r'\bcommittees?\b',
        r'\bph\.?d\.?\s+committee',
        r'\bm\.?a\.?\s+committee',
        r'\bdissertation\s+committee',
        r'\bthesis\s+committee',
        r'\badvisory\s+committee',
        r'\bdoctoral\s+committee'
    ]

    if any(re.search(pattern, subsection_lower) for pattern in committee_patterns):
        # Only override if this looks like formal mentoring (named individuals)
        # Check for patterns suggesting named individuals in hierarchy context
        return (
            "N",
            "Mentoring & Advising",
            f"Committee subsection detected: '{subsection_header}' → N (formal advising, not K teaching)"
        )

    # RULE 3: Student mentoring with named mentees → N (Mentoring & Advising)
    # Pattern: "Graduate Student Mentorship", "Postdoc Mentoring", etc.
    mentoring_patterns = [
        r'\bmentoring\b',
        r'\bmentor(ship)?\b',
        r'\badvis(ing|ory|or)\b',
        r'\bsupervis(ing|ion|or)\b'
    ]

    # Only override K → N, not other parent codes
    if parent_code == "K" and any(re.search(pattern, subsection_lower) for pattern in mentoring_patterns):
        # Additional check: if subsection contains "student" or "postdoc", likely formal mentoring
        formal_mentee_keywords = ['student', 'postdoc', 'trainee', 'fellow', 'mentee']
        if any(keyword in subsection_lower for keyword in formal_mentee_keywords):
            return (
                "N",
                "Mentoring & Advising",
                f"Formal mentoring subsection detected: '{subsection_header}' → N (not K)"
            )

    # RULE 4: Teaching Publications → S (Bibliography)
    # Pattern: "Teaching Publications", "Publications on Teaching", etc.
    # These are scholarly works ABOUT teaching, not teaching activities
    publication_patterns = [
        r'\bpublications?\b',
        r'\bpublished\b',
        r'\bscholarship\b',
        r'\bwriting\b',
        r'\barticles?\b'
    ]

    # Only override K → S (Teaching publications should be Bibliography)
    if parent_code == "K" and any(re.search(pattern, subsection_lower) for pattern in publication_patterns):
        # Additional check: if subsection contains "publication" or "published", it's scholarly output
        if 'publication' in subsection_lower or 'published' in subsection_lower:
            return (
                "S",
                "Bibliography",
                f"Publication subsection detected: '{subsection_header}' → S (scholarly output, not K teaching)"
            )

    # RULE 5: Courses taught → K (Teaching)
    # This is more of a confirmation rule - courses under "Teaching" should stay K
    # But courses under "Education" should NOT become K
    course_patterns = [
        r'\bcourses?\s+taught\b',
        r'\bteaching\s+responsibilities\b',
        r'\bclasses\s+taught\b'
    ]

    # No override needed - this is to ensure courses stay in K when appropriate

    # RULE 6: Publications/Bibliography subsections → G (Publications)
    # Pattern: "Publications", "Books", "Articles", "Book Chapters", "Journal Articles", etc.
    # These should map to G regardless of parent section
    publications_patterns = [
        r'\bpublications?\b',
        r'\bbooks?\b(?!\s+review)',  # "Books" but not "Book Reviews"
        r'\bmonographs?\b',
        r'\bjournal\s+articles?\b',
        r'\bpeer[- ]?reviewed\b',
        r'\brefereed\b',
        r'\bbook\s+chapters?\b',
        r'\barticles?\s+in\b',
        r'\bbibliography\b',
        r'\bwritten\s+works?\b',
        r'\bscholarly\s+works?\b'
    ]

    # Override to G if it looks like publications (and not already G)
    if parent_code != "G" and any(re.search(pattern, subsection_lower) for pattern in publications_patterns):
        # Exception: Don't override if it's clearly editorship or reviews
        editorial_keywords = ['editor', 'editorial', 'review board', 'reviewer']
        if not any(keyword in subsection_lower for keyword in editorial_keywords):
            return (
                "G",
                "Publications",
                f"Publications subsection detected: '{subsection_header}' → G (regardless of parent '{parent_code}')"
            )

    # RULE 7: Grants/Funding subsections → E (Grant Support)
    # Pattern: "Grants", "Funding", "Research Grants", "Training Grants", etc.
    grants_patterns = [
        r'\bgrants?\b',
        r'\bfunding\b',
        r'\bsponsored\s+(research|projects?)\b',
        r'\bexternal\s+(sources?|funding)\b',
        r'\bresearch\s+support\b',
        r'\btraining\s+grants?\b',
        r'\bfunded\s+(research|projects?)\b'
    ]

    # Override to E if it looks like grants (and not already E)
    if parent_code != "E" and any(re.search(pattern, subsection_lower) for pattern in grants_patterns):
        return (
            "E",
            "Grant Support",
            f"Grant subsection detected: '{subsection_header}' → E (regardless of parent '{parent_code}')"
        )

    # RULE 8: Editorial/Review Board subsections → I (Editorial Activities)
    # Pattern: "Editorships", "Editorial Board", "Journal Reviewer", "Review Boards", etc.
    editorial_patterns = [
        r'\beditor(ship)?s?\b',
        r'\beditorial\s+board\b',
        r'\breview\s+board\b',
        r'\bjournal\s+review(er)?\b',
        r'\breviewer\s+experience\b',
        r'\bpeer\s+review(er)?\b',
        r'\bad[- ]?hoc\s+review(er)?\b'
    ]

    # Override to I if it looks like editorial work (and not already I)
    if parent_code != "I" and any(re.search(pattern, subsection_lower) for pattern in editorial_patterns):
        return (
            "I",
            "Editorial Activities",
            f"Editorial subsection detected: '{subsection_header}' → I (regardless of parent '{parent_code}')"
        )

    # RULE 9: Presentations/Talks subsections → R (Invitations to Speak)
    # Pattern: "Presentations", "Invited Talks", "Conference Presentations", etc.
    presentation_patterns = [
        r'\bpresentations?\b',
        r'\binvited\s+(talk|lecture|presentation|speaker|address)\b',
        r'\btalks?\b',
        r'\blectures?\s+given\b',
        r'\bconference\s+presentations?\b',
        r'\bspeaking\s+engagements?\b',
        r'\bkeynote\b'
    ]

    # Override to R if it looks like presentations (and not already R)
    if parent_code != "R" and any(re.search(pattern, subsection_lower) for pattern in presentation_patterns):
        return (
            "R",
            "Invitations to Speak/Present",
            f"Presentations subsection detected: '{subsection_header}' → R (regardless of parent '{parent_code}')"
        )

    # RULE 10: Contributed papers/posters at conferences → Q (Abstracts, Posters, Exhibits)
    # Pattern: "Contributed Papers", "Posters", "Abstracts", etc.
    poster_patterns = [
        r'\bcontributed\s+(papers?|presentations?)\b',
        r'\bposters?\b',
        r'\babstracts?\b',
        r'\bexhibits?\b'
    ]

    # Override to Q if it looks like posters/abstracts (and not already Q)
    if parent_code != "Q" and any(re.search(pattern, subsection_lower) for pattern in poster_patterns):
        return (
            "Q",
            "Abstracts, Posters, Exhibits",
            f"Posters/abstracts subsection detected: '{subsection_header}' → Q (regardless of parent '{parent_code}')"
        )

    # No override detected
    return None


def get_override_examples(override_parent_code: str) -> str:
    """
    Get examples for the override parent category to help LLM understand the context.

    Args:
        override_parent_code: The parent code to show examples for

    Returns:
        String with examples and guidance for the override category
    """

    examples = {
        "H": """
OVERRIDE CONTEXT: This subsection contains AWARDS/HONORS, not teaching activities.

IMPORTANT: Awards are categorized by their nature (recognition), NOT by subject area.
- Teaching Award → H (Honors & Awards), NOT K (Teaching)
- Research Award → H (Honors & Awards), NOT M (Research)
- Service Award → H (Honors & Awards), NOT Q (Service)

KEY INDICATORS for H (Honors):
- Text contains "Award", "Prize", "Recognition", "Honor"
- One-time recognition event, not ongoing role
- No grant mechanics (PI, funding amount, project period)
- Appears in awards/honors section of CV

EXCEPTION: If text includes grant mechanics (PI, Co-I, funding amount, R01 number),
it may be M2 (Research Funding) even if called an "award".
""",
        "N": """
OVERRIDE CONTEXT: This subsection contains FORMAL MENTORING, not teaching.

IMPORTANT: Distinguish formal mentoring (N) from informal teaching (K):
- K (Teaching): Classroom instruction, courses, lectures, precepting
- N (Mentoring): Named individuals with advisor/committee role and outcomes

KEY INDICATORS for N (Mentoring):
- Named individuals (students, postdocs, fellows)
- Formal advisor/committee member role
- Dates and outcomes (graduation, current position)
- Ph.D./M.A./dissertation committees
- Thesis/dissertation advising

EXCEPTION: If entries are course rosters or teaching assistants without individual
outcomes, they should stay in K (Teaching).
"""
    }

    return examples.get(override_parent_code, "")


def apply_hierarchy_overrides(
    section_header: str,
    subsection_header: str,
    section_entries: list[dict],
    parent_code: str,
    parent_label: str
) -> tuple[str, str, str | None]:
    """
    Apply hierarchy-based overrides to determine the correct parent code for a section.

    Args:
        section_header: Top-level section header
        subsection_header: Immediate subsection header
        section_entries: List of entries in this section
        parent_code: Parent code from Pass 1
        parent_label: Parent label from Pass 1

    Returns:
        Tuple of (final_parent_code, final_parent_label, override_reason)
        If no override, returns (parent_code, parent_label, None)
    """

    # Get first entry's hierarchy for context
    if not section_entries:
        return parent_code, parent_label, None

    first_entry = section_entries[0]
    hierarchy = first_entry.get('hierarchy', [])

    # Check for cross-category override
    override_result = detect_cross_category_override(
        section_header=section_header,
        subsection_header=subsection_header,
        hierarchy=hierarchy,
        parent_code=parent_code
    )

    if override_result:
        override_code, override_label, reason = override_result
        return override_code, override_label, reason

    # No override - use Pass 1 result
    return parent_code, parent_label, None
