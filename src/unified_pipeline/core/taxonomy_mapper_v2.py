"""
Two-Pass Hierarchical Taxonomy Mapping (v2)

MAJOR IMPROVEMENTS OVER V1:
1. Two-pass classification: Parent (20 sections) → Child (3-9 subsections)
2. Context preservation: Structural info flows from segmentation
3. Sequential awareness: Previous entry informs current classification
4. Intelligent routing: Different strategies for unambiguous vs high-ambiguity sections
5. Token efficiency: 65% reduction through progressive narrowing

Architecture:
  Pass 1: Map to 20 parent sections (A-T)
  Router: Decide if Pass 2 needed based on ambiguity
  Pass 2: Map to specific child section (conditional, with context)

Usage:
    python taxonomy_mapper_v2.py cv_word_segmented.json
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

from unified_pipeline.llm_client import call_llm
from unified_pipeline.config import calculate_cost as _centralized_calculate_cost

# Import candidate surfacing (for TAXONOMY_CODES_CONDENSED enum constraint)
from .candidate_surfacer import TAXONOMY_CODES_CONDENSED

# Import disambiguation validators (Pass 2.5)
from .disambiguation_validators import validate_cv

# Import candidate surfacer for guided classification
from .candidate_surfacer import (
    surface_candidates_for_subsection,
    format_candidates_for_prompt,
    extract_parent_code,
    has_subsections
)

# Import taxonomy contexts and confusion detection
try:
    from .taxonomy_contexts import (
        WCM_SECTION_CONTEXTS,
        PARENT_SECTIONS,
        get_parent_section_config,
        get_section_context
    )
    from .confusion_matrix import (
        get_confusion_info,
        detect_confusion_triggers,
        get_disambiguation_guidance,
        get_subsection_examples,
        compute_structural_hints
    )
    from .s7_validator import validate_s7_assignment
    from .valid_taxonomy_codes import (
        VALID_PARENT_CODES,
        VALID_CHILD_CODES,
        VALID_ALL_CODES,
        validate_taxonomy_code,
        get_valid_children
    )
except ImportError:
    # Running as standalone script
    from taxonomy_contexts import (
        WCM_SECTION_CONTEXTS,
        PARENT_SECTIONS,
        get_parent_section_config,
        get_section_context
    )
    from confusion_matrix import (
        get_confusion_info,
        detect_confusion_triggers,
        get_disambiguation_guidance,
        get_subsection_examples,
        compute_structural_hints
    )
    from s7_validator import validate_s7_assignment
    from valid_taxonomy_codes import (
        VALID_PARENT_CODES,
        VALID_CHILD_CODES,
        VALID_ALL_CODES,
        validate_taxonomy_code,
        get_valid_children
    )

# PHASE 2 FIX #23: Personal Information vs Employment disambiguation guidance
PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE = """
==================================================================================
CRITICAL DISTINCTION: Personal Information (A) vs Employment/Positions (D)
==================================================================================

Personal Information (A) ONLY includes:
- Contact details: email, phone, fax, address
- ORCID, URLs, social media links
- Current title/affiliation (single line at top of CV)
- Name, credentials, professional identifiers

Employment/Positions (D) includes:
- Historical positions with date ranges
- Job descriptions and responsibilities
- Multiple positions over time
- Academic appointments with duties
- Founding/leadership roles in companies/organizations
- Entrepreneurial ventures and startups

RED FLAGS for misclassification:
- If entry has DATE RANGE (e.g., "2015-2020") → D (Employment), NOT A
- If entry is INSTITUTION NAME only → could be A (address) or D (employment), check for dates
- If entry has job responsibilities/duties → D (Employment), NOT A
- If entry contains COMPANY SUFFIX (Inc., LLC, Corp., Ltd., LLP, etc.) → D (Employment), NOT A
- If entry says "Founder of [Company]" → D (Employment), NOT A
- If entry describes business role (CEO, Director, Partner) → D (Employment), NOT A

COMPANY SUFFIX INDICATORS (always Employment, never Contact):
- Inc., Incorporated
- LLC, L.L.C.
- Corp., Corporation
- Ltd., Limited
- LLP, L.L.P.
- Co., Company
- GmbH, AG, S.A., S.A.S. (international)
==================================================================================
"""


# DISAMBIGUATION GUIDANCE: Enhanced with 6 preventable confusion patterns (v2.5)
# Based on validator analysis showing 67% of confusions have unambiguous signals
DISAMBIGUATION_GUIDANCE = """
==================================================================================
DISAMBIGUATION RULES FOR COMMON CONFUSIONS
==================================================================================
CRITICAL: These rules prevent 60-70% of misclassifications. Apply them FIRST.

──────────────────────────────────────────────────────────────────────────────
1. FELLOWSHIP STATUS (H vs I) - STRICT RULES
──────────────────────────────────────────────────────────────────────────────
Check for POST-NOMINAL ABBREVIATIONS - these are ALWAYS I (membership):
  FACP, FAAP, FAAFP, FACOG, FACS, FACR, FACC, FAHA, FASN, FAPA, FASCO, FAAN
  FRCP, FRCS, FRCA, FRCOG, FRCPCH, FRCOphth, FRCR, FRCPath, FRCGP, FRCEM
  FRCPC, FRCSC, FRACP, FRACS, FRACGP, FACEM, FANZCA, FRANZCP, FRANZCO

RULE: If abbreviation present → I (Professional Org), NOT H
Example: "Fellow, American College of Physicians (FACP)" → I

Check for ONGOING MEMBERSHIP language:
  "since YYYY", "YYYY–present", "current fellow", "member since"
RULE: If ongoing range → I (Professional Org), NOT H
Example: "Fellow, Royal College, since 2019" → I

Check for ONE-TIME HONOR language:
  "honorary", "elected", "distinguished" WITHOUT ongoing signals
RULE: If one-time recognition → H (Honor)
Example: "Honorary Fellow, Royal Society, 2020" → H

──────────────────────────────────────────────────────────────────────────────
2. LEADERSHIP ROLES (H vs Q1 vs I) - STRICT RULES
──────────────────────────────────────────────────────────────────────────────
Check for LEADERSHIP TITLES in society context:
  president, vice president, chair, co-chair, secretary, treasurer
  board member, trustee, director, officer, councilor, council member
  executive committee, steering committee, advisory board, editorial board

RULE: Leadership title + society name + date range → Q1 (Leadership), NOT H
Example: "President, American Academy of X, 2017–2018" → Q1

RULE: "Award from Society" → H (Honor)
Example: "Presidential Award from American Academy of X" → H

RULE: Simple membership without leadership duties → I (Professional Org)
Example: "Member, American Academy of X, 2010–present" → I

KEY QUESTION: Does title imply ongoing governance duties or operational work?
  YES → Q1 (Leadership in External Orgs)
  NO → H (Recognition/Award) or I (Membership)

──────────────────────────────────────────────────────────────────────────────
3. SOFTWARE VS PATENTS (S11 vs M2D) - UNAMBIGUOUS MARKERS
──────────────────────────────────────────────────────────────────────────────
Check for SOFTWARE RELEASE markers:
  GitHub, GitLab, Bitbucket, PyPI, CRAN, npm, Bioconductor, Conda
  "open-source", "open source", "code repository", "version X.Y"
  github.com, gitlab.com, bitbucket.com URLs

RULE: GitHub/repository URLs → S11 (Software/Code), NOT M2D
Example: "BPAC tool available at github.com/lab/bpac" → S11

Check for PATENT/IP markers:
  Patent numbers: US1234567, EP1234567, WO1234567, PCT/US
  "patent pending", "provisional patent", "patent filed", "patent application"
  "licensed IP", "licensed to [Company]", "patent no.", "inventor"

RULE: Patent numbers or "licensed IP" → M2D (Patents), NOT S11
Example: "SmartClinic app, US Patent 10,123,456, licensed to HealthTech Inc." → M2D

──────────────────────────────────────────────────────────────────────────────
4. DATASETS VS RESEARCH (S12 vs M1) - UNAMBIGUOUS MARKERS
──────────────────────────────────────────────────────────────────────────────
Check for DATASET PRODUCT markers:
  DOI: 10.XXXX/YYYY
  Accession numbers: GSE12345, SRR12345, PRJ12345, PDB1234, phs001234
  "deposited in", "released", "publicly available", "archived in"
  Repositories: dbGaP, GEO, SRA, GenBank, Figshare, Zenodo, Dryad, Dataverse

RULE: DOI or accession number → S12 (Dataset), NOT M1
Example: "Urban Asthma Dataset, DOI: 10.1234/abcd, deposited in dbGaP" → S12

Check for RESEARCH INFRASTRUCTURE markers:
  "developing", "building", "maintaining", "ongoing enrollment"
  "PI for registry", "coordinating", "establishing", "current project"
  NO DOI, NO accession number

RULE: Ongoing/building language without DOI → M1 (Research Activity), NOT S12
Example: "PI for Urban Asthma Registry, ongoing enrollment since 2015" → M1

──────────────────────────────────────────────────────────────────────────────
5. MENTEE PUBLICATIONS (S vs N) - SECTION HEADER DETECTION
──────────────────────────────────────────────────────────────────────────────
Check SECTION HEADER for mentee language:
  "Publications of Mentees", "Trainee Publications", "Mentee Scholarly Output"
  "Publications by Trainees", "Student Publications", "Advisee Publications"

RULE: Mentee-focused header → N (Mentoring), NOT S
Example: Section titled "Publications of Mentees" → classify as N3 or N4

Check for MENTEE ANNOTATIONS in entries:
  "mentee:", "trainee:", "advisee:", "student:", "(mentee)", "(trainee)"
  "supervised", "mentored", "thesis student:", "postdoc:"
  "now at", "current position:", "currently at" (outcome tracking)

RULE: 2+ entries with mentee annotations → N (Mentoring), NOT S
Example: "Smith J (mentee), ... Now at Harvard Medical School" → N

RULE: Standard "Publications" or "Bibliography" without annotations → S
Example: Section titled "Peer-Reviewed Publications" → classify as S1, S2, etc.

──────────────────────────────────────────────────────────────────────────────
6. TRAINING GRANTS - DUAL DOCUMENTATION (M2 AND N)
──────────────────────────────────────────────────────────────────────────────
Training grants (T32, K12, institutional training programs) serve BOTH purposes.

RULE: Program Director on T32 → Document in BOTH M2 (grant) AND N1 (mentoring)
RULE: Primary mentor on K23/K08 → Document in BOTH M2 (other support) AND N3 (mentee)

NOTE: This is NOT a misclassification. Training grants should appear in BOTH sections:
  M2 - Documents the funding structure
  N - Documents the mentoring relationship

If you see training grant in only ONE section, this is INCOMPLETE documentation,
not a confusion. The entry is correct but additional documentation is warranted.

──────────────────────────────────────────────────────────────────────────────
ADDITIONAL STRICT RULES (Recently Added)
──────────────────────────────────────────────────────────────────────────────

1. HONOR SOCIETY INDUCTIONS (H vs I):
   Check for INDUCTION language:
     "inducted into", "induction into", "honor society", "honorary society",
     "elected to [Honor Society]", "selection to [Honor Society]"

   RULE: Induction to honor society → H (Honor), NOT I
   Example: "Inducted into Delta Omega National Public Health Honor Society, 2014" → H

   RULE: Post-nominal fellowship abbreviations (FACP, FRCP, etc.) → I, NOT H
   Example: "Fellow, American College of Physicians (FACP), since 2019" → I

2. HRSA AND TRAINING FACULTY ROLES (H vs M2/N):
   Check for HRSA/training grant signals:
     "HRSA", "Health Resources and Services Administration",
     "Maternal and Child Health", "MCH", "T32", "T15", "T35", "TL1"
     "Program Faculty Mentor", "Training Grant Faculty", "Program Director",
     "Program Co-Director", "T32 Director", "Faculty Mentor on"

   RULE: HRSA grants or faculty mentor roles → M2 (Funding) and/or N1 (Program Leadership), NOT H
   Example: "MCH Training Grant supported by HRSA, Program Faculty Mentor" → M2 + N1

3. CLINICAL TRIALS VS PUBLICATIONS (M2A/M2B/M2C vs S):
   NOTE: Clinical trials now use M2A/M2B/M2C based on status (like grants):
     - M2A = Active/ongoing clinical trials
     - M2B = Completed clinical trials
     - M2C = Planned/pending clinical trials

   Check for TRIAL CONDUCT markers (→ M2A/M2B/M2C):
     NCT numbers, "Phase I/II/III", "enrollment:", "Site PI",
     "multicenter trial", "protocol", "accrual"
     WITHOUT full journal citation (authors, journal, volume, pages)

   RULE: NCT number without journal citation → M2A/M2B/M2C (based on status), NOT S
   Example: "NCT01234567: Phase III trial of Drug X, Site PI, enrollment: 150" → M2A (if ongoing)

   Check for PUBLICATION markers (→ S):
     Full citation format: authors, journal, year, volume, pages
     "PMID:", "doi:", "published in", "first author", "corresponding author"

   RULE: Full citation + PMID/DOI → S1 (Peer-Reviewed Publications), NOT M2A/M2B/M2C
   Example: "Smith J, et al. N Engl J Med 2024; 390(5):456-467. PMID: 12345678" → S1

   NOTE: Publications ABOUT trial results can appear in BOTH M2A/M2B/M2C and S1

4. BOOKS VS TEACHING MATERIALS (S3/S4 vs K):
   Check for BOOK PUBLICATION markers (→ S3/S4):
     "ISBN:", full publisher name (Springer, Elsevier, Wiley, Oxford, Cambridge, etc.),
     "edition", "doi:", "chapter in:", "pages", "published by"

   RULE: ISBN or major publisher → S3/S4 (Books/Chapters), NOT K
   Example: "Clinical Bioinformatics, 2nd ed. Elsevier, 2022. ISBN: 978-0-12-345" → S3

   Check for TEACHING MATERIAL markers (→ K):
     "course code", "syllabus", "curriculum", "used in [Course]",
     "lecture notes", "course materials", "for students", "class handout"

   RULE: Course codes/syllabi without ISBN → K1 (Educational Contributions), NOT S3/S4
   Example: "Introduction to Genomics syllabus, used in BIOL 5150" → K1

   NOTE: Published textbooks used in teaching can appear in BOTH S3 and K1

==================================================================================
SUPPLEMENTAL RULES (for genuinely ambiguous cases)
==================================================================================

7. CLINICAL TRIALS (L vs M1 vs M2A/M2B/M2C):
   • NCT registration number, "Phase I/II/III", "randomized" → M2A/M2B/M2C (based on status)
   • "Quality improvement", "service evaluation", no publication plan → L (Clinical)
   • "Study", "IRB protocol", "hypothesis", publication plan → M1 (Research)

8. CLINICAL SUPERVISION (L vs N vs K):
   • Clinical FTE/caseload emphasis → L (Clinical Practice)
   • Named mentees with dates/outcomes → N (Mentoring)
   • Formal curriculum/course → K (Teaching)

9. POSTDOC ROLES (C vs B vs D):
   • Residency, Fellowship, Postdoctoral Fellow → C (Postdoc/Fellowship)
   • MD, PhD, MS with graduation year → B (Education)
   • "Assistant Professor", faculty position → D (Positions)
   • Ambiguous "Instructor" roles → Use context (training vs faculty)

──────────────────────────────────────────────────────────────────────────────
10. MIXED CONTENT SECTIONS - PREFER PARENT CATEGORY
──────────────────────────────────────────────────────────────────────────────
⚠️ CRITICAL: If a section contains MULTIPLE types of content at different levels,
classify as the PARENT/BROADER category, NOT a specific child type.

