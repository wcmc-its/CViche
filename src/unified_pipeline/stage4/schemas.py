"""Field schemas, descriptions and taxonomy labels for stage 4 extraction.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
public name here. The built-in dicts are the fallback; the versioned config
file takes precedence when present (see `load_field_schemas_from_config`).

`_LOADED_SCHEMAS` is a mutable module-level cache and is deliberately NOT
re-exported by the facade: a re-export would be a stale second binding that
never sees the lazy initialisation (the #496 split-state lesson). Reach it as
`unified_pipeline.stage4.schemas._LOADED_SCHEMAS` or not at all.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional


# ============================================================================
# Field Schema Configuration
# ============================================================================

def load_field_schemas_from_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load field schemas from versioned config file.

    Only includes fields where "extract": true in the config.
    This allows us to define optional fields in config but not send them to LLM.

    Args:
        config_path: Path to config file. If None, uses default location.

    Returns:
        Dict mapping taxonomy codes to field lists (only extractable fields)
    """
    if config_path is None:
        config_path = FIELD_SCHEMA_CONFIG_PATH

    if Path(config_path).exists():
        with open(config_path, "r") as f:
            config = json.load(f)

        # Convert config format to simple {code: {"fields": [...], "required": []}} format
        # Only include fields where extract=true
        schemas = {}
        total_fields = 0
        extracted_fields = 0

        for code, schema_data in config.get("schemas", {}).items():
            # Skip non-dict entries (e.g., comment strings like "__NOTE_...")
            if not isinstance(schema_data, dict):
                continue
            fields_config = schema_data.get("fields", {})
            # Filter to only fields with extract=true
            extractable_fields = [
                field_name for field_name, field_info in fields_config.items()
                if field_info.get("extract", True)  # Default to True for backwards compatibility
            ]

            total_fields += len(fields_config)
            extracted_fields += len(extractable_fields)

            schemas[code] = {
                "fields": extractable_fields,
                "required": [],
                "description": schema_data.get("description", ""),
                "wcm_section": schema_data.get("wcm_section"),
                "extraction_mode": schema_data.get("extraction_mode", "minimal")
            }

        version = config.get('version', '?')
        print(f"  Loaded field schemas v{version} ({len(schemas)} codes, {extracted_fields}/{total_fields} fields active)")
        return schemas
    else:
        print(f"  Warning: Config file not found at {config_path}, using built-in schemas")
        return None

# Schema version info
FIELD_SCHEMA_VERSION = "1.1"
FIELD_SCHEMA_CONFIG_PATH = Path(__file__).parent / "config" / "field_schemas_v1.1.json"

# ============================================================================
# Field Extraction Schemas by Taxonomy Code
# These are the built-in defaults; config file takes precedence if available
# ============================================================================

