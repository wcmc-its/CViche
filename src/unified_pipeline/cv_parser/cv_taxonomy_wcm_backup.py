"""
CV section taxonomy aligned to Weill Cornell Medical College CV Template (October 2022).

This taxonomy expands from 46 to ~75 nodes with detailed subtypes for CV construction.
Structure matches WCM template ordering and hierarchy exactly.
"""
import re
import logging

logger = logging.getLogger(__name__)

FIELD_DOCS = {
    "id": "A stable machine-readable slug identifying the section. Use this as a unique key for mapping or lookups.",
    "canonical": "The canonical display name of the section as it should appear in structured outputs or UI labels.",
    "aliases": "A list of common alternate names, abbreviations, and short forms used by faculty or institutions (all lowercase). \
Exact or fuzzy matches against these terms should be treated as referring to this section.",
    "parent": "The id of a higher-level grouping (None if this is a root node). Enables hierarchical traversal and specificity ranking.",
    "children": "A list of ids for sub-sections or child categories. Used to ensure mapping prefers more specific leaves when applicable.",
    "approx_location_percentile": "A 0–100 percentile estimate of where the section tends to appear in a typical biomedical CV \
(e.g., Education ~10–20, Publications ~80). Useful for context-aware ordering or validation.",
    "commonness_percentile": "A 0–100 score for how often this section appears across faculty CVs. \
Higher values indicate more universal presence (e.g., Contact Information ≈100, CME Activities ≈35).",
    "specificity_rank": "A relative score (higher = more specific) to help scripts prefer subtypes over broader parents \
(e.g., Book Chapters > Bibliography). Enables disambiguation when multiple matches exist.",
    "priority": "A tie-breaker (0–100) used when two matches have equal specificity. Higher values should win. \
Can encode institutional or heuristic preferences.",
    "patterns": "A list of uncompiled regex-like string patterns for rough keyword detection or fallback matching. \
Integrators can compile these to prefilter candidate headers.",
    "notes": "Optional freeform guidance or comments for developers or data curators. \
May include lineage info or special handling notes.",
    "wcm_section_number": "WCM template section number (e.g., 1, 2, 3...). Used to preserve WCM ordering.",
    "wcm_required": "Boolean indicating if this section is required in WCM template.",
}