Check for MIXED EDUCATION signals:
  • "EDUCATION" or "EDUCATION AND TRAINING" containing:
    - BA/BS (undergraduate) + MA/MS (master's) + PhD (doctoral) + Postdoc
    - Wide date range (e.g., 1990-2024 suggests multiple career stages)

  RULE: Section with multiple education levels → B (Education) parent, NOT C (Postdoc only)
  CORRECT: "EDUCATION" with BA, PhD, postdoc → B (Education)
  WRONG: "EDUCATION" with BA, PhD, postdoc → C (Postdoctoral Training only)

Check for MIXED POSITION signals:
  • "PROFESSIONAL EXPERIENCE" or "APPOINTMENTS" containing:
    - Faculty positions + administrative roles + external consulting
    - Different institution types (university + hospital + company)

  RULE: Section with diverse position types → D (Positions) parent, NOT D1 (Faculty only)
  CORRECT: "Professional Experience" with faculty + admin → D (Positions)
  WRONG: "Professional Experience" with faculty + admin → D1 (Faculty Appointments only)

Check for MIXED PUBLICATION signals:
  • "PUBLICATIONS" or "BIBLIOGRAPHY" containing:
    - Journal articles + book chapters + abstracts + works in progress

  RULE: Section with diverse publication types → S (Bibliography) parent
  CORRECT: "Publications" with articles + chapters + abstracts → S (Bibliography)
  WRONG: "Publications" with articles + chapters → S1 (Peer-Reviewed Articles only)

INDICATORS OF MIXED CONTENT:
  1. Generic section label ("Education", "Experience", "Publications", "Background")
  2. Wide date range spanning multiple career stages (>10 years)
  3. Sample entries show different formats/types
  4. Section spans first entry to last entry with varying characteristics

WHEN TO USE CHILD vs PARENT:
  • Use CHILD (specific) when: ALL entries are clearly one type (all postdocs, all faculty, all journal articles)
  • Use PARENT (broader) when: Entries span multiple types or section label is generic

WHY THIS MATTERS:
  Pass 2 will properly split mixed content into appropriate child sections.
  If Pass 1 incorrectly assigns to child, Pass 2 cannot expand back to parent.

──────────────────────────────────────────────────────────────────────────────
11. HOSPITAL/CLINICAL APPOINTMENTS - EMPLOYMENT vs TEACHING
──────────────────────────────────────────────────────────────────────────────
⚠️ CRITICAL: "Appointments at Hospitals" usually means EMPLOYMENT (D), not teaching (K).

🔑 KEY DISTINCTION:
  • FACULTY APPOINTMENTS (job titles) → D (Professional Positions)
  • TEACHING ACTIVITIES (courses taught) → K (Educational Contributions)

EMPLOYMENT POSITION INDICATORS (→ D):
  High Reliability Job Titles:
    • "Staff Nurse", "Clinical Nurse", "Per-diem Nurse", "Float Nurse"
    • "Clinical Informatician", "Nurse Informatician", "Bioinformaticist"
    • "Clinical Specialist", "Clinical Coordinator"
    • ANY title with "Staff", "Per-diem", "Full-time", "Part-time" prefix

  High Reliability Department/Unit Names:
    • Operational units: "Information Services Department", "Clinical Informatics"
    • Patient care units: "Critical Care Units", "Coronary Care Unit", "ICU"
    • Technical departments: "IT Department", "Nursing Administration" (as operational)

  High Reliability Language Patterns:
    • Employment terms: "full-time", "part-time", "per-diem", "FTE"
    • Date ranges with specific months: "07/2015 - 09/2020"
    • Institutional departments (not schools): "Department of Medicine", "Partners Healthcare"

TEACHING APPOINTMENT INDICATORS (→ K):
  High Reliability Job Titles:
    • "Clinical Instructor" (ONLY if teaching duties listed)
    • "Nurse Educator", "Clinical Educator", "Clinical Preceptor"
    • ANY title with "Instructor", "Educator", "Lecturer", "Adjunct" (teaching context)

  High Reliability Language Patterns:
    • Curriculum development, course design, teaching responsibilities
    • "Supervised X students", "Clinical teaching", "Didactic instruction"
    • Educational programs, residency training, student mentorship

  High Reliability Department Names:
    • Academic units: "School of Nursing", "College of Medicine"
    • Educational departments: "Medical Education", "Clinical Education Department"

⚠️ FACULTY APPOINTMENT TITLES = POSITIONS (D), NOT TEACHING (K):
  • "Assistant Professor" → D (Professional Position)
  • "Associate Professor" → D (Professional Position)
  • "Clinical Assistant Professor" → D (Professional Position)

  BUT: Courses TAUGHT by those faculty → K (Educational Contributions)

  Example:
    - Section: "Professional Appointments"
      Entry: "Assistant Professor of Nursing, Columbia, 2018-present" → D
    - Section: "Teaching Activities"
      Entry: "Taught Advanced Clinical Practice course, 2019-2024" → K

DECISION ALGORITHM:
  Step 1: Evaluate JOB TITLE first
    - Staff/Clinical/Operational role + No teaching language → D
    - Educator/Instructor/Preceptor + Teaching language → K
    - Faculty title (Professor, Instructor) → D (it's a position)

  Step 2: Check DEPARTMENT/UNIT context
    - Operational/Clinical unit → D
    - School/College/Educational unit → K (if teaching duties)

  Step 3: Check ENTRY LANGUAGE
    - Job responsibilities, patient care, operations → D
    - Curriculum, courses, students, teaching → K

  Step 4: Default for "Appointments at Hospitals"
    - If ambiguous → D (hospital appointments usually = employment)

SECTION LABEL PATTERNS:
  Clear Employment (→ D):
    • "Professional Experience", "Employment History", "Clinical Positions"
    • "Hospital Appointments", "Affiliated Institutions", "Work History"
    • "Professional Appointments", "Clinical Experience"

  Clear Teaching (→ K):
    • "Teaching Appointments", "Educational Activities", "Clinical Teaching"
    • "Teaching Experience", "Courses Taught", "Educational Contributions"

  Ambiguous (→ Check entry-level signals):
    • "Appointments" (could be employment OR teaching)
    • "Academic Appointments" (could be faculty positions OR teaching activities)
    • "Clinical Appointments" (could be practice positions OR clinical teaching)

SPECIAL CASES:
  1. DUAL ROLES (employment + teaching):
     Example: "Assistant Professor of Nursing AND Clinical Nurse Specialist"
     → PRIMARY role is the position (D), teaching activities go in separate K section

  2. NURSING ROLES at teaching hospitals:
     - "Staff Nurse" at MGH → D (employment, even at teaching hospital)
     - "Clinical Preceptor" supervising nursing students → K (teaching role)

  3. INFORMATICIST/BIOINFORMATICIST roles:
     - "Clinical Informatician" in Information Services → D (operational role)
     - "Research Informaticist" in academic lab → M1 (Research Positions)
     - "Informatics Instructor" teaching courses → K (teaching role)

EXAMPLE CLASSIFICATIONS:

✅ CORRECT (D - Employment):
  Section: "Appointments at Hospitals/Affiliated Institutions"
  Entries:
    • "Clinical Informatician, Information Services, NYP, 2018-2024"
    • "Staff Nurse, Critical Care Unit, MGH, 2015-2018"
    • "Per-diem Float Nurse, Scripps Memorial, 2010-2015"
  → All are EMPLOYMENT positions at hospitals (D)

✅ CORRECT (K - Teaching):
  Section: "Clinical Teaching Appointments"
  Entries:
    • "Clinical Preceptor for NP students, Columbia, 2019-present"
    • "Nursing Clinical Instructor, supervising 10 students/semester"
  → All involve TEACHING activities (K)

✅ CORRECT (Mixed - Separate):
  Section: "Professional Appointments"
  Entry: "Assistant Professor of Nursing, Columbia, 2018-present"
  → Classified as D (it's a position/appointment)

  Section: "Teaching Activities"
  Entry: "Taught Advanced Practice Nursing course, 2019-2024"
  → Classified as K (it's a teaching activity)

──────────────────────────────────────────────────────────────────────────────
12. PATENTS (P) VS OTHER PUBLICATIONS (O) - STRICT KEYWORD DETECTION
──────────────────────────────────────────────────────────────────────────────
Check for PATENT MARKERS (→ P - Patents):
  Patent identifiers: "US Patent", "Patent No.", "Patent Application",
  "Provisional Patent", "PCT/", "EP Patent", "WO Patent"
  Patent numbers: US1234567, EP1234567, WO2023/123456
  "inventor", "assignee", "filed", "granted", "pending"
  "licensed to [Company]", "patent portfolio", "IP rights"

RULE: Patent number or "inventor" → P (Patents), NOT O
Example: "Smart Pump System, US Patent 10,234,567, inventor" → P

Check for OTHER PUBLICATION MARKERS (→ O - Other Publications):
  "white paper", "technical report", "policy brief", "position paper"
  "newsletter", "magazine article", "blog post", "commentary"
  "report to [Organization]", "commissioned report", "working paper"
  "preprint", "arXiv", "bioRxiv", "medRxiv", "SSRN"
  "conference proceedings", "poster", "presentation slides"
  NO patent numbers, NO "inventor" language

RULE: Non-traditional publication format without patent markers → O, NOT P
Example: "Policy Brief on Healthcare Reform, published by Think Tank" → O

Check for AMBIGUOUS CASES (software, tools, apps):
  If has patent number → P
  If on GitHub/open-source → S11 (Software)
  If published as white paper/technical report → O
  If described but not documented → M2 (Other Research)

STRICT RULE: When in doubt, check for patent number FIRST
  Patent number present → P (always)
  No patent number + non-traditional format → O
  No patent number + code repository → S11

──────────────────────────────────────────────────────────────────────────────
13. APPENDIX/OTHER (T) - REDUCE CATCH-ALL OVERUSE
──────────────────────────────────────────────────────────────────────────────
CRITICAL: T is NOT a fallback for low-confidence classifications!
T is ONLY for genuinely miscellaneous content that doesn't fit anywhere else.

BEFORE assigning T, check these common misclassifications:

If content describes RESEARCH ACTIVITY:
  ✗ WRONG: T (Appendix)
  ✓ RIGHT: M2 (Other Research), M1 (Completed Research), or M2A/M2B/M2C (Clinical Trials)
  Keywords: "study", "project", "investigation", "analysis", "research"

If content is DOCUMENT METADATA:
  ✗ WRONG: T (Appendix)
  ✓ RIGHT: META_STRUCTURAL (not part of professional content)
  Keywords: "date of CV", "prepared", "updated", "version", "last modified"
  Examples: "CV prepared January 2024", "Last updated: 2024-01-15"

If content is CLINICAL SERVICE:
  ✗ WRONG: T (Appendix)
  ✓ RIGHT: R1 (Clinical Service), L (Clinical Practice), or D2 (Hospital Appointments)
  Keywords: "clinic", "patient care", "clinical duties", "attending", "consultation"

If content is ADMINISTRATIVE:
  ✗ WRONG: T (Appendix)
  ✓ RIGHT: Q1 (Leadership in External Orgs) or R2 (Administrative Service)
  Keywords: "committee", "director", "chair", "coordinator", "leadership"

CONFIDENCE THRESHOLD RULE:
If you're considering T with confidence < 0.85:
  1. Re-read the entry carefully
  2. Look for ANY specific keywords matching M, R, Q, N, K, or L
  3. If found, assign the specific section instead of T
  4. ONLY use T if content is truly miscellaneous (photos, hobbies, languages, etc.)

LEGITIMATE T (Appendix/Other) EXAMPLES:
  ✓ "Languages: English (native), Spanish (fluent), French (conversational)"
  ✓ "Hobbies: Marathon running, classical piano, woodworking"
  ✓ "Personal Statement: [narrative about career philosophy]"
  ✓ "References available upon request"
  ✓ "Photo ID" or "Headshot"

STRICT RULE: If entry has professional relevance, it belongs in a specific section, NOT T

──────────────────────────────────────────────────────────────────────────────
14. GRANT STATUS DETECTION (Q1/Q2/Q3/Q4) - TEMPORAL KEYWORD ANALYSIS
──────────────────────────────────────────────────────────────────────────────
Grant subsections are distinguished by FUNDING SOURCE and TEMPORAL STATUS:

Q1: FEDERAL GRANTS (NIH, NSF, DOD, VA, CDC, AHRQ, etc.)
Q2: NON-FEDERAL GRANTS - CURRENT/ACTIVE
Q3: NON-FEDERAL GRANTS - COMPLETED
Q4: GRANT APPLICATIONS - PENDING/SUBMITTED

STEP 1: Check funding source
  Federal keywords: "NIH", "R01", "R21", "K23", "P01", "U01", "NSF", "DOD",
  "VA Merit", "CDC", "AHRQ", "HRSA Federal", "SAMHSA", "NIDA", "NIMH", "NCI"

  RULE: Federal funding → Q1 (regardless of status), NOT Q2/Q3
  Example: "R01 CA123456, Genomics of Lung Cancer, PI, 2020-2025" → Q1

STEP 2: If non-federal, check temporal status

CURRENT/ACTIVE grants (→ Q2):
  Temporal keywords: "current", "ongoing", "active", "in progress"
  Date patterns: "2023-present", "2024-2027" (future end date)
  Verb tense: Present tense ("This study examines...")
  Status: "funded", "awarded", "approved"

  RULE: Non-federal + active/current → Q2
  Example: "American Heart Association, 2023-2025, PI, $150K, ongoing" → Q2

COMPLETED grants (→ Q3):
  Temporal keywords: "completed", "concluded", "ended", "finished", "past"
  Date patterns: "2018-2020", "2015-2019" (both dates in past)
  Verb tense: Past tense ("This study examined...")
  Status: "final report submitted", "closed"

  RULE: Non-federal + completed → Q3
  Example: "Robert Wood Johnson Foundation, 2018-2020, PI, completed" → Q3

PENDING/SUBMITTED grants (→ Q4):
  Status keywords: "pending", "submitted", "under review", "awaiting decision"
  "application submitted", "proposal", "resubmission", "score: XX"
  Date: Future start date only

  RULE: Non-federal + pending → Q4
  Example: "Doris Duke Foundation, submitted Jan 2024, decision pending" → Q4

AMBIGUOUS DATE HANDLING:
  "2022-2025" in 2025 → If no other signals, assume CURRENT → Q2
  "2018-2021" without explicit status → Assume COMPLETED → Q3
  No dates given → Look for verb tense and status keywords

EDGE CASES:
  Multi-year grants in transition:
    "2020-2024, final year" → Still CURRENT → Q2 (not completed until ended)
    "2020-2024, no-cost extension to 2025" → CURRENT → Q2

  Unfunded applications:
    "Not funded" or "declined" → Q4 (Pending/Applications)
    Some CVs list unsuccessful applications to show grant-seeking experience

STRICT RULE: Check federal vs non-federal FIRST, then check temporal status

──────────────────────────────────────────────────────────────────────────────
15. RESEARCH ACTIVITY TYPE (M1 vs M2A/M2B/M2C/M2D) - PROJECT vs ROLE DISTINCTION
──────────────────────────────────────────────────────────────────────────────
Research sections are distinguished by STRUCTURE and OUTPUTS:

M1: RESEARCH ACTIVITIES (Discrete projects with findings)
  Characteristics:
    - Specific research question or hypothesis stated
    - Start and end dates (completed projects)
    - Findings, results, or outcomes mentioned
    - Often leads to publications (may reference)
    - PI, Co-I, or key personnel role specified
    - Budget/funding amount often included

  Keywords: "completed", "findings showed", "resulted in", "demonstrated",
  "concluded", "we found", "study examined", "investigation of"

  RULE: Discrete project + specific findings → M1
  Example: "Genomic Predictors of Drug Response, 2018-2020, PI, found 3 novel variants" → M1

M2A/M2B/M2C: RESEARCH SUPPORT (Grants AND Clinical Trials based on status)
  M2A = Current/Active funding or ongoing trials
  M2B = Past/Completed funding or completed trials
  M2C = Pending/Submitted funding or planned trials

  Characteristics:
    - Grant funding with PI/Co-I role
    - Clinical trials with NCT numbers
    - Budget/funding amounts
    - Specific start/end dates

  Keywords: "R01", "NIH", "NSF", "funded by", "NCT", "Phase I/II/III"

  RULE: Grant or clinical trial → M2A/M2B/M2C based on status
  Example: "NIH R01, 2020-2025, $1.5M, PI" → M2A (current)
  Example: "NCT03456789, Phase II trial, Site PI, enrollment: 50" → M2A (if ongoing)

M2D: PATENTS & INVENTIONS
  Characteristics:
    - Patent numbers (US, EP, WO, PCT)
    - "patent pending", "provisional patent"
    - "licensed IP", "licensed to [Company]"

  RULE: Patent numbers or licensing → M2D
  Example: "US Patent 10,123,456, licensed to HealthTech Inc." → M2D

DECISION TREE:
  Has patent number or licensing? → M2D (Patents)
  ↓ NO
  Has NCT number or Phase I/II/III or grant number? → M2A/M2B/M2C (based on status)
  ↓ NO
  Has discrete project with findings? → M1 (Research Activities)

AMBIGUOUS CASES:
  "Currently analyzing data from 2020-2022 study":
    → M1 if project phase completed (data collection done)
    → M2 if analysis is ongoing role/service

  "Consultant for Genomics Institute":
    → M2 (ongoing advisory role)

  "Built cohort database, 2018-2021":
    → M1 if discrete project with completion
    → S12 if dataset is published product
    → M2 if database is ongoing infrastructure

STRICT RULE: If in doubt between M1 and M2, ask:
  "Is this a discrete project with findings/outcomes?" → YES = M1, NO = M2

──────────────────────────────────────────────────────────────────────────────
16. POSITION TYPE DISAMBIGUATION (D1 vs D2 vs D3 vs K)
──────────────────────────────────────────────────────────────────────────────
Professional positions are distinguished by PRIMARY INSTITUTION TYPE:

D1: ACADEMIC APPOINTMENTS (University/School faculty positions)
  Institution keywords:
    "University", "College", "School of Medicine", "School of Public Health"
    "Department of", "Division of", "Institute of" (within university)
    Academic titles: "Professor", "Associate Professor", "Assistant Professor"
    "Lecturer", "Instructor", "Research Associate Professor"

  RULE: University/academic institution → D1
  Example: "Associate Professor, Dept of Pediatrics, Harvard Medical School" → D1

D2: HOSPITAL APPOINTMENTS (Clinical institution positions)
  Institution keywords:
    "Hospital", "Medical Center", "Health System", "Clinic", "Healthcare"
    "Medical Group", "Physician Group", "Clinical Practice"
    Clinical titles: "Attending Physician", "Clinical Director", "Medical Director"
    "Chief of Service", "Staff Physician", "Hospitalist"

  RULE: Hospital/clinical institution → D2
  Example: "Attending Physician, Massachusetts General Hospital" → D2

D3: OTHER PROFESSIONAL POSITIONS (Industry, government, non-profit)
  Institution types:
    Pharmaceutical/biotech companies (Pfizer, Genentech, etc.)
    Government agencies (FDA, CDC, State Health Dept)
    Non-profit organizations (foundations, advocacy groups)
    Consulting firms, startups, private companies
    Corporate/industry titles: "Senior Scientist", "Medical Director", "VP R&D"

  RULE: Non-academic, non-hospital institution → D3
  Example: "Senior Medical Director, Pfizer Inc." → D3

K (TEACHING): NOT a position category
  K is for EDUCATIONAL CONTRIBUTIONS (courses taught, curriculum developed)
  Teaching activities should be in K, not D

  RULE: If entry lists courses taught or educational activities → K, NOT D
  Example: "Taught BIOL 5150: Genomics, 2020-2024" → K1, NOT D1

DUAL APPOINTMENTS (Most common confusion):
  Format: "Associate Professor (University) AND Attending Physician (Hospital)"

  CLASSIFICATION RULE for dual appointments:
    If BOTH academic title AND hospital role:
      → Create TWO entries: one D1, one D2
      → OR choose PRIMARY appointment (where salary originates)

    Quick heuristic: Which comes first in the title?
      "Professor of Medicine, Hospital X" → D1 (academic primary)
      "Attending Physician, Hospital X, Assistant Professor, University Y" → D2 (hospital primary)

  Example: "Associate Professor, Dept of Psychiatry, Columbia University Irving Medical Center"
    → D1 (academic title is primary despite "Medical Center" in name)

  Example: "Attending Physician, NewYork-Presbyterian Hospital, Assistant Professor, Columbia"
    → D2 (hospital position is primary, academic is secondary/courtesy)

STRICT RULE: Institution type determines category
  University/School → D1
  Hospital/Clinic → D2
  Industry/Gov/Nonprofit → D3
  Educational activity (not position) → K

──────────────────────────────────────────────────────────────────────────────
17. OTHER PUBLICATIONS (O) - COMPREHENSIVE INCLUSION CRITERIA
──────────────────────────────────────────────────────────────────────────────
O (Other Publications) captures intellectual output NOT in peer-reviewed journals,
books, or abstracts (S1-S7).

INCLUDE in O (Other Publications):
  ✓ White papers, technical reports, policy briefs
  ✓ Government reports, commissioned studies
  ✓ Newsletter articles, magazine articles, blog posts
  ✓ Op-eds, commentaries in popular media
  ✓ Preprints (arXiv, bioRxiv, medRxiv, SSRN, etc.)
  ✓ Working papers, discussion papers
  ✓ Conference proceedings (non-peer-reviewed)
  ✓ Poster presentations, slide decks (archived/published)
  ✓ Webinars, recorded lectures (archived online)
  ✓ Wikipedia articles, online encyclopedias
  ✓ Industry white papers, product guides
  ✓ Grant reports to funders (publicly available)

EXCLUDE from O (assign to correct section):
  ✗ Peer-reviewed journal articles → S1
  ✗ Books, book chapters → S3, S4
  ✗ Published abstracts → S5-S7
  ✗ Patents → M2D (Patents & Inventions)
  ✗ Software/code → S11
  ✗ Datasets → S12
  ✗ Unpublished manuscripts with no archive → M2 (other research)
  ✗ Leadership roles in journals/societies → Q1
  ✗ Teaching materials → K

DISAMBIGUATION from Q1 (Leadership):
  "Editorial in journal" → Is it AUTHORSHIP or EDITORIAL POSITION?
    "Published editorial in JAMA" → O (authored piece)
    "Editor-in-Chief, JAMA" → Q1 (leadership role)

  "Advisory board report" → Who is the audience?
    "Report to Advisory Board on policy" → O (authored report)
    "Member, Advisory Board, Foundation X" → Q1 (leadership position)

DISAMBIGUATION from T (Appendix):
  O is for PUBLICATIONS (documented intellectual output)
  T is for MISCELLANEOUS (non-professional content)

  If it's a document/article/report with authors → O
  If it's personal info, hobbies, or other misc → T

STRICT RULE: If intellectual output is documented in non-traditional format → O
  If it's a role/position → Q1
  If it's a traditional academic publication → S1-S7
  If it's research without output → M1/M2

──────────────────────────────────────────────────────────────────────────────
18. TEACHING (K) VS MENTORING (N) - ACTIVITY TYPE DISTINCTION
──────────────────────────────────────────────────────────────────────────────
K and N both involve education, but serve different purposes:

K: EDUCATIONAL CONTRIBUTIONS (Group teaching, curriculum)
  Characteristics:
    - Classroom teaching, lectures to groups
    - Course development, syllabus creation
    - Grand rounds, workshop facilitation
    - Continuing medical education (CME) programs
    - Clerkship/rotation coordination
    - Educational leadership roles

  Keywords: "course", "lecture", "curriculum", "syllabus", "workshop",
  "CME", "grand rounds", "teaching award", "instructor"

  RULE: Group-based instruction → K1 (Didactic Teaching)
  Example: "BIOL 5150: Genomics, 45 students, 2020-present" → K1

N: ADVISING AND MENTORING (Individual mentorship)
  Characteristics:
    - One-on-one or small group mentorship
    - Thesis/dissertation advising
    - Career mentoring, research mentoring
    - Postdoc supervision, fellow mentoring
    - Named mentees with outcomes
    - Mentee publications, grants, positions tracked

  Keywords: "mentee", "advisee", "thesis advisor", "postdoc mentor",
  "research mentor", "career mentor", "mentoring award"

  RULE: Individual mentorship with named mentees → N3 or N4
  Example: "Jane Smith, PhD student, 2018-2022, now Assistant Professor at MIT" → N3

AMBIGUOUS CASES:
  "Supervising 5 PhD students in lab":
    Context matters:
      → N3 if students are NAMED with individual outcomes tracked
      → K2 if students rotate through as part of course/program

  "Teaching rounds with residents":
    → K2 (Clinical Teaching) if educational rounds for group
    → L (Clinical Practice) if patient care with teaching component
    → N if specific resident mentorship with outcomes

DECISION TREE:
  Are named individuals tracked with outcomes? → N (Mentoring)
  ↓ NO
  Is it course-based or curriculum-based teaching? → K (Teaching)
  ↓ NO
  Is it clinical service with educational component? → L or K2

──────────────────────────────────────────────────────────────────────────────
19. ABSTRACT TYPES (S5/S6/S7) - PRESENTATION STATUS DETECTION
──────────────────────────────────────────────────────────────────────────────
Abstract subsections are based on WHERE/HOW presented:

S5: ABSTRACTS - INVITED (Invited talks, keynotes)
  Keywords: "invited", "keynote", "plenary", "distinguished lecture"
  "named lecture", "honorary", "commencement address"

  RULE: Explicit invitation or honor designation → S5
  Example: "Keynote speaker, American Academy of Pediatrics Annual Meeting" → S5

S6: ABSTRACTS - SUBMITTED (Regular conference abstracts)
  Keywords: "presented at", "oral presentation", "poster presentation"
  "abstract accepted", "conference proceedings"
  NO invitation language, standard conference submission

  RULE: Standard conference presentation → S6
  Example: "Genomic Predictors of Cancer. Poster, ASCO 2023." → S6

S7: ABSTRACTS - OTHER (Local, internal, or published abstracts)
  Types:
    - Published abstracts in journals (not just conference proceedings)
    - Departmental talks, grand rounds abstracts
    - Institutional symposia, local conferences
    - Abstracts in supplement journals

  RULE: Published abstract format or local presentation → S7
  Example: "Abstract published in J Clin Oncol 2023;41(Suppl):1234" → S7

DISAMBIGUATION:
  Full conference proceedings paper → S1 (if peer-reviewed) or O (if not)
  Extended abstract in journal supplement → S7
  Invited grand rounds at external institution → S5
  Regular grand rounds at home institution → K1 (teaching) or S7

STRICT RULE: Check for "invited" or "keynote" → S5, else → S6 (standard) or S7 (local/published)

──────────────────────────────────────────────────────────────────────────────
20. ADDITIONAL DISAMBIGUATION RULES
──────────────────────────────────────────────────────────────────────────────

LEADERSHIP SERVICE LEVELS:
  Q1 (External Leadership): Leadership in professional societies, journals, national orgs
  N1 (Program Leadership): Program director, training program coordinator
  R2 (Administrative Service): Institutional committees, department leadership

  RULE: Check scope - External → Q1, Training → N1, Internal → R2

GRANT vs RESEARCH vs LEADERSHIP:
  M2 (Other Research Support): Grant funding without full details
  Q2/Q3 (Grants): Full grant details with budget, aims, role
  N1 (Program Leadership): Training grant faculty roles

  RULE: Training grant → Both M2 AND N1 (dual classification acceptable)

CLINICAL SERVICE LEVELS:
  L (Clinical Practice): Patient care FTE, clinical workload
  R1 (Clinical Service/Administration): Clinical program development, quality improvement
  D2 (Hospital Appointments): Official clinical positions

  RULE: Direct patient care → L, Program/QI → R1, Position → D2

PUBLICATION vs PRESENTATION:
  S1-S4 (Publications): Peer-reviewed, published, has citation
  S5-S7 (Abstracts): Conference presentations, talks
  O (Other Publications): Non-peer-reviewed written output

  RULE: Check for full citation → S1-S4, Conference → S5-S7, Other written → O

==================================================================================
"""


# S1 vs S7 COMPREHENSIVE DISAMBIGUATION (Based on bibliometrics best practices)
# Added 2025-11-17 from ChatGPT ground truth analysis
# RULE 3: Valid taxonomy codes whitelist (from ChatGPT feedback on CV 2036)
VALID_TAXONOMY_CODES = {
    'A', 'A1', 'A2', 'A3',
    'B', 'B1', 'B2',
    'C', 'C1', 'C2', 'C3',
    'D', 'D1', 'D2',
    'E', 'F', 'G', 'H',
    'I',
    'J',
    'K', 'K1', 'K2', 'K3', 'K4',
    'L',
    'M', 'M1', 'M2', 'M2A', 'M2B', 'M2C', 'M2D',
    'N', 'N1', 'N2', 'N3', 'N4',
    'O', 'P',
    'Q', 'Q1', 'Q2', 'Q3',
    'R', 'R1', 'R2',
    'S', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9',
    'T', 'T4', 'T5'
}

# All valid codes excluding T (used for guided classification where T is handled separately)
# Low-confidence entries auto-route to T, so we exclude it from the enum to prevent explicit selection
ALL_VALID_CODES_EXCLUDING_T = sorted([code for code in VALID_TAXONOMY_CODES if not code.startswith('T')])


def validate_and_correct_taxonomy_code(
    code: str,
    group_data: Dict,
    context: str = ""
) -> tuple:
    """
    Validate taxonomy code and auto-correct common errors.

    This fixes invalid codes like R3, Q4D that were identified in CV 2036 Hoffman review.

    Args:
        code: The taxonomy code to validate
        group_data: Group dictionary with entries, label, etc.
        context: Additional context (e.g., "Pass 1" or "Pass 2")

    Returns:
        (corrected_code, correction_reason)
    """
    if code in VALID_TAXONOMY_CODES:
        return code, ""

    # R3 → R1 or R2 based on context
    if code == 'R3' or (code.startswith('R') and code not in VALID_TAXONOMY_CODES):
        # Check for national/international indicators
        text = ' '.join([e.get('text', '') or e.get('text_snippet', '')
                        for e in group_data.get('entries', [])])
        label = group_data.get('label', '')
        combined = (text + ' ' + label).lower()

        national_indicators = [
            'international', 'national', 'society for',
            'american society', 'joint meeting',
            'annual meeting', 'conference'
        ]

        if any(ind in combined for ind in national_indicators):
            return 'R2', f"Corrected invalid {code} → R2 (national/international presentation detected)"
        return 'R1', f"Corrected invalid {code} → R1 (default for presentations)"

    # Q4D → Q3 or Q2 based on context
    if code == 'Q4D' or (code.startswith('Q') and code not in VALID_TAXONOMY_CODES):
        text = ' '.join([e.get('text', '') or e.get('text_snippet', '')
                        for e in group_data.get('entries', [])])
        combined = text.lower()

        if 'grant' in combined or 'study section' in combined:
            return 'Q2', f"Corrected invalid {code} → Q2 (grant reviewing detected)"
        return 'Q3', f"Corrected invalid {code} → Q3 (editorial/reviewer activity detected)"

    # Unknown invalid code - flag for review. Log at ERROR so this is investigated:
    # a hallucinated code, or a valid code missing from VALID_TAXONOMY_CODES (the sets
    # have drifted -- see #383), silently sends real CV content to the Appendix (#384).
    logger.error(
        "Invalid taxonomy code %r (context=%s, label=%r) -- mapping to T (Appendix). "
        "This indicates a model/prompt regression or a stale VALID_TAXONOMY_CODES.",
        code, context or "n/a", group_data.get('label', ''),
    )
    return 'T', f"INVALID CODE {code} - mapped to T (Other) for manual review"


def is_structural_header(group: Dict) -> bool:
    """
    Detect if group is just a structural header with no real content.

    Based on ChatGPT feedback - Rule 8:
    - Groups 6, 8 in Hoffman CV were headers like "PUBLICATIONS:" with no content
    - Should not be forced into specific subsections

    Patterns:
    - Single entry, all caps or title case
    - No year, author, or specific content
    - Common headers: PUBLICATIONS:, SERVICE:, RESEARCH:

    Args:
        group: Group dictionary with entries, label, etc.

    Returns:
        True if this is a structural header, False otherwise
    """
    entries = group.get('entries', [])

    # Very short group (1-2 entries)
    if len(entries) > 2:
        return False

    # Check if all entries are header-like
    for entry in entries:
        text = entry.get('text', '') or entry.get('text_snippet', '')
        text = text.strip()

        # Skip empty
        if not text:
            continue

        # Header patterns
        if text.endswith(':') and len(text) < 50:
            return True

        # All caps short text
        if text.isupper() and len(text) < 30:
            return True

        # No digits (likely not actual content)
        if not any(c.isdigit() for c in text):
            # Check if it's a common header word
            header_words = [
                'publication', 'research', 'service',
                'teaching', 'award', 'presentation',
                'grant', 'education', 'training',
                'position', 'experience', 'membership',
                'committee', 'honor', 'license'
            ]
            if any(word in text.lower() for word in header_words):
                return True

    return False


# JSON schemas for Structured Outputs (V6: Added enum validation to eliminate 15% error rate)
PASS1_SCHEMA = {
    "type": "object",
    "properties": {
        "parent_section_id": {
            "type": "string",
            "enum": VALID_PARENT_CODES,
            "description": "Top classification (A-T parent section only - must be one of the valid WCM codes)"
        },
        "parent_canonical_name": {"type": "string"},
        "confidence": {
            "type": "number",
            "description": "Confidence in top guess (0.0-1.0)"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of classification decision"
        },
        "alternative_matches": {
            "type": "array",
            "description": "Alternative classifications with probabilities (2-4 alternatives recommended). Must use valid WCM codes only.",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {
                        "type": "string",
                        "enum": VALID_PARENT_CODES,
                        "description": "Parent section ID (A-T) - must be valid WCM code"
                    },
                    "canonical_name": {"type": "string"},
                    "probability": {
                        "type": "number",
                        "description": "Probability of this classification (0.0-1.0)"
                    }
                },
                "required": ["section_id", "canonical_name", "probability"],
                "additionalProperties": False
            }
        }
    },
    "required": ["parent_section_id", "parent_canonical_name", "confidence", "reasoning", "alternative_matches"],
    "additionalProperties": False
}

# Legacy PASS2_SCHEMA for backwards compatibility (no enum validation)
PASS2_SCHEMA = {
    "type": "object",
    "properties": {
        "child_section_id": {
            "type": "string",
            "description": "Top subsection classification (deprecated - use get_pass2_schema() instead)"
        },
        "child_canonical_name": {"type": "string"},
        "confidence": {
            "type": "number",
            "description": "Confidence in top guess (0.0-1.0)"
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation using routing rules and context"
        },
        "triggers_detected": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of confusion triggers found (e.g., abstract_indicator)"
        },
        "alternative_subsections": {
            "type": "array",
            "description": "Alternative subsection classifications with probabilities (2-3 alternatives recommended)",
            "items": {
                "type": "object",
                "properties": {
                    "section_id": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "probability": {
                        "type": "number",
                        "description": "Probability of this classification (0.0-1.0)"
                    }
                },
                "required": ["section_id", "canonical_name", "probability"],
                "additionalProperties": False
            }
        }
    },
    "required": ["child_section_id", "child_canonical_name", "confidence", "reasoning", "triggers_detected", "alternative_subsections"],
    "additionalProperties": False
}

def get_batch_pass2_schema(parent_section_id: str) -> dict:
    """
    Generate batch Pass 2 schema with enum validation for child codes.

    Args:
        parent_section_id: Parent section code (e.g., 'S')

    Returns:
        JSON schema for batch processing with enum-constrained child_section_id

    Raises:
        ValueError: If parent section has no subsections
    """
    valid_children = get_valid_children(parent_section_id)

    if not valid_children:
        raise ValueError(
            f"Cannot create batch Pass 2 schema for {parent_section_id}: "
            f"Section has no subsections. Pass 2 should not be called for this section."
        )

    return {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "description": "Classification for each entry in the batch",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_index": {
                            "type": "integer",
                            "description": "Index of entry in batch (0-based)"
                        },
                        "child_section_id": {
                            "type": "string",
                            "enum": valid_children,
                            "description": f"WCM child section ID for {parent_section_id} - must be one of: {', '.join(valid_children)}"
                        },
                        "child_canonical_name": {
                            "type": "string",
                            "description": "Full name of child section"
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Confidence score 0.0-1.0"
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Brief explanation of classification"
                        },
                        "alternative_subsections": {
                            "type": "array",
                            "description": "Alternative subsection classifications with probabilities (2-3 alternatives recommended)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "section_id": {
                                        "type": "string",
                                        "enum": valid_children,
                                        "description": f"Alternative subsection - must be valid child of {parent_section_id}"
                                    },
                                    "canonical_name": {"type": "string"},
                                    "probability": {
                                        "type": "number",
                                        "description": "Probability of this classification (0.0-1.0)"
                                    }
                                },
                                "required": ["section_id", "canonical_name", "probability"],
                                "additionalProperties": False
                            }
                        }
                    },
                    "required": ["entry_index", "child_section_id", "child_canonical_name", "confidence", "reasoning", "alternative_subsections"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["entries"],
        "additionalProperties": False
    }

# Legacy batch schema (no enum validation)
BATCH_PASS2_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "description": "Classification for each entry in the batch",
            "items": {
                "type": "object",
                "properties": {
                    "entry_index": {
                        "type": "integer",
                        "description": "Index of entry in batch (0-based)"
                    },
                    "child_section_id": {
                        "type": "string",
                        "description": "WCM child section ID (deprecated - use get_batch_pass2_schema() instead)"
                    },
                    "child_canonical_name": {
                        "type": "string",
                        "description": "Full name of child section"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence score 0.0-1.0"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of classification"
                    },
                    "alternative_subsections": {
                        "type": "array",
                        "description": "Alternative subsection classifications with probabilities (2-3 alternatives recommended)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section_id": {"type": "string"},
                                "canonical_name": {"type": "string"},
                                "probability": {
                                    "type": "number",
                                    "description": "Probability of this classification (0.0-1.0)"
                                }
                            },
                            "required": ["section_id", "canonical_name", "probability"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["entry_index", "child_section_id", "child_canonical_name", "confidence", "reasoning", "alternative_subsections"],
                "additionalProperties": False
            }
        }
    },
    "required": ["entries"],
    "additionalProperties": False
}


def is_metadata_header(section_label: str, entries: List[Dict]) -> bool:
    """
    GROUND TRUTH FIX #1: Detect document metadata/headers.

    These are structural elements, not content sections:
    - Document titles: "CURRICULUM VITAE", "CV", "RESUME"
    - Dates only: "October 29th, 2020", "Updated: 2024"
    - Empty sections with Roman numerals: "III. SCHOLARSHIP"
    - Section markers: "Part 1", "Section A"

    Args:
        section_label: The inferred label for this section
        entries: List of entries in the section

    Returns:
        True if this is metadata/header, not content
    """
    label_lower = section_label.lower().strip()

    # Document title headers
    document_titles = [
        'curriculum vitae',
        'cv',
        'resume',
        'résumé',
        'vita',
        'professional profile'
    ]
    if label_lower in document_titles:
        return True

    # Date-only sections (timestamp/date with no substantive content)
    if entries:
        # If single entry that's just a date
        if len(entries) == 1:
            entry_text = entries[0].get('text_snippet', '').strip()
            # Check if it's a date pattern
            date_patterns = [
                r'^\w+ \d{1,2}(?:st|nd|rd|th)?,? \d{4}$',  # October 29th, 2020
                r'^(?:Updated|Revised|As of):?\s+\w+ \d{1,2}?,? \d{4}$',  # Updated: October 2020
                r'^\d{1,2}/\d{1,2}/\d{2,4}$',  # 10/29/2020
                r'^\d{4}-\d{2}-\d{2}$',  # 2020-10-29
            ]
            if any(re.match(pattern, entry_text) for pattern in date_patterns):
                return True

    # Roman numeral section markers (often structural)
    # "III.", "III. SCHOLARSHIP", "Part III"
    if re.match(r'^(?:Part )?[IVX]+\.?\s*(?:\w+)?$', section_label, re.IGNORECASE):
        # Only skip if empty or very short
        if len(entries) == 0:
            return True

    # Generic section markers
    section_markers = [
        r'^part \d+',
        r'^section [a-z0-9]',
        r'^appendix [a-z]?$'
    ]
    if any(re.match(pattern, label_lower) for pattern in section_markers):
        return True

    return False


def build_parent_taxonomy_string() -> str:
    """
    Build compact string of 20 parent sections for Pass 1.

    Much smaller than v1 (800 tokens vs 4800 tokens).
    """
    sections_text = []

    for section in PARENT_SECTIONS:
        parts = [
            f"• {section['code']} - {section['canonical']}",
            f"  WCM Section #{section['wcm_section_number']}",
            f"  Description: {section['description']}"
        ]
        sections_text.append('\n'.join(parts))

    return '\n\n'.join(sections_text)


def classify_pass1_parent(
    section_label: str,
    sample_entries: List[str],
    hierarchical_position: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    PASS 1: Classify to parent section (A-T).

    Args:
        section_label: Section header text
        sample_entries: 3-5 sample entry snippets
        hierarchical_position: Optional position info (level, parent, siblings)

    Returns:
        {
            "parent_section_id": str,
            "parent_canonical_name": str,
            "confidence": float,
            "reasoning": str,
            "alternative_matches": List[Dict],
            "token_usage": Dict
        }
    """
    system_prompt = """You are a CV taxonomy expert performing PASS 1 classification: mapping academic CV sections to 20 parent categories in the Weill Cornell Medicine (WCM) taxonomy.

Your task: Given a section label and sample entries, identify the BEST matching parent section (A-T) OR an appropriate escape hatch.

GUIDELINES:

1. SEMANTIC UNDERSTANDING:
   - Look beyond string similarity - understand actual content
   - "Publications" could be S (Bibliography) or S with N (if mentee theses)
   - "Service" could be O (leadership), P (committee), or Q (extramural)

2. ESCAPE HATCH DECISION TREE (when multiple could apply, use the one that appears FIRST):

   Step 1: Is this pure layout/formatting (page numbers, decorative lines)?
           → YES: NOT_VALID_SECTION

   Step 2: Does this semantically duplicate content from earlier sections?
           → YES: DUPLICATE_REDUNDANT

   Step 3: Is this metadata about the document itself (version, update date)?
           Check for DOCUMENT METADATA keywords:
           • "Date of CV", "CV prepared", "CV updated", "Last modified"
           • "Date prepared", "Prepared on", "Updated:", "Version"
           • "Table of contents", "Index", "Page", purely structural headers
           • "Instructions for", "How to read", document navigation
           → YES: META_STRUCTURAL (not professional content)

   Step 4: Do entries belong to multiple strong standard sections?
           Check ALL three conditions:
           • At least 2 standard sections ≥ 0.30 probability each?
           • Difference between top two < 0.20?
           • Entries clearly assignable to different sections (not just ambiguous)?
           → YES (all three): MIXED_CONTENT

   Step 5: Is this clearly valid CV content but doesn't fit A-T taxonomy?
           Can you describe the section's purpose in one sentence?
           → YES, but no A-T match: UNMAPPED_VALID_SECTION
           → NO, cannot describe: AMBIGUOUS_INSUFFICIENT_INFO

   Step 6: Is this section ONLY an isolated metadata field without context?
           Check: Label matches metadata patterns? Very few entries? Only isolated fragments?
           → YES (all three): FRAGMENT_METADATA

   Step 7: Otherwise → Choose best standard A-T parent section

   ESCAPE HATCH DEFINITIONS:
   • NOT_VALID_SECTION: Pure layout - removing would not change semantic content
   • DUPLICATE_REDUNDANT: Semantically repeats earlier sections
   • META_STRUCTURAL: Document metadata (doc-about-the-doc)
   • MIXED_CONTENT: Multiple competing strong classes (see mechanical rule above)
   • UNMAPPED_VALID_SECTION: Legitimate CV content not in current taxonomy
   • AMBIGUOUS_INSUFFICIENT_INFO: Cannot determine what content is about (last resort)
   • FRAGMENT_METADATA: Isolated metadata field (PI:, Role:, Date:, Amount:) without broader context

3. MULTI-GUESS REQUIREMENT:
   - Provide your TOP classification as parent_section_id (can be A-T or escape hatch)
   - In alternative_matches, provide 2-4 alternative classifications with probabilities
   - Probabilities represent relative likelihood across ALL options
   - Sum of (top probability + all alternative probabilities) should be 0.95-1.05
   - Include escape hatches as alternatives when applicable
   - Example: Top: S (0.65), Alternatives: R (0.25), MIXED_CONTENT (0.10)

4. CONFIDENCE SCORING:
   - 0.95-1.0: Perfect match, unambiguous
   - 0.85-0.94: Strong match, minor ambiguity
   - 0.70-0.84: Reasonable match, some ambiguity
   - 0.50-0.69: Weak match, significant ambiguity
   - Below 0.50: Poor match - strongly consider escape hatches

5. USE SAMPLE ENTRIES:
   - Sample entries provide critical context
   - "Awards" with dollar amounts → H (Honors) or M2 (Grants)
   - "Education" with course titles → K (Teaching) vs B (Degrees)

6. PARENT SECTION FOCUS:
   - This is Pass 1 - classify to broad parent only
   - Detailed subsection mapping happens in Pass 2
   - Example: Don't try to distinguish S1 vs S8 yet, just classify as S

Return structured JSON matching the provided schema."""

    # Build user prompt
    user_prompt = f"""Classify this CV section into WCM parent taxonomy (A-T):

SECTION LABEL: "{section_label}"

SAMPLE ENTRIES (first 3-5 items):
"""
    for i, entry in enumerate(sample_entries[:5], 1):
        # Handle dict entries (extract text_snippet if present)
        if isinstance(entry, dict):
            entry_text = entry.get('text_snippet', '')
        else:
            entry_text = entry

        # Skip empty entries
        if not entry_text:
            continue

        entry_text = entry_text[:300] + "..." if len(entry_text) > 300 else entry_text
        user_prompt += f"{i}. {entry_text}\n"

    # Add hierarchical context if available
    if hierarchical_position:
        user_prompt += f"\nHIERARCHICAL CONTEXT:\n"
        user_prompt += f"- Level: {hierarchical_position.get('level', 'unknown')}\n"
        if hierarchical_position.get('parent_classification'):
            user_prompt += f"- Parent classification: {hierarchical_position['parent_classification']}\n"

    # Compute and add structural hints
    all_hints = []
    for entry in sample_entries[:5]:
        # Handle dict entries (extract text_snippet if present)
        if isinstance(entry, dict):
            entry_text = entry.get('text_snippet', '')
        else:
            entry_text = entry

        # Skip empty entries
        if not entry_text:
            continue

        hints = compute_structural_hints(entry_text, section_label)
        all_hints.extend(hints['triggered_hints'])

    if all_hints:
        # Deduplicate hints
        unique_hints = list(dict.fromkeys(all_hints))
        user_prompt += f"\n{'='*70}\n"
        user_prompt += "STRUCTURAL HINTS (pattern-based signals detected in entries):\n"
        for hint in unique_hints:
            user_prompt += f"• {hint}\n"
        user_prompt += f"{'='*70}\n"

    # Add parent taxonomy
    taxonomy_string = build_parent_taxonomy_string()
    user_prompt += f"""

AVAILABLE WCM PARENT SECTIONS (A-T):

{taxonomy_string}

ESCAPE HATCHES (use when standard taxonomy doesn't fit):
- NOT_VALID_SECTION: Page numbers, headers, footers, TOC entries
- MIXED_CONTENT: Content spans multiple categories with no clear majority
- AMBIGUOUS_INSUFFICIENT_INFO: Too vague/incomplete to classify
- DUPLICATE_REDUNDANT: Duplicates earlier sections
- META_STRUCTURAL: CV metadata, version info
- UNMAPPED_VALID_SECTION: Legitimate CV content not in current taxonomy
- FRAGMENT_METADATA: Isolated metadata field (PI:, Role:, Date:, Amount:) without context

{DISAMBIGUATION_GUIDANCE}

{PERSONAL_INFO_VS_EMPLOYMENT_GUIDANCE}

Based on the section label and sample entries:
1. Identify the BEST matching parent section (A-T) OR escape hatch
2. Provide 2-4 alternative classifications with probabilities
3. Return structured JSON with your classification, confidence, and alternatives"""

    # Log prompt
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "pass1_parent_classification",
            "strict": True,
            "schema": PASS1_SCHEMA
        }
    }

    # Call LLM
    result_llm = call_llm(
        stage="core_taxonomy_v2",
        messages=messages,
        response_format=response_format,
        max_tokens=500,
    )

    # Parse result
    result = json.loads(result_llm["content"])

    # Add token usage
    result['token_usage'] = {
        'prompt_tokens': result_llm["prompt_tokens"],
        'completion_tokens': result_llm["completion_tokens"],
        'total_tokens': result_llm["total_tokens"]
    }

    return result


def apply_grant_keyword_fallback(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    PHASE 1 FIX #4: Apply keyword-based fallback for grant/funding sections when LLM confidence is low.

    If Pass 1 confidence < 0.7 and grant-related keywords are detected with supporting evidence
    (grant numbers or dollar amounts), override classification to M (Research Support).

    Args:
        pass1_result: Original Pass 1 classification result
        section_label: Section header text
        sample_entries: Sample entry texts

    Returns:
        Modified pass1_result (or unchanged if fallback doesn't apply)
    """
    # Only apply fallback if confidence is low
    if pass1_result['confidence'] >= 0.7:
        return pass1_result

    # Combine section label and sample entries for analysis
    combined_text = section_label.lower()
    for entry in sample_entries[:5]:
        # Handle dict entries (extract text_snippet if present)
        if isinstance(entry, dict):
            entry_text = entry.get('text_snippet', '')
        else:
            entry_text = entry
        combined_text += " " + entry_text.lower()

    # Grant-related keywords
    grant_keywords = [
        'grant', 'grants', 'fellowship', 'fellowships', 'funding', 'funded',
        'award', 'awards', 'nih', 'nsf', 'nci', 'niaid', 'nida', 'nhlbi',
        'r01', 'r21', 'r03', 'k99', 'k23', 'k08', 'p01', 'p50', 'u01',
        'principal investigator', 'co-investigator', 'pi:', 'co-i:', 'co-pi:',
        'sponsor', 'sponsored'
    ]

    # Check for grant keywords
    has_grant_keyword = any(keyword in combined_text for keyword in grant_keywords)

    # Look for supporting evidence
    # Grant numbers: R01-HL123456, K99AG067890, etc.
    has_grant_number = bool(re.search(r'\b[A-Z]{1,3}[-\s]?\d{2,3}[-\s]?[A-Z]{2}\d{5,7}\b', combined_text, re.IGNORECASE))

    # Dollar amounts: $500,000 or $500K
    has_dollar_amount = bool(re.search(r'\$[\d,]+(?:K|k|M|m)?\b', combined_text))

    # Role indicators: "PI:", "Co-I:", etc.
    has_role_indicator = bool(re.search(r'\b(?:PI|Co-I|Co-PI|Principal Investigator|Co-Investigator)\s*:', combined_text, re.IGNORECASE))

    # Apply fallback if grant keyword + any supporting evidence
    if has_grant_keyword and (has_grant_number or has_dollar_amount or has_role_indicator):
        # Find parent section M (Research Support) details
        research_parent = None
        for section in PARENT_SECTIONS:
            if section['code'] == 'M':
                research_parent = section
                break

        if research_parent:
            # Override to Research Support with boosted confidence
            original_classification = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"

            pass1_result['parent_section_id'] = 'M'
            pass1_result['parent_canonical_name'] = research_parent['canonical']
            pass1_result['confidence'] = 0.80  # Boost confidence to solid level
            pass1_result['reasoning'] += f" [OVERRIDE by PHASE 1 FIX #4: Grant keyword fallback - detected grant keywords with supporting evidence (grant_number={has_grant_number}, dollar_amount={has_dollar_amount}, role_indicator={has_role_indicator}). Original: {original_classification}]"

    return pass1_result


# FIX #312 --------------------------------------------------------------------
# Cached: normalized canonical WCM section title -> (parent_code, canonical).
# Built once from the WCM taxonomy; top-level sections only.
_CANONICAL_HEADER_TO_PARENT: Optional[Dict[str, tuple]] = None


def _normalize_header(text: str) -> str:
    """Lowercase, strip a leading code prefix ('O. '/'R) '), collapse separators."""
    t = (text or '').strip().lower()
    t = re.sub(r'^[a-z][0-9]?[.)]\s+', '', t)       # 'o. ' / 'r) ' style prefixes
    t = re.sub(r'[\s/&,]+', ' ', t).strip()          # normalize spaces & separators
    return t


def _canonical_header_map() -> Dict[str, tuple]:
    # Source: PARENT_SECTIONS — the SAME parent list PASS-1 classifies into, so
    # canonical<->code alignment matches what the classifier/renderer emit.
    # (Do NOT use CV_SECTIONS: its canonical field swaps H/Honors vs I/Orgs
    # relative to runtime codes, which would misroute those sections.)
    global _CANONICAL_HEADER_TO_PARENT
    if _CANONICAL_HEADER_TO_PARENT is None:
        m: Dict[str, tuple] = {}
        for s in PARENT_SECTIONS:
            code, canonical = s.get('code'), s.get('canonical')
            if code and canonical:
                m[_normalize_header(canonical)] = (code, canonical)
        _CANONICAL_HEADER_TO_PARENT = m
    return _CANONICAL_HEADER_TO_PARENT


def apply_canonical_header_pin(
    pass1_result: Dict[str, Any],
    section_label: str
) -> Dict[str, Any]:
    """
    FIX #312: When the section header is the *verbatim* canonical WCM section
    title, trust the header over content.

    PASS-1 otherwise content-classifies topically homogeneous CVs into the
    dominant-content parent. On C0ZGFW (an all-POCUS CV) it routed
    "INSTITUTIONAL LEADERSHIP ACTIVITIES" -> K (Educational Contributions) and
    emptied section O. The author's explicit, canonical section header is a
    stronger signal than entry semantics.

    ponytail: canonical-exact match only, NOT the alias list — aliases like
    "seminars"/"talks"/"presentations" legitimately collide with teaching (K).
    Orphaned sub-labels (Regional/National) need the hierarchy-nesting fix
    (issue #312 Part B), not this pin.
    """
    pinned = _canonical_header_map().get(_normalize_header(section_label))
    if not pinned:
        return pass1_result
    pinned_code, pinned_canonical = pinned
    current = pass1_result.get('parent_section_id')
    # Rescue only a real A-S misclassification:
    #  - leave escape hatches (NOT_VALID_SECTION, MIXED_CONTENT, ...) alone —
    #    they all contain '_'; no parent code does;
    #  - leave 'T' (Appendix/Other) alone — a T group is boilerplate/unmapped
    #    (WCM template instruction text), and forcing it into a real section
    #    would surface that boilerplate as content;
    #  - no-op when it already matches the header.
    if not current or '_' in current or current == 'T' or current == pinned_code:
        return pass1_result
    original = f"{current} ({pass1_result.get('parent_canonical_name')})"
    pass1_result['parent_section_id'] = pinned_code
    pass1_result['parent_canonical_name'] = pinned_canonical
    pass1_result['confidence'] = max(pass1_result.get('confidence', 0.0) or 0.0, 0.90)
    pass1_result['reasoning'] = (pass1_result.get('reasoning', '') +
        f" [OVERRIDE #312: header is the canonical WCM title for {pinned_code}; "
        f"header trumps content. Original: {original}]")
    return pass1_result


def _canonical_code_to_name() -> Dict[str, str]:
    """parent code -> canonical name (for labelling overrides)."""
    return {code: canon for (code, canon) in _canonical_header_map().values()}


_PARENT_RECOGNITION_MAP: Optional[Dict[str, str]] = None


def _parent_recognition_map() -> Dict[str, str]:
    """
    Normalized WCM top-level section header (canonical OR multi-word alias) ->
    parent code. Used to identify the current parent while walking groups in
    document order, so orphaned sub-labels can inherit it.

    Broader than the canonical-only pin map because real CVs use alias forms
    ("INVITED PRESENTATIONS" for R). Single-word aliases are dropped — they are
    ambiguous ("presentations"/"talks"/"seminars" also read as teaching).
    Aliases are mapped via canonical NAME to the authoritative PARENT_SECTIONS
    code, so the CV_SECTIONS H(Honors)/I(Orgs) swap cannot leak in.
    """
    global _PARENT_RECOGNITION_MAP
    if _PARENT_RECOGNITION_MAP is None:
        canon = _canonical_header_map()                       # {norm: (code, canonical)}
        m: Dict[str, str] = {k: v[0] for k, v in canon.items()}
        name_to_code = {_normalize_header(v[1]): v[0] for v in canon.values()}
        try:
            from ..cv_parser.cv_taxonomy_wcm import CV_SECTIONS
        except Exception:
            CV_SECTIONS = []
        ambiguous = set()
        for s in CV_SECTIONS:
            if s.get('parent_section_code'):                  # top-level sections only
                continue
            code = name_to_code.get(_normalize_header(s.get('canonical', '')))
            if not code:
                continue
            for alias in s.get('aliases', []):
                na = _normalize_header(alias)
                if len(na.split()) < 2:                       # drop ambiguous single words
                    continue
                if na in m and m[na] != code:
                    ambiguous.add(na)
                else:
                    m.setdefault(na, code)
        for na in ambiguous:
            m.pop(na, None)
        _PARENT_RECOGNITION_MAP = m
    return _PARENT_RECOGNITION_MAP


# Parents that use bare geographic scope sub-labels (Regional/National/
# International) as sub-sections. Both have geographic children; the bare label
# is ambiguous between them, so the resolving signal is the *parent*.
_GEO_SUBLABELS = {'regional', 'national', 'international'}
_GEO_PARENTS = {'R', 'Q'}


def apply_geographic_sublabel_pin(
    pass1_result: Dict[str, Any],
    section_label: str,
    effective_parent: Optional[str]
) -> Dict[str, Any]:
    """
    FIX #312 Part B: resolve a bare geographic sub-label (Regional / National /
    International) to its document-order parent when that parent has geographic
    children (R Invitations, Q Extramural committees).

    The label alone is ambiguous — 'national' is a child of BOTH R (National
    Invitations) and Q (National Boards/Committees). Segmentation flattens the
    hierarchy, so PASS-1 sees the bare label + content and can misroute (on
    C0ZGFW it sent the National invited-presentations table to K). The
    disambiguator is the canonical parent section that precedes it in document
    order (threaded in as effective_parent).

    ponytail: geographic-labels-only, parent-constrained to R/Q; same guards as
    the canonical pin (leave escape hatches and 'T' boilerplate alone).
    """
    if effective_parent not in _GEO_PARENTS:
        return pass1_result
    if _normalize_header(section_label) not in _GEO_SUBLABELS:
        return pass1_result
    current = pass1_result.get('parent_section_id')
    if not current or '_' in current or current == 'T' or current == effective_parent:
        return pass1_result
    original = f"{current} ({pass1_result.get('parent_canonical_name')})"
    pass1_result['parent_section_id'] = effective_parent
    pass1_result['parent_canonical_name'] = _canonical_code_to_name().get(effective_parent, effective_parent)
    pass1_result['confidence'] = max(pass1_result.get('confidence', 0.0) or 0.0, 0.90)
    pass1_result['reasoning'] = (pass1_result.get('reasoning', '') +
        f" [OVERRIDE #312B: geographic sub-label under document-order parent "
        f"{effective_parent}; parent disambiguates. Original: {original}]")
    return pass1_result
# END FIX #312 ----------------------------------------------------------------


def apply_signal_overrides(
    pass1_result: Dict[str, Any],
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    PHASE 2 FIX #18: Apply signal-to-taxonomy overrides.

    If confidence < 0.7 and strong signals present, override classification.

    Args:
        pass1_result: Original Pass 1 classification result
        sample_entries: Sample entry texts

    Returns:
        Modified pass1_result (or unchanged if no override)
    """
    # Only apply overrides if confidence is low
    if pass1_result['confidence'] >= 0.7:
        return pass1_result

    # Import signal functions from confusion_matrix
    try:
        from confusion_matrix import (
            _score_doi_pattern,
            _score_pmid_pattern,
            _score_grant_amount,
            _score_grant_number_patterns,
            _score_keynote_indicators,
            _score_committee_service
        )
    except ImportError:
        # If import fails, skip override
        return pass1_result

    # Aggregate signal scores from sample entries
    signal_scores = {
        'doi': 0.0,
        'pmid': 0.0,
        'grant_amount': 0.0,
        'grant_patterns': 0.0,
        'keynote_indicators': 0.0,
        'committee_service': 0.0
    }

    # Compute max score across all sample entries for each signal
    for entry in sample_entries[:5]:
        # Handle dict entries (extract text_snippet if present)
        if isinstance(entry, dict):
            entry_text = entry.get('text_snippet', '')
        else:
            entry_text = entry

        # Skip empty entries
        if not entry_text:
            continue

        signal_scores['doi'] = max(signal_scores['doi'], _score_doi_pattern(entry_text))
        signal_scores['pmid'] = max(signal_scores['pmid'], _score_pmid_pattern(entry_text))
        signal_scores['grant_amount'] = max(signal_scores['grant_amount'], _score_grant_amount(entry_text))
        signal_scores['grant_patterns'] = max(signal_scores['grant_patterns'], _score_grant_number_patterns(entry_text))
        signal_scores['keynote_indicators'] = max(signal_scores['keynote_indicators'], _score_keynote_indicators(entry_text))
        signal_scores['committee_service'] = max(signal_scores['committee_service'], _score_committee_service(entry_text))

    # Override rules based on signal patterns (PHASE 2 FIX #18)
    overrides = []

    # DOI → Publications (S)
    if signal_scores.get('doi', 0) >= 0.6:
        overrides.append(('S', 'Bibliography (Publications)', 0.85, 'DOI pattern'))

    # PMID → Publications (S)
    if signal_scores.get('pmid', 0) >= 0.6:
        overrides.append(('S', 'Bibliography (Publications)', 0.85, 'PMID pattern'))

    # Grant amount → Research Support (M)
    if signal_scores.get('grant_amount', 0) >= 0.6:
        overrides.append(('M', 'Research Support', 0.80, 'Grant amount'))

    # Grant patterns → Research Support (M)
    if signal_scores.get('grant_patterns', 0) >= 0.8:
        overrides.append(('M', 'Research Support', 0.80, 'Grant pattern'))

    # Keynote indicators → Presentations (R)
    if signal_scores.get('keynote_indicators', 0) >= 0.8:
        overrides.append(('R', 'Professional Presentations', 0.80, 'Keynote indicator'))

    # Committee service → Committee Service (P)
    if signal_scores.get('committee_service', 0) >= 0.8:
        overrides.append(('P', 'Committee Service', 0.75, 'Committee pattern'))

    # Apply highest-confidence override
    if overrides:
        overrides.sort(key=lambda x: x[2], reverse=True)  # Sort by confidence
        section_id, section_name, confidence, reason = overrides[0]

        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"
        pass1_result['parent_section_id'] = section_id
        pass1_result['parent_canonical_name'] = section_name
        pass1_result['confidence'] = confidence
        pass1_result['reasoning'] += f" [OVERRIDE by PHASE 2 FIX #18: {reason} signal. Original: {original}]"

    return pass1_result


def distinguish_research_narrative_vs_output(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    PHASE 2 FIX #22: Distinguish narrative research sections from outputs.

    Research Activities/Interests → E (Education - Research Experience)
    Research Output → S (Bibliography)

    Args:
        pass1_result: Pass 1 classification
        section_label: Section header
        sample_entries: Sample entry texts

    Returns:
        Modified classification if needed
    """
    # Only apply if classified as S (Bibliography)
    if pass1_result['parent_section_id'] != 'S':
        return pass1_result

    section_lower = section_label.lower()

    # Check for narrative indicators in header
    narrative_keywords = ['interest', 'activities', 'experience', 'focus', 'area']
    has_narrative_header = any(kw in section_lower for kw in narrative_keywords)

    if not has_narrative_header:
        return pass1_result  # Likely legitimate publications

    # Check entry content - publications have citations, narratives don't
    citation_count = 0
    narrative_count = 0

    for entry in sample_entries[:5]:
        # Handle dict entries (extract text_snippet if present)
        if isinstance(entry, dict):
            entry_text = entry.get('text_snippet', '')
        else:
            entry_text = entry
        text_lower = entry_text.lower()

        # Citation indicators
        if any(indicator in text_lower for indicator in ['et al', 'journal of', 'doi:', 'pmid:', 'vol.', 'pp.']):
            citation_count += 1

        # Narrative indicators
        if any(indicator in text_lower for indicator in ['focus on', 'interested in', 'research in', 'study of', 'investigate']):
            narrative_count += 1

    # If mostly narrative, reclassify to E (Education - Research Experience)
    if narrative_count > citation_count:
        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"
        pass1_result['parent_section_id'] = 'E'
        pass1_result['parent_canonical_name'] = 'Education'
        pass1_result['reasoning'] += f" [RECLASSIFIED by PHASE 2 FIX #22: Research narrative vs output distinction. Original: {original}]"

    return pass1_result


def apply_mentorship_override(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    NEW FIX #8: Override Employment (D) → Mentorship (N) when mentorship keywords detected.

    Addresses issue where "Students / Trainees / Mentorship" sections are misclassified
    as Employment instead of Mentorship.

    Args:
        pass1_result: Pass 1 classification
        section_label: Section header
        sample_entries: Sample entry texts

    Returns:
        Modified classification if needed
    """
    # Only apply if classified as D (Employment)
    if pass1_result['parent_section_id'] != 'D':
        return pass1_result

    # Combine label and entries for analysis
    # Handle dict entries (extract text_snippet if present)
    entry_texts = []
    for entry in sample_entries[:5]:
        if isinstance(entry, dict):
            entry_texts.append(entry.get('text_snippet', ''))
        else:
            entry_texts.append(entry)
    combined_text = section_label.lower() + ' ' + ' '.join(entry_texts).lower()

    # Mentorship keywords in header
    mentorship_header_keywords = [
        'trainee', 'trainees', 'mentorship', 'mentoring', 'mentored',
        'student', 'students', 'supervision', 'supervised', 'advisee',
        'fellow', 'fellows', 'resident', 'residents'
    ]

    has_mentorship_header = any(kw in section_label.lower() for kw in mentorship_header_keywords)

    # Mentorship patterns in entries
    mentorship_entry_patterns = [
        r'\b(mentored|supervised|advised|trained)\s+\d+\s+(student|trainee|fellow|resident)',
        r'\b(thesis|dissertation)\s+(advisor|committee|supervision)',
        r'\b(postdoctoral|graduate|undergraduate)\s+(student|trainee|fellow)',
        r'\b(research\s+mentor|clinical\s+mentor|teaching\s+mentor)',
    ]

    has_mentorship_pattern = any(
        re.search(pattern, combined_text, re.IGNORECASE)
        for pattern in mentorship_entry_patterns
    )

    # Override if header contains mentorship keywords OR strong patterns in entries
    if has_mentorship_header or has_mentorship_pattern:
        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"

        # Find parent section N (Mentorship)
        for section in PARENT_SECTIONS:
            if section['code'] == 'N':
                pass1_result['parent_section_id'] = 'N'
                pass1_result['parent_canonical_name'] = section['canonical']
                pass1_result['confidence'] = 0.90
                pass1_result['reasoning'] += f" [OVERRIDE by NEW FIX #8: Mentorship keywords detected (header={has_mentorship_header}, pattern={has_mentorship_pattern}). Original: {original}]"
                break

    return pass1_result


def apply_other_appointments_override(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    NEW FIX #10: Routing rule for "Other Appointments" to distinguish consulting vs educational.

    Addresses issue where "Other Appointments" sections are inconsistently routed to
    K (Educational Contributions) vs Q (Extramural Professional Responsibilities).

    Args:
        pass1_result: Pass 1 classification
        section_label: Section header
        sample_entries: Sample entry texts

    Returns:
        Modified classification if needed
    """
    # Only apply if header suggests "Other Appointments"
    other_appt_keywords = [
        'other appointment', 'other position', 'additional appointment',
        'additional position', 'adjunct', 'affiliate', 'courtesy'
    ]

    if not any(kw in section_label.lower() for kw in other_appt_keywords):
        return pass1_result

    # Combine label and entries for analysis
    # Handle dict entries (extract text_snippet if present)
    entry_texts = []
    for entry in sample_entries[:5]:
        if isinstance(entry, dict):
            entry_texts.append(entry.get('text_snippet', ''))
        else:
            entry_texts.append(entry)
    combined_text = section_label.lower() + ' ' + ' '.join(entry_texts).lower()

    # Consulting/Advisory indicators (→ Q: Extramural Professional Responsibilities)
    consulting_keywords = [
        'consult', 'advisor', 'advisory', 'board member', 'board of directors',
        'scientific advisory', 'editorial board', 'review panel', 'steering committee',
        'external', 'outside', 'industry', 'corporate', 'commercial'
    ]

    consulting_patterns = [
        r'\b(scientific|medical|technical|clinical)\s+advisor',
        r'\b(advisory|steering|scientific)\s+(board|committee|panel)',
        r'\bconsult(ant|ing)\b',
        r'\bexternal\s+(advisor|consultant|reviewer)',
    ]

    # Educational indicators (→ K: Educational Contributions)
    educational_keywords = [
        'teaching', 'instructor', 'lecturer', 'professor', 'faculty',
        'course', 'curriculum', 'preceptor', 'didactic', 'educational',
        'academic', 'university', 'school', 'department'
    ]

    educational_patterns = [
        r'\b(adjunct|visiting|clinical|assistant|associate)\s+(professor|instructor|faculty)',
        r'\b(teach|taught|teaching)\b',
        r'\bcourse\s+(director|coordinator|instructor)',
        r'\beducational\s+(role|position|appointment)',
    ]

    # Count signals
    consulting_count = sum(1 for kw in consulting_keywords if kw in combined_text)
    consulting_count += sum(1 for p in consulting_patterns if re.search(p, combined_text, re.IGNORECASE))

    educational_count = sum(1 for kw in educational_keywords if kw in combined_text)
    educational_count += sum(1 for p in educational_patterns if re.search(p, combined_text, re.IGNORECASE))

    # Apply override based on stronger signal
    if consulting_count > educational_count and consulting_count >= 2:
        # Route to Q (Extramural Professional Responsibilities)
        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"
        for section in PARENT_SECTIONS:
            if section['code'] == 'Q':
                pass1_result['parent_section_id'] = 'Q'
                pass1_result['parent_canonical_name'] = section['canonical']
                pass1_result['confidence'] = 0.85
                pass1_result['reasoning'] += f" [OVERRIDE by NEW FIX #10: Other Appointments → Consulting/Advisory (consulting_signals={consulting_count}, educational_signals={educational_count}). Original: {original}]"
                break

    elif educational_count > consulting_count and educational_count >= 2:
        # Route to K (Educational Contributions)
        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"
        for section in PARENT_SECTIONS:
            if section['code'] == 'K':
                pass1_result['parent_section_id'] = 'K'
                pass1_result['parent_canonical_name'] = section['canonical']
                pass1_result['confidence'] = 0.85
                pass1_result['reasoning'] += f" [OVERRIDE by NEW FIX #10: Other Appointments → Educational (educational_signals={educational_count}, consulting_signals={consulting_count}). Original: {original}]"
                break

    return pass1_result


def apply_company_suffix_override(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    NEW FIX: Override Personal Information (A) → Employment (D) when company suffixes detected.

    Addresses recurring issue where "Founder of Breethe, Inc." is misclassified as
    Contact Information instead of Employment.

    Args:
        pass1_result: Pass 1 classification
        section_label: Section header
        sample_entries: Sample entry texts

    Returns:
        Modified classification if needed
    """
    # Only apply if classified as A (Personal Information)
    if pass1_result['parent_section_id'] != 'A':
        return pass1_result

    # Combine label and entries for analysis
    # Handle dict entries (extract text_snippet if present)
    entry_texts = []
    for entry in sample_entries[:3]:
        if isinstance(entry, dict):
            entry_texts.append(entry.get('text_snippet', ''))
        else:
            entry_texts.append(entry)
    combined_text = section_label + ' ' + ' '.join(entry_texts)

    # Company suffix patterns
    company_patterns = [
        r'\b(Inc|Incorporated)\b\.?',
        r'\b(LLC|L\.L\.C\.)\b',
        r'\b(Corp|Corporation)\b\.?',
        r'\b(Ltd|Limited)\b\.?',
        r'\b(LLP|L\.L\.P\.)\b',
        r'\bCo\.\b',
        r'\b(GmbH|AG|S\.A\.|S\.A\.S\.)\b',  # International
    ]

    # Business role indicators
    role_patterns = [
        r'\b(Founder|Co-Founder|Co-founder)\b',
        r'\b(CEO|Chief Executive Officer)\b',
        r'\b(Director|Managing Director)\b',
        r'\b(Partner|Managing Partner)\b',
        r'\b(President|Vice President)\b',
        r'\b(Owner|Proprietor)\b',
    ]

    # Check for company suffixes
    has_company_suffix = any(re.search(pattern, combined_text, re.IGNORECASE) for pattern in company_patterns)

    # Check for business roles
    has_business_role = any(re.search(pattern, combined_text, re.IGNORECASE) for pattern in role_patterns)

    # Override if both indicators present OR strong role indicator
    if (has_company_suffix and has_business_role) or (has_business_role and 'founder' in combined_text.lower()):
        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"

        # Find parent section D (Employment) details
        for section in PARENT_SECTIONS:
            if section['code'] == 'D':
                pass1_result['parent_section_id'] = 'D'
                pass1_result['parent_canonical_name'] = section['canonical']
                pass1_result['confidence'] = 0.85
                pass1_result['reasoning'] += f" [OVERRIDE by NEW FIX: Company suffix detected (suffix={has_company_suffix}, role={has_business_role}). Original: {original}]"
                break

    return pass1_result


def classify_pass2_batch(
    parent_section_id: str,
    parent_confidence: float,
    section_header: str,
    subsection_header: str,
    entries: List[str],
    model: str = "gpt-5.1",
    max_batch_size: int = 10,
    entry_objects: List[Dict] = None
) -> Dict[str, Any]:
    """
    PASS 2 BATCH: Classify multiple entries together with hierarchical context.

    Implements Option C adaptive routing:
    - If Pass 1 confidence ≥ 0.90: Show only parent's children (binding)
    - If Pass 1 confidence < 0.90: Show parent's children + alternatives (escape hatch)

    Args:
        parent_section_id: Parent section from Pass 1
        parent_confidence: Confidence from Pass 1 (for adaptive routing)
        section_header: Top-level section header from CV
        subsection_header: Immediate parent header from CV
        entries: List of entry texts to classify together
        model: Model to use
        max_batch_size: Maximum entries per batch

    Returns:
        {
            'success': bool,
            'classifications': List[Dict],
            'avg_confidence': float,
            'token_usage': Dict,
            'elapsed_time': float,
            'error': Optional[str]
        }
    """
    # Get confusion info from comprehensive matrix
    confusion_info = get_confusion_info(parent_section_id)

    # Check if this section has subsections in the taxonomy
    section_context = get_section_context(parent_section_id)
    has_subsections = bool(section_context and section_context.get('related_sections', {}))

    # CRITICAL: If section has subsections in taxonomy, ALWAYS run Pass 2
    # Sections with subsections: A, B, C, D, H, I, K, M, N, Q, R, S, T
    # Only E, F, G, O, P have no subsections (Pass 1 sufficient)
    if not confusion_info and not has_subsections:
        return {
            'success': False,
            'classifications': [],
            'avg_confidence': 0.0,
            'token_usage': {},
            'elapsed_time': 0.0,
            'error': f'No confusion matrix or subsections for section {parent_section_id} (Pass 1 sufficient)'
        }

    # Detect confusion triggers from first entry + headers
    sample_text = entries[0] if entries else ""
    # Ensure sample_text is a string (handle case where dict might be passed)
    if isinstance(sample_text, dict):
        sample_text = sample_text.get('text_snippet', '')
    trigger_info = detect_confusion_triggers(
        entry_text=sample_text,
        parent_section_id=parent_section_id,
        section_header=section_header,
        subsection_header=subsection_header
    )

    # PRE-VALIDATION: Get guidance from validators (all sections)
    try:
        from .validators import analyze_entries_for_guidance
        # V6: Pass entry objects (dicts with text_snippet, etc.) if available, else fall back to text strings
        # Note: entry_type removed in V6 - validators use semantic content detection
        entries_for_validation = entry_objects if entry_objects else entries
        validation_guidance = analyze_entries_for_guidance(
            parent_section_id=parent_section_id,
            entries=entries_for_validation,
            section_label=section_header  # Pass section label for context-aware validation
        )
        # DEBUG: Log if guidance was provided
        if validation_guidance and validation_guidance.has_guidance():
            print(f"      [VALIDATOR] {', '.join(validation_guidance.validators_applied)} → {len(validation_guidance.hints)} hints, confidence={validation_guidance.confidence_in_guidance:.2f}")
    except ImportError:
        # Validators not available - proceed without guidance
        validation_guidance = None

    # For sections with subsections but no confusion matrix, use section context
    if has_subsections and not confusion_info:
        if not section_context:
            return {
                'success': False,
                'classifications': [],
                'avg_confidence': 0.0,
                'token_usage': {},
                'elapsed_time': 0.0,
                'error': f'Cannot find section context for {parent_section_id}'
            }
        # Create minimal confusion_info structure from section context
        confusion_info = {
            'children': section_context.get('related_sections', {}),
            'confusion_risk': section_context.get('confusion_risk', 'medium')
        }

    # Adaptive routing based on Pass 1 confidence (Option C)
    show_alternatives = parent_confidence < 0.90

    # Build system prompt
    system_prompt = f"""You are a CV taxonomy expert performing PASS 2 classification: mapping entries to specific subsections within the WCM taxonomy.

PARENT SECTION: {confusion_info.get('canonical_name', 'Unknown')}
- Description: {confusion_info.get('description', 'N/A')}
- Confusion Risk: {confusion_info.get('confusion_risk', 'low')}

HIERARCHICAL CONTEXT (from CV structure):
  Section: {section_header}
  Subsection: {subsection_header if subsection_header else '(none specified)'}

CLASSIFICATION LOGIC: BALANCED CONSISTENCY
- You MUST classify each entry individually, but NOT in isolation.
- Use a **balanced decision hierarchy**:
    1) **Per-entry classification (primary)**
       • Start by evaluating ONLY the content of the single entry.
       • Assign the most specific subsection code that fits this entry alone.

    2) **Section-level influence (moderate)**
       • The parent section reflects a constrained domain (e.g., Publications).
       • This SHOULD increase the likelihood of publication-related subsections and decrease likelihood of unrelated categories.
       • Section headers SHOULD influence the *type* of classification expected, but NOT force the same subsection across all entries.
       • Section-level cues help determine *which family of subsections* is most probable, but NOT which specific code must be applied.

    3) **Batch consistency (selective, evidence-based)**
       • If multiple entries clearly share the same type, purpose, and structural pattern (e.g., multiple FACE fatality reports), they SHOULD receive the same code.
       • Favor consistency ONLY when similarity is explicit and unambiguous.

    4) **Differentiation (required)**
       • If an entry differs in type, venue, purpose, or format (e.g., a technical report vs. case investigation), it can receive a different subsection when warranted.
       • Dominant patterns in the batch MUST NOT override meaningful differences in an individual entry.

- IMPORTANT: It is NOT expected or desirable for all entries in a batch to receive the same subsection code.
- Consistency must always be **evidence-driven**, not assumed.

Your task: Classify each entry to the MOST SPECIFIC subsection, using hierarchical context, routing rules, and examples."""

    # Add core principles if available
    if 'core_principles' in confusion_info:
        system_prompt += "\n\nCORE PRINCIPLES:\n"
        for principle in confusion_info['core_principles']:
            system_prompt += f"- {principle}\n"

    # Add decision order if available
    if 'decision_order' in confusion_info:
        system_prompt += "\nDECISION ORDER:\n"
        for step in confusion_info['decision_order']:
            system_prompt += f"{step}\n"

    # Build user prompt
    user_prompt = f"Classify these entries from the CV section:\n\n"

    # Add entries (limit preview to 300 chars each)
    for i, entry in enumerate(entries[:max_batch_size]):
        preview = entry[:300] + "..." if len(entry) > 300 else entry
        user_prompt += f"[{i}] {preview}\n\n"

    # Add routing rules
    if 'routing_rules' in confusion_info:
        user_prompt += "\nROUTING RULES:\n"
        for rule_key, rule_text in confusion_info['routing_rules'].items():
            user_prompt += f"- {rule_text}\n"
        user_prompt += "\n"

    # Add disambiguation guidance if triggers detected
    if trigger_info['triggers']:
        user_prompt += f"\n**TRIGGERS DETECTED**: {', '.join(trigger_info['triggers'])}\n\n"
        guidance = get_disambiguation_guidance(parent_section_id, trigger_info['triggers'])
        if guidance:
            user_prompt += "DISAMBIGUATION GUIDANCE:\n"
            for rule in guidance[:5]:  # Limit to top 5 most relevant
                user_prompt += f"- {rule}\n"
            user_prompt += "\n"

    # Add validation guidance (if available)
    if validation_guidance and validation_guidance.has_guidance():
        user_prompt += "\n" + "="*70 + "\n"
        user_prompt += f"VALIDATION GUIDANCE (confidence: {validation_guidance.confidence_in_guidance:.2f})\n"
        user_prompt += "="*70 + "\n\n"

        if validation_guidance.recommended_subsections:
            user_prompt += f"Recommended subsections: {', '.join(validation_guidance.recommended_subsections)}\n"

        if validation_guidance.excluded_subsections:
            user_prompt += f"Excluded subsections: {', '.join(validation_guidance.excluded_subsections)}\n"
            user_prompt += "\nReasons for exclusions:\n"
            for section, reason in validation_guidance.exclusion_reasons.items():
                user_prompt += f"  • {section}: {reason}\n"

        if validation_guidance.hints:
            user_prompt += "\nGuidance hints:\n"
            for hint in validation_guidance.hints:
                user_prompt += f"  • {hint}\n"

        user_prompt += "\nGUIDANCE INTERPRETATION:\n"
        user_prompt += "- You SHOULD choose from recommended subsections based on the evidence\n"
        user_prompt += "- You MAY choose an excluded subsection IF you have strong contradicting evidence\n"
        user_prompt += "- If you override guidance, provide detailed justification in your reasoning\n"
        user_prompt += "- If you override guidance, your confidence should be < 0.70 unless you're very certain\n"
        user_prompt += "\nESCAPE HATCH: Validation guidance is based on pattern matching and may miss\n"
        user_prompt += "context. If you believe guidance is incorrect for a specific entry, explain why.\n\n"

    # PRIMARY OPTIONS: Parent's children with examples
    user_prompt += f"\nPRIMARY OPTIONS ({confusion_info.get('canonical_name', 'Unknown')}):\n\n"

    # Get subsection examples - try from confusion_info first, then from subsection_examples function
    if 'children' in confusion_info and confusion_info['children']:
        # Use children from section context (for sections without confusion matrix)
        subsection_examples = confusion_info['children']
    else:
        # Use subsection_examples from confusion matrix (for sections with confusion matrix)
        subsection_examples = get_subsection_examples(parent_section_id, limit_per_section=2)

    valid_codes = list(subsection_examples.keys())

    for section_id, section_info in subsection_examples.items():
        user_prompt += f"• **{section_id}**: {section_info.get('title', 'Unknown')}\n"
        if 'description' in section_info:
            user_prompt += f"  {section_info['description']}\n"
        if 'examples' in section_info and section_info['examples']:
            for ex in section_info['examples'][:2]:
                user_prompt += f"  Example: {ex}\n"
        user_prompt += "\n"

    # Add explicit code validation warning
    user_prompt += f"\n{'='*70}\n"
    user_prompt += f"⚠️  CRITICAL: You MUST use ONLY these exact section codes:\n"
    user_prompt += f"   Valid codes: {', '.join(valid_codes)}\n"
    user_prompt += f"\n"
    user_prompt += f"   ❌ DO NOT use:\n"
    user_prompt += f"   - Descriptive names (e.g., 'research_interests', 'funded_research')\n"
    user_prompt += f"   - Grant/project numbers (e.g., 'R01CA123456', 'U54 OH007548')\n"
    user_prompt += f"   - Invalid codes (e.g., 'H1' when H1 doesn't exist)\n"
    user_prompt += f"   - Generic terms (e.g., 'unknown', 'other')\n"
    user_prompt += f"\n"
    user_prompt += f"   ✅ ONLY use the exact codes listed above: {', '.join(valid_codes)}\n"
    user_prompt += f"{'='*70}\n\n"

    # ALTERNATIVES: Show if Pass 1 confidence < 0.90 OR high confusion risk
    if show_alternatives or trigger_info['confusion_risk'] == 'high':
        alternative_parents = confusion_info.get('alternative_parents', [])

        if alternative_parents:
            user_prompt += "\n" + "="*60 + "\n"
            user_prompt += "ALTERNATIVE SECTIONS (if primary doesn't fit):\n"
            user_prompt += "="*60 + "\n\n"

            for alt in alternative_parents:
                user_prompt += f"\n{alt['parent_name']}\n"
                user_prompt += f"Reason to consider: {alt['reason']}\n\n"

                # Show ALL children of alternative parent if flagged
                if alt.get('show_all_children', False):
                    alt_parent_id = alt['parent_id']
                    alt_examples = get_subsection_examples(alt_parent_id, limit_per_section=1)

                    for section_id, section_info in alt_examples.items():
                        user_prompt += f"  • {section_id}: {section_info.get('title', 'Unknown')}\n"
                        if 'examples' in section_info and section_info['examples']:
                            user_prompt += f"    Example: {section_info['examples'][0]}\n"

                # Add disambiguation guidance
                if 'disambiguation_guidance' in alt:
                    user_prompt += "\nDisambiguation:\n"
                    for guidance in alt['disambiguation_guidance']:
                        user_prompt += f"  - {guidance}\n"
                user_prompt += "\n"

    user_prompt += "\n" + "="*70 + "\n"
    user_prompt += "CLASSIFICATION REQUIREMENTS:\n"
    user_prompt += "="*70 + "\n"
    user_prompt += "1. Classify EACH entry independently to the MOST SPECIFIC subsection from the valid codes above\n"
    user_prompt += "2. Apply section-level context to guide which types of subsections are most plausible\n"
    user_prompt += "3. Apply consistency ONLY when entries genuinely describe the same type of activity (same structure, same purpose, same type)\n"
    user_prompt += "4. Differences in document type, venue, or structure MUST result in different subsections when appropriate\n"
    user_prompt += f"5. ONLY use these exact codes: {', '.join(valid_codes)}\n"
    user_prompt += "6. If unsure between codes for a single entry, select the most specific fit and list alternatives\n"
    user_prompt += "7. Return structured JSON with one classification object per entry\n"

    # Prepare API call
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # V6: Use dynamic schema with enum validation for child codes
    batch_schema = get_batch_pass2_schema(parent_section_id)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "batch_classification",
            "strict": True,
            "schema": batch_schema
        }
    }

    # Call LLM
    try:
        start_time = time.time()

        result_llm = call_llm(
            stage="core_taxonomy_v2",
            messages=messages,
            response_format=response_format,
            max_tokens=2000,
        )

        elapsed_time = time.time() - start_time

        # Parse result with robust error handling
        raw_content = result_llm["content"]

        # Try to parse JSON directly first
        try:
            result = json.loads(raw_content)
            classifications = result['entries']
        except json.JSONDecodeError as e:
            # Some models may include reasoning text before JSON
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', raw_content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group(0))
                    classifications = result['entries']
                    print(f"      [JSON RECOVERY] Extracted JSON from response with reasoning text")
                except json.JSONDecodeError:
                    return {
                        'success': False,
                        'classifications': [],
                        'avg_confidence': 0.0,
                        'token_usage': {},
                        'elapsed_time': elapsed_time,
                        'error': f'JSON parsing failed: {str(e)}'
                    }
            else:
                return {
                    'success': False,
                    'classifications': [],
                    'avg_confidence': 0.0,
                    'token_usage': {},
                    'elapsed_time': elapsed_time,
                    'error': f'No JSON found in response: {raw_content[:200]}'
                }

        # POST-VALIDATION: Check for violations and apply corrections
        validation_stats = {
            'violations_detected': 0,
            'corrections_applied': 0,
            'overrides_logged': 0
        }

        if validation_guidance:
            try:
                from .validators.validation_checker import check_violations, apply_correction
                from .validators.calibration_logger import (
                    log_classification_event,
                    log_calibration_issue,
                    log_validation_event
                )

                # Check each classification
                for i, classification in enumerate(classifications):
                    entry_text = entries[i] if i < len(entries) else ""

                    # Check violations
                    llm_result = {
                        'entry_id': f"batch_{i}",
                        'section_id': classification['child_section_id'],
                        'confidence': classification['confidence'],
                        'reasoning': classification.get('reasoning', ''),
                        'entry_text': entry_text,
                        'model': model
                    }

                    violations = check_violations(llm_result, validation_guidance)

                    if violations['has_violations']:
                        validation_stats['violations_detected'] += 1

                        # Log violation event
                        log_validation_event(
                            event_type='violation',
                            llm_result=llm_result,
                            guidance=validation_guidance.__dict__,
                            validation_check=violations
                        )

                        # Apply correction if hard rule violated
                        if violations['severity'] == 'hard':
                            if classification['confidence'] >= 0.85:
                                # High confidence but wrong → calibration issue
                                log_calibration_issue(llm_result, violations)

                            # Apply correction
                            corrected = apply_correction(llm_result, validation_guidance)
                            classification['child_section_id'] = corrected['section_id']
                            classification['original_child_section_id'] = llm_result['section_id']
                            classification['validation_override'] = True
                            classification['override_reason'] = corrected.get('override_reason', '')
                            validation_stats['corrections_applied'] += 1

                        elif violations['severity'] == 'soft':
                            # Soft violation - flag but allow
                            classification['guidance_overridden'] = True
                            validation_stats['overrides_logged'] += 1

                    # Log classification for calibration analysis
                    log_classification_event({
                        **llm_result,
                        'section_id': classification['child_section_id'],
                        'guidance_applied': validation_guidance.__dict__,
                        'validation_check': violations,
                        'validation_override': classification.get('validation_override', False)
                    })

            except ImportError:
                # Validators not available - skip post-validation
                pass

        # Calculate average confidence
        avg_confidence = sum(c['confidence'] for c in classifications) / len(classifications) if classifications else 0.0

        return {
            'success': True,
            'classifications': classifications,
            'avg_confidence': avg_confidence,
            'token_usage': {
                'prompt_tokens': result_llm["prompt_tokens"],
                'completion_tokens': result_llm["completion_tokens"],
                'total_tokens': result_llm["total_tokens"],
                'model_used': result_llm["model"]
            },
            'elapsed_time': elapsed_time,
            'error': None,
            'validation_stats': validation_stats
        }

    except Exception as e:
        # ponytail: broad catch is kept for now (returns a failure result the caller
        # handles); Phase 2 of #396 narrows it so unexpected exceptions propagate,
        # behind a corpus gate. Until then, at least make the swallow loud.
        logger.exception("classify_pass2_batch failed -- returning failure result")
        return {
            'success': False,
            'classifications': [],
            'avg_confidence': 0.0,
            'token_usage': {},
            'elapsed_time': 0.0,
            'error': str(e)
        }


def map_cv_sections_v2(segmented_cv_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Map all sections from a segmented CV using two-pass hierarchical classification.

    Args:
        segmented_cv_path: Path to segmented CV JSON
        output_path: Where to save mapped output

    Returns:
        Dictionary with mappings, stats, and output file path
    """
    print("="*80)
    print("TWO-PASS HIERARCHICAL TAXONOMY MAPPING (V2)")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print(f"Strategy: Pass 1 (20 parents) → Router → Pass 2 (3-9 children)")
    print()

    # Load segmented CV
    with open(segmented_cv_path, 'r') as f:
        segmented_cv = json.load(f)

    print("✓ Loaded segmented CV")
    print()

    # Track token usage per model
    token_usage_by_model = defaultdict(lambda: {'prompt': 0, 'completion': 0, 'total': 0, 'calls': 0})
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    pass1_count = 0
    pass2_count = 0
    skipped_headers_count = 0
    s7_corrections_count = 0

    mappings = []
    groups = segmented_cv.get('groups', [])

    print(f"Processing {len(groups)} top-level groups...")
    print()

    def map_group_recursive(group, level=1, parent_path="", hierarchical_context=None, effective_parent=None):
        """
        Recursively map a group and all its subgroups.

        Args:
            group: Group to map
            level: Nesting level
            parent_path: Path string for tracking
            hierarchical_context: Dict with section_header, subsection_header, parent_label
        """
        nonlocal total_prompt_tokens, total_completion_tokens, total_tokens
        nonlocal pass1_count, pass2_count, skipped_headers_count, s7_corrections_count
        nonlocal token_usage_by_model

        # Support both 'label' and 'label_inferred' (different segmenter versions)
        section_label = group.get('label') or group.get('label_inferred', 'Unknown')
        group_id = group.get('id', 'Unknown')
        entries = group.get('entries', [])
        subgroups = group.get('subgroups', [])

        # FIX #312B: subgroups inherit this group's code if it is itself a
        # recognized WCM section, otherwise the effective parent passed in.
        _this_parent = _parent_recognition_map().get(_normalize_header(section_label))
        child_effective_parent = _this_parent if _this_parent else effective_parent

        # Build hierarchical context for this group
        if hierarchical_context is None:
            hierarchical_context = {
                'section_header': section_label,
                'subsection_header': '',
                'parent_label': ''
            }
        else:
            # Update context as we descend
            new_context = hierarchical_context.copy()
            if level == 1:
                new_context['section_header'] = section_label
                new_context['subsection_header'] = ''
            elif level == 2:
                new_context['subsection_header'] = section_label
            new_context['parent_label'] = section_label
            hierarchical_context = new_context

        # GROUND TRUTH FIX #1: Detect document metadata/headers (CURRICULUM VITAE, dates, etc.)
        is_document_metadata = is_metadata_header(section_label, entries)

        # NEW FIX #34: Skip structural container headers (0 entries + has subgroups)
        is_structural_container = (len(entries) == 0 and len(subgroups) > 0)

        # Skip structural headers (marked during preprocessing or detected as containers)
        if group.get('skip_classification', False) or group.get('is_structural_header', False) or is_structural_container or is_document_metadata:
            indent = "  " * level
            skipped_headers_count += 1
            if is_document_metadata:
                skip_reason = "document metadata"
            elif is_structural_container:
                skip_reason = "container"
            else:
                skip_reason = "structural header"
            print(f"{indent}[SKIP] {section_label} ({skip_reason} - {len(entries)} entries, {len(subgroups)} subgroups)")
            # Still process subgroups if any
            for subgroup in subgroups:
                map_group_recursive(subgroup, level + 1, f"{parent_path}/{section_label}" if parent_path else section_label, hierarchical_context, effective_parent=child_effective_parent)
            return

        # Get sample entries - ensure all are strings, not dicts
        sample_entries = []
        for entry in entries[:5]:
            # Handle both dict and string entries
            if isinstance(entry, dict):
                text = entry.get('text_snippet', '')
            elif isinstance(entry, str):
                text = entry
            else:
                text = ''

            if text and isinstance(text, str):
                sample_entries.append(text)

        # Also sample from subgroups if needed
        if len(sample_entries) < 3:
            for subgroup in subgroups:
                for entry in subgroup.get('entries', [])[:3]:
                    # Handle both dict and string entries
                    if isinstance(entry, dict):
                        text = entry.get('text_snippet', '')
                    elif isinstance(entry, str):
                        text = entry
                    else:
                        text = ''

                    if text and isinstance(text, str) and len(sample_entries) < 5:
                        sample_entries.append(text)

        # DEFENSIVE: Final validation - ensure NO dicts in sample_entries
        # This catches any edge cases where dicts might slip through
        validated_sample_entries = []
        for item in sample_entries:
            if isinstance(item, dict):
                # Emergency extraction if a dict somehow got through
                text = item.get('text_snippet', str(item))
                validated_sample_entries.append(text)
            elif isinstance(item, str):
                validated_sample_entries.append(item)
            else:
                # Convert anything else to string
                validated_sample_entries.append(str(item))
        sample_entries = validated_sample_entries

        indent = "  " * level
        path = f"{parent_path}/{section_label}" if parent_path else section_label

        print(f"{indent}[{len(mappings)+1}] {section_label}")
        print(f"{indent}    Entries: {len(entries)}, Subgroups: {len(subgroups)}")

        # RULE 8: Check for structural headers (no real content)
        if is_structural_header(group):
            print(f"{indent}    ⚠️  Structural Header Detected - Skipping detailed classification")
            # Map to T (Other) to avoid forcing into specific subsections
            mapping = {
                "group_id": group.get('group_id', f"gen_{len(mappings)+1}"),
                "label": section_label,
                "section_id": "T",
                "canonical_name": "Other (Structural Header)",
                "confidence": 0.50,
                "pass1_parent": "T",
                "pass1_confidence": 0.50,
                "pass2_child": None,
                "pass2_confidence": None,
                "correction_reason": "Structural header with no substantive content",
                "entries": entries
            }
            mappings.append(mapping)

            # Process subgroups recursively
            if subgroups:
                for subgroup in subgroups:
                    map_group_recursive(
                        group=subgroup,
                        level=level + 1,
                        parent_path=path,
                        hierarchical_context=hierarchical_context.copy(),
                        effective_parent=child_effective_parent
                    )
            return

        # PRE-VALIDATION: Get guidance from validators (Pass 1)
        validation_guidance = None
        try:
            from .validators import analyze_entries_for_guidance
            # V6: Pass full entry objects (dicts) - entry_type removed, validators use semantic detection
            validation_guidance = analyze_entries_for_guidance(
                parent_section_id='*',  # Universal - don't know parent yet
                entries=entries,  # Full entry objects
                section_label=section_label
            )
            # DEBUG: Log if guidance was provided
            if validation_guidance and validation_guidance.has_guidance():
                print(f"{indent}      [VALIDATOR-P1] {', '.join(validation_guidance.validators_applied)} → {len(validation_guidance.hints)} hints, confidence={validation_guidance.confidence_in_guidance:.2f}")
        except ImportError:
            # Validators not available - proceed without guidance
            pass

        # PASS 1: Parent classification
        try:
            # DEBUG: Verify sample_entries are all strings
            for idx, entry in enumerate(sample_entries):
                if not isinstance(entry, str):
                    logger.warning("sample_entries[%d] is %s, not str: %r", idx, type(entry), entry)

            _hier_pos = {"level": level}
            if effective_parent:  # FIX #312B: hand PASS-1 the document-order parent
                _hier_pos["parent_classification"] = effective_parent
            pass1_result = classify_pass1_parent(
                section_label=section_label,
                sample_entries=sample_entries,
                hierarchical_position=_hier_pos
            )

            # PHASE 2 FIX #18: Apply signal-based overrides if low confidence
            pass1_result = apply_signal_overrides(
                pass1_result=pass1_result,
                sample_entries=sample_entries
            )

            # PHASE 2 FIX #22: Distinguish research narrative vs output
            pass1_result = distinguish_research_narrative_vs_output(
                pass1_result=pass1_result,
                section_label=section_label,
                sample_entries=sample_entries
            )

            # NEW FIX #8: Apply mentorship override (D → N for trainee/student sections)
            pass1_result = apply_mentorship_override(
                pass1_result=pass1_result,
                section_label=section_label,
                sample_entries=sample_entries
            )

            # VALIDATOR OVERRIDE: Apply label-content conflict detection
            if validation_guidance and validation_guidance.has_guidance():
                # If validator detected a conflict and recommends a specific parent section
                if validation_guidance.recommended_subsections:
                    original_parent = pass1_result['parent_section_id']
                    recommended_parent = validation_guidance.recommended_subsections[0]
                    # Override if validator has high confidence
                    if validation_guidance.confidence_in_guidance >= 0.80:
                        pass1_result['parent_section_id'] = recommended_parent
                        pass1_result['confidence'] = validation_guidance.confidence_in_guidance
                        pass1_result['reasoning'] = f"VALIDATOR OVERRIDE: {' '.join(validation_guidance.hints)}"
                        print(f"{indent}      [OVERRIDE] Validator: {original_parent} → {recommended_parent} (label-content conflict detected)")

            # NEW FIX #10: Apply Other Appointments routing (K vs Q distinction)
            pass1_result = apply_other_appointments_override(
                pass1_result=pass1_result,
                section_label=section_label,
                sample_entries=sample_entries
            )

            # NEW FIX: Apply company suffix override (A → D for company founders/roles)
            pass1_result = apply_company_suffix_override(
                pass1_result=pass1_result,
                section_label=section_label,
                sample_entries=sample_entries
            )

            # PHASE 1 FIX #4: Apply grant keyword fallback if low confidence
            pass1_result = apply_grant_keyword_fallback(
                pass1_result=pass1_result,
                section_label=section_label,
                sample_entries=sample_entries
            )

            # FIX #312: An explicit canonical WCM section header trumps content
            pass1_result = apply_canonical_header_pin(
                pass1_result=pass1_result,
                section_label=section_label
            )

            # FIX #312B: geographic sub-label resolves to its document-order parent
            pass1_result = apply_geographic_sublabel_pin(
                pass1_result=pass1_result,
                section_label=section_label,
                effective_parent=effective_parent
            )

            parent_id = pass1_result['parent_section_id']
            confidence = pass1_result['confidence']

            print(f"{indent}    Pass 1 → {pass1_result['parent_canonical_name']} ({confidence:.2f})")

            # Track tokens (Pass 1 uses gpt-5.1)
            if 'token_usage' in pass1_result:
                usage = pass1_result['token_usage']
                prompt_tok = usage.get('prompt_tokens', 0)
                completion_tok = usage.get('completion_tokens', 0)
                total_tok = usage.get('total_tokens', 0)

                # Update totals
                total_prompt_tokens += prompt_tok
                total_completion_tokens += completion_tok
                total_tokens += total_tok

                # Track per-model usage (Pass 1 uses gpt-5.1)
                model = 'gpt-5.1'
                token_usage_by_model[model]['prompt'] += prompt_tok
                token_usage_by_model[model]['completion'] += completion_tok
                token_usage_by_model[model]['total'] += total_tok
                token_usage_by_model[model]['calls'] += 1
            pass1_count += 1

            # Get routing config for this parent
            parent_config = get_parent_section_config(parent_id)

            # Initialize batch_result for scope (may not be set if Pass 2 skipped)
            batch_result = None

            # ROUTER: Decide if Pass 2 needed
            # V6: Use definitive subsection check from valid_taxonomy_codes
            # CRITICAL: Never run Pass 2 on sections without subsections (E, F, G, H, I, J, L, O, P)
            from .valid_taxonomy_codes import has_subsections

            has_subsections_router = has_subsections(parent_id)

            # Run Pass 2 ONLY if section has subsections AND (confidence < 0.90 OR config requires 2 passes)
            # V6 FIX: Don't run Pass 2 on sections without subsections, even with low confidence
            if has_subsections_router and ((parent_config and parent_config.get('passes', 1) == 2) or confidence < 0.90):
                # PASS 2: Batch classification with hierarchical context
                # Triggered by: (1) parent_config requires 2 passes OR (2) low Pass 1 confidence
                if confidence < 0.90 and parent_config and parent_config.get('passes', 1) != 2:
                    print(f"{indent}    [CONFIDENCE TRIGGER] Low confidence ({confidence:.2f}) → Running Pass 2")

                if entries:
                    # Collect all entry texts
                    entry_texts = [e.get('text_snippet', '') for e in entries if e.get('text_snippet', '')]

                    if entry_texts:
                        # Always use gpt-5.1 for consistent quality
                        pass2_model = "gpt-5.1"

                        # Use batch classification with hierarchical context
                        batch_result = classify_pass2_batch(
                            parent_section_id=parent_id,
                            parent_confidence=confidence,
                            section_header=hierarchical_context.get('section_header', section_label),
                            subsection_header=hierarchical_context.get('subsection_header', ''),
                            entries=entry_texts,
                            model=pass2_model,
                            max_batch_size=10,
                            entry_objects=entries  # Pass full entry objects for validators
                        )

                        if batch_result['success'] and batch_result['classifications']:
                            # Use first classification for group-level mapping
                            first_classification = batch_result['classifications'][0]
                            final_section_id = first_classification['child_section_id']
                            final_canonical_name = first_classification['child_canonical_name']
                            avg_confidence = batch_result['avg_confidence']

                            # Show each entry's classification
                            print(f"{indent}    Pass 2 (batch of {len(entry_texts)}) classifications:")
                            for c in batch_result['classifications']:
                                entry_idx = c['entry_index']
                                code = c['child_section_id']
                                conf = c['confidence']
                                # Truncate entry text for display
                                entry_preview = entry_texts[entry_idx][:60] if entry_idx < len(entry_texts) else "N/A"
                                print(f"{indent}      [{entry_idx}] {code} ({conf:.2f}) - {entry_preview}...")

                            # PHASE 2 FIX: Validate S7 assignments
                            if final_section_id == 'S7' and entry_texts:
                                # Validate using first entry as representative
                                validation = validate_s7_assignment(entry_texts[0], final_section_id)
                                if not validation['is_valid'] and validation['confidence'] >= 4:
                                    # High-confidence error: override assignment
                                    old_section = final_section_id
                                    final_section_id = validation['should_be']
                                    # Update canonical name based on corrected section
                                    section_context = get_section_context(final_section_id)
                                    if section_context:
                                        final_canonical_name = section_context.get('section', final_canonical_name)
                                    s7_corrections_count += 1
                                    print(f"{indent}    ⚠️  S7 Validator: {old_section} → {final_section_id} ({validation['reason']})")

                            # Track tokens (Pass 2 may use different models)
                            if 'token_usage' in batch_result:
                                usage = batch_result['token_usage']
                                prompt_tok = usage.get('prompt_tokens', 0)
                                completion_tok = usage.get('completion_tokens', 0)
                                total_tok = usage.get('total_tokens', 0)

                                # Update totals
                                total_prompt_tokens += prompt_tok
                                total_completion_tokens += completion_tok
                                total_tokens += total_tok

                                # Track per-model usage (get model from usage, default to gpt-5.1)
                                model = usage.get('model_used', 'gpt-5.1')
                                token_usage_by_model[model]['prompt'] += prompt_tok
                                token_usage_by_model[model]['completion'] += completion_tok
                                token_usage_by_model[model]['total'] += total_tok
                                token_usage_by_model[model]['calls'] += 1
                            pass2_count += 1
                        else:
                            logger.warning(
                                "Pass 2 disambiguation failed for group %r -- keeping coarse parent %s: %s",
                                section_label, parent_id, batch_result.get('error', 'unknown error'),
                            )
                            final_section_id = parent_id
                            final_canonical_name = pass1_result['parent_canonical_name']
                    else:
                        final_section_id = parent_id
                        final_canonical_name = pass1_result['parent_canonical_name']
                else:
                    final_section_id = parent_id
                    final_canonical_name = pass1_result['parent_canonical_name']
            else:
                final_section_id = parent_id
                final_canonical_name = pass1_result['parent_canonical_name']

            # RULE 3: Validate and correct invalid taxonomy codes
            corrected_code, correction_reason = validate_and_correct_taxonomy_code(
                final_section_id,
                group,
                context="After Pass 1/2"
            )
            if correction_reason:
                print(f"{indent}    ⚠️  Code Validator: {final_section_id} → {corrected_code} ({correction_reason})")
                final_section_id = corrected_code
                # Update canonical name if changed
                if corrected_code != parent_id:
                    section_context = get_section_context(corrected_code)
                    if section_context:
                        final_canonical_name = section_context.get('section', final_canonical_name)

            # Store mapping
            mapping = {
                'source_label': section_label,
                'source_group_id': group_id,
                'pass1_parent_id': pass1_result['parent_section_id'],
                'pass1_confidence': pass1_result['confidence'],
                'pass1_alternatives': pass1_result.get('alternative_matches', []),  # Multi-guess Pass 1
                'final_section_id': final_section_id,
                'final_canonical_name': final_canonical_name,
                'pass2_alternatives': batch_result.get('classifications', [{}])[0].get('alternative_subsections', []) if batch_result and batch_result.get('success') else [],  # Multi-guess Pass 2
                'num_entries': len(entries),
                'num_subgroups': len(subgroups),
                'level': level,
                'path': path
            }
            mappings.append(mapping)

        except Exception as e:
            # This broad catch has historically buried bugs (note the removed
            # "'dict' object has no attribute" special-case) by turning a whole
            # group into an UNMAPPED/ERROR entry. logger.exception captures the
            # full traceback at ERROR so it is investigable. Phase 2 of #396
            # narrows this to let genuine bugs propagate (corpus-gated).
            logger.exception(
                "Group classification failed for %r (group_id=%s) -- recording as UNMAPPED",
                section_label, group_id,
            )
            mappings.append({
                'source_label': section_label,
                'source_group_id': group_id,
                'final_section_id': 'UNMAPPED',
                'final_canonical_name': 'ERROR',
                'error': str(e),
                'num_entries': len(entries),
                'level': level,
                'path': path
            })

        print()

        # Recursively map subgroups
        for subgroup in subgroups:
            map_group_recursive(subgroup, level + 1, path, hierarchical_context, effective_parent=child_effective_parent)

    # Map all top-level groups.
    # FIX #312B: walk in document order, tracking the current canonical WCM
    # parent so orphaned sub-labels (sub-headers the flat segmenter promoted to
    # siblings) inherit it as classification context.
    cur_canonical_parent = None
    for group in groups:
        _lbl = _normalize_header(group.get('label') or group.get('label_inferred', ''))
        _code = _parent_recognition_map().get(_lbl)
        if _code:
            cur_canonical_parent = _code
            eff = None  # this group IS a recognized section; nothing to inherit
        else:
            eff = cur_canonical_parent
        map_group_recursive(group, level=1, effective_parent=eff)

    # Calculate stats
    total_sections = len(mappings)
    high_confidence = sum(1 for m in mappings if m.get('pass1_confidence', 0) >= 0.85)
    unmapped = sum(1 for m in mappings if m.get('final_section_id') == 'UNMAPPED')

    avg_confidence = sum(m.get('pass1_confidence', 0) for m in mappings) / total_sections if total_sections > 0 else 0.0

    # Calculate per-model costs
    by_model_summary = {}
    total_cost = 0.0
    for model, usage in token_usage_by_model.items():
        cost = _centralized_calculate_cost(usage['prompt'], usage['completion'], model=model)
        by_model_summary[model] = {
            'prompt_tokens': usage['prompt'],
            'completion_tokens': usage['completion'],
            'total_tokens': usage['total'],
            'calls': usage['calls'],
            'cost_usd': round(cost, 4)
        }
        total_cost += cost

    stats = {
        'total_sections': total_sections,
        'skipped_structural_headers': skipped_headers_count,
        'classified_sections': total_sections,
        'high_confidence': high_confidence,
        'unmapped': unmapped,
        'avg_confidence': round(avg_confidence, 3),
        'pass1_calls': pass1_count,
        'pass2_calls': pass2_count,
        'total_api_calls': pass1_count + pass2_count,
        'api_calls_saved': skipped_headers_count,
        's7_corrections': s7_corrections_count
    }

    # Build output
    output_data = {
        'document_uid': segmented_cv.get('document_uid'),
        'source_file': segmented_cv_path,
        'meta': {
            **segmented_cv.get('meta', {}),
            'mapping_stats': stats,
            'mapping_approach': 'Two-pass hierarchical (v2) with context and routing',
            'token_usage': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_tokens,
                'by_model': by_model_summary,
                'total_cost_usd': round(total_cost, 4)
            }
        },
        'mappings': mappings
    }

    # =================================================================
    # PASS 2.5: POST-CLASSIFICATION VALIDATION
    # Run disambiguation validators on classified groups
    # =================================================================
    print("="*80)
    print("PASS 2.5: DISAMBIGUATION VALIDATION")
    print("="*80)
    print(f"Running 9 validators on {len(mappings)} classified groups...")
    print()

    # Convert mappings to groups for validation
    # Each mapping represents a classified group
    validation_input = {
        'groups': []
    }

    for idx, mapping in enumerate(mappings):
        # Reconstruct group structure for validators
        # Validators need: label_inferred, parent_section_id, entries, etc.
        group_for_validation = {
            'id': mapping.get('source_group_id', f'group_{idx}'),
            'label_inferred': mapping.get('source_label', ''),
            'parent_section_id': mapping.get('final_section_id', '').split('-')[0] if mapping.get('final_section_id') else '',  # Extract parent from final_section_id
            'child_section_id': mapping.get('final_section_id', ''),
            'entries': [],  # Validators primarily use labels and section IDs for disambiguation
            'confidence': mapping.get('pass1_confidence', 0.0)
        }
        validation_input['groups'].append(group_for_validation)

    # Run validators
    validated_data = validate_cv(validation_input)

    # Extract validation flags and merge back into mappings
    validation_flags_added = 0
    for idx, group in enumerate(validated_data.get('groups', [])):
        if 'validation_flags' in group and group['validation_flags']:
            # Add validation flags to corresponding mapping
            mappings[idx]['validation_flags'] = group['validation_flags']
            validation_flags_added += len(group['validation_flags'])

    # Add validation summary to output metadata
    if 'validation_summary' in validated_data.get('meta', {}):
        output_data['meta']['validation_summary'] = validated_data['meta']['validation_summary']

    # Print validation results
    validation_summary = validated_data.get('meta', {}).get('validation_summary', {})
    if validation_summary.get('total_flags', 0) > 0:
        print(f"✓ Validation complete: {validation_summary['total_flags']} flags raised")
        print(f"  By severity: {validation_summary.get('by_severity', {})}")
        print(f"  By type: {validation_summary.get('by_confusion_type', {})}")
    else:
        print(f"✓ Validation complete: No classification ambiguities detected")
    print()

    # Write output using OutputManager for consistent Stage 3 paths
    from .output_manager import OutputManager

    if output_path is None:
        # Use OutputManager for standard Stage 3 structure
        om = OutputManager(segmented_cv_path)
        output_path = om.get_stage3_path()
    else:
        output_path = Path(output_path)

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    # Print results
    print("="*80)
    print("MAPPING RESULTS (V2)")
    print("="*80)
    print(f"Classified sections: {total_sections}")
    print(f"  High confidence (≥0.85): {high_confidence} ({high_confidence/total_sections*100:.1f}%)")
    if unmapped > 0:
        print(f"  Unmapped (errors): {unmapped}")
    print(f"Average confidence: {avg_confidence:.3f}")
    if skipped_headers_count > 0:
        print()
        print(f"Skipped structural headers: {skipped_headers_count} (preserved for recovery context)")
    print()
    print(f"API Calls:")
    print(f"  Pass 1 (parent): {pass1_count}")
    print(f"  Pass 2 (child): {pass2_count}")
    print(f"  Total: {pass1_count + pass2_count}")
    if skipped_headers_count > 0:
        print(f"  Saved (skipped headers): {skipped_headers_count} ({skipped_headers_count/(pass1_count + pass2_count + skipped_headers_count)*100:.1f}% efficiency gain)")
    print()
    print(f"Token Usage:")
    print(f"  Prompt: {total_prompt_tokens:,}")
    print(f"  Completion: {total_completion_tokens:,}")
    print(f"  Total: {total_tokens:,}")
    print()
    print(f"Cost Breakdown:")
    for model, usage in by_model_summary.items():
        if usage['calls'] > 0:
            print(f"  {model}: ${usage['cost_usd']:.4f} ({usage['calls']} calls, {usage['total_tokens']:,} tokens)")
    print(f"  Total Cost: ${total_cost:.4f}")
    print()
    print(f"✓ Results saved to: {output_path}")

    return {
        'mappings': mappings,
        'stats': stats,
        'output_file': str(output_path),
        'meta': {
            'mapping_stats': stats,
            'token_usage': {
                'prompt_tokens': total_prompt_tokens,
                'completion_tokens': total_completion_tokens,
                'total_tokens': total_tokens,
                'by_model': by_model_summary,
                'total_cost_usd': round(total_cost, 4)
            }
        }
    }


# =============================================================================
# GUIDED CLASSIFICATION WITH LLM-SURFACED CANDIDATES (V10 Architecture)
# =============================================================================

def classify_with_surfaced_candidates(
    entries: List[Dict],
    primary_candidates: List[Dict],
    secondary_candidates: List[Dict],
    hierarchy: List[str],
    model: str = "gpt-5.1",
    low_confidence_threshold: float = 0.4
) -> Dict[str, Any]:
    """
    LLM-SURFACED GUIDED CLASSIFICATION: Classify entries using surfaced candidates.

    This implements the V10 architecture where candidates are pre-filtered by
    analyzing CV structure (headers + sample entries) and presented as GUIDANCE
    rather than hard ENUM constraints.

    Args:
        entries: List of entry dicts with 'text' key
        primary_candidates: High-likelihood codes from candidate surfacing
        secondary_candidates: Fallback codes (escape hatch)
        hierarchy: Full hierarchy path for context
        model: LLM model to use

    Returns:
        {
            'success': bool,
            'classifications': List[Dict],  # One per entry
            'avg_confidence': float,
            'escape_hatch_usage': int,      # Count of escape hatch selections
            'token_usage': Dict,
            'elapsed_time': float,
            'error': Optional[str]
        }

    Each classification dict contains:
        {
            'taxonomy_code': str,           # e.g., 'S1'
            'taxonomy_label': str,
            'confidence': float,
            'reasoning': str,
            'used_escape_hatch': bool,
            'alternatives': List[str]       # Alternative codes considered
        }
    """
    start_time = time.time()

    # Format candidates for prompt
    formatted_candidates = format_candidates_for_prompt(
        primary_candidates=primary_candidates,
        secondary_candidates=secondary_candidates,
        include_full_descriptions=True
    )

    # Build system prompt
    system_prompt = """You are a CV taxonomy expert performing GUIDED CLASSIFICATION with pre-filtered candidates.

You have been provided with:
- PRIMARY CANDIDATES: Pre-filtered codes most likely for this subsection (based on semantic analysis)
- ESCAPE HATCH: Alternative codes if primary candidates don't fit

Your task: Select the MOST SPECIFIC taxonomy code that fits each entry.

CLASSIFICATION LOGIC:
1. **Start with PRIMARY CANDIDATES** - These are pre-filtered as most likely based on:
   - Section/subsection headers
   - Sample entries showing actual content type
   - Semantic understanding of CV structure

2. **Hierarchical context as strong hints** - Use section headers to understand domain

3. **Cross-category awareness** - If samples clearly show content contradicts section header,
   PRIMARY candidates already reflect this (e.g., "Teaching Publications" → S codes)

4. **Escape hatch for edge cases** - If PRIMARY doesn't fit, use ESCAPE HATCH:
   - Explain why PRIMARY candidates don't match
   - Provide detailed reasoning for alternative choice
   - Set used_escape_hatch: true

IMPORTANT: PRIMARY candidates are GUIDANCE, not constraints. Trust your analysis."""

    # Build user prompt
    section_header = hierarchy[0] if hierarchy else "(unknown)"
    subsection_header = hierarchy[-1] if len(hierarchy) > 1 else "(none)"

    user_prompt = f"""Classify these CV entries to taxonomy codes.

HIERARCHICAL CONTEXT:
  Section: {section_header}
  Subsection: {subsection_header}

{formatted_candidates}

ENTRIES TO CLASSIFY:
"""

    # Add entries (limit preview to 300 chars each)
    for i, entry in enumerate(entries[:10]):  # Max 10 per batch
        entry_text = entry.get('text', '') if isinstance(entry, dict) else entry
        preview = entry_text[:300] + "..." if len(entry_text) > 300 else entry_text
        user_prompt += f"\n[{i}] {preview}\n"

    user_prompt += """

OUTPUT FORMAT:
Return JSON array with one classification per entry:
[
  {
    "taxonomy_code": "S1",
    "taxonomy_label": "Peer-Reviewed Original Research Articles",
    "confidence": 0.85,
    "reasoning": "Entry describes original research published in peer-reviewed journal...",
    "used_escape_hatch": false,
    "alternatives": ["S2", "S10"]
  },
  ...
]

IMPORTANT:
- Classify EACH entry individually (do not batch-assign same code)
- Use reasoning to explain your choice
- Mark used_escape_hatch: true if selecting from ESCAPE HATCH
- Provide alternatives considered
"""

    # Prepare API call (non-strict JSON schema for flexibility)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    # JSON schema WITH ENUM constraint (prevents invalid code invention like A4, AT1, etc.)
    # IMPORTANT: "T" is excluded from enum - LLM must choose real codes, low confidence routes to T automatically
    response_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "guided_classification",
            "strict": False,  # Allow flexibility, but codes are restricted by enum (minus T)
            "schema": {
                "type": "object",
                "properties": {
                    "classifications": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "taxonomy_code": {
                                    "type": "string",
                                    "enum": ALL_VALID_CODES_EXCLUDING_T,
                                    "description": "Selected taxonomy code (prefer PRIMARY candidates). T is hidden - low confidence routes there automatically."
                                },
                                "taxonomy_label": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "reasoning": {"type": "string"},
                                "used_escape_hatch": {"type": "boolean"},
                                "alternatives": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            },
                            "required": ["taxonomy_code", "taxonomy_label", "confidence", "reasoning"]
                        }
                    }
                },
                "required": ["classifications"]
            }
        }
    }

    # Call LLM
    try:
        result_llm = call_llm(
            stage="core_taxonomy_v2",
            messages=messages,
            response_format=response_schema,
            max_tokens=2500,
        )

        # Parse response
        result = json.loads(result_llm["content"])
        classifications = result.get('classifications', [])

        # Low-confidence routing: Remap entries with confidence < threshold to "T" (Appendix/Other)
        # T is hidden from LLM enum to force real classification attempts
        routed_to_t = 0
        for classification in classifications:
            conf = classification.get('confidence', 0.0)
            if conf < low_confidence_threshold:
                classification['taxonomy_code'] = 'T'
                classification['taxonomy_label'] = 'Appendix/Other'
                classification['reasoning'] = f"Low confidence ({conf:.2f} < {low_confidence_threshold}) - routed to T. Original: {classification.get('reasoning', 'N/A')}"
                routed_to_t += 1

        # Calculate metrics
        confidences = [c.get('confidence', 0.0) for c in classifications]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        escape_hatch_usage = sum(
            1 for c in classifications if c.get('used_escape_hatch', False)
        )

        elapsed_time = time.time() - start_time

        return {
            'success': True,
            'classifications': classifications,
            'avg_confidence': avg_confidence,
            'escape_hatch_usage': escape_hatch_usage,
            'routed_to_t': routed_to_t,
            'token_usage': {
                'prompt_tokens': result_llm["prompt_tokens"],
                'completion_tokens': result_llm["completion_tokens"],
                'total_tokens': result_llm["total_tokens"],
                'cost': result_llm["cost"]
            },
            'elapsed_time': elapsed_time,
            'error': None
        }

    except Exception as e:
        # Return error result (Phase 2 of #396 narrows this, corpus-gated).
        logger.exception("classify_with_surfaced_candidates failed -- returning failure result")
        return {
            'success': False,
            'classifications': [],
            'avg_confidence': 0.0,
            'escape_hatch_usage': 0,
            'token_usage': {},
            'elapsed_time': time.time() - start_time,
            'error': str(e)
        }


def refine_parent_to_child(
    entry_text: str,
    parent_code: str,
    hierarchy: List[str],
    model: str = "gpt-5.1"
) -> Dict[str, Any]:
    """
    AUTO-REFINEMENT: When LLM selects parent-only code, refine to specific child.

    This handles the case where guided classification chooses a parent code from
    the escape hatch (e.g., "N" for Mentoring). We automatically run a targeted
    Pass 2 with just that parent's children to get the specific child code.

    Args:
        entry_text: The CV entry text
        parent_code: Parent code that needs refinement (e.g., "N")
        hierarchy: Full hierarchy path for context
        model: LLM model to use

    Returns:
        Classification dict with refined child code
    """
    # Check if parent actually has children
    if not has_subsections(parent_code):
        # No children - return parent code as-is
        section_context = get_section_context(parent_code)
        label = section_context.get('canonical_name', parent_code) if section_context else parent_code

        return {
            'taxonomy_code': parent_code,
            'taxonomy_label': label,
            'confidence': 0.70,  # Lower confidence for parent-only
            'reasoning': f'Parent code {parent_code} has no subsections',
            'used_escape_hatch': True,
            'alternatives': [],
            'refinement_applied': False
        }

    # Get child codes for this parent
    try:
        child_codes_list = get_valid_children(parent_code)
        if not child_codes_list:
            # No children found
            return {
                'taxonomy_code': parent_code,
                'taxonomy_label': parent_code,
                'confidence': 0.70,
                'reasoning': f'Could not find children for {parent_code}',
                'used_escape_hatch': True,
                'alternatives': [],
                'refinement_applied': False
            }
    except Exception as e:
        # Degrades to the parent code -- make the swallowed error loud so it is
        # not mistaken for a benign escape-hatch (Phase 2 of #396 narrows this).
        logger.exception(
            "refine_parent_to_child failed for parent %s -- falling back to parent @0.50",
            parent_code,
        )
        return {
            'taxonomy_code': parent_code,
            'taxonomy_label': parent_code,
            'confidence': 0.50,
            'reasoning': f'Error getting children: {str(e)}',
            'used_escape_hatch': True,
            'alternatives': [],
            'refinement_applied': False
        }

    # Run targeted Pass 2 with just this parent's children
    section_header = hierarchy[0] if hierarchy else ""
    subsection_header = hierarchy[-1] if len(hierarchy) > 1 else ""

    # Use existing classify_pass2_batch function with parent's children
    result = classify_pass2_batch(
        parent_section_id=parent_code,
        parent_confidence=0.70,  # Medium confidence (escape hatch selection)
        section_header=section_header,
        subsection_header=subsection_header,
        entries=[entry_text],
        model=model,
        max_batch_size=1
    )

    if result.get('success') and result.get('classifications'):
        classification = result['classifications'][0]
        # Add refinement flag
        classification['refinement_applied'] = True
        classification['used_escape_hatch'] = True  # Still came from escape hatch
        return classification
    else:
        # Refinement failed - return parent code
        return {
            'taxonomy_code': parent_code,
            'taxonomy_label': parent_code,
            'confidence': 0.60,
            'reasoning': f'Auto-refinement failed: {result.get("error", "Unknown error")}',
            'used_escape_hatch': True,
            'alternatives': [],
            'refinement_applied': False
        }


def main():
    """Command-line interface for v2 taxonomy mapping."""
    if len(sys.argv) < 2:
        print("Two-Pass Hierarchical Taxonomy Mapper (v2)")
        print()
        print("Usage: python taxonomy_mapper_v2.py <segmented_cv_path> [output_path]")
        print()
        print("Maps segmented CV sections using two-pass hierarchical classification")
        print()
        sys.exit(1)

    segmented_cv_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(segmented_cv_path):
        print(f"Error: File not found: {segmented_cv_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = map_cv_sections_v2(segmented_cv_path, output_path)
        sys.exit(0)

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
