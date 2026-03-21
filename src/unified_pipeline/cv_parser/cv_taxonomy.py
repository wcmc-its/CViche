"""
CV section taxonomy for standardized section detection and mapping.
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
May include lineage info or special handling notes."
}

CV_SECTIONS = [
    # ---------- Root / Structural ----------
    {
        "id": "contact_information",
        "canonical": "Contact Information",
        "aliases": [
            "contact information", "contact", "contact details", "name & contact",
            "profile", "bio", "summary", "overview", "personal data", "header",
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
        "children": [],
        "approx_location_percentile": 2,
        "commonness_percentile": 100,
        "specificity_rank": 50,
        "priority": 80,
        "patterns": [r"\b(contact|personal|profile|bio|summary)\b"],
        "notes": ""
    },
    {
        "id": "education_and_training",
        "canonical": "Education and Training",
        "aliases": [
            "education and training", "training and education", "education", "training", "degrees",
            "educational background", "academic background", "undergraduate education",
            "graduate education", "medical education", "professional training", "b. education",
            "academic training",
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
        "children": ["postdoctoral_training"],
        "approx_location_percentile": 12,
        "commonness_percentile": 98,
        "specificity_rank": 60,
        "priority": 85,
        "patterns": [r"\beducation\b", r"\btraining\b", r"\bdegrees?\b"],
        "notes": ""
    },
    {
        "id": "postdoctoral_training",
        "canonical": "Postdoctoral Training",
        "aliases": [
            "postdoctoral training", "postdoc", "fellowship", "fellowships",
            "residency", "residency and fellowship", "house staff training",
            "clinical training", "advanced training", "c. postdoctoral training",
            "post-doctoral training/fellowship", "fellowship training", "residency training",
            "medical fellowships", "post-doctoral researcher", "postgraduate training",
            "post-graduate training",
            # Expanded variations
            "training postdoctoral", "postdoctoral/fellowship", "fellowship/postdoctoral",
            "fellowship and residency", "residency/fellowship", "fellowship & residency",
            "residency & fellowship", "postdoc training", "postdoc fellowship",
            "training fellowship", "training residency", "residency programs",
            "fellowship programs", "post doctoral", "postdoc research", "postdoc experience",
            "clinical fellowship", "clinical residency", "residencies and fellowships"
        ],
        "parent": "education_and_training",
        "children": [],
        "approx_location_percentile": 18,
        "commonness_percentile": 90,
        "specificity_rank": 80,
        "priority": 88,
        "patterns": [r"\b(post-?doc(toral)?|fellow(ship)?s?|residen(cy|t))\s+(training|fellowship)\b", r"\b(postdoc|fellow(ship)?|residen(cy|t))\b"],
        "notes": "Subtype of Education."
    },
    {
        "id": "professional_positions_employment",
        "canonical": "Professional Positions & Employment",
        "aliases": [
            "professional positions & employment", "academic positions and appointments", "positions", "appointments",
            "employment", "experience", "academic appointments", "academic positions",
            "faculty appointments", "professional positions", "employment history",
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
        "children": [],
        "approx_location_percentile": 24,
        "commonness_percentile": 95,
        "specificity_rank": 60,
        "priority": 80,
        "patterns": [r"\b(positions?|appointments?|employment|experience)\b"],
        "notes": ""
    },
    {
        "id": "institutional_hospital_affiliation",
        "canonical": "Institutional / Hospital Affiliation",
        "aliases": [
            "institutional / hospital affiliation", "affiliations", "hospital",
            "hospital appointments", "clinical positions", "hospital privileges",
            "affiliated hospitals", "clinical appointments", "f. institutional / hospital affiliation",
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
        "notes": ""
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
        "notes": ""
    },
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
        "notes": ""
    },
    {
        "id": "licensure_and_certification",
        "canonical": "Licensure and Certification",
        "aliases": [
            "licensure and certification", "licensure", "certification", "licenses",
            "professional licenses", "medical licensure", "licensure & registration",
            "state licensure", "e. licensure, board certification", "certification and licensure",
            "licensing & certification",
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
        "notes": ""
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
        "notes": "Subtype of Licensure/Certification."
    },
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
        "notes": ""
    },
    {
        "id": "professional_orgs_societies",
        "canonical": "Professional Organizations and Society Memberships",
        "aliases": [
            "professional organizations and society memberships", "memberships",
            "organizations", "societies", "professional memberships", "professional affiliations",
            "scientific societies", "medical societies", "associations",
            "i. professional organizations and society memberships",
            "membership in professional organizations", "professional societies",
            "membership in organizations", "memberships in scholarly and professional societies",
            "memberships in professional societies",
            # Expanded variations
            "society memberships", "organizations and societies", "societies and organizations",
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
        "notes": ""
    },
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
        "notes": ""
    },

    # ---------- Education / Teaching / Mentoring ----------
    {
        "id": "educational_contributions",
        "canonical": "Educational Contributions",
        "aliases": [
            "educational contributions", "teaching", "instruction", "courses",
            "teaching activities", "teaching experience", "educational activities",
            "course directorships", "curriculum contributions", "k. educational contributions",
            "teaching experience and responsibilities", "courses taught",
            # Expanded variations
            "contributions educational", "teaching contributions", "contributions teaching",
            "educational/teaching contributions", "teaching/educational contributions",
            "educational & teaching contributions", "teaching & educational contributions",
            "teaching responsibilities", "instructional contributions", "curriculum development",
            "course teaching", "teaching courses", "educational work", "teaching duties",
            "instruction and teaching", "teaching and instruction", "academic teaching"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 60,
        "commonness_percentile": 85,
        "specificity_rank": 65,
        "priority": 75,
        "patterns": [r"\b(teaching|instruction|courses?)\b"],
        "notes": ""
    },
    {
        "id": "mentoring_and_supervision",
        "canonical": "Mentoring and Supervision",
        "aliases": [
            "mentoring and supervision", "mentoring", "mentorship", "advising", "supervision",
            "supervision of trainees", "graduate/residency mentoring", "mentees and advisees",
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
        "children": [],
        "approx_location_percentile": 62,
        "commonness_percentile": 80,
        "specificity_rank": 70,
        "priority": 75,
        "patterns": [r"\b(mentor(ing|ship)?|advis(ing|ors?)|supervis(ion|e|or))\b"],
        "notes": ""
    },

    # ---------- Clinical / Leadership ----------
    {
        "id": "clinical_practice_innovation_leadership",
        "canonical": "Clinical Practice, Innovation, and Leadership",
        "aliases": [
            "clinical practice, innovation, and leadership", "clinical", "practice", "innovation",
            "leadership", "clinical expertise and interests", "quality improvement", "clinical innovations",
            "clinical leadership roles", "l. clinical practice, innovation, and leadership",
            # Expanded variations
            "clinical practice and innovation", "innovation and clinical practice", "clinical/innovation/leadership",
            "practice and innovation", "clinical practice & innovation", "innovation & clinical practice",
            "clinical leadership", "practice leadership", "innovation leadership", "clinical work",
            "clinical activities", "practice activities", "innovation activities", "clinical care",
            "clinical services", "clinical expertise", "innovative practice", "practice innovation"
        ],
        "parent": None,
        "children": ["clinical_qi_projects", "clinical_trials"],
        "approx_location_percentile": 56,
        "commonness_percentile": 70,
        "specificity_rank": 60,
        "priority": 70,
        "patterns": [r"\b(clinical|qi|quality improvement|innovation)\b"],
        "notes": ""
    },
    {
        "id": "clinical_qi_projects",
        "canonical": "Clinical Innovations or Quality Improvement Projects",
        "aliases": [
            "clinical innovations or quality improvement projects", "quality improvement (qi)",
            "clinical process improvement", "patient safety & qi", "clinical innovation projects",
            "outcomes improvement", "care delivery innovation", "quality initiatives", "practice redesign",
            "qi/qa projects", "implementation projects",
            # Expanded variations
            "quality improvement projects", "qi projects", "clinical quality improvement",
            "improvement projects", "quality/improvement projects", "qi & qa projects",
            "innovation projects", "projects quality improvement", "clinical qi",
            "qi initiatives", "qa projects", "quality assurance projects", "improvement initiatives",
            "patient safety projects", "safety and quality", "process improvement projects"
        ],
        "parent": "clinical_practice_innovation_leadership",
        "children": [],
        "approx_location_percentile": 66,
        "commonness_percentile": 55,
        "specificity_rank": 80,
        "priority": 80,
        "patterns": [r"\b(qi|quality\s*improvement|process improvement)\b"],
        "notes": "Subtype under Clinical."
    },
    {
        "id": "clinical_trials",
        "canonical": "Clinical Trials (PI or Co-I roles)",
        "aliases": [
            "clinical trials (pi or co-i roles)", "clinical research trials", "investigator roles in trials",
            "trial portfolio", "industry-sponsored trials", "nih/federal trials", "clinical studies",
            "trial leadership", "clinical trial participation", "regulatory trials", "translational trials",
            # Expanded variations
            "trials clinical", "clinical trial research", "research trials", "trials/studies",
            "clinical trials & studies", "trials and studies", "investigator trials",
            "pi trials", "co-i trials", "trial participation", "clinical research studies",
            "human subjects research", "trial investigator", "clinical investigation",
            "sponsored trials", "trial roles", "clinical trial work"
        ],
        "parent": "clinical_practice_innovation_leadership",
        "children": [],
        "approx_location_percentile": 67,
        "commonness_percentile": 50,
        "specificity_rank": 80,
        "priority": 80,
        "patterns": [r"\b(clinical\s+trials?|trial(s)?|pi|co-?i)\b"],
        "notes": "Subtype under Clinical."
    },

    # ---------- Research / Funding ----------
    {
        "id": "research_overview",
        "canonical": "Research Overview",
        "aliases": [
            "research overview", "research", "research interests", "research statement", "scholarship", "m. research (overview)",
            # Expanded variations
            "overview research", "research summary", "research background", "research/scholarship",
            "research & scholarship", "research interests and overview", "interests research",
            "research focus", "research areas", "research activities", "scholarly research",
            "research agenda", "research program", "research profile", "statement research"
        ],
        "parent": None,
        "children": ["grant_support"],
        "approx_location_percentile": 52,
        "commonness_percentile": 75,
        "specificity_rank": 55,
        "priority": 65,
        "patterns": [r"\bresearch( interests| statement)?\b", r"\bscholarship\b"],
        "notes": ""
    },
    {
        "id": "grant_support",
        "canonical": "Grant Support (Active and Completed)",
        "aliases": [
            "grant support (active and completed)", "funding", "grants", "support", "research support",
            "grants and contracts", "sponsored research", "current & prior support", "funding history",
            "extramural funding", "external funding", "grant funding",
            "m. research (funding)", "current funding", "past funding", "grants & contract awards",
            "ongoing research support", "completed research support", "current grants",
            # Expanded variations
            "support grant", "grant/funding support", "funding and grants", "grants/contracts",
            "grants & funding", "funding & grants", "research funding", "funding research",
            "grant awards", "funding awards", "sponsored projects", "research grants",
            "active grants", "completed grants", "grant history", "funding sources",
            "extramural support", "external grants", "grant portfolio"
        ],
        "parent": "research_overview",
        "children": [],
        "approx_location_percentile": 58,
        "commonness_percentile": 75,
        "specificity_rank": 75,
        "priority": 80,
        "patterns": [r"\b(grants?|funding|sponsored research|contracts?|extramural)\b"],
        "notes": "Subtype under Research."
    },

    # ---------- Leadership / Service / Outreach ----------
    {
        "id": "institutional_leadership",
        "canonical": "Institutional Leadership Activities",
        "aliases": [
            "institutional leadership activities", "leadership", "administration", "administrative roles",
            "program/center directorships", "o. institutional leadership activities", "leadership roles",
            # Expanded variations
            "leadership institutional", "leadership activities", "activities leadership",
            "institutional/leadership activities", "leadership & administration", "administration & leadership",
            "leadership positions", "administrative leadership", "leadership administrative",
            "directorships", "center leadership", "program leadership", "leadership program",
            "institutional roles", "leadership institutional roles", "institutional administration leadership"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 50,
        "commonness_percentile": 60,
        "specificity_rank": 60,
        "priority": 65,
        "patterns": [r"\b(leadership|director(ship)?|chair|chief)\b"],
        "notes": ""
    },
    {
        "id": "institutional_administration",
        "canonical": "Institutional Administrative Activities",
        "aliases": [
            "institutional administrative activities", "committees", "service", "institutional service",
            "departmental/school service", "governance and committees", "p. institutional administrative activities",
            "committee service", "committee memberships", "committee membership",
            "committee membership/service to columbia university", "committee membership/service to teachers college",
            "university administrative service", "administrative service", "university service",
            "university activities", "service to the school",
            # Expanded variations
            "administrative activities", "activities administrative", "administration institutional",
            "institutional/administrative activities", "administrative & institutional activities",
            "committee work", "committee activities", "service committees", "committees and service",
            "service/committees", "committees & service", "service institutional", "service university",
            "departmental service", "school service", "governance activities", "administrative duties",
            "institutional committees", "university committees", "internal service"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 74,
        "commonness_percentile": 85,
        "specificity_rank": 60,
        "priority": 70,
        "patterns": [r"\bcommittee\s+(service|memberships?)\b", r"\b(committee|service|governance)\b"],
        "notes": ""
    },
    {
        "id": "extramural_professional_activities",
        "canonical": "Extramural Professional Activities",
        "aliases": [
            "extramural professional activities", "extramural", "professional activities", "consulting",
            "editorial and reviewer roles", "advisory boards", "professional service outside institution",
            "q. extramural professional activities", "service as grant reviewer", "grant reviewer",
            # Expanded variations
            "activities extramural", "professional extramural activities", "extramural/professional activities",
            "extramural & professional activities", "activities professional", "external activities",
            "external professional activities", "outside activities", "extramural service",
            "service extramural", "professional service external", "extramural work",
            "external service", "professional activities external", "outside professional service"
        ],
        "parent": None,
        "children": ["editorial_reviewer_roles", "consulting_activities"],
        "approx_location_percentile": 78,
        "commonness_percentile": 65,
        "specificity_rank": 60,
        "priority": 65,
        "patterns": [r"\b(extramural|consult(ing|ant)|advis(ory|er)|editorial)\b"],
        "notes": ""
    },
    {
        "id": "editorial_reviewer_roles",
        "canonical": "Editorial and Reviewer Roles",
        "aliases": [
            "editorial and reviewer roles", "editorial activities", "peer review service",
            "editorial board memberships", "manuscript reviewing", "journal service",
            "associate/section editor roles", "grant reviewing (study sections)",
            "reviewer and referee service", "editorial service", "scholarly reviewing",
            "journal editor-in-chief", "journal reviewer", "editor for journal",
            "editorial board appointments",
            # Expanded variations
            "reviewer and editorial roles", "editorial/reviewer roles", "reviewer/editorial roles",
            "editorial & reviewer roles", "reviewer & editorial roles", "editorial work",
            "reviewer roles", "reviewing activities", "peer review activities", "editorial duties",
            "reviewing service", "review service", "manuscript review", "journal editing",
            "editor roles", "editorial positions", "reviewer service", "refereeing activities"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 48,
        "commonness_percentile": 60,
        "specificity_rank": 75,
        "priority": 75,
        "patterns": [r"\bjournal\s+(editor|reviewer)\b", r"\b(editor(ial)?|review(er|ing)|referee)\b"],
        "notes": "Subtype under Extramural Activities."
    },
    {
        "id": "consulting_activities",
        "canonical": "Consulting Activities",
        "aliases": [
            "consulting activities", "consulting work", "advisory roles", "industry consulting",
            "expert witness/consulting", "advisory board memberships", "scientific consulting",
            "consultancies", "external advisory roles", "corporate engagements", "paid consulting",
            # Expanded variations
            "activities consulting", "consulting/advisory activities", "advisory/consulting activities",
            "consulting & advisory activities", "advisory & consulting activities", "consulting services",
            "advisory services", "consulting engagements", "advisory work", "consulting roles",
            "advisory positions", "consultant work", "advisory consulting", "consulting advisory",
            "external consulting", "professional consulting", "consulting professional"
        ],
        "parent": "extramural_professional_activities",
        "children": [],
        "approx_location_percentile": 90,
        "commonness_percentile": 45,
        "specificity_rank": 75,
        "priority": 72,
        "patterns": [r"\b(consult(ing|ant)|advis(or|ory))\b"],
        "notes": "Subtype under Extramural Activities."
    },
    {
        "id": "invitations_to_speak",
        "canonical": "Invitations to Speak/Present",
        "aliases": [
            "invitations to speak/present", "presentations", "invited talks", "invited lectures",
            "invitations", "grand rounds (invited)", "seminars", "colloquia", "talks",
            "r. invitations to speak/present", "invited presentations",
            "extramural invited presentations",
            # Expanded variations
            "invited speaking", "speaking invitations", "invited/speaking engagements",
            "speaking & presentations", "presentations & speaking", "invited speaker",
            "speaker invitations", "lectures invited", "talks invited", "speaking engagements",
            "presentation invitations", "invited addresses", "speaking activities",
            "invited seminars", "keynote addresses", "plenary talks", "distinguished lectures"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 68,
        "commonness_percentile": 85,
        "specificity_rank": 70,
        "priority": 75,
        "patterns": [r"\b(invited|grand rounds|colloquium|seminar|presentation|talks?)\b"],
        "notes": ""
    },
    {
        "id": "conference_presentations_posters",
        "canonical": "Conference Presentations / Posters",
        "aliases": [
            "conference presentations / posters", "conferences", "talks", "posters", "abstracts",
            "meeting presentations", "platform/oral presentations", "poster presentations",
            "symposia contributions", "international conference papers", "conference podium presentations",
            "presentations, oral abstracts, & posters",
            # Expanded variations
            "presentations and posters", "posters and presentations", "conference/posters",
            "presentations/posters", "presentations & posters", "posters & presentations",
            "conference activities", "meeting posters", "poster sessions", "oral presentations",
            "platform presentations", "conference contributions", "meeting contributions",
            "conference talks", "symposium presentations", "poster abstracts"
        ],
        "parent": None,
        "children": ["abstracts"],
        "approx_location_percentile": 70,
        "commonness_percentile": 90,
        "specificity_rank": 70,
        "priority": 74,
        "patterns": [r"\b(international\s+)?conference\s+(papers|presentations|podium)\b", r"\b(poster|platform|oral|conference|sympos(ium|ia))\b"],
        "notes": ""
    },
    {
        "id": "public_outreach_media",
        "canonical": "Public Outreach / Media Contributions",
        "aliases": [
            "public outreach / media contributions", "media", "outreach", "public communication",
            "science communication", "press and media", "public engagement",
            "other media (podcasts/tv/radio)", "engagement", "media coverage",
            # Expanded variations
            "outreach and media", "media and outreach", "outreach/media", "media/outreach",
            "outreach & media", "media & outreach", "public media", "media contributions",
            "outreach contributions", "outreach activities", "media activities", "public relations",
            "communication activities", "media engagement", "outreach engagement", "public affairs",
            "science outreach", "community outreach media"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 78,
        "commonness_percentile": 50,
        "specificity_rank": 60,
        "priority": 60,
        "patterns": [r"\b(media|outreach|press|podcast|tv|radio)\b"],
        "notes": ""
    },
    {
        "id": "community_service",
        "canonical": "Community Service",
        "aliases": [
            "community service", "community engagement", "community outreach",
            "volunteer work", "public service", "community activities",
            "service to community", "civic engagement",
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
        "notes": ""
    },

    # ---------- Bibliography (parent) and subtypes ----------
    {
        "id": "bibliography",
        "canonical": "Bibliography",
        "aliases": [
            "bibliography", "publications", "publications list", "complete bibliography", "s. bibliography",
            "research/publications", "scholarly publications", "published or in press articles",
            # Expanded variations
            "publications list", "list of publications", "publications/bibliography",
            "bibliography & publications", "publications & bibliography", "scholarly works",
            "published works", "written works", "publication record", "publishing record",
            "works published", "publication history", "publications history", "academic publications",
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
        "notes": "Parent container for publication subtypes."
    },
    {
        "id": "peer_reviewed_articles",
        "canonical": "Peer-reviewed Research Articles",
        "aliases": [
            "peer-reviewed research articles", "peer-reviewed", "research articles",
            "journal articles", "refereed papers", "original research", "1. peer-reviewed research articles",
            "peer reviewed journal articles (original work)", "peer reviewed journal articles",
            "peer reviewed journal articles original work",
            # Expanded variations
            "peer reviewed articles", "articles peer-reviewed", "research/peer-reviewed articles",
            "peer-reviewed & research articles", "peer reviewed research", "refereed articles",
            "journal publications", "peer reviewed publications", "original articles",
            "research papers", "scholarly articles", "academic articles", "peer-reviewed papers",
            "journal research articles", "published articles", "refereed research"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 81,
        "commonness_percentile": 100,
        "specificity_rank": 90,
        "priority": 95,
        "patterns": [r"\bpeer[- ]?reviewed\s+(journal\s+)?articles?\b", r"\b(peer-?reviewed|refereed)\b", r"\b(journal|original)\s+articles?\b"],
        "notes": "Leaf subtype."
    },
    {
        "id": "reviews_and_editorials",
        "canonical": "Reviews and Editorials",
        "aliases": [
            "reviews and editorials", "reviews", "editorials", "invited reviews", "narrative reviews", "systematic reviews", "2. reviews and editorials",
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
        "notes": "Leaf subtype."
    },
    {
        "id": "books",
        "canonical": "Books",
        "aliases": [
            "books", "monographs", "edited volumes", "scholarly books", "3. books",
            # Expanded variations
            "authored books", "books authored", "books/monographs", "monographs/books",
            "books & monographs", "monographs & books", "book publications",
            "published books", "edited books", "volumes edited", "authored volumes",
            "scholarly monographs", "academic books", "textbooks authored", "book authorship"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 83,
        "commonness_percentile": 40,
        "specificity_rank": 88,
        "priority": 75,
        "patterns": [r"\b(books?|monograph|edited volume)\b"],
        "notes": "Leaf subtype."
    },
    {
        "id": "book_chapters",
        "canonical": "Chapters",
        "aliases": [
            "chapters", "book chapters", "handbook chapters", "textbook chapters", "4. chapters",
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
        "notes": "Leaf subtype."
    },
    {
        "id": "non_peer_reviewed_publications",
        "canonical": "Non-peer-reviewed Research Publications",
        "aliases": [
            "non-peer-reviewed research publications", "non-peer-reviewed", "nonrefereed",
            "reports", "white papers", "technical reports", "practice guidelines (non-refereed)",
            "5. non-peer-reviewed research publications",
            # Expanded variations
            "non peer reviewed publications", "publications non-peer-reviewed", "non-refereed publications",
            "non-peer-reviewed/publications", "non peer reviewed research", "technical publications",
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
        "notes": "Leaf subtype."
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
        "notes": "Leaf subtype."
    },
    {
        "id": "in_review_submitted",
        "canonical": "In Review / Submitted / In Preparation",
        "aliases": [
            "in review / submitted / in preparation", "in review", "submitted", "under review",
            "in preparation", "preprints", "manuscripts under review",
            "7. in review (submitted/in preparation)", "manuscripts under peer review",
            "submitted articles", "submitted publications", "unpublished papers",
            # Expanded variations
            "submitted/in review", "review/submitted", "in review & submitted", "submitted & in review",
            "manuscripts submitted", "papers submitted", "submitted manuscripts", "papers in review",
            "articles in review", "pending publications", "forthcoming publications",
            "manuscripts in preparation", "works in preparation", "in press", "accepted manuscripts"
        ],
        "parent": "bibliography",
        "children": [],
        "approx_location_percentile": 85,
        "commonness_percentile": 65,
        "specificity_rank": 85,
        "priority": 72,
        "patterns": [r"\bmanuscripts?\s+under\s+(peer\s+)?review\b", r"\bsubmitted\s+(articles?|publications?)\b", r"\b(in|under)\s+review\b", r"\bsubmitted\b", r"\bin\s+preparation\b", r"\bpreprint(s)?\b"],
        "notes": "Leaf subtype."
    },
    {
        "id": "abstracts",
        "canonical": "Abstracts",
        "aliases": [
            "abstracts", "conference abstracts", "meeting abstracts", "proceedings", "published abstracts", "8. abstracts (selected)",
            # Expanded variations
            "abstract publications", "abstracts published", "meeting/conference abstracts",
            "conference/meeting abstracts", "abstracts & proceedings", "proceedings & abstracts",
            "symposium abstracts", "poster abstracts published", "oral abstracts",
            "abstract presentations", "abstracts presentations", "selected abstracts",
            "abstracts selected", "scientific abstracts", "research abstracts"
        ],
        "parent": "conference_presentations_posters",
        "children": [],
        "approx_location_percentile": 84,
        "commonness_percentile": 70,
        "specificity_rank": 85,
        "priority": 76,
        "patterns": [r"\babstracts?\b", r"\bproceedings?\b"],
        "notes": "Placed under Conference Presentations/Posters by default."
    },
    {
        "id": "other_scholarly_outputs",
        "canonical": "Other Scholarly Outputs (Media/Podcasts/etc.)",
        "aliases": [
            "other scholarly outputs (media/podcasts/etc.)", "other", "media", "podcasts", "videos", "online media", "science communication outputs", "9. other (media, podcasts, etc.)",
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
        "patterns": [r"\b(podcast|video|media|blog|op-?ed)\b"],
        "notes": "Leaf subtype."
    },
    {
        "id": "patents",
        "canonical": "Patents",
        "aliases": [
            "patents", "patent applications", "patents applied-for",
            "intellectual property", "patents filed", "patent portfolio",
            "patents issued", "patents pending", "patents applied for",
            # Expanded variations
            "patent/applications", "applications patents", "patents & applications",
            "applications & patents", "patent filings", "filed patents", "issued patents",
            "pending patents", "patent holdings", "patent work", "intellectual property patents",
            "ip portfolio", "patent inventions", "inventions patented", "patent disclosures"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 87,
        "commonness_percentile": 35,
        "specificity_rank": 75,
        "priority": 70,
        "patterns": [r"\bpatents?\b", r"\bpatent\s+(applications?|applied-?for|filed|issued|pending)\b", r"\bintellectual\s+property\b"],
        "notes": ""
    },

    # ---------- Metrics / Development / Misc ----------
    {
        "id": "bibliometric_summary",
        "canonical": "Bibliometric Summary",
        "aliases": [
            "bibliometric summary", "bibliometrics", "citation metrics", "research impact", "h-index and citations", "icite/relative citation ratio", "research impact summary",
            # Expanded variations
            "summary bibliometric", "metrics citation", "impact research", "bibliometric/citation metrics",
            "citation & bibliometric summary", "metrics & bibliometrics", "publication metrics",
            "citation analysis", "impact metrics", "citation summary", "h-index summary",
            "bibliometric analysis", "citation statistics", "research metrics", "scholarly impact"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 88,
        "commonness_percentile": 25,
        "specificity_rank": 70,
        "priority": 55,
        "patterns": [r"\b(h-?index|citations?|bibliometric|icite|rcr)\b"],
        "notes": ""
    },
    {
        "id": "professional_development",
        "canonical": "Professional Development and Continuing Education",
        "aliases": [
            "professional development and continuing education", "professional development",
            "continuing education", "development", "faculty development", "short courses & certificates",
            "career development workshops",
            # Expanded variations
            "development professional", "education continuing", "development/continuing education",
            "continuing education & development", "professional & continuing education",
            "development activities", "continuing development", "professional education",
            "faculty training", "professional training", "career development", "lifelong learning",
            "professional learning", "continuing professional education", "ongoing education"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 92,
        "commonness_percentile": 60,
        "specificity_rank": 60,
        "priority": 55,
        "patterns": [r"\b(continuing|professional)\s+education\b", r"\bdevelopment\b"],
        "notes": ""
    },
    {
        "id": "languages",
        "canonical": "Languages",
        "aliases": [
            "languages", "language skills", "language proficiency", "languages spoken", "multilingual abilities",
            # Expanded variations
            "language abilities", "linguistic skills", "proficiency languages", "skills language",
            "languages/proficiency", "language competency", "foreign languages", "spoken languages",
            "language fluency", "fluency languages", "multilingual skills", "linguistic proficiency",
            "language knowledge", "languages known", "linguistic abilities"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 94,
        "commonness_percentile": 35,
        "specificity_rank": 50,
        "priority": 40,
        "patterns": [r"\blanguage(s)?\b", r"\bproficiency\b"],
        "notes": ""
    },
    {
        "id": "references",
        "canonical": "References",
        "aliases": [
            "references", "referees", "professional references", "references available upon request", "list of references",
            # Expanded variations
            "professional referees", "referees professional", "references/referees",
            "referees & references", "references & referees", "reference list",
            "list referees", "contact references", "reference contacts", "references provided",
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
        "notes": ""
    },
    {
        "id": "cme_activities",
        "canonical": "Continuing Medical Education (CME) Activities",
        "aliases": [
            "continuing medical education (cme) activities", "cme", "cpd", "cme teaching", "continuing professional development", "cme course leadership", "professional education (cme)",
            # Expanded variations
            "cme activities", "activities cme", "cme/cpd activities", "cpd/cme activities",
            "cme & cpd", "cpd & cme", "medical education continuing", "cme courses",
            "cme programming", "cme instruction", "cme participation", "cpd activities",
            "medical continuing education", "continuing medical ed", "cme leadership"
        ],
        "parent": "professional_development",
        "children": [],
        "approx_location_percentile": 72,
        "commonness_percentile": 35,
        "specificity_rank": 75,
        "priority": 65,
        "patterns": [r"\b(cme|cpd)\b", r"\bcontinuing\s+(medical|professional)\s+education\b"],
        "notes": "Subtype of Professional Development."
    },
    {
        "id": "visiting_professorships",
        "canonical": "Visiting Professorships",
        "aliases": [
            "visiting professorships", "visiting professor", "visiting faculty",
            "visiting scholar", "visiting appointments", "visiting positions",
            # Expanded variations
            "professorships visiting", "visiting/scholar appointments", "visiting faculty positions",
            "visiting & guest appointments", "guest professorships", "visiting academic positions",
            "visiting roles", "visiting scholar appointments", "visiting appointments academic",
            "academic visiting positions", "visiting professor positions", "visiting faculty appointments",
            "scholar visiting", "guest faculty", "visiting lectureships"
        ],
        "parent": None,
        "children": [],
        "approx_location_percentile": 54,
        "commonness_percentile": 40,
        "specificity_rank": 70,
        "priority": 70,
        "patterns": [r"\bvisiting\s+(professor(ships?)?|faculty|scholar|appointments?|positions?)\b"],
        "notes": ""
    },
    {
        "id": "courses_attended",
        "canonical": "Courses Attended",
        "aliases": [
            "courses attended", "professional courses", "training courses attended",
            "workshops attended", "courses and workshops attended",
            # Expanded variations
            "attended courses", "courses/workshops attended", "workshops/courses attended",
            "attended workshops", "courses & workshops attended", "workshops & courses attended",
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
        "notes": "Subtype of Professional Development."
    }
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
