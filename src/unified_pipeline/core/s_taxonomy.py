"""
S-Taxonomy: Subsection Classification for Publications and Other Records

This taxonomy defines the specific subsection types (S1-S15, etc.) used
in Phase 1 taxonomy mapping. Used by Phase 2 test generator for evaluation.

Extracted from taxonomy_mapper_v2.py prompts.
"""
from typing import Dict, List

S_TAXONOMY = {
    # Publications (S1-S15)
    "S1": {
        "canonical": "Peer-Reviewed Research Articles (original research)",
        "description": "Original research with methods/results published in peer-reviewed journals",
        "examples": [
            'Chen L, Patel A, Nguyen T. "Single-cell mapping of renal carcinoma immune niches." Nature Medicine. 2024;30:1123–1135. doi:10.1038/s41591-024-01890-y',
            'Gonzalez M, Li Y, Ahmed R. "Machine learning prediction of sepsis mortality using EHR data." JAMA Netw Open. 2023;6(4):e238741.'
        ],
        "notes": "Primary research articles with novel findings"
    },
    "S2": {
        "canonical": "Reviews & Editorials (synthesis, commentary)",
        "description": "Review articles, systematic reviews, meta-analyses, editorials, commentary",
        "examples": [
            'Lopez MT, Chen L. "Recent advances in cardio-oncology." Circulation Reviews. 2024;15(2):55–68.',
            'Singh P. "Editorial: Emerging roles of AI in clinical decision support." Lancet Digital Health. 2023;5(3):e127–e128.'
        ],
        "notes": "Synthesis/review of existing literature, opinion pieces"
    },
    "S3": {
        "canonical": "Books (authored or edited)",
        "description": "Full books, monographs, edited volumes",
        "examples": [
            'Rossi E (Ed.). Principles of Translational Oncology. Springer; 2023. ISBN 978-3-030-55555-4.',
            'Doe JQ, Kim SY. Foundations of Clinical Bioinformatics. Elsevier; 2022.'
        ],
        "notes": "Entire books, not chapters"
    },
    "S4": {
        "canonical": "Chapters (book chapters)",
        "description": "Individual chapters within edited volumes or textbooks",
        "examples": [
            'Doe JQ; "Deep learning in medical imaging." In: Artificial Intelligence in Medicine; Chen L, ed.; Elsevier; 2024:55–80.',
            'Gonzalez M; "Metabolomic profiling of cancer cells." In: Molecular Oncology Methods; Patel A & Li Y, eds.; Springer; 2022:211–230.'
        ],
        "notes": "Chapters in books edited by someone else"
    },
    "S5": {
        "canonical": "Non-Peer-Reviewed Publications (white papers, reports)",
        "description": "White papers, technical reports, practice guidelines (non-refereed)",
        "examples": [
            'Doe JQ, "Building equitable datasets for health AI." Policy Brief: Global Health Data Ethics. 2023.',
            'Nguyen T, "A hospital\'s experience adopting tele-ICU services." Hospital Management Review. 2021;18(2):4–10.'
        ],
        "notes": "Publications without formal peer review"
    },
    "S6": {
        "canonical": "Case Reports (single patient descriptions)",
        "description": "Clinical case reports, case series",
        "examples": [
            'Chen L, "Rare presentation of cardiac sarcoidosis mimicking STEMI." Chest. 2023;164(1):e15–e18.',
            'Kim SY, "Unexpected thyroid storm following checkpoint inhibitor therapy." Endocrine Practice. 2022;28(5):560–563.'
        ],
        "notes": "Simple case reports/case series (primarily clinical description)"
    },
    "S7": {
        "canonical": "In Review / Submitted / In Preparation (includes preprints)",
        "description": "Manuscripts under review, submitted, in preparation; includes preprints (bioRxiv, medRxiv, arXiv)",
        "examples": [
            'Doe JQ, "Spatial transcriptomics reveals immune gradients in RCC", submitted to Nature Cancer, Jan 2025.',
            'Patel A, "Automated ECG triage via transformer networks", under review at Circulation, Mar 2024.',
            'Chen L, "Immune checkpoint pathways in rare sarcomas", bioRxiv 2025; doi:10.1101/2025.02.18.123456.'
        ],
        "notes": "Not yet published (in review, submitted, in prep). Includes preprints on bioRxiv/medRxiv (DOI 10.1101)."
    },
    "S8": {
        "canonical": "Abstracts & Conference Proceedings",
        "description": "Published conference abstracts, meeting proceedings",
        "examples": [
            'Kim SY; "AI-driven CT analysis for COVID-19 severity." RSNA Annual Meeting; Chicago USA; 2022; Abstract A432.',
            'Shaikh N, Patil S, et al. "Development and Evaluation of Food Behavior Survey." The FASEB Journal. 2016;30(1 Suppl):33-7.'
        ],
        "notes": "Conference abstracts without full peer-reviewed articles. Common indicators: (1) Abstract journals (FASEB, Circulation supplements); (2) Volume(Suppl) format; (3) volume:abstract# citation; (4) explicit meeting/conference name."
    },
    "S9": {
        "canonical": "Other (Media, Podcasts, Blogs)",
        "description": "Podcasts, videos, blog posts, op-eds, webinars, media appearances",
        "examples": [
            '"Translating Genomics to the Bedside", Podcast – Clinician Voices, Apr 2024.',
            '"AI in Radiology: Beyond the Hype", Webinar hosted by RSNA, Dec 2023.'
        ],
        "notes": "Other formats (media, podcasts, non-traditional)"
    },
    "S11": {
        "canonical": "Software / Code",
        "description": "Software packages, code releases, computational tools",
        "examples": [
            'oncoTools v2.0, PyPI package (doi:10.5281/zenodo.1234567) – Python toolkit for oncology data (2024).',
            'neuroScanR, R package, CRAN (2023) – EEG signal preprocessing.'
        ],
        "notes": "⚠️ PROCESSING INTERMEDIATE ONLY - not official WCM section. Software ultimately rolls up to S9 (Other) or appropriate S1-S9 category"
    },
    "S12": {
        "canonical": "Data Resources (published datasets)",
        "description": "Published datasets in repositories (Zenodo, Dryad, Figshare)",
        "examples": [
            'Cancer Immune Atlas v2, Zenodo 10.5281/zenodo.9999999 (2024) – CC-BY 4.0.',
            'COVID-19 Chest CT Dataset, Dryad doi:10.5061/dryad.abc123 (2023).'
        ],
        "notes": "⚠️ PROCESSING INTERMEDIATE ONLY - not official WCM section. Data resources ultimately roll up to S9 (Other) or S16 (Data Descriptors) if peer-reviewed"
    },
    "S13": {
        "canonical": "Protocols / Methods",
        "description": "Published protocols, methodological papers (STAR Protocols, JOVE)",
        "examples": [
            'Doe JQ, "Spatial RNA-seq sample preparation", STAR Protocols 2023 doi:10.1016/j.xpro.2023.102345.',
            'Nguyen T, "Imaging flow cytometry for T-cell subsets", JOVE 2022 doi:10.3791/60012.'
        ],
        "notes": "⚠️ PROCESSING INTERMEDIATE ONLY - not official WCM section. Protocols ultimately roll up to S1 (Peer-Reviewed Articles) or S5 (Non-Peer-Reviewed)"
    },
    "S14": {
        "canonical": "Guidelines / Consensus Statements",
        "description": "Professional guidelines, consensus statements, position papers",
        "examples": [
            'ACC/AHA Task Force; "Guidelines for Heart Failure Management." Circulation. 2023.',
            'WHO Expert Panel; "Ethical AI in Healthcare Framework." Geneva 2024; ISBN 978-92-4-007555-3.'
        ],
        "notes": "⚠️ PROCESSING INTERMEDIATE ONLY - not official WCM section. Guidelines ultimately roll up to S1 (if peer-reviewed) or S5 (Non-Peer-Reviewed)"
    },
    "S15": {
        "canonical": "Registered Reports / Preregistrations",
        "description": "Registered reports, study preregistrations (OSF, clinical trial registrations)",
        "examples": [
            'Doe JQ; "Plan for spatial metabolomics in RCC." OSF Registered Report osf.io/x4y2b (2024) Stage 1.',
            'Nguyen T; "Neural correlates of empathy." Psych Science RR (2023) Stage 2.'
        ],
        "notes": "⚠️ PROCESSING INTERMEDIATE ONLY - not official WCM section. Registered reports ultimately roll up to S7 (In Review/Submitted) or S1 (if published)"
    },
    "S16": {
        "canonical": "Data Descriptor Articles",
        "description": "Peer-reviewed data descriptor articles (Scientific Data, GigaScience, Data in Brief)",
        "examples": [],
        "notes": "Peer-reviewed articles describing datasets"
    },

    # Clinical subsections (L1, L2, L3)
    "L1": {
        "canonical": "Clinical Practice",
        "description": "Where and how clinical work occurs: clinical sites, patient care activities",
        "examples": [
            "Attending Physician, Internal Medicine Clinic, 2020–present",
            "Clinical practice in General Surgery, NewYork-Presbyterian Hospital"
        ],
        "notes": "Clinical service delivery locations and activities"
    },
    "L2": {
        "canonical": "Clinical Innovations",
        "description": "New clinical methods, technologies, care models; quality improvement projects",
        "examples": [
            "Implementation of rapid sepsis protocol reducing mortality by 15%",
            "Developed novel tele-ICU consultation service",
            "Quality improvement initiative: reducing central line infections"
        ],
        "notes": "Clinical innovations, QI/QA projects, care delivery improvements"
    },
    "L3": {
        "canonical": "Clinical Leadership",
        "description": "Oversight roles in clinical programs: Director/Chief of clinical services",
        "examples": [
            "Medical Director, Heart Failure Clinic, 2019–present",
            "Clinical Service Chief, General Surgery, 2021–present"
        ],
        "notes": "Clinical program leadership and oversight"
    },

    # Research subsections (M1, M2A-D)
    "M1": {
        "canonical": "Research Activities (Summary)",
        "description": "Research overview, research statement, research interests",
        "examples": [
            "Principal Investigator, Immunogenomics of Cancer Study 2022–present"
        ],
        "notes": "Narrative description of research interests, not publication list"
    },
    "M2": {
        "canonical": "Research Support (Grants/Funding)",
        "description": "Grant funding, research support (parent category)",
        "examples": [
            "NIH R01 CA123456 'Tumor PD-L1 Pathways' (PI: J. Doe) 2022–2027 $1.2M"
        ],
        "notes": "Grant applications or funding, not publications"
    },
    "M2A": {
        "canonical": "Current Research Funding",
        "description": "Active grants and clinical trials (ongoing funding)",
        "examples": [
            "NIH R01 CA123456 2022-2027 (Active)",
            "PI, Phase II trial NCT12345678 2023-present"
        ],
        "notes": "Currently active grants AND clinical trials"
    },
    "M2B": {
        "canonical": "Past Research Funding",
        "description": "Completed grants and clinical trials",
        "examples": [
            "NIH R21 CA654321 2019-2022 (Completed)",
            "Site PI, TESTED trial NCT12345678 2020-2024 (Completed)"
        ],
        "notes": "Completed/past grants AND clinical trials"
    },
    "M2C": {
        "canonical": "Pending Research Funding",
        "description": "Submitted grants and planned clinical trials",
        "examples": [
            "NIH R01 (Pending review)",
            "Phase III trial protocol (Under development)"
        ],
        "notes": "Pending/submitted grants AND clinical trial proposals"
    },
    "M2D": {
        "canonical": "Patents & Innovations",
        "description": "Patents, patent applications, intellectual property",
        "examples": [
            "Patent US1234567B2 'Nanoparticle-based PD-L1 inhibitor' filed 2023, pending"
        ],
        "notes": "Patent disclosures, IP portfolio"
    },
    # NOTE: M4 clinical trial codes removed - clinical trials now classified as M2A/M2B/M2C

    # Extramural professional activities (Q1-Q4D)
    "Q1": {
        "canonical": "Leadership in Extramural Organizations",
        "description": "Leadership roles in external professional organizations (NOT simple membership)",
        "examples": [
            "Treasurer, International Society for Clinical Research 2021–2024",
            "President-Elect, State Medical Association 2023–2024"
        ],
        "notes": "External organizational leadership with role title (Chair, President, Officer). Simple membership → I"
    },
    "Q2": {
        "canonical": "Grant Reviewing / Study Section",
        "description": "External grant reviewing and study section service",
        "examples": [
            "Member, NIH Study Section 2020-2024",
            "Ad hoc reviewer, National Science Foundation 2021"
        ],
        "notes": "External grant review panels, study sections"
    },
    "Q3": {
        "canonical": "Editorial Activities (Combined Board + Reviewer)",
        "description": "Journal editorial roles including board membership and manuscript reviewing",
        "examples": [
            "Associate Editor, Journal of Clinical Oncology 2019–present",
            "Reviewer, Nature Medicine 2018–present"
        ],
        "notes": "Journal editorial activities. Often split into Q4C (board) and Q4D (reviewer) but may be combined as Q3"
    },
    "Q4C": {
        "canonical": "Editorial Board Membership",
        "description": "Editorial board membership for journals/publications (formal ongoing role)",
        "examples": [
            "Editorial Board Member, Journal of Adolescent Health March 2020–current",
            "Associate Editor, American Journal of Medicine 2018–present"
        ],
        "notes": "Formal editorial board positions. Pattern: 'Journal Name (date range)'. NOT simple peer review."
    },
    "Q4D": {
        "canonical": "Manuscript Reviewer / Abstract Reviewer",
        "description": "Ad hoc peer review service for manuscripts and abstracts",
        "examples": [
            "Reviewer for Journal of Mental Health 2009",
            "Manuscript reviewer, The Lancet 2020–present"
        ],
        "notes": "Manuscript reviewing activity. Pattern: 'Reviewer for Journal X'. NOT editorial board membership."
    },

    # Additional parent sections for cross-section error detection
    "A": {
        "canonical": "Personal Data / Contact Information",
        "description": "Contact details, identifiers (ORCID, NPI), web presence",
        "examples": [
            "jane.doe@example.edu",
            "ORCID: 0000-0002-1234-5678"
        ],
        "notes": "Personal info only - NOT employment positions"
    },
    "B": {
        "canonical": "Education",
        "description": "CV owner's formal education (degrees earned)",
        "examples": [
            "PhD Biochemistry, Stanford University, 2015",
            "MD, Yale School of Medicine, 2013"
        ],
        "notes": "YOUR education (not teaching others)"
    },
    "B1": {
        "canonical": "Academic Degrees",
        "description": "All degree-granting education: BA/BS, MA/MS, MBA, MPH, MD, PhD, JD, etc.",
        "examples": [
            "BA Biology, Harvard, 2010",
            "PhD Biochemistry, Stanford, 2015",
            "MD, Yale School of Medicine, 2013",
            "MPH Epidemiology, Johns Hopkins, 2012",
            "JD, Columbia Law School, 2008"
        ],
        "notes": "All academic degrees consolidated per WCM taxonomy"
    },
    "B2": {
        "canonical": "Other Educational Experiences",
        "description": "Non-degree education: certificates, short courses, CME/CEU where person is learner",
        "examples": [
            "Certificate in Clinical Research, Harvard Extension, 2018",
            "Data Science Bootcamp, General Assembly, 2019",
            "Prolonged Exposure Therapy Course, Washington University, 2024",
            "Advanced Statistics Workshop, Cold Spring Harbor, 2019"
        ],
        "notes": "Educational programs that do not confer degrees; includes CME/CEU, professional development as learner"
    },
    "C": {
        "canonical": "Postdoctoral Training",
        "description": "Postdoc, residency, fellowship positions",
        "examples": [
            "Postdoctoral Fellow, Cancer Biology, Stanford, 2015-2018",
            "Internal Medicine Residency, MGH, 2013-2016"
        ],
        "notes": "Training positions (not faculty appointments)"
    },
    "D": {
        "canonical": "Professional Positions & Employment",
        "description": "Faculty appointments, employment history, professional positions",
        "examples": [
            "Associate Professor, Department of Medicine, 2018-present",
            "Attending Physician, Internal Medicine, 2016-present"
        ],
        "notes": "YOUR employment positions (not mentees' positions)"
    },
    "D1": {
        "canonical": "Academic Appointments",
        "description": "University/academic medical center titles: teaching and research positions (Instructor, Assistant Professor, Associate Professor, Professor, etc.)",
        "examples": [
            "Associate Professor of Medicine, Cornell, 2018-present",
            "Assistant Professor of Oncology, Johns Hopkins, 2015-2018"
        ],
        "notes": "Academic appointments: faculty titles, teaching/research positions"
    },
    "D2": {
        "canonical": "Hospital Appointments",
        "description": "Clinical staff appointments: Assistant Attending, Attending, etc. Do not list administrative titles (Director, Chair) here.",
        "examples": [
            "Attending Physician, Internal Medicine, NewYork-Presbyterian, 2016-present",
            "Assistant Attending, Surgery, Memorial Sloan Kettering, 2014-2016"
        ],
        "notes": "Hospital clinical appointments (not academic titles, not admin roles)"
    },
    "D3": {
        "canonical": "Other Professional Positions & Employment",
        "description": "Non-academic/hospital roles: industry, private practice, consulting",
        "examples": [
            "Senior Data Scientist, Pfizer, 2019-2021",
            "Consulting Physician, Private Practice, 2017-present"
        ],
        "notes": "Industry, private practice, consulting, other non-academic/hospital positions"
    },
    "F1": {
        "canonical": "Licensure",
        "description": "Government-issued practice permissions: state licenses, DEA, NPI numbers",
        "examples": [
            "Medical License: New York State #123456, active 2015-present",
            "DEA Registration: AB1234567",
            "NPI Number: 1234567890"
        ],
        "notes": "State medical licenses, DEA registration, NPI numbers, other practice permissions"
    },
    "F2": {
        "canonical": "Board Certification",
        "description": "Specialty/subspecialty credentials from professional boards",
        "examples": [
            "Board Certified in Internal Medicine, ABIM, 2016",
            "Board Certified in Oncology, ABIM, 2019"
        ],
        "notes": "Board certifications from ABIM, specialty boards, etc."
    },
    "H": {
        "canonical": "Honors and Awards",
        "description": "Academic honors, awards, recognitions",
        "examples": [
            "NIH Early Career Award, 2018",
            "Best Paper Award, American Cancer Society, 2020"
        ],
        "notes": "Awards and honors received"
    },
    "I": {
        "canonical": "Professional Organizations & Society Memberships",
        "description": "Simple membership in professional societies (NOT leadership roles)",
        "examples": [
            "American Public Health Association (APHA) member",
            "Fellow, American College of Physicians",
            "Member, American Sociological Association since 2015"
        ],
        "notes": "Simple membership. Leadership roles → Q1"
    },
    "K": {
        "canonical": "Educational Contributions / Teaching",
        "description": "Teaching activities, course instruction, curriculum development",
        "examples": [
            "Instructor, Medical Ethics Course, 2020-present",
            "Clinical preceptor, Family Medicine clerkship"
        ],
        "notes": "YOU teaching others (not your education)"
    },
    "K1": {
        "canonical": "Didactic Teaching (courses taught)",
        "description": "Formal courses taught to students",
        "examples": ["Instructor, Sociology of the Family 2011-present"],
        "notes": "Course instruction"
    },
    "K2": {
        "canonical": "Clinical Teaching",
        "description": "Clinical teaching and supervision",
        "examples": ["Attending physician, Internal Medicine service 2020–present"],
        "notes": "Clinical supervision/teaching"
    },
    "N": {
        "canonical": "Mentoring",
        "description": "Formal supervision and guidance of trainees/junior faculty",
        "examples": [
            "Primary mentor, PhD candidate in Immunology (2019–present)",
            "Advisor, MSc thesis on Clinical Data Mining"
        ],
        "notes": "Mentoring activity. NOT your own positions."
    },
    "O": {
        "canonical": "Institutional Leadership Activities (executive)",
        "description": "Executive/oversight roles INSIDE institution with formal authority",
        "examples": [
            "Program Director, Residency Training 2020–present",
            "Section Chief, Hospital Medicine 2019–2023",
            "Vice Chair for Education 2021–present"
        ],
        "notes": "Executive titles: President, Chair, Dean, Director, Chief. Committee membership → P"
    },
    "P": {
        "canonical": "Institutional Administrative Activities",
        "description": "Committee service, governance WITHOUT executive authority",
        "examples": [
            "Member, Faculty Senate Executive Committee 2021–present",
            "Co-Chair, Curriculum Committee 2022–present"
        ],
        "notes": "Committee membership, shared roles. Executive authority → O"
    },
    "R": {
        "canonical": "Invited Presentations",
        "description": "Invited speaking engagements and presentations",
        "examples": [
            "Keynote, Boston Medical Association, 2023",
            "Grand rounds, Massachusetts General Hospital, 2022"
        ],
        "notes": "Invited talks, presentations"
    },
    "R1": {
        "canonical": "Invited Keynotes, Named Lectures, Plenaries",
        "description": "Major invited presentations",
        "examples": ["Keynote speaker, Annual Conference, 2024"],
        "notes": "High-profile invited talks"
    },
    "R2": {
        "canonical": "Grand Rounds, Visiting Professorships, Departmental Seminars",
        "description": "Departmental and institutional invited talks",
        "examples": ["Grand rounds, Massachusetts General Hospital, 2022"],
        "notes": "Institutional invited presentations"
    },
    "R3": {
        "canonical": "Invited Panels, Workshops, Symposia",
        "description": "Panel discussions and workshop presentations",
        "examples": ["Panelist, AI in Medicine Symposium, 2023"],
        "notes": "Panel/workshop participation"
    },
    "T": {
        "canonical": "Appendix / Other",
        "description": "Additional professional information not fitting standard categories",
        "examples": [
            "Languages: English (Native), Spanish (Fluent)",
            "Professional interests and hobbies"
        ],
        "notes": "Miscellaneous professional info"
    },
}


def get_s_taxonomy_reference() -> Dict[str, Dict]:
    """Return the complete S-taxonomy reference."""
    return S_TAXONOMY


def get_s_taxonomy_for_ids(section_ids: List[str]) -> Dict[str, Dict]:
    """Get taxonomy info for specific section IDs."""
    return {
        sid: S_TAXONOMY[sid]
        for sid in section_ids
        if sid in S_TAXONOMY
    }
