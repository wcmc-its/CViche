"""
Configuration for CV parsing - section mappings and constants
"""

# WCM Template Major Sections (in order)
WCM_SECTIONS = [
    "PERSONAL DATA",
    "EDUCATION",
    "POSTDOCTORAL TRAINING",
    "PROFESSIONAL POSITIONS & EMPLOYMENT",
    "EMPLOYMENT STATUS",
    "LICENSURE, BOARD CERTIFICATION",
    "INSTITUTIONAL/HOSPITAL AFFILIATION",
    "HONORS, AWARDS",
    "PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS",
    "PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES",
    "EDUCATIONAL CONTRIBUTIONS",
    "RESEARCH",
    "MENTORING",
    "INSTITUTIONAL LEADERSHIP ACTIVITIES",
    "INSTITUTIONAL ADMINISTRATIVE ACTIVITIES",
    "EXTRAMURAL PROFESSIONAL RESPONSIBILITIES",
    "INVITATIONS TO SPEAK/PRESENT",
    "BIBLIOGRAPHY"
]

# Common section name variations and aliases
SECTION_ALIASES = {
    "education": ["EDUCATION", "Academic Background", "Degrees", "Academic Degrees"],
    "employment": ["PROFESSIONAL POSITIONS & EMPLOYMENT", "Employment", "Positions", "Work Experience", "Academic Appointments"],
    "training": ["POSTDOCTORAL TRAINING", "Training", "Residency", "Fellowship", "Postdoctoral"],
    "honors": ["HONORS, AWARDS", "Honors", "Awards", "Recognition", "Honors and Awards"],
    "publications": ["BIBLIOGRAPHY", "Publications", "Selected Publications", "Peer-Reviewed Publications"],
    "presentations": ["INVITATIONS TO SPEAK/PRESENT", "Presentations", "Speaking Engagements", "Invited Talks"],
    "memberships": ["PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS", "Professional Memberships", "Societies", "Organizations"],
    "licensure": ["LICENSURE, BOARD CERTIFICATION", "Licensure", "Certification", "Board Certification"],
    "research": ["RESEARCH", "Research Experience", "Research Interests", "Research Activities"],
    "teaching": ["EDUCATIONAL CONTRIBUTIONS", "Teaching", "Teaching Experience", "Education Activities"],
    "mentoring": ["MENTORING", "Mentorship", "Student Advising", "Mentoring Activities"],
    "leadership": ["INSTITUTIONAL LEADERSHIP ACTIVITIES", "Leadership", "Leadership Roles", "Administrative Leadership"],
    "service": ["INSTITUTIONAL ADMINISTRATIVE ACTIVITIES", "Service", "Committee Service", "Administrative Service"],
    "extramural": ["EXTRAMURAL PROFESSIONAL RESPONSIBILITIES", "External Service", "Professional Service"],
}

# Section keywords for rule-based classification
SECTION_KEYWORDS = {
    "education": ["degree", "bachelor", "master", "phd", "doctorate", "university", "college", "graduated"],
    "employment": ["professor", "assistant", "associate", "director", "attending", "physician", "position"],
    "training": ["residency", "fellowship", "intern", "postdoctoral", "training program"],
    "honors": ["award", "honor", "prize", "recognition", "fellowship", "scholar"],
    "publications": ["published", "journal", "article", "paper", "author", "doi", "pmid"],
    "presentations": ["presentation", "speaker", "talk", "lecture", "symposium", "conference"],
    "memberships": ["member", "society", "association", "organization", "board member"],
    "licensure": ["license", "certification", "board certified", "state license"],
}

# LLM prompts for section classification
SECTION_CLASSIFICATION_PROMPT = """You are analyzing a section from a CV. Your task is to classify it into one of these standard WCM CV sections:

{section_list}

Given this text from a CV section:

{section_text}

Respond with ONLY the exact section name from the list above that best matches this content. If uncertain, respond with "UNKNOWN".
"""

# LLM prompts for entity extraction
ENTITY_EXTRACTION_PROMPT = """Extract structured information from this CV section.

Section type: {section_type}
Text: {section_text}

Return a JSON object with the extracted entities. Follow these guidelines:
- Extract dates in format: YYYY or MM/YYYY or MM/DD/YYYY
- Extract institution names, locations (city, state)
- Extract titles, positions, degrees
- Extract any relevant identifiers (DOI, PMID, license numbers)
- Preserve original text when uncertain

Return ONLY valid JSON, no additional text.
"""