FIELD_SCHEMAS = {
    # -------------------------------------------------------------------------
    # Personal/Contact Information (A)
    # Note: "narrative" field captures any additional prose context
    # -------------------------------------------------------------------------
    "A": {  # Personal/Contact Information (parent)
        "fields": ["name", "title", "degrees", "department", "institution", "email", "phone", "address", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Education (B1, B2)
    # -------------------------------------------------------------------------
    "B1": {  # Academic Degrees
        "fields": ["degree", "institution", "discipline", "year", "thesis_title", "advisor", "narrative"],
        "required": []
    },
    "B2": {  # Other Educational Experiences (certificates, training programs, compliance training)
        "fields": ["program_type", "program_name", "institution", "year", "duration", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Postdoctoral Training (C)
    # -------------------------------------------------------------------------
    "C": {  # Postdoctoral Training (residency, fellowship, postdoc, graduate assistantship)
        "fields": ["training_type", "specialty", "institution", "department", "mentor", "start_date", "end_date", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Positions & Employment (D1, D2, D3)
    # -------------------------------------------------------------------------
    "D1": {  # Academic Appointments (faculty, endowed chairs, emeritus, museum appointments)
        "fields": ["title", "institution", "department", "start_date", "end_date", "track", "tenure_status", "narrative"],
        "required": []
    },
    "D2": {  # Hospital Appointments (attending, consulting, hospitalist)
        "fields": ["title", "institution", "department", "start_date", "end_date", "appointment_type", "narrative"],
        "required": []
    },
    "D3": {  # Other Professional Positions (non-faculty research staff, industry, consulting)
        "fields": ["title", "organization", "department", "start_date", "end_date", "role_type", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Employment Status (E)
    # -------------------------------------------------------------------------
    "E": {  # Employment Status
        "fields": ["status", "fte_percentage", "effective_date", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Credentials (F1, F2)
    # -------------------------------------------------------------------------
    "F1": {  # Licensure
        "fields": ["license_type", "state_country", "license_number", "issue_date", "expiration_date", "status", "narrative"],
        "required": []
    },
    "F2": {  # Board Certification
        "fields": ["specialty", "certifying_board", "year_certified", "recertification_date", "status", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Affiliations & Languages (G)
    # -------------------------------------------------------------------------
    "G": {  # Institutional & Hospital Affiliations
        "fields": ["affiliation_type", "organization", "department", "start_date", "end_date", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Honors & Awards (H)
    # -------------------------------------------------------------------------
    "H": {  # Honors & Awards
        "fields": ["award_name", "granting_body", "date", "amount", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Professional Organizations (I)
    # -------------------------------------------------------------------------
    "I": {  # Professional Organizations & Society Memberships
        "fields": ["organization", "membership_type", "start_date", "end_date", "fellowship_designation", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Percent Effort (J)
    # -------------------------------------------------------------------------
    "J": {  # Percent Effort & Institutional Responsibilities
        "fields": ["clinical_percent", "research_percent", "teaching_percent", "admin_percent", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Teaching & Educational Contributions (K1-K5)
    # -------------------------------------------------------------------------
    "K1": {  # Didactic Teaching
        "fields": ["course_code", "course_title", "institution", "department", "role", "level", "start_date", "end_date", "enrollment", "hours_per_year", "narrative"],
        "required": []
    },
    "K2": {  # Research Mentoring & Clinical Teaching
        "fields": ["teaching_role", "institution", "setting", "learner_level", "start_date", "end_date", "hours_per_week", "description", "narrative"],
        "required": []
    },
    "K3": {  # Educational Program Leadership
        "fields": ["program_name", "role", "institution", "start_date", "end_date", "scope", "description", "narrative"],
        "required": []
    },
    "K4": {  # CME & Professional Education
        "fields": ["activity_title", "institution", "role", "date", "cme_credits", "target_audience", "description", "narrative"],
        "required": []
    },
    "K5": {  # Community Education or Patient Outreach
        "fields": ["activity_title", "audience", "location", "date", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Clinical Activities (L1, L2, L3)
    # -------------------------------------------------------------------------
    "L1": {  # Clinical Practice
        "fields": ["clinical_role", "institution", "service_setting", "fte_clinical", "sessions_per_week", "start_date", "end_date", "description", "narrative"],
        "required": []
    },
    "L2": {  # Clinical Innovations (QI projects)
        "fields": ["project_name", "institution", "role", "start_date", "end_date", "outcome", "description", "narrative"],
        "required": []
    },
    "L3": {  # Clinical Leadership
        "fields": ["leadership_role", "institution", "unit_program", "start_date", "end_date", "scope", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Research & Scholarship (M1, M2, M3, M4)
    # -------------------------------------------------------------------------
    "M1": {  # Research Activities - narrative IS the primary content here
        "fields": ["research_area", "description", "institution", "start_date", "end_date", "narrative"],
        "required": []
    },
    "M2": {  # Research Support (generic grant)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "annual_funding", "percent_effort", "narrative"],
        "required": []
    },
    "M2A": {  # Current Research Funding (active grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "annual_funding", "percent_effort", "narrative"],
        "required": []
    },
    "M2B": {  # Past Research Funding (completed grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "percent_effort", "narrative"],
        "required": []
    },
    "M2C": {  # Pending Research Funding (submitted grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "total_funding_requested", "submission_date", "narrative"],
        "required": []
    },
    "M2D": {  # Patents & Innovations (formerly M3)
        "fields": ["patent_number", "title", "inventors", "filing_date", "issue_date", "status", "assignee", "narrative"],
        "required": []
    },
    # NOTE: M4 clinical trial codes have been removed. Clinical trials should now be
    # classified as M2A (active/current), M2B (completed/past), or M2C (pending) based
    # on their status. The M2A/M2B/M2C schemas support clinical trial fields via narrative.

    # -------------------------------------------------------------------------
    # Mentoring (N1, N2, N3, N4)
    # -------------------------------------------------------------------------
    "N1": {  # Leadership and Mentoring in Programs
        "fields": ["program_name", "role", "institution", "start_date", "end_date", "number_trainees", "narrative"],
        "required": []
    },
    "N2": {  # Institutional Training Grants and Mentored Trainee Grants
        "fields": ["grant_number", "grant_title", "role", "agency", "start_date", "end_date", "trainees_supported", "narrative"],
        "required": []
    },
    "N3": {  # Mentees (generic)
        "fields": ["mentee_name", "mentee_level", "start_date", "end_date", "thesis_title", "current_position", "narrative"],
        "required": []
    },
    "N3A": {  # Current Mentees
        "fields": ["mentee_name", "mentee_level", "program", "start_date", "expected_completion", "research_focus", "narrative"],
        "required": []
    },
    "N3B": {  # Past Mentees
        "fields": ["mentee_name", "mentee_level", "program", "start_date", "end_date", "thesis_title", "current_position", "narrative"],
        "required": []
    },
    "N4": {  # Scholarly Outputs Resulting From Mentorship
        "fields": ["output_type", "mentee_name", "title", "date", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Institutional Leadership (O)
    # -------------------------------------------------------------------------
    "O": {  # Institutional Leadership Activities
        "fields": ["leadership_role", "institution", "division_department", "start_date", "end_date", "budget_authority", "personnel_supervised", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Institutional Administrative Activities (P)
    # -------------------------------------------------------------------------
    "P": {  # Institutional Administrative Activities (committee membership)
        "fields": ["committee_name", "role", "institution", "start_date", "end_date", "description", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Professional Service / Extramural (Q1-Q4)
    # -------------------------------------------------------------------------
    "Q1": {  # Leadership in Extramural Organizations
        "fields": ["role", "organization", "start_date", "end_date", "scope", "narrative"],
        "required": []
    },
    "Q2": {  # Service on External Boards/Committees
        "fields": ["committee_name", "role", "organization", "start_date", "end_date", "narrative"],
        "required": []
    },
    "Q3": {  # Grant Reviewing / Study Sections
        "fields": ["panel_name", "agency", "role", "start_date", "end_date", "review_type", "narrative"],
        "required": []
    },
    "Q4": {  # Editorial Activities (generic)
        "fields": ["role", "journal_name", "start_date", "end_date", "narrative"],
        "required": []
    },
    "Q4A": {  # Editor/Co-Editor
        "fields": ["role", "journal_name", "publisher", "start_date", "end_date", "narrative"],
        "required": []
    },
    "Q4B": {  # Associate/Section Editor
        "fields": ["role", "journal_name", "section", "start_date", "end_date", "narrative"],
        "required": []
    },
    "Q4C": {  # Editorial Board Membership
        "fields": ["journal_name", "start_date", "end_date", "narrative"],
        "required": []
    },
    "Q4D": {  # Journal Reviewing / Ad hoc Reviewing
        "fields": ["journal_name", "year", "number_reviews", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Invited Presentations (R)
    # -------------------------------------------------------------------------
    "R": {  # Invitations to Speak/Present
        "fields": ["title", "event_name", "location", "date", "presentation_type", "host_organization", "authors", "target_name", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Publications (S0-S9)
    # Note: narrative captures meta-statements like "co-authored with my mentee"
    # -------------------------------------------------------------------------
    "S0": {  # Researcher Profile & Bibliometric Summary
        "fields": ["orcid", "google_scholar_url", "scopus_id", "researchgate_url", "h_index", "total_citations", "publication_count", "narrative"],
        "required": []
    },
    "S1": {  # Peer-Reviewed Research Articles
        "fields": ["authors", "year", "title", "journal", "volume", "issue", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
        "required": []
    },
    "S2": {  # Reviews and Editorials
        "fields": ["authors", "year", "title", "journal", "volume", "issue", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
        "required": []
    },
    "S3": {  # Books
        "fields": ["authors", "editors", "year", "title", "publisher", "edition", "isbn", "target_name", "narrative"],
        "required": []
    },
    "S4": {  # Book Chapters
        "fields": ["authors", "year", "chapter_title", "book_title", "editors", "publisher", "pages", "doi", "target_name", "narrative"],
        "required": []
    },
    "S5": {  # Non-peer-reviewed Research Publications
        "fields": ["authors", "year", "title", "publication_venue", "report_number", "url", "target_name", "narrative"],
        "required": []
    },
    "S6": {  # Case Reports
        "fields": ["authors", "year", "title", "journal", "volume", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
        "required": []
    },
    "S7": {  # In Review / Submitted / In Preparation
        "fields": ["authors", "year", "title", "status", "target_journal", "target_name", "narrative"],
        "required": []
    },
    "S8": {  # Abstracts & Conference Proceedings
        "fields": ["authors", "year", "title", "conference_name", "location", "abstract_number", "doi", "target_name", "narrative"],
        "required": []
    },
    "S9": {  # Other Media (Podcasts, Blogs, Videos)
        "fields": ["authors", "year", "title", "media_type", "venue", "url", "target_name", "narrative"],
        "required": []
    },

    # -------------------------------------------------------------------------
    # Appendix/Other (T)
    # -------------------------------------------------------------------------
    "T": {  # Appendix/Other (structural elements, references, misc)
        "fields": ["content_type", "description", "narrative"],
        "required": []
    },
}

# ============================================================================
# Field Descriptions for LLM Prompt Guidance
# Shared source of truth used by both primary extraction and recovery pass.
# Each entry maps a taxonomy code to a dict of field_name -> description string.
# ============================================================================
FIELD_DESCRIPTIONS = {
    "K1": {
        "course_title": "Name of the course taught",
        "role": "Your role (e.g., 'PBL Tutor', 'Course Director', 'Lecturer')",
        "institution": "Where the teaching occurred",
        "start_date": "When teaching started (year)",
        "end_date": "When teaching ended (year or 'present')",
    },
    "K2": {
        "teaching_role": "Type of teaching (e.g., 'Clinical Preceptor', 'Mentor')",
        "setting": "Where teaching occurs (e.g., 'Inpatient', 'Outpatient', 'OR')",
        "learner_level": "Who you teach (e.g., 'Medical Students', 'Residents', 'Fellows')",
        "institution": "Institution name",
        "start_date": "When teaching started",
        "end_date": "When teaching ended or 'present'",
    },
    "K3": {
        "program_name": "Name of educational program led",
        "role": "Leadership role (e.g., 'Director', 'Co-Director', 'Chair')",
        "institution": "Institution name",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "K4": {
        "activity_title": "Name of course/workshop/presentation ONLY - NOT the person's role. If text reads '[Role] of [Title]', the title is [Title]",
        "role": "What the person did (e.g., 'Creator and Presenter', 'Instructor', 'Course Director') - ONLY the role words",
        "institution": "Where the activity took place",
        "date": "When it occurred",
    },
    "K5": {
        "activity_title": "Name of the outreach activity or presentation",
        "audience": "Who the activity was for (e.g., 'patients', 'community members')",
        "location": "Where it occurred",
        "date": "When it occurred",
    },
    "I": {
        "organization": "Name of the professional society or organization ONLY - NOT the membership level",
        "membership_type": "Membership designation or level (e.g., 'Fellow', 'Member', 'Diplomat', 'Associate Member'). If text reads 'Fellow | Organization' or 'Fellow, Organization', extract 'Fellow' here",
        "start_date": "When membership began",
        "end_date": "When membership ended or 'present'",
    },
    "P": {
        "committee_name": "Name of the committee or administrative body ONLY - NOT your role on it",
        "role": "Your role (e.g., 'Chair', 'Member', 'Vice Chair') - ONLY the role word(s)",
        "institution": "Institution name",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "O": {
        "leadership_role": "Title/position (e.g., 'Division Chief', 'Vice Chair')",
        "institution": "Institution name",
        "division_department": "Department or division",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q1": {
        "role": "Your role (e.g., 'Chair', 'Member', 'Officer', 'Secretary')",
        "organization": "Name of the extramural organization",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q2": {
        "committee_name": "Name of the specific committee ONLY - NOT the parent organization and NOT your role",
        "role": "ONLY the role word(s) (e.g., 'Member', 'Chair', 'Reviewer') - do NOT include committee name here",
        "organization": "Parent organization that the committee belongs to - NOT the committee name",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q3": {
        "agency": "Funding agency or organization (e.g., 'NIH', 'NSF')",
        "role": "Your role (e.g., 'Reviewer', 'Study Section Member', 'Chair')",
        "panel_name": "Name of the review panel or study section",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q4A": {
        "journal_name": "Name of the journal",
        "role": "Editorial role (e.g., 'Editor', 'Co-Editor', 'Editor-in-Chief')",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q4B": {
        "journal_name": "Name of the journal",
        "role": "Editorial role (e.g., 'Associate Editor', 'Section Editor')",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q4C": {
        "journal_name": "Name of the journal or editorial board",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "Q4D": {
        "journal_name": "Name of the journal reviewed for",
        "start_date": "Start year",
        "end_date": "End year or 'present'",
    },
    "R": {
        "title": "Title of the talk or presentation",
        "location": "Institution or venue where presented",
        "date": "Date of the presentation",
        "event_name": "Name of the conference or event (if applicable)",
    },
    "H": {
        "award_name": "Name of the honor/award (e.g., 'Best Teacher Award', 'NIH Merit Award')",
        "granting_body": "Organization that gave the award (e.g., 'American Academy of Pediatrics', 'NIH')",
        "date": "Year received (look for 4-digit years in the text)",
    },
    "L1": {
        "clinical_role": "Type of clinical practice or role",
        "institution": "Where practice occurs",
        "start_date": "Start date",
        "end_date": "End date or 'present'",
    },
    "L3": {
        "leadership_role": "Leadership position (e.g., 'Medical Director', 'Site Chief')",
        "institution": "Institution name",
        "unit_program": "Program or unit led",
        "start_date": "Start date",
        "end_date": "End date or 'present'",
    },
    "M2A": {
        "grant_number": "Grant/award number (e.g., R01CA123456)",
        "title": "Scientific title of the research project (NOT a person's name, NOT FTE/effort)",
        "pi_name": "Name of the Principal Investigator (a PERSON'S NAME like 'John Smith')",
        "pi_role": "CV owner's role (e.g., 'PI', 'Co-PI', 'Co-Investigator', 'Consultant')",
        "agency": "Funding agency (e.g., 'NIH/NCI', 'NSF')",
        "start_date": "Funding period start",
        "end_date": "Funding period end",
        "total_funding": "Total award amount in dollars",
        "annual_funding": "Annual/yearly direct costs",
        "percent_effort": "FTE/effort percentage (convert '.08FTE' to '8%')",
    },
    "M2B": {
        "grant_number": "Grant/award number",
        "title": "Scientific title of the research project (NOT a person's name, NOT FTE)",
        "pi_name": "Name of the Principal Investigator (a PERSON'S NAME, NOT the project title)",
        "pi_role": "CV owner's role (e.g., 'PI', 'Co-Investigator', 'Consultant')",
        "agency": "Funding agency",
        "start_date": "Funding period start",
        "end_date": "Funding period end",
        "total_funding": "Total award amount",
        "percent_effort": "FTE/effort (extract '.08FTE' as '8%')",
    },
    "D3": {
        "title": "Job title or position",
        "organization": "Company/organization name with city and state",
        "start_date": "Employment start date (mm/yy format)",
        "end_date": "Employment end date (mm/yy format)",
    },
}

# Fallback for unlisted codes
DEFAULT_SCHEMA = {
    "fields": ["text", "date", "description"],
    "required": []
}

# Taxonomy code labels for prompt context
TAXONOMY_LABELS = {
    "A": "Personal/Contact Information",
    "B1": "Academic Degrees",
    "B2": "Other Educational Experiences",
    "C": "Postdoctoral Training",
    "D1": "Academic Appointments",
    "D2": "Hospital Appointments",
    "D3": "Other Professional Positions",
    "E": "Employment Status",
    "F1": "Licensure",
    "F2": "Board Certification",
    "G": "Institutional & Hospital Affiliations",
    "H": "Honors & Awards",
    "I": "Professional Organizations & Society Memberships",
    "J": "Percent Effort & Institutional Responsibilities",
    "K1": "Didactic Teaching",
    "K2": "Research Mentoring & Clinical Teaching",
    "K3": "Educational Program Leadership",
    "K4": "CME & Professional Education",
    "K5": "Community Education or Patient Outreach",
    "L1": "Clinical Practice",
    "L2": "Clinical Innovations",
    "L3": "Clinical Leadership",
    "M1": "Research Activities",
    "M2": "Research Support",
    "M2A": "Current Research Funding",
    "M2B": "Past Research Funding",
    "M2C": "Pending Research Funding",
    "M2D": "Patents & Innovations",
    # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C
    "N1": "Leadership and Mentoring in Programs",
    "N2": "Institutional Training Grants",
    "N3": "Mentees",
    "N3A": "Current Mentees",
    "N3B": "Past Mentees",
    "N4": "Scholarly Outputs From Mentorship",
    "O": "Institutional Leadership",
    "P": "Institutional Administrative Activities",
    "Q1": "Leadership in Extramural Organizations",
    "Q2": "Service on External Boards/Committees",
    "Q3": "Grant Reviewing / Study Sections",
    "Q4": "Editorial Activities",
    "Q4A": "Editor/Co-Editor",
    "Q4B": "Associate/Section Editor",
    "Q4C": "Editorial Board Membership",
    "Q4D": "Journal Reviewing",
    "R": "Invited Presentations",
    "S0": "Researcher Profile & Bibliometric Summary",
    "S1": "Peer-Reviewed Research Articles",
    "S2": "Reviews and Editorials",
    "S3": "Books",
    "S4": "Book Chapters",
    "S5": "Non-peer-reviewed Publications",
    "S6": "Case Reports",
    "S7": "In Review / Submitted / In Preparation",
    "S8": "Abstracts & Conference Proceedings",
    "S9": "Other Media",
    "T": "Appendix/Other",
}

def get_taxonomy_label(taxonomy_code: str) -> str:
    """Get human-readable label for a taxonomy code."""
    if taxonomy_code in TAXONOMY_LABELS:
        return TAXONOMY_LABELS[taxonomy_code]
    # Try parent code
    parent_code = taxonomy_code[0] if taxonomy_code else None
    if parent_code in TAXONOMY_LABELS:
        return TAXONOMY_LABELS[parent_code]
    return "Unknown"

# Global variable to hold loaded schemas (initialized lazily)
_LOADED_SCHEMAS: Optional[Dict[str, Any]] = None

def get_active_schemas() -> Dict[str, Any]:
    """
    Get the active field schemas, loading from config if available.

    Returns config-based schemas if available, otherwise built-in FIELD_SCHEMAS.
    """
    global _LOADED_SCHEMAS

    if _LOADED_SCHEMAS is None:
        # Try to load from config file
        _LOADED_SCHEMAS = load_field_schemas_from_config()

        if _LOADED_SCHEMAS is None:
            # Fall back to built-in schemas
            _LOADED_SCHEMAS = FIELD_SCHEMAS

    return _LOADED_SCHEMAS

def get_field_schema(taxonomy_code: str) -> Dict[str, Any]:
    """
    Get the field extraction schema for a taxonomy code.

    Prefers schemas from config file, falls back to built-in schemas.
    """
    schemas = get_active_schemas()

    # Try exact match first
    if taxonomy_code in schemas:
        return schemas[taxonomy_code]

    # Try parent code (e.g., S1 → S)
    parent_code = taxonomy_code[0] if taxonomy_code else None
    if parent_code in schemas:
        return schemas[parent_code]

    return DEFAULT_SCHEMA
