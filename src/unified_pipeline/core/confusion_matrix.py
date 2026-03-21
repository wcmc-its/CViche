"""
Comprehensive Section Confusion Matrix

Defines which sections are commonly confused, how to distinguish them,
and provides routing rules with examples for disambiguation.

This replaces and consolidates:
- extract_unextracted_fallback.py WCM_SECTION_CONTEXTS
- Routing rules from legacy extraction scripts
- Confusion detection logic

IMPORTANT - BIDIRECTIONAL CONFUSION SYMMETRY:
If section A can be confused with section B, then B can also be confused with A.
This is a fundamental principle: confusion arises from AMBIGUOUS TERMS that apply
to multiple sections, and the ambiguity exists FROM EITHER DIRECTION.

Example: "Education" creates confusion between:
  - B (Education Received) ↔ K (Teaching Provided)

When adding confusion relationships:
1. If `education_and_training` (B) lists `educational_contributions` (K) as alternative
2. Then `educational_contributions` (K) MUST list `education_and_training` (B) as alternative
3. Both should reference the SAME ambiguous terms with INVERSE disambiguation guidance

This ensures consistency, maintainability, and complete confusion coverage.
See CV_PIPELINE_TARGET_ARCHITECTURE.md Principle #6 for details.

Updated based on classification errors and user feedback.
Last major update: 2025-11-13
"""

import re
from typing import Dict, List, Any

# =============================================================================
# COMPREHENSIVE CONFUSION MATRIX
# =============================================================================

