#!/usr/bin/env python3
"""
Stage 4: Intra-Entry Field Extraction (v2.0)

Extracts structured fields from Stage 3b classified entries.

Input: Stage 3b classified entries with taxonomy codes (S1, M2A, D1, K3, etc.)
Output: Entries with extracted structured fields (authors, dates, titles, etc.)

Changes in v2.0:
- Updated to read from stage_3b_classified_entries/ output
- Complete field schemas for all 60+ taxonomy codes
- Handles sub-codes (M2A/M2B/M2C, Q4A-D, N3A/N3B, etc.)
- Aligned with taxonomy v7 field definitions

Examples:
- S1 (Peer-reviewed Research): authors, year, title, journal, volume, pages, DOI, PMID
- M2A (Active Grants): grant_number, title, pi_role, agency, start_date, end_date, total_funding
- D1 (Academic Appointments): title, institution, department, start_date, end_date
- C (Training): program_type, institution, discipline, start_date, end_date, mentor
- H (Awards): award_name, granting_body, date, amount
"""

import sys
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm


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


def calculate_unextracted_content(original_text: str, extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate what content from the original text was not extracted into any field.

    Args:
        original_text: Original CV entry text
        extracted_fields: Dictionary of extracted field values

    Returns:
        Dictionary with:
        - unextracted_words: List of words from original not found in any extracted field
        - extraction_coverage_percent: Percentage of original words that were extracted
        - total_original_words: Total content words in original text
        - total_extracted_words: Total content words found in extracted fields
    """
    import re

    # Tokenize original text (alphanumeric words only, lowercase)
    def tokenize(text):
        if not text or not isinstance(text, str):
            return set()
        # Extract alphanumeric tokens (ignore pure numbers, keep words with numbers like "2023")
        tokens = re.findall(r'\b[a-z]+[a-z0-9]*\b', text.lower())
        # Filter out common stop words and very short tokens
        stop_words = {'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                     'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
                     'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                     'would', 'should', 'could', 'may', 'might', 'must', 'can', 'it', 'this',
                     'that', 'these', 'those', 'i', 'you', 'he', 'she', 'we', 'they', 'my',
                     'your', 'his', 'her', 'our', 'their'}
        return set(t for t in tokens if len(t) > 2 and t not in stop_words)

    # Get tokens from original text
    original_tokens = tokenize(original_text)

    if not original_tokens:
        return {
            "unextracted_words": [],
            "extraction_coverage_percent": 100.0,
            "total_original_words": 0,
            "total_extracted_words": 0
        }

    # Collect all tokens from extracted fields
    extracted_tokens = set()
    for field_name, field_value in extracted_fields.items():
        if field_value is not None:
            # Handle lists (e.g., years_taught)
            if isinstance(field_value, list):
                for item in field_value:
                    extracted_tokens.update(tokenize(str(item)))
            else:
                extracted_tokens.update(tokenize(str(field_value)))

    # Find unextracted words
    unextracted = original_tokens - extracted_tokens
    extracted_from_original = original_tokens & extracted_tokens

    # Calculate coverage
    coverage_percent = (len(extracted_from_original) / len(original_tokens) * 100) if original_tokens else 100.0

    return {
        "unextracted_words": sorted(list(unextracted)),
        "extraction_coverage_percent": round(coverage_percent, 1),
        "total_original_words": len(original_tokens),
        "total_extracted_words": len(extracted_from_original)
    }


# =============================================================================
# LLM-ASSISTED RECOVERY FOR MESSY TABLE STRUCTURES
# =============================================================================

def needs_llm_recovery(entry: Dict[str, Any], min_original_chars: int = 200, max_coverage: float = 30.0) -> bool:
    """
    Determine if an entry needs LLM-assisted recovery due to poor extraction.

    Triggers when:
    1. Original text is substantial (>200 chars)
    2. Extraction coverage is low (<30%)
    3. Entry has extractable patterns (dates, names, etc.)

    Also triggers, regardless of length floor or date/structure markers, when
    extraction produced nothing at all from substantive text (>=50 chars) --
    total-extraction loss on short entries (board certifications, languages,
    role lines) otherwise slips under the 200-char floor and vanishes from the
    output silently (#322).

    Args:
        entry: Entry with extraction_coverage information
        min_original_chars: Minimum original text length to consider
        max_coverage: Maximum coverage percentage to trigger recovery

    Returns:
        True if entry should be sent to LLM recovery
    """
    import re

    original_text = entry.get("text", "")
    coverage_info = entry.get("extraction_coverage", {})
    coverage_pct = coverage_info.get("extraction_coverage_percent", 100.0)

    # Total-extraction loss: no field got any value despite substantive text.
    # Sufficient signal by itself -- skip the length/date/structure checks.
    extracted_fields = entry.get("extracted_fields") or {}
    if len(original_text.strip()) >= 50 and not any(extracted_fields.values()):
        return True

    # Check basic conditions
    if len(original_text) < min_original_chars:
        return False
    if coverage_pct > max_coverage:
        return False

    # Check if there's extractable content (dates, structure indicators)
    has_dates = bool(re.search(r'\b(19|20)\d{2}\b', original_text))
    has_structure = bool(re.search(r'[\t|]|\n.*\n', original_text))  # Tabs, pipes, or multiple newlines

    return has_dates or has_structure


def attempt_llm_recovery(
    entries: List[Dict[str, Any]],
    model: str = None
) -> List[Dict[str, Any]]:
    """
    Attempt LLM-assisted recovery for entries with poor extraction coverage.

    Uses taxonomy context to help the LLM understand the expected data structure
    and parse messy table-like content.

    Args:
        entries: List of entries needing recovery (same taxonomy code)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument

    Returns:
        List of entries with recovered fields
    """
    if not entries:
        return entries

    # All entries should have the same taxonomy code
    taxonomy_code = entries[0].get("taxonomy_code", "UNKNOWN")
    taxonomy_label = get_taxonomy_label(taxonomy_code)
    schema = get_field_schema(taxonomy_code)

    # Combine all entry texts for context
    combined_text = "\n---ENTRY BOUNDARY---\n".join(
        entry.get("text", "") for entry in entries
    )

    # Build recovery prompt with taxonomy context
    prompt = f"""You are parsing a poorly formatted CV section. The text appears to have table-like structure where items and their attributes (like dates) may be misaligned or separated.

**Section Type**: {taxonomy_code} - {taxonomy_label}

**Expected Fields**: {', '.join(schema['fields'])}

**Field Descriptions**:
{_get_field_descriptions(taxonomy_code)}

**Raw Text to Parse**:
{combined_text}

**Instructions**:
1. This text likely contains multiple entries that should each have their own set of fields
2. Items and dates may be in separate columns or blocks - match them by position (1st item → 1st date, 2nd → 2nd, etc.)
3. Look for patterns like:
   - Newline-separated lists where items are in one section and dates in another
   - Tab or space-aligned columns
   - Parenthetical information (role, dates, etc.)
4. Extract ALL entries you can identify, even if some fields are missing
5. For each entry, extract: {', '.join(schema['fields'][:5])}{'...' if len(schema['fields']) > 5 else ''}
6. Use "---ENTRY BOUNDARY---" markers to identify separate entry groups if present

**Return JSON**:
{{
  "recovered_entries": [
    {{
      "original_text_snippet": "first 50 chars of the entry text",
      "fields": {{
        "field1": "value1",
        "field2": "value2",
        ...
      }}
    }},
    ...
  ],
  "recovery_notes": "Brief explanation of how you parsed the structure"
}}"""

    try:
        llm_result = call_llm(
            stage="stage_4",
            messages=[
                {"role": "system", "content": "You are an expert at parsing messy document structures. Extract structured data even from poorly formatted tables and lists."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )

        result_text = llm_result["content"]
        result = json.loads(result_text)

        recovered = result.get("recovered_entries", [])
        recovery_notes = result.get("recovery_notes", "")

        cost = llm_result["cost"]

        print(f"    LLM Recovery: {len(recovered)} entries recovered | ${cost:.4f}")
        if recovery_notes:
            print(f"       Notes: {recovery_notes[:100]}...")

        # Match recovered entries back to original entries
        # This is approximate - we try to match by text snippet
        recovered_entries = []
        for entry in entries:
            entry_text = entry.get("text", "")
            matched_recovery = None

            # Try to find a matching recovery by text snippet
            for rec in recovered:
                snippet = rec.get("original_text_snippet", "")
                if snippet and snippet.lower() in entry_text.lower()[:100]:
                    matched_recovery = rec
                    break

            if matched_recovery:
                # Apply recovered fields
                recovered_fields = matched_recovery.get("fields", {})
                # Coerce off-type LLM values before regex/downstream consumers
                recovered_fields = coerce_field_value_types(recovered_fields)
                # Normalize dates
                recovered_fields = normalize_dates(recovered_fields)
                # Apply regex post-processing
                recovered_fields, reformatted = apply_regex_post_processing(
                    entry_text, recovered_fields, taxonomy_code
                )
                # Recalculate coverage
                new_coverage = calculate_unextracted_content(entry_text, recovered_fields)

                entry_result = {
                    **entry,
                    "extracted_fields": recovered_fields,
                    "extraction_success": True,
                    "extraction_coverage": new_coverage,
                    "llm_recovery_applied": True
                }
                if reformatted:
                    entry_result["reformatted_fields"] = reformatted
                recovered_entries.append(entry_result)
            else:
                # No matching recovery - keep original
                recovered_entries.append({
                    **entry,
                    "llm_recovery_attempted": True,
                    "llm_recovery_matched": False
                })

        return recovered_entries

    except Exception as e:
        print(f"    ⚠ LLM Recovery failed: {e}")
        # Return original entries unchanged
        return [{**entry, "llm_recovery_error": str(e)} for entry in entries]


def _get_field_descriptions(taxonomy_code: str) -> str:
    """Get human-readable descriptions of expected fields for a taxonomy code.

    Reads from the shared FIELD_DESCRIPTIONS constant (single source of truth).
    """
    if taxonomy_code in FIELD_DESCRIPTIONS:
        lines = []
        for field_name, desc in FIELD_DESCRIPTIONS[taxonomy_code].items():
            lines.append(f"- {field_name}: {desc}")
        return "\n".join(lines)

    return f"Extract all available fields: {', '.join(get_field_schema(taxonomy_code)['fields'])}"


def build_extraction_prompt(entry: Dict[str, Any], schema: Dict[str, Any]) -> str:
    """
    Build LLM prompt for field extraction.
    """
    taxonomy_code = entry.get("taxonomy_code", "UNKNOWN")
    taxonomy_label = entry.get("taxonomy_label", "Unknown")
    text = entry.get("text", "")

    fields = schema["fields"]
    required = schema.get("required", [])

    prompt = f"""Extract structured fields from this CV entry.

**Entry Classification**: {taxonomy_code} - {taxonomy_label}

**Entry Text**:
{text}

**Fields to Extract**:
{', '.join(fields)}

**Required Fields** (must extract if present):
{', '.join(required)}

**Instructions**:
1. Extract all available fields from the text
2. Use null for fields not found
3. For dates: use YYYY-MM-DD format when possible, or YYYY if only year available
4. For authors: extract as a single string (e.g., "Smith J, Doe A, et al.")
5. Be precise - only extract what is explicitly stated
6. Do not infer or guess missing information

Return JSON with the extracted fields."""

    return prompt


def extract_fields_batch(
    entries: List[Dict[str, Any]],
    batch_idx: int,
    total_batches: int,
    model: str = None,
    cv_owner_name: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Extract fields from a batch of entries using LLM.

    Args:
        entries: List of entries to process
        batch_idx: Current batch index
        total_batches: Total number of batches
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        cv_owner_name: Dict with 'last_name' and optionally 'full_name' of CV owner
    """
    print(f"  Processing batch {batch_idx + 1}/{total_batches} ({len(entries)} entries)...")

    # Group by taxonomy code for better prompting
    entries_by_code = {}
    for entry in entries:
        code = entry.get("taxonomy_code", "UNKNOWN")
        if code not in entries_by_code:
            entries_by_code[code] = []
        entries_by_code[code].append(entry)

    all_extracted = []
    total_cost = 0.0
    total_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0

    for code, code_entries in entries_by_code.items():
        schema = get_field_schema(code)

        # Build batch prompt
        batch_items = []
        for i, entry in enumerate(code_entries):
            item = {
                "entry_index": i,
                "text": entry.get("text", ""),
                "element_idx_start": entry.get("element_idx_start"),
                "element_idx_end": entry.get("element_idx_end")
            }
            batch_items.append(item)

        # Get label for this taxonomy code
        code_label = get_taxonomy_label(code)

        # Build target_name instruction for publications/presentations
        target_name_instruction = ""
        if code.startswith('S') or code == 'R':
            if cv_owner_name and cv_owner_name.get('last_name'):
                last_name = cv_owner_name['last_name']
                target_name_instruction = f"""
9. **target_name**: This is the CV owner's publication. Find "{last_name}" (or similar) in the author list and extract their name EXACTLY as it appears (e.g., "{last_name} JA" or "{last_name}, J."). This identifies the CV owner among the authors."""
            else:
                target_name_instruction = """
9. **target_name**: Extract the CV owner's name from the author list. In a CV, the owner is typically the first author, last author, or marked with an asterisk (*). Extract the name exactly as it appears in the author list."""

        # Build field guide from FIELD_DESCRIPTIONS if available for this code
        field_guide_section = ""
        if code in FIELD_DESCRIPTIONS:
            guide_lines = []
            for field_name, desc in FIELD_DESCRIPTIONS[code].items():
                guide_lines.append(f"- {field_name}: {desc}")
            field_guide_section = "\n**Field Guide** (what each field should contain):\n" + "\n".join(guide_lines) + "\n"

        prompt = f"""Extract structured fields from these CV entries.

**Classification**: {code} - {code_label}

**Fields to Extract**: {', '.join(schema['fields'])}
{field_guide_section}
**Required Fields**: {', '.join(schema.get('required', []))}

**Entries**:
"""
        for item in batch_items:
            prompt += f"\n[Entry {item['entry_index']}]:\n{item['text']}\n"

        # Add code-specific instructions
        code_specific_instructions = ""
        if code.startswith('M2'):
            code_specific_instructions = """
9. **GRANTS (M2A/M2B/M2C)**:
   - pi_name = a PERSON'S NAME (e.g., "Susan Bostwick", "John Smith") - NOT the project title
   - title = the scientific project title - NOT a person's name, NOT FTE information
   - percent_effort = extract FTE as percentage (e.g., ".08FTE" → "8%", "0.1 FTE" → "10%")
   - Do NOT put the project title in pi_name field
   - If no PI name is found, leave pi_name as null"""
        elif code == 'K4':
            code_specific_instructions = """
9. **CONTINUING EDUCATION (K4)** - CRITICAL field separation:
   - If text reads "[Role] of/for [Title]" (e.g., "Creator and Presenter of Insomnia evaluation and management"):
     * role = "Creator and Presenter" (everything BEFORE "of/for")
     * activity_title = "Insomnia evaluation and management" (everything AFTER "of/for")
   - activity_title must NEVER include the person's role
   - role must NEVER include the activity/course name"""
        elif code == 'I':
            code_specific_instructions = """
9. **MEMBERSHIPS (I)** - CRITICAL field separation:
   - If text reads "Fellow | American Academy of Pediatrics" or "Fellow, Organization Name":
     * membership_type = "Fellow" (the designation/level)
     * organization = "American Academy of Pediatrics" (the society name only)
   - Common membership_type values: Fellow, Member, Diplomat, Associate Member, Honorary Member
   - Do NOT merge membership_type into organization - they are separate fields"""
        elif code == 'Q2':
            code_specific_instructions = """
9. **EXTRAMURAL COMMITTEES (Q2)** - CRITICAL three-way field separation:
   - committee_name = the specific committee name ONLY (e.g., "Education Committee")
   - role = ONLY the role word (e.g., "Member", "Chair") - NOT the committee name
   - organization = the parent organization (e.g., "American Academy of Neurology")
   - Example: "Member, Education Committee, American Academy of Neurology"
     * committee_name = "Education Committee"
     * role = "Member"
     * organization = "American Academy of Neurology"
   - Do NOT put the committee name in the role field or vice versa"""
        elif code == 'P':
            code_specific_instructions = """
9. **INSTITUTIONAL COMMITTEES (P)** - CRITICAL field separation:
   - committee_name = the committee/body name ONLY (e.g., "Quality Improvement Committee")
   - role = ONLY the role word(s) (e.g., "Chair", "Member") - NOT the committee name
   - Do NOT merge role into committee_name or vice versa"""

        prompt += f"""
**Instructions**:
1. For each entry, extract all available fields
2. Use null for fields not found
3. Dates:
   - For single dates: use YYYY-MM-DD or YYYY format
   - For date ranges (e.g., "2005-2008"): use start_date and end_date fields
   - For ongoing dates: preserve "present", "ongoing", or "current" exactly as written (do NOT convert to a year)
4. Authors: single string (e.g., "Smith J, Doe A")
5. Emails: extract multiple emails separately (primary_email, secondary_email, institutional_email, personal_email)
6. Tab-separated values: If text contains tabs (\\t) or pipe characters (|), these indicate table columns - extract each column as a separate field value, not as merged text
7. Only extract explicitly stated information - do not infer or guess
8. CRITICAL: Include "entry_index" field in each extraction to match the entry number above{target_name_instruction}{code_specific_instructions}

Return JSON with format:
{{
  "entries": [
    {{
      "entry_index": 0,
      "field1": "value1",
      "field2": "value2",
      ...
    }},
    {{
      "entry_index": 1,
      ...
    }}
  ]
}}"""

        # Call LLM
        try:
            messages = [
                {"role": "system", "content": "You are a precise field extraction system for academic CVs. Extract only explicitly stated information."},
                {"role": "user", "content": prompt}
            ]

            llm_result = call_llm(
                stage="stage_4",
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"}
            )

            # Parse response
            content = llm_result["content"]
            result = json.loads(content)

            cost = llm_result["cost"]
            total_cost += cost
            total_tokens += llm_result["total_tokens"]
            total_cache_read_tokens += llm_result.get("cache_read_tokens", 0)
            total_cache_write_tokens += llm_result.get("cache_write_tokens", 0)

            # Log cost for this call
            print(f"    [{code}] {len(code_entries)} entries | {llm_result['total_tokens']:,} tokens | ${cost:.4f}")

            # Merge extracted fields back with entries
            # CRITICAL: Use entry_index from LLM response to match correctly
            extractions = result.get("entries", result.get("extractions", []))

            # Create index map for safe merging
            extraction_map = {}
            for extracted in extractions:
                entry_idx = extracted.get("entry_index")
                if entry_idx is not None:
                    extraction_map[entry_idx] = extracted

            # Merge using explicit indices to avoid mismapping
            for i, entry in enumerate(code_entries):
                if i in extraction_map:
                    # Remove entry_index from extracted fields (it's just for matching)
                    extracted_fields = {k: v for k, v in extraction_map[i].items()
                                       if k != "entry_index"}

                    # Coerce off-type LLM values (e.g. list-valued strings) before
                    # any string/number consumer (regex post-processing, downstream
                    # stages) touches them -- see coerce_field_value_types().
                    extracted_fields = coerce_field_value_types(extracted_fields)

                    # Apply date normalization to split ranges into start_date/end_date
                    extracted_fields = normalize_dates(extracted_fields)

                    # Apply regex post-processing and track reformatted values
                    original_text = entry.get("text", "")
                    taxonomy_code = entry.get("taxonomy_code", "")
                    extracted_fields, reformatted_fields = apply_regex_post_processing(
                        original_text, extracted_fields, taxonomy_code
                    )

                    # Calculate unextracted content for quality assurance
                    unextracted_info = calculate_unextracted_content(original_text, extracted_fields)

                    entry_result = {
                        **entry,
                        "extracted_fields": extracted_fields,
                        "extraction_success": True,
                        "extraction_coverage": unextracted_info
                    }

                    # Add reformatted_fields if any reformatting occurred
                    if reformatted_fields:
                        entry_result["reformatted_fields"] = reformatted_fields

                    all_extracted.append(entry_result)
                else:
                    # No extraction found - mark as failed
                    all_extracted.append({
                        **entry,
                        "extracted_fields": {},
                        "extraction_success": False,
                        "extraction_error": "No matching extraction in LLM response"
                    })

        except Exception as e:
            print(f"    ⚠ Error extracting fields for code {code}: {e}")
            # Fallback: mark as failed
            for entry in code_entries:
                all_extracted.append({
                    **entry,
                    "extracted_fields": {},
                    "extraction_success": False,
                    "extraction_error": str(e)
                })

    # ==========================================================================
    # LLM RECOVERY PASS: Re-process entries with poor extraction coverage
    # ==========================================================================
    entries_needing_recovery = [e for e in all_extracted if needs_llm_recovery(e)]

    if entries_needing_recovery:
        print(f"\n  🔧 LLM Recovery: {len(entries_needing_recovery)} entries with poor extraction coverage")

        # Group by taxonomy code for better context
        recovery_by_code = {}
        for entry in entries_needing_recovery:
            code = entry.get("taxonomy_code", "UNKNOWN")
            if code not in recovery_by_code:
                recovery_by_code[code] = []
            recovery_by_code[code].append(entry)

        # Attempt recovery for each code group
        recovered_entries = {}
        recovery_cost = 0.0

        for code, code_entries in recovery_by_code.items():
            print(f"    [{code}] Attempting recovery for {len(code_entries)} entries...")
            recovered = attempt_llm_recovery(code_entries, model=model)

            # Track recovered entries by their element_idx for replacement
            for rec_entry in recovered:
                key = (rec_entry.get("element_idx_start"), rec_entry.get("element_idx_end"))
                recovered_entries[key] = rec_entry

        # Replace original entries with recovered versions
        final_extracted = []
        for entry in all_extracted:
            key = (entry.get("element_idx_start"), entry.get("element_idx_end"))
            if key in recovered_entries:
                final_extracted.append(recovered_entries[key])
            else:
                final_extracted.append(entry)

        all_extracted = final_extracted

        # Log recovery summary
        successful_recoveries = sum(1 for e in all_extracted if e.get("llm_recovery_applied"))
        print(f"  ✓ Recovery complete: {successful_recoveries}/{len(entries_needing_recovery)} entries improved")

    return {
        "entries": all_extracted,
        "cost": total_cost,
        "tokens": total_tokens,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
        "success": True
    }


def coerce_field_value_types(extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce LLM-extracted field values to the scalar types downstream stages assume.

    Stage 4 stores raw LLM JSON (the extraction call uses
    ``response_format={"type": "json_object"}`` with no schema), so a field the
    prompt asks for as a string can legitimately come back as a list of strings.
    This is common in practice -- e.g. ``narrative`` is a list in 55/65 sample
    outputs, and ``training_type``/``program_name``/``description``/``specialty``/
    ``start_date``/``end_date`` have all been observed as lists. Downstream code
    then calls ``.strip()``, ``.lower()``, ``.split()``, ``re.search()``,
    ``< 0.7`` or ``", ".join([...])`` on the value and crashes the entire run
    (e.g. "Pipeline failed -- expected str instance, list found", or a
    ``'<' not supported between instances of 'str' and 'int'`` TypeError).
    stage_6_word_template.py already patches a handful of fields ad hoc with
    ``isinstance(x, list)`` checks; this normalizes every field once, centrally,
    before any consumer sees it.

    The rule is deliberately conservative -- it only touches values that would
    otherwise crash a string/number consumer:

    - a list whose items are all scalars -> ``"; "``-joined string of the
      non-empty items (mirrors the ``"; ".join(...)`` convention already used in
      stage_6_word_template.py)
    - everything else is returned untouched, so numeric fields (``year``,
      ``volume``) stay numeric, structured fields (``locations`` and other
      list-of-dict / dict values) keep their shape, and ``None`` stays ``None``.
    """
    if not isinstance(extracted_fields, dict):
        return extracted_fields

    coerced = {}
    for key, value in extracted_fields.items():
        if isinstance(value, list) and all(
            item is None or isinstance(item, (str, int, float)) for item in value
        ):
            coerced[key] = "; ".join(
                str(item).strip() for item in value if item not in (None, "")
            )
        else:
            coerced[key] = value
    return coerced


def normalize_dates(extracted_fields: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize date fields by splitting ranges into start_date and end_date.

    Handles formats like:
    - "YYYY-YYYY" → start_date: YYYY, end_date: YYYY
    - "YYYY-present" → start_date: YYYY, end_date: "present"
    - "YYYY-MM-YYYY-MM" → start_date: YYYY-MM, end_date: YYYY-MM

    Args:
        extracted_fields: Dictionary of extracted fields

    Returns:
        Dictionary with normalized date fields (always uses start_date/end_date)
    """
    import re

    normalized = extracted_fields.copy()

    # Fields that might contain date ranges
    date_field_names = ['date', 'year', 'years', 'years_taught', 'dates', 'period']

    for field_name in date_field_names:
        if field_name not in normalized:
            continue

        value = normalized[field_name]
        if not value or not isinstance(value, str):
            continue

        value_str = str(value).strip()

        # Pattern 1: YYYY-YYYY (e.g., "2015-2016")
        match = re.match(r'^(\d{4})-(\d{4})$', value_str)
        if match:
            start_year, end_year = match.groups()
            # Always use standardized start_date/end_date field names
            normalized['start_date'] = start_year
            normalized['end_date'] = end_year
            # Remove original field to avoid duplication
            del normalized[field_name]
            continue

        # Pattern 2: YYYY-present (e.g., "2009-present", "2009-2025" where 2025 might mean ongoing)
        match = re.match(r'^(\d{4})-(present|ongoing|current)$', value_str, re.IGNORECASE)
        if match:
            start_year = match.group(1)
            normalized['start_date'] = start_year
            normalized['end_date'] = 'present'
            del normalized[field_name]
            continue

        # Pattern 3: YYYY-MM-YYYY-MM (e.g., "2015-06-2016-08")
        match = re.match(r'^(\d{4}-\d{2})-(\d{4}-\d{2})$', value_str)
        if match:
            start_date, end_date = match.groups()
            normalized['start_date'] = start_date
            normalized['end_date'] = end_date
            del normalized[field_name]
            continue

    return normalized


# ============================================================================
# Regex Post-Processing Patterns
# ============================================================================
REGEX_PATTERNS = {
    'pmid': r'PMID[:\s]*(\d{7,8})',
    'pmcid': r'PMC(\d+)',
    'doi': r'(10\.\d{4,}/[^\s\]>\),]+)',
    'orcid': r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])',
}


def normalize_authors_vancouver(authors_string: str) -> str:
    """
    Normalize author string to Vancouver citation style.

    Vancouver format: LastName AB, LastName CD, LastName EF, et al.
    - No periods in initials
    - No comma between last name and initials
    - Authors separated by commas
    - "et al." at end (with period)

    Examples:
        "Smith, John A., Jones, Mary B." → "Smith JA, Jones MB"
        "Smith J.A., Jones M.B." → "Smith JA, Jones MB"
        "Smith, J. A. and Jones, M. B." → "Smith JA, Jones MB"
        "Smith JA, Jones MB, et al.," → "Smith JA, Jones MB, et al."

    Args:
        authors_string: Original author string in any format

    Returns:
        Normalized Vancouver-style author string
    """
    import re

    if not authors_string:
        return authors_string

    result = authors_string

    # Step 1: Normalize "et al" variations at the end
    # Capture and temporarily remove et al to process authors
    et_al_match = re.search(r',?\s*(et\.?\s*al\.?)\s*[,;\.]*\s*$', result, re.IGNORECASE)
    has_et_al = bool(et_al_match)
    if has_et_al:
        result = result[:et_al_match.start()]

    # Step 2: Replace "and" / "&" with comma for consistent splitting
    result = re.sub(r'\s+and\s+', ', ', result, flags=re.IGNORECASE)
    result = re.sub(r'\s*&\s*', ', ', result)

    # Step 3: Split into individual authors
    # Split on comma, semicolon, or period followed by space and capital letter
    authors = re.split(r'[;]\s*|,\s*(?=[A-Z])', result)

    normalized_authors = []
    for author in authors:
        author = author.strip()
        if not author:
            continue

        # Try to parse "LastName, FirstName MiddleName" format
        # e.g., "Smith, John Albert" or "Smith, J. A." or "Smith, JA"
        comma_match = re.match(r'^([A-Za-z\-\']+),\s*(.+)$', author)
        if comma_match:
            last_name = comma_match.group(1)
            first_parts = comma_match.group(2)

            # Extract initials from first/middle names
            initials = extract_initials(first_parts)
            if initials:
                normalized_authors.append(f"{last_name} {initials}")
                continue

        # Try "FirstName LastName" format (less common in citations)
        # e.g., "John A. Smith" or "John Albert Smith"
        space_parts = author.split()
        if len(space_parts) >= 2:
            # Check if last part looks like a last name (not initials)
            if len(space_parts[-1]) > 2 and not re.match(r'^[A-Z]+$', space_parts[-1]):
                last_name = space_parts[-1]
                first_parts = ' '.join(space_parts[:-1])
                initials = extract_initials(first_parts)
                if initials:
                    normalized_authors.append(f"{last_name} {initials}")
                    continue

        # Already in "LastName AB" format or couldn't parse - clean up periods
        cleaned = re.sub(r'\.(?=[A-Z]|\s|$)', '', author)  # Remove periods from initials
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()  # Normalize spaces
        if cleaned:
            normalized_authors.append(cleaned)

    # Step 4: Rejoin with commas
    result = ', '.join(normalized_authors)

    # Step 5: Add back "et al." if it was present
    if has_et_al:
        result = result.rstrip(',').strip() + ', et al.'

    # Step 6: Clean up any trailing punctuation
    result = re.sub(r'[,;\.]+\s*$', '', result)
    if has_et_al:
        result += '.'  # et al. needs the period

    return result


def extract_initials(name_parts: str) -> str:
    """
    Extract initials from first/middle name string.

    Examples:
        "John Albert" → "JA"
        "J. A." → "JA"
        "JA" → "JA"
        "John A." → "JA"

    Args:
        name_parts: First and middle names as string

    Returns:
        Uppercase initials without periods or spaces
    """
    import re

    if not name_parts:
        return ""

    # If already looks like initials (all caps, 1-3 chars), just clean periods
    cleaned = re.sub(r'[\.\s]', '', name_parts)
    if re.match(r'^[A-Z]{1,4}$', cleaned):
        return cleaned

    # Extract first letter of each word/initial
    initials = []
    parts = re.split(r'[\s\.]+', name_parts)
    for part in parts:
        part = part.strip()
        if part:
            initials.append(part[0].upper())

    return ''.join(initials)


def apply_regex_post_processing(
    original_text: str,
    extracted_fields: Dict[str, Any],
    taxonomy_code: str
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Apply regex patterns to catch identifiers missed by LLM extraction.

    Also tracks reformatted values for transparency.

    Args:
        original_text: Original CV entry text
        extracted_fields: Fields extracted by LLM
        taxonomy_code: Entry taxonomy code (e.g., S1, M2A)

    Returns:
        Tuple of (updated_fields, reformatted_fields)
        - updated_fields: Extracted fields with regex-caught values added
        - reformatted_fields: Dict tracking original -> reformatted values
    """
    import re

    updated = extracted_fields.copy()
    reformatted = {}

    # Only apply identifier extraction to relevant taxonomy codes
    if taxonomy_code.startswith('S') or taxonomy_code in ('R', 'N4'):

        # Extract PMID if not already present
        if not updated.get('pmid'):
            pmid_match = re.search(REGEX_PATTERNS['pmid'], original_text, re.IGNORECASE)
            if pmid_match:
                original_pmid_text = pmid_match.group(0)  # Full match like "PMID: 12345678"
                pmid_value = pmid_match.group(1)  # Just the number
                updated['pmid'] = pmid_value
                reformatted['pmid'] = {
                    'original': original_pmid_text,
                    'reformatted': pmid_value,
                    'reason': 'Extracted PMID number via regex'
                }

        # Extract PMCID if not already present
        if not updated.get('pmcid'):
            pmcid_match = re.search(REGEX_PATTERNS['pmcid'], original_text, re.IGNORECASE)
            if pmcid_match:
                pmcid_value = f"PMC{pmcid_match.group(1)}"
                updated['pmcid'] = pmcid_value
                reformatted['pmcid'] = {
                    'original': pmcid_match.group(0),
                    'reformatted': pmcid_value,
                    'reason': 'Extracted PMCID via regex'
                }

        # Extract/normalize DOI if not already present
        if not updated.get('doi'):
            doi_match = re.search(REGEX_PATTERNS['doi'], original_text)
            if doi_match:
                doi_value = doi_match.group(1)
                # Clean up trailing punctuation that might have been captured
                doi_value = re.sub(r'[\.,;:]+$', '', doi_value)
                updated['doi'] = doi_value
                reformatted['doi'] = {
                    'original': doi_match.group(0),
                    'reformatted': doi_value,
                    'reason': 'Extracted DOI via regex'
                }
        elif updated.get('doi'):
            # Normalize existing DOI (remove doi.org prefix, etc.)
            existing_doi = updated['doi']
            normalized_doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', existing_doi)
            normalized_doi = re.sub(r'^doi:', '', normalized_doi, flags=re.IGNORECASE)
            normalized_doi = re.sub(r'[\.,;:]+$', '', normalized_doi)
            if normalized_doi != existing_doi:
                updated['doi'] = normalized_doi
                reformatted['doi'] = {
                    'original': existing_doi,
                    'reformatted': normalized_doi,
                    'reason': 'Normalized DOI format'
                }

    # Extract ORCID for profile sections
    if taxonomy_code in ('A', 'S0'):
        if not updated.get('orcid'):
            orcid_match = re.search(REGEX_PATTERNS['orcid'], original_text)
            if orcid_match:
                updated['orcid'] = orcid_match.group(1)
                reformatted['orcid'] = {
                    'original': orcid_match.group(0),
                    'reformatted': orcid_match.group(1),
                    'reason': 'Extracted ORCID via regex'
                }

    # Normalize author formatting to Vancouver style
    # Vancouver: LastName AB, LastName CD (no periods in initials, no comma before initials)
    if updated.get('authors'):
        original_authors = updated['authors']
        normalized_authors = normalize_authors_vancouver(original_authors)
        if normalized_authors != original_authors:
            updated['authors'] = normalized_authors
            reformatted['authors'] = {
                'original': original_authors,
                'reformatted': normalized_authors,
                'reason': 'Normalized to Vancouver author format'
            }

    # Clean title (remove leading labels like "Featured:", "Submitted:")
    if updated.get('title'):
        original_title = updated['title']
        # Remove common prefixes
        cleaned_title = re.sub(r'^(Featured|Submitted|In Review|In Preparation|Accepted)[:\s]+', '',
                               original_title, flags=re.IGNORECASE)
        # Remove stray punctuation artifacts
        cleaned_title = re.sub(r'^[:\?\s]+', '', cleaned_title)
        cleaned_title = re.sub(r'[:\?\s]+$', '', cleaned_title)
        if cleaned_title != original_title:
            updated['title'] = cleaned_title
            reformatted['title'] = {
                'original': original_title,
                'reformatted': cleaned_title,
                'reason': 'Removed prefix label from title'
            }

    # Extract percent effort/FTE for grant entries
    if taxonomy_code.startswith('M2'):
        if not updated.get('percent_effort'):
            # Match various FTE formats: .08FTE, 0.08 FTE, 8%, 8 %, .08 FTE, 8% effort, etc.
            fte_patterns = [
                r'\.(\d{1,2})\s*FTE',          # .08FTE, .08 FTE
                r'0\.(\d{1,2})\s*FTE',         # 0.08FTE, 0.08 FTE
                r'(\d{1,3})\s*%\s*(?:effort|FTE)?',  # 8%, 8 %, 8% effort
                r'(\d{1,3})\s*percent',        # 8 percent
            ]
            for pattern in fte_patterns:
                fte_match = re.search(pattern, original_text, re.IGNORECASE)
                if fte_match:
                    fte_value = fte_match.group(1)
                    # Normalize to percentage format
                    if '.' not in pattern:  # Already a percentage
                        percent_effort = f"{fte_value}%"
                    else:  # Decimal FTE format, convert to percentage
                        percent_effort = f"{int(fte_value)}%"
                    updated['percent_effort'] = percent_effort
                    reformatted['percent_effort'] = {
                        'original': fte_match.group(0),
                        'reformatted': percent_effort,
                        'reason': 'Extracted percent effort via regex'
                    }
                    break

    return updated, reformatted


def extract_cv_owner_name(document_uid: str, mapped_entries: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Extract CV owner's name using LLM from the first chunk of CV content.

    Uses an LLM (configured in llm_config.yaml) for a reliable extraction that
    handles all edge cases (dashes, various formats, credentials, etc.)
    without brittle regex.

    Args:
        document_uid: Document identifier (e.g., "2015_Wende")
        mapped_entries: List of all mapped entries

    Returns:
        Dict with 'first_name', 'middle_name', 'last_name', 'suffix',
        'full_name', and 'full_name_with_credentials'
    """
    result = {
        'first_name': '',
        'middle_name': '',
        'last_name': '',
        'suffix': '',
        'full_name': '',
        'full_name_with_credentials': ''
    }

    # Gather first ~10 entries to give LLM context
    first_entries = []
    for entry in mapped_entries[:12]:
        text = entry.get('text', '').strip()
        if text and len(text) < 500:  # Skip very long entries
            first_entries.append(text)

    # Helper to extract last name from document_uid as fallback.
    #
    # This works for filename-style uids ('2097_Upton_Cv' -> 'Upton') and is
    # worth keeping for them. It must NOT fire for an opaque uid: 'web151' was
    # written in as the owner's surname on every CV whose name extraction
    # returned nothing (#457). A manufactured surname is worse than an empty
    # one -- it looks plausible, defeats emptiness checks in spirit, and feeds
    # add_target_names and the bibliography author bolding a token that matches
    # nothing. A missing name should look missing.
    def fallback_from_uid():
        import re
        if document_uid:
            # Case-insensitive: '_CV' was not stripped, so '2026_OBrien_CV'
            # yielded the literal 'CV' as the surname.
            uid_clean = re.sub(r'_cv$', '', document_uid, flags=re.IGNORECASE)
            # Remove random prefix like "WSP0KQ_"
            uid_clean = re.sub(r'^[A-Z0-9]{6}_', '', uid_clean)
            parts = uid_clean.split('_')
            name_parts = [p for p in parts if not re.match(r'^\d{4}$', p) and len(p) > 1]
            # Only a purely alphabetic token can be a surname. 'web151' and
            # 'I5NKUG' are identifiers, not names.
            if name_parts and name_parts[-1].isalpha():
                result['last_name'] = name_parts[-1]

    if not first_entries:
        fallback_from_uid()
        return result

    content_block = "\n".join(first_entries[:10])

    prompt = f"""This is the beginning of a CV/resume. Extract the CV owner's name.

Content:
{content_block}

Return JSON with:
- "first_name": First/given name (e.g., "Spencer", "John")
- "middle_name": Middle name or initial if present, empty string if none (e.g., "A.", "Elizabeth", "")
- "last_name": Last/family name (e.g., "Upton", "Smith")
- "suffix": Name suffix if present, empty string if none (e.g., "Jr.", "III", "")
- "full_name": Full name without credentials (e.g., "Spencer Upton", "John A. Smith Jr.")
- "full_name_with_credentials": Full name with degrees/credentials if present (e.g., "Spencer Upton, MS, MA")

If you cannot determine a field, return an empty string for it."""

    try:
        llm_result = call_llm(
            stage="stage_4",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )

        response_text = llm_result["content"]
        parsed = json.loads(response_text)

        result['first_name'] = parsed.get('first_name', '').strip()
        result['middle_name'] = parsed.get('middle_name', '').strip()
        result['last_name'] = parsed.get('last_name', '').strip()
        result['suffix'] = parsed.get('suffix', '').strip()
        result['full_name'] = parsed.get('full_name', '').strip()
        result['full_name_with_credentials'] = parsed.get('full_name_with_credentials', '').strip()

    except Exception as e:
        print(f"  Warning: LLM name extraction failed: {e}")
        fallback_from_uid()

    # If LLM didn't find a last_name, try fallback
    if not result['last_name']:
        fallback_from_uid()

    return result


# Fields that name where the CV owner themselves works, studies or teaches.
# Deliberately excludes 'venue' / 'publication_venue' and the 'location' field on
# R / S8 / K5: those record where a talk was given or a paper appeared, and a
# conference city is not the owner's location.
_OWNER_AFFILIATION_FIELDS = ('employer', 'institution', 'organization', 'address')


def _entry_end_year(fields: Dict[str, Any]) -> int:
    """Sort key for recency: 'present' beats any year, an absent date sorts last."""
    import re

    raw = fields.get('end_date', '') or fields.get('dates_attended_end_date', '') or ''
    text = str(raw).lower()
    if 'present' in text or 'current' in text:
        return 9999
    match = re.search(r'(\d{4})', text)
    return int(match.group(1)) if match else 0


def _owner_affiliation_lines(
    mapped_entries: List[Dict[str, Any]],
    limit: int = 15,
) -> List[str]:
    """Recency-ranked affiliation lines drawn from *any* taxonomy code.

    The primary pool in infer_cv_owner_location is gated on a fixed list of
    section codes, which assumes the owner's location appears under Personal
    Data / Education / Positions. Plenty of CVs state it only under Employment
    Status (code E) or across a wall of teaching entries (K*), and those return
    an empty pool. This builds a last-resort pool instead of growing the
    allow-list, so it does not matter which section the CV happens to use.
    """
    counts: Dict[str, int] = {}
    best_year: Dict[str, int] = {}

    for entry in mapped_entries:
        fields = entry.get('extracted_fields', {}) or {}
        if not isinstance(fields, dict):
            continue
        # Code E is "Employment Status" -- current by definition, so it outranks
        # everything else regardless of whether it carries a date.
        year = 9999 if entry.get('taxonomy_code') == 'E' else _entry_end_year(fields)
        for name in _OWNER_AFFILIATION_FIELDS:
            value = str(fields.get(name) or '').strip()
            if len(value) < 3:
                continue
            counts[value] = counts.get(value, 0) + 1
            best_year[value] = max(best_year.get(value, 0), year)

    if not counts:
        return []

    # Recency outranks frequency: a long-held past post must not beat a current
    # one just by appearing more often. Frequency only breaks recency ties.
    ranked = sorted(counts, key=lambda v: (best_year[v], counts[v]), reverse=True)[:limit]

    lines = ["AFFILIATIONS STATED ACROSS THE CV (most recent first, with how often each appears):"]
    for value in ranked:
        marker = " [current]" if best_year[value] == 9999 else ""
        lines.append(f"  - {value[:150]} (x{counts[value]}){marker}")
    return lines


def infer_cv_owner_location(
    mapped_entries: List[Dict[str, Any]],
    model: str = None
) -> Dict[str, Any]:
    """
    Infer CV owner's current location(s) from employment, education, and training history.

    This information is used to classify geographic scope (Regional/National/International)
    for service activities, presentations, and conferences.

    Args:
        mapped_entries: List of all mapped entries with taxonomy codes
        model: Unused -- the model is resolved from llm_config.yaml, not this argument

    Returns:
        Dict with:
        - 'locations': List of {institution, city, state, country, confidence}
        - 'metro_area': Inferred metropolitan area (e.g., "New York City")
        - 'primary_location': The most likely current location
    """
    import re
    from datetime import datetime

    result = {
        'locations': [],
        'metro_area': '',
        'primary_location': None,
        'inference_success': False
    }

    # Collect location-relevant entries (positions, education, training, personal data)
    location_codes = ['A', 'B1', 'B2', 'C', 'D1', 'D2', 'D3']
    location_entries = []

    for entry in mapped_entries:
        code = entry.get('taxonomy_code', '')
        if code in location_codes:
            text = entry.get('text', '').strip()
            fields = entry.get('extracted_fields', {}) or {}

            # Skip entries without meaningful location data
            if not text and not fields:
                continue

            # Parse end_date for sorting (prioritize current positions)
            end_date_raw = fields.get('end_date', '') or fields.get('dates_attended_end_date', '') or ''
            end_date_str = str(end_date_raw).lower()

            # Assign sort priority: "present" = 9999, dates = year, empty = 0
            if 'present' in end_date_str or 'current' in end_date_str:
                sort_year = 9999
            else:
                year_match = re.search(r'(\d{4})', end_date_str)
                sort_year = int(year_match.group(1)) if year_match else 0

            location_entries.append({
                'code': code,
                'text': text[:300],  # Truncate for prompt efficiency
                'fields': fields,
                'sort_year': sort_year
            })

    # An empty pool is not fatal any more -- it falls through to the
    # affiliation-wide fallback below.
    # Sort by date descending (most recent first)
    location_entries.sort(key=lambda x: x['sort_year'], reverse=True)

    # Format entries for LLM prompt
    formatted_lines = []

    # Group by type for clarity
    positions = [e for e in location_entries if e['code'] in ['D1', 'D2', 'D3']]
    training = [e for e in location_entries if e['code'] == 'C']
    education = [e for e in location_entries if e['code'] in ['B1', 'B2']]
    personal = [e for e in location_entries if e['code'] == 'A']

    if positions:
        formatted_lines.append("CURRENT AND PAST POSITIONS (most recent first):")
        for e in positions[:6]:
            fields = e['fields']
            title = fields.get('title', '')
            institution = fields.get('institution', fields.get('organization', ''))
            start = fields.get('start_date', '')
            end = fields.get('end_date', '')
            if institution:
                formatted_lines.append(f"  - {title} | {institution} | {start} - {end}")
            else:
                formatted_lines.append(f"  - {e['text'][:150]}")

    if training:
        formatted_lines.append("\nTRAINING:")
        for e in training[:3]:
            fields = e['fields']
            prog = fields.get('training_type', '')
            institution = fields.get('institution', '')
            end = fields.get('end_date', '')
            if institution:
                formatted_lines.append(f"  - {prog} | {institution} | ended {end}")
            else:
                formatted_lines.append(f"  - {e['text'][:150]}")

    if education:
        formatted_lines.append("\nEDUCATION:")
        for e in education[:3]:
            fields = e['fields']
            degree = fields.get('degree', fields.get('program_name', ''))
            institution = fields.get('institution', '')
            year = fields.get('year', fields.get('end_date', ''))
            if institution:
                formatted_lines.append(f"  - {degree} | {institution} | {year}")
            else:
                formatted_lines.append(f"  - {e['text'][:150]}")

    # Check personal data for address
    for e in personal:
        fields = e['fields']
        address = fields.get('address', '')
        if address and ('NY' in address or 'New York' in address or len(address) > 20):
            formatted_lines.append(f"\nOFFICE/HOME ADDRESS:\n  {address[:200]}")
            break

    def _query(history_text: str) -> bool:
        """Run the location prompt over one pool. True if it yielded a primary_location."""
        prompt = f"""Based on this CV owner's employment, education, and training history, determine their current primary location(s).

{history_text}

Return a JSON object with:
1. "locations": array of current affiliations, each with:
   - "institution": institution name
   - "city": city name
   - "state": state/province (if applicable)
   - "country": country name (default "USA" if US state)
   - "confidence": 0.0-1.0 (1.0 for current "present" positions)
2. "metro_area": the metropolitan area (e.g., "New York City", "Boston", "San Francisco Bay Area")
3. "primary_location": the single most likely current work location (copy of the highest-confidence entry)

Focus on positions with end_date="present" or most recent dates.
Return ONLY valid JSON, no explanation."""

        try:
            llm_result = call_llm(
                stage="stage_4",
                messages=[
                    {"role": "system", "content": "You extract location information from CV data. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )

            response_text = llm_result["content"].strip()

            # Clean up response (remove markdown code blocks if present)
            if response_text.startswith('```'):
                response_text = re.sub(r'^```(?:json)?\s*', '', response_text)
                response_text = re.sub(r'\s*```$', '', response_text)

            parsed = json.loads(response_text)

        except json.JSONDecodeError as e:
            print(f"  Warning: Could not parse location inference response: {e}")
            return False
        except Exception as e:
            print(f"  Warning: Location inference failed: {e}")
            return False

        result['tokens'] = result.get('tokens', 0) + llm_result["total_tokens"]
        result['cost'] = result.get('cost', 0.0) + llm_result["cost"]

        result['locations'] = parsed.get('locations', []) or result['locations']
        result['metro_area'] = parsed.get('metro_area', '') or result['metro_area']

        # A response with no primary_location is not a success -- downstream
        # scope classification reads exactly that key, and reporting success
        # without it is what made this failure invisible.
        if not parsed.get('primary_location'):
            return False

        result['primary_location'] = parsed.get('primary_location')
        result['inference_success'] = True
        return True

    if formatted_lines and _query("\n".join(formatted_lines)):
        return result

    # The section-gated pool above found nothing usable. Before giving up, retry
    # once against every affiliation stated anywhere in the CV, regardless of
    # which section it sits under. This only runs when the CV would otherwise
    # have returned no location at all, so it cannot change a working inference.
    fallback_lines = _owner_affiliation_lines(mapped_entries)
    if fallback_lines:
        _query("\n".join(fallback_lines))

    return result


def find_target_name_in_authors(text: str, cv_owner_last_name: str) -> str:
    """
    Find and extract the CV owner's name from raw citation text.

    This function handles full citation text (not just author list) and extracts
    the author portion before searching for the target name.

    Handles various marking conventions for CV owner:
    - Asterisk: "Smith J*", "*Smith J", "Smith* J"
    - Underline: "Smith J" (when underlined in original)
    - Bold markers: "**Smith J**"
    - Superscript markers: "Smith J†", "Smith J1"

    Matching strategy:
    - If only one author matches the last name → return it (permissive)
    - If multiple authors match → look for one with special markers
    - If still ambiguous → return the first match

    Args:
        text: Raw citation text or author string (e.g., "Smith J, Doe A. Title of Paper...")
        cv_owner_last_name: Last name to search for (e.g., "Smith")

    Returns:
        Exact author name as it appears in the text (e.g., "Smith J"), or empty string
    """
    import re

    if not text or not cv_owner_last_name:
        return ""

    # Normalize the last name for matching
    last_name_lower = cv_owner_last_name.lower()

    # Extract author portion from citation text
    # Common patterns: authors end before title (which typically follows a period after author list)
    # Look for pattern: "Authors. Title" or "Authors: Title" or "Authors (Year)"
    authors_string = text

    # Try to extract just the author portion (before the title)
    # Pattern: Authors followed by period and then a capital letter (start of title)
    author_match = re.match(r'^(.+?)\.\s+[A-Z]', text)
    if author_match:
        authors_string = author_match.group(1)
    else:
        # Alternative: look for year pattern that often separates authors from title
        author_match = re.match(r'^(.+?)\s*\(\d{4}\)', text)
        if author_match:
            authors_string = author_match.group(1)

    # Split by comma or semicolon
    author_list = re.split(r'[,;]', authors_string)

    # Find all matching authors
    matches = []
    marked_matches = []  # Authors with special markers (*, †, etc.)

    # Markers that indicate the CV owner
    marker_pattern = r'[\*†‡§¶#\^]'

    for author in author_list:
        author = author.strip()
        if not author:
            continue

        # Clean version for matching (remove markers and numbers)
        author_clean = re.sub(r'[\*†‡§¶#\^\d]+', '', author).strip()

        # Check if last name matches (word boundary to avoid partial matches)
        # e.g., "Wende" should match "Wende ME" but not "Wendell J"
        if re.search(rf'\b{re.escape(last_name_lower)}\b', author_clean.lower()):
            matches.append(author)

            # Check if this author has special markers
            if re.search(marker_pattern, author):
                marked_matches.append(author)

    # Decision logic
    if not matches:
        # No matches - try more permissive matching (substring)
        for author in author_list:
            author = author.strip()
            if last_name_lower in author.lower():
                matches.append(author)
                if re.search(marker_pattern, author):
                    marked_matches.append(author)

    if not matches:
        return ""

    if len(matches) == 1:
        # Only one match - use it
        return matches[0]

    if marked_matches:
        # Multiple matches but some are marked - prefer marked one
        return marked_matches[0]

    # Multiple unmarked matches - return first one (usually most prominent position)
    return matches[0]


def add_target_names(entries: List[Dict[str, Any]], cv_owner_last_name: str) -> List[Dict[str, Any]]:
    """
    Add target_name field to publication and presentation entries.

    IMPORTANT: Uses raw text to find target author, not extracted/formatted authors.
    This preserves the exact representation from the original CV (including markers,
    formatting variations, and "et al." as written).

    Args:
        entries: List of extracted entries
        cv_owner_last_name: CV owner's last name to search for

    Returns:
        Updated entries with target_name field added
    """
    for entry in entries:
        code = entry.get('taxonomy_code', '')
        # Only process S-codes (publications) and R-codes (presentations)
        if code.startswith('S') or code.startswith('R'):
            fields = entry.get('extracted_fields', {})
            raw_text = entry.get('text', '')

            # Only try to find target_name if not already set (or set to None)
            existing_target = fields.get('target_name')
            if raw_text and cv_owner_last_name and not existing_target:
                # Use raw text to find target author (preserves original formatting)
                target_name = find_target_name_in_authors(raw_text, cv_owner_last_name)
                if target_name:
                    fields['target_name'] = target_name

    return entries


def extract_fields_from_mapped_entries(
    mapped_entries: List[Dict[str, Any]],
    batch_size: int = 10,
    model: str = None,
    document_uid: str = "",
    cancel_check: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Extract structured fields from all mapped entries.

    Args:
        mapped_entries: List of taxonomy-mapped entries from Stage 3
        batch_size: Number of entries to process per batch (default: 10)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        document_uid: Document identifier for extracting CV owner name
        cancel_check: Optional zero-arg callable invoked at the top of each
            batch iteration. It should raise to abort the run (the web
            orchestrator passes its check_cancelled). None (the standalone CLI
            default) is a no-op.
    """
    print(f"\n{'='*80}")
    print("Stage 4: Intra-Entry Field Extraction")
    print(f"{'='*80}")
    print(f"Total entries: {len(mapped_entries)}")

    # Load and display schema version
    schemas = get_active_schemas()
    print(f"Field schemas: v{FIELD_SCHEMA_VERSION} ({len(schemas)} taxonomy codes)")

    # Extract CV owner's name for target_name identification
    cv_owner_name = extract_cv_owner_name(document_uid, mapped_entries)
    if cv_owner_name.get('last_name'):
        print(f"CV Owner: {cv_owner_name.get('full_name', cv_owner_name['last_name'])} (last name: {cv_owner_name['last_name']})")

    # Location inference runs *after* extraction -- see the call site below.

    # Filter out entries with empty or minimal text
    valid_entries = []
    skipped_entries = []

    for entry in mapped_entries:
        text = entry.get("text", "").strip()
        # Skip entries with empty text or less than 5 characters
        if text and len(text) >= 5:
            valid_entries.append(entry)
        else:
            skipped_entries.append({
                **entry,
                "extracted_fields": {},
                "extraction_success": False,
                "extraction_skipped": True,
                "skip_reason": "empty_or_minimal_text" if len(text) < 5 else "empty_text"
            })

    print(f"  - Valid entries (with text): {len(valid_entries)}")
    print(f"  - Skipped entries (empty/minimal text): {len(skipped_entries)}")

    if not valid_entries:
        print("\n⚠ No valid entries to process")
        return {
            "entries": skipped_entries,
            "total_cost": 0.0,
            "total_tokens": 0,
            "success": True
        }

    # Process in batches
    num_batches = (len(valid_entries) + batch_size - 1) // batch_size
    all_entries = []
    total_cost = 0.0
    total_tokens = 0
    total_cache_read_tokens = 0
    total_cache_write_tokens = 0

    for batch_idx in range(num_batches):
        # Check for cancellation before each batch's LLM calls so an aborted
        # run terminates promptly rather than running every batch to completion.
        if cancel_check is not None:
            cancel_check()

        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(valid_entries))
        batch = valid_entries[start_idx:end_idx]

        result = extract_fields_batch(batch, batch_idx, num_batches, model=model, cv_owner_name=cv_owner_name)

        if result.get("success"):
            all_entries.extend(result.get("entries", []))
            total_cost += result.get("cost", 0.0)
            total_tokens += result.get("tokens", 0)
            total_cache_read_tokens += result.get("cache_read_tokens", 0)
            total_cache_write_tokens += result.get("cache_write_tokens", 0)
            # Show running total after each batch
            print(f"  Batch {batch_idx + 1}/{num_batches} complete | Running total: ${total_cost:.4f}")

    # Add back skipped entries
    all_entries.extend(skipped_entries)

    # Sort by original order (element_idx) - convert to int in case values are strings
    def safe_int(val, default=9999):
        try:
            return int(val) if val is not None else default
        except (ValueError, TypeError):
            return default
    all_entries.sort(key=lambda e: (safe_int(e.get("element_idx_start")), safe_int(e.get("element_idx_end"))))

    # Fallback: Add target_name via regex matching if LLM didn't extract it
    cv_owner_last_name = cv_owner_name.get('last_name', '')
    if cv_owner_last_name:
        all_entries = add_target_names(all_entries, cv_owner_last_name)
        pub_with_target = sum(1 for e in all_entries if e.get('extracted_fields', {}).get('target_name'))
        if pub_with_target > 0:
            print(f"\n✓ target_name identified in {pub_with_target} publication/presentation entries")

    # Count entries with reformatted fields
    reformatted_count = sum(1 for e in all_entries if e.get('reformatted_fields'))
    if reformatted_count > 0:
        print(f"✓ Applied reformatting to {reformatted_count} entries")

    # Infer CV owner's current location(s) for geographic scope classification.
    # This runs after extraction, not before it: the affiliation fallback reads
    # named fields (employer/institution/organization/address), and stage 3b
    # emits no extracted_fields at all -- every entry arrives here with the key
    # absent. Inferring before extraction made that fallback dead code in every
    # real run while still looking correct when replayed over a saved
    # *_fields.json. Nothing in the extraction loop consumes the result; it is
    # carried in the return value for downstream geographic-scope use.
    cv_owner_location = infer_cv_owner_location(all_entries)
    if cv_owner_location.get('inference_success'):
        metro = cv_owner_location.get('metro_area', '')
        primary = cv_owner_location.get('primary_location', {})
        if primary:
            loc_str = f"{primary.get('institution', '')} in {primary.get('city', '')}, {primary.get('state', '')}"
            print(f"CV Location: {loc_str} (metro: {metro})")
            if cv_owner_location.get('cost'):
                print(f"  Location inference cost: ${cv_owner_location['cost']:.4f}")
    else:
        cv_owner_location = None  # Set to None if inference failed

    # Include location inference cost in total
    location_cost = cv_owner_location.get('cost', 0) if cv_owner_location else 0
    location_tokens = cv_owner_location.get('tokens', 0) if cv_owner_location else 0

    return {
        "entries": all_entries,
        "cv_owner": cv_owner_name,  # Include CV owner info in output
        "cv_owner_location": cv_owner_location,  # Include location context for geographic scope
        "total_cost": total_cost + location_cost,
        "total_tokens": total_tokens + location_tokens,
        "cache_read_tokens": total_cache_read_tokens,
        "cache_write_tokens": total_cache_write_tokens,
        "stats": {
            "total_entries": len(all_entries),
            "extracted": len(valid_entries),
            "skipped": len(skipped_entries),
            "batches_processed": num_batches,
            "entries_reformatted": reformatted_count,
            "cache_read_tokens": total_cache_read_tokens,
            "cache_write_tokens": total_cache_write_tokens
        },
        "success": True
    }


def process_cv(
    docx_path: str,
    model: str = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """
    Main pipeline: Load Stage 3b classified entries and extract fields.

    Args:
        docx_path: Path to the CV document (or just the document UID)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        cancel_check: Optional zero-arg callable threaded into the per-batch
            extraction loop. It should raise to abort the run (the web
            orchestrator passes its check_cancelled, which raises
            CancelledException). This stage is the heaviest -- ~130 LLM calls
            spread across batches -- so an intra-stage check is what lets a
            cancel land mid-stage instead of after the last batch. None (the
            standalone CLI default) is a no-op, leaving CLI behavior unchanged.
    """
    # Derive UIDs
    filename = Path(docx_path).stem
    document_uid = filename

    # Try Stage 3b output first (new format), fall back to Stage 3 (legacy)
    stage3b_path = Path(__file__).parent / "outputs" / "stage_3b_classified_entries" / f"{document_uid}_classified.json"
    stage3_path = Path(__file__).parent / "outputs" / "stage_3_taxonomy_mapping" / f"{document_uid}_mapped.json"

    if stage3b_path.exists():
        print(f"\n Loading Stage 3b output: {stage3b_path.name}")
        with open(stage3b_path, "r") as f:
            stage_data = json.load(f)
        # Stage 3b uses "entries" key
        mapped_entries = stage_data.get("entries", [])
    elif stage3_path.exists():
        print(f"\n Loading Stage 3 output (legacy): {stage3_path.name}")
        with open(stage3_path, "r") as f:
            stage_data = json.load(f)
        # Legacy Stage 3 uses "mapped_entries" key
        mapped_entries = stage_data.get("mapped_entries", [])
    else:
        raise FileNotFoundError(
            f"No Stage 3b or Stage 3 output found for {document_uid}.\n"
            f"  Tried: {stage3b_path}\n"
            f"  Tried: {stage3_path}"
        )

    # Filter out fragments and duplicates (they don't need field extraction)
    valid_entries = [
        e for e in mapped_entries
        if not e.get("is_fragment") and not e.get("is_duplicate")
    ]
    fragment_count = len([e for e in mapped_entries if e.get("is_fragment")])
    duplicate_count = len([e for e in mapped_entries if e.get("is_duplicate")])

    print(f"  Total entries from Stage 3b: {len(mapped_entries)}")
    print(f"  - Fragments (skipped): {fragment_count}")
    print(f"  - Duplicates (skipped): {duplicate_count}")
    print(f"  - Valid for extraction: {len(valid_entries)}")

    # Extract fields
    result = extract_fields_from_mapped_entries(
        valid_entries,
        batch_size=10,
        model=model,
        document_uid=document_uid,
        cancel_check=cancel_check,
    )

    # Build output with stage metadata
    output = {
        "document_uid": document_uid,
        "stage": "4",
        "stage_name": "Field Extraction",
        "source_stage": "3b",
        "cv_owner": result.get("cv_owner"),  # Include CV owner info
        "cv_owner_location": result.get("cv_owner_location"),  # Include location for geographic scope
        "total_entries": len(result["entries"]),
        "total_cost": result["total_cost"],
        "total_tokens": result["total_tokens"],
        "cache_read_tokens": result.get("cache_read_tokens", 0),
        "cache_write_tokens": result.get("cache_write_tokens", 0),
        "stats": {
            **result.get("stats", {}),
            "fragments_skipped": fragment_count,
            "duplicates_skipped": duplicate_count,
        },
        "entries": result["entries"]
    }

    # Save output
    output_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_uid}_fields.json"

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*80}")
    print("Stage 4 Complete")
    print(f"{'='*80}")
    print(f"Total entries: {len(result['entries'])}")
    print(f"  - Extracted: {result['stats']['extracted']}")
    print(f"  - Skipped (empty/minimal): {result['stats']['skipped']}")
    print(f"  - Fragments (excluded): {fragment_count}")
    print(f"  - Duplicates (excluded): {duplicate_count}")
    print(f"  - Reformatted: {result['stats'].get('entries_reformatted', 0)}")
    print(f"Total cost: ${result['total_cost']:.4f}")
    print(f"Total tokens: {result['total_tokens']:,}")
    print(f"Output: {output_path}")
    print(f"{'='*80}\n")

    return {
        "output": output,
        "output_path": str(output_path)
    }


def run_validation(output_path: str) -> None:
    """
    Run validation script on the extraction output.

    Args:
        output_path: Path to the Stage 4 output JSON file
    """
    import subprocess

    # Find validation script (project root directory)
    # __file__ is in: src/unified_pipeline/stage_4_field_extractor.py
    # Validator is in: validate_stage4_extraction.py
    validator_path = Path(__file__).parent.parent.parent / "validate_stage4_extraction.py"

    if not validator_path.exists():
        print(f"\n⚠️  Validation script not found: {validator_path}")
        print("   Skipping validation (extraction still successful)")
        return

    print(f"\n{'='*80}")
    print("Running Validation")
    print(f"{'='*80}\n")

    try:
        # Run validation script
        result = subprocess.run(
            [sys.executable, str(validator_path), output_path],
            capture_output=False,  # Show output directly
            text=True
        )

        if result.returncode != 0:
            print(f"\n⚠️  Validation completed with warnings")

    except Exception as e:
        print(f"\n⚠️  Validation error: {e}")
        print("   Extraction still successful")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage_4_field_extractor.py <document_uid_or_path>")
        print("  document_uid_or_path: Either the document UID (e.g., '2005_Bpg')")
        print("                        or path to CV document (e.g., 'path/to/2005_Bpg.docx')")
        print("  (the LLM model is configured in llm_config.yaml)")
        sys.exit(1)

    input_arg = sys.argv[1]

    # Handle both document UID and file path
    # If it's a path to an existing file, use it directly
    # If it's just a UID, use it to look up Stage 3b output
    if os.path.exists(input_arg):
        docx_path = input_arg
    else:
        # Assume it's a document UID - create a fake path (only stem is used)
        docx_path = f"{input_arg}.docx"

    try:
        result = process_cv(docx_path)
        output_path = result["output_path"]

        # Run validation on the output
        run_validation(output_path)

        print("\n Success")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