CV_SECTIONS = [
    # ========== WCM SECTION 1: PERSONAL DATA / CONTACT INFORMATION ==========
    {
        "id": "contact_information",
        "canonical": "Personal Data / Contact Information",
        "aliases": [
            "personal data", "contact information", "contact", "contact details", "name & contact",
            "profile", "bio", "summary", "overview", "header",
            "header information", "correspondence information", "professional contact",
            "identity and contact", "address • phone • email", "profile & contact",
            "personal information",
            # Expanded variations
            "information contact", "details contact", "contact/information",
            "contact info", "contact data", "name and contact", "contact and name",
            "biographical information", "biographical data", "personal profile",
            "professional profile", "personal details", "details personal",
            "contact and correspondence", "correspondence and contact",
            "contact/correspondence", "identifying information", "identity information"
        ],
        "parent": None,
        "children": ["name", "email_address", "phone_numbers", "physical_address", "professional_identifiers", "web_presence"],
        "approx_location_percentile": 2,
        "commonness_percentile": 100,
        "specificity_rank": 50,
        "priority": 80,
        "patterns": [r"\b(contact|personal|profile|bio|summary)\b"],
        "notes": "WCM Section 1 - Top level container for all contact/personal data",
        "wcm_section_number": 1,
        "wcm_required": True
    },
    {
        "id": "name",
        "canonical": "Name",
        "aliases": [
            "name", "full name", "given name", "surname", "legal name",
            "preferred name", "professional name"
        ],
        "parent": "contact_information",
        "children": [],
        "approx_location_percentile": 1,
        "commonness_percentile": 100,
        "specificity_rank": 80,
        "priority": 95,
        "patterns": [r"\bname\b"],
        "notes": "CV owner's name",
        "wcm_section_number": 1,
        "wcm_required": True
    },
    {
        "id": "email_address",
        "canonical": "Email Address",
        "aliases": [
            "email address", "email", "e-mail", "electronic mail",
            "contact email", "professional email", "work email",
            "institutional email"
        ],
        "parent": "contact_information",
        "children": [],
        "approx_location_percentile": 2,
        "commonness_percentile": 100,
        "specificity_rank": 85,
        "priority": 90,
        "patterns": [r"\be-?mail\b", r"@"],
        "notes": "Email contact",
        "wcm_section_number": 1,
        "wcm_required": True
    },
    {
        "id": "phone_numbers",
        "canonical": "Phone Numbers",
        "aliases": [
            "phone numbers", "phone", "telephone", "tel", "mobile", "cell",
            "office phone", "work phone", "contact number"
        ],
        "parent": "contact_information",
        "children": [],
        "approx_location_percentile": 2,
        "commonness_percentile": 95,
        "specificity_rank": 85,
        "priority": 88,
        "patterns": [r"\b(phone|tel(ephone)?|mobile|cell)\b", r"\d{3}[-.]\d{3}[-.]\d{4}"],
        "notes": "Phone contact numbers",
        "wcm_section_number": 1,
        "wcm_required": False
    },
    {
        "id": "physical_address",
        "canonical": "Physical Address",
        "aliases": [
            "physical address", "address", "mailing address", "postal address",
            "street address", "office address", "work address",
            "institutional address"
        ],
        "parent": "contact_information",
        "children": [],
        "approx_location_percentile": 2,
        "commonness_percentile": 90,
        "specificity_rank": 85,
        "priority": 85,
        "patterns": [r"\baddress\b", r"\bstreet\b", r"\bcity\b", r"\bstate\b", r"\bzip\b"],
        "notes": "Mailing/office address",
        "wcm_section_number": 1,
        "wcm_required": False
    },
    {
        "id": "professional_identifiers",
        "canonical": "Professional Identifiers",
        "aliases": [
            "professional identifiers", "identifiers", "professional ids",
            "credentials", "professional numbers"
        ],
        "parent": "contact_information",
        "children": ["npi_number", "orcid_id"],
        "approx_location_percentile": 3,
        "commonness_percentile": 75,
        "specificity_rank": 70,
        "priority": 75,
        "patterns": [r"\b(npi|orcid|identifier)\b"],
        "notes": "Container for NPI, ORCID, etc.",
        "wcm_section_number": 1,
        "wcm_required": False
    },
    {
        "id": "npi_number",
        "canonical": "NPI Number",
        "aliases": [
            "npi number", "npi", "national provider identifier",
            "provider number", "npi #"
        ],
        "parent": "professional_identifiers",
        "children": [],
        "approx_location_percentile": 3,
        "commonness_percentile": 70,
        "specificity_rank": 90,
        "priority": 80,
        "patterns": [r"\bnpi\b", r"\bnational provider identifier\b"],
        "notes": "National Provider Identifier for medical professionals",
        "wcm_section_number": 1,
        "wcm_required": False
    },
    {
        "id": "orcid_id",
        "canonical": "ORCID iD",
        "aliases": [
            "orcid id", "orcid", "orcid identifier", "orcid number",
            "researcher id", "scholar id"
        ],
        "parent": "professional_identifiers",
        "children": [],
        "approx_location_percentile": 3,
        "commonness_percentile": 60,
        "specificity_rank": 90,
        "priority": 78,
        "patterns": [r"\borcid\b", r"\d{4}-\d{4}-\d{4}-\d{4}"],
        "notes": "Open Researcher and Contributor ID",
        "wcm_section_number": 1,
        "wcm_required": False
    },
    {
        "id": "web_presence",
        "canonical": "Web Presence / Links",
        "aliases": [
            "web presence", "website", "homepage", "personal website",
            "professional website", "web links", "online profiles",
            "linkedin", "research gate", "google scholar", "urls"
        ],
        "parent": "contact_information",
        "children": [],
        "approx_location_percentile": 3,
        "commonness_percentile": 65,
        "specificity_rank": 80,
        "priority": 70,
        "patterns": [r"\b(website|homepage|linkedin|url)\b", r"https?://"],
        "notes": "Personal websites, social media, professional profiles",
        "wcm_section_number": 1,
        "wcm_required": False
    },

    # ========== WCM SECTION 2: EDUCATION ==========
    {
        "id": "education_and_training",
        "canonical": "Education",
        "aliases": [
            "education", "education and training", "training and education",
            "degrees", "educational background", "academic background",
            "professional training", "b. education", "academic training",
            # Gap analysis additions
            "teaching and pedagogy", "teaching_and_pedagogy", "pedagogy",
            # Expanded variations
            "education/training", "training/education", "education & training",
            "training & education", "educational training", "training educational",
            "academic degrees", "degrees and education", "degrees/education",
            "background education", "background educational", "background academic",
            "formal education", "degree programs", "higher education",
            "university education", "college education", "academic credentials",
            "educational credentials", "credentials academic", "educational history"
        ],
        "parent": None,
        "children": ["undergraduate_education", "graduate_education", "doctoral_degree", "medical_degree", "combined_degrees", "certificates_other_degrees"],
        "approx_location_percentile": 12,
        "commonness_percentile": 98,
        "specificity_rank": 60,
        "priority": 85,
        "patterns": [r"\beducation\b", r"\btraining\b", r"\bdegrees?\b"],
        "notes": "WCM Section 2 - CV owner's formal education. NOT research about education.",
        "wcm_section_number": 2,
        "wcm_required": True
    },
    {
        "id": "undergraduate_education",
        "canonical": "Undergraduate Education",
        "aliases": [
            "undergraduate education", "undergraduate degree", "bachelor's degree",
            "bachelors", "ba", "bs", "bsc", "undergraduate training",
            "college education", "baccalaureate"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 13,
        "commonness_percentile": 95,
        "specificity_rank": 85,
        "priority": 88,
        "patterns": [r"\b(undergraduate|bachelor|ba|bs|bsc)\b"],
        "notes": "Undergraduate/Bachelor's degrees",
        "wcm_section_number": 2,
        "wcm_required": False
    },
    {
        "id": "graduate_education",
        "canonical": "Graduate Education",
        "aliases": [
            "graduate education", "graduate degree", "master's degree", "masters",
            "ma", "ms", "msc", "mph", "mba", "graduate training",
            "graduate school", "postgraduate education"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 14,
        "commonness_percentile": 85,
        "specificity_rank": 85,
        "priority": 86,
        "patterns": [r"\b(graduate|master|ma|ms|msc|mph|mba)\b"],
        "notes": "Master's and other graduate degrees",
        "wcm_section_number": 2,
        "wcm_required": False
    },
    {
        "id": "doctoral_degree",
        "canonical": "Doctoral Degree",
        "aliases": [
            "doctoral degree", "doctorate", "phd", "ph.d.", "dphil",
            "doctoral training", "doctoral education", "doctor of philosophy"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 15,
        "commonness_percentile": 90,
        "specificity_rank": 90,
        "priority": 90,
        "patterns": [r"\b(doctoral|phd|ph\.?d\.?|dphil|doctorate)\b"],
        "notes": "PhD and other doctoral degrees",
        "wcm_section_number": 2,
        "wcm_required": False
    },
    {
        "id": "medical_degree",
        "canonical": "Medical Degree",
        "aliases": [
            "medical degree", "md", "m.d.", "mbbs", "mbbch", "doctor of medicine",
            "medical education", "medical school", "medical training"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 15,
        "commonness_percentile": 85,
        "specificity_rank": 90,
        "priority": 92,
        "patterns": [r"\b(md|m\.?d\.?|mbbs|mbbch|medical degree)\b"],
        "notes": "MD and equivalent medical degrees",
        "wcm_section_number": 2,
        "wcm_required": False
    },
    {
        "id": "combined_degrees",
        "canonical": "Combined Degrees",
        "aliases": [
            "combined degrees", "dual degree", "joint degree", "md/phd", "md-phd",
            "md phd", "jd/phd", "dual degrees", "combined degree programs"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 15,
        "commonness_percentile": 40,
        "specificity_rank": 92,
        "priority": 88,
        "patterns": [r"\b(md[/-]?phd|dual degree|joint degree|combined degree)\b"],
        "notes": "MD/PhD and other combined degree programs",
        "wcm_section_number": 2,
        "wcm_required": False
    },
    {
        "id": "certificates_other_degrees",
        "canonical": "Certificates and Other Degrees",
        "aliases": [
            "certificates", "certificate programs", "other degrees",
            "professional certificates", "certification programs",
            "advanced certificates", "diploma programs"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 16,
        "commonness_percentile": 60,
        "specificity_rank": 80,
        "priority": 75,
        "patterns": [r"\b(certificate|diploma|credential)\b"],
        "notes": "Certificate programs and other educational credentials",
        "wcm_section_number": 2,
        "wcm_required": False
    },

    # ========== WCM SECTION 3: POSTDOCTORAL TRAINING (ROOT LEVEL) ==========
    {
        "id": "postdoctoral_training",
        "canonical": "Postdoctoral Training",
        "aliases": [
            "postdoctoral training", "postdoc", "postdoctoral",
            "c. postdoctoral training",
            # Expanded variations
            "training postdoctoral", "postdoc training", "postdoc experience",
            "postdoctoral experience", "post doctoral", "postdoc research",
            "post-doctoral training", "post-doctoral fellowship",
            "postdoctoral researcher", "postgraduate training", "post-graduate training"
        ],
        "parent": None,
        "children": ["postdoctoral_research", "residency_training", "fellowship_training", "internships"],
        "approx_location_percentile": 18,
        "commonness_percentile": 90,
        "specificity_rank": 75,
        "priority": 88,
        "patterns": [r"\b(post-?doc(toral)?)\b"],
        "notes": "WCM Section 3 - ROOT LEVEL (not under Education). Postdoc research positions.",
        "wcm_section_number": 3,
        "wcm_required": False
    },
    {
        "id": "postdoctoral_research",
        "canonical": "Postdoctoral Research Positions",
        "aliases": [
            "postdoctoral research", "postdoc research", "postdoctoral position",
            "postdoc position", "research fellow", "postdoctoral fellow"
        ],
        "parent": "postdoctoral_training",
        "children": [],
        "approx_location_percentile": 18,
        "commonness_percentile": 85,
        "specificity_rank": 90,
        "priority": 85,
        "patterns": [r"\bpostdoc(toral)? (research|position|fellow)\b"],
        "notes": "Research-focused postdoctoral positions",
        "wcm_section_number": 3,
        "wcm_required": False
    },
    {
        "id": "residency_training",
        "canonical": "Residency Training",
        "aliases": [
            "residency training", "residency", "residencies", "medical residency",
            "clinical residency", "resident training", "house staff training",
            "residency programs"
        ],
        "parent": "postdoctoral_training",
        "children": [],
        "approx_location_percentile": 19,
        "commonness_percentile": 80,
        "specificity_rank": 90,
        "priority": 90,
        "patterns": [r"\bresiden(cy|t|cies)\b"],
        "notes": "Medical residency training",
        "wcm_section_number": 3,
        "wcm_required": False
    },
    {
        "id": "fellowship_training",
        "canonical": "Fellowship Training",
        "aliases": [
            "fellowship training", "fellowship", "fellowships", "clinical fellowship",
            "medical fellowship", "advanced fellowship", "fellowship programs",
            "research fellowship"
        ],
        "parent": "postdoctoral_training",
        "children": [],
        "approx_location_percentile": 19,
        "commonness_percentile": 85,
        "specificity_rank": 90,
        "priority": 88,
        "patterns": [r"\bfellow(ship)?s?\b"],
        "notes": "Clinical and research fellowships",
        "wcm_section_number": 3,
        "wcm_required": False
    },
    {
        "id": "internships",
        "canonical": "Internships",
        "aliases": [
            "internships", "internship", "intern", "clinical internship",
            "medical internship", "rotating internship"
        ],
        "parent": "postdoctoral_training",
        "children": [],
        "approx_location_percentile": 19,
        "commonness_percentile": 60,
        "specificity_rank": 88,
        "priority": 82,
        "patterns": [r"\bintern(ship)?s?\b"],
        "notes": "Medical internships",
        "wcm_section_number": 3,
        "wcm_required": False
    },

    # ========== WCM SECTION 4: PROFESSIONAL POSITIONS & EMPLOYMENT ==========
    {
        "id": "professional_positions_employment",
        "canonical": "Professional Positions & Employment",
        "aliases": [
            "professional positions & employment", "academic positions and appointments",
            "positions", "appointments", "employment", "experience",
            "academic appointments", "academic positions", "faculty appointments",
            "professional positions", "employment history",
            "d. professional positions & employment", "professional appointments",
            "positions and employment",
            # Expanded variations
            "employment and positions", "positions/employment", "employment/positions",
            "positions & employment", "employment & positions", "professional experience",
            "academic experience", "appointments and positions", "positions/appointments",
            "appointments & positions", "career positions", "positions professional",
            "employment professional", "faculty positions", "positions faculty",
            "appointments academic", "appointments faculty", "work experience",
            "professional history", "career history", "employment record"
        ],
        "parent": None,
        "children": ["academic_positions", "clinical_positions", "research_positions", "administrative_positions"],
        "approx_location_percentile": 24,
        "commonness_percentile": 95,
        "specificity_rank": 60,
        "priority": 80,
        "patterns": [r"\b(positions?|appointments?|employment|experience)\b"],
        "notes": "WCM Section 4 - Professional employment history",
        "wcm_section_number": 4,
        "wcm_required": True
    },
    {
        "id": "academic_positions",
        "canonical": "Academic Positions",
        "aliases": [
            "academic positions", "faculty positions", "academic appointments",
            "professor", "assistant professor", "associate professor", "full professor",
            "instructor", "lecturer", "adjunct", "tenure track", "tenured"
        ],
        "parent": "professional_positions_employment",
        "children": [],
        "approx_location_percentile": 24,
        "commonness_percentile": 90,
        "specificity_rank": 85,
        "priority": 90,
        "patterns": [r"\b(professor|instructor|lecturer|faculty)\b"],
        "notes": "Academic faculty positions and ranks",
        "wcm_section_number": 4,
        "wcm_required": False
    },
    {
        "id": "clinical_positions",
        "canonical": "Clinical Positions",
        "aliases": [
            "clinical positions", "clinical appointments", "attending physician",
            "clinical faculty", "clinician", "physician", "clinical practice"
        ],
        "parent": "professional_positions_employment",
        "children": [],
        "approx_location_percentile": 25,
        "commonness_percentile": 75,
        "specificity_rank": 85,
        "priority": 85,
        "patterns": [r"\b(clinical|attending|physician|clinician)\b"],
        "notes": "Clinical practice positions",
        "wcm_section_number": 4,
        "wcm_required": False
    },
    {
        "id": "research_positions",
        "canonical": "Research Positions",
        "aliases": [
            "research positions", "research appointments", "research scientist",
            "research associate", "research faculty", "principal investigator",
            "senior scientist"
        ],
        "parent": "professional_positions_employment",
        "children": [],
        "approx_location_percentile": 25,
        "commonness_percentile": 70,
        "specificity_rank": 85,
        "priority": 83,
        "patterns": [r"\b(research (scientist|associate|faculty)|principal investigator)\b"],
        "notes": "Research-focused positions",
        "wcm_section_number": 4,
        "wcm_required": False
    },
    {
        "id": "administrative_positions",
        "canonical": "Administrative Positions",
        "aliases": [
            "administrative positions", "administrative appointments",
            "director", "chief", "chair", "dean", "vice chair",
            "program director", "department chair"
        ],
        "parent": "professional_positions_employment",
        "children": [],
        "approx_location_percentile": 25,
        "commonness_percentile": 60,
        "specificity_rank": 85,
        "priority": 80,
        "patterns": [r"\b(director|chief|chair|dean)\b"],
        "notes": "Administrative leadership positions",
        "wcm_section_number": 4,
        "wcm_required": False
    },

    # ========== WCM SECTION 5: EMPLOYMENT STATUS ==========
    {
        "id": "employment_status",
        "canonical": "Employment Status",
        "aliases": [
            "employment status", "status", "appointment type", "full-time/part-time status",
            "primary appointment status", "g. employment status",
            # Expanded variations
            "status employment", "status appointment", "appointment/employment status",
            "employment & appointment status", "full-time status", "part-time status",
            "ft/pt status", "employment type", "type of appointment", "type of employment",
            "current status", "primary status", "status primary", "appointment classification",
            "employment classification", "work status", "professional status"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 30,
        "commonness_percentile": 45,
        "specificity_rank": 65,
        "priority": 60,
        "patterns": [r"\b(status|appointment type)\b"],
        "notes": "WCM Section 5 - Full-time/part-time status",
        "wcm_section_number": 5,
        "wcm_required": False
    },

    # ========== WCM SECTION 6: LICENSURE, BOARD CERTIFICATION ==========
    {
        "id": "licensure_and_certification",
        "canonical": "Licensure and Certification",
        "aliases": [
            "licensure and certification", "licensure", "certification", "licenses",
            "professional licenses", "medical licensure", "licensure & registration",
            "state licensure", "e. licensure, board certification",
            "certification and licensure", "licensing & certification",
            # Expanded variations
            "certification and licensure", "licensure/certification", "certification/licensure",
            "licensure & certification", "certification & licensure", "licenses and certifications",
            "certifications and licenses", "licensing and certification", "certification and licensing",
            "professional licensure", "professional certification", "medical licenses",
            "medical certifications", "state certification", "registration and licensure",
            "licensure and registration", "credentials and licensure", "license and certification"
        ],
        "parent": None,
        "children": ["board_certification"],
        "approx_location_percentile": 32,
        "commonness_percentile": 85,
        "specificity_rank": 60,
        "priority": 80,
        "patterns": [r"\b(licen[sc]e|licen[sc]ure|certif(ication|ications))\b"],
        "notes": "WCM Section 6 - Professional licenses and board certification",
        "wcm_section_number": 6,
        "wcm_required": True
    },
    {
        "id": "board_certification",
        "canonical": "Board Certification",
        "aliases": [
            "board certification", "boards", "certifications", "medical board certifications",
            "specialty board certification", "subspecialty certification", "abms certification",
            "e. licensure, board certification (boards)", "registrations & certification",
            # Expanded variations
            "certification board", "board certifications", "board/specialty certification",
            "board & specialty certification", "medical boards", "specialty boards",
            "board eligible", "board certified", "abms boards", "certification specialty",
            "subspecialty boards", "certification subspecialty", "specialty certification",
            "board status", "certification status board", "medical board status"
        ],
        "parent": "licensure_and_certification",
        "children": [],
        "approx_location_percentile": 35,
        "commonness_percentile": 70,
        "specificity_rank": 80,
        "priority": 85,
        "patterns": [r"\b(board|abms).*(cert)"],
        "notes": "Medical specialty board certification",
        "wcm_section_number": 6,
        "wcm_required": False
    },

    # ========== WCM SECTION 7: INSTITUTIONAL / HOSPITAL AFFILIATION ==========
    {
        "id": "institutional_hospital_affiliation",
        "canonical": "Institutional / Hospital Affiliation",
        "aliases": [
            "institutional / hospital affiliation", "affiliations", "hospital",
            "hospital appointments", "clinical positions", "hospital privileges",
            "affiliated hospitals", "clinical appointments",
            "f. institutional / hospital affiliation",
            "hospital staff appointments", "medical staff appointments",
            # Expanded variations
            "affiliation institutional", "affiliation hospital", "hospital/institutional affiliation",
            "institutional/hospital affiliation", "hospital & institutional affiliation",
            "institutional & hospital affiliation", "appointments hospital", "hospital staff",
            "staff appointments", "medical staff", "privileges hospital", "clinical affiliation",
            "hospital affiliation", "institutional affiliation", "affiliated institutions",
            "hospital memberships", "institutional memberships", "hospital association",
            "institutional positions"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 28,
        "commonness_percentile": 75,
        "specificity_rank": 65,
        "priority": 70,
        "patterns": [r"\b(hospital|medical)\s+(staff\s+)?appointments?\b", r"\b(hospital|affiliation|privileges?)\b"],
        "notes": "WCM Section 7 - Hospital staff appointments and privileges",
        "wcm_section_number": 7,
        "wcm_required": False
    },

    # ========== WCM SECTION 8: HONORS, AWARDS ==========
    {
        "id": "honors_and_awards",
        "canonical": "Honors and Awards",
        "aliases": [
            "honors and awards", "honors", "awards", "recognition", "distinctions",
            "prizes", "merit awards", "academic honors", "h. honors, awards",
            "professional awards and honors", "awards and honors",
            # Expanded variations
            "awards and honors", "honors/awards", "awards/honors", "honors & awards",
            "awards & honors", "recognitions and honors", "honors and recognitions",
            "distinctions and awards", "awards and distinctions", "honors received",
            "awards received", "professional honors", "academic awards", "merit recognition",
            "prizes and awards", "fellowships and awards", "honors and fellowships"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 40,
        "commonness_percentile": 80,
        "specificity_rank": 55,
        "priority": 70,
        "patterns": [r"\b(honou?rs?|awards?|distinctions?)\b"],
        "notes": "WCM Section 8 - Academic and professional honors",
        "wcm_section_number": 8,
        "wcm_required": False
    },

    # ========== WCM SECTION 9: PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS ==========
    {
        "id": "professional_orgs_societies",
        "canonical": "Professional Organizations and Society Memberships",
        "aliases": [
            "professional organizations and society memberships", "memberships",
            "organizations", "societies", "professional affiliations",
            "scientific societies", "medical societies", "associations",
            "i. professional organizations and society memberships",
            "membership in professional organizations", "professional societies",
            "membership in organizations",
            "memberships in scholarly and professional societies",
            "memberships in professional societies",
            # Gap analysis additions
            "professional memberships", "professional_memberships", "society memberships",
            # Expanded variations
            "organizations and societies", "societies and organizations",
            "organizations/societies", "societies/organizations", "organizations & societies",
            "societies & organizations", "professional organizations", "scholarly societies",
            "memberships professional", "memberships society", "society affiliations",
            "organizational memberships", "academic societies", "learned societies",
            "society membership", "organization membership", "professional associations"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 45,
        "commonness_percentile": 85,
        "specificity_rank": 55,
        "priority": 70,
        "patterns": [r"\bmemberships?\s+(in\s+)?(professional\s+)?(organizations?|societies)\b", r"\b(membership|societ(y|ies)|association|affiliation)s?\b"],
        "notes": "WCM Section 9 - Professional society memberships",
        "wcm_section_number": 9,
        "wcm_required": False
    },

    # ========== WCM SECTION 10: PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES ==========
    {
        "id": "percent_effort_responsibilities",
        "canonical": "Percent Effort and Institutional Responsibilities",
        "aliases": [
            "percent effort and institutional responsibilities", "responsibilities", "effort",
            "effort allocation", "percent effort by mission", "assigned responsibilities",
            "j. percent effort and institutional responsibilities",
            # Expanded variations
            "effort and responsibilities", "responsibilities and effort", "effort/responsibilities",
            "responsibilities/effort", "effort & responsibilities", "responsibilities & effort",
            "percent effort", "effort percentages", "allocation of effort", "effort distribution",
            "institutional effort", "effort institutional", "responsibilities institutional",
            "assigned effort", "effort assignment", "time allocation", "workload distribution"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 47,
        "commonness_percentile": 35,
        "specificity_rank": 65,
        "priority": 60,
        "patterns": [r"\b(percent|%|time)\s*effort\b", r"\bresponsibilit(y|ies)\b"],
        "notes": "WCM Section 10 - Effort allocation by mission area",
        "wcm_section_number": 10,
        "wcm_required": False
    },

    # ========== WCM SECTION 11: EDUCATIONAL CONTRIBUTIONS ==========
    {
        "id": "educational_contributions",
        "canonical": "Educational Contributions",
        "aliases": [
            "educational contributions", "teaching", "instruction", "courses",
            "teaching activities", "teaching experience", "educational activities",
            "course directorships", "curriculum contributions",
            "k. educational contributions",
            "teaching experience and responsibilities", "courses taught",
            # Gap analysis additions
            "teaching and pedagogy", "teaching_and_pedagogy", "pedagogy",
            # Expanded variations
            "contributions educational", "teaching contributions", "contributions teaching",
            "educational/teaching contributions", "teaching/educational contributions",
            "educational & teaching contributions", "teaching & educational contributions",
            "teaching responsibilities", "instructional contributions", "curriculum development",
            "course teaching", "teaching courses", "educational work", "teaching duties",
            "instruction and teaching", "teaching and instruction", "academic teaching"
        ],
        "parent": None,
        "children": ["didactic_teaching", "continuing_medical_education"],
        "approx_location_percentile": 60,
        "commonness_percentile": 85,
        "specificity_rank": 65,
        "priority": 75,
        "patterns": [r"\b(teaching|instruction|courses?)\b"],
        "notes": "WCM Section 11 - Teaching and educational activities",
        "wcm_section_number": 11,
        "wcm_required": False
    },
    {
        "id": "didactic_teaching",
        "canonical": "Didactic Teaching",
        "aliases": [
            "didactic teaching", "didactic", "classroom teaching", "lecture teaching",
            "formal teaching", "course instruction", "lectures"
        ],
        "parent": "educational_contributions",
        "children": [],
        "approx_location_percentile": 61,
        "commonness_percentile": 75,
        "specificity_rank": 80,
        "priority": 78,
        "patterns": [r"\b(didactic|lecture|classroom)\s+(teaching|instruction)\b"],
        "notes": "Formal didactic teaching activities",
        "wcm_section_number": 11,
        "wcm_required": False
    },
    {
        "id": "continuing_medical_education",
        "canonical": "Continuing Medical Education",
        "aliases": [
            "continuing medical education", "cme", "cme activities", "cme teaching",
            "continuing education", "cpd", "continuing professional development",
            "cme course leadership", "professional education (cme)",
            # Expanded variations
            "cme activities", "activities cme", "cme/cpd activities", "cpd/cme activities",
            "cme & cpd", "cpd & cme", "medical education continuing", "cme courses",
            "cme programming", "cme instruction", "cme participation", "cpd activities",
            "medical continuing education", "continuing medical ed", "cme leadership"
        ],
        "parent": "educational_contributions",
        "children": [],
        "approx_location_percentile": 62,
        "commonness_percentile": 60,
        "specificity_rank": 85,
        "priority": 75,
        "patterns": [r"\b(cme|cpd)\b", r"\bcontinuing\s+(medical|professional)\s+education\b"],
        "notes": "CME/CPD activities and teaching",
        "wcm_section_number": 11,
        "wcm_required": False
    },

    # ========== WCM SECTION 12: CLINICAL PRACTICE, INNOVATION, AND LEADERSHIP ==========
    {
        "id": "clinical_practice_innovation_leadership",
        "canonical": "Clinical Practice, Innovation, and Leadership",
        "aliases": [
            "clinical practice, innovation, and leadership", "clinical", "practice",
            "innovation", "leadership", "clinical expertise and interests",
            "quality improvement", "clinical innovations",
            "clinical leadership roles", "l. clinical practice, innovation, and leadership",
            # Gap analysis additions
            "clinical experience", "clinical_experience",
            # Expanded variations
            "clinical practice and innovation", "innovation and clinical practice",
            "clinical/innovation/leadership",
            "practice and innovation", "clinical practice & innovation",
            "innovation & clinical practice",
            "clinical leadership", "practice leadership", "innovation leadership",
            "clinical work", "clinical activities", "practice activities",
            "innovation activities", "clinical care",
            "clinical services", "clinical expertise", "innovative practice",
            "practice innovation"
        ],
        "parent": None,
        "children": ["clinical_practice", "clinical_innovations", "clinical_leadership"],
        "approx_location_percentile": 56,
        "commonness_percentile": 70,
        "specificity_rank": 60,
        "priority": 70,
        "patterns": [r"\b(clinical|qi|quality improvement|innovation)\b"],
        "notes": "WCM Section 12 - Clinical practice, QI, and leadership",
        "wcm_section_number": 12,
        "wcm_required": False
    },
    {
        "id": "clinical_practice",
        "canonical": "Clinical Practice",
        "aliases": [
            "clinical practice", "clinical work", "clinical care", "patient care",
            "clinical services", "clinical activities", "medical practice"
        ],
        "parent": "clinical_practice_innovation_leadership",
        "children": [],
        "approx_location_percentile": 57,
        "commonness_percentile": 75,
        "specificity_rank": 80,
        "priority": 75,
        "patterns": [r"\bclinical\s+(practice|care|work)\b"],
        "notes": "Direct clinical practice activities",
        "wcm_section_number": 12,
        "wcm_required": False
    },
    {
        "id": "clinical_innovations",
        "canonical": "Clinical Innovations",
        "aliases": [
            "clinical innovations", "clinical innovation projects",
            "quality improvement", "qi", "quality improvement projects",
            "clinical process improvement", "patient safety & qi",
            "outcomes improvement", "care delivery innovation", "quality initiatives",
            "practice redesign", "qi/qa projects", "implementation projects",
            # Expanded variations
            "quality improvement projects", "qi projects", "clinical quality improvement",
            "improvement projects", "quality/improvement projects", "qi & qa projects",
            "innovation projects", "projects quality improvement", "clinical qi",
            "qi initiatives", "qa projects", "quality assurance projects",
            "improvement initiatives",
            "patient safety projects", "safety and quality", "process improvement projects"
        ],
        "parent": "clinical_practice_innovation_leadership",
        "children": [],
        "approx_location_percentile": 66,
        "commonness_percentile": 55,
        "specificity_rank": 85,
        "priority": 80,
        "patterns": [r"\b(qi|quality\s*improvement|process improvement|innovation)\b"],
        "notes": "Quality improvement and innovation projects",
        "wcm_section_number": 12,
        "wcm_required": False
    },
    {
        "id": "clinical_leadership",
        "canonical": "Clinical Leadership",
        "aliases": [
            "clinical leadership", "clinical leadership roles",
            "medical director", "clinical director", "service chief",
            "division chief", "clinical program leadership"
        ],
        "parent": "clinical_practice_innovation_leadership",
        "children": [],
        "approx_location_percentile": 57,
        "commonness_percentile": 60,
        "specificity_rank": 85,
        "priority": 78,
        "patterns": [r"\bclinical\s+(leadership|director|chief)\b"],
        "notes": "Clinical leadership positions",
        "wcm_section_number": 12,
        "wcm_required": False
    },

    # ========== WCM SECTION 13: RESEARCH ==========
    {
        "id": "research_overview",
        "canonical": "Research",
        "aliases": [
            "research", "research overview", "research interests", "research statement",
            "scholarship", "m. research (overview)",
            # Gap analysis additions
            "research program overview", "research_program_overview", "research program",
            "research contributions",
            # Expanded variations
            "overview research", "research summary", "research background",
            "research/scholarship",
            "research & scholarship", "research interests and overview",
            "interests research",
            "research focus", "research areas", "research activities", "scholarly research",
            "research agenda", "research profile", "statement research"
        ],
        "parent": None,
        "children": ["grant_support_funding", "patents_inventions", "clinical_trials"],
        "approx_location_percentile": 52,
        "commonness_percentile": 75,
        "specificity_rank": 55,
        "priority": 65,
        "patterns": [r"\bresearch( interests| statement)?\b", r"\bscholarship\b"],
        "notes": "WCM Section 13 - Research activities and funding",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "grant_support_funding",
        "canonical": "Grant Support and Funding",
        "aliases": [
            "grant support and funding", "funding", "grants", "support",
            "research support",
            "grants and contracts", "sponsored research", "current & prior support",
            "funding history",
            "extramural funding", "external funding", "grant funding",
            "m. research (funding)", "current funding", "past funding",
            "grants & contract awards",
            "ongoing research support", "completed research support", "current grants",
            # Expanded variations
            "support grant", "grant/funding support", "funding and grants",
            "grants/contracts",
            "grants & funding", "funding & grants", "research funding", "funding research",
            "grant awards", "funding awards", "sponsored projects", "research grants",
            "active grants", "completed grants", "grant history", "funding sources",
            "extramural support", "external grants", "grant portfolio"
        ],
        "parent": "research_overview",
        "children": ["current_funding", "past_funding", "pending_funding"],
        "approx_location_percentile": 58,
        "commonness_percentile": 75,
        "specificity_rank": 75,
        "priority": 80,
        "patterns": [r"\b(grants?|funding|sponsored research|contracts?|extramural)\b"],
        "notes": "Research grants and funding support",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "current_funding",
        "canonical": "Current Funding",
        "aliases": [
            "current funding", "active funding", "current grants", "active grants",
            "ongoing funding", "current support", "active support"
        ],
        "parent": "grant_support_funding",
        "children": [],
        "approx_location_percentile": 58,
        "commonness_percentile": 75,
        "specificity_rank": 90,
        "priority": 85,
        "patterns": [r"\b(current|active|ongoing)\s+(funding|grants|support)\b"],
        "notes": "Currently active grants and funding",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "past_funding",
        "canonical": "Past Funding",
        "aliases": [
            "past funding", "completed funding", "prior funding", "past grants",
            "completed grants", "prior support", "past support", "completed support"
        ],
        "parent": "grant_support_funding",
        "children": [],
        "approx_location_percentile": 58,
        "commonness_percentile": 70,
        "specificity_rank": 90,
        "priority": 83,
        "patterns": [r"\b(past|completed|prior)\s+(funding|grants|support)\b"],
        "notes": "Previously completed grants",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "pending_funding",
        "canonical": "Pending Funding",
        "aliases": [
            "pending funding", "pending grants", "submitted grants", "under review grants",
            "pending support", "submitted funding", "funding under review"
        ],
        "parent": "grant_support_funding",
        "children": [],
        "approx_location_percentile": 58,
        "commonness_percentile": 55,
        "specificity_rank": 90,
        "priority": 80,
        "patterns": [r"\b(pending|submitted|under review)\s+(funding|grants|support)\b"],
        "notes": "Pending or submitted grant applications",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "patents_inventions",
        "canonical": "Patents and Inventions",
        "aliases": [
            "patents and inventions", "patents", "inventions",
            "patent applications", "patents applied-for",
            "intellectual property", "patents filed", "patent portfolio",
            "patents issued", "patents pending", "patents applied for",
            # Expanded variations
            "inventions and patents", "patent/applications", "applications patents",
            "patents & applications",
            "applications & patents", "patent filings", "filed patents", "issued patents",
            "pending patents", "patent holdings", "patent work",
            "intellectual property patents",
            "ip portfolio", "patent inventions", "inventions patented", "patent disclosures"
        ],
        "parent": "research_overview",
        "children": [],
        "approx_location_percentile": 59,
        "commonness_percentile": 35,
        "specificity_rank": 85,
        "priority": 75,
        "patterns": [r"\bpatents?\b", r"\bpatent\s+(applications?|applied-?for|filed|issued|pending)\b", r"\bintellectual\s+property\b"],
        "notes": "Patents, inventions, and IP - MOVED UNDER RESEARCH per WCM template",
        "wcm_section_number": 13,
        "wcm_required": False
    },
    {
        "id": "clinical_trials",
        "canonical": "Clinical Trials",
        "aliases": [
            "clinical trials", "clinical trials (pi or co-i roles)",
            "clinical research trials", "investigator roles in trials",
            "trial portfolio", "industry-sponsored trials", "nih/federal trials",
            "clinical studies",
            "trial leadership", "clinical trial participation", "regulatory trials",
            "translational trials",
            # Expanded variations
            "trials clinical", "clinical trial research", "research trials",
            "trials/studies",
            "clinical trials & studies", "trials and studies", "investigator trials",
            "pi trials", "co-i trials", "trial participation", "clinical research studies",
            "human subjects research", "trial investigator", "clinical investigation",
            "sponsored trials", "trial roles", "clinical trial work"
        ],
        "parent": "research_overview",
        "children": [],
        "approx_location_percentile": 59,
        "commonness_percentile": 50,
        "specificity_rank": 85,
        "priority": 78,
        "patterns": [r"\b(clinical\s+trials?|trial(s)?|pi|co-?i)\b"],
        "notes": "Clinical trials with PI or Co-I roles",
        "wcm_section_number": 13,
        "wcm_required": False
    },

    # ========== WCM SECTION 14: MENTORING ==========
    {
        "id": "mentoring",
        "canonical": "Mentoring",
        "aliases": [
            "mentoring", "mentoring and supervision", "mentorship", "advising",
            "supervision",
            "supervision of trainees", "graduate/residency mentoring",
            "mentees and advisees",
            "n. mentoring",
            # Expanded variations
            "supervision and mentoring", "mentoring/supervision", "supervision/mentoring",
            "mentoring & supervision", "supervision & mentoring", "mentorship and advising",
            "advising and mentorship", "trainee supervision", "student mentoring",
            "mentoring activities", "supervisory activities", "advising activities",
            "mentees supervised", "advisees and mentees", "mentoring trainees",
            "supervision trainees", "graduate mentoring", "postdoc mentoring"
        ],
        "parent": None,
        "children": ["mentoring_programs", "training_grants", "current_mentees", "past_mentees"],
        "approx_location_percentile": 62,
        "commonness_percentile": 80,
        "specificity_rank": 70,
        "priority": 75,
        "patterns": [r"\b(mentor(ing|ship)?|advis(ing|ors?)|supervis(ion|e|or))\b"],
        "notes": "WCM Section 14 - Mentoring and trainee supervision",
        "wcm_section_number": 14,
        "wcm_required": False
    },
    {
        "id": "mentoring_programs",
        "canonical": "Mentoring Programs",
        "aliases": [
            "mentoring programs", "formal mentoring programs",
            "mentorship programs", "advising programs",
            "trainee programs", "mentoring initiatives"
        ],
        "parent": "mentoring",
        "children": [],
        "approx_location_percentile": 62,
        "commonness_percentile": 60,
        "specificity_rank": 85,
        "priority": 75,
        "patterns": [r"\bmentoring\s+programs?\b"],
        "notes": "Formal mentoring program participation",
        "wcm_section_number": 14,
        "wcm_required": False
    },
    {
        "id": "training_grants",
        "canonical": "Training Grants",
        "aliases": [
            "training grants", "t32 grants", "training grant leadership",
            "predoctoral training grants", "postdoctoral training grants",
            "training program grants", "trainee grants"
        ],
        "parent": "mentoring",
        "children": [],
        "approx_location_percentile": 62,
        "commonness_percentile": 45,
        "specificity_rank": 90,
        "priority": 78,
        "patterns": [r"\b(training grants?|t32)\b"],
        "notes": "Training grant leadership (T32, etc.)",
        "wcm_section_number": 14,
        "wcm_required": False
    },
    {
        "id": "current_mentees",
        "canonical": "Current Mentees",
        "aliases": [
            "current mentees", "current trainees", "current advisees",
            "active mentees", "current students", "current postdocs"
        ],
        "parent": "mentoring",
        "children": [],
        "approx_location_percentile": 63,
        "commonness_percentile": 70,
        "specificity_rank": 85,
        "priority": 80,
        "patterns": [r"\bcurrent\s+(mentees|trainees|advisees)\b"],
        "notes": "Currently active mentees and trainees",
        "wcm_section_number": 14,
        "wcm_required": False
    },
    {
        "id": "past_mentees",
        "canonical": "Past Mentees",
        "aliases": [
            "past mentees", "former mentees", "past trainees", "former trainees",
            "past advisees", "former advisees", "alumni mentees"
        ],
        "parent": "mentoring",
        "children": [],
        "approx_location_percentile": 63,
        "commonness_percentile": 65,
        "specificity_rank": 85,
        "priority": 78,
        "patterns": [r"\b(past|former)\s+(mentees|trainees|advisees)\b"],
        "notes": "Previously mentored trainees",
        "wcm_section_number": 14,
        "wcm_required": False
    },

    # ========== WCM SECTION 15: INSTITUTIONAL LEADERSHIP ACTIVITIES ==========
    {
        "id": "institutional_leadership",
        "canonical": "Institutional Leadership Activities",
        "aliases": [
            "institutional leadership activities", "leadership", "administration",
            "administrative roles",
            "program/center directorships", "o. institutional leadership activities",
            "leadership roles",
            # Expanded variations
            "leadership institutional", "leadership activities", "activities leadership",
            "institutional/leadership activities", "leadership & administration",
            "administration & leadership",
            "leadership positions", "administrative leadership", "leadership administrative",
            "directorships", "center leadership", "program leadership", "leadership program",
            "institutional roles", "leadership institutional roles",
            "institutional administration leadership"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 50,
        "commonness_percentile": 60,
        "specificity_rank": 60,
        "priority": 65,
        "patterns": [r"\b(leadership|director(ship)?|chair|chief)\b"],
        "notes": "WCM Section 15 - Program/center directorships and major leadership",
        "wcm_section_number": 15,
        "wcm_required": False
    },

    # ========== WCM SECTION 16: INSTITUTIONAL ADMINISTRATIVE ACTIVITIES ==========
    {
        "id": "institutional_administration",
        "canonical": "Institutional Administrative Activities",
        "aliases": [
            "institutional administrative activities", "committees", "service",
            "institutional service",
            "departmental/school service", "governance and committees",
            "p. institutional administrative activities",
            "committee service", "committee memberships", "committee membership",
            "committee membership/service to columbia university",
            "committee membership/service to teachers college",
            "university administrative service", "administrative service",
            "university service",
            "university activities", "service to the school",
            # Gap analysis additions - "service" as general term
            "service",
            # Expanded variations
            "administrative activities", "activities administrative",
            "administration institutional",
            "institutional/administrative activities",
            "administrative & institutional activities",
            "committee work", "committee activities", "service committees",
            "committees and service",
            "service/committees", "committees & service", "service institutional",
            "service university",
            "departmental service", "school service", "governance activities",
            "administrative duties",
            "institutional committees", "university committees", "internal service"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 74,
        "commonness_percentile": 85,
        "specificity_rank": 60,
        "priority": 70,
        "patterns": [r"\bcommittee\s+(service|memberships?)\b", r"\b(committee|service|governance)\b"],
        "notes": "WCM Section 16 - Committee service and institutional governance",
        "wcm_section_number": 16,
        "wcm_required": False
    },

    # ========== WCM SECTION 17: EXTRAMURAL PROFESSIONAL ACTIVITIES ==========
    {
        "id": "extramural_professional_activities",
        "canonical": "Extramural Professional Responsibilities",
        "aliases": [
            "extramural professional responsibilities",
            "extramural professional activities", "extramural", "professional activities",
            "consulting",
            "editorial and reviewer roles", "advisory boards",
            "professional service outside institution",
            "q. extramural professional activities", "service as grant reviewer",
            "grant reviewer",
            # Gap analysis additions
            "professional service", "professional_service",
            # Expanded variations
            "activities extramural", "professional extramural activities",
            "extramural/professional activities",
            "extramural & professional activities", "activities professional",
            "external activities",
            "external professional activities", "outside activities", "extramural service",
            "service extramural", "professional service external", "extramural work",
            "external service", "professional activities external",
            "outside professional service"
        ],
        "parent": None,
        "children": ["extramural_leadership", "regional_boards_committees", "national_boards_committees", "international_boards_committees", "grant_reviewing", "editorial_activities"],
        "approx_location_percentile": 78,
        "commonness_percentile": 65,
        "specificity_rank": 60,
        "priority": 65,
        "patterns": [r"\b(extramural|consult(ing|ant)|advis(ory|er)|editorial)\b"],
        "notes": "WCM Section 17 - External professional service and activities",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "extramural_leadership",
        "canonical": "Extramural Leadership",
        "aliases": [
            "extramural leadership", "external leadership", "national leadership",
            "international leadership", "professional leadership",
            "society leadership", "organization leadership"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 50,
        "specificity_rank": 80,
        "priority": 75,
        "patterns": [r"\b(extramural|external|national|international)\s+leadership\b"],
        "notes": "Leadership in external organizations",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "regional_boards_committees",
        "canonical": "Regional Boards and Committees",
        "aliases": [
            "regional boards and committees", "regional boards", "regional committees",
            "local boards", "local committees", "regional service",
            "regional advisory boards"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 55,
        "specificity_rank": 85,
        "priority": 73,
        "patterns": [r"\b(regional|local)\s+(boards?|committees?)\b"],
        "notes": "Regional/local boards and committees",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "national_boards_committees",
        "canonical": "National Boards and Committees",
        "aliases": [
            "national boards and committees", "national boards", "national committees",
            "national service", "national advisory boards"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 60,
        "specificity_rank": 85,
        "priority": 75,
        "patterns": [r"\bnational\s+(boards?|committees?)\b"],
        "notes": "National boards and committees",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "international_boards_committees",
        "canonical": "International Boards and Committees",
        "aliases": [
            "international boards and committees", "international boards",
            "international committees",
            "international service", "international advisory boards", "global committees"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 45,
        "specificity_rank": 85,
        "priority": 73,
        "patterns": [r"\b(international|global)\s+(boards?|committees?)\b"],
        "notes": "International boards and committees",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "grant_reviewing",
        "canonical": "Grant Reviewing",
        "aliases": [
            "grant reviewing", "grant review", "study section service",
            "grant reviewer", "peer review of grants", "review panel service",
            "grant review panels", "study sections"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 79,
        "commonness_percentile": 65,
        "specificity_rank": 85,
        "priority": 78,
        "patterns": [r"\bgrant\s+(review(ing|er)?|panel)\b", r"\bstudy section\b"],
        "notes": "Grant review and study section service",
        "wcm_section_number": 17,
        "wcm_required": False
    },
    {
        "id": "editorial_activities",
        "canonical": "Editorial Activities",
        "aliases": [
            "editorial activities", "editorial service",
            "editorial and reviewer roles", "editorial board memberships",
            "peer review service",
            "manuscript reviewing", "journal service",
            "associate/section editor roles", "grant reviewing (study sections)",
            "reviewer and referee service", "scholarly reviewing",
            "journal editor-in-chief", "journal reviewer", "editor for journal",
            "editorial board appointments",
            # Gap analysis additions
            "editorial review service", "editorial_review_service",
            "editorials commentaries", "editorials_commentaries", "commentaries",
            # Expanded variations
            "reviewer and editorial roles", "editorial/reviewer roles",
            "reviewer/editorial roles",
            "editorial & reviewer roles", "reviewer & editorial roles", "editorial work",
            "reviewer roles", "reviewing activities", "peer review activities",
            "editorial duties",
            "reviewing service", "review service", "manuscript review", "journal editing",
            "editor roles", "editorial positions", "reviewer service", "refereeing activities"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 79,
        "commonness_percentile": 75,
        "specificity_rank": 85,
        "priority": 80,
        "patterns": [r"\bjournal\s+(editor|reviewer)\b", r"\b(editor(ial)?|review(er|ing)|referee)\b"],
        "notes": "Editorial boards, manuscript review, journal editing",
        "wcm_section_number": 17,
        "wcm_required": False
    },

    # ========== WCM SECTION 18: INVITATIONS TO SPEAK/PRESENT ==========
    {
        "id": "invitations_to_speak",
        "canonical": "Invitations to Speak/Present",
        "aliases": [
            "invitations to speak/present", "presentations", "invited talks",
            "invited lectures",
            "invitations", "grand rounds (invited)", "seminars", "colloquia", "talks",
            "r. invitations to speak/present", "invited presentations",
            "extramural invited presentations",
            # Expanded variations
            "invited speaking", "speaking invitations", "invited/speaking engagements",
            "speaking & presentations", "presentations & speaking", "invited speaker",
            "speaker invitations", "lectures invited", "talks invited",
            "speaking engagements",
            "presentation invitations", "invited addresses", "speaking activities",
            "invited seminars", "keynote addresses", "plenary talks", "distinguished lectures"
        ],
        "parent": None,
        "children": ["regional_invitations", "national_invitations", "international_invitations"],
        "approx_location_percentile": 68,
        "commonness_percentile": 85,
        "specificity_rank": 70,
        "priority": 75,
        "patterns": [r"\b(invited|grand rounds|colloquium|seminar|presentation|talks?)\b"],
        "notes": "WCM Section 18 - Invited presentations and speaking engagements",
        "wcm_section_number": 18,
        "wcm_required": False
    },
    {
        "id": "regional_invitations",
        "canonical": "Regional Invitations",
        "aliases": [
            "regional invitations", "regional presentations", "regional talks",
            "local invitations", "regional speaking", "local presentations"
        ],
        "parent": "invitations_to_speak",
        "children": [],
        "approx_location_percentile": 68,
        "commonness_percentile": 70,
        "specificity_rank": 85,
        "priority": 75,
        "patterns": [r"\b(regional|local)\s+(invitations?|presentations?|talks?)\b"],
        "notes": "Regional/local invited presentations",
        "wcm_section_number": 18,
        "wcm_required": False
    },
    {
        "id": "national_invitations",
        "canonical": "National Invitations",
        "aliases": [
            "national invitations", "national presentations", "national talks",
            "national speaking", "domestic invitations"
        ],
        "parent": "invitations_to_speak",
        "children": [],
        "approx_location_percentile": 68,
        "commonness_percentile": 75,
        "specificity_rank": 85,
        "priority": 78,
        "patterns": [r"\bnational\s+(invitations?|presentations?|talks?)\b"],
        "notes": "National invited presentations",
        "wcm_section_number": 18,
        "wcm_required": False
    },
    {
        "id": "international_invitations",
        "canonical": "International Invitations",
        "aliases": [
            "international invitations", "international presentations",
            "international talks",
            "international speaking", "global invitations"
        ],
        "parent": "invitations_to_speak",
        "children": [],
        "approx_location_percentile": 68,
        "commonness_percentile": 65,
        "specificity_rank": 85,
        "priority": 76,
        "patterns": [r"\binternational\s+(invitations?|presentations?|talks?)\b"],
        "notes": "International invited presentations",
        "wcm_section_number": 18,
        "wcm_required": False
    },

    # ========== WCM SECTION 19: BIBLIOGRAPHY ==========
    {
        "id": "bibliography",
        "canonical": "Bibliography",
        "aliases": [
            "bibliography", "publications", "publications list", "complete bibliography",
            "s. bibliography",
            "research/publications", "scholarly publications",
            "published or in press articles",
            # Expanded variations
            "publications list", "list of publications", "publications/bibliography",
            "bibliography & publications", "publications & bibliography", "scholarly works",
            "published works", "written works", "publication record", "publishing record",
            "works published", "publication history", "publications history",
            "academic publications",
            "research publications", "publication portfolio", "scholarly output"
        ],
        "parent": None,
        "children": [
            "peer_reviewed_articles", "reviews_and_editorials", "books", "book_chapters",
            "non_peer_reviewed_publications", "case_reports", "in_review_submitted",
            "abstracts", "other_scholarly_outputs"
        ],
        "approx_location_percentile": 80,
        "commonness_percentile": 100,
        "specificity_rank": 50,
        "priority": 65,
        "patterns": [r"\b(scholarly\s+)?publications?\b", r"\b(publications?|bibliograph(y|ies))\b"],
        "notes": "WCM Section 19 - Container for all publication types",
        "wcm_section_number": 19,
        "wcm_required": True
    },
    {
        "id": "peer_reviewed_articles",
        "canonical": "Peer-reviewed Research Articles",
        "aliases": [
            "peer-reviewed research articles", "peer-reviewed", "research articles",
            "journal articles", "refereed papers", "original research",
            "1. peer-reviewed research articles",
            "peer reviewed journal articles (original work)",
            "peer reviewed journal articles",
            "peer reviewed journal articles original work",
            # Expanded variations
            "peer reviewed articles", "articles peer-reviewed",
            "research/peer-reviewed articles",
            "peer-reviewed & research articles", "peer reviewed research", "refereed articles",
            "journal publications", "peer reviewed publications", "original articles",
            "research papers", "scholarly articles", "academic articles",
            "peer-reviewed papers",
            "journal research articles", "published articles", "refereed research"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 81,
        "commonness_percentile": 100,
        "specificity_rank": 90,
        "priority": 95,
        "patterns": [r"\bpeer[- ]?reviewed\s+(journal\s+)?articles?\b", r"\b(peer-?reviewed|refereed)\b", r"\b(journal|original)\s+articles?\b"],
        "notes": "WCM Bibliography #1 - Peer-reviewed journal articles",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "reviews_and_editorials",
        "canonical": "Reviews and Editorials",
        "aliases": [
            "reviews and editorials", "reviews", "editorials", "invited reviews",
            "narrative reviews", "systematic reviews", "2. reviews and editorials",
            # Gap analysis additions
            "editorials commentaries", "editorials_commentaries", "commentaries",
            # Expanded variations
            "editorials and reviews", "reviews/editorials", "editorials/reviews",
            "reviews & editorials", "editorials & reviews", "review articles",
            "editorial articles", "review papers", "editorial pieces", "literature reviews",
            "review publications", "editorial publications", "published reviews",
            "published editorials", "invited editorials", "editorial work published"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 82,
        "commonness_percentile": 70,
        "specificity_rank": 88,
        "priority": 80,
        "patterns": [r"\breviews?\b", r"\beditorials?\b"],
        "notes": "WCM Bibliography #2 - Review articles and editorials",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "books",
        "canonical": "Books",
        "aliases": [
            "books", "monographs", "scholarly books", "3. books",
            # Gap analysis additions
            "edited volumes", "edited_volumes", "edited books",
            # Expanded variations
            "authored books", "books authored", "books/monographs", "monographs/books",
            "books & monographs", "monographs & books", "book publications",
            "published books", "volumes edited", "authored volumes",
            "scholarly monographs", "academic books", "textbooks authored", "book authorship"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 83,
        "commonness_percentile": 40,
        "specificity_rank": 88,
        "priority": 75,
        "patterns": [r"\b(books?|monograph|edited volume)\b"],
        "notes": "WCM Bibliography #3 - Authored or edited books",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "book_chapters",
        "canonical": "Chapters",
        "aliases": [
            "chapters", "book chapters", "handbook chapters", "textbook chapters",
            "4. chapters",
            # Expanded variations
            "chapters in books", "chapters/books", "book/chapters", "chapters & books",
            "chapters authored", "authored chapters", "chapter publications",
            "published chapters", "handbook contributions", "textbook contributions",
            "chapters in edited volumes", "contributed chapters", "book sections",
            "chapters written", "chapter authorship", "invited chapters"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 83,
        "commonness_percentile": 55,
        "specificity_rank": 90,
        "priority": 86,
        "patterns": [r"\b(chapter|book chapter|textbook chapter|handbook)\b"],
        "notes": "WCM Bibliography #4 - Book chapters",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "non_peer_reviewed_publications",
        "canonical": "Non-peer-reviewed Research Publications",
        "aliases": [
            "non-peer-reviewed research publications", "non-peer-reviewed", "nonrefereed",
            "reports", "white papers", "practice guidelines (non-refereed)",
            "5. non-peer-reviewed research publications",
            # Gap analysis additions
            "technical reports", "technical_reports", "research reports",
            # Expanded variations
            "non peer reviewed publications", "publications non-peer-reviewed",
            "non-refereed publications",
            "non-peer-reviewed/publications", "non peer reviewed research",
            "technical publications",
            "white paper publications", "report publications", "non refereed research",
            "non-reviewed publications", "non reviewed research", "unrefereed publications",
            "grey literature", "technical writings", "non peer reviewed articles"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 84,
        "commonness_percentile": 50,
        "specificity_rank": 88,
        "priority": 78,
        "patterns": [r"\b(non[- ]?peer[- ]?reviewed|nonrefereed|white paper|technical report)\b"],
        "notes": "WCM Bibliography #5 - Non-peer-reviewed publications and reports",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "case_reports",
        "canonical": "Case Reports",
        "aliases": [
            "case reports", "cases", "case series", "6. case reports (optional)",
            # Expanded variations
            "reports case", "clinical cases", "clinical case reports", "case/reports",
            "case studies", "case study reports", "published cases", "case publications",
            "series case", "case series reports", "reported cases", "case presentations",
            "cases reported", "cases published", "case series publications"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 84,
        "commonness_percentile": 45,
        "specificity_rank": 88,
        "priority": 70,
        "patterns": [r"\bcase( reports?| series)\b"],
        "notes": "WCM Bibliography #6 - Case reports (optional)",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "in_review_submitted",
        "canonical": "In Review / Submitted / In Preparation",
        "aliases": [
            "in review / submitted / in preparation", "in review", "submitted",
            "under review",
            "in preparation", "preprints", "manuscripts under review",
            "7. in review (submitted/in preparation)", "manuscripts under peer review",
            "submitted articles", "submitted publications", "unpublished papers",
            # Expanded variations
            "submitted/in review", "review/submitted", "in review & submitted",
            "submitted & in review",
            "manuscripts submitted", "papers submitted", "submitted manuscripts",
            "papers in review",
            "articles in review", "pending publications", "forthcoming publications",
            "manuscripts in preparation", "works in preparation", "in press",
            "accepted manuscripts"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 85,
        "commonness_percentile": 65,
        "specificity_rank": 85,
        "priority": 72,
        "patterns": [r"\bmanuscripts?\s+under\s+(peer\s+)?review\b", r"\bsubmitted\s+(articles?|publications?)\b", r"\b(in|under)\s+review\b", r"\bsubmitted\b", r"\bin\s+preparation\b", r"\bpreprint(s)?\b"],
        "notes": "WCM Bibliography #7 - Manuscripts in review, submitted, or in preparation",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "abstracts",
        "canonical": "Abstracts",
        "aliases": [
            "abstracts", "conference abstracts", "meeting abstracts", "proceedings",
            "published abstracts", "8. abstracts (selected)",
            # Expanded variations
            "abstract publications", "abstracts published", "meeting/conference abstracts",
            "conference/meeting abstracts", "abstracts & proceedings",
            "proceedings & abstracts",
            "symposium abstracts", "poster abstracts published", "oral abstracts",
            "abstract presentations", "abstracts presentations", "selected abstracts",
            "abstracts selected", "scientific abstracts", "research abstracts"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 84,
        "commonness_percentile": 70,
        "specificity_rank": 85,
        "priority": 76,
        "patterns": [r"\babstracts?\b", r"\bproceedings?\b"],
        "notes": "WCM Bibliography #8 - MOVED FROM conference_presentations to bibliography per WCM template",
        "wcm_section_number": 19,
        "wcm_required": False
    },
    {
        "id": "other_scholarly_outputs",
        "canonical": "Other (Media, Podcasts, etc.)",
        "aliases": [
            "other scholarly outputs (media/podcasts/etc.)", "other", "media", "podcasts",
            "videos", "online media", "science communication outputs",
            "9. other (media, podcasts, etc.)",
            # Gap analysis additions
            "software tools artifacts", "software_tools_artifacts", "software", "tools",
            "datasets", "code", "repositories", "digital artifacts",
            # Expanded variations
            "other outputs", "outputs other", "other/media outputs", "media/other outputs",
            "other publications", "other scholarly work", "miscellaneous outputs",
            "miscellaneous publications", "digital outputs", "online publications",
            "multimedia outputs", "alternative outputs", "non-traditional outputs",
            "other scholarly contributions", "other media", "supplementary publications"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 86,
        "commonness_percentile": 40,
        "specificity_rank": 80,
        "priority": 60,
        "patterns": [r"\b(podcast|video|media|blog|op-?ed|software|dataset|code|repository)\b"],
        "notes": "WCM Bibliography #9 - Media, podcasts, software, datasets, and other outputs",
        "wcm_section_number": 19,
        "wcm_required": False
    },

    # ========== ADDITIONAL COMMON SECTIONS (not in WCM template but frequently used) ==========

    {
        "id": "conference_presentations_posters",
        "canonical": "Conference Presentations / Posters",
        "aliases": [
            "conference presentations / posters", "conferences", "talks", "posters",
            "meeting presentations", "platform/oral presentations",
            "poster presentations",
            "symposia contributions", "international conference papers",
            "conference podium presentations",
            "presentations, oral abstracts, & posters",
            # Expanded variations
            "presentations and posters", "posters and presentations", "conference/posters",
            "presentations/posters", "presentations & posters", "posters & presentations",
            "conference activities", "meeting posters", "poster sessions",
            "oral presentations",
            "platform presentations", "conference contributions", "meeting contributions",
            "conference talks", "symposium presentations", "poster abstracts"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 70,
        "commonness_percentile": 90,
        "specificity_rank": 70,
        "priority": 74,
        "patterns": [r"\b(international\s+)?conference\s+(papers|presentations|podium)\b", r"\b(poster|platform|oral|conference|sympos(ium|ia))\b"],
        "notes": "Separate from Bibliography - conference presentations may not be publications. Abstracts MOVED to Bibliography per WCM.",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "military_service",
        "canonical": "Military Service",
        "aliases": [
            "military service", "military", "military experience", "armed forces",
            "military background", "service record", "military history", "veteran status",
            # Expanded variations
            "service military", "experience military", "military/armed forces",
            "armed forces service", "military & armed forces", "armed forces experience",
            "military record", "service record military", "history military",
            "veteran service", "military veteran", "armed services", "military duty",
            "active duty", "reserve service", "military assignments", "military career"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 26,
        "commonness_percentile": 20,
        "specificity_rank": 70,
        "priority": 65,
        "patterns": [r"\b(military|armed forces|veteran)\b"],
        "notes": "Military service history - included per user request",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "community_service",
        "canonical": "Community Service",
        "aliases": [
            "community service", "community outreach",
            "volunteer work", "public service", "community activities",
            "service to community", "civic engagement",
            # Gap analysis additions
            "community engagement", "community_engagement",
            # Expanded variations
            "service community", "community/service", "service/community",
            "service & community", "community & service", "engagement community",
            "outreach community", "volunteer service", "service volunteer", "civic service",
            "community work", "community involvement", "public volunteer work",
            "service civic", "community participation", "volunteer activities"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 76,
        "commonness_percentile": 45,
        "specificity_rank": 65,
        "priority": 65,
        "patterns": [r"\bcommunity\s+(service|engagement|outreach)\b", r"\bvolunteer\s+work\b"],
        "notes": "Community service and civic engagement",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "public_outreach_media",
        "canonical": "Public Outreach / Media Contributions",
        "aliases": [
            "public outreach / media contributions", "media", "outreach",
            "public communication",
            "science communication", "press and media", "public engagement",
            "other media (podcasts/tv/radio)", "engagement", "media coverage",
            # Expanded variations
            "outreach and media", "media and outreach", "outreach/media", "media/outreach",
            "outreach & media", "media & outreach", "public media", "media contributions",
            "outreach contributions", "outreach activities", "media activities",
            "public relations",
            "communication activities", "media engagement", "outreach engagement",
            "public affairs",
            "science outreach", "community outreach media"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 50,
        "specificity_rank": 60,
        "priority": 60,
        "patterns": [r"\b(media|outreach|press|podcast|tv|radio)\b"],
        "notes": "Public communication and media engagement",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "bibliometric_summary",
        "canonical": "Bibliometric Summary",
        "aliases": [
            "bibliometric summary", "bibliometrics", "citation metrics", "research impact",
            "h-index and citations", "icite/relative citation ratio",
            "research impact summary",
            # Expanded variations
            "summary bibliometric", "metrics citation", "impact research",
            "bibliometric/citation metrics",
            "citation & bibliometric summary", "metrics & bibliometrics",
            "publication metrics",
            "citation analysis", "impact metrics", "citation summary", "h-index summary",
            "bibliometric analysis", "citation statistics", "research metrics",
            "scholarly impact"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 88,
        "commonness_percentile": 25,
        "specificity_rank": 70,
        "priority": 55,
        "patterns": [r"\b(h-?index|citations?|bibliometric|icite|rcr)\b"],
        "notes": "H-index, citations, impact metrics",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "professional_development",
        "canonical": "Professional Development and Continuing Education",
        "aliases": [
            "professional development and continuing education", "professional development",
            "continuing education", "development", "faculty development",
            "short courses & certificates",
            "career development workshops",
            # Expanded variations
            "development professional", "education continuing",
            "development/continuing education",
            "continuing education & development", "professional & continuing education",
            "development activities", "continuing development", "professional education",
            "faculty training", "professional training", "career development",
            "lifelong learning",
            "professional learning", "continuing professional education", "ongoing education"
        ],
        "parent": None,
        "children": ["courses_attended"],
        "approx_location_percentile": 92,
        "commonness_percentile": 60,
        "specificity_rank": 60,
        "priority": 55,
        "patterns": [r"\b(continuing|professional)\s+education\b", r"\bdevelopment\b"],
        "notes": "Professional development courses and workshops attended",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "courses_attended",
        "canonical": "Courses Attended",
        "aliases": [
            "courses attended", "professional courses", "training courses attended",
            "workshops attended", "courses and workshops attended",
            # Expanded variations
            "attended courses", "courses/workshops attended", "workshops/courses attended",
            "attended workshops", "courses & workshops attended",
            "workshops & courses attended",
            "training attended", "attended training", "professional training attended",
            "courses completed", "workshops completed", "training courses completed",
            "completed training", "courses taken", "workshops taken"
        ],
        "parent": "professional_development",
        "children": [],
        "approx_location_percentile": 93,
        "commonness_percentile": 30,
        "specificity_rank": 70,
        "priority": 60,
        "patterns": [r"\bcourses?\s+attended\b", r"\b(workshops?|training)\s+attended\b"],
        "notes": "Courses and workshops attended for professional development",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "languages",
        "canonical": "Languages",
        "aliases": [
            "languages", "language skills", "language proficiency", "languages spoken",
            "multilingual abilities",
            # Expanded variations
            "language abilities", "linguistic skills", "proficiency languages",
            "skills language",
            "languages/proficiency", "language competency", "foreign languages",
            "spoken languages",
            "language fluency", "fluency languages", "multilingual skills",
            "linguistic proficiency",
            "language knowledge", "languages known", "linguistic abilities"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 94,
        "commonness_percentile": 35,
        "specificity_rank": 50,
        "priority": 40,
        "patterns": [r"\blanguage(s)?\b", r"\bproficiency\b"],
        "notes": "Language skills and proficiency",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "references",
        "canonical": "References",
        "aliases": [
            "references", "referees", "professional references",
            "references available upon request", "list of references",
            # Expanded variations
            "professional referees", "referees professional", "references/referees",
            "referees & references", "references & referees", "reference list",
            "list referees", "contact references", "reference contacts",
            "references provided",
            "available references", "references upon request", "personal references",
            "character references", "recommendation references"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 98,
        "commonness_percentile": 40,
        "specificity_rank": 50,
        "priority": 40,
        "patterns": [r"\breferences?\b", r"\breferees?\b"],
        "notes": "Professional references",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "visiting_professorships",
        "canonical": "Visiting Professorships",
        "aliases": [
            "visiting professorships", "visiting professor", "visiting faculty",
            "visiting scholar", "visiting appointments", "visiting positions",
            # Expanded variations
            "professorships visiting", "visiting/scholar appointments",
            "visiting faculty positions",
            "visiting & guest appointments", "guest professorships",
            "visiting academic positions",
            "visiting roles", "visiting scholar appointments",
            "visiting appointments academic",
            "academic visiting positions", "visiting professor positions",
            "visiting faculty appointments",
            "scholar visiting", "guest faculty", "visiting lectureships"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 54,
        "commonness_percentile": 40,
        "specificity_rank": 70,
        "priority": 70,
        "patterns": [r"\bvisiting\s+(professor(ships?)?|faculty|scholar|appointments?|positions?)\b"],
        "notes": "Visiting academic appointments",
        "wcm_section_number": None,
        "wcm_required": False
    },

    {
        "id": "consulting_activities",
        "canonical": "Consulting Activities",
        "aliases": [
            "consulting activities", "consulting work", "advisory roles",
            "industry consulting",
            "expert witness/consulting", "advisory board memberships",
            "scientific consulting",
            "consultancies", "external advisory roles", "corporate engagements",
            "paid consulting",
            # Expanded variations
            "activities consulting", "consulting/advisory activities",
            "advisory/consulting activities",
            "consulting & advisory activities", "advisory & consulting activities",
            "consulting services",
            "advisory services", "consulting engagements", "advisory work",
            "consulting roles",
            "advisory positions", "consultant work", "advisory consulting",
            "consulting advisory",
            "external consulting", "professional consulting", "consulting professional"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 90,
        "commonness_percentile": 45,
        "specificity_rank": 75,
        "priority": 72,
        "patterns": [r"\b(consult(ing|ant)|advis(or|ory))\b"],
        "notes": "Consulting and advisory work - can also be child of extramural_professional_activities",
        "wcm_section_number": None,
        "wcm_required": False
    },
]


def build_taxonomy_index():
    """Build lookup indices for fast taxonomy matching."""
    # Alias to section mapping
    alias_map = {}
    # Compiled pattern to section mapping
    pattern_map = []

    for section in CV_SECTIONS:
        section_id = section['id']

        # Index all aliases (lowercase)
        for alias in section['aliases']:
            alias_lower = alias.lower().strip()
            if alias_lower not in alias_map:
                alias_map[alias_lower] = []
            alias_map[alias_lower].append(section)

        # Compile and store patterns
        for pattern_str in section['patterns']:
            try:
                compiled = re.compile(pattern_str, re.IGNORECASE)
                pattern_map.append((compiled, section))
            except re.error:
                logger.warning(f"Invalid regex pattern in {section_id}: {pattern_str}")

    return {
        'alias_map': alias_map,
        'pattern_map': pattern_map,
        'sections_by_id': {s['id']: s for s in CV_SECTIONS}
    }


def get_taxonomy_statistics():
    """Get statistics about the taxonomy."""
    total_sections = len(CV_SECTIONS)
    root_sections = [s for s in CV_SECTIONS if s['parent'] is None]
    wcm_sections = [s for s in CV_SECTIONS if s.get('wcm_section_number') is not None]
    wcm_required = [s for s in CV_SECTIONS if s.get('wcm_required') is True]

    # Build parent-child relationships
    children_counts = {}
    for section in CV_SECTIONS:
        if section['children']:
            children_counts[section['id']] = len(section['children'])

    max_depth = 0
    for section in CV_SECTIONS:
        depth = 1
        current = section
        while current['parent'] is not None:
            depth += 1
            parent_id = current['parent']
            current = next((s for s in CV_SECTIONS if s['id'] == parent_id), None)
            if current is None:
                break
        max_depth = max(max_depth, depth)

    return {
        'total_sections': total_sections,
        'root_sections': len(root_sections),
        'wcm_template_sections': len(wcm_sections),
        'wcm_required_sections': len(wcm_required),
        'max_hierarchy_depth': max_depth,
        'sections_with_children': len(children_counts),
        'total_aliases': sum(len(s['aliases']) for s in CV_SECTIONS)
    }