SECTION_CONFUSION_MATRIX = {

    # =========================================================================
    # BIBLIOGRAPHY (S) - HIGHEST CONFUSION RISK
    # =========================================================================
    'bibliography': {
        'primary_parent': 'S',
        'canonical_name': 'Bibliography',
        'confusion_risk': 'high',
        'description': 'Publicly disseminated scholarly works across all publication types. Strategy: Map to ~21+ detailed subsections (S1-S30) initially, then roll up to S1-S9.',

        'core_principles': [
            'S1 = Original empirical research (new data, methods/results sections)',
            'S2 = Peer-reviewed interpretive/methodological (reviews, editorials, protocols, guidelines, meta-research)',
            'Prefer specificity: use S10–S30 when clearly indicated; otherwise fall back to S1–S9',
            'Published vs pre-publication: if journal issue/pages/DOI (not 10.1101/*) → published; otherwise check S7/S10/S15',
            'Registry/Repository artifacts (software, datasets, apps, regulatory filings) do NOT go in S1/S2 unless peer-reviewed article about them'
        ],

        'decision_order': [
            '1) PRE-PUBLICATION? → S7 (submissions/in prep/in press) or S10 (preprints) or S15 (registered reports)',
            '2) JOURNAL ARTICLE? If original data → S1; if review/editorial/methods/guideline/meta-research → S2',
            '3) SINGLE-PATIENT clinical observation? → S6',
            '4) ABSTRACT/PROCEEDING only (no full paper)? → S8',
            '5) SOFTWARE/CODE, DATASET, DIGITAL APP/TOOL, REGULATORY SUBMISSION, POLICY/ADVISORY? → S11/S12/S24/S21/S25/S26/S28',
            '6) EDUCATIONAL CURRICULUM or ONLINE LEARNING? → S22 or S23',
            '7) Books/Chapters → S3/S4; Media/podcasts → S9'
        ],

        'precedence_rules': [
            'Preprint/Prereg status overrides: S10/S15 > S7 > others',
            'Journal article types override generic: S1/S2 > S5/S9',
            'Case reports override S1: S6 > S1 when explicitly single-patient/small series',
            'Abstract-only overrides: S8 > S1/S2 if no full paper',
            'Repository/Registry artifacts override media: S11/S12/S21/S24 > S9'
        ],

        'routing_rules': {
            'peer_reviewed_vs_review': 'S1 if original research with methods/results. S2 if review article, editorial, commentary, protocol (peer-reviewed venue), guideline, or meta-research.',
            'article_vs_case_report': 'S1 for research studies. S6 for single patient case descriptions.',
            'published_vs_preprint': 'S1-S6 if published in journal. S10 if bioRxiv/medRxiv/arXiv preprint (DOI starts with 10.1101).',
            'abstract_detection': 'S8 if conference abstract/poster - CHECK FIRST, OVERRIDES S1. DEFINITIVE PATTERN: volume:abstract# with 3-4 digit abstract number (e.g., "30:1153.1154") → This is an ABSTRACT, NOT a full journal article. Other indicators: (1) abstract journals (FASEB Journal, Circulation, AHA, ASCO, RSNA); (2) "Suppl" or "Supplement"; (3) explicit "Abstract" or "Poster" + meeting name. If no abstract indicators, then S1 for full articles.',
            'manuscript_status': 'S7 for unpublished: "submitted", "under review", "in preparation", "in press", "accepted", "revise and resubmit".',
            'data_paper_vs_dataset': 'S16 if peer-reviewed data descriptor article (Scientific Data, GigaScience, Data in Brief); S12 if repository dataset deposit (data service, Zenodo, Dryad, Figshare) without peer review.',
            'software_article_vs_release': 'S1/S2 if peer-reviewed article about tool; S11 if code/package release itself.',
            'regulatory_submissions': 'S21 for IND/IDE/IRB/NCT registry entries without peer-reviewed article.',
            'educational_materials': 'S22 for curricula/CME; S23 for MOOCs/online courses; both roll up to S9.',
            'policy_testimony': 'S25 for government/society testimony/advisory reports → rolls up to S5.',
            'meta_research': 'S30 for peer-reviewed studies on research methods/reproducibility → rolls up to S2.'
        },

        'trigger_keywords': {
            'preprint': ['biorxiv', 'medrxiv', 'arxiv', 'preprint', 'osf preprint', '10.1101/'],
            'abstract': ['faseb journal', 'circulation', 'aha ', 'asco ', 'aacr ', 'rsna', 'suppl', 'supplement', 'abstract', 'poster'],
            'case_report': ['case report', 'case study', 'rare presentation', 'patient description'],
            'review': ['review', 'editorial', 'commentary', 'perspective', 'opinion'],
            'unpublished': ['submitted', 'under review', 'in preparation', 'in press', 'accepted', 'revise and resubmit'],
            'data': ['scientific data', 'gigascience', 'data in brief', 'zenodo', 'dryad', 'figshare', 'dataset'],
            'report': ['discussion paper', 'policy brief', 'working paper', 'white paper', 'technical report'],
            'regulatory': ['clinicaltrials.gov', 'nct', 'ind', 'ide', 'irb'],
            'protocol': ['star protocols', 'jove', 'nature protocols', 'protocol exchange', 'protocols.io'],
            'guideline': ['guideline', 'consensus', 'recommendation', 'statement', 'acc/aha', 'who', 'cdc'],
            'mentoring_misclassified': ['directed study', 'directed studies', 'independent study', 'thesis supervision', 'dissertation supervision', 'thesis committee', 'student advising']
        },

        'alternative_parents': [
            # CONFUSION AREA: S2 ↔ Q3 (Reviews/Editorials vs Editorial Service)
            {
                'parent_id': 'extramural_professional_activities',
                'parent_name': 'Q. Extramural Professional Responsibilities',
                'reason': 'CONFUSION AREA: Manuscript reviewer/editorial activity vs editorial publications',
                'show_all_children': True,
                'trigger_keywords': ['reviewer', 'review for', 'manuscript review', 'editorial board', 'editor', 'guest editor', 'special issue', 'associate editor'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA: Reviews/Editorials (S2) vs Editorial Service (Q3/Q4) ===',
                    '',
                    'KEY QUESTION: Is the emphasis on CONTENT PRODUCED (publication) or ROLE/POSITION (service)?',
                    '',
                    '→ S2 (Reviews & Editorials - Publications) if:',
                    '  • Appears in bibliography with journal name, year, volume/pages',
                    '  • Specific special issue/collection that can be cited',
                    '  • Guest-edited issue presented as scholarly output',
                    '  • Example: "Guest Editor, Special Issue of Depression and Anxiety on ECT and TMS, 2000"',
                    '  • Context: Listed among publications, citable product',
                    '',
                    '→ Q3/Q4 (Editorial Service - Professional Activities) if:',
                    '  • Appears among "Editorial Board Memberships", "Editorial Roles", "Professional Service"',
                    '  • Lists durations of service: "2000–2010", multiple journals',
                    '  • No specific issue volume/pages - just ongoing role',
                    '  • Examples:',
                    '    - "Editorial Board Member, Journal of Psychiatry, 2010–present" → Q4C',
                    '    - "Ad hoc reviewer for JAMA, Lancet, NEJM" → Q4D',
                    '    - "Associate Editor, Brain Stimulation, 2015–2020" → Q4C',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • S2 = "Scholarly product" (citable editorial work, special issues as outputs)',
                    '  • Q3/Q4 = "Service role" (editorial board membership, reviewing)',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Guest Editor, Special Issue..." → Check context:',
                    '    - In bibliography with year/volume → S2 (publication)',
                    '    - In service list with date range → Q4C (editorial service)',
                    '  • "Editorial Board" → Almost always Q4C (service)',
                    '  • "Wrote editorial for..." with full citation → S2 (publication)',
                    '',
                    'EXAMPLES:',
                    '  S2: "Smith J (Guest Ed.). \'Neuromodulation Advances.\' Brain Stimul. 2020;13(Spec Issue):1-200."',
                    '      → Special issue as scholarly output with full citation',
                    '  Q4C: "Guest Editor, Special Section on TMS, Brain Stimulation, 2018–2019"',
                    '       → Editorial role with duration (service)',
                    '  Q4C: "Editorial Board Member, Depression and Anxiety, 2000–present"',
                    '       → Ongoing board membership (service)',
                    '  Q4D: "Ad hoc manuscript reviewer for 15+ journals including JAMA, Lancet"',
                    '       → Reviewing activity (service)',
                    '  S2: "Lisanby SH. \'Editorial: The future of brain stimulation.\' J ECT. 2015;31(1):1-2."',
                    '      → Published editorial (scholarly output)',
                    '',
                    'SECTION HEADER CLUES:',
                    '  • "PUBLICATIONS", "BIBLIOGRAPHY" → Likely S2',
                    '  • "EDITORIAL ACTIVITIES", "PROFESSIONAL SERVICE", "JOURNAL ACTIVITIES" → Likely Q',
                    '  • "PEER REVIEW" → Likely Q4D (manuscript reviewing)',
                    '',
                    'IF listing journals where person REVIEWED manuscripts → Q4D Manuscript Reviewer',
                    'IF listing journals where person is BOARD MEMBER → Q4C Editorial Board',
                    'IF listing person\'s OWN editorial publications with citations → S2 (Bibliography)'
                ]
            },
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research',
                'reason': 'Research description/narrative (not publication list)',
                'show_all_children': True,
                'trigger_keywords': ['research focuses', 'my research', 'interests include', 'investigates'],
                'disambiguation_guidance': [
                    'IF narrative description of research interests → M1 Research Overview',
                    'IF list of specific publications with citations → S (Bibliography)',
                    'IF grant applications or funding → M2 Research Funding'
                ]
            },
            # CONFUSION AREA #7: S ↔ N (Bibliography vs Mentoring - mentee publications/theses)
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'CONFUSION AREA #7: Student/trainee advising misclassified as publications',
                'show_all_children': True,
                'trigger_keywords': ['directed study', 'thesis', 'dissertation committee', 'supervised', 'mentee', 'advisee', 'student publications'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #7: Bibliography (S) vs Mentoring (N) ===',
                    '',
                    'KEY QUESTION: Is this YOUR publication or MENTEE documentation?',
                    '',
                    '→ N (Mentoring) if:',
                    '  • Section header: "MENTEES", "ADVISEES", "STUDENTS SUPERVISED"',
                    '  • Mentee name as primary: "John Doe (advisee): [thesis title]"',
                    '  • Supervision context: "Thesis committee member for...", "Dissertation advisor to..."',
                    '  • Student projects listed: "Directed studies supervised:", "Independent study students:"',
                    '  • Mentee outcomes: "Current position:", "Now at Harvard", "Defended 2023"',
                    '  • Focus: Documenting mentoring relationships and supervised work',
                    '',
                    '→ S (Bibliography) if:',
                    '  • Publication citation format: Author list, title, journal, year, DOI',
                    '  • You are listed as author/co-author (even if with student)',
                    '  • Published thesis/dissertation in formal citation format',
                    '  • No mentee context provided (just bibliographic info)',
                    '  • Focus: Documenting YOUR scholarly publications',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • N = "Mentoring record" (who you supervised, what they did)',
                    '  • S = "Publication record" (what you published, including with students)',
                    '',
                    'THESIS/DISSERTATION HANDLING:',
                    '  • If listing student + their thesis as MENTEE → N3/N4',
                    '  • If citing thesis as YOUR co-authored publication → S (bibliography)',
                    '  • If YOU are the student → B1 (your own degree)',
                    '  • Committee membership without supervision → May be Q or N depending on role',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Doe J (advisee). \'Machine Learning in Healthcare.\' PhD Thesis, 2023"',
                    '    → N (mentee record with thesis title)',
                    '  • "Smith A, Doe J. \'ML in Healthcare.\' J Med AI. 2024;10:123"',
                    '    → S1 (publication, even if Doe was your student)',
                    '  • "Directed study: Doe J - Analyzing EHR data for sepsis prediction"',
                    '    → N (mentoring activity)',
                    '  • Section titled "STUDENT PUBLICATIONS" with full citations',
                    '    → Could be N (if focused on students) OR S (if your co-authored pubs)',
                    '',
                    'EXAMPLES:',
                    '  N3: "Jane Smith, PhD student 2020-2024. Thesis: \'Deep Learning for Cancer Imaging\'"',
                    '       "  Current position: Assistant Professor, Stanford University"',
                    '       → Mentee record with thesis and outcome',
                    '  S3: "Smith J. Deep Learning for Cancer Imaging. [PhD Dissertation]. MIT; 2024."',
                    '       → Formal thesis citation (rare, but possible if notable)',
                    '  N4: "Directed Studies Supervised:"',
                    '       "  - John Doe: \'RNA-seq analysis methods\' (Spring 2023)"',
                    '       "  - Mary Lee: \'Clinical trial design\' (Fall 2023)"',
                    '       → List of mentees with project topics',
                    '  S1: "Patel R, Doe J, Smith M. \'Novel biomarkers for sepsis.\' JAMA. 2024;330(5):450-458."',
                    '       → Publication with student co-author (still YOUR publication)',
                    '',
                    'SECTION HEADER RULES:',
                    '  • "MENTEES", "ADVISEES", "TRAINEES", "STUDENTS SUPERVISED" → Strong bias to N',
                    '  • "PUBLICATIONS", "BIBLIOGRAPHY", "PEER-REVIEWED" → Strong bias to S',
                    '  • "STUDENT PUBLICATIONS" → Ambiguous, check content structure',
                    '',
                    'CHECK section header: "MENTEES", "ADVISEES" → strongly suggests N'
                ]
            },
            {
                'parent_id': 'invitations_to_speak',
                'parent_name': 'R. Invitations to Speak/Present',
                'reason': 'Publication/abstract vs. invited talk',
                'show_all_children': False,
                'trigger_keywords': ['invited', 'keynote', 'plenary', 'grand rounds', 'visiting professor'],
                'disambiguation_guidance': [
                    'IF published work (journal article, proceeding) → S Bibliography',
                    'IF invited speaking engagement (not published) → R Invitations to Speak',
                    'S indicators: journal name, DOI, PMID, pages',
                    'R indicators: "invited", "keynote speaker", "plenary"'
                ]
            },
            # CONFUSION AREA #5: S11 ↔ M2D (Software/Code Releases vs Patents & Inventions)
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research',
                'reason': 'CONFUSION AREA #5: Software releases vs patents/inventions',
                'show_all_children': False,
                'trigger_keywords': ['patent', 'invention', 'provisional', 'filed', 'licensing', 'intellectual property', 'ip'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #5: Software/Code (S11) vs Patents (M2D) ===',
                    '',
                    'KEY QUESTION: Is this SOFTWARE or INTELLECTUAL PROPERTY?',
                    '',
                    '→ S11 (Software/Code) if:',
                    '  • Code repository: GitHub, GitLab, Bitbucket URL',
                    '  • Package release: PyPI, CRAN, npm, Bioconductor',
                    '  • Software DOI: Zenodo, Software Heritage',
                    '  • Open-source license: MIT, GPL, Apache',
                    '  • Version numbers: v1.0, release 2.3.1',
                    '  • Focus: Publicly available code/tools for research community',
                    '',
                    '→ M2D (Patents & Inventions) if:',
                    '  • Patent filing: "filed", "provisional", "issued"',
                    '  • Patent numbers: US1234567B2, PCT/US2023/12345',
                    '  • Licensing agreements: commercial licenses, royalty terms',
                    '  • Invention disclosures: technology transfer office',
                    '  • IP protection: "patent pending", "proprietary"',
                    '  • Focus: Legal protection of intellectual property',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Software patent" → M2D (focus on patent)',
                    '  • "Open-source software with patent claims" → Usually S11 (focus on distribution)',
                    '  • "Zenodo-archived code for patented method" → S11 (the code itself)',
                    '  • "Patent describing novel algorithm" → M2D (patent filing is primary)',
                    '',
                    'EXAMPLES:',
                    '  S11: "immunoTools v2.0, PyPI package (doi:10.5281/zenodo.1234567) – Python toolkit (2024)"',
                    '       → Software release with version and repository',
                    '  M2D: "Patent US9876543B2 \'Machine learning diagnostic method\' issued 2023"',
                    '       → Patent filing with number',
                    '  S11: "EHR-Parser 1.3, GitHub repo, https://github.com/user/EHR-Parser (2022)"',
                    '       → Open-source code repository',
                    '  M2D: "Provisional patent \'AI-powered ECG analysis system\' filed 2024"',
                    '       → Patent filing (even if provisional)',
                    '  M2D: "Licensing agreement for \'DiagnosticAI\' software with MedTech Inc. 2024"',
                    '       → Commercial licensing (IP focus)',
                    '',
                    'NOTE: Software CAN appear in both places:',
                    '  • S11 for the code release/repository',
                    '  • M2D for patents covering the underlying algorithms'
                ]
            },
            # CONFUSION AREA #6: S12 ↔ M1 (Datasets vs Research Activities)
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research',
                'reason': 'CONFUSION AREA #6: Dataset deposits vs research activity descriptions',
                'show_all_children': False,
                'trigger_keywords': ['dataset', 'data repository', 'research activities', 'research statement', 'data collection'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #6: Datasets (S12) vs Research Activities (M1) ===',
                    '',
                    'KEY QUESTION: Is this a DATASET DEPOSIT or RESEARCH DESCRIPTION?',
                    '',
                    '→ S12 (Datasets) if:',
                    '  • Repository deposit: Zenodo, Dryad, Figshare, GenBank, GEO',
                    '  • Dataset DOI: 10.5281/zenodo.*, 10.6084/*, etc.',
                    '  • Versioned data release: "v1.0", "Release 2023"',
                    '  • Accession numbers: GSE123456, PRJNA123456',
                    '  • Data descriptor: size, format, contents of dataset',
                    '  • Focus: Citable data artifact available for reuse',
                    '',
                    '→ M1 (Research Activities/Statement) if:',
                    '  • Research narrative: "My research focuses on...", "I study..."',
                    '  • Data collection as activity: "collected 500 patient samples"',
                    '  • Project description: aims, methods, ongoing work',
                    '  • Research interests/vision statement',
                    '  • IRB protocols describing data collection',
                    '  • Focus: Describing research work, not citing dataset',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • S12 = "Product" (dataset as publication)',
                    '  • M1 = "Process" (research activity description)',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Created dataset of 10K patient records" → M1 (activity)',
                    '  • "Patient Records Dataset v2.0 (Zenodo doi:10.5281/...)" → S12 (deposit)',
                    '  • "Data collection protocol for COVID study" → M1 (research description)',
                    '  • "COVID-19 Patient Database, Figshare 2023" → S12 (dataset deposit)',
                    '',
                    'EXAMPLES:',
                    '  S12: "Cancer Genomics Dataset v1.0, Zenodo (doi:10.5281/zenodo.7654321) – 500 samples (2024)"',
                    '       → Specific dataset deposit with DOI',
                    '  M1: "My research activities include large-scale genomic data collection from cancer patients"',
                    '       → Research activity description',
                    '  S12: "GSE123456 - RNA-seq profiles of renal carcinoma, GEO 2023"',
                    '       → Repository accession number',
                    '  M1: "IRB Protocol #12345: Prospective data collection of EHR records for ML model development"',
                    '       → Research protocol/activity',
                    '  S12: "COVID-19 Imaging Database, TCIA (doi:10.7937/...), 1000 CT scans"',
                    '       → Data repository deposit',
                    '',
                    'NOTE: Peer-reviewed data descriptor ARTICLES → S16 (not S12)',
                    '  Example: "Chen et al. \'Multi-omics dataset of liver cancer\' Scientific Data 2024" → S16'
                ]
            },
            # CONFUSION AREA #10: S3/S4 ↔ K (Books & Chapters vs Educational Contributions)
            {
                'parent_id': 'educational_contributions',
                'parent_name': 'K. Educational Contributions',
                'reason': 'CONFUSION AREA #10: Published books/chapters vs educational materials',
                'show_all_children': True,
                'trigger_keywords': ['textbook', 'handbook', 'manual', 'curriculum', 'course materials', 'syllabus', 'lecture notes'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #10: Books/Chapters (S3/S4) vs Educational Materials (K5/K6) ===',
                    '',
                    'KEY QUESTION: Is this PUBLISHED SCHOLARSHIP or TEACHING MATERIALS?',
                    '',
                    '→ S3 (Books) / S4 (Chapters) if:',
                    '  • Formal publication: ISBN, publisher (Springer, Wiley, Elsevier, Oxford)',
                    '  • Peer-reviewed academic book/chapter',
                    '  • Publicly available for purchase/library access',
                    '  • Citation format: Author, Title, Publisher, Year',
                    '  • DOI for chapter (10.1007/*, 10.1002/*, etc.)',
                    '  • Focus: Scholarly contribution to field',
                    '',
                    '→ K5 (Educational Materials) / K6 (Curriculum Development) if:',
                    '  • Course-specific materials: syllabus, lecture slides, handouts',
                    '  • Curriculum development: course design, module creation',
                    '  • Internal teaching resources (not formally published)',
                    '  • CME/CEU materials for professional training',
                    '  • Online course content (Coursera, edX modules)',
                    '  • Focus: Teaching activity, not scholarly publication',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • S3/S4 = "Publication" (peer-reviewed, ISBN, publicly distributed)',
                    '  • K5/K6 = "Teaching Tool" (course materials, internal use)',
                    '',
                    'TEXTBOOK RULES:',
                    '  • Published textbook with ISBN → S3 (Book)',
                    '  • Course syllabus/materials USING textbook → K (Educational)',
                    '  • "Developed curriculum based on textbook X" → K6 (Curriculum)',
                    '  • "Authored \'Clinical Methods\' (Springer 2023)" → S3 (Book)',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Handbook" with ISBN/publisher → S3 (published book)',
                    '  • "Handbook" as internal course guide → K5 (educational material)',
                    '  • "Manual of Clinical Procedures" (McGraw-Hill) → S3 (published)',
                    '  • "Laboratory Manual for BIOC 301" → K5 (course material)',
                    '',
                    'EXAMPLES:',
                    '  S3: "Doe JQ (Ed.). Principles of Translational Oncology. Springer; 2023. ISBN 978-3-030-55555-4"',
                    '       → Published book with ISBN',
                    '  K6: "Developed new oncology curriculum for medical students, 2023"',
                    '       → Curriculum development activity',
                    '  S4: "Chen L; \'Deep learning in medical imaging.\' In: AI in Medicine; Elsevier; 2024:55–80"',
                    '       → Published book chapter',
                    '  K5: "Created lecture series on deep learning for Radiology 501 course"',
                    '       → Teaching materials for course',
                    '  S3: "Clinical Cardiology Handbook, 4th Ed. (Wiley-Blackwell 2022) ISBN 978-1-119-12345-6"',
                    '       → Published handbook',
                    '  K5: "Student Handbook for Cardiology Clerkship (internal document)"',
                    '       → Internal educational material',
                    '',
                    'SPECIAL CASES:',
                    '  • Open Educational Resources (OER) with DOI → S3/S4 if peer-reviewed',
                    '  • Online course textbook (self-published) → K5 if course-specific',
                    '  • CME module published in journal → S (bibliography)',
                    '  • CME module delivered at conference → K4 (CME)',
                    '  • Lecture notes published as book chapter → S4 (publication takes precedence)'
                ]
            }
        ],

        'subsection_examples': {
            'S1': {
                'title': 'Peer-Reviewed Research Articles (original research)',
                'examples': [
                    'Chen L, Patel A, Nguyen T. "Single-cell mapping of renal carcinoma immune niches." Nature Medicine. 2024;30:1123–1135. doi:10.1038/s41591-024-01890-y',
                    'Gonzalez M, Li Y, Ahmed R. "Machine learning prediction of sepsis mortality using EHR data." JAMA Netw Open. 2023;6(4):e238741.',
                    'Smith JA, Doe JQ, Brown R. "Epigenetic regulation of T-cell exhaustion." Cell Reports. 2022;40(7):110412.',
                    'Kim SY, et al. "Genome-wide CRISPR screen identifies novel tumor suppressors." Science Advances. 2021;7:eabg5123.'
                ]
            },
            'S2': {
                'title': 'Reviews & Editorials (synthesis, commentary)',
                'examples': [
                    'Lopez MT, Chen L. "Recent advances in cardio-oncology." Circulation Reviews. 2024;15(2):55–68.',
                    'Singh P. "Editorial: Emerging roles of AI in clinical decision support." Lancet Digital Health. 2023;5(3):e127–e128.',
                    'Doe JQ, Ahmed R. "Targeting the tumor microenvironment: A decade in review." Annual Review of Medicine. 2022;73:101–118.'
                ]
            },
            'S3': {
                'title': 'Books (authored or edited)',
                'examples': [
                    'Rossi E (Ed.). Principles of Translational Oncology. Springer; 2023. ISBN 978-3-030-55555-4.',
                    'Doe JQ, Kim SY. Foundations of Clinical Bioinformatics. Elsevier; 2022.',
                    'Liang H, Patel A, eds. Telemedicine and Digital Health Handbook. Wiley; 2021.'
                ]
            },
            'S4': {
                'title': 'Chapters (book chapters)',
                'examples': [
                    'Doe JQ; "Deep learning in medical imaging." In: Artificial Intelligence in Medicine; Chen L, ed.; Elsevier; 2024:55–80.',
                    'Gonzalez M; "Metabolomic profiling of cancer cells." In: Molecular Oncology Methods; Patel A & Li Y, eds.; Springer; 2022:211–230.'
                ]
            },
            'S5': {
                'title': 'Non-Peer-Reviewed Publications (white papers, reports)',
                'examples': [
                    'Doe JQ, "Building equitable datasets for health AI." Policy Brief: Global Health Data Ethics. 2023.',
                    'Nguyen T, "A hospital\'s experience adopting tele-ICU services." Hospital Management Review. 2021;18(2):4–10.'
                ]
            },
            'S6': {
                'title': 'Case Reports (single patient descriptions)',
                'examples': [
                    'Chen L, "Rare presentation of cardiac sarcoidosis mimicking STEMI." Chest. 2023;164(1):e15–e18.',
                    'Kim SY, "Unexpected thyroid storm following checkpoint inhibitor therapy." Endocrine Practice. 2022;28(5):560–563.'
                ]
            },
            'S7': {
                'title': 'In Review / Submitted / In Preparation',
                'examples': [
                    'Doe JQ, "Spatial transcriptomics reveals immune gradients in RCC", submitted to Nature Cancer, Jan 2025.',
                    'Patel A, "Automated ECG triage via transformer networks", under review at Circulation, Mar 2024.',
                    'Rossi E, "Patient-reported outcomes in tele-rehabilitation", manuscript in preparation 2025.'
                ]
            },
            'S8': {
                'title': 'Abstracts & Conference Proceedings',
                'description': 'Conference abstracts without full peer-reviewed articles. Common indicators: (1) Abstract journals (FASEB, Circulation supplements); (2) Volume(Suppl) format; (3) volume:abstract# citation; (4) explicit meeting/conference name.',
                'examples': [
                    'Kim SY; "AI-driven CT analysis for COVID-19 severity." RSNA Annual Meeting; Chicago USA; 2022; Abstract A432.',
                    'Shaikh N, Patil S, et al. "Development and Evaluation of Food Behavior Survey." The FASEB Journal. 2016;30(1 Suppl):33-7.',
                    'Johnson A, Smith B. "Cardiac biomarkers in sepsis." Circulation. 2019;140(Suppl 2):A12345.',
                    'Chen L. "Machine learning for image segmentation." CVPR Annual Conference; 2023; Poster 45.'
                ]
            },
            'S9': {
                'title': 'Other (Media, Podcasts, Blogs)',
                'examples': [
                    '"Translating Genomics to the Bedside", Podcast – Clinician Voices, Apr 2024.',
                    '"AI in Radiology: Beyond the Hype", Webinar hosted by RSNA, Dec 2023.',
                    '"Data Science in Hospitals", Blog post on Medium by Doe JQ, 2021.'
                ]
            },
            'S10': {
                'title': 'Preprints (bioRxiv, medRxiv, arXiv)',
                'examples': [
                    'Doe JQ, "Immune checkpoint pathways in rare sarcomas", bioRxiv 2025; doi:10.1101/2025.02.18.123456.',
                    'Nguyen T, "Predicting mortality in COVID-19 with hybrid CNN-LSTM", medRxiv 2023; 10.1101/2023.04.05.543210.',
                    'Patel A, "Cross-institutional EHR harmonization pipeline", arXiv 2022; arXiv:2207.01345 v2.'
                ]
            },
            'S11': {
                'title': 'Software / Code',
                'examples': [
                    'oncoTools v2.0, PyPI package (doi:10.5281/zenodo.1234567) – Python toolkit for oncology data (2024).',
                    'neuroScanR, R package, CRAN (2023) – EEG signal preprocessing.',
                    'EHR-Parser 1.3, GitHub repo, https://github.com/ahmedr/EHR-Parser (2022).'
                ]
            },
            'S12': {
                'title': 'Data Resources (published datasets)',
                'examples': [
                    'Cancer Immune Atlas v2, Zenodo 10.5281/zenodo.9999999 (2024) – CC-BY 4.0.',
                    'COVID-19 Chest CT Dataset, Dryad doi:10.5061/dryad.abc123 (2023).',
                    'Global Rare Disease Registry, Figshare 10.6084/m9.figshare.19753124 (2022).'
                ]
            },
            'S13': {
                'title': 'Protocols / Methods',
                'examples': [
                    'Doe JQ, "Spatial RNA-seq sample preparation", STAR Protocols 2023 doi:10.1016/j.xpro.2023.102345.',
                    'Nguyen T, "Imaging flow cytometry for T-cell subsets", JOVE 2022 doi:10.3791/60012.',
                    'Patel A, "Generating organoid cultures from patient biopsies", Nature Protocols 2024.'
                ]
            },
            'S14': {
                'title': 'Guidelines / Consensus Statements',
                'examples': [
                    'ACC/AHA Task Force; "Guidelines for Heart Failure Management." Circulation. 2023.',
                    'WHO Expert Panel; "Ethical AI in Healthcare Framework." Geneva 2024; ISBN 978-92-4-007555-3.',
                    'CDC Advisory Committee; "COVID-19 Vaccination Recommendations." MMWR 2021; 70(4):1-6.'
                ]
            },
            'S15': {
                'title': 'Registered Reports / Preregistrations',
                'examples': [
                    'Doe JQ; "Plan for spatial metabolomics in RCC." OSF Registered Report osf.io/x4y2b (2024) Stage 1.',
                    'Nguyen T; "Neural correlates of empathy." Psych Science RR (2023) Stage 2.',
                    'Patel A; "Clinical trial preregistration: sleep-apnea wearable." ClinicalTrials.gov NCT05012345 (2022).'
                ]
            }
        }
    },

    # =========================================================================
    # EDUCATION AND TRAINING (B) - MEDIUM CONFUSION RISK
    # =========================================================================
    'education_and_training': {
        'primary_parent': 'B',
        'canonical_name': 'Education and Training',
        'confusion_risk': 'medium',
        'description': 'Formal education and training received by the CV owner (degrees, coursework, certifications). NOT teaching or education provided to others.',

        'core_principles': [
            'B = Education YOU received (student role)',
            'K = Education YOU provided (teacher role)',
            'B1 = Academic Degrees (all degrees: BA/BS, MA/MS, MBA, MPH, MD, PhD, JD, etc.)',
            'B2 = Other Educational Experiences (certificates, short courses)',
            'B2 includes Professional Development & Continuing Education (CME/CEU workshops as learner)',
            'C = Postdoctoral Training (postdoc fellowships, residencies - separate parent section)'
        ],

        'decision_order': [
            '1) Is this education RECEIVED (B) or PROVIDED (K)?',
            '2) If RECEIVED: Is it a degree? → B1 (all degrees: BA, MA, MBA, MPH, MD, PhD, JD, etc.)',
            '3) If non-degree education (certificate, short course) → B2',
            '4) If CME/CEU, workshops, professional development (as learner) → B2',
            '5) If postdoctoral training (postdoc, residency, fellowship) → C (separate parent section)',
            '6) If section says "MENTEES" or "ADVISEES" → likely N (their degrees), not B (your degrees)'
        ],

        'routing_rules': {
            'B_vs_K': 'B if person is RECEIVING education (student, trainee). K if person is PROVIDING education (teacher, instructor, course director).',
            'all_degrees_to_B1': 'B1 for ALL academic degrees: Bachelor\'s (BA, BS, AB), Master\'s (MA, MS, MBA, MPH, MPA), Doctoral (PhD, MD, DrPH, DVM, JD, EdD, PharmD, PsyD).',
            'non_degree_education': 'B2 for non-degree educational experiences: certificates, diplomas, short courses (not CME).',
            'professional_development': 'B2 for continuing education where person is learner: CME credits, workshops, professional development courses.',
            'postdoctoral_training': 'C (separate parent section) for postdoctoral fellowships, residencies, clinical fellowships - structured training AFTER terminal degree.',
            'mentee_degrees': 'CRITICAL: If section header says "MENTEES", "ADVISEES", "STUDENTS SUPERVISED" → their degrees are N3/N4 (mentoring), NOT your degrees (B).'
        },

        'trigger_keywords': {
            'teaching_activity': ['taught', 'instructor', 'course director', 'lecturer', 'teaching assistant'],
            'student_role': ['student', 'graduated', 'degree', 'coursework', 'thesis research', 'dissertation'],
            'postdoc': ['postdoctoral', 'postdoc', 'research fellow', 'clinical fellow'],
            'mentee_section': ['mentees', 'advisees', 'students supervised', 'trainees', 'dissertation committee']
        },

        'alternative_parents': [
            {
                'parent_id': 'educational_contributions',
                'parent_name': 'K. Educational Contributions',
                'reason': 'Teaching vs. learning - inverse relationship',
                'show_all_children': True,
                'trigger_keywords': ['taught', 'instructor', 'course director', 'teaching'],
                'disambiguation_guidance': [
                    'CRITICAL: B = Education YOU RECEIVED (you are the student/participant)',
                    'CRITICAL: K = Education YOU PROVIDED (you are the teacher/instructor)',
                    '',
                    'IF person is STUDENT/TRAINEE receiving education → B',
                    'IF person is TEACHER/INSTRUCTOR providing education → K',
                    '',
                    'B2 examples (continuing education YOU attended):',
                    '  "Prolonged Exposure Therapy Course, Washington University, 2024" → B2',
                    '  "Bioethics Intensive, Johns Hopkins University, 2016" → B2',
                    '  "Advanced Statistics Workshop, Cold Spring Harbor, 2019" → B2',
                    '  "Leadership Development Program, AAMC, 2021" → B2',
                    '  "Clinical Research Certificate Program, Harvard CME, 2020" → B2 or B2',
                    '',
                    'K1 examples (teaching YOU provided):',
                    '  "Taught: Introduction to Biostatistics, Fall 2021" → K1',
                    '  "Instructor, Advanced Nursing Practice, 2020-2022" → K1',
                    '  "Course Director, Medical Ethics Seminar" → K1',
                    '  "TA for Biochemistry 101" → K1',
                    '',
                    'Header context clues:',
                    '  "ADDITIONAL TRAINING" → Usually B2 (training you received)',
                    '  "CONTINUING EDUCATION" → Usually B2 (CME/CEU courses you took)',
                    '  "CERTIFICATES" → Usually B2 (non-degree credentials)',
                    '  "TEACHING" or "COURSES TAUGHT" → K1 (instruction you provided)',
                    '  "EDUCATION" (no qualifier) → Check content: degree earned = B1, course taught = K'
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment',
                'reason': 'Degree earned vs. current job title',
                'show_all_children': False,
                'trigger_keywords': ['assistant professor', 'associate professor', 'professor', 'instructor', 'fellow'],
                'disambiguation_guidance': [
                    'IF terminal degree earned in past (PhD 2015, MD 2013, MBA 2018) → B1 (all degrees)',
                    'IF current academic appointment (Assistant Professor 2020-present) → D1',
                    'IF postdoctoral fellowship as TRAINING → C (Postdoctoral Training - separate parent section)',
                    'IF postdoctoral position listed as EMPLOYMENT appointment → also C'
                ]
            },
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'Your degrees vs. mentee degrees',
                'show_all_children': True,
                'trigger_keywords': ['mentees', 'advisees', 'supervised', 'thesis committee'],
                'disambiguation_guidance': [
                    'CHECK SECTION HEADER FIRST',
                    'IF section labeled "MENTEES", "ADVISEES" → degrees listed are for STUDENTS (N), not you (B)',
                    'IF your own educational history → B',
                    'Example under "MENTEES": "Sarah Kim, PhD 2019" → N4 (past mentee), not B2'
                ]
            },
            {
                'parent_id': 'postdoctoral_training',
                'parent_name': 'C. Postdoctoral Training',
                'reason': 'Earlier education vs. postdoctoral training',
                'show_all_children': True,
                'trigger_keywords': ['postdoctoral', 'postdoc', 'fellow', 'residency', 'resident', 'fellowship'],
                'disambiguation_guidance': [
                    'CRITICAL: Is this earlier education or postdoctoral training?',
                    'IF degree program or pre-terminal-degree training → B Education',
                    'IF postdoctoral/residency/fellowship (after PhD/MD) → C Postdoctoral Training',
                    'B indicators: degree programs (BA, MS, PhD, MD), coursework, thesis',
                    'C indicators: "postdoctoral fellow", "resident", "clinical fellow", dates after PhD/MD'
                ]
            },
            {
                'parent_id': 'unknown',
                'parent_name': 'T. Appendix / Other',
                'reason': 'Formal education vs. other professional information',
                'show_all_children': True,
                'trigger_keywords': ['languages', 'skills', 'certifications', 'licenses', 'other'],
                'disambiguation_guidance': [
                    'IF formal degree or educational program → B Education and Training',
                    'IF language skills, licenses, other professional info → T Appendix/Other',
                    'B examples: "PhD 2015, Harvard"',
                    'T examples: "Languages: Spanish (fluent)"'
                ]
            },
            {
                'parent_id': 'licensure_and_certification',
                'parent_name': 'F. Licensure and Certification',
                'reason': 'CONFUSION AREA #6: Professional certificate programs vs board certifications (BIDIRECTIONAL)',
                'show_all_children': False,
                'trigger_keywords': ['certificate', 'certification', 'certified', 'diplomate', 'board'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "certificate", "certification", "certificate program", "diploma"',
                    '',
                    'KEY QUESTION: Is this an educational program/course (B2) or a professional credential/license (F)?',
                    '',
                    'Indicators favoring B2 (Professional Development):',
                    '  • Educational institution: "Certificate in Clinical Research Methods, Duke University"',
                    '  • Coursework/curriculum: program duration, credits, courses listed',
                    '  • Learning focus: acquiring new skills/methods, not credential to practice',
                    '  • Program completion from university/educational institution',
                    '  • Certificate programs: "Leadership Certificate", "Data Science Certificate", "Informatics Certificate"',
                    '  • Training received by YOU as learner',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • National certifying board: ABMS boards (ABPN, ABIM, ABNM, etc.)',
                    '  • Specialty credential: "Board Certified in [Specialty]"',
                    '  • Diplomate status from professional board',
                    '  • State/professional license: "Licensed Psychologist, State of NY"',
                    '  • No coursework described, just credential awarded',
                    '  • Authorization to practice in specialty',
                    '',
                    'EXAMPLES:',
                    '  B2: "Certificate in Clinical Research Methods, Duke University, 2015" (educational program)',
                    '  F2: "Board Certified, American Board of Neuromuscular Medicine, 2016" (specialty credential)',
                    '  B2: "Professional Certificate in Medical Education, Harvard Macy Institute, 2020" (educational training)',
                    '  F1: "Licensed Psychologist, State of New York, #12345" (practice license)',
                    '  B2: "Certificate in Healthcare Quality and Safety, IHI, 2019" (professional development)',
                    '  F2: "Diplomate, American Board of Medical Genetics, 2018" (board certification)',
                    '',
                    'CRITICAL TEST: Is this from a certifying board/state (F) or an educational institution providing training (B2)?',
                    '',
                    'BIDIRECTIONAL: B2 → F when professional certificates are misread as credentials. F → B2 when board certifications are mistaken for certificate programs.'
                ]
            }
        ],

        'subsection_examples': {
            'B1': {
                'title': 'Academic Degrees (all degree-granting education)',
                'examples': [
                    'Bachelor of Science, Biology, University of California Berkeley, 2008',
                    'BA, Chemistry (summa cum laude), Harvard College, 2010',
                    'MPH, Global Health, Columbia University Mailman School, 2014',
                    'MS, Biostatistics, University of Washington, 2016',
                    'MBA, Healthcare Management, Wharton School, 2019',
                    'PhD, Immunology, Stanford University, 2015. Dissertation: "T-cell receptor signaling in autoimmunity"',
                    'MD, Yale School of Medicine, 2018',
                    'DrPH, Epidemiology, Harvard T.H. Chan School of Public Health, 2020',
                    'JD, Columbia Law School, 2017'
                ]
            },
            'B2': {
                'title': 'Other Educational Experiences (non-degree education)',
                'examples': [
                    'Certificate in Clinical Research, Harvard Extension School, 2018',
                    'Data Science Bootcamp, General Assembly, 2019',
                    'Postgraduate Diploma in Medical Education, University of Dundee, 2020'
                ]
            },
            'B2': {
                'title': 'Professional Development & Continuing Education (CME/CEU)',
                'examples': [
                    'Prolonged Exposure Therapy Course, Washington University, 2024',
                    'Leadership Development Program, AAMC, 2021',
                    'Advanced Statistics Workshop, Cold Spring Harbor Laboratory, 2019',
                    'Bioethics Intensive, Johns Hopkins University, 2016'
                ]
            }
        }
    },

    # =========================================================================
    # LICENSURE AND CERTIFICATION (F) - MEDIUM CONFUSION RISK
    # =========================================================================
    'licensure_and_certification': {
        'primary_parent': 'F',
        'canonical_name': 'Licensure and Certification',
        'confusion_risk': 'medium',
        'description': 'Professional licenses, board certifications, and state/specialty credentials required to practice.',

        'core_principles': [
            'F = License/certification credentials (state medical license, board certification)',
            'K4 = Continuing education courses/credits (CME, preparation courses)',
            'B2 = Professional development programs (certificates in new skills/methods)',
            'H = Honors within certification process (top 10%, passed with distinction)',
            'F1 = Medical licenses',
            'F2 = Board certifications and specialty credentials'
        ],

        'decision_order': [
            '1) Is this recording official licensure/credential status (F)?',
            '2) Is this celebrating exam performance/achievement (H)?',
            '3) Is this documenting the education/course that led to credential (K4/B2)?',
            '4) Medical license (F1) vs Board certification (F2)?'
        ],

        'precedence_rules': [
            'Bare credential status → F (e.g., "Board Certified, ABPN, 2018")',
            'Credential + performance metric → H (e.g., "Board Certified, top 10%")',
            'Preparation course/CME → K4 (e.g., "CME: Preparing for ABPN Exam")',
            'Certificate program → B2 (e.g., "Certificate in Clinical Research Methods")'
        ],

        'trigger_keywords': {
            'licensure': ['medical license', 'state license', 'licensed to practice', 'license number'],
            'board_cert': ['board certified', 'board certification', 'diplomate', 'abms', 'abpn', 'abim', 'abnm'],
            'certification': ['certified', 'certification', 'credential', 'specialist certification'],
            'honors': ['top 10%', 'top 5%', 'highest score', 'passed with distinction', 'honor roll', 'with honors'],
            'cme': ['cme course', 'continuing education', 'preparation course', 'review course', 'board prep'],
            'certificate': ['certificate in', 'certificate of completion', 'certification program', 'professional certificate']
        },

        'routing_rules': {
            'license_vs_honor': 'F if recording credential status. H if highlighting achievement/performance.',
            'certification_vs_cme': 'F if final credential awarded. K4 if course/education leading to it.',
            'board_cert_vs_certificate_program': 'F2 if ABMS/specialty board. B2 if university certificate program.',
            'distinction_within_cert': 'F if just credential. H if performance noted (top X%, with distinction).'
        },

        'alternative_parents': [
            {
                'parent_id': 'educational_contributions',
                'parent_name': 'K. Educational Contributions (specifically K4 - CME/Continuing Education)',
                'reason': 'CONFUSION AREA #5: Certification preparation courses vs credential status',
                'show_all_children': False,
                'trigger_keywords': ['cme course', 'preparation course', 'board prep', 'review course', 'continuing education credits', 'ceu', 'certificate of completion'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "certificate", "board certified", "certification exam", "CME course", "certificate of completion"',
                    '',
                    'KEY QUESTION: Is this recording the official credential/license (F) or the educational preparation for it (K4)?',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • Official credential awarded: "Board Certified", "Diplomate", "Licensed to Practice"',
                    '  • Certification body: ABMS boards (ABPN, ABIM, ABNM, etc.)',
                    '  • State medical licenses: "Medical License #12345, New York State"',
                    '  • Bare credential statement without course details',
                    '  • Format: "Board Certified, [Board Name], [Year]"',
                    '',
                    'Indicators favoring K4 (Continuing Education):',
                    '  • Course/program leading to credential: "CME Course: Preparing for ABPN Exam"',
                    '  • Education hours/credits: "40 CME credits", "120 contact hours"',
                    '  • Preparation/review focus: "Board Review Course", "Exam Preparation Workshop"',
                    '  • Certificate of completion (not credential itself)',
                    '  • Sponsor is educational institution, not certifying board',
                    '',
                    'EXAMPLES:',
                    '  F2: "Board Certified, American Board of Psychiatry and Neurology, 2018" (credential status)',
                    '  K4: "CME Course: Preparing for the ABPN Certification Exam, 40 hrs, 2017" (preparation education)',
                    '  F1: "Medical License #87654, State of Massachusetts, 2016–present" (licensure)',
                    '  K4: "Certificate of Completion: Advanced Cardiac Life Support (ACLS), AHA, 2023" (educational completion)',
                    '  F2: "Diplomate, American Board of Internal Medicine, 2015" (credential)',
                    '',
                    'CRITICAL TEST: Ask "Is this an official credential from a certifying body (F) or education/training that prepared me for it (K4)?"',
                    '',
                    'BIDIRECTIONAL: F → K4 when certification entry includes prep course details. K4 → F when course completion is misread as credential.'
                ]
            },
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education Received (specifically B2 - Professional Development)',
                'reason': 'CONFUSION AREA #6: Board certification vs professional certificate programs',
                'show_all_children': False,
                'trigger_keywords': ['certificate in', 'certification program', 'professional certificate', 'certificate program', 'diploma in', 'credential program'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "certificate", "certification", "certificate program", "diploma"',
                    '',
                    'KEY QUESTION: Is this a specialty board certification (F) or a professional development certificate program (B2)?',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • National board: ABMS member boards (ABPN, ABIM, ABNM, etc.)',
                    '  • Specialty credential: "Board Certified in [Specialty]"',
                    '  • Diplomate status from certifying board',
                    '  • State/professional license required for practice',
                    '  • No coursework/curriculum described',
                    '',
                    'Indicators favoring B2 (Professional Development):',
                    '  • University/institution certificate program: "Certificate in Clinical Research Methods, Duke"',
                    '  • Coursework/curriculum component: credits, courses, program duration',
                    '  • Educational focus: learning new skills/methods, not credential to practice',
                    '  • Certificate of completion from educational institution',
                    '  • Program names: "Leadership Certificate", "Data Science Certificate", "Clinical Informatics Certificate"',
                    '',
                    'EXAMPLES:',
                    '  F2: "Board Certified, American Board of Neuromuscular Medicine, 2016" (specialty credential)',
                    '  B2: "Certificate in Clinical Research Methods, Duke University, 2015" (educational program)',
                    '  F1: "Licensed Psychologist, State of New York, #12345" (practice license)',
                    '  B2: "Professional Certificate in Medical Education, Harvard Macy Institute, 2020" (professional development)',
                    '  F2: "Diplomate, American Board of Medical Genetics, 2018" (board certification)',
                    '  B2: "Certificate in Healthcare Quality and Safety, IHI, 2019" (professional training)',
                    '',
                    'CRITICAL TEST: Is this from a certifying board/state (F) or an educational institution (B2)?',
                    '',
                    'BIDIRECTIONAL: F → B2 when board certifications are mistaken for certificate programs. B2 → F when professional certificates are misread as credentials.'
                ]
            },
            {
                'parent_id': 'honors_and_awards',
                'parent_name': 'H. Honors & Awards',
                'reason': 'CONFUSION AREA #7: Certification honors vs credential status',
                'show_all_children': False,
                'trigger_keywords': ['top 10%', 'top 5%', 'highest score', 'passed with distinction', 'honor roll', 'with honors', 'outstanding performance'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "board certified", "passed with distinction", "top 10%", "honor roll", "with honors"',
                    '',
                    'KEY QUESTION: Is this documenting credential status (F) or celebrating exam performance/achievement (H)?',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • Bare credential statement: "Board Certified, [Board], [Year]"',
                    '  • No performance metrics or achievement language',
                    '  • Focus on status/authorization to practice',
                    '  • Listed in licensure section without competitive language',
                    '  • Simple "Diplomate of [Board]" or "Licensed in [State]"',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • Performance metrics: "top 10%", "top 5%", "highest score"',
                    '  • Achievement language: "passed with distinction", "with honors", "outstanding performance"',
                    '  • Competitive ranking: "scored in 95th percentile"',
                    '  • Recognition for exam excellence',
                    '  • Listed in awards section or with other honors',
                    '',
                    'EXAMPLES:',
                    '  F2: "Board Certified, American Board of Psychiatry and Neurology, 2018" (credential status)',
                    '  H: "Board Certified, American Board of Psychiatry and Neurology, 2018, Top 10%" (exam achievement)',
                    '  F2: "Diplomate, American Board of Internal Medicine, 2015" (certification status)',
                    '  H: "ABIM Certification Exam, Passed with Distinction, 2015" (exam honor)',
                    '  F1: "Medical License, State of California, 2017" (license)',
                    '  H: "Medical License Exam (USMLE Step 3), Score 265, 95th percentile, 2017" (exam performance)',
                    '',
                    'CRITICAL TEST: Check for achievement/performance language. If present → H. If absent → F.',
                    '  • "Board Certified, ABPN, 2018" = F2 (status only)',
                    '  • "Board Certified, ABPN, 2018, Top 5%" = H (performance honor)',
                    '',
                    'BIDIRECTIONAL: F → H when credential includes performance metric. H → F when exam achievement is listed without distinction language.'
                ]
            }
        ],

        'subsections': {
            'F1': {
                'title': 'Medical Licenses',
                'examples': [
                    'Medical License #12345, State of New York, 2015–present',
                    'Licensed Psychologist, California Board of Psychology, #PSY67890, 2018',
                    'DEA Registration #AB1234567, 2016–2024'
                ]
            },
            'F2': {
                'title': 'Board Certifications and Specialty Credentials',
                'examples': [
                    'Board Certified, American Board of Psychiatry and Neurology, 2018',
                    'Diplomate, American Board of Internal Medicine, 2015',
                    'Board Certified, American Board of Neuromuscular Medicine, 2016',
                    'Certified Clinical Research Professional (CCRP), SoCRA, 2020'
                ]
            }
        }
    },

    # =========================================================================
    # POSTDOCTORAL TRAINING (C) - MEDIUM CONFUSION RISK
    # =========================================================================
    'postdoctoral_training': {
        'primary_parent': 'C',
        'canonical_name': 'Postdoctoral Training',
        'confusion_risk': 'medium',
        'description': 'Postdoctoral research fellowships, clinical residencies, and fellowship training positions. Post-terminal-degree structured training period.',

        'core_principles': [
            'C = Training YOU received after terminal degree (postdoc/residency/fellowship)',
            'B = Education/degrees YOU earned (pre-terminal degree)',
            'D = Employment positions/appointments (after training)',
            'N = Trainees/mentees YOU supervised (not your own training)',
            'C1 = Postdoctoral research positions',
            'C2 = Clinical residency training',
            'C3 = Clinical fellowship training'
        ],

        'decision_order': [
            '1) Is this YOUR training (C) or training YOU supervised (N)?',
            '2) Is this postdoc research (C1), residency (C2), or fellowship (C3)?',
            '3) Is this training (C) or employment appointment (D)?',
            '4) Is this postdoctoral (C) or earlier degree program (B)?'
        ],

        'routing_rules': {
            'C_vs_B': 'C for postdoctoral/residency/fellowship training AFTER terminal degree. B for degree programs and pre-terminal degree training.',
            'C_vs_D': 'C if structured training position with mentor/supervisor. D if faculty appointment or staff employment position.',
            'C_vs_N': 'C if YOU received the training. N if YOU supervised the trainees.',
            'C1_research_postdoc': 'C1 for research postdoctoral fellowships (NIH T32, K awards during postdoc, university postdoc programs).',
            'C2_residency': 'C2 for clinical residency training (internal medicine, surgery, psychiatry, etc.).',
            'C3_fellowship': 'C3 for clinical fellowship training (cardiology, oncology, subspecialty training after residency).'
        },

        'trigger_keywords': {
            'postdoc': ['postdoctoral', 'postdoc', 'post-doctoral', 'research fellow', 'NIH T32', 'postdoctoral research'],
            'residency': ['residency', 'resident', 'intern', 'internship', 'pgy'],
            'fellowship': ['fellowship', 'fellow', 'clinical fellow'],
            'training': ['training', 'trainee', 'advisor', 'mentor', 'supervised by']
        },

        'alternative_parents': [
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education and Training',
                'reason': 'Postdoctoral training vs. earlier education',
                'show_all_children': True,
                'trigger_keywords': ['degree', 'phd', 'md', 'education', 'training', 'coursework'],
                'disambiguation_guidance': [
                    'CRITICAL: Is this post-terminal-degree training or earlier education?',
                    'IF postdoctoral/residency/fellowship (after PhD/MD) → C Postdoctoral Training',
                    'IF degree program or pre-terminal-degree training → B Education',
                    'C indicators: "postdoctoral fellow", "resident", "clinical fellow", dates after PhD/MD',
                    'B indicators: degree programs (BA, MS, PhD, MD), coursework, thesis'
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment / O. Leadership',
                'reason': 'CONFUSION AREA #10: Fellow/Resident ambiguity - Trainee vs Faculty directing training',
                'show_all_children': True,
                'trigger_keywords': ['assistant professor', 'associate professor', 'instructor', 'position', 'appointment', 'faculty', 'fellow', 'resident', 'fellowship director', 'program director', 'residency director'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "Fellow", "Resident", "Fellowship", "Residency", "Program Director", "Fellowship Director"',
                    '',
                    'KEY QUESTION: Is the CV owner receiving training or running/teaching the program?',
                    '',
                    'Indicators favoring C (Postdoctoral Training - CV owner as TRAINEE):',
                    '  • Appears chronologically immediately after degrees',
                    '  • Roles: "Resident", "Postdoctoral Fellow", "Clinical Fellow", "Research Fellow"',
                    '  • Explicit training context: "Residency Training", "Fellowship Training"',
                    '  • Mentions mentor/advisor/supervisor (e.g., "Advisor: Dr. Smith")',
                    '  • Section headers: "POSTDOCTORAL TRAINING", "RESIDENCY", "FELLOWSHIP"',
                    '  • Date range consistent with training period (2-5 years post-degree)',
                    '',
                    'Indicators favoring D (Professional Positions - CV owner as FACULTY):',
                    '  • Roles: "Assistant Professor", "Associate Professor", "Instructor", "Attending Physician"',
                    '  • Job title with salary/employment context',
                    '  • Teaching or clinical service duties described',
                    '  • Section headers: "PROFESSIONAL POSITIONS", "ACADEMIC APPOINTMENTS", "EMPLOYMENT"',
                    '  • Appears after training period in chronological CV',
                    '',
                    'Indicators favoring O/N (Leadership/Mentoring - CV owner as PROGRAM LEADER):',
                    '  • Roles: "Fellowship Director", "Residency Program Director", "Training Program Director"',
                    '  • Administrative oversight of training programs',
                    '  • Section headers: "LEADERSHIP", "ADMINISTRATIVE ROLES", "PROGRAM DIRECTION"',
                    '  • Responsibilities include trainee supervision, curriculum development',
                    '',
                    'EXAMPLES:',
                    '  C3: "Fellow, Neuromuscular Medicine Fellowship, 07/2015–06/2016, Wake Forest" (trainee)',
                    '  D1: "Assistant Professor, Department of Neurology, 2017–present" (faculty position)',
                    '  O/N1: "Fellowship Director, Neuromuscular Medicine Fellowship, 2019–present" (program leadership)',
                    '  C2: "Resident, Physical Medicine and Rehabilitation Residency, 2012–2015" (trainee)',
                    '  O/K: "Director, PM&R Resident Didactic Course, 2018–present" (teaching leadership)',
                    '',
                    'CRITICAL TEST: Look at chronological position and section context',
                    '  • If immediately post-MD/PhD with mentor → C (training)',
                    '  • If after training period with job duties → D (employment)',
                    '  • If directing/overseeing trainees → O/N (leadership)',
                    '',
                    'BIDIRECTIONAL: C → D when training roles lack mentor context. D → C when faculty positions are misread as fellowships. D/O → N when program direction focuses on mentoring rather than administration.'
                ]
            },
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'Receiving training vs. supervising trainees',
                'show_all_children': False,
                'trigger_keywords': ['postdoc', 'fellow', 'resident', 'trainee', 'mentee', 't32'],
                'disambiguation_guidance': [
                    'CRITICAL: Whose training is being described?',
                    'IF YOU received postdoctoral/residency/fellowship training → C Postdoctoral Training',
                    'IF YOU supervised/mentored postdocs/residents/fellows → N Mentoring',
                    'Section header: "POSTDOCTORAL TRAINING", "RESIDENCY" → C (your training)',
                    'Section header: "MENTEES", "TRAINEES SUPERVISED" → N (their training)'
                ]
            }
        ],

        'subsection_examples': {
            'C1': {
                'title': 'Postdoctoral Research Positions',
                'examples': [
                    'Postdoctoral Fellow, Cancer Biology, Stanford University, 2015-2018',
                    'Research Fellow, Immunology, Harvard Medical School, Advisor: Dr. Smith, 2016-2019',
                    'NIH T32 Postdoctoral Fellow, Cardiovascular Research, Mayo Clinic, 2017-2020'
                ]
            },
            'C2': {
                'title': 'Residency Training',
                'examples': [
                    'Internal Medicine Residency, Massachusetts General Hospital, 2013-2016',
                    'Surgery Residency, Johns Hopkins Hospital, 2014-2019',
                    'Psychiatry Residency, UCSF Medical Center, 2015-2018'
                ]
            },
            'C3': {
                'title': 'Fellowship Training',
                'examples': [
                    'Cardiology Fellowship, Cleveland Clinic, 2016-2019',
                    'Oncology Fellowship, MD Anderson Cancer Center, 2015-2018',
                    'Endocrinology Fellowship, Columbia University Medical Center, 2017-2020'
                ]
            }
        }
    },

    # =========================================================================
    # EDUCATIONAL CONTRIBUTIONS (K) - HIGH CONFUSION RISK
    # =========================================================================
    'educational_contributions': {
        'primary_parent': 'K',
        'canonical_name': 'Educational Contributions',
        'confusion_risk': 'high',
        'description': 'Teaching activities, course instruction, curriculum development, educational materials. IMPORTANT: This section requires subsection routing (K1, K2, K4, etc.) - do NOT use generic classification unless content truly cannot be structured.',

        'routing_rules': {
            'K_vs_B3': 'K if person TEACHES. B2 if person LEARNS/ATTENDS training.',
            'K1_vs_K4': 'K1 for formal courses (undergrad/grad). K4 for CME/professional education.',
            'structured_vs_narrative': 'If content lists courses taught, use K1 even if narrative format. Use generic ONLY for truly unstructured teaching descriptions.',
            'teaching_role': 'Instructor, Professor, Teacher, Preceptor → K',
            'learner_role': 'Student, Attendee, Participant, Trainee → B',
            'course_vs_mentoring': 'Formal course with syllabus → K1, Individual supervision → N'
        },

        'trigger_keywords': {
            'teaching': ['instructor', 'taught', 'teaching', 'course', 'lecture', 'preceptor'],
            'learning': ['attended', 'participant', 'completed', 'training', 'workshop attended']
        },

        'alternative_parents': [
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education',
                'reason': 'Person RECEIVING education vs. PROVIDING education',
                'show_all_children': True,
                'trigger_keywords': ['attended', 'completed', 'training', 'workshop', 'certificate', 'course completed'],
                'disambiguation_guidance': [
                    'IF person TAUGHT/INSTRUCTED course → K1 Didactic Teaching',
                    'IF person ATTENDED/COMPLETED training → B Education',
                    'IF person DEVELOPED curriculum → K3 Administrative Teaching (curriculum work)'
                ]
            },
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'Teaching vs. individual mentoring distinction',
                'show_all_children': True,
                'trigger_keywords': ['thesis', 'dissertation', 'independent study', 'directed study', 'one-on-one'],
                'disambiguation_guidance': [
                    'IF formal course teaching with multiple students → K1 Didactic Teaching',
                    'IF one-on-one student supervision → N Mentoring',
                    'IF directed studies/independent study supervision → N Mentoring'
                ]
            },
            {
                'parent_id': 'invitations_to_speak',
                'parent_name': 'R. Invitations to Speak/Present',
                'reason': 'Regular teaching vs. one-time invited lecture',
                'show_all_children': True,
                'trigger_keywords': ['invited', 'guest lecture', 'visiting', 'grand rounds', 'keynote'],
                'disambiguation_guidance': [
                    'IF regular course teaching (repeated) → K Educational Contributions',
                    'IF one-time invited lecture/visit → R Invitations to Speak',
                    'Test: Is this a regular course you teach (K) or a one-time invitation (R)?'
                ]
            },
            {
                'parent_id': 'licensure_and_certification',
                'parent_name': 'F. Licensure and Certification (specifically F2 - Board Certifications)',
                'reason': 'CONFUSION AREA #5: CME courses vs certification credentials (BIDIRECTIONAL)',
                'show_all_children': False,
                'trigger_keywords': ['board certified', 'certification', 'diplomate', 'licensed', 'credential'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "certificate", "certification", "CME course", "board prep"',
                    '',
                    'KEY QUESTION: Is this teaching/providing education (K4) or documenting credential status (F)?',
                    '',
                    'Indicators favoring K4 (Continuing Education):',
                    '  • Teaching CME course/workshop: "Faculty, CME Workshop on [Topic]"',
                    '  • Providing professional education: "Instructor, Board Review Course"',
                    '  • Course development: "Developed CME module for..."',
                    '  • Educational delivery with attendees/participants',
                    '  • YOU are the teacher/instructor',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • Official credential awarded TO YOU: "Board Certified, ABPN, 2018"',
                    '  • License/certification status: "Medical License #12345"',
                    '  • Diplomate status: "Diplomate, ABIM"',
                    '  • YOU are receiving the credential',
                    '  • No teaching/instruction role described',
                    '',
                    'EXAMPLES:',
                    '  K4: "Faculty, Annual CME Workshop: Updates in Neurology, 2023" (teaching CME)',
                    '  F2: "Board Certified, American Board of Neurology, 2018" (credential)',
                    '  K4: "Instructor, ABPN Board Review Course, 2022" (teaching review course)',
                    '  F2: "Diplomate, American Board of Internal Medicine, 2015" (credential status)',
                    '',
                    'CRITICAL TEST: Ask "Am I TEACHING this (K4) or did I RECEIVE this credential (F)?"',
                    '',
                    'BIDIRECTIONAL: K4 → F when teaching role is misread as credential. F → K4 when credential includes prep course taught.'
                ]
            }
        ],

        'subsection_examples': {
            'K1': {
                'title': 'Didactic Teaching (courses taught)',
                'examples': [
                    'Instructor, SOC213 Sociology of the Family 2011-present',
                    'Guest lecture "Ethics in Medicine" 2022',
                    'Course Director, Evidence-Based Medicine (Year 2) 2017–2022',
                    'CBH:6205 Designing and Implementing Interventions (3ch) Fall 2021'
                ]
            },
            'K2': {
                'title': 'Clinical Teaching',
                'examples': [
                    'Attending physician, Internal Medicine inpatient service 2020–present',
                    'Clinical preceptor, Family Medicine clerkship 2018–2023',
                    'OR teaching rounds, General Surgery residents weekly',
                    'Ambulatory precepting, Cardiology fellows clinic 2019–present'
                ]
            },
            'K3': {
                'title': 'Administrative Teaching Roles',
                'examples': [
                    'Chair, Curriculum Committee overseeing MD program revision 2022–present',
                    'Course Director, Evidence-Based Medicine (Year 2) 2017–2022',
                    'Lead Organizer, Faculty Teaching Certificate Program 2019–2021',
                    'Director, Medical Education Scholars Program 2020–present'
                ]
            },
            'K4': {
                'title': 'Continuing/Professional Education (CME)',
                'examples': [
                    'CME workshop "Gender-Based Violence Training" 2023',
                    'Online webinar for practicing physicians 2021',
                    'Faculty development workshop on active learning 2022'
                ]
            },
                                }
    },

    # =========================================================================
    # PROFESSIONAL POSITIONS & EMPLOYMENT (D) - MEDIUM CONFUSION RISK
    # =========================================================================
    'professional_positions_employment': {
        'primary_parent': 'D',
        'canonical_name': 'Professional Positions & Employment',
        'confusion_risk': 'medium',
        'description': 'Faculty appointments, employment history, academic/clinical positions held. Focus on JOB TITLES and appointments, not leadership roles within those positions.',

        'core_principles': [
            'D = Job titles and employment positions (what you ARE)',
            'O/P = Leadership roles within organizations (what you DO)',
            'D1 = Current positions (present, ongoing)',
            'D2 = Past positions (completed appointments)',
            'Include faculty rank, clinical appointments, administrative titles',
            'Focus on the APPOINTMENT itself, not activities within it'
        ],

        'decision_order': [
            '1) Is this a JOB TITLE/APPOINTMENT (D) or a LEADERSHIP ROLE (O/P)?',
            '2) If JOB TITLE: Is it current (D1) or past (D2)?',
            '3) If unclear: Does it show employment relationship with institution? → D',
            '4) If shows leadership/service activity within that job → O or P',
            '5) Check section header: "APPOINTMENTS", "EMPLOYMENT", "POSITIONS" → likely D'
        ],

        'routing_rules': {
            'D_vs_O_P': 'D for JOB TITLES (Assistant Professor, Attending Physician). O/P for LEADERSHIP ROLES (Department Chair, Committee Member).',
            'current_vs_past': 'D1 if position is ongoing/current (2020-present). D2 if position ended (2015-2018).',
            'appointment_vs_degree': 'D for POSITIONS held (Assistant Professor 2020-present). B for DEGREES earned (PhD 2015).',
            'faculty_ranks': 'D1/D2 for: Professor, Associate Professor, Assistant Professor, Instructor, Lecturer, Adjunct Faculty.',
            'clinical_titles': 'D1/D2 for: Attending Physician, Staff Surgeon, Clinical Fellow (if paid position), Hospitalist.',
            'administrative_titles': 'D1/D2 for official job titles (Director of X Program, Vice Chair for Y) that represent PRIMARY appointment. O if it\'s an ADDITIONAL leadership role.',
            'research_positions': 'D1/D2 for: Research Scientist, Research Associate, Lab Manager, Principal Investigator (if it\'s your job title).',
            'mentee_positions': 'CRITICAL: If section says "MENTEES" or shows supervised trainees → their positions are N (Mentoring), NOT your positions (D).'
        },

        'trigger_keywords': {
            'leadership_role': ['chair', 'director', 'chief', 'head', 'dean', 'vice', 'associate dean', 'president'],
            'committee': ['committee member', 'task force', 'working group', 'board member'],
            'faculty_rank': ['professor', 'assistant professor', 'associate professor', 'instructor', 'lecturer'],
            'clinical': ['attending', 'physician', 'surgeon', 'hospitalist', 'clinician'],
            'mentee_section': ['mentees', 'trainees', 'supervised students', 'advisees']
        },

        'alternative_parents': [
            {
                'parent_id': 'institutional_leadership',
                'parent_name': 'O. Institutional Leadership Activities',
                'reason': 'Job title vs. leadership role distinction',
                'show_all_children': True,
                'trigger_keywords': ['chair', 'director', 'chief', 'dean', 'head', 'president'],
                'disambiguation_guidance': [
                    'IF PRIMARY appointment/job title → D (e.g., "Associate Professor 2018-present")',
                    'IF ADDITIONAL leadership role → O (e.g., "Chair, Faculty Senate 2020-2022")',
                    'Test: Could you hold this role WITHOUT the job title? If yes → O. If no → D.',
                    'Example: "Department Chair" alone is D1 if it\'s your job; O if it\'s service within your Professor role'
                ]
            },
            {
                'parent_id': 'institutional_administration',
                'parent_name': 'P. Institutional Administrative Activities',
                'reason': 'Employment vs. committee/service roles',
                'show_all_children': True,
                'trigger_keywords': ['committee', 'member', 'representative', 'task force'],
                'disambiguation_guidance': [
                    'IF you are EMPLOYED in this role (it\'s your job) → D',
                    'IF you SERVE in this role (committee, service) → P',
                    'Example: "Curriculum Committee Member" → P (service), not D (job)'
                ]
            },
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education and Training',
                'reason': 'Position held vs. degree earned',
                'show_all_children': False,
                'trigger_keywords': ['phd', 'md', 'degree', 'graduated'],
                'disambiguation_guidance': [
                    'IF degree earned (PhD 2015) → B2',
                    'IF faculty position held (Assistant Professor 2020-present) → D1'
                ]
            },
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'Your positions vs. mentee positions',
                'show_all_children': True,
                'trigger_keywords': ['mentees', 'trainees', 'advisees', 'supervised'],
                'disambiguation_guidance': [
                    'CHECK SECTION HEADER',
                    'IF section says "MENTEES" → positions listed are for TRAINEES (N), not you (D)',
                    'Example under "MENTEES": "John Doe, Research Fellow 2020-2022" → N4, not D2'
                ]
            },
            {
                'parent_id': 'postdoctoral_training',
                'parent_name': 'C. Postdoctoral Training',
                'reason': 'Employment position vs. training position',
                'show_all_children': True,
                'trigger_keywords': ['postdoctoral', 'postdoc', 'fellow', 'residency', 'resident', 'fellowship'],
                'disambiguation_guidance': [
                    'CRITICAL: Is this an employment position or a training position?',
                    'IF faculty appointment or staff position → D Professional Positions',
                    'IF postdoctoral/residency/fellowship training → C Postdoctoral Training',
                    'D indicators: "assistant professor", "instructor", "professor", salary appointment',
                    'C indicators: "postdoctoral fellow", "resident", "clinical fellow", with mentor/advisor'
                ]
            },
            # CONFUSION AREA: G ↔ D2/L (Institutional Affiliation vs Clinical Positions)
            {
                'parent_id': 'institutional_hospital_affiliation',
                'parent_name': 'G. Institutional / Hospital Affiliation',
                'reason': 'CONFUSION AREA: Clinical privileges/affiliation vs actual clinical employment positions',
                'show_all_children': False,
                'trigger_keywords': ['clinical privileges', 'hospital appointment', 'staff physician', 'medical staff', 'admitting privileges', 'credentialed'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA: Institutional Affiliation (G) vs Clinical Positions (D2) / Clinical Practice (L) ===',
                    '',
                    'KEY QUESTION: Is this authorization to practice at a site (affiliation) or an explicit clinical role with duties?',
                    '',
                    '→ G (Institutional / Hospital Affiliation) if:',
                    '  • Words: "clinical privileges", "hospital staff appointment", "credentialed at..."',
                    '  • No explicit role titles like "Attending", "Director", "Service Chief"',
                    '  • Emphasis on authorization/privilege to practice',
                    '  • Passive affiliation without defined responsibilities',
                    '  • Example: "Clinical Privileges, NIH Clinical Center, Bethesda, MD"',
                    '',
                    '→ D2 (Clinical Positions - Past/Current) if:',
                    '  • Titles: "Attending Physician", "Staff Surgeon", "Consultant Psychiatrist"',
                    '  • Defined clinical role with responsibilities',
                    '  • Employment relationship (not just privileges)',
                    '  • Example: "Attending Psychiatrist, Duke University Hospital, 2015-2018"',
                    '',
                    '→ L (Clinical Practice & Leadership) if:',
                    '  • Leadership titles: "Director, [Service]", "Service Line Director", "Medical Director"',
                    '  • Phrases like "Service Chief", "Division Director" with clinical content',
                    '  • Example: "Director, ECT Service, Columbia University Medical Center"',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • G = "Permission to practice" (privilege/credentialing)',
                    '  • D2 = "Job with responsibilities" (employment position)',
                    '  • L = "Leadership in clinical operations" (directing clinical services)',
                    '',
                    'AMBIGUOUS CASES:',
                    '  • "Clinical appointment, Hospital X" → Check for role title',
                    '    - With title (Attending, Staff) → D2',
                    '    - Without title (just "appointment") → G',
                    '  • "Hospital staff, X Medical Center" → G (affiliation)',
                    '  • "Attending Physician, Hospital staff" → D2 (has role title)',
                    '',
                    'EXAMPLES:',
                    '  G: "Clinical privileges, Duke University Health System, 2015–present"',
                    '     → Pure affiliation/authorization',
                    '  D2: "Staff Psychiatrist, NIH Clinical Center, 2010-2015"',
                    '      → Defined clinical employment position',
                    '  L: "Director, TMS Clinical Service, Columbia University, 2018–present"',
                    '     → Clinical leadership role',
                    '  G: "Hospital appointment, Massachusetts General Hospital"',
                    '     → No specific role = affiliation',
                    '  D2: "Attending Physician, Hospital Medicine Service, MGH, 2020–present"',
                    '      → Specific attending role = position'
                ]
            },
            {
                'parent_id': 'clinical_practice_innovation_leadership',
                'parent_name': 'L. Clinical Practice, Innovation, and Leadership',
                'reason': 'Job title vs. clinical practice activities',
                'show_all_children': True,
                'trigger_keywords': ['clinical service', 'patient care', 'icu', 'clinic', 'attending physician'],
                'disambiguation_guidance': [
                    'IF describing JOB TITLE/APPOINTMENT → D Professional Positions',
                    'IF describing CLINICAL PRACTICE activities → L Clinical Practice',
                    'D examples: "Assistant Professor of Medicine"',
                    'L examples: "Attending Physician in ICU", "Clinical service in cardiology clinic"'
                ]
            }
        ],

        'subsection_examples': {
            'D1': {
                'title': 'Current Academic/Clinical Positions',
                'examples': [
                    'Associate Professor of Medicine (tenure-track), Weill Cornell Medicine, 2020-present',
                    'Attending Physician, NewYork-Presbyterian Hospital, 2019-present',
                    'Assistant Professor, Department of Epidemiology, Johns Hopkins Bloomberg School, 2021-present',
                    'Adjunct Faculty, Biostatistics, Columbia University, 2022-present'
                ]
            },
            'D2': {
                'title': 'Past Academic/Clinical Positions',
                'examples': [
                    'Assistant Professor, Stanford School of Medicine, 2015-2020',
                    'Clinical Instructor, Department of Surgery, UCSF, 2013-2015',
                    'Research Scientist, Memorial Sloan Kettering Cancer Center, 2012-2016',
                    'Instructor in Medicine, Harvard Medical School, 2014-2018'
                ]
            }
        }
    },

    # =========================================================================
    # CLINICAL PRACTICE & LEADERSHIP (L) - MEDIUM CONFUSION
    # Key confusion: D (job title/appointment) vs L (clinical activities/care)
    # =========================================================================
    'clinical_practice_innovation_leadership': {
        'primary_parent': 'L',
        'canonical_name': 'Clinical Practice, Innovation, and Leadership',
        'confusion_risk': 'medium (L1↔M1; narrative vision statements with patient language)',
        'description': 'Direct patient care, clinical activities, quality improvement, and leadership roles IN CLINICAL SETTINGS. NOT job titles or academic appointments.',
        'notes': 'Common confusion: Research vision statements with clinical focus ("I aim to improve patient outcomes") should map to M1 (aspirational research goals), not L1 (factual clinical practice descriptions).',

        'core_principles': [
            'L = Clinical ACTIVITIES and patient care (what clinical work you DO)',
            'D = Job TITLES and appointments (what you ARE employed as)',
            'L1 = Direct patient care, clinical practice',
            'L2 = Clinical leadership roles (Chief of Service, Medical Director of X Unit)',
            'L3 = Clinical quality improvement, patient safety initiatives',
            'L = Clinical Practice, Innovation, and Leadership (L1-L3)',
            'Clinical roles that are job titles → D. Clinical care activities → L.'
        ],

        'routing_rules': {
            'L_vs_D': 'L if CLINICAL PRACTICE/CARE activity (RN in ICU, Attending Physician in Cardiology). D if JOB TITLE/APPOINTMENT (Assistant Professor, Director of Nursing Research).',
            'L_vs_M': 'L for factual or role-based clinical practice descriptions. M1 for aspirational or hypothesis-driven research VISION/STATEMENT/INTERESTS, even when patient-focused ("I aim to improve outcomes").',
            'clinical_roles': 'RN, Staff Physician, Clinical Nurse, Attending Physician → L1 (clinical practice). Assistant Professor, Associate Professor → D (job title).',
            'clinical_leadership': 'L2 if CLINICAL leadership (Chief of Surgery, Medical Director of ICU). O/P if INSTITUTIONAL/ADMINISTRATIVE leadership (Department Chair, Committee Member).',
            'job_vs_activity': 'Test: Is this describing a JOB you hold OR clinical CARE you provide? Job → D. Care → L.'
        },

        'trigger_keywords': {
            'clinical_practice': ['rn', 'attending physician', 'clinical service', 'patient care', 'icu', 'clinical nurse', 'clinic hours', 'on-call', 'rounds'],
            'research_vision': ['vision', 'i aim to', 'my research', 'research interests', 'research statement', 'research activities', 'biosketch', 'specific aims', 'grant', 'funded by', 'research focus']
        },

        'alternative_parents': [
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research',
                'reason': 'Research vision/statement with clinical context vs. clinical practice',
                'show_all_children': True,
                'trigger_keywords': ['vision', 'i aim to', 'my research', 'research interests', 'research statement', 'research activities', 'biosketch', 'specific aims', 'grant', 'funded by', 'research focus', 'my work focuses'],
                'disambiguation_guidance': [
                    'IF narrative VISION/STATEMENT/INTERESTS about research → M1 Research Activities/Statement',
                    'IF describing actual clinical PRACTICE/CARE delivery → L1 Clinical Practice',
                    'Keywords strongly favoring M1: "vision", "I aim to", "my research", "research interests", "biosketch", "research statement", "specific aims", "grant", "funded by"',
                    'Keywords strongly favoring L1: "RN", "attending physician", "clinical service", "patient care", "ICU", "clinic hours", "on-call", "rounds"',
                    'CRITICAL: "I aim to utilize my art as a data translator" → M1 (research vision), NOT L1',
                    'CRITICAL: Vision statements with clinical context ("healthcare systems", "patients") still go to M1 if describing RESEARCH goals/interests',
                    'CRITICAL: Narrative about research mission, vision, or interests → M1, even if mentions clinical applications',
                    'Example: "My clinical experience inspires my research on patient outcomes" → M1 (research vision, not L1 clinical practice)'
                ]
            },
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research (specifically M1 Research Activities / M2A-M2C Clinical Trials)',
                'reason': 'CONFUSION AREA #11: QI/clinical improvement vs research studies; Clinical trials as practice vs research',
                'show_all_children': True,
                'trigger_keywords': ['quality improvement', 'qi project', 'practice improvement', 'implementation', 'registry', 'clinical trial', 'nct', 'research study', 'hypothesis'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "quality improvement", "QI project", "registry", "clinical trial", "implementation project"',
                    '',
                    'KEY QUESTION: Is the primary purpose to improve/deliver care (L) or generate generalizable research knowledge (M)?',
                    '',
                    'Indicators favoring L (Clinical Practice/Innovation):',
                    '  • Quality improvement language: "QI", "quality improvement initiative", "practice improvement"',
                    '  • Implementation focus: "implementation of new protocol", "clinical operations", "service redesign"',
                    '  • Local scope: institution-specific changes, no external sponsor',
                    '  • No trial registration: no NCT number, no IRB research protocol mentioned',
                    '',
                    'Indicators favoring M1/M2A-M2C (Research Activities/Clinical Trials):',
                    '  • Research framing: "study", "trial", "hypothesis", "research aims", "specific aims"',
                    '  • External registration: NCT number (NCT01234567)',
                    '  • Sponsor identified: NIH, pharma company, foundation funding',
                    '  • Research role: "Principal Investigator", "Co-Investigator", "Site PI"',
                    '  NOTE: Clinical trials use M2A (active), M2B (completed), M2C (pending) based on status',
                    '',
                    'REGISTRY/DATABASE DISAMBIGUATION:',
                    '  L: "Developed institutional stroke registry for care coordination" (clinical tool)',
                    '  M1: "PI, National Stroke Registry - prospective cohort study, NCT12345" (research platform)',
                    '',
                    'CLINICAL TRIAL DISAMBIGUATION:',
                    '  L: "Participating physician in industry-sponsored trial" (routine clinical role)',
                    '  M2A: "Sub-Investigator, Phase III randomized trial of XYZ therapy (NCT01234567)" (active research role)',
                    '',
                    'QI vs IMPLEMENTATION SCIENCE:',
                    '  L: "QI project: Reducing surgical site infections through checklist implementation" (local improvement)',
                    '  M1: "Implementation science study: Evaluating checklist adoption across 50 hospitals" (research)',
                    '',
                    'CRITICAL TEST: Ask "Is this FOR improving my institution\'s care (L) or FOR advancing field knowledge (M)?"',
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment',
                'reason': 'Clinical job titles vs clinical care activities',
                'show_all_children': True,
                'trigger_keywords': ['professor', 'assistant', 'associate', 'director', 'chair', 'dean', 'appointment'],
                'disambiguation_guidance': [
                    'IF job title/appointment (Professor, Director of X Department) → D',
                    'IF clinical practice/care activity (RN, Attending Physician, Clinical Nurse) → L',
                    'Test: Does this describe your EMPLOYMENT or your CLINICAL ACTIVITIES?',
                    'Example: "Assistant Professor of Medicine, 2020-present" → D1 (job title)',
                    'Example: "Attending Physician, Cardiology Service, 2020-present" → L1 (clinical practice)',
                    'Example: "RN Intensive Care Unit, 2020-2024" → L1 (clinical practice)',
                    'Clinical titles WITH academic rank → D (e.g., "Associate Professor of Clinical Nursing")',
                    'Clinical roles WITHOUT academic rank → L (e.g., "Clinical Nurse II, Cardiac ICU")'
                ]
            },
            {
                'parent_id': 'institutional_leadership',
                'parent_name': 'O. Institutional Leadership Activities',
                'reason': 'Clinical leadership vs institutional leadership',
                'show_all_children': True,
                'trigger_keywords': ['chair', 'director', 'chief', 'head', 'committee', 'council'],
                'disambiguation_guidance': [
                    'IF CLINICAL leadership (Chief of Surgery, Medical Director of ICU) → L2',
                    'IF INSTITUTIONAL/COMMITTEE leadership (Department Chair, Faculty Senate) → O',
                    'L2 = Leadership IN clinical units/services (patient care delivery)',
                    'O = Leadership IN institutional governance/administration',
                    'Example: "Chief of Cardiology Service" → L2 (clinical leadership)',
                    'Example: "Chair, Department of Medicine" → D1 (if primary job) OR O (if service role)'
                ]
            }],

        'subsection_examples': {
            'L1': {
                'title': 'Clinical Practice (Direct Patient Care)',
                'examples': [
                    'RN Intensive Care Unit — Capital Region Medical Center, Jefferson City, MO, 2020-present',
                    'Attending Physician, Cardiology Service — Johns Hopkins Hospital, 2018-2024',
                    'Clinical Nurse II, Cardiac Intensive Care Unit — University of Missouri Medical Center, 2011-2014',
                    'Staff Psychiatrist, Outpatient Clinic — Veterans Affairs Medical Center, 2015-present',
                    'Clinical Pharmacist, Oncology — Memorial Sloan Kettering, 2020-2023'
                ]
            },
            'L2': {
                'title': 'Clinical Leadership',
                'examples': [
                    'Chief of Surgery — Massachusetts General Hospital, 2022-present',
                    'Medical Director, Intensive Care Unit — Cleveland Clinic, 2020-2024',
                    'Nursing Unit Manager, Emergency Department — UCLA Medical Center, 2018-present',
                    'Division Chief, Pediatric Cardiology — Children\'s Hospital of Philadelphia, 2019-present'
                ]
            },
            'L3': {
                'title': 'Clinical Quality & Safety',
                'examples': [
                    'Led CLABSI reduction initiative, achieving 40% reduction in central line infections, 2021-2022',
                    'Developed sepsis early warning system, implemented hospital-wide, 2023',
                    'Chair, Patient Safety Committee — Barnes-Jewish Hospital, 2020-present',
                    'Quality Improvement Project: Reduced surgical site infections by 25% through enhanced protocols'
                ]
            },
                    }
    },

    # =========================================================================
    # INSTITUTIONAL LEADERSHIP (O) vs ADMINISTRATION (P) - HIGH CONFUSION
    # =========================================================================
    'institutional_leadership': {
        'primary_parent': 'O',
        'canonical_name': 'Institutional Leadership Activities',
        'confusion_risk': 'high',
        'description': 'Executive/oversight roles INSIDE institution with formal authority',

        'routing_rules': {
            'O_vs_P': 'O if: President, Chair (sole), Dean, Director, Chief, Vice Chair. P if: Member, Representative, Participant, Co-Chair (shared).',
            'authority_test': 'O requires decision-making authority over people, programs, or budgets',
            'president_chair_rule': 'University Senate PRESIDENT or Faculty Council CHAIR = Section O (executive leadership), NOT P',
            'chair_detection': 'Chair of Executive Council = O (executive authority). Member of Executive Council = P (committee participation).',
            'exec_titles': 'President, Chair (when sole), Vice President, Dean, Associate Dean, Director, Chief, Vice Chair all indicate O.',
            'committee_titles': 'Elected Member, Representative, Committee Member (not chair), Participant all indicate P.',
            'mixed_roles': 'If entry contains BOTH executive (O) and committee (P) roles: (1) Route to dominant role; (2) In extracted_data, separate into "leadership_roles" (O-level) and "committee_roles" (P-level); (3) Note in reasoning that entry contains mixed roles.'
        },

        'trigger_keywords': {
            'executive': ['president', 'dean', 'director', 'chief', 'vice chair', 'associate dean', 'chair'],
            'committee': ['member', 'representative', 'committee member', 'participant', 'elected member', 'co-chair']
        },

        'alternative_parents': [
            {
                'parent_id': 'institutional_administration',
                'parent_name': 'P. Institutional Administrative Activities',
                'reason': 'Administrative service vs. leadership authority',
                'show_all_children': True,
                'trigger_keywords': ['member', 'committee', 'representative', 'co-chair', 'participant'],
                'disambiguation_guidance': [
                    'IF sole authority position (President, Dean, Director, Chair) → O Leadership',
                    'IF shared/committee role (Member, Representative, Co-Chair) → P Administration',
                    'IF reviewing/evaluating programs → P Administration',
                    'IF decision-making authority over budget/people → O Leadership'
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment',
                'reason': 'Academic appointment vs. leadership role',
                'show_all_children': False,
                'trigger_keywords': ['professor', 'assistant professor', 'associate professor', 'appointed'],
                'disambiguation_guidance': [
                    'IF academic appointment (Professor, Assistant Professor) → D1 Academic Appointments',
                    'IF leadership role with authority (Director, Chair) → O Leadership',
                    'Can be BOTH: "Associate Professor and Vice Chair" → O Leadership (with note about dual role)'
                ]
            },
            {
                'parent_id': 'clinical_practice_innovation_leadership',
                'parent_name': 'L. Clinical Practice, Innovation, and Leadership',
                'reason': 'Institutional leadership vs. clinical leadership',
                'show_all_children': True,
                'trigger_keywords': ['clinical service', 'patient care', 'medical director', 'chief of service'],
                'disambiguation_guidance': [
                    'IF institutional/administrative leadership → O Institutional Leadership',
                    'IF clinical leadership (patient care settings) → L2 Clinical Leadership',
                    'O examples: "Chair, Department of Medicine"',
                    'L2 examples: "Chief of Surgery", "Medical Director of ICU"'
                ]
            }
        ],

        'subsection_examples': {
            'O': {
                'title': 'Institutional Leadership Activities (executive)',
                'examples': [
                    'Program Director, Residency Training 2020–present (O - has authority)',
                    'Section Chief, Hospital Medicine 2019–2023 (O - leadership role)',
                    'Vice Chair for Education, Department of Medicine 2021–present (O - executive)',
                    'Associate Dean for Student Affairs 2020–present (O - dean-level)',
                    'Chair, Faculty Senate Executive Committee 2022–present (O - chair role)'
                ]
            }
        }
    },

    'institutional_administration': {
        'primary_parent': 'P',
        'canonical_name': 'Institutional Administrative Activities',
        'confusion_risk': 'medium',
        'description': 'Committee service, governance roles WITHOUT executive authority',

        'routing_rules': {
            'P_vs_O': 'P for committee participation. O for chairs/directors with formal authority.',
            'internal_vs_external': 'P for INTERNAL institutional committees. Q for EXTERNAL organization service.'
        },

        'trigger_keywords': {
            'committee': ['member', 'representative', 'committee member', 'participant', 'co-chair'],
            'executive': ['president', 'dean', 'director', 'chief', 'chair']
        },

        'alternative_parents': [
            {
                'parent_id': 'institutional_leadership',
                'parent_name': 'O. Institutional Leadership Activities',
                'reason': 'Committee role vs. executive authority',
                'show_all_children': True,
                'trigger_keywords': [
                    'president', 'dean', 'director', 'chief', 'chair', 'chairing',
                    'executive member', 'executive committee member',
                    'sole representative', 'sole faculty representative',
                    'oversight committee chair', 'search committee chair',
                    'chair of', 'chaired', 'co-chair with sole authority'
                ],
                'disambiguation_guidance': [
                    'IF committee participation/membership → P Administration',
                    'IF executive/chair role with authority → O Leadership',
                    'O indicators: "Chair of X", "Sole representative", "Executive Member", "Chairing"',
                    'P indicators: "Member of X", "Participated in", "Served on"',
                    'Test: Does role have decision-making authority or chair title? → O. Just membership? → P.'
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment',
                'reason': 'Committee service vs. job title/position',
                'show_all_children': True,
                'trigger_keywords': ['professor', 'director', 'appointment', 'employed as', 'position held'],
                'disambiguation_guidance': [
                    'IF describing JOB TITLE/APPOINTMENT (employment relationship) → D Professional Positions',
                    'IF describing COMMITTEE SERVICE/ADMINISTRATIVE ROLE → P Institutional Administration',
                    'D examples: "Associate Professor 2018-present", "Clinical Director"',
                    'P examples: "Member, Faculty Council", "Representative to Senate"',
                    'Test: Does this show an employment relationship? → D. Committee participation? → P.'
                ]
            }
        ],

        'subsection_examples': {
            'P': {
                'title': 'Institutional Administrative Activities',
                'examples': [
                    'Elected member, Faculty Senate Executive Committee 2021–present (P - member not chair)',
                    'Co-Chair, Curriculum Committee (shared leadership) 2022–present (P - shared not sole)',
                    'Representative, Department of Medicine to University Council 2020–2023 (P - representative)',
                    'Search Committee member for Department Chair 2022 (P - committee participation)',
                    'Reviewer of BS in Public Health Program 2015',
                    'Lead author, CEPH self-study report section 2018'
                ]
            }
        }
    },

    # =========================================================================
    # EXTRAMURAL PROFESSIONAL (Q) vs MEMBERSHIPS (I) - HIGH CONFUSION
    # =========================================================================
    'extramural_professional_activities': {
        'primary_parent': 'Q',
        'canonical_name': 'Extramural Professional Responsibilities',
        'confusion_risk': 'high',
        'description': 'Service/leadership in EXTERNAL organizations',

        'routing_rules': {
            'Q1_vs_I': 'Q1 if role title (Chair, President, Officer). I if just "Member" or "Fellow".',
            'membership_test': 'Simple membership listing → I. Leadership role → Q1.',
            'Q4D_vs_Q4C': 'Q4D for manuscript reviewer (peer review). Q4C for editorial board member (ongoing role).',
            'reviewer_vs_board': 'Check if REVIEWER (Q4D) or BOARD MEMBER (Q4C). Keywords: "Reviewer for Journal X" → Q4D. "Editorial Board, Journal X" → Q4C.'
        },

        'trigger_keywords': {
            'leadership': ['chair', 'president', 'officer', 'treasurer', 'board member', 'council'],
            'membership': ['member', 'fellow', 'affiliate', 'society membership'],
            'reviewer': ['reviewer', 'manuscript review', 'peer review', 'reviewed for'],
            'editorial': ['editorial board', 'editor', 'associate editor', 'section editor']
        },

        'alternative_parents': [
            {
                'parent_id': 'professional_orgs_societies',
                'parent_name': 'I. Professional Organizations & Society Memberships',
                'reason': 'Simple membership vs. leadership/service role',
                'show_all_children': True,
                'trigger_keywords': ['member', 'fellow', 'affiliate', 'society'],
                'disambiguation_guidance': [
                    'IF role title (Chair, President, Officer, Board Member) → Q1 Leadership',
                    'IF simple membership (Member, Fellow) → I Memberships',
                    'IF regional representative (non-voting) → I Memberships'
                ]
            },
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography',
                'reason': 'Editorial/reviewer activity vs. publications',
                'show_all_children': False,
                'trigger_keywords': ['journal', 'publication', 'article'],
                'disambiguation_guidance': [
                    'IF listing where person SERVES as reviewer/editor → Q4D or Q4C',
                    'IF listing person\'s OWN publications → S Bibliography',
                    '"Editorial Board, Journal X" → Q4C. "Published in Journal X" → S1/S2.'
                ]
            },
            {
                'parent_id': 'invitations_to_speak',
                'parent_name': 'R. Invitations to Speak/Present',
                'reason': 'External service role vs. invited speaking',
                'show_all_children': True,
                'trigger_keywords': ['keynote', 'plenary', 'invited speaker', 'visiting professor', 'grand rounds'],
                'disambiguation_guidance': [
                    'IF external service/committee role → Q Extramural Professional Activities',
                    'IF invited speaking engagement → R Invitations to Speak',
                    'Q examples: "Board Member, ASCO", "Reviewer for NIH"',
                    'R examples: "Keynote at ASCO Annual Meeting", "Invited talk at NIH"'
                ]
            }
        ],

        'subsection_examples': {
            'Q1': {
                'title': 'Leadership in Extramural Organizations',
                'examples': [
                    'Treasurer, International Society for Clinical Research 2021–2024',
                    'Committee Chair, AMA Section Council 2022–present',
                    'Board Member, National Foundation for Medical Research 2020–present',
                    'President-Elect, State Medical Association 2023–2024'
                ]
            },
            'Q2': {
                'title': 'Service on Boards and/or Committees',
                'examples': [
                    'Member, NIH Study Section 2022–present',
                    'Advisory Board Member, Lancet Child and Adolescent Health Journal 2017–current',
                    'Member of Advisory Board to review Policy Brief on Health Professional Education 2016'
                ]
            },
            'Q4C': {
                'title': 'Editorial Board Membership',
                'examples': [
                    'Editorial Board Member, Journal of Adolescent Health March 2020–current',
                    'Associate Editor, JAMA Pediatrics 2021–present',
                    'Advisory Board Member, Innovations in Global Medical & Health Education journal 2014–2017'
                ]
            },
            'Q4D': {
                'title': 'Manuscript Reviewer / Abstract Reviewer',
                'examples': [
                    'Reviewer for Journal of Mental Health 2009',
                    'Reviewer for Education for Health 2005-2017',
                    'Manuscript review for Bulletin of the World Health Organization 2003-2012, 2019, 2021',
                    'Abstract reviewer, APHA Annual meeting 2013, 2014, 2015',
                    'Reviewer for grant proposals, University of Iowa pilot grants 2019–2021'
                ]
            }
        }
    },

    # =========================================================================
    # INVITATIONS TO SPEAK/PRESENT (R) - MEDIUM CONFUSION RISK
    # =========================================================================
    'invitations_to_speak': {
        'primary_parent': 'R',
        'canonical_name': 'Invitations to Speak/Present',
        'confusion_risk': 'medium',
        'description': 'Invited presentations, lectures, keynotes, grand rounds, visiting professorships where you were INVITED (not submitted abstracts). Focus on invitation-based speaking engagements.',

        'core_principles': [
            'R = INVITED to speak (keynote, plenary, visiting professor)',
            'S8 = SUBMITTED abstract/presentation (not invited)',
            'K = Regular teaching (courses you instruct)',
            'R1 = Invited keynotes, named lectures, plenaries',
            'R2 = Grand rounds, visiting professorships',
            'R3 = Invited panels, workshops, symposia'
        ],

        'decision_order': [
            '1) Was this INVITED or SUBMITTED?',
            '2) If INVITED: What type? → Keynote (R1), Grand Rounds (R2), Panel/Workshop (R3)',
            '3) If SUBMITTED: Conference abstract → S8, not R',
            '4) If regular teaching course → K (Educational Contributions)',
            '5) If unclear: Look for "invited", "keynote", "visiting professor" → R'
        ],

        'routing_rules': {
            'invited_vs_submitted': 'R if you were INVITED to speak (keynote, plenary, named lecture). S8 if you SUBMITTED an abstract/proposal.',
            'keynote_indicators': 'R1 for: Keynote speaker, Plenary speaker, Distinguished lecturer, Named lecture series, Commencement speaker, Major invited lectures.',
            'grand_rounds': 'R2 for: Grand rounds presentations, Visiting professor/scholar, Departmental seminar (invited), Invited colloquium.',
            'panels_workshops': 'R3 for: Invited panelist, Workshop facilitator (invited), Symposium organizer, Conference chair (NOT chair as in keynote).',
            'teaching_vs_invited': 'K if REGULAR teaching (course you teach each year). R if ONE-TIME invited lecture/visit.',
            'guest_lecture_ambiguity': 'Guest lecture in someone else\'s course → likely R2 if invited, K if you regularly co-teach.',
            'header_context_routing': 'Section header provides strong routing signal: "MAJOR INVITED LECTURES" or "KEYNOTES" → R1. "GRAND ROUNDS" → R2. "PANELS" or "WORKSHOPS" → R3.',
            'podium_vs_workshop': 'Podium presentation (invited) at conference → Usually R1 if keynote/plenary, R3 if symposium/panel. Workshop facilitation → R3.'
        },

        'trigger_keywords': {
            'invited': ['invited', 'keynote', 'plenary', 'distinguished lecture', 'visiting professor', 'named lecture'],
            'grand_rounds': ['grand rounds', 'departmental seminar', 'colloquium', 'visiting scholar'],
            'submitted': ['poster', 'abstract', 'presentation submitted', 'conference submission'],
            'teaching': ['course', 'taught', 'instructor', 'teaching assistant', 'curriculum'],
            'panel': ['panelist', 'panel discussion', 'symposium', 'workshop facilitator']
        },

        'alternative_parents': [
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography',
                'reason': 'Invited talk vs. submitted conference abstract',
                'show_all_children': False,
                'trigger_keywords': ['abstract', 'poster', 'presentation', 'conference'],
                'disambiguation_guidance': [
                    'IF you were INVITED to speak (keynote, plenary) → R1',
                    'IF you SUBMITTED abstract/poster to conference → S8',
                    'Indicators of S8: "Abstract #123", "Poster session", volume(suppl) citation',
                    'Indicators of R: "Invited keynote", "Plenary speaker", "Distinguished lecture"'
                ]
            },
            {
                'parent_id': 'educational_contributions',
                'parent_name': 'K. Educational Contributions',
                'reason': 'One-time invited lecture vs. regular teaching',
                'show_all_children': True,
                'trigger_keywords': ['course', 'teaching', 'instructor', 'curriculum'],
                'disambiguation_guidance': [
                    'IF regular course you teach (recurring) → K1 (Didactic Teaching)',
                    'IF one-time invited guest lecture → R2 (Grand Rounds/Visiting Professor)',
                    'IF invited to teach workshop/CME → could be R2 or K4 (depends on context)',
                    'Example: "Guest lecture in Pathology 501" once → R2. Teaching Pathology 501 every year → K1'
                ]
            },
            {
                'parent_id': 'extramural_professional_activities',
                'parent_name': 'Q. Extramural Professional Responsibilities',
                'reason': 'Invited speaker vs. conference leadership role',
                'show_all_children': False,
                'trigger_keywords': ['chair', 'organizer', 'conference director', 'program committee'],
                'disambiguation_guidance': [
                    'IF invited to SPEAK at conference → R',
                    'IF ORGANIZING/CHAIRING the conference → Q1 (Extramural Leadership)',
                    'IF serving on PROGRAM COMMITTEE → Q (not R)',
                    'Example: "Conference Chair" → Q1. "Keynote speaker at that conference" → R1'
                ]
            },
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research',
                'reason': 'Invited talk vs. research statement/vision',
                'show_all_children': False,
                'trigger_keywords': ['research statement', 'research interests', 'vision', 'biosketch', 'my research'],
                'disambiguation_guidance': [
                    'IF invited speaking engagement → R Invitations to Speak',
                    'IF research description/vision statement → M1 Research Activities',
                    'R indicators: venue name, date, "invited", "keynote"',
                    'M1 indicators: "research interests", "my work focuses", "vision"'
                ]
            },
            {
                'parent_id': 'other',
                'parent_name': 'T. Other (Structural Header)',
                'reason': 'Media interview/press coverage vs scientific presentation',
                'show_all_children': False,
                'trigger_keywords': [
                    'interviewed by', 'interview with', 'interview in', 'featured in',
                    'profiled in', 'appeared in', 'press', 'news', 'media coverage',
                    'newspaper', 'magazine', 'herald', 'times', 'post', 'tribune',
                    'cnn', 'npr', 'bbc', 'reuters'
                ],
                'disambiguation_guidance': [
                    'IF scientific presentation/talk → R Invitations to Speak',
                    'IF media interview/press coverage → T Other',
                    'R indicators: "Keynote at conference", "Grand Rounds at hospital", "Invited seminar"',
                    'T indicators: "Interview in New York Times", "Featured in Science Magazine", "Press coverage"',
                    'Test: Is this a scientific venue where you presented? → R. Media outlet that covered you? → T.'
                ]
            }
        ],

        'subsection_examples': {
            'R1': {
                'title': 'Invited Keynotes, Named Lectures, Plenaries',
                'examples': [
                    'Keynote speaker, American Society for Clinical Oncology Annual Meeting, Chicago, IL, June 2023',
                    'Distinguished Lecturer, Johns Hopkins School of Medicine, "Precision Medicine in Cancer Care", Baltimore, MD, March 2022',
                    'Plenary speaker, International Conference on Global Health, "Digital Health Innovations", Geneva, Switzerland, September 2021',
                    'Harvey Cushing Oration, American Association of Neurological Surgeons, April 2020'
                ]
            },
            'R2': {
                'title': 'Grand Rounds, Visiting Professorships, Departmental Seminars',
                'examples': [
                    'Internal Medicine Grand Rounds, Massachusetts General Hospital, "Novel Biomarkers in Sepsis", Boston, MA, January 2024',
                    'Visiting Professor, Department of Epidemiology, University of Washington, Seattle, WA, May 2023',
                    'Invited speaker, Cardiology Research Seminar Series, Stanford University, October 2022',
                    'Guest lecture, Clinical Skills course (MD program), Weill Cornell Medicine, "Evidence-based Medicine", 2021'
                ]
            },
            'R3': {
                'title': 'Invited Panels, Workshops, Symposia',
                'examples': [
                    'Panelist, "AI in Clinical Decision Support", Health Datapalooza Conference, Washington DC, April 2024',
                    'Workshop facilitator, "Grant Writing for Early Career Investigators", AAMC Annual Meeting, November 2023',
                    'Invited symposium speaker, "Translational Genomics", Cold Spring Harbor Laboratory, August 2022',
                    'Panel discussant, "Future of Medical Education", UCSF Dean\'s Forum, San Francisco, CA, March 2021'
                ]
            }
        }
    },

    # =========================================================================
    # HONORS AND AWARDS (H) - HIGH CONFUSION RISK
    # =========================================================================
    'honors_and_awards': {
        'primary_parent': 'H',
        'canonical_name': 'Honors and Awards',
        'confusion_risk': 'high',
        'description': 'Academic honors, awards, prizes, recognitions, honorary fellowships, and distinctions. NOT funding grants or membership categories.',

        'core_principles': [
            'H = Competitive recognition/honor without substantial research funding',
            'M2 = Research grants/funding with PI role, budget, project period',
            'I = Professional membership category/status (including Fellow status)',
            'R = Invitation to speak (named lectures may be honors embodied as talks)',
            'Focus: Recognition of achievement vs. ongoing support vs. membership'
        ],

        'decision_order': [
            '1) FUNDING TEST: Does it provide research support (PI role, budget, grant number)?',
            '   → YES: M2 (Research Support), even if called "award"',
            '   → NO: Continue to step 2',
            '2) MEMBERSHIP TEST: Is it an ongoing membership category/status?',
            '   → YES: I (Professional Organizations), even if called "Fellow"',
            '   → NO: Continue to step 3',
            '3) LECTURE TEST: Is the honor embodied as a talk/lecture?',
            '   → Focus on talk details (title, venue, date): R (Invited Presentations)',
            '   → Focus on being selected/named: H (Honor)',
            '4) DEFAULT: Recognition/distinction → H (Honors and Awards)'
        ],

        'routing_rules': {
            'H_vs_M2_grant_awards': 'H if symbolic recognition. M2 if research funding (has PI/Co-I role, budget, grant number, project period). Example: "NIH K23 Career Development Award" → M2 (has funding). "Best Paper Award" → H (no funding).',
            'H_vs_I_fellow_status': 'H if competitive selection/honor. I if ongoing membership category. Example: "Elected Fellow, AAAS" → check context: one-time recognition → H; ongoing Fellow status → I.',
            'H_vs_R_named_lectures': 'H if focus on being selected/named. R if focus on talk details. Example: "Smith Distinguished Lecture" → awarded the honor → H; details about the talk → R.',
            'fellowship_disambiguation': '"Fellowship" can mean: (1) Research training grant → M2; (2) Honor/recognition → H; (3) Membership status → I. Check for funding details, competitive selection language, or membership context.',
            'award_lecture_test': 'Named lecture that IS an award → H. Regular invited lecture → R. Test: Would this appear in an "Awards" CV section? → H. "Invited Talks" section? → R.',
            'mentored_award_routing': 'Mentored awards (K23, K12, KL2, T32) with mentor name and funding → M2. Check for explicit mentor relationship, funding period, PI/scholar role.'
        },

        'trigger_keywords': {
            'honor_indicators': ['award', 'prize', 'recognition', 'distinction', 'merit', 'excellence', 'outstanding', 'best', 'finalist', 'nominee', 'recipient', 'awardee', 'medal', 'laureate'],
            'grant_indicators': ['pi', 'co-investigator', 'grant', 'funding', 'award amount', 'budget', 'project period', 'source of support', 'nih', 'nsf', 'r01', 'k23', 'u01'],
            'fellowship_ambiguous': ['fellow', 'fellowship', 'elected fellow', 'senior fellow', 'honorary fellow', 'distinguished fellow'],
            'membership_indicators': ['member', 'membership', 'society', 'association', 'since', 'present', 'ongoing'],
            'lecture_indicators': ['lecture', 'lecturer', 'named lecture', 'distinguished lecture', 'keynote', 'oration']
        },

        'alternative_parents': [
            {
                'parent_id': 'professional_orgs_societies',
                'parent_name': 'I. Professional Organizations & Society Memberships / Q1. Leadership',
                'reason': 'CONFUSION AREA #1: Fellow status as honor vs. membership category vs. leadership role',
                'show_all_children': False,
                'trigger_keywords': ['fellow', 'elected fellow', 'honorary member', 'distinguished member', 'society fellow', 'board member', 'elected to board'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "Fellow", "Fellowship", "Elected Fellow", "Senior Fellow", "Board Member"',
                    '',
                    'KEY QUESTION: Is this primarily a recognition/award, membership status, or a leadership role?',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • Competitive selection/recognition language ("awarded", "elected", "distinguished")',
                    '  • Appears in CV section with other awards/prizes',
                    '  • Single event/selection year (e.g., "2015 – Elected Fellow")',
                    '  • Focus on honor conferred for scholarly contributions',
                    '  • Honorary titles: "Honorary Fellow", "Distinguished Fellow"',
                    '',
                    'Indicators favoring I (Membership):',
                    '  • Ongoing membership category ("Fellow status", "Fellow member")',
                    '  • Listed with other professional societies',
                    '  • Date range showing ongoing membership ("2015–present")',
                    '  • No competitive wording, just membership class designation',
                    '  • Simple "Member, [Society Name]" without leadership role',
                    '',
                    'Indicators favoring Q1 (Extramural Leadership):',
                    '  • Explicit role title: "Board Member", "President", "Chair", "Officer", "Treasurer"',
                    '  • Leadership term limits ("Board Member, 2023–2026")',
                    '  • Active governance/committee role (not just membership status)',
                    '',
                    'EXAMPLES:',
                    '  H: "2015 – Elected Fellow, American Psychopathological Association" (competitive recognition)',
                    '  I: "Fellow, American College of Physicians since 2015" (ongoing membership category)',
                    '  Q1: "Board Member, AANEM, 11/2023–10/2026" (leadership role)',
                    '  H: "2016/2017 Senior Fellow, Humanities Center, Northeastern University" (competitive internal honor)',
                    '  I: "Member, Fellow class, American Public Health Association" (membership designation)',
                    '  Q1: "President, American Association of Neuromuscular Medicine, 2022–2024" (leadership)',
                    '',
                    'BIDIRECTIONAL: H → I when "Elected Fellow" is actually ongoing membership. I → H when "Honorary Member" is one-time recognition. I/H → Q1 when role involves leadership duties.'
                ]
            },
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research (specifically M2 - Research Support)',
                'reason': 'CONFUSION AREA #2: Awards with funding vs. symbolic recognition',
                'show_all_children': False,
                'trigger_keywords': ['award', 'career development award', 'fellowship', 'mentored award', 'loan repayment', 'k award', 't32', 'f award'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "Award", "Career Development Award", "Fellowship", "Mentored Award", "Loan Repayment Program"',
                    '',
                    'KEY QUESTION: Is this primarily about money to support research or symbolic recognition?',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • No clear budget, project title, or role (PI/Co-I/Scholar)',
                    '  • Appears among teaching awards, departmental prizes',
                    '  • Phrasing like "Best Paper Award", "Young Investigator Award"',
                    '  • Small/symbolic awards ("$500 travel award")',
                    '',
                    'Indicators favoring M2 (Research Support):',
                    '  • Includes PI/Co-PI/Co-I role or "Scholar" designation',
                    '  • Explicit grant number (R01, K23, T32, F31, etc.) or funding agency + period',
                    '  • Mentions "Total funding", "Award amount", "Project period", "Direct costs"',
                    '  • Has mentor name (for mentored awards: K, T, F series)',
                    '  • Project title or research aims described',
                    '',
                    'EXAMPLES:',
                    '  H: "2020 Young Investigator Award, American Heart Association" (recognition, no substantial funding)',
                    '  M2: "NIH K23 Career Development Award, 2019-2024, $600K total costs, PI" (research funding)',
                    '  H: "Best Poster Award, National Conference, 2021" (symbolic recognition)',
                    '  M2: "NIH Loan Repayment Program in Health Disparities, 2005-2012, $50K/year" (funding mechanism)',
                    '  M2: "AHA Predoctoral Fellowship, 2023-2025, PI, $100K" (funded fellowship)',
                    '',
                    'SPECIAL CASE - Training Grants: T32, F31, K12 usually → M2 (funding), but small travel/conference awards → H',
                    '',
                    'BIDIRECTIONAL: H → M2 when "award" has grant mechanics. M2 → H when pilot/seed grants are tiny symbolic amounts.'
                ]
            },
            {
                'parent_id': 'invitations_to_speak',
                'parent_name': 'R. Invitations to Speak/Present',
                'reason': 'CONFUSION AREA #3: Named lectures as honors vs. presentations',
                'show_all_children': False,
                'trigger_keywords': ['distinguished lecture', 'named lecture', 'award lecture', 'keynote speaker', 'honorary lecture'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "Distinguished Lecture", "Named Lecture", "Award Lecture", "Keynote Speaker for [X] Award"',
                    '',
                    'KEY QUESTION: Is the focus on the talk given, or on the honor of being selected?',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • Entry is mostly about being selected/named, not talk details',
                    '  • Appears among awards with no abstract/poster context',
                    '  • Phrasing emphasizes "award lecture", "honorary lecture", "named lecture"',
                    '  • No talk title, just "Smith Distinguished Lecturer 2023"',
                    '',
                    'Indicators favoring R (Invited Presentations):',
                    '  • Has talk title, meeting name, city, date like other talks',
                    '  • "Invited presentation", "Grand Rounds", "Keynote" emphasized as speaking events',
                    '  • Appears in list with other conference/seminar presentations',
                    '  • Focus on venue and presentation topic',
                    '',
                    'EXAMPLES:',
                    '  H: "2023 John Smith Distinguished Lecture Award, Harvard Medical School" (honor/selection)',
                    '  R: "John Smith Distinguished Lecture: \'Advances in Immunotherapy\', Harvard Medical School, Nov 2023" (presentation)',
                    '  H: "Keynote speaker selection, Global Stigma Conference 2016" (honor)',
                    '  R: "Keynote: \'Global Stigma Patterns\', Global Stigma Conference, March 2016" (invited talk)',
                    '',
                    'TEST: Does the CV list this in "Awards" or "Invited Lectures"? Context matters.',
                    '',
                    'BIDIRECTIONAL: H → R when award/named lectures show talk details. R → H when ordinary invited lectures are mis-coded due to adjectives like "distinguished" or "keynote".'
                ]
            },
            {
                'parent_id': 'education_received',
                'parent_name': 'B. Education Received (specifically B1 - Academic Degrees)',
                'reason': 'CONFUSION AREA #4: Honors embedded in education (summa cum laude, scholarships with degrees)',
                'show_all_children': False,
                'trigger_keywords': ['summa cum laude', 'magna cum laude', 'cum laude', 'with distinction', 'honors program', 'dean\'s list', 'scholarship', 'academic excellence'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "summa cum laude", "with distinction", "honors program", "Dean\'s list", "scholarship", "fellowship"',
                    '',
                    'KEY QUESTION: Is this primarily recording an academic degree/program or highlighting recognition?',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • Free-standing scholarship/prize NOT required for degree completion',
                    '  • Phrases: "award", "prize", "scholarship", "recognition", "Dean\'s Award" (not "Dean\'s List")',
                    '  • Scholarship for outstanding performance (competitive, not need-based)',
                    '  • Listed in awards section separate from education',
                    '  • "Graduated summa cum laude" AS STANDALONE LINE in awards section',
                    '',
                    'Indicators favoring B (Education):',
                    '  • Degree name or training program plus dates and institution',
                    '  • Honors appear as modifiers of a degree (e.g., "BS Biology, magna cum laude")',
                    '  • Latin honors (summa/magna/cum laude) embedded within degree line',
                    '  • "Scholarship for Outstanding Academic Performance, 1987–1990" IF listed under education with degree program',
                    '  • School name + graduation date + honor modifier',
                    '',
                    'EXAMPLES:',
                    '  H: "Scholarship for Outstanding Academic Performance, National Science Foundation, 1987–1990" (free-standing competitive award)',
                    '  B1: "BS in Psychology, summa cum laude, University of Mary Washington, 2009" (honor modifier of degree)',
                    '  H: "Dean\'s Award for Excellence in Research, 2015" (competitive award)',
                    '  B1: "Dean\'s List, Fall 2014, Spring 2015" (academic standing marker, part of education record)',
                    '  B1: "Graduated summa cum laude, Lyceum No.1, Bydgoszcz, Poland, 06/1986" (IF under EDUCATION section)',
                    '  H: "Graduated summa cum laude, Lyceum No.1, Bydgoszcz, Poland, 06/1986" (IF under AWARDS section)',
                    '',
                    'CRITICAL TEST: Check section header and whether honor is embedded in degree line or standalone',
                    '  • "BS Biology, summa cum laude" = B1 (honor is modifier)',
                    '  • "summa cum laude distinction" = H (if listed separately in awards)',
                    '',
                    'BIDIRECTIONAL: H → B when graduation honors/scholarships are part of degree listing. B → H when honors appear standalone in awards section even with school context.'
                ]
            },
            {
                'parent_id': 'licensure_and_certification',
                'parent_name': 'F. Licensure and Certification',
                'reason': 'CONFUSION AREA #7: Certification honors vs credential status (BIDIRECTIONAL)',
                'show_all_children': False,
                'trigger_keywords': ['board certified', 'top 10%', 'top 5%', 'passed with distinction', 'with honors', 'honor roll', 'highest score'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "board certified", "top 10%", "passed with distinction", "with honors", "honor roll"',
                    '',
                    'KEY QUESTION: Is this celebrating exam performance/achievement (H) or documenting credential status (F)?',
                    '',
                    'Indicators favoring H (Honors):',
                    '  • Performance metrics: "top 10%", "top 5%", "highest score", "95th percentile"',
                    '  • Achievement language: "passed with distinction", "with honors", "outstanding performance"',
                    '  • Competitive ranking: "scored in 95th percentile", "top of class"',
                    '  • Recognition for exam excellence (not just passing)',
                    '  • Listed in awards section or with other honors',
                    '  • Emphasis on HOW WELL you passed, not just THAT you passed',
                    '',
                    'Indicators favoring F (Licensure):',
                    '  • Bare credential statement: "Board Certified, [Board], [Year]"',
                    '  • No performance metrics or achievement language',
                    '  • Focus on status/authorization to practice',
                    '  • Listed in licensure section without competitive language',
                    '  • Simple "Diplomate of [Board]" or "Licensed in [State]"',
                    '  • Emphasis on credential obtained, not performance',
                    '',
                    'EXAMPLES:',
                    '  H: "Board Certified, American Board of Psychiatry and Neurology, 2018, Top 10%" (exam achievement)',
                    '  F2: "Board Certified, American Board of Psychiatry and Neurology, 2018" (credential status)',
                    '  H: "ABIM Certification Exam, Passed with Distinction, 2015" (exam honor)',
                    '  F2: "Diplomate, American Board of Internal Medicine, 2015" (certification status)',
                    '  H: "Medical License Exam (USMLE Step 3), Score 265, 95th percentile, 2017" (exam performance)',
                    '  F1: "Medical License, State of California, 2017" (license status)',
                    '',
                    'CRITICAL TEST: Check for achievement/performance language',
                    '  • If performance metrics present (top X%, with distinction) → H',
                    '  • If bare credential only (no performance language) → F',
                    '  • "Board Certified, ABPN, 2018" = F2 (status only)',
                    '  • "Board Certified, ABPN, 2018, Top 5%" = H (performance honor)',
                    '',
                    'BIDIRECTIONAL: H → F when exam achievements are listed without distinction language. F → H when credential includes performance metric.'
                ]
            }
        ],

        'subsection_examples': {
            'H': {
                'title': 'Honors and Awards',
                'description': 'Academic honors, prizes, awards, distinctions, honorary titles and fellowships (no substantial research funding)',
                'examples': [
                    'NIH Early Career Award for Excellence in Research, 2018',
                    'Best Paper Award, American Cancer Society Annual Meeting, 2020',
                    'Young Investigator Award, Society for Epidemiology, 2019 ($1,000)',
                    'Dean\'s Teaching Excellence Award, 2021',
                    'Elected Fellow, American Psychopathological Association, 2015 (competitive honor)',
                    'Alpha Omega Alpha Honor Medical Society, inducted 2012',
                    'Phi Beta Kappa, 2008',
                    'Summa Cum Laude, Harvard College, 2010',
                    'John Smith Distinguished Lecture Award (named honor), 2023',
                    'Outstanding Mentor Award, Department of Medicine, 2022',
                    'Finalist, Blavatnik National Awards for Young Scientists, 2020'
                ]
            }
        }
    },

    'professional_orgs_societies': {
        'primary_parent': 'I',
        'canonical_name': 'Professional Organizations & Society Memberships',
        'confusion_risk': 'medium',
        'description': 'EXTERNAL organization memberships (passive, not leadership roles)',

        'routing_rules': {
            'I_vs_Q1': 'I for simple membership. Q1 for leadership roles in external organizations.',
            'membership_indicators': 'Member, Fellow, Affiliate (without role title) → I'
        },

        'trigger_keywords': {
            'membership': ['member', 'fellow', 'affiliate', 'society membership'],
            'leadership': ['chair', 'president', 'officer', 'board member']
        },

        'alternative_parents': [
            {
                'parent_id': 'extramural_professional_activities',
                'parent_name': 'Q. Extramural Professional Responsibilities',
                'reason': 'Leadership role vs. simple membership',
                'show_all_children': True,
                'trigger_keywords': ['chair', 'president', 'officer', 'board', 'council'],
                'disambiguation_guidance': [
                    'IF has role title (Chair, President, Officer) → Q1 Leadership',
                    'IF simple membership → I Memberships'
                ]
            }
        ],

        'subsection_examples': {
            'I': {
                'title': 'Professional Organizations & Society Memberships',
                'examples': [
                    'Inaugural member, Society for Physician-Scientists 2020–present',
                    'Regional representative (non-voting), American College of Physicians',
                    'Fellow, American Academy of Neurology since 2018',
                    'Member, International Society for Pharmacoeconomics 2019–present',
                    'American Public Health Association (APHA) member',
                    'American Sociological Association (ASA) member'
                ]
            }
        }
    },

    # =========================================================================
    # MENTORING (N) - MEDIUM-HIGH CONFUSION RISK
    # =========================================================================
    'mentoring': {
        'primary_parent': 'N',
        'canonical_name': 'Mentoring',
        'confusion_risk': 'medium',
        'description': 'Formal supervision and guidance of trainees/junior faculty',

        'routing_rules': {
            'N_vs_K': 'N for sustained supervision/mentoring relationships. K for teaching/instruction.',
            'N1_vs_N3': 'N1 for program leadership. N3/N4 for individual mentee relationships.',
            'research_vs_clinical': 'Specify type: research mentorship (N3) vs clinical supervision (K2)',
            'mentee_positions_vs_own_appointments': 'CRITICAL: If section header says "MENTEES" or "ADVISEES", entries show mentee positions (their job titles), NOT your appointments. Example: "Research Assistant Professor, Emory University, present" in a FACULTY MENTEES section means you mentored someone who is a Research Assistant Professor. Route to N3/N4, extract mentee name/position/institution/years.',
            'header_detection': 'Section headers like "MENTEES", "ADVISEES", "SUPERVISED STUDENTS" are CRITICAL indicators → N',
            'role_markers': 'Look for "advisor", "supervisor", "committee chair" → N',
            'degree_levels': 'Specify N1 (predoctoral) vs N2 (postdoctoral) vs N3 (junior faculty)'
        },

        'trigger_keywords': {
            'mentoring': ['mentor', 'advisor', 'supervised', 'mentee', 'advisee', 'thesis', 'dissertation', 'independent study', 'directed study'],
            'teaching': ['instructor', 'taught', 'course', 'class'],
            'section_headers': ['mentees', 'advisees', 'supervised students', 'trainees supervised', 'students advised']
        },

        'alternative_parents': [
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography',
                'reason': 'Mentee thesis/dissertation listed as publication',
                'show_all_children': False,
                'trigger_keywords': ['thesis:', 'dissertation:', 'defended', 'publication'],
                'disambiguation_guidance': [
                    'IF listing supervised students with their projects → N Mentoring',
                    'IF listing thesis/dissertation as a publication citation → S3 Books/Chapters',
                    'CHECK section header: "MENTEES", "ADVISEES" → strongly suggests N'
                ]
            },
            {
                'parent_id': 'educational_contributions',
                'parent_name': 'K. Educational Contributions',
                'reason': 'Teaching vs. mentoring distinction',
                'show_all_children': True,
                'trigger_keywords': ['course', 'class', 'taught', 'instructor'],
                'disambiguation_guidance': [
                    'IF formal course teaching → K1 Didactic Teaching',
                    'IF one-on-one student supervision → N Mentoring',
                    'IF directed studies/independent study → N Mentoring'
                ]
            },
            {
                'parent_id': 'postdoctoral_training',
                'parent_name': 'C. Postdoctoral Training',
                'reason': 'Supervising trainees vs. receiving training',
                'show_all_children': False,
                'trigger_keywords': ['postdoctoral', 'postdoc', 'fellow', 'residency', 'resident', 'fellowship'],
                'disambiguation_guidance': [
                    'CRITICAL: Whose training is being described?',
                    'IF YOU supervised/mentored postdocs/residents/fellows → N Mentoring',
                    'IF YOU received postdoctoral/residency/fellowship training → C Postdoctoral Training',
                    'Section header: "MENTEES", "TRAINEES SUPERVISED" → N (their training)',
                    'Section header: "POSTDOCTORAL TRAINING", "RESIDENCY" → C (your training)'
                ]
            },
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education and Training',
                'reason': 'Mentee education vs. mentor\'s own education',
                'show_all_children': True,
                'trigger_keywords': ['degree', 'graduated', 'coursework', 'phd', 'md', 'training program'],
                'disambiguation_guidance': [
                    'CRITICAL: Determine WHOSE education is being described',
                    'IF CV owner is SUPERVISING students (listing their degrees) → N Mentoring',
                    'IF CV owner is describing THEIR OWN education received → B Education and Training',
                    'Section header: "MENTEES", "ADVISEES" with degrees listed → N',
                    'Section header: "EDUCATION", "TRAINING" → B'
                ]
            },
            {
                'parent_id': 'professional_positions_employment',
                'parent_name': 'D. Professional Positions & Employment',
                'reason': 'Mentee positions vs. your own positions',
                'show_all_children': True,
                'trigger_keywords': ['assistant professor', 'associate professor', 'postdoc', 'position', 'appointment'],
                'disambiguation_guidance': [
                    'CRITICAL: Determine WHOSE position is being described',
                    'IF CV owner is SUPERVISING trainees (listing their positions) → N Mentoring',
                    'IF CV owner is describing THEIR OWN positions held → D Professional Positions',
                    'Section header: "MENTEES", "ADVISEES", "FACULTY MENTORED" → N',
                    'Section header: "APPOINTMENTS", "EMPLOYMENT", "POSITIONS" → D',
                    'Example: "Assistant Professor, Emory, 2020-present" in FACULTY MENTEES → N',
                    'Example: "Assistant Professor, Emory, 2020-present" in YOUR APPOINTMENTS → D'
                ]
            },
            # CONFUSION AREA #4 (BIDIRECTIONAL): N ↔ M2 (Mentoring vs Research Support/Training Grants)
            {
                'parent_id': 'research_overview',
                'parent_name': 'M. Research (specifically M2 - Research Support)',
                'reason': 'CONFUSION AREA #4: Mentoring on funded fellowships vs. training grants as funding',
                'show_all_children': True,
                'trigger_keywords': ['t32', 'k12', 'k23', 'k08', 'k99', 'kl2', 'f31', 'f32', 'training grant', 'career development', 'funding', 'grant number', 'award amount'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #4: Mentoring (N) vs Training Grants (M2) ===',
                    '',
                    'KEY QUESTION: Is this documenting MENTORING RELATIONSHIPS or FUNDING mechanics?',
                    '',
                    '→ N (Mentoring) if:',
                    '  • Mentor/mentee names listed: "Mentor: Dr. Smith", list of trainees supervised',
                    '  • Supervision responsibilities emphasized: "supervised 3 postdocs", "trained 5 students"',
                    '  • Training program structure: "T32 Training Grant - supervised the following trainees:"',
                    '  • Mentee outcomes listed: "John Doe (now at Harvard)", "completed PhD 2023"',
                    '  • Focus: Documenting mentoring relationships',
                    '',
                    '→ M2 (Research Support) if:',
                    '  • Grant mechanics emphasized: mechanism code (K23, T32), funding amount, project period',
                    '  • PI/Co-I role emphasized: "Principal Investigator", "Co-Investigator"',
                    '  • Project aims/goals described: research objectives, specific aims',
                    '  • No mentee names listed (or only brief mentor attribution)',
                    '  • Focus: Documenting funding received',
                    '',
                    'MECHANISM CODE PATTERNS:',
                    '  • T32: Institutional training grant → Usually N (program with trainees)',
                    '  • K12: Mentored career development → Usually N if mentees listed, M2 if describing own funding',
                    '  • K23, K08, K99, KL2: Individual career development → Usually M2 (own funding)',
                    '  • F31, F32: Individual fellowships → Usually M2 (own funding)',
                    '',
                    'EXAMPLES:',
                    '  N1: "NIH T32 Training Grant - Director 2018-present. Supervised: John Doe (PhD 2020), Jane Smith (PhD 2022)"',
                    '      → Focus on mentees and program leadership',
                    '  M2: "NIH K23 AI123456 (PI: J. Smith) 2020-2025 $750K; Mentor: Dr. Jones"',
                    '      → Focus on funding mechanics, brief mentor mention',
                    '  N3: "Mentored Career Development Award K23: Worked with mentor Dr. Chen on sepsis research"',
                    '      → Focus on mentoring relationship',
                    '  M2: "Career Development Award, American Heart Assoc., 2022-2024, $150K, Co-mentor: Dr. Lee"',
                    '      → Focus on award amount and period',
                    '',
                    'SPECIAL CASES:',
                    '  • "T32 Director" with trainee list → N1 (Leadership & Mentoring Programs)',
                    '  • "K12 Scholar" receiving funding → M2 (Research Support)',
                    '  • "Mentor on K23 award for Dr. Smith" → N3 (Current Mentees - Research)',
                    '  • Section header "RESEARCH SUPPORT" → bias toward M2',
                    '  • Section header "MENTEES" or "TRAINEES" → bias toward N',
                    '',
                    'BIDIRECTIONAL: N → M2 when mentoring entries specify grant numbers and funding. M2 → N when training grants emphasize mentees.'
                ]
            }
        ],

        'subsection_examples': {
            'N1': {
                'title': 'Leadership & Mentoring Programs',
                'examples': [
                    'Director, Faculty Mentoring Program 2020–present',
                    'Co-lead, Institutional Peer Mentoring Initiative 2022–2023',
                    'Coordinator, Resident Mentoring Pilot Project 2019–2021',
                    'Developed online mentoring toolkit for early-career faculty 2023'
                ]
            },
            'N3': {
                'title': 'Current Mentees (Research)',
                'examples': [
                    'Primary mentor, PhD candidate in Immunology (2019–present)',
                    'Co-mentor, postdoc in Machine Learning for Pathology (2022–present)',
                    'Advisor, MSc thesis on Clinical Data Mining (completed 2021)',
                    'Mentor, undergraduate summer research intern (2023)',
                    'Dissertation committee member, Jane Doe (Epidemiology) 2020–2023',
                    'Thesis advisor: Noah Wick, "Barriers to PrEP uptake", BS Public Health 2020–2021'
                ]
            }
        }
    },

    # =========================================================================
    # RESEARCH (M) - MEDIUM CONFUSION RISK
    # =========================================================================
    'research_overview': {
        'primary_parent': 'M',
        'canonical_name': 'Research',
        'confusion_risk': 'medium',
        'description': 'Scientific/scholarly work: activities, support, IP, clinical trials',

        'routing_rules': {
            'M1_vs_M2': 'M1 for research narrative/statement/vision/interests/activities/IRB protocols. M2 for grants/funding.',
            'M2D_vs_M2': 'M2D for patents/inventions. M2A/M2B/M2C for grant support.',
            'M2A_vs_L': 'M2A/M2B/M2C for research clinical trials (based on status). L for routine clinical practice.',
            'M1_vs_S': 'M1 for research description/narrative/vision statement. S for publication list.',
            'M1_vs_L': 'M1 for research vision/activities (narrative). L for clinical practice descriptions.'
        },

        'trigger_keywords': {
            'narrative_research': ['vision', 'research interests', 'research activities', 'research statement', 'research focus', 'research mission', 'biosketch', 'irb protocol', 'statement of key contributions', 'my research', 'i aim to', 'my work focuses'],
            'activities': ['principal investigator', 'co-investigator', 'research focuses', 'developed'],
            'funding': ['nih', 'grant', 'r01', 'funding', 'award', 'foundation'],
            'patents': ['patent', 'invention', 'provisional', 'licensing'],
            'trials': ['clinical trial', 'nct', 'phase ii', 'phase iii']
        },

        'alternative_parents': [
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography',
                'reason': 'Research publications vs. research activities',
                'show_all_children': False,
                'trigger_keywords': ['published', 'article', 'journal', 'doi'],
                'disambiguation_guidance': [
                    'IF describing research interests/activities → M1 Research Activities',
                    'IF listing specific publications → S Bibliography',
                    'IF grant support → M2 Research Support'
                ]
            },
            {
                'parent_id': 'clinical_practice_innovation_leadership',
                'parent_name': 'L. Clinical Practice, Innovation, and Leadership',
                'reason': 'CONFUSION AREA #11: QI/clinical improvement vs research studies; Clinical trials as practice vs research',
                'show_all_children': False,
                'trigger_keywords': ['quality improvement', 'qi project', 'practice improvement', 'implementation', 'registry', 'clinical trial', 'outcomes study', 'program evaluation', 'clinical database', 'prospective cohort'],
                'disambiguation_guidance': [
                    'AMBIGUOUS TERMS: "quality improvement", "QI project", "registry", "clinical trial", "implementation project", "outcomes study", "program evaluation"',
                    '',
                    'KEY QUESTION: Is the primary purpose to improve/deliver care (L) or generate generalizable research knowledge (M)?',
                    '',
                    'Indicators favoring L (Clinical Practice/Innovation):',
                    '  • Quality improvement language: "QI", "quality improvement initiative", "practice improvement", "reducing door-to-needle time"',
                    '  • Implementation focus: "implementation of new protocol", "clinical operations", "service redesign", "program evaluation"',
                    '  • Local scope: institution-specific changes, no external sponsor',
                    '  • No trial registration: no NCT number, no IRB research protocol mentioned',
                    '  • Care delivery emphasis: "improving patient outcomes", "practice efficiency", "workflow optimization"',
                    '  • Clinical operations: "clinical service", "attending physician", "RN", "patient care responsibilities"',
                    '',
                    'Indicators favoring M1/M2A-M2C (Research Activities/Clinical Trials):',
                    '  • Research framing: "study", "trial", "hypothesis", "research aims", "specific aims", "data analysis plan"',
                    '  • External registration: NCT number (NCT01234567), ClinicalTrials.gov listing',
                    '  • Sponsor identified: NIH, pharma company, foundation funding',
                    '  • Research role: "Principal Investigator", "Co-Investigator", "Site PI", "Sub-Investigator"',
                    '  • Randomization/control: "randomized controlled trial", "phase II", "phase III", "placebo-controlled"',
                    '  • Generalizable knowledge: "disseminate findings", "publish results", "contribute to evidence base"',
                    '  • IRB protocol: "IRB-approved research protocol #12345"',
                    '  NOTE: Clinical trials use M2A (active), M2B (completed), M2C (pending) based on status',
                    '',
                    'REGISTRY/DATABASE DISAMBIGUATION:',
                    '  L: "Developed institutional stroke registry for care coordination" (clinical tool)',
                    '  M1: "PI, National Stroke Registry - prospective cohort study, NCT12345" (research platform)',
                    '  L: "Maintain clinical database for quality metrics reporting" (operations)',
                    '  M1: "Lead investigator, longitudinal cohort (n=5000), doi:10.5061/dryad.xyz" (research dataset)',
                    '',
                    'CLINICAL TRIAL DISAMBIGUATION:',
                    '  L: "Participating physician in industry-sponsored trial" (routine clinical role, no PI/Co-I designation)',
                    '  M2A: "Sub-Investigator, Phase III randomized trial of XYZ therapy (NCT01234567)" (active research role)',
                    '  M2B: "Site PI, Multi-center RCT of Novel Anticoagulant, 2019-2023" (completed research)',
                    '',
                    'QI vs IMPLEMENTATION SCIENCE:',
                    '  L: "QI project: Reducing surgical site infections through checklist implementation" (local improvement)',
                    '  M1: "Implementation science study: Evaluating checklist adoption across 50 hospitals" (research on implementation)',
                    '',
                    'EXAMPLES:',
                    '  L: "PI, \'Reducing Door-to-Needle Time in Acute Stroke\': A Quality Improvement Initiative, University Hospital, 2022–present"',
                    '  M2A: "Sub-Investigator, Phase III randomized clinical trial of XYZ therapy in heart failure (NCT01234567), 2019–present"',
                    '  L: "Developed and maintain institutional sepsis registry for quality reporting"',
                    '  M1: "PI, Multi-center Sepsis Outcomes Registry - prospective cohort study, NIH-funded, 2020-2025"',
                    '',
                    'CRITICAL TEST: Ask "Is this FOR improving my institution\'s care (L) or FOR advancing field knowledge (M)?"',
                    '',
                    'SPECIAL CASES:',
                    '  • If both QI AND research: Check primary emphasis. "QI project that we plan to publish" → usually still L unless IRB research protocol.',
                    '  • Program evaluation: Usually L unless explicitly funded research with aims/hypotheses.',
                    '  • Outcomes tracking: L if routine clinical metrics; M if research study with analysis plan.',
                    '',
                    'BIDIRECTIONAL: L → M when QI uses research methods. M → L when clinical trials emphasize routine participation over research role.'
                ]
            },
            # CONFUSION AREA #5 (BIDIRECTIONAL): M2D ↔ S11 (Patents vs Software/Code)
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography (specifically S11 - Software/Code)',
                'reason': 'CONFUSION AREA #5: Patents for software vs. public code releases',
                'show_all_children': False,
                'trigger_keywords': ['software', 'code', 'algorithm', 'tool', 'platform', 'app', 'package', 'github', 'pypi', 'cran', 'repository'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #5: Patents (M2D) vs Software/Code (S11) ===',
                    '',
                    'KEY QUESTION: Is this legal IP protection (M2D) or public software dissemination (S11)?',
                    '',
                    '→ M2D (Patents & Inventions) if:',
                    '  • Patent filing: "filed", "provisional", "issued"',
                    '  • Patent numbers: US1234567B2, PCT/US2023/12345',
                    '  • Licensing agreements: commercial licenses, royalty terms',
                    '  • Invention disclosures: technology transfer office',
                    '  • IP protection: "patent pending", "proprietary"',
                    '  • Focus: Legal protection of intellectual property',
                    '',
                    '→ S11 (Software/Code) if:',
                    '  • Code repository: GitHub, GitLab, Bitbucket URL',
                    '  • Package release: PyPI, CRAN, npm, Bioconductor',
                    '  • Software DOI: Zenodo, Software Heritage',
                    '  • Open-source license: MIT, GPL, Apache',
                    '  • Version numbers: v1.0, release 2.3.1',
                    '  • Focus: Publicly available code/tools for research community',
                    '',
                    'EXAMPLES:',
                    '  M2D: "Patent US9876543B2 \'Machine learning diagnostic method\' issued 2023"',
                    '  S11: "immunoTools v2.0, PyPI package (doi:10.5281/zenodo.1234567) – Python toolkit (2024)"',
                    '  M2D: "Licensing agreement for \'DiagnosticAI\' software with MedTech Inc. 2024"',
                    '  S11: "EHR-Parser 1.3, GitHub repo, https://github.com/user/EHR-Parser (2022)"',
                    '',
                    'BIDIRECTIONAL: M2D → S11 when patents describe "software tool" without patent numbers. S11 → M2D when software entry mentions patent protection.'
                ]
            },
            # CONFUSION AREA #6 (BIDIRECTIONAL): M1 ↔ S12 (Research Activities vs Datasets)
            {
                'parent_id': 'bibliography',
                'parent_name': 'S. Bibliography (specifically S12 - Datasets)',
                'reason': 'CONFUSION AREA #6: Research registries/databases vs. published datasets',
                'show_all_children': False,
                'trigger_keywords': ['dataset', 'data repository', 'zenodo', 'dryad', 'figshare', 'doi', 'accession', 'deposit'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #6: Research Activities (M1) vs Datasets (S12) ===',
                    '',
                    'KEY QUESTION: Is this research infrastructure/activity (M1) or citable dataset product (S12)?',
                    '',
                    '→ M1 (Research Activities/Statement) if:',
                    '  • Research narrative: "My research focuses on...", "I study..."',
                    '  • Data collection as activity: "collected 500 patient samples"',
                    '  • Project description: aims, methods, ongoing work',
                    '  • Research interests/vision statement',
                    '  • IRB protocols describing data collection',
                    '  • Focus: Describing research work, not citing dataset',
                    '',
                    '→ S12 (Datasets) if:',
                    '  • Repository deposit: Zenodo, Dryad, Figshare, GenBank, GEO',
                    '  • Dataset DOI: 10.5281/zenodo.*, 10.6084/*, etc.',
                    '  • Versioned data release: "v1.0", "Release 2023"',
                    '  • Accession numbers: GSE123456, PRJNA123456',
                    '  • Data descriptor: size, format, contents of dataset',
                    '  • Focus: Citable data artifact available for reuse',
                    '',
                    'CRITICAL DISTINCTION:',
                    '  • M1 = "Process" (research activity description)',
                    '  • S12 = "Product" (dataset as publication)',
                    '',
                    'EXAMPLES:',
                    '  M1: "My research activities include large-scale genomic data collection from cancer patients"',
                    '  S12: "Cancer Genomics Dataset v1.0, Zenodo (doi:10.5281/zenodo.7654321) – 500 samples (2024)"',
                    '  M1: "IRB Protocol #12345: Prospective data collection of EHR records for ML model development"',
                    '  S12: "GSE123456 - RNA-seq profiles of renal carcinoma, GEO 2023"',
                    '',
                    'NOTE: Peer-reviewed data descriptor ARTICLES → S16 (not S12)',
                    'BIDIRECTIONAL: M1 → S12 when research statements mention specific released datasets with citations. S12 → M1 when datasets lack DOIs and read like research activities.'
                ]
            },
            # NEW FIX #42: Add R (Invitations to Speak) as alternative parent (Issue 2033_I02)
            # Prevents research-themed talks from being misclassified as Research Activities
            {
                'parent_id': 'invitations_to_speak',
                'parent_name': 'R. Invitations to Speak/Present',
                'reason': 'Invited talks about research vs. research activities/projects',
                'show_all_children': False,
                'trigger_keywords': ['invited', 'talk', 'presentation', 'seminar', 'keynote', 'speaker', 'lecture', 'symposium', 'conference presentation', 'workshop'],
                'disambiguation_guidance': [
                    'IF describing an invited TALK/PRESENTATION/SEMINAR → R Invitations to Speak',
                    'IF describing research ACTIVITIES/PROJECTS/GRANTS → M Research',
                    'Keywords favoring R: "invited talk", "keynote", "speaker", "seminar", "presentation", "symposium", "lecture"',
                    'Keywords favoring M: "principal investigator", "grant", "research project", "funded by", "IRB protocol"',
                    'VENUE/EVENT context favors R (e.g., "Vienna University December 2010")',
                    'FUNDING/ROLE context favors M (e.g., "NIH R01", "PI on grant")'
                ]
            },
            # CONFUSION AREA #4: M2 ↔ N (Research Funding vs Mentoring / Training Grants)
            {
                'parent_id': 'mentoring',
                'parent_name': 'N. Mentoring',
                'reason': 'CONFUSION AREA #4: Training grants can be research funding (M2) or mentoring programs (N)',
                'show_all_children': True,
                'trigger_keywords': ['t32', 'k12', 'k23', 'k08', 'k99', 'kl2', 'training grant', 'training program', 'career development', 'mentored award', 'mentor:', 'mentee:', 'trainee', 'supervised'],
                'disambiguation_guidance': [
                    '=== CONFUSION AREA #4: Training Grants / Mentored Awards ===',
                    '',
                    'KEY QUESTION: Is this documenting FUNDING or MENTORING RELATIONSHIPS?',
                    '',
                    '→ M2 (Research Support) if:',
                    '  • Grant mechanics emphasized: mechanism code (K23, T32), funding amount, project period',
                    '  • PI/Co-I role emphasized: "Principal Investigator", "Co-Investigator"',
                    '  • Project aims/goals described: research objectives, specific aims',
                    '  • No mentee names listed (or only brief mentor attribution)',
                    '  • Focus: Documenting funding received',
                    '',
                    '→ N (Mentoring) if:',
                    '  • Mentor/mentee names listed: "Mentor: Dr. Smith", list of trainees supervised',
                    '  • Supervision responsibilities emphasized: "supervised 3 postdocs", "trained 5 students"',
                    '  • Training program structure: "T32 Training Grant - supervised the following trainees:"',
                    '  • Mentee outcomes listed: "John Doe (now at Harvard)", "completed PhD 2023"',
                    '  • Focus: Documenting mentoring relationships',
                    '',
                    'MECHANISM CODE PATTERNS:',
                    '  • T32: Institutional training grant → Usually N (program with trainees)',
                    '  • K12: Mentored career development → Usually N if mentees listed, M2 if describing own funding',
                    '  • K23, K08, K99, KL2: Individual career development → Usually M2 (own funding)',
                    '  • F31, F32: Individual fellowships → Usually M2 (own funding)',
                    '',
                    'EXAMPLES:',
                    '  M2: "NIH K23 AI123456 (PI: J. Smith) 2020-2025 $750K; Mentor: Dr. Jones"',
                    '      → Focus on funding mechanics, brief mentor mention',
                    '  N1: "NIH T32 Training Grant - Director 2018-present. Supervised: John Doe (PhD 2020), Jane Smith (PhD 2022)"',
                    '      → Focus on mentees and program leadership',
                    '  M2: "Career Development Award, American Heart Assoc., 2022-2024, $150K, Co-mentor: Dr. Lee"',
                    '      → Focus on award amount and period',
                    '  N3: "Mentored Career Development Award K23: Worked with mentor Dr. Chen on sepsis research"',
                    '      → Focus on mentoring relationship',
                    '',
                    'SPECIAL CASES:',
                    '  • "T32 Director" with trainee list → N1 (Leadership & Mentoring Programs)',
                    '  • "K12 Scholar" receiving funding → M2 (Research Support)',
                    '  • "Mentor on K23 award for Dr. Smith" → N3 (Current Mentees - Research)',
                    '  • Section header "RESEARCH SUPPORT" → bias toward M2',
                    '  • Section header "MENTEES" or "TRAINEES" → bias toward N'
                ]
            }
        ],

        'subsection_examples': {
            'M1': {
                'title': 'Research Activities / Statement',
                'description': 'Narrative research interests, vision, activities (similar to NIH Biosketch). IRB protocols. Statement of Key Contributions.',
                'examples': [
                    'VISION: I aim to utilize my art as a data translator across multiple sensory modalities to make complex health information accessible to diverse populations.',
                    'Research Activities: My research focuses on developing novel computational methods for analyzing single-cell transcriptomic data in cancer immunology.',
                    'My research interests include machine learning applications in clinical decision support, electronic health record phenotyping, and predictive modeling for sepsis outcomes.',
                    'IRB Protocol #12345: Active study examining the impact of social determinants on COVID-19 outcomes in underserved communities.',
                    'Principal Investigator, Immunogenomics of Cancer Study 2022–present',
                    'Co-investigator, Machine Learning for EHR Phenotyping project 2023–2025',
                    'Statement of Key Contributions: I have pioneered the use of wearable sensors for continuous vital sign monitoring in critically ill patients.'
                ]
            },
            'M2': {
                'title': 'Research Support (Grants/Funding)',
                'examples': [
                    'NIH R01 CA123456 \'Tumor PD-L1 Pathways\' (PI: J. Doe) 2022–2027 $1.2M',
                    'DoD Idea Award \'TBI Rehab Study\' 2021–2025 Co-I $500K',
                    'AHA Grant 23POST102 Postdoctoral Fellowship 2023–2025 $158K',
                    'Foundation for Cancer Research Pilot Award 2022 PI $50K'
                ]
            },
            'M2D': {
                'title': 'Patents & Inventions',
                'examples': [
                    'Patent US1234567B2 \'Nanoparticle-based PD-L1 inhibitor\' filed 2023, pending',
                    'Provisional patent \'Wearable ECG patch with AI analysis\' filed 2022',
                    'Licensing agreement for \'ImmunoAI diagnostic kit\' executed with BioTech Inc. 2024'
                ]
            }
            # NOTE: Clinical trials now use M2A/M2B/M2C based on status (active/completed/pending)
            # Example: 'PI, Phase II trial evaluating PD-L1 inhibitor response (NCT12345678) 2023–present' → M2A
        }
    },

    # =========================================================================
    # OTHER PROFESSIONAL INFORMATION (T) - MEDIUM CONFUSION
    # =========================================================================
    'unknown': {
        'primary_parent': 'T',
        'canonical_name': 'Appendix / Other',
        'confusion_risk': 'medium',
        'description': 'Additional professional information including language skills, military service, or other relevant non-categorical data',

        'routing_rules': {
            'languages_vs_training': 'T if listing language proficiency levels (Native, Fluent, Intermediate). B2 if describing language courses/training attended.',
            'personal_vs_professional': 'T5 for professional context languages. A for contact info only.'
        },

        'trigger_keywords': {
            'languages': ['fluent', 'native', 'proficient', 'intermediate', 'language'],
            'training': ['course', 'institute', 'training', 'workshop']
        },

        'alternative_parents': [
            {
                'parent_id': 'education_and_training',
                'parent_name': 'B. Education',
                'reason': 'Language proficiency vs. language training',
                'show_all_children': False,
                'trigger_keywords': ['course', 'institute', 'training'],
                'disambiguation_guidance': [
                    'IF listing current proficiency levels → T5',
                    'IF describing language courses attended → B2 Professional Development'
                ]
            }
        ],

        'subsection_examples': {
            'T5': {
                'title': 'Other Professional Information - Languages',
                'examples': [
                    'English (Native); French (Fluent); Arabic (Intermediate)',
                    'Spanish (Fluent); Mandarin (Beginning)',
                    'English (Native); German (Intermediate); Portuguese (Beginning)'
                ]
            }
        }
    }
}


