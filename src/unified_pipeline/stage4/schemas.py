"""Field schemas, descriptions and taxonomy labels for stage 4 extraction.

Moved verbatim from `stage_4_field_extractor.py` (#498), which re-exports every
public name here. The built-in dicts are the fallback; the versioned config
file takes precedence when present (see `load_field_schemas_from_config`).

`_LOADED_SCHEMAS` is a mutable module-level cache and is deliberately NOT
re-exported by the facade: a re-export would be a stale second binding that
never sees the lazy initialisation (the #496 split-state lesson). Reach it as
`unified_pipeline.stage4.schemas._LOADED_SCHEMAS` or not at all.
"""

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SchemaConfigurationError(Exception):
    """Raised when a field-schema config file is structurally malformed."""


# ============================================================================
# Field Schema Configuration
# ============================================================================

def load_field_schemas_from_config(config_path: str | None = None) -> dict[str, Any] | None:
    """
    Load field schemas from versioned config file.

    Only includes fields where "extract": true in the config.
    This allows us to define optional fields in config but not send them to LLM.

    Args:
        config_path: Path to config file. If None, uses default location.

    Returns:
        Dict mapping taxonomy codes to field lists (only extractable fields),
        or None if the config file does not exist.

    Raises:
        SchemaConfigurationError: the config file exists but is structurally
            malformed (a schema's "fields" value, or an individual field
            entry, is not a dict).
    """
    if config_path is None:
        config_path = FIELD_SCHEMA_CONFIG_PATH

    try:
        with Path(config_path).open("r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.warning("Config file not found at %s, using built-in schemas", config_path)
        return None

    version = config.get("version", "?")
    if version != FIELD_SCHEMA_VERSION:
        logger.warning(
            "Field schema config version mismatch: module expects v%s but config "
            "file %s declares v%s; proceeding with the config file as loaded",
            FIELD_SCHEMA_VERSION, config_path, version,
        )

    # Convert config format to simple {code: {"fields": [...]}} format
    # Only include fields where extract=true
    schemas = {}
    total_fields = 0
    extracted_fields = 0

    for code, schema_data in config.get("schemas", {}).items():
        # Skip non-dict entries (e.g., comment strings like "__NOTE_...")
        if not isinstance(schema_data, dict):
            continue

        fields_config = schema_data.get("fields", {})
        if not isinstance(fields_config, dict):
            raise SchemaConfigurationError(
                f"Schema config for taxonomy code '{code}' has a non-dict 'fields' "
                f"value ({type(fields_config).__name__}): {fields_config!r}"
            )

        extractable_fields = []
        for field_name, field_info in fields_config.items():
            if not isinstance(field_info, dict):
                raise SchemaConfigurationError(
                    f"Schema config for taxonomy code '{code}', field '{field_name}' "
                    f"must be a dict, got {type(field_info).__name__}: {field_info!r}"
                )
            if field_info.get("extract", True):  # Default to True for backwards compatibility
                extractable_fields.append(field_name)

        total_fields += len(fields_config)
        extracted_fields += len(extractable_fields)

        schemas[code] = {
            "fields": extractable_fields,
            "description": schema_data.get("description", ""),
            "wcm_section": schema_data.get("wcm_section"),
            "extraction_mode": schema_data.get("extraction_mode", "minimal")
        }

    logger.info(
        "Loaded field schemas v%s (%d codes, %d/%d fields active)",
        version, len(schemas), extracted_fields, total_fields,
    )
    return schemas

# Schema version info
FIELD_SCHEMA_VERSION = "1.1"
FIELD_SCHEMA_CONFIG_PATH = Path(__file__).parent.parent / "config" / "field_schemas_v1.1.json"

# When the LLM returns 2+ items for ONE entry (a paragraph listing several
# trainees, committees or societies), stage 4 keeps every item, in reply order,
# as a list under this `extracted_fields` key; the entry's own scalar fields
# stay the LAST item, as they were before every item was kept. Not a schema
# field: `stage6/fan_out.py` splits the list into one row per record, and stage
# 6 hands it this name because nothing under `stage6/` may import `stage4`.
STAGE4_RECORDS_KEY = "stage4_records"

# The entry-level count of items the reply held for the entry, written only
# when it is 2 or more, so the multi-record shape stays visible downstream.
STAGE4_RECORDS_RETURNED_KEY = "stage4_records_returned"

# The entry-level count of reply items whose `entry_index` named no entry of
# the entry's taxonomy-code group, so no entry could take them (#1243). Written
# on every entry of that group, and only when the group had 2+ entries: a
# one-entry group folds such items into its entry instead. Write-only: it
# records the loss for the doctor rather than dropping the items silently.
STAGE4_UNPLACED_ITEMS_KEY = "stage4_unplaced_items"

# `<schema field>_<n>`: a numbered second copy of a schema field, which the
# model uses for a second record (`organization_2` held the second column of
# a two-column memberships list, #1245). Stage 4 splits it into its own
# record; the doctor's `offschema_fields` lint reports what is left.
NUMBERED_FIELD_RE = re.compile(r"^(?P<field>.+)_(?P<n>\d+)$")

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
    },

    # -------------------------------------------------------------------------
    # Education (B1, B2)
    # -------------------------------------------------------------------------
    "B1": {  # Academic Degrees
        "fields": ["degree", "institution", "discipline", "year", "thesis_title", "advisor", "narrative"],
    },
    "B2": {  # Other Educational Experiences (certificates, training programs, compliance training)
        "fields": ["program_type", "program_name", "institution", "year", "duration", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Postdoctoral Training (C)
    # -------------------------------------------------------------------------
    "C": {  # Postdoctoral Training (residency, fellowship, postdoc, graduate assistantship)
        "fields": ["training_type", "specialty", "institution", "department", "mentor", "start_date", "end_date", "role", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Positions & Employment (D1, D2, D3)
    # -------------------------------------------------------------------------
    "D1": {  # Academic Appointments (faculty, endowed chairs, emeritus, museum appointments)
        "fields": ["title", "institution", "department", "start_date", "end_date", "track", "tenure_status", "narrative"],
    },
    "D2": {  # Hospital Appointments (attending, consulting, hospitalist)
        "fields": ["title", "institution", "department", "start_date", "end_date", "appointment_type", "narrative"],
    },
    "D3": {  # Other Professional Positions (non-faculty research staff, industry, consulting)
        "fields": ["title", "organization", "department", "start_date", "end_date", "role_type", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Employment Status (E)
    # -------------------------------------------------------------------------
    "E": {  # Employment Status
        "fields": ["status", "fte_percentage", "effective_date", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Credentials (F1, F2)
    # -------------------------------------------------------------------------
    "F1": {  # Licensure
        "fields": ["license_type", "state_country", "license_number", "issue_date", "expiration_date", "status", "narrative"],
    },
    "F2": {  # Board Certification
        "fields": ["specialty", "certifying_board", "year_certified", "recertification_date", "status", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Affiliations & Languages (G)
    # -------------------------------------------------------------------------
    "G": {  # Institutional & Hospital Affiliations
        "fields": ["affiliation_type", "organization", "department", "start_date", "end_date", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Honors & Awards (H)
    # -------------------------------------------------------------------------
    "H": {  # Honors & Awards
        "fields": ["award_name", "granting_body", "date", "amount", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Professional Organizations (I)
    # -------------------------------------------------------------------------
    "I": {  # Professional Organizations & Society Memberships
        "fields": ["organization", "membership_type", "start_date", "end_date", "fellowship_designation", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Percent Effort (J)
    # -------------------------------------------------------------------------
    "J": {  # Percent Effort & Institutional Responsibilities
        "fields": ["clinical_percent", "research_percent", "teaching_percent", "admin_percent", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Teaching & Educational Contributions (K1-K5)
    # -------------------------------------------------------------------------
    "K1": {  # Didactic Teaching
        "fields": ["course_code", "course_title", "institution", "department", "role", "level", "start_date", "end_date", "enrollment", "hours_per_year", "narrative"],
    },
    "K2": {  # Research Mentoring & Clinical Teaching
        "fields": ["teaching_role", "institution", "setting", "learner_level", "start_date", "end_date", "hours_per_week", "description", "narrative"],
    },
    "K3": {  # Educational Program Leadership
        "fields": ["program_name", "role", "institution", "start_date", "end_date", "scope", "description", "narrative"],
    },
    "K4": {  # CME & Professional Education
        "fields": ["activity_title", "institution", "role", "date", "cme_credits", "target_audience", "description", "narrative"],
    },
    "K5": {  # Community Education or Patient Outreach
        "fields": ["activity_title", "audience", "location", "date", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Clinical Activities (L1, L2, L3)
    # -------------------------------------------------------------------------
    "L1": {  # Clinical Practice
        "fields": ["clinical_role", "institution", "service_setting", "fte_clinical", "sessions_per_week", "start_date", "end_date", "description", "narrative"],
    },
    "L2": {  # Clinical Innovations (QI projects)
        "fields": ["project_name", "institution", "role", "start_date", "end_date", "outcome", "description", "narrative"],
    },
    "L3": {  # Clinical Leadership
        "fields": ["leadership_role", "institution", "unit_program", "start_date", "end_date", "scope", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Research & Scholarship (M1, M2, M3, M4)
    # -------------------------------------------------------------------------
    "M1": {  # Research Activities - narrative IS the primary content here
        "fields": ["research_area", "description", "institution", "start_date", "end_date", "narrative"],
    },
    "M2": {  # Research Support (generic grant)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "annual_funding", "percent_effort", "narrative"],
    },
    "M2A": {  # Current Research Funding (active grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "annual_funding", "percent_effort", "status", "notes", "narrative"],
    },
    "M2B": {  # Past Research Funding (completed grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "start_date", "end_date", "total_funding", "percent_effort", "status", "notes", "narrative"],
    },
    "M2C": {  # Pending Research Funding (submitted grants)
        "fields": ["grant_number", "title", "pi_name", "pi_role", "agency", "total_funding_requested", "submission_date", "status", "notes", "narrative"],
    },
    "M2D": {  # Patents & Innovations (formerly M3)
        "fields": ["patent_number", "title", "inventors", "filing_date", "issue_date", "status", "assignee", "narrative"],
    },
    # Clinical trials have no code of their own (#291): they file as M2A (no end
    # date) or M2B (ended), and map onto the grant fields -- NCT/protocol number
    # -> grant_number, sponsor -> agency, trial role -> pi_role, trial title
    # (with its phase) -> title. The mapping is stated once, in stage 4's
    # grant instructions (extraction.build_extraction_prompt).

    # -------------------------------------------------------------------------
    # Mentoring (N1, N2, N3, N4)
    # -------------------------------------------------------------------------
    "N1": {  # Leadership and Mentoring in Programs
        "fields": ["program_name", "role", "institution", "start_date", "end_date", "number_trainees", "narrative"],
    },
    "N2": {  # Institutional Training Grants and Mentored Trainee Grants
        "fields": ["grant_number", "grant_title", "role", "agency", "start_date", "end_date", "trainees_supported", "narrative"],
    },
    "N3": {  # Mentees (generic)
        "fields": ["mentee_name", "mentee_level", "start_date", "end_date", "thesis_title", "current_position", "narrative"],
    },
    "N3A": {  # Current Mentees
        "fields": ["mentee_name", "mentee_level", "program", "start_date", "expected_completion", "research_focus", "narrative"],
    },
    "N3B": {  # Past Mentees
        "fields": ["mentee_name", "mentee_level", "program", "start_date", "end_date", "thesis_title", "current_position", "narrative"],
    },
    "N4": {  # Scholarly Outputs Resulting From Mentorship
        "fields": ["output_type", "mentee_name", "title", "date", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Institutional Leadership (O)
    # -------------------------------------------------------------------------
    "O": {  # Institutional Leadership Activities
        "fields": ["leadership_role", "institution", "division_department", "start_date", "end_date", "budget_authority", "personnel_supervised", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Institutional Administrative Activities (P)
    # -------------------------------------------------------------------------
    "P": {  # Institutional Administrative Activities (committee membership)
        "fields": ["committee_name", "role", "institution", "start_date", "end_date", "description", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Professional Service / Extramural (Q1-Q4)
    # -------------------------------------------------------------------------
    "Q1": {  # Leadership in Extramural Organizations
        "fields": ["role", "organization", "start_date", "end_date", "scope", "narrative"],
    },
    "Q2": {  # Service on External Boards/Committees
        "fields": ["committee_name", "role", "organization", "start_date", "end_date", "narrative"],
    },
    "Q3": {  # Grant Reviewing / Study Sections
        "fields": ["panel_name", "agency", "role", "start_date", "end_date", "review_type", "narrative"],
    },
    "Q4": {  # Editorial Activities (generic)
        "fields": ["role", "journal_name", "start_date", "end_date", "narrative"],
    },
    "Q4A": {  # Editor/Co-Editor
        "fields": ["role", "journal_name", "publisher", "start_date", "end_date", "narrative"],
    },
    "Q4B": {  # Associate/Section Editor
        "fields": ["role", "journal_name", "section", "start_date", "end_date", "narrative"],
    },
    "Q4C": {  # Editorial Board Membership
        "fields": ["journal_name", "start_date", "end_date", "narrative"],
    },
    "Q4D": {  # Journal Reviewing / Ad hoc Reviewing
        "fields": ["journal_name", "year", "number_reviews", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Invited Presentations (R)
    # -------------------------------------------------------------------------
    "R": {  # Invitations to Speak/Present
        "fields": ["title", "event_name", "location", "date", "role", "presentation_type", "host_organization", "authors", "target_name", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Publications (S0-S9)
    # Note: narrative captures meta-statements like "co-authored with my mentee"
    # -------------------------------------------------------------------------
    "S0": {  # Researcher Profile & Bibliometric Summary
        "fields": ["orcid", "google_scholar_url", "scopus_id", "researchgate_url", "h_index", "total_citations", "publication_count", "narrative"],
    },
    "S1": {  # Peer-Reviewed Research Articles
        "fields": ["authors", "year", "title", "journal", "volume", "issue", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
    },
    "S2": {  # Reviews and Editorials
        "fields": ["authors", "year", "title", "journal", "volume", "issue", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
    },
    "S3": {  # Books
        "fields": ["authors", "editors", "year", "title", "publisher", "edition", "isbn", "target_name", "narrative"],
    },
    "S4": {  # Book Chapters
        "fields": ["authors", "year", "chapter_title", "book_title", "editors", "publisher", "pages", "doi", "target_name", "narrative"],
    },
    "S5": {  # Non-peer-reviewed Research Publications
        "fields": ["authors", "year", "title", "publication_venue", "report_number", "url", "target_name", "narrative"],
    },
    "S6": {  # Case Reports
        "fields": ["authors", "year", "title", "journal", "volume", "pages", "doi", "pmid", "pmcid", "target_name", "narrative"],
    },
    "S7": {  # In Review / Submitted / In Preparation
        "fields": ["authors", "year", "title", "status", "target_journal", "target_name", "narrative"],
    },
    "S8": {  # Abstracts & Conference Proceedings
        "fields": ["authors", "year", "title", "conference_name", "location", "abstract_number", "doi", "target_name", "narrative"],
    },
    "S9": {  # Other Media (Podcasts, Blogs, Videos)
        "fields": ["authors", "year", "title", "media_type", "venue", "url", "target_name", "narrative"],
    },

    # -------------------------------------------------------------------------
    # Appendix/Other (T)
    # -------------------------------------------------------------------------
    "T": {  # Appendix/Other (structural elements, references, misc)
        "fields": ["content_type", "description", "narrative"],
    },
}

# ============================================================================
# Field Descriptions for LLM Prompt Guidance
# Shared source of truth used by both primary extraction and recovery pass.
# Each entry maps a taxonomy code to a dict of field_name -> description string.
# ============================================================================
# Grant-record fields whose meaning the prompt pins down (#982). Shared by the
# M2A/M2B/M2C field guides so the three buckets cannot word them differently.
GRANT_STATUS_DESCRIPTION = (
    "Status of the grant exactly as the CV words it (e.g. 'withdrawn', 'not funded', "
    "'under review'), only when the entry states one; null otherwise. Do NOT infer it from dates"
)
GRANT_NOTES_DESCRIPTION = (
    "Any labelled remark on the entry that no other field holds (e.g. text after 'Update:' "
    "or 'Note:'), verbatim; null otherwise. Do NOT repeat the title, amounts or status here"
)

FIELD_DESCRIPTIONS = {
    # A role held during the training ("Chief Resident") survived only in entry
    # text and was dropped from the residency row (#946). The whole C guide is
    # written, not `role` alone: a guide that names one field of five made the
    # model null training_type/specialty/institution on degree lines that the
    # section holds (live A/B, #946).
    "C": {
        "training_type": "The kind of training program (e.g., 'Residency', 'Fellowship', 'Postdoctoral Research Fellow'), not the trainee's position title; for a degree line, the degree",
        "specialty": "The area of training (e.g., 'Emergency Medicine', 'Biostatistics')",
        "institution": "Where the training took place",
        "start_date": "When the training started",
        "end_date": "When the training ended",
        "role": "A distinct role held during the training, worded as the CV words it (e.g., 'Chief Resident', 'Chief Fellow'); an empty string when the text names none, and for the plain trainee title ('Resident Physician', 'Fellow physician'), which training_type already covers",
    },
    # `program_name` fills the template's "Description" column and no other B2
    # field holds detail text, so a title-only extraction drops the rest of the
    # line silently (#1092).
    "B2": {
        "program_name": "The WHOLE entry as the CV words it: its title AND any detail after it (e.g. 'Advanced Life Support: completed the two-day provider course' is all of that, not just 'Advanced Life Support'). Leave out only the institution and dates",
        "institution": "Where it took place",
        "start_date": "When it started",
        "end_date": "When it ended",
    },
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
        "committee_name": "Name of the specific committee ONLY - NOT the parent organization and NOT your role. For a session, panel, symposium or workshop entry, its title",
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
        "role": "The speaker's role or title at the invitation (e.g., 'Visiting Professor', 'Keynote Speaker', 'Panelist'), only when the text states one. Keep the talk's own title in title (do not empty it because a role is present) and leave role empty for a plain 'Invited Speaker'. If the text names the presentation format (e.g., 'Invited Workshop', 'Invited Talk') and no event name, put that format in event_name; never drop it because role is present",
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
        "status": GRANT_STATUS_DESCRIPTION,
        "notes": GRANT_NOTES_DESCRIPTION,
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
        "status": GRANT_STATUS_DESCRIPTION,
        "notes": GRANT_NOTES_DESCRIPTION,
    },
    "M2C": {
        "status": GRANT_STATUS_DESCRIPTION,
        "notes": GRANT_NOTES_DESCRIPTION,
    },
    "D3": {
        "title": "Job title or position. For a consulting engagement that states no job title, the project topic",
        "organization": "Company/organization name with city and state",
        "start_date": "Employment start date (mm/yy format)",
        "end_date": "Employment end date (mm/yy format)",
    },
}

# Fallback for unlisted codes
DEFAULT_SCHEMA = {
    "fields": ["text", "date", "description"],
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
    # NOTE: M4 clinical trial codes removed - clinical trials file as M2A or M2B by end date (#291)
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
_LOADED_SCHEMAS: dict[str, Any] | None = None

def get_active_schemas() -> dict[str, Any]:
    """
    Get the active field schemas: built-in FIELD_SCHEMAS with config-file
    schemas merged over top.

    Config-file schemas (when the config file loads and parses successfully)
    override matching taxonomy codes; codes absent from the config file keep
    their built-in FIELD_SCHEMAS default. Returns a deep copy on every call
    so callers can't mutate the process-wide cache through the return value.
    """
    global _LOADED_SCHEMAS

    if _LOADED_SCHEMAS is None:
        config_schemas = load_field_schemas_from_config()
        merged = copy.deepcopy(FIELD_SCHEMAS)
        if config_schemas:
            merged.update(config_schemas)
        _LOADED_SCHEMAS = merged

    return copy.deepcopy(_LOADED_SCHEMAS)

def get_field_schema(taxonomy_code: str) -> dict[str, Any]:
    """
    Get the field extraction schema for a taxonomy code.

    Prefers schemas from config file, falls back to built-in schemas.
    Unknown codes fall back to DEFAULT_SCHEMA.
    """
    schemas = get_active_schemas()

    if taxonomy_code in schemas:
        return schemas[taxonomy_code]

    return DEFAULT_SCHEMA