# =============================================================================
# CONFUSION DETECTION FUNCTIONS
# =============================================================================

def get_confusion_info(parent_section_id: str) -> Dict[str, Any]:
    """
    Get confusion matrix information for a section.

    Args:
        parent_section_id: Either full ID (e.g., 'educational_contributions') or code (e.g., 'K')
    """
    # Try direct lookup first
    info = SECTION_CONFUSION_MATRIX.get(parent_section_id)
    if info:
        return info

    # If not found, try looking up by code using taxonomy_contexts mapping
    try:
        from core.taxonomy_contexts import get_parent_section_config
        parent_config = get_parent_section_config(parent_section_id)
        if parent_config:
            return SECTION_CONFUSION_MATRIX.get(parent_config['id'], {})
    except ImportError:
        pass

    return {}


# =============================================================================
# STRUCTURAL SIGNAL FUNCTIONS (Phase 1)
# =============================================================================

def _score_clinical_role_keywords(text: str) -> float:
    """
    Detect clinical role patterns like RN, Clinical Nurse, Staff Physician.

    Returns 1.0 if clinical role detected (and NOT a mentee listing), 0.0 otherwise.

    IMPORTANT: RN can appear in multiple sections (Clinical Practice, Mentoring).
    This signal excludes entries that look like mentee listings (name + degree).
    """
    if not text:
        return 0.0

    # Check for clinical role keywords
    if re.search(r'\b(RN|Clinical Nurse|Staff Nurse|Staff Physician|Attending Physician)\b', text, re.IGNORECASE):
        # Exclude if it looks like a mentee listing (name followed by degree)
        # Pattern: FirstName, MiddleInitial LastName, Degree
        if re.search(r'[A-Z][a-z]+,\s+[A-Z]\.?\s+[A-Z][a-z]+.*\b(PhD|MD|MS|MA|MPH|ScD|RN)\b', text):
            return 0.0  # Likely a mentee listing like "Smith, J. Doe, PhD, RN"
        return 1.0

    return 0.0


def _score_major_invited_lecture_header(text: str, header: str) -> float:
    """
    Strong signal from section header for keynotes/major invited lectures.

    Returns 1.0 if header suggests major invited lectures (R1), 0.0 otherwise.
    """
    if not header:
        return 0.0

    h = header.upper()

    # Strong indicators of R1 (Keynotes)
    if 'MAJOR' in h and ('INVITED' in h or 'LECTURE' in h or 'KEYNOTE' in h):
        return 1.0
    if 'KEYNOTE' in h or 'PLENARY' in h or 'DISTINGUISHED LECTURE' in h:
        return 1.0

    return 0.0


def _score_training_received_header(text: str, header: str) -> float:
    """
    Strong signal from section header for training/education the person RECEIVED.

    Returns 1.0 if header suggests training received (B2 - Professional Development), 0.0 otherwise.

    IMPORTANT: Distinguish from K (teaching) - this is for education YOU took, not education YOU gave.
    """
    if not header:
        return 0.0

    h = header.upper()

    # Strong indicators of B2 (Professional Development & Continuing Education)
    if 'ADDITIONAL' in h and 'TRAINING' in h:
        return 1.0
    if 'CONTINUING EDUCATION' in h or 'PROFESSIONAL DEVELOPMENT' in h:
        return 1.0
    if 'CME' in h or 'CE CREDITS' in h:
        return 1.0

    return 0.0


def _score_unit_acronym_shape(text: str) -> float:
    """
    Detect clinical unit acronym patterns (ICU, ED, NICU, etc.) near Unit/Center/Dept.

    Returns 1.0 if clinical unit pattern detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # Pattern: 2-4 uppercase letters + nearby "Unit", "Center", "ICU", or "Dept"
    if re.search(r'\b[A-Z]{2,4}\b.{0,20}\b(Unit|Center|ICU|Dept|Department)\b', text):
        return 1.0

    return 0.0


def _score_podium_presentation(text: str) -> float:
    """
    Detect "Podium Presentation" pattern.

    Returns 1.0 if podium presentation detected, 0.0 otherwise.

    NOTE: Podium presentations can be R1 (keynote) or R3 (symposium) depending on header context.
    """
    if not text:
        return 0.0

    if re.search(r'\bPodium Presentation\b', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_date_range_like(text: str) -> bool:
    """
    Helper: Detect date range patterns (YYYY-YYYY, M/D/YYYY-M/D/YYYY, etc.).

    Returns True if date range detected, False otherwise.
    """
    if not text:
        return False

    # YYYY-YYYY or YYYY-present
    if re.search(r'\b\d{4}\s*[-–]\s*(\d{4}|present|current)\b', text, re.IGNORECASE):
        return True

    # M/D/YYYY - M/D/YYYY
    if re.search(r'\b\d{1,2}/\d{1,2}/\d{4}\s*[-–]\s*\d{1,2}/\d{1,2}/\d{4}\b', text):
        return True

    return False


def _score_citation_like(text: str) -> float:
    """
    Detect citation patterns (author list + journal volume/pages).

    Returns 1.0 if citation pattern detected, 0.0 otherwise.

    Pattern: Last names + journal + volume(issue):pages
    Example: "Smith J, Doe A. Title. Journal. 2020;15(3):123-45."
    """
    if not text:
        return 0.0

    # Author pattern: Capital letter + period (initial) or Last name, First initial
    has_authors = re.search(r'[A-Z][a-z]+\s+[A-Z]\.?\s*,|[A-Z]\.\s*[A-Z][a-z]+', text)

    # Volume/issue/pages: volume(issue):pages or volume:pages
    has_citation = re.search(r'\b\d{1,4}\s*\(\d{1,3}\)\s*:\s*\d+[-–]\d+|'
                             r'\b\d{1,4}\s*:\s*\d+[-–]\d+', text)

    if has_authors and has_citation:
        return 1.0

    return 0.0


def _score_location_tail(text: str) -> float:
    """
    Detect location at end of entry (City, State or City, Country).

    Returns 1.0 if location tail detected, 0.0 otherwise.

    Examples:
    - "Johns Hopkins University, Baltimore, MD"
    - "University of Oxford, Oxford, United Kingdom"
    """
    if not text:
        return 0.0

    # City, ST (US state abbreviation)
    if re.search(r',\s+[A-Z][a-z]+\s*,\s+[A-Z]{2}\s*$', text):
        return 1.0

    # City, Country (at end)
    if re.search(r',\s+[A-Z][a-z]+\s*,\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?\s*$', text):
        return 1.0

    return 0.0


def _score_grant_amount(text: str) -> float:
    """
    Detect grant amount patterns ($XXX,XXX or $XXX.XX).

    Returns 1.0 if grant amount detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # $XXX,XXX or $X.XX million
    if re.search(r'\$\s*\d{1,3}(,\d{3})+|\$\s*[\d.]+\s*(million|thousand|M|K)', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_doi_pattern(text: str) -> float:
    """
    Detect DOI (Digital Object Identifier) patterns.

    Returns 1.0 if DOI detected, 0.0 otherwise.

    Example: "doi:10.1234/journal.2020.001" or "DOI: 10.1234/..."
    """
    if not text:
        return 0.0

    if re.search(r'\bdoi\s*:\s*10\.\d{4,}/[^\s]+', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_pmid_pattern(text: str) -> float:
    """
    Detect PMID (PubMed ID) patterns.

    Returns 1.0 if PMID detected, 0.0 otherwise.

    Example: "PMID: 12345678" or "PubMed ID: 12345678"
    """
    if not text:
        return 0.0

    if re.search(r'\b(PMID|PubMed\s+ID)\s*:\s*\d{7,9}\b', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_author_position(text: str) -> Dict[str, float]:
    """
    Detect author position indicators (first, last, corresponding author).

    Returns dict with scores for each position type.

    Examples:
    - "First author" → {'first': 1.0}
    - "Corresponding author" → {'corresponding': 1.0}
    - "Senior author" → {'last': 1.0}
    """
    scores = {
        'first': 0.0,
        'last': 0.0,
        'corresponding': 0.0
    }

    if not text:
        return scores

    text_lower = text.lower()

    if 'first author' in text_lower or 'primary author' in text_lower:
        scores['first'] = 1.0

    if 'last author' in text_lower or 'senior author' in text_lower or 'principal investigator' in text_lower:
        scores['last'] = 1.0

    if 'corresponding author' in text_lower or 'contact author' in text_lower:
        scores['corresponding'] = 1.0

    return scores


def _score_keynote_indicators(text: str) -> float:
    """
    PHASE 2 FIX #16 ENHANCEMENT: Improved keynote/invited talk detection.

    Detect explicit keynote/plenary/invited talk indicators in entry text.

    Returns 1.0 if keynote/invited indicators detected, 0.0 otherwise.

    Examples:
    - "Keynote speaker"
    - "Invited talk at NIH"
    - "Plenary session"
    - "Distinguished lecture"
    - "Invited presentation"
    """
    if not text:
        return 0.0

    # Expanded keynote patterns (PHASE 2 FIX #16)
    keynote_patterns = [
        'keynote',
        'invited talk',
        'invited speaker',
        'invited lecture',
        'invited presentation',
        'plenary',
        'distinguished lecture',
        'named lecture',
        'commencement'
    ]

    text_lower = text.lower()
    for pattern in keynote_patterns:
        if pattern in text_lower:
            return 1.0

    return 0.0


def _score_panel_workshop_indicators(text: str) -> float:
    """
    Detect panel/workshop indicators in entry text.

    Returns 1.0 if panel/workshop indicators detected, 0.0 otherwise.

    Examples:
    - "Panelist"
    - "Workshop facilitator"
    - "Symposium organizer"
    """
    if not text:
        return 0.0

    if re.search(r'\b(Panelist|Panel\s+member|Workshop\s+facilitator|Symposium\s+organizer|Moderator)\b', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_mentee_pattern(text: str) -> float:
    """
    Detect mentee listing patterns (name + degree).

    Returns 1.0 if mentee pattern detected, 0.0 otherwise.

    Example: "Smith, Jane Doe, PhD, RN (2020-2023)"
    """
    if not text:
        return 0.0

    # Pattern: Last, First Middle, Degree
    if re.search(r'[A-Z][a-z]+,\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?\s*,\s*(PhD|MD|MS|MA|MPH|ScD|RN|NP)', text):
        return 1.0

    return 0.0


def _score_teaching_role(text: str) -> float:
    """
    PHASE 2 FIX #20b ENHANCEMENT: Expanded teaching role detection.

    Returns 1.0 if teaching role detected, 0.0 otherwise.

    Examples:
    - "Course Director"
    - "Instructor"
    - "Teaching Assistant"
    - "Guest Lecture"
    - "Curriculum Development"
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Expanded teaching keywords (PHASE 2 FIX #20b)
    teaching_keywords = [
        'instructor',
        'lecturer',
        'teaching assistant',
        'professor',
        'course director',
        'taught',
        'teaching',
        'curriculum',
        'syllabus',
        'guest lecture',
        'seminar leader',
        'lab instructor'
    ]

    for keyword in teaching_keywords:
        if keyword in text_lower:
            return 1.0

    return 0.0


def _score_committee_role(text: str) -> float:
    """
    Detect committee role indicators.

    Returns 1.0 if committee role detected, 0.0 otherwise.

    Examples:
    - "Committee Member"
    - "Committee Chair"
    - "Served on [Committee Name]"
    """
    if not text:
        return 0.0

    if re.search(r'\b(Committee\s+(Member|Chair|Co-Chair)|Served\s+on|Member\s+of.*Committee)\b', text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_committee_service(text: str) -> float:
    """
    PHASE 2 FIX #17: Detect committee and service roles.

    Returns 1.0 if committee/service detected, 0.5 for weak match, 0.0 otherwise.

    Examples:
    - "Committee member" (strong)
    - "Advisory board" (strong)
    - "Editorial board" (strong)
    - "Service to department" (weak)
    - "Volunteer reviewer" (weak)
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Strong committee indicators (PHASE 2 FIX #17)
    strong_committee = [
        'committee member',
        'committee chair',
        'steering committee',
        'advisory board',
        'editorial board',
        'review panel',
        'search committee',
        'curriculum committee'
    ]

    for pattern in strong_committee:
        if pattern in text_lower:
            return 1.0

    # Service indicators (weaker match)
    service_indicators = [
        'service',
        'volunteer',
        'board member',
        'reviewer',
        'editor',
        'organizer'
    ]

    for pattern in service_indicators:
        if pattern in text_lower:
            return 0.5

    return 0.0


def _score_award_honor_keywords(text: str) -> float:
    """
    PHASE 2 FIX #20a ENHANCEMENT: Expanded award/honor detection.

    Returns 1.0 if strong award keywords present, 0.0 otherwise.

    Examples:
    - "Award"
    - "Prize"
    - "Recognition"
    - "Distinguished Scholar"
    - "Fellowship"
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Expanded award keywords (PHASE 2 FIX #20a)
    award_keywords = [
        'award',
        'prize',
        'recognition',
        'honor',
        'fellow',
        'fellowship',  # When not in Education context
        'distinguished',
        'outstanding',
        'excellence',
        'achievement',
        'medal',
        'certificate of',
        'honoree',
        'recipient'
    ]

    for keyword in award_keywords:
        if keyword in text_lower:
            return 1.0

    return 0.0


def _score_date_range_with_institution(text: str) -> float:
    """
    Detect year range + institution name pattern (employment/position indicator).

    Returns 1.0 if both date range AND institution detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    has_range = _score_date_range_like(text)
    has_institution = re.search(r'(University|Hospital|Center|Medical Center|Clinic|College|School of)', text, re.IGNORECASE)

    if has_range and has_institution:
        return 1.0

    return 0.0


def _score_email_pattern(text: str) -> float:
    """
    Detect email address patterns (PHASE 1 FIX #1).

    Returns 1.0 if email detected, 0.0 otherwise.

    Examples:
    - "john.doe@university.edu"
    - "Email: jane_smith@hospital.org"
    - "Contact: researcher123@gmail.com"
    """
    if not text:
        return 0.0

    # Standard email regex pattern
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

    if re.search(email_pattern, text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_phone_pattern(text: str) -> float:
    """
    Detect phone number patterns (PHASE 1 FIX #2).

    Returns 1.0 if phone number detected, 0.0 otherwise.

    Examples:
    - "Phone: (212) 555-1234"
    - "Tel: 212-555-1234"
    - "Mobile: +1-212-555-1234"
    - "Telephone: 212.555.1234"
    """
    if not text:
        return 0.0

    # Match various phone number formats
    phone_patterns = [
        r'(?:phone|tel|telephone|cell|mobile|office)\s*:?\s*(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
        r'\b\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b',  # Simple (123) 456-7890 or 123-456-7890
    ]

    for pattern in phone_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0

    return 0.0


def _score_fax_pattern(text: str) -> float:
    """
    Detect fax number patterns (PHASE 1 FIX #2).

    Returns 1.0 if fax number detected, 0.0 otherwise.

    Examples:
    - "Fax: (212) 555-1234"
    - "Facsimile: 212-555-1234"
    """
    if not text:
        return 0.0

    # Match fax-specific patterns
    fax_pattern = r'(?:fax|facsimile)\s*:?\s*(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})'

    if re.search(fax_pattern, text, re.IGNORECASE):
        return 1.0

    return 0.0


def _score_url_pattern(text: str) -> float:
    """
    PHASE 2 FIX #7: Detect URL patterns (website, LinkedIn, ResearchGate, etc.).

    Returns 1.0 if URL detected, 0.0 otherwise.
    Helps promote contact information to section A (Personal Information).
    """
    if not text:
        return 0.0

    # Match various URL formats
    url_patterns = [
        r'https?://[^\s]+',  # Standard HTTP/HTTPS URLs
        r'www\.[^\s]+',  # URLs starting with www
        r'\b[a-zA-Z0-9-]+\.(?:com|org|edu|net|gov|io|co\.uk|ac\.uk)[^\s]*',  # Domain patterns
    ]

    # Also check for common keywords that indicate web presence
    web_keywords = [
        'website:', 'web:', 'homepage:', 'url:', 'link:',
        'linkedin', 'researchgate', 'orcid', 'scholar.google',
        'github', 'twitter', 'researcherid'
    ]

    text_lower = text.lower()

    # Check for URL patterns
    for pattern in url_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0

    # Check for web keywords
    for keyword in web_keywords:
        if keyword in text_lower:
            return 1.0

    return 0.0


def _score_orcid_pattern(text: str) -> float:
    """
    PHASE 2 FIX #7: Detect ORCID identifier patterns.

    Returns 1.0 if ORCID detected, 0.0 otherwise.
    ORCID format: 0000-0002-1825-0097 (16 digits in 4 groups)
    """
    if not text:
        return 0.0

    # ORCID pattern: 4 groups of 4 digits separated by hyphens
    orcid_pattern = r'\b\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b'

    # Also check for "ORCID:" keyword
    if 'orcid' in text.lower() or re.search(orcid_pattern, text):
        return 1.0

    return 0.0


def _score_grant_number_patterns(text: str) -> float:
    """
    PHASE 2 FIX #11: Detect NIH/NSF grant number patterns.

    Returns 1.0 if grant number detected, 0.0 otherwise.

    Detects patterns like:
    - R01-HL123456, R21-CA098765 (R-series: R01, R03, R15, R21, R34, R35, R61)
    - K99AG067890, K23DK112345 (K-series: K01, K08, K22, K23, K24, K99)
    - U01HL123456, U54CA098765 (U-series: U01, U19, U24, U54)
    - P01AG012345, P50CA098765 (P-series: P01, P20, P30, P50)
    - F31MH123456, F32AG098765 (F-series: F30, F31, F32, F33)
    - T32GM123456 (T-series: T32, T35, T36)
    """
    if not text:
        return 0.0

    # NIH grant number patterns
    # Format: [Letter][2-digit activity code]-[2-letter IC code][6-7 digit serial]
    # Common activity codes
    grant_patterns = [
        # R-series (Research Project Grants)
        r'\b[Rr](?:01|03|15|16|21|24|34|35|36|37|61)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # K-series (Career Development Awards)
        r'\b[Kk](?:01|02|07|08|18|22|23|24|25|43|99|00|R61)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # U-series (Cooperative Agreements)
        r'\b[Uu](?:01|19|24|34|41|42|54|56)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # P-series (Program Project/Center Grants)
        r'\b[Pp](?:01|20|30|40|41|42|50|51|60)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # F-series (Fellowship Awards)
        r'\b[Ff](?:30|31|32|33|99)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # T-series (Training Grants)
        r'\b[Tt](?:15|32|34|35|36|37|90)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # M-series (Research Career Awards)
        r'\b[Mm](?:01)[-\s]?[A-Z]{2,3}\d{5,7}\b',

        # S-series (Research-Related Programs)
        r'\b[Ss](?:06|10|21)[-\s]?[A-Z]{2,3}\d{5,7}\b',
    ]

    # Check for grant number patterns
    for pattern in grant_patterns:
        if re.search(pattern, text):
            return 1.0

    # Also check for explicit grant keywords with numbers
    grant_keywords_with_numbers = [
        r'\bgrant\s+(?:number|#|no\.?)?\s*[A-Z]{1,3}[-\s]?\d{2}',
        r'\b(?:NIH|NSF|NIAID|NCI|NHLBI|NIDA|NIMH|NIA|NINDS)\s+[A-Z]\d{2}',
    ]

    for pattern in grant_keywords_with_numbers:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0

    return 0.0


def _score_date_only_line(text: str) -> float:
    """
    PHASE 2 FIX #14: Detect lines that are purely dates or date ranges (metadata).

    Returns 1.0 if line is date-only, 0.0 otherwise.

    Detects patterns like:
    - "2015-2020"
    - "Jan 2018 - Dec 2020"
    - "2015 - present"
    - "September 2019"
    - Single year: "2020"
    """
    if not text:
        return 0.0

    # Strip whitespace and check length
    text_stripped = text.strip()

    # Must be short (< 50 chars) to be metadata
    if len(text_stripped) > 50:
        return 0.0

    # Date range patterns
    date_patterns = [
        # Year ranges: 2015-2020, 2015 - 2020, 2015 – 2020
        r'^\s*\d{4}\s*[-–—]\s*\d{4}\s*$',
        r'^\s*\d{4}\s*[-–—]\s*(?:present|current|ongoing)\s*$',

        # Month/Year ranges: Jan 2018 - Dec 2020
        r'^\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\s*[-–—]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\s*$',
        r'^\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\s*[-–—]\s*(?:present|current|ongoing)\s*$',

        # Full month names
        r'^\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*[-–—]\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*$',
        r'^\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*[-–—]\s*(?:present|current|ongoing)\s*$',

        # Single dates
        r'^\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s*$',
        r'^\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\s*$',

        # Single year only (but must be 1900-2099)
        r'^\s*(?:19|20)\d{2}\s*$',
    ]

    # Check for date patterns
    for pattern in date_patterns:
        if re.search(pattern, text_stripped, re.IGNORECASE):
            return 1.0

    return 0.0


def _score_conference_pattern(text: str) -> float:
    """
    PHASE 2 FIX #19: Detect conference presentation patterns.

    Returns 1.0 if conference detected, 0.0 otherwise.

    Helps distinguish presentations (R/S8) from publications (S1-S6).
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Conference keywords
    conference_keywords = [
        'conference', 'symposium', 'congress', 'meeting',
        'workshop', 'seminar', 'colloquium', 'forum',
        'annual meeting', 'international meeting',
        'national meeting', 'regional meeting',
        'poster session', 'oral presentation',
        'platform presentation', 'roundtable',
    ]

    # Check for conference keywords
    for keyword in conference_keywords:
        if keyword in text_lower:
            return 1.0

    # Also check for common conference name patterns
    # e.g., "Society for Neuroscience Annual Meeting", "AHA Scientific Sessions"
    conference_patterns = [
        r'\b(?:society|association|academy|federation|congress)\s+(?:for|of)\s+\w+\s+(?:annual|international|national)?\s*(?:meeting|conference|symposium)',
        r'\b(?:annual|international|national|regional)\s+(?:meeting|conference|symposium|congress)',
        r'\bscientific\s+sessions?\b',
    ]

    for pattern in conference_patterns:
        if re.search(pattern, text_lower):
            return 1.0

    return 0.0


def _detect_section_context(section_header: str) -> Dict[str, float]:
    """
    Detect the likely context of a section based on header keywords.

    Returns scores [0.0-1.0] for different contexts.
    """
    header_lower = section_header.lower()

    return {
        'is_education': 1.0 if any(kw in header_lower for kw in
            ['education', 'training', 'degree', 'postdoc', 'fellowship', 'residency']) else 0.0,
        'is_grants': 1.0 if any(kw in header_lower for kw in
            ['grant', 'funding', 'research support', 'extramural']) else 0.0,
        'is_mentoring': 1.0 if any(kw in header_lower for kw in
            ['mentor', 'advisee', 'trainee', 'student']) else 0.0,
        'is_positions': 1.0 if any(kw in header_lower for kw in
            ['position', 'employment', 'appointment', 'academic']) else 0.0,
        'is_invited_talks': 1.0 if any(kw in header_lower for kw in
            ['invited', 'keynote', 'plenary', 'lecture']) else 0.0,
    }


def compute_structural_hints(
    entry_text: str,
    section_header: str = "",
    track_effectiveness: bool = False
) -> Dict[str, Any]:
    """
    Compute all structural hints for an entry.

    Args:
        entry_text: The entry text to analyze
        section_header: The section header (if available)
        track_effectiveness: If True, include all signal scores for tracking

    Returns:
        {
            'scores': Dict[str, float],  # Raw signal scores
            'triggered_hints': List[str],  # Human-readable hint messages
            'signal_summary': Dict  # Summary of which signals fired (if track_effectiveness=True)
        }
    """
    # Detect section context for context-aware signal suppression
    context = _detect_section_context(section_header)

    # Compute all signals
    scores = {
        # Phase 1 signals (already tested)
        'clinical_role': _score_clinical_role_keywords(entry_text),
        'major_lecture_header': _score_major_invited_lecture_header(entry_text, section_header),
        'training_received_header': _score_training_received_header(entry_text, section_header),
        'unit_acronym': _score_unit_acronym_shape(entry_text),
        'podium': _score_podium_presentation(entry_text),
        'employment_pattern': _score_date_range_with_institution(entry_text),

        # Phase 2 signals (new - comprehensive library)
        'citation_like': _score_citation_like(entry_text),
        'location_tail': _score_location_tail(entry_text),
        'grant_amount': _score_grant_amount(entry_text),
        'doi': _score_doi_pattern(entry_text),
        'pmid': _score_pmid_pattern(entry_text),
        'keynote_indicators': _score_keynote_indicators(entry_text),
        'panel_workshop': _score_panel_workshop_indicators(entry_text),
        'mentee_pattern': _score_mentee_pattern(entry_text),
        'teaching_role': _score_teaching_role(entry_text),
        'committee_role': _score_committee_role(entry_text),
        'committee_service': _score_committee_service(entry_text),  # PHASE 2 FIX #17
        'award_honor': _score_award_honor_keywords(entry_text),

        # PHASE 1 FIXES - Contact detection signals
        'email_pattern': _score_email_pattern(entry_text),
        'phone_pattern': _score_phone_pattern(entry_text),
        'fax_pattern': _score_fax_pattern(entry_text),

        # PHASE 2 FIX #7 - Additional contact/identity signals
        'url_pattern': _score_url_pattern(entry_text),
        'orcid_pattern': _score_orcid_pattern(entry_text),

        # PHASE 2 FIX #11 - Grant number detection
        'grant_number_patterns': _score_grant_number_patterns(entry_text),

        # PHASE 2 FIX #14 - Date-only line detection
        'date_only_line': _score_date_only_line(entry_text),

        # PHASE 2 FIX #19 - Conference pattern detection
        'conference_pattern': _score_conference_pattern(entry_text),
    }

    # Author position returns dict, handle separately
    author_pos = _score_author_position(entry_text)
    scores['author_first'] = author_pos['first']
    scores['author_last'] = author_pos['last']
    scores['author_corresponding'] = author_pos['corresponding']

    # ===== FIX #1: Context-aware suppression =====
    # Suppress award_honor in Education and Grants sections (ChatGPT feedback)
    if context['is_education'] >= 0.6 or context['is_grants'] >= 0.6:
        scores['award_honor'] = 0.0

    # Suppress mentee_pattern in Education/Positions sections (ChatGPT feedback)
    if context['is_education'] >= 0.6 or context['is_positions'] >= 0.6:
        scores['mentee_pattern'] = 0.0

    # ===== FIX #2: DOI/PMID boost for citation_like =====
    # If DOI or PMID present, boost citation_like by +0.2 (ChatGPT feedback)
    if scores['doi'] >= 0.8 or scores['pmid'] >= 0.8:
        scores['citation_like'] = min(1.0, scores['citation_like'] + 0.2)

    # ===== FIX #3: Invited talks header bias (R over S8) =====
    # When header indicates invited talks, add strong bias toward R
    # Addresses issue: "Invited Talks — National" routed to S8 instead of R (Albrecht CV)
    invited_talks_bias = context['is_invited_talks']

    # ===== FIX #4: Presentation type detection =====
    # Detect submitted presentations (poster/abstract) vs invited talks
    # Addresses issue: "Scientific Presentations" with plenary/poster → S8 instead of R (Lau CV)
    text_lower = entry_text.lower()
    is_submitted_presentation = any(kw in text_lower for kw in [
        'poster presentation', 'poster session', 'abstract #', 'submitted abstract'
    ])

    # Build human-readable hint messages for triggered signals
    triggered_hints = []

    # Phase 1 signals (high confidence, already tested)
    if scores['clinical_role'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains clinical role pattern (RN, Clinical Nurse, Staff Physician) "
            "→ suggests L (Clinical Practice), NOT D (Positions)."
        )

    if scores['major_lecture_header'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Section header indicates MAJOR INVITED LECTURES "
            "→ suggests R (Invited Speaking), subsection R1 (Keynotes/Major Lectures)."
        )

    if scores['training_received_header'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Section header indicates ADDITIONAL TRAINING or CONTINUING EDUCATION "
            "→ suggests B (Education & Training), subsection B2 (Professional Development & Continuing Education), NOT K (Teaching) or T (Other)."
        )

    if scores['unit_acronym'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains clinical unit acronym pattern (ICU, ED, NICU) "
            "→ suggests L (Clinical Practice)."
        )

    if scores['podium'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains 'Podium Presentation' "
            "→ check header context: MAJOR/KEYNOTE → R1, SYMPOSIUM/PANEL → R3."
        )

    if scores['employment_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry has date range + institution pattern "
            "→ suggests D (Positions) or L (Clinical Practice) depending on job title vs clinical role."
        )

    # Phase 2 signals (new - comprehensive library)
    if scores['citation_like'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry has citation pattern (authors + volume/pages) "
            "→ suggests S (Bibliography), likely peer-reviewed publication."
        )

    if scores['location_tail'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry ends with location (City, State/Country) "
            "→ suggests D (Positions), R (Invited Speaking), or L (Clinical Practice)."
        )

    if scores['grant_amount'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains grant amount ($XXX,XXX) "
            "→ suggests M (Research), subsection M2 (Grants/Funding)."
        )

    if scores['doi'] >= 0.8 or scores['pmid'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains DOI or PMID "
            "→ suggests S (Bibliography), published research article."
        )

    if scores['keynote_indicators'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Keynote/invited talk detected (PHASE 2 FIX #16) "
            "→ STRONGLY suggests R (Professional Presentations - Invited/Keynote), NOT S8 (Publications)."
        )

    if scores['panel_workshop'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains panel/workshop indicators "
            "→ suggests R (Invited Speaking), subsection R3 (Panels/Workshops), NOT R1 (Keynotes)."
        )

    if scores['committee_service'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Committee/service role detected (PHASE 2 FIX #17) "
            "→ suggests P (Committee Service) or Q (Extramural Service), NOT O (Administrative)."
        )

    if scores['mentee_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry has mentee listing pattern (name + degree) "
            "→ suggests N (Mentoring), NOT L (Clinical Practice) even if RN/MD present."
        )

    if scores['teaching_role'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains teaching role indicators "
            "→ suggests K (Educational Contributions), subsection K1 (Didactic Teaching)."
        )

    if scores['committee_role'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains committee role indicators "
            "→ suggests P (Institutional Admin) or Q (Extramural Professional)."
        )

    if scores['award_honor'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains award/honor keywords "
            "→ suggests H (Honors and Awards)."
        )

    if scores['author_first'] >= 0.8 or scores['author_last'] >= 0.8 or scores['author_corresponding'] >= 0.8:
        author_types = []
        if scores['author_first'] >= 0.8:
            author_types.append('first')
        if scores['author_last'] >= 0.8:
            author_types.append('last/senior')
        if scores['author_corresponding'] >= 0.8:
            author_types.append('corresponding')
        triggered_hints.append(
            f"STRUCTURAL HINT: Entry indicates author position ({', '.join(author_types)}) "
            f"→ suggests S (Bibliography), may inform subsection (S1 vs S2)."
        )

    # FIX #3 & #4: Invited talks bias and presentation type hints
    if invited_talks_bias >= 0.8:
        if is_submitted_presentation:
            triggered_hints.append(
                "STRUCTURAL HINT: Section header indicates INVITED TALKS but entry contains poster/abstract indicators "
                "→ verify if this is R (Invited Speaking) or S8 (Submitted Abstract). "
                "Presence of 'poster presentation' or 'abstract #' suggests S8."
            )
        elif scores['keynote_indicators'] >= 0.8 or scores['podium'] >= 0.8:
            triggered_hints.append(
                "STRUCTURAL HINT: Section header indicates INVITED TALKS + keynote/podium/plenary indicators "
                "→ strongly suggests R (Invited Speaking), subsection R1 (Keynotes), NOT S8 (Submitted Abstracts)."
            )
        else:
            triggered_hints.append(
                "STRUCTURAL HINT: Section header indicates INVITED TALKS "
                "→ suggests R (Invited Speaking) over S8 (Submitted Abstracts). "
                "Check for keynote/plenary → R1, grand rounds → R2, panels/workshops → R3."
            )
    elif is_submitted_presentation:
        triggered_hints.append(
            "STRUCTURAL HINT: Entry contains poster/abstract submission indicators "
            "→ suggests S8 (Submitted Abstracts), NOT R (Invited Speaking) unless header indicates invited."
        )

    # PHASE 1 FIXES - Contact detection hints
    if scores['email_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Email address detected "
            "→ strongly suggests A (Personal Information/Contact), NOT D (Employment) or B (Education)."
        )

    if scores['phone_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Phone number detected "
            "→ strongly suggests A (Personal Information/Contact), NOT D (Employment) or B (Education)."
        )

    if scores['fax_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Fax number detected "
            "→ strongly suggests A (Personal Information/Contact), NOT D (Employment) or B (Education)."
        )

    # PHASE 2 FIX #7 - URL/ORCID detection hints
    if scores['url_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: URL/website detected "
            "→ strongly suggests A (Personal Information/Contact), NOT D (Employment) or B (Education)."
        )

    if scores['orcid_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: ORCID identifier detected "
            "→ strongly suggests A (Personal Information/Contact), NOT D (Employment) or B (Education)."
        )

    # PHASE 2 FIX #11 - Grant number detection hints
    if scores['grant_number_patterns'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: NIH/NSF grant number detected (R01, K99, U01, P01, etc.) "
            "→ strongly suggests M (Research Support), NOT H (Awards/Honors) or B (Education)."
        )

    # PHASE 2 FIX #14 - Date-only line hints
    if scores['date_only_line'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Line is purely a date/date range (metadata) "
            "→ Should be classified based on context. May indicate header/section metadata rather than content."
        )

    # PHASE 2 FIX #19 - Conference pattern hints
    if scores['conference_pattern'] >= 0.8:
        triggered_hints.append(
            "STRUCTURAL HINT: Conference/meeting/symposium detected "
            "→ strongly suggests R (Invited Speaking) or S8 (Submitted Abstracts), NOT S1-S6 (Publications)."
        )

    # Build signal summary for tracking
    signal_summary = {}
    if track_effectiveness:
        signal_summary = {
            'total_signals': len(scores),
            'fired_signals': sum(1 for v in scores.values() if v >= 0.8),
            'fired_signal_names': [k for k, v in scores.items() if v >= 0.8],
            'scores': scores
        }

    result = {
        'scores': scores,
        'triggered_hints': triggered_hints
    }

    if track_effectiveness:
        result['signal_summary'] = signal_summary

    return result


def detect_confusion_triggers(
    entry_text: str,
    parent_section_id: str,
    section_header: str = "",
    subsection_header: str = ""
) -> Dict[str, Any]:
    """
    Detect confusion triggers in entry text.

    Returns:
        {
            'triggers': List[str],  # Trigger types detected
            'confusion_risk': str,  # low/medium/high
            'alternative_sections': List[Dict],  # Suggested alternatives
            'should_show_full_examples': bool
        }
    """
    confusion_info = get_confusion_info(parent_section_id)
    if not confusion_info:
        return {
            'triggers': [],
            'confusion_risk': 'low',
            'alternative_sections': [],
            'should_show_full_examples': False
        }

    detected_triggers = []
    text_lower = entry_text.lower()
    header_lower = section_header.lower()
    subheader_lower = subsection_header.lower()

    # Check trigger keywords
    trigger_keywords = confusion_info.get('trigger_keywords', {})
    for trigger_type, keywords in trigger_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            detected_triggers.append(trigger_type)

    # Check section headers for critical context
    for trigger_type, keywords in trigger_keywords.items():
        if any(keyword in header_lower or keyword in subheader_lower for keyword in keywords):
            detected_triggers.append(f'{trigger_type}_in_header')

    # Determine confusion risk
    base_risk = confusion_info.get('confusion_risk', 'low')
    if len(detected_triggers) >= 3:
        risk = 'high'
    elif len(detected_triggers) >= 1 or base_risk == 'high':
        risk = 'high' if base_risk == 'high' else 'medium'
    else:
        risk = base_risk

    # Get alternative sections if triggers detected
    alternative_sections = []
    if detected_triggers:
        for alt in confusion_info.get('alternative_parents', []):
            # Check if any of the alternative's triggers match
            alt_keywords = alt.get('trigger_keywords', [])
            if any(keyword in text_lower for keyword in alt_keywords):
                alternative_sections.append(alt)

    return {
        'triggers': detected_triggers,
        'confusion_risk': risk,
        'alternative_sections': alternative_sections,
        'should_show_full_examples': risk == 'high' or len(detected_triggers) >= 2
    }


def get_disambiguation_guidance(
    parent_section_id: str,
    trigger_types: List[str] = None
) -> List[str]:
    """Get relevant disambiguation guidance based on triggers."""
    confusion_info = get_confusion_info(parent_section_id)
    if not confusion_info:
        return []

    routing_rules = confusion_info.get('routing_rules', {})

    # If specific triggers, return relevant rules
    if trigger_types:
        relevant_rules = []
        for trigger in trigger_types:
            # Find rules mentioning this trigger type
            for rule_key, rule_text in routing_rules.items():
                if trigger.replace('_', ' ') in rule_key.lower() or trigger.replace('_', ' ') in rule_text.lower():
                    relevant_rules.append(f"{rule_key}: {rule_text}")
        return relevant_rules

    # Otherwise return all rules
    return [f"{k}: {v}" for k, v in routing_rules.items()]


def get_subsection_examples(
    parent_section_id: str,
    limit_per_section: int = 2
) -> Dict[str, Dict]:
    """Get subsection examples for a parent section."""
    confusion_info = get_confusion_info(parent_section_id)
    if not confusion_info:
        return {}

    examples = confusion_info.get('subsection_examples', {})

    # Limit examples per subsection
    limited_examples = {}
    for subsection_id, subsection_info in examples.items():
        limited_info = subsection_info.copy()
        if 'examples' in limited_info:
            limited_info['examples'] = limited_info['examples'][:limit_per_section]
        limited_examples[subsection_id] = limited_info

    return limited_examples


# =============================================================================
# BIDIRECTIONAL CONFUSION SYMMETRY VALIDATION
# =============================================================================

def validate_confusion_symmetry() -> List[str]:
    """
    Verify all confusion relationships are bidirectional.

    Returns list of violations (empty list if all valid).

    Principle: If section A can be confused with section B,
    then B can also be confused with A, because confusion arises
    from AMBIGUOUS TERMS that apply to multiple sections.

    Example: "Education" creates confusion between:
      - B (Education Received) ↔ K (Teaching Provided)

    This must be explicitly defined in both directions.
    """
    violations = []

    for section_id, config in SECTION_CONFUSION_MATRIX.items():
        alternatives = config.get('alternative_parents', [])

        for alt in alternatives:
            alt_id = alt['parent_id']

            # Check if the reverse relationship exists
            if alt_id in SECTION_CONFUSION_MATRIX:
                reverse_alts = SECTION_CONFUSION_MATRIX[alt_id].get('alternative_parents', [])
                has_reverse = any(r['parent_id'] == section_id for r in reverse_alts)

                if not has_reverse:
                    violations.append(
                        f"  ❌ {section_id} → {alt_id} exists, but {alt_id} → {section_id} is MISSING"
                    )
            else:
                # Alternative parent not in confusion matrix at all
                violations.append(
                    f"  ⚠️  {section_id} → {alt_id} references non-existent section {alt_id}"
                )

    return violations


def validate_and_report_confusion_symmetry():
    """
    Validate confusion symmetry and print report.
    Raises ValueError if violations found.
    """
    violations = validate_confusion_symmetry()

    if violations:
        report = [
            "",
            "=" * 80,
            "CONFUSION MATRIX BIDIRECTIONAL SYMMETRY VIOLATIONS",
            "=" * 80,
            "",
            "The confusion matrix violates the bidirectional symmetry principle:",
            "If A → B exists, then B → A must also exist.",
            "",
            "Violations found:",
            ""
        ]
        report.extend(violations)
        report.extend([
            "",
            "Fix: Add missing reverse relationships to SECTION_CONFUSION_MATRIX.",
            "See CV_PIPELINE_TARGET_ARCHITECTURE.md Principle #6 for details.",
            "=" * 80,
            ""
        ])

        raise ValueError("\n".join(report))

    return True
